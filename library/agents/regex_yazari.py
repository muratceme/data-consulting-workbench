"""Regex yazarı (AI agent): geçerli ve geçersiz örnek değerlerden doğrulama deseni (regex) yazdırır. Model yalnızca deseni
tasarlar; desen yerelde derlenir ve HER örnek üzerinde denenir: geçerli bir örneği reddeden, geçersiz bir örneği kabul eden ya da
iç içe niceleyici (felaket geri izleme riski) içeren desen gerekçesiyle modele geri gönderilir. İsteğe bağlı olarak desen gerçek
bir veri sütununda çalıştırılıp eşleşmeyen değerler sayılır. Çıktı: desen.md (Python, SQL ve kalite-kontrol kuralı örnekleriyle),
desen.json, rules.json (kalite-kontrol bloğu için)."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

YES = {'1', 'evet', 'e', 'true', 'doğru', 'dogru', 'gecerli', 'geçerli', 'yes', 'y', 'valid'}
NO = {'0', 'hayır', 'hayir', 'h', 'false', 'yanlış', 'yanlis', 'gecersiz', 'geçersiz', 'no', 'n', 'invalid'}
NESTED = re.compile(r'\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]')


def load_examples(path, value_col, label_col):
    columns, rows = tabular.read(path)
    for c in (value_col, label_col):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    good, bad = [], []
    for i, r in enumerate(rows, start=2):
        label = r[label_col].strip().casefold()
        if label in YES:
            good.append(r[value_col])
        elif label in NO:
            bad.append(r[value_col])
        elif label:
            raise ValueError(f'{i}. satır: "{r[label_col]}" geçerli/geçersiz olarak okunamadı (evet/hayır, 1/0).')
    if not good or not bad:
        raise ValueError('En az bir geçerli ve bir geçersiz örnek gerekli.')
    return good, bad


def validator(good, bad):
    def validate(obj):
        pattern = obj.get('desen')
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError('"desen" boş.')
        pattern = pattern.strip()
        if len(pattern) > 500:
            raise ValueError('Desen 500 karakterden uzun; sadeleştir.')
        if NESTED.search(pattern):
            raise ValueError(f'İç içe niceleyici var ({NESTED.search(pattern).group(0)}); felaket geri izleme riski. Niceleyiciyi grubun dışına alma.')
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f'Desen derlenmiyor: {exc}') from None
        rejected = [v for v in good if not rx.fullmatch(v)]
        accepted = [v for v in bad if rx.fullmatch(v)]
        if rejected or accepted:
            raise ValueError('Desen örneklerle uyuşmuyor. Reddedilen geçerli örnekler: ' + (', '.join(f'"{v}"' for v in rejected[:10]) or '—')
                             + '; kabul edilen geçersiz örnekler: ' + (', '.join(f'"{v}"' for v in accepted[:10]) or '—'))
        parts = [p for p in obj.get('parcalar', []) if isinstance(p, dict) and p.get('parca')]
        return {'pattern': pattern, 'explanation': str(obj.get('aciklama', '')).strip(),
                'parts': [{'parca': str(p['parca']), 'anlam': str(p.get('anlam', ''))} for p in parts],
                'limits': [str(x) for x in obj.get('sinirlar', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen düzenli ifade (regex) uzmanısın. Geçerli örneklerin HEPSİNİ tam eşleşmeyle (fullmatch) kabul eden, geçersiz '
          'örneklerin HİÇBİRİNİ kabul etmeyen, Python re ile derlenen en sade deseni yaz. Örneklerdeki ortak kuralı genelle; örnekleri tek tek '
          'sıralayan desen yazma. İç içe niceleyici ((a+)+ gibi) kullanma. Örnekler arasında [TCKN], [TELEFON] gibi maskeli değer varsa onu '
          'genelleme dışında bırak ve sinirlar listesinde belirt. Yanıt şeması: {"desen": "^...$", "aciklama": "...", '
          '"parcalar": [{"parca": "(0[1-9]|...)", "anlam": "..."}], "sinirlar": ["desenin kapsamadığı durumlar"]}')


def main():
    p = argparse.ArgumentParser(description='Geçerli/geçersiz örneklerden doğrulanmış regex yazan agent.')
    p.add_argument('--examples', default=str(here('ornek', 'plaka_ornekleri.csv')), help='Örnek dosyası (değer + geçerli mi sütunu)')
    p.add_argument('--value-col', default='deger')
    p.add_argument('--label-col', default='gecerli')
    p.add_argument('--request', help='Kuralın kısa tarifi (isteğe bağlı)', default='Türkiye araç plakası; boşluklu yazım standart.')
    p.add_argument('--data', help='Deseni gerçek veride denemek için CSV/XLSX (isteğe bağlı)')
    p.add_argument('--column', help='--data içindeki sütun')
    llm.add_arguments(p)
    args = p.parse_args()
    good, bad = load_examples(args.examples, args.value_col, args.label_col)
    out = output_dir(args.output)
    user = (f"Kural: {args.request}\n\nGeçerli örnekler ({len(good)}):\n" + '\n'.join(f'- {v}' for v in good)
            + f"\n\nGeçersiz örnekler ({len(bad)}):\n" + '\n'.join(f'- {v}' for v in bad))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(good, bad))
    if result is None:
        return
    rx, pattern = re.compile(result['pattern']), result['pattern']
    data_check = None
    if args.data:
        columns, rows = tabular.read(args.data)
        column = args.column or columns[0]
        if column not in columns:
            raise ValueError(f'"{column}" sütunu --data dosyasında yok.')
        values = [r[column] for r in rows if r[column].strip()]
        misses = [v for v in values if not rx.fullmatch(v.strip())]
        data_check = {'dosya': Path(args.data).name, 'sutun': column, 'deger': len(values), 'eslesmeyen': len(misses), 'ornek': misses[:20]}
    sql_pattern = pattern.replace("'", "''")
    write_json(out / 'desen.json', {**result, 'ornek_sayisi': {'gecerli': len(good), 'gecersiz': len(bad)}, 'veri_kontrolu': data_check, 'model': meta['model']})
    write_json(out / 'rules.json', {'rules': [{'column': args.column or 'SUTUN_ADI', 'check': 'regex', 'value': pattern, 'severity': 'hata', 'note': result['explanation'][:200]}]})
    md = ['# Doğrulama deseni', '', f'```\n{pattern}\n```', '', result['explanation'], '']
    if result['parts']:
        md += [md_table(['Parça', 'Anlam'], [[f"`{x['parca']}`", x['anlam']] for x in result['parts']]), '']
    md += [f'Yerelde denendi: {len(good)} geçerli örneğin hepsi kabul, {len(bad)} geçersiz örneğin hiçbiri kabul edilmedi.', '']
    if data_check:
        md += [f"Gerçek veri ({data_check['dosya']} / {data_check['sutun']}): {data_check['deger']} değerden {data_check['eslesmeyen']} tanesi desene uymuyor.", '']
    md += ['## Kullanım', '', '```python', f'import re\nDESEN = re.compile(r"{pattern}")\ngecerli = bool(DESEN.fullmatch(deger))', '```', '',
           '```sql', f"-- PostgreSQL\nWHERE NOT (sutun ~ '{sql_pattern}')\n-- BigQuery\nWHERE NOT REGEXP_CONTAINS(sutun, r'{sql_pattern}')", '```', '',
           '`rules.json` kalite-kontrol kod bloğuna doğrudan verilebilir (SUTUN_ADI’nı değiştirin).']
    if result['limits']:
        md += ['', '## Desenin sınırları', ''] + [f'- {x}' for x in result['limits']]
    md += ['', f"_Model: {meta['model']} · Desen örneklerle doğrulandı; örneklerin kapsamadığı durumlar için yeni örnek ekleyip tekrar çalıştırın._"]
    write_text(out / 'desen.md', '\n'.join(md))
    tail = f"; veride {data_check['eslesmeyen']}/{data_check['deger']} değer uymuyor" if data_check else ''
    print(f"[OK] Desen {len(good) + len(bad)} örnekle doğrulandı{tail}. Çıktı: {out / 'desen.md'}")


if __name__ == '__main__':
    llm.run(main)
