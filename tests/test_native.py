"""Actual HTTP requests to a local contract server, never labelled vendor E2E tests."""
import io
import ast
import base64
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
                try:payload=json.loads(raw) if raw else None
                except ValueError:payload=raw  # file uploads, forms and multipart bodies stay bytes
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
                if callable(response):response=response()
                data=response if isinstance(response,bytes) else json.dumps(response).encode()
                self.send_response(expected.get('status',200))
                headers={'Content-Type':'application/octet-stream' if isinstance(response,bytes) else 'application/json',**expected.get('headers',{})}
                for k,v in headers.items():self.send_header(k,v)
                self.end_headers();self.wfile.write(data)
            do_GET=do_POST=do_PUT=do_PATCH=handle_request
        self.errors=[];self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01},daemon=True);self.thread.start()
        self.env=patch.dict(os.environ,{k:'test-only-secret' for op in OPERATIONS for k in op['secrets']});self.env.start()
        self.env2=patch.dict(os.environ,{'MODEL':'test','PROVIDER':'openai'});self.env2.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.env2.stop();self.env.stop();self.temp.cleanup()
        self.assertEqual(self.errors,[])
    def factory(self,base,headers=None):
        return native.HTTP(f'http://127.0.0.1:{self.server.server_port}'+urlsplit(base).path,headers,allow_test_http=True)
    def step(self,method,path,body=None,**extra):self.queue.append(dict(method=method,path=path,body={} if body is None else body,**extra))
    def run_op(self,op,**cfg):return native.execute({'operation':op,**cfg},self.output,self.factory)
    def test_dataform_create_compilation_pins_commit_and_reads_back(self):
        parent='projects/p/locations/l/repositories/r';name=parent+'/compilationResults/c';sha='a'*40
        result={'name':name,'resolvedGitCommitSha':sha,'internalMetadata':'PRIVATE_SENTINEL'}
        self.step('POST','/v1/'+parent+'/compilationResults',result,payload={'gitCommitish':sha},request_headers={'Authorization':'Bearer test-only-secret'})
        self.step('GET','/v1/'+name,result)
        self.run_op('dataform-compile',repository=parent,git_commit_sha=sha.upper())
        self.assertFalse(self.queue)
        receipt=json.loads((self.output/'dataform-compilation.json').read_text())
        self.assertEqual(receipt,{'name':name,'resolvedGitCommitSha':sha,'state':'compiled'})
        self.assertNotIn('PRIVATE_SENTINEL',''.join(p.read_text() for p in self.output.iterdir()))

    def test_dataform_compile_invalid_source_never_calls_api(self):
        for parent,sha in [('projects/p/locations/l/repositories/r','main'),('projects/p/locations/l/repositories/r','a'*8),('https://other.example','a'*40)]:
            with self.subTest(parent=parent,sha=sha):
                self.output=Path(self.temp.name)/('invalid-'+str(len(list(Path(self.temp.name).iterdir()))))
                with self.assertRaises(native.NativeError):self.run_op('dataform-compile',repository=parent,git_commit_sha=sha)
        self.assertFalse(self.received)

    def test_dataform_compile_errors_or_wrong_commit_fail_closed(self):
        parent='projects/p/locations/l/repositories/r';name=parent+'/compilationResults/c';sha='a'*40
        bad=[{'resolvedGitCommitSha':'b'*40},{'compilationErrors':[{'message':'PRIVATE_SENTINEL'}]},{'compilationErrors':{}},{'name':'projects/other/locations/l/repositories/r/compilationResults/c'}]
        for index,change in enumerate(bad):
            with self.subTest(change=change):
                self.output=Path(self.temp.name)/str(index)
                self.step('POST','/v1/'+parent+'/compilationResults',{'name':name,'resolvedGitCommitSha':sha,**change})
                with self.assertRaises(native.NativeError):self.run_op('dataform-compile',repository=parent,git_commit_sha=sha)
                self.assertFalse((self.output/'manifest.json').exists())
                self.assertNotIn('PRIVATE_SENTINEL',(self.output/'run-state.json').read_text())
        self.assertEqual(len(self.received),4)

    def test_dataform_compile_readback_mismatch_keeps_created_id(self):
        parent='projects/p/locations/l/repositories/r';name=parent+'/compilationResults/c';sha='a'*40
        self.step('POST','/v1/'+parent+'/compilationResults',{'name':name,'resolvedGitCommitSha':sha})
        self.step('GET','/v1/'+name,{'name':name,'resolvedGitCommitSha':'b'*40})
        with self.assertRaises(native.NativeError):self.run_op('dataform-compile',repository=parent,git_commit_sha=sha)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['compilation_result'],name)
        self.assertFalse((self.output/'dataform-compilation.json').exists())

    def test_dataform_compile_ambiguous_write_never_retries(self):
        parent='projects/p/locations/l/repositories/r'
        self.step('POST','/v1/'+parent+'/compilationResults',status=503)
        with self.assertRaises(native.NativeError):self.run_op('dataform-compile',repository=parent,git_commit_sha='a'*40)
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'compilation_pending')

    def test_checkpoint_replace_failure_preserves_previous_run_identity(self):
        self.output.mkdir()
        native.journal(self.output,run_id=123,state='running',label='Türkçe')
        before=(self.output/'run-state.json').read_bytes()
        with patch.object(native.os,'replace',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):native.journal(self.output,state='success')
        self.assertEqual((self.output/'run-state.json').read_bytes(),before)
        self.assertEqual(list(self.output.glob('*.tmp')),[])
        native.journal(self.output,state='success')
        self.assertEqual(json.loads((self.output/'run-state.json').read_text(encoding='utf-8')),{'run_id':123,'state':'success','label':'Türkçe'})

    def test_checkpoint_flush_failure_does_not_truncate_previous_state(self):
        self.output.mkdir();native.journal(self.output,run_id=123,state='running')
        with patch.object(native.os,'fsync',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):native.journal(self.output,state='success')
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'running')
        self.assertEqual(list(self.output.glob('*.tmp')),[])

    def test_transport_rejects_nonobject_json_without_exposing_body(self):
        client=self.factory('https://example.test')
        for raw in [b'null',b'[]',b'"PRIVATE_SENTINEL"',b'13',b'{invalid PRIVATE_SENTINEL']:
            self.step('GET','/shape',raw)
            with self.assertRaises(native.NativeError) as error:client.json('GET','/shape')
            self.assertNotIn('PRIVATE_SENTINEL',str(error.exception))

    def test_cli_malformed_nested_response_does_not_print_trace_or_secret(self):
        config=Path(self.temp.name)/'config.json';config.write_text('{"operation":"airbyte-monitor"}')
        for error in [AttributeError('PRIVATE_SENTINEL'),TypeError('PRIVATE_SENTINEL')]:
            stderr=io.StringIO()
            with patch.object(sys,'argv',['native.py','--config',str(config),'--execute']),patch.object(sys,'stderr',stderr),patch.object(native,'execute',side_effect=error),patch('agent.load_env'):
                with self.assertRaises(SystemExit) as stopped:native.main()
            self.assertEqual(stopped.exception.code,1)
            self.assertNotIn('PRIVATE_SENTINEL',stderr.getvalue());self.assertNotIn('Traceback',stderr.getvalue())

    FABRIC_WORKSPACE='11111111-1111-4111-8111-111111111111'
    FABRIC_ITEM='22222222-2222-4222-8222-222222222222'
    FABRIC_OPERATION='33333333-3333-4333-8333-333333333333'
    def fabric_setup(self):
        template=next(op for op in OPERATIONS if op['operation']=='fabric-notebook')['input_files']['notebook.ipynb']
        self.fabric_document=json.loads(template)
        ast.parse(''.join(self.fabric_document['cells'][0]['source']))
        self.fabric_file=Path(self.temp.name)/'input.ipynb';self.fabric_file.write_text(template,encoding='utf-8')
        self.fabric_path='/v1/workspaces/'+self.FABRIC_WORKSPACE+'/notebooks'
        self.fabric_item={'id':self.FABRIC_ITEM,'workspaceId':self.FABRIC_WORKSPACE,'type':'Notebook','displayName':'My notebook'}
        self.fabric_payload={'displayName':'My notebook','definition':{'format':'ipynb','parts':[{'path':'notebook-content.ipynb','payloadType':'InlineBase64','payload':base64.b64encode(json.dumps(self.fabric_document,ensure_ascii=False).encode()).decode()}]}}
    def fabric_execute(self):
        return self.run_op('fabric-notebook',workspace_id=self.FABRIC_WORKSPACE,display_name='My notebook',notebook_file=str(self.fabric_file))
    def fabric_export(self,document=None):
        return {'definition':{'parts':[{'path':'notebook-content.ipynb','payloadType':'InlineBase64','payload':base64.b64encode(json.dumps(document or self.fabric_document).encode()).decode()},{'path':'.platform','payloadType':'InlineBase64','payload':'ignored metadata'}]}}
    def fabric_accept(self,path,payload=None,operation=None):
        operation=operation or self.FABRIC_OPERATION
        self.step('POST',path,status=202,payload=payload,headers={'Location':'https://api.fabric.microsoft.com/v1/operations/'+operation,'x-ms-operation-id':operation,'Retry-After':'1'})

    def test_fabric_creates_reads_back_and_exports_native_notebook(self):
        self.fabric_setup()
        self.step('POST',self.fabric_path,self.fabric_item,status=201,payload=self.fabric_payload,request_headers={'Authorization':'Bearer test-only-secret'})
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        self.step('POST',self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',self.fabric_export(),payload=None)
        result=self.fabric_execute()
        self.assertFalse(self.queue);self.assertEqual(result['artifact']['file'],'notebook.ipynb')
        self.assertEqual(json.loads((self.output/'notebook.ipynb').read_text()),self.fabric_document)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['execution'],'not_run_by_package')

    def test_fabric_async_creation_and_export_wait_for_success(self):
        self.fabric_setup();self.fabric_accept(self.fabric_path,self.fabric_payload)
        route='/v1/operations/'+self.FABRIC_OPERATION
        self.step('GET',route,{'status':'Running'},headers={'Retry-After':'3'})
        self.step('GET',route,{'status':'Succeeded'})
        self.step('GET',route+'/result',self.fabric_item)
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        operation='44444444-4444-4444-8444-444444444444';export_route='/v1/operations/'+operation
        self.fabric_accept(self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',operation=operation)
        self.step('GET',export_route,{'status':'Succeeded'})
        self.step('GET',export_route+'/result',self.fabric_export())
        with patch.object(native.time,'sleep') as sleep:self.fabric_execute()
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[1,3,1]);self.assertFalse(self.queue)
        state=json.loads((self.output/'run-state.json').read_text());self.assertEqual(state['create_operation_id'],self.FABRIC_OPERATION);self.assertEqual(state['export_operation_id'],operation)

    def test_fabric_failed_operation_does_not_fetch_result_or_expose_error(self):
        self.fabric_setup();self.fabric_accept(self.fabric_path,self.fabric_payload)
        self.step('GET','/v1/operations/'+self.FABRIC_OPERATION,{'status':'Failed','error':{'message':'PRIVATE_SENTINEL'}})
        with patch.object(native.time,'sleep'),self.assertRaises(native.NativeError) as error:self.fabric_execute()
        self.assertNotIn('PRIVATE_SENTINEL',str(error.exception));self.assertFalse((self.output/'manifest.json').exists())
        self.assertEqual(len(self.received),2)

    def test_fabric_wrong_location_rejected_without_following(self):
        self.fabric_setup()
        self.step('POST',self.fabric_path,status=202,headers={'Location':'https://other.example/','x-ms-operation-id':self.FABRIC_OPERATION})
        with self.assertRaises(native.NativeError):self.fabric_execute()
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['create_operation_id'],self.FABRIC_OPERATION)

    def test_fabric_wrong_workspace_or_content_never_delivered(self):
        self.fabric_setup()
        self.step('POST',self.fabric_path,{**self.fabric_item,'workspaceId':'wrong'},status=201)
        with self.assertRaises(native.NativeError):self.fabric_execute()
        self.assertEqual(len(self.received),1)
        self.output=Path(self.temp.name)/'changed'
        self.step('POST',self.fabric_path,self.fabric_item,status=201)
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        changed=json.loads(json.dumps(self.fabric_document));changed['cells'][0]['source']=['print("changed")']
        self.step('POST',self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',self.fabric_export(changed))
        with self.assertRaises(native.NativeError):self.fabric_execute()
        self.assertFalse((self.output/'notebook.ipynb').exists())

    def test_fabric_execution_outputs_rejected_before_upload(self):
        self.fabric_setup();self.fabric_document['cells'][0]['outputs']=[{'data':{'text/plain':'PRIVATE_SENTINEL'}}]
        self.fabric_file.write_text(json.dumps(self.fabric_document))
        with self.assertRaises(native.NativeError):self.fabric_execute()
        self.assertFalse(self.received)

    def test_fabric_post503_not_retried_and_timeout_keeps_operation(self):
        self.fabric_setup();self.step('POST',self.fabric_path,status=503)
        with self.assertRaises(native.NativeError):self.fabric_execute()
        self.assertEqual(len(self.received),1)
        self.output=Path(self.temp.name)/'timeout';self.fabric_accept(self.fabric_path,self.fabric_payload)
        for _ in range(120):self.step('GET','/v1/operations/'+self.FABRIC_OPERATION,{'status':'Running'})
        with patch.object(native.time,'sleep'),self.assertRaisesRegex(native.NativeError,'zaman aşımı'):self.fabric_execute()
        self.assertFalse(self.queue);self.assertFalse((self.output/'manifest.json').exists())
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['create_operation_id'],self.FABRIC_OPERATION)

    def test_fabric_unknown_state_or_changed_operation_rejected(self):
        self.fabric_setup()
        for index,(body,headers) in enumerate([({'status':'PRIVATE_SENTINEL'},{}),({'status':'Succeeded'},{'x-ms-operation-id':self.FABRIC_ITEM})]):
            self.output=Path(self.temp.name)/('bad-state-'+str(index));self.fabric_accept(self.fabric_path,self.fabric_payload)
            self.step('GET','/v1/operations/'+self.FABRIC_OPERATION,body,headers=headers)
            with patch.object(native.time,'sleep'),self.assertRaises(native.NativeError):self.fabric_execute()
            self.assertNotIn('PRIVATE_SENTINEL',(self.output/'run-state.json').read_text())
            self.assertFalse((self.output/'manifest.json').exists())

    def test_fabric_malformed_export_not_saved_as_notebook(self):
        self.fabric_setup()
        parts_cases=[[],[{'path':'notebook.ipynb','payloadType':'InlineBase64','payload':'%%%'}],[{'path':'notebook.ipynb','payloadType':'InlineBase64','payload':base64.b64encode(b'{"error":"PRIVATE_SENTINEL"}').decode()}],['invalid']]
        for index,parts in enumerate(parts_cases):
            self.output=Path(self.temp.name)/('bad-export-'+str(index))
            self.step('POST',self.fabric_path,self.fabric_item,status=201)
            self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
            self.step('POST',self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',{'definition':{'parts':parts}})
            with self.assertRaises(native.NativeError):self.fabric_execute()
            self.assertFalse((self.output/'notebook.ipynb').exists())
            self.assertEqual(json.loads((self.output/'run-state.json').read_text())['notebook_id'],self.FABRIC_ITEM)

    def test_resume_maps_supported_platforms_without_loading_checkpoint_secrets_or_urls(self):
        for start,(monitor,context,ids) in native.RESUME_OPERATIONS.items():
            with self.subTest(operation=start):
                cfg={'operation':start,'server':'https://trusted.example',**{key:'original-'+key for key in context}}
                state={**{key:cfg[key] for key in context},**{key:123 for key in ids},'server':'https://untrusted.example','operation':'qlik-template','access_token':'PRIVATE_SENTINEL'}
                resumed=native.resume_config(cfg,state)
                self.assertEqual(resumed['operation'],monitor);self.assertEqual(resumed['server'],cfg['server'])
                self.assertNotIn('access_token',resumed);self.assertEqual(cfg['operation'],start)
                for key in ids:self.assertEqual(resumed[key],123)
                self.assertEqual(native.resume_config(resumed,state),resumed)

    def test_resume_rejects_missing_ids_wrong_source_and_unsupported_operations(self):
        cfg={'operation':'databricks-run','job_id':17}
        for state in [{'job_id':18,'run_id':42},{'job_id':17},{'job_id':17,'run_id':True},{'job_id':17,'run_id':-1}]:
            with self.assertRaises(native.NativeError):native.resume_config(cfg,state)
        with self.assertRaises(native.NativeError):native.resume_config({'operation':'fabric-notebook'},{})
        self.assertFalse(self.received)

    def test_cli_resume_plan_is_offline_and_reports_monitor_effects(self):
        config=Path(self.temp.name)/'config.json';config.write_text('{"operation":"databricks-run","job_id":17}')
        checkpoint=Path(self.temp.name)/'run-state.json';checkpoint.write_text('{"job_id":17,"run_id":42}')
        contract=Path(self.temp.name)/'contract.json';contract.write_text('{"operation":"databricks-run","effects":"START NEW RUN"}')
        stdout=io.StringIO()
        with patch.object(sys,'argv',['native.py','--config',str(config),'--plan','--resume-from',str(checkpoint)]),patch.object(sys,'stdout',stdout),patch('agent.load_env'):
            native.main()
        plan=json.loads(stdout.getvalue())
        self.assertEqual(plan['operation'],'databricks-monitor');self.assertEqual(plan['network_calls'],0)
        self.assertNotIn('START NEW RUN',stdout.getvalue());self.assertFalse(self.received)

    def test_cli_resume_executes_only_existing_databricks_run(self):
        config=Path(self.temp.name)/'config.json';config.write_text('{"operation":"databricks-run","job_id":17,"workspace_url":"https://workspace.example"}')
        checkpoint=Path(self.temp.name)/'run-state.json';checkpoint.write_text('{"job_id":17,"run_id":42}')
        self.step('GET','/api/2.2/jobs/runs/get?run_id=42',{'job_id':17,'run_id':42,'state':{'life_cycle_state':'TERMINATED','result_state':'SUCCESS'}})
        original=native.execute;stdout=io.StringIO()
        with patch.object(sys,'argv',['native.py','--config',str(config),'--execute','--resume-from',str(checkpoint),'--output',str(self.output)]),patch.object(sys,'stdout',stdout),patch('agent.load_env'),patch.object(native,'execute',side_effect=lambda cfg,out:original(cfg,out,self.factory)):
            native.main()
        self.assertEqual(json.loads(stdout.getvalue())['operation'],'databricks-monitor')
        self.assertEqual([request[0] for request in self.received],['GET'])
        self.assertEqual(json.loads(checkpoint.read_text()),{'job_id':17,'run_id':42})

    def test_fabric_existing_notebook_export_never_creates_or_runs(self):
        self.fabric_setup()
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        self.step('POST',self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',self.fabric_export(),payload=None)
        self.run_op('fabric-notebook-export',workspace_id=self.FABRIC_WORKSPACE,notebook_id=self.FABRIC_ITEM)
        self.assertFalse(self.queue);self.assertEqual(len(self.received),2)
        self.assertEqual(json.loads((self.output/'notebook.ipynb').read_text()),self.fabric_document)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'exported')

    def test_fabric_export_wrong_item_and_invalid_uuid_block_definition_call(self):
        self.fabric_setup()
        with self.assertRaises(native.NativeError):self.run_op('fabric-notebook-export',workspace_id=self.FABRIC_WORKSPACE,notebook_id='../invalid')
        self.assertFalse(self.received)
        self.output=Path(self.temp.name)/'wrong-item'
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,{**self.fabric_item,'type':'Lakehouse'})
        with self.assertRaises(native.NativeError):self.run_op('fabric-notebook-export',workspace_id=self.FABRIC_WORKSPACE,notebook_id=self.FABRIC_ITEM)
        self.assertEqual(len(self.received),1)

    def test_fabric_existing_export_rejects_cell_outputs(self):
        self.fabric_setup();self.fabric_document['cells'][0]['outputs']=[{'output_type':'stream','text':'PRIVATE_SENTINEL'}]
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        self.step('POST',self.fabric_path+'/'+self.FABRIC_ITEM+'/getDefinition?format=ipynb',self.fabric_export())
        with self.assertRaises(native.NativeError):self.run_op('fabric-notebook-export',workspace_id=self.FABRIC_WORKSPACE,notebook_id=self.FABRIC_ITEM)
        self.assertFalse((self.output/'notebook.ipynb').exists())
        self.assertNotIn('PRIVATE_SENTINEL',(self.output/'run-state.json').read_text())

    def fabric_job_start(self,**headers):
        self.fabric_setup()
        self.fabric_job_path='/v1/workspaces/'+self.FABRIC_WORKSPACE+'/items/'+self.FABRIC_ITEM+'/jobs/'
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        self.step('POST',self.fabric_job_path+'RunNotebook/instances',status=202,payload=None,headers={'Location':'https://api.fabric.microsoft.com'+self.fabric_job_path+'instances/'+self.FABRIC_OPERATION,'Retry-After':'4',**headers})
    def fabric_job_status(self,state='Completed',**extra):
        self.step('GET',self.fabric_job_path+'instances/'+self.FABRIC_OPERATION,{'id':self.FABRIC_OPERATION,'itemId':self.FABRIC_ITEM,'jobType':'RunNotebook','status':state,**extra},headers={'Retry-After':'3'})
    def fabric_job_execute(self,monitor=False):
        return self.run_op('fabric-notebook-monitor' if monitor else 'fabric-notebook-run',workspace_id=self.FABRIC_WORKSPACE,notebook_id=self.FABRIC_ITEM,**({'job_instance_id':self.FABRIC_OPERATION} if monitor else {}))

    def test_fabric_notebook_run_respects_retry_after_and_omits_exit_value(self):
        self.fabric_job_start();self.fabric_job_status('InProgress');self.fabric_job_status(exitValue='PRIVATE_SENTINEL')
        with patch.object(native.time,'sleep') as sleep:self.fabric_job_execute()
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[4,3]);self.assertFalse(self.queue)
        self.assertNotIn('PRIVATE_SENTINEL',''.join(p.read_text() for p in self.output.iterdir()))

    def test_fabric_job_fail_cancel_deduped_are_not_success(self):
        for status in ('Failed','Cancelled','Deduped'):
            self.output=Path(self.temp.name)/status;self.fabric_job_start();self.fabric_job_status(status,failureReason={'message':'PRIVATE_SENTINEL'})
            with patch.object(native.time,'sleep'),self.assertRaises(native.NativeError):self.fabric_job_execute()
            self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
            self.assertNotIn('PRIVATE_SENTINEL',(self.output/'run-state.json').read_text())

    def test_fabric_job_identity_failure_blocks_delivery(self):
        for index,extra in enumerate([{'id':self.FABRIC_ITEM},{'itemId':self.FABRIC_OPERATION},{'jobType':'OtherJob'},{'failureReason':{'message':'failure'}}]):
            self.output=Path(self.temp.name)/str(index);self.fabric_job_start();self.fabric_job_status(**extra)
            with patch.object(native.time,'sleep'),self.assertRaises(native.NativeError):self.fabric_job_execute()
            self.assertFalse((self.output/'fabric-notebook-run.json').exists())

    def test_fabric_job_untrusted_location_never_followed(self):
        self.fabric_job_start(Location='https://other.example/jobs/'+self.FABRIC_OPERATION)
        with self.assertRaises(native.NativeError):self.fabric_job_execute()
        self.assertEqual(len(self.received),2)

    def test_fabric_job_ambiguous_post_is_not_retried(self):
        self.fabric_setup()
        self.step('GET',self.fabric_path+'/'+self.FABRIC_ITEM,self.fabric_item)
        self.step('POST','/v1/workspaces/'+self.FABRIC_WORKSPACE+'/items/'+self.FABRIC_ITEM+'/jobs/RunNotebook/instances',status=503)
        with self.assertRaises(native.NativeError):self.fabric_job_execute()
        self.assertEqual(len(self.received),2);self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')

    def test_fabric_job_timeout_then_resume_only_gets_existing_instance(self):
        self.fabric_job_start()
        for _ in range(120):self.fabric_job_status('InProgress')
        with patch.object(native.time,'sleep'),self.assertRaisesRegex(native.NativeError,'zaman aşımı'):self.fabric_job_execute()
        checkpoint=json.loads((self.output/'run-state.json').read_text());self.assertEqual(checkpoint['job_instance_id'],self.FABRIC_OPERATION)
        self.assertFalse((self.output/'manifest.json').exists());before=len(self.received)
        self.output=Path(self.temp.name)/'continued';self.fabric_job_status()
        cfg=native.resume_config({'operation':'fabric-notebook-run','workspace_id':self.FABRIC_WORKSPACE,'notebook_id':self.FABRIC_ITEM},checkpoint)
        native.execute(cfg,self.output,self.factory)
        self.assertEqual([call[0] for call in self.received[before:]],['GET'])
        self.assertTrue((self.output/'fabric-notebook-run.json').exists())

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

    def test_databricks_generates_imports_and_downloads_profile_notebook(self):
        self.step('GET','/api/2.0/workspace/get-status?path=%2FShared',{'object_type':'DIRECTORY','path':'/Shared'})
        self.step('POST','/api/2.0/workspace/import',{})
        target='/Shared/datawork_profile_fixed'
        self.step('GET','/api/2.0/workspace/get-status?path=%2FShared%2Fdatawork_profile_fixed',{'object_type':'NOTEBOOK','language':'PYTHON','path':target,'object_id':12})
        self.step('GET','/api/2.0/workspace/export?path=%2FShared%2Fdatawork_profile_fixed&format=SOURCE',lambda:{'content':self.received[1][2]['content']})
        with patch.object(native.uuid,'uuid4') as uid:
            uid.return_value.hex='fixed'
            result=self.run_op('databricks-profile',workspace_url='https://workspace.example',workspace_directory='/Shared',source_table='main.sales.orders')
        payload=self.received[1][2];self.assertFalse(payload['overwrite']);self.assertEqual(payload['language'],'PYTHON')
        source=(self.output/'profile_notebook.py').read_text();ast.parse(source)
        self.assertIn("'main.sales.orders'",source);self.assertIn('isNull()',source)
        self.assertEqual(source,base64.b64decode(payload['content']).decode());self.assertEqual(result['artifact']['file'],'profile_notebook.py')
        self.assertEqual(len(self.received),4);self.assertFalse(self.queue)

    def test_databricks_bad_table_is_rejected_without_request(self):
        with self.assertRaises(native.NativeError):self.run_op('databricks-profile',source_table="a.b.c'; DROP TABLE t")
        self.assertFalse(self.received)

    def test_databricks_import_failure_does_not_export(self):
        self.step('GET','/api/2.0/workspace/get-status?path=%2FShared',{'object_type':'DIRECTORY'})
        self.step('POST','/api/2.0/workspace/import',status=403)
        with self.assertRaises(native.NativeError):self.run_op('databricks-profile',workspace_url='https://workspace.example',workspace_directory='/Shared',source_table='main.sales.orders')
        self.assertEqual(len(self.received),2);self.assertFalse((self.output/'profile_notebook.py').exists())
        self.assertTrue((self.output/'run-state.json').exists())

    def test_adf_deploy_new_pipeline_and_read_back(self):
        path='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f/pipelines/datawork_fixed'
        definition={'properties':{'activities':[{'name':'Wait','type':'Wait','typeProperties':{'waitTimeInSeconds':1}}]}}
        config_file=Path(self.temp.name)/'pipeline.json';config_file.write_text(json.dumps(definition))
        response={'id':path,'name':'datawork_fixed',**definition}
        self.step('PUT',path+'?api-version=2018-06-01',response,payload=definition,request_headers={'Authorization':'Bearer test-only-secret'})
        self.step('GET',path+'?api-version=2018-06-01',response)
        with patch.object(native.uuid,'uuid4') as uid:
            uid.return_value.hex='fixed'
            self.run_op('adf-deploy',subscription_id='s',resource_group='g',factory_name='f',pipeline_file=str(config_file))
        self.assertFalse(self.queue);self.assertEqual(len(self.received),2)
        self.assertEqual(json.loads((self.output/'pipeline.json').read_text())['id'],path)

    def test_adf_empty_definition_never_publishes(self):
        source=Path(self.temp.name)/'pipeline.json';source.write_text('{"properties":{"activities":[]}}')
        with self.assertRaises(native.NativeError):self.run_op('adf-deploy',subscription_id='s',resource_group='g',factory_name='f',pipeline_file=str(source))
        self.assertFalse(self.received)

    def test_adf_pipeline_run_waits_for_succeeded(self):
        source=Path(self.temp.name)/'parameters.json';source.write_text('{"period":"2026-10"}')
        base='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f'
        self.step('POST',base+'/pipelines/p/createRun?api-version=2018-06-01',{'runId':'r'},payload={'period':'2026-10'})
        self.step('GET',base+'/pipelineruns/r?api-version=2018-06-01',{'status':'InProgress','runId':'r','pipelineName':'p'})
        self.step('GET',base+'/pipelineruns/r?api-version=2018-06-01',{'status':'Succeeded','runId':'r','pipelineName':'p'})
        with patch.object(native.time,'sleep'):self.run_op('adf-run',subscription_id='s',resource_group='g',factory_name='f',pipeline_name='p',parameters_file=str(source))
        self.assertFalse(self.queue);self.assertTrue((self.output/'pipeline-run.json').exists())

    def test_adf_failed_run_has_no_success_artifact(self):
        source=Path(self.temp.name)/'params.json';source.write_text('{}')
        base='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f'
        self.step('POST',base+'/pipelines/p/createRun?api-version=2018-06-01',{'runId':'r'})
        self.step('GET',base+'/pipelineruns/r?api-version=2018-06-01',{'status':'Failed','runId':'r','pipelineName':'p'})
        with self.assertRaises(native.NativeError):self.run_op('adf-run',subscription_id='s',resource_group='g',factory_name='f',pipeline_name='p',parameters_file=str(source))
        self.assertFalse((self.output/'pipeline-run.json').exists());self.assertTrue((self.output/'run-state.json').exists())

    def test_adf_ambiguous_start_records_pending_and_does_not_retry(self):
        source=Path(self.temp.name)/'params.json';source.write_text('{}')
        base='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f'
        self.step('POST',base+'/pipelines/p/createRun?api-version=2018-06-01',status=503)
        with self.assertRaises(native.NativeError):self.run_op('adf-run',subscription_id='s',resource_group='g',factory_name='f',pipeline_name='p',parameters_file=str(source))
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(state['state'],'request_pending');self.assertEqual(state['pipeline_name'],'p')
        self.assertEqual(len(self.received),1);self.assertFalse((self.output/'manifest.json').exists())

    def test_airflow_v3_body_run_and_success(self):
        source=Path(self.temp.name)/'conf.json';source.write_text('{"region":"EU"}')
        self.step('POST','/api/v2/dags/sales/dagRuns',{'dag_id':'sales','dag_run_id':'datawork_fixed'},payload={'dag_run_id':'datawork_fixed','logical_date':None,'conf':{'region':'EU'}})
        self.step('GET','/api/v2/dags/sales/dagRuns/datawork_fixed',{'state':'running','dag_id':'sales','dag_run_id':'datawork_fixed'})
        self.step('GET','/api/v2/dags/sales/dagRuns/datawork_fixed',{'state':'success','dag_id':'sales','dag_run_id':'datawork_fixed'})
        with patch.object(native.uuid,'uuid4') as uid,patch.object(native.time,'sleep'):
            uid.return_value.hex='fixed'
            self.run_op('airflow-run',server='https://airflow.example',api_version='3.2',dag_id='sales',conf_file=str(source))
        self.assertFalse(self.queue);self.assertTrue((self.output/'dag-run.json').exists())

    def test_airflow_v2_rejected_before_network(self):
        with self.assertRaises(native.NativeError):self.run_op('airflow-run',api_version='2.10')
        self.assertFalse(self.received)

    def test_airflow_failed_run_not_delivered_as_success(self):
        source=Path(self.temp.name)/'conf.json';source.write_text('{}')
        self.step('POST','/api/v2/dags/d/dagRuns',{'dag_id':'d','dag_run_id':'datawork_fixed'})
        self.step('GET','/api/v2/dags/d/dagRuns/datawork_fixed',{'state':'failed'})
        with patch.object(native.uuid,'uuid4') as uid:
            uid.return_value.hex='fixed'
            with self.assertRaises(native.NativeError):self.run_op('airflow-run',server='https://airflow.example',api_version='3.2',dag_id='d',conf_file=str(source))
        self.assertFalse((self.output/'dag-run.json').exists())

    def test_sap_quotation_draft_create_readback_logout(self):
        document={'DocObjectCode':'23','CardCode':'C001','DocumentLines':[{'ItemCode':'I001','Quantity':2,'UnitPrice':30}]}
        source=Path(self.temp.name)/'document.json';source.write_text(json.dumps(document))
        self.step('POST','/b1s/v2/Login',{'SessionId':'s'})
        self.step('POST','/b1s/v2/Drafts',{'DocEntry':12},payload=document,request_headers={'Cookie':'B1SESSION=s'})
        self.step('GET','/b1s/v2/Drafts(12)',{'DocEntry':12,**document})
        self.step('POST','/b1s/v2/Logout',b'',status=204)
        self.run_op('sap-quotation-draft',service_url='https://sap.example/b1s/v2/',company_db='c',document_file=str(source))
        self.assertEqual(json.loads((self.output/'sap-quotation-draft.json').read_text())['DocEntry'],12)
        self.assertFalse(self.queue);self.assertEqual(len(self.received),4)

    def test_sap_nonquotation_document_rejected_before_login(self):
        source=Path(self.temp.name)/'document.json';source.write_text('{"DocObjectCode":"13"}')
        with self.assertRaises(native.NativeError):self.run_op('sap-quotation-draft',document_file=str(source))
        self.assertFalse(self.received)

    def test_sap_failed_create_logs_out_without_retry(self):
        document={'DocObjectCode':'23','CardCode':'C','DocumentLines':[{'ItemCode':'I','Quantity':1,'UnitPrice':0}]}
        source=Path(self.temp.name)/'document.json';source.write_text(json.dumps(document))
        self.step('POST','/b1s/v2/Login',{'SessionId':'s'})
        self.step('POST','/b1s/v2/Drafts',status=400)
        self.step('POST','/b1s/v2/Logout',b'',status=204)
        with self.assertRaises(native.NativeError):self.run_op('sap-quotation-draft',service_url='https://sap.example/b1s/v2/',company_db='c',document_file=str(source))
        self.assertFalse((self.output/'manifest.json').exists());self.assertEqual(len(self.received),3);self.assertFalse(self.queue)


    def dbt_start(self):
        self.step('POST','/api/v2/accounts/1/jobs/2/run/',{'data':{'id':3}},request_headers={'Authorization':'Token test-only-secret'})
    def dbt_status(self,status,**overrides):
        self.step('GET','/api/v2/accounts/1/runs/3/',{'data':{'id':3,'job_id':2,'account_id':1,'status':status,**overrides}})
    def dbt_execute(self):
        return self.run_op('dbt-run',server='https://cloud.getdbt.com',account_id='1',job_id='2')
    def test_dbt_waits_and_downloads_native_artifact(self):
        self.dbt_start();self.dbt_status(3);self.dbt_status(10)
        artifact={'metadata':{'dbt_schema_version':'https://schemas.getdbt.com/dbt/run-results/v6.json'},'results':[{'unique_id':'test.a','status':'pass'}]}
        self.step('GET','/api/v2/accounts/1/runs/3/artifacts/run_results.json',artifact)
        with patch.object(native.time,'sleep'):result=self.dbt_execute()
        self.assertEqual(result['artifact']['file'],'run_results.json')
        self.assertEqual(json.loads((self.output/'run_results.json').read_text()),artifact)
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(self.received[0][2],{'cause':state['cause']});self.assertFalse(self.queue)
    def test_dbt_failed_or_cancelled_has_no_artifact(self):
        for status in (20,30):
            with self.subTest(status=status):
                self.output=Path(self.temp.name)/str(status)
                self.dbt_start();self.dbt_status(status)
                with self.assertRaises(native.NativeError):self.dbt_execute()
                self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_dbt_wrong_job_identity_is_rejected(self):
        self.dbt_start();self.dbt_status(10,job_id=4)
        with self.assertRaises(native.NativeError):self.dbt_execute()
        self.assertEqual(len(self.received),2)
    def test_dbt_missing_artifact_is_not_success(self):
        self.dbt_start();self.dbt_status(10)
        self.step('GET','/api/v2/accounts/1/runs/3/artifacts/run_results.json',status=404)
        with self.assertRaises(native.NativeError):self.dbt_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_dbt_error_body_is_not_native_artifact(self):
        self.dbt_start();self.dbt_status(10)
        self.step('GET','/api/v2/accounts/1/runs/3/artifacts/run_results.json',{'status':{'is_success':False}})
        with self.assertRaises(native.NativeError):self.dbt_execute()
        self.assertFalse((self.output/'run_results.json').exists())
    def test_dbt_ambiguous_post_preserves_cause_and_does_not_retry(self):
        self.step('POST','/api/v2/accounts/1/jobs/2/run/',status=503)
        with self.assertRaises(native.NativeError):self.dbt_execute()
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')

    PREFECT_DEPLOYMENT='00000000-0000-0000-0000-000000000001'
    PREFECT_BASE='/api/accounts/a/workspaces/w/'
    def prefect_execute(self):
        source=Path(self.temp.name)/'parameters.json';source.write_text('{"days":7}')
        return self.run_op('prefect-run',api_url='https://api.prefect.cloud'+self.PREFECT_BASE,deployment_id=self.PREFECT_DEPLOYMENT,parameters_file=str(source))
    def prefect_start(self,**extra):
        self.step('POST',self.PREFECT_BASE+'deployments/'+self.PREFECT_DEPLOYMENT+'/create_flow_run',{'id':'run','deployment_id':self.PREFECT_DEPLOYMENT},request_headers={'Authorization':'Bearer test-only-secret'},**extra)
    def prefect_status(self,state,**overrides):
        self.step('GET',self.PREFECT_BASE+'flow_runs/run',{'id':'run','deployment_id':self.PREFECT_DEPLOYMENT,'state':{'type':state},'parameters':{'secret':'must-not-be-saved'},**overrides})
    def test_prefect_cloud_path_payload_and_completed_receipt(self):
        self.prefect_start();self.prefect_status('RUNNING');self.prefect_status('COMPLETED')
        with patch.object(native.time,'sleep'):result=self.prefect_execute()
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(self.received[0][2],{'parameters':{'days':7},'state':{'type':'SCHEDULED'},'idempotency_key':state['idempotency_key']})
        self.assertEqual(result['artifact']['file'],'flow-run.json')
        self.assertNotIn('must-not-be-saved',(self.output/'flow-run.json').read_text());self.assertFalse(self.queue)
    def test_prefect_terminal_failures_are_not_success(self):
        for status in ('FAILED','CRASHED','CANCELLED'):
            with self.subTest(status=status):
                self.output=Path(self.temp.name)/status
                self.prefect_start();self.prefect_status(status)
                with self.assertRaises(native.NativeError):self.prefect_execute()
                self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_prefect_wrong_run_is_rejected(self):
        self.prefect_start();self.prefect_status('COMPLETED',id='another-run')
        with self.assertRaises(native.NativeError):self.prefect_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_prefect_post_unauthorized_is_not_retried(self):
        self.prefect_start(status=401)
        with self.assertRaises(native.NativeError):self.prefect_execute()
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')
    def test_prefect_timeout_leaves_run_trace(self):
        self.prefect_start()
        for _ in range(120):self.prefect_status('SCHEDULED')
        with patch.object(native.time,'sleep'),self.assertRaisesRegex(native.NativeError,'zaman aşımına'):
            self.prefect_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['flow_run_id'],'run')
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)


    SF_UUID='00000000-0000-0000-0000-000000000001'
    SF_PATH='/api/v2/statements?async=true&requestId='+SF_UUID
    def sf_execute(self,**overrides):
        cfg=dict(server='https://a.snowflakecomputing.com',source_table='DB.PUBLIC.SOURCE',target_schema='DB.QUALITY',columns=['ID','VALUE'],warehouse='WH',role='ANALYST',timeout_seconds=60)
        cfg.update(overrides)
        with patch.object(native.uuid,'uuid4',return_value=native.uuid.UUID(self.SF_UUID)),patch.object(native.time,'sleep'):
            return self.run_op('snowflake-quality',**cfg)
    def sf_result(self,handle='create',**extra):
        return {'statementHandle':handle,'code':'090001','sqlState':'00000',**extra}
    def sf_counts(self,**extra):
        return self.sf_result('check',resultSetMetaData={'numRows':1,'rowType':[{'name':n} for n in ['ROW_COUNT','NULL_0','NULL_1']]},data=[['10','0','2']],**extra)
    def test_snowflake_creates_native_view_then_queries_with_oauth(self):
        self.step('POST',self.SF_PATH,{'statementHandle':'create'},status=202,request_headers={'Authorization':'Bearer test-only-secret','X-Snowflake-Authorization-Token-Type':'OAUTH'})
        self.step('GET','/api/v2/statements/create',self.sf_result())
        self.step('POST',self.SF_PATH,self.sf_counts())
        result=self.sf_execute()
        receipt=json.loads((self.output/'snowflake-quality.json').read_text())
        self.assertEqual(receipt['null_counts'],{'ID':0,'VALUE':2})
        sql=(self.output/'quality-view.sql').read_text()
        self.assertTrue(sql.startswith('CREATE VIEW "DB"."QUALITY"."DATAWORK_QUALITY_'))
        self.assertIn('COUNT(*) - COUNT("VALUE") AS "NULL_1"',sql)
        self.assertNotIn('OR REPLACE',sql)
        self.assertEqual(self.received[0][2],{'statement':sql.strip().rstrip(';'),'timeout':60,'warehouse':'WH','role':'ANALYST'})
        self.assertEqual(self.received[2][2]['statement'],'SELECT * FROM '+receipt['view_name'])
        self.assertEqual(result['artifact']['file'],'snowflake-quality.json');self.assertFalse(self.queue)
    def test_snowflake_rejects_injected_identifier_before_network(self):
        with self.assertRaises(native.NativeError):self.sf_execute(columns=['ID); DROP TABLE X;--'])
        self.assertFalse(self.received)
    def test_snowflake_failed_ddl_does_not_query_or_retry(self):
        self.step('POST',self.SF_PATH,status=422)
        with self.assertRaises(native.NativeError):self.sf_execute()
        self.assertEqual(len(self.received),1);self.assertFalse((self.output/'manifest.json').exists())
        state=json.loads((self.output/'run-state.json').read_text());self.assertIn('create_request_id',state)
    def test_snowflake_failed_check_preserves_created_view(self):
        self.step('POST',self.SF_PATH,self.sf_result())
        self.step('POST',self.SF_PATH,status=422)
        with self.assertRaises(native.NativeError):self.sf_execute()
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(state['create_statement_handle'],'create');self.assertIn('view_name',state)
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_snowflake_different_poll_handle_fails(self):
        self.step('POST',self.SF_PATH,{'statementHandle':'create'},status=202)
        self.step('GET','/api/v2/statements/create',self.sf_result('wrong'))
        with self.assertRaises(native.NativeError):self.sf_execute()
        self.assertEqual(len(self.received),2)
    def test_snowflake_error_in_http200_is_not_success(self):
        self.step('POST',self.SF_PATH,self.sf_result(code='123456'))
        with self.assertRaises(native.NativeError):self.sf_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_snowflake_incomplete_results_are_not_delivered(self):
        self.step('POST',self.SF_PATH,self.sf_result())
        result=self.sf_counts();result['data']=[]
        self.step('POST',self.SF_PATH,result)
        with self.assertRaises(native.NativeError):self.sf_execute()
        self.assertFalse((self.output/'snowflake-quality.json').exists())
    def test_snowflake_timeout_keeps_statement_for_recovery(self):
        self.step('POST',self.SF_PATH,{'statementHandle':'create'},status=202)
        for _ in range(120):self.step('GET','/api/v2/statements/create',{'statementHandle':'create'},status=202)
        with self.assertRaisesRegex(native.NativeError,'zaman aşımı'):self.sf_execute()
        self.assertFalse(self.queue);self.assertFalse((self.output/'manifest.json').exists())


    GH_BASE='/repos/owner/repo/actions'
    def gh_execute(self):
        source=Path(self.temp.name)/'inputs.json';source.write_text('{"dataset":"sales"}')
        with patch.object(native.time,'sleep'):
            return self.run_op('github-actions-run',owner='owner',repo='repo',workflow_id='data.yml',ref='main',inputs_file=str(source))
    def gh_workflow(self,state='active'):
        self.step('GET',self.GH_BASE+'/workflows/data.yml',{'id':5,'state':state},request_headers={'Authorization':'Bearer test-only-secret','X-GitHub-Api-Version':'2026-03-10'})
    def gh_start(self,**extra):
        self.step('POST',self.GH_BASE+'/workflows/data.yml/dispatches',{'workflow_run_id':10,'run_url':'https://untrusted.example/ignored'},payload={'ref':'main','inputs':{'dataset':'sales'}},**extra)
    def gh_status(self,status='completed',conclusion='success',**overrides):
        self.step('GET',self.GH_BASE+'/runs/10',{'id':10,'workflow_id':5,'repository':{'full_name':'owner/repo'},'event':'workflow_dispatch','status':status,'conclusion':conclusion,'head_sha':'abc',**overrides})
    def test_github_dispatch_tracks_exact_run_and_receipt(self):
        self.gh_workflow();self.gh_start();self.gh_status('queued',None);self.gh_status()
        self.assertEqual(self.gh_execute()['artifact']['file'],'workflow-run.json')
        result=json.loads((self.output/'workflow-run.json').read_text())
        self.assertEqual(result['run_id'],10);self.assertEqual(result['html_url'],'https://github.com/owner/repo/actions/runs/10')
        self.assertNotIn('dataset',result);self.assertFalse(self.queue)
    def test_github_disabled_workflow_never_dispatches(self):
        self.gh_workflow('disabled_manually')
        with self.assertRaises(native.NativeError):self.gh_execute()
        self.assertEqual(len(self.received),1)
    def test_github_legacy204_is_not_success_or_redispatched(self):
        self.gh_workflow();self.gh_start(status=204)
        with self.assertRaises(native.NativeError):self.gh_execute()
        self.assertEqual(len(self.received),2);self.assertFalse((self.output/'manifest.json').exists())
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'dispatch_pending')
    def test_github_failed_conclusions_never_produce_success(self):
        for conclusion in ('failure','cancelled','timed_out','skipped','neutral','action_required',None):
            with self.subTest(conclusion=conclusion):
                self.output=Path(self.temp.name)/str(conclusion)
                self.gh_workflow();self.gh_start();self.gh_status(conclusion=conclusion)
                with self.assertRaises(native.NativeError):self.gh_execute()
                self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_github_wrong_repository_or_run_is_rejected(self):
        for i,override in enumerate([{'id':11},{'workflow_id':6},{'repository':{'full_name':'other/repo'}},{'event':'push'}]):
            self.output=Path(self.temp.name)/str(i)
            self.gh_workflow();self.gh_start();self.gh_status(**override)
            with self.assertRaises(native.NativeError):self.gh_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_github_dispatch_503_not_retried(self):
        self.gh_workflow();self.gh_start(status=503)
        with self.assertRaises(native.NativeError):self.gh_execute()
        self.assertEqual(len(self.received),2);self.assertFalse(self.queue)
    def test_github_timeout_retains_run_id(self):
        self.gh_workflow();self.gh_start()
        for _ in range(120):self.gh_status('waiting',None)
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.gh_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['run_id'],10)
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)


    def ft_details(self,success='2026-10-01T00:00:00Z',failed=None,**overrides):
        data={'id':'c','schedule_type':'manual','paused':False,'status':{'setup_state':'connected','sync_state':'scheduled','is_historical_sync':False},'succeeded_at':success,'failed_at':failed,'config':{'password':'source-password-not-for-disk'},**overrides}
        self.step('GET','/v1/connections/c',{'code':'Success','data':data})
    def ft_start(self,result='STARTED',**extra):
        self.step('POST','/v1/connections/c/sync',{'code':'Success','data':{'result':result}},payload={'force':False},request_headers={'Accept':'application/json;version=2','Authorization':'Basic '+base64.b64encode(b'test-only-secret:test-only-secret').decode()},**extra)
    def ft_execute(self):
        with patch.object(native.time,'sleep'):return self.run_op('fivetran-sync',connection_id='c')
    def test_fivetran_waits_for_new_success_and_omits_credentials(self):
        self.ft_details();self.ft_start();self.ft_details();self.ft_details('2026-10-02T00:00:00Z')
        self.assertEqual(self.ft_execute()['artifact']['file'],'fivetran-sync.json')
        self.assertEqual(len(self.received),4);self.assertFalse(self.queue)
        for file in self.output.iterdir():
            self.assertNotIn('source-password-not-for-disk',file.read_text())
            self.assertNotIn('test-only-secret',file.read_text())
    def test_fivetran_unsafe_preconditions_never_trigger(self):
        for i,override in enumerate([{'schedule_type':'auto'},{'paused':True},{'status':{'setup_state':'connected','sync_state':'syncing','is_historical_sync':False}},{'status':{'setup_state':'connected','sync_state':'scheduled','is_historical_sync':True}}]):
            self.output=Path(self.temp.name)/str(i);self.ft_details(**override)
            with self.assertRaises(native.NativeError):self.ft_execute()
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)
    def test_fivetran_nonstarted_responses_are_not_success(self):
        for result in ('PAUSED','INCOMPLETE','ALREADY_RUNNING','NOT_STARTED'):
            self.output=Path(self.temp.name)/result;self.ft_details();self.ft_start(result)
            with self.assertRaises(native.NativeError):self.ft_execute()
            self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_fivetran_new_failure_is_not_success(self):
        self.ft_details();self.ft_start();self.ft_details(failed='2026-10-02T00:00:00Z')
        with self.assertRaises(native.NativeError):self.ft_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_fivetran_old_success_times_out(self):
        self.ft_details();self.ft_start()
        for _ in range(120):self.ft_details()
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.ft_execute()
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_fivetran_wrong_connection_response_rejected(self):
        self.ft_details();self.ft_start();self.ft_details('2026-10-02T00:00:00Z',id='wrong')
        with self.assertRaises(native.NativeError):self.ft_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_fivetran_post503_not_retried(self):
        self.ft_details();self.ft_start(status=503)
        with self.assertRaises(native.NativeError):self.ft_execute()
        self.assertEqual(len(self.received),2)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')
    def test_fivetran_timestamp_requires_timezone(self):
        self.ft_details('2026-10-01T00:00:00')
        with self.assertRaises(native.NativeError):self.ft_execute()
        self.assertEqual(len(self.received),1)


    AB_CONNECTION='00000000-0000-0000-0000-000000000001'
    def ab_response(self,status='running',**overrides):
        return {'jobId':7,'connectionId':self.AB_CONNECTION,'jobType':'sync','status':status,**overrides}
    def ab_start(self,**extra):
        self.step('POST','/v1/jobs',self.ab_response(),payload={'connectionId':self.AB_CONNECTION,'jobType':'sync'},request_headers={'Authorization':'Bearer test-only-secret'},**extra)
    def ab_execute(self,**overrides):
        with patch.object(native.time,'sleep'):
            return self.run_op('airbyte-sync',**{'connection_id':self.AB_CONNECTION,**overrides})
    def test_airbyte_sync_polls_exact_job_and_keeps_only_counters(self):
        self.ab_start();self.step('GET','/v1/jobs/7',self.ab_response('queued'))
        self.step('GET','/v1/jobs/7',self.ab_response('succeeded',rowsSynced=0,bytesSynced=0,config={'password':'must-not-persist'}))
        self.assertEqual(self.ab_execute()['artifact']['file'],'airbyte-job.json')
        result=json.loads((self.output/'airbyte-job.json').read_text())
        self.assertEqual(result['rowsSynced'],0);self.assertNotIn('config',result);self.assertFalse(self.queue)
    def test_airbyte_failures_never_report_success(self):
        for status in ('incomplete','failed','cancelled'):
            self.output=Path(self.temp.name)/status;self.ab_start()
            self.step('GET','/v1/jobs/7',self.ab_response(status))
            with self.assertRaises(native.NativeError):self.ab_execute()
            self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_airbyte_wrong_job_connection_or_type_rejected(self):
        for i,overrides in enumerate([{'jobId':8},{'connectionId':'other'},{'jobType':'reset'}]):
            self.output=Path(self.temp.name)/str(i);self.ab_start()
            self.step('GET','/v1/jobs/7',self.ab_response('succeeded',**overrides))
            with self.assertRaises(native.NativeError):self.ab_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_airbyte_invalid_connection_never_calls_api(self):
        with self.assertRaises(native.NativeError):self.ab_execute(connection_id='invalid')
        self.assertFalse(self.received)
    def test_airbyte_post_failure_is_not_retried(self):
        self.ab_start(status=503)
        with self.assertRaises(native.NativeError):self.ab_execute()
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')
    def test_airbyte_expired_token_keeps_job_id(self):
        self.ab_start();self.step('GET','/v1/jobs/7',status=401)
        with self.assertRaises(native.NativeError):self.ab_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['job_id'],7)
        self.assertFalse((self.output/'manifest.json').exists())
    def test_airbyte_timeout_never_cancels_or_retriggers(self):
        self.ab_start()
        for _ in range(120):self.step('GET','/v1/jobs/7',self.ab_response())
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.ab_execute()
        self.assertEqual(sum(r[0]=='POST' for r in self.received),1);self.assertFalse(self.queue)
    def test_airbyte_unknown_state_and_invalid_counter_rejected(self):
        for i,response in enumerate([self.ab_response('unexpected'),self.ab_response('succeeded',rowsSynced=-1)]):
            self.output=Path(self.temp.name)/str(i);self.ab_start();self.step('GET','/v1/jobs/7',response)
            with self.assertRaises(native.NativeError):self.ab_execute()
            self.assertFalse((self.output/'manifest.json').exists())


    DF_PARENT='projects/p/locations/europe-west1/repositories/r'
    def df_compilation(self,**overrides):
        self.step('GET','/v1/'+self.DF_PARENT+'/compilationResults/c',{'name':self.DF_PARENT+'/compilationResults/c',**overrides},request_headers={'Authorization':'Bearer test-only-secret'})
    def df_start(self,**extra):
        self.step('POST','/v1/'+self.DF_PARENT+'/workflowInvocations',{'name':self.DF_PARENT+'/workflowInvocations/run'},payload={'compilationResult':self.DF_PARENT+'/compilationResults/c'},**extra)
    def df_status(self,state='SUCCEEDED',**overrides):
        self.step('GET','/v1/'+self.DF_PARENT+'/workflowInvocations/run',{'name':self.DF_PARENT+'/workflowInvocations/run','compilationResult':self.DF_PARENT+'/compilationResults/c','state':state,**overrides})
    def df_execute(self,**overrides):
        with patch.object(native.time,'sleep'):
            return self.run_op('dataform-run',**{'compilation_result':self.DF_PARENT+'/compilationResults/c',**overrides})
    def test_dataform_compilation_then_invocation_and_poll(self):
        self.df_compilation();self.df_start();self.df_status('RUNNING');self.df_status(internalMetadata='must-not-persist')
        self.assertEqual(self.df_execute()['artifact']['file'],'dataform-invocation.json')
        self.assertNotIn('must-not-persist',(self.output/'dataform-invocation.json').read_text());self.assertFalse(self.queue)
    def test_dataform_compilation_errors_block_execution(self):
        self.df_compilation(compilationErrors=[{'message':'SQL error'}])
        with self.assertRaises(native.NativeError):self.df_execute()
        self.assertEqual(len(self.received),1)
    def test_dataform_failure_and_cancellation_not_success(self):
        for status in ('FAILED','CANCELLED'):
            self.output=Path(self.temp.name)/status;self.df_compilation();self.df_start();self.df_status(status)
            with self.assertRaises(native.NativeError):self.df_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_dataform_wrong_compilation_response_rejected(self):
        self.df_compilation();self.df_start();self.df_status(compilationResult=self.DF_PARENT+'/compilationResults/other')
        with self.assertRaises(native.NativeError):self.df_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_dataform_post503_not_retried(self):
        self.df_compilation();self.df_start(status=503)
        with self.assertRaises(native.NativeError):self.df_execute()
        self.assertEqual(len(self.received),2)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')
    def test_dataform_invalid_resource_never_calls_api(self):
        with self.assertRaises(native.NativeError):self.df_execute(compilation_result=self.DF_PARENT+'/compilationResults/../other')
        self.assertFalse(self.received)
    def test_dataform_timeout_keeps_invocation(self):
        self.df_compilation();self.df_start()
        for _ in range(120):self.df_status('RUNNING')
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.df_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['workflow_invocation'],self.DF_PARENT+'/workflowInvocations/run')
        self.assertFalse(self.queue);self.assertFalse((self.output/'manifest.json').exists())


    PBI_ID='00000000-0000-0000-0000-000000000001'
    PBI_PATH='/v1.0/myorg/groups/'+PBI_ID+'/datasets/'+PBI_ID+'/refreshes'
    def pbi_start(self,location=None,status=202):
        self.step('POST',self.PBI_PATH,b'',status=status,headers={'x-ms-request-id':self.PBI_ID,'Location':location or 'https://api.powerbi.com'+self.PBI_PATH+'/'+self.PBI_ID},payload={'type':'full','commitMode':'transactional','retryCount':0},request_headers={'Authorization':'Bearer test-only-secret'})
    def pbi_status(self,state='Completed',extended='Completed',status=200):
        self.step('GET',self.PBI_PATH+'/'+self.PBI_ID,{'status':state,'extendedStatus':extended,'serviceExceptionJson':'do-not-save'},status=status)
    def pbi_execute(self):
        with patch.object(native.time,'sleep'):
            return self.run_op('powerbi-refresh',workspace_id=self.PBI_ID,dataset_id=self.PBI_ID)
    def test_powerbi_refresh_waits_for_completed_not_accepted(self):
        self.pbi_start();self.pbi_status('Unknown','InProgress',202);self.pbi_status()
        self.assertEqual(self.pbi_execute()['artifact']['file'],'powerbi-refresh.json')
        self.assertNotIn('do-not-save',(self.output/'powerbi-refresh.json').read_text());self.assertFalse(self.queue)
    def test_powerbi_refresh_terminal_failures_not_success(self):
        for extended in ('Failed','Disabled','Cancelled','TimedOut'):
            self.output=Path(self.temp.name)/extended;self.pbi_start();self.pbi_status('Unknown',extended)
            with self.assertRaises(native.NativeError):self.pbi_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_powerbi_refresh_external_or_wrong_location_never_followed(self):
        for i,location in enumerate(['https://external.example/steal','https://api.powerbi.com/other']):
            self.output=Path(self.temp.name)/str(i);self.pbi_start(location)
            with self.assertRaises(native.NativeError):self.pbi_execute()
        self.assertEqual(len(self.received),2)
    def test_powerbi_refresh_post503_not_retried(self):
        self.pbi_start(status=503)
        with self.assertRaises(native.NativeError):self.pbi_execute()
        self.assertEqual(len(self.received),1)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['state'],'request_pending')
    def test_powerbi_refresh_timeout_keeps_refresh_id(self):
        self.pbi_start()
        for _ in range(120):self.pbi_status('Unknown','NotStarted',202)
        with self.assertRaisesRegex(native.NativeError,'zaman aşımı'):self.pbi_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['refresh_id'],self.PBI_ID)
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_powerbi_refresh_inconsistent_status_rejected(self):
        self.pbi_start();self.pbi_status(status=202)
        with self.assertRaises(native.NativeError):self.pbi_execute()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_powerbi_refresh_missing_id_not_success(self):
        self.step('POST',self.PBI_PATH,b'',status=202)
        with self.assertRaises(native.NativeError):self.pbi_execute()
        self.assertEqual(len(self.received),1);self.assertFalse((self.output/'manifest.json').exists())


    def dbr_start(self,**extra):
        self.step('POST','/api/2.2/jobs/run-now',{'run_id':9},request_headers={'Authorization':'Bearer test-only-secret'},**extra)
    def dbr_status(self,lifecycle='TERMINATED',result='SUCCESS',**overrides):
        self.step('GET','/api/2.2/jobs/runs/get?run_id=9',{'run_id':9,'job_id':8,'state':{'life_cycle_state':lifecycle,'result_state':result},**overrides})
    def dbr_execute(self,parameters=None,**overrides):
        source=Path(self.temp.name)/'parameters.json';source.write_text(json.dumps(parameters if parameters is not None else {'date':'2026-10-13'}))
        with patch.object(native.time,'sleep'):
            return self.run_op('databricks-run',**{'workspace_url':'https://test.cloud.databricks.com','job_id':8,'parameters_file':str(source),**overrides})
    def test_databricks_job_parameters_idempotency_and_success(self):
        self.dbr_start();self.dbr_status('QUEUED',None);self.dbr_status('RUNNING',None);self.dbr_status(job_parameters={'password':'do-not-save'})
        self.assertEqual(self.dbr_execute()['artifact']['file'],'databricks-run.json')
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(self.received[0][2],{'job_id':8,'job_parameters':{'date':'2026-10-13'},'idempotency_token':state['idempotency_token']})
        self.assertNotIn('do-not-save',(self.output/'databricks-run.json').read_text());self.assertFalse(self.queue)
    def test_databricks_terminated_failure_is_not_success(self):
        for result in ('FAILED','TIMEDOUT','CANCELED','SUCCESS_WITH_FAILURES',None):
            self.output=Path(self.temp.name)/str(result);self.dbr_start();self.dbr_status(result=result)
            with self.assertRaises(native.NativeError):self.dbr_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_databricks_skipped_internal_error_not_success(self):
        for lifecycle in ('SKIPPED','INTERNAL_ERROR'):
            self.output=Path(self.temp.name)/lifecycle;self.dbr_start();self.dbr_status(lifecycle,None)
            with self.assertRaises(native.NativeError):self.dbr_execute()
            self.assertFalse((self.output/'manifest.json').exists())
    def test_databricks_wrong_run_or_job_rejected(self):
        for i,override in enumerate([{'job_id':10},{'run_id':10}]):
            self.output=Path(self.temp.name)/str(i);self.dbr_start();self.dbr_status(**override)
            with self.assertRaises(native.NativeError):self.dbr_execute()
    def test_databricks_invalid_job_or_parameters_never_calls_api(self):
        with self.assertRaises(native.NativeError):self.dbr_execute(job_id=True)
        with self.assertRaises(native.NativeError):self.dbr_execute(parameters={'count':5})
        self.assertFalse(self.received)
    def test_databricks_job_post503_preserves_idempotency_no_retry(self):
        self.dbr_start(status=503)
        with self.assertRaises(native.NativeError):self.dbr_execute()
        self.assertEqual(len(self.received),1)
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(state['idempotency_token'],self.received[0][2]['idempotency_token'])
    def test_databricks_job_timeout_keeps_run(self):
        self.dbr_start()
        for _ in range(120):self.dbr_status('RUNNING',None)
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.dbr_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['run_id'],9)
        self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
    def test_databricks_job_auth_failure_does_not_hide_started_run(self):
        self.dbr_start();self.step('GET','/api/2.2/jobs/runs/get?run_id=9',status=401)
        with self.assertRaises(native.NativeError):self.dbr_execute()
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['run_id'],9)
        self.assertFalse((self.output/'manifest.json').exists())


    def dbr_monitor(self,**overrides):
        with patch.object(native.time,'sleep'):
            return self.run_op('databricks-monitor',**{'workspace_url':'https://test.cloud.databricks.com','job_id':8,'run_id':9,**overrides})
    def test_databricks_monitor_only_gets_existing_run(self):
        self.dbr_status('RUNNING',None);self.dbr_status()
        self.assertEqual(self.dbr_monitor()['artifact']['file'],'databricks-run.json')
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)
    def test_databricks_monitor_missing_run_id_never_starts_job(self):
        with self.assertRaises(native.NativeError):self.dbr_monitor(run_id=0)
        self.assertFalse(self.received)
    def test_databricks_monitor_wrong_job_is_rejected(self):
        self.dbr_status(job_id=99)
        with self.assertRaises(native.NativeError):self.dbr_monitor()
        self.assertEqual(len(self.received),1);self.assertFalse((self.output/'manifest.json').exists())
    def test_databricks_monitor_failed_run_is_not_restarted(self):
        self.dbr_status(result='FAILED')
        with self.assertRaises(native.NativeError):self.dbr_monitor()
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse((self.output/'manifest.json').exists())
    def test_databricks_monitor_timeout_then_continue_in_new_output(self):
        for _ in range(120):self.dbr_status('RUNNING',None)
        with self.assertRaises(native.NativeError):self.dbr_monitor()
        previous=self.output
        state=json.loads((previous/'run-state.json').read_text())
        self.output=Path(self.temp.name)/'continued';self.dbr_status()
        self.dbr_monitor(job_id=state['job_id'],run_id=state['run_id'])
        self.assertFalse((previous/'manifest.json').exists());self.assertTrue((self.output/'manifest.json').exists())
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)


    def ab_monitor(self,**overrides):
        with patch.object(native.time,'sleep'):
            return self.run_op('airbyte-monitor',**{'connection_id':self.AB_CONNECTION,'job_id':7,**overrides})
    def test_airbyte_monitor_get_only_success(self):
        self.step('GET','/v1/jobs/7',self.ab_response())
        self.step('GET','/v1/jobs/7',self.ab_response('succeeded',rowsSynced=4))
        self.assertEqual(self.ab_monitor()['artifact']['file'],'airbyte-job.json')
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)
    def test_airbyte_monitor_invalid_job_no_requests(self):
        for job in (-1,True,'7',2**63,None):
            with self.assertRaises(native.NativeError):self.ab_monitor(job_id=job)
        self.assertFalse(self.received)
    def test_airbyte_monitor_failure_never_restarts(self):
        for status in ('incomplete','failed','cancelled'):
            self.output=Path(self.temp.name)/status
            self.step('GET','/v1/jobs/7',self.ab_response(status))
            with self.assertRaises(native.NativeError):self.ab_monitor()
            self.assertFalse((self.output/'manifest.json').exists())
        self.assertTrue(all(r[0]=='GET' for r in self.received))
    def test_airbyte_monitor_wrong_connection_rejected(self):
        self.step('GET','/v1/jobs/7',self.ab_response('succeeded',connectionId='other'))
        with self.assertRaises(native.NativeError):self.ab_monitor()
        self.assertFalse((self.output/'manifest.json').exists())
    def test_airbyte_monitor_timeout_then_continue_preserves_previous(self):
        for _ in range(120):self.step('GET','/v1/jobs/7',self.ab_response())
        with self.assertRaisesRegex(native.NativeError,'zaman aşımına'):self.ab_monitor()
        previous=self.output;state=json.loads((previous/'run-state.json').read_text())
        self.output=Path(self.temp.name)/'continued'
        self.step('GET','/v1/jobs/7',self.ab_response('succeeded'))
        self.ab_monitor(connection_id=state['connection_id'],job_id=state['job_id'])
        self.assertFalse((previous/'manifest.json').exists());self.assertTrue((self.output/'manifest.json').exists())
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)
    def test_airbyte_monitor_expired_token_retains_identity(self):
        self.step('GET','/v1/jobs/7',status=401)
        with self.assertRaises(native.NativeError):self.ab_monitor()
        state=json.loads((self.output/'run-state.json').read_text())
        self.assertEqual(state['connection_id'],self.AB_CONNECTION);self.assertEqual(state['job_id'],7)
        self.assertFalse((self.output/'manifest.json').exists())


    def monitor_cases(self):
        adf='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f/pipelineruns/r?api-version=2018-06-01'
        return [
            ('adf-monitor',dict(subscription_id='s',resource_group='g',factory_name='f',pipeline_name='p',run_id='r'),adf,{'runId':'r','pipelineName':'p','status':'Succeeded','parameters':{'secret':'must-not-save'}},{'runId':'r','pipelineName':'p','status':'Failed'},'run_id'),
            ('airflow-monitor',dict(server='https://airflow.example',api_version='3.2',dag_id='d',dag_run_id='r'),'/api/v2/dags/d/dagRuns/r',{'dag_id':'d','dag_run_id':'r','state':'success','conf':{'secret':'must-not-save'}},{'dag_id':'d','dag_run_id':'r','state':'failed'},'dag_run_id'),
            ('dbt-monitor',dict(server='https://cloud.getdbt.com',account_id='1',job_id='2',run_id=3),'/api/v2/accounts/1/runs/3/',{'data':{'id':3,'account_id':1,'job_id':2,'status':10}},{'data':{'id':3,'account_id':1,'job_id':2,'status':20}},'run_id'),
            ('prefect-monitor',dict(api_url='https://api.prefect.cloud'+self.PREFECT_BASE,deployment_id=self.PREFECT_DEPLOYMENT,flow_run_id='run'),self.PREFECT_BASE+'flow_runs/run',{'id':'run','deployment_id':self.PREFECT_DEPLOYMENT,'state':{'type':'COMPLETED'}},{'id':'run','deployment_id':self.PREFECT_DEPLOYMENT,'state':{'type':'FAILED'}},'flow_run_id'),
            ('dataform-monitor',dict(compilation_result=self.DF_PARENT+'/compilationResults/c',workflow_invocation=self.DF_PARENT+'/workflowInvocations/run'),'/v1/'+self.DF_PARENT+'/workflowInvocations/run',{'name':self.DF_PARENT+'/workflowInvocations/run','compilationResult':self.DF_PARENT+'/compilationResults/c','state':'SUCCEEDED'},{'name':self.DF_PARENT+'/workflowInvocations/run','compilationResult':self.DF_PARENT+'/compilationResults/c','state':'FAILED'},'workflow_invocation'),
            ('powerbi-monitor',dict(workspace_id=self.PBI_ID,dataset_id=self.PBI_ID,refresh_id=self.PBI_ID),self.PBI_PATH+'/'+self.PBI_ID,{'status':'Completed','extendedStatus':'Completed'},{'status':'Failed','extendedStatus':'Failed'},'refresh_id'),
        ]
    def test_six_monitor_packages_only_get_and_preserve_identity(self):
        for op,cfg,path,success,_,_ in self.monitor_cases():
            with self.subTest(operation=op):
                self.output=Path(self.temp.name)/op;self.step('GET',path,success)
                if op=='dbt-monitor':self.step('GET',path+'artifacts/run_results.json',{'metadata':{'dbt_schema_version':'https://schemas.getdbt.com/dbt/run-results/v6.json'},'results':[]})
                self.run_op(op,**cfg)
                for file in self.output.iterdir():self.assertNotIn('must-not-save',file.read_text())
                self.assertTrue((self.output/'manifest.json').exists());self.assertFalse(self.queue)
        self.assertTrue(all(r[0]=='GET' for r in self.received))
    def test_six_monitor_failures_never_restart(self):
        for op,cfg,path,_,failed,_ in self.monitor_cases():
            self.output=Path(self.temp.name)/op;self.step('GET',path,failed)
            with self.assertRaises(native.NativeError):self.run_op(op,**cfg)
            self.assertFalse((self.output/'manifest.json').exists());self.assertFalse(self.queue)
        self.assertTrue(all(r[0]=='GET' for r in self.received))
    def test_six_monitors_reject_missing_identity_without_network(self):
        for op,cfg,_,_,_,key in self.monitor_cases():
            self.output=Path(self.temp.name)/op;del cfg[key]
            with self.assertRaises(native.NativeError):self.run_op(op,**cfg)
        self.assertFalse(self.received)
    def test_six_monitor_authorization_failure_preserves_trace(self):
        for op,cfg,path,_,_,_ in self.monitor_cases():
            self.output=Path(self.temp.name)/op;self.step('GET',path,status=401)
            with self.assertRaises(native.NativeError):self.run_op(op,**cfg)
            self.assertTrue((self.output/'run-state.json').exists());self.assertFalse((self.output/'manifest.json').exists())
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)
    def test_six_monitors_reject_mismatched_identity_or_state(self):
        for op,cfg,path,_,_,_ in self.monitor_cases():
            self.output=Path(self.temp.name)/op
            self.step('GET',path,{'status':'Unknown'} if op=='powerbi-monitor' else {})
            with self.assertRaises(native.NativeError):self.run_op(op,**cfg)
            self.assertFalse((self.output/'manifest.json').exists())
        self.assertTrue(all(r[0]=='GET' for r in self.received));self.assertFalse(self.queue)

    # ---------- from-scratch build packages (kurulum/ comes from the local block's sample output) ----------
    def kurulum(self,op,edit=None):
        files=next(o for o in OPERATIONS if o['operation']==op)['input_files']
        root=Path(self.temp.name)/('kurulum-'+op)
        for name,content in files.items():
            target=root/name[len('kurulum/'):]
            target.parent.mkdir(parents=True,exist_ok=True)
            if edit:content=edit(name,content)
            target.write_bytes(content if isinstance(content,bytes) else content.encode('utf-8'))
        return str(root)

    def test_qlik_cloud_build_uploads_scripts_reloads_and_checks_the_model(self):
        root=self.kurulum('qlik-cloud-build');script=(Path(root)/'qlik-uygulama'/'script.qvs').read_text(encoding='utf-8')
        fields=sorted(set(__import__('re').findall(r'^ {4}.+ as \[([^\]]+)\],?$',script,flags=__import__('re').M)))
        for i,f in enumerate(['magaza.csv','musteri.csv','satis.csv','urun.csv']):self.step('POST','/api/v1/data-files',{'id':'df'+str(i)})
        self.step('POST','/api/v1/apps',{'attributes':{'id':'app1'}})
        self.step('POST','/api/v1/apps/app1/scripts',{})
        self.step('POST','/api/v1/reloads',{'id':'r1'},payload={'appId':'app1'})
        self.step('GET','/api/v1/reloads/r1',{'status':'SUCCEEDED'})
        self.step('GET','/api/v1/apps/app1/data/metadata',{'fields':[{'name':f} for f in fields]+[{'name':'$Field','is_system':True}],'tables':[{'name':t} for t in ['Satis','Musteri','Urun','Magaza','Takvim']]})
        self.run_op('qlik-cloud-build',tenant='https://t.eu.qlikcloud.com',name='Mağaza',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        upload=self.received[2][2]
        self.assertIn(b'name="json"',upload);self.assertIn(b'{"name": "satis.csv"}',upload);self.assertIn(b'satis_id,tarih,musteri_id',upload)
        self.assertEqual(self.received[5][2]['script'],script)
        receipt=json.loads((self.output/'qlik-app.json').read_text(encoding='utf-8'))
        self.assertEqual((receipt['app_id'],receipt['synthetic_keys'],receipt['tables']),('app1',0,5))

    def test_qlik_cloud_build_rejects_a_model_with_synthetic_keys(self):
        root=self.kurulum('qlik-cloud-build')
        for i in range(4):self.step('POST','/api/v1/data-files',{'id':'df'+str(i)})
        self.step('POST','/api/v1/apps',{'attributes':{'id':'app1'}});self.step('POST','/api/v1/apps/app1/scripts',{})
        self.step('POST','/api/v1/reloads',{'id':'r1'});self.step('GET','/api/v1/reloads/r1',{'status':'SUCCEEDED'})
        self.step('GET','/api/v1/apps/app1/data/metadata',{'fields':[{'name':'$Syn 1'}],'tables':[{'name':'$Syn 1 Table'}]})
        with self.assertRaisesRegex(native.NativeError,'sentetik anahtar'):self.run_op('qlik-cloud-build',tenant='https://t.eu.qlikcloud.com',name='M',kurulum_klasoru=root)
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['created_app_id'],'app1')
        self.assertFalse((self.output/'manifest.json').exists())

    def test_fabric_lakehouse_build_uploads_loads_and_lists_tables(self):
        ws,lh,op='11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222','33333333-3333-3333-3333-333333333333'
        root=self.kurulum('fabric-lakehouse-build');base='/v1/workspaces/'+ws+'/lakehouses'
        self.step('POST',base,{'id':lh},status=201,payload={'displayName':'magaza_lh'})
        self.step('GET',base+'/'+lh,{'id':lh,'type':'Lakehouse','displayName':'magaza_lh'})
        names=['magaza','musteri','satis','urun']
        for n in names:
            target='/'+ws+'/'+lh+'/Files/kurulum/'+n+'.csv'
            self.step('PUT',target+'?resource=file',request_headers={'x-ms-version':'2023-11-03'})
            size=len((Path(root)/'veri'/(n+'.csv')).read_bytes())
            self.step('PATCH',target+'?action=append&position=0');self.step('PATCH',target+'?action=flush&position='+str(size))
        for n in names:
            self.step('POST',base+'/'+lh+'/tables/'+n+'/load',status=202,headers={'Location':'https://api.fabric.microsoft.com'+base+'/'+lh+'/operations/'+op},
                      payload={'relativePath':'Files/kurulum/'+n+'.csv','pathType':'File','mode':'Overwrite','formatOptions':{'header':True,'delimiter':',','format':'Csv'}})
            self.step('GET',base+'/'+lh+'/operations/'+op,{'Status':3})
        self.step('GET',base+'/'+lh+'/tables',{'data':[{'name':n} for n in names]})
        self.run_op('fabric-lakehouse-build',workspace_id=ws,display_name='magaza_lh',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        self.assertEqual(self.received[3][2],(Path(root)/'veri'/'magaza.csv').read_bytes())  # the append carries the file bytes
        self.assertEqual(json.loads((self.output/'lakehouse.json').read_text())['tables'],names)

    def test_fabric_semantic_model_posts_tmdl_and_reads_it_back(self):
        ws,sm='11111111-1111-1111-1111-111111111111','44444444-4444-4444-4444-444444444444'
        root=self.kurulum('fabric-semantic-model');base='/v1/workspaces/'+ws+'/semanticModels'
        tables=['definition/tables/'+t+'.tmdl' for t in ['Magaza','Musteri','Satis','Takvim','Urun']]
        self.step('POST',base,{'id':sm},status=201)
        self.step('GET',base+'/'+sm,{'id':sm,'type':'SemanticModel','displayName':'Model'})
        self.step('POST',base+'/'+sm+'/getDefinition?format=TMDL',{'definition':{'parts':[{'path':p} for p in tables+['definition.pbism']]}})
        self.run_op('fabric-semantic-model',workspace_id=ws,display_name='Model',kurulum_klasoru=root)
        sent=self.received[0][2]['definition']
        self.assertEqual(sent['format'],'TMDL')
        paths={p['path'] for p in sent['parts']}
        self.assertTrue({'definition.pbism','definition/model.tmdl','definition/relationships.tmdl',*tables}<=paths)
        rel=native.base64.b64decode(next(p['payload'] for p in sent['parts'] if p['path']=='definition/relationships.tmdl')).decode()
        self.assertIn("fromColumn: Satis.'Musteri Id'",rel)
        self.assertEqual(json.loads((self.output/'semantic-model.json').read_text())['tables'],['Magaza','Musteri','Satis','Takvim','Urun'])

    def test_metabase_dashboard_creates_cards_and_checks_placement(self):
        root=self.kurulum('metabase-dashboard');cards=json.loads((Path(root)/'metabase'/'kartlar.json').read_text(encoding='utf-8'))['cards']
        self.step('GET','/api/database/2',{'id':2},request_headers={'X-API-Key':'test-only-secret'})
        self.step('POST','/api/collection',{'id':5});self.step('POST','/api/dashboard',{'id':7})
        for i in range(len(cards)):self.step('POST','/api/card',{'id':100+i})
        self.step('PUT','/api/dashboard/7',{})
        self.step('GET','/api/dashboard/7',{'dashcards':[{'card_id':100+i} for i in range(len(cards))]})
        self.run_op('metabase-dashboard',server='https://mb.example.com',database_id=2,kurulum_klasoru=root)
        self.assertFalse(self.queue)
        self.assertEqual(self.received[3][2]['dataset_query'],{'type':'native','native':{'query':cards[0]['sql']},'database':2})
        placed=self.received[-2][2]['dashcards']
        self.assertEqual([(d['card_id'],d['row'],d['col']) for d in placed][:2],[(100,cards[0]['row'],cards[0]['col']),(101,cards[1]['row'],cards[1]['col'])])
        self.assertEqual(json.loads((self.output/'metabase-dashboard.json').read_text())['cards'],len(cards))

    def test_metabase_dashboard_refuses_writing_sql_before_network(self):
        root=self.kurulum('metabase-dashboard',edit=lambda n,c:c.replace('SELECT SUM','DELETE FROM x; SELECT SUM',1) if n.endswith('kartlar.json') else c)
        with self.assertRaisesRegex(native.NativeError,'yalnızca okuma'):self.run_op('metabase-dashboard',server='https://mb.example.com',database_id=2,kurulum_klasoru=root)
        self.assertFalse(self.received)

    def looker_steps(self,missing=()):
        root=self.kurulum('looker-dashboard');spec=json.loads((Path(root)/'lookml'/'pano.json').read_text(encoding='utf-8'))
        fields=sorted({f for e in spec['elements'] for f in e['fields']}-set(missing))
        self.step('POST','/api/4.0/login',{'access_token':'tok'})
        self.step('GET','/api/4.0/lookml_models/magaza/explores/satis',{'fields':{'dimensions':[{'name':f} for f in fields if not f.startswith('satis.toplam') and 'sayisi' not in f and 'ortalama' not in f],
                                                                                   'measures':[{'name':f} for f in fields if f.startswith('satis.toplam') or 'sayisi' in f or 'ortalama' in f]}},
                  request_headers={'Authorization':'token tok'})
        return root,spec

    def test_looker_dashboard_checks_fields_then_builds_tiles(self):
        root,spec=self.looker_steps()
        self.step('POST','/api/4.0/dashboards',{'id':'d1'},payload={'title':spec['title'],'folder_id':'9'})
        for i,_ in enumerate(spec['elements']):
            self.step('POST','/api/4.0/queries',{'id':'q'+str(i)});self.step('POST','/api/4.0/dashboard_elements',{'id':'e'+str(i)})
        self.step('GET','/api/4.0/dashboards/d1/dashboard_elements',lambda:json.dumps([{'id':'e'+str(i)} for i in range(len(spec['elements']))]).encode())
        self.queue[-1]['headers']={'Content-Type':'application/json'}
        self.run_op('looker-dashboard',server='https://x.cloud.looker.com',folder_id='9',kurulum_klasoru=root)
        self.assertEqual(self.received[0][2],b'client_id=test-only-secret&client_secret=test-only-secret')
        self.assertEqual(self.received[3][2]['vis_config'],{'type':spec['elements'][0]['type']})
        self.assertEqual(json.loads((self.output/'looker-dashboard.json').read_text())['elements'],len(spec['elements']))

    def test_looker_dashboard_stops_when_lookml_is_not_deployed(self):
        root,_=self.looker_steps(missing=('satis.tarih_month',))
        with self.assertRaisesRegex(native.NativeError,'satis.tarih_month'):self.run_op('looker-dashboard',server='https://x.cloud.looker.com',folder_id='9',kurulum_klasoru=root)
        self.assertEqual(len(self.received),2)  # nothing created

    def test_snowflake_setup_runs_each_statement_with_its_role_and_verifies_grants(self):
        root=self.kurulum('snowflake-setup',edit=lambda n,c:c.replace("'<ACIK_ANAHTAR>'","'MIIBIjANBgkq'") if n.endswith('.sql') else c)
        sql=(Path(root)/'snowflake_kurulum.sql').read_text(encoding='utf-8')
        import csv as _csv,re as _re
        statements=[c.strip().rstrip(';').strip() for c in _re.sub(r'^--.*$','',sql,flags=_re.M).split(';\n')]
        statements=[s for s in statements if s and not s.startswith('USE ROLE')]
        matrix=list(_csv.DictReader(open(Path(root)/'yetki_matrisi.csv',encoding='utf-8-sig')))
        for _ in statements:self.step('POST',self.SF_PATH,self.sf_result('s'))
        level={'okuma':'R','yazma':'RW','sahip':'OWN'}
        cols=[{'name':n} for n in ['created_on','privilege','granted_on','name','granted_to','grantee_name']]
        for role in sorted({r['kimlik'] for r in matrix}):
            rows=[['x','USAGE','ROLE',r['veritabani']+'_'+level[r['etkin']],'ROLE',role] for r in matrix if r['kimlik']==role and r['etkin'] in level]
            rows.append(['x','USAGE','WAREHOUSE','WH','ROLE',role])
            self.step('POST',self.SF_PATH,self.sf_result('v',resultSetMetaData={'rowType':cols},data=rows))
        with patch.object(native.uuid,'uuid4',return_value=native.uuid.UUID(self.SF_UUID)),patch.object(native.time,'sleep'):
            self.run_op('snowflake-setup',server='https://a.snowflakecomputing.com',warehouse='WH',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        roles=[r[2]['role'] for r in self.received[:len(statements)]]
        self.assertEqual(roles[0],'USERADMIN');self.assertIn('ACCOUNTADMIN',roles)
        first_monitor=next(i for i,s in enumerate(statements) if s.startswith('CREATE RESOURCE MONITOR'))
        self.assertEqual(roles[first_monitor],'ACCOUNTADMIN')
        self.assertEqual(self.received[len(statements)][2]['statement'],'SHOW GRANTS TO ROLE DEV_ANALITIK')
        self.assertEqual(json.loads((self.output/'snowflake-setup.json').read_text())['differences'],0)

    def test_snowflake_setup_refuses_unfilled_keys_and_foreign_statements(self):
        root=self.kurulum('snowflake-setup')
        with self.assertRaisesRegex(native.NativeError,'RSA_PUBLIC_KEY'):self.run_op('snowflake-setup',server='https://a.snowflakecomputing.com',warehouse='WH',kurulum_klasoru=root)
        self.output=Path(self.temp.name)/'run2'
        root=self.kurulum('snowflake-setup',edit=lambda n,c:c.replace("'<ACIK_ANAHTAR>'","'K'")+'DROP DATABASE PROD_MART;\n' if n.endswith('.sql') else c)
        with self.assertRaisesRegex(native.NativeError,'izin verilmeyen komut'):self.run_op('snowflake-setup',server='https://a.snowflakecomputing.com',warehouse='WH',kurulum_klasoru=root)
        self.assertFalse(self.received)

    def test_unity_catalog_setup_creates_grants_verifies_then_hands_over_ownership(self):
        root=self.kurulum('unity-catalog-setup');plan=json.loads((Path(root)/'uc_plan.json').read_text(encoding='utf-8'))
        b='/api/2.1/unity-catalog/'
        for c in plan['catalogs']:self.step('GET',b+'catalogs/'+c['name'],status=404)
        for c in plan['catalogs']:self.step('POST',b+'catalogs',{'name':c['name']},payload={'name':c['name'],'storage_root':c['storage_root'],'comment':c['comment']})
        for s in plan['schemas']:self.step('POST',b+'schemas',{'full_name':s['catalog_name']+'.'+s['name']})
        grouped={}
        for g in plan['grants']:grouped.setdefault((g['securable_type'],g['full_name']),[]).append(g)
        for (k,f),gs in grouped.items():self.step('PATCH',b+'permissions/'+k+'/'+f,{},payload={'changes':[{'principal':g['principal'],'add':g['privileges']} for g in gs]})
        for (k,f),gs in grouped.items():self.step('GET',b+'permissions/'+k+'/'+f,{'privilege_assignments':[{'principal':g['principal'],'privileges':g['privileges']} for g in gs]})
        for o in plan['owners']:self.step('PATCH',b+'schemas/'+o['full_name'],{'owner':o['owner']},payload={'owner':o['owner']})
        self.run_op('unity-catalog-setup',workspace_url='https://w.cloud.databricks.com',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        self.assertEqual(json.loads((self.output/'unity-catalog.json').read_text())['grants'],len(plan['grants']))

    def test_unity_catalog_setup_never_touches_an_existing_catalog(self):
        root=self.kurulum('unity-catalog-setup')
        self.step('GET','/api/2.1/unity-catalog/catalogs/dev',{'name':'dev'})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('unity-catalog-setup',workspace_url='https://w.cloud.databricks.com',kurulum_klasoru=root)
        self.assertEqual(len(self.received),1)

    def test_bigquery_setup_creates_accounts_and_datasets_with_acl(self):
        root=self.kurulum('bigquery-setup');plan=json.loads((Path(root)/'bq_plan.json').read_text(encoding='utf-8'))
        for d in plan['datasets']:self.step('GET','/bigquery/v2/projects/'+d['project']+'/datasets/'+d['dataset_id'],status=404)
        for a in plan['service_accounts']:self.step('POST','/v1/projects/'+a['project']+'/serviceAccounts',{'email':a['account_id']+'@'+a['project']+'.iam.gserviceaccount.com'})
        for d in plan['datasets']:
            self.step('POST','/bigquery/v2/projects/'+d['project']+'/datasets',{})
            self.step('GET','/bigquery/v2/projects/'+d['project']+'/datasets/'+d['dataset_id'],{'access':d['access']+[{'role':'OWNER','userByEmail':'creator@x.com'}]})
        self.run_op('bigquery-setup',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        sandbox=next(r[2] for r in self.received if r[0]=='POST' and isinstance(r[2],dict) and r[2].get('datasetReference',{}).get('datasetId')=='sandbox')
        self.assertEqual(sandbox['defaultTableExpirationMs'],str(30*86400000))

    def test_adf_solution_refuses_placeholders_then_deploys_in_dependency_order(self):
        root=self.kurulum('adf-solution')
        cfg=dict(subscription_id='s',resource_group='g',factory_name='f',kurulum_klasoru=root)
        with self.assertRaisesRegex(native.NativeError,'yer tutucu'):self.run_op('adf-solution',**cfg)
        self.assertFalse(self.received)
        self.output=Path(self.temp.name)/'run2'
        root=self.kurulum('adf-solution',edit=lambda n,c:c.replace('<depo>','depo1').replace('<kasa>','kasa1'))
        cfg['kurulum_klasoru']=root
        base='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f';api='?api-version=2018-06-01'
        order=[('linkedservices','LS_DataLake'),('linkedservices','LS_KeyVault'),('linkedservices','LS_SqlKaynak'),('datasets','DS_Lake_Parquet'),('datasets','DS_SqlKaynak'),
               ('pipelines','PL_Kontrol_Yukleme'),('pipelines','PL_Ana'),('triggers','TR_Gece')]
        for k,n in order:self.step('GET',base+'/'+k+'/dw_'+n+api,status=404)
        for k,n in order:self.step('PUT',base+'/'+k+'/dw_'+n+api,{});self.step('GET',base+'/'+k+'/dw_'+n+api,{'name':'dw_'+n})
        self.run_op('adf-solution',**cfg)
        self.assertFalse(self.queue)
        master=next(r[2] for r in self.received if r[0]=='PUT' and r[1].startswith(base+'/pipelines/dw_PL_Ana'))
        self.assertEqual(master['properties']['activities'][0]['typeProperties']['pipeline']['referenceName'],'dw_PL_Kontrol_Yukleme')

    def test_airflow_activate_checks_import_and_tasks_before_unpausing(self):
        root=self.kurulum('airflow-activate')
        tasks=['erp_dosyasi_bekle','crm_yukle','erp_yukle','staging','dbt_mart','kalite_kontrol','rapor_yenile']
        self.step('GET','/api/v2/importErrors',{'import_errors':[{'filename':'/dags/baska.py'}]})
        self.step('GET','/api/v2/dags/gece_veri_hatti',{'dag_id':'gece_veri_hatti'})
        self.step('GET','/api/v2/dags/gece_veri_hatti/tasks',{'tasks':[{'task_id':t} for t in tasks]})
        self.step('PATCH','/api/v2/dags/gece_veri_hatti?update_mask=is_paused',{'is_paused':False},payload={'is_paused':False})
        self.run_op('airflow-activate',server='https://af.example.com',api_version='3.2',dag_id='gece_veri_hatti',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        self.assertFalse(json.loads((self.output/'airflow-dag.json').read_text())['is_paused'])

    def test_airflow_activate_stops_on_stale_task_list(self):
        root=self.kurulum('airflow-activate')
        self.step('GET','/api/v2/importErrors',{'import_errors':[]})
        self.step('GET','/api/v2/dags/gece_veri_hatti',{'dag_id':'gece_veri_hatti'})
        self.step('GET','/api/v2/dags/gece_veri_hatti/tasks',{'tasks':[{'task_id':'eski'}]})
        with self.assertRaisesRegex(native.NativeError,'eşleşmedi'):self.run_op('airflow-activate',server='https://af.example.com',api_version='3.2',dag_id='gece_veri_hatti',kurulum_klasoru=root)
        self.assertFalse(any(r[0]=='PATCH' for r in self.received))


    # ---------- plan-driven setup, live audit and maintenance packages ----------
    def plan(self,op,edit=None):
        """Write the package's sample input files; returns {filename: absolute path}."""
        files=next(o for o in OPERATIONS if o['operation']==op)['input_files']
        root=Path(self.temp.name)/('in-'+op+'-'+str(len(list(Path(self.temp.name).iterdir()))))
        paths={}
        for name,content in files.items():
            if edit:content=edit(name,content)
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
            paths[Path(name).name]=str(target)
        return paths
    def sample(self,op,name):
        return json.loads(next(o for o in OPERATIONS if o['operation']==op)['input_files'][name])
    def listing(self,method,path,items,**extra):self.step(method,path,json.dumps(items).encode(),headers={'Content-Type':'application/json'},**extra)
    def csv_out(self,name):
        import csv as _csv
        return list(_csv.DictReader(io.StringIO((self.output/name).read_text(encoding='utf-8'))))
    def nap(self):return patch.object(native.time,'sleep')

    def test_confluent_topics_creates_topics_checks_configs_and_registers_schema(self):
        p=self.plan('confluent-topics');topics=self.sample('confluent-topics','topics.json')['topics']
        base='/kafka/v3/clusters/lkc-abc/topics'
        for t in topics:self.step('GET',base+'/'+t['name'],status=404)
        self.step('GET','/subjects/siparis.olaylari.v1-value/versions',status=404)
        for t in topics:self.step('POST',base,{'topic_name':t['name']},status=201,request_headers={'Authorization':native.basic_auth('test-only-secret','test-only-secret')})
        for t in topics:
            self.step('GET',base+'/'+t['name'],{'topic_name':t['name'],'partitions_count':t['partitions']})
            self.step('GET',base+'/'+t['name']+'/configs',{'data':[{'name':k,'value':v} for k,v in t['configs'].items()]+[{'name':'segment.ms','value':'1'}]})
        self.step('PUT','/config/siparis.olaylari.v1-value',{'compatibility':'BACKWARD'},payload={'compatibility':'BACKWARD'})
        self.step('POST','/subjects/siparis.olaylari.v1-value/versions',{'id':41})
        self.step('GET','/subjects/siparis.olaylari.v1-value/versions/latest',{'id':41,'version':1})
        self.run_op('confluent-topics',rest_endpoint='https://pkc.x.confluent.cloud',cluster_id='lkc-abc',schema_registry='https://psrc.x.confluent.cloud',plan_file=p['topics.json'])
        self.assertFalse(self.queue)
        created=self.received[3][2]
        self.assertEqual((created['partitions_count'],created['replication_factor']),(6,3))
        self.assertIn({'name':'min.insync.replicas','value':'2'},created['configs'])
        schema=json.loads(next(r[2] for r in self.received if r[1].endswith('/versions') and r[0]=='POST')['schema'])
        self.assertEqual(schema['name'],'SiparisOlayi')
        self.assertEqual(json.loads((self.output/'confluent-topics.json').read_text())['schemas'],{'siparis.olaylari.v1-value':41})

    def test_confluent_topics_never_touches_existing_topic_or_unknown_config(self):
        p=self.plan('confluent-topics')
        self.step('GET','/kafka/v3/clusters/lkc-abc/topics/siparis.olaylari.v1',{'topic_name':'siparis.olaylari.v1'})
        cfg=dict(rest_endpoint='https://pkc.x.confluent.cloud',cluster_id='lkc-abc',schema_registry='https://psrc.x.confluent.cloud',plan_file=p['topics.json'])
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('confluent-topics',**cfg)
        self.assertEqual(len(self.received),1)
        self.output=Path(self.temp.name)/'run2'
        p=self.plan('confluent-topics',edit=lambda n,c:c.replace('"cleanup.policy": "delete"','"unclean.leader.election.enable": "true"'))
        with self.assertRaisesRegex(native.NativeError,'izinli listede'):self.run_op('confluent-topics',**{**cfg,'plan_file':p['topics.json']})
        self.assertEqual(len(self.received),1)

    def test_dbt_cloud_job_checks_environment_and_names_then_creates_paused_jobs(self):
        p=self.plan('dbt-cloud-job');jobs=self.sample('dbt-cloud-job','dbt_jobs.json')['jobs']
        self.step('GET','/api/v3/accounts/1/projects/2/environments/3/',{'data':{'id':3,'project_id':2,'type':'deployment'}},request_headers={'Authorization':'Token test-only-secret'})
        self.step('GET','/api/v2/accounts/1/jobs/?project_id=2&limit=100',{'data':[{'name':'Eski job'}]})
        for i,j in enumerate(jobs):
            self.step('POST','/api/v2/accounts/1/jobs/',{'data':{'id':70+i}})
            self.step('GET','/api/v2/accounts/1/jobs/'+str(70+i)+'/',{'data':{'name':j['name'],'execute_steps':j['steps']}})
        self.run_op('dbt-cloud-job',server='https://cloud.getdbt.com',account_id=1,project_id=2,environment_id=3,plan_file=p['dbt_jobs.json'])
        body=self.received[2][2]
        self.assertEqual(body['execute_steps'],jobs[0]['steps']);self.assertFalse(body['triggers']['schedule'])
        self.assertEqual(body['schedule']['cron'],'0 3 * * *')
        self.assertEqual(json.loads((self.output/'dbt-jobs.json').read_text())['job_ids'],[70,71])

    def test_dbt_cloud_job_refuses_shell_steps_and_existing_names(self):
        p=self.plan('dbt-cloud-job',edit=lambda n,c:c.replace('dbt source freshness','dbt run; curl x'))
        cfg=dict(server='https://cloud.getdbt.com',account_id=1,project_id=2,environment_id=3)
        with self.assertRaisesRegex(native.NativeError,'dbt adımları'):self.run_op('dbt-cloud-job',plan_file=p['dbt_jobs.json'],**cfg)
        self.assertFalse(self.received)
        self.output=Path(self.temp.name)/'run2';p=self.plan('dbt-cloud-job')
        self.step('GET','/api/v3/accounts/1/projects/2/environments/3/',{'data':{'id':3,'project_id':2,'type':'deployment'}})
        self.step('GET','/api/v2/accounts/1/jobs/?project_id=2&limit=100',{'data':[{'name':'Gece üretim yüklemesi'}]})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('dbt-cloud-job',plan_file=p['dbt_jobs.json'],**cfg)
        self.assertFalse(any(r[0]=='POST' for r in self.received))

    def test_databricks_job_create_builds_dependent_tasks_paused(self):
        p=self.plan('databricks-job-create');plan=self.sample('databricks-job-create','databricks_job.json')
        keys=[t['task_key'] for t in plan['tasks']]
        self.step('GET','/api/2.2/jobs/list?name=gece_veri_hatti',{'has_more':False})
        self.step('POST','/api/2.2/jobs/create',{'job_id':991})
        self.step('GET','/api/2.2/jobs/get?job_id=991',{'settings':{'name':'gece_veri_hatti','tasks':[{'task_key':k} for k in keys],'schedule':{'pause_status':'PAUSED'}}})
        self.run_op('databricks-job-create',workspace_url='https://w.cloud.databricks.com',plan_file=p['databricks_job.json'])
        body=self.received[1][2]
        staging=next(t for t in body['tasks'] if t['task_key']=='staging')
        self.assertEqual(staging['depends_on'],[{'task_key':'crm_yukle'},{'task_key':'erp_yukle'}])
        self.assertEqual(body['schedule']['pause_status'],'PAUSED');self.assertEqual(body['tags'],{'olusturan':'datawork'})

    def test_databricks_job_create_rejects_cycles_and_existing_job(self):
        p=self.plan('databricks-job-create',edit=lambda n,c:c.replace('"depends_on": []','"depends_on": ["kalite_kontrol"]',1))
        with self.assertRaisesRegex(native.NativeError,'döngüsel'):self.run_op('databricks-job-create',workspace_url='https://w.cloud.databricks.com',plan_file=p['databricks_job.json'])
        self.output=Path(self.temp.name)/'run2';p=self.plan('databricks-job-create')
        self.step('GET','/api/2.2/jobs/list?name=gece_veri_hatti',{'jobs':[{'job_id':5}]})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('databricks-job-create',workspace_url='https://w.cloud.databricks.com',plan_file=p['databricks_job.json'])
        self.assertEqual(len(self.received),1)

    def test_prefect_deployment_checks_pool_and_name_then_creates_paused(self):
        p=self.plan('prefect-deployment');d=self.sample('prefect-deployment','prefect_deployment.json')['deployments'][0]
        base='/api/accounts/a/workspaces/w/'
        self.step('GET',base+'work_pools/uretim-havuzu',{'name':'uretim-havuzu'})
        self.step('GET',base+'deployments/name/gece-veri-hatti/uretim',status=404)
        self.step('POST',base+'flows/',{'id':'f1'},payload={'name':'gece-veri-hatti'})
        self.step('POST',base+'deployments/',{'id':'d1'})
        self.step('GET',base+'deployments/d1',{'name':'uretim','flow_id':'f1','entrypoint':d['entrypoint']})
        self.run_op('prefect-deployment',api_url='https://api.prefect.cloud/api/accounts/a/workspaces/w',plan_file=p['prefect_deployment.json'])
        body=self.received[3][2]
        self.assertTrue(body['paused']);self.assertEqual(body['schedules'],[{'schedule':{'cron':'0 2 * * *','timezone':'Europe/Istanbul'},'active':False}])

    def test_prefect_deployment_existing_name_is_never_upserted(self):
        p=self.plan('prefect-deployment');base='/api/accounts/a/workspaces/w/'
        self.step('GET',base+'work_pools/uretim-havuzu',{'name':'uretim-havuzu'})
        self.step('GET',base+'deployments/name/gece-veri-hatti/uretim',{'id':'old'})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('prefect-deployment',api_url='https://api.prefect.cloud/api/accounts/a/workspaces/w',plan_file=p['prefect_deployment.json'])
        self.assertFalse(any(r[0]=='POST' for r in self.received))

    def test_dagster_schedule_verifies_code_then_starts_stopped_only(self):
        p=self.plan('dagster-schedule')
        self.step('POST','/prod/graphql',{'data':{'scheduleOrError':{'__typename':'Schedule','cronSchedule':'0 2 * * *','executionTimezone':'Europe/Istanbul','scheduleState':{'status':'STOPPED'}}}},
                  request_headers={'Dagster-Cloud-Api-Token':'test-only-secret'})
        self.step('POST','/prod/graphql',{'data':{'scheduleOrError':{'__typename':'Schedule','cronSchedule':'0 6 * * 1','executionTimezone':'Europe/Istanbul','scheduleState':{'status':'RUNNING'}}}})
        self.step('POST','/prod/graphql',{'data':{'startSchedule':{'__typename':'ScheduleStateResult','scheduleState':{'status':'RUNNING'}}}})
        self.run_op('dagster-schedule',server='https://org.dagster.cloud/prod',plan_file=p['dagster_schedules.json'])
        self.assertEqual(self.received[2][2]['variables']['s']['scheduleName'],'gece_yukleme_schedule')
        receipt=json.loads((self.output/'dagster-schedules.json').read_text())
        self.assertEqual((receipt['started'],receipt['already_running']),(['gece_yukleme_schedule'],['haftalik_rapor_schedule']))

    def test_dagster_schedule_stale_code_is_not_started(self):
        p=self.plan('dagster-schedule')
        self.step('POST','/prod/graphql',{'data':{'scheduleOrError':{'__typename':'Schedule','cronSchedule':'0 3 * * *','executionTimezone':'Europe/Istanbul'}}})
        with self.assertRaisesRegex(native.NativeError,'eşleşmedi'):self.run_op('dagster-schedule',server='https://org.dagster.cloud/prod',plan_file=p['dagster_schedules.json'])
        self.assertEqual(len(self.received),1)

    def airbyte_steps(self,streams):
        s,d='00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002'
        self.step('GET','/v1/sources/'+s,{'sourceId':s,'workspaceId':'ws'});self.step('GET','/v1/destinations/'+d,{'destinationId':d,'workspaceId':'ws'})
        self.step('GET','/v1/connections?workspaceIds=ws&limit=100',{'data':[{'name':'Başka'}]})
        self.listing('GET','/v1/streams?sourceId='+s+'&destinationId='+d,streams)

    def test_airbyte_connection_matches_streams_and_creates_inactive(self):
        p=self.plan('airbyte-connection');conn='00000000-0000-0000-0000-0000000000c1'
        modes=['incremental_deduped_history','incremental_append','full_refresh_overwrite','full_refresh_append']
        self.airbyte_steps([{'streamName':n,'syncModes':modes} for n in ('contacts','deals','owners','tickets')])
        self.step('POST','/v1/connections',{'connectionId':conn})
        self.step('GET','/v1/connections/'+conn,{'status':'inactive','configurations':{'streams':[{'name':n} for n in ('contacts','deals','owners')]}})
        self.run_op('airbyte-connection',plan_file=p['airbyte_connection.json'])
        body=self.received[4][2]
        self.assertEqual(body['status'],'inactive');self.assertEqual(body['configurations']['streams'][0],{'name':'contacts','syncMode':'incremental_deduped_history','cursorField':['updatedAt'],'primaryKey':[['id']]})

    def test_airbyte_connection_unsupported_mode_stops_before_create(self):
        p=self.plan('airbyte-connection')
        self.airbyte_steps([{'streamName':'contacts','syncModes':['full_refresh_overwrite']},{'streamName':'deals','syncModes':['incremental_append']},{'streamName':'owners','syncModes':['full_refresh_overwrite']}])
        with self.assertRaisesRegex(native.NativeError,'desteklemiyor'):self.run_op('airbyte-connection',plan_file=p['airbyte_connection.json'])
        self.assertFalse(any(r[0]=='POST' for r in self.received))

    def test_fivetran_schema_changes_only_differing_tables_and_frequency(self):
        p=self.plan('fivetran-schema');base='/v1/connections/conn_1'
        live={'crm':{'tables':{'contacts':{'enabled':True},'deals':{'enabled':False},'email_events':{'enabled':True},'other':{'enabled':True}}},'erp':{'tables':{'invoices':{'enabled':True}}}}
        after={'crm':{'tables':{'contacts':{'enabled':True},'deals':{'enabled':True},'email_events':{'enabled':False}}},'erp':{'tables':{'invoices':{'enabled':True}}}}
        self.step('GET',base+'/schemas',{'code':'Success','data':{'schemas':live}})
        self.step('PATCH',base+'/schemas/crm/tables/deals',{'code':'Success'},payload={'enabled':True})
        self.step('PATCH',base+'/schemas/crm/tables/email_events',{'code':'Success'},payload={'enabled':False})
        self.step('GET',base,{'data':{'sync_frequency':1440}});self.step('PATCH',base,{},payload={'sync_frequency':360});self.step('GET',base,{'data':{'sync_frequency':360}})
        self.step('GET',base+'/schemas',{'code':'Success','data':{'schemas':after}})
        self.run_op('fivetran-schema',connection_id='conn_1',plan_file=p['fivetran_tablolar.json'])
        receipt=json.loads((self.output/'fivetran-schema.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['changed'],['crm.deals=açık','crm.email_events=kapalı']);self.assertEqual(receipt['sync_frequency'],{'before':1440,'after':360})

    def glossary_terms(self):
        import csv as _csv
        rows=list(_csv.DictReader(io.StringIO(next(o for o in OPERATIONS if o['operation']=='purview-glossary')['input_files']['kurulum/purview_sozluk.csv'])))
        return rows

    def test_purview_glossary_creates_parents_first_and_links_isa(self):
        p=self.plan('purview-glossary');rows=self.glossary_terms();v='api-version=2023-09-01';base='/datamap/api/atlas/v2/glossary'
        self.listing('GET',base+'?'+v,[{'guid':'g1','name':'Glossary'},{'guid':'g2','name':'Başka'}],request_headers={'Authorization':'Bearer test-only-secret'})
        self.listing('GET',base+'/g1/terms?limit=1000&offset=0&'+v,[{'name':'Eski terim','guid':'x'}])
        for i,_ in enumerate(rows):self.step('POST',base+'/term?includeTermHierarchy=true&'+v,{'guid':'t'+str(i)})
        def back():
            sent=[r[2] for r in self.received if r[0]=='POST']
            return json.dumps([{'name':b['name'],'guid':'t'+str(i)} for i,b in enumerate(sent)]).encode()
        self.step('GET',base+'/g1/terms?limit=1000&offset=0&'+v,back)
        self.run_op('purview-glossary',account='kurum-purview',glossary_name='Glossary',sozluk_file=p['purview_sozluk.csv'])
        sent=[r[2] for r in self.received if r[0]=='POST'];names=[b['name'] for b in sent]
        child=next(b for b in sent if b['name']=='Aktif Müşteri')
        self.assertLess(names.index('Müşteri'),names.index('Aktif Müşteri'))
        self.assertEqual(child['isA'],[{'termGuid':'t'+str(names.index('Müşteri'))}])
        self.assertEqual(sent[0]['resources'],[{'displayName':'Kaynak','url':'https://wiki.kurum.local/musteri'}])
        self.assertNotIn('contacts',sent[0])  # e-mail stewards are not guessed as object ids
        self.assertGreater(json.loads((self.output/'purview-glossary.json').read_text())['contacts_skipped'],0)

    def test_purview_glossary_existing_term_stops_everything(self):
        p=self.plan('purview-glossary');v='api-version=2023-09-01';base='/datamap/api/atlas/v2/glossary'
        self.listing('GET',base+'?'+v,[{'guid':'g1','name':'Glossary'}])
        self.listing('GET',base+'/g1/terms?limit=1000&offset=0&'+v,[{'name':'Ciro'}])
        with self.assertRaisesRegex(native.NativeError,'Ciro'):self.run_op('purview-glossary',account='kurum-purview',glossary_name='Glossary',sozluk_file=p['purview_sozluk.csv'])
        self.assertFalse(any(r[0]=='POST' for r in self.received))

    def test_openmetadata_glossary_nests_terms_by_fqn(self):
        p=self.plan('openmetadata-glossary');rows=self.glossary_terms()
        self.step('GET','/api/v1/glossaries/name/Kurumsal_Sozluk',status=404)
        self.step('POST','/api/v1/glossaries',{'id':'gid'})
        def term():
            body=self.received[-1][2]
            return {'fullyQualifiedName':body.get('parent','Kurumsal_Sozluk')+'.'+body['name']}
        for _ in rows:self.step('POST','/api/v1/glossaryTerms',term)
        self.step('GET','/api/v1/glossaryTerms?glossary=gid&limit=1000',lambda:{'data':[{'displayName':r[2]['displayName']} for r in self.received if r[1]=='/api/v1/glossaryTerms']})
        self.run_op('openmetadata-glossary',server='https://om.example.com',glossary_name='Kurumsal Sözlük',sozluk_file=p['purview_sozluk.csv'])
        child=next(r[2] for r in self.received if r[1]=='/api/v1/glossaryTerms' and r[2]['displayName']=='Aktif Müşteri')
        self.assertEqual((child['name'],child['parent']),('Aktif_Musteri','Kurumsal_Sozluk.Musteri'))

    def test_datahub_glossary_creates_node_terms_and_isa_links(self):
        p=self.plan('datahub-glossary');rows=self.glossary_terms()
        self.step('POST','/api/graphql',{'data':{'glossaryNode':None}})
        for _ in rows:self.step('POST','/api/graphql',{'data':{'glossaryTerm':None}})
        self.step('POST','/api/graphql',{'data':{'createGlossaryNode':'urn:li:glossaryNode:kurumsal_sozluk'}})
        last=lambda:self.received[-1][2]['variables']['i']
        for _ in rows:self.step('POST','/api/graphql',lambda:{'data':{'createGlossaryTerm':'urn:li:glossaryTerm:'+last()['id']}})
        children=[r for r in rows if r['Parent Term Name']]
        for _ in children:self.step('POST','/api/graphql',{'data':{'addRelatedTerms':True}})
        names={'urn:li:glossaryTerm:kurumsal_sozluk.'+native.slug(r['Name']).lower():r['Name'] for r in rows}
        for _ in rows:self.step('POST','/api/graphql',lambda:{'data':{'glossaryTerm':{'properties':{'name':names[self.received[-1][2]['variables']['u']]}}}})
        self.run_op('datahub-glossary',server='https://datahub.example.com',glossary_name='Kurumsal Sözlük',sozluk_file=p['purview_sozluk.csv'])
        isa=[r[2]['variables']['i'] for r in self.received if 'addRelatedTerms' in r[2]['query']]
        self.assertIn({'urn':'urn:li:glossaryTerm:kurumsal_sozluk.aktif_musteri','termUrns':['urn:li:glossaryTerm:kurumsal_sozluk.musteri'],'relationshipType':'isA'},isa)

    def test_montecarlo_rules_resolves_warehouse_and_creates_rules(self):
        p=self.plan('montecarlo-rules');dw='00000000-0000-0000-0000-0000000000d1'
        self.step('POST','/graphql',{'data':{'getUser':{'account':{'warehouses':[{'uuid':dw,'name':'snowflake-prod'},{'uuid':'x','name':'diger'}]}}}},request_headers={'x-mcd-id':'test-only-secret'})
        self.step('POST','/graphql',{'data':{'getCustomRules':{'edges':[{'node':{'description':'Eski kural'}}]}}})
        self.step('POST','/graphql',{'data':{'createOrUpdateCustomRule':{'customRule':{'uuid':'00000000-0000-0000-0000-0000000000e1'}}}})
        self.step('POST','/graphql',{'data':{'createOrUpdateCustomRule':{'customRule':{'uuid':'00000000-0000-0000-0000-0000000000e2'}}}})
        self.step('POST','/graphql',{'data':{'getCustomRules':{'edges':[{'node':{'description':d}} for d in ('Eski kural','Siparişte negatif tutar','Müşterisiz sipariş')]}}})
        self.run_op('montecarlo-rules',warehouse_name='snowflake-prod',plan_file=p['mc_kurallar.json'],start_time='2026-11-01T00:00:00Z')
        v=self.received[2][2]['variables']
        self.assertEqual((v['dw'],v['c'][0]['threshold'],v['s']['intervalMinutes']),(dw,0.0,1440))

    def test_montecarlo_rules_refuse_writing_sql(self):
        p=self.plan('montecarlo-rules',edit=lambda n,c:c.replace('SELECT siparis_id, tutar FROM','DELETE FROM',1))
        with self.assertRaisesRegex(native.NativeError,'SELECT'):self.run_op('montecarlo-rules',warehouse_name='snowflake-prod',plan_file=p['mc_kurallar.json'])
        self.assertFalse(self.received)

    def test_soda_scan_triggers_by_header_id_and_writes_checks(self):
        self.step('POST','/api/v1/scans',b'',status=201,headers={'X-Soda-Scan-Id':'scan-9'})
        self.step('GET','/api/v1/scans/scan-9',{'state':'executing'})
        self.step('GET','/api/v1/scans/scan-9',{'state':'completedWithFailures','checks':[{'name':'row_count > 0','evaluationStatus':'pass'},{'name':'missing_count(email) = 0','evaluationStatus':'fail'}]})
        with self.nap():self.run_op('soda-scan',server='https://cloud.soda.io',scan_definition='gunluk_tarama')
        self.assertEqual(self.received[0][2],b'scanDefinition=gunluk_tarama')
        self.assertEqual(json.loads((self.output/'soda-scan.json').read_text())['failed'],1)
        self.assertEqual(len(self.csv_out('soda_kontrolleri.csv')),2)

    def test_soda_scan_failed_state_is_not_success(self):
        self.step('POST','/api/v1/scans',b'',status=201,headers={'X-Soda-Scan-Id':'scan-9'})
        self.step('GET','/api/v1/scans/scan-9',{'state':'failed'})
        with self.assertRaises(native.NativeError):self.run_op('soda-scan',server='https://cloud.soda.io',scan_definition='gunluk_tarama')
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['scan_id'],'scan-9')

    def ga4_lists(self,dims,mets,keys):
        b='/v1beta/properties/123'
        self.step('GET',b+'/customDimensions?pageSize=200',{'customDimensions':dims})
        self.step('GET',b+'/customMetrics?pageSize=200',{'customMetrics':mets})
        self.step('GET',b+'/keyEvents?pageSize=200',{'keyEvents':keys})

    def test_ga4_admin_checks_quota_and_duplicates_then_creates_everything(self):
        p=self.plan('ga4-admin');plan=self.sample('ga4-admin','ga4_plan.json');b='/v1beta/properties/123'
        self.ga4_lists([{'parameterName':'eski','scope':'EVENT'}],[],[{'eventName':'first_visit'}])
        self.step('GET','/v1alpha/properties/123/bigQueryLinks?pageSize=200',{'bigqueryLinks':[]})
        for _ in plan['custom_dimensions']:self.step('POST',b+'/customDimensions',{})
        self.step('POST',b+'/customMetrics',{})
        for _ in plan['key_events']:self.step('POST',b+'/keyEvents',{})
        self.step('POST','/v1alpha/properties/123/bigQueryLinks',{},payload=plan['bigquery_link'])
        self.ga4_lists([{'parameterName':d['parameterName'],'scope':d['scope']} for d in plan['custom_dimensions']],[{'parameterName':'indirim_tutari'}],[{'eventName':'purchase'},{'eventName':'sign_up'}])
        self.run_op('ga4-admin',property_id='123',plan_file=p['ga4_plan.json'])
        self.assertFalse(self.queue)
        self.assertEqual(len(json.loads((self.output/'ga4-admin.json').read_text())['created']),6)

    def test_ga4_admin_user_scope_quota_stops_before_writing(self):
        p=self.plan('ga4-admin')
        self.ga4_lists([{'parameterName':'u'+str(i),'scope':'USER'} for i in range(25)],[],[])
        with self.assertRaisesRegex(native.NativeError,'USER'):self.run_op('ga4-admin',property_id='123',plan_file=p['ga4_plan.json'])
        self.assertFalse(any(r[0]=='POST' for r in self.received))

    def test_pinecone_index_waits_until_ready_and_checks_dimension(self):
        p=self.plan('pinecone-index')
        self.step('GET','/indexes/dokuman-parcalari',status=404,request_headers={'Api-Key':'test-only-secret','X-Pinecone-API-Version':'2025-04'})
        self.step('POST','/indexes',{'name':'dokuman-parcalari'},status=201)
        self.step('GET','/indexes/dokuman-parcalari',{'status':{'ready':False,'state':'Initializing'}})
        self.step('GET','/indexes/dokuman-parcalari',{'status':{'ready':True,'state':'Ready'},'dimension':1536,'metric':'cosine','host':'dokuman-x.svc.pinecone.io'})
        with self.nap():self.run_op('pinecone-index',plan_file=p['pinecone_index.json'])
        self.assertEqual(self.received[1][2]['spec'],{'serverless':{'cloud':'aws','region':'us-east-1'}})
        self.assertEqual(json.loads((self.output/'pinecone-indexes.json').read_text())['hosts'],{'dokuman-parcalari':'dokuman-x.svc.pinecone.io'})

    def test_qdrant_collection_creates_vectors_and_payload_indexes(self):
        p=self.plan('qdrant-collection');c='/collections/dokuman_parcalari'
        self.step('GET',c+'/exists',{'result':{'exists':False}},request_headers={'api-key':'test-only-secret'})
        self.step('PUT',c,{'result':True},payload={'vectors':{'size':1536,'distance':'Cosine','on_disk':True}})
        for f,s in sorted({'kaynak':'keyword','dil':'keyword','tarih':'datetime'}.items()):self.step('PUT',c+'/index?wait=true',{'result':{'status':'completed'}},payload={'field_name':f,'field_schema':s})
        self.step('GET',c,{'result':{'status':'green','config':{'params':{'vectors':{'size':1536,'distance':'Cosine'}}},'payload_schema':{'kaynak':{},'dil':{},'tarih':{}}}})
        self.run_op('qdrant-collection',server='https://q.cloud.qdrant.io:6333',plan_file=p['qdrant_collection.json'])
        self.assertFalse(self.queue)

    def test_qdrant_existing_collection_is_untouched(self):
        p=self.plan('qdrant-collection')
        self.step('GET','/collections/dokuman_parcalari/exists',{'result':{'exists':True}})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('qdrant-collection',server='https://q.cloud.qdrant.io:6333',plan_file=p['qdrant_collection.json'])
        self.assertEqual(len(self.received),1)

    def test_powerbi_scan_batches_workspaces_and_merges_results(self):
        ids=['%08d-0000-0000-0000-000000000000'%i for i in range(150)];admin='/v1.0/myorg/admin/workspaces/'
        flags='lineage=True&datasourceDetails=True&getArtifactUsers=True&datasetSchema=False&datasetExpressions=False'
        self.listing('GET',admin+'modified?excludePersonalWorkspaces=true&excludeInActiveWorkspaces=true',[{'id':i} for i in ids])
        for n,chunk in enumerate([ids[:100],ids[100:]]):
            scan='%08d-1111-1111-1111-111111111111'%n
            self.step('POST',admin+'getInfo?'+flags,{'id':scan,'status':'NotStarted'},status=202,payload={'workspaces':chunk})
            self.step('GET',admin+'scanStatus/'+scan,{'status':'Succeeded'})
            self.step('GET',admin+'scanResult/'+scan,{'workspaces':[{'id':chunk[0],'name':'Alan '+str(n),'state':'Active','reports':[{}],'datasets':[{},{}],'users':[{'groupUserAccessRight':'Admin'}]}],
                                                      'datasourceInstances':[{'datasourceInstanceId':str(n)}]})
        self.run_op('powerbi-scan')
        merged=json.loads((self.output/'scan_result.json').read_text(encoding='utf-8'))
        self.assertEqual((len(merged['workspaces']),len(merged['datasourceInstances'])),(2,2))
        self.assertEqual(self.csv_out('calisma_alanlari.csv')[0]['model'],'2')

    def test_snowflake_usage_runs_fixed_selects_and_writes_csvs(self):
        cols=lambda *n:{'rowType':[{'name':x} for x in n]}
        results=[self.sf_result('a',resultSetMetaData=cols('WAREHOUSE_NAME','KREDI'),data=[['ETL_WH','120.5'],['BI_WH','40']]),self.sf_result('b',resultSetMetaData=cols('WAREHOUSE_NAME','KREDI'),data=[['ESKI_WH','3']]),
                 self.sf_result('c',resultSetMetaData=cols('KULLANICI','ROL'),data=[['AHMET','ACCOUNTADMIN']]),self.sf_result('d',resultSetMetaData=cols('KULLANICI'),data=[])]
        for r in results:self.step('POST',self.SF_PATH,r)
        with patch.object(native.uuid,'uuid4',return_value=native.uuid.UUID(self.SF_UUID)):
            self.run_op('snowflake-usage',server='https://a.snowflakecomputing.com',warehouse='WH',days=14)
        sent=[r[2]['statement'] for r in self.received]
        self.assertTrue(all(s.startswith('SELECT') for s in sent));self.assertIn('DATEADD(day,-14,',sent[0])
        self.assertEqual(self.csv_out('kullanilmayan_depolar.csv'),[{'warehouse_name':'ESKI_WH','kredi':'3'}])
        self.assertEqual([r['satir'] for r in self.csv_out('ozet.csv')],['2','1','1','0'])

    def test_databricks_cost_follows_result_chunks(self):
        wh='0123456789abcdef'
        self.step('POST','/api/2.0/sql/statements',{'statement_id':'s1','status':{'state':'PENDING'}})
        self.step('GET','/api/2.0/sql/statements/s1',{'statement_id':'s1','status':{'state':'SUCCEEDED'},'manifest':{'schema':{'columns':[{'name':'urun'},{'name':'liste_tutari'}]}},
                  'result':{'data_array':[['JOBS','10']],'next_chunk_internal_link':'/api/2.0/sql/statements/s1/result/chunks/1'}})
        self.step('GET','/api/2.0/sql/statements/s1/result/chunks/1',{'data_array':[['SQL','4']]})
        for n in (2,3):self.step('POST','/api/2.0/sql/statements',{'statement_id':'s'+str(n),'status':{'state':'SUCCEEDED'},'manifest':{'schema':{'columns':[{'name':'x'}]}},'result':{'data_array':[]}})
        with self.nap():self.run_op('databricks-cost',workspace_url='https://w.cloud.databricks.com',warehouse_id=wh)
        self.assertEqual(self.received[0][2]['warehouse_id'],wh);self.assertIn('system.billing.usage',self.received[0][2]['statement'])
        self.assertEqual([r['urun'] for r in self.csv_out('urun_maliyeti.csv')],['JOBS','SQL'])

    def test_databricks_cost_rejects_foreign_chunk_link(self):
        self.step('POST','/api/2.0/sql/statements',{'statement_id':'s1','status':{'state':'SUCCEEDED'},'manifest':{'schema':{'columns':[]}},'result':{'next_chunk_internal_link':'/api/2.0/other'}})
        with self.assertRaisesRegex(native.NativeError,'beklenmeyen'):self.run_op('databricks-cost',workspace_url='https://w.cloud.databricks.com',warehouse_id='0123456789abcdef')

    def test_qlik_reload_history_pages_and_aggregates_per_app(self):
        now=native.time.time();t=lambda h:native.utc(now-h*3600)
        self.step('GET','/api/v1/reloads?limit=100',{'data':[{'appId':'a1','status':'FAILED','creationTime':t(5)},{'appId':'a1','status':'SUCCEEDED','creationTime':t(30),'startTime':t(30),'endTime':native.utc(now-30*3600+120)}],
                  'links':{'next':{'href':'/api/v1/reloads?limit=100&next=2'}}})
        self.step('GET','/api/v1/reloads?limit=100&next=2',{'data':[{'appId':'a2','status':'SUCCEEDED','creationTime':t(2),'startTime':t(2),'endTime':native.utc(now-2*3600+60)},{'appId':'a3','status':'FAILED','creationTime':t(24*60)}]})
        self.step('GET','/api/v1/items?resourceType=app&limit=100',{'data':[{'resourceId':'a1','name':'Satış'},{'resourceId':'a2','name':'Stok'}]})
        self.run_op('qlik-reload-history',tenant='https://t.eu.qlikcloud.com',days=30)
        rows=self.csv_out('reload_gecmisi.csv')
        self.assertEqual([(r['uygulama'],r['reload'],r['basarisiz'],r['son_durum']) for r in rows],[('Satış','2','1','FAILED'),('Stok','1','0','SUCCEEDED')])
        self.assertEqual(rows[0]['ort_sure_sn'],'120')

    def tableau_signin(self):
        self.step('POST','/api/3.24/auth/signin',{'credentials':{'token':'tok','site':{'id':'site1'}}})

    def test_tableau_usage_sums_views_per_workbook_and_signs_out(self):
        self.tableau_signin();b='/api/3.24/sites/site1'
        self.step('GET',b+'/views?includeUsageStatistics=true&pageSize=100&pageNumber=1',{'pagination':{'totalAvailable':'3'},'views':{'view':[
            {'workbook':{'id':'w1'},'usage':{'totalViewCount':'10'}},{'workbook':{'id':'w1'},'usage':{'totalViewCount':'5'}},{'workbook':{'id':'w2'},'usage':{'totalViewCount':'0'}}]}},request_headers={'X-Tableau-Auth':'tok'})
        self.step('GET',b+'/workbooks?pageSize=100&pageNumber=1',{'pagination':{'totalAvailable':'2'},'workbooks':{'workbook':[{'id':'w1','name':'Satış'},{'id':'w2','name':'Eski'}]}})
        self.step('POST','/api/3.24/auth/signout',{})
        self.run_op('tableau-usage',server='https://x.online.tableau.com',api_version='3.24')
        rows=self.csv_out('calisma_kitabi_kullanimi.csv')
        self.assertEqual([(r['calisma_kitabi'],r['toplam_izlenme'],r['kullanilmiyor']) for r in rows],[('Eski','0','evet'),('Satış','15','')])

    def test_airflow_history_aggregates_runs_and_failed_tasks(self):
        def path(p):return p+'&limit=100&offset=0'
        with patch.object(native.time,'time',return_value=1800000000):
            since=native.ident(native.utc(1800000000-30*86400))
            self.step('GET',path('/api/v2/dags/~/dagRuns?run_after_gte='+since),{'total_entries':3,'dag_runs':[
                {'dag_id':'gece','state':'success','run_after':'1','start_date':'2026-10-01T02:00:00Z','end_date':'2026-10-01T02:10:00Z'},
                {'dag_id':'gece','state':'failed','run_after':'2'},{'dag_id':'rapor','state':'success','run_after':'1'}]})
            self.step('GET',path('/api/v2/dags/~/dagRuns/~/taskInstances?state=failed&run_after_gte='+since),{'total_entries':2,'task_instances':[{'dag_id':'gece','task_id':'erp_yukle'},{'dag_id':'gece','task_id':'erp_yukle'}]})
            self.run_op('airflow-history',server='https://af.example.com',api_version='3.2')
        rows=self.csv_out('dag_ozeti.csv')
        self.assertEqual([(r['dag_id'],r['basarisiz'],r['basari_yuzde'],r['ort_sure_sn'],r['son_durum']) for r in rows],[('gece','1','50.0','600','failed'),('rapor','0','100.0','','success')])
        self.assertEqual(self.csv_out('basarisiz_gorevler.csv'),[{'dag_id':'gece','task_id':'erp_yukle','hata_sayisi':'2'}])

    def test_adf_history_follows_continuation_token(self):
        base='/subscriptions/s/resourceGroups/g/providers/Microsoft.DataFactory/factories/f/queryPipelineRuns?api-version=2018-06-01'
        self.step('POST',base,{'value':[{'pipelineName':'PL_Ana','status':'Succeeded','durationInMs':60000,'runStart':'1'},{'pipelineName':'PL_Ana','status':'Failed','runId':'r2','runStart':'2'}],'continuationToken':'tok'})
        self.step('POST',base,{'value':[{'pipelineName':'PL_Yan','status':'Succeeded','durationInMs':1000,'runStart':'1'}]})
        self.run_op('adf-history',subscription_id='s',resource_group='g',factory_name='f',days=7)
        self.assertEqual(self.received[1][2]['continuationToken'],'tok')
        rows=self.csv_out('pipeline_ozeti.csv')
        self.assertEqual([(r['pipeline'],r['basarisiz'],r['son_hata_run_id'],r['ort_sure_sn']) for r in rows],[('PL_Ana','1','r2','60'),('PL_Yan','0','','1')])

    def test_powerbi_deploy_checks_stages_and_waits_for_success(self):
        pl,op='11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222';b='/v1.0/myorg/pipelines/'+pl
        self.step('GET',b+'/stages',{'value':[{'order':0,'workspaceId':'w0','workspaceName':'Dev'},{'order':1,'workspaceId':'w1','workspaceName':'Test'},{'order':2}]})
        self.step('POST',b+'/deployAll',{'id':op},status=202)
        self.step('GET',b+'/operations/'+op,{'status':'Executing'})
        self.step('GET',b+'/operations/'+op,{'status':'Succeeded','executionPlan':{'steps':[{'status':'Succeeded'},{'status':'Succeeded'}]}})
        with self.nap():self.run_op('powerbi-deploy',pipeline_id=pl,source_stage=0)
        self.assertEqual(self.received[1][2]['options'],{'allowCreateArtifact':True,'allowOverwriteArtifact':True,'allowPurgeData':False,'allowTakeOver':False})
        receipt=json.loads((self.output/'powerbi-deployment.json').read_text())
        self.assertEqual((receipt['from'],receipt['to'],receipt['steps']),('Dev','Test',2))

    def test_powerbi_deploy_unassigned_target_stage_never_deploys(self):
        pl='11111111-1111-1111-1111-111111111111'
        self.step('GET','/v1.0/myorg/pipelines/'+pl+'/stages',{'value':[{'order':0,'workspaceId':'w0'},{'order':1},{'order':2}]})
        with self.assertRaisesRegex(native.NativeError,'atanmış'):self.run_op('powerbi-deploy',pipeline_id=pl,source_stage=0)
        self.assertEqual(len(self.received),1)

    def test_powerbi_refresh_schedule_saves_previous_and_reads_back(self):
        ws,ds='11111111-1111-1111-1111-111111111111','33333333-3333-3333-3333-333333333333'
        path='/v1.0/myorg/groups/'+ws+'/datasets/'+ds+'/refreshSchedule'
        old={'days':['Sunday'],'times':['01:00'],'enabled':True,'localTimeZoneId':'UTC','notifyOption':'NoNotification'}
        self.step('GET',path,old)
        self.step('PATCH',path,b'',payload={'value':{'days':['Monday','Friday'],'times':['06:00','12:30'],'enabled':True,'localTimeZoneId':'Turkey Standard Time','notifyOption':'MailOnFailure'}})
        self.step('GET',path,{'days':['Monday','Friday'],'times':['06:00','12:30'],'enabled':True,'localTimeZoneId':'Turkey Standard Time'})
        self.run_op('powerbi-refresh-schedule',workspace_id=ws,dataset_id=ds,days=['Friday','Monday'],times=['12:30','06:00'],time_zone='Turkey Standard Time')
        self.assertEqual(json.loads((self.output/'run-state.json').read_text())['previous_schedule'],old)

    def test_powerbi_refresh_schedule_rejects_odd_minutes_without_network(self):
        with self.assertRaisesRegex(native.NativeError,'SS:00'):
            self.run_op('powerbi-refresh-schedule',workspace_id='11111111-1111-1111-1111-111111111111',dataset_id='33333333-3333-3333-3333-333333333333',days=['Monday'],times=['06:15'],time_zone='UTC')
        self.assertFalse(self.received)

    def rls_definition(self,members=()):
        role="role 'Bolge Marmara'\n\tmodelPermission: read\n\n\ttablePermission Magaza = [Bolge] = \"Marmara\"\n"+''.join(f"\n\tmember '{m}' = user\n\t\tidentityProvider: AzureAD\n" for m in members)
        enc=lambda t:base64.b64encode(t.encode()).decode()
        return {'definition':{'parts':[{'path':'definition/model.tmdl','payload':enc('model Model\n'),'payloadType':'InlineBase64'},
                                       {'path':'definition/roles/Bolge Marmara.tmdl','payload':enc(role),'payloadType':'InlineBase64'},
                                       {'path':'definition/roles/Bolge Ege.tmdl','payload':enc('role \'Bolge Ege\'\n\tmodelPermission: read\n'),'payloadType':'InlineBase64'}]}}

    def test_powerbi_rls_members_adds_only_missing_members(self):
        p=self.plan('powerbi-rls-members');ws,sm='11111111-1111-1111-1111-111111111111','44444444-4444-4444-4444-444444444444'
        base='/v1/workspaces/'+ws+'/semanticModels/'+sm
        self.step('POST',base+'/getDefinition?format=TMDL',self.rls_definition(['AYSE.KAYA@kurum.com.tr']))
        self.step('POST',base+'/updateDefinition',{})
        def back():
            return {'definition':{'parts':next(r[2] for r in self.received if r[1].endswith('updateDefinition'))['definition']['parts']}}
        self.step('POST',base+'/getDefinition?format=TMDL',back)
        self.run_op('powerbi-rls-members',workspace_id=ws,semantic_model_id=sm,members_file=p['rls_uyeleri.csv'])
        parts={x['path']:base64.b64decode(x['payload']).decode() for x in self.received[1][2]['definition']['parts']}
        marmara=parts['definition/roles/Bolge Marmara.tmdl']
        self.assertIn("\tmember 'marmara-satis@kurum.com.tr' = group\n\t\tidentityProvider: AzureAD",marmara)
        self.assertEqual(marmara.count('ayse.kaya'),0);self.assertIn('tablePermission Magaza',marmara)
        self.assertIn("member 'can.demir@kurum.com.tr' = user",parts['definition/roles/Bolge Ege.tmdl'])
        receipt=json.loads((self.output/'powerbi-rls.json').read_text())
        self.assertEqual((len(receipt['added']),receipt['already_present']),(2,1))

    def test_powerbi_rls_members_unknown_role_never_updates(self):
        p=self.plan('powerbi-rls-members',edit=lambda n,c:c.replace('Bolge Ege','Bolge Karadeniz'));ws,sm='11111111-1111-1111-1111-111111111111','44444444-4444-4444-4444-444444444444'
        self.step('POST','/v1/workspaces/'+ws+'/semanticModels/'+sm+'/getDefinition?format=TMDL',self.rls_definition())
        with self.assertRaisesRegex(native.NativeError,'Bolge Karadeniz'):self.run_op('powerbi-rls-members',workspace_id=ws,semantic_model_id=sm,members_file=p['rls_uyeleri.csv'])
        self.assertEqual(len(self.received),1)

    def test_powerbi_workspace_access_adds_missing_and_reports_different_rights(self):
        p=self.plan('powerbi-workspace-access');ws='11111111-1111-1111-1111-111111111111';path='/v1.0/myorg/groups/'+ws+'/users'
        group='00000000-0000-0000-0000-0000000000a1'
        self.step('GET',path,{'value':[{'emailAddress':'Ayse.Kaya@kurum.com.tr','groupUserAccessRight':'Viewer'}]})
        self.step('POST',path,b'',payload={'groupUserAccessRight':'Viewer','principalType':'Group','identifier':group})
        self.step('GET',path,{'value':[{'emailAddress':'ayse.kaya@kurum.com.tr','groupUserAccessRight':'Viewer'},{'identifier':group,'groupUserAccessRight':'Viewer'}]})
        self.run_op('powerbi-workspace-access',workspace_id=ws,access_file=p['calisma_alani_erisimi.csv'])
        receipt=json.loads((self.output/'powerbi-workspace-access.json').read_text())
        self.assertEqual((receipt['added'],receipt['different_right_not_changed']),([group],['ayse.kaya@kurum.com.tr (Viewer)']))

    def test_qlik_publish_new_app_and_requires_flag_to_republish(self):
        cfg=dict(tenant='https://t.eu.qlikcloud.com',app_id='app1',space_id='sp1')
        self.step('GET','/api/v1/spaces/sp1',{'id':'sp1','type':'managed'})
        self.step('GET','/api/v1/apps/app1',{'attributes':{'id':'app1','name':'Satış'}})
        self.step('GET','/api/v1/items?resourceType=app&spaceId=sp1&limit=100',{'data':[]})
        self.step('POST','/api/v1/apps/app1/publish',{'attributes':{'id':'pub1'}},payload={'spaceId':'sp1','data':'source','attributes':{'name':'Satış'}})
        self.step('GET','/api/v1/apps/pub1',{'attributes':{'id':'pub1','published':True,'originAppId':'app1','spaceId':'sp1'}})
        self.run_op('qlik-publish',**cfg)
        self.output=Path(self.temp.name)/'run2'
        self.step('GET','/api/v1/spaces/sp1',{'id':'sp1','type':'managed'})
        self.step('GET','/api/v1/apps/app1',{'attributes':{'id':'app1','name':'Satış'}})
        self.step('GET','/api/v1/items?resourceType=app&spaceId=sp1&limit=100',{'data':[{'resourceId':'pub1','resourceAttributes':{'originAppId':'app1'}}]})
        with self.assertRaisesRegex(native.NativeError,'republish'):self.run_op('qlik-publish',**cfg)
        self.assertEqual(sum(1 for r in self.received if r[0] in ('POST','PUT')),1)

    def test_qlik_reload_schedule_refuses_second_active_task(self):
        cfg=dict(tenant='https://t.eu.qlikcloud.com',app_id='app1',timezone='Europe/Istanbul',start='2026-11-01T06:00:00',recurrence=['RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0'])
        self.step('GET','/api/v1/reload-tasks?appId=app1&limit=100',{'data':[{'id':'t0','state':'Disabled'}]})
        self.step('POST','/api/v1/reload-tasks',{'id':'t1'})
        self.step('GET','/api/v1/reload-tasks/t1',{'appId':'app1','recurrence':cfg['recurrence'],'state':'Enabled'})
        self.run_op('qlik-reload-schedule',**cfg)
        self.assertEqual(self.received[1][2]['type'],'scheduled_reload')
        self.output=Path(self.temp.name)/'run2'
        self.step('GET','/api/v1/reload-tasks?appId=app1&limit=100',{'data':[{'id':'t1','state':'Enabled'}]})
        with self.assertRaisesRegex(native.NativeError,'zaten var'):self.run_op('qlik-reload-schedule',**cfg)

    def test_qlik_space_access_resolves_people_and_adds_missing(self):
        p=self.plan('qlik-space-access');path='/api/v1/spaces/sp1/assignments'
        self.step('GET','/api/v1/spaces/sp1',{'id':'sp1','type':'managed'})
        self.step('GET','/api/v1/users?filter='+native.ident('email eq "ayse.kaya@kurum.com.tr"'),{'data':[{'id':'u1'}]})
        self.step('GET','/api/v1/groups?filter='+native.ident('name eq "Satis Analistleri"'),{'data':[{'id':'g1'}]})
        self.step('GET',path+'?limit=100',{'data':[{'assigneeId':'u1','roles':['consumer']}]})
        self.step('POST',path,{},payload={'type':'group','assigneeId':'g1','roles':['consumer','dataconsumer']})
        self.step('GET',path+'?limit=100',{'data':[{'assigneeId':'u1','roles':['consumer']},{'assigneeId':'g1','roles':['dataconsumer','consumer']}]})
        self.run_op('qlik-space-access',tenant='https://t.eu.qlikcloud.com',space_id='sp1',members_file=p['alan_uyeleri.csv'])
        receipt=json.loads((self.output/'qlik-space-access.json').read_text())
        self.assertEqual((receipt['added'],receipt['already_present']),(['Satis Analistleri'],['ayse.kaya@kurum.com.tr']))

    def test_tableau_permissions_adds_missing_capabilities_reports_conflicts(self):
        p=self.plan('tableau-permissions');b='/api/3.24/sites/site1'
        self.tableau_signin()
        self.step('GET',b+'/projects?filter='+native.ident('name:eq:Satış'),{'projects':{'project':[{'id':'p1','name':'Satış'}]}})
        self.step('GET',b+'/groups?filter='+native.ident('name:eq:Satış Analistleri'),{'groups':{'group':[{'id':'g1','name':'Satış Analistleri'}]}})
        grant=lambda caps:{'permissions':{'granteeCapabilities':[{'group':{'id':'g1'},'capabilities':{'capability':[{'name':c,'mode':m} for c,m in caps]}}]}}
        self.step('GET',b+'/projects/p1/permissions',grant([('Read','Allow')]))
        self.step('GET',b+'/projects/p1/default-permissions/workbooks',grant([('ExportData','Deny')]))
        self.step('PUT',b+'/projects/p1/default-permissions/workbooks',{},payload=grant([('Read','Allow')]))
        self.step('GET',b+'/projects/p1/default-permissions/workbooks',grant([('ExportData','Deny'),('Read','Allow')]))
        self.step('GET',b+'/projects/p1/default-permissions/datasources',{'permissions':{}})
        self.step('PUT',b+'/projects/p1/default-permissions/datasources',{},payload=grant([('Connect','Allow')]))
        self.step('GET',b+'/projects/p1/default-permissions/datasources',grant([('Connect','Allow')]))
        self.step('POST','/api/3.24/auth/signout',{})
        self.run_op('tableau-permissions',server='https://x.online.tableau.com',api_version='3.24',permissions_file=p['tableau_yetkileri.csv'])
        receipt=json.loads((self.output/'tableau-permissions.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['conflicts_not_changed'],['Satış/workbooks/Satış Analistleri:ExportData (Deny)'])
        self.assertEqual(len(receipt['added']),2)

    def test_tableau_extract_schedule_skips_scheduled_workbooks(self):
        p=self.plan('tableau-extract-schedule');b='/api/3.24/sites/site1'
        self.tableau_signin()
        self.step('GET',b+'/tasks/extractRefreshes',{'tasks':{'task':[{'extractRefresh':{'id':'old','workbook':{'id':'w2'}}}]}})
        self.step('GET',b+'/workbooks?filter='+native.ident('name:eq:Satış Panosu'),{'workbooks':{'workbook':[{'id':'w1','project':{'name':'Satış'}},{'id':'wx','project':{'name':'Arşiv'}}]}})
        self.step('POST',b+'/tasks/extractRefreshes',{'extractRefresh':{'id':'t1'}})
        self.step('GET',b+'/tasks/extractRefreshes/t1',{'task':{'extractRefresh':{'workbook':{'id':'w1'}}}})
        self.step('GET',b+'/workbooks?filter='+native.ident('name:eq:Stok Raporu'),{'workbooks':{'workbook':[{'id':'w2','project':{'name':'Operasyon'}}]}})
        self.step('POST','/api/3.24/auth/signout',{})
        self.run_op('tableau-extract-schedule',server='https://x.online.tableau.com',api_version='3.24',schedule_file=p['extract_zamanlamasi.csv'])
        schedule=self.received[3][2]['schedule']
        self.assertEqual(schedule['frequencyDetails']['start'],'06:00:00');self.assertEqual(schedule['frequencyDetails']['intervals']['interval'][0],{'hours':'24'})
        receipt=json.loads((self.output/'tableau-extract-schedule.json').read_text(encoding='utf-8'))
        self.assertEqual((receipt['created_tasks'],receipt['already_scheduled']),(['t1'],['Stok Raporu']))

    def test_snowflake_grant_sync_grants_missing_and_reports_extra(self):
        root=self.kurulum('snowflake-grant-sync')
        import csv as _csv
        matrix=list(_csv.DictReader(io.StringIO((Path(root)/'yetki_matrisi.csv').read_text(encoding='utf-8-sig'))))
        level={'okuma':'R','yazma':'RW','sahip':'OWN'}
        cols=[{'name':n} for n in ['privilege','granted_on','name']]
        roles=sorted({r['kimlik'] for r in matrix})
        expected={g:sorted(r['veritabani']+'_'+level[r['etkin']] for r in matrix if r['kimlik']==g and r['etkin'] in level) for g in roles}
        first=roles[0]
        for g in roles:
            have=expected[g][1:] if g==first else expected[g]+(['PROD_RAW_RW'] if g==roles[1] else [])
            self.step('POST',self.SF_PATH,self.sf_result('s',resultSetMetaData={'rowType':cols},data=[['USAGE','ROLE',x] for x in have]+[['USAGE','WAREHOUSE','WH']]))
        self.step('POST',self.SF_PATH,self.sf_result('g'))
        self.step('POST',self.SF_PATH,self.sf_result('v',resultSetMetaData={'rowType':cols},data=[['USAGE','ROLE',x] for x in expected[first]]))
        with patch.object(native.uuid,'uuid4',return_value=native.uuid.UUID(self.SF_UUID)):
            self.run_op('snowflake-grant-sync',server='https://a.snowflakecomputing.com',warehouse='WH',kurulum_klasoru=root)
        grant=self.received[len(roles)][2]
        self.assertEqual((grant['statement'],grant['role']),('GRANT ROLE '+expected[first][0]+' TO ROLE '+first,'SECURITYADMIN'))
        self.assertFalse(any('REVOKE' in r[2]['statement'] for r in self.received))
        receipt=json.loads((self.output/'snowflake-grant-sync.json').read_text())
        self.assertEqual((receipt['granted'],receipt['extra_not_revoked']),(1,1))

    def test_uc_grant_sync_patches_only_missing_privileges(self):
        root=self.kurulum('uc-grant-sync');plan=json.loads((Path(root)/'uc_plan.json').read_text(encoding='utf-8'))
        b='/api/2.1/unity-catalog/permissions/'
        grouped={}
        for g in plan['grants']:grouped.setdefault((g['securable_type'],g['full_name']),[]).append(g)
        items=list(grouped.items());(k0,f0),gs0=items[0]
        for i,((k,f),gs) in enumerate(items):
            have=[{'principal':g['principal'],'privileges':g['privileges'][1:] if i==0 and j==0 else g['privileges']} for j,g in enumerate(gs)]
            if i==1:have.append({'principal':'eski_grup','privileges':['SELECT']})
            self.step('GET',b+k+'/'+f,{'privilege_assignments':have})
        self.step('PATCH',b+k0+'/'+f0,{},payload={'changes':[{'principal':gs0[0]['principal'],'add':sorted(gs0[0]['privileges'][:1])}]})
        self.step('GET',b+k0+'/'+f0,{'privilege_assignments':[{'principal':g['principal'],'privileges':g['privileges']} for g in gs0]})
        self.run_op('uc-grant-sync',workspace_url='https://w.cloud.databricks.com',kurulum_klasoru=root)
        self.assertFalse(self.queue)
        rows=self.csv_out('yetki_farki.csv')
        self.assertEqual(sorted(r['durum'] for r in rows),['eksikti → verildi','fazla (geri alınmadı)'])

    def test_uc_grant_sync_apply_false_only_reports(self):
        root=self.kurulum('uc-grant-sync');plan=json.loads((Path(root)/'uc_plan.json').read_text(encoding='utf-8'))
        keys=[]
        for g in plan['grants']:
            if (g['securable_type'],g['full_name']) not in keys:keys.append((g['securable_type'],g['full_name']))
        for k,f in keys:self.step('GET','/api/2.1/unity-catalog/permissions/'+k+'/'+f,{'privilege_assignments':[]})
        self.run_op('uc-grant-sync',workspace_url='https://w.cloud.databricks.com',apply=False,kurulum_klasoru=root)
        self.assertTrue(all(r[0]=='GET' for r in self.received))
        self.assertTrue(all(r['durum']=='eksik' for r in self.csv_out('yetki_farki.csv')))

if __name__=='__main__':unittest.main()
