"""dbt proje denetimi: target/manifest.json (isteğe bağlı catalog.json) dosyasından dokümantasyon ve test kapsamını,
katman kurallarını (stg/int/fct/dim), sabit tablo adlarını, kaynak freshness tanımlarını ve DAG derinliğini çıkarır.
`dbt parse` veya `dbt docs generate` çıktısıyla çalışır; veritabanına bağlanmaz. İnternete bağlanmaz.
Çıktı: dbt-denetim.md, modeller.csv, audit.json."""
import argparse, json, re
from collections import defaultdict
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

HARDCODED = re.compile(r'\b(?:from|join)\s+(?!\{\{)([A-Za-z_][\w$]*\.[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\b', re.I)


def load(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'Dosya bulunamadı: {p}. `dbt parse` sonrası target/manifest.json dosyasını verin.')
    data = json.loads(p.read_text(encoding='utf-8'))
    if 'nodes' not in data:
        raise ValueError('Bu bir dbt manifest.json dosyası değil ("nodes" yok).')
    return data


def layer(name):
    for prefix, label in (('stg_', 'staging'), ('base_', 'staging'), ('int_', 'intermediate'), ('fct_', 'mart'), ('dim_', 'mart'), ('mart_', 'mart'), ('rpt_', 'mart')):
        if name.startswith(prefix):
            return label
    return 'diğer'


def audit(manifest, catalog=None):
    nodes = manifest['nodes']
    sources = manifest.get('sources', {})
    models = {k: v for k, v in nodes.items() if v.get('resource_type') == 'model'}
    tests = [v for v in nodes.values() if v.get('resource_type') == 'test']
    tests_by_model = defaultdict(list)
    for t in tests:
        attached = t.get('attached_node') or next((d for d in t.get('depends_on', {}).get('nodes', []) if d in models), None)
        if attached:
            meta = t.get('test_metadata') or {}
            tests_by_model[attached].append({'name': meta.get('name') or t.get('name', ''), 'column': t.get('column_name') or (meta.get('kwargs') or {}).get('column_name')})
    children = defaultdict(set)
    for k, v in models.items():
        for d in v.get('depends_on', {}).get('nodes', []):
            children[d].add(k)
    exposures = manifest.get('exposures', {})
    exposed = {d for e in exposures.values() for d in e.get('depends_on', {}).get('nodes', [])}
    depth_cache = {}

    def depth(uid, trail=()):
        if uid in depth_cache:
            return depth_cache[uid]
        if uid in trail:
            return 0
        parents = [d for d in models.get(uid, {}).get('depends_on', {}).get('nodes', []) if d in models]
        depth_cache[uid] = 1 + max((depth(p, trail + (uid,)) for p in parents), default=0)
        return depth_cache[uid]

    rows, findings = [], []
    for uid, m in sorted(models.items(), key=lambda kv: kv[1].get('name', '')):
        name = m.get('name', uid)
        declared = m.get('columns', {}) or {}
        documented = [c for c, info in declared.items() if (info or {}).get('description')]
        actual = None
        if catalog and uid in catalog.get('nodes', {}):
            actual = [c.lower() for c in catalog['nodes'][uid].get('columns', {})]
        tlist = tests_by_model.get(uid, [])
        tested_cols = defaultdict(set)
        for t in tlist:
            if t['column']:
                tested_cols[t['column'].lower()].add(t['name'])
        has_pk = any({'unique', 'not_null'} <= names for names in tested_cols.values()) or any(t['name'] in ('unique_combination_of_columns', 'dbt_utils.unique_combination_of_columns') for t in tlist)
        deps = m.get('depends_on', {}).get('nodes', [])
        src_parents = [d for d in deps if d.startswith('source.')]
        model_parents = [d for d in deps if d in models]
        lyr = layer(name)
        issues = []
        if not (m.get('description') or '').strip():
            issues.append('açıklama yok')
        if not tlist:
            issues.append('test yok')
        elif not has_pk:
            issues.append('anahtar testi (unique + not_null) yok')
        if lyr == 'staging' and model_parents:
            issues.append('staging başka modele bağlı')
        if lyr == 'staging' and len(src_parents) > 1:
            issues.append('staging birden çok kaynağı birleştiriyor')
        if lyr == 'mart' and src_parents:
            issues.append('mart doğrudan kaynak okuyor')
        code = m.get('raw_code') or m.get('raw_sql') or ''
        stripped = re.sub(r'\{#.*?#\}', '', re.sub(r'--[^\n]*', '', code), flags=re.S)
        hard = sorted(set(HARDCODED.findall(stripped)))
        if hard:
            issues.append('sabit tablo adı: ' + ', '.join(hard))
        undocumented_actual = [c for c in (actual or []) if c not in {d.lower() for d in documented}]
        rows.append({'model': name, 'katman': lyr, 'materialization': (m.get('config') or {}).get('materialized', ''),
                     'aciklama': 'evet' if (m.get('description') or '').strip() else 'hayır',
                     'dokumante_sutun': f"{len(documented)}/{len(actual) if actual is not None else len(declared)}",
                     'test_sayisi': len(tlist), 'derinlik': depth(uid), 'ust': len(deps), 'alt': len(children.get(uid, ())),
                     'exposure': 'evet' if uid in exposed else '', 'sorunlar': '; '.join(issues),
                     'belgesiz_sutunlar': ', '.join(undocumented_actual[:20])})
    used_sources = {d for m in models.values() for d in m.get('depends_on', {}).get('nodes', []) if d.startswith('source.')}
    source_rows = []
    for uid, s in sorted(sources.items()):
        fresh = s.get('freshness') or {}
        has_fresh = bool((fresh.get('warn_after') or {}).get('count') or (fresh.get('error_after') or {}).get('count'))
        source_rows.append({'source': f"{s.get('source_name', '')}.{s.get('name', '')}", 'freshness': 'evet' if has_fresh else 'hayır',
                            'loaded_at_field': s.get('loaded_at_field') or '', 'kullaniliyor': 'evet' if uid in used_sources else 'hayır',
                            'aciklama': 'evet' if (s.get('description') or '').strip() else 'hayır'})
    total = len(rows)
    summary = {
        'project': (manifest.get('metadata') or {}).get('project_name') or '', 'dbt_version': (manifest.get('metadata') or {}).get('dbt_version', ''),
        'models': total, 'tests': len(tests), 'sources': len(sources), 'exposures': len(exposures),
        'documented_models_pct': round(100 * sum(r['aciklama'] == 'evet' for r in rows) / total, 1) if total else 0,
        'tested_models_pct': round(100 * sum(r['test_sayisi'] > 0 for r in rows) / total, 1) if total else 0,
        'max_depth': max((r['derinlik'] for r in rows), default=0),
        'sources_without_freshness': sum(r['freshness'] == 'hayır' for r in source_rows),
        'unused_sources': sum(r['kullaniliyor'] == 'hayır' for r in source_rows),
    }
    leaf = [r['model'] for r in rows if not r['alt'] and not r['exposure'] and r['katman'] != 'mart']
    if leaf:
        findings.append(f"Hiçbir yerde kullanılmayan staging/ara model: {', '.join(leaf)}")
    return summary, rows, source_rows, findings


def markdown(summary, rows, source_rows, findings):
    problems = [r for r in rows if r['sorunlar']]
    lines = [f"# dbt denetimi — {summary['project'] or 'proje'}", '',
             f"- dbt {summary['dbt_version'] or '—'} · Model: **{summary['models']}** · Test: **{summary['tests']}** · Kaynak: **{summary['sources']}** · Exposure: **{summary['exposures']}**",
             f"- Açıklaması olan model: **%{summary['documented_models_pct']}** · Testi olan model: **%{summary['tested_models_pct']}** · En uzun zincir: **{summary['max_depth']}** model",
             f"- Freshness tanımsız kaynak: **{summary['sources_without_freshness']}** · Kullanılmayan kaynak: **{summary['unused_sources']}**", '']
    if findings:
        lines += ['## Genel bulgular', ''] + [f'- {f}' for f in findings] + ['']
    lines += ['## Sorunlu modeller', '', md_table(['Model', 'Katman', 'Sorunlar'], [[r['model'], r['katman'], r['sorunlar']] for r in problems]) if problems else '_Sorunlu model yok._\n',
              '## Tüm modeller', '', md_table(['Model', 'Katman', 'Mat.', 'Açıklama', 'Belgeli sütun', 'Test', 'Derinlik', 'Alt model'],
                                               [[r['model'], r['katman'], r['materialization'], r['aciklama'], r['dokumante_sutun'], r['test_sayisi'], r['derinlik'], r['alt']] for r in rows]),
              '## Kaynaklar', '', md_table(['Kaynak', 'Freshness', 'loaded_at_field', 'Kullanılıyor', 'Açıklama'],
                                           [[s['source'], s['freshness'], s['loaded_at_field'], s['kullaniliyor'], s['aciklama']] for s in source_rows])]
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='dbt manifest.json denetimi (internet ve veritabanı bağlantısı yok).')
    p.add_argument('--manifest', default=str(here('ornek', 'manifest.json')))
    p.add_argument('--catalog', help='İsteğe bağlı target/catalog.json (gerçek sütun listesi için)')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        manifest = load(args.manifest)
        catalog = json.loads(Path(args.catalog).read_text(encoding='utf-8')) if args.catalog else None
        summary, rows, source_rows, findings = audit(manifest, catalog)
        out = output_dir(args.output)
        write_json(out / 'audit.json', {'summary': summary, 'models': rows, 'sources': source_rows, 'findings': findings})
        tabular.write_csv(out / 'modeller.csv', list(rows[0]) if rows else ['model'], rows)
        write_text(out / 'dbt-denetim.md', markdown(summary, rows, source_rows, findings))
    except json.JSONDecodeError as exc:
        p.exit(1, f'[X] JSON okunamadı: {exc}\n')
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    bad = sum(1 for r in rows if r['sorunlar'])
    print(f"[{'!' if bad else 'OK'}] {summary['models']} model, {bad} sorunlu; test kapsamı %{summary['tested_models_pct']}. Çıktı: {out / 'dbt-denetim.md'}")


if __name__ == '__main__':
    main()
