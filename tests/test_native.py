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
                if callable(response):response=response()
                data=response if isinstance(response,bytes) else json.dumps(response).encode()
                self.send_response(expected.get('status',200))
                headers={'Content-Type':'application/octet-stream' if isinstance(response,bytes) else 'application/json',**expected.get('headers',{})}
                for k,v in headers.items():self.send_header(k,v)
                self.end_headers();self.wfile.write(data)
            do_GET=do_POST=do_PUT=handle_request
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


if __name__=='__main__':unittest.main()
