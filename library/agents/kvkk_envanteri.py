"""KVKK işleme envanteri (AI agent): CSV/XLSX dosyasındaki kişisel veri sütunlarını yerelde bulur (değer kalıbı ve sütun adı),
serbest metin sütunlarına gömülü kişisel verileri sayar; modele yalnızca sütun adlarını, tespit edilen veri türlerini ve doluluk
oranlarını gönderir. Model her kişisel sütun için işleme amacı, hukuki sebep (KVKK md. 5/6), saklama süresi, erişen roller, aktarım
ve teknik önlem önerir. Her kişisel sütunun bir kez ele alındığı ve hukuki sebep kodlarının geçerli olduğu yerelde denetlenir.
Değerler gönderilmez. Çıktı hukuki görüş değildir; VERBİS ve aydınlatma metni çalışmasına taslak girdidir.
Çıktı: kvkk-envanteri.md, kvkk-envanteri.csv, kvkk-envanteri.json."""
import argparse
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table, mask_text
from kvkk_maskeleme import detect

LEGAL = {'5/1': 'açık rıza', '5/2-a': 'kanunlarda açıkça öngörülmesi', '5/2-b': 'fiili imkânsızlık', '5/2-c': 'sözleşmenin kurulması veya ifası',
         '5/2-ç': 'hukuki yükümlülük', '5/2-d': 'ilgili kişinin alenileştirmesi', '5/2-e': 'bir hakkın tesisi, kullanılması veya korunması',
         '5/2-f': 'meşru menfaat', '6': 'özel nitelikli veri (md. 6 şartları)'}
TRANSFER = ('yok', 'yurt içi', 'yurt dışı')
SPECIAL = ('sağlık', 'saglik', 'biyometrik', 'genetik', 'din', 'mezhep', 'sendika', 'ceza', 'etnik', 'ırk', 'siyasi', 'kılık')


def scan(columns, rows):
    found = detect(columns, rows)
    embedded = {}
    for c in columns:
        if c in found:
            continue
        hits = sum(1 for r in rows if r[c].strip() and mask_text(r[c])[1])
        if hits:
            embedded[c] = hits
    fill = {c: round(100 * sum(1 for r in rows if r[c].strip()) / len(rows), 1) if rows else 0 for c in columns}
    return found, embedded, fill


def code_of(value):
    v = str(value).strip().lower().replace('md.', '').replace('madde', '').replace(' ', '').replace('/2/', '/2-')
    return v if v in LEGAL else None


def validator(found, embedded, columns):
    targets = list(found) + list(embedded)

    def validate(obj):
        items = obj.get('kayitlar')
        if not isinstance(items, list) or not items:
            raise ValueError('"kayitlar" listesi boş.')
        by_col, extra, problems = {}, [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            col = str(it.get('sutun', '')).strip()
            if col not in columns:
                extra.append(col or '?')
            elif col in by_col:
                problems.append(f'"{col}" iki kez yer aldı')
            else:
                by_col[col] = it
        missing = [c for c in targets if c not in by_col]
        if missing or extra:
            raise ValueError(f"Her kişisel veri sütunu tam bir kez yer almalı. Eksik: {', '.join(missing) or '—'}; dosyada olmayan: {', '.join(extra) or '—'}")
        rows = []
        for col in targets:
            it = by_col[col]
            raw = it.get('hukuki_sebep') or []
            raw = raw if isinstance(raw, list) else [raw]
            codes = [code_of(x) for x in raw]
            if not codes or None in codes:
                problems.append(f'"{col}" hukuki sebep kodu geçersiz ({", ".join(map(str, raw)) or "boş"}); şunlardan seç: {", ".join(LEGAL)}')
                continue
            category = str(it.get('veri_kategorisi', '')).strip()
            if any(s in category.lower() for s in SPECIAL) and '6' not in codes:
                problems.append(f'"{col}" özel nitelikli veri kategorisinde; hukuki sebep "6" olmalı')
            transfer = str(it.get('aktarim', '')).strip().lower()
            if transfer not in TRANSFER:
                problems.append(f'"{col}" aktarim şunlardan biri olmalı: {", ".join(TRANSFER)}')
                continue
            for f in ('amac', 'saklama_suresi'):
                if not str(it.get(f, '')).strip():
                    problems.append(f'"{col}" için {f} boş')
            roles = it.get('erisim') or []
            rows.append({'sutun': col, 'tespit': found.get(col) or f'serbest metinde kişisel veri ({embedded[col]} satır)',
                         'veri_kategorisi': category, 'amac': str(it.get('amac', '')).strip(),
                         'hukuki_sebep': '; '.join(f'{c} {LEGAL[c]}' for c in dict.fromkeys(codes)),
                         'saklama_suresi': str(it.get('saklama_suresi', '')).strip(),
                         'erisim': ', '.join(map(str, roles)) if isinstance(roles, list) else str(roles), 'aktarim': transfer,
                         'onlem': str(it.get('onlem', '')).strip(), 'kod_notu': ('md. 9 yurt dışı aktarım şartlarını kontrol edin' if transfer == 'yurt dışı' else '')
                         + ('; serbest metin alanı: veri minimizasyonu için temizlenmeli' if col in embedded else '')})
        if problems:
            raise ValueError('; '.join(problems))
        return {'rows': rows, 'questions': [str(q) for q in obj.get('sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen KVKK uyum çalışmalarında deneyimli bir veri yönetişimi danışmanısın. Verilen kişisel veri sütunları için işleme '
          'envanteri taslağı yaz. Her sütunu tam bir kez ele al; hukuki sebebi şu kodlardan seç: '
          + ', '.join(f'"{k}" ({v})' for k, v in LEGAL.items()) + '. Bağlamdan çıkarılamayan amaç veya süre için tahmin yürütme; '
          '"belirlenecek" yaz ve sorular listesine ekle. aktarim: yok, yurt içi veya yurt dışı. '
          'Yanıt şeması: {"kayitlar": [{"sutun": "dosyadaki ad (aynen)", "veri_kategorisi": "kimlik, iletişim, finans…", "amac": "...", '
          '"hukuki_sebep": ["5/2-c"], "saklama_suresi": "...", "erisim": ["rol"], "aktarim": "yok", "onlem": "maskeleme, erişim kısıtı…"}], "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Dosyadaki kişisel verilerden KVKK işleme envanteri taslağı yazan agent (değerler gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'musteriler.csv')))
    p.add_argument('--sheet')
    p.add_argument('--context', default=str(here('ornek', 'isleme-baglami.md')), help='Verinin hangi süreçte, kim tarafından işlendiği')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    found, embedded, fill = scan(columns, rows)
    if not found and not embedded:
        print('[OK] Kişisel veri sütunu bulunamadı; model çağrılmadı.')
        return
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    lines = [f'Dosya: {Path(args.input).name} · {len(rows)} satır · sütunlar: {", ".join(columns)}', 'Kişisel veri sütunları (yerelde tespit edildi):']
    lines += [f'- {c}: tür={k}; doluluk=%{fill[c]}' for c, k in found.items()]
    lines += [f'- {c}: serbest metin, {n} satırda gömülü kişisel veri; doluluk=%{fill[c]}' for c, n in embedded.items()]
    user = '\n'.join(lines) + f"\n\nİşleme bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(found, embedded, columns))
    if result is None:
        return
    fields = ['sutun', 'tespit', 'veri_kategorisi', 'amac', 'hukuki_sebep', 'saklama_suresi', 'erisim', 'aktarim', 'onlem', 'kod_notu']
    tabular.write_csv(out / 'kvkk-envanteri.csv', fields, result['rows'])
    write_json(out / 'kvkk-envanteri.json', result)
    md = [f'# KVKK işleme envanteri — {Path(args.input).name}', '',
          md_table(['Sütun', 'Tespit', 'Kategori', 'Amaç', 'Hukuki sebep', 'Saklama', 'Erişim', 'Aktarım', 'Önlem'],
                   [[r[f] for f in fields[:-1]] for r in result['rows']])]
    notes = [f"- **{r['sutun']}**: {r['kod_notu'].strip('; ')}" for r in result['rows'] if r['kod_notu']]
    if notes:
        md += ['', '## Kod tarafından eklenen notlar', ''] + notes
    if result['questions']:
        md += ['', '## Süreç sahibine sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Bu tablo hukuki görüş değildir; KVKK sorumlusu/hukuk birimi onaylamadan VERBİS’e veya aydınlatma metnine aktarmayın._"]
    write_text(out / 'kvkk-envanteri.md', '\n'.join(md))
    abroad = sum(r['aktarim'] == 'yurt dışı' for r in result['rows'])
    print(f"[{'!' if abroad else 'OK'}] {len(result['rows'])} kişisel veri sütunu envantere alındı{f', {abroad} sütun yurt dışına aktarılıyor' if abroad else ''}. Çıktı: {out / 'kvkk-envanteri.md'}")


if __name__ == '__main__':
    llm.run(main)
