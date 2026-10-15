"""DAX ölçü yazarı (AI agent): Power BI modelini (.pbit / model.bim) yerelde okur, iş isteğine göre yeni DAX ölçüleri yazar.
Model yalnızca tablo/sütun adlarını, ilişkileri ve mevcut ölçüleri görür; veri satırı gönderilmez.
Yerel denetim: başvurulan her Tablo[Sütun] ve [Ölçü] modelde (veya yeni ölçülerde) olmalı, parantezler dengeli olmalı,
ad çakışması olmamalı. Çıktı olculer.dax dosyası Power BI Desktop > DAX sorgu görünümüne yapıştırılıp
"Modeli güncelle" ile eklenebilir. Çıktı: olculer.dax, olculer.md, dogrulama.json."""
import argparse, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from pbit_model import read_schema, normalize

COLUMN_REF = re.compile(r"('(?:[^']|'')+'|[A-Za-z_][\w]*)\s*\[([^\]]+)\]")
MEASURE_REF = re.compile(r"(?<![\w'\]])\[([^\]]+)\]")


def strip_strings(expr):
    return re.sub(r'"(?:[^"]|"")*"', '""', re.sub(r'//[^\n]*|--[^\n]*|/\*.*?\*/', ' ', expr, flags=re.S))


def balanced(expr):
    depth = 0
    for ch in strip_strings(expr):
        depth += ch == '('
        depth -= ch == ')'
        if depth < 0:
            return False
    return depth == 0


def validator(model):
    tables = {t['name']: {c['name'] for c in t['columns']} for t in model['tables'] if not t['auto_date']}
    existing = {x['name'] for t in model['tables'] for x in t['measures']}
    lower = {t.lower(): t for t in tables}

    def validate(obj):
        items = obj.get('olculer')
        if not isinstance(items, list) or not items:
            raise ValueError('"olculer" listesi boş.')
        names, problems, cleaned = set(), [], []
        for i, m in enumerate(items, start=1):
            if not isinstance(m, dict):
                raise ValueError('Her ölçü bir nesne olmalı.')
            name = str(m.get('ad', '')).strip().strip('[]')
            expr = str(m.get('ifade', '')).strip()
            table = str(m.get('tablo', '')).strip().strip("'")
            if not name or not expr:
                problems.append(f'{i}. ölçünün adı veya ifadesi boş')
                continue
            expr = re.sub(r'^\s*' + re.escape(name) + r'\s*:?=\s*', '', expr)
            if name in existing:
                problems.append(f'"{name}" adı modelde zaten var; farklı bir ad ver')
            if name in names:
                problems.append(f'"{name}" adı iki kez kullanıldı')
            names.add(name)
            if table.lower() not in lower:
                problems.append(f'"{name}" için ev tablosu "{table}" modelde yok')
            if not balanced(expr):
                problems.append(f'"{name}" ifadesinde parantezler dengesiz')
            cleaned.append({'name': name, 'table': lower.get(table.lower(), table), 'expression': expr,
                            'format': str(m.get('bicim', '') or ''), 'description': str(m.get('aciklama', '') or ''),
                            'folder': str(m.get('klasor', '') or '')})
        all_measures = existing | names
        all_columns = {c for cols in tables.values() for c in cols}
        for m in cleaned:
            body = strip_strings(m['expression'])
            for t, col in COLUMN_REF.findall(body):
                t = t.strip("'").replace("''", "'")
                if t.lower() not in lower:
                    problems.append(f'"{m["name"]}": {t} tablosu modelde yok')
                elif col not in tables[lower[t.lower()]]:
                    problems.append(f'"{m["name"]}": {t}[{col}] sütunu modelde yok')
            for ref in MEASURE_REF.findall(COLUMN_REF.sub(' ', body)):
                if ref not in all_measures and ref not in all_columns:
                    problems.append(f'"{m["name"]}": [{ref}] ne ölçü ne sütun')
        if problems:
            raise ValueError('; '.join(dict.fromkeys(problems)))
        return {'measures': cleaned, 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


def model_text(model):
    lines = []
    for t in model['tables']:
        if t['auto_date']:
            continue
        cols = ', '.join(f"{c['name']} ({c['data_type']})" for c in t['columns'])
        lines.append(f"Tablo '{t['name']}': {cols}")
        for x in t['measures']:
            lines.append(f"  Ölçü [{x['name']}] = {x['expression'][:300]}")
    lines.append('İlişkiler:')
    for r in model['relationships']:
        if not r['to_table'].startswith('LocalDateTable_'):
            lines.append(f"  '{r['from_table']}'[{r['from_column']}] {r['cardinality']} '{r['to_table']}'[{r['to_column']}]"
                         f"{' (çift yönlü)' if r['both_directions'] else ''}{' (pasif)' if not r['active'] else ''}")
    return '\n'.join(lines)


def quote(table):
    return "'" + table.replace("'", "''") + "'"


SYSTEM = (llm.SAFETY + ' Sen kıdemli bir Power BI / DAX geliştiricisisin. Verilen modele göre istenen ölçüleri yaz. '
          'Sütunlara her zaman Tablo[Sütun], ölçülere [Ölçü] biçiminde başvur; yalnızca modelde olan tablo ve sütunları kullan. '
          'Bölmede DIVIDE kullan, tüm tabloyu FILTER etmek yerine sütun filtresi kullan, zaman zekâsı için modeldeki tarih tablosunu kullan. '
          'Yanıt şeması: {"olculer": [{"ad": "...", "tablo": "ölçünün ekleneceği tablo", "ifade": "DAX ifadesi (ad ve = olmadan)", '
          '"bicim": "biçim dizesi, ör. #,0 veya 0.0%", "aciklama": "iş tanımı", "klasor": "görüntüleme klasörü"}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Power BI modeline göre DAX ölçüleri yazan agent.')
    p.add_argument('--model-file', default=str(here('ornek', 'satis-modeli.pbit')), help='.pbit, model.bim veya DataModelSchema')
    p.add_argument('--request', default=str(here('ornek', 'dax-istek.md')))
    llm.add_arguments(p)
    args = p.parse_args()
    schema, layout = read_schema(args.model_file)
    model = normalize(schema, layout)
    request = Path(args.request).read_text(encoding='utf-8-sig').strip()
    if not request:
        raise ValueError('İstek dosyası boş.')
    out = output_dir(args.output)
    user = f'Model:\n{model_text(model)}\n\nİstenen ölçüler:\n"""\n{request}\n"""'
    result, meta = llm.ask(args, out, SYSTEM, user, validator(model))
    if result is None:
        return
    measures = result['measures']
    dax = ['// datawork DAX yazarı · ' + meta['model'],
           '// Power BI Desktop > DAX sorgu görünümüne yapıştırın, çalıştırıp sonucu kontrol edin,',
           '// ardından her ölçünün üstündeki "Modeli güncelle" bağlantısıyla modele ekleyin.', 'DEFINE']
    for m in measures:
        if m['description']:
            dax.append('    // ' + m['description'].replace('\n', ' '))
        body = m['expression'].replace('\n', '\n        ')
        dax.append(f"    MEASURE {quote(m['table'])}[{m['name']}] =\n        {body}")
    dax.append('EVALUATE')
    dax.append('    ROW (\n' + ',\n'.join(f'        "{m["name"]}", [{m["name"]}]' for m in measures) + '\n    )')
    write_text(out / 'olculer.dax', '\n'.join(dax))
    md = ['# Yeni DAX ölçüleri', '', md_table(['Ölçü', 'Tablo', 'Biçim', 'Açıklama'], [[m['name'], m['table'], m['format'], m['description']] for m in measures])]
    for m in measures:
        md += [f"## {m['name']}", '', '```dax', m['expression'], '```', '']
    if result['notes']:
        md += ['## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', 'Yerel denetim: tüm tablo/sütun/ölçü başvuruları modelde bulundu, parantezler dengeli, ad çakışması yok. '
           'Sonuçların doğruluğunu bilinen bir rakamla karşılaştırarak kontrol edin.']
    write_text(out / 'olculer.md', '\n'.join(md))
    write_json(out / 'dogrulama.json', {'measures': [m['name'] for m in measures], 'checks': 'references, parentheses, names', 'meta': meta})
    print(f"[OK] {len(measures)} ölçü yazıldı ve yerelde denetlendi ({meta['attempts']} deneme). Çıktı: {out / 'olculer.dax'}")


if __name__ == '__main__':
    llm.run(main)
