"""ERP: source/target reconciliation against a live ERP extract (code) and an evidence-bound data conversion & mapping plan (agent).

Readers use each ERP's own interface: OData (S/4HANA, ECC Gateway, Dynamics 365 Finance, Business Central, IFS Cloud),
Service Layer (Business One), Fusion REST, Odoo JSON-RPC, Workday RaaS, and read-only SQL on the documented tables
of on-premise products (Logo Tiger/GO, Netsis, E-Business Suite). No document is created or changed in the ERP."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, basic, bearer, guard_select, ident, journal, name, names, need_list, need_text, odata_metadata,
                           odata_rows, opt_int, plain, plan_json, profile, read_table, receipt, reconcile_task, required, rows_md, secret, sql_source,
                           write_csv, MAX_ROWS)

ENTITY = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_/]{0,127}')


def fields_of(cfg, key='fields'):
    return names(cfg, key, limit=100, pattern=FIELD, optional=True)


# ---------- OData-based ERPs ----------
def sap_client(cfg, factory):
    import os
    server = required(cfg, 'server')
    if os.environ.get('SAP_ACCESS_TOKEN'):
        return bearer(factory, server, 'SAP_ACCESS_TOKEN', {'sap-client': str(cfg.get('sap_client', ''))} if cfg.get('sap_client') else None)
    return basic(factory, server, 'SAP_USER', 'SAP_PASSWORD', {'sap-client': str(cfg['sap_client'])} if cfg.get('sap_client') else None)


def sap_service(cfg):
    service = required(cfg, 'service')
    if not re.fullmatch(r'/sap/opu/odata4?/[A-Za-z0-9_/;=.-]{3,200}', service):
        raise NativeError('service örn. /sap/opu/odata/sap/API_BUSINESS_PARTNER olmalı.')
    return service.rstrip('/')


def sap_read(cfg, factory):
    client = sap_client(cfg, factory)
    entity = name(cfg, 'entity_set', ENTITY)
    rows = odata_rows(client, sap_service(cfg) + '/' + entity, select=fields_of(cfg), filter_=cfg.get('filter') or None)
    return rows


def sap_meta(cfg, factory):
    client = sap_client(cfg, factory)
    raw, _ = client.request('GET', sap_service(cfg) + '/$metadata', accept='application/xml', limit=128 * 1024 * 1024)
    return odata_metadata(raw, [name(cfg, 'entity_set', ENTITY)])


def b1_login(cfg, factory):
    server = required(cfg, 'server').rstrip('/')
    login = factory(server, {})
    _, headers = login.request('POST', '/b1s/v1/Login', {'CompanyDB': required(cfg, 'company_db'), 'UserName': secret('B1_USER'), 'Password': secret('B1_PASSWORD')})
    cookie = '; '.join(c.split(';', 1)[0] for c in re.split(r',\s*(?=[A-Za-z_]+=)', native.header(headers, 'Set-Cookie') or '') if c.split(';', 1)[0].startswith(('B1SESSION=', 'ROUTEID=')))
    if 'B1SESSION=' not in cookie:
        raise NativeError('Service Layer oturumu açılamadı.')
    return factory(server, {'Cookie': cookie, 'Prefer': 'odata.maxpagesize=500'})


def b1_logout(client):
    try:
        client.request('POST', '/b1s/v1/Logout')
    except NativeError:
        pass


def b1_read(cfg, factory):
    client = b1_login(cfg, factory)
    try:
        return odata_rows(client, '/b1s/v1/' + name(cfg, 'entity_set', ENTITY), select=fields_of(cfg), filter_=cfg.get('filter') or None, v2=False)
    finally:
        b1_logout(client)


def b1_meta(cfg, factory):
    client = b1_login(cfg, factory)
    try:
        raw, _ = client.request('GET', '/b1s/v1/$metadata', accept='application/xml', limit=128 * 1024 * 1024)
    finally:
        b1_logout(client)
    return odata_metadata(raw, [name(cfg, 'entity_set', ENTITY)])


def d365_client(cfg, factory):
    return bearer(factory, required(cfg, 'environment_url'), 'D365_ACCESS_TOKEN')


def d365_read(cfg, factory):
    extra = {'cross-company': 'true'} if cfg.get('cross_company', True) else {}
    return odata_rows(d365_client(cfg, factory), '/data/' + name(cfg, 'entity_set', ENTITY), select=fields_of(cfg), filter_=cfg.get('filter') or None, extra=extra, v2=False)


def d365_meta(cfg, factory):
    raw, _ = d365_client(cfg, factory).request('GET', '/data/$metadata', accept='application/xml', limit=512 * 1024 * 1024)
    return odata_metadata(raw, [name(cfg, 'entity_set', ENTITY)])


def bc_base(cfg):
    tenant, env = required(cfg, 'tenant_id'), name(cfg, 'environment', re.compile(r'[A-Za-z0-9_-]{1,64}'))
    return f'/v2.0/{ident(tenant)}/{ident(env)}/api/v2.0'


def bc_read(cfg, factory):
    client = bearer(factory, 'https://api.businesscentral.dynamics.com', 'BC_ACCESS_TOKEN')
    company = required(cfg, 'company_id')
    return odata_rows(client, f'{bc_base(cfg)}/companies({ident(company)})/{name(cfg, "entity_set", ENTITY)}', select=fields_of(cfg), filter_=cfg.get('filter') or None, v2=False)


def bc_meta(cfg, factory):
    client = bearer(factory, 'https://api.businesscentral.dynamics.com', 'BC_ACCESS_TOKEN')
    raw, _ = client.request('GET', bc_base(cfg) + '/$metadata', accept='application/xml', limit=128 * 1024 * 1024)
    return odata_metadata(raw, [name(cfg, 'entity_set', ENTITY)])


def ifs_path(cfg):
    return '/main/ifsapplications/projection/v1/' + name(cfg, 'projection', ENTITY) + '.svc'


def ifs_read(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'IFS_ACCESS_TOKEN')
    return odata_rows(client, ifs_path(cfg) + '/' + name(cfg, 'entity_set', ENTITY), select=fields_of(cfg), filter_=cfg.get('filter') or None, v2=False)


def ifs_meta(cfg, factory):
    raw, _ = bearer(factory, required(cfg, 'server'), 'IFS_ACCESS_TOKEN').request('GET', ifs_path(cfg) + '/$metadata', accept='application/xml', limit=128 * 1024 * 1024)
    return odata_metadata(raw, [name(cfg, 'entity_set', ENTITY)])


# ---------- Oracle Fusion Cloud ERP ----------
FUSION = '/fscmRestApi/resources/11.13.18.05/'


def fusion_client(cfg, factory):
    import os
    if os.environ.get('FUSION_ACCESS_TOKEN'):
        return bearer(factory, required(cfg, 'server'), 'FUSION_ACCESS_TOKEN')
    return basic(factory, required(cfg, 'server'), 'FUSION_USER', 'FUSION_PASSWORD')


def fusion_read(cfg, factory):
    client = fusion_client(cfg, factory)
    resource = name(cfg, 'resource', ENTITY)
    rows, offset = [], 0
    for _ in range(2000):
        q = {'onlyData': 'true', 'limit': '500', 'offset': str(offset), **({'fields': ','.join(fields_of(cfg))} if fields_of(cfg) else {}), **({'q': cfg['filter']} if cfg.get('filter') else {})}
        r = client.json('GET', FUSION + resource + '?' + urlencode(q))
        items = [{k: plain(v) for k, v in i.items() if not isinstance(v, (dict, list))} for i in r.get('items', []) if isinstance(i, dict)]
        rows += items
        offset += len(items)
        if not r.get('hasMore') or not items or len(rows) > MAX_ROWS:
            return rows
    raise NativeError('Fusion sayfalama sınırı aşıldı.')


def fusion_meta(cfg, factory):
    resource = name(cfg, 'resource', ENTITY)
    r = fusion_client(cfg, factory).json('GET', FUSION + resource + '/describe')
    attrs = ((r.get('Resources') or {}).get(resource) or {}).get('attributes', [])
    if not attrs:
        raise NativeError('describe yanıtında öznitelik yok: ' + resource)
    return [{'varlik': resource, 'alan': a.get('name'), 'tip': a.get('type'), 'zorunlu': bool(a.get('mandatory')), 'anahtar': False,
             'uzunluk': a.get('maxLength') or a.get('precision') or ''} for a in attrs if a.get('updatable', True) or a.get('mandatory')]


# ---------- Odoo (JSON-RPC external API) ----------
def odoo_call(client, service, method, *args):
    r = client.json('POST', '/jsonrpc', {'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': {'service': service, 'method': method, 'args': list(args)}})
    if r.get('error'):
        raise NativeError('Odoo JSON-RPC hata döndürdü (veritabanı, kullanıcı, API anahtarı ve model erişimini kontrol edin).')
    return r.get('result')


def odoo_session(cfg, factory):
    client = factory(required(cfg, 'server'), {})
    db, login, key = required(cfg, 'database'), secret('ODOO_USER'), secret('ODOO_API_KEY')
    uid = odoo_call(client, 'common', 'authenticate', db, login, key, {})
    if not isinstance(uid, int) or uid <= 0:
        raise NativeError('Odoo kimlik doğrulaması başarısız.')
    return lambda method, args, kw: odoo_call(client, 'object', 'execute_kw', db, uid, key, name(cfg, 'model', re.compile(r'[a-z_][a-z0-9_.]{0,63}')), method, args, kw)


def odoo_read(cfg, factory):
    call = odoo_session(cfg, factory)
    domain = cfg.get('domain') or []
    if not isinstance(domain, list):
        raise NativeError('domain Odoo arama alanı listesi olmalı, örn. [["active","=",true]].')
    rows, offset = [], 0
    for _ in range(1000):
        batch = call('search_read', [domain], {'fields': fields_of(cfg) or [], 'limit': 500, 'offset': offset})
        if not isinstance(batch, list):
            raise NativeError('Odoo search_read beklenen listeyi döndürmedi.')
        rows += [{k: (v[1] if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) else plain(v)) for k, v in r.items()} for r in batch]
        offset += len(batch)
        if len(batch) < 500 or len(rows) > MAX_ROWS:
            return rows
    raise NativeError('Odoo sayfalama sınırı aşıldı.')


def odoo_meta(cfg, factory):
    call = odoo_session(cfg, factory)
    fields = call('fields_get', [], {'attributes': ['string', 'type', 'required', 'readonly', 'size']})
    model = cfg['model']
    return [{'varlik': model, 'alan': k, 'tip': v.get('type'), 'zorunlu': bool(v.get('required')), 'anahtar': k == 'id', 'uzunluk': v.get('size') or '', 'etiket': v.get('string')}
            for k, v in sorted((fields or {}).items()) if not v.get('readonly') or v.get('required')]


# ---------- Workday (Report-as-a-Service) ----------
def raas_path(cfg):
    tenant, owner, report = (name(cfg, k, re.compile(r'[A-Za-z0-9_.@-]{1,128}')) for k in ('tenant', 'report_owner', 'report_name'))
    return f'/ccx/service/customreport2/{tenant}/{quote(owner)}/{quote(report)}'


def workday_client(cfg, factory):
    return basic(factory, required(cfg, 'server'), 'WORKDAY_USER', 'WORKDAY_PASSWORD')


def workday_read(cfg, factory):
    params = cfg.get('report_params') or {}
    if not isinstance(params, dict):
        raise NativeError('report_params {ad: değer} olmalı.')
    r = workday_client(cfg, factory).json('GET', raas_path(cfg) + '?' + urlencode({**params, 'format': 'json'}))
    return [{k: plain(v) for k, v in e.items() if not isinstance(v, (dict, list))} for e in r.get('Report_Entry', []) if isinstance(e, dict)]


def workday_meta(cfg, factory):
    raw, _ = workday_client(cfg, factory).request('GET', raas_path(cfg) + '?xsd', accept='application/xml')
    root = ET.fromstring(raw)
    fields = []
    for el in root.iter():
        if el.tag.rsplit('}', 1)[-1] == 'element' and el.get('name') and el.get('name') not in ('Report_Data', 'Report_Entry'):
            fields.append({'varlik': cfg['report_name'], 'alan': el.get('name'), 'tip': (el.get('type') or '').split(':')[-1], 'zorunlu': el.get('minOccurs', '1') != '0',
                           'anahtar': False, 'uzunluk': ''})
    return fields


# ---------- On-premise ERPs over read-only SQL (documented standard tables) ----------
def logo_names(cfg):
    firm, period = str(cfg.get('firm', '')), str(cfg.get('period', ''))
    if not re.fullmatch(r'\d{3}', firm) or not re.fullmatch(r'\d{2}', period):
        raise NativeError('firm üç haneli (ör. 001), period iki haneli (ör. 01) olmalı.')
    return firm, period


LOGO_PRESETS = {
    'cari': lambda f, p: (f'SELECT C.CODE AS kod, C.DEFINITION_ AS unvan, C.TAXNR AS vergi_no, '
                          f'SUM(CASE WHEN L.SIGN = 0 THEN L.AMOUNT WHEN L.SIGN = 1 THEN -L.AMOUNT ELSE 0 END) AS bakiye '
                          f'FROM LG_{f}_CLCARD C LEFT JOIN LG_{f}_{p}_CLFLINE L ON L.CLIENTREF = C.LOGICALREF AND L.CANCELLED = 0 '
                          f'GROUP BY C.CODE, C.DEFINITION_, C.TAXNR', [f'LG_{f}_CLCARD', f'LG_{f}_{p}_CLFLINE']),
    'malzeme': lambda f, p: (f'SELECT CODE AS kod, NAME AS ad, ACTIVE AS aktif FROM LG_{f}_ITEMS', [f'LG_{f}_ITEMS']),
}
NETSIS_PRESETS = {
    'cari': lambda: ('SELECT C.CARI_KOD AS kod, C.CARI_ISIM AS unvan, C.VERGI_NUMARASI AS vergi_no, SUM(ISNULL(H.BORC, 0) - ISNULL(H.ALACAK, 0)) AS bakiye '
                     'FROM TBLCASABIT C LEFT JOIN TBLCAHAR H ON H.CARI_KOD = C.CARI_KOD GROUP BY C.CARI_KOD, C.CARI_ISIM, C.VERGI_NUMARASI', ['TBLCASABIT', 'TBLCAHAR']),
    'stok': lambda: ('SELECT STOK_KODU AS kod, STOK_ADI AS ad FROM TBLSTSABIT', ['TBLSTSABIT']),
}
EBS_PRESETS = {
    'musteri': lambda: ("SELECT ca.account_number AS kod, p.party_name AS unvan FROM hz_cust_accounts ca JOIN hz_parties p ON p.party_id = ca.party_id WHERE ca.status = 'A'",
                        ['HZ_CUST_ACCOUNTS', 'HZ_PARTIES']),
    'tedarikci': lambda: ('SELECT segment1 AS kod, vendor_name AS unvan FROM ap_suppliers', ['AP_SUPPLIERS']),
    'ar_bakiye': lambda: ('SELECT ca.account_number AS kod, SUM(ps.amount_due_remaining) AS bakiye FROM ar_payment_schedules_all ps '
                          'JOIN hz_cust_accounts ca ON ca.cust_account_id = ps.customer_id GROUP BY ca.account_number', ['AR_PAYMENT_SCHEDULES_ALL', 'HZ_CUST_ACCOUNTS']),
}


def preset_sql(key, cfg):
    preset = cfg.get('preset', 'ozel_sorgu')
    if preset == 'ozel_sorgu':
        return guard_select(Path(cfg.get('query_file', 'sorgu.sql')).read_text(encoding='utf-8')), names(cfg, 'target_tables', optional=True)
    table = {'logo-tiger': LOGO_PRESETS, 'logo-go': LOGO_PRESETS, 'netsis': NETSIS_PRESETS, 'ebs': EBS_PRESETS}.get(key, {})
    if preset not in table:
        raise NativeError('preset şunlardan biri olmalı: ' + ', '.join(sorted(table) + ['ozel_sorgu']))
    return table[preset](*logo_names(cfg)) if key.startswith('logo') else table[preset]()


SQL_KIND = {'logo-tiger': 'mssql', 'logo-go': 'mssql', 'netsis': 'mssql', 'ebs': 'oracle'}


def sql_read(key, cfg, factory, out):
    kind = SQL_KIND.get(key) or cfg.get('database_type', 'mssql')
    if kind not in ('mssql', 'oracle', 'postgres'):
        raise NativeError('database_type mssql, oracle veya postgres olmalı.')
    sql, _ = preset_sql(key, cfg)
    return sql_source(kind, cfg, factory, out).query(sql, 'erp_extract')[1]


def sql_meta(key, cfg, factory, out):
    kind = SQL_KIND.get(key) or cfg.get('database_type', 'mssql')
    _, tables = preset_sql(key, cfg)
    tables = tables or names(cfg, 'target_tables', 'target_tables (eşleme hedef tabloları)', limit=20)
    src = sql_source(kind, cfg, factory, out)
    if kind == 'oracle':
        sql = ("SELECT table_name, column_name, data_type, nullable, data_length FROM all_tab_columns WHERE table_name IN ("
               + ', '.join("'" + t.upper() + "'" for t in tables) + ')')
    else:
        sql = ('SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type, IS_NULLABLE AS nullable, CHARACTER_MAXIMUM_LENGTH AS data_length '
               'FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME IN (' + ', '.join("'" + t + "'" for t in tables) + ')')
    _, rows = src.query(sql, 'target_columns')
    return [{'varlik': r.get('table_name'), 'alan': r.get('column_name'), 'tip': r.get('data_type'), 'zorunlu': str(r.get('nullable')).upper() in ('NO', 'N'),
             'anahtar': False, 'uzunluk': r.get('data_length') or ''} for r in rows]


PRODUCTS = {
    's4hana': ('SAP S/4HANA', sap_read, sap_meta), 'ecc': ('SAP ECC (Gateway OData)', sap_read, sap_meta), 'business-one': ('SAP Business One', b1_read, b1_meta),
    'logo-tiger': ('Logo Tiger 3', None, None), 'logo-go': ('Logo GO 3', None, None), 'netsis': ('Logo Netsis', None, None), 'j-platform': ('Logo j-Platform', None, None),
    'fusion': ('Oracle Fusion Cloud ERP', fusion_read, fusion_meta), 'ebs': ('Oracle E-Business Suite', None, None),
    'd365-finance': ('Dynamics 365 Finance', d365_read, d365_meta), 'business-central': ('Business Central', bc_read, bc_meta),
    'odoo': ('Odoo', odoo_read, odoo_meta), 'ifs': ('IFS Cloud', ifs_read, ifs_meta), 'workday': ('Workday', workday_read, workday_meta),
}


def erp_rows(key, cfg, factory, out):
    reader = PRODUCTS[key][1]
    rows = reader(cfg, factory) if reader else sql_read(key, cfg, factory, out)
    if not rows:
        raise NativeError('ERP filtreyle eşleşen kayıt döndürmedi.')
    return rows


def reconcile(cfg, out, factory):
    key = cfg['operation'].rsplit('-reconcile', 1)[0]
    rows = erp_rows(key, cfg, factory, out)
    _, target = read_table(required(cfg, 'target_file'))
    write_csv(out, 'erp_cekimi.csv', rows)
    journal(out, erp_rows=len(rows))
    return reconcile_task(cfg, out, rows, target, source_label=PRODUCTS[key][0], target_label=Path(cfg['target_file']).name)


# ---------- Conversion & mapping agent ----------
TRANSFORMS = {'dogrudan', 'sabit', 'birlestir', 'bol', 'tarih_bicimi', 'sayi_bicimi', 'kod_eslestirme', 'buyuk_kucuk_harf', 'kirp', 'turet'}
SCHEMA = ('{"eslemeler":[{"hedef":"ERP alanı (kanıttaki)","kaynak":["kaynak dosya kolonu"],"donusum":"dogrudan|sabit|birlestir|bol|tarih_bicimi|sayi_bicimi|'
          'kod_eslestirme|buyuk_kucuk_harf|kirp|turet","parametre":"biçim, sabit değer veya kural","aciklama":"..."}],'
          '"eslenmeyen_zorunlular":[{"hedef":"...","neden":"..."}],"kontroller":["mutabakat/kalite kontrolleri"],"sorular":["..."]}')


def meta_of(key, cfg, factory, out):
    reader = PRODUCTS[key][2]
    meta = reader(cfg, factory) if reader else sql_meta(key, cfg, factory, out)
    if not meta:
        raise NativeError('ERP hedef alan tanımları okunamadı.')
    return meta


def mapping_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-mapping-plan', 1)[0]
    label = PRODUCTS[key][0]
    meta = meta_of(key, cfg, factory, out)
    columns, rows = read_table(required(cfg, 'source_file'))
    source_profile = profile(columns, rows)
    targets = {m['alan'].lower(): m for m in meta}
    sources = {c.lower(): c for c in columns}
    required_fields = {m['alan'].lower() for m in meta if m.get('zorunlu') and not m.get('anahtar')}

    def validate(obj):
        maps = need_list(obj, 'eslemeler', 'eşlemeler', maximum=400)
        covered = set()
        for i, m in enumerate(maps, 1):
            target = need_text(m, 'hedef', f'eşleme {i}').lower()
            if target not in targets:
                raise ValueError(f'eşleme {i}: ERP’de olmayan hedef alan: {m["hedef"]}')
            covered.add(target)
            kind = need_text(m, 'donusum', f'eşleme {i}', choices=TRANSFORMS)
            src = m.get('kaynak') or []
            if isinstance(src, str):
                src = [src]
            if kind not in ('sabit', 'turet') and not src:
                raise ValueError(f'eşleme {i}: {kind} için kaynak kolon gerekli.')
            bad = [s for s in src if str(s).lower() not in sources]
            if bad:
                raise ValueError(f'eşleme {i}: kaynak dosyada olmayan kolon: ' + ', '.join(map(str, bad)))
            m['kaynak'] = [sources[str(s).lower()] for s in src]
        listed = {str(x.get('hedef', '')).lower() for x in obj.get('eslenmeyen_zorunlular') or [] if isinstance(x, dict)}
        missing = sorted(required_fields - covered - listed)
        if missing:
            raise ValueError('Zorunlu ERP alanları ne eşlendi ne de eslenmeyen_zorunlular’da gerekçelendirildi: ' + ', '.join(targets[x]['alan'] for x in missing[:15]))
        return obj

    def render(result):
        lengths = {p['kolon']: p.get('max_uzunluk') for p in source_profile}
        rows_ = []
        for m in result['eslemeler']:
            t = targets[m['hedef'].lower()]
            limit = int(t['uzunluk']) if str(t.get('uzunluk') or '').isdigit() else None
            longest = max((lengths.get(s) or 0 for s in m['kaynak']), default=0)
            risk = f'kaynakta {longest} karakter > hedef {limit}' if limit and m['donusum'] in ('dogrudan', 'birlestir') and longest > limit else ''
            rows_.append({'hedef': t['alan'], 'hedef_tipi': t['tip'], 'zorunlu': 'evet' if t.get('zorunlu') else '', 'kaynak': ', '.join(m['kaynak']),
                          'donusum': m['donusum'], 'parametre': m.get('parametre', ''), 'uzunluk_riski': risk, 'aciklama': m.get('aciklama', '')})
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows_[0]), lineterminator='\n')
        w.writeheader(); w.writerows(rows_)
        unmapped_sources = sorted(set(columns) - {s for m in result['eslemeler'] for s in m['kaynak']})
        return {'esleme_plani.json': plan_json(result), 'esleme.csv': buf.getvalue(),
                'esleme_plani.md': rows_md(f'ERP veri dönüşümü ve eşleme planı — {label}',
                                           [('Eşlemeler', rows_, ['hedef', 'hedef_tipi', 'zorunlu', 'kaynak', 'donusum', 'parametre', 'uzunluk_riski']),
                                            ('Eşlenmeyen zorunlu alanlar', result.get('eslenmeyen_zorunlular') or [], ['hedef', 'neden']),
                                            ('Kullanılmayan kaynak kolonlar', [{'kolon': c} for c in unmapped_sources], ['kolon']),
                                            ('Kontroller ve sorular', [{'madde': x} for x in result.get('kontroller', []) + result.get('sorular', [])], ['madde'])])}
    evidence = {'erp_hedef_alanlari': meta[:1500], 'kaynak_dosya': {'ad': Path(cfg['source_file']).name, 'satir': len(rows), 'kolon_profili': source_profile}}
    return agent_task(cfg, out, product=label, title='ERP veri dönüşümü ve eşleme planı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Her zorunlu ERP alanını ya eşle ya da eslenmeyen_zorunlular’da gerekçelendir. Para birimi, şirket kodu, dönem ve kod listeleri için kod_eslestirme kullan; '
                            'kaynak değer örneği uydurma (yalnızca profil var).')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-reconcile'] = reconcile
    HANDLERS[_key + '-mapping-plan'] = mapping_plan
