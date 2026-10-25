"""Orchestration & DataOps: pipeline metric report from the orchestrator's run history (code) and an evidence-bound failure
root-cause analysis over failed runs, failed tasks/steps and log tails (agent). Airflow's metric report is the existing
airflow-history package; this module adds Airflow's agent and both tasks for the other orchestrators."""
import datetime as dt
import json
import re
from collections import defaultdict
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, basic, bearer, ident, journal, metrics_task, name, need_list, need_text, number, opt_int, parse_time,
                           plan_json, receipt, required, rows_md, secret, names)

OK, FAIL = 'başarılı', 'başarısız'


def since(days):
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


def tail(text, lines=40):
    return '\n'.join(str(text or '').splitlines()[-lines:])[-6000:]


# ---------- Airflow 3 API v2 (Airflow and Astro deployments) ----------
def airflow_client(cfg, factory, env='AIRFLOW_ACCESS_TOKEN'):
    return factory(required(cfg, 'server' if 'server' in cfg else 'deployment_url'), {'Authorization': 'Bearer ' + secret(env)})


def airflow_pages(client, path, key, limit=5000):
    items = []
    for offset in range(0, limit, 100):
        r = client.json('GET', path + ('&' if '?' in path else '?') + f'limit=100&offset={offset}')
        batch = [x for x in r.get(key, []) if isinstance(x, dict)]
        items += batch
        if not batch or offset + 100 >= int(r.get('total_entries') or 0):
            return items
    return items


def airflow_runs(cfg, factory, out, env='AIRFLOW_ACCESS_TOKEN'):
    days = opt_int(cfg, 'days', 7, 1, 90)
    client = airflow_client(cfg, factory, env)
    stamp = ident(since(days).strftime('%Y-%m-%dT%H:%M:%SZ'))
    runs = airflow_pages(client, f'api/v2/dags/~/dagRuns?run_after_gte={stamp}', 'dag_runs')
    state = {'success': OK, 'failed': FAIL}
    return [{'pipeline': r.get('dag_id'), 'calisma_id': r.get('dag_run_id'), 'durum': state.get(r.get('state'), 'diğer'), 'ham_durum': r.get('state'),
             'baslangic': r.get('start_date'), 'bitis': r.get('end_date'), 'sure_sn': ''} for r in runs], days, client


def airflow_failures(cfg, factory, out, env='AIRFLOW_ACCESS_TOKEN'):
    runs, days, client = airflow_runs(cfg, factory, out, env)
    stamp = ident(since(days).strftime('%Y-%m-%dT%H:%M:%SZ'))
    tasks = airflow_pages(client, f'api/v2/dags/~/dagRuns/~/taskInstances?state=failed&run_after_gte={stamp}', 'task_instances', 1000)
    details = []
    for t in tasks[:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        path = (f"api/v2/dags/{ident(t.get('dag_id'))}/dagRuns/{ident(t.get('dag_run_id'))}/taskInstances/{ident(t.get('task_id'))}/logs/{int(t.get('try_number') or 1)}"
                '?full_content=false')
        raw, _ = client.request('GET', path)
        try:
            body = json.loads(raw)
            content = body.get('content')
            text = '\n'.join(str(e.get('event', e)) if isinstance(e, dict) else str(e) for e in content) if isinstance(content, list) else str(content)
        except ValueError:
            text = raw.decode('utf-8', 'replace')
        details.append({'calisma_id': t.get('dag_run_id'), 'pipeline': t.get('dag_id'), 'adim': t.get('task_id'), 'deneme': t.get('try_number'),
                        'operator': t.get('operator'), 'log_sonu': tail(text)})
    return runs, details


# ---------- Dagster (GraphQL) ----------
DAGSTER_RUNS = ('query($f:RunsFilter,$c:String,$l:Int){runsOrError(filter:$f,cursor:$c,limit:$l){__typename ... on Runs{results{runId jobName status startTime endTime}}}}')
DAGSTER_LOGS = ('query($r:ID!){logsForRun(runId:$r){__typename ... on EventConnection{events{__typename ... on MessageEvent{message level} '
                '... on ExecutionStepFailureEvent{stepKey error{message className}}}}}}')


def dagster_client(cfg, factory):
    import os
    headers = {'Dagster-Cloud-Api-Token': os.environ['DAGSTER_CLOUD_API_TOKEN']} if os.environ.get('DAGSTER_CLOUD_API_TOKEN') else {}
    return factory(required(cfg, 'server'), headers)


def dagster_runs(cfg, factory, out, statuses=None):
    client = dagster_client(cfg, factory)
    days = opt_int(cfg, 'days', 7, 1, 90)
    filt = {'createdAfter': since(days).timestamp(), **({'statuses': statuses} if statuses else {})}
    runs, cursor = [], None
    for _ in range(50):
        data = native.graphql(client, 'graphql', DAGSTER_RUNS, {'f': filt, 'c': cursor, 'l': 200})
        res = (data.get('runsOrError') or {}).get('results') or []
        runs += res
        if len(res) < 200:
            break
        cursor = res[-1]['runId']
    state = {'SUCCESS': OK, 'FAILURE': FAIL}
    return [{'pipeline': r.get('jobName'), 'calisma_id': r.get('runId'), 'durum': state.get(r.get('status'), 'diğer'), 'ham_durum': r.get('status'),
             'baslangic': r.get('startTime'), 'bitis': r.get('endTime'), 'sure_sn': (r['endTime'] - r['startTime']) if r.get('startTime') and r.get('endTime') else ''}
            for r in runs], days, client


def dagster_failures(cfg, factory, out):
    runs, _, client = dagster_runs(cfg, factory, out)
    details = []
    for r in [x for x in runs if x['durum'] == FAIL][:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        events = ((native.graphql(client, 'graphql', DAGSTER_LOGS, {'r': r['calisma_id']}).get('logsForRun') or {}).get('events') or [])
        failures = [e for e in events if e.get('__typename') == 'ExecutionStepFailureEvent']
        errors = [e.get('message') for e in events if e.get('level') in ('ERROR', 'CRITICAL')]
        for f in failures or [{}]:
            details.append({'calisma_id': r['calisma_id'], 'pipeline': r['pipeline'], 'adim': f.get('stepKey', ''), 'hata_tipi': (f.get('error') or {}).get('className', ''),
                            'log_sonu': tail('\n'.join([(f.get('error') or {}).get('message', '')] + errors[-10:]))})
    return runs, details


# ---------- Prefect ----------
def prefect_client(cfg, factory):
    return bearer(factory, required(cfg, 'api_url'), 'PREFECT_API_KEY')


def prefect_runs(cfg, factory, out):
    client = prefect_client(cfg, factory)
    days = opt_int(cfg, 'days', 7, 1, 90)
    runs = []
    for offset in range(0, 5000, 200):
        raw, _ = client.request('POST', 'flow_runs/filter', {'flow_runs': {'start_time': {'after_': since(days).isoformat()}}, 'limit': 200, 'offset': offset, 'sort': 'START_TIME_DESC'})
        batch = json.loads(raw) if raw else []
        runs += batch
        if len(batch) < 200:
            break
    flow_ids = sorted({r.get('flow_id') for r in runs if r.get('flow_id')})
    flows = {}
    if flow_ids:
        raw, _ = client.request('POST', 'flows/filter', {'flows': {'id': {'any_': flow_ids}}, 'limit': 200})
        flows = {f['id']: f.get('name') for f in (json.loads(raw) if raw else [])}
    state = {'COMPLETED': OK, 'FAILED': FAIL, 'CRASHED': FAIL}
    return [{'pipeline': flows.get(r.get('flow_id'), r.get('flow_id')), 'calisma_id': r.get('id'), 'durum': state.get(r.get('state_type'), 'diğer'), 'ham_durum': r.get('state_type'),
             'baslangic': r.get('start_time'), 'bitis': r.get('end_time'), 'sure_sn': r.get('total_run_time') or ''} for r in runs], days, client


def prefect_failures(cfg, factory, out):
    runs, _, client = prefect_runs(cfg, factory, out)
    details = []
    for r in [x for x in runs if x['durum'] == FAIL][:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        raw, _ = client.request('POST', 'logs/filter', {'logs': {'flow_run_id': {'any_': [r['calisma_id']]}, 'level': {'ge_': 40}}, 'limit': 50, 'sort': 'TIMESTAMP_ASC'})
        logs = json.loads(raw) if raw else []
        raw, _ = client.request('POST', 'task_runs/filter', {'flow_runs': {'id': {'any_': [r['calisma_id']]}}, 'task_runs': {'state': {'type': {'any_': ['FAILED', 'CRASHED']}}}, 'limit': 20})
        tasks = json.loads(raw) if raw else []
        details.append({'calisma_id': r['calisma_id'], 'pipeline': r['pipeline'], 'adim': ', '.join(t.get('name', '') for t in tasks),
                        'log_sonu': tail('\n'.join(str(l.get('message')) for l in logs))})
    return runs, details


# ---------- Kestra ----------
def kestra_client(cfg, factory):
    import os
    if os.environ.get('KESTRA_TOKEN'):
        return bearer(factory, required(cfg, 'server'), 'KESTRA_TOKEN')
    return basic(factory, required(cfg, 'server'), 'KESTRA_USER', 'KESTRA_PASSWORD')


def kestra_prefix(cfg):
    tenant = cfg.get('tenant') or ''
    if tenant and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', tenant):
        raise NativeError('tenant geçersiz.')
    return 'api/v1/' + (tenant + '/' if tenant else '')


def kestra_runs(cfg, factory, out):
    client = kestra_client(cfg, factory)
    days = opt_int(cfg, 'days', 7, 1, 90)
    runs = []
    for page in range(1, 51):
        q = {'startDate': since(days).isoformat(), 'size': 100, 'page': page, **({'namespace': cfg['namespace']} if cfg.get('namespace') else {})}
        r = client.json('GET', kestra_prefix(cfg) + 'executions/search?' + urlencode(q))
        batch = r.get('results', [])
        runs += batch
        if len(batch) < 100:
            break
    state = {'SUCCESS': OK, 'FAILED': FAIL, 'KILLED': FAIL}
    return [{'pipeline': f"{e.get('namespace')}.{e.get('flowId')}", 'calisma_id': e.get('id'), 'durum': state.get((e.get('state') or {}).get('current'), 'diğer'),
             'ham_durum': (e.get('state') or {}).get('current'), 'baslangic': (e.get('state') or {}).get('startDate'), 'bitis': (e.get('state') or {}).get('endDate'), 'sure_sn': ''}
            for e in runs], days, client


def kestra_failures(cfg, factory, out):
    runs, _, client = kestra_runs(cfg, factory, out)
    details = []
    for r in [x for x in runs if x['durum'] == FAIL][:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        raw, _ = client.request('GET', kestra_prefix(cfg) + f"logs/{ident(r['calisma_id'])}?minLevel=ERROR")
        logs = json.loads(raw) if raw else []
        details.append({'calisma_id': r['calisma_id'], 'pipeline': r['pipeline'], 'adim': ', '.join(sorted({str(l.get('taskId')) for l in logs if l.get('taskId')})),
                        'log_sonu': tail('\n'.join(str(l.get('message')) for l in logs))})
    return runs, details


# ---------- GitHub Actions ----------
def github_client(cfg, factory):
    return bearer(factory, 'https://api.github.com', 'GITHUB_TOKEN', {'X-GitHub-Api-Version': '2022-11-28'})


def github_repo(cfg):
    repo = required(cfg, 'repository')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise NativeError('repository sahip/depo biçiminde olmalı.')
    return repo


def github_runs(cfg, factory, out):
    client, repo = github_client(cfg, factory), github_repo(cfg)
    days = opt_int(cfg, 'days', 7, 1, 90)
    runs = []
    for page in range(1, 31):
        r = client.json('GET', f'/repos/{repo}/actions/runs?' + urlencode({'created': '>=' + since(days).date().isoformat(), 'per_page': 100, 'page': page}))
        batch = r.get('workflow_runs', [])
        runs += batch
        if len(batch) < 100:
            break
    state = {'success': OK, 'failure': FAIL, 'timed_out': FAIL, 'startup_failure': FAIL}
    rows = []
    for r in runs:
        a, b = parse_time(r.get('run_started_at')), parse_time(r.get('updated_at'))
        rows.append({'pipeline': r.get('name'), 'calisma_id': r.get('id'), 'durum': state.get(r.get('conclusion'), 'diğer'), 'ham_durum': r.get('conclusion') or r.get('status'),
                     'baslangic': r.get('run_started_at'), 'bitis': r.get('updated_at') if r.get('status') == 'completed' else '',
                     'sure_sn': (b - a).total_seconds() if a and b and r.get('status') == 'completed' else ''})
    return rows, days, client


def github_failures(cfg, factory, out):
    runs, _, client = github_runs(cfg, factory, out)
    repo = github_repo(cfg)
    details = []
    for r in [x for x in runs if x['durum'] == FAIL][:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        jobs = client.json('GET', f"/repos/{repo}/actions/runs/{int(r['calisma_id'])}/jobs?per_page=50").get('jobs', [])
        for j in [j for j in jobs if j.get('conclusion') in ('failure', 'timed_out')][:3]:
            raw, _ = client.request('GET', f"/repos/{repo}/check-runs/{int(j['id'])}/annotations?per_page=30")
            notes = json.loads(raw) if raw else []
            steps = [s.get('name') for s in j.get('steps', []) if s.get('conclusion') == 'failure']
            details.append({'calisma_id': r['calisma_id'], 'pipeline': r['pipeline'], 'adim': f"{j.get('name')} / {', '.join(steps)}", 'runner': j.get('runner_name'),
                            'log_sonu': tail('\n'.join(f"{n.get('annotation_level')}: {n.get('message')}" for n in notes))})
    return runs, details


# ---------- Jenkins ----------
def jenkins_client(cfg, factory):
    return basic(factory, required(cfg, 'server'), 'JENKINS_USER', 'JENKINS_API_TOKEN')


def job_path(job):
    return '/'.join('job/' + quote(part, safe='') for part in job.split('/'))  # relative: Jenkins may live under a context path


def jenkins_runs(cfg, factory, out):
    client = jenkins_client(cfg, factory)
    days = opt_int(cfg, 'days', 7, 1, 90)
    jobs = names(cfg, 'jobs', limit=100, pattern=re.compile(r'[^\x00-\x1f]{1,200}'), optional=True)
    if not jobs:
        jobs = [j.get('name') for j in client.json('GET', 'api/json?tree=jobs[name]').get('jobs', []) if j.get('name')]
    cutoff = since(days).timestamp() * 1000
    rows = []
    state = {'SUCCESS': OK, 'FAILURE': FAIL, 'UNSTABLE': FAIL}
    for job in jobs:
        builds = client.json('GET', job_path(job) + '/api/json?tree=' + quote('builds[number,result,duration,timestamp]{0,200}', safe='[],{}')).get('builds', [])
        for b in builds:
            if (b.get('timestamp') or 0) < cutoff:
                continue
            rows.append({'pipeline': job, 'calisma_id': f"{job}#{b.get('number')}", 'durum': state.get(b.get('result'), 'diğer'), 'ham_durum': b.get('result') or 'RUNNING',
                         'baslangic': dt.datetime.fromtimestamp(b['timestamp'] / 1000, dt.timezone.utc).isoformat(), 'bitis': '', 'sure_sn': (b.get('duration') or 0) / 1000 or ''})
    return rows, days, client


def jenkins_failures(cfg, factory, out):
    runs, _, client = jenkins_runs(cfg, factory, out)
    details = []
    for r in [x for x in runs if x['durum'] == FAIL][:opt_int(cfg, 'max_logs', 15, 0, 50)]:
        job, number_ = r['calisma_id'].rsplit('#', 1)
        raw, _ = client.request('GET', job_path(job) + f'/{int(number_)}/consoleText', accept='text/plain', limit=8 * 1024 * 1024)
        details.append({'calisma_id': r['calisma_id'], 'pipeline': job, 'adim': '', 'log_sonu': tail(raw.decode('utf-8', 'replace'), 60)})
    return runs, details


PRODUCTS = {
    'dagster': ('Dagster', dagster_runs, dagster_failures), 'prefect': ('Prefect', prefect_runs, prefect_failures),
    'astro': ('Astro', lambda c, f, o: airflow_runs(c, f, o, 'ASTRO_API_TOKEN'), lambda c, f, o: airflow_failures(c, f, o, 'ASTRO_API_TOKEN')),
    'kestra': ('Kestra', kestra_runs, kestra_failures), 'github-actions': ('GitHub Actions', github_runs, github_failures), 'jenkins': ('Jenkins', jenkins_runs, jenkins_failures),
    'airflow': ('Apache Airflow', airflow_runs, airflow_failures),
}


def pipeline_metrics(cfg, out, factory):
    key = cfg['operation'].rsplit('-pipeline-metrics', 1)[0]
    label, reader, _ = PRODUCTS[key]
    runs, days, _ = reader(cfg, factory, out)
    return metrics_task(out, runs, source_label=label, days=days)


CATEGORIES = {'veri', 'kod', 'altyapi', 'bagimlilik', 'yetki', 'zaman_asimi', 'yapilandirma', 'kaynak_kotasi', 'bilinmiyor'}
SCHEMA = ('{"olaylar":[{"calisma_id":"kanıttaki çalışma kimliği","pipeline":"...","adim":"...","kategori":"veri|kod|altyapi|bagimlilik|yetki|zaman_asimi|yapilandirma|kaynak_kotasi|bilinmiyor",'
          '"kok_neden":"...","kanit":"log satırından kısa alıntı","duzeltme":"...","onleme":"retry/alarm/test önerisi","oncelik":"yuksek|orta|dusuk"}],'
          '"ortak_desenler":["..."],"sorular":["..."]}')


def root_cause(cfg, out, factory):
    key = cfg['operation'].rsplit('-root-cause', 1)[0]
    label, _, collect = PRODUCTS[key]
    runs, details = collect(cfg, factory, out)
    failed = [r for r in runs if r['durum'] == FAIL]
    if not failed:
        raise NativeError('Seçilen dönemde başarısız çalışma yok; kök neden analizi gerekmiyor.')
    run_ids = {str(r['calisma_id']).lower() for r in failed}
    summary = {}
    for r in runs:
        s = summary.setdefault(r['pipeline'], {'pipeline': r['pipeline'], 'calisma': 0, 'basarisiz': 0})
        s['calisma'] += 1
        s['basarisiz'] += r['durum'] == FAIL
    evidence = {'basarisiz_calismalar': failed[:200], 'ayrintilar': details, 'pipeline_ozeti': sorted(summary.values(), key=lambda s: -s['basarisiz'])[:100]}

    def validate(obj):
        for i, e in enumerate(need_list(obj, 'olaylar', 'olaylar', maximum=60), 1):
            run = str(need_text(e, 'calisma_id', f'olay {i}')).lower()
            if run not in run_ids:
                raise ValueError(f'olay {i}: kanıtta olmayan çalışma kimliği: {e["calisma_id"]}')
            need_text(e, 'kategori', f'olay {i}', choices=CATEGORIES)
            need_text(e, 'kok_neden', f'olay {i}')
            need_text(e, 'duzeltme', f'olay {i}')
            need_text(e, 'oncelik', f'olay {i}', choices={'yuksek', 'orta', 'dusuk'})
        return obj

    def render(result):
        import csv, io
        buf = io.StringIO()
        cols = ['oncelik', 'pipeline', 'calisma_id', 'adim', 'kategori', 'kok_neden', 'kanit', 'duzeltme', 'onleme']
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(result['olaylar'])
        return {'kok_neden.json': plan_json(result), 'kok_neden.csv': buf.getvalue(),
                'kok_neden.md': rows_md('Pipeline hata kök neden analizi — ' + label,
                                        [('Olaylar', result['olaylar'], cols), ('Ortak desenler ve sorular', [{'madde': x} for x in result.get('ortak_desenler', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Pipeline hata kök neden analizi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Kök nedeni yalnızca log ve adım kanıtına dayandır; kanıt yetersizse kategori "bilinmiyor" olsun ve hangi logun gerektiğini soru olarak yaz. '
                            'Aynı hatayı tekrarlayan çalışmaları ortak desen olarak grupla.')


HANDLERS = {k + '-pipeline-metrics': pipeline_metrics for k in PRODUCTS if k != 'airflow'}
HANDLERS.update({k + '-root-cause': root_cause for k in PRODUCTS})
