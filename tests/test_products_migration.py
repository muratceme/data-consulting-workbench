"""Migration product packages: service table statistics and independent counts, and the wave/cutover agent with dependency checks."""
import csv
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import migration  # noqa: E402

PAIR = {'source': {'kind': 'postgres', 'host': 's', 'database': 'd', 'user': 'u'}, 'target': {'kind': 'postgres', 'host': 't', 'database': 'd', 'user': 'u'},
        'tables': ['public.customers', 'public.orders']}


class Sides:
    """sql_source fake that answers per side (source/target) and per stage."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, kind, conf, factory, out):
        side = 'source' if conf.get('host') == 's' else 'target'
        owner = self

        class Q:
            def query(self, sql, stage):
                owner.calls.append((side, stage, sql))
                return [], owner.answers[(side, stage)]
        return Q()


class Reconcile(Base):
    def rows(self):
        with open(self.out / 'gecis_mutabakati.csv', encoding='utf-8') as fh:
            return {r['nesne']: r for r in csv.DictReader(fh)}

    def test_aws_dms_statistics(self):
        self.server.add('POST', '/', {'TableStatistics': [{'SchemaName': 'hr', 'TableName': 'emp', 'FullLoadRows': 100, 'Inserts': 5, 'Deletes': 1, 'ValidationState': 'Validated'},
                                                          {'SchemaName': 'hr', 'TableName': 'dept', 'FullLoadRows': 10, 'FullLoadErrorRows': 2, 'ValidationState': 'Error'}]})
        (self.root / 'src.csv').write_text('tablo,satir\nhr.emp,104\nhr.dept,12\n')
        self.run_op('aws-dms-migration-reconcile', region='eu-central-1', replication_task_arn='arn:aws:dms:eu-central-1:1:task:T', source_counts_file='src.csv')
        rows = self.rows()
        self.assertEqual((rows['hr.emp']['durum'], rows['hr.dept']['durum']), ('eşit', 'fark atlanan satırlarla açıklanıyor'))
        self.assertEqual(self.read('ozet.json')['dogrulama_hatali'], ['hr.dept'])
        self.assertEqual(self.server.received[0][3]['X-Amz-Target'], 'AmazonDMSv20160101.DescribeTableStatistics')

    def test_azure_dms_copy_progress(self):
        target = '/subscriptions/s/resourceGroups/r/providers/Microsoft.Sql/servers/sv'
        self.server.add('GET', target + r'/providers/Microsoft.DataMigration/databaseMigrations/db1\?.*', {'properties': {'migrationStatus': 'Succeeded', 'migrationStatusDetails': {
            'listOfCopyProgressDetails': [{'tableName': 'dbo.orders', 'rowsRead': 50, 'rowsCopied': 49, 'status': 'Succeeded'}]}}})
        self.run_op('azure-dms-migration-reconcile', target_resource_id=target, target_database='db1')
        self.assertEqual(self.rows()['dbo.orders']['durum'], 'FARK')

    def test_google_dms_with_two_databases(self):
        self.server.add('GET', '/v1/projects/p/locations/l/migrationJobs/j', {'state': 'COMPLETED', 'phase': 'CDC'})
        fake = Sides({('source', 'source_counts'): [{'tablo': 'public.customers', 'satir': 10}, {'tablo': 'public.orders', 'satir': 20}],
                      ('target', 'target_counts'): [{'tablo': 'public.customers', 'satir': 10}, {'tablo': 'public.orders', 'satir': 19}]})
        with patch.object(migration, 'sql_source', side_effect=fake), patch.dict('os.environ', {'SOURCE_DB_PASSWORD': 'sp', 'TARGET_DB_PASSWORD': 'tp'}):
            self.run_op('google-dms-migration-reconcile', migration_job='projects/p/locations/l/migrationJobs/j', **PAIR)
        rows = self.rows()
        self.assertEqual((rows['public.customers']['durum'], rows['public.orders']['durum']), ('eşit', 'FARK'))
        self.assertEqual(self.read('ozet.json')['faz'], 'CDC')

    def test_migration_cockpit_template_vs_odata_count(self):
        ns = 'urn:schemas-microsoft-com:office:spreadsheet'
        rows = ''.join(f'<Row><Cell><Data ss:Type="String">h{i}</Data></Cell></Row>' for i in range(2)) + \
               ''.join(f'<Row><Cell><Data ss:Type="String">BP{i}</Data></Cell></Row>' for i in range(3))
        (self.root / 't.xml').write_text(f'<?xml version="1.0"?><Workbook xmlns="{ns}" xmlns:ss="{ns}"><Worksheet ss:Name="General Data"><Table>{rows}</Table></Worksheet></Workbook>')
        self.server.add('GET', r'/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner/\$count', b'3')
        self.run_op('migration-cockpit-migration-reconcile', template_file='t.xml', header_rows=2, server='https://s4.example',
                    sheet_entities={'General Data': {'service': '/sap/opu/odata/sap/API_BUSINESS_PARTNER', 'entity_set': 'A_BusinessPartner'}})
        self.assertEqual(self.rows()['General Data']['durum'], 'eşit')

    def test_zdm_phases(self):
        out = 'Job ID: 5\nCurrent status: SUCCEEDED\nZDM_SETUP_SRC ......... COMPLETED\nZDM_SWITCHOVER_SRC ......... COMPLETED\n'
        fake = Sides({('source', 'source_counts'): [{'tablo': 'HR.EMP', 'satir': 3}], ('target', 'target_counts'): [{'tablo': 'HR.EMP', 'satir': 3}]})
        with patch.object(migration.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, out, '')), patch.object(migration, 'sql_source', side_effect=fake):
            self.run_op('oracle-zdm-migration-reconcile', job_id='5', source={'kind': 'oracle', 'host': 's', 'user': 'u', 'dsn': 'x'},
                        target={'kind': 'oracle', 'host': 't', 'user': 'u', 'dsn': 'y'}, tables=['HR.EMP'])
        summary = self.read('ozet.json')
        self.assertEqual((summary['durum'], len(summary['fazlar'])), ('SUCCEEDED', 2))


class Cutover(Base):
    def test_dependency_order_enforced(self):
        self.server.add('GET', '/v1/projects/p/locations/l/migrationJobs/j', {'state': 'COMPLETED'})
        self.server.add('GET', '/v1/projects/p/locations/l/migrationJobs/j', {'state': 'COMPLETED'})
        fake = Sides({('source', 'source_counts'): [{'tablo': 'public.customers', 'satir': 10}, {'tablo': 'public.orders', 'satir': 20}],
                      ('target', 'target_counts'): [{'tablo': 'public.customers', 'satir': 10}, {'tablo': 'public.orders', 'satir': 20}],
                      ('source', 'foreign_keys'): [{'child': 'public.orders', 'parent': 'public.customers'}]})
        bad = {'dalgalar': [{'ad': 'D1', 'tablolar': ['public.orders']}, {'ad': 'D2', 'tablolar': ['public.customers']}], 'cutover': [{'adim': 'dondur', 'geri_donus': 'aç'}]}
        good = {'dalgalar': [{'ad': 'D1', 'tablolar': ['public.customers']}, {'ad': 'D2', 'tablolar': ['public.orders']}], 'cutover': [{'adim': 'dondur', 'geri_donus': 'aç', 'sure_dk': 10}]}
        with patch.object(migration, 'sql_source', side_effect=fake), model_reply(bad, good) as chat:
            self.run_op('google-dms-cutover-plan', migration_job='projects/p/locations/l/migrationJobs/j', **PAIR)
        self.assertIn('bağımlılık sırası bozuk', chat.call_args_list[1][0][3][-1]['content'])
        self.assertIn('D2,2,public.orders', self.read('dalgalar.csv'))
