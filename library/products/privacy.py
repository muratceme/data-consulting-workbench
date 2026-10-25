"""Privacy & data security: mask the columns a platform classified as sensitive in a local extract (code) and an
evidence-bound access & data classification design with policy drafts (agent). No policy is created on the platform."""
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, fold, ident, journal, mask_task, name, names, need_list, need_text, opt_int, plan_json,
                           read_table, receipt, required, rows_md, secret, MASKS)


def finding(table, column, label, source):
    return {'tablo': str(table or ''), 'kolon': str(column or ''), 'siniflandirma': str(label or ''), 'kaynak': source}


# ---------- Microsoft Purview Information Protection (Data Map classifications + sensitivity labels) ----------
def purview_findings(cfg, factory):
    account = name(cfg, 'account', re.compile(r'[a-z0-9-]{3,63}'))
    client = bearer(factory, f'https://{account}.purview.azure.com', 'PURVIEW_ACCESS_TOKEN')
    kind, qn = required(cfg, 'entity_type'), required(cfg, 'qualified_name')
    e = client.json('GET', f'/datamap/api/atlas/v2/entity/uniqueAttribute/type/{ident(kind)}?' + urlencode({'attr:qualifiedName': qn, 'api-version': '2023-09-01'}))
    ent, refs = e.get('entity') or {}, e.get('referredEntities') or {}
    labels = [l.get('labelName') or l.get('name') for l in (ent.get('attributes') or {}).get('sensitivityLabels', []) if isinstance(l, dict)]
    out = []
    for col in (ent.get('relationshipAttributes') or {}).get('columns', []):
        ce = refs.get(col.get('guid')) or {}
        for c in ce.get('classifications', []):
            out.append(finding(qn, (ce.get('attributes') or {}).get('name') or col.get('displayText'), c.get('typeName'), 'Purview sınıflandırması'))
    return out, {'varlik': qn, 'duyarlilik_etiketleri': labels}


# ---------- OneTrust Data Discovery (results export) ----------
def onetrust_findings(cfg, factory):
    columns, rows = read_table(required(cfg, 'results_file'))
    col_h, cls_h, table_h = cfg.get('column_header', 'Column Name'), cfg.get('classification_header', 'Classification'), cfg.get('table_header', 'Table Name')
    missing = [h for h in (col_h, cls_h) if h not in columns]
    if missing:
        raise NativeError('Sonuç dosyasında başlık bulunamadı: ' + ', '.join(missing) + ' (column_header/classification_header ile ayarlayın).')
    return [finding(r.get(table_h), r.get(col_h), r.get(cls_h), 'OneTrust Data Discovery') for r in rows if r.get(col_h) and r.get(cls_h)], {'dosya': Path(cfg['results_file']).name}


# ---------- BigID ----------
def bigid_findings(cfg, factory):
    client = factory(required(cfg, 'server'), {'Authorization': secret('BIGID_TOKEN')})
    obj = required(cfg, 'object_name')
    r = client.json('GET', '/api/v1/data-catalog/object-details/columns?' + urlencode({'object_name': obj}))
    out = []
    for c in r.get('data', r.get('columns', [])):
        attrs = c.get('attribute_list') or c.get('attributes') or []
        for a in attrs:
            label = a.get('attribute_name') or a.get('attribute_original_name') or a.get('name') if isinstance(a, dict) else a
            if label:
                out.append(finding(obj, c.get('column_name') or c.get('name'), label, 'BigID'))
    return out, {'nesne': obj}


# ---------- Immuta ----------
def immuta_findings(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'IMMUTA_API_KEY')
    source = required(cfg, 'data_source_name')
    hits = client.json('GET', '/dataSource?' + urlencode({'searchText': source, 'size': 20})).get('hits', [])
    match = [h for h in hits if h.get('name') == source]
    if len(match) != 1:
        raise NativeError('Veri kaynağı bulunamadı veya birden fazla: ' + source)
    dictionary = client.json('GET', f"/dictionary/{int(match[0]['id'])}")
    out = []
    for col in dictionary.get('metadata', []):
        for t in col.get('tags', []):
            out.append(finding(source, col.get('name'), t.get('name') if isinstance(t, dict) else t, 'Immuta etiketi'))
    return out, {'veri_kaynagi': source, 'id': match[0]['id']}


# ---------- Privacera (Apache Ranger tag store) ----------
def ranger_client(cfg, factory):
    return factory(required(cfg, 'ranger_url'), {'Authorization': native.basic_auth(secret('RANGER_USER'), secret('RANGER_PASSWORD'))})


def privacera_findings(cfg, factory):
    client = ranger_client(cfg, factory)
    service = name(cfg, 'tag_service', re.compile(r'[A-Za-z0-9_-]{1,128}'))
    raw, _ = client.request('GET', '/service/tags/resources?' + urlencode({'serviceName': service}))
    resources = {r['id']: r for r in (json.loads(raw) if raw else [])}
    raw, _ = client.request('GET', '/service/tags/tags')
    tags = {t['id']: t.get('type') for t in (json.loads(raw) if raw else [])}
    raw, _ = client.request('GET', '/service/tags/tagresourcemaps')
    out = []
    for m in (json.loads(raw) if raw else []):
        res = resources.get(m.get('resourceId'))
        if not res:
            continue
        el = res.get('resourceElements') or {}
        value = lambda k: ','.join((el.get(k) or {}).get('values') or [])
        if value('column'):
            out.append(finding('.'.join(x for x in (value('database'), value('table')) if x), value('column'), tags.get(m.get('tagId')), 'Privacera/Ranger etiketi'))
    return out, {'etiket_servisi': service}


# ---------- IBM Guardium (classification results report) ----------
def guardium_findings(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'GUARDIUM_TOKEN')
    body = {'reportName': required(cfg, 'report_name'), 'indexFrom': 1, 'fetchSize': opt_int(cfg, 'fetch_size', 5000, 1, 20000), 'reportParameter': cfg.get('report_parameters') or {}}
    raw, _ = client.request('POST', '/restAPI/online_report', body)
    data = json.loads(raw) if raw else []
    rows = data if isinstance(data, list) else data.get('results') or data.get('Rows') or []
    col_f, cls_f, table_f = cfg.get('column_field', 'Column Name'), cfg.get('classification_field', 'Rule Description'), cfg.get('table_field', 'Table Name')
    return [finding(r.get(table_f), r.get(col_f), r.get(cls_f), 'Guardium sınıflandırıcı') for r in rows if isinstance(r, dict) and r.get(col_f)], {'rapor': cfg['report_name']}


PRODUCTS = {'purview-ip': ('Microsoft Purview Information Protection', purview_findings), 'onetrust': ('OneTrust Data Discovery', onetrust_findings),
            'bigid': ('BigID', bigid_findings), 'immuta': ('Immuta', immuta_findings), 'privacera': ('Privacera', privacera_findings), 'guardium': ('IBM Guardium', guardium_findings)}


def mask_fields(cfg, out, factory):
    key = cfg['operation'].rsplit('-mask-fields', 1)[0]
    label, reader = PRODUCTS[key]
    findings, info = reader(cfg, factory)
    table = (cfg.get('table_filter') or '').lower()
    if table:
        findings = [f for f in findings if table in f['tablo'].lower()]
    receipt(out, 'siniflandirma_kaynagi.json', {**info, 'bulgu': len(findings)})
    return mask_task(cfg, out, findings, source_label=label)


# ---------- Access & classification design agent ----------
ACCESS = {'tam', 'maskeli', 'yok'}
SCHEMA = ('{"siniflar":[{"sinif":"Kişisel","tanim":"...","koruma":"..."}],"kolon_atamalari":[{"tablo":"kanıttaki tablo","kolon":"kanıttaki kolon","sinif":"siniflar içindeki ad","gerekce":"..."}],'
          '"erisim_kurallari":[{"sinif":"...","rol":"iş rolü","erisim":"tam|maskeli|yok","maske":"hash|redact|partial|null","kosul":"..."}],"saklama":[{"sinif":"...","sure":"..."}],"sorular":["..."]}')


def ranger_drafts(result, cfg):
    by_class = defaultdict(list)
    for a in result['kolon_atamalari']:
        by_class[a['sinif']].append(a)
    mask_type = {'hash': 'MASK_HASH', 'redact': 'MASK', 'partial': 'MASK_SHOW_LAST_4', 'null': 'MASK_NULL'}
    policies = []
    for rule in result['erisim_kurallari']:
        if rule['erisim'] != 'maskeli':
            continue
        for a in by_class.get(rule['sinif'], []):
            db, _, tbl = a['tablo'].rpartition('.')
            policies.append({'service': cfg.get('resource_service', 'YOUR_HIVE_SERVICE'), 'name': f"datawork-{a['tablo']}-{a['kolon']}-{rule['rol']}"[:240], 'policyType': 1, 'isEnabled': False,
                             'resources': {'database': {'values': [db or '*']}, 'table': {'values': [tbl]}, 'column': {'values': [a['kolon']]}},
                             'dataMaskPolicyItems': [{'roles': [rule['rol']], 'accesses': [{'type': 'select', 'isAllowed': True}],
                                                      'dataMaskInfo': {'dataMaskType': mask_type.get(rule.get('maske'), 'MASK')}}]})
    return json.dumps(policies, ensure_ascii=False, indent=2)


def access_design(cfg, out, factory):
    key = cfg['operation'].rsplit('-access-design', 1)[0]
    label, reader = PRODUCTS[key]
    findings, info = reader(cfg, factory)
    columns = {}
    for f in findings:
        columns.setdefault((f['tablo'].lower(), f['kolon'].lower()), f)
    extra_file = cfg.get('columns_file')
    if extra_file and Path(extra_file).exists():
        cols, _ = read_table(extra_file)
        for c in cols:
            columns.setdefault((str(cfg.get('table_filter') or '').lower(), c.lower()), finding(cfg.get('table_filter', ''), c, '', 'dosya başlığı'))
    if not columns:
        raise NativeError('Platform sınıflandırma bulgusu döndürmedi.')
    by_col = {k[1]: k for k in columns}
    evidence = {'kaynak': info, 'siniflandirmalar': findings[:1500], 'kolonlar': [{'tablo': v['tablo'], 'kolon': v['kolon']} for v in columns.values()][:2000]}

    def validate(obj):
        classes = {need_text(c, 'sinif', 'sınıf').lower() for c in need_list(obj, 'siniflar', 'sınıflar', maximum=12)}
        for i, a in enumerate(need_list(obj, 'kolon_atamalari', 'kolon atamaları', maximum=3000), 1):
            k = (str(a.get('tablo', '')).lower(), str(a.get('kolon', '')).lower())
            if k not in columns and k[1] not in by_col:
                raise ValueError(f'atama {i}: kanıtta olmayan kolon: {a.get("tablo")}.{a.get("kolon")}')
            if str(a.get('sinif', '')).lower() not in classes:
                raise ValueError(f'atama {i}: tanımsız sınıf: {a.get("sinif")}')
        for i, r in enumerate(need_list(obj, 'erisim_kurallari', 'erişim kuralları', maximum=200), 1):
            if str(r.get('sinif', '')).lower() not in classes:
                raise ValueError(f'kural {i}: tanımsız sınıf: {r.get("sinif")}')
            need_text(r, 'rol', f'kural {i}')
            need_text(r, 'erisim', f'kural {i}', choices=ACCESS)
            if r['erisim'] == 'maskeli' and r.get('maske') not in MASKS:
                raise ValueError(f'kural {i}: maske ' + ', '.join(sorted(MASKS)) + ' olmalı.')
        return obj

    def render(result):
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['tablo', 'kolon', 'sinif', 'gerekce'], extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(result['kolon_atamalari'])
        files = {'erisim_tasarimi.json': plan_json(result), 'kolon_siniflari.csv': buf.getvalue(),
                 'erisim_tasarimi.md': rows_md('Erişim ve veri sınıflandırma tasarımı — ' + label,
                                               [('Sınıflar', result['siniflar'], ['sinif', 'tanim', 'koruma']), ('Erişim kuralları', result['erisim_kurallari'], ['sinif', 'rol', 'erisim', 'maske', 'kosul']),
                                                ('Saklama', result.get('saklama') or [], ['sinif', 'sure']), ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
        if key == 'privacera':
            files['ranger_maskeleme_taslagi.json'] = ranger_drafts(result, cfg)
        return files
    return agent_task(cfg, out, product=label, title='Erişim ve veri sınıflandırma tasarımı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Platform sınıflandırmasını temel al; değiştiriyorsan gerekçede belirt. En az ayrıcalık ilkesine uy, hukuki uygunluk kararı verme; KVKK/GDPR açısından sorulması gerekenleri soru olarak yaz.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-mask-fields'] = mask_fields
    HANDLERS[_key + '-access-design'] = access_design
