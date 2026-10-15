"""SQL soy ağacı: bir klasördeki .sql dosyalarından (veya tek dosyadan) hangi ifadenin hangi tabloları okuyup
hangi tabloya yazdığını çıkarır; ham kaynakları, ara tabloları ve son hedefleri ayırır; WHERE'siz UPDATE/DELETE ve
SELECT * kullanımını işaretler. Stored procedure, ETL ve rapor SQL'i devralırken kullanılır. İnternete bağlanmaz.
Çıktı: sql-soyagaci.md, lineage.csv, lineage.json."""
import argparse, re
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

IDENT = r'(?:\[[^\]]+\]|"[^"]+"|`[^`]+`|[A-Za-z_#@][\w$#@]*)'
NAME = rf'{IDENT}(?:\s*\.\s*{IDENT}){{0,3}}'
TARGETS = [
    ('CREATE', re.compile(rf'^\s*CREATE\s+(?:OR\s+REPLACE\s+|OR\s+ALTER\s+)?(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY|TRANSIENT|MATERIALIZED|SECURE|EXTERNAL)\s+)*(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?({NAME})', re.I)),
    ('INSERT', re.compile(rf'\bINSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?({NAME})', re.I)),
    ('MERGE', re.compile(rf'^\s*MERGE\s+(?:INTO\s+)?({NAME})', re.I)),
    ('UPDATE', re.compile(rf'^\s*UPDATE\s+({NAME})', re.I)),
    ('DELETE', re.compile(rf'^\s*DELETE\s+(?:FROM\s+)?({NAME})', re.I)),
    ('TRUNCATE', re.compile(rf'^\s*TRUNCATE\s+TABLE\s+({NAME})', re.I)),
    ('SELECT INTO', re.compile(rf'\bSELECT\b.*?\bINTO\s+({NAME})\s+FROM\b', re.I | re.S)),
]
SOURCE = re.compile(rf'\b(?:FROM|JOIN|USING)\s+(?!\()({NAME})', re.I)
CTE = re.compile(rf'(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)({IDENT})\s*(?:\([^)]*\)\s*)?AS\s*(?:MATERIALIZED\s+)?\(', re.I)
NOT_TABLES = {'dual', 'unnest', 'lateral', 'table', 'values', 'generate_series', 'flatten', 'openjson', 'string_split'}


def clean_sql(text):
    """Remove comments and blank out string literals so they cannot look like table names."""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'--[^\n]*', '', text)
    return re.sub(r"'(?:''|[^'])*'", "''", text)


def norm(name):
    parts = re.split(r'\s*\.\s*', name.strip())
    return '.'.join(p.strip('[]"`') for p in parts).lower()


def split_statements(text):
    parts = re.split(r';|^\s*GO\s*$', text, flags=re.M | re.I)
    return [p.strip() for p in parts if p and p.strip()]


def analyse(sql_text, file_name):
    out = []
    for n, stmt in enumerate(split_statements(clean_sql(sql_text)), start=1):
        kind, target = 'SELECT' if re.match(r'^\s*(WITH|SELECT)\b', stmt, re.I) else None, None
        for label, rx in TARGETS:
            m = rx.search(stmt)
            if m:
                kind, target = label, norm(m.group(1))
                break
        if kind is None:
            continue
        ctes = {norm(c) for c in CTE.findall(stmt)}
        sources = []
        for s in SOURCE.findall(stmt):
            name = norm(s)
            if name in ctes or name in NOT_TABLES or name == target and kind in ('DELETE', 'UPDATE'):
                continue
            if name.startswith('@') and kind != 'SELECT':
                continue
            sources.append(name)
        if kind == 'DELETE' and target in sources:
            sources.remove(target)
        risks = []
        if kind in ('UPDATE', 'DELETE') and not re.search(r'\bWHERE\b', stmt, re.I):
            risks.append(f'WHERE olmadan {kind}')
        if re.search(r'\bSELECT\s+(?:DISTINCT\s+|TOP\s+\d+\s+)?\*', stmt, re.I):
            risks.append('SELECT *')
        if kind == 'TRUNCATE':
            risks.append('TRUNCATE')
        out.append({'file': file_name, 'statement': n, 'kind': kind, 'target': target or '', 'sources': sorted(dict.fromkeys(sources)),
                    'ctes': sorted(ctes), 'risks': risks})
    return out


def collect(path):
    p = Path(path)
    if p.is_dir():
        files = sorted(f for f in p.rglob('*.sql') if f.is_file())
        if not files:
            raise ValueError(f'{p} içinde .sql dosyası yok.')
        return [(str(f.relative_to(p)).replace('\\', '/'), f) for f in files]
    if p.is_file():
        return [(p.name, p)]
    raise ValueError(f'Bulunamadı: {p}')


def summarise(statements):
    written = {s['target'] for s in statements if s['target']}
    read = {src for s in statements for src in s['sources']}
    return {'raw_sources': sorted(read - written), 'intermediate': sorted(read & written), 'final_targets': sorted(written - read)}


def mermaid(statements):
    lines, ids = ['flowchart LR'], {}

    def node(name):
        if name not in ids:
            ids[name] = f't{len(ids)}'
            lines.append(f'  {ids[name]}["{name}"]')
        return ids[name]
    edges = []
    for s in statements:
        if s['target']:
            for src in s['sources']:
                edges.append(f"  {node(src)} --> {node(s['target'])}")
    return '\n'.join(lines + list(dict.fromkeys(edges)))


def markdown(statements, groups, source):
    risky = [s for s in statements if s['risks']]
    lines = [f'# SQL soy ağacı — {source}', '',
             f"- Dosya: **{len({s['file'] for s in statements})}** · İfade: **{len(statements)}** · Yazılan tablo: **{len({s['target'] for s in statements if s['target']})}**",
             f"- Ham kaynak: **{len(groups['raw_sources'])}** · Ara tablo: **{len(groups['intermediate'])}** · Son hedef: **{len(groups['final_targets'])}**", '',
             '## Tablolar', '', md_table(['Grup', 'Tablolar'], [['Ham kaynak (yalnızca okunuyor)', ', '.join(groups['raw_sources']) or '—'],
                                                             ['Ara tablo (yazılıp okunuyor)', ', '.join(groups['intermediate']) or '—'],
                                                             ['Son hedef (yalnızca yazılıyor)', ', '.join(groups['final_targets']) or '—']])]
    if risky:
        lines += ['## Riskler', '', md_table(['Dosya', '#', 'Tür', 'Hedef', 'Risk'], [[s['file'], s['statement'], s['kind'], s['target'], ', '.join(s['risks'])] for s in risky])]
    lines += ['## Akış', '', '```mermaid', mermaid(statements), '```', '',
              '## İfadeler', '', md_table(['Dosya', '#', 'Tür', 'Hedef', 'Kaynaklar', 'CTE'],
                                          [[s['file'], s['statement'], s['kind'], s['target'] or '—', ', '.join(s['sources']), ', '.join(s['ctes'])] for s in statements])]
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='SQL dosyalarından tablo soy ağacı (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'sql')), help='.sql dosyası veya klasör (alt klasörler dahil)')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        statements = []
        for name, f in collect(args.input):
            raw = f.read_bytes()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('cp1254')
            statements += analyse(text, name)
        if not statements:
            raise ValueError('Okunabilir SQL ifadesi bulunamadı.')
        groups = summarise(statements)
        out = output_dir(args.output)
        write_json(out / 'lineage.json', {'statements': statements, **groups})
        tabular.write_csv(out / 'lineage.csv', ['source', 'target', 'kind', 'file', 'statement'],
                          [{'source': src, 'target': s['target'], 'kind': s['kind'], 'file': s['file'], 'statement': s['statement']} for s in statements if s['target'] for src in s['sources']])
        write_text(out / 'sql-soyagaci.md', markdown(statements, groups, Path(args.input).name))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    risks = sum(1 for s in statements if s['risks'])
    print(f"[{'!' if risks else 'OK'}] {len(statements)} ifade, {len(groups['final_targets'])} son hedef, {risks} riskli ifade. Çıktı: {out / 'sql-soyagaci.md'}")


if __name__ == '__main__':
    main()
