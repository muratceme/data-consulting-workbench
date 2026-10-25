"""Master data management: duplicate detection on master records read from the MDM hub (code) and an evidence-bound golden
record & matching strategy whose effect is measured by code on the same records (agent). Nothing is merged."""
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, basic, bearer, dedupe, dedupe_task, ident, journal, name, names, need_list, need_text, odata_rows,
                           opt_int, plain, plan_json, profile, receipt, required, rows_md, secret, MAX_ROWS)

FIELD = re.compile(r'[^\x00-\x1f]{1,128}')


def limit_of(cfg):
    return opt_int(cfg, 'max_records', 20000, 1, MAX_ROWS)


def flat(record, prefix=''):
    out = {}
    for k, v in (record or {}).items():
        if isinstance(v, dict):
            out.update(flat(v, f'{prefix}{k}.'))
        elif isinstance(v, list):
            if v and isinstance(v[0], dict) and 'value' in v[0]:
                out[f'{prefix}{k}'] = plain(v[0].get('value'))
            elif v and not isinstance(v[0], (dict, list)):
                out[f'{prefix}{k}'] = plain(v[0])
        else:
            out[f'{prefix}{k}'] = plain(v)
    return out


# ---------- SAP Master Data Governance (business partner OData on S/4HANA) ----------
def mdg_records(cfg, factory):
    server = required(cfg, 'server')
    extra = {'sap-client': str(cfg['sap_client'])} if cfg.get('sap_client') else None
    client = bearer(factory, server, 'SAP_ACCESS_TOKEN', extra) if os.environ.get('SAP_ACCESS_TOKEN') else basic(factory, server, 'SAP_USER', 'SAP_PASSWORD', extra)
    service = required(cfg, 'service')
    if not re.fullmatch(r'/sap/opu/odata4?/[A-Za-z0-9_/;=.-]{3,200}', service):
        raise NativeError('service örn. /sap/opu/odata/sap/API_BUSINESS_PARTNER olmalı.')
    rows = odata_rows(client, service.rstrip('/') + '/' + name(cfg, 'entity_set'), select=names(cfg, 'fields', limit=50, pattern=FIELD, optional=True),
                      filter_=cfg.get('filter') or None)
    return rows[:limit_of(cfg)], {}


# ---------- Informatica MDM SaaS (Business 360 search) ----------
def b360_records(cfg, factory):
    login = factory(required(cfg, 'login_url'), {})
    r = login.json('POST', '/saas/public/core/v3/login', {'username': secret('IDMC_USER'), 'password': secret('IDMC_PASSWORD')})
    session = required(r.get('userInfo') or {}, 'sessionId')
    base = required(cfg, 'mdm_url')
    client = factory(base, {'IDS-SESSION-ID': session, **({'MODEL_VERSION': str(cfg['model_version'])} if cfg.get('model_version') else {})})
    entity = name(cfg, 'entity_type', re.compile(r'[a-z0-9_.]{2,100}'))
    rows, offset = [], 0
    while len(rows) < limit_of(cfg):
        res = client.json('POST', 'search/public/api/v1/search', {'entityType': entity, 'search': cfg.get('search') or '*', 'recordOffset': offset,
                                                                  'recordsToReturn': 1000}).get('searchResult') or {}
        batch = res.get('records') or []
        rows += [{**{k: plain(v) for k, v in rec.items() if k != '_meta' and not isinstance(v, (dict, list))}, 'businessId': (rec.get('_meta') or {}).get('businessId')} for rec in batch]
        offset += len(batch)
        if not batch or offset >= int(res.get('hits') or 0):
            break
    return rows[:limit_of(cfg)], {'varlik': entity}


# ---------- Semarchy xDM ----------
def semarchy_records(cfg, factory):
    client = factory(required(cfg, 'server'), {'API-Key': secret('SEMARCHY_API_KEY')})
    location, entity = name(cfg, 'data_location'), name(cfg, 'entity')
    view = cfg.get('view', 'GD')
    if view not in ('GD', 'MD', 'SD', 'MI'):
        raise NativeError('view GD, MD, SD veya MI olmalı.')
    rows, offset = [], 0
    while len(rows) < limit_of(cfg):
        raw, _ = client.request('GET', f'/semarchy/api/rest/query/{quote(location)}/{quote(entity)}/{view}?' + urlencode({'$limit': 1000, '$offset': offset}))
        data = json.loads(raw) if raw else {}
        batch = data.get('records', data) if isinstance(data, dict) else data
        rows += [flat(b) for b in batch if isinstance(b, dict)]
        offset += len(batch)
        if len(batch) < 1000:
            break
    return rows[:limit_of(cfg)], {'gorunum': view}


# ---------- Reltio ----------
def reltio_client(cfg, factory):
    env = name(cfg, 'environment', re.compile(r'[a-z0-9-]{2,40}'))
    return bearer(factory, f'https://{env}.reltio.com', 'RELTIO_ACCESS_TOKEN'), f"/reltio/api/{ident(required(cfg, 'tenant'))}"


def reltio_records(cfg, factory):
    client, base = reltio_client(cfg, factory)
    etype = name(cfg, 'entity_type', re.compile(r'[A-Za-z0-9_]{1,100}'))
    rows, offset = [], 0
    while len(rows) < limit_of(cfg):
        q = {'filter': f"equals(type,'configuration/entityTypes/{etype}')", 'select': 'uri,attributes', 'max': 200, 'offset': offset}
        raw, _ = client.request('GET', f'{base}/entities?' + urlencode(q))
        batch = json.loads(raw) if raw else []
        rows += [{'uri': e.get('uri'), **flat(e.get('attributes') or {})} for e in batch]
        offset += len(batch)
        if len(batch) < 200:
            break
    return rows[:limit_of(cfg)], {'varlik_tipi': etype}


def reltio_config(cfg, factory):
    client, base = reltio_client(cfg, factory)
    conf = client.json('GET', f'{base}/configuration/entityTypes/{ident(cfg["entity_type"])}')
    return {'eslesme_gruplari': [{'uri': g.get('uri'), 'tur': g.get('type'), 'kural': json.dumps(g.get('rule') or {}, ensure_ascii=False)[:1500]} for g in conf.get('matchGroups', [])],
            'hayatta_kalma': [{'ozellik': s.get('attribute'), 'strateji': s.get('survivorshipStrategy')} for g in conf.get('survivorshipGroups', []) for s in g.get('mapping', [])][:200]}


# ---------- TIBCO EBX ----------
def ebx_records(cfg, factory):
    client = basic(factory, required(cfg, 'server'), 'EBX_USER', 'EBX_PASSWORD')
    dataspace, dataset = name(cfg, 'dataspace'), name(cfg, 'dataset')
    table = required(cfg, 'table_path')
    if not re.fullmatch(r'(/[A-Za-z_][A-Za-z0-9_]*)+', table):
        raise NativeError('table_path örn. /root/Customers olmalı.')
    path = f'/ebx-dataservices/rest/data/v1/B{quote(dataspace)}/{quote(dataset)}{table}?pageSize=1000'
    rows = []
    for _ in range(500):
        r = client.json('GET', path)
        for row in r.get('rows', []):
            content = row.get('content') or {}
            rows.append({k: plain(v.get('content') if isinstance(v, dict) else v) for k, v in content.items() if not isinstance((v or {}).get('content') if isinstance(v, dict) else v, (dict, list))})
        nxt = (r.get('pagination') or {}).get('nextPage')
        if not nxt or len(rows) >= limit_of(cfg):
            break
        path = nxt
    return rows[:limit_of(cfg)], {}


# ---------- Stibo STEP (STEPXML export) ----------
def step_records(cfg, factory):
    path = Path(required(cfg, 'stepxml_file'))
    kind = cfg.get('object_kind', 'Product')
    rows = []
    for _, el in ET.iterparse(str(path), events=('end',)):
        if el.tag.rsplit('}', 1)[-1] != kind:
            continue
        rec = {'ID': el.get('ID'), 'UserTypeID': el.get('UserTypeID'), 'Name': next((n.text for n in el if n.tag.rsplit('}', 1)[-1] == 'Name'), '')}
        for values in (v for v in el if v.tag.rsplit('}', 1)[-1] == 'Values'):
            for v in values:
                attr = v.get('AttributeID')
                if attr:
                    rec[attr] = v.text if v.tag.rsplit('}', 1)[-1] == 'Value' else ' '.join(x.text or '' for x in v)
        rows.append(rec)
        el.clear()
        if len(rows) >= limit_of(cfg):
            break
    if not rows:
        raise NativeError(f'STEPXML içinde {kind} nesnesi bulunamadı.')
    return rows, {'dosya': path.name, 'nesne_turu': kind}


PRODUCTS = {'sap-mdg': ('SAP Master Data Governance', mdg_records, None), 'informatica-mdm': ('Informatica MDM SaaS', b360_records, None),
            'semarchy': ('Semarchy xDM', semarchy_records, None), 'reltio': ('Reltio', reltio_records, reltio_config),
            'tibco-ebx': ('TIBCO EBX', ebx_records, None), 'stibo-step': ('Stibo Systems STEP', step_records, None)}


def duplicates(cfg, out, factory):
    key = cfg['operation'].rsplit('-duplicates', 1)[0]
    label, reader, _ = PRODUCTS[key]
    rows, info = reader(cfg, factory)
    fields = names(cfg, 'match_fields', limit=10, pattern=FIELD)
    receipt(out, 'kaynak_bilgisi.json', {**info, 'kayit': len(rows)})
    return dedupe_task(cfg, out, rows, fields=fields, key=cfg.get('id_field') or None, label=label)


COMPARE = {'tam', 'normalize', 'benzerlik'}
SURVIVE = {'en_guncel', 'kaynak_onceligi', 'en_dolu', 'en_sik', 'en_uzun', 'elle'}
SCHEMA = ('{"eslesme_kurallari":[{"ad":"ad+vergi no","alanlar":["kanıttaki alan"],"karsilastirma":"tam|normalize|benzerlik","esik":0.92,'
          '"aksiyon":"otomatik_birlestir|incelemeye_gonder","gerekce":"..."}],"hayatta_kalma":[{"alan":"kanıttaki alan","kural":"en_guncel|kaynak_onceligi|en_dolu|en_sik|en_uzun|elle",'
          '"kaynak_onceligi":["..."],"gerekce":"..."}],"normalizasyon":["unvan kısaltmaları vb."],"sorular":["..."]}')


def golden_record(cfg, out, factory):
    key = cfg['operation'].rsplit('-golden-record', 1)[0]
    label, reader, config_reader = PRODUCTS[key]
    rows, info = reader(cfg, factory)
    if not rows:
        raise NativeError('MDM kayıt döndürmedi.')
    columns = list(dict.fromkeys(k for r in rows[:2000] for k in r))
    prof = profile(columns, rows)
    allowed = {c.lower(): c for c in columns}
    candidates = names(cfg, 'match_fields', limit=10, pattern=FIELD, optional=True)
    probes = []
    for f in candidates:
        if f in columns:
            _, pairs, summary = dedupe(rows, [f], threshold=0.92)
            probes.append({'alanlar': [f], 'birebir_fazla': summary['birebir_fazla_kayit'], 'benzer_cift': summary['benzer_aday_cifti']})
    evidence = {'kaynak': info, 'kayit': len(rows), 'alan_profili': prof, 'alan_denemeleri': probes, **({'platform_ayarlari': config_reader(cfg, factory)} if config_reader else {})}

    def validate(obj):
        for i, r in enumerate(need_list(obj, 'eslesme_kurallari', 'eşleşme kuralları', maximum=20), 1):
            need_text(r, 'ad', f'kural {i}')
            fields = r.get('alanlar')
            if not isinstance(fields, list) or not fields or any(str(f).lower() not in allowed for f in fields):
                raise ValueError(f'kural {i}: alanlar kanıttaki kolonlardan seçilmeli: {fields}')
            r['alanlar'] = [allowed[str(f).lower()] for f in fields]
            need_text(r, 'karsilastirma', f'kural {i}', choices=COMPARE)
            need_text(r, 'aksiyon', f'kural {i}', choices={'otomatik_birlestir', 'incelemeye_gonder'})
            t = r.get('esik', 1.0)
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0.5 <= t <= 1:
                raise ValueError(f'kural {i}: esik 0.5–1 olmalı.')
            if r['aksiyon'] == 'otomatik_birlestir' and r['karsilastirma'] == 'benzerlik' and t < 0.95:
                raise ValueError(f'kural {i}: benzerlikle otomatik birleştirme için eşik en az 0.95 olmalı (yanlış birleştirme riski).')
        for s in obj.get('hayatta_kalma') or []:
            if not isinstance(s, dict) or str(s.get('alan', '')).lower() not in allowed:
                raise ValueError('hayatta kalma kuralı kanıtta olmayan alanı gösteriyor: ' + str(s.get('alan') if isinstance(s, dict) else s))
            need_text(s, 'kural', s['alan'], choices=SURVIVE)
        return obj

    def render(result):
        measured = []
        for r in result['eslesme_kurallari']:
            threshold = 1.0 if r['karsilastirma'] in ('tam', 'normalize') else float(r.get('esik', 0.92))
            _, pairs, summary = dedupe(rows, r['alanlar'], threshold=threshold)
            measured.append({**r, 'alanlar': ', '.join(r['alanlar']), 'olculen_birebir_fazla': summary['birebir_fazla_kayit'], 'olculen_benzer_cift': len(pairs)})
        return {'eslestirme_stratejisi.json': plan_json({**result, 'olculen_etki': measured}),
                'eslestirme_stratejisi.md': rows_md(f'Golden record ve eşleştirme stratejisi — {label} ({len(rows)} kayıt)',
                                                    [('Eşleşme kuralları (etkisi bu kayıtlar üzerinde kodla ölçüldü)', measured,
                                                      ['ad', 'alanlar', 'karsilastirma', 'esik', 'aksiyon', 'olculen_birebir_fazla', 'olculen_benzer_cift']),
                                                     ('Hayatta kalma kuralları', result.get('hayatta_kalma') or [], ['alan', 'kural', 'kaynak_onceligi', 'gerekce']),
                                                     ('Normalizasyon ve sorular', [{'madde': x} for x in result.get('normalizasyon', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=label, title='Golden record ve eşleştirme stratejisi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Benzerliğe dayalı kuralları otomatik birleştirme yerine incelemeye gönder; otomatik birleştirmeyi güçlü tekil anahtarlara (vergi no, e-posta) bırak. '
                            'Hayatta kalma kuralını alanın doluluk ve kaynak güvenilirliğine göre gerekçelendir.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-duplicates'] = duplicates
    HANDLERS[_key + '-golden-record'] = golden_record
