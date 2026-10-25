"""BI product packages: vendor render/export flows on the local contract server, Engine/QMS through fakes, KPI agent validation."""
import base64
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import bi  # noqa: E402

PDF = b'%PDF-1.7 contract fixture'
TABLEAU = {'server': 'https://t.example', 'api_version': '3.24', 'site_content_url': 's'}


def tableau_signin(server):
    server.add('POST', '/api/3.24/auth/signin', {'credentials': {'token': 'tok', 'site': {'id': 'site1'}}})


class Exports(Base):
    def test_qlik_cloud_reporting_api(self):
        self.server.add('POST', '/api/v1/reports', {}, 202, {'Location': 'https://x.eu.qlikcloud.com/api/v1/reports/r1/status'})
        self.server.add('GET', '/api/v1/reports/r1/status', {'status': 'processing'})
        self.server.add('GET', '/api/v1/reports/r1/status', {'status': 'done', 'results': [{'outputId': 'datawork', 'location': 'https://x.eu.qlikcloud.com/api/v1/temp-contents/abc'}]})
        self.server.add('GET', '/api/v1/temp-contents/abc', PDF)
        with patch.object(bi.time, 'sleep'):
            self.run_op('qlik-cloud-report-export', tenant='https://x.eu.qlikcloud.com', app_id='a', sheet_id='s')
        body = self.server.received[0][2]
        self.assertEqual((body['type'], body['senseSheetTemplate']['sheet']['id'], body['output']['type']), ('sense-sheet-1.0', 's', 'pdf'))
        self.assertEqual((self.out / 'rapor.pdf').read_bytes(), PDF)

    def test_qlik_cloud_failed_report(self):
        self.server.add('POST', '/api/v1/reports', {}, 202, {'Location': '/api/v1/reports/r1/status'})
        self.server.add('GET', '/api/v1/reports/r1/status', {'status': 'failed'})
        with self.assertRaises(native.NativeError):
            self.run_op('qlik-cloud-report-export', tenant='https://x.eu.qlikcloud.com', app_id='a', sheet_id='s')
        self.assertEqual(json.loads((self.out / 'run-state.json').read_text())['report_status'], '/api/v1/reports/r1/status')

    def test_error_page_is_not_saved_as_pdf(self):
        self.server.add('POST', '/v1.0/myorg/groups/w/reports/r/ExportTo', {'id': 'e1'})
        self.server.add('GET', '/v1.0/myorg/groups/w/reports/r/exports/e1', {'status': 'Succeeded'})
        self.server.add('GET', '/v1.0/myorg/groups/w/reports/r/exports/e1/file', b'<html>login</html>')
        with self.assertRaises(native.NativeError):
            self.run_op('powerbi-service-report-export', workspace_id='w', report_id='r')
        self.assertFalse((self.out / 'rapor.pdf').exists())

    def test_powerbi_export_pages(self):
        self.server.add('POST', '/v1.0/myorg/groups/w/reports/r/ExportTo', {'id': 'e1'})
        self.server.add('GET', '/v1.0/myorg/groups/w/reports/r/exports/e1', {'status': 'Succeeded', 'percentComplete': 100})
        self.server.add('GET', '/v1.0/myorg/groups/w/reports/r/exports/e1/file', PDF)
        self.run_op('powerbi-service-report-export', workspace_id='w', report_id='r', pages=['ReportSection1'])
        self.assertEqual(self.server.received[0][2], {'format': 'PDF', 'powerBIReportConfiguration': {'pages': [{'pageName': 'ReportSection1'}]}})

    def test_tableau_view_pdf_by_name(self):
        tableau_signin(self.server)
        self.server.add('GET', r'/api/3.24/sites/site1/views\?filter=name%3Aeq%3AOzet&fields=_default_,workbook.name&pageSize=100&pageNumber=1',
                        {'views': {'view': [{'id': 'v1', 'workbook': {'name': 'Satis'}}, {'id': 'v2', 'workbook': {'name': 'Diger'}}]}, 'pagination': {'totalAvailable': 2}})
        self.server.add('GET', r'/api/3.24/sites/site1/views/v1/pdf\?type=A4&orientation=Landscape', PDF)
        self.server.add('POST', '/api/3.24/auth/signout', {})
        self.run_op('tableau-cloud-report-export', **TABLEAU, workbook_name='Satis', view_name='Ozet')
        self.assertEqual(self.server.received[1][3]['X-Tableau-Auth'], 'tok')
        self.assertTrue((self.out / 'rapor.pdf').exists())

    def test_looker_render_task(self):
        self.server.add('POST', '/api/4.0/login', {'access_token': 'lt'})
        self.server.add('POST', r'/api/4.0/render_tasks/dashboards/7/pdf\?.*pdf_paper_size=a4.*', {'id': 'rt'})
        self.server.add('GET', '/api/4.0/render_tasks/rt', {'status': 'success'})
        self.server.add('GET', '/api/4.0/render_tasks/rt/results', PDF)
        self.run_op('looker-report-export', server='https://l.example', dashboard_id='7')
        self.assertEqual(self.server.received[1][3]['Authorization'], 'token lt')

    def test_looker_studio_link(self):
        self.server.add('GET', '/bigquery/v2/projects/p/datasets/d/tables/t', {'numRows': '10', 'schema': {'fields': [{'name': 'tutar', 'type': 'NUMERIC'}]}})
        self.run_op('looker-studio-report-export', project_id='p', dataset_id='d', table_id='t', report_name='Satış raporu', template_report_id='abcdefgh1234')
        url = self.read('rapor.json')['baglanti']
        self.assertTrue(url.startswith('https://lookerstudio.google.com/reporting/create?'))
        for part in ('ds.ds0.connector=bigQuery', 'ds.ds0.tableId=t', 'c.reportId=abcdefgh1234', 'r.reportName=Sat%C4%B1%C5%9F%20raporu'):
            self.assertIn(part, url)

    def test_bo_raylight_logon_export_logoff(self):
        self.server.add('POST', '/biprws/logon/long', {}, headers={'X-SAP-LogonToken': '"tok&x"'})
        self.server.add('GET', '/biprws/raylight/v1/documents/42', PDF)
        self.server.add('POST', '/biprws/logoff', {})
        self.run_op('sap-bo-report-export', server='https://bo.example:6405', document_id='42')
        self.assertEqual(self.server.received[1][3]['Accept'], 'application/pdf')
        self.assertEqual(self.server.received[1][3]['X-Sap-Logontoken'], '"tok&x"')

    def test_sac_data_export_summary(self):
        page = {'value': [{'Date': '202601', 'SignedData': 10}, {'Date': '202601', 'SignedData': 5}, {'Date': '202602', 'SignedData': 1}]}
        self.server.add('GET', r'/api/v1/dataexport/providers/sac/M1/FactData\?\$select=Date,SignedData', page)
        self.run_op('sac-report-export', tenant_url='https://t.eu10.sapanalytics.cloud', provider_id='M1', dimensions=['Date'], measures=['SignedData'])
        self.assertIn('202601,2,15.0', self.read('rapor.csv'))

    def test_oac_workbook_export(self):
        b64 = 'L3NoYXJlZC9TYWxlcy9NeVNhbGVzV29ya2Jvb2s'
        self.server.add('POST', f'/api/20210901/catalog/workbooks/{b64}/exports', {}, 202, {'oa-work-request-id': 'wr1'})
        self.server.add('GET', '/api/20210901/workRequests/wr1', {'status': 'SUCCEEDED'})
        self.server.add('GET', f'/api/20210901/catalog/workbooks/{b64}/exports/wr1', PDF)
        self.run_op('oac-report-export', server='https://o.example', workbook_path='/shared/Sales/MySalesWorkbook', canvas_ids=['snapshot!canvas!1'])
        self.assertEqual(self.server.received[0][2]['canvasIds'], ['snapshot!canvas!1'])

    def test_microstrategy_cookie_and_base64_pdf(self):
        self.server.add('POST', '/MicroStrategyLibrary/api/auth/login', {}, 204, {'X-MSTR-AuthToken': 'mt', 'Set-Cookie': 'JSESSIONID=abc; Path=/; HttpOnly'})
        self.server.add('POST', f'/MicroStrategyLibrary/api/dossiers/{"A" * 32}/instances', {'mid': 'm1'})
        self.server.add('POST', f'/MicroStrategyLibrary/api/documents/{"A" * 32}/instances/m1/pdf', {'data': base64.b64encode(PDF).decode()})
        self.server.add('POST', '/MicroStrategyLibrary/api/auth/logout', {})
        self.run_op('microstrategy-report-export', server='https://m.example', project_id='P', dashboard_id='A' * 32)
        headers = self.server.received[1][3]
        self.assertEqual((headers['X-Mstr-Authtoken'], headers['Cookie'], headers['X-Mstr-Projectid']), ('mt', 'JSESSIONID=abc', 'P'))

    def test_superset_and_metabase_exports(self):
        self.server.add('POST', '/api/v1/security/login', {'access_token': 'st'})
        self.server.add('GET', '/api/v1/dashboard/3/charts', {'result': [{'id': 11, 'slice_name': 'Satış'}]})
        self.server.add('GET', r'/api/v1/chart/11/data/\?format=csv&type=full', b'a,b\n1,2\n')
        self.run_op('superset-report-export', server='https://s.example', dashboard_id=3)
        self.assertTrue((self.out / 'grafik_11.csv').exists())
        self.out = self.root / 'mb'
        self.server.add('GET', '/api/dashboard/4', {'name': 'Pano', 'dashcards': [{'card': {'id': 9, 'name': 'Ciro'}}, {'card': {}}]})
        self.server.add('POST', '/api/card/9/query/xlsx', b'PK\x05\x06' + b'\x00' * 18)
        self.run_op('metabase-report-export', server='https://mb.example', dashboard_id=4)
        self.assertEqual(self.server.received[-1][3]['X-Api-Key'], 'test-only-secret')


class FakeWs:
    def __init__(self, replies):
        self.replies, self.sent = replies, []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        if not self.replies:
            raise AssertionError('no reply')
        reply = self.replies.pop(0)
        if 'id' in reply and reply['id'] == 'auto':
            reply = {**reply, 'id': self.sent[-1]['id']}
        return json.dumps(reply)

    def close(self):
        pass


class Engines(Base):
    def test_qlik_sense_engine_export(self):
        ws = FakeWs([{'method': 'OnConnected'}, {'id': 'auto', 'result': {'qReturn': {'qHandle': 1}}}, {'id': 'auto', 'result': {'qReturn': {'qHandle': 2}}},
                     {'id': 'auto', 'result': {'qUrl': '/tempcontent/x/table.xlsx?serverNodeId=1'}}])
        self.server.add('GET', r'/jwt/tempcontent/x/table.xlsx\?serverNodeId=1', b'PK\x05\x06' + b'\x00' * 18)
        with patch.object(bi, 'ws_connect', return_value=ws) as connect:
            self.run_op('qlik-sense-report-export', server='https://q.example', virtual_proxy='jwt', app_id='app-1', object_ids=['obj1'])
        self.assertEqual(connect.call_args[0][0], 'wss://q.example/jwt/app/app-1')
        self.assertEqual([m['method'] for m in ws.sent], ['OpenDoc', 'GetObject', 'ExportData'])
        self.assertEqual(ws.sent[2]['handle'], 2)
        self.assertTrue((self.out / 'obj1.xlsx').exists())

    def test_engine_error_is_reported(self):
        ws = FakeWs([{'id': 'auto', 'error': {'code': 5, 'message': 'secret detail'}}])
        with patch.object(bi, 'ws_connect', return_value=ws), self.assertRaises(native.NativeError) as err:
            self.run_op('qlik-sense-report-export', server='https://q.example', app_id='a', object_ids=['o'])
        self.assertNotIn('secret', str(err.exception))

    def test_qlikview_qms_soap(self):
        calls = []

        class Response:
            def __init__(self, xml):
                self.status_code, self.content = 200, xml.encode()

        class Session:
            auth = None
            def post(self, url, data, headers, timeout, allow_redirects):
                action = headers['SOAPAction'].strip('"').rsplit('/', 1)[1]
                calls.append((action, headers.get('X-Service-Key'), data.decode()))
                body = {'GetTimeLimitedServiceKey': '<GetTimeLimitedServiceKeyResult>key1</GetTimeLimitedServiceKeyResult>',
                        'RunTask': '', 'GetTaskStatus': '<General><Status>Completed</Status></General>'}[action]
                return Response(f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><r xmlns="{bi.QMS_NS}">{body}</r></s:Body></s:Envelope>')
        fake_requests = type('requests', (), {'Session': Session})
        fake_ntlm = type(sys)('requests_ntlm'); fake_ntlm.HttpNtlmAuth = lambda u, p: (u, p)
        with patch.object(bi, 'driver', return_value=fake_requests), patch.dict(sys.modules, {'requests_ntlm': fake_ntlm}):
            self.run_op('qlikview-report-export', qms_url='https://qv.example:4799/QMS/Service', task_id='12345678-1234-1234-1234-123456789012')
        self.assertEqual([c[0] for c in calls], ['GetTimeLimitedServiceKey', 'RunTask', 'GetTimeLimitedServiceKey', 'GetTaskStatus'])
        self.assertEqual(calls[1][1], 'key1')
        self.assertEqual(self.read('qlikview-gorev.json')['durum'], 'Completed')

    def test_qlikview_requires_https_unless_explicit(self):
        with patch.object(bi, 'driver', return_value=type('requests', (), {})), patch.dict(sys.modules, {'requests_ntlm': type(sys)('requests_ntlm')}):
            sys.modules['requests_ntlm'].HttpNtlmAuth = object
            with self.assertRaises(native.NativeError):
                self.run_op('qlikview-report-export', qms_url='http://qv:4799/QMS/Service', task_id='12345678-1234-1234-1234-123456789012')


GOOD = {'kpiler': [{'ad': 'Ciro', 'tanim': 'toplam satış', 'alan': 'Tutar', 'tablo': 'Satis', 'toplama': 'sum', 'iyi_yon': 'artis', 'grain': 'ay'},
                   {'ad': 'Ortalama sepet', 'tanim': 'x', 'alan': 'Tutar', 'toplama': 'sum', 'bolen': {'alan': 'SiparisNo', 'toplama': 'count_distinct'}, 'iyi_yon': 'artis'}],
        'sayfalar': [{'ad': 'Özet', 'amac': 'yönetim', 'gorseller': [{'tur': 'kpi', 'baslik': 'Ciro', 'kpi': 'Ciro'}, {'tur': 'cubuk', 'baslik': 'Bölge', 'kpi': 'Ciro', 'boyutlar': ['Bolge']}]}],
        'sorular': []}


class KpiAgent(Base):
    def powerbi_evidence(self):
        cols = {'results': [{'tables': [{'rows': [{'[Table]': 'Satis', '[Name]': 'Tutar', '[DataType]': 'Decimal'}, {'[Table]': 'Satis', '[Name]': 'SiparisNo', '[DataType]': 'Text'},
                                                  {'[Table]': 'Satis', '[Name]': 'Bolge', '[DataType]': 'Text'}]}]}]}
        self.server.add('POST', '/v1.0/myorg/groups/w/datasets/d/executeQueries', cols)
        self.server.add('POST', '/v1.0/myorg/groups/w/datasets/d/executeQueries', {'results': [{'tables': [{'rows': []}]}]})

    def test_powerbi_kpi_dax_is_generated_by_code(self):
        self.powerbi_evidence()
        bad = json.loads(json.dumps(GOOD)); bad['sayfalar'][0]['gorseller'][1]['boyutlar'] = ['Sehir']
        with model_reply(bad, GOOD) as chat:
            self.run_op('powerbi-service-kpi-design', workspace_id='w', dataset_id='d')
        self.assertIn('Sehir', chat.call_args_list[1][0][3][-1]['content'])
        kpis = {k['ad']: k['ifade'] for k in self.read('kpi_tasarimi.json')['kpiler']}
        self.assertEqual(kpis['Ciro'], "SUM('Satis'[Tutar])")
        self.assertEqual(kpis['Ortalama sepet'], "DIVIDE(SUM('Satis'[Tutar]), DISTINCTCOUNT('Satis'[SiparisNo]))")
        self.assertIn('INFO.VIEW.COLUMNS()', self.server.received[0][2]['queries'][0]['query'])

    def test_desktop_tmdl_tableau_twb_and_qlikview_prj(self):
        tables = self.root / 'Rapor.SemanticModel/definition/tables'
        tables.mkdir(parents=True)
        (tables / 'Satis.tmdl').write_text("table Satis\n\tcolumn Tutar\n\t\tdataType: decimal\n\tcolumn SiparisNo\n\t\tdataType: string\n\tcolumn Bolge\n\t\tdataType: string\n"
                                            "\tmeasure 'Toplam Tutar' = SUM(Satis[Tutar])\n", encoding='utf-8')
        with model_reply(GOOD):
            self.run_op('powerbi-desktop-kpi-design', semantic_model_dir=str(self.root / 'Rapor.SemanticModel'))
        self.assertEqual(len(self.read('kanit.json')['alanlar']), 4)
        (self.root / 'p.twb').write_text('<workbook><datasources><datasource name="ds1" caption="Satis"><column name="[Tutar]" datatype="real" role="measure"/>'
                                         '<column name="[SiparisNo]" datatype="string" role="dimension"/><column name="[Bolge]" datatype="string" role="dimension"/></datasource></datasources></workbook>')
        self.out = self.root / 't'
        with model_reply(GOOD):
            self.run_op('tableau-desktop-kpi-design', workbook_file=str(self.root / 'p.twb'))
        self.assertIn('COUNTD([SiparisNo])', json.dumps(self.read('kpi_tasarimi.json'), ensure_ascii=False))
        prj = self.root / 'Belge-prj'; prj.mkdir()
        (prj / 'LoadScript.txt').write_text('Satis:\nLOAD Tutar, SiparisNo, Upper(bolge) as Bolge\nFROM [lib://x/satis.qvd] (qvd);\n', encoding='utf-8')
        self.out = self.root / 'qv'
        with model_reply(GOOD):
            self.run_op('qlikview-kpi-design', prj_folder=str(prj))
        self.assertIn('Count(DISTINCT([SiparisNo]))', json.dumps(self.read('kpi_tasarimi.json'), ensure_ascii=False))

    def test_oac_requires_column_list(self):
        for kind in ('workbooks', 'datasets', 'subjectAreas'):
            self.server.add('GET', r'/api/20210901/catalog\?type=' + kind, [])
        with self.assertRaises(native.NativeError):
            self.run_op('oac-kpi-design', server='https://o.example')
