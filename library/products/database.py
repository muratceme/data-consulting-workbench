"""Operational databases: live schema snapshot/diff (code) and an evidence-bound index & query review (agent)."""
import json
import os
import re
from collections import Counter, defaultdict

from products.core import (NativeError, agent_task, aws_json, basic, driver, literal, name, names, need_list, need_text, opt_int,
                           plain, plan_json, region_of, required, rows_md, schema_task, secret, sql_in, sql_source)

INFO_COLUMNS = ('SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position '
                'FROM information_schema.columns WHERE table_schema IN ({})')

RELATIONAL = {
    'postgres': dict(label='PostgreSQL', source='postgres', scope='schemas', columns=INFO_COLUMNS),
    'sqlserver': dict(label='SQL Server', source='mssql', scope='schemas', columns=INFO_COLUMNS),
    'azure-sql': dict(label='Azure SQL', source='mssql', scope='schemas', columns=INFO_COLUMNS),
    'mysql': dict(label='MySQL', source='mysql', scope='databases', columns=INFO_COLUMNS),
    'oracle-db': dict(label='Oracle Database', source='oracle', scope='schemas',
                      columns="SELECT owner AS table_schema, table_name, column_name, data_type, CASE nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable, "
                              'column_id AS ordinal_position FROM all_tab_columns WHERE owner IN ({})'),
}

# Index & workload evidence per relational engine. Optional statements (statistics views that need extensions or
# extra privileges) are skipped when they fail; the evidence then says so instead of guessing.
EVIDENCE_SQL = {
    'postgres': [
        ('indexler', True, 'SELECT schemaname AS table_schema, tablename AS table_name, indexname AS index_name, indexdef AS definition FROM pg_indexes WHERE schemaname IN ({})'),
        ('index_kullanimi', True, 'SELECT schemaname AS table_schema, relname AS table_name, indexrelname AS index_name, idx_scan, pg_relation_size(indexrelid) AS bytes FROM pg_stat_user_indexes WHERE schemaname IN ({})'),
        ('tablo_istatistikleri', True, 'SELECT schemaname AS table_schema, relname AS table_name, seq_scan, idx_scan, n_live_tup, n_dead_tup FROM pg_stat_user_tables WHERE schemaname IN ({})'),
        ('sorgular', False, 'SELECT queryid::text AS query_id, calls, round(total_exec_time::numeric, 1) AS total_ms, round(mean_exec_time::numeric, 2) AS mean_ms, rows, '
                            'left(query, 600) AS query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 40'),
    ],
    'mssql': [
        ('indexler', True, 'SELECT s.name AS table_schema, t.name AS table_name, i.name AS index_name, i.type_desc AS definition, i.is_unique, '
                           "STUFF((SELECT ',' + c.name FROM sys.index_columns ic JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
                           "WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0 ORDER BY ic.key_ordinal FOR XML PATH('')), 1, 1, '') AS key_columns "
                           'FROM sys.indexes i JOIN sys.tables t ON t.object_id = i.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE i.index_id > 0 AND s.name IN ({})'),
        ('index_kullanimi', True, 'SELECT s.name AS table_schema, t.name AS table_name, i.name AS index_name, u.user_seeks, u.user_scans, u.user_lookups, u.user_updates '
                                  'FROM sys.dm_db_index_usage_stats u JOIN sys.indexes i ON i.object_id = u.object_id AND i.index_id = u.index_id '
                                  'JOIN sys.tables t ON t.object_id = i.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE u.database_id = DB_ID() AND s.name IN ({})'),
        ('eksik_index_onerileri', False, 'SELECT d.statement AS table_name, d.equality_columns, d.inequality_columns, d.included_columns, s.user_seeks, s.avg_user_impact '
                                         'FROM sys.dm_db_missing_index_details d JOIN sys.dm_db_missing_index_groups g ON g.index_handle = d.index_handle '
                                         'JOIN sys.dm_db_missing_index_group_stats s ON s.group_handle = g.index_group_handle WHERE d.database_id = DB_ID()'),
        ('sorgular', False, 'SELECT TOP 40 CONVERT(varchar(34), qs.query_hash, 1) AS query_id, qs.execution_count AS calls, qs.total_elapsed_time / 1000 AS total_ms, '
                            'qs.total_logical_reads AS logical_reads, LEFT(st.text, 600) AS query FROM sys.dm_exec_query_stats qs CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st '
                            'WHERE st.dbid = DB_ID() ORDER BY qs.total_elapsed_time DESC'),
    ],
    'mysql': [
        ('indexler', True, 'SELECT table_schema, table_name, index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS key_columns, MIN(non_unique) AS non_unique '
                           'FROM information_schema.statistics WHERE table_schema IN ({}) GROUP BY table_schema, table_name, index_name'),
        ('kullanilmayan_indexler', False, 'SELECT object_schema AS table_schema, object_name AS table_name, index_name FROM sys.schema_unused_indexes WHERE object_schema IN ({})'),
        ('sorgular', False, 'SELECT digest AS query_id, count_star AS calls, ROUND(sum_timer_wait / 1000000000, 1) AS total_ms, sum_rows_examined AS rows_examined, '
                            'sum_rows_sent AS rows_sent, LEFT(digest_text, 600) AS query FROM performance_schema.events_statements_summary_by_digest '
                            'WHERE schema_name IN ({}) ORDER BY sum_timer_wait DESC LIMIT 40'),
    ],
    'oracle': [
        ('indexler', True, "SELECT i.table_owner AS table_schema, i.table_name, i.index_name, i.uniqueness AS definition, LISTAGG(c.column_name, ',') WITHIN GROUP (ORDER BY c.column_position) AS key_columns "
                           'FROM all_indexes i JOIN all_ind_columns c ON c.index_owner = i.owner AND c.index_name = i.index_name WHERE i.table_owner IN ({}) '
                           'GROUP BY i.table_owner, i.table_name, i.index_name, i.uniqueness'),
        ('tablo_istatistikleri', True, 'SELECT owner AS table_schema, table_name, num_rows, last_analyzed FROM all_tables WHERE owner IN ({})'),
        ('sorgular', False, 'SELECT sql_id AS query_id, executions AS calls, ROUND(elapsed_time / 1000) AS total_ms, buffer_gets, SUBSTR(sql_text, 1, 600) AS query '
                            'FROM v$sql WHERE parsing_schema_name IN ({}) ORDER BY elapsed_time DESC FETCH FIRST 40 ROWS ONLY'),
    ],
}


def norm_rows(rows):
    return [{str(k).lower(): plain(v) for k, v in r.items()} for r in rows]


def relational_columns(spec, scope, src):
    _, rows = src.query(spec['columns'].format(sql_in(scope)), 'columns')
    return [{'tablo': f"{r.get('table_schema')}.{r.get('table_name')}", 'kolon': r.get('column_name'), 'tip': r.get('data_type'),
             'bos_olabilir': r.get('is_nullable'), 'sira': r.get('ordinal_position')} for r in norm_rows(rows)]


# ---------- Document, key-value and graph stores ----------
def bson_type(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int):
        return 'int'
    if isinstance(value, float):
        return 'double'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    return type(value).__name__.lower()


def infer(docs, prefix='', depth=0):
    """Field path → set of types, plus presence count (missing in some documents = nullable)."""
    fields, present = defaultdict(set), Counter()
    for doc in docs:
        for k, v in (doc or {}).items():
            path = prefix + k
            fields[path].add(bson_type(v))
            present[path] += 1
            if isinstance(v, dict) and depth < 2:
                sub, cnt = infer([v], path + '.', depth + 1)
                for p, t in sub.items():
                    fields[p] |= t
                    present[p] += cnt[p]
    return fields, present


def mongo_client(cfg):
    pymongo = driver('pymongo')
    return pymongo.MongoClient(secret('MONGODB_URI'), serverSelectionTimeoutMS=20000, readPreference=cfg.get('read_preference', 'secondaryPreferred'))


def mongo_read(cfg, out, factory, *, evidence=False):
    client = mongo_client(cfg)
    sample = opt_int(cfg, 'sample_size', 1000, 10, 10000)
    columns, extra = [], []
    try:
        for db_name in names(cfg, 'databases', limit=20):
            db = client[db_name]
            wanted = names(cfg, 'collections', optional=True)
            for coll in sorted(wanted or db.list_collection_names()):
                docs = list(db[coll].aggregate([{'$sample': {'size': sample}}], allowDiskUse=False))
                fields, present = infer(docs)
                for i, (path, types) in enumerate(sorted(fields.items()), 1):
                    columns.append({'tablo': f'{db_name}.{coll}', 'kolon': path, 'tip': '|'.join(sorted(types)),
                                    'bos_olabilir': 'YES' if 'null' in types or present[path] < len(docs) else 'NO', 'sira': i})
                if evidence:
                    indexes = [{'index_name': ix.get('name'), 'key_columns': ','.join(ix.get('key', {}).keys()), 'unique': bool(ix.get('unique'))}
                               for ix in db[coll].list_indexes()]
                    usage = [{'index_name': s.get('name'), 'ops': int(((s.get('accesses') or {}).get('ops')) or 0)}
                             for s in db[coll].aggregate([{'$indexStats': {}}])]
                    stats = db.command('collStats', coll)
                    extra.append({'tablo': f'{db_name}.{coll}', 'belge': stats.get('count'), 'boyut': stats.get('size'), 'indexler': indexes, 'index_kullanimi': usage})
    finally:
        client.close()
    return columns, {'koleksiyonlar': extra}


PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f-]{27}|\d+', re.I)


def slow_entry(entry):
    # Only the command name and a digit-free key pattern are kept; arguments can carry business values.
    command = entry.get('command') or ''
    parts = (command.decode('utf-8', 'replace') if isinstance(command, bytes) else str(command)).split(' ')
    return {'komut': parts[0][:20].upper(), 'sure_us': entry.get('duration'), 'anahtar_deseni': PATTERN.sub('*', parts[1])[:80] if len(parts) > 1 else ''}


def redis_read(cfg, out, factory, *, evidence=False):
    redis = driver('redis')
    client = redis.Redis.from_url(secret('REDIS_URL'), socket_timeout=20, decode_responses=True)
    limit = opt_int(cfg, 'max_keys', 5000, 100, 100000)
    match = cfg.get('match', '*')
    if not isinstance(match, str) or len(match) > 200:
        raise NativeError('match en fazla 200 karakterlik SCAN deseni olmalı.')
    groups = defaultdict(lambda: {'adet': 0, 'tip': Counter(), 'alanlar': Counter()})
    try:
        for i, key in enumerate(client.scan_iter(match=match, count=500)):
            if i >= limit:
                break
            pattern = PATTERN.sub('*', key)
            kind = client.type(key)
            g = groups[pattern]
            g['adet'] += 1
            g['tip'][kind] += 1
            if kind == 'hash' and g['adet'] <= 20:
                for f in list(client.hkeys(key))[:50]:
                    g['alanlar'][f] += 1
        info = client.info() if evidence else {}
        slow = client.slowlog_get(50) if evidence else []
    finally:
        client.close()
    columns = []
    for pattern, g in sorted(groups.items()):
        kind = '|'.join(sorted(g['tip']))
        columns.append({'tablo': pattern, 'kolon': '(değer)', 'tip': kind, 'bos_olabilir': 'NO', 'sira': 0})
        for j, (field, n) in enumerate(sorted(g['alanlar'].items()), 1):
            columns.append({'tablo': pattern, 'kolon': field, 'tip': 'hash alanı', 'bos_olabilir': 'YES' if n < min(g['adet'], 20) else 'NO', 'sira': j})
    ev = {}
    if evidence:
        ev = {'anahtar_desenleri': [{'tablo': p, 'adet': g['adet'], 'tipler': dict(g['tip'])} for p, g in sorted(groups.items())],
              'bellek': {k: info.get(k) for k in ('used_memory', 'maxmemory', 'maxmemory_policy', 'mem_fragmentation_ratio', 'evicted_keys', 'expired_keys')},
              'yavas_komutlar': [slow_entry(s) for s in slow]}
    return columns, ev


def capella_query(client, statement):
    r = client.json('POST', '/query/service', {'statement': statement, 'readonly': True, 'timeout': '60s'})
    if r.get('status') != 'success':
        raise NativeError('Couchbase sorgu servisi başarılı sonuç döndürmedi.')
    return r.get('results') or []


def capella_read(cfg, out, factory, *, evidence=False):
    client = basic(factory, required(cfg, 'query_url'), 'COUCHBASE_USER', 'COUCHBASE_PASSWORD')
    keyspaces = names(cfg, 'collections', 'collections (bucket.scope.collection)', limit=50)
    sample = opt_int(cfg, 'sample_size', 1000, 10, 10000)
    columns, extra = [], []
    for ks in keyspaces:
        if len(ks.split('.')) != 3:
            raise NativeError('collections bucket.scope.collection biçiminde olmalı: ' + ks)
        quoted = '.'.join('`' + p + '`' for p in ks.split('.'))
        flavors = capella_query(client, f'INFER {quoted} WITH {{"sample_size": {sample}}}')
        flavors = flavors[0] if flavors and isinstance(flavors[0], list) else flavors
        seen = {}
        def walk(props, prefix, depth):
            for field, spec in (props or {}).items():
                types = spec.get('type') if isinstance(spec, dict) else None
                types = types if isinstance(types, list) else [types]
                path = prefix + field
                seen.setdefault(path, set()).update(t for t in types if t)
                if isinstance(spec, dict) and spec.get('properties') and depth < 2:
                    walk(spec['properties'], path + '.', depth + 1)
        for flavor in flavors if isinstance(flavors, list) else []:
            if isinstance(flavor, dict):
                walk(flavor.get('properties'), '', 0)
        for i, (path, types) in enumerate(sorted(seen.items()), 1):
            columns.append({'tablo': ks, 'kolon': path, 'tip': '|'.join(sorted(types)), 'bos_olabilir': 'YES' if 'null' in types or 'missing' in types else 'NO', 'sira': i})
    if evidence:
        bucket = sorted({k.split('.')[0] for k in keyspaces})
        extra = {'indexler': [{'index_name': ix.get('name'), 'tablo': '.'.join(x for x in (ix.get('bucket_id') or ix.get('keyspace_id'), ix.get('scope_id'), ix.get('keyspace_id')) if x),
                                'key_columns': ','.join(map(str, ix.get('index_key') or [])), 'durum': ix.get('state')}
                               for ix in (r.get('indexes', r) for r in capella_query(client, f'SELECT * FROM system:indexes WHERE bucket_id IN [{sql_in(bucket)}] OR keyspace_id IN [{sql_in(bucket)}]'))]}
        try:
            extra['sorgular'] = [{'query_id': r.get('requestId'), 'sure': r.get('elapsedTime'), 'sonuc': r.get('resultCount'), 'query': str(r.get('statement'))[:600]}
                                 for r in (x.get('completed_requests', x) for x in capella_query(client, 'SELECT * FROM system:completed_requests ORDER BY STR_TO_DURATION(elapsedTime) DESC LIMIT 40'))]
        except NativeError:
            extra['sorgular'] = 'system:completed_requests okunamadı (yetki gerekebilir)'
    return columns, extra


def dynamo(factory, region, action, payload):
    return aws_json(factory, 'dynamodb', region, 'DynamoDB_20120810.' + action, payload, content_type='application/x-amz-json-1.0')


def dynamodb_read(cfg, out, factory, *, evidence=False):
    region = region_of(cfg)
    tables = names(cfg, 'tables', optional=True)
    if not tables:
        token = None
        for _ in range(50):
            r = dynamo(factory, region, 'ListTables', {'Limit': 100, **({'ExclusiveStartTableName': token} if token else {})})
            tables += r.get('TableNames', [])
            token = r.get('LastEvaluatedTableName')
            if not token:
                break
    columns, extra = [], []
    for t in tables:
        d = dynamo(factory, region, 'DescribeTable', {'TableName': t}).get('Table') or {}
        types = {a.get('AttributeName'): a.get('AttributeType') for a in d.get('AttributeDefinitions', [])}
        keys = [(k.get('AttributeName'), k.get('KeyType')) for k in d.get('KeySchema', [])]
        for i, (attr, role) in enumerate(keys, 1):
            columns.append({'tablo': t, 'kolon': attr, 'tip': f'{types.get(attr)} ({role})', 'bos_olabilir': 'NO', 'sira': i})
        for ix_kind in ('GlobalSecondaryIndexes', 'LocalSecondaryIndexes'):
            for ix in d.get(ix_kind, []):
                for attr_role in ix.get('KeySchema', []):
                    attr = attr_role.get('AttributeName')
                    columns.append({'tablo': t, 'kolon': f"{ix.get('IndexName')}:{attr}", 'tip': f"{types.get(attr)} ({attr_role.get('KeyType')})", 'bos_olabilir': 'YES', 'sira': 99})
        extra.append({'tablo': t, 'oge_sayisi': d.get('ItemCount'), 'boyut': d.get('TableSizeBytes'), 'faturalama': (d.get('BillingModeSummary') or {}).get('BillingMode', 'PROVISIONED'),
                      'kapasite': {k: (d.get('ProvisionedThroughput') or {}).get(k) for k in ('ReadCapacityUnits', 'WriteCapacityUnits')},
                      'indexler': [{'index_name': ix.get('IndexName'), 'key_columns': ','.join(k.get('AttributeName') for k in ix.get('KeySchema', [])),
                                    'projeksiyon': (ix.get('Projection') or {}).get('ProjectionType')} for k_ in ('GlobalSecondaryIndexes', 'LocalSecondaryIndexes') for ix in d.get(k_, [])]})
    if not tables:
        raise NativeError('Bölgede DynamoDB tablosu bulunamadı.')
    return columns, {'tablolar': extra}


def neo4j_query(client, database, statement):
    r = client.json('POST', '/db/' + database + '/query/v2', {'statement': statement})
    if r.get('errors'):
        raise NativeError('Neo4j Query API hata döndürdü.')
    data = r.get('data') or {}
    fields = data.get('fields') or []
    return [dict(zip(fields, v)) for v in data.get('values') or []]


def neo4j_read(cfg, out, factory, *, evidence=False):
    client = basic(factory, required(cfg, 'server'), 'NEO4J_USER', 'NEO4J_PASSWORD')
    database = name(cfg, 'database')
    columns = []
    for statement, kind in (('CALL db.schema.nodeTypeProperties()', 'nodeType'), ('CALL db.schema.relTypeProperties()', 'relType')):
        for i, r in enumerate(neo4j_query(client, database, statement), 1):
            columns.append({'tablo': str(r.get(kind)).replace('`', ''), 'kolon': r.get('propertyName') or '(özellik yok)',
                            'tip': '|'.join(r.get('propertyTypes') or []), 'bos_olabilir': 'NO' if r.get('mandatory') else 'YES', 'sira': i})
    extra = {}
    if evidence:
        extra['indexler'] = [{'index_name': r.get('name'), 'tablo': ':' + ':'.join(r.get('labelsOrTypes') or []), 'key_columns': ','.join(r.get('properties') or []),
                              'tur': r.get('type'), 'okuma': r.get('readCount')} for r in neo4j_query(client, database, 'SHOW INDEXES YIELD name, type, labelsOrTypes, properties, readCount')]
        extra['kisitlar'] = [{'ad': r.get('name'), 'tur': r.get('type'), 'tablo': ':' + ':'.join(r.get('labelsOrTypes') or []), 'key_columns': ','.join(r.get('properties') or [])}
                             for r in neo4j_query(client, database, 'SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties')]
    return columns, extra


STORES = {
    'mongodb': dict(label='MongoDB Atlas', read=mongo_read, dialect='mongodb'),
    'redis': dict(label='Redis', read=redis_read, dialect='redis'),
    'capella': dict(label='Couchbase Capella', read=capella_read, dialect='couchbase'),
    'dynamodb': dict(label='DynamoDB', read=dynamodb_read, dialect='dynamodb'),
    'neo4j': dict(label='Neo4j', read=neo4j_read, dialect='neo4j'),
}


def label_of(key):
    return (RELATIONAL.get(key) or STORES[key])['label']


def read(key, cfg, factory, out, *, evidence=False):
    if key in STORES:
        columns, extra = STORES[key]['read'](cfg, out, factory, evidence=evidence)
        return {'urun': label_of(key)}, columns, extra
    spec = RELATIONAL[key]
    scope = names(cfg, spec['scope'], limit=50)
    src = sql_source(spec['source'], cfg, factory, out)
    columns = relational_columns(spec, scope, src)
    extra = {}
    if evidence:
        for section, needed, sql in EVIDENCE_SQL['mssql' if spec['source'] == 'mssql' else spec['source']]:
            try:
                extra[section] = norm_rows(src.query(sql.format(sql_in(scope)), section)[1])
            except NativeError:
                if needed:
                    raise
                extra[section] = 'okunamadı (eklenti/yetki gerekebilir); bu bölüm kanıt dışı'
    return {spec['scope']: scope, 'urun': spec['label']}, columns, extra


def schema_diff(cfg, out, factory):
    key = cfg['operation'].rsplit('-schema-diff', 1)[0]
    scope, columns, _ = read(key, cfg, factory, out)
    return schema_task(cfg, out, columns, scope=scope)


# ---------- Index and query review agent ----------
KINDS = {'index_ekle', 'index_kaldir', 'sorgu_yeniden_yaz', 'yapi_degisikligi'}
SCHEMA = ('{"oneriler":[{"tur":"index_ekle|index_kaldir|sorgu_yeniden_yaz|yapi_degisikligi","nesne":"sema.tablo (kanıttaki tablo/koleksiyon/etiket)",'
          '"kolonlar":["kolon"],"index":"kaldırılacak mevcut index adı (index_kaldir için)","kanit":"dayandığı sorgu kimliği, index adı veya istatistik",'
          '"gerekce":"...","risk":"yazma maliyeti, kilit, depolama","oncelik":"yuksek|orta|dusuk"}],"sorular":["..."]}')


def index_names(extra):
    found = set()
    def walk(v):
        if isinstance(v, dict):
            if v.get('index_name'):
                found.add(str(v['index_name']).lower())
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(extra)
    return found


def review_validator(columns, extra):
    tables = defaultdict(set)
    for c in columns:
        tables[c['tablo'].lower()].add(str(c['kolon']).lower())
    indexes = index_names(extra)

    def validate(obj):
        for i, r in enumerate(need_list(obj, 'oneriler', 'öneriler', maximum=60), 1):
            kind = need_text(r, 'tur', f'öneri {i}', choices=KINDS)
            need_text(r, 'gerekce', f'öneri {i}')
            need_text(r, 'oncelik', f'öneri {i}', choices={'yuksek', 'orta', 'dusuk'})
            target = need_text(r, 'nesne', f'öneri {i}').lower()
            if target not in tables:
                raise ValueError(f'öneri {i}: nesne kanıtta yok: {target}')
            cols = r.get('kolonlar') or []
            if not isinstance(cols, list):
                raise ValueError(f'öneri {i}: kolonlar liste olmalı.')
            bad = [c for c in cols if str(c).lower() not in tables[target]]
            if bad:
                raise ValueError(f'öneri {i}: {target} içinde olmayan kolon: ' + ', '.join(map(str, bad)))
            if kind == 'index_ekle' and not cols:
                raise ValueError(f'öneri {i}: index_ekle en az bir kolon içermeli.')
            if kind == 'index_kaldir' and str(r.get('index', '')).lower() not in indexes:
                raise ValueError(f'öneri {i}: kaldırılacak index kanıtta yok: {r.get("index")}')
        return obj
    return validate


def index_name(table, cols):
    base = 'ix_' + re.sub(r'[^a-z0-9]+', '_', (table.split('.')[-1] + '_' + '_'.join(cols)).lower()).strip('_')
    return base[:60]


def ddl_lines(key, recs):
    dialect = (RELATIONAL.get(key) or {}).get('source') or STORES[key]['dialect']
    lines = []
    for r in recs:
        table, cols = r['nesne'], r.get('kolonlar') or []
        ix = index_name(table, cols)
        if r['tur'] == 'index_ekle':
            lines.append({
                'postgres': f'CREATE INDEX CONCURRENTLY {ix} ON {table} ({", ".join(cols)});',
                'mssql': f'CREATE NONCLUSTERED INDEX {ix} ON {table} ({", ".join(cols)}) WITH (ONLINE = ON);  -- ONLINE: Enterprise/Azure SQL',
                'mysql': f'ALTER TABLE {table} ADD INDEX {ix} ({", ".join(cols)}), ALGORITHM=INPLACE, LOCK=NONE;',
                'oracle': f'CREATE INDEX {ix} ON {table} ({", ".join(cols)}) ONLINE;',
                'mongodb': f'db.getSiblingDB("{table.split(".")[0]}").getCollection("{".".join(table.split(".")[1:])}").createIndex({json.dumps({c: 1 for c in cols})}, {{name: "{ix}"}});',
                'couchbase': f'CREATE INDEX {ix} ON {".".join("`" + p + "`" for p in table.split("."))}({", ".join("`" + c + "`" for c in cols)});',
                'neo4j': f'CREATE INDEX {ix} IF NOT EXISTS FOR (n:{table.lstrip(":").split(":")[0]}) ON ({", ".join("n." + c for c in cols)});',
                'dynamodb': '# aws dynamodb update-table --table-name ' + table + " --attribute-definitions ... --global-secondary-index-updates '" +
                            json.dumps([{'Create': {'IndexName': ix, 'KeySchema': [{'AttributeName': c, 'KeyType': 'HASH' if n == 0 else 'RANGE'} for n, c in enumerate(cols[:2])],
                                                    'Projection': {'ProjectionType': 'KEYS_ONLY'}}}]) + "'",
                'redis': f'# {table}: veri yapısı önerisi (Redis index DDL yoktur): {r["gerekce"]}'}[dialect])
        elif r['tur'] == 'index_kaldir':
            old = r['index']
            lines.append({'postgres': f'DROP INDEX CONCURRENTLY {table.split(".")[0]}.{old};', 'mssql': f'DROP INDEX {old} ON {table};',
                          'mysql': f'ALTER TABLE {table} DROP INDEX {old};', 'oracle': f'DROP INDEX {table.split(".")[0]}.{old} ONLINE;',
                          'mongodb': f'db.getSiblingDB("{table.split(".")[0]}").getCollection("{".".join(table.split(".")[1:])}").dropIndex("{old}");',
                          'couchbase': f'DROP INDEX `{old}` ON {".".join("`" + p + "`" for p in table.split("."))};', 'neo4j': f'DROP INDEX {old} IF EXISTS;',
                          'dynamodb': f"# aws dynamodb update-table --table-name {table} --global-secondary-index-updates '[{{\"Delete\":{{\"IndexName\":\"{old}\"}}}}]'",
                          'redis': f'# {old}'}[dialect])
    head = {'mongodb': '// mongosh taslağı', 'neo4j': '// Cypher taslağı', 'dynamodb': '# AWS CLI taslağı', 'redis': '# Redis notları'}.get(dialect, '-- SQL taslağı')
    return head + ' — doğrulanmış önerilerden kodla üretildi, ÇALIŞTIRILMADI. Bakım penceresinde, önce test ortamında deneyin.\n' + '\n'.join(lines) + '\n'


def index_review(cfg, out, factory):
    key = cfg['operation'].rsplit('-index-review', 1)[0]
    scope, columns, extra = read(key, cfg, factory, out, evidence=True)
    tables = defaultdict(list)
    for c in columns:
        tables[c['tablo']].append({'kolon': c['kolon'], 'tip': c['tip']})
    evidence = {**scope, 'nesneler': [{'tablo': t, 'kolonlar': cols} for t, cols in sorted(tables.items())], 'is_yuku': extra}

    def render(result):
        recs = result['oneriler']
        return {'oneriler.json': plan_json(result),
                'oneriler.md': rows_md('İndeks ve sorgu inceleme önerileri — ' + label_of(key),
                                       [('Öneriler', recs, ['oncelik', 'tur', 'nesne', 'kolonlar', 'index', 'kanit', 'gerekce', 'risk']),
                                        ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])]),
                'degisiklik_taslagi.txt': ddl_lines(key, recs)}
    return agent_task(cfg, out, product=label_of(key), title='İndeks ve sorgu iyileştirme incelemesi', evidence=evidence, schema=SCHEMA,
                      validate=review_validator(columns, extra), render=render,
                      rules='Her öneri kanıttaki bir sorgu istatistiğine, index kullanım sayısına veya tablo istatistiğine dayansın ve "kanit" alanında bunu adlandırsın. '
                            'Kullanılmayan index önerirken istatistiklerin son yeniden başlatmadan beri biriktiğini hatırlat; yazma yükünü risk olarak yaz.')


HANDLERS = {}
for _key in list(RELATIONAL) + list(STORES):
    HANDLERS[_key + '-schema-diff'] = schema_diff
    HANDLERS[_key + '-index-review'] = index_review
