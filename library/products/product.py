"""Product & marketing analytics: daily event report from each analytics platform's reporting API (code) and an
evidence-bound funnel & measurement plan with platform-native funnel queries (agent). Nothing is configured on the platform."""
import datetime as dt
import json
import re
from collections import defaultdict
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, basic, bearer, ident, journal, name, names, need_list, need_text, number, opt_int, plan_json,
                           receipt, required, rows_md, secret, write_csv)

EVENT = re.compile(r'[^\x00-\x1f]{1,200}')


def window(cfg):
    days = opt_int(cfg, 'days', 30, 1, 400)
    end = dt.date.today()
    return days, end - dt.timedelta(days=days), end


def event_list(cfg):
    return names(cfg, 'events', limit=50, pattern=EVENT, optional=True)


# ---------- Google Analytics 4 (Data API) ----------
def ga4_client(cfg, factory):
    prop = str(required(cfg, 'property_id'))
    if not prop.isdigit():
        raise NativeError('property_id sayısal GA4 mülk kimliği olmalı.')
    return bearer(factory, 'https://analyticsdata.googleapis.com', 'GOOGLE_ACCESS_TOKEN'), prop


def ga4_events(cfg, factory):
    client, prop = ga4_client(cfg, factory)
    days, start, end = window(cfg)
    body = {'dateRanges': [{'startDate': start.isoformat(), 'endDate': end.isoformat()}], 'dimensions': [{'name': 'date'}, {'name': 'eventName'}],
            'metrics': [{'name': 'eventCount'}, {'name': 'totalUsers'}], 'limit': 100000}
    if event_list(cfg):
        body['dimensionFilter'] = {'filter': {'fieldName': 'eventName', 'inListFilter': {'values': event_list(cfg)}}}
    r = client.json('POST', f'/v1beta/properties/{prop}:runReport', body)
    rows = []
    for x in r.get('rows', []):
        d, e = [v.get('value') for v in x.get('dimensionValues', [])]
        c, u = [v.get('value') for v in x.get('metricValues', [])]
        rows.append({'gun': f'{d[:4]}-{d[4:6]}-{d[6:]}', 'olay': e, 'adet': number(c), 'kullanici': number(u)})
    return rows


def ga4_catalog(cfg, factory):
    client, prop = ga4_client(cfg, factory)
    meta = client.json('GET', f'/v1beta/properties/{prop}/metadata')
    return {'ozel_boyutlar': [d.get('apiName') for d in meta.get('dimensions', []) if d.get('customDefinition')],
            'ozel_metrikler': [m.get('apiName') for m in meta.get('metrics', []) if m.get('customDefinition')]}


# ---------- Adobe Analytics (2.0 Reports API) ----------
def adobe_client(cfg, factory):
    company = name(cfg, 'global_company_id', re.compile(r'[A-Za-z0-9]{3,40}'))
    return factory('https://analytics.adobe.io', {'Authorization': 'Bearer ' + secret('ADOBE_ACCESS_TOKEN'), 'x-api-key': secret('ADOBE_CLIENT_ID'),
                                                   'x-proxy-global-company-id': company}), company


def adobe_events(cfg, factory):
    client, company = adobe_client(cfg, factory)
    days, start, end = window(cfg)
    metrics = names(cfg, 'metrics', limit=20, pattern=re.compile(r'metrics/[A-Za-z0-9_.]{1,80}'))
    body = {'rsid': required(cfg, 'report_suite_id'), 'globalFilters': [{'type': 'dateRange', 'dateRange': f'{start.isoformat()}T00:00:00.000/{end.isoformat()}T00:00:00.000'}],
            'metricContainer': {'metrics': [{'columnId': str(i), 'id': m} for i, m in enumerate(metrics)]}, 'dimension': 'variables/daterangeday', 'settings': {'limit': 400, 'page': 0}}
    r = client.json('POST', f'/api/{company}/reports', body)
    rows = []
    for x in r.get('rows', []):
        for m, v in zip(metrics, x.get('data', [])):
            rows.append({'gun': x.get('value'), 'olay': m, 'adet': number(v), 'kullanici': ''})
    return rows


def adobe_catalog(cfg, factory):
    client, company = adobe_client(cfg, factory)
    rsid = required(cfg, 'report_suite_id')
    raw, _ = client.request('GET', f'/api/{company}/metrics?' + urlencode({'rsid': rsid}))
    metrics = json.loads(raw) if raw else []
    raw, _ = client.request('GET', f'/api/{company}/dimensions?' + urlencode({'rsid': rsid}))
    dims = json.loads(raw) if raw else []
    return {'metrikler': [{'olay': m.get('id'), 'ad': m.get('name')} for m in metrics if isinstance(m, dict)][:400],
            'boyutlar': [d.get('id') for d in dims if isinstance(d, dict)][:400]}


# ---------- Amplitude (Dashboard REST API) ----------
def amplitude_client(cfg, factory):
    region = cfg.get('region', 'us')
    host = 'https://analytics.eu.amplitude.com' if region == 'eu' else 'https://amplitude.com'
    return basic(factory, host, 'AMPLITUDE_API_KEY', 'AMPLITUDE_SECRET_KEY')


def amplitude_events(cfg, factory):
    client = amplitude_client(cfg, factory)
    days, start, end = window(cfg)
    events = event_list(cfg) or [e.get('name') for e in client.json('GET', '/api/2/events/list').get('data', [])[:opt_int(cfg, 'top_events', 15, 1, 100)]]
    rows = []
    for e in events:
        q = {'e': json.dumps({'event_type': e}), 'start': start.strftime('%Y%m%d'), 'end': end.strftime('%Y%m%d'), 'm': 'totals', 'i': 1}
        data = client.json('GET', '/api/2/events/segmentation?' + urlencode(q)).get('data') or {}
        dates, series = data.get('xValues', []), (data.get('series') or [[]])[0]
        rows += [{'gun': d, 'olay': e, 'adet': number(v), 'kullanici': ''} for d, v in zip(dates, series)]
    return rows


def amplitude_catalog(cfg, factory):
    client = amplitude_client(cfg, factory)
    return {'olaylar': [{'olay': e.get('name'), 'haftalik_toplam': e.get('totals'), 'gizli': e.get('hidden')} for e in client.json('GET', '/api/2/events/list').get('data', [])][:500]}


# ---------- Mixpanel (Query API) ----------
def mixpanel_client(cfg, factory):
    host = 'https://eu.mixpanel.com' if cfg.get('region') == 'eu' else 'https://mixpanel.com'
    return basic(factory, host, 'MIXPANEL_SERVICE_ACCOUNT', 'MIXPANEL_SERVICE_SECRET'), str(required(cfg, 'project_id'))


def mixpanel_events(cfg, factory):
    client, project = mixpanel_client(cfg, factory)
    days, start, end = window(cfg)
    events = event_list(cfg) or list((client.json('GET', '/api/query/events/top?' + urlencode({'project_id': project, 'type': 'general', 'limit': 15})).get('events') or []))
    events = [e.get('event') if isinstance(e, dict) else e for e in events]
    r = client.json('GET', '/api/query/events?' + urlencode({'project_id': project, 'event': json.dumps(events), 'type': 'general', 'unit': 'day',
                                                             'from_date': start.isoformat(), 'to_date': end.isoformat()}))
    values = (r.get('data') or {}).get('values') or {}
    return [{'gun': d, 'olay': e, 'adet': number(n), 'kullanici': ''} for e, series in values.items() for d, n in sorted(series.items())]


def mixpanel_catalog(cfg, factory):
    client, project = mixpanel_client(cfg, factory)
    raw, _ = client.request('GET', '/api/query/events/names?' + urlencode({'project_id': project, 'type': 'general', 'limit': 255}))
    return {'olaylar': [{'olay': e} for e in (json.loads(raw) if raw else [])]}


# ---------- Segment (Public API event volume) ----------
def segment_client(cfg, factory):
    return bearer(factory, cfg.get('api_url', 'https://api.segmentapis.com'), 'SEGMENT_TOKEN')


def segment_events(cfg, factory):
    client = segment_client(cfg, factory)
    days, start, end = window(cfg)
    q = [('granularity', 'DAY'), ('startTime', f'{start.isoformat()}T00:00:00Z'), ('endTime', f'{end.isoformat()}T00:00:00Z'), ('groupBy', 'eventName')]
    q += [('sourceId', s) for s in names(cfg, 'source_ids', limit=20, optional=True)]
    data = (client.json('GET', '/events/volume?' + urlencode(q)).get('data') or {}).get('result', [])
    return [{'gun': p.get('time', '')[:10], 'olay': x.get('eventName'), 'adet': number(p.get('count')), 'kullanici': ''} for x in data for p in x.get('series', [])]


def segment_catalog(cfg, factory):
    client = segment_client(cfg, factory)
    plans = client.json('GET', '/tracking-plans?' + urlencode({'pagination.count': 50})).get('data', {}).get('trackingPlans', [])
    rules = []
    for p in plans[:5]:
        r = client.json('GET', f"/tracking-plans/{ident(p['id'])}/rules?" + urlencode({'pagination.count': 200})).get('data', {}).get('rules', [])
        rules += [{'olay': x.get('key'), 'plan': p.get('name'), 'ozellikler': list(((x.get('jsonSchema') or {}).get('properties') or {}).get('properties', {}).get('properties', {}).keys())}
                  for x in r if x.get('type') == 'TRACK']
    return {'izleme_plani_kurallari': rules[:500]}


# ---------- Salesforce Data Cloud (Query API V2) ----------
def datacloud_client(cfg, factory):
    return bearer(factory, required(cfg, 'tenant_url'), 'DATACLOUD_ACCESS_TOKEN')


def datacloud_events(cfg, factory):
    client = datacloud_client(cfg, factory)
    dmo, ev, ts = name(cfg, 'dmo'), name(cfg, 'event_field'), name(cfg, 'time_field')
    days, start, end = window(cfg)
    sql = (f"SELECT DATE_TRUNC('day', {ts}) AS gun, {ev} AS olay, COUNT(*) AS adet FROM {dmo} WHERE {ts} >= TIMESTAMP '{start.isoformat()} 00:00:00' "
           f'GROUP BY 1, 2 ORDER BY 1')
    r = client.json('POST', '/api/v2/query', {'sql': sql})
    cols = sorted((r.get('metadata') or {}).items(), key=lambda kv: kv[1].get('placeInOrder', 0))
    names_ = [c[0].lower() for c in cols] or ['gun', 'olay', 'adet']
    return [{**dict(zip(names_, row)), 'kullanici': ''} for row in r.get('data', [])]


def datacloud_catalog(cfg, factory):
    client = datacloud_client(cfg, factory)
    meta = client.json('GET', '/api/v1/metadata?' + urlencode({'entityName': cfg['dmo']}))
    ent = (meta.get('metadata') or [{}])[0]
    values = []
    try:
        r = client.json('POST', '/api/v2/query', {'sql': f"SELECT {name(cfg, 'event_field')} AS olay, COUNT(*) AS adet FROM {name(cfg, 'dmo')} GROUP BY 1 ORDER BY 2 DESC LIMIT 200"})
        values = [{'olay': row[0], 'toplam': row[1]} for row in r.get('data', [])]
    except NativeError:
        pass
    return {'alanlar': [f.get('name') for f in ent.get('fields', [])][:400], 'olaylar': values}


# ---------- HubSpot (custom behavioral events) ----------
def hubspot_client(cfg, factory):
    return bearer(factory, 'https://api.hubapi.com', 'HUBSPOT_TOKEN')


def hubspot_events(cfg, factory):
    client = hubspot_client(cfg, factory)
    days, start, end = window(cfg)
    types = event_list(cfg)
    if not types:
        raise NativeError('events alanına HubSpot olay tiplerini (ör. pe12345_purchase) yazın.')
    counts = defaultdict(int)
    for t in types:
        after = None
        for _ in range(500):
            q = {'objectType': cfg.get('object_type', 'contact'), 'eventType': t, 'occurredAfter': f'{start.isoformat()}T00:00:00Z', 'limit': 100, **({'after': after} if after else {})}
            r = client.json('GET', '/events/v3/events?' + urlencode(q))
            for e in r.get('results', []):
                counts[(str(e.get('occurredAt', ''))[:10], t)] += 1
            after = ((r.get('paging') or {}).get('next') or {}).get('after')
            if not after:
                break
    return [{'gun': d, 'olay': t, 'adet': n, 'kullanici': ''} for (d, t), n in sorted(counts.items())]


def hubspot_catalog(cfg, factory):
    client = hubspot_client(cfg, factory)
    r = client.json('GET', '/events/v3/event-definitions?' + urlencode({'limit': 100}))
    return {'olay_tanimlari': [{'olay': d.get('fullyQualifiedName') or d.get('name'), 'etiket': d.get('label'), 'ozellikler': [p.get('name') for p in d.get('properties', [])][:50]}
                               for d in r.get('results', [])]}


PRODUCTS = {'ga4': ('Google Analytics 4', ga4_events, ga4_catalog), 'adobe-analytics': ('Adobe Analytics', adobe_events, adobe_catalog),
            'amplitude': ('Amplitude', amplitude_events, amplitude_catalog), 'mixpanel': ('Mixpanel', mixpanel_events, mixpanel_catalog),
            'segment': ('Segment', segment_events, segment_catalog), 'data-cloud': ('Salesforce Data Cloud', datacloud_events, datacloud_catalog),
            'hubspot': ('HubSpot CRM', hubspot_events, hubspot_catalog)}


def event_report(cfg, out, factory):
    key = cfg['operation'].rsplit('-event-report', 1)[0]
    label, reader, _ = PRODUCTS[key]
    rows = reader(cfg, factory)
    if not rows:
        raise NativeError('Seçilen dönemde olay verisi dönmedi.')
    totals = defaultdict(float)
    for r in rows:
        totals[r['olay']] += number(r.get('adet')) or 0
    summary = [{'olay': e, 'toplam': round(t, 2), 'gunluk_ort': round(t / max(1, len({r['gun'] for r in rows})), 2)} for e, t in sorted(totals.items(), key=lambda x: -x[1])]
    days = sorted({str(r['gun']) for r in rows})
    half = len(days) // 2
    for s in summary:
        a = sum(number(r['adet']) or 0 for r in rows if r['olay'] == s['olay'] and str(r['gun']) in days[:half])
        b = sum(number(r['adet']) or 0 for r in rows if r['olay'] == s['olay'] and str(r['gun']) in days[half:])
        s['degisim'] = round((b - a) / a, 4) if a else ''
        s['sifir_gun'] = sum(1 for d in days if not any(str(r['gun']) == d and r['olay'] == s['olay'] and (number(r['adet']) or 0) > 0 for r in rows))
    write_csv(out, 'gunluk_olaylar.csv', rows, ['gun', 'olay', 'adet', 'kullanici'])
    write_csv(out, 'olay_ozeti.csv', summary, ['olay', 'toplam', 'gunluk_ort', 'degisim', 'sifir_gun'])
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, 'gun': len(days), 'olay_turu': len(summary), 'en_cok': summary[:5],
                                      'kesintili_olaylar': [s['olay'] for s in summary if s['sifir_gun']][:20]})


# ---------- Funnel & measurement plan agent ----------
SCHEMA = ('{"funneller":[{"ad":"Satın alma","adimlar":[{"olay":"kanıttaki olay"}],"donusum_penceresi_gun":7,"kirilimlar":["kanıttaki boyut/özellik"]}],'
          '"kpiler":[{"ad":"Dönüşüm oranı","pay":"olay","payda":"olay","hedef":"..."}],"eksik_olaylar":[{"olay":"yeni_olay_adi","neden":"...","ozellikler":["..."]}],'
          '"veri_kalitesi":["..."],"sorular":["..."]}')


def ga4_funnel(f):
    return {'dateRanges': [{'startDate': '30daysAgo', 'endDate': 'today'}], 'funnel': {'isOpenFunnel': False, 'steps': [
        {'name': s['olay'], 'filterExpression': {'funnelEventFilter': {'eventName': s['olay']}},
         **({'withinDurationFromPriorStep': f'{int(f.get("donusum_penceresi_gun", 7)) * 86400}s'} if i else {})} for i, s in enumerate(f['adimlar'])]}}


def funnel_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-funnel-plan', 1)[0]
    label, reader, catalog = PRODUCTS[key]
    rows = reader(cfg, factory)
    meta = catalog(cfg, factory)
    known = {str(r['olay']).lower(): r['olay'] for r in rows}
    for k in ('olaylar', 'metrikler', 'olay_tanimlari', 'izleme_plani_kurallari'):
        for e in meta.get(k, []) or []:
            if isinstance(e, dict) and e.get('olay'):
                known.setdefault(str(e['olay']).lower(), e['olay'])
    props = {str(p).lower() for k in ('ozel_boyutlar', 'boyutlar', 'alanlar') for p in meta.get(k, []) or []}
    props |= {str(p).lower() for k in ('olay_tanimlari', 'izleme_plani_kurallari') for e in meta.get(k, []) or [] for p in e.get('ozellikler', [])}
    totals = defaultdict(float)
    for r in rows:
        totals[r['olay']] += number(r.get('adet')) or 0
    evidence = {'olay_hacimleri': [{'olay': e, 'toplam': t} for e, t in sorted(totals.items(), key=lambda x: -x[1])][:300], 'katalog': meta}

    def validate(obj):
        for i, f in enumerate(need_list(obj, 'funneller', 'funneller', maximum=10), 1):
            need_text(f, 'ad', f'funnel {i}')
            steps = need_list(f, 'adimlar', f'funnel {i} adımları', minimum=2, maximum=10)
            bad = [s.get('olay') for s in steps if str(s.get('olay', '')).lower() not in known]
            if bad:
                raise ValueError(f'funnel {i}: kanıtta olmayan olay (yeni olayı eksik_olaylar’a yazın): ' + ', '.join(map(str, bad)))
            for s in steps:
                s['olay'] = known[str(s['olay']).lower()]
            w = f.get('donusum_penceresi_gun', 7)
            if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= 90:
                raise ValueError(f'funnel {i}: donusum_penceresi_gun 1–90 tamsayı olmalı.')
            badp = [p for p in f.get('kirilimlar') or [] if props and str(p).lower() not in props]
            if badp:
                raise ValueError(f'funnel {i}: katalogda olmayan kırılım: ' + ', '.join(map(str, badp)))
        for e in obj.get('eksik_olaylar') or []:
            if isinstance(e, dict) and str(e.get('olay', '')).lower() in known:
                raise ValueError(f'eksik olay zaten var: {e.get("olay")}')
        for k in obj.get('kpiler') or []:
            for side in ('pay', 'payda'):
                if k.get(side) and str(k[side]).lower() not in known and str(k[side]).lower() not in {str(e.get('olay', '')).lower() for e in obj.get('eksik_olaylar') or []}:
                    raise ValueError(f'KPI {k.get("ad")}: {side} olayı tanımsız: {k[side]}')
        return obj

    def render(result):
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['olay', 'durum', 'son_donem_toplam', 'ozellikler', 'neden'], lineterminator='\n')
        w.writeheader()
        used = {s['olay'] for f in result['funneller'] for s in f['adimlar']}
        for e in sorted(used):
            w.writerow({'olay': e, 'durum': 'mevcut', 'son_donem_toplam': totals.get(e, ''), 'ozellikler': '', 'neden': 'funnel adımı'})
        for e in result.get('eksik_olaylar') or []:
            w.writerow({'olay': e.get('olay'), 'durum': 'yeni (izlenmeli)', 'son_donem_toplam': '', 'ozellikler': ', '.join(e.get('ozellikler') or []), 'neden': e.get('neden', '')})
        files = {'olcumleme_plani.json': plan_json(result), 'izleme_plani.csv': buf.getvalue(),
                 'olcumleme_plani.md': rows_md('Funnel ve ölçümleme planı — ' + label,
                                               [('Funneller', [{'ad': f['ad'], 'adimlar': ' → '.join(s['olay'] for s in f['adimlar']), 'donusum_penceresi_gun': f.get('donusum_penceresi_gun', 7),
                                                                'kirilimlar': ', '.join(f.get('kirilimlar') or [])} for f in result['funneller']], ['ad', 'adimlar', 'donusum_penceresi_gun', 'kirilimlar']),
                                                ('KPI’lar', result.get('kpiler') or [], ['ad', 'pay', 'payda', 'hedef']),
                                                ('Eksik olaylar', result.get('eksik_olaylar') or [], ['olay', 'neden', 'ozellikler']),
                                                ('Veri kalitesi ve sorular', [{'madde': x} for x in result.get('veri_kalitesi', []) + result.get('sorular', [])], ['madde'])])}
        if key == 'ga4':
            files['ga4_runFunnelReport.json'] = json.dumps([ga4_funnel(f) for f in result['funneller']], ensure_ascii=False, indent=2)
        if key == 'amplitude':
            files['amplitude_funnel_sorgulari.txt'] = '\n'.join('/api/2/funnels?' + '&'.join('e=' + quote(json.dumps({'event_type': s['olay']})) for s in f['adimlar']) +
                                                                f"&cs={int(f.get('donusum_penceresi_gun', 7)) * 86400}&start=YYYYMMDD&end=YYYYMMDD" for f in result['funneller']) + '\n'
        return files
    return agent_task(cfg, out, product=label, title='Funnel ve ölçümleme planı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Funnel adımlarını yalnızca kanıttaki olaylardan kur; izlenmeyen ama gerekli adımları eksik_olaylar’a yeni olarak yaz. '
                            'Hacmi çok düşük veya kesintili olayları veri kalitesi riski olarak belirt.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-event-report'] = event_report
    HANDLERS[_key + '-funnel-plan'] = funnel_plan
