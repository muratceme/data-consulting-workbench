"""Lakehouses: cross-layer schema check (code) and an evidence-bound Bronze/Silver/Gold architecture (agent).

Readers use each product's own metadata surface: SQL information schema (Databricks SQL, Fabric SQL endpoint,
BigQuery for BigLake, Dremio), catalog APIs (Unity Catalog, Glue for Lake Formation, Iceberg REST catalog) or the
table format's own transaction log on storage (Delta log on OneLake/S3/local, Hudi timeline on S3/local)."""
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, aws_call, aws_json, bearer, ident, journal, name, names, need_list, need_text,
                           plan_json, receipt, region_of, required, rows_md, secret, sql_in, sql_source, write_csv, NAME)


# ---------- Storage backends for table-format logs ----------
class LocalStore:
    def __init__(self, cfg):
        self.root = Path(required(cfg, 'root_path')).resolve()

    def list(self, prefix):
        path = (self.root / prefix).resolve()
        if self.root not in path.parents and path != self.root:
            raise NativeError('Yol kök klasörün dışına çıkıyor.')
        return sorted(p.name + ('/' if p.is_dir() else '') for p in path.iterdir()) if path.is_dir() else []

    def get(self, key):
        return (self.root / key).read_bytes()


class S3Store:
    def __init__(self, cfg, factory):
        self.factory, self.region = factory, region_of(cfg)
        self.bucket = name(cfg, 'bucket', re.compile(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]'))
        self.host = f'{self.bucket}.s3.{self.region}.amazonaws.com'
        self.base = (cfg.get('root_prefix') or '').strip('/')

    def key(self, rel):
        return '/'.join(x for x in (self.base, rel.strip('/')) if x)

    def list(self, prefix):
        items, token = [], None
        ns = '{http://s3.amazonaws.com/doc/2006-03-01/}'
        for _ in range(200):
            q = {'list-type': '2', 'prefix': self.key(prefix) + '/', 'delimiter': '/', **({'continuation-token': token} if token else {})}
            raw = aws_call(self.factory, 's3', self.region, host=self.host, method='GET', path='/', query=urlencode(sorted(q.items()), quote_via=quote),
                           accept='application/xml')
            root = ET.fromstring(raw)
            start = len(self.key(prefix)) + 1
            items += [c.findtext(ns + 'Key')[start:] for c in root.iter(ns + 'Contents')]
            items += [p.findtext(ns + 'Prefix')[start:] for p in root.iter(ns + 'CommonPrefixes')]
            token = root.findtext(ns + 'NextContinuationToken')
            if not token:
                return sorted(i for i in items if i)
        raise NativeError('S3 listeleme sınırı aşıldı.')

    def get(self, rel):
        return aws_call(self.factory, 's3', self.region, host=self.host, method='GET', path='/' + quote(self.key(rel)), accept='application/octet-stream')


class OneLakeStore:
    """OneLake exposes the ADLS Gen2 DFS API; filesystem = workspace, path = <item>.Lakehouse/..."""
    def __init__(self, cfg, factory):
        self.client = bearer(factory, 'https://onelake.dfs.fabric.microsoft.com', 'ONELAKE_ACCESS_TOKEN', {'x-ms-version': '2023-11-03'})
        self.workspace = required(cfg, 'workspace')
        self.item = required(cfg, 'item_path')
        if not re.fullmatch(r'[^/\\]{1,256}\.(Lakehouse|Warehouse)', self.item):
            raise NativeError('item_path örn. Satis.Lakehouse olmalı.')

    def list(self, prefix):
        directory = self.item + '/' + prefix.strip('/')
        r = self.client.json('GET', '/' + ident(self.workspace) + '?' + urlencode({'resource': 'filesystem', 'directory': directory, 'recursive': 'false'}))
        start = len(directory) + 1
        return sorted(p['name'][start:] + ('/' if str(p.get('isDirectory')).lower() == 'true' else '') for p in r.get('paths', []) if p.get('name', '').startswith(directory + '/'))

    def get(self, rel):
        raw, _ = self.client.request('GET', '/' + ident(self.workspace) + '/' + quote(self.item + '/' + rel.strip('/')), accept='application/octet-stream')
        return raw


def store_of(cfg, factory):
    kind = cfg.get('storage', 'local')
    if kind == 's3':
        return S3Store(cfg, factory)
    if kind == 'onelake':
        return OneLakeStore(cfg, factory)
    if kind == 'local':
        return LocalStore(cfg)
    raise NativeError('storage: local, s3 veya onelake olmalı.')


def spark_type(t):
    if isinstance(t, str):
        return t.upper()
    if isinstance(t, dict):
        kind = t.get('type')
        if kind == 'array':
            return f'ARRAY<{spark_type(t.get("elementType"))}>'
        if kind == 'map':
            return f'MAP<{spark_type(t.get("keyType"))},{spark_type(t.get("valueType"))}>'
        if kind == 'struct':
            return 'STRUCT<' + ','.join(f'{f["name"]}:{spark_type(f["type"])}' for f in t.get('fields', [])) + '>'
    return str(t).upper()


def delta_schema(store, table_dir):
    """Latest metaData action: newest JSON commit first, then the checkpoint named by _last_checkpoint (needs pyarrow)."""
    log = table_dir.rstrip('/') + '/_delta_log'
    files = store.list(log)
    commits = sorted((f for f in files if re.fullmatch(r'\d{20}\.json', f)), reverse=True)
    for commit in commits[:200]:
        for line in store.get(log + '/' + commit).decode('utf-8').splitlines():
            if '"metaData"' in line:
                meta = json.loads(line).get('metaData') or {}
                return json.loads(meta.get('schemaString') or '{}'), meta.get('partitionColumns') or [], commit
    if '_last_checkpoint' in files:
        last = json.loads(store.get(log + '/_last_checkpoint'))
        version = int(last['version'])
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise NativeError('Şema yalnızca Parquet checkpoint’te kalmış; pip install pyarrow') from None
        name_ = f'{version:020d}.checkpoint.parquet'
        table = pq.read_table(io.BytesIO(store.get(log + '/' + name_)), columns=['metaData'])
        for meta in table.column('metaData').to_pylist():
            if meta:
                return json.loads(meta.get('schemaString') or '{}'), meta.get('partitionColumns') or [], name_
    raise NativeError('Delta günlüğünde metaData bulunamadı: ' + table_dir)


def delta_layers(cfg, factory, layers):
    """Layer = folder under the root (e.g. Tables/bronze or bronze); each child folder with _delta_log is a table."""
    store = store_of(cfg, factory)
    prefix = (cfg.get('tables_prefix') or '').strip('/')
    columns = []
    for layer in layers:
        base = '/'.join(x for x in (prefix, layer) if x)
        for entry in store.list(base):
            if not entry.endswith('/'):
                continue
            table = entry.rstrip('/')
            if '_delta_log/' not in store.list(base + '/' + table):
                continue
            schema, partitions, source = delta_schema(store, base + '/' + table)
            for i, f in enumerate(schema.get('fields', []), 1):
                columns.append({'tablo': f'{layer}.{table}', 'kolon': f['name'], 'tip': spark_type(f.get('type')) + (' (partition)' if f['name'] in partitions else ''),
                                'bos_olabilir': 'YES' if f.get('nullable', True) else 'NO', 'sira': i})
    return columns


AVRO = {'string': 'STRING', 'int': 'INT', 'long': 'BIGINT', 'double': 'DOUBLE', 'float': 'FLOAT', 'boolean': 'BOOLEAN', 'bytes': 'BINARY'}


def avro_type(t):
    if isinstance(t, list):
        inner = [x for x in t if x != 'null']
        return '|'.join(avro_type(x)[0] for x in inner), 'null' in t
    if isinstance(t, dict):
        if t.get('logicalType'):
            return t['logicalType'].upper(), False
        return {'array': 'ARRAY', 'map': 'MAP', 'record': 'STRUCT', 'enum': 'ENUM', 'fixed': 'FIXED'}.get(t.get('type'), str(t.get('type')).upper()), False
    return AVRO.get(t, str(t).upper()), False


def hudi_layers(cfg, factory, layers):
    """Hudi timeline: newest completed (delta)commit carries the writer schema in extraMetadata.schema."""
    store = store_of(cfg, factory)
    prefix = (cfg.get('tables_prefix') or '').strip('/')
    columns = []
    for layer in layers:
        base = '/'.join(x for x in (prefix, layer) if x)
        for entry in store.list(base):
            table = entry.rstrip('/')
            if not entry.endswith('/'):
                continue
            timeline = base + '/' + table + '/.hoodie'
            files = store.list(timeline)
            if not files:
                continue
            instants = sorted((f for f in files if re.fullmatch(r'\d+(_\d+)?\.(commit|deltacommit|replacecommit)', f)), reverse=True)
            schema = None
            for instant in instants[:50]:
                extra = (json.loads(store.get(timeline + '/' + instant) or b'{}').get('extraMetadata') or {})
                if extra.get('schema'):
                    schema = json.loads(extra['schema'])
                    break
            if schema is None:
                raise NativeError('Hudi zaman çizelgesinde şema bulunamadı (v1 timeline .commit dosyaları beklenir): ' + table)
            for i, f in enumerate(x for x in schema.get('fields', []) if not x['name'].startswith('_hoodie_')):
                kind, nullable = avro_type(f.get('type'))
                columns.append({'tablo': f'{layer}.{table}', 'kolon': f['name'], 'tip': kind, 'bos_olabilir': 'YES' if nullable else 'NO', 'sira': i + 1})
    return columns


# ---------- Catalog/SQL readers ----------
def info_schema(kind, cfg, factory, out, layers, table_expr):
    src = sql_source(kind, cfg, factory, out)
    columns = []
    for layer in layers:
        _, rows = src.query(table_expr(layer), 'columns_' + layer)
        for r in rows:
            r = {str(k).lower(): v for k, v in r.items()}
            columns.append({'tablo': f"{layer}.{r.get('table_name')}", 'kolon': r.get('column_name'), 'tip': r.get('data_type'),
                            'bos_olabilir': r.get('is_nullable'), 'sira': r.get('ordinal_position')})
    return columns


SELECT = 'SELECT table_name, column_name, data_type, is_nullable, ordinal_position FROM '


def databricks_columns(cfg, factory, out, layers):
    catalog = name(cfg, 'catalog')
    return info_schema('databricks', cfg, factory, out, layers, lambda l: SELECT + f"`{catalog}`.information_schema.columns WHERE table_schema = '{l}'")


def fabric_columns(cfg, factory, out, layers):
    return info_schema('mssql', cfg, factory, out, layers, lambda l: SELECT + f"[{l}].INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo'")


def biglake_columns(cfg, factory, out, layers):
    project = name(cfg, 'project_id', re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]'))
    return info_schema('bigquery', cfg, factory, out, layers, lambda l: SELECT + f'`{project}`.`{l}`.INFORMATION_SCHEMA.COLUMNS')


def dremio_columns(cfg, factory, out, layers):
    return info_schema('dremio', cfg, factory, out, layers, lambda l: 'SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type, '
                       f"IS_NULLABLE AS is_nullable, ORDINAL_POSITION AS ordinal_position FROM INFORMATION_SCHEMA.\"COLUMNS\" WHERE TABLE_SCHEMA = '{l}'")


def unity_columns(cfg, factory, out, layers):
    client = bearer(factory, required(cfg, 'workspace_url'), 'DATABRICKS_ACCESS_TOKEN')
    catalog = name(cfg, 'catalog')
    columns = []
    for layer in layers:
        token = None
        for _ in range(100):
            q = {'catalog_name': catalog, 'schema_name': layer, 'max_results': 50, **({'page_token': token} if token else {})}
            r = client.json('GET', '/api/2.1/unity-catalog/tables?' + urlencode(q))
            for t in r.get('tables', []):
                for c in t.get('columns', []):
                    columns.append({'tablo': f"{layer}.{t.get('name')}", 'kolon': c.get('name'), 'tip': c.get('type_text') or c.get('type_name'),
                                    'bos_olabilir': 'YES' if c.get('nullable', True) else 'NO', 'sira': (c.get('position') or 0) + 1})
            token = r.get('next_page_token')
            if not token:
                break
    return columns


def glue_columns(cfg, factory, out, layers):
    region = region_of(cfg)
    catalog = cfg.get('catalog_id')
    columns = []
    for layer in layers:
        token = None
        for _ in range(100):
            payload = {'DatabaseName': layer, 'MaxResults': 100, **({'CatalogId': catalog} if catalog else {}), **({'NextToken': token} if token else {})}
            r = aws_json(factory, 'glue', region, 'AWSGlue.GetTables', payload)
            for t in r.get('TableList', []):
                cols = (t.get('StorageDescriptor') or {}).get('Columns', []) + [{**p, 'Type': p.get('Type', '') + ' (partition)'} for p in t.get('PartitionKeys', [])]
                for i, c in enumerate(cols, 1):
                    columns.append({'tablo': f"{layer}.{t.get('Name')}", 'kolon': c.get('Name'), 'tip': c.get('Type'), 'bos_olabilir': 'YES', 'sira': i})
            token = r.get('NextToken')
            if not token:
                break
    return columns


def iceberg_columns(cfg, factory, out, layers):
    client = bearer(factory, required(cfg, 'catalog_uri'), 'ICEBERG_TOKEN')
    prefix = cfg.get('prefix')
    base = '/v1/' + (ident(prefix) + '/' if prefix else '')
    columns = []
    for layer in layers:
        ns = ident(layer.replace('.', '\x1f'))
        token, tables = None, []
        for _ in range(100):
            r = client.json('GET', base + 'namespaces/' + ns + '/tables' + ('?pageToken=' + ident(token) if token else ''))
            tables += [t.get('name') for t in r.get('identifiers', [])]
            token = r.get('next-page-token')
            if not token:
                break
        for t in tables:
            meta = client.json('GET', base + 'namespaces/' + ns + '/tables/' + ident(t)).get('metadata') or {}
            current = meta.get('current-schema-id')
            schema = next((s for s in meta.get('schemas', []) if s.get('schema-id') == current), None) or meta.get('schema') or {}
            for i, f in enumerate(schema.get('fields', []), 1):
                kind = f.get('type')
                columns.append({'tablo': f'{layer}.{t}', 'kolon': f.get('name'), 'tip': kind if isinstance(kind, str) else str((kind or {}).get('type')),
                                'bos_olabilir': 'NO' if f.get('required') else 'YES', 'sira': i})
    return columns


PRODUCTS = {
    'databricks-lakehouse': dict(label='Databricks Lakehouse', read=databricks_columns),
    'unity-catalog': dict(label='Unity Catalog', read=unity_columns),
    'fabric-lakehouse': dict(label='Fabric Lakehouse', read=fabric_columns),
    'onelake': dict(label='OneLake', read=lambda c, f, o, l: delta_layers({**c, 'storage': 'onelake'}, f, l)),
    'lake-formation': dict(label='S3 / Lake Formation', read=glue_columns),
    'biglake': dict(label='Cloud Storage / BigLake', read=biglake_columns),
    'iceberg': dict(label='Apache Iceberg', read=iceberg_columns),
    'hudi': dict(label='Apache Hudi', read=lambda c, f, o, l: hudi_layers(c, f, l)),
    'delta-lake': dict(label='Delta Lake', read=lambda c, f, o, l: delta_layers(c, f, l)),
    'dremio': dict(label='Dremio', read=dremio_columns),
}
LAYER = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}')


def read(key, cfg, factory, out):
    layers = names(cfg, 'layers', 'Katmanlar (sıralı)', limit=6, pattern=LAYER)
    if len(layers) < 2 and cfg['operation'].endswith('layer-check'):
        raise NativeError('layers en az iki katman içermeli (ör. bronze, silver, gold).')
    columns = PRODUCTS[key]['read'](cfg, factory, out, layers)
    if not columns:
        raise NativeError('Katmanlarda tablo/kolon bulunamadı; ad ve yetkiyi kontrol edin.')
    return layers, columns


def layer_diff(layers, columns, table_map):
    by = defaultdict(dict)
    for c in columns:
        layer, _, table = c['tablo'].partition('.')
        by[(layer, table.lower())][c['kolon'].lower()] = c
    rows = []
    for upper, lower in zip(layers, layers[1:]):
        uppers = sorted(t for (l, t) in by if l == upper)
        for table in uppers:
            target = table_map.get(f'{upper}.{table}', table).lower()
            if (lower, target) not in by:
                rows.append({'kaynak': f'{upper}.{table}', 'hedef': f'{lower}.{target}', 'kolon': '', 'bulgu': 'sonraki katmanda tablo yok', 'onceki': '', 'sonraki': ''})
                continue
            a, b = by[(upper, table)], by[(lower, target)]
            for col in sorted(set(a) | set(b)):
                if col not in b:
                    rows.append({'kaynak': f'{upper}.{table}', 'hedef': f'{lower}.{target}', 'kolon': col, 'bulgu': 'kolon sonraki katmana taşınmamış', 'onceki': a[col]['tip'], 'sonraki': ''})
                elif col not in a:
                    rows.append({'kaynak': f'{upper}.{table}', 'hedef': f'{lower}.{target}', 'kolon': col, 'bulgu': 'türetilmiş/yeni kolon', 'onceki': '', 'sonraki': b[col]['tip']})
                elif str(a[col]['tip']).upper().replace(' (PARTITION)', '') != str(b[col]['tip']).upper().replace(' (PARTITION)', ''):
                    rows.append({'kaynak': f'{upper}.{table}', 'hedef': f'{lower}.{target}', 'kolon': col, 'bulgu': 'tip değişti', 'onceki': a[col]['tip'], 'sonraki': b[col]['tip']})
                elif str(a[col]['bos_olabilir']).upper() in ('NO', 'FALSE') and str(b[col]['bos_olabilir']).upper() in ('YES', 'TRUE'):
                    rows.append({'kaynak': f'{upper}.{table}', 'hedef': f'{lower}.{target}', 'kolon': col, 'bulgu': 'NOT NULL kuralı gevşedi', 'onceki': 'NOT NULL', 'sonraki': 'NULL'})
    return rows


def layer_check(cfg, out, factory):
    key = cfg['operation'].rsplit('-layer-check', 1)[0]
    layers, columns = read(key, cfg, factory, out)
    table_map = cfg.get('table_map') or {}
    if not isinstance(table_map, dict):
        raise NativeError('table_map {"bronze.ham_tablo": "temiz_tablo"} biçiminde olmalı.')
    rows = layer_diff(layers, columns, {str(k).lower(): str(v) for k, v in table_map.items()})
    write_csv(out, 'katman_farki.csv', rows, ['kaynak', 'hedef', 'kolon', 'bulgu', 'onceki', 'sonraki'])
    receipt(out, 'sema.json', {'katmanlar': layers, 'kolonlar': columns})
    counts = defaultdict(int)
    for r in rows:
        counts[r['bulgu']] += 1
    per_layer = {l: len({c['tablo'] for c in columns if c['tablo'].startswith(l + '.')}) for l in layers}
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': PRODUCTS[key]['label'], 'katman_tablo_sayisi': per_layer, 'bulgu': dict(counts),
                                      'not': 'Kolonun sonraki katmana taşınmaması bilinçli olabilir; tip değişikliği ve gevşeyen NOT NULL kuralını önce inceleyin.'})


# ---------- Medallion architecture agent ----------
LAYERS = {'bronze', 'silver', 'gold'}
SCHEMA = ('{"tablolar":[{"tablo":"katman.tablo (kanıttaki)","katman":"bronze|silver|gold","gerekce":"..."}],'
          '"akislar":[{"kaynaklar":["katman.tablo"],"hedef":"katman.tablo","yeni":true,"donusum":"temizleme/birleştirme/özetleme adımları",'
          '"anahtar":["kaynak tablodaki kolon"],"artimli":"append|merge|overwrite","kalite_kontrolleri":["..."]}],'
          '"kurallar":["adlandırma, saklama, şema evrimi kuralları"],"sorular":["..."]}')


def medallion_validator(columns):
    tables = defaultdict(set)
    for c in columns:
        tables[c['tablo'].lower()].add(str(c['kolon']).lower())

    def validate(obj):
        for t in need_list(obj, 'tablolar', 'tablo katmanları', minimum=1, maximum=400):
            ref = need_text(t, 'tablo', 'tablo').lower()
            if ref not in tables:
                raise ValueError('tablolar: kanıtta olmayan tablo: ' + ref)
            need_text(t, 'katman', ref, choices=LAYERS)
        for i, f in enumerate(need_list(obj, 'akislar', 'akışlar', minimum=1, maximum=200), 1):
            sources = f.get('kaynaklar')
            if not isinstance(sources, list) or not sources:
                raise ValueError(f'akış {i}: kaynaklar boş olamaz.')
            bad = [s for s in sources if str(s).lower() not in tables]
            if bad:
                raise ValueError(f'akış {i}: kanıtta olmayan kaynak: ' + ', '.join(map(str, bad)))
            target = need_text(f, 'hedef', f'akış {i}')
            if target.lower() not in tables and not f.get('yeni'):
                raise ValueError(f'akış {i}: hedef kanıtta yok; yeni tablo ise "yeni": true yazın: {target}')
            if f.get('yeni') and not all(NAME.fullmatch(p) for p in target.split('.')):
                raise ValueError(f'akış {i}: yeni hedef adı geçersiz: {target}')
            need_text(f, 'artimli', f'akış {i}', choices={'append', 'merge', 'overwrite'})
            keys = f.get('anahtar') or []
            source_cols = set().union(*(tables[str(s).lower()] for s in sources))
            missing = [k for k in keys if str(k).lower() not in source_cols]
            if missing:
                raise ValueError(f'akış {i}: anahtar kolonu kaynaklarda yok: ' + ', '.join(map(str, missing)))
        return obj
    return validate


def medallion(cfg, out, factory):
    key = cfg['operation'].rsplit('-medallion', 1)[0]
    layers, columns = read(key, cfg, factory, out)
    grouped = defaultdict(list)
    for c in columns:
        grouped[c['tablo']].append({'kolon': c['kolon'], 'tip': c['tip']})
    evidence = {'mevcut_katmanlar': layers, 'tablolar': [{'tablo': t, 'kolonlar': cols} for t, cols in sorted(grouped.items())]}

    def render(result):
        flows = [{**f, 'kaynaklar': ', '.join(f['kaynaklar']), 'anahtar': ', '.join(f.get('anahtar') or []),
                  'kalite_kontrolleri': '; '.join(f.get('kalite_kontrolleri') or [])} for f in result['akislar']]
        files = {'medallion_plani.json': plan_json(result),
                 'medallion_plani.md': rows_md('Bronze / Silver / Gold mimarisi — ' + PRODUCTS[key]['label'],
                                              [('Tablo katmanları', result['tablolar'], ['tablo', 'katman', 'gerekce']),
                                               ('Akışlar', flows, ['kaynaklar', 'hedef', 'yeni', 'artimli', 'anahtar', 'donusum', 'kalite_kontrolleri']),
                                               ('Kurallar ve sorular', [{'madde': x} for x in result.get('kurallar', []) + result.get('sorular', [])], ['madde'])])}
        buf = io.StringIO()
        import csv
        w = csv.DictWriter(buf, fieldnames=['kaynaklar', 'hedef', 'yeni', 'artimli', 'anahtar', 'donusum', 'kalite_kontrolleri'], extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(flows)
        files['akislar.csv'] = buf.getvalue()
        return files
    return agent_task(cfg, out, product=PRODUCTS[key]['label'], title='Bronze / Silver / Gold mimarisi', evidence=evidence, schema=SCHEMA,
                      validate=medallion_validator(columns), render=render,
                      rules='Bronze ham ve değişmez, Silver temizlenmiş/tekilleştirilmiş, Gold iş odaklı özet olsun. Merge önerilen her akışın anahtarı olsun. '
                            'Mevcut tablo adlarını değiştirmeyi önermek yerine yeni hedefleri "yeni": true ile işaretle.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-layer-check'] = layer_check
    HANDLERS[_key + '-medallion'] = medallion
