"""Governance product packages: catalog reads into a data dictionary and the ownership model agent."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402


class Dictionary(Base):
    def dictionary(self):
        with open(self.out / 'veri_sozlugu.csv', encoding='utf-8') as fh:
            return list(csv.DictReader(fh))

    def test_purview_search_and_entity(self):
        self.server.add('POST', r'/datamap/api/search/query\?api-version=2023-09-01', {'value': [{'id': 'g1', 'qualifiedName': 'mssql://s/db/dbo/orders'}]})
        self.server.add('GET', r'/datamap/api/atlas/v2/entity/guid/g1\?api-version=2023-09-01', {
            'entity': {'typeName': 'azure_sql_table', 'attributes': {'qualifiedName': 'mssql://s/db/dbo/orders', 'userDescription': 'Siparişler'},
                       'contacts': {'Owner': [{'id': 'oid-1'}]}, 'relationshipAttributes': {'columns': [{'guid': 'c1', 'displayText': 'email'}], 'meanings': [{'displayText': 'Sipariş'}]}},
            'referredEntities': {'c1': {'attributes': {'name': 'email', 'data_type': 'nvarchar'}, 'classifications': [{'typeName': 'MICROSOFT.PERSONAL.EMAIL'}]}}})
        self.run_op('purview-data-dictionary', account='myacct')
        rows = self.dictionary()
        self.assertEqual((rows[0]['sahip'], rows[0]['terim']), ('oid-1', 'Sipariş'))
        self.assertEqual(rows[1]['etiketler'], 'MICROSOFT.PERSONAL.EMAIL')
        self.assertIn('açıklama', self.read('eksikler.csv'))

    def test_collibra_relations(self):
        self.server.add('GET', r'/rest/2.0/assets\?.*', {'results': [{'id': 't1', 'name': 'orders', 'status': {'name': 'Approved'}}], 'total': 1})
        self.server.add('GET', r'/rest/2.0/attributes\?assetId=t1.*', {'results': [{'value': 'Sipariş tablosu'}]})
        self.server.add('GET', r'/rest/2.0/responsibilities\?resourceIds=t1.*', {'results': [{'role': {'name': 'Data Owner'}, 'owner': {'name': 'ayse'}}]})
        self.server.add('GET', r'/rest/2.0/relations\?targetId=t1.*', {'results': [{'source': {'id': 'c1', 'name': 'amount'}}]})
        self.server.add('GET', r'/rest/2.0/attributes\?assetId=c1.*', {'results': []})
        self.run_op('collibra-data-dictionary', server='https://c.example', domain_id='d')
        self.assertEqual(self.dictionary()[0]['sahip'], 'Data Owner: ayse')

    def test_openmetadata_and_datahub(self):
        self.server.add('GET', r'/api/v1/tables\?.*', {'data': [{'fullyQualifiedName': 'svc.db.s.orders', 'description': 'x', 'owners': [{'name': 'team-sales'}],
                                                                 'tags': [{'tagFQN': 'Glossary.Order', 'source': 'Glossary'}, {'tagFQN': 'PII.Sensitive', 'source': 'Classification'}],
                                                                 'columns': [{'name': 'id', 'dataType': 'INT', 'tags': []}]}], 'paging': {}})
        self.run_op('openmetadata-data-dictionary', server='https://om.example')
        self.assertEqual((self.dictionary()[0]['terim'], self.dictionary()[0]['etiketler']), ('Glossary.Order', 'PII.Sensitive'))
        self.out = self.root / 'dh'
        self.server.add('POST', '/api/graphql', {'data': {'searchAcrossEntities': {'total': 1, 'searchResults': [{'entity': {
            'urn': 'u', 'name': 'orders', 'platform': {'name': 'snowflake'}, 'properties': {'qualifiedName': 'DB.S.ORDERS', 'description': 'raw'}, 'editableProperties': {'description': 'Siparişler'},
            'ownership': {'owners': [{'owner': {'username': 'ali'}}]}, 'schemaMetadata': {'fields': [{'fieldPath': 'ID', 'nativeDataType': 'NUMBER'}]},
            'editableSchemaMetadata': {'editableSchemaFieldInfo': [{'fieldPath': 'ID', 'description': 'Kimlik'}]}}}]}}})
        self.run_op('datahub-data-dictionary', server='https://dh.example')
        rows = self.dictionary()
        self.assertEqual((rows[0]['aciklama'], rows[1]['aciklama']), ('Siparişler', 'Kimlik'))


class Ownership(Base):
    def test_invented_owner_rejected(self):
        page = {'data': [{'fullyQualifiedName': 'svc.db.s.orders', 'owners': [{'name': 'team-sales'}], 'columns': [{'name': 'email'}]},
                         {'fullyQualifiedName': 'svc.db.s.customers', 'columns': []}], 'paging': {}}
        self.server.add('GET', r'/api/v1/tables\?.*', page)
        bad = {'alanlar': [{'alan': 'Satış'}], 'atamalar': [{'varlik': 'svc.db.s.orders', 'alan': 'Satış', 'sahip_rolu': 'Satış direktörü', 'onerilen_sahip': 'mehmet.yilmaz',
                                                            'siniflandirma': 'kisisel'}]}
        good = {'alanlar': [{'alan': 'Satış'}], 'atamalar': [{'varlik': 'svc.db.s.orders', 'alan': 'Satış', 'sahip_rolu': 'Satış direktörü', 'onerilen_sahip': 'team-sales',
                                                             'siniflandirma': 'kisisel', 'gerekce': 'email kolonu'}]}
        with model_reply(bad, good) as chat:
            self.run_op('openmetadata-ownership-model', server='https://om.example')
        self.assertIn('mehmet.yilmaz', chat.call_args_list[1][0][3][-1]['content'])
        self.assertIn('svc.db.s.customers', self.read('yonetisim_modeli.md'))
