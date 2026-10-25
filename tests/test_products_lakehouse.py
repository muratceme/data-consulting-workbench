"""Lakehouse product packages: table-format logs on local disk/S3/OneLake, catalog APIs over the local HTTP server."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import lakehouse  # noqa: E402


def delta_table(root, layer, table, fields, partitions=()):
    log = root / layer / table / '_delta_log'
    log.mkdir(parents=True)
    schema = {'type': 'struct', 'fields': [{'name': n, 'type': t, 'nullable': nullable, 'metadata': {}} for n, t, nullable in fields]}
    (log / ('0' * 20 + '.json')).write_text(json.dumps({'protocol': {'minReaderVersion': 1}}) + '\n' +
                                            json.dumps({'metaData': {'schemaString': json.dumps(schema), 'partitionColumns': list(partitions)}}) + '\n')
    (log / ('0' * 19 + '1.json')).write_text(json.dumps({'add': {'path': 'x.parquet'}}) + '\n')


class Lakehouse(Base):
    def test_delta_local_layer_check(self):
        lake = self.root / 'lake'
        delta_table(lake, 'bronze', 'orders', [('id', 'long', True), ('amount', 'string', True), ('raw', 'string', True)])
        delta_table(lake, 'silver', 'orders', [('id', 'long', False), ('amount', 'double', True), ('day', 'date', True)], partitions=['day'])
        delta_table(lake, 'bronze', 'events', [('id', 'long', True)])
        self.run_op('delta-lake-layer-check', storage='local', root_path=str(lake), layers=['bronze', 'silver'])
        found = {(r.split(',')[2], r.split(',')[3]) for r in self.read('katman_farki.csv').splitlines()[1:]}
        self.assertIn(('amount', 'tip değişti'), found)
        self.assertIn(('raw', 'kolon sonraki katmana taşınmamış'), found)
        self.assertIn(('day', 'türetilmiş/yeni kolon'), found)
        self.assertIn(('', 'sonraki katmanda tablo yok'), found)

    def test_single_layer_is_rejected(self):
        with self.assertRaises(native.NativeError):
            self.run_op('delta-lake-layer-check', storage='local', root_path=str(self.root), layers=['bronze'])

    def test_local_store_blocks_path_escape(self):
        with self.assertRaises(native.NativeError):
            lakehouse.LocalStore({'root_path': str(self.root)}).list('../..')

    def test_hudi_timeline_schema(self):
        timeline = self.root / 'lake/bronze/trips/.hoodie'
        timeline.mkdir(parents=True)
        schema = {'type': 'record', 'name': 'r', 'fields': [{'name': '_hoodie_commit_time', 'type': 'string'}, {'name': 'id', 'type': 'long'},
                                                            {'name': 'fare', 'type': ['null', 'double']}]}
        (timeline / '20250101000000.commit').write_text(json.dumps({'extraMetadata': {'schema': json.dumps(schema)}}))
        cols = lakehouse.hudi_layers({'storage': 'local', 'root_path': str(self.root / 'lake')}, None, ['bronze'])
        self.assertEqual([(c['kolon'], c['tip'], c['bos_olabilir']) for c in cols], [('id', 'BIGINT', 'NO'), ('fare', 'DOUBLE', 'YES')])

    def test_s3_listing_is_signed_and_parsed(self):
        ns = 'http://s3.amazonaws.com/doc/2006-03-01/'
        xml = (f'<ListBucketResult xmlns="{ns}"><Contents><Key>lake/bronze/t/_delta_log/00000000000000000000.json</Key></Contents>'
               f'<CommonPrefixes><Prefix>lake/bronze/t/_delta_log/sub/</Prefix></CommonPrefixes></ListBucketResult>').encode()
        self.server.add('GET', r'/\?delimiter=%2F&list-type=2&prefix=lake%2Fbronze%2Ft%2F_delta_log%2F', xml)
        store = lakehouse.S3Store({'region': 'eu-central-1', 'bucket': 'my-bucket', 'root_prefix': 'lake'}, self.server.factory)
        self.assertEqual(store.list('bronze/t/_delta_log'), ['00000000000000000000.json', 'sub/'])
        headers = self.server.received[0][3]
        self.assertIn('/eu-central-1/s3/aws4_request', headers['Authorization'])
        self.assertEqual(len(headers['X-Amz-Content-Sha256']), 64)

    def test_onelake_listing(self):
        self.server.add('GET', r'/ws\?resource=filesystem&directory=Sales.Lakehouse%2FTables%2Fbronze&recursive=false',
                        {'paths': [{'name': 'Sales.Lakehouse/Tables/bronze/orders', 'isDirectory': 'true'}]})
        store = lakehouse.OneLakeStore({'workspace': 'ws', 'item_path': 'Sales.Lakehouse'}, self.server.factory)
        self.assertEqual(store.list('Tables/bronze'), ['orders/'])
        self.assertEqual(self.server.received[0][3]['X-Ms-Version'], '2023-11-03')

    def test_unity_catalog_tables_api_paging(self):
        page1 = {'tables': [{'name': 'orders', 'columns': [{'name': 'id', 'type_text': 'bigint', 'nullable': False, 'position': 0}]}], 'next_page_token': 'p2'}
        self.server.add('GET', r'/api/2.1/unity-catalog/tables\?catalog_name=main&schema_name=bronze&max_results=50', page1)
        self.server.add('GET', r'/api/2.1/unity-catalog/tables\?catalog_name=main&schema_name=bronze&max_results=50&page_token=p2', {'tables': []})
        self.server.add('GET', r'/api/2.1/unity-catalog/tables\?catalog_name=main&schema_name=silver&max_results=50',
                        {'tables': [{'name': 'orders', 'columns': [{'name': 'id', 'type_text': 'bigint', 'nullable': True, 'position': 0}]}]})
        self.run_op('unity-catalog-layer-check', workspace_url='https://w.example', catalog='main', layers=['bronze', 'silver'])
        self.assertIn('NOT NULL kuralı gevşedi', self.read('katman_farki.csv'))

    def test_glue_get_tables(self):
        self.server.add('POST', '/', {'TableList': [{'Name': 'orders', 'StorageDescriptor': {'Columns': [{'Name': 'id', 'Type': 'bigint'}]}, 'PartitionKeys': [{'Name': 'dt', 'Type': 'string'}]}]})
        self.server.add('POST', '/', {'TableList': [{'Name': 'orders', 'StorageDescriptor': {'Columns': [{'Name': 'id', 'Type': 'bigint'}]}}]})
        self.run_op('lake-formation-layer-check', region='eu-central-1', layers=['bronze', 'silver'])
        self.assertEqual(self.server.received[0][3]['X-Amz-Target'], 'AWSGlue.GetTables')
        self.assertIn('dt', self.read('katman_farki.csv'))

    def test_iceberg_rest_catalog_current_schema(self):
        self.server.add('GET', '/v1/namespaces/raw%1Fbronze/tables', {'identifiers': [{'namespace': ['raw', 'bronze'], 'name': 'orders'}]})
        self.server.add('GET', '/v1/namespaces/raw%1Fbronze/tables/orders', {'metadata': {'current-schema-id': 1, 'schemas': [
            {'schema-id': 0, 'fields': [{'name': 'old', 'type': 'int', 'required': False}]},
            {'schema-id': 1, 'fields': [{'name': 'id', 'type': 'long', 'required': True}]}]}})
        cols = lakehouse.iceberg_columns({'catalog_uri': 'https://i.example'}, self.server.factory, self.out, ['raw.bronze'])
        self.assertEqual([(c['kolon'], c['tip'], c['bos_olabilir']) for c in cols], [('id', 'long', 'NO')])

    def test_medallion_agent_requires_marked_new_targets(self):
        sql = FakeSql({'columns_bronze': ([], [{'table_name': 'orders', 'column_name': 'id', 'data_type': 'bigint', 'is_nullable': 'YES', 'ordinal_position': 1}]),
                       'columns_silver': ([], [])})
        bad = {'tablolar': [{'tablo': 'bronze.orders', 'katman': 'bronze', 'gerekce': 'ham'}],
               'akislar': [{'kaynaklar': ['bronze.orders'], 'hedef': 'silver.orders', 'donusum': 'tekilleştir', 'anahtar': ['id'], 'artimli': 'merge'}]}
        good = json.loads(json.dumps(bad)); good['akislar'][0]['yeni'] = True
        with patch.object(lakehouse, 'sql_source', return_value=sql), model_reply(bad, good) as chat:
            self.run_op('databricks-lakehouse-medallion', workspace_url='https://w.example', warehouse_id='w', catalog='main', layers=['bronze', 'silver'])
        self.assertEqual(chat.call_count, 2)
        self.assertIn('silver.orders', self.read('akislar.csv'))
        self.assertIn('`main`.information_schema.columns', sql.queries[0][1])
