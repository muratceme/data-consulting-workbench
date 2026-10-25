"""Integration product packages: run counters from each tool and the ETL plan agent."""
import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import integration  # noqa: E402

ADF = {'subscription_id': 's', 'resource_group': 'r', 'factory_name': 'f'}
BASE = '/subscriptions/s/resourceGroups/r/providers/Microsoft.DataFactory/factories/f'


class Counters(Base):
    def rows(self):
        with open(self.out / 'satir_mutabakati.csv', encoding='utf-8') as fh:
            return {r['nesne']: r for r in csv.DictReader(fh)}

    def test_adf_copy_activity_counters(self):
        self.server.add('POST', BASE + r'/queryPipelineRuns\?api-version=2018-06-01', {'value': [{'runId': 'run1', 'pipelineName': 'PL'}]})
        self.server.add('POST', BASE + r'/pipelineruns/run1/queryActivityruns\?api-version=2018-06-01', {'value': [
            {'activityName': 'CopyA', 'status': 'Succeeded', 'output': {'rowsRead': 10, 'rowsCopied': 10}},
            {'activityName': 'CopyB', 'status': 'Succeeded', 'output': {'rowsRead': 10, 'rowsCopied': 8, 'rowsSkipped': 2}},
            {'activityName': 'CopyC', 'status': 'Succeeded', 'output': {'rowsRead': 10, 'rowsCopied': 7}}]})
        self.run_op('adf-row-reconcile', **ADF)
        rows = self.rows()
        self.assertEqual((rows['CopyA']['durum'], rows['CopyB']['durum'], rows['CopyC']['durum']), ('eşit', 'fark atlanan satırlarla açıklanıyor', 'FARK'))
        self.assertEqual(self.server.received[1][2]['filters'][0]['values'], ['Copy'])
        self.assertEqual(self.read('ozet.json')['fark'], 1)

    def test_ssis_flow_sources_sinks_and_error_paths(self):
        stats = [{'execution_id': 1, 'package_name': 'p.dtsx', 'start_time': 't', 'package_path': '\\Package\\DFT', 'source_component_name': 'OLE Src', 'destination_component_name': 'Derived',
                  'dataflow_path_name': 'OLE DB Source Output', 'rows_sent': 100},
                 {'execution_id': 1, 'package_name': 'p.dtsx', 'start_time': 't', 'package_path': '\\Package\\DFT', 'source_component_name': 'Derived', 'destination_component_name': 'OLE Dst',
                  'dataflow_path_name': 'Derived Column Output', 'rows_sent': 97},
                 {'execution_id': 1, 'package_name': 'p.dtsx', 'start_time': 't', 'package_path': '\\Package\\DFT', 'source_component_name': 'Derived', 'destination_component_name': 'Reject file',
                  'dataflow_path_name': 'Derived Column Error Output', 'rows_sent': 3}]
        with patch.object(integration, 'sql_source', return_value=FakeSql({'data_statistics': ([], stats)})):
            self.run_op('ssis-row-reconcile', odbc='Driver=x')
        row = self.rows()['\\Package\\DFT']
        self.assertEqual((row['okunan'], row['yazilan'], row['atlanan'], row['durum']), ('100', '97', '3', 'fark atlanan satırlarla açıklanıyor'))

    def test_idmc_login_and_activity_log(self):
        self.server.add('POST', '/ma/api/v2/user/login', {'serverUrl': 'https://usw1.dm-us.informaticacloud.com/saas', 'icSessionId': 'sess'})
        self.server.add('GET', r'/saas/api/v2/activity/activityLog\?rowLimit=200', [{'runId': 5, 'objectName': 'mt_customers', 'successSourceRows': 50, 'successTargetRows': 50,
                                                                                     'failedTargetRows': 0}])
        self.run_op('idmc-row-reconcile', login_url='https://dm-us.informaticacloud.com')
        self.assertEqual(self.server.received[1][3]['Icsessionid'], 'sess')
        self.assertEqual(self.rows()['mt_customers']['durum'], 'eşit')

    def test_powercenter_and_odi_sql(self):
        fake = FakeSql({'session_log': ([], [{'subject_area': 'F', 'workflow_id': 1, 'session_name': 's_load', 'actual_start': 'x', 'succ_source_rows': 10, 'succ_target_rows': 9,
                                               'failed_target_rows': 1, 'failed_source_rows': 0}])})
        with patch.object(integration, 'sql_source', return_value=fake):
            self.run_op('powercenter-row-reconcile', user='u', dsn='d', days=3)
        self.assertIn('REP_SESS_LOG WHERE ACTUAL_START >= SYSDATE - 3', fake.queries[0][1])
        self.out = self.root / 'odi'
        fake = FakeSql({'step_log': ([], [{'sess_no': 1, 'sess_name': 'LOAD', 'step_name': 'MAP_A', 'nb_row': 10, 'nb_ins': 6, 'nb_upd': 4, 'nb_err': 0}])})
        with patch.object(integration, 'sql_source', return_value=fake):
            self.run_op('odi-row-reconcile', user='u', dsn='d', work_schema='ODI_REPO')
        self.assertEqual(self.rows()['MAP_A']['durum'], 'eşit')

    def test_talend_component_metrics_graph(self):
        self.server.add('GET', r'/processing/executables/tasks/t1/executions\?.*', {'items': [{'executionId': 'e1', 'jobName': 'job'}]})
        self.server.add('GET', r'/monitoring/observability/executions/e1/component\?limit=200&offset=0', [
            {'connector_label': 'tDBInput_1', 'target_label': 'tMap_1', 'component_connection_rows_total': 20, 'connection_name': 'output'},
            {'connector_label': 'tMap_1', 'target_label': 'tDBOutput_1', 'component_connection_rows_total': 18, 'connection_name': 'output'},
            {'connector_label': 'tMap_1', 'target_label': 'tLogRow_1', 'component_connection_rows_total': 2, 'connection_name': 'reject'}])
        self.run_op('talend-cloud-row-reconcile', region='eu', task_id='t1')
        self.assertEqual(self.rows()['job']['durum'], 'fark atlanan satırlarla açıklanıyor')

    def test_replicate_with_target_counts(self):
        self.server.add('GET', '/attunityenterprisemanager/api/v1/login', {}, headers={'EnterpriseManager.APISessionID': 'qs'})
        self.server.add('GET', r'/attunityenterprisemanager/api/v1/servers/rs/tasks/t/tables\?action=getstatus.*', {'tablestatuses': [
            {'source_schema': 'dbo', 'source_table': 'orders', 'state': 'TABLE_COMPLETED', 'table_full_load_info': {'transferred_row_count': 100}}]})
        (self.root / 'hedef_sayimlari.csv').write_text('tablo,satir\ndbo.orders,99\n')
        self.run_op('qlik-replicate-row-reconcile', server='https://qem.example', replicate_server='rs', task='t', counts_file='hedef_sayimlari.csv')
        self.assertEqual(self.server.received[1][3]['Enterprisemanager.Apisessionid'], 'qs')
        self.assertEqual(self.rows()['dbo.orders']['durum'], 'FARK')

    def test_fivetran_requires_source_counts_and_reads_log(self):
        fake = FakeSql({'destination_counts': ([], [{'tablo': 'sales.orders', 'satir': 100}]),
                        'fivetran_log': ([], [{'message_data': json.dumps({'schema': 'sales', 'table': 'orders', 'count': 7})}])})
        with patch.object(integration, 'sql_source', return_value=fake), self.assertRaises(native.NativeError):
            self.run_op('fivetran-row-reconcile', destination='snowflake', tables=['sales.orders'])
        (self.root / 'k.csv').write_text('tablo,satir\nsales.orders,100\n')
        self.out = self.root / 'ok'
        with patch.object(integration, 'sql_source', return_value=fake):
            self.run_op('fivetran-row-reconcile', destination='snowflake', tables=['sales.orders'], counts_file='k.csv', log_schema='FIVETRAN_METADATA')
        row = self.rows()['sales.orders']
        self.assertEqual((row['durum'], row['fivetran_degisen']), ('eşit', '7'))

    def test_airbyte_config_api_stream_stats(self):
        self.server.add('POST', '/api/v1/jobs/list', {'jobs': [{'job': {'id': 9}}]})
        self.server.add('POST', '/api/v1/jobs/get', {'job': {'id': 9}, 'attempts': [{'attempt': {'streamStats': [{'streamName': 'users', 'stats': {'recordsEmitted': 5, 'recordsCommitted': 4}}]}}]})
        with patch.dict('os.environ', {'AIRBYTE_TOKEN': 'x'}):
            self.run_op('airbyte-row-reconcile', connection_id='c', config_api=True, server='https://ab.example')
        self.assertEqual(self.rows()['users']['durum'], 'FARK')

    def test_data_services_monitor_log(self):
        logs = self.root / 'logs'; logs.mkdir()
        (logs / 'monitor_01.txt').write_text('/DF_Cust/CUSTOMERS_SRC,STOP,120,1.0,2.0\n/DF_Cust/Query,STOP,120,1.0,2.0\n/DF_Cust/DIM_CUSTOMER,STOP,118,1.0,2.0\n')
        self.run_op('sap-data-services-row-reconcile', monitor_log_dir=str(logs), source_objects=['customers_src'], target_objects=['dim_customer'])
        self.assertEqual(self.rows()['DF_Cust']['durum'], 'FARK')

    def test_goldengate_stats_parsing(self):
        text = ('Sending STATS request to Extract group EXT1 ...\nStart of statistics at 2026-10-01.\nExtracting from HR.EMP to HR.EMP:\n*** Total statistics since 2026-10-01 ***\n'
                'Total inserts 10.00\nTotal updates 2.00\nTotal operations 12.00\nEnd of statistics.\n')
        rep = text.replace('Extracting from HR.EMP to HR.EMP', 'Replicating from HR.EMP to DW.EMP').replace('Total operations 12.00', 'Total operations 11.00')
        self.server.add('POST', '/services/v2/extracts/EXT1/command', {'response': {'reply': text}})
        self.server.add('POST', '/services/v2/replicats/REP1/command', {'response': {'reply': rep}})
        self.run_op('goldengate-row-reconcile', server='https://gg.example', extract='EXT1', replicat='REP1', table_map={'HR.EMP': 'DW.EMP'})
        row = self.rows()['hr.emp']
        self.assertEqual((row['okunan'], row['yazilan'], row['durum']), ('12.0', '11.0', 'FARK'))

    def test_meltano_singer_metrics(self):
        (self.root / 'run.log').write_text(
            'x METRIC: {"type": "counter", "metric": "record_count", "value": 5, "tags": {"stream": "users"}} cmd_type=elb consumer=False name=tap-github producer=True\n'
            'x METRIC: {"type": "counter", "metric": "record_count", "value": 5, "tags": {"stream": "users"}} cmd_type=elb consumer=True name=target-postgres producer=False\n'
            'x METRIC: {"type": "timer", "metric": "sync_duration", "value": 1.2, "tags": {"stream": "users"}} name=tap-github producer=True\n')
        self.run_op('meltano-row-reconcile', log_files=['run.log'])
        self.assertEqual(self.rows()['users']['durum'], 'eşit')


class EtlAgent(Base):
    def test_adf_datasets_and_method_rules(self):
        datasets = {'value': [{'name': 'SrcOrders', 'properties': {'type': 'SqlServerTable', 'schema': [{'name': 'id', 'type': 'int'}, {'name': 'updated_at', 'type': 'datetime'}]}},
                              {'name': 'DwOrders', 'properties': {'type': 'AzureSqlTable', 'schema': [{'name': 'id', 'type': 'int'}]}}]}
        for _ in range(2):
            self.server.add('GET', BASE + r'/datasets\?api-version=2018-06-01', datasets)
            self.server.add('GET', BASE + r'/pipelines\?api-version=2018-06-01', {'value': [{'name': 'PL', 'properties': {'activities': [{'type': 'Copy'}]}}]})
        bad = {'akislar': [{'kaynak': 'SrcOrders', 'hedef': 'DwOrders', 'yontem': 'artimli_watermark', 'anahtar': ['id']}]}
        good = {'akislar': [{'kaynak': 'SrcOrders', 'hedef': 'DwOrders', 'yontem': 'artimli_watermark', 'anahtar': ['id'], 'watermark': 'updated_at',
                             'kolon_eslemeleri': [{'kaynak': 'id', 'hedef': 'id', 'donusum': 'doğrudan'}]}]}
        with model_reply(bad, good):
            self.run_op('adf-etl-plan', **ADF)
        self.assertIn('SrcOrders,DwOrders,id,id', self.read('kolon_eslemeleri.csv'))
        self.out = self.root / 'bad'
        with model_reply({'akislar': [{'kaynak': 'Ghost', 'hedef': 'DwOrders', 'yontem': 'tam_yukleme'}]},
                         {'akislar': [{'kaynak': 'SrcOrders', 'hedef': 'NewTable', 'yontem': 'tam_yukleme'}]}), self.assertRaises(native.NativeError):
            self.run_op('adf-etl-plan', **ADF)

    def test_ssis_dtsx_and_talend_item_evidence(self):
        pk = self.root / 'paketler'; pk.mkdir()
        (pk / 'p.dtsx').write_text('<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"><components><component name="Src" componentClassID="Microsoft.OLEDBSource">'
                                   '<properties><property name="OpenRowset">[dbo].[Orders]</property></properties><outputs><output><externalMetadataColumns>'
                                   '<externalMetadataColumn name="OrderID" dataType="i4"/></externalMetadataColumns></output></outputs></component></components></DTS:Executable>')
        objects, _ = integration.ev_ssis({'packages_dir': str(pk)}, None, self.out)
        self.assertEqual((objects[0]['nesne'], objects[0]['rol'], objects[0]['kolonlar'][0]['kolon']), ('dbo.Orders', 'kaynak', 'OrderID'))
        jobs = self.root / 'talend'; jobs.mkdir()
        (jobs / 'job.item').write_text('<talendfile:ProcessType xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd"><node componentName="tDBInput">'
                                       '<elementParameter name="TABLE" value="&quot;customers&quot;"/><metadata connector="FLOW"><column name="id" type="id_Integer"/></metadata></node></talendfile:ProcessType>')
        objects, _ = integration.ev_talend({'jobs_dir': str(jobs)}, None, self.out)
        self.assertEqual((objects[0]['nesne'], objects[0]['kolonlar'][0]['kolon']), ('customers', 'id'))
