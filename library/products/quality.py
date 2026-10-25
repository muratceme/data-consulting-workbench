"""Data quality: rule check results from each quality product (code) and an evidence-bound root-cause analysis of failing checks (agent).

GX Core and Soda Core run their own engines locally on the user's project; Soda Cloud, Talend (Qlik Trust Score),
Ataccama ONE and Informatica Cloud Data Profiling are read through their APIs; Deequ metrics are read from its
metrics repository. No rule, check or profile definition is created or changed."""
import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, driver, ident, journal, name, names, need_list, need_text, number, opt_float, opt_int,
                           parse_time, plan_json, receipt, required, rows_md, secret, write_artifact, write_csv)

PASS, FAIL, WARN, ERROR = 'geçti', 'kaldı', 'uyarı', 'hata'
COLUMNS = ['kontrol', 'nesne', 'kolon', 'sonuc', 'deger', 'esik', 'ayrinti']


def result(check, obj, column, outcome, value='', threshold='', detail=''):
    return {'kontrol': str(check), 'nesne': str(obj or ''), 'kolon': str(column or ''), 'sonuc': outcome, 'deger': value if value is not None else '', 'esik': threshold,
            'ayrinti': str(detail or '')[:300]}


# ---------- Great Expectations (GX Core) ----------
def gx_results(cfg, factory, out):
    validation_dir = cfg.get('validation_dir')
    payloads = []
    if validation_dir:
        for path in sorted(Path(validation_dir).rglob('*.json'))[-opt_int(cfg, 'max_files', 20, 1, 500):]:
            payloads.append(json.loads(path.read_text(encoding='utf-8')))
    else:
        gx = driver('great_expectations')
        context = gx.get_context(mode='file', project_root_dir=required(cfg, 'project_dir'))
        checkpoint = context.checkpoints.get(required(cfg, 'checkpoint'))
        run = checkpoint.run()
        payload = run.to_json_dict() if hasattr(run, 'to_json_dict') else run
        payloads = [payload]
    rows = []
    for p in payloads:
        runs = p.get('run_results') or {}
        validations = [v.get('validation_result', v) for v in runs.values()] if isinstance(runs, dict) and runs else [p]
        for v in validations:
            asset = ((v.get('meta') or {}).get('active_batch_definition') or {}).get('data_asset_name') or (v.get('meta') or {}).get('batch_spec', {}).get('data_asset_name') or v.get('suite_name', '')
            for r in v.get('results', []):
                conf = r.get('expectation_config') or {}
                kind = conf.get('type') or conf.get('expectation_type') or 'expectation'
                kwargs = conf.get('kwargs') or {}
                res = r.get('result') or {}
                outcome = ERROR if (r.get('exception_info') or {}).get('raised_exception') else PASS if r.get('success') else FAIL
                value = res.get('unexpected_percent') if res.get('unexpected_percent') is not None else res.get('observed_value')
                rows.append(result(kind, asset, kwargs.get('column') or ','.join(kwargs.get('column_list', [])), outcome, value if not isinstance(value, (list, dict)) else '',
                                   kwargs.get('mostly', ''), f"unexpected_count={res.get('unexpected_count')}, element_count={res.get('element_count')}"))
    return rows, {'kaynak': 'GX doğrulama sonucu'}


# ---------- Soda Core ----------
def soda_core_results(cfg, factory, out):
    soda = driver('soda.scan')
    scan = soda.Scan()
    scan.set_data_source_name(required(cfg, 'data_source'))
    scan.add_configuration_yaml_file(file_path=required(cfg, 'configuration_file'))
    for f in names(cfg, 'checks_files', limit=50, pattern=re.compile(r'[^\x00-\x1f]{1,300}')):
        scan.add_sodacl_yaml_file(f)
    scan.set_scan_definition_name('datawork')
    scan.execute()
    results = scan.get_scan_results()
    outcome = {'pass': PASS, 'fail': FAIL, 'warn': WARN}
    rows = [result(c.get('name') or c.get('definition', '')[:80], c.get('table'), c.get('column'), outcome.get(c.get('outcome'), ERROR),
                   (c.get('diagnostics') or {}).get('value'), '', (c.get('diagnostics') or {}).get('fail') or '') for c in results.get('checks', [])]
    return rows, {'veri_kaynagi': cfg['data_source'], 'hata_logu': [l.get('message') for l in results.get('logs', []) if l.get('level') == 'error'][:10]}


# ---------- Soda Cloud ----------
def soda_cloud_results(cfg, factory, out):
    client = factory(required(cfg, 'server'), {'Authorization': native.basic_auth(secret('SODA_API_KEY_ID'), secret('SODA_API_KEY_SECRET'))})
    rows = []
    for page in range(0, 100):
        q = {'size': 1000, 'page': page, **({'datasetId': cfg['dataset_id']} if cfg.get('dataset_id') else {})}
        r = client.json('GET', '/api/v1/checks?' + urlencode(q))
        for c in r.get('content', []):
            status = str(c.get('evaluationStatus') or c.get('lastCheckResultValue', {}).get('outcome') if isinstance(c.get('lastCheckResultValue'), dict) else c.get('evaluationStatus') or '').lower()
            ds = (c.get('datasets') or [{}])[0]
            value = (c.get('lastCheckResultValue') or {}).get('value') if isinstance(c.get('lastCheckResultValue'), dict) else c.get('lastCheckResultValue')
            rows.append(result(c.get('name'), ds.get('name'), c.get('column'), {'pass': PASS, 'fail': FAIL, 'warn': WARN}.get(status, 'bilinmiyor'), value, '',
                               f"son çalışma: {c.get('lastCheckRunTime', '')}"))
        if r.get('last', True):
            break
    return rows, {'kaynak': 'Soda Cloud /api/v1/checks'}


# ---------- Deequ (metrics repository) ----------
def deequ_results(cfg, factory, out):
    entries = json.loads(Path(required(cfg, 'metrics_repository_file')).read_text(encoding='utf-8'))
    if not isinstance(entries, list) or not entries:
        raise NativeError('metrics_repository_file Deequ FileSystemMetricsRepository JSON listesi olmalı.')
    latest = max(entries, key=lambda e: (e.get('resultKey') or {}).get('dataSetDate', 0))
    metrics = {}
    for m in (latest.get('analyzerContext') or {}).get('metricMap', []):
        metric = m.get('metric') or {}
        metrics[(str(metric.get('name')).lower(), str(metric.get('instance')).lower())] = number(metric.get('value'))
    rules = cfg.get('rules') or []
    if not isinstance(rules, list) or not rules:
        raise NativeError('rules [{"metrik":"Completeness","kolon":"email","min":0.99}] listesi gerekli.')
    rows = []
    for r in rules:
        key = (str(r.get('metrik', '')).lower(), str(r.get('kolon', '*')).lower())
        value = metrics.get(key)
        if value is None:
            rows.append(result(r.get('metrik'), latest.get('resultKey', {}).get('tags', {}), r.get('kolon'), ERROR, '', '', 'metrik depoda yok'))
            continue
        ok = (r.get('min') is None or value >= r['min']) and (r.get('max') is None or value <= r['max'])
        rows.append(result(r.get('metrik'), json.dumps((latest.get('resultKey') or {}).get('tags', {}), ensure_ascii=False), r.get('kolon'), PASS if ok else FAIL, value,
                           f"[{r.get('min', '')}, {r.get('max', '')}]"))
    history = sorted(entries, key=lambda e: (e.get('resultKey') or {}).get('dataSetDate', 0))[-30:]
    trend = [{'tarih': (e.get('resultKey') or {}).get('dataSetDate'), **{f"{m['metric']['name']}:{m['metric']['instance']}": m['metric'].get('value')
                                                                       for m in (e.get('analyzerContext') or {}).get('metricMap', []) if m.get('metric')}} for e in history]
    return rows, {'son_calisma': (latest.get('resultKey') or {}).get('dataSetDate'), 'gecmis': trend}


# ---------- Informatica Cloud Data Quality (profiling results) ----------
def idmc_v3(cfg, factory):
    login = factory(required(cfg, 'login_url'), {})
    r = login.json('POST', '/saas/public/core/v3/login', {'username': secret('IDMC_USER'), 'password': secret('IDMC_PASSWORD')})
    session = required(r.get('userInfo') or {}, 'sessionId')
    return factory(required(cfg, 'profiling_url'), {'INFA-SESSION-ID': session})


def informatica_results(cfg, factory, out):
    client = idmc_v3(cfg, factory)
    profile = name(cfg, 'profile_id', re.compile(r'[A-Za-z0-9_-]{1,64}'))
    raw, headers = client.request('GET', f"metric-store/api/v1/Profiles('{profile}')/Export", accept='application/octet-stream')
    write_artifact(out, 'profil_disa_aktarim' + ('.json' if raw.lstrip().startswith(b'{') else '.csv'), raw)
    rows = []
    rules = cfg.get('rules') or []
    try:
        table = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    except UnicodeDecodeError:
        table = []
    by_col = {str(r.get('Column Name') or r.get('Column') or r.get('Name') or '').lower(): r for r in table}
    for rule in rules:
        row = by_col.get(str(rule.get('kolon', '')).lower())
        metric = rule.get('metrik', '')
        value = number(str((row or {}).get(metric, '')).rstrip('%')) if row else None
        if value is None:
            rows.append(result(metric, profile, rule.get('kolon'), ERROR, '', '', 'kolon/metrik dışa aktarımda bulunamadı'))
            continue
        ok = (rule.get('min') is None or value >= rule['min']) and (rule.get('max') is None or value <= rule['max'])
        rows.append(result(metric, profile, rule.get('kolon'), PASS if ok else FAIL, value, f"[{rule.get('min', '')}, {rule.get('max', '')}]"))
    return rows, {'profil': profile, 'kolon': len(table), 'dosya_kolonlari': list(table[0].keys()) if table else []}


# ---------- Talend Data Quality (Qlik Trust Score) ----------
def talend_results(cfg, factory, out):
    client = native.qlik_client(cfg, factory)
    ids = names(cfg, 'dataset_ids', limit=100, pattern=re.compile(r'[A-Za-z0-9_-]{1,64}'))
    minimum = opt_float(cfg, 'min_score', 80, 0, 100)
    data = client.json('POST', '/api/data-governance/trust-scores/results/data-sets/actions/filter', {'datasetIds': ids, 'recomputeUsage': False}).get('data', [])
    rows = []
    for d in data:
        rows.append(result('TRUST_SCORE', d.get('datasetId'), '', PASS if (number(d.get('score')) or 0) >= minimum else FAIL, d.get('score'), minimum,
                           f"önceki: {d.get('previousScore')}, güncelleme: {d.get('updatedAt')}"))
        for a in d.get('axes', []):
            if a.get('enabled') and a.get('applicable', True) and a.get('score') is not None:
                rows.append(result(a.get('id'), d.get('datasetId'), '', PASS if number(a['score']) >= minimum else FAIL, a['score'], minimum,
                                   ', '.join(f"{m.get('id')}={m.get('score')}" for m in a.get('metrics', []))))
    missing = sorted(set(ids) - {d.get('datasetId') for d in data})
    rows += [result('TRUST_SCORE', m, '', ERROR, '', minimum, 'Trust Score hesaplanmamış') for m in missing]
    return rows, {'veri_kumesi': len(ids)}


# ---------- Ataccama ONE ----------
ATACCAMA_Q = ('query($p:GID!,$i:GID!,$c:[GID!]){monitoringProject(gid:$p){publishedVersion{dqCheckResultOverTime(selector:{limit:1},catalogItemId:$i,checkIds:$c)'
              '{processingId startedAt results{dqCheckId results{id count name}}}}}}')


def ataccama_results(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'ATACCAMA_TOKEN')
    project, item = required(cfg, 'monitoring_project_gid'), required(cfg, 'catalog_item_gid')
    checks = names(cfg, 'check_gids', limit=200, pattern=re.compile(r'[A-Za-z0-9_-]{1,64}'))
    max_invalid = opt_float(cfg, 'max_invalid_ratio', 0.0, 0, 1)
    data = native.graphql(client, '/graphql', ATACCAMA_Q, {'p': project, 'i': item, 'c': checks})
    over = ((((data.get('monitoringProject') or {}).get('publishedVersion') or {}).get('dqCheckResultOverTime')) or [])
    rows = []
    for run in over:
        for chk in run.get('results') or []:
            counts = {str(x.get('name', '')).lower(): number(x.get('count')) or 0 for x in chk.get('results') or []}
            bad = sum(v for k, v in counts.items() if any(w in k for w in ('invalid', 'fail', 'error')))
            total = sum(counts.values())
            ratio = bad / total if total else None
            rows.append(result(chk.get('dqCheckId'), item, '', ERROR if ratio is None else PASS if ratio <= max_invalid else FAIL, round(ratio, 6) if ratio is not None else '',
                               max_invalid, f"işlem {run.get('processingId')} {run.get('startedAt')}: " + ', '.join(f'{k}={v:g}' for k, v in counts.items())))
    return rows, {'proje': project, 'katalog_ogesi': item}


PRODUCTS = {'gx': ('Great Expectations (GX Core)', gx_results), 'soda-core': ('Soda Core', soda_core_results), 'soda-cloud': ('Soda Cloud', soda_cloud_results),
            'deequ': ('Deequ', deequ_results), 'informatica-cdq': ('Informatica Cloud Data Quality', informatica_results),
            'talend-dq': ('Talend Data Quality (Qlik Trust Score)', talend_results), 'ataccama': ('Ataccama ONE', ataccama_results)}


def rule_check(cfg, out, factory):
    key = cfg['operation'].rsplit('-rule-check', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    if not rows:
        raise NativeError('Platform kontrol sonucu döndürmedi.')
    write_csv(out, 'kontrol_sonuclari.csv', rows, COLUMNS)
    counts = defaultdict(int)
    for r in rows:
        counts[r['sonuc']] += 1
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, 'kontrol': len(rows), **dict(counts), 'kaynak': info if key != 'deequ' else {'son_calisma': info['son_calisma']},
                                      'basarili': counts[FAIL] == 0 and counts[ERROR] == 0})


CATEGORIES = {'kaynak_sistem', 'donusum_mantigi', 'zamanlama_gecikme', 'sema_degisikligi', 'kural_hatali', 'referans_veri', 'tekrarli_yukleme', 'bilinmiyor'}
SCHEMA = ('{"bulgular":[{"kontrol":"kanıttaki kontrol adı","nesne":"...","kategori":"kaynak_sistem|donusum_mantigi|zamanlama_gecikme|sema_degisikligi|kural_hatali|referans_veri|tekrarli_yukleme|bilinmiyor",'
          '"kok_neden":"...","kanit":"değer/eşik/eğilim","duzeltme":"...","sahip":"rol (ör. kaynak sistem sahibi)","oncelik":"yuksek|orta|dusuk"}],'
          '"kural_onerileri":[{"kontrol":"...","oneri":"eşik/kural değişikliği"}],"sorular":["..."]}')


def root_cause(cfg, out, factory):
    key = cfg['operation'].rsplit('-quality-root-cause', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    failing = [r for r in rows if r['sonuc'] in (FAIL, ERROR, WARN)]
    if not failing:
        raise NativeError('Başarısız kontrol yok; kök neden analizi gerekmiyor.')
    allowed = {r['kontrol'].lower() for r in rows}
    evidence = {'basarisiz_kontroller': failing[:300], 'gecen_kontrol_sayisi': sum(1 for r in rows if r['sonuc'] == PASS), 'ek': info}

    def validate(obj):
        for i, b in enumerate(need_list(obj, 'bulgular', 'bulgular', maximum=100), 1):
            check = need_text(b, 'kontrol', f'bulgu {i}')
            if check.lower() not in allowed:
                raise ValueError(f'bulgu {i}: kanıtta olmayan kontrol: {check}')
            need_text(b, 'kategori', f'bulgu {i}', choices=CATEGORIES)
            need_text(b, 'kok_neden', f'bulgu {i}')
            need_text(b, 'oncelik', f'bulgu {i}', choices={'yuksek', 'orta', 'dusuk'})
        for r in obj.get('kural_onerileri') or []:
            if not isinstance(r, dict) or str(r.get('kontrol', '')).lower() not in allowed:
                raise ValueError('kural önerisi kanıtta olmayan kontrolü gösteriyor.')
        return obj

    def render(result_):
        buf = io.StringIO()
        cols = ['oncelik', 'kontrol', 'nesne', 'kategori', 'kok_neden', 'kanit', 'duzeltme', 'sahip']
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(result_['bulgular'])
        return {'kok_neden.json': plan_json(result_), 'kok_neden.csv': buf.getvalue(),
                'kok_neden.md': rows_md('Kalite sorunu kök neden analizi — ' + label,
                                        [('Bulgular', result_['bulgular'], cols), ('Kural önerileri', result_.get('kural_onerileri') or [], ['kontrol', 'oneri']),
                                         ('Sorular', [{'soru': q} for q in result_.get('sorular', [])], ['soru'])])}
    return agent_task(cfg, out, product=label, title='Kalite sorunu kök neden analizi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Aynı nesnede birlikte kalan kontrolleri tek kök nedene bağlamaya çalış; eğilim verisi varsa değişimin başladığı çalışmayı kanıt olarak göster. '
                            'Eşiği gevşetmeyi ancak kuralın iş tanımıyla çeliştiğini gösterebiliyorsan öner.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-rule-check'] = rule_check
    HANDLERS[_key + '-quality-root-cause'] = root_cause
