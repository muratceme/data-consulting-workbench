"""Data observability: volume & missing-value report from each observability platform's metrics/events (code) and an
evidence-bound data incident review & action plan (agent). Monitors and incidents are only read."""
import datetime as dt
import json
import re
import statistics
import time
from collections import defaultdict
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, ident, journal, name, names, need_list, need_text, number, opt_float, opt_int,
                           parse_time, plan_json, receipt, required, rows_md, secret, sql_source, write_csv)

COLUMNS = ['nesne', 'kolon', 'metrik', 'deger', 'baz', 'sapma', 'durum', 'zaman', 'ayrinti']


def row(obj, column, metric, value, base='', status='', when='', detail=''):
    dev = ''
    if number(value) is not None and number(base):
        dev = round((number(value) - number(base)) / number(base), 4)
    return {'nesne': str(obj or ''), 'kolon': str(column or ''), 'metrik': metric, 'deger': value if value is not None else '', 'baz': base, 'sapma': dev,
            'durum': status, 'zaman': when or '', 'ayrinti': str(detail or '')[:300]}


def series_check(series, cfg):
    """series: {(nesne, kolon, metrik): [(time, value)]} → latest value vs median of the earlier points."""
    tolerance = opt_float(cfg, 'volume_tolerance', 0.3, 0.01, 10)
    max_null = opt_float(cfg, 'max_null_rate', 0.05, 0, 1)
    rows = []
    for (obj, col, metric), points in sorted(series.items()):
        points = sorted((p for p in points if number(p[1]) is not None), key=lambda p: str(p[0]))
        if not points:
            continue
        when, latest = points[-1][0], number(points[-1][1])
        earlier = [number(v) for _, v in points[:-1]]
        base = statistics.median(earlier) if earlier else None
        low = metric.lower()
        if 'null' in low or 'missing' in low:
            rate = latest if 'percent' in low or 'rate' in low or latest <= 1 else None
            status = 'eşik üstü' if rate is not None and rate > max_null else 'normal'
        elif base:
            status = 'sapma' if abs(latest - base) / base > tolerance else 'normal'
            if latest == 0:
                status = 'boş yükleme'
        else:
            status = 'baz yok'
        rows.append(row(obj, col, metric, latest, round(base, 4) if base is not None else '', status, when, f'{len(points)} nokta'))
    return rows


# ---------- Monte Carlo (events: volume / field health / freshness) ----------
MC_EVENTS = ('query($first:Int!,$after:String,$startTime:DateTime,$descOrder:Boolean){getEvents(first:$first,after:$after,startTime:$startTime,descOrder:$descOrder)'
             '{edges{node{uuid eventType eventState createdTime description data monitorId alert{id} table{mcon fullTableId}}} pageInfo{endCursor hasNextPage}}}')


def montecarlo(cfg, factory, out):
    client = factory('https://api.getmontecarlo.com', {'x-mcd-id': secret('MCD_ID'), 'x-mcd-token': secret('MCD_TOKEN')})
    days = opt_int(cfg, 'days', 7, 1, 90)
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    events, after = [], None
    for _ in range(50):
        data = native.graphql(client, '/graphql', MC_EVENTS, {'first': 200, 'after': after, 'startTime': start, 'descOrder': True}).get('getEvents') or {}
        events += [e.get('node') or {} for e in data.get('edges', [])]
        info = data.get('pageInfo') or {}
        if not info.get('hasNextPage'):
            break
        after = info.get('endCursor')
    wanted = re.compile(cfg.get('event_type_pattern', r'(?i)volume|size|row|null|field|freshness|unchanged'))
    rows = [row((e.get('table') or {}).get('fullTableId'), '', e.get('eventType'), '', '', e.get('eventState'), e.get('createdTime'), e.get('description'))
            for e in events if wanted.search(str(e.get('eventType') or ''))]
    return rows, {'olay': events[:300], 'gun': days}


# ---------- Bigeye ----------
def bigeye(cfg, factory, out):
    client = factory(required(cfg, 'server'), {'Authorization': 'apikey ' + secret('BIGEYE_API_KEY')})
    tables = [int(t) for t in cfg.get('table_ids') or [] if str(t).isdigit()]
    if not tables:
        raise NativeError('table_ids Bigeye tablo kimliklerinin listesi olmalı.')
    r = client.json('POST', '/api/v1/metrics/info', {'tableIds': tables})
    rows, raw = [], []
    for m in r.get('metricInfos', []):
        conf = m.get('metricConfiguration') or {}
        mtype = ((conf.get('metricType') or {}).get('predefinedMetric') or {}).get('metricName') or conf.get('name')
        column = next((p.get('columnName') for p in conf.get('parameters', []) if p.get('columnName')), '')
        runs = m.get('latestMetricRuns') or []
        latest = runs[0] if runs else {}
        rows.append(row(conf.get('tableName') or conf.get('datasetName') or conf.get('warehouseId'), column, mtype, latest.get('value'), '',
                        str(m.get('status', '')).replace('METRIC_RUN_STATUS_', '').lower(), latest.get('runAtEpochSeconds'), conf.get('name')))
        raw.append({'metrik': mtype, 'kolon': column, 'durum': m.get('status')})
    return rows, {'metrikler': raw[:500]}


# ---------- Datadog Data Streams Monitoring ----------
def datadog(cfg, factory, out):
    site = cfg.get('site', 'datadoghq.com')
    if not re.fullmatch(r'(us3\.|us5\.|ap1\.|ap2\.)?datadoghq\.(com|eu)|ddog-gov\.com', site):
        raise NativeError('site datadoghq.com, datadoghq.eu, us3/us5/ap1/ap2.datadoghq.com veya ddog-gov.com olmalı.')
    client = factory(f'https://api.{site}', {'DD-API-KEY': secret('DD_API_KEY'), 'DD-APPLICATION-KEY': secret('DD_APP_KEY')})
    hours = opt_int(cfg, 'hours', 24, 1, 720)
    env = name(cfg, 'env', re.compile(r'[A-Za-z0-9_.:-]{1,64}'))
    now = int(time.time())
    queries = {'kafka_lag_mesaj': f'max:data_streams.kafka.lag_messages{{env:{env}}} by {{topic,consumer_group}}',
               'kafka_lag_saniye': f'max:data_streams.kafka.lag_seconds{{env:{env}}} by {{topic,consumer_group}}',
               'mesaj_sayisi': f'sum:data_streams.payload_size.count{{env:{env}}} by {{topic}}.as_count()'}
    series = defaultdict(list)
    gaps = []
    for label, q in queries.items():
        r = client.json('GET', '/api/v1/query?' + urlencode({'from': now - hours * 3600, 'to': now, 'query': q}))
        for s in r.get('series', []):
            tags = dict(t.split(':', 1) for t in s.get('tag_set', []) if ':' in t)
            obj = tags.get('topic', s.get('scope'))
            points = [(dt.datetime.fromtimestamp(p[0] / 1000, dt.timezone.utc).isoformat(), p[1]) for p in s.get('pointlist', []) if p[1] is not None]
            series[(obj, tags.get('consumer_group', ''), label)] += points
            if label == 'mesaj_sayisi':
                gaps += [row(obj, '', 'mesaj gelmeyen aralık', 0, '', 'eksik veri', t) for t, v in points if v == 0]
    rows = series_check(series, cfg)
    max_lag = opt_float(cfg, 'max_lag_seconds', 300, 1, 10 ** 7)
    for r in rows:
        if r['metrik'] == 'kafka_lag_saniye' and number(r['deger']) and number(r['deger']) > max_lag:
            r['durum'] = 'gecikme eşik üstü'
    return rows + gaps[:200], {'ortam': env, 'saat': hours}


# ---------- Acceldata (ADOC policies) ----------
def acceldata(cfg, factory, out):
    client = factory(required(cfg, 'server'), {'accessKey': secret('ACCELDATA_ACCESS_KEY'), 'secretKey': secret('ACCELDATA_SECRET_KEY')})
    rows, raw = [], []
    for uid in names(cfg, 'asset_uids', limit=50, pattern=re.compile(r'[^\x00-\x1f]{1,300}')):
        r = client.json('GET', f'/catalog-server/api/assets/byUid/{ident(uid)}/rulesWithLatestExecution?' + urlencode({'onlyLatest': 'true', 'page': 0, 'size': 100}))
        items = r.get('rules') or r.get('content') or r.get('data') or []
        for it in items:
            rule = it.get('rule') or it
            ex = it.get('latestExecution') or it.get('execution') or {}
            rows.append(row(uid, '', f"{rule.get('ruleType') or rule.get('type')}: {rule.get('name')}", ex.get('qualityScore') if ex.get('qualityScore') is not None else '',
                            '', str(ex.get('resultStatus') or ex.get('executionStatus') or '').lower(), ex.get('finishedAt') or ex.get('startedAt'), ex.get('executionError') or ''))
            raw.append({'varlik': uid, 'kural': rule.get('name'), 'tur': rule.get('ruleType'), 'sonuc': ex.get('resultStatus'), 'durum': ex.get('executionStatus')})
    return rows, {'politikalar': raw[:500]}


# ---------- Elementary (dbt package tables in the warehouse) ----------
def elementary(cfg, factory, out):
    kind = cfg.get('destination', 'snowflake')
    if kind not in ('snowflake', 'bigquery', 'databricks', 'redshift', 'postgres'):
        raise NativeError('destination snowflake, bigquery, databricks, redshift veya postgres olmalı.')
    schema = name(cfg, 'elementary_schema')
    days = opt_int(cfg, 'days', 14, 1, 90)
    since = {'bigquery': f'TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)', 'snowflake': f'DATEADD(day, -{days}, CURRENT_TIMESTAMP())',
             'databricks': f'current_timestamp() - INTERVAL {days} DAYS'}.get(kind, f"CURRENT_TIMESTAMP - INTERVAL '{days} days'")
    src = sql_source(kind, cfg, factory, out)
    _, metrics = src.query(f"SELECT full_table_name, column_name, metric_name, metric_value, bucket_end FROM {schema}.data_monitoring_metrics "
                           f"WHERE bucket_end >= {since} AND metric_name IN ('row_count', 'null_count', 'null_percent', 'missing_count', 'missing_percent')", 'metrics')
    _, tests = src.query(f"SELECT test_type, test_sub_type, status, table_name, column_name, detected_at, test_results_description FROM {schema}.elementary_test_results "
                         f"WHERE detected_at >= {since} AND status <> 'pass'", 'test_results')
    series = defaultdict(list)
    for m in metrics:
        series[(m.get('full_table_name'), m.get('column_name') or '', m.get('metric_name'))].append((str(m.get('bucket_end')), m.get('metric_value')))
    rows = series_check(series, cfg)
    rows += [row(t.get('table_name'), t.get('column_name'), f"{t.get('test_type')}/{t.get('test_sub_type')}", '', '', t.get('status'), t.get('detected_at'),
                 t.get('test_results_description')) for t in tests]
    return rows, {'test_sonuclari': tests[:300]}


PRODUCTS = {'montecarlo': ('Monte Carlo', montecarlo), 'bigeye': ('Bigeye', bigeye), 'datadog-dsm': ('Datadog Data Streams Monitoring', datadog),
            'acceldata': ('Acceldata', acceldata), 'elementary': ('Elementary', elementary)}


def volume_report(cfg, out, factory):
    key = cfg['operation'].rsplit('-volume-report', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    if not rows:
        raise NativeError('Seçilen dönemde hacim/eksik değer metriği veya olayı bulunamadı.')
    write_csv(out, 'hacim_ve_eksik_deger.csv', rows, COLUMNS)
    issues = [r for r in rows if r['durum'] not in ('normal', 'pass', 'healthy', 'ok', 'baz yok')]
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, 'satir': len(rows), 'sorunlu': len(issues), 'nesne': len({r['nesne'] for r in rows}),
                                      'ilk_sorunlar': issues[:10]})


SCHEMA = ('{"olaylar":[{"referans":"kanıttaki nesne/olay/kural","etki":"hangi rapor/tüketici","olasi_neden":"...","kanit":"değer/baz/sapma","aksiyonlar":["..."],'
          '"sahip":"rol","oncelik":"yuksek|orta|dusuk","kapanis_kriteri":"..."}],"runbook":["adım"],"izleme_onerileri":["eşik/monitor değişikliği"],"sorular":["..."]}')


def incident_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-incident-plan', 1)[0]
    label, reader = PRODUCTS[key]
    rows, info = reader(cfg, factory, out)
    issues = [r for r in rows if r['durum'] not in ('normal', 'pass', 'healthy', 'ok')]
    if not issues:
        raise NativeError('İncelenecek veri olayı yok.')
    refs = {r['nesne'].lower() for r in issues} | {str(r['metrik']).lower() for r in issues}
    for v in (info.get('olay') or []):
        refs.add(str(v.get('uuid', '')).lower())

    def validate(obj):
        for i, e in enumerate(need_list(obj, 'olaylar', 'olaylar', maximum=60), 1):
            ref = need_text(e, 'referans', f'olay {i}').lower()
            if ref not in refs and not any(ref in r or r in ref for r in refs if r):
                raise ValueError(f'olay {i}: kanıtta olmayan referans: {e["referans"]}')
            need_text(e, 'olasi_neden', f'olay {i}')
            need_text(e, 'oncelik', f'olay {i}', choices={'yuksek', 'orta', 'dusuk'})
            if not isinstance(e.get('aksiyonlar'), list) or not e['aksiyonlar']:
                raise ValueError(f'olay {i}: en az bir aksiyon gerekli.')
        return obj

    def render(result):
        flat = [{**e, 'aksiyonlar': '; '.join(e['aksiyonlar'])} for e in result['olaylar']]
        return {'olay_plani.json': plan_json(result),
                'olay_plani.md': rows_md('Veri olayı incelemesi ve aksiyon planı — ' + label,
                                         [('Olaylar', flat, ['oncelik', 'referans', 'etki', 'olasi_neden', 'kanit', 'aksiyonlar', 'sahip', 'kapanis_kriteri']),
                                          ('Runbook', [{'adim': x} for x in result.get('runbook', [])], ['adim']),
                                          ('İzleme önerileri ve sorular', [{'madde': x} for x in result.get('izleme_onerileri', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Veri olayı incelemesi ve aksiyon planı', evidence={'sorunlar': issues[:300], 'ek': {k: v for k, v in info.items() if k != 'olay'}},
                      schema=SCHEMA, validate=validate, render=render,
                      rules='Aynı nesnedeki hacim ve null sorunlarını tek olayda topla; her olay için ölçülebilir kapanış kriteri yaz. Yanlış alarm olasılığını kanıtla tartış.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-volume-report'] = volume_report
    HANDLERS[_key + '-incident-plan'] = incident_plan
