"""Veri sözlüğü yazarı (AI agent): CSV/XLSX dosyasının profilini yerelde çıkarır (veri-profili kod bloğu), modele yalnızca
sütun adlarını, tiplerini, doluluk/aralık istatistiklerini ve kişisel veri içermeyen sık değerleri gönderir; her sütun için iş
adı, açıklama ve hassasiyet sınıfı yazdırır. Satırların kendisi gönderilmez. Kişisel veri izi bulunan sütunlar modelin
cevabından bağımsız olarak "kişisel" sınıflanır. Çıktı: veri-sozlugu.md, veri-sozlugu.csv, profile.json."""
import argparse, json
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from veri_profili import profile

LEVELS = ('genel', 'iç', 'gizli', 'kişisel')


def profile_text(data, include_values=True):
    lines = [f"Dosya: {data['source']} · {data['row_count']} satır · aday anahtar: {', '.join(data['candidate_keys']) or 'yok'}"]
    for c in data['columns']:
        bits = [f"tip={c['type']}", f"boş=%{c['missing_pct']}", f"farklı={c['distinct']}"]
        if 'min' in c:
            bits.append(f"aralık={c['min']}…{c['max']}")
        if c['pii']:
            bits.append(f"kişisel veri izi={c['pii']}")
        elif include_values and c['top']:
            bits.append('sık değerler=' + ', '.join(str(t['value'])[:40] for t in c['top']))
        lines.append(f"- {c['name']}: " + '; '.join(bits))
    return '\n'.join(lines)


def validator(data):
    names = [c['name'] for c in data['columns']]
    pii = {c['name']: c['pii'] for c in data['columns'] if c['pii']}

    def validate(obj):
        cols = obj.get('sutunlar')
        if not isinstance(cols, list):
            raise ValueError('"sutunlar" listesi yok.')
        by_name = {}
        for c in cols:
            if isinstance(c, dict) and str(c.get('ad', '')) in names:
                by_name[str(c['ad'])] = c
        missing = [n for n in names if n not in by_name]
        extra = [str(c.get('ad')) for c in cols if isinstance(c, dict) and str(c.get('ad', '')) not in names]
        if missing or extra:
            raise ValueError(f"Her sütun tam olarak bir kez yer almalı. Eksik: {', '.join(missing) or '—'}; dosyada olmayan: {', '.join(extra) or '—'}")
        rows = []
        for n in names:
            c = by_name[n]
            level = str(c.get('hassasiyet', '')).strip().lower()
            level = level if level in LEVELS else 'iç'
            if n in pii:
                level = 'kişisel'  # deterministic override: detected personal data is never downgraded
            if not str(c.get('aciklama', '')).strip():
                raise ValueError(f'"{n}" için açıklama boş.')
            rows.append({'sutun': n, 'is_adi': str(c.get('is_adi', '')).strip() or n, 'aciklama': str(c['aciklama']).strip(),
                         'hassasiyet': level, 'kural_onerisi': str(c.get('kural_onerisi', '') or '').strip(),
                         'tip': next(x['type'] for x in data['columns'] if x['name'] == n), 'kisisel_veri_izi': pii.get(n, '')})
        return {'table': str(obj.get('tablo_aciklamasi', '')).strip(), 'rows': rows, 'questions': [str(q) for q in obj.get('acik_sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri yönetişimi uzmanısın. Verilen sütun profiline ve iş bağlamına bakarak iş kullanıcılarının anlayacağı bir veri sözlüğü yaz. '
          'Bilmediğin anlamı uydurma; tahmin ediyorsan açıklamada "muhtemelen" de ve soruyu acik_sorular listesine ekle. '
          f'hassasiyet şunlardan biri olmalı: {", ".join(LEVELS)}. '
          'Yanıt şeması: {"tablo_aciklamasi": "...", "sutunlar": [{"ad": "dosyadaki sütun adı (aynen)", "is_adi": "...", '
          '"aciklama": "...", "hassasiyet": "...", "kural_onerisi": "ör. boş olamaz, 0’dan büyük"}], "acik_sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='CSV/XLSX için veri sözlüğü yazan agent (satır verisi gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')))
    p.add_argument('--sheet')
    p.add_argument('--context', default=str(here('ornek', 'baglam.md')), help='Tablonun iş bağlamı (isteğe bağlı)')
    p.add_argument('--no-values', action='store_true', help='Sık değerleri de gönderme (yalnızca istatistik)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    data = {'source': Path(args.input).name, **profile(columns, rows)}
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    write_json(out / 'profile.json', data)
    user = f"Sütun profili:\n{profile_text(data, not args.no_values)}\n\nİş bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(data))
    if result is None:
        return
    tabular.write_csv(out / 'veri-sozlugu.csv', ['sutun', 'is_adi', 'tip', 'hassasiyet', 'aciklama', 'kural_onerisi', 'kisisel_veri_izi'], result['rows'])
    md = [f"# Veri sözlüğü — {data['source']}", '', result['table'] or '', '',
          md_table(['Sütun', 'İş adı', 'Tip', 'Hassasiyet', 'Açıklama', 'Kural önerisi'],
                   [[r['sutun'], r['is_adi'], r['tip'], r['hassasiyet'], r['aciklama'], r['kural_onerisi']] for r in result['rows']])]
    if result['questions']:
        md += ['## Veri sahibine sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Açıklamalar taslaktır; veri sahibi onaylamadan yayımlamayın. Kişisel veri izi bulunan sütunlar kod tarafından 'kişisel' işaretlendi._"]
    write_text(out / 'veri-sozlugu.md', '\n'.join(md))
    print(f"[OK] {len(result['rows'])} sütun tanımlandı, {len(result['questions'])} açık soru. Çıktı: {out / 'veri-sozlugu.md'}")


if __name__ == '__main__':
    llm.run(main)
