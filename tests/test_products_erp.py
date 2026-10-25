"""ERP product packages: extracts through each ERP interface, reconciliation against a target file and the mapping agent."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import erp  # noqa: E402

V2_META = b'''<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0"><edmx:DataServices><Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="API">
<EntityType Name="A_BusinessPartnerType"><Key><PropertyRef Name="BusinessPartner"/></Key><Property Name="BusinessPartner" Type="Edm.String" Nullable="false" MaxLength="10"/>
<Property Name="BusinessPartnerName" Type="Edm.String" MaxLength="81"/><Property Name="BusinessPartnerCategory" Type="Edm.String" Nullable="false" MaxLength="1"/></EntityType>
<EntityContainer Name="C"><EntitySet Name="A_BusinessPartner" EntityType="API.A_BusinessPartnerType"/></EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'''
SAP = {'server': 'https://s4.example', 'service': '/sap/opu/odata/sap/API_BUSINESS_PARTNER', 'entity_set': 'A_BusinessPartner', 'fields': ['BusinessPartner', 'BusinessPartnerName']}


class Erp(Base):
    def target(self, text):
        (self.root / 'hedef.csv').write_text(text, encoding='utf-8')

    def test_s4_odata_v2_paging_and_reconcile(self):
        page1 = {'d': {'results': [{'__metadata': {}, 'BusinessPartner': '1', 'BusinessPartnerName': 'Acme'}],
                       '__next': '/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner?$skiptoken=1'}}
        page2 = {'d': {'results': [{'BusinessPartner': '2', 'BusinessPartnerName': 'Beta'}]}}
        self.server.add('GET', r'/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner\?\$select=BusinessPartner,BusinessPartnerName&\$format=json&\$inlinecount=none', page1)
        self.server.add('GET', r'/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner\?\$skiptoken=1', page2)
        self.target('BusinessPartner,BusinessPartnerName\n1,ACME\n3,Gamma\n')
        self.run_op('s4hana-reconcile', **SAP, keys=['BusinessPartner'], target_file='hedef.csv')
        result = self.read('mutabakat.json')
        self.assertEqual((result['eslesen'], result['yalniz_kaynakta'], result['yalniz_hedefte'], result['farkli_kayit']), (1, 1, 1, 0))
        self.assertTrue(self.server.received[0][3]['Authorization'].startswith('Basic '))

    def test_b1_service_layer_session(self):
        self.server.add('POST', '/b1s/v1/Login', {}, headers={'Set-Cookie': 'B1SESSION=abc; HttpOnly'})
        self.server.add('GET', r'/b1s/v1/BusinessPartners\?\$select=CardCode,CardName', {'value': [{'CardCode': 'C1', 'CardName': 'A'}], 'odata.nextLink': 'BusinessPartners?$skip=1'})
        self.server.add('GET', r'/b1s/v1/BusinessPartners\?\$skip=1', {'value': [{'CardCode': 'C2', 'CardName': 'B'}]})
        self.server.add('POST', '/b1s/v1/Logout', {})
        self.target('CardCode,CardName\nC1,A\nC2,B\n')
        self.run_op('business-one-reconcile', server='https://b1.example:50000', company_db='DB', entity_set='BusinessPartners', fields=['CardCode', 'CardName'],
                    keys=['CardCode'], target_file='hedef.csv')
        self.assertEqual(self.server.received[1][3]['Cookie'], 'B1SESSION=abc')
        self.assertEqual(self.server.received[1][3]['Prefer'], 'odata.maxpagesize=500')
        self.assertTrue(self.read('mutabakat.json')['mutabik'])

    def test_logo_preset_and_validation(self):
        fake = FakeSql({'erp_extract': ([], [{'kod': '120.01', 'unvan': 'A', 'bakiye': 100.0}])})
        self.target('kod,bakiye\n120.01,100.004\n')
        with patch.object(erp, 'sql_source', return_value=fake):
            self.run_op('logo-tiger-reconcile', odbc='Driver=x;Server=y', firm='001', period='01', preset='cari', keys=['kod'], compare_fields=['bakiye'], tolerance=0.01,
                        target_file='hedef.csv')
        self.assertIn('LG_001_01_CLFLINE', fake.queries[0][1]); self.assertIn('L.CANCELLED = 0', fake.queries[0][1])
        self.assertTrue(self.read('mutabakat.json')['mutabik'])
        with patch.object(erp, 'sql_source') as source, self.assertRaises(native.NativeError):
            self.out = self.root / 'bad'
            self.run_op('logo-go-reconcile', odbc='x', firm='1; DROP', period='01', preset='cari', keys=['kod'], target_file='hedef.csv')
        source.assert_not_called()

    def test_jplatform_custom_query_must_be_select(self):
        (self.root / 'sorgu.sql').write_text('DELETE FROM x', encoding='utf-8')
        with patch.object(erp, 'sql_source') as source, self.assertRaises(native.NativeError):
            self.run_op('j-platform-reconcile', odbc='x', keys=['kod'], target_file='hedef.csv')
        source.assert_not_called()

    def test_fusion_paging_and_describe(self):
        self.server.add('GET', r'/fscmRestApi/resources/11.13.18.05/receivablesInvoices\?onlyData=true&limit=500&offset=0.*', {'items': [{'TransactionNumber': 'T1', 'links': []}], 'hasMore': True})
        self.server.add('GET', r'/fscmRestApi/resources/11.13.18.05/receivablesInvoices\?onlyData=true&limit=500&offset=1.*', {'items': [{'TransactionNumber': 'T2'}], 'hasMore': False})
        self.target('TransactionNumber\nT1\nT2\n')
        self.run_op('fusion-reconcile', server='https://f.example', resource='receivablesInvoices', fields=['TransactionNumber'], keys=['TransactionNumber'], target_file='hedef.csv')
        self.assertEqual(self.read('mutabakat.json')['eslesen'], 2)

    def test_odoo_jsonrpc(self):
        self.server.add('POST', '/jsonrpc', {'result': 7})
        self.server.add('POST', '/jsonrpc', {'result': [{'id': 1, 'ref': 'C1', 'name': 'A', 'country_id': [5, 'Türkiye']}]})
        self.target('ref,name\nC1,A\n')
        self.run_op('odoo-reconcile', server='https://o.example', database='db', model='res.partner', fields=['ref', 'name'], keys=['ref'], target_file='hedef.csv')
        call = self.server.received[1][2]['params']
        self.assertEqual(call['args'][:6], ['db', 7, 'test-only-secret', 'res.partner', 'search_read', [[]]])
        self.assertIn('Türkiye', self.read('erp_cekimi.csv'))

    def test_workday_raas(self):
        self.server.add('GET', r'/ccx/service/customreport2/acme/ISU/Workers\?format=json', {'Report_Entry': [{'Employee_ID': 'E1', 'Name': 'x'}]})
        self.target('Employee_ID,Name\nE1,x\n')
        self.run_op('workday-reconcile', server='https://wd.example', tenant='acme', report_owner='ISU', report_name='Workers', keys=['Employee_ID'], target_file='hedef.csv')
        self.assertTrue(self.read('mutabakat.json')['mutabik'])

    def test_mapping_agent_requires_required_fields(self):
        self.server.add('GET', r'/sap/opu/odata/sap/API_BUSINESS_PARTNER/\$metadata', V2_META)
        self.server.add('GET', r'/sap/opu/odata/sap/API_BUSINESS_PARTNER/\$metadata', V2_META)
        (self.root / 'kaynak.csv').write_text('musteri_no,unvan,tur\n1,' + 'A' * 90 + ',K\n', encoding='utf-8')
        bad = {'eslemeler': [{'hedef': 'BusinessPartner', 'kaynak': ['musteri_no'], 'donusum': 'dogrudan'}]}
        good = {'eslemeler': [{'hedef': 'BusinessPartner', 'kaynak': ['musteri_no'], 'donusum': 'dogrudan'}, {'hedef': 'BusinessPartnerName', 'kaynak': ['unvan'], 'donusum': 'dogrudan'},
                              {'hedef': 'BusinessPartnerCategory', 'kaynak': ['tur'], 'donusum': 'kod_eslestirme', 'parametre': 'K→1'}], 'eslenmeyen_zorunlular': []}
        with model_reply(bad, good) as chat:
            self.run_op('s4hana-mapping-plan', **SAP, source_file='kaynak.csv')
        self.assertIn('BusinessPartnerCategory', chat.call_args_list[1][0][3][-1]['content'])
        self.assertIn('kaynakta 90 karakter > hedef 81', self.read('esleme.csv'))
        with model_reply({'eslemeler': [{'hedef': 'Ghost', 'kaynak': ['x'], 'donusum': 'dogrudan'}]}, {'eslemeler': [{'hedef': 'BusinessPartner', 'kaynak': ['yok'], 'donusum': 'dogrudan'}]}):
            self.out = self.root / 'bad'
            with self.assertRaises(native.NativeError):
                self.run_op('s4hana-mapping-plan', **SAP, source_file='kaynak.csv')

    def test_d365_cross_company_and_bc_paths(self):
        self.server.add('GET', r'/data/CustomersV3\?\$select=CustomerAccount,dataAreaId&cross-company=true', {'value': [{'CustomerAccount': 'C1', 'dataAreaId': 'usmf'}]})
        self.target('CustomerAccount,dataAreaId\nC1,usmf\n')
        self.run_op('d365-finance-reconcile', environment_url='https://d.example', entity_set='CustomersV3', fields=['CustomerAccount', 'dataAreaId'],
                    keys=['CustomerAccount', 'dataAreaId'], target_file='hedef.csv')
        self.out = self.root / 'bc'
        self.server.add('GET', r'/v2.0/t1/production/api/v2.0/companies\(c1\)/customers\?\$select=number', {'value': [{'number': 'C1'}]})
        (self.root / 'hedef.csv').write_text('number\nC1\n')
        self.run_op('business-central-reconcile', tenant_id='t1', environment='production', company_id='c1', entity_set='customers', fields=['number'], keys=['number'], target_file='hedef.csv')
        self.assertTrue(self.read('mutabakat.json')['mutabik'])
