"""Privacy product packages: platform classifications drive local masking; access design with Ranger drafts."""
import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402

DATA = 'id,Email,phone,city\n1,a@b.com,05321234567,Ankara\n2,c@d.com,,İzmir\n'


class Masking(Base):
    def setUp(self):
        super().setUp()
        (self.root / 'veri.csv').write_text(DATA, encoding='utf-8')

    def masked(self):
        with open(self.out / 'maskelenmis.csv', encoding='utf-8') as fh:
            return list(csv.DictReader(fh))

    def test_immuta_tags_drive_masking_and_source_untouched(self):
        self.server.add('GET', r'/dataSource\?searchText=crm&size=20', {'hits': [{'id': 5, 'name': 'crm'}]})
        self.server.add('GET', '/dictionary/5', {'metadata': [{'name': 'email', 'tags': [{'name': 'Discovered.PII.Email'}]}, {'name': 'city', 'tags': []}]})
        self.run_op('immuta-mask-fields', server='https://i.example', data_source_name='crm', extra_fields=['phone'])
        rows = self.masked()
        self.assertNotIn('a@b.com', json.dumps(rows)); self.assertEqual(rows[0]['city'], 'Ankara')
        self.assertEqual(rows[1]['phone'], '')
        self.assertEqual((self.root / 'veri.csv').read_text(encoding='utf-8'), DATA)
        self.assertEqual(self.read('maskeleme.json')['maskelenen_kolon'], ['Email', 'phone'])

    def test_privacera_ranger_tags(self):
        self.server.add('GET', r'/service/tags/resources\?serviceName=tagsvc', [{'id': 1, 'resourceElements': {'database': {'values': ['crm']}, 'table': {'values': ['customers']},
                                                                                                          'column': {'values': ['email']}}}])
        self.server.add('GET', '/service/tags/tags', [{'id': 10, 'type': 'PII_EMAIL'}])
        self.server.add('GET', '/service/tags/tagresourcemaps', [{'resourceId': 1, 'tagId': 10}])
        self.run_op('privacera-mask-fields', ranger_url='https://r.example', tag_service='tagsvc', mask_method='partial')
        self.assertEqual(self.masked()[0]['Email'], '***.com')

    def test_onetrust_export_and_missing_header(self):
        (self.root / 'ot.csv').write_text('Table Name,Column Name,Classification\ncustomers,phone,Phone Number\n', encoding='utf-8')
        self.run_op('onetrust-mask-fields', results_file='ot.csv', mask_method='redact')
        self.assertEqual(self.masked()[0]['phone'], '***')
        self.out = self.root / 'bad'
        with self.assertRaises(native.NativeError):
            self.run_op('onetrust-mask-fields', results_file='ot.csv', classification_header='Type')

    def test_no_matching_column_fails(self):
        self.server.add('GET', r'/api/v1/data-catalog/object-details/columns\?object_name=ds.s.t', {'data': [{'column_name': 'ssn', 'attribute_list': [{'attribute_name': 'SSN'}]}]})
        with self.assertRaises(native.NativeError):
            self.run_op('bigid-mask-fields', server='https://b.example', object_name='ds.s.t')

    def test_hash_requires_salt(self):
        self.server.add('GET', r'/dataSource\?searchText=crm&size=20', {'hits': [{'id': 5, 'name': 'crm'}]})
        self.server.add('GET', '/dictionary/5', {'metadata': [{'name': 'email', 'tags': ['PII']}]})
        with patch.dict('os.environ', {'MASK_SALT': 'short'}), self.assertRaises(native.NativeError):
            self.run_op('immuta-mask-fields', server='https://i.example', data_source_name='crm')


class AccessDesign(Base):
    def test_ranger_masking_drafts_disabled(self):
        self.server.add('GET', r'/service/tags/resources\?serviceName=tagsvc', [{'id': 1, 'resourceElements': {'database': {'values': ['crm']}, 'table': {'values': ['customers']},
                                                                                                          'column': {'values': ['email']}}}])
        self.server.add('GET', '/service/tags/tags', [{'id': 10, 'type': 'PII_EMAIL'}])
        self.server.add('GET', '/service/tags/tagresourcemaps', [{'resourceId': 1, 'tagId': 10}])
        reply = {'siniflar': [{'sinif': 'Kişisel'}], 'kolon_atamalari': [{'tablo': 'crm.customers', 'kolon': 'email', 'sinif': 'Kişisel'}],
                 'erisim_kurallari': [{'sinif': 'Kişisel', 'rol': 'analyst', 'erisim': 'maskeli', 'maske': 'hash'}, {'sinif': 'Kişisel', 'rol': 'dpo', 'erisim': 'tam'}]}
        bad = {**reply, 'kolon_atamalari': [{'tablo': 'crm.customers', 'kolon': 'tckn', 'sinif': 'Kişisel'}]}
        with model_reply(bad, reply):
            self.run_op('privacera-access-design', ranger_url='https://r.example', tag_service='tagsvc')
        policies = self.read('ranger_maskeleme_taslagi.json')
        self.assertEqual(len(policies), 1)
        self.assertFalse(policies[0]['isEnabled'])
        self.assertEqual(policies[0]['dataMaskPolicyItems'][0]['dataMaskInfo']['dataMaskType'], 'MASK_HASH')
