import json, os, sys, tempfile, unittest, zipfile, hashlib, subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'library'))
import runner, connector, agent

class Engines(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.input=self.root/'input.csv';self.input.write_text('id,region,amount,email\n1,A,20,a@example.test\n2,B,-5,\n2,B,10,b@example.test\n',encoding='utf-8')
 def tearDown(self): self.tmp.cleanup()
 def test_profile_and_finite(self):
  result=runner.execute('profile',self.input);self.assertEqual(result['row_count'],3);self.assertEqual(result['columns'][2]['sum'],25);self.assertEqual(result['columns'][3]['missing'],1);self.assertIsNone(runner.number('NaN'));self.assertIsNone(runner.number('inf'))
 def test_quality_detects_negative_and_duplicate(self):
  result=runner.execute('quality',self.input,{'rules':{'id':{'unique':True},'amount':{'min':0}}});self.assertEqual(len(result['issues']),3);self.assertFalse(result['passed'])
 def test_quality_no_rules_is_explicit(self): self.assertIn('tanımlanmadı',runner.execute('quality',self.input)['note'])
 def test_report_grouping(self):
  result=runner.execute('report',self.input,{'group_by':'region'});self.assertEqual(result['groups'][1]['row_count'],2)
 def test_mask_no_source_modification(self):
  before=self.input.read_bytes();result=runner.execute('mask',self.input,{'mask_fields':['email']});self.assertTrue(all(r['email']=='[MASKED]' for r in result['rows']));self.assertEqual(before,self.input.read_bytes())
 def test_mask_requires_fields(self):
  with self.assertRaises(ValueError): runner.execute('mask',self.input)
 def test_dedupe(self): self.assertEqual(runner.execute('dedupe',self.input,{'keys':['id']})['duplicate_groups'][0]['rows'],[3,4])
 def test_reconcile_rejects_ambiguous_keys(self):
  with self.assertRaisesRegex(ValueError,'benzersiz'):runner.execute('reconcile',self.input,{'keys':['id']},self.input)
 def test_reconcile_and_schema(self):
  left=self.root/'left.csv';right=self.root/'right.csv';left.write_text('id,amount\n1,20\n2,30\n');right.write_text('id,amount,extra\n1,25,x\n3,40,y\n');r=runner.execute('reconcile',left,{'keys':['id']},right);self.assertEqual(r['only_source'],[['2']]);self.assertEqual(r['only_target'],[['3']]);self.assertEqual(r['changed'][0]['fields']['amount']['target'],'25');self.assertEqual(runner.execute('schema',left,compare=right)['added'],['extra'])
 def test_empty_csv_and_invalid_rows(self):
  self.input.write_text('id,amount\n');self.assertEqual(runner.execute('profile',self.input)['row_count'],0)
  self.input.write_text('id,amount\n1,2,3\n')
  with self.assertRaises(ValueError):runner.execute('profile',self.input)
 def test_missing_fields(self):
  with self.assertRaises(ValueError):runner.execute('report',self.input,{'group_by':'missing'})
 def test_html_is_escaped_and_dictionary_exists(self):
  output=self.root/'output';r=runner.execute('dictionary',self.input);r['unsafe']='<script>alert(1)</script>';runner.save(r,'dictionary',output);self.assertIn('&lt;script&gt;', (output/'report.html').read_text(encoding='utf-8'));self.assertTrue((output/'dictionary.md').exists())
 def test_sql(self):
  sql=self.root/'model.sql';sql.write_text('select * from a.sales join b.users on 1=1');self.assertEqual(runner.execute('sql',sql)['files'][0]['tables'],['a.sales','b.users'])
 def test_connector_pagination(self):
  with patch.object(connector,'fetch',side_effect=[{'tables':[{'id':'a'}],'nextPageToken':'next'},{'tables':[{'id':'b'}]}]) as fetch:
   result=connector.inventory({'connector':'bigquery','project_id':'p','dataset_id':'d'});self.assertEqual(result['count'],2);self.assertIn('pageToken=next',fetch.call_args[0][0])
 def test_qlik_pagination_and_origin(self):
  with patch.object(connector,'fetch',side_effect=[{'data':[{'id':'a'}],'links':{'next':{'href':'/api/v1/items?page=2'}}},{'data':[{'id':'b'}]}]):self.assertEqual(connector.inventory({'connector':'qlik','tenant':'https://test.eu.qlikcloud.com'})['count'],2)
  with patch.object(connector,'fetch',return_value={'links':{'next':{'href':'https://evil.example'}}}):
   with self.assertRaises(ValueError):connector.inventory({'connector':'qlik','tenant':'https://test.eu.qlikcloud.com'})
 def test_powerbi(self):
  with patch.object(connector,'fetch',return_value={'value':[{'id':'r'}]}) as f:self.assertEqual(connector.inventory({'connector':'powerbi','workspace_id':'x'})['count'],1);self.assertIn('/groups/x/reports',f.call_args[0][0])

class Packages(unittest.TestCase):
 def test_catalog_packages_integrity_and_unique_ids(self):
  data=json.loads((ROOT/'dist/catalog.json').read_text(encoding='utf-8'));ids=[];downloads=set();planned=0
  for d in data:
   for tool in d['tools']:
    for product in tool['products']:
     for task in product['tasks']:
      ids.append(task['id'])
      self.assertEqual(task['verification']['live'],'not-tested')
      if task['status']=='planned':
       planned+=1;self.assertNotIn('download',task);continue
      file=ROOT/'dist'/task['download'].lstrip('/');downloads.add(file.name)
      self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),task['sha256'])
      with zipfile.ZipFile(file) as z:
       self.assertIsNone(z.testzip());self.assertFalse(any(x.endswith('.html') for x in z.namelist()))
       if task['engine']=='local':
        self.assertIn('main.py',z.namelist());self.assertEqual(task['kind']=='agent','.env.example' in z.namelist());continue
       self.assertIn('native.py',z.namelist());self.assertIn('.env.example',z.namelist())
       self.assertNotIn('sample.csv',z.namelist())
  self.assertEqual(len(ids),len(set(ids)));self.assertGreater(planned,0)
  self.assertEqual({p.name for p in (ROOT/'dist/packages').glob('*.zip')},downloads)
  self.assertEqual(len(json.loads((ROOT/'dist/integration-plan.json').read_text(encoding='utf-8'))),197)
 def test_every_native_package_plan_runs_without_credentials_or_network(self):
  data=json.loads((ROOT/'dist/catalog.json').read_text(encoding='utf-8'));tested=set()
  for d in data:
   for tool in d['tools']:
    for product in tool['products']:
     for task in product['tasks']:
      if not task.get('download') or task['engine']=='local':continue
      tested.add(task['operation'])
      with tempfile.TemporaryDirectory() as tmp:
       with zipfile.ZipFile(ROOT/'dist'/task['download'].lstrip('/')) as z:z.extractall(tmp)
       done=subprocess.run([sys.executable,'native.py','--plan'],cwd=tmp,capture_output=True,text=True,encoding='utf-8')
       self.assertEqual(done.returncode,0,done.stderr);self.assertEqual(json.loads(done.stdout)['network_calls'],0)
       self.assertFalse((Path(tmp)/'output').exists())
  sys.path.insert(0,str(ROOT/'catalog'))
  from native_catalog import OPERATIONS
  self.assertEqual(tested,{x['operation'] for x in OPERATIONS})

class AgentProviders(unittest.TestCase):
 def test_all_provider_payloads_and_response_formats(self):
  for provider,response in [('openai',{'choices':[{'message':{'content':'ok'}}]}),('anthropic',{'content':[{'type':'text','text':'ok'}]}),('ollama',{'message':{'content':'ok'}})]:
   with self.subTest(provider=provider),patch.dict(os.environ,{'MODEL':'test-model','OPENAI_API_KEY':'test','ANTHROPIC_API_KEY':'test'}),patch.object(agent,'request',return_value=response) as call:
    self.assertEqual(agent.complete(provider,'system','evidence'),'ok');self.assertEqual(call.call_args[0][1]['model'],'test-model');self.assertIn('messages',call.call_args[0][1])
 def test_missing_model_fails_before_network(self):
  with patch.dict(os.environ,{'MODEL':''}),patch.object(agent,'request') as call:
   with self.assertRaises(ValueError):agent.complete('openai','s','u')
   call.assert_not_called()
if __name__=='__main__':unittest.main()
