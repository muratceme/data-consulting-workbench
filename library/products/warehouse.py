"""Data warehouses: live schema snapshot/diff (code) and an evidence-bound dimensional model design (agent)."""
import re

from products.core import (NativeError, agent_task, check_refs, literal, name, names, need_list, need_text, plan_json, rows_md,
                           schema_task, sql_in, sql_source)


def bq_region(cfg):
    location = cfg['location'].lower()
    if not re.fullmatch(r'[a-z]+(-[a-z0-9]+)*', location):
        raise NativeError('location örn. EU veya europe-west1 olmalı.')
    return '`' + cfg['project_id'] + '`.`region-' + location + '`'


def teradata_type(r):
    code = str(r.get('columntype') or '').strip().upper()
    length = int(float(r.get('columnlength') or 0))
    digits, scale = r.get('decimaltotaldigits'), r.get('decimalfractionaldigits')
    simple = {'I': 'INTEGER', 'I8': 'BIGINT', 'I2': 'SMALLINT', 'I1': 'BYTEINT', 'F': 'FLOAT', 'DA': 'DATE', 'TS': 'TIMESTAMP(6)',
              'SZ': 'TIMESTAMP(6) WITH TIME ZONE', 'AT': 'TIME(6)', 'N': 'NUMBER', 'JN': 'JSON', 'BO': 'BLOB', 'CO': 'CLOB'}
    if code in simple:
        return simple[code]
    if code == 'D':
        return f'DECIMAL({int(float(digits or 18))},{int(float(scale or 0))})'
    if code in ('CV', 'CF'):
        return ('VARCHAR' if code == 'CV' else 'CHAR') + f'({length})'
    return code or 'UNKNOWN'


# Each product: SQL source kind, scope config key, columns SQL builder, row mapper, optional row-count SQL, DDL dialect.
PRODUCTS = {
    'bigquery': dict(label='BigQuery', source='bigquery', scope='datasets',
                     columns=lambda c, s: f'SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position FROM {bq_region(c)}.INFORMATION_SCHEMA.COLUMNS WHERE table_schema IN ({sql_in(s)})',
                     counts=lambda c, s: f'SELECT table_schema, table_name, total_rows FROM {bq_region(c)}.INFORMATION_SCHEMA.TABLE_STORAGE WHERE table_schema IN ({sql_in(s)})'),
    'snowflake': dict(label='Snowflake', source='snowflake', scope='schemas',
                      columns=lambda c, s: f'SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position FROM {name(c, "database")}.INFORMATION_SCHEMA.COLUMNS WHERE table_schema IN ({sql_in(s)})',
                      counts=lambda c, s: f"SELECT table_schema, table_name, row_count AS total_rows FROM {name(c, 'database')}.INFORMATION_SCHEMA.TABLES WHERE table_schema IN ({sql_in(s)}) AND table_type = 'BASE TABLE'"),
    'fabric-warehouse': dict(label='Fabric Warehouse', source='mssql', scope='schemas',
                             columns=lambda c, s: f'SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA IN ({sql_in(s)})'),
    'synapse': dict(label='Azure Synapse', source='mssql', scope='schemas',
                    columns=lambda c, s: f'SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA IN ({sql_in(s)})'),
    'redshift': dict(label='Redshift', source='redshift', scope='schemas',
                     columns=lambda c, s: f'SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position FROM svv_columns WHERE table_schema IN ({sql_in(s)})',
                     counts=lambda c, s: f'SELECT "schema" AS table_schema, "table" AS table_name, tbl_rows AS total_rows FROM svv_table_info WHERE "schema" IN ({sql_in(s)})'),
    'adw': dict(label='Autonomous Data Warehouse', source='ords', scope='schemas',
                columns=lambda c, s: f"SELECT owner AS table_schema, table_name, column_name, data_type, CASE nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable, column_id AS ordinal_position FROM all_tab_columns WHERE owner IN ({sql_in(s)})",
                counts=lambda c, s: f'SELECT owner AS table_schema, table_name, num_rows AS total_rows FROM all_tables WHERE owner IN ({sql_in(s)})'),
    'teradata': dict(label='Teradata Vantage', source='teradata', scope='databases',
                     columns=lambda c, s: f'SELECT TRIM(DatabaseName) AS table_schema, TRIM(TableName) AS table_name, TRIM(ColumnName) AS column_name, ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits, Nullable, ColumnId AS ordinal_position FROM DBC.ColumnsV WHERE DatabaseName IN ({sql_in(s)})'),
    'clickhouse': dict(label='ClickHouse', source='clickhouse', scope='databases',
                       columns=lambda c, s: f'SELECT database AS table_schema, table AS table_name, name AS column_name, type AS data_type, position AS ordinal_position FROM system.columns WHERE database IN ({sql_in(s)})',
                       counts=lambda c, s: f'SELECT database AS table_schema, name AS table_name, total_rows FROM system.tables WHERE database IN ({sql_in(s)}) AND is_temporary = 0'),
}


def to_schema(product, rows):
    result = []
    for r in rows:
        r = {str(k).lower(): v for k, v in r.items()}
        if product == 'teradata':
            kind, nullable = teradata_type(r), str(r.get('nullable', '')).strip().upper() == 'Y'
        elif product == 'clickhouse':
            kind, nullable = str(r.get('data_type')), str(r.get('data_type', '')).startswith('Nullable(')
        else:
            kind, nullable = r.get('data_type'), str(r.get('is_nullable', '')).upper() in ('YES', 'Y', 'TRUE')
        result.append({'tablo': f"{r.get('table_schema')}.{r.get('table_name')}", 'kolon': r.get('column_name'), 'tip': kind,
                       'bos_olabilir': 'YES' if nullable else 'NO', 'sira': r.get('ordinal_position')})
    return result


def read_schema(key, cfg, factory, out, *, with_counts=False):
    spec = PRODUCTS[key]
    scope = names(cfg, spec['scope'], limit=50)
    if key == 'bigquery':
        name(cfg, 'project_id', re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]'))
    source = sql_source(spec['source'], cfg, factory, out)
    _, rows = source.query(spec['columns'](cfg, scope), 'columns')
    columns = to_schema(key, rows)
    counts = {}
    if with_counts and spec.get('counts'):
        _, count_rows = source.query(spec['counts'](cfg, scope), 'row_counts')
        for r in count_rows:
            r = {str(k).lower(): v for k, v in r.items()}
            counts[f"{r.get('table_schema')}.{r.get('table_name')}".lower()] = r.get('total_rows')
    return scope, columns, counts


def schema_diff(cfg, out, factory):
    key = cfg['operation'].rsplit('-schema-diff', 1)[0]
    scope, columns, _ = read_schema(key, cfg, factory, out)
    return schema_task(cfg, out, columns, scope={PRODUCTS[key]['scope']: scope, 'urun': PRODUCTS[key]['label']})


# ---------- Dimensional model agent ----------
AGG = {'sum', 'count', 'avg', 'min', 'max', 'count_distinct'}
IDENT = re.compile(r'[a-z][a-z0-9_]{0,62}')
DIALECT = {  # surrogate key, SCD timestamp, boolean, quoting is avoided: generated names are plain identifiers
    'bigquery': ('INT64', 'TIMESTAMP', 'BOOL'), 'snowflake': ('NUMBER(38,0)', 'TIMESTAMP_NTZ', 'BOOLEAN'),
    'fabric-warehouse': ('BIGINT', 'DATETIME2(6)', 'BIT'), 'synapse': ('BIGINT', 'DATETIME2(6)', 'BIT'), 'redshift': ('BIGINT', 'TIMESTAMP', 'BOOLEAN'),
    'adw': ('NUMBER(19)', 'TIMESTAMP', 'NUMBER(1)'), 'teradata': ('BIGINT', 'TIMESTAMP(6)', 'BYTEINT'), 'clickhouse': ('UInt64', 'DateTime64(6)', 'Bool')}
SCHEMA = ('{"olgular":[{"ad":"fct_ornek","grain":"bir satır neyi temsil eder","kaynak_tablolar":["sema.tablo"],'
          '"olcular":[{"ad":"tutar","kaynak_kolon":"sema.tablo.kolon","toplama":"sum|count|avg|min|max|count_distinct"}],'
          '"boyutlar":["dim_ornek"]}],"boyutlar":[{"ad":"dim_ornek","kaynak_tablo":"sema.tablo","dogal_anahtar":["kolon"],"scd_tipi":1,'
          '"nitelikler":["kolon"]}],"varsayimlar":["..."],"sorular":["..."]}')


def model_validator(columns):
    tables = {}
    for c in columns:
        tables.setdefault(c['tablo'].lower(), {})[c['kolon'].lower()] = c
    allowed = {t: t for t in tables}

    def validate(obj):
        facts = need_list(obj, 'olgular', 'olgu tabloları', maximum=30)
        dims = need_list(obj, 'boyutlar', 'boyutlar', minimum=0, maximum=60)
        dim_names = set()
        for d in dims:
            dname = need_text(d, 'ad', 'boyut')
            if not IDENT.fullmatch(dname):
                raise ValueError('Boyut adı küçük harf ve alt çizgi olmalı: ' + dname)
            dim_names.add(dname)
            table = need_text(d, 'kaynak_tablo', dname).lower()
            check_refs([table], allowed, dname + ' kaynak tablosu')
            keys, attrs = d.get('dogal_anahtar'), d.get('nitelikler', [])
            if not isinstance(keys, list) or not keys or not isinstance(attrs, list):
                raise ValueError(dname + ': dogal_anahtar ve nitelikler liste olmalı.')
            check_refs([str(k).lower() for k in keys + attrs], tables[table], dname + ' kolonları')
            if d.get('scd_tipi') not in (0, 1, 2):
                raise ValueError(dname + ': scd_tipi 0, 1 veya 2 olmalı.')
        for f in facts:
            fname = need_text(f, 'ad', 'olgu')
            if not IDENT.fullmatch(fname):
                raise ValueError('Olgu adı küçük harf ve alt çizgi olmalı: ' + fname)
            need_text(f, 'grain', fname)
            sources = f.get('kaynak_tablolar')
            if not isinstance(sources, list) or not sources:
                raise ValueError(fname + ': kaynak_tablolar boş olamaz.')
            check_refs([str(s).lower() for s in sources], allowed, fname + ' kaynak tabloları')
            for m in need_list(f, 'olcular', fname + ' ölçüleri', maximum=60):
                mname = need_text(m, 'ad', fname + ' ölçüsü')
                if not IDENT.fullmatch(mname):
                    raise ValueError('Ölçü adı küçük harf ve alt çizgi olmalı: ' + mname)
                need_text(m, 'toplama', mname, choices=AGG)
                ref = need_text(m, 'kaynak_kolon', mname).lower()
                table, _, column = ref.rpartition('.')
                if table not in tables or column not in tables[table]:
                    raise ValueError(f'{mname}: kaynak_kolon kanıtta yok: {ref}')
            unknown = [d for d in f.get('boyutlar', []) if d not in dim_names]
            if unknown:
                raise ValueError(fname + ' tanımsız boyuta bağlanıyor: ' + ', '.join(map(str, unknown)))
        return obj
    return validate, tables


def ddl(key, model, tables, target):
    sk, ts, flag = DIALECT[key]
    lines = [f'-- Taslak DDL ({PRODUCTS[key]["label"]}); doğrulanmış model JSON\'ından kodla üretildi, çalıştırılmadı.', '']
    def create(table, cols, order):
        body = ',\n  '.join(f'{c} {t}' for c, t in cols)
        tail = f'\nENGINE = MergeTree ORDER BY ({order})' if key == 'clickhouse' else ''
        return f'CREATE TABLE {target}.{table} (\n  {body}\n){tail};\n'
    for d in model['boyutlar']:
        src = tables[d['kaynak_tablo'].lower()]
        cols = [(d['ad'] + '_sk', sk)] + [(k.lower(), src[k.lower()]['tip']) for k in d['dogal_anahtar']]
        cols += [(a.lower(), src[a.lower()]['tip']) for a in d['nitelikler'] if a.lower() not in [k.lower() for k in d['dogal_anahtar']]]
        if d['scd_tipi'] == 2:
            cols += [('gecerlilik_baslangic', ts), ('gecerlilik_bitis', ts), ('gecerli_mi', flag)]
        lines.append(create(d['ad'], cols, d['ad'] + '_sk'))
    for f in model['olgular']:
        cols = [(d + '_sk', sk) for d in f.get('boyutlar', [])]
        for m in f['olcular']:
            table, _, column = m['kaynak_kolon'].lower().rpartition('.')
            source_type = tables[table][column]['tip']
            cols.append((m['ad'], sk if m['toplama'] in ('count', 'count_distinct') else source_type))
        lines.append(create(f['ad'], cols or [('kayit_sayisi', sk)], ', '.join(c for c, _ in cols[:3]) or 'tuple()'))
    return '\n'.join(lines)


def dimensional_model(cfg, out, factory):
    key = cfg['operation'].rsplit('-dimensional-model', 1)[0]
    scope, columns, counts = read_schema(key, cfg, factory, out, with_counts=True)
    target = name(cfg, 'target_schema')
    validate, tables = model_validator(columns)
    evidence = {'kapsam': scope, 'tablolar': [{'tablo': t, 'satir': counts.get(t), 'kolonlar': [{'kolon': c['kolon'], 'tip': c['tip'], 'bos_olabilir': c['bos_olabilir']}
                                                                                             for c in cols.values()]} for t, cols in sorted(tables.items())]}
    def render(model):
        return {'boyutsal_model.json': plan_json(model),
                'boyutsal_model.md': rows_md('Boyutsal model önerisi — ' + PRODUCTS[key]['label'],
                                             [('Olgu tabloları', model['olgular'], ['ad', 'grain', 'kaynak_tablolar', 'boyutlar']),
                                              ('Boyutlar', model['boyutlar'], ['ad', 'kaynak_tablo', 'dogal_anahtar', 'scd_tipi', 'nitelikler']),
                                              ('Varsayımlar ve sorular', [{'madde': x} for x in model.get('varsayimlar', []) + model.get('sorular', [])], ['madde'])]),
                'ddl_taslak.sql': ddl(key, model, tables, target)}
    return agent_task(cfg, out, product=PRODUCTS[key]['label'], title='Boyutsal model ve veri ambarı tasarımı', evidence=evidence, schema=SCHEMA,
                      validate=validate, render=render,
                      rules='Her olgunun grain\'i tek cümle olsun. Ölçüler yalnızca sayısal kolonlardan (count hariç) gelsin. '
                            'SCD tipi seçimini niteliklerin değişme sıklığına göre gerekçelendir; bilinmiyorsa soru olarak yaz.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-schema-diff'] = schema_diff
    HANDLERS[_key + '-dimensional-model'] = dimensional_model
