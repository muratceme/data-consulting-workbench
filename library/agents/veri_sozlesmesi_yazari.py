"""Veri sözleşmesi yazarı (AI agent): örnek bir teslimat dosyasının profilini yerelde çıkarır; model, üretici ve tüketici ekip için
sütun tipleri, zorunluluk, tekillik, izin verilen değerler, desen ve sınırlar içeren bir veri sözleşmesi (data contract) önerir.
Sözleşme yerelde veri-sozlesmesi kod bloğuyla aynı örnek dosyaya uygulanır: her sütun tam bir kez tanımlanmalı, tip ve desen
geçerli olmalı ve sözleşme örnek veride ihlal üretmemeli — model bir ihlali bilerek kabul ediyorsa (ör. üreticinin düzeltmesi
gereken bir hata) bunu gerekçesiyle "bilinen_ihlaller" listesine yazmak zorunda. Satırlar gönderilmez, yalnızca profil.
Çıktı: veri_sozlesmesi.json (veri-sozlesmesi bloğuyla her teslimatta kullanılabilir), sozlesme.md."""
import argparse, json, re
from argparse import Namespace
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from veri_profili import profile
from veri_sozlugu import profile_text
from katalog import contract

TYPES = ('string', 'integer', 'number', 'date', 'boolean')


def validator(data, columns, rows, out):
    names = [c['name'] for c in data['columns']]

    def validate(obj):
        spec = obj.get('sozlesme')
        if not isinstance(spec, dict) or not isinstance(spec.get('columns'), list):
            raise ValueError('"sozlesme" nesnesi ve içinde "columns" listesi gerekli.')
        problems, seen = [], []
        for c in spec['columns']:
            if not isinstance(c, dict):
                continue
            n = str(c.get('name', ''))
            if n not in names:
                problems.append(f'dosyada olmayan sütun: {n or "?"}')
            elif n in seen:
                problems.append(f'{n} iki kez tanımlı')
            seen.append(n)
            if str(c.get('type', 'string')).lower() not in TYPES:
                problems.append(f'{n}: type şunlardan biri olmalı: {", ".join(TYPES)}')
            if c.get('pattern'):
                try:
                    re.compile(c['pattern'])
                except re.error as exc:
                    problems.append(f'{n}: pattern derlenmiyor ({exc})')
            if 'allowed' in c and (not isinstance(c['allowed'], list) or not c['allowed']):
                problems.append(f'{n}: allowed boş olmayan bir liste olmalı')
        missing = [n for n in names if n not in seen]
        if missing:
            problems.append('sözleşmede olmayan sütun: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        accepted = {(str(k.get('sutun', '')).strip(), str(k.get('kural', '')).strip().lower()) for k in obj.get('bilinen_ihlaller') or [] if isinstance(k, dict) and str(k.get('gerekce', '')).strip()}
        path = out / '_aday_sozlesme.json'
        write_json(path, spec)
        res = contract(columns, rows, Namespace(contract=str(path), strict=False))
        path.unlink(missing_ok=True)
        unexplained = [r for r in res.rows if not any(r['sutun'] == col and r['kural'].lower().startswith(rule) for col, rule in accepted if rule)]
        if unexplained:
            raise ValueError('Sözleşme örnek veride ihlal üretiyor: ' + '; '.join(f"{r['sutun']} / {r['kural']}: {r['ihlal']} satır" for r in unexplained[:10])
                             + '. Kuralı veriyle uyumlu hâle getir ya da üreticinin düzeltmesi gerekiyorsa bilinen_ihlaller listesine sütun, kural ve gerekçeyle yaz.')
        return {'spec': spec, 'violations': res.rows, 'accepted': [k for k in obj.get('bilinen_ihlaller') or [] if isinstance(k, dict)], 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri yönetişimi uzmanısın. Sütun profilinden üretici ve tüketici ekiplerin imzalayacağı bir veri sözleşmesi yaz. '
          f'type: {", ".join(TYPES)}. Kurallar: required (bool), unique (bool), allowed (liste), pattern (regex, tam eşleşme için ^…$), min, max; ayrıca min_rows. '
          'Kurallar örnek veride geçmeli. Profilde gördüğün bir veri hatası (ör. negatif tutar, yanlış yazılmış durum) sözleşmeyle yasaklanmalıysa kuralı yaz ve bu ihlali '
          'bilinen_ihlaller listesine sütun, kural adı (zorunlu, tip, tekil, izin verilen değerler, desen, aralık) ve gerekçeyle ekle. '
          'Yanıt şeması: {"sozlesme": {"name": "...", "owner": "...", "version": "1.0", "min_rows": 1, "columns": [{"name": "...", "type": "string", "required": true}]}, '
          '"bilinen_ihlaller": [{"sutun": "...", "kural": "...", "gerekce": "..."}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Örnek teslimattan doğrulanmış veri sözleşmesi (data contract) yazan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'sozlesme_verisi.csv')))
    p.add_argument('--context', help='Veri setinin amacı, üretici ve tüketici ekipler (isteğe bağlı .md)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    data = {'source': Path(args.input).name, **profile(columns, rows)}
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    user = f"Sütun profili:\n{profile_text(data)}\n\nBağlam:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(data, columns, rows, out))
    if result is None:
        return
    spec = result['spec']
    write_json(out / 'veri_sozlesmesi.json', spec)
    md = [f"# Veri sözleşmesi — {spec.get('name', data['source'])}", '', f"Sahip: {spec.get('owner', '—')} · Sürüm: {spec.get('version', '—')} · En az satır: {spec.get('min_rows', '—')}", '',
          md_table(['Sütun', 'Tip', 'Zorunlu', 'Tekil', 'Kural'], [[c['name'], c.get('type', 'string'), 'evet' if c.get('required') else '', 'evet' if c.get('unique') else '',
                                                                 '; '.join(x for x in [f"izinli: {', '.join(map(str, c['allowed']))}" if c.get('allowed') else '', f"desen: `{c['pattern']}`" if c.get('pattern') else '',
                                                                                      f"≥ {c['min']}" if 'min' in c else '', f"≤ {c['max']}" if 'max' in c else ''] if x)] for c in spec['columns']])]
    if result['violations']:
        md += ['', '## Örnek teslimatta bilerek kabul edilen ihlaller', '', md_table(['Sütun', 'Kural', 'Satır'], [[v['sutun'], v['kural'], v['ihlal']] for v in result['violations']])]
        md += [f"- {a.get('sutun')} / {a.get('kural')}: {a.get('gerekce')}" for a in result['accepted']]
    if result['notes']:
        md += ['', '## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · Sözleşme örnek veriye karşı yerelde çalıştırıldı. Her teslimatta: veri-sozlesmesi bloğu `--contract veri_sozlesmesi.json --strict`._"]
    write_text(out / 'sozlesme.md', '\n'.join(md))
    print(f"[OK] {len(spec['columns'])} sütunluk sözleşme örnek veride doğrulandı; {len(result['violations'])} bilinen ihlal. Çıktı: {out / 'veri_sozlesmesi.json'}")


if __name__ == '__main__':
    llm.run(main)
