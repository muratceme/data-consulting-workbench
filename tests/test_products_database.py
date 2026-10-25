"""Database product packages: relational engines through fake DB-API sources, document/key-value/graph stores through fake drivers or the local HTTP server."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import core, database  # noqa: E402

PG = {'host': 'h', 'database': 'd', 'user': 'u', 'schemas': ['public']}
COLS = (['table_schema', 'table_name', 'column_name', 'data_type', 'is_nullable', 'ordinal_position'],
        [{'table_schema': 'public', 'table_name': 'orders', 'column_name': c, 'data_type': t, 'is_nullable': 'YES', 'ordinal_position': i}
         for i, (c, t) in enumerate((('id', 'bigint'), ('customer_id', 'bigint'), ('created_at', 'timestamp')), 1)])


class FailingSql(FakeSql):
    def query(self, sql, stage='query'):
        if stage not in self.answers:
            raise native.NativeError('relation pg_stat_statements does not exist')
        return super().query(sql, stage)


class Relational(Base):
    def evidence(self):
        return {'columns': COLS,
                'indexler': (['index_name'], [{'table_schema': 'public', 'table_name': 'orders', 'index_name': 'orders_pkey', 'definition': 'CREATE UNIQUE INDEX ...'},
                                              {'table_schema': 'public', 'table_name': 'orders', 'index_name': 'ix_old', 'definition': 'CREATE INDEX ix_old ON orders(created_at)'}]),
                'index_kullanimi': (['index_name'], [{'index_name': 'ix_old', 'idx_scan': 0}]),
                'tablo_istatistikleri': (['table_name'], [{'table_name': 'orders', 'seq_scan': 9000}])}

    def test_postgres_schema_diff_uses_information_schema(self):
        fake = FakeSql({'columns': COLS})
        with patch.object(database, 'sql_source', return_value=fake):
            self.run_op('postgres-schema-diff', **PG)
        self.assertIn("information_schema.columns WHERE table_schema IN ('public')", fake.queries[0][1])
        self.assertEqual(self.read('ozet.json')['kolon'], 3)

    def test_index_review_skips_missing_extension_and_builds_ddl(self):
        reply = {'oneriler': [{'tur': 'index_ekle', 'nesne': 'public.orders', 'kolonlar': ['customer_id'], 'kanit': 'seq_scan 9000', 'gerekce': 'filtre', 'risk': 'yazma', 'oncelik': 'yuksek'},
                              {'tur': 'index_kaldir', 'nesne': 'public.orders', 'index': 'ix_old', 'kolonlar': [], 'kanit': 'idx_scan 0', 'gerekce': 'kullanılmıyor', 'risk': 'düşük', 'oncelik': 'orta'}],
                 'sorular': ['istatistikler ne zaman sıfırlandı?']}
        with patch.object(database, 'sql_source', return_value=FailingSql(self.evidence())), model_reply(reply):
            self.run_op('postgres-index-review', **PG)
        self.assertIn('okunamadı', self.read('kanit.json')['is_yuku']['sorgular'])
        ddl = self.read('degisiklik_taslagi.txt')
        self.assertIn('CREATE INDEX CONCURRENTLY ix_orders_customer_id ON public.orders (customer_id);', ddl)
        self.assertIn('DROP INDEX CONCURRENTLY public.ix_old;', ddl)

    def test_index_review_rejects_unknown_index_and_column(self):
        bad = {'oneriler': [{'tur': 'index_kaldir', 'nesne': 'public.orders', 'index': 'ix_ghost', 'gerekce': 'x', 'oncelik': 'orta'}]}
        bad2 = {'oneriler': [{'tur': 'index_ekle', 'nesne': 'public.orders', 'kolonlar': ['email'], 'gerekce': 'x', 'oncelik': 'orta'}]}
        with patch.object(database, 'sql_source', return_value=FailingSql(self.evidence())), model_reply(bad, bad2):
            with self.assertRaises(native.NativeError):
                self.run_op('postgres-index-review', **PG)

    def test_required_evidence_failure_is_not_hidden(self):
        with patch.object(database, 'sql_source', return_value=FailingSql({'columns': COLS})):
            with self.assertRaises(native.NativeError):
                self.run_op('mysql-index-review', host='h', database='d', user='u', databases=['shop'])

    def test_sqlserver_uses_mssql_evidence(self):
        fake = FailingSql({'columns': COLS, 'indexler': ([], []), 'index_kullanimi': ([], [])})
        reply = {'oneriler': [{'tur': 'sorgu_yeniden_yaz', 'nesne': 'public.orders', 'kolonlar': [], 'gerekce': 'x', 'oncelik': 'dusuk'}]}
        with patch.object(database, 'sql_source', return_value=fake), model_reply(reply):
            self.run_op('azure-sql-index-review', odbc='Driver=x;Server=y', schemas=['public'])
        self.assertTrue(any('sys.dm_db_index_usage_stats' in q for _, q in fake.queries))


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline, **kw):
        if '$indexStats' in pipeline[0]:
            return [{'name': '_id_', 'accesses': {'ops': 5}}]
        return list(self.docs)

    def list_indexes(self):
        return [{'name': '_id_', 'key': {'_id': 1}}]


class FakeMongo:
    closed = False

    def __init__(self, *a, **k):
        self.db = type('DB', (), {'list_collection_names': lambda s: ['people'], '__getitem__': lambda s, n: FakeCollection([{'name': 'a', 'age': 3, 'addr': {'city': 'x'}}, {'name': None}]),
                                  'command': lambda s, *a: {'count': 2, 'size': 10}})()

    def __getitem__(self, name):
        return self.db

    def close(self):
        FakeMongo.closed = True


class Stores(Base):
    def test_mongodb_infers_types_and_optional_fields(self):
        with patch.object(database, 'driver', return_value=type('pymongo', (), {'MongoClient': FakeMongo})):
            self.run_op('mongodb-schema-diff', databases=['crm'])
        cols = {c['kolon']: c for c in self.read('sema.json')['kolonlar']}
        self.assertEqual(cols['name']['tip'], 'NULL|STRING'); self.assertTrue(cols['age']['bos_olabilir']); self.assertIn('addr.city', cols)
        self.assertTrue(FakeMongo.closed)

    def test_redis_groups_keys_without_reading_values(self):
        class FakeRedis:
            def scan_iter(self, match, count): return iter(['user:1', 'user:2', 'session:9f0a1b2c-1111-2222-3333-444455556666'])
            def type(self, key): return 'hash' if key.startswith('user') else 'string'
            def hkeys(self, key): return ['name', 'email']
            def close(self): pass
            def info(self): return {'used_memory': 1}
            def slowlog_get(self, n): return [{'command': 'HGETALL user:42 secretvalue', 'duration': 900}]
        fake = type('redis', (), {'Redis': type('R', (), {'from_url': staticmethod(lambda *a, **k: FakeRedis())})})
        with patch.object(database, 'driver', return_value=fake):
            self.run_op('redis-schema-diff')
        tables = {c['tablo'] for c in self.read('sema.json')['kolonlar']}
        self.assertEqual(tables, {'user:*', 'session:*'})
        self.assertEqual(database.slow_entry({'command': 'HGETALL user:42 secretvalue', 'duration': 9}), {'komut': 'HGETALL', 'sure_us': 9, 'anahtar_deseni': 'user:*'})

    def test_capella_infer(self):
        infer = {'status': 'success', 'results': [[{'properties': {'type': {'type': 'string'}, 'geo': {'type': 'object', 'properties': {'lat': {'type': ['number', 'null']}}}}}]]}
        self.server.add('POST', '/query/service', infer)
        self.run_op('capella-schema-diff', query_url='https://c.example:18093', collections=['travel.inventory.hotel'])
        self.assertIn('INFER `travel`.`inventory`.`hotel`', self.server.received[0][2]['statement'])
        self.assertTrue(self.server.received[0][2]['readonly'])
        cols = {c['kolon']: c for c in self.read('sema.json')['kolonlar']}
        self.assertTrue(cols['geo.lat']['bos_olabilir'])

    def test_dynamodb_keys_and_indexes(self):
        self.server.add('POST', '/', {'TableNames': ['orders']})
        self.server.add('POST', '/', {'Table': {'AttributeDefinitions': [{'AttributeName': 'pk', 'AttributeType': 'S'}, {'AttributeName': 'gs', 'AttributeType': 'N'}],
                                                'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}],
                                                'GlobalSecondaryIndexes': [{'IndexName': 'by_gs', 'KeySchema': [{'AttributeName': 'gs', 'KeyType': 'HASH'}]}]}})
        self.run_op('dynamodb-schema-diff', region='eu-central-1')
        self.assertEqual(self.server.received[1][3]['X-Amz-Target'], 'DynamoDB_20120810.DescribeTable')
        self.assertEqual({c['kolon'] for c in self.read('sema.json')['kolonlar']}, {'pk', 'by_gs:gs'})

    def test_neo4j_index_review_ddl(self):
        node = {'data': {'fields': ['nodeType', 'propertyName', 'propertyTypes', 'mandatory'], 'values': [[':`Person`', 'email', ['String'], False]]}}
        rel = {'data': {'fields': ['relType'], 'values': []}}
        idx = {'data': {'fields': ['name', 'type', 'labelsOrTypes', 'properties', 'readCount'], 'values': [['old', 'RANGE', ['Person'], ['email'], 0]]}}
        cons = {'data': {'fields': ['name'], 'values': []}}
        for body in (node, rel, idx, cons):
            self.server.add('POST', '/db/neo4j/query/v2', body)
        reply = {'oneriler': [{'tur': 'index_ekle', 'nesne': ':Person', 'kolonlar': ['email'], 'gerekce': 'x', 'oncelik': 'orta'}]}
        with model_reply(reply):
            self.run_op('neo4j-index-review', server='https://n.example', database='neo4j')
        self.assertIn('CREATE INDEX ix_person_email IF NOT EXISTS FOR (n:Person) ON (n.email);', self.read('degisiklik_taslagi.txt'))
