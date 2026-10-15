"""Kalite kuralı önerici (AI agent): CSV/XLSX profilini yerelde çıkarır, modelden iş bağlamına uygun veri kalitesi kuralları
ister, kuralları kalite-kontrol kod bloğunun şemasına göre denetler ve hemen aynı dosyada çalıştırır.
Satırların kendisi gönderilmez. Çıktı rules.json, kalite-kontrol kod bloğuyla (ve zamanlanmış görevlerde --strict ile)
tekrar tekrar kullanılabilir. Çıktı: rules.json, gerekce.md, kalite-raporu.md, ihlaller.csv, sonuc.json."""
import argparse
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from veri_profili import profile
from veri_sozlugu import profile_text
from kalite_kontrol import CHECKS, TYPES, validate_rules, run, markdown as quality_markdown

RULE_HELP = ('Kullanılabilir kontroller: not_null, unique, unique_together (columns listesiyle), min/max (sayı), between ([alt, üst]), '
             'allowed (değer listesi), regex (tam eşleşme), max_length, type (' + ', '.join(sorted(TYPES)) + '), not_future, no_whitespace, row_count_min. '
             'severity "hata" veya "uyarı".')


def validator(columns):
    def validate(obj):
        rules = obj.get('rules')
        if not isinstance(rules, list) or not rules:
            raise ValueError('"rules" listesi boş.')
        cleaned = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            keep = {k: r[k] for k in ('column', 'columns', 'check', 'value', 'severity') if k in r}
            keep['note'] = str(r.get('gerekce') or r.get('note') or '').strip()
            if keep.get('severity') not in ('hata', 'uyarı'):
                keep['severity'] = 'hata'
            cleaned.append(keep)
        validate_rules({'rules': cleaned}, columns)
        seen, unique = set(), []
        for r in cleaned:
            key = (r.get('check'), r.get('column'), str(r.get('columns')), str(r.get('value')))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return {'rules': unique, 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri kalitesi mühendisisin. Verilen sütun profiline ve iş bağlamına göre anlamlı, test edilebilir kalite kuralları öner. '
          'Profilde görülen sorunları (boşluk, negatif değer, tekrar, birden çok biçim) yakalayan kurallar ekle; ama verinin bugünkü hatalı hâlini '
          'kural olarak kabul etme. ' + RULE_HELP + ' Her kural için kısa gerekçe yaz. '
          'Yanıt şeması: {"rules": [{"column": "...", "check": "...", "value": ..., "severity": "hata|uyarı", "gerekce": "..."}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Profil ve iş bağlamından veri kalitesi kuralları öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')))
    p.add_argument('--sheet')
    p.add_argument('--context', default=str(here('ornek', 'baglam.md')))
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    data = {'source': Path(args.input).name, **profile(columns, rows)}
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    user = f"Sütun profili:\n{profile_text(data)}\n\nİş bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns))
    if result is None:
        return
    spec = {'rules': result['rules']}
    write_json(out / 'rules.json', spec)
    results, violations = run(columns, rows, spec)
    write_json(out / 'sonuc.json', {'source': data['source'], 'rows': len(rows), 'results': results})
    tabular.write_csv(out / 'ihlaller.csv', ['kural', 'seviye', 'satir', 'sutun', 'deger', 'aciklama'], violations)
    write_text(out / 'kalite-raporu.md', quality_markdown(data['source'], rows, results))
    md = ['# Önerilen kalite kuralları', '', md_table(['#', 'Kural', 'Seviye', 'Gerekçe', 'Bugünkü ihlal'],
                                                     [[r['id'], r['rule'], r['severity'], spec['rules'][r['id'] - 1].get('note', ''), r['failed']] for r in results])]
    if result['notes']:
        md += ['## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', 'rules.json kalite-kontrol kod bloğuyla tekrar çalıştırılabilir: `python main.py --input veri.csv --rules rules.json --strict`',
           f"_Model: {meta['model']} · Kurallar taslaktır; eşik ve izinli değerleri veri sahibiyle onaylayın._"]
    write_text(out / 'gerekce.md', '\n'.join(md))
    broken = sum(1 for r in results if not r['passed'])
    print(f"[OK] {len(results)} kural önerildi ve çalıştırıldı; bugün {broken} kural bozuluyor. Çıktı: {out / 'gerekce.md'}")


if __name__ == '__main__':
    llm.run(main)
