"""Analytics engineering: SQL model inventory & lineage from each tool's own project artifacts (code) and an evidence-bound
transformation model & test strategy that is rendered into the tool's native test syntax (agent)."""
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, ident, inventory_task, journal, name, names, need_list, need_text, opt_int, plan_json,
                           receipt, required, rows_md, secret, sql_refs, write_text)


# ---------- dbt (manifest.json from dbt Core or dbt Cloud) ----------
def from_manifest(manifest):
    nodes = manifest.get('nodes') or {}
    if not nodes:
        raise NativeError('manifest.json içinde düğüm yok (dbt parse/compile çalıştırıldı mı?).')
    names_ = {uid: n.get('name') for uid, n in nodes.items()}
    names_.update({uid: f"{s.get('source_name')}.{s.get('name')}" for uid, s in (manifest.get('sources') or {}).items()})
    models, tests = [], defaultdict(list)
    for uid, n in nodes.items():
        if n.get('resource_type') == 'test':
            meta = n.get('test_metadata') or {}
            target = next((names_.get(d) for d in (n.get('depends_on') or {}).get('nodes', []) if d.startswith(('model.', 'seed.', 'snapshot.'))), None)
            if target:
                tests[target].append({'tur': meta.get('name') or 'tekil test', 'kolon': n.get('column_name') or (meta.get('kwargs') or {}).get('column_name') or ''})
            continue
        if n.get('resource_type') not in ('model', 'snapshot', 'seed'):
            continue
        models.append({'model': n.get('name'), 'tur': n.get('resource_type'), 'materyalizasyon': (n.get('config') or {}).get('materialized', ''),
                       'sql': n.get('compiled_code') or n.get('raw_code') or '', 'aciklama': n.get('description', ''),
                       'bagimliliklar': [names_.get(d, d) for d in (n.get('depends_on') or {}).get('nodes', [])],
                       'kolonlar': [{'kolon': c, 'tip': (v or {}).get('data_type') or '', 'aciklama': (v or {}).get('description', '')} for c, v in (n.get('columns') or {}).items()]})
    for m in models:
        m['mevcut_testler'] = tests.get(m['model'], [])
    return models


def dbt_core_models(cfg, factory, out):
    path = Path(cfg.get('manifest_file', 'target/manifest.json'))
    models = from_manifest(json.loads(path.read_text(encoding='utf-8')))
    catalog = Path(cfg.get('catalog_file', 'target/catalog.json'))
    if catalog.exists():
        nodes = json.loads(catalog.read_text(encoding='utf-8')).get('nodes') or {}
        types = {n.get('metadata', {}).get('name', '').lower(): {c.lower(): v.get('type') for c, v in (n.get('columns') or {}).items()} for n in nodes.values()}
        for m in models:
            known = {c['kolon'].lower() for c in m['kolonlar']}
            for col, kind in types.get(m['model'].lower(), {}).items():
                if col not in known:
                    m['kolonlar'].append({'kolon': col, 'tip': kind, 'aciklama': ''})
    return models, {'manifest': path.name}


def dbt_cloud_models(cfg, factory, out):
    server = required(cfg, 'server')
    client = factory(server, {'Authorization': 'Token ' + secret('DBT_CLOUD_TOKEN')})
    account, job = required(cfg, 'account_id'), required(cfg, 'job_id')
    runs = client.json('GET', f'/api/v2/accounts/{ident(account)}/runs/?' + urlencode({'job_definition_id': job, 'status': 10, 'order_by': '-id', 'limit': 1})).get('data') or []
    if not runs:
        raise NativeError('İşin başarılı (status=10) çalışması yok; manifest artefaktı alınamadı.')
    run = runs[0]['id']
    raw, _ = client.request('GET', f'/api/v2/accounts/{ident(account)}/runs/{int(run)}/artifacts/manifest.json', limit=256 * 1024 * 1024)
    journal(out, run_id=run)
    return from_manifest(native.json_object(raw)), {'run_id': run}


# ---------- Dataform ----------
def dataform_models(cfg, factory, out):
    client = bearer(factory, 'https://dataform.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    repo = required(cfg, 'repository')
    if not re.fullmatch(r'projects/[^/]+/locations/[^/]+/repositories/[^/]+', repo):
        raise NativeError('repository projects/P/locations/L/repositories/R biçiminde olmalı.')
    compiled = cfg.get('compilation_result') or ''
    if not compiled:
        listing = client.json('GET', f'/v1/{repo}/compilationResults?' + urlencode({'pageSize': 1, 'orderBy': 'create_time desc'})).get('compilationResults') or []
        if not listing:
            raise NativeError('Depoda derleme sonucu yok; önce Dataform’da derleyin.')
        compiled = listing[0]['name']
    if not compiled.startswith(repo + '/compilationResults/'):
        raise NativeError('compilation_result bu depoya ait değil.')
    actions, token = [], None
    for _ in range(200):
        r = client.json('GET', f'/v1/{compiled}:query?' + urlencode({'pageSize': 1000, **({'pageToken': token} if token else {})}))
        actions += r.get('compilationResultActions', [])
        token = r.get('nextPageToken')
        if not token:
            break
    tgt = lambda t: '.'.join(x for x in ((t or {}).get('schema'), (t or {}).get('name')) if x)
    models, assertions = [], defaultdict(list)
    for a in actions:
        target = tgt(a.get('target'))
        if a.get('assertion'):
            parent = tgt((a['assertion'].get('parentAction') or {}))
            assertions[parent].append({'tur': 'assertion', 'kolon': ''})
            continue
        body = a.get('relation') or a.get('operations')
        if not body:
            continue
        models.append({'model': target, 'tur': 'operations' if a.get('operations') else 'relation', 'materyalizasyon': (body.get('relationType') or '').lower(),
                       'sql': body.get('selectQuery') or '\n'.join(body.get('queries') or []), 'aciklama': (body.get('relationDescriptor') or {}).get('description', 'x'),
                       'bagimliliklar': [tgt(d) for d in body.get('dependencyTargets', [])],
                       'kolonlar': [{'kolon': '.'.join(c.get('path') or []), 'tip': '', 'aciklama': c.get('description', '')} for c in (body.get('relationDescriptor') or {}).get('columns', [])],
                       'dosya': a.get('filePath')})
    for m in models:
        m['mevcut_testler'] = assertions.get(m['model'], [])
    return models, {'compilation_result': compiled}


# ---------- SQLMesh ----------
MODEL_HEAD = re.compile(r'^\s*MODEL\s*\((.*?)\)\s*;', re.S | re.I)


def sqlmesh_models(cfg, factory, out):
    root = Path(required(cfg, 'project_dir'))
    models = []
    for path in sorted((root / 'models').rglob('*.sql')):
        text = path.read_text(encoding='utf-8')
        head = MODEL_HEAD.search(text)
        if not head:
            continue
        props = head.group(1)
        model = re.search(r'\bname\s+([\w.]+)', props, re.I)
        kind = re.search(r'\bkind\s+([A-Z_]+)', props, re.I)
        audits = re.findall(r'\b(not_null|unique_values|accepted_values|number_of_rows|forall|unique_combination_of_columns)\b', props, re.I)
        sql = text[head.end():]
        models.append({'model': model.group(1) if model else path.stem, 'tur': 'sql', 'materyalizasyon': kind.group(1).lower() if kind else '',
                       'sql': sql, 'aciklama': 'x' if re.search(r'\bdescription\b', props, re.I) else '', 'bagimliliklar': [],
                       'kolonlar': [{'kolon': c, 'tip': '', 'aciklama': ''} for c in re.findall(r'^\s*(?:[\w.]+\s+AS\s+)?(\w+)\s*,?\s*(?:--.*)?$', sql.split('FROM')[0], re.I | re.M)
                                    if c.lower() not in ('select', 'distinct')][:200],
                       'mevcut_testler': [{'tur': a.lower(), 'kolon': ''} for a in audits], 'dosya': str(path.relative_to(root))})
    if not models:
        raise NativeError('project_dir/models altında MODEL(...) başlıklı .sql bulunamadı.')
    return models, {'proje': root.name}


# ---------- Coalesce ----------
def coalesce_models(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'COALESCE_TOKEN')
    env = required(cfg, 'environment_id')
    nodes, cursor = [], None
    for _ in range(100):
        r = client.json('GET', f'/api/v1/environments/{ident(env)}/nodes?' + urlencode({'detail': 'true', 'limit': 500, **({'startingFrom': cursor} if cursor else {})}))
        nodes += r.get('data', [])
        cursor = r.get('next')
        if not cursor:
            break
    models = []
    for n in nodes:
        meta = n.get('metadata') or {}
        deps = [f"{d.get('locationName')}.{d.get('nodeName')}" for m in meta.get('sourceMapping') or [] for d in m.get('dependencies') or [] if d.get('nodeName')]
        models.append({'model': f"{n.get('locationName')}.{n.get('name')}", 'tur': n.get('nodeType', ''), 'materyalizasyon': (n.get('config') or {}).get('materializationType', ''),
                       'sql': '\n'.join(m.get('customSQL', {}).get('customSQL', '') if isinstance(m.get('customSQL'), dict) else '' for m in meta.get('sourceMapping') or []),
                       'aciklama': n.get('description', 'x'), 'bagimliliklar': deps,
                       'kolonlar': [{'kolon': c.get('name'), 'tip': c.get('dataType', ''), 'aciklama': c.get('description', '')} for c in meta.get('columns') or []],
                       'mevcut_testler': [{'tur': t.get('name', 'test'), 'kolon': c.get('name')} for c in meta.get('columns') or [] for t in (c.get('tests') or [])]})
    return models, {'ortam': env}


# ---------- Cube ----------
def cube_models(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'CUBE_TOKEN')
    meta = client.json('GET', '/cubejs-api/v1/meta')
    files = {}
    model_dir = cfg.get('model_dir')
    if model_dir and Path(model_dir).is_dir():
        for path in Path(model_dir).rglob('*'):
            if path.suffix in ('.yml', '.yaml', '.js'):
                for block in re.split(r'\n\s*-\s*name:\s*|cube\(\s*[`\'"]', path.read_text(encoding='utf-8'))[1:]:
                    cname = re.match(r'[`\'"]?(\w+)', block).group(1)
                    sql = re.search(r'sql_table:\s*[\'"]?([\w.]+)|sql_table:\s*`([\w.]+)`|sql:\s*[>|]?\s*(.+?)(?:\n\s*\w+:|\Z)', block, re.S)
                    joins = re.findall(r'-\s*name:\s*(\w+)\s*\n\s*sql:|(\w+):\s*\{\s*relationship', block)
                    files[cname] = (('SELECT * FROM ' + (sql.group(1) or sql.group(2))) if sql and (sql.group(1) or sql.group(2)) else (sql.group(3) if sql else ''),
                                    [j[0] or j[1] for j in joins])
    models = []
    for c in meta.get('cubes', []):
        sql, joins = files.get(c.get('name'), ('', []))
        models.append({'model': c.get('name'), 'tur': c.get('type', 'cube'), 'materyalizasyon': 'semantik', 'sql': sql, 'aciklama': c.get('description', ''),
                       'bagimliliklar': joins, 'kolonlar': [{'kolon': x.get('name', '').split('.')[-1], 'tip': x.get('type', ''), 'aciklama': x.get('description', '')}
                                                            for x in c.get('dimensions', []) + c.get('measures', [])], 'mevcut_testler': []})
    if not models:
        raise NativeError('Cube /v1/meta küp döndürmedi.')
    return models, {'kup': len(models)}


PRODUCTS = {'dbt-core': ('dbt Core', dbt_core_models, 'dbt'), 'dbt-cloud': ('dbt Cloud', dbt_cloud_models, 'dbt'), 'dataform': ('Dataform', dataform_models, 'dataform'),
            'sqlmesh': ('SQLMesh', sqlmesh_models, 'sqlmesh'), 'coalesce': ('Coalesce', coalesce_models, 'coalesce'), 'cube': ('Cube', cube_models, 'cube')}


def model_inventory(cfg, out, factory):
    key = cfg['operation'].rsplit('-model-inventory', 1)[0]
    label, reader, _ = PRODUCTS[key]
    models, info = reader(cfg, factory, out)
    receipt(out, 'kaynak_bilgisi.json', info)
    return inventory_task(out, models, source_label=label)


# ---------- Test strategy agent ----------
KINDS = {'unique', 'not_null', 'accepted_values', 'relationships', 'expression', 'row_count', 'freshness', 'unique_combination'}
SCHEMA = ('{"testler":[{"model":"kanıttaki model","kolon":"kanıttaki kolon (model düzeyi testte boş)","tur":"unique|not_null|accepted_values|relationships|expression|row_count|freshness|unique_combination",'
          '"parametre":{"degerler":["..."],"hedef_model":"...","hedef_kolon":"...","ifade":"...","kolonlar":["..."],"min":1},"siddet":"error|warn","gerekce":"..."}],'
          '"model_onerileri":[{"model":"...","oneri":"grain, materyalizasyon, artımlı strateji"}],"sorular":["..."]}')


def dbt_yaml(tests):
    """schema.yml with dbt 1.10+ data_tests/arguments syntax; model-level tests use dbt_utils/dbt_expectations."""
    by_model = defaultdict(lambda: defaultdict(list))
    for t in tests:
        by_model[t['model']][t.get('kolon') or ''].append(t)

    def config(t, indent):
        return [' ' * indent + 'config:', ' ' * (indent + 2) + 'severity: warn'] if t.get('siddet') == 'warn' else []

    lines = ['version: 2', '', 'models:']
    for model, cols in sorted(by_model.items()):
        lines.append(f'  - name: {model.split(".")[-1]}')
        model_tests = cols.pop('', [])
        if model_tests:
            lines.append('    data_tests:')
            for t in model_tests:
                p = t.get('parametre') or {}
                if t['tur'] == 'unique_combination':
                    lines += ['      - dbt_utils.unique_combination_of_columns:', '          arguments:',
                              '            combination_of_columns: [' + ', '.join(p.get('kolonlar', [])) + ']'] + config(t, 10)
                elif t['tur'] == 'expression':
                    lines += ['      - dbt_utils.expression_is_true:', '          arguments:', '            expression: ' + json.dumps(p.get('ifade', ''), ensure_ascii=False)] + config(t, 10)
                elif t['tur'] == 'row_count':
                    lines += ['      - dbt_expectations.expect_table_row_count_to_be_between:', '          arguments:', f"            min_value: {int(p.get('min', 1))}"] + config(t, 10)
        if cols:
            lines.append('    columns:')
        for col, items in sorted(cols.items()):
            lines += [f'      - name: {col}', '        data_tests:']
            for t in items:
                p = t.get('parametre') or {}
                if t['tur'] in ('unique', 'not_null'):
                    extra = config(t, 14)
                    lines += [f'          - {t["tur"]}' + (':' if extra else '')] + extra
                elif t['tur'] == 'accepted_values':
                    lines += ['          - accepted_values:', '              arguments:',
                              '                values: [' + ', '.join(json.dumps(v, ensure_ascii=False) for v in p.get('degerler', [])) + ']'] + config(t, 14)
                elif t['tur'] == 'relationships':
                    lines += ['          - relationships:', '              arguments:', f"                to: ref('{str(p.get('hedef_model', '')).split('.')[-1]}')",
                              f"                field: {p.get('hedef_kolon', '')}"] + config(t, 14)
    return '\n'.join(lines) + '\n'


def dataform_assertions(tests):
    by_model = defaultdict(lambda: {'uniqueKey': [], 'nonNull': [], 'rowConditions': []})
    for t in tests:
        a, p = by_model[t['model']], t.get('parametre') or {}
        if t['tur'] == 'unique':
            a['uniqueKey'].append(t['kolon'])
        elif t['tur'] == 'unique_combination':
            a.setdefault('uniqueKeys', []).append(p.get('kolonlar', []))
        elif t['tur'] == 'not_null':
            a['nonNull'].append(t['kolon'])
        elif t['tur'] == 'accepted_values':
            a['rowConditions'].append(f"{t['kolon']} IN ({', '.join(json.dumps(v, ensure_ascii=False).replace(chr(34), chr(39)) for v in p.get('degerler', []))})")
        elif t['tur'] == 'expression':
            a['rowConditions'].append(p.get('ifade', ''))
    blocks = []
    for model, a in sorted(by_model.items()):
        clean = {k: v for k, v in a.items() if v}
        blocks.append(f'// {model}\nconfig {{\n  assertions: {json.dumps(clean, ensure_ascii=False, indent=2)}\n}}\n')
    return '\n'.join(blocks)


def sqlmesh_audits(tests):
    by_model = defaultdict(list)
    for t in tests:
        p = t.get('parametre') or {}
        if t['tur'] == 'not_null':
            by_model[t['model']].append(f"not_null(columns := ({t['kolon']}))")
        elif t['tur'] == 'unique':
            by_model[t['model']].append(f"unique_values(columns := ({t['kolon']}))")
        elif t['tur'] == 'accepted_values':
            by_model[t['model']].append(f"accepted_values(column := {t['kolon']}, is_in := ({', '.join(repr(v) for v in p.get('degerler', []))}))")
        elif t['tur'] == 'unique_combination':
            by_model[t['model']].append(f"unique_combination_of_columns(columns := ({', '.join(p.get('kolonlar', []))}))")
        elif t['tur'] == 'row_count':
            by_model[t['model']].append(f"number_of_rows(threshold := {int(p.get('min', 1))})")
        elif t['tur'] == 'expression':
            by_model[t['model']].append(f"forall(criteria := ({p.get('ifade', '')}))")
    return '\n'.join(f'-- {m}\naudits (\n  ' + ',\n  '.join(a) + '\n),' for m, a in sorted(by_model.items())) + '\n'


def test_sql(tests):
    out = []
    for t in tests:
        c, m, p = t.get('kolon'), t['model'], t.get('parametre') or {}
        q = {'unique': f'SELECT {c}, COUNT(*) FROM {m} GROUP BY {c} HAVING COUNT(*) > 1', 'not_null': f'SELECT * FROM {m} WHERE {c} IS NULL',
             'accepted_values': f"SELECT * FROM {m} WHERE {c} NOT IN ({', '.join(repr(v) for v in p.get('degerler', []))})",
             'relationships': f"SELECT a.* FROM {m} a LEFT JOIN {p.get('hedef_model')} b ON a.{c} = b.{p.get('hedef_kolon')} WHERE a.{c} IS NOT NULL AND b.{p.get('hedef_kolon')} IS NULL",
             'expression': f"SELECT * FROM {m} WHERE NOT ({p.get('ifade', '')})"}.get(t['tur'])
        if q:
            out.append(f"-- {t['tur']} {m}.{c or ''} (başarısız satırları döndürür)\n{q};")
    return '\n\n'.join(out) + '\n'


def test_strategy(cfg, out, factory):
    key = cfg['operation'].rsplit('-test-strategy', 1)[0]
    label, reader, dialect = PRODUCTS[key]
    models, info = reader(cfg, factory, out)
    index = {m['model'].lower(): m for m in models}
    short = {m['model'].split('.')[-1].lower(): m for m in models}

    def model_of(ref, label_):
        m = index.get(str(ref).lower()) or short.get(str(ref).split('.')[-1].lower())
        if not m:
            raise ValueError(f'{label_}: kanıtta olmayan model: {ref}')
        return m

    def validate(obj):
        for i, t in enumerate(need_list(obj, 'testler', 'testler', maximum=400), 1):
            m = model_of(need_text(t, 'model', f'test {i}'), f'test {i}')
            t['model'] = m['model']
            kind = need_text(t, 'tur', f'test {i}', choices=KINDS)
            cols = {c['kolon'].lower() for c in m['kolonlar']}
            col = str(t.get('kolon') or '')
            p = t.get('parametre') or {}
            if kind in ('unique', 'not_null', 'accepted_values', 'relationships'):
                if not col:
                    raise ValueError(f'test {i}: {kind} için kolon gerekli.')
                if cols and col.lower() not in cols:
                    raise ValueError(f'test {i}: {m["model"]} içinde olmayan kolon: {col}')
            if kind == 'accepted_values' and not (isinstance(p.get('degerler'), list) and p['degerler']):
                raise ValueError(f'test {i}: accepted_values için parametre.degerler gerekli.')
            if kind == 'relationships':
                other = model_of(p.get('hedef_model'), f'test {i} hedefi')
                p['hedef_model'] = other['model']
                ocols = {c['kolon'].lower() for c in other['kolonlar']}
                if not p.get('hedef_kolon') or (ocols and str(p['hedef_kolon']).lower() not in ocols):
                    raise ValueError(f'test {i}: relationships hedef_kolon kanıtta yok.')
            if kind == 'unique_combination':
                bad = [c for c in p.get('kolonlar') or [] if cols and str(c).lower() not in cols]
                if not p.get('kolonlar') or bad:
                    raise ValueError(f'test {i}: unique_combination kolonları geçersiz: ' + ', '.join(map(str, bad)))
            if kind == 'expression' and (not isinstance(p.get('ifade'), str) or re.search(r';|\b(drop|delete|insert|update|alter|grant)\b', p['ifade'], re.I)):
                raise ValueError(f'test {i}: expression tek bir koşul ifadesi olmalı.')
            if t.get('siddet', 'error') not in ('error', 'warn'):
                raise ValueError(f'test {i}: siddet error veya warn olmalı.')
        for r in obj.get('model_onerileri') or []:
            model_of(r.get('model') if isinstance(r, dict) else r, 'model önerisi')
        return obj

    def render(result):
        tests = result['testler']
        files = {'test_stratejisi.json': plan_json(result), 'test_sorgulari.sql': test_sql(tests),
                 'test_stratejisi.md': rows_md('Dönüşüm modeli ve test stratejisi — ' + label,
                                               [('Testler', tests, ['model', 'kolon', 'tur', 'siddet', 'gerekce']),
                                                ('Model önerileri', result.get('model_onerileri') or [], ['model', 'oneri']),
                                                ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
        native_file = {'dbt': ('schema_testleri.yml', dbt_yaml), 'dataform': ('assertions.sqlx.txt', dataform_assertions), 'sqlmesh': ('audits.sql.txt', sqlmesh_audits)}.get(dialect)
        if native_file:
            files[native_file[0]] = native_file[1](tests)
        return files
    evidence = {'kaynak': info, 'modeller': [{'model': m['model'], 'materyalizasyon': m.get('materyalizasyon'), 'bagimliliklar': m.get('bagimliliklar', [])[:30],
                                             'kolonlar': m['kolonlar'][:150], 'mevcut_testler': m.get('mevcut_testler', [])} for m in models[:400]]}
    return agent_task(cfg, out, product=label, title='Dönüşüm modeli ve test stratejisi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Her olgu/boyut modelinin grain anahtarına unique + not_null öner; kanıttaki bağımlılıklar boyunca relationships testleri kur. '
                            'Mevcut testleri tekrar önerme; accepted_values değerlerini uydurma, bilmiyorsan soru olarak sor.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-model-inventory'] = model_inventory
    HANDLERS[_key + '-test-strategy'] = test_strategy
