"""Governance & data catalog: data dictionary from the catalog's own asset metadata (code) and an evidence-bound data
ownership & governance model (agent). Nothing is written to the catalog."""
import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, dictionary_task, ident, journal, name, names, need_list, need_text, opt_int, plan_json,
                           receipt, required, rows_md, secret)


def asset(table, column='', kind='', description='', owner='', tags=(), term=''):
    return {'tablo': table, 'kolon': column, 'tip': kind or '', 'aciklama': (description or '').strip(), 'sahip': owner or '',
            'etiketler': ', '.join(t for t in tags if t), 'terim': term or ''}


def limit_of(cfg):
    return opt_int(cfg, 'max_assets', 200, 1, 5000)


# ---------- Microsoft Purview (Data Map) ----------
def purview_assets(cfg, factory):
    account = name(cfg, 'account', re.compile(r'[a-z0-9-]{3,63}'))
    client = bearer(factory, f'https://{account}.purview.azure.com', 'PURVIEW_ACCESS_TOKEN')
    version = 'api-version=2023-09-01'
    body = {'keywords': cfg.get('keywords') or '*', 'limit': 50, 'filter': {'and': [{'entityType': cfg.get('entity_type', 'azure_sql_table')}] +
                                                                              ([{'collectionId': cfg['collection_id']}] if cfg.get('collection_id') else [])}}
    hits, token = [], None
    while len(hits) < limit_of(cfg):
        r = client.json('POST', '/datamap/api/search/query?' + version, {**body, **({'continuationToken': token} if token else {})})
        hits += r.get('value', [])
        token = r.get('continuationToken')
        if not token or not r.get('value'):
            break
    rows = []
    for h in hits[:limit_of(cfg)]:
        e = client.json('GET', f"/datamap/api/atlas/v2/entity/guid/{ident(h['id'])}?{version}")
        ent, refs = e.get('entity') or {}, e.get('referredEntities') or {}
        attrs = ent.get('attributes') or {}
        table = attrs.get('qualifiedName') or h.get('qualifiedName')
        owners = ', '.join(c.get('id', '') for c in ((ent.get('contacts') or {}).get('Owner') or []))
        terms = ', '.join(m.get('displayText', '') for m in (ent.get('relationshipAttributes') or {}).get('meanings', []))
        rows.append(asset(table, '', ent.get('typeName'), attrs.get('userDescription') or attrs.get('description'), owners,
                          [c.get('typeName') for c in ent.get('classifications', [])], terms))
        for col in (ent.get('relationshipAttributes') or {}).get('columns', []):
            ce = refs.get(col.get('guid')) or {}
            ca = ce.get('attributes') or {}
            rows.append(asset(table, ca.get('name') or col.get('displayText'), ca.get('data_type') or ca.get('dataType'), ca.get('userDescription') or ca.get('description'),
                              '', [c.get('typeName') for c in ce.get('classifications', [])],
                              ', '.join(m.get('displayText', '') for m in (ce.get('relationshipAttributes') or {}).get('meanings', []))))
    return rows


# ---------- Collibra ----------
COLLIBRA = {'table_type': '00000000-0000-0000-0000-000000031007', 'column_type': '00000000-0000-0000-0000-000000031008',
            'column_of_table': '00000000-0000-0000-0000-000000007042', 'description': '00000000-0000-0000-0000-000000003114'}


def collibra_assets(cfg, factory):
    client = factory(required(cfg, 'server'), {'Authorization': native.basic_auth(secret('COLLIBRA_USER'), secret('COLLIBRA_PASSWORD'))})
    ids = {k: cfg.get(k, v) for k, v in COLLIBRA.items()}
    q = {'typeIds': ids['table_type'], 'limit': 100, **({'communityId': cfg['community_id']} if cfg.get('community_id') else {}),
         **({'domainId': cfg['domain_id']} if cfg.get('domain_id') else {})}
    tables, offset = [], 0
    while len(tables) < limit_of(cfg):
        r = client.json('GET', '/rest/2.0/assets?' + urlencode({**q, 'offset': offset}))
        tables += r.get('results', [])
        offset += 100
        if offset >= int(r.get('total') or 0):
            break

    def description(asset_id):
        r = client.json('GET', '/rest/2.0/attributes?' + urlencode({'assetId': asset_id, 'typeIds': ids['description']}))
        return next((a.get('value') for a in r.get('results', [])), '')

    def owners(asset_id):
        r = client.json('GET', '/rest/2.0/responsibilities?' + urlencode({'resourceIds': asset_id, 'limit': 50}))
        return ', '.join(f"{(x.get('role') or {}).get('name')}: {(x.get('owner') or {}).get('name') or (x.get('owner') or {}).get('id')}" for x in r.get('results', []))

    rows = []
    for t in tables[:limit_of(cfg)]:
        rows.append(asset(t.get('name'), '', 'Table', description(t['id']), owners(t['id']), [(t.get('status') or {}).get('name')], ''))
        rel = client.json('GET', '/rest/2.0/relations?' + urlencode({'targetId': t['id'], 'relationTypeId': ids['column_of_table'], 'limit': 1000}))
        for r in rel.get('results', []):
            col = r.get('source') or {}
            rows.append(asset(t.get('name'), col.get('name'), 'Column', description(col.get('id')), '', [], ''))
    return rows


# ---------- Alation ----------
def alation_assets(cfg, factory):
    client = factory(required(cfg, 'server'), {'TOKEN': secret('ALATION_TOKEN')})
    q = {'limit': 100, **({'schema_id': cfg['schema_id']} if cfg.get('schema_id') else {}), **({'ds_id': cfg['datasource_id']} if cfg.get('datasource_id') else {})}
    tables, skip = [], 0
    while len(tables) < limit_of(cfg):
        raw, _ = client.request('GET', '/integration/v2/table/?' + urlencode({**q, 'skip': skip}))
        batch = json.loads(raw) if raw else []
        tables += batch
        skip += 100
        if len(batch) < 100:
            break
    rows = []
    for t in tables[:limit_of(cfg)]:
        tname = t.get('key') or t.get('name')
        rows.append(asset(tname, '', t.get('table_type', 'TABLE'), re.sub(r'<[^>]+>', ' ', t.get('description') or ''), '', [], ''))
        raw, _ = client.request('GET', '/integration/v2/column/?' + urlencode({'table_id': t['id'], 'limit': 1000}))
        for c in (json.loads(raw) if raw else []):
            rows.append(asset(tname, c.get('name'), c.get('column_type') or c.get('data_type'), re.sub(r'<[^>]+>', ' ', c.get('description') or ''), '', [], ''))
    return rows


# ---------- Google Dataplex Universal Catalog ----------
def dataplex_assets(cfg, factory):
    client = bearer(factory, 'https://dataplex.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    project = name(cfg, 'project_id', re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]'))
    query = cfg.get('query') or 'type=table'
    entries, token = [], None
    while len(entries) < limit_of(cfg):
        r = client.json('POST', f'/v1/projects/{project}/locations/global:searchEntries', {'query': query, 'pageSize': 100, **({'pageToken': token} if token else {})})
        entries += [x.get('dataplexEntry') or {} for x in r.get('results', [])]
        token = r.get('nextPageToken')
        if not token:
            break
    rows = []
    for e in entries[:limit_of(cfg)]:
        full = client.json('GET', f"/v1/{e['name']}?view=ALL")
        src = full.get('entrySource') or {}
        aspects = full.get('aspects') or {}
        schema = next((a.get('data') or {} for k, a in aspects.items() if k.endswith('.global.schema')), {})
        overview = next((a.get('data') or {} for k, a in aspects.items() if k.endswith('.global.overview')), {})
        contacts = next((a.get('data') or {} for k, a in aspects.items() if k.endswith('.global.contacts')), {})
        table = full.get('fullyQualifiedName') or e['name']
        owners = ', '.join(f"{i.get('role')}: {i.get('id') or i.get('name')}" for i in contacts.get('identities', []))
        rows.append(asset(table, '', full.get('entryType', '').rsplit('/', 1)[-1], src.get('description') or overview.get('content', '')[:500], owners, list(src.get('labels', {}).keys()), ''))
        for f in schema.get('fields', []):
            rows.append(asset(table, f.get('name'), f.get('dataType'), f.get('description'), '', [], ''))
    return rows


# ---------- Atlan ----------
def atlan_assets(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'ATLAN_API_TOKEN')
    prefix = required(cfg, 'qualified_name_prefix')
    def search(type_name, extra, attributes):
        found, start = [], 0
        while True:
            dsl = {'from': start, 'size': 100, 'query': {'bool': {'filter': [{'term': {'__typeName.keyword': type_name}}, {'term': {'__state': 'ACTIVE'}}, extra]}}}
            r = client.json('POST', '/api/meta/search/indexsearch', {'dsl': dsl, 'attributes': attributes})
            batch = r.get('entities') or []
            found += batch
            start += 100
            if len(batch) < 100 or len(found) >= limit_of(cfg) * (50 if type_name == 'Column' else 1):
                return found
    attrs = ['name', 'qualifiedName', 'description', 'userDescription', 'ownerUsers', 'ownerGroups', 'meanings', 'dataType', 'tableQualifiedName']
    tables = search('Table', {'prefix': {'qualifiedName': prefix}}, attrs)[:limit_of(cfg)]
    columns = search('Column', {'prefix': {'tableQualifiedName': prefix}}, attrs)
    by_table = defaultdict(list)
    for c in columns:
        by_table[(c.get('attributes') or {}).get('tableQualifiedName')].append(c)
    rows = []
    for t in tables:
        a = t.get('attributes') or {}
        owners = ', '.join((a.get('ownerUsers') or []) + [f'@{g}' for g in a.get('ownerGroups') or []])
        terms = ', '.join(m.get('displayText', '') for m in t.get('meanings') or a.get('meanings') or [] if isinstance(m, dict))
        rows.append(asset(a.get('qualifiedName'), '', 'Table', a.get('userDescription') or a.get('description'), owners, t.get('classificationNames') or [], terms))
        for c in by_table.get(a.get('qualifiedName'), []):
            ca = c.get('attributes') or {}
            rows.append(asset(a.get('qualifiedName'), ca.get('name'), ca.get('dataType'), ca.get('userDescription') or ca.get('description'), '', c.get('classificationNames') or [], ''))
    return rows


# ---------- OpenMetadata ----------
def openmetadata_assets(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'OPENMETADATA_TOKEN')
    q = {'fields': 'columns,owners,tags,domains', 'limit': 100, **({'database': cfg['database_fqn']} if cfg.get('database_fqn') else {}),
         **({'databaseSchema': cfg['schema_fqn']} if cfg.get('schema_fqn') else {})}
    tables, after = [], None
    while len(tables) < limit_of(cfg):
        r = client.json('GET', '/api/v1/tables?' + urlencode({**q, **({'after': after} if after else {})}))
        tables += r.get('data', [])
        after = (r.get('paging') or {}).get('after')
        if not after:
            break
    rows = []
    for t in tables[:limit_of(cfg)]:
        owners = ', '.join(o.get('name', '') for o in (t.get('owners') or ([t['owner']] if t.get('owner') else [])))
        tags = [g.get('tagFQN') for g in t.get('tags', []) if g.get('source') != 'Glossary']
        terms = ', '.join(g.get('tagFQN') for g in t.get('tags', []) if g.get('source') == 'Glossary')
        rows.append(asset(t.get('fullyQualifiedName'), '', t.get('tableType'), t.get('description'), owners, tags, terms))
        for c in t.get('columns', []):
            rows.append(asset(t.get('fullyQualifiedName'), c.get('name'), c.get('dataTypeDisplay') or c.get('dataType'), c.get('description'), '',
                              [g.get('tagFQN') for g in c.get('tags', []) if g.get('source') != 'Glossary'], ', '.join(g.get('tagFQN') for g in c.get('tags', []) if g.get('source') == 'Glossary')))
    return rows


# ---------- DataHub ----------
DATAHUB_Q = ('query($i:SearchAcrossEntitiesInput!){searchAcrossEntities(input:$i){total searchResults{entity{urn ... on Dataset{name platform{name} '
             'properties{description qualifiedName} editableProperties{description} ownership{owners{owner{... on CorpUser{username} ... on CorpGroup{name}}}} '
             'glossaryTerms{terms{term{name}}} tags{tags{tag{name}}} schemaMetadata{fields{fieldPath nativeDataType description}} '
             'editableSchemaMetadata{editableSchemaFieldInfo{fieldPath description}}}}}}}')


def datahub_assets(cfg, factory):
    client = bearer(factory, required(cfg, 'server'), 'DATAHUB_TOKEN')
    filters = [{'field': 'platform', 'values': [f"urn:li:dataPlatform:{name(cfg, 'platform')}"]}] if cfg.get('platform') else []
    results, start = [], 0
    while len(results) < limit_of(cfg):
        data = native.graphql(client, '/api/graphql', DATAHUB_Q, {'i': {'types': ['DATASET'], 'query': cfg.get('query') or '*', 'start': start, 'count': 100,
                                                                       **({'orFilters': [{'and': filters}]} if filters else {})}})
        page = (data.get('searchAcrossEntities') or {})
        results += [r.get('entity') or {} for r in page.get('searchResults', [])]
        start += 100
        if start >= int(page.get('total') or 0):
            break
    rows = []
    for d in results[:limit_of(cfg)]:
        props, edit = d.get('properties') or {}, d.get('editableProperties') or {}
        table = props.get('qualifiedName') or d.get('name') or d.get('urn')
        owners = ', '.join((o.get('owner') or {}).get('username') or (o.get('owner') or {}).get('name') or '' for o in (d.get('ownership') or {}).get('owners', []))
        terms = ', '.join(((t.get('term') or {}).get('name') or '') for t in (d.get('glossaryTerms') or {}).get('terms', []))
        rows.append(asset(table, '', (d.get('platform') or {}).get('name'), edit.get('description') or props.get('description'), owners,
                          [(t.get('tag') or {}).get('name') for t in (d.get('tags') or {}).get('tags', [])], terms))
        edits = {f.get('fieldPath'): f.get('description') for f in (d.get('editableSchemaMetadata') or {}).get('editableSchemaFieldInfo', [])}
        for f in (d.get('schemaMetadata') or {}).get('fields', []):
            rows.append(asset(table, f.get('fieldPath'), f.get('nativeDataType'), edits.get(f.get('fieldPath')) or f.get('description'), '', [], ''))
    return rows


PRODUCTS = {'purview': ('Microsoft Purview', purview_assets), 'collibra': ('Collibra Data Intelligence Cloud', collibra_assets), 'alation': ('Alation', alation_assets),
            'dataplex': ('Dataplex Universal Catalog', dataplex_assets), 'atlan': ('Atlan', atlan_assets), 'openmetadata': ('OpenMetadata', openmetadata_assets),
            'datahub': ('DataHub', datahub_assets)}


def data_dictionary(cfg, out, factory):
    key = cfg['operation'].rsplit('-data-dictionary', 1)[0]
    label, reader = PRODUCTS[key]
    return dictionary_task(out, reader(cfg, factory), source_label=label)


CLASSES = {'genel', 'ic', 'gizli', 'kisisel', 'ozel_nitelikli'}
SCHEMA = ('{"alanlar":[{"alan":"Satış","aciklama":"iş alanı"}],"atamalar":[{"varlik":"kanıttaki tablo","alan":"alanlar içindeki ad","sahip_rolu":"veri sahibi rolü",'
          '"onerilen_sahip":"kanıtta geçen kişi/grup veya boş","steward_rolu":"...","siniflandirma":"genel|ic|gizli|kisisel|ozel_nitelikli","gerekce":"..."}],'
          '"politikalar":[{"ad":"...","kural":"...","sorumlu_rol":"..."}],"sorular":["..."]}')


def governance_model(cfg, out, factory):
    key = cfg['operation'].rsplit('-ownership-model', 1)[0]
    label, reader = PRODUCTS[key]
    rows = reader(cfg, factory)
    if not rows:
        raise NativeError('Katalog seçilen kapsamda varlık döndürmedi.')
    tables = {}
    for r in rows:
        t = tables.setdefault(r['tablo'], {'varlik': r['tablo'], 'aciklama': '', 'sahip': '', 'etiketler': set(), 'terimler': set(), 'kolonlar': [], 'aciklamasiz_kolon': 0})
        if not r['kolon']:
            t.update(aciklama=r['aciklama'][:300], sahip=r['sahip'])
        else:
            t['kolonlar'].append(r['kolon'])
            t['aciklamasiz_kolon'] += not r['aciklama']
        t['etiketler'] |= {x.strip() for x in r['etiketler'].split(',') if x.strip()}
        t['terimler'] |= {x.strip() for x in r['terim'].split(',') if x.strip()}
    people = {p.strip() for t in tables.values() for p in re.split(r',\s*', t['sahip']) if p.strip()}
    people_plain = {re.sub(r'^[^:]+:\s*', '', p).lower(): re.sub(r'^[^:]+:\s*', '', p) for p in people}
    evidence = {'varliklar': [{**t, 'etiketler': sorted(t['etiketler']), 'terimler': sorted(t['terimler']), 'kolonlar': t['kolonlar'][:80]} for t in list(tables.values())[:500]],
                'mevcut_sahipler': sorted(people)}

    def validate(obj):
        domains = {need_text(d, 'alan', 'alan').lower() for d in need_list(obj, 'alanlar', 'iş alanları', maximum=40)}
        for i, a in enumerate(need_list(obj, 'atamalar', 'atamalar', maximum=1000), 1):
            v = need_text(a, 'varlik', f'atama {i}')
            if v not in tables and v.lower() not in {k.lower() for k in tables}:
                raise ValueError(f'atama {i}: katalogda olmayan varlık: {v}')
            if str(a.get('alan', '')).lower() not in domains:
                raise ValueError(f'atama {i}: tanımsız iş alanı: {a.get("alan")}')
            need_text(a, 'sahip_rolu', f'atama {i}')
            need_text(a, 'siniflandirma', f'atama {i}', choices=CLASSES)
            owner = str(a.get('onerilen_sahip') or '').strip()
            if owner and owner.lower() not in people_plain and owner not in people:
                raise ValueError(f'atama {i}: kanıtta olmayan kişi/grup önerilemez (rol yazın veya boş bırakın): {owner}')
        return obj

    def render(result):
        import csv, io
        buf = io.StringIO()
        cols = ['varlik', 'alan', 'sahip_rolu', 'onerilen_sahip', 'steward_rolu', 'siniflandirma', 'gerekce']
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(result['atamalar'])
        unassigned = sorted(set(tables) - {a['varlik'] for a in result['atamalar']})
        return {'yonetisim_modeli.json': plan_json(result), 'sahiplik_atamalari.csv': buf.getvalue(),
                'yonetisim_modeli.md': rows_md('Veri sahipliği ve yönetişim modeli — ' + label,
                                               [('İş alanları', result['alanlar'], ['alan', 'aciklama']), ('Atamalar', result['atamalar'], cols),
                                                ('Atanmamış varlıklar', [{'varlik': v} for v in unassigned], ['varlik']),
                                                ('Politikalar', result.get('politikalar') or [], ['ad', 'kural', 'sorumlu_rol']),
                                                ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
    return agent_task(cfg, out, product=label, title='Veri sahipliği ve yönetişim modeli', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Kişi adı uydurma: yalnızca kanıttaki mevcut sahipleri öner, yoksa rol yaz. Kişisel veri izi taşıyan etiket/kolon adlarını sınıflandırmada gerekçe olarak göster.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-data-dictionary'] = data_dictionary
    HANDLERS[_key + '-ownership-model'] = governance_model
