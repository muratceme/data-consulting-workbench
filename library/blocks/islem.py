"""Shared runner for small, single-purpose code blocks.

A category module registers each block with @op(...): its catalog text, CLI arguments, sample files and the result the
end-to-end test expects on that sample. The packaged main.py is a two-line stub that calls run(<id>).
Every block reads its input, never modifies it, never touches the network and writes into output/:
  sonuc.csv (when the block produces a table), rapor.md (readable summary) and ozet.json (summary for automation)."""
import argparse, re, sys
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

OPS = {}


def op(op_id, *, title, domain, product, description, input, output, tag, samples, command, kind='table',
       args=(), sample_input=None, expect=None, test_args=(), exit_code=0, requires=()):
    """Register a block. kind: 'table' (reads --input CSV/XLSX), 'file' (reads --input path), 'none' (generator)."""
    def wrap(func):
        OPS[op_id] = dict(id=op_id, title=title, domain=domain, product=product, description=description, input=input, output=output,
                          tag=tag, samples=dict(samples), command=command, kind=kind, args=list(args), func=func,
                          sample_input=sample_input or (next(iter(samples)) if samples else None),
                          expect=expect or {}, test_args=list(test_args), exit_code=exit_code, module=func.__module__, requires=list(requires))
        return func
    return wrap


def arg(*flags, **kw):
    return flags, kw


class Result:
    def __init__(self, headline, columns=None, rows=None, summary=None, notes=(), tables=(), files=None, code=0):
        self.headline, self.columns, self.rows = headline, columns, rows
        self.summary, self.notes, self.tables = summary or {}, list(notes), list(tables)
        self.files, self.code = files or {}, code


# ---------- helpers shared by blocks ----------
def col_list(value, columns, label='--columns', required=True):
    names = [c.strip() for c in (value or '').split(',') if c.strip()]
    if required and not names:
        raise ValueError(f'{label} ile en az bir sütun verin. Sütunlar: {", ".join(columns)}')
    missing = [c for c in names if c not in columns]
    if missing:
        raise ValueError(f'{label}: dosyada olmayan sütun: {", ".join(missing)}. Sütunlar: {", ".join(columns)}')
    return names


def need(name, columns, label):
    if name not in columns:
        raise ValueError(f'{label}: "{name}" sütunu dosyada yok. Sütunlar: {", ".join(columns)}')
    return name


def numbers(rows, column):
    """Parsed numbers of a column (None where empty or not a number), using the column's own decimal style."""
    values = [r[column] for r in rows]
    style = tabular.number_style([v for v in values if v.strip()])
    return [tabular.parse_number(v, style) if v.strip() else None for v in values]


def dates(rows, column):
    return [(tabular.parse_date(r[column]) or (None,))[0] for r in rows]


def fmt(n, digits=2):
    if n is None:
        return ''
    if isinstance(n, float) and n.is_integer() and abs(n) < 1e15 and digits <= 2:
        return str(int(n))
    return f'{n:.{digits}f}'.rstrip('0').rstrip('.') if isinstance(n, float) else str(n)


def tr_money(n):
    text = f'{abs(n):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('-' if n < 0 else '') + text


TR_UPPER = str.maketrans('iı', 'İI')
TR_LOWER = str.maketrans('İI', 'iı')
FOLD = str.maketrans('çğıöşüÇĞİÖŞÜâîûÂÎÛ', 'cgiosuCGIOSUaiuAIU')


def tr_upper(s):
    return s.translate(TR_UPPER).upper()


def tr_lower(s):
    return s.translate(TR_LOWER).lower()


def tr_title(s):
    return ' '.join(tr_upper(w[:1]) + tr_lower(w[1:]) for w in s.split(' '))


def fold(s):
    """ASCII key for matching: 'İSTANBUL', 'Istanbul' and 'istanbul' fold to the same value."""
    return re.sub(r'\s+', ' ', tr_lower(s).translate(FOLD)).strip()


TR_ALPHABET = 'abcçdefgğhıijklmnoöprsştuüvyz'


def tr_sort_key(s):
    return [TR_ALPHABET.index(ch) if ch in TR_ALPHABET else 100 + ord(ch) for ch in tr_lower(str(s))]


# ---------- runner ----------
def report(spec, res, source):
    lines = [f"# {spec['title']}", '', f'_{source}_' if source else '', '', f'**{res.headline}**', '']
    if res.summary:
        lines += [md_table(['Ölçü', 'Değer'], [[k, v if not isinstance(v, (list, dict)) else ', '.join(map(str, v)) if isinstance(v, list) else
                                                  ', '.join(f'{a}: {b}' for a, b in v.items())] for k, v in res.summary.items()])]
    for title, headers, rows in res.tables:
        lines += [f'## {title}', '', md_table(headers, rows[:200])]
        if len(rows) > 200:
            lines.append(f'_İlk 200 satır gösterildi ({len(rows)} satır)._\n')
    if res.rows is not None and res.columns:
        lines += ['## Sonuç önizlemesi', '', md_table(res.columns[:12], [[r.get(c, '') for c in res.columns[:12]] for r in res.rows[:15]])]
        if len(res.rows) > 15:
            lines.append(f'_Tamamı sonuc.csv dosyasında ({len(res.rows)} satır)._\n')
    if res.notes:
        lines += ['## Notlar', ''] + [f'- {n}' for n in res.notes]
    return '\n'.join(lines)


def run(op_id):
    console()
    spec = OPS[op_id]
    p = argparse.ArgumentParser(description=spec['title'] + '. İnternete bağlanmaz; girdi dosyaları değiştirilmez.')
    if spec['kind'] in ('table', 'file'):
        p.add_argument('--input', default=str(here('ornek', spec['sample_input'])), help='Girdi dosyası' + (' (CSV/XLSX)' if spec['kind'] == 'table' else ''))
    if spec['kind'] == 'table':
        p.add_argument('--sheet', help='XLSX sayfa adı (varsayılan ilk sayfa)')
    for flags, kw in spec['args']:
        if kw.get('default') == '@sample' or isinstance(kw.get('default'), str) and kw['default'].startswith('@ornek/'):
            kw = {**kw, 'default': str(here('ornek', kw['default'][7:] if kw['default'].startswith('@ornek/') else spec['sample_input']))}
        p.add_argument(*flags, **kw)
    p.add_argument('--output', default='output', help='Çıktı klasörü')
    args = p.parse_args()
    try:
        if spec['kind'] == 'table':
            columns, rows = tabular.read(args.input, args.sheet)
            if not columns:
                raise tabular.InputError('Dosyada başlık satırı yok.')
            res = spec['func'](columns, rows, args)
        else:
            res = spec['func'](args)
        out = output_dir(args.output)
        if res.rows is not None:
            tabular.write_csv(out / 'sonuc.csv', res.columns, res.rows)
        for name, content in res.files.items():
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, tuple):
                tabular.write_csv(out / name, content[0], content[1])
            elif isinstance(content, bytes):
                (out / name).write_bytes(content)
            else:
                write_text(out / name, content)
        source = Path(args.input).name if spec['kind'] != 'none' else ''
        write_json(out / 'ozet.json', {'blok': op_id, 'kaynak': source, 'sonuc': res.headline,
                                       'satir': len(res.rows) if res.rows is not None else None, **res.summary})
        write_text(out / 'rapor.md', report(spec, res, source))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    print(f"[{'OK' if not res.code else '!'}] {res.headline}. Çıktı: {out / ('sonuc.csv' if res.rows is not None else 'rapor.md')}")
    if res.code:
        raise SystemExit(res.code)


def stub(spec):
    """main.py of a packaged block."""
    doc = f"{spec['title']}. {spec['description']}".replace('"""', "'''")
    return f'"""{doc}\n\nKullanım: python main.py --help"""\nimport islem\nimport {spec["module"]}  # noqa: F401  (blokları kaydeder)\n\nislem.run({spec["id"]!r})\n'
