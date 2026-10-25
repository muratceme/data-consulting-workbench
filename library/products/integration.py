"""Data integration: post-transfer row reconciliation from the tool's own run counters (code) and an evidence-bound
source-to-target mapping & ETL plan (agent).

Counters come from each product: ADF/Fabric copy activity output, SSISDB data statistics, IDMC activity log,
PowerCenter REP_SESS_LOG, Talend observability metrics, Qlik Enterprise Manager table statuses, Fivetran destination
counts, Airbyte stream stats, Data Services monitor logs, ODI step logs, GoldenGate STATS and Meltano/Singer metrics."""
import base64
import datetime as dt
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import native
from products.core import (NativeError, agent_task, basic, bearer, count_reconcile, ident, journal, name, names, need_list, need_text, number, opt_int,
                           plain, plan_json, read_table, receipt, region_of, required, rows_md, secret, sql_in, sql_source, write_csv, NAME)


def window(cfg, default=7):
    days = opt_int(cfg, 'days', default, 1, 90)
    end = dt.datetime.now(dt.timezone.utc)
    return days, (end - dt.timedelta(days=days)).isoformat().replace('+00:00', 'Z'), end.isoformat().replace('+00:00', 'Z')


def counts_file(cfg):
    """Optional independent counts (tablo, satir) from the source/target system, e.g. SELECT COUNT(*) exports."""
    path = cfg.get('counts_file')
    if not path or not Path(path).exists():
        return {}
    _, rows = read_table(path)
    return {str(r.get('tablo', '')).lower(): number(r.get('satir')) for r in rows if r.get('tablo')}


def flow_counts(edges):
    """edges: [(from, to, rows, is_error)] of one run → rows leaving pure sources, entering pure sinks, rejected."""
    sources = {e[0] for e in edges} - {e[1] for e in edges}
    sinks = {e[1] for e in edges} - {e[0] for e in edges}
    read_ = sum(e[2] for e in edges if e[0] in sources and not e[3])
    written = sum(e[2] for e in edges if e[1] in sinks and not e[3])
    rejected = sum(e[2] for e in edges if e[3])
    return read_, written, rejected


# ---------- Azure Data Factory / Fabric Data Factory ----------
def adf_base(cfg):
    return ('/subscriptions/' + ident(required(cfg, 'subscription_id')) + '/resourceGroups/' + ident(required(cfg, 'resource_group')) +
            '/providers/Microsoft.DataFactory/factories/' + ident(required(cfg, 'factory_name')))


def copy_rows(activity):
    out = activity.get('output') or {}
    return {'calisma': activity.get('pipelineRunId', ''), 'nesne': activity.get('activityName'), 'durum_platform': activity.get('status'),
            'okunan': out.get('rowsRead'), 'yazilan': out.get('rowsCopied'), 'atlanan': out.get('rowsSkipped') or 0, 'baslangic': activity.get('activityRunStart')}


def adf_counts(cfg, factory, out):
    client = bearer(factory, 'https://management.azure.com', 'AZURE_ACCESS_TOKEN')
    base, version = adf_base(cfg), '?api-version=2018-06-01'
    _, start, end = window(cfg)
    filters = [{'operand': 'PipelineName', 'operator': 'Equals', 'values': names(cfg, 'pipelines', limit=20, pattern=re.compile(r'[^\x00-\x1f]{1,140}'))}] if cfg.get('pipelines') else []
    runs = client.json('POST', base + '/queryPipelineRuns' + version, {'lastUpdatedAfter': start, 'lastUpdatedBefore': end, 'filters': filters}).get('value', [])
    rows = []
    for run in runs[:opt_int(cfg, 'max_runs', 50, 1, 500)]:
        acts = client.json('POST', base + '/pipelineruns/' + ident(run['runId']) + '/queryActivityruns' + version,
                           {'lastUpdatedAfter': start, 'lastUpdatedBefore': end, 'filters': [{'operand': 'ActivityType', 'operator': 'Equals', 'values': ['Copy']}]}).get('value', [])
        rows += [{**copy_rows(a), 'pipeline': run.get('pipelineName')} for a in acts]
    return rows


def fabric_df_counts(cfg, factory, out):
    client = bearer(factory, 'https://api.fabric.microsoft.com', 'FABRIC_ACCESS_TOKEN')
    ws, item = required(cfg, 'workspace_id'), required(cfg, 'pipeline_id')
    _, start, end = window(cfg)
    jobs = client.json('GET', f'/v1/workspaces/{ident(ws)}/items/{ident(item)}/jobs/instances').get('value', [])
    rows = []
    for job in jobs[:opt_int(cfg, 'max_runs', 20, 1, 200)]:
        acts = client.json('POST', f'/v1/workspaces/{ident(ws)}/datapipelines/pipelineruns/{ident(job["id"])}/queryactivityruns',
                           {'filters': [{'operand': 'ActivityType', 'operator': 'Equals', 'values': ['Copy']}], 'lastUpdatedAfter': start, 'lastUpdatedBefore': end}).get('value', [])
        rows += [{**copy_rows(a), 'calisma': job['id'], 'pipeline': item} for a in acts]
    return rows


# ---------- SSIS (SSISDB catalog) ----------
def ssis_counts(cfg, factory, out):
    days, _, _ = window(cfg)
    src = sql_source('mssql', cfg, factory, out)
    packages = names(cfg, 'packages', limit=50, pattern=re.compile(r'[^\x00-\x1f\']{1,260}'), optional=True)
    where = f'AND e.package_name IN ({sql_in(packages)})' if packages else ''
    _, rows = src.query('SELECT e.execution_id, e.package_name, e.start_time, s.package_path, s.source_component_name, s.destination_component_name, '
                        's.dataflow_path_name, SUM(s.rows_sent) AS rows_sent FROM SSISDB.catalog.executions e JOIN SSISDB.catalog.execution_data_statistics s '
                        f'ON s.execution_id = e.execution_id WHERE e.start_time >= DATEADD(day, -{days}, SYSDATETIMEOFFSET()) {where} '
                        'GROUP BY e.execution_id, e.package_name, e.start_time, s.package_path, s.source_component_name, s.destination_component_name, s.dataflow_path_name',
                        'data_statistics')
    flows = defaultdict(list)
    meta = {}
    for r in rows:
        key = (r['execution_id'], r['package_path'])
        meta[key] = r
        flows[key].append((r['source_component_name'], r['destination_component_name'], int(number(r['rows_sent']) or 0), 'error' in str(r.get('dataflow_path_name', '')).lower()))
    result = []
    for key, edges in flows.items():
        a, b, c = flow_counts(edges)
        result.append({'calisma': key[0], 'pipeline': meta[key]['package_name'], 'nesne': key[1], 'okunan': a, 'yazilan': b, 'atlanan': c, 'baslangic': meta[key]['start_time']})
    return result


# ---------- Informatica IDMC / PowerCenter ----------
def idmc_session(cfg, factory):
    login = factory(required(cfg, 'login_url'), {})
    r = login.json('POST', '/ma/api/v2/user/login', {'@type': 'login', 'username': secret('IDMC_USER'), 'password': secret('IDMC_PASSWORD')})
    server, session = required(r, 'serverUrl'), required(r, 'icSessionId')
    return factory(server, {'icSessionId': session})


def idmc_counts(cfg, factory, out):
    client = idmc_session(cfg, factory)
    q = {'rowLimit': str(opt_int(cfg, 'max_runs', 200, 1, 1000))}
    if cfg.get('task_id'):
        q['taskId'] = required(cfg, 'task_id')
    raw, _ = client.request('GET', 'api/v2/activity/activityLog?' + urlencode(q))
    entries = json.loads(raw) if raw else []
    rows = []
    for e in entries if isinstance(entries, list) else []:
        for item in e.get('entries') or [e]:
            rows.append({'calisma': e.get('runId'), 'pipeline': e.get('objectName'), 'nesne': item.get('objectName') or e.get('objectName'),
                         'okunan': item.get('successSourceRows'), 'yazilan': item.get('successTargetRows'),
                         'atlanan': (number(item.get('failedTargetRows')) or 0), 'kaynak_hata': item.get('failedSourceRows'), 'baslangic': e.get('startTime')})
    return rows


def powercenter_counts(cfg, factory, out):
    days, _, _ = window(cfg)
    kind = cfg.get('repository_type', 'oracle')
    if kind not in ('oracle', 'mssql'):
        raise NativeError('repository_type oracle veya mssql olmalı.')
    since = f"SYSDATE - {days}" if kind == 'oracle' else f"DATEADD(day, -{days}, GETDATE())"
    folders = names(cfg, 'folders', limit=50, pattern=re.compile(r'[^\x00-\x1f\']{1,80}'), optional=True)
    where = f' AND SUBJECT_AREA IN ({sql_in(folders)})' if folders else ''
    _, rows = sql_source(kind, cfg, factory, out).query(
        'SELECT SUBJECT_AREA, WORKFLOW_ID, SESSION_NAME, ACTUAL_START, SUCC_SOURCE_ROWS, FAILED_SOURCE_ROWS, SUCC_TARGET_ROWS, FAILED_TARGET_ROWS, RUN_STATUS_CODE '
        f'FROM REP_SESS_LOG WHERE ACTUAL_START >= {since}{where}', 'session_log')
    return [{'calisma': f"{r.get('workflow_id')}/{r.get('actual_start')}", 'pipeline': r.get('subject_area'), 'nesne': r.get('session_name'),
             'okunan': r.get('succ_source_rows'), 'yazilan': r.get('succ_target_rows'), 'atlanan': r.get('failed_target_rows') or 0,
             'kaynak_hata': r.get('failed_source_rows'), 'baslangic': r.get('actual_start')} for r in rows]


# ---------- Talend Cloud ----------
def talend_client(cfg, factory):
    region = name(cfg, 'region', re.compile(r'[a-z0-9-]{2,20}'))
    return bearer(factory, f'https://api.{region}.cloud.talend.com', 'TALEND_TOKEN')


def talend_counts(cfg, factory, out):
    client = talend_client(cfg, factory)
    task = required(cfg, 'task_id')
    days, _, _ = window(cfg)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    raw, _ = client.request('GET', f'/processing/executables/tasks/{ident(task)}/executions?' + urlencode({'from': now - days * 86400000, 'to': now, 'limit': 50}))
    data = json.loads(raw) if raw else {}
    executions = data.get('items', data) if isinstance(data, dict) else data
    rows = []
    for ex in executions[:opt_int(cfg, 'max_runs', 20, 1, 100)]:
        exid = ex.get('executionId') or ex.get('id')
        metrics = []
        for page in range(50):
            raw, _ = client.request('GET', f'/monitoring/observability/executions/{ident(exid)}/component?' + urlencode({'limit': 200, 'offset': page * 200}))
            batch = json.loads(raw) if raw else []
            batch = batch.get('items', batch) if isinstance(batch, dict) else batch
            metrics += batch
            if len(batch) < 200:
                break
        edges = [(m.get('connector_label') or m.get('connector_id'), m.get('target_label') or m.get('target_id'), int(number(m.get('component_connection_rows_total')) or 0),
                  str(m.get('connection_name', '')).lower() in ('reject', 'error')) for m in metrics if m.get('target_label') or m.get('target_id')]
        a, b, c = flow_counts(edges) if edges else (None, None, 0)
        rows.append({'calisma': exid, 'pipeline': task, 'nesne': ex.get('jobName') or task, 'okunan': a, 'yazilan': b, 'atlanan': c, 'baslangic': ex.get('startTimestamp') or ex.get('startTime')})
    return rows


# ---------- Qlik Replicate (Enterprise Manager API) ----------
def qem_session(cfg, factory):
    base = required(cfg, 'server').rstrip('/') + '/attunityenterprisemanager/api/v1'
    login = factory(base, {'Authorization': native.basic_auth(secret('QEM_USER'), secret('QEM_PASSWORD'))})
    _, headers = login.request('GET', 'login')
    session = native.header(headers, 'EnterpriseManager.APISessionID')
    if not session:
        raise NativeError('Enterprise Manager oturum kimliği dönmedi.')
    return factory(base, {'EnterpriseManager.APISessionID': session})


def replicate_counts(cfg, factory, out):
    client = qem_session(cfg, factory)
    server, task = required(cfg, 'replicate_server'), required(cfg, 'task')
    r = client.json('GET', f'servers/{ident(server)}/tasks/{ident(task)}/tables?action=getstatus&completed=true&loading=true&error=true&queued=true&change_processing=true')
    counts = counts_file(cfg)
    rows = []
    for t in r.get('tablestatuses', r.get('table_statuses', [])):
        ref = f"{t.get('source_schema') or t.get('schema_on_source')}.{t.get('source_table') or t.get('table_on_source')}"
        fl, cdc = t.get('table_full_load_info') or t.get('full_load_info') or {}, t.get('table_cdc_info') or t.get('cdc_info') or {}
        target = counts.get(ref.lower())
        rows.append({'calisma': task, 'pipeline': server, 'nesne': ref, 'durum_platform': t.get('state'), 'okunan': fl.get('transferred_row_count'),
                     'yazilan': target, 'atlanan': 0, 'cdc_ekleme': cdc.get('insert_count'), 'cdc_guncelleme': cdc.get('update_count'), 'cdc_silme': cdc.get('delete_count')})
    return rows


# ---------- Fivetran (destination counts + platform log) ----------
def fivetran_counts(cfg, factory, out):
    kind = cfg.get('destination', 'snowflake')
    if kind not in ('snowflake', 'bigquery', 'databricks', 'redshift', 'postgres', 'mssql'):
        raise NativeError('destination snowflake, bigquery, databricks, redshift, postgres veya mssql olmalı.')
    src = sql_source(kind, cfg, factory, out)
    tables = names(cfg, 'tables', 'tables (şema.tablo)', limit=200)
    counts = counts_file(cfg)
    if not counts:
        raise NativeError('counts_file (tablo,satir) gerekli: kaynak sistemdeki satır sayıları hedefteki COUNT(*) ile karşılaştırılır.')
    union = ' UNION ALL '.join(f"SELECT '{t}' AS tablo, COUNT(*) AS satir FROM {t}" for t in tables)
    _, dest = src.query(union, 'destination_counts')
    rows = []
    for r in dest:
        t = str(r.get('tablo'))
        rows.append({'calisma': 'son durum', 'pipeline': cfg.get('connection_id', ''), 'nesne': t, 'okunan': counts.get(t.lower()), 'yazilan': r.get('satir'), 'atlanan': 0})
    log = cfg.get('log_schema')
    if log:
        days, _, _ = window(cfg)
        since = {'bigquery': f'TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)', 'mssql': f'DATEADD(day, -{days}, GETDATE())'}.get(kind, f"CURRENT_TIMESTAMP - INTERVAL '{days} days'")
        _, events = src.query(f"SELECT time_stamp, message_data FROM {name(cfg, 'log_schema')}.log WHERE message_event = 'records_modified' AND time_stamp >= {since}", 'fivetran_log')
        modified = defaultdict(int)
        for e in events:
            try:
                data = json.loads(e.get('message_data') or '{}')
            except ValueError:
                continue
            modified[f"{data.get('schema')}.{data.get('table')}".lower()] += int(number(data.get('count')) or 0)
        for r in rows:
            r['fivetran_degisen'] = modified.get(r['nesne'].lower(), '')
    return rows


# ---------- Airbyte ----------
def airbyte_counts(cfg, factory, out):
    connection = required(cfg, 'connection_id')
    if cfg.get('config_api'):
        client = bearer(factory, required(cfg, 'server'), 'AIRBYTE_TOKEN')
        jobs = client.json('POST', '/api/v1/jobs/list', {'configTypes': ['sync'], 'configId': connection, 'pagination': {'pageSize': opt_int(cfg, 'max_runs', 10, 1, 100)}}).get('jobs', [])
        rows = []
        for j in jobs:
            detail = client.json('POST', '/api/v1/jobs/get', {'id': (j.get('job') or {}).get('id')})
            last = (detail.get('attempts') or [{}])[-1].get('attempt') or {}
            for s in last.get('streamStats') or []:
                st = s.get('stats') or {}
                rows.append({'calisma': (detail.get('job') or {}).get('id'), 'pipeline': connection, 'nesne': s.get('streamName'), 'okunan': st.get('recordsEmitted'),
                             'yazilan': st.get('recordsCommitted'), 'atlanan': 0, 'baslangic': last.get('createdAt')})
        return rows
    client = bearer(factory, 'https://api.airbyte.com', 'AIRBYTE_ACCESS_TOKEN')
    data = client.json('GET', '/v1/jobs?' + urlencode({'connectionId': connection, 'jobType': 'sync', 'limit': opt_int(cfg, 'max_runs', 10, 1, 100)})).get('data', [])
    return [{'calisma': j.get('jobId'), 'pipeline': connection, 'nesne': 'tüm akışlar', 'okunan': None, 'yazilan': j.get('rowsSynced'), 'atlanan': 0,
             'durum_platform': j.get('status'), 'baslangic': j.get('startTime')} for j in data]


# ---------- SAP Data Services (monitor log) ----------
def ds_counts(cfg, factory, out):
    folder = Path(required(cfg, 'monitor_log_dir'))
    sources = [s.lower() for s in names(cfg, 'source_objects', limit=100, pattern=re.compile(r'[^\x00-\x1f,]{1,200}'))]
    targets = [t.lower() for t in names(cfg, 'target_objects', limit=100, pattern=re.compile(r'[^\x00-\x1f,]{1,200}'))]
    rows = []
    for log in sorted(folder.glob('monitor_*.txt'))[-opt_int(cfg, 'max_runs', 20, 1, 200):]:
        read_, written = defaultdict(int), defaultdict(int)
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = [p.strip() for p in re.split(r'\t|,', line)]
            if len(parts) < 3 or number(parts[2]) is None:
                continue
            path, count = parts[0], int(number(parts[2]))
            obj = path.rsplit('/', 1)[-1].lower()
            flow = path.strip('/').split('/', 1)[0]
            if any(obj.startswith(s) for s in sources):
                read_[flow] = max(read_[flow], count)
            if any(obj.startswith(t) for t in targets):
                written[flow] = max(written[flow], count)
        for flow in sorted(set(read_) | set(written)):
            rows.append({'calisma': log.name, 'pipeline': flow, 'nesne': flow, 'okunan': read_.get(flow), 'yazilan': written.get(flow), 'atlanan': 0})
    return rows


# ---------- Oracle Data Integrator / GoldenGate ----------
def odi_counts(cfg, factory, out):
    days, _, _ = window(cfg)
    schema = name(cfg, 'work_schema')
    _, rows = sql_source('oracle', cfg, factory, out).query(
        f'SELECT s.SESS_NO, s.SESS_NAME, s.SESS_BEG, st.STEP_NAME, l.NB_ROW, l.NB_INS, l.NB_UPD, l.NB_DEL, l.NB_ERR, l.STEP_STATUS '
        f'FROM {schema}.SNP_STEP_LOG l JOIN {schema}.SNP_SESSION s ON s.SESS_NO = l.SESS_NO '
        f'JOIN {schema}.SNP_SESS_STEP st ON st.SESS_NO = l.SESS_NO AND st.NNO = l.NNO WHERE s.SESS_BEG >= SYSDATE - {days}', 'step_log')
    return [{'calisma': r.get('sess_no'), 'pipeline': r.get('sess_name'), 'nesne': r.get('step_name'), 'okunan': r.get('nb_row'),
             'yazilan': (number(r.get('nb_ins')) or 0) + (number(r.get('nb_upd')) or 0), 'atlanan': r.get('nb_err') or 0, 'silinen': r.get('nb_del'),
             'durum_platform': r.get('step_status'), 'baslangic': r.get('sess_beg')} for r in rows]


STAT = re.compile(r'(?:Extracting|Replicating) from (\S+?)(?: to (\S+?))?:\s*(.*?)(?=(?:Extracting|Replicating) from|\Z)', re.S)
TOTAL = re.compile(r'\*\*\* Total statistics since .*?\*\*\*(.*?)(?:\*\*\*|\Z)', re.S)


def gg_stats(text):
    """STATS command reply text → {table: total operations} using the 'Total statistics' block of each table."""
    result = {}
    for src, tgt, body in STAT.findall(text):
        block = TOTAL.search(body)
        part = block.group(1) if block else body
        ops = re.search(r'Total operations\s+([\d.,]+)', part)
        if ops:
            result[(tgt or src).lower()] = number(ops.group(1).replace(',', ''))
            result.setdefault('_kaynak_' + src.lower(), result[(tgt or src).lower()])
    return result


def goldengate_counts(cfg, factory, out):
    client = basic(factory, required(cfg, 'server'), 'GG_USER', 'GG_PASSWORD')
    def stats(kind, process):
        r = client.json('POST', f'/services/v2/{kind}/{ident(process)}/command', {'command': 'STATS', 'arguments': 'TOTAL'})
        reply = (r.get('response') or {}).get('reply') or ''
        if not reply:
            raise NativeError(f'GoldenGate {process} STATS yanıtı boş.')
        return gg_stats(reply)
    extract = stats('extracts', name(cfg, 'extract'))
    replicat = stats('replicats', name(cfg, 'replicat'))
    rows = []
    mapping = {str(k).lower(): str(v).lower() for k, v in (cfg.get('table_map') or {}).items()}
    for key, value in extract.items():
        if not key.startswith('_kaynak_'):
            continue
        source = key[len('_kaynak_'):]
        target = mapping.get(source, source)
        written = replicat.get(target, replicat.get(source.split('.')[-1]))
        if written is None:
            written = next((v for k, v in replicat.items() if not k.startswith('_kaynak_') and k.split('.')[-1] == source.split('.')[-1]), None)
        rows.append({'calisma': 'STATS TOTAL', 'pipeline': f"{cfg['extract']} → {cfg['replicat']}", 'nesne': source, 'okunan': value, 'yazilan': written, 'atlanan': 0})
    return rows


# ---------- Meltano (Singer metrics) ----------
METRIC = re.compile(r'METRIC:\s*(\{.*\})')


def meltano_counts(cfg, factory, out):
    rows, produced, consumed = [], defaultdict(int), defaultdict(int)
    for path in names(cfg, 'log_files', limit=50, pattern=re.compile(r'[^\x00-\x1f]{1,300}')):
        for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
            m = METRIC.search(line)
            if not m:
                continue
            try:
                metric = json.loads(m.group(1))
            except ValueError:
                continue
            if metric.get('metric') != 'record_count':
                continue
            stream = (metric.get('tags') or {}).get('stream') or (metric.get('tags') or {}).get('endpoint') or '?'
            target = 'consumer=True' in line or 'cmd_type=loader' in line or re.search(r'\bname=target-', line)
            (consumed if target else produced)[stream] += int(number(metric.get('value')) or 0)
    for stream in sorted(set(produced) | set(consumed)):
        rows.append({'calisma': 'log', 'pipeline': 'meltano run', 'nesne': stream, 'okunan': produced.get(stream), 'yazilan': consumed.get(stream), 'atlanan': 0})
    return rows


PRODUCTS = {
    'adf': ('Azure Data Factory', adf_counts), 'ssis': ('SSIS', ssis_counts), 'fabric-data-factory': ('Fabric Data Factory', fabric_df_counts),
    'idmc': ('Informatica IDMC', idmc_counts), 'powercenter': ('Informatica PowerCenter', powercenter_counts), 'talend-cloud': ('Talend Cloud', talend_counts),
    'qlik-replicate': ('Qlik Replicate', replicate_counts), 'fivetran': ('Fivetran', fivetran_counts), 'airbyte': ('Airbyte', airbyte_counts),
    'sap-data-services': ('SAP Data Services', ds_counts), 'odi': ('Oracle Data Integrator', odi_counts), 'goldengate': ('Oracle GoldenGate', goldengate_counts),
    'meltano': ('Meltano', meltano_counts),
}
COLUMNS = ['pipeline', 'calisma', 'nesne', 'okunan', 'yazilan', 'atlanan', 'fark', 'durum', 'durum_platform', 'baslangic']


def row_reconcile(cfg, out, factory):
    key = cfg['operation'].rsplit('-row-reconcile', 1)[0]
    label, reader = PRODUCTS[key]
    rows = reader(cfg, factory, out)
    if not rows:
        raise NativeError('Seçilen dönemde sayaç içeren çalışma bulunamadı (kopyalama etkinliği, günlük düzeyi veya filtreyi kontrol edin).')
    result = count_reconcile([{k: plain(v) for k, v in r.items()} for r in rows])
    extra = sorted({k for r in result for k in r} - set(COLUMNS))
    write_csv(out, 'satir_mutabakati.csv', result, COLUMNS + extra)
    diffs = [r for r in result if r['durum'] == 'FARK']
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, 'satir': len(result), 'fark': len(diffs), 'sayac_yok': sum(1 for r in result if r['durum'] == 'sayaç yok'),
                                      'esit': sum(1 for r in result if r['durum'] == 'eşit'), 'en_buyuk_farklar': sorted(diffs, key=lambda r: -abs(number(r['fark']) or 0))[:10]})


# ---------- Mapping & ETL plan agent: object/column evidence ----------
def obj(name_, role, columns=(), **extra):
    return {'nesne': str(name_), 'rol': role, 'kolonlar': [{'kolon': str(c[0]), 'tip': str(c[1] or '')} for c in columns if c[0]], **extra}


def ev_adf(cfg, factory, out):
    client = bearer(factory, 'https://management.azure.com', 'AZURE_ACCESS_TOKEN')
    base, version = adf_base(cfg), '?api-version=2018-06-01'
    datasets = client.json('GET', base + '/datasets' + version).get('value', [])
    objects = []
    for d in datasets:
        props = d.get('properties') or {}
        cols = props.get('schema') or props.get('structure') or []
        objects.append(obj(d.get('name'), props.get('type', ''), [(c.get('name'), c.get('type')) for c in cols if isinstance(c, dict)],
                           baglanti=(props.get('linkedServiceName') or {}).get('referenceName')))
    pipes = client.json('GET', base + '/pipelines' + version).get('value', [])
    return objects, {'pipelinelar': [{'pipeline': p.get('name'), 'etkinlikler': [a.get('type') for a in (p.get('properties') or {}).get('activities', [])]} for p in pipes]}


def ev_fabric_df(cfg, factory, out):
    client = bearer(factory, 'https://api.fabric.microsoft.com', 'FABRIC_ACCESS_TOKEN')
    ws, item = required(cfg, 'workspace_id'), required(cfg, 'pipeline_id')
    raw, headers, status = client.request('POST', f'/v1/workspaces/{ident(ws)}/items/{ident(item)}/getDefinition', {}, with_status=True)
    if status == 202:
        operation = urlsplit(native.header(headers, 'Location')).path
        if not re.fullmatch(r'/v1/operations/[0-9a-fA-F-]{36}', operation):
            raise NativeError('Fabric işlem adresi beklenen biçimde değil.')
        for _ in range(60):
            state = client.json('GET', operation)
            if state.get('status') == 'Succeeded':
                raw, _ = client.request('GET', operation + '/result')
                break
            if state.get('status') == 'Failed':
                raise NativeError('Fabric pipeline tanımı alınamadı.')
            time.sleep(2)
        else:
            raise NativeError('Fabric tanım dışa aktarımı zaman aşımına uğradı.')
    definition = native.json_object(raw).get('definition') or {}
    part = next((p for p in definition.get('parts', []) if p.get('path') == 'pipeline-content.json'), None)
    if not part:
        raise NativeError('pipeline-content.json bulunamadı.')
    content = json.loads(base64.b64decode(part['payload']))
    objects = []
    for a in (content.get('properties') or {}).get('activities', []):
        tp = a.get('typeProperties') or {}
        for role in ('source', 'sink'):
            spec = tp.get(role) or {}
            ds = (spec.get('datasetSettings') or {})
            label_ = ((ds.get('typeProperties') or {}).get('table') or (ds.get('typeProperties') or {}).get('location', {}).get('fileName') or f"{a.get('name')}:{role}")
            cols = [(m.get(role if role == 'source' else 'sink', {}).get('name'), m.get(role if role == 'source' else 'sink', {}).get('type'))
                    for m in ((tp.get('translator') or {}).get('mappings') or [])]
            objects.append(obj(label_, 'kaynak' if role == 'source' else 'hedef', cols, tur=spec.get('type'), etkinlik=a.get('name')))
    return objects, {}


def ev_ssis(cfg, factory, out):
    objects = []
    for path in sorted(Path(required(cfg, 'packages_dir')).glob('*.dtsx')):
        root = ET.parse(path).getroot()
        for comp in root.iter():
            if comp.tag.rsplit('}', 1)[-1] != 'component':
                continue
            cls = comp.get('componentClassID', '')
            role = 'kaynak' if 'Source' in cls or 'Source' in (comp.get('description') or '') else 'hedef' if 'Destination' in cls or 'Destination' in (comp.get('description') or '') else ''
            if not role:
                continue
            table = next((p.text for p in comp.iter() if p.tag.rsplit('}', 1)[-1] == 'property' and p.get('name') in ('OpenRowset', 'SqlCommand', 'TableOrViewName') and p.text), comp.get('name'))
            cols = [(c.get('name'), c.get('dataType')) for c in comp.iter() if c.tag.rsplit('}', 1)[-1] == 'externalMetadataColumn']
            objects.append(obj(str(table).strip('[]').replace('].[', '.'), role, cols, paket=path.name, bilesen=comp.get('name')))
    if not objects:
        raise NativeError('packages_dir içinde kaynak/hedef bileşenli .dtsx bulunamadı.')
    return objects, {}


def ev_idmc(cfg, factory, out):
    client = idmc_session(cfg, factory)
    objects = []
    for side, role in (('source', 'kaynak'), ('target', 'hedef')):
        conn = required(cfg, f'{side}_connection_id')
        for o in names(cfg, f'{side}_objects', limit=50, pattern=re.compile(r'[^\x00-\x1f]{1,200}')):
            raw, _ = client.request('GET', f'api/v2/connection/{side}/{ident(conn)}/metadata?' + urlencode({'objectName': o}))
            fields = (json.loads(raw) if raw else {}).get('fieldList') or []
            objects.append(obj(o, role, [(f.get('name'), f.get('type')) for f in fields]))
    return objects, {}


def ev_powercenter(cfg, factory, out):
    kind = cfg.get('repository_type', 'oracle')
    src = sql_source(kind, cfg, factory, out)
    folders = names(cfg, 'folders', limit=50, pattern=re.compile(r'[^\x00-\x1f\']{1,80}'))
    objects = []
    for view, role, tcol, fcol in (('REP_SRC_TBL_FLDS', 'kaynak', 'TABLE_NAME', 'COLUMN_NAME'), ('REP_TARG_TBL_COLS', 'hedef', 'TABLE_NAME', 'COLUMN_NAME')):
        _, rows = src.query(f'SELECT {tcol} AS t, {fcol} AS c, DATA_TYPE AS d FROM {view} WHERE SUBJECT_AREA IN ({sql_in(folders)})', view)
        grouped = defaultdict(list)
        for r in rows:
            grouped[r['t']].append((r['c'], r.get('d')))
        objects += [obj(t, role, cols) for t, cols in grouped.items()]
    return objects, {}


def ev_talend(cfg, factory, out):
    objects = []
    for path in sorted(Path(required(cfg, 'jobs_dir')).rglob('*.item')):
        root = ET.parse(path).getroot()
        for node in root.iter():
            if node.tag.rsplit('}', 1)[-1] != 'node':
                continue
            comp = node.get('componentName', '')
            role = 'kaynak' if comp.endswith('Input') else 'hedef' if comp.endswith('Output') else ''
            if not role:
                continue
            label_ = next((p.get('value') for p in node.iter() if p.tag.rsplit('}', 1)[-1] == 'elementParameter' and p.get('name') in ('TABLE', 'FILENAME', 'UNIQUE_NAME')), comp)
            cols = [(c.get('name'), c.get('type')) for m in node.iter() if m.tag.rsplit('}', 1)[-1] == 'metadata' and m.get('connector') == 'FLOW'
                    for c in m if c.tag.rsplit('}', 1)[-1] == 'column']
            objects.append(obj(str(label_).strip('"'), role, cols, is_=path.stem, bilesen=comp))
    if not objects:
        raise NativeError('jobs_dir içinde Talend iş (.item) dosyası bulunamadı (projeyi dışa aktarın).')
    return objects, {}


def ev_replicate(cfg, factory, out):
    client = qem_session(cfg, factory)
    server, task = required(cfg, 'replicate_server'), required(cfg, 'task')
    raw, _ = client.request('GET', f'servers/{ident(server)}/tasks/{ident(task)}?action=export')
    spec = json.loads(raw) if raw else {}
    tasks = ((spec.get('cmd.replication_definition') or {}).get('tasks') or [{}])
    objects = []
    for t in tasks:
        for e in ((t.get('source') or {}).get('source_tables') or {}).get('explicit_included_tables', []):
            objects.append(obj(f"{e.get('owner')}.{e.get('name')}", 'kaynak'))
        for m in (t.get('manipulations') or []):
            objects.append(obj(f"{(m.get('table_manipulation') or {}).get('owner')}.{(m.get('table_manipulation') or {}).get('name')}", 'donusum'))
    return objects, {'gorev': task}


def ev_fivetran(cfg, factory, out):
    client = native.fivetran_client(factory)
    r = client.json('GET', '/v1/connections/' + ident(required(cfg, 'connection_id')) + '/schemas')
    objects = []
    for sname, s in ((r.get('data') or {}).get('schemas') or {}).items():
        for tname, t in (s.get('tables') or {}).items():
            cols = [(c, (spec or {}).get('hashed') and 'hashed' or '') for c, spec in (t.get('columns') or {}).items() if (spec or {}).get('enabled', True)]
            objects.append(obj(f"{s.get('name_in_destination', sname)}.{t.get('name_in_destination', tname)}", 'kaynak→hedef', cols, etkin=t.get('enabled'), sync_mode=t.get('sync_mode')))
    return objects, {}


def ev_airbyte(cfg, factory, out):
    client = bearer(factory, 'https://api.airbyte.com', 'AIRBYTE_ACCESS_TOKEN')
    raw, _ = client.request('GET', '/v1/streams?' + urlencode({'sourceId': required(cfg, 'source_id'), 'destinationId': required(cfg, 'destination_id')}))
    streams = json.loads(raw) if raw else []
    return [obj(s.get('streamName'), 'kaynak', [('.'.join(p) if isinstance(p, list) else p, '') for p in s.get('propertyFields', [])],
                senkron_modlari=s.get('syncModes'), varsayilan_imlec=s.get('defaultCursorField'), birincil_anahtar=s.get('sourceDefinedPrimaryKey'))
            for s in (streams if isinstance(streams, list) else [])], {}


def ev_ds(cfg, factory, out):
    src = sql_source(cfg.get('repository_type', 'mssql'), cfg, factory, out)
    datastores = names(cfg, 'datastores', limit=20)
    _, rows = src.query(f'SELECT DATASTORE, OWNER, TABLENAME, COLUMNNAME, DATATYPE FROM ALVW_COLUMNINFO WHERE DATASTORE IN ({sql_in(datastores)})', 'column_info')
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r.get('datastore'), f"{r.get('owner')}.{r.get('tablename')}")].append((r.get('columnname'), r.get('datatype')))
    return [obj(t, 'kaynak' if ds == cfg.get('source_datastore') else 'hedef' if ds == cfg.get('target_datastore') else 'bilinmiyor', cols, datastore=ds)
            for (ds, t), cols in grouped.items()], {}


def ev_odi(cfg, factory, out):
    schema = name(cfg, 'work_schema')
    models = names(cfg, 'models', limit=20)
    _, rows = sql_source('oracle', cfg, factory, out).query(
        f'SELECT m.MOD_NAME, t.TABLE_NAME, c.COL_NAME, c.SOURCE_DT, c.LONGC FROM {schema}.SNP_MODEL m JOIN {schema}.SNP_TABLE t ON t.I_MOD = m.I_MOD '
        f'JOIN {schema}.SNP_COL c ON c.I_TABLE = t.I_TABLE WHERE m.MOD_NAME IN ({sql_in(models)})', 'model_columns')
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r.get('mod_name'), r.get('table_name'))].append((r.get('col_name'), f"{r.get('source_dt')}({r.get('longc')})"))
    return [obj(t, 'kaynak' if m == cfg.get('source_model') else 'hedef' if m == cfg.get('target_model') else 'bilinmiyor', cols, model=m) for (m, t), cols in grouped.items()], {}


MAP_RE = re.compile(r'^\s*(TABLE|MAP)\s+([^,;\s]+)\s*(?:,\s*TARGET\s+([^,;\s]+))?', re.I | re.M)


def ev_goldengate(cfg, factory, out):
    client = basic(factory, required(cfg, 'server'), 'GG_USER', 'GG_PASSWORD')
    objects = []
    for process in (name(cfg, 'extract'), name(cfg, 'replicat')):
        r = client.json('GET', f'/services/v2/config/files/{ident(process.lower())}.prm')
        lines = ((r.get('response') or {}).get('lines') or [])
        for kind, src, tgt in MAP_RE.findall('\n'.join(lines)):
            objects.append(obj(src, 'kaynak', esleme=tgt or '', surec=process))
            if tgt:
                objects.append(obj(tgt, 'hedef', surec=process))
    return objects, {}


def ev_meltano(cfg, factory, out):
    catalog = json.loads(Path(required(cfg, 'catalog_file')).read_text(encoding='utf-8'))
    objects = []
    for s in catalog.get('streams', []):
        props = ((s.get('schema') or {}).get('properties') or {})
        objects.append(obj(s.get('tap_stream_id') or s.get('stream'), 'kaynak', [(k, ','.join(v.get('type')) if isinstance(v.get('type'), list) else v.get('type')) for k, v in props.items()],
                           anahtar=next((m.get('metadata', {}).get('table-key-properties') for m in s.get('metadata', []) if not m.get('breadcrumb')), None)))
    return objects, {'meltano_yml': Path(cfg.get('meltano_file', 'meltano.yml')).read_text(encoding='utf-8')[:4000] if Path(cfg.get('meltano_file', 'meltano.yml')).exists() else ''}


EVIDENCE = {'adf': ev_adf, 'ssis': ev_ssis, 'fabric-data-factory': ev_fabric_df, 'idmc': ev_idmc, 'powercenter': ev_powercenter, 'talend-cloud': ev_talend,
            'qlik-replicate': ev_replicate, 'fivetran': ev_fivetran, 'airbyte': ev_airbyte, 'sap-data-services': ev_ds, 'odi': ev_odi, 'goldengate': ev_goldengate,
            'meltano': ev_meltano}
METHODS = {'tam_yukleme', 'artimli_watermark', 'cdc', 'birlestir_upsert', 'scd2', 'anlik_goruntu'}
SCHEMA = ('{"akislar":[{"kaynak":"kanıttaki nesne","hedef":"kanıttaki nesne veya yeni hedef","yeni_hedef":false,"yontem":"tam_yukleme|artimli_watermark|cdc|birlestir_upsert|scd2|anlik_goruntu",'
          '"anahtar":["kaynak kolonu"],"watermark":"kaynak kolonu","kolon_eslemeleri":[{"kaynak":"kolon","hedef":"kolon","donusum":"..."}],"siklik":"gunluk/saatlik",'
          '"hata_yonetimi":"..."}],"siralama":["önce boyutlar ..."],"riskler":["..."],"sorular":["..."]}')


def etl_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-etl-plan', 1)[0]
    label = PRODUCTS[key][0]
    objects, extra = EVIDENCE[key](cfg, factory, out)
    if not objects:
        raise NativeError('Platformdan kaynak/hedef nesne okunamadı.')
    by_name = {o['nesne'].lower(): o for o in objects}

    def validate(result):
        for i, f in enumerate(need_list(result, 'akislar', 'akışlar', maximum=200), 1):
            source = need_text(f, 'kaynak', f'akış {i}').lower()
            if source not in by_name:
                raise ValueError(f'akış {i}: kanıtta olmayan kaynak nesne: {f["kaynak"]}')
            target = need_text(f, 'hedef', f'akış {i}').lower()
            if target not in by_name and not f.get('yeni_hedef'):
                raise ValueError(f'akış {i}: hedef kanıtta yok; yeni ise "yeni_hedef": true yazın: {f["hedef"]}')
            need_text(f, 'yontem', f'akış {i}', choices=METHODS)
            cols = {c['kolon'].lower() for c in by_name[source]['kolonlar']}
            if cols:
                used = list(f.get('anahtar') or []) + ([f['watermark']] if f.get('watermark') else []) + [m.get('kaynak') for m in f.get('kolon_eslemeleri') or [] if isinstance(m, dict) and m.get('kaynak')]
                bad = [c for c in used if str(c).lower() not in cols]
                if bad:
                    raise ValueError(f'akış {i}: {f["kaynak"]} içinde olmayan kolon: ' + ', '.join(map(str, bad)))
            if f['yontem'] in ('artimli_watermark',) and not f.get('watermark'):
                raise ValueError(f'akış {i}: artimli_watermark için watermark kolonu gerekli.')
            if f['yontem'] in ('birlestir_upsert', 'scd2', 'cdc') and not f.get('anahtar'):
                raise ValueError(f'akış {i}: {f["yontem"]} için anahtar gerekli.')
        return result

    def render(result):
        flows = [{**f, 'anahtar': ', '.join(f.get('anahtar') or []), 'kolon_sayisi': len(f.get('kolon_eslemeleri') or [])} for f in result['akislar']]
        maps = [{'kaynak_nesne': f['kaynak'], 'hedef_nesne': f['hedef'], **m} for f in result['akislar'] for m in f.get('kolon_eslemeleri') or [] if isinstance(m, dict)]
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['kaynak_nesne', 'hedef_nesne', 'kaynak', 'hedef', 'donusum'], extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(maps)
        return {'etl_plani.json': plan_json(result), 'kolon_eslemeleri.csv': buf.getvalue(),
                'etl_plani.md': rows_md(f'Kaynak-hedef eşleme ve ETL planı — {label}',
                                        [('Akışlar', flows, ['kaynak', 'hedef', 'yeni_hedef', 'yontem', 'anahtar', 'watermark', 'siklik', 'kolon_sayisi', 'hata_yonetimi']),
                                         ('Sıralama, riskler ve sorular', [{'madde': x} for x in result.get('siralama', []) + result.get('riskler', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Kaynak-hedef eşleme ve ETL planı', evidence={'nesneler': objects[:600], **extra}, schema=SCHEMA,
                      validate=validate, render=render,
                      rules='Artımlı yöntem öneriyorsan watermark kolonunu, upsert/SCD2/CDC için anahtarı kanıttaki kolonlardan seç. Silme ve geç gelen veriyi riskler bölümünde ele al.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-row-reconcile'] = row_reconcile
    HANDLERS[_key + '-etl-plan'] = etl_plan
