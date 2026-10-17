"""Migrasyon eşleme yazarı (AI agent): kaynak ve hedef şemaların DDL’ini (CREATE TABLE) yerelde çözümler; model her hedef sütun
için kaynak tabloyu, kaynak sütunları ve dönüşüm ifadesini önerir. Her hedef sütunun tam bir kez eşlendiği, kaynak sütunların
gerçekten o kaynak tabloda olduğu ve ifadede başka tablonun sütununun geçmediği yerelde denetlenir. Kod ayrıca tip daralmasını
(veri kaybı), NOT NULL hedefe boş olabilen kaynağın COALESCE’siz taşınmasını ve hiçbir hedefe gitmeyen kaynak sütunları işaretler,
her hedef tablo için INSERT … SELECT taslağı yazar. Veri gönderilmez, hiçbir veritabanına bağlanılmaz.
Çıktı: esleme.csv, esleme.md, yukleme.sql."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from sema_farki import load, narrowing

WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def schema_text(label, tables):
    lines = [f'{label}:']
    for t, cols in tables.items():
        lines.append(f'- {t}: ' + ', '.join(f"{c['name']} {c['type']}{' PK' if c['pk'] else ''}{'' if c['nullable'] else ' NOT NULL'}" for c in cols))
    return '\n'.join(lines)


def find(tables, name):
    return next((t for t in tables if t.lower() == str(name).strip().lower()), None)


def validator(source, target):
    def validate(obj):
        items = obj.get('eslemeler')
        if not isinstance(items, list) or not items:
            raise ValueError('"eslemeler" listesi boş.')
        problems, seen, table_src, rows = [], {}, {}, []
        for it in items:
            if not isinstance(it, dict):
                continue
            tt = find(target, it.get('hedef_tablo'))
            if not tt:
                problems.append(f'hedef şemada olmayan tablo: {it.get("hedef_tablo")}')
                continue
            tcol = next((c for c in target[tt] if c['name'].lower() == str(it.get('hedef_sutun', '')).strip().lower()), None)
            if not tcol:
                problems.append(f'{tt} tablosunda olmayan hedef sütun: {it.get("hedef_sutun")}')
                continue
            key = (tt, tcol['name'])
            if key in seen:
                problems.append(f'{tt}.{tcol["name"]} iki kez eşlendi')
                continue
            st = find(source, it.get('kaynak_tablo'))
            if not st:
                problems.append(f'{tt}.{tcol["name"]}: kaynak şemada olmayan tablo "{it.get("kaynak_tablo")}"')
                continue
            if table_src.setdefault(tt, st) != st:
                problems.append(f'{tt} için tek kaynak tablo kullan ({table_src[tt]} ve {st} karıştı)')
                continue
            expr = str(it.get('ifade', '')).strip()
            srcs = [str(x).strip() for x in (it.get('kaynak_sutunlar') or [])]
            names = {c['name'].lower(): c for c in source[st]}
            if not expr:
                problems.append(f'{tt}.{tcol["name"]}: ifade boş')
                continue
            bad = [s for s in srcs if s.lower() not in names]
            if bad:
                problems.append(f'{tt}.{tcol["name"]}: {st} tablosunda olmayan sütun: {", ".join(bad)}')
                continue
            words = {w.lower() for w in WORD.findall(re.sub(r"'[^']*'", '', expr))}
            absent = [s for s in srcs if s.lower() not in words]
            foreign = sorted({c['name'] for t, cols in source.items() if t != st for c in cols if c['name'].lower() in words and c['name'].lower() not in names})
            if absent:
                problems.append(f'{tt}.{tcol["name"]}: kaynak_sutunlar ifadede geçmiyor: {", ".join(absent)}')
            if foreign:
                problems.append(f'{tt}.{tcol["name"]}: ifadede {st} dışındaki tabloların sütunu var: {", ".join(foreign)}')
            if absent or foreign:
                continue
            seen[key] = True
            warn = []
            plain = len(srcs) == 1 and expr.lower() == srcs[0].lower()
            if plain and narrowing(names[srcs[0].lower()]['type'], tcol['type']):
                warn.append(f"tip daralması {names[srcs[0].lower()]['type']} → {tcol['type']}")
            if not tcol['nullable'] and any(names[s.lower()]['nullable'] for s in srcs) and 'coalesce' not in expr.lower() and 'isnull' not in expr.lower():
                warn.append('hedef NOT NULL, kaynak boş olabilir (COALESCE yok)')
            rows.append({'hedef_tablo': tt, 'hedef_sutun': tcol['name'], 'hedef_tip': tcol['type'], 'kaynak_tablo': st, 'kaynak_sutunlar': ', '.join(srcs),
                         'ifade': expr, 'not': str(it.get('not', '')).strip(), 'uyari': '; '.join(warn)})
        missing = [f'{t}.{c["name"]}' for t, cols in target.items() for c in cols if (t, c['name']) not in seen]
        if missing and not problems:
            problems.append('eşlenmeyen hedef sütun: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        order = {(t, c['name']): i for t, cols in target.items() for i, c in enumerate(cols)}
        rows.sort(key=lambda r: (list(target).index(r['hedef_tablo']), order[(r['hedef_tablo'], r['hedef_sutun'])]))
        return {'rows': rows, 'questions': [str(q) for q in obj.get('sorular', []) if q]}
    return validate


def load_sql(rows, target):
    out = []
    for t in target:
        mine = [r for r in rows if r['hedef_tablo'] == t]
        cols = ',\n    '.join(r['hedef_sutun'] for r in mine)
        exprs = []
        for i, r in enumerate(mine):
            line = r['ifade'] + ('' if r['ifade'].lower() == r['hedef_sutun'].lower() else f" AS {r['hedef_sutun']}") + (',' if i < len(mine) - 1 else '')
            exprs.append(line + (f"  -- UYARI: {r['uyari']}" if r['uyari'] else ''))
        out.append(f"INSERT INTO {t} (\n    {cols}\n)\nSELECT\n    " + '\n    '.join(exprs) + f"\nFROM {mine[0]['kaynak_tablo']};")
    return '\n\n'.join(out) + '\n'


SYSTEM = (llm.SAFETY + ' Sen veri migrasyonu uzmanısın. Kaynak ve hedef şemaya bakarak HER hedef sütun için bir eşleme yaz: kaynak_tablo, '
          'kaynak_sutunlar ve ANSI SQL dönüşüm ifadesi (ifade yalnızca o kaynak tablonun sütunlarını kullanır). Her hedef tablo tek bir kaynak '
          'tablodan beslenir. Kaynakta karşılığı olmayan sütun için sabit/sistem ifadesi (ör. \'ERP\', CURRENT_TIMESTAMP) yaz, kaynak_sutunlar boş '
          'bırak ve notta belirt. Tip daralması ya da NOT NULL hedefe boş olabilen kaynak varsa ifadede CAST/COALESCE kullan. '
          'Yanıt şeması: {"eslemeler": [{"hedef_tablo": "...", "hedef_sutun": "...", "kaynak_tablo": "...", "kaynak_sutunlar": ["..."], '
          '"ifade": "...", "not": "..."}], "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Kaynak ve hedef DDL’den sütun eşleme ve yükleme SQL taslağı yazan agent.')
    p.add_argument('--source', default=str(here('ornek', 'sema_v1.sql')), help='Kaynak DDL (.sql dosyası veya klasör)')
    p.add_argument('--target', default=str(here('ornek', 'hedef_dwh_ddl.sql')), help='Hedef DDL (.sql dosyası veya klasör)')
    p.add_argument('--context', help='Kurallar/bağlam (isteğe bağlı .md)')
    llm.add_arguments(p)
    args = p.parse_args()
    source, target = load(args.source), load(args.target)
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    user = f"{schema_text('Kaynak şema', source)}\n\n{schema_text('Hedef şema', target)}\n\nBağlam:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(source, target))
    if result is None:
        return
    rows = result['rows']
    used = {(r['kaynak_tablo'], s.strip().lower()) for r in rows for s in r['kaynak_sutunlar'].split(',') if s.strip()}
    mapped_tables = {r['kaynak_tablo'] for r in rows}
    dropped = [f"{t}.{c['name']}" for t in mapped_tables for c in source[t] if (t, c['name'].lower()) not in used]
    unused_tables = [t for t in source if t not in mapped_tables]
    fields = ['hedef_tablo', 'hedef_sutun', 'hedef_tip', 'kaynak_tablo', 'kaynak_sutunlar', 'ifade', 'uyari', 'not']
    tabular.write_csv(out / 'esleme.csv', fields, rows)
    write_text(out / 'yukleme.sql', f'-- Yükleme taslağı (model: {meta["model"]}). Gözden geçirmeden ve test ortamında denemeden çalıştırmayın.\n\n' + load_sql(rows, target))
    write_json(out / 'esleme.json', {**result, 'tasinmayan_sutunlar': dropped, 'kullanilmayan_kaynak_tablolar': unused_tables})
    md = ['# Migrasyon eşlemesi', '', f'{len(target)} hedef tablo, {len(rows)} sütun eşlendi.', '',
          md_table(['Hedef', 'Tip', 'Kaynak', 'İfade', 'Uyarı'], [[f"{r['hedef_tablo']}.{r['hedef_sutun']}", r['hedef_tip'], r['kaynak_tablo'], f"`{r['ifade']}`", r['uyari'] or '—'] for r in rows])]
    if dropped or unused_tables:
        md += ['', '## Hedefe taşınmayanlar (kodla bulundu)', ''] + [f'- Sütun: {d}' for d in dropped] + [f'- Tablo: {t}' for t in unused_tables]
    if result['questions']:
        md += ['', '## Kaynak/hedef sahiplerine sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Eşleme taslaktır; yukleme.sql’i test ortamında deneyin ve sonrasında satır/tutar mutabakatı yapın._"]
    write_text(out / 'esleme.md', '\n'.join(md))
    warns = sum(bool(r['uyari']) for r in rows)
    print(f"[{'!' if warns or dropped else 'OK'}] {len(rows)} hedef sütun eşlendi; {warns} uyarı, {len(dropped)} taşınmayan kaynak sütun. Çıktı: {out / 'esleme.md'}")


if __name__ == '__main__':
    llm.run(main)
