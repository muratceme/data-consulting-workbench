"""Enterprise planning & EPM: budget vs actual variance from plan data exported through each planning tool's API (code) and an
evidence-bound forecast model & scenario design (agent). Nothing is written back to the planning model."""
import csv
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, basic, bearer, ident, journal, name, names, need_list, need_text, number, odata_metadata, odata_rows,
                           opt_float, opt_int, plan_json, read_table, receipt, required, rows_md, secret, write_csv)

FIELD = re.compile(r'[^\x00-\x1f]{1,128}')


def csv_rows(text):
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


# ---------- SAP Analytics Cloud Planning (Data Export API) ----------
def sac_client(cfg, factory):
    return bearer(factory, required(cfg, 'tenant_url'), 'SAC_ACCESS_TOKEN')


def sac_version(cfg, factory, version):
    model = name(cfg, 'provider_id', re.compile(r'[A-Za-z0-9_.:-]{1,128}'))
    dims = names(cfg, 'dimensions', limit=10, pattern=FIELD)
    measure = name(cfg, 'measure', FIELD)
    vfield = cfg.get('version_field', 'Version')
    flt = f"{vfield} eq '{version.replace(chr(39), '')}'"
    return odata_rows(sac_client(cfg, factory), f'/api/v1/dataexport/providers/sac/{ident(model)}/FactData', select=dims + [measure], filter_=flt, v2=False)


def sac_plan(cfg, factory):
    return sac_version(cfg, factory, required(cfg, 'budget_version'))


def sac_actual(cfg, factory):
    return sac_version(cfg, factory, cfg['actual_version']) if cfg.get('actual_version') else None


def sac_meta(cfg, factory):
    model = name(cfg, 'provider_id', re.compile(r'[A-Za-z0-9_.:-]{1,128}'))
    raw, _ = sac_client(cfg, factory).request('GET', f'/api/v1/dataexport/providers/sac/{ident(model)}/$metadata', accept='application/xml')
    return {'model_alanlari': odata_metadata(raw, ['FactData'])}


# ---------- Oracle Cloud EPM (Planning REST exportdataslice) ----------
def epm_client(cfg, factory):
    return basic(factory, required(cfg, 'server'), 'EPM_USER', 'EPM_PASSWORD')


def epm_slice(cfg, factory, grid_key):
    app, cube = name(cfg, 'application'), name(cfg, 'plan_type')
    grid = json.loads(Path(required(cfg, grid_key)).read_text(encoding='utf-8'))
    r = epm_client(cfg, factory).json('POST', f'/HyperionPlanning/rest/v3/applications/{ident(app)}/plantypes/{ident(cube)}/exportdataslice',
                                       {'exportPlanningData': False, 'gridDefinition': grid})
    col_dims = [d for c in grid.get('columns', []) for d in c.get('dimensions', [])]
    row_dims = [d for rw in grid.get('rows', []) for d in rw.get('dimensions', [])]
    columns = r.get('columns') or []
    headers = list(zip(*columns)) if columns and isinstance(columns[0], list) else [(c,) for c in columns]
    out = []
    for row in r.get('rows', []):
        for col_members, value in zip(headers, row.get('data', [])):
            if value in (None, '', '#Missing'):
                continue
            rec = dict(zip(row_dims, row.get('headers', [])))
            rec.update(dict(zip(col_dims, col_members)))
            rec['deger'] = value
            out.append(rec)
    return out


def epm_meta(cfg, factory):
    client = epm_client(cfg, factory)
    app = name(cfg, 'application')
    r = client.json('GET', f'/HyperionPlanning/rest/v3/applications/{ident(app)}/plantypes')
    return {'plan_tipleri': [p.get('name') for p in r.get('items', [])], 'izgara': json.loads(Path(required(cfg, 'grid_file')).read_text(encoding='utf-8'))}


# ---------- Anaplan (Bulk API export) ----------
def anaplan_client(cfg, factory):
    auth = factory('https://auth.anaplan.com', {'Authorization': native.basic_auth(secret('ANAPLAN_USER'), secret('ANAPLAN_PASSWORD'))})
    token = required(auth.json('POST', '/token/authenticate').get('tokenInfo') or {}, 'tokenValue')
    return factory('https://api.anaplan.com', {'Authorization': 'AnaplanAuthToken ' + token})


def anaplan_export(cfg, factory, key):
    client = anaplan_client(cfg, factory)
    ws, model = name(cfg, 'workspace_id', re.compile(r'[0-9A-Fa-f]{32}')), name(cfg, 'model_id', re.compile(r'[0-9A-Fa-f]{32}'))
    export = name(cfg, key, re.compile(r'\d{6,20}'))
    base = f'/2/0/workspaces/{ws}/models/{model}'
    task = required(client.json('POST', f'{base}/exports/{export}/tasks', {'localeName': cfg.get('locale', 'en_US')}).get('task') or {}, 'taskId')
    for _ in range(150):
        state = (client.json('GET', f'{base}/exports/{export}/tasks/{task}').get('task') or {})
        if state.get('taskState') == 'COMPLETE':
            if not (state.get('result') or {}).get('successful'):
                raise NativeError('Anaplan dışa aktarma işi başarısız.')
            break
        time.sleep(2)
    else:
        raise NativeError('Anaplan dışa aktarma zaman aşımına uğradı.')
    chunks = client.json('GET', f'{base}/files/{export}/chunks').get('chunks', [])
    data = b''.join(client.request('GET', f"{base}/files/{export}/chunks/{c['id']}", accept='application/octet-stream')[0] for c in chunks)
    return csv_rows(data.decode('utf-8-sig'))


def anaplan_meta(cfg, factory):
    client = anaplan_client(cfg, factory)
    base = f"/2/0/workspaces/{name(cfg, 'workspace_id')}/models/{name(cfg, 'model_id')}"
    modules = client.json('GET', f'{base}/modules').get('modules', [])
    lists = client.json('GET', f'{base}/lists').get('lists', [])
    return {'moduller': [m.get('name') for m in modules][:300], 'listeler': [l.get('name') for l in lists][:300]}


# ---------- IBM Planning Analytics (TM1 REST, MDX) ----------
def tm1_client(cfg, factory):
    import os
    if os.environ.get('TM1_ACCESS_TOKEN'):
        return bearer(factory, required(cfg, 'server'), 'TM1_ACCESS_TOKEN')
    return basic(factory, required(cfg, 'server'), 'TM1_USER', 'TM1_PASSWORD')


def tm1_mdx(cfg, factory, key):
    mdx = Path(required(cfg, key)).read_text(encoding='utf-8').strip()
    if not re.match(r'(?is)^\s*SELECT\b.+\bFROM\b', mdx) or re.search(r'(?i)\b(UPDATE|CREATE|DROP|ALTER)\b', mdx):
        raise NativeError('MDX dosyası tek bir SELECT … FROM ifadesi olmalı.')
    q = '$expand=Axes($expand=Hierarchies($select=Name),Tuples($expand=Members($select=Name))),Cells($select=Ordinal,Value)'
    r = tm1_client(cfg, factory).json('POST', '/api/v1/ExecuteMDX?' + q, {'MDX': mdx})
    axes = r.get('Axes', [])
    cols, rows_axis = axes[0].get('Tuples', []), (axes[1].get('Tuples', []) if len(axes) > 1 else [{}])
    col_dims = [h.get('Name') for h in axes[0].get('Hierarchies', [])] if axes else []
    row_dims = [h.get('Name') for h in axes[1].get('Hierarchies', [])] if len(axes) > 1 else []
    cells = {c.get('Ordinal'): c.get('Value') for c in r.get('Cells', [])}
    out = []
    for ri, rt in enumerate(rows_axis):
        for ci, ct in enumerate(cols):
            value = cells.get(ri * len(cols) + ci)
            if value in (None, ''):
                continue
            rec = dict(zip(row_dims, [m.get('Name') for m in rt.get('Members', [])]))
            rec.update(dict(zip(col_dims, [m.get('Name') for m in ct.get('Members', [])])))
            rec['deger'] = value
            out.append(rec)
    return out


def tm1_meta(cfg, factory):
    cube = name(cfg, 'cube', FIELD)
    r = tm1_client(cfg, factory).json('GET', f"/api/v1/Cubes('{quote(cube)}')?$expand=Dimensions($select=Name)")
    return {'kup': cube, 'boyutlar': [d.get('Name') for d in r.get('Dimensions', [])]}


# ---------- Workday Adaptive Planning (XML API exportData) ----------
def adaptive_call(cfg, factory, body):
    from xml.sax.saxutils import quoteattr
    creds = f'<credentials login={quoteattr(secret("ADAPTIVE_USER"))} password={quoteattr(secret("ADAPTIVE_PASSWORD"))}' + \
            (f' instanceCode={quoteattr(cfg["instance_code"])}' if cfg.get('instance_code') else '') + '/>'
    xml = f'<?xml version="1.0" encoding="UTF-8"?>{body.replace("{CREDENTIALS}", creds)}'
    raw, _ = factory(cfg.get('api_url', 'https://api.adaptiveplanning.com'), {}).request('POST', '/api/' + cfg.get('api_version', 'v41'), raw=xml.encode('utf-8'),
                                                                                         content_type='text/xml; charset=UTF-8', accept='text/xml')
    root = ET.fromstring(raw)
    if root.get('success') != 'true':
        raise NativeError('Adaptive Planning çağrısı başarısız (kimlik, sürüm adı veya filtreler).')
    return root


def adaptive_export(cfg, factory, version_key):
    from xml.sax.saxutils import quoteattr
    version = required(cfg, version_key)
    accounts = ''.join(f'<account code={quoteattr(a)} isAssumption="false" includeDescendants="true"/>' for a in names(cfg, 'accounts', limit=50, pattern=FIELD))
    span = cfg.get('time_span') or {}
    time_xml = f'<timeSpan start={quoteattr(span["start"])} end={quoteattr(span["end"])}/>' if span.get('start') and span.get('end') else ''
    body = (f'<call method="exportData" callerName="datawork">{{CREDENTIALS}}<version name={quoteattr(version)} isDefault="false"/>'
            f'<format useInternalCodes="true" includeUnmappedItems="false"/><filters><accounts>{accounts}</accounts>{time_xml}</filters></call>')
    root = adaptive_call(cfg, factory, body)
    text = root.findtext('output') or ''
    return csv_rows(text.strip())


def adaptive_meta(cfg, factory):
    root = adaptive_call(cfg, factory, '<call method="exportVersions" callerName="datawork">{CREDENTIALS}</call>')
    versions = [v.get('name') for v in root.iter('version')]
    root = adaptive_call(cfg, factory, '<call method="exportAccounts" callerName="datawork">{CREDENTIALS}</call>')
    accounts = [{'kod': a.get('code'), 'ad': a.get('name'), 'tur': a.get('accountTypeCode')} for a in root.iter('account')][:500]
    return {'surumler': versions, 'hesaplar': accounts}


# ---------- Board (procedure export file) ----------
def board_file(cfg, factory, key):
    _, rows = read_table(required(cfg, key))
    return rows


def board_meta(cfg, factory):
    columns, rows = read_table(required(cfg, 'budget_file'))
    return {'kolonlar': columns, 'satir': len(rows)}


PRODUCTS = {
    'sac-planning': ('SAP Analytics Cloud Planning', sac_plan, sac_actual, sac_meta),
    'oracle-epm': ('Oracle Cloud EPM', lambda c, f: epm_slice(c, f, 'grid_file'), lambda c, f: epm_slice(c, f, 'actual_grid_file') if c.get('actual_grid_file') else None, epm_meta),
    'anaplan': ('Anaplan', lambda c, f: anaplan_export(c, f, 'export_id'), lambda c, f: anaplan_export(c, f, 'actual_export_id') if c.get('actual_export_id') else None, anaplan_meta),
    'planning-analytics': ('IBM Planning Analytics', lambda c, f: tm1_mdx(c, f, 'mdx_file'), lambda c, f: tm1_mdx(c, f, 'actual_mdx_file') if c.get('actual_mdx_file') else None, tm1_meta),
    'adaptive-planning': ('Workday Adaptive Planning', lambda c, f: adaptive_export(c, f, 'budget_version'),
                          lambda c, f: adaptive_export(c, f, 'actual_version') if c.get('actual_version') else None, adaptive_meta),
    'board': ('Board', lambda c, f: board_file(c, f, 'budget_file'), lambda c, f: board_file(c, f, 'actual_file') if c.get('actual_file') else None, board_meta),
}


def aggregate(rows, keys, measure):
    out = defaultdict(float)
    for r in rows:
        v = number(r.get(measure))
        if v is not None:
            out[tuple(str(r.get(k, '')).strip() for k in keys)] += v
    return out


def variance(cfg, out, factory):
    key = cfg['operation'].rsplit('-budget-variance', 1)[0]
    label, plan_reader, actual_reader, _ = PRODUCTS[key]
    budget = plan_reader(cfg, factory)
    actual = actual_reader(cfg, factory)
    if actual is None:
        _, actual = read_table(required(cfg, 'actuals_file'))
    if not budget:
        raise NativeError('Planlama aracı bütçe verisi döndürmedi.')
    keys = names(cfg, 'keys', 'keys (eşleşme boyutları)', limit=8, pattern=FIELD)
    measure, actual_measure = cfg.get('measure', 'deger'), cfg.get('actual_measure') or cfg.get('measure', 'deger')
    for label_, rows, m in (('bütçe', budget, measure), ('gerçekleşen', actual, actual_measure)):
        missing = [k for k in keys + [m] if rows and k not in rows[0]]
        if missing:
            raise NativeError(f'{label_} verisinde kolon yok: ' + ', '.join(missing))
    b, a = aggregate(budget, keys, measure), aggregate(actual, keys, actual_measure)
    tolerance = opt_float(cfg, 'tolerance_pct', 5.0, 0, 1000)
    rows = []
    for k in sorted(set(b) | set(a)):
        bv, av = b.get(k), a.get(k)
        diff = (av or 0) - (bv or 0)
        pct = round(diff / bv * 100, 2) if bv else None
        status = 'yalnız bütçede' if av is None else 'yalnız gerçekleşende' if bv is None else 'eşik üstü sapma' if pct is not None and abs(pct) > tolerance else 'eşik içinde'
        rows.append({**dict(zip(keys, k)), 'butce': round(bv, 2) if bv is not None else '', 'gerceklesen': round(av, 2) if av is not None else '', 'fark': round(diff, 2),
                     'fark_yuzde': pct if pct is not None else '', 'durum': status})
    write_csv(out, 'butce_sapma.csv', rows, keys + ['butce', 'gerceklesen', 'fark', 'fark_yuzde', 'durum'])
    journal(out, state='verified')
    tb, ta = sum(b.values()), sum(a.values())
    return receipt(out, 'ozet.json', {'urun': label, 'kalem': len(rows), 'butce_toplam': round(tb, 2), 'gerceklesen_toplam': round(ta, 2),
                                      'toplam_fark_yuzde': round((ta - tb) / tb * 100, 2) if tb else None, 'esik_yuzde': tolerance,
                                      'esik_ustu': sum(1 for r in rows if r['durum'] == 'eşik üstü sapma'), 'eslesmeyen': sum(1 for r in rows if r['durum'].startswith('yalnız'))})


# ---------- Forecast model & scenario agent ----------
METHODS = {'surucu_bazli', 'trend', 'mevsimsel', 'yuzde_buyume', 'sifirdan_butce', 'yuvarlanan_tahmin', 'regresyon'}
SCHEMA = ('{"suruculer":[{"ad":"birim fiyat","boyut":"kanıttaki boyut/hesap","tanim":"...","veri_kaynagi":"..."}],'
          '"tahmin_yontemleri":[{"kalem":"kanıttaki hesap/kalem","yontem":"surucu_bazli|trend|mevsimsel|yuzde_buyume|sifirdan_butce|yuvarlanan_tahmin|regresyon","gerekce":"..."}],'
          '"senaryolar":[{"ad":"Kötümser","varsayimlar":[{"surucu":"suruculer içindeki ad","degisim_yuzde":-10}]}],"kontroller":["..."],"sorular":["..."]}')


def scenario_design(cfg, out, factory):
    key = cfg['operation'].rsplit('-scenario-design', 1)[0]
    label, plan_reader, _, meta_reader = PRODUCTS[key]
    budget = plan_reader(cfg, factory)
    meta = meta_reader(cfg, factory)
    keys = names(cfg, 'keys', limit=8, pattern=FIELD)
    measure = cfg.get('measure', 'deger')
    members = defaultdict(set)
    for r in budget:
        for k in keys:
            members[k].add(str(r.get(k, '')))
    known = {m.lower() for v in members.values() for m in v} | {k.lower() for k in keys}
    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            known.add(v.lower())
    walk(meta)
    totals = aggregate(budget, keys[:1], measure)
    evidence = {'boyut_uyeleri': {k: sorted(v)[:200] for k, v in members.items()}, 'ilk_boyut_toplamlari': [{'uye': k[0], 'toplam': round(v, 2)} for k, v in sorted(totals.items(), key=lambda x: -abs(x[1]))][:100],
                'model': meta}

    def validate(obj):
        drivers = set()
        for d in need_list(obj, 'suruculer', 'sürücüler', maximum=40):
            drivers.add(need_text(d, 'ad', 'sürücü').lower())
            if str(d.get('boyut', '')).lower() not in known:
                raise ValueError(f'sürücü {d.get("ad")}: kanıtta olmayan boyut/hesap: {d.get("boyut")}')
        for i, t in enumerate(need_list(obj, 'tahmin_yontemleri', 'tahmin yöntemleri', maximum=200), 1):
            if str(t.get('kalem', '')).lower() not in known:
                raise ValueError(f'yöntem {i}: kanıtta olmayan kalem: {t.get("kalem")}')
            need_text(t, 'yontem', f'yöntem {i}', choices=METHODS)
        for s in need_list(obj, 'senaryolar', 'senaryolar', maximum=10):
            need_text(s, 'ad', 'senaryo')
            for v in s.get('varsayimlar') or []:
                if str(v.get('surucu', '')).lower() not in drivers:
                    raise ValueError(f'senaryo {s["ad"]}: tanımsız sürücü: {v.get("surucu")}')
                p = v.get('degisim_yuzde')
                if isinstance(p, bool) or not isinstance(p, (int, float)) or not -100 <= p <= 500:
                    raise ValueError(f'senaryo {s["ad"]}: degisim_yuzde -100..500 olmalı.')
        return obj

    def render(result):
        flat = [{'senaryo': s['ad'], 'surucu': v['surucu'], 'degisim_yuzde': v['degisim_yuzde']} for s in result['senaryolar'] for v in s.get('varsayimlar') or []]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['senaryo', 'surucu', 'degisim_yuzde'], lineterminator='\n')
        w.writeheader(); w.writerows(flat)
        return {'senaryo_tasarimi.json': plan_json(result), 'senaryo_varsayimlari.csv': buf.getvalue(),
                'senaryo_tasarimi.md': rows_md('Tahmin modeli ve senaryo tasarımı — ' + label,
                                               [('Sürücüler', result['suruculer'], ['ad', 'boyut', 'tanim', 'veri_kaynagi']),
                                                ('Tahmin yöntemleri', result['tahmin_yontemleri'], ['kalem', 'yontem', 'gerekce']),
                                                ('Senaryo varsayımları', flat, ['senaryo', 'surucu', 'degisim_yuzde']),
                                                ('Kontroller ve sorular', [{'madde': x} for x in result.get('kontroller', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Tahmin modeli ve senaryo tasarımı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Sürücüleri ve kalemleri yalnızca kanıttaki boyut üyeleri ve model öğelerinden seç; büyük toplamlı kalemlere öncelik ver. '
                            'Senaryo değişim yüzdelerini varsayım olarak işaretle, kanıt yoksa soru sor.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-budget-variance'] = variance
    HANDLERS[_key + '-scenario-design'] = scenario_design
