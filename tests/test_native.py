"""Actual HTTP requests to a local contract server, never labelled vendor E2E tests."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'library'));sys.path.insert(0,str(ROOT/'catalog'))
import native
from native_catalog import OPERATIONS


def archive(filename):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(filename,'contract fixture, not a real vendor artifact')
    return out.getvalue()


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.output=Path(self.temp.name)/'run'
        self.queue=[];self.received=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle_request(self):
                length=int(self.headers.get('Content-Length',0));raw=self.rfile.read(length)
                payload=json.loads(raw) if raw else None
                owner.received.append((self.command,self.path,payload,dict(self.headers)))
                if not owner.queue:
                    self.send_response(500);self.end_headers();return
                expected=owner.queue.pop(0)
                if (self.command,self.path)!=(expected['method'],expected['path']):
                    owner.errors.append((self.command,self.path,expected));self.send_response(500);self.end_headers();return
                if 'payload' in expected and payload!=expected['payload']:owner.errors.append(('payload',payload,expected['payload']))
                for k,v in expected.get('request_headers',{}).items():
                    if self.headers.get(k)!=v:owner.errors.append(('header',k,self.headers.get(k),v))
                response=expected.get('body',{})
                data=response if isinstance(response,bytes) else json.dumps(response).encode()
                self.send_response(expected.get('status',200))
                headers={'Content-Type':'application/octet-stream' if isinstance(response,bytes) else 'application/json',**expected.get('headers',{})}
                for k,v in headers.items():self.send_header(k,v)
                self.end_headers();self.wfile.write(data)
            do_GET=do_POST=handle_request
        self.errors=[];self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.env=patch.dict(os.environ,{k:'test-only-secret' for op in OPERATIONS for k in op['secrets']});self.env.start()
        self.env2=patch.dict(os.environ,{'MODEL':'test','PROVIDER':'openai'});self.env2.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.env2.stop();self.env.stop();self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def factory(self,base,headers=None):
        return native.HTTP(f'http://127.0.0.1:{self.server.server_port}'+urlsplit(base).path,headers,allow_test_http=True)
    def step(self,method,path,body=None,**extra):self.queue.append(dict(method=method,path=path,body={} if body is None else body,**extra))
    def run_op(self,op,**cfg):return native.execute({'operation':op,**cfg},self.output,self.factory)
    def qlik_export(self,app='app'):
        self.step('POST',f'/api/v1/apps/{app}/export',status=201,headers={'Location':'/api/v1/temp-contents/file'})
        self.step('GET','/api/v1/temp-contents/file',b'QVF test bytes; NOT engine-generated')
    def test_qlik_export(self):
        self.qlik_export();r=self.run_op('qlik-export',tenant='https://test.eu.qlikcloud.com',app_id='app')
        self.assertEqual(r['artifact']['file'],'artifact.qvf');self.assertFalse(self.queue)
        self.assertEqual(self.received[0][3]['Authorization'],'Bearer test-only-secret')
    def test_qlik_template_reload_then_qvf(self):
        self.step('POST','/api/v1/apps/template/copy',{'attributes':{'id':'copy'}},payload={'attributes':{'name':'Report'}})
        self.step('POST','/api/v1/reloads',{'id':'reload'},payload={'appId':'copy'})
        self.step('GET','/api/v1/reloads/reload',{'status':'RELOADING'})
        self.step('GET','/api/v1/reloads/reload',{'status':'SUCCEEDED'})
        self.qlik_export('copy')
        with patch.object(native.time,'sleep'):self.run_op('qlik-template',tenant='https://test.eu.qlikcloud.com',app_id='template',name='Report')
        self.assertFalse(self.queue);self.assertEqual(json.loads((self.output/'run-state.json').read_text())['created_app_id'],'copy')
    def test_failed_reload_never_exports(self):
        self.step('POST','/api/v1/apps/t/copy',{'attributes':{'id':'c'}})
        self.step('POST','/api/v1/reloads',{'id':'r'})
        self.step('GET','/api/v1/reloads/r',{'status':'FAILED'})
        with self.assertRaises(native.NativeError):self.run_op('qlik-template',tenant='https://test.qlikcloud.com',app_id='t',name='N')
        self.assertFalse((self.output/'manifest.json').exists());self.assertEqual(len(self.received),3)
    def test_qlik_cross_origin_export_is_rejected(self):
        self.step('POST','/api/v1/apps/a/export',status=201,headers={'Location':'https://external.example/steal'})
        with self.assertRaises(native.NativeError):self.run_op('qlik-export',tenant='https://test.qlikcloud.com',app_id='a')
        self.assertEqual(len(self.received),1)
    def test_powerbi_pbix(self):
        self.step('GET','/v1.0/myorg/groups/w/reports/r/Export',archive('Report/Layout'))
        self.assertEqual(self.run_op('powerbi-export',workspace_id='w',report_id='r')['artifact']['file'],'artifact.pbix')
    def test_powerbi_invalid_artifact_not_renamed_pbix(self):
        self.step('GET','/v1.0/myorg/groups/w/reports/r/Export',b'<Report/>')
        with self.assertRaises(native.NativeError):self.run_op('powerbi-export',workspace_id='w',report_id='r')
        self.assertFalse((self.output/'artifact.pbix').exists())
    def test_tableau_pat_download_and_logout(self):
        self.step('POST','/api/3.24/auth/signin',{'credentials':{'token':'session','site':{'id':'site'}}},payload={'credentials':{'personalAccessTokenName':'test-only-secret','personalAccessTokenSecret':'test-only-secret','site':{'contentUrl':''}}})
        self.step('GET','/api/3.24/sites/site/workbooks/w/content?includeExtract=true',archive('report.twb'),request_headers={'X-Tableau-Auth':'session'})
        self.step('POST','/api/3.24/auth/signout',b'',status=204)
        r=self.run_op('tableau-export',server='https://tableau.example',api_version='3.24',workbook_id='w')
        self.assertEqual(r['artifact']['file'],'artifact.twbx');self.assertFalse(self.queue)
    def test_sap_b1_session_metadata_logout(self):
        self.step('POST','/b1s/v2/Login',{'SessionId':'session'},payload={'CompanyDB':'company','UserName':'test-only-secret','Password':'test-only-secret'})
        self.step('GET','/b1s/v2/$metadata',b'<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx"/>',request_headers={'Cookie':'B1SESSION=session'})
        self.step('POST','/b1s/v2/Logout',b'',status=204)
        self.assertEqual(self.run_op('sap-b1-schema',service_url='https://sap.example/b1s/v2/',company_db='company')['artifact']['file'],'service.edmx')
    def test_sap_odata_metadata(self):
        self.step('GET','/sap/opu/odata/sap/SERVICE/$metadata',b'<Edmx/>')
        self.run_op('sap-odata-schema',service_url='https://sap.example/sap/opu/odata/sap/SERVICE/')
        self.assertFalse(self.queue)
    def test_oracle_describe(self):
        self.step('GET','/fscmRestApi/resources/11.13.18.05/describe',{'Resources':{'items':{}}})
        self.assertEqual(self.run_op('oracle-schema',server='https://oracle.example',api_version='11.13.18.05')['artifact']['file'],'fusion-describe.json')
    def test_bigquery_table_dryrun_job_and_poll(self):
        sql=Path(self.temp.name)/'query.sql';sql.write_text('SELECT 1')
        check={'statistics':{'query':{'statementType':'SELECT','totalBytesProcessed':'20'}}}
        self.step('POST','/bigquery/v2/projects/p/jobs',check)
        self.step('POST','/bigquery/v2/projects/p/jobs',{'jobReference':{'jobId':'datawork_fixed'}})
        self.step('GET','/bigquery/v2/projects/p/jobs/datawork_fixed?location=EU',{'status':{'state':'DONE'},'jobReference':{'jobId':'datawork_fixed'}})
        with patch.object(native.uuid,'uuid4') as uid:
            uid.return_value.hex='fixed'
            self.run_op('bigquery-table',project_id='p',location='EU',sql_file=str(sql),destination_dataset='d',destination_table='t',maximum_bytes_billed=50)
        job=self.received[1][2];self.assertEqual(job['configuration']['query']['writeDisposition'],'WRITE_EMPTY')
        self.assertEqual(job['configuration']['query']['maximumBytesBilled'],'50');self.assertFalse(self.queue)
    def test_bigquery_budget_and_sql_type_block_before_write(self):
        sql=Path(self.temp.name)/'query.sql';sql.write_text('SELECT 1')
        self.step('POST','/bigquery/v2/projects/p/jobs',{'statistics':{'query':{'statementType':'SELECT','totalBytesProcessed':'1000'}}})
        with self.assertRaises(native.NativeError):self.run_op('bigquery-table',project_id='p',location='EU',sql_file=str(sql),maximum_bytes_billed=10)
        self.assertEqual(len(self.received),1)
    def test_bigquery_rejects_ddl_even_when_api_dryrun_accepts_syntax(self):
        sql=Path(self.temp.name)/'query.sql';sql.write_text('CREATE TABLE example AS SELECT 1')
        self.step('POST','/bigquery/v2/projects/p/jobs',{'statistics':{'query':{'statementType':'CREATE_TABLE_AS_SELECT'}}})
        with self.assertRaises(native.NativeError):self.run_op('bigquery-table',project_id='p',location='EU',sql_file=str(sql),maximum_bytes_billed=100)
        self.assertEqual(len(self.received),1);self.assertFalse((self.output/'manifest.json').exists())
    def test_bigquery_done_with_error_is_not_success(self):
        sql=Path(self.temp.name)/'query.sql';sql.write_text('SELECT 1')
        self.step('POST','/bigquery/v2/projects/p/jobs',{'statistics':{'query':{'statementType':'SELECT'}}})
        self.step('POST','/bigquery/v2/projects/p/jobs',{})
        self.step('GET','/bigquery/v2/projects/p/jobs/datawork_failed?location=EU',{'status':{'state':'DONE','errorResult':{'reason':'accessDenied'}}})
        with patch.object(native.uuid,'uuid4') as uid:
            uid.return_value.hex='failed'
            with self.assertRaises(native.NativeError):self.run_op('bigquery-table',project_id='p',location='EU',sql_file=str(sql),maximum_bytes_billed=100,destination_dataset='d',destination_table='t')
        self.assertFalse((self.output/'bigquery-table.json').exists());self.assertFalse((self.output/'manifest.json').exists())
        self.assertTrue((self.output/'run-state.json').exists())
    def test_tableau_logout_also_on_download_failure(self):
        self.step('POST','/api/3.24/auth/signin',{'credentials':{'token':'session','site':{'id':'site'}}})
        self.step('GET','/api/3.24/sites/site/workbooks/w/content?includeExtract=true',status=403)
        self.step('POST','/api/3.24/auth/signout',b'',status=204)
        with self.assertRaises(native.NativeError):self.run_op('tableau-export',server='https://tableau.example',api_version='3.24',workbook_id='w')
        self.assertFalse(self.queue);self.assertFalse((self.output/'manifest.json').exists())
    def test_agent_api_schema_model_sql_and_platform_validation(self):
        context=Path(self.temp.name)/'context.md';context.write_text('Count rows')
        self.step('GET','/bigquery/v2/projects/p/datasets/d/tables/t',{'tableReference':{'projectId':'p','datasetId':'d','tableId':'t'},'schema':{'fields':[{'name':'id','type':'INTEGER'}]}})
        self.step('POST','/bigquery/v2/projects/p/jobs',{'statistics':{'query':{'statementType':'SELECT'}}})
        import agent
        with patch.object(agent,'complete',return_value='SELECT COUNT(*) FROM `p.d.t`') as model:
            result=self.run_op('bigquery-agent',project_id='p',location='EU',dataset_id='d',table_id='t',context_file=str(context))
        self.assertIn('INTEGER',model.call_args.args[2]);self.assertEqual(result['artifact']['file'],'query.sql')
        self.assertTrue(self.received[-1][2]['configuration']['dryRun']);self.assertFalse(self.queue)
    def test_no_secret_no_request(self):
        with patch.dict(os.environ,{'POWERBI_ACCESS_TOKEN':''}):
            with self.assertRaises(native.NativeError):self.run_op('powerbi-export',workspace_id='w',report_id='r')
        self.assertEqual(self.received,[])
    def test_transport_does_not_follow_redirects_or_retry_posts(self):
        client=self.factory('https://api.example',{'Authorization':'Bearer test-only-secret'})
        self.step('GET','/file',status=302,headers={'Location':'https://external.example'})
        with self.assertRaises(native.NativeError):client.request('GET','/file')
        self.step('POST','/write',status=503)
        with self.assertRaises(native.NativeError):client.json('POST','/write',{})
        self.assertEqual(len(self.received),2)
    def test_get_rate_limit_retries(self):
        self.step('GET','/data',status=429,headers={'Retry-After':'0'});self.step('GET','/data',{'ok':True})
        self.assertTrue(self.factory('https://api.example').json('GET','/data')['ok'])
    def test_https_and_output_guards(self):
        with self.assertRaises(native.NativeError):native.HTTP('http://example.com')
        with self.assertRaises(native.NativeError):native.HTTP('https://user:password@example.com')
        self.output.mkdir();(self.output/'keep').write_text('keep')
        with self.assertRaises(native.NativeError):self.run_op('powerbi-export')
        self.assertEqual((self.output/'keep').read_text(),'keep')


if __name__=='__main__':unittest.main()
