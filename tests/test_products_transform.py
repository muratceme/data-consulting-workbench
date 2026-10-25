"""Analytics engineering product packages: model inventories from each tool and native test rendering."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import transform  # noqa: E402

MANIFEST = {'nodes': {
    'model.p.stg_orders': {'resource_type': 'model', 'name': 'stg_orders', 'config': {'materialized': 'view'}, 'raw_code': 'select * from {{ source("shop","orders") }}',
                           'compiled_code': 'select * from raw.shop.orders', 'depends_on': {'nodes': ['source.p.shop.orders']}, 'columns': {'order_id': {'data_type': 'int'}, 'status': {}},
                           'description': ''},
    'model.p.fct_orders': {'resource_type': 'model', 'name': 'fct_orders', 'config': {'materialized': 'table'}, 'compiled_code': 'select order_id, status from analytics.stg_orders',
                           'depends_on': {'nodes': ['model.p.stg_orders']}, 'columns': {'order_id': {}, 'customer_id': {}, 'status': {}}, 'description': 'Siparişler'},
    'model.p.dim_customers': {'resource_type': 'model', 'name': 'dim_customers', 'config': {'materialized': 'table'}, 'compiled_code': 'select 1 as customer_id',
                              'depends_on': {'nodes': []}, 'columns': {'customer_id': {}}, 'description': 'x'},
    'test.p.unique_fct_orders_order_id': {'resource_type': 'test', 'test_metadata': {'name': 'unique', 'kwargs': {'column_name': 'order_id'}}, 'column_name': 'order_id',
                                          'depends_on': {'nodes': ['model.p.fct_orders']}}},
    'sources': {'source.p.shop.orders': {'source_name': 'shop', 'name': 'orders'}}}


class Inventory(Base):
    def write_manifest(self):
        (self.root / 'target').mkdir(exist_ok=True)
        (self.root / 'target/manifest.json').write_text(json.dumps(MANIFEST))

    def test_dbt_core_inventory_flags(self):
        self.write_manifest()
        self.run_op('dbt-core-model-inventory')
        rows = self.read('model_envanteri.csv')
        self.assertIn('stg_orders,model,view,1,1', rows)
        self.assertIn('SELECT *', rows); self.assertIn('yaprak model', rows)
        self.assertIn('shop.orders,stg_orders', self.read('soy_agaci.csv'))

    def test_dbt_cloud_latest_successful_run_artifact(self):
        self.server.add('GET', r'/api/v2/accounts/1/runs/\?job_definition_id=2&status=10&order_by=-id&limit=1', {'data': [{'id': 77}]})
        self.server.add('GET', '/api/v2/accounts/1/runs/77/artifacts/manifest.json', MANIFEST)
        self.run_op('dbt-cloud-model-inventory', server='https://cloud.getdbt.com', account_id='1', job_id='2')
        self.assertEqual(self.server.received[0][3]['Authorization'], 'Token test-only-secret')
        self.assertEqual(self.read('envanter.json')['model'], 3)

    def test_dataform_query_actions(self):
        repo = 'projects/p/locations/l/repositories/r'
        self.server.add('GET', rf'/v1/{repo}/compilationResults\?pageSize=1&orderBy=create_time\+desc', {'compilationResults': [{'name': repo + '/compilationResults/c1'}]})
        self.server.add('GET', rf'/v1/{repo}/compilationResults/c1:query\?pageSize=1000', {'compilationResultActions': [
            {'target': {'schema': 'df', 'name': 'orders'}, 'relation': {'relationType': 'TABLE', 'selectQuery': 'SELECT id FROM raw.orders', 'dependencyTargets': []}},
            {'target': {'schema': 'df', 'name': 'orders_assert'}, 'assertion': {'parentAction': {'schema': 'df', 'name': 'orders'}}}]})
        self.run_op('dataform-model-inventory', repository=repo)
        self.assertIn('raw.orders,df.orders,dış tablo', self.read('soy_agaci.csv'))

    def test_sqlmesh_project_files(self):
        models = self.root / 'proj/models'; models.mkdir(parents=True)
        (models / 'orders.sql').write_text('MODEL (\n  name sales.orders,\n  kind INCREMENTAL_BY_TIME_RANGE (time_column ds),\n  audits (not_null(columns := (id)))\n);\n'
                                           'SELECT\n  id,\n  ds\nFROM raw.orders\n', encoding='utf-8')
        self.run_op('sqlmesh-model-inventory', project_dir=str(self.root / 'proj'))
        self.assertIn('sales.orders,sql,incremental_by_time_range', self.read('model_envanteri.csv'))

    def test_cube_meta(self):
        self.server.add('GET', '/cubejs-api/v1/meta', {'cubes': [{'name': 'orders', 'type': 'cube', 'measures': [{'name': 'orders.count'}], 'dimensions': [{'name': 'orders.status'}]}]})
        (self.root / 'model').mkdir()
        (self.root / 'model/orders.yml').write_text('cubes:\n  - name: orders\n    sql_table: public.orders\n', encoding='utf-8')
        self.run_op('cube-model-inventory', server='https://c.example', model_dir='model')
        self.assertIn('public.orders,orders', self.read('soy_agaci.csv'))


class Strategy(Base):
    def test_dbt_tests_validated_and_rendered(self):
        (self.root / 'target').mkdir()
        (self.root / 'target/manifest.json').write_text(json.dumps(MANIFEST))
        bad = {'testler': [{'model': 'fct_orders', 'kolon': 'amount', 'tur': 'not_null'}]}
        good = {'testler': [{'model': 'fct_orders', 'kolon': 'customer_id', 'tur': 'relationships', 'parametre': {'hedef_model': 'dim_customers', 'hedef_kolon': 'customer_id'}},
                            {'model': 'fct_orders', 'kolon': 'status', 'tur': 'accepted_values', 'parametre': {'degerler': ['new', 'shipped']}, 'siddet': 'warn'},
                            {'model': 'fct_orders', 'kolon': 'order_id', 'tur': 'not_null'},
                            {'model': 'fct_orders', 'tur': 'unique_combination', 'parametre': {'kolonlar': ['order_id', 'status']}}],
                'model_onerileri': [{'model': 'stg_orders', 'oneri': 'SELECT * yerine kolon listesi'}]}
        with model_reply(bad, good):
            self.run_op('dbt-core-test-strategy')
        yml = self.read('schema_testleri.yml')
        for part in ("to: ref('dim_customers')", 'values: ["new", "shipped"]', 'severity: warn', '          - not_null', 'combination_of_columns: [order_id, status]'):
            self.assertIn(part, yml)
        self.assertIn('LEFT JOIN dim_customers b', self.read('test_sorgulari.sql'))

    def test_expression_injection_rejected(self):
        (self.root / 'target').mkdir()
        (self.root / 'target/manifest.json').write_text(json.dumps(MANIFEST))
        bad = {'testler': [{'model': 'fct_orders', 'tur': 'expression', 'parametre': {'ifade': '1=1; drop table x'}}]}
        with model_reply(bad, bad), self.assertRaises(native.NativeError):
            self.run_op('dbt-core-test-strategy')

    def test_sqlmesh_and_dataform_renderers(self):
        tests = [{'model': 'm', 'kolon': 'id', 'tur': 'unique'}, {'model': 'm', 'kolon': 'id', 'tur': 'not_null'}, {'model': 'm', 'tur': 'row_count', 'parametre': {'min': 5}}]
        self.assertIn('unique_values(columns := (id))', transform.sqlmesh_audits(tests))
        self.assertIn('number_of_rows(threshold := 5)', transform.sqlmesh_audits(tests))
        self.assertIn('"uniqueKey": [\n    "id"\n  ]', transform.dataform_assertions(tests))
