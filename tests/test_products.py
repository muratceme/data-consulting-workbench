"""Product packages: local HTTP contract server for platform transports, fake drivers for DB-API sources, scripted model replies."""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
for folder in ('library', 'library/agents', 'library/blocks', 'catalog'):
    sys.path.insert(0, str(ROOT / folder))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')
import native
import llm
from products import core, warehouse
from product_catalog import PRODUCTS


class Server:
    """Routes are consumed in order: (method, path regex, response body or callable, status, headers)."""
    def __init__(self):
        self.routes, self.received, self.errors = [], [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = raw.decode('utf-8', 'replace')
                owner.received.append((self.command, self.path, payload, dict(self.headers)))
                if not owner.routes:
                    owner.errors.append(('unexpected', self.command, self.path))
                    self.send_response(500); self.end_headers(); return
                method, pattern, body, status, headers = owner.routes.pop(0)
                if method != self.command or not re.fullmatch(pattern, self.path):
                    owner.errors.append((self.command, self.path, method, pattern))
                    self.send_response(500); self.end_headers(); return
                if callable(body):
                    body = body(payload)
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for k, v in {'Content-Type': 'application/octet-stream' if isinstance(body, bytes) else 'application/json', **headers}.items():
                    self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def add(self, method, pattern, body=None, status=200, headers=None):
        self.routes.append((method, pattern, {} if body is None else body, status, headers or {}))

    def factory(self, base, headers=None):
        return native.HTTP(f'http://127.0.0.1:{self.http.server_port}' + urlsplit(base).path, headers, allow_test_http=True)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()


class FakeSql(core.Sql):
    def __init__(self, answers):
        self.answers, self.queries = answers, []

    def query(self, sql, stage='query'):
        self.queries.append((stage, sql))
        return self.answers[stage]


def model_reply(*replies):
    """Patch llm.chat with scripted JSON replies (one per attempt)."""
    texts = [json.dumps(r) if not isinstance(r, str) else r for r in replies]
    return patch.object(llm, 'chat', side_effect=texts)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / 'run'
        self.server = Server()
        secrets = {k: 'test-only-secret' for e in PRODUCTS for k in e['secrets'] + ['DREMIO_TOKEN', 'DATABRICKS_ACCESS_TOKEN', 'SNOWFLAKE_ACCESS_TOKEN', 'GOOGLE_ACCESS_TOKEN']}
        self.env = patch.dict(os.environ, {**secrets, 'PROVIDER': 'openai', 'MODEL': 'test-model', 'OPENAI_API_KEY': 'test', 'MASK_SALT': 'x' * 20})
        self.env.start()
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop(); self.server.close(); self.temp.cleanup()
        self.assertEqual(self.server.errors, [])

    def run_op(self, op, **cfg):
        return native.execute({'operation': op, '_flags': {'yes': True}, **cfg}, self.out, self.server.factory)

    def read(self, name):
        text = (self.out / name).read_text(encoding='utf-8')
        return json.loads(text) if name.endswith('.json') else text


COLUMNS = {'columns': (['table_schema', 'table_name', 'column_name', 'data_type', 'is_nullable', 'ordinal_position'],
                       [{'table_schema': 'sales', 'table_name': 'orders', 'column_name': 'order_id', 'data_type': 'INT64', 'is_nullable': 'NO', 'ordinal_position': 1},
                        {'table_schema': 'sales', 'table_name': 'orders', 'column_name': 'customer_id', 'data_type': 'INT64', 'is_nullable': 'YES', 'ordinal_position': 2},
                        {'table_schema': 'sales', 'table_name': 'orders', 'column_name': 'amount', 'data_type': 'NUMERIC', 'is_nullable': 'YES', 'ordinal_position': 3},
                        {'table_schema': 'sales', 'table_name': 'customers', 'column_name': 'customer_id', 'data_type': 'INT64', 'is_nullable': 'NO', 'ordinal_position': 1},
                        {'table_schema': 'sales', 'table_name': 'customers', 'column_name': 'city', 'data_type': 'STRING', 'is_nullable': 'YES', 'ordinal_position': 2}]),
           'row_counts': (['table_schema', 'table_name', 'total_rows'], [{'table_schema': 'sales', 'table_name': 'orders', 'total_rows': 10}])}
BQ = {'project_id': 'my-project', 'location': 'EU', 'datasets': ['sales'], 'maximum_bytes_billed': 1000}


class Warehouse(Base):
    def test_snapshot_then_diff_reports_breaking_changes(self):
        fake = FakeSql(COLUMNS)
        with patch.object(warehouse, 'sql_source', return_value=fake):
            self.run_op('bigquery-schema-diff', **BQ)
        self.assertIn('`region-eu`.INFORMATION_SCHEMA.COLUMNS', fake.queries[0][1])
        self.assertIn('İlk çalışma', self.read('ozet.json')['not'])
        (self.root / 'onceki').mkdir(); (self.out / 'sema.json').replace(self.root / 'onceki/sema.json')
        changed = {'columns': (COLUMNS['columns'][0], [r for r in COLUMNS['columns'][1] if r['column_name'] != 'city'] +
                               [{**COLUMNS['columns'][1][2], 'data_type': 'FLOAT64'}][:0])}
        rows = [dict(r) for r in COLUMNS['columns'][1] if r['column_name'] != 'city']
        rows[2]['data_type'] = 'FLOAT64'
        self.out = self.root / 'run2'
        with patch.object(warehouse, 'sql_source', return_value=FakeSql({'columns': (COLUMNS['columns'][0], rows)})):
            self.run_op('bigquery-schema-diff', **BQ, baseline_file='onceki/sema.json')
        summary = self.read('ozet.json')
        self.assertEqual((summary['degisiklik'], summary['kirici']), (2, 2))
        self.assertIn('kolon kaldırıldı', self.read('sema_farki.csv'))

    def test_invalid_scope_never_queries(self):
        for bad in (["sales'; DROP TABLE x"], [], 'YOUR_DATASET'):
            with self.subTest(bad=bad), patch.object(warehouse, 'sql_source') as source:
                self.out = self.root / ('bad' + str(len(list(self.root.iterdir()))))
                with self.assertRaises(native.NativeError):
                    self.run_op('bigquery-schema-diff', **{**BQ, 'datasets': bad})
                source.assert_not_called()

    def test_teradata_type_codes(self):
        self.assertEqual(warehouse.teradata_type({'columntype': 'D ', 'decimaltotaldigits': 18, 'decimalfractionaldigits': 2}), 'DECIMAL(18,2)')
        self.assertEqual(warehouse.teradata_type({'columntype': 'CV', 'columnlength': 40}), 'VARCHAR(40)')

    GOOD = {'olgular': [{'ad': 'fct_siparis', 'grain': 'bir sipariş', 'kaynak_tablolar': ['sales.orders'],
                         'olcular': [{'ad': 'tutar', 'kaynak_kolon': 'sales.orders.amount', 'toplama': 'sum'}], 'boyutlar': ['dim_musteri']}],
            'boyutlar': [{'ad': 'dim_musteri', 'kaynak_tablo': 'sales.customers', 'dogal_anahtar': ['customer_id'], 'scd_tipi': 2, 'nitelikler': ['city']}],
            'varsayimlar': ['şehir değişebilir'], 'sorular': []}

    def test_agent_rejects_invented_column_then_accepts_correction(self):
        bad = json.loads(json.dumps(self.GOOD)); bad['olgular'][0]['olcular'][0]['kaynak_kolon'] = 'sales.orders.discount'
        with patch.object(warehouse, 'sql_source', return_value=FakeSql(COLUMNS)), model_reply(bad, self.GOOD) as chat:
            self.run_op('bigquery-dimensional-model', **BQ, target_schema='dwh')
        self.assertEqual(chat.call_count, 2)
        self.assertIn('discount', chat.call_args_list[1][0][3][-1]['content'])
        sql = self.read('ddl_taslak.sql')
        self.assertIn('CREATE TABLE dwh.dim_musteri', sql); self.assertIn('gecerlilik_bitis TIMESTAMP', sql); self.assertIn('tutar NUMERIC', sql)
        self.assertEqual(self.read('kanit.json')['tablolar'][1]['satir'], 10)
        self.assertNotIn('rows', json.dumps(self.read('kanit.json')))

    def test_agent_fails_closed_after_two_invalid_replies(self):
        with patch.object(warehouse, 'sql_source', return_value=FakeSql(COLUMNS)), model_reply('not json', {'olgular': []}):
            with self.assertRaises(native.NativeError):
                self.run_op('bigquery-dimensional-model', **BQ, target_schema='dwh')
        self.assertFalse((self.out / 'ddl_taslak.sql').exists())

    def test_agent_dry_run_never_calls_model(self):
        with patch.object(warehouse, 'sql_source', return_value=FakeSql(COLUMNS)), patch.object(llm, 'chat') as chat:
            native.execute({'operation': 'clickhouse-dimensional-model', '_flags': {'dry_run': True}, 'server': 'https://ch.example', 'databases': ['sales'],
                            'target_schema': 'dwh'}, self.out, self.server.factory)
        chat.assert_not_called()
        self.assertTrue((self.out / 'gonderilecek-istek.json').exists())


class SqlTransports(Base):
    def test_snowflake_sql_api(self):
        rows = {'statementHandle': 'h1', 'sqlState': '00000', 'code': '090001', 'resultSetMetaData': {'rowType': [{'name': 'A'}]}, 'data': [['1']]}
        self.server.add('POST', r'/api/v2/statements\?async=true&requestId=[0-9a-f-]+', {'statementHandle': 'h1'}, 202)
        self.server.add('GET', r'/api/v2/statements/h1', rows)
        src = core.SnowflakeSql({'server': 'https://x.snowflakecomputing.com', 'warehouse': 'W', 'role': 'R'}, self.server.factory, self.root)
        with patch.object(native.time, 'sleep'):
            self.assertEqual(src.query('SELECT 1'), (['a'], [{'a': '1'}]))
        self.assertEqual(self.server.received[0][2]['role'], 'R')

    def test_bigquery_paging(self):
        self.server.add('POST', r'/bigquery/v2/projects/p/queries', {'jobComplete': True, 'jobReference': {'jobId': 'j'}, 'schema': {'fields': [{'name': 'a'}]},
                                                                    'rows': [{'f': [{'v': '1'}]}], 'pageToken': 't'})
        self.server.add('GET', r'/bigquery/v2/projects/p/queries/j\?location=EU&pageToken=t&maxResults=1000', {'rows': [{'f': [{'v': '2'}]}]})
        src = core.BigQuerySql({'project_id': 'p', 'location': 'EU'}, self.server.factory, self.root)
        self.assertEqual(src.query('SELECT a')[1], [{'a': '1'}, {'a': '2'}])
        self.assertEqual(self.server.received[0][2]['maximumBytesBilled'], str(10 * 1024 ** 3))

    def test_redshift_data_api_is_signed_and_paged(self):
        self.server.add('POST', '/', {'Id': 's1'})
        self.server.add('POST', '/', {'Status': 'FINISHED', 'HasResultSet': True})
        self.server.add('POST', '/', {'ColumnMetadata': [{'name': 'a'}], 'Records': [[{'longValue': 1}]], 'NextToken': 'n'})
        self.server.add('POST', '/', {'Records': [[{'isNull': True}]]})
        src = core.RedshiftSql({'region': 'eu-central-1', 'workgroup_name': 'wg', 'database': 'dev'}, self.server.factory, self.root)
        self.assertEqual(src.query('SELECT 1')[1], [{'a': 1}, {'a': None}])
        method, path, payload, headers = self.server.received[0]
        self.assertEqual(headers['X-Amz-Target'], 'RedshiftData.ExecuteStatement')
        self.assertRegex(headers['Authorization'], r'^AWS4-HMAC-SHA256 Credential=test-only-secret/\d{8}/eu-central-1/redshift-data/aws4_request, SignedHeaders=content-type;host;x-amz-date;x-amz-target, Signature=[0-9a-f]{64}$')
        self.assertEqual(payload['WorkgroupName'], 'wg')

    def test_athena_skips_header_row(self):
        self.server.add('POST', '/', {'QueryExecutionId': 'q'})
        self.server.add('POST', '/', {'QueryExecution': {'Status': {'State': 'SUCCEEDED'}}})
        self.server.add('POST', '/', {'ResultSet': {'ResultSetMetadata': {'ColumnInfo': [{'Name': 'a'}]}, 'Rows': [{'Data': [{'VarCharValue': 'a'}]}, {'Data': [{'VarCharValue': '5'}]}]}})
        src = core.AthenaSql({'region': 'eu-west-1', 'database': 'cur'}, self.server.factory, self.root)
        self.assertEqual(src.query('SELECT a')[1], [{'a': '5'}])

    def test_athena_failure(self):
        self.server.add('POST', '/', {'QueryExecutionId': 'q'})
        self.server.add('POST', '/', {'QueryExecution': {'Status': {'State': 'FAILED'}}})
        with self.assertRaises(native.NativeError):
            core.AthenaSql({'region': 'eu-west-1'}, self.server.factory, self.root).query('SELECT 1')

    def test_clickhouse_readonly_http(self):
        self.server.add('POST', r'/\?readonly=1&default_format=JSON.*', {'meta': [{'name': 'a'}], 'data': [{'a': 1}]})
        src = core.ClickHouseSql({'server': 'https://ch.example'}, self.server.factory, self.root)
        self.assertEqual(src.query('SELECT 1 AS a'), (['a'], [{'a': 1}]))
        self.assertEqual(self.server.received[0][3]['X-Clickhouse-User'], 'test-only-secret')

    def test_dremio_job_results(self):
        self.server.add('POST', '/api/v3/sql', {'id': 'j'})
        self.server.add('GET', '/api/v3/job/j', {'jobState': 'COMPLETED'})
        self.server.add('GET', r'/api/v3/job/j/results\?offset=0&limit=500', {'rowCount': 1, 'schema': [{'name': 'a'}], 'rows': [{'a': 1}]})
        self.assertEqual(core.DremioSql({'server': 'https://d.example'}, self.server.factory, self.root).query('SELECT 1'), (['a'], [{'a': 1}]))

    def test_ords_rest_enabled_sql(self):
        self.server.add('POST', '/ords/s/_/sql', {'items': [{'resultSet': {'metadata': [{'columnName': 'A'}], 'items': [{'a': 1}], 'hasMore': False}}]})
        self.assertEqual(core.OrdsSql({'ords_url': 'https://o.example/ords/s/'}, self.server.factory, self.root).query('SELECT 1 a FROM dual'), (['a'], [{'a': 1}]))
        self.server.add('POST', '/ords/s/_/sql', {'items': [{'errorCode': 942}]})
        with self.assertRaises(native.NativeError):
            core.OrdsSql({'ords_url': 'https://o.example/ords/s/'}, self.server.factory, self.root).query('SELECT 1')

    def test_dbapi_driver_errors_hide_details(self):
        class Cursor:
            description = None
            def execute(self, sql, *a): raise RuntimeError('password=SECRET host=db')
            def close(self): pass
        class Conn:
            read_only = False
            def cursor(self): return Cursor()
        fake = type('psycopg', (), {'connect': staticmethod(lambda **k: Conn())})
        with patch.object(core, 'driver', return_value=fake):
            src = core.sql_source('postgres', {'host': 'h', 'database': 'd', 'user': 'u'}, None, self.root)
            with self.assertRaises(native.NativeError) as err:
                src.query('SELECT 1')
        self.assertNotIn('SECRET', str(err.exception))
        self.assertTrue(src.conn.read_only)

    def test_guard_select(self):
        self.assertEqual(core.guard_select('-- x\nSELECT 1;'), 'SELECT 1')
        for bad in ('DELETE FROM t', 'SELECT 1; DROP TABLE t', 'WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a'):
            with self.subTest(bad=bad), self.assertRaises(native.NativeError):
                core.guard_select(bad)


class Engines(unittest.TestCase):
    def test_reconcile_tolerance_and_duplicates(self):
        summary, diff, totals = core.reconcile([{'k': '1', 'v': '10'}, {'k': '2', 'v': '5'}], [{'k': '1', 'v': '10.004'}, {'k': '3', 'v': '1'}], ['k'], ['v'], 0.01)
        self.assertEqual((summary['yalniz_kaynakta'], summary['yalniz_hedefte'], summary['farkli_kayit']), (1, 1, 0))
        with self.assertRaises(native.NativeError):
            core.reconcile([{'k': '1'}, {'k': '1'}], [], ['k'])

    def test_dedupe_exact_and_fuzzy(self):
        rows = [{'ad': 'ACME Ltd. Şti.'}, {'ad': 'acme ltd sti'}, {'ad': 'Acme Ltd Stii'}, {'ad': 'Başka'}]
        groups, pairs, summary = core.dedupe(rows, ['ad'], threshold=0.9)
        self.assertEqual(summary['birebir_grup'], 1); self.assertEqual(len(pairs), 1)

    def test_quality_rules_and_privacy(self):
        issues = core.quality([{'e': 'a@b.com', 'n': '-1'}, {'e': '', 'n': '5'}], {'e': {'required': True, 'regex': r'\d+'}, 'n': {'min': 0}})
        self.assertEqual(sorted(i['kural'] for i in issues), ['biçim', 'min 0', 'zorunlu'])
        self.assertNotIn('a@b.com', json.dumps(issues))

    def test_sql_refs_skip_ctes_and_comments(self):
        refs = core.sql_refs("WITH x AS (SELECT * FROM raw.a) -- FROM fake.b\nINSERT INTO mart.c SELECT * FROM x JOIN raw.d ON 1=1 WHERE s='FROM q.r'")
        self.assertEqual(refs, [('raw.a', 'okur'), ('mart.c', 'yazar'), ('raw.d', 'okur')])

    def test_mask_requires_salt(self):
        with patch.dict(os.environ, {'MASK_SALT': ''}), self.assertRaises(native.NativeError):
            core.mask_rows([{'a': 'x'}], {'a'})
        with patch.dict(os.environ, {'MASK_SALT': 's' * 16}):
            masked = core.mask_rows([{'a': 'x', 'b': 'y'}], {'a'})
        self.assertNotEqual(masked[0]['a'], 'x'); self.assertEqual(masked[0]['b'], 'y')

    def test_odata_metadata(self):
        xml = b'''<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0"><edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="N">
        <EntityType Name="Customer"><Key><PropertyRef Name="No"/></Key><Property Name="No" Type="Edm.String" Nullable="false" MaxLength="20"/><Property Name="Name" Type="Edm.String"/></EntityType>
        <EntityContainer Name="C"><EntitySet Name="customers" EntityType="N.Customer"/></EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'''
        meta = core.odata_metadata(xml, ['customers'])
        self.assertEqual(meta[0], {'varlik': 'customers', 'alan': 'No', 'tip': 'String', 'zorunlu': True, 'anahtar': True, 'uzunluk': '20'})


class Catalog(unittest.TestCase):
    def test_every_entry_has_handler_and_unique_slot(self):
        from products import HANDLERS
        seen = set()
        for e in PRODUCTS:
            self.assertIn(e['operation'], HANDLERS)
            key = (e['domain'], e.get('tool'), e['product'], e['slot'])  # AWS and Google both ship a 'Database Migration Service'
            self.assertNotIn(key, seen); seen.add(key)
            self.assertTrue(e['sources'] and all(s.startswith('https://') for s in e['sources']))


if __name__ == '__main__':
    unittest.main()
