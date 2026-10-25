"""Data migration: pre/post migration reconciliation from the migration service's own table statistics and independent row
counts (code) and an evidence-bound migration wave & cutover plan whose dependency order is checked by code (agent)."""
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, aws_json, basic, bearer, count_reconcile, ident, journal, name, names, need_list, need_text, number,
                           opt_int, plan_json, read_table, receipt, region_of, required, rows_md, secret, sql_in, sql_source, write_csv)

TABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_$#]{0,127}(\.[A-Za-z_][A-Za-z0-9_$#]{0,127}){0,2}')


def counts(path):
    if not path or not Path(path).exists():
        return {}
    _, rows = read_table(path)
    return {str(r.get('tablo', '')).lower(): number(r.get('satir')) for r in rows if r.get('tablo')}


def side_query(side, cfg, factory, out, sql, stage):
    """Run one read-only query on cfg[side] = {kind, host/odbc/dsn…}; its password comes from <SIDE>_DB_PASSWORD (or password_env)."""
    conf = cfg.get(side) or {}
    kind = conf.get('kind')
    if kind not in ('postgres', 'mysql', 'mssql', 'oracle'):
        raise NativeError(f'{side}.kind postgres, mysql, mssql veya oracle olmalı.')
    old = os.environ.get('DB_PASSWORD')
    os.environ['DB_PASSWORD'] = os.environ.get(conf.get('password_env', side.upper() + '_DB_PASSWORD'), '')
    try:
        return kind, sql_source(kind, conf, factory, out).query(sql(kind) if callable(sql) else sql, stage)[1]
    finally:
        if old is None:
            os.environ.pop('DB_PASSWORD', None)
        else:
            os.environ['DB_PASSWORD'] = old


def db_counts(side, cfg, factory, out, tables):
    union = ' UNION ALL '.join(f"SELECT '{t}' AS tablo, COUNT(*) AS satir FROM {t}" for t in tables)
    _, rows = side_query(side, cfg, factory, out, union, side + '_counts')
    return {str(r.get('tablo')).lower(): number(r.get('satir')) for r in rows}


def table_list(cfg):
    return names(cfg, 'tables', 'tables (şema.tablo)', limit=500, pattern=re.compile(r'[A-Za-z_][A-Za-z0-9_$#]{0,127}'))


# ---------- AWS Database Migration Service ----------
def dms(factory, cfg, action, payload):
    return aws_json(factory, 'dms', region_of(cfg), 'AmazonDMSv20160101.' + action, payload)


def aws_dms_stats(cfg, factory, out):
    arn = required(cfg, 'replication_task_arn')
    if not arn.startswith('arn:aws:dms:'):
        raise NativeError('replication_task_arn bir DMS görev ARN’si olmalı.')
    stats, marker = [], None
    for _ in range(100):
        r = dms(factory, cfg, 'DescribeTableStatistics', {'ReplicationTaskArn': arn, 'MaxRecords': 500, **({'Marker': marker} if marker else {})})
        stats += r.get('TableStatistics', [])
        marker = r.get('Marker')
        if not marker:
            break
    src, tgt = counts(cfg.get('source_counts_file')), counts(cfg.get('target_counts_file'))
    rows = []
    for s in stats:
        t = f"{s.get('SchemaName')}.{s.get('TableName')}"
        loaded = (number(s.get('FullLoadRows')) or 0) + (number(s.get('Inserts')) or 0) - (number(s.get('Deletes')) or 0)
        rows.append({'nesne': t, 'okunan': src.get(t.lower(), s.get('FullLoadRows')), 'yazilan': tgt.get(t.lower(), loaded), 'atlanan': number(s.get('FullLoadErrorRows')) or 0,
                     'tablo_durumu': s.get('TableState'), 'dogrulama': s.get('ValidationState'), 'dogrulama_hatali': s.get('ValidationFailedRecords'),
                     'ekleme': s.get('Inserts'), 'guncelleme': s.get('Updates'), 'silme': s.get('Deletes')})
    return rows, {'gorev': arn, 'tablo': len(stats)}


# ---------- Azure Database Migration Service (SQL migration) ----------
def azure_dms_stats(cfg, factory, out):
    client = bearer(factory, 'https://management.azure.com', 'AZURE_ACCESS_TOKEN')
    target = required(cfg, 'target_resource_id')
    if not re.fullmatch(r'/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Sql/(servers|managedInstances)/[^/]+', target):
        raise NativeError('target_resource_id …/providers/Microsoft.Sql/servers/X veya managedInstances/X olmalı.')
    db = name(cfg, 'target_database')
    r = client.json('GET', f'{target}/providers/Microsoft.DataMigration/databaseMigrations/{ident(db)}?' + urlencode({'$expand': 'MigrationStatusDetails',
                                                                                                                  'api-version': cfg.get('api_version', '2023-07-15-preview')}))
    props = r.get('properties') or {}
    details = props.get('migrationStatusDetails') or {}
    src, tgt = counts(cfg.get('source_counts_file')), counts(cfg.get('target_counts_file'))
    rows = []
    for t in details.get('listOfCopyProgressDetails') or []:
        name_ = str(t.get('tableName'))
        rows.append({'nesne': name_, 'okunan': src.get(name_.lower(), t.get('rowsRead')), 'yazilan': tgt.get(name_.lower(), t.get('rowsCopied')), 'atlanan': 0,
                     'tablo_durumu': t.get('status'), 'kopyalama_hizi': t.get('copyThroughput'), 'baslangic': t.get('copyStart')})
    return rows, {'gocu_durumu': props.get('migrationStatus'), 'kaynak_db': props.get('sourceDatabaseName'), 'hata': (props.get('migrationFailureError') or {}).get('message', '')}


# ---------- Google Database Migration Service ----------
def google_dms_stats(cfg, factory, out):
    client = bearer(factory, 'https://datamigration.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    job = required(cfg, 'migration_job')
    if not re.fullmatch(r'projects/[^/]+/locations/[^/]+/migrationJobs/[^/]+', job):
        raise NativeError('migration_job projects/P/locations/L/migrationJobs/J biçiminde olmalı.')
    info = client.json('GET', f'/v1/{job}')
    tables = table_list(cfg)
    src, tgt = db_counts('source', cfg, factory, out, tables), db_counts('target', cfg, factory, out, tables)
    rows = [{'nesne': t, 'okunan': src.get(t.lower()), 'yazilan': tgt.get(t.lower()), 'atlanan': 0} for t in tables]
    return rows, {'is': info.get('displayName') or job, 'durum': info.get('state'), 'faz': info.get('phase'), 'hata': (info.get('error') or {}).get('message', ''),
                  'tur': info.get('type')}


# ---------- SAP S/4HANA Migration Cockpit (staging template vs loaded records) ----------
def cockpit_template(path, header_rows):
    root = ET.parse(path).getroot()
    ss = 'urn:schemas-microsoft-com:office:spreadsheet'
    sheets = {}
    for ws in root.iter(f'{{{ss}}}Worksheet'):
        title = ws.get(f'{{{ss}}}Name')
        rows = list(ws.iter(f'{{{ss}}}Row'))
        data = [r for r in rows[header_rows:] if any((c.findtext(f'{{{ss}}}Data') or '').strip() for c in r.iter(f'{{{ss}}}Cell'))]
        if title and title.lower() not in ('introduction', 'field list', 'giriş', 'alan listesi'):
            sheets[title] = len(data)
    return sheets


def cockpit_stats(cfg, factory, out):
    sheets = cockpit_template(required(cfg, 'template_file'), opt_int(cfg, 'header_rows', 8, 0, 50))
    mapping = cfg.get('sheet_entities') or {}
    if not isinstance(mapping, dict) or not mapping:
        raise NativeError('sheet_entities {"Sayfa adı": {"service": "/sap/opu/odata/sap/API_X", "entity_set": "A_X", "filter": "..."}} gerekli.')
    server = required(cfg, 'server')
    client = bearer(factory, server, 'SAP_ACCESS_TOKEN') if os.environ.get('SAP_ACCESS_TOKEN') else basic(factory, server, 'SAP_USER', 'SAP_PASSWORD')
    rows = []
    for sheet, target in mapping.items():
        if sheet not in sheets:
            raise NativeError('Şablonda sayfa yok: ' + sheet)
        service, entity = target.get('service', ''), target.get('entity_set', '')
        if not re.fullmatch(r'/sap/opu/odata4?/[A-Za-z0-9_/;=.-]{3,200}', service) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', entity):
            raise NativeError('sheet_entities service/entity_set geçersiz: ' + sheet)
        q = '?' + urlencode({'$filter': target['filter']}, quote_via=quote) if target.get('filter') else ''
        raw, _ = client.request('GET', f'{service.rstrip("/")}/{entity}/$count{q}', accept='text/plain')
        rows.append({'nesne': sheet, 'okunan': sheets[sheet], 'yazilan': number(raw.decode().strip()), 'atlanan': 0, 'hedef': f'{service}/{entity}'})
    return rows, {'sablon': Path(cfg['template_file']).name, 'sayfalar': sheets}


# ---------- Oracle Zero Downtime Migration ----------
def zdm_stats(cfg, factory, out):
    job = str(required(cfg, 'job_id'))
    if not job.isdigit():
        raise NativeError('job_id sayısal ZDM iş kimliği olmalı.')
    try:
        done = subprocess.run([cfg.get('zdmcli', 'zdmcli'), 'query', 'job', '-jobid', job], capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        raise NativeError('zdmcli bulunamadı (ZDM hizmet ana bilgisayarında çalıştırın).') from None
    if done.returncode != 0:
        raise NativeError('zdmcli query job hata döndürdü: ' + done.stderr[-300:])
    phases = re.findall(r'^\s*(ZDM_[A-Z0-9_]+)\s+\.+\s+(\w+)', done.stdout, re.M)
    status = re.search(r'Current status:\s*(\w+)', done.stdout)
    tables = table_list(cfg)
    src, tgt = db_counts('source', cfg, factory, out, tables), db_counts('target', cfg, factory, out, tables)
    rows = [{'nesne': t, 'okunan': src.get(t.lower()), 'yazilan': tgt.get(t.lower()), 'atlanan': 0} for t in tables]
    return rows, {'is': job, 'durum': status.group(1) if status else '', 'fazlar': [{'faz': p, 'durum': s} for p, s in phases]}


PRODUCTS = {'aws-dms': ('AWS Database Migration Service', aws_dms_stats), 'azure-dms': ('Azure Database Migration Service', azure_dms_stats),
            'google-dms': ('Google Database Migration Service', google_dms_stats), 'migration-cockpit': ('SAP S/4HANA Migration Cockpit', cockpit_stats),
            'oracle-zdm': ('Oracle Zero Downtime Migration', zdm_stats)}
COLUMNS = ['nesne', 'okunan', 'yazilan', 'atlanan', 'fark', 'durum']


def migration_reconcile(cfg, out, factory):
    key = cfg['operation'].rsplit('-migration-reconcile', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    if not rows:
        raise NativeError('Migrasyon hizmeti tablo istatistiği döndürmedi.')
    result = count_reconcile(rows)
    extra = sorted({k for r in result for k in r} - set(COLUMNS))
    write_csv(out, 'gecis_mutabakati.csv', result, COLUMNS + extra)
    journal(out, state='verified')
    failed_validation = [r['nesne'] for r in result if str(r.get('dogrulama', '')).lower() in ('error', 'failed', 'mismatched records', 'suspended')]
    return receipt(out, 'ozet.json', {'urun': label, **info, 'tablo': len(result), 'fark': sum(1 for r in result if r['durum'] == 'FARK'),
                                      'sayac_yok': sum(1 for r in result if r['durum'] == 'sayaç yok'), 'dogrulama_hatali': failed_validation[:50]})


# ---------- Wave & cutover plan agent ----------
FK_SQL = {
    'postgres': ("SELECT tc.table_schema || '.' || tc.table_name AS child, ccu.table_schema || '.' || ccu.table_name AS parent FROM information_schema.table_constraints tc "
                 "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name WHERE tc.constraint_type = 'FOREIGN KEY'"),
    'mysql': ("SELECT CONCAT(table_schema, '.', table_name) AS child, CONCAT(referenced_table_schema, '.', referenced_table_name) AS parent "
              "FROM information_schema.key_column_usage WHERE referenced_table_name IS NOT NULL"),
    'mssql': ("SELECT OBJECT_SCHEMA_NAME(parent_object_id) + '.' + OBJECT_NAME(parent_object_id) AS child, "
              "OBJECT_SCHEMA_NAME(referenced_object_id) + '.' + OBJECT_NAME(referenced_object_id) AS parent FROM sys.foreign_keys"),
    'oracle': ("SELECT a.owner || '.' || a.table_name AS child, c.owner || '.' || c.table_name AS parent FROM all_constraints a JOIN all_constraints c "
               "ON a.r_owner = c.owner AND a.r_constraint_name = c.constraint_name WHERE a.constraint_type = 'R'"),
}


def dependencies(cfg, factory, out):
    """Foreign keys among the migrated tables when the source database is reachable over SQL."""
    if (cfg.get('source') or {}).get('kind') not in FK_SQL:
        return []
    wanted = {t.lower() for t in table_list(cfg)}
    try:
        _, rows = side_query('source', cfg, factory, out, lambda kind: FK_SQL[kind], 'foreign_keys')
    except NativeError:
        return []
    return [{'cocuk': r['child'], 'ebeveyn': r['parent']} for r in rows if str(r.get('child', '')).lower() in wanted and str(r.get('parent', '')).lower() in wanted and r['child'] != r['parent']]


SCHEMA = ('{"dalgalar":[{"ad":"Dalga 1","tablolar":["kanıttaki tablo"],"neden":"...","kesinti_dakika":30}],"kapsam_disi":[{"tablo":"...","neden":"..."}],'
          '"cutover":[{"adim":"...","sorumlu":"rol","sure_dk":15,"geri_donus":"..."}],"dogrulama":["satır sayısı, sağlama toplamı ..."],"riskler":["..."],"sorular":["..."]}')


def wave_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-cutover-plan', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    tables = {str(r['nesne']).lower(): r for r in rows}
    deps = dependencies(cfg, factory, out)
    evidence = {'tablolar': [{'tablo': r['nesne'], 'okunan': r.get('okunan'), 'yazilan': r.get('yazilan'), 'durum': r.get('tablo_durumu', '')} for r in rows[:1000]],
                'bagimliliklar': deps, 'hizmet': info}

    def validate(obj):
        seen = {}
        waves = need_list(obj, 'dalgalar', 'dalgalar', maximum=30)
        for wi, w in enumerate(waves):
            need_text(w, 'ad', 'dalga')
            for t in w.get('tablolar') or []:
                k = str(t).lower()
                if k not in tables:
                    raise ValueError(f'{w["ad"]}: kanıtta olmayan tablo: {t}')
                if k in seen:
                    raise ValueError(f'tablo birden fazla dalgada: {t}')
                seen[k] = wi
        for x in obj.get('kapsam_disi') or []:
            k = str(x.get('tablo', '')).lower() if isinstance(x, dict) else ''
            if k not in tables or k in seen:
                raise ValueError(f'kapsam dışı tablo geçersiz veya dalgada da var: {x}')
            seen[k] = None
        missing = sorted(set(tables) - set(seen))
        if missing:
            raise ValueError('Hiçbir dalgaya veya kapsam dışına konmamış tablolar: ' + ', '.join(tables[m]['nesne'] for m in missing[:15]))
        for d in deps:
            c, p = seen.get(d['cocuk'].lower()), seen.get(d['ebeveyn'].lower())
            if c is not None and p is not None and p > c:
                raise ValueError(f'bağımlılık sırası bozuk: {d["cocuk"]} (dalga {c + 1}) ebeveyni {d["ebeveyn"]} (dalga {p + 1}) sonra taşınıyor.')
        for s in need_list(obj, 'cutover', 'cutover adımları', maximum=60):
            need_text(s, 'adim', 'cutover'); need_text(s, 'geri_donus', s['adim'])
        return obj

    def render(result):
        flat = [{'dalga': w['ad'], 'sira': i + 1, 'tablo': t, 'kaynak_satir': tables[str(t).lower()].get('okunan', '')} for i, w in enumerate(result['dalgalar']) for t in w.get('tablolar') or []]
        import csv, io
        buf = io.StringIO()
        wr = csv.DictWriter(buf, fieldnames=['dalga', 'sira', 'tablo', 'kaynak_satir'], lineterminator='\n')
        wr.writeheader(); wr.writerows(flat)
        total_min = sum(int(number(s.get('sure_dk')) or 0) for s in result['cutover'])
        return {'gecis_plani.json': plan_json(result), 'dalgalar.csv': buf.getvalue(),
                'gecis_plani.md': rows_md(f'Migrasyon dalgası ve cutover planı — {label} (cutover toplam ≈ {total_min} dk)',
                                          [('Dalgalar', [{**w, 'tablolar': ', '.join(w.get('tablolar') or [])} for w in result['dalgalar']], ['ad', 'tablolar', 'neden', 'kesinti_dakika']),
                                           ('Kapsam dışı', result.get('kapsam_disi') or [], ['tablo', 'neden']),
                                           ('Cutover runbook', result['cutover'], ['adim', 'sorumlu', 'sure_dk', 'geri_donus']),
                                           ('Doğrulama, riskler ve sorular', [{'madde': x} for x in result.get('dogrulama', []) + result.get('riskler', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Migrasyon dalgası ve cutover planı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Her tabloyu tam olarak bir dalgaya ya da kapsam dışına koy; bağımlı (FK) tabloları ebeveynleriyle aynı veya sonraki dalgaya yerleştir. '
                            'Her cutover adımı için geri dönüş yolu yaz; mutabakat farkı olan tabloları riskler bölümünde belirt.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-migration-reconcile'] = migration_reconcile
    HANDLERS[_key + '-cutover-plan'] = wave_plan
