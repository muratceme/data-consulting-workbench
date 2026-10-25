"""MDM product packages: master record reads, duplicate detection and the matching strategy agent with measured effect."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402

STEP = '''<STEP-ProductInformation><Products>
<Product ID="P1" UserTypeID="Item"><Name>Vida M8 Paslanmaz</Name><Values><Value AttributeID="EAN">8690000000011</Value></Values></Product>
<Product ID="P2" UserTypeID="Item"><Name>VİDA M8 PASLANMAZ</Name><Values><Value AttributeID="EAN">8690000000011</Value></Values></Product>
<Product ID="P3" UserTypeID="Item"><Name>Somun M8</Name><Values><Value AttributeID="EAN">8690000000028</Value></Values></Product>
</Products></STEP-ProductInformation>'''


class Duplicates(Base):
    def test_stepxml_duplicates(self):
        (self.root / 's.xml').write_text(STEP, encoding='utf-8')
        self.run_op('stibo-step-duplicates', stepxml_file='s.xml', match_fields=['Name'], id_field='ID')
        self.assertEqual(self.read('mukerrer.json')['birebir_grup'], 1)
        self.assertIn('1,P1', self.read('birebir_mukerrer.csv'))

    def test_b360_search_paging(self):
        self.server.add('POST', '/saas/public/core/v3/login', {'userInfo': {'sessionId': 'S'}})
        self.server.add('POST', '/search/public/api/v1/search', {'searchResult': {'hits': 2, 'records': [{'_meta': {'businessId': 'b1'}, 'c360organization.name': 'Acme A.Ş.'}]}})
        self.server.add('POST', '/search/public/api/v1/search', {'searchResult': {'hits': 2, 'records': [{'_meta': {'businessId': 'b2'}, 'c360organization.name': 'ACME AŞ'}]}})
        self.run_op('informatica-mdm-duplicates', login_url='https://dm-us.informaticacloud.com', mdm_url='https://mdm.example', entity_type='c360.organization',
                    match_fields=['c360organization.name'], id_field='businessId', similarity_threshold=0.8)
        self.assertEqual(self.server.received[2][2]['recordOffset'], 1)
        self.assertEqual(self.server.received[1][3]['Ids-Session-Id'], 'S')
        self.assertEqual(self.read('mukerrer.json')['benzer_aday_cifti'], 1)

    def test_reltio_attributes_flattened(self):
        self.server.add('GET', r'/reltio/api/t1/entities\?.*offset=0', [{'uri': 'entities/1', 'attributes': {'Name': [{'value': 'Beta Ltd'}]}},
                                                                         {'uri': 'entities/2', 'attributes': {'Name': [{'value': 'beta ltd.'}]}}])
        self.run_op('reltio-duplicates', environment='eu-360', tenant='t1', entity_type='Organization', match_fields=['Name'], id_field='uri')
        self.assertEqual(self.read('mukerrer.json')['birebir_grup'], 1)

    def test_semarchy_and_ebx(self):
        self.server.add('GET', r'/semarchy/api/rest/query/Hub/Customer/GD\?.*', {'records': [{'CustomerID': 1, 'CustomerName': 'X'}, {'CustomerID': 2, 'CustomerName': 'x'}]})
        self.run_op('semarchy-duplicates', server='https://s.example', data_location='Hub', entity='Customer', match_fields=['CustomerName'], id_field='CustomerID')
        self.assertEqual(self.server.received[0][3]['Api-Key'], 'test-only-secret')
        self.out = self.root / 'ebx'
        self.server.add('GET', r'/ebx-dataservices/rest/data/v1/BReference/ds/root/Customers\?pageSize=1000',
                        {'rows': [{'content': {'id': {'content': 1}, 'name': {'content': 'Ayşe'}}}], 'pagination': {'nextPage': '/ebx-dataservices/rest/data/v1/BReference/ds/root/Customers?pageSize=1000&page=2'}})
        self.server.add('GET', r'/ebx-dataservices/rest/data/v1/BReference/ds/root/Customers\?pageSize=1000&page=2', {'rows': [{'content': {'id': {'content': 2}, 'name': {'content': 'AYSE'}}}]})
        self.run_op('tibco-ebx-duplicates', server='https://e.example', dataspace='Reference', dataset='ds', table_path='/root/Customers', match_fields=['name'], id_field='id')
        self.assertEqual(self.read('mukerrer.json')['birebir_grup'], 1)


class GoldenRecord(Base):
    def test_rules_validated_and_effect_measured(self):
        (self.root / 's.xml').write_text(STEP, encoding='utf-8')
        bad = {'eslesme_kurallari': [{'ad': 'ad', 'alanlar': ['Name'], 'karsilastirma': 'benzerlik', 'esik': 0.8, 'aksiyon': 'otomatik_birlestir'}]}
        good = {'eslesme_kurallari': [{'ad': 'EAN', 'alanlar': ['EAN'], 'karsilastirma': 'tam', 'aksiyon': 'otomatik_birlestir'},
                                      {'ad': 'ad', 'alanlar': ['Name'], 'karsilastirma': 'benzerlik', 'esik': 0.85, 'aksiyon': 'incelemeye_gonder'}],
                'hayatta_kalma': [{'alan': 'Name', 'kural': 'en_uzun'}]}
        with model_reply(bad, good) as chat:
            self.run_op('stibo-step-golden-record', stepxml_file='s.xml', match_fields=['Name', 'EAN'])
        self.assertIn('0.95', chat.call_args_list[1][0][3][-1]['content'])
        measured = {m['ad']: m for m in self.read('eslestirme_stratejisi.json')['olculen_etki']}
        self.assertEqual(measured['EAN']['olculen_birebir_fazla'], 1)
