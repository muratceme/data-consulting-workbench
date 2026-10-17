"""Veri olayı analisti (AI agent): uygulama/pipeline logundaki hataları yerelde imzalarına göre gruplar (sayılar, kimlikler,
e-posta ve IP yer tutucuya çevrilir), her imza için adet, ilk/son görülme, seviye ve istisna türünü çıkarır. Modele yalnızca bu
imza özeti gider; model her imza için olası neden, kategori, öncelik ve aksiyon yazar. Her imzanın tam bir kez ele alındığı,
kategori ve önceliğin izinli değerlerde olduğu yerelde denetlenir; FATAL/CRITICAL imzalar model ne derse desin P1 olur.
Çıktı: olay-raporu.md, olay-imzalari.csv, olay.json."""
import argparse, re
from collections import Counter
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from gozlem import LOG_HEAD, ERROR_LEVELS, signature

CATEGORIES = ('altyapı', 'kod', 'veri', 'kaynak sistem', 'yetki', 'yapılandırma', 'kapasite', 'bilinmiyor')
PRIORITIES = ('P1', 'P2', 'P3')
EXC = re.compile(r'^([\w.]+(?:Error|Exception|Fault))\b')


def parse(path, max_signatures):
    records, cur = [], None
    for line in Path(path).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        m = LOG_HEAD.match(line)
        if m:
            cur = {'zaman': m.group(1), 'seviye': (m.group(2) or '').upper().replace('WARNING', 'WARN'), 'mesaj': m.group(3), 'ek': []}
            records.append(cur)
        elif cur is not None and line.strip():
            cur['ek'].append(line.strip())
    if not records:
        raise ValueError('Zaman damgalı log satırı bulunamadı (2026-10-15 08:00:01 ERROR … biçimi bekleniyor).')
    groups = {}
    for r in records:
        kinds = [m.group(1).split('.')[-1] for x in r['ek'] for m in [EXC.match(x)] if m]
        if r['seviye'] not in ERROR_LEVELS and not kinds:
            continue
        sig = signature(r['mesaj'])
        g = groups.setdefault(sig, {'imza': sig, 'seviye': r['seviye'] or 'istisna', 'adet': 0, 'ilk': r['zaman'], 'son': r['zaman'], 'istisna': set(), 'iz': ''})
        g['adet'] += 1
        g['son'] = r['zaman']
        g['istisna'].update(kinds)
        if not g['iz'] and r['ek']:
            g['iz'] = signature(' | '.join(r['ek'][-3:]))
    out = sorted(groups.values(), key=lambda g: (-g['adet'], g['ilk']))[:max_signatures]
    for i, g in enumerate(out, start=1):
        g['id'] = f'S{i}'
        g['istisna'] = ', '.join(sorted(g['istisna']))
    levels = Counter(r['seviye'] or '-' for r in records)
    return records, out, levels


def validator(sigs):
    ids = [g['id'] for g in sigs]
    fatal = {g['id'] for g in sigs if g['seviye'] in ('FATAL', 'CRITICAL')}

    def validate(obj):
        items = obj.get('imzalar')
        if not isinstance(items, list) or not items:
            raise ValueError('"imzalar" listesi boş.')
        by_id, extra, problems = {}, [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            sid = str(it.get('id', '')).strip().upper()
            if sid not in ids:
                extra.append(sid or '?')
            else:
                by_id[sid] = it
        missing = [i for i in ids if i not in by_id]
        if missing or extra:
            raise ValueError(f"Her imza (S1…S{len(ids)}) tam bir kez yer almalı. Eksik: {', '.join(missing) or '—'}; olmayan: {', '.join(extra) or '—'}")
        rows = []
        for g in sigs:
            it = by_id[g['id']]
            cat = str(it.get('kategori', '')).strip().lower()
            pri = str(it.get('oncelik', '')).strip().upper()
            actions = it.get('aksiyonlar') or []
            if cat not in CATEGORIES:
                problems.append(f"{g['id']} kategori şunlardan biri olmalı: {', '.join(CATEGORIES)}")
            if pri not in PRIORITIES:
                problems.append(f"{g['id']} oncelik P1, P2 veya P3 olmalı")
            if not isinstance(actions, list) or not actions or not str(it.get('olasi_neden', '')).strip():
                problems.append(f"{g['id']} için olasi_neden ve en az bir aksiyon gerekli")
                continue
            note = ''
            if g['id'] in fatal and pri != 'P1':
                note, pri = f'model {pri} dedi; FATAL/CRITICAL olduğu için P1 yapıldı', 'P1'
            rows.append({'id': g['id'], 'imza': g['imza'], 'seviye': g['seviye'], 'adet': g['adet'], 'ilk': g['ilk'], 'son': g['son'], 'istisna': g['istisna'],
                         'kategori': cat, 'oncelik': pri, 'olasi_neden': str(it['olasi_neden']).strip(), 'aksiyonlar': [str(a) for a in actions], 'kod_notu': note})
        if problems:
            raise ValueError('; '.join(problems))
        rows.sort(key=lambda r: (r['oncelik'], -r['adet']))
        return {'summary': str(obj.get('ozet', '')).strip(), 'rows': rows, 'questions': [str(q) for q in obj.get('sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu nöbetçi mühendisisin (SRE). Verilen hata imzalarından olay değerlendirmesi yaz. '
          '<N>, <ID>, <EPOSTA>, <IP>, <METİN> yer tutuculardır. Log’da olmayan bir sistem, sürüm veya değişiklik uydurma; nedeni '
          '"olası" diye yaz ve doğrulamak için gereken bilgiyi sorular listesine ekle. Her imzayı tam bir kez ele al. '
          f'kategori: {", ".join(CATEGORIES)}. oncelik: P1 (hizmet/veri kaybı, hemen), P2 (bugün), P3 (planlı). '
          'Yanıt şeması: {"ozet": "2-3 cümle", "imzalar": [{"id": "S1", "kategori": "...", "oncelik": "P2", "olasi_neden": "...", '
          '"aksiyonlar": ["..."]}], "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Uygulama/pipeline logundan olay değerlendirmesi yazan agent (log satırları gönderilmez, yalnızca imza özeti).')
    p.add_argument('--input', default=str(here('ornek', 'uygulama.log')))
    p.add_argument('--context', help='Sistem hakkında kısa bağlam (isteğe bağlı .md)')
    p.add_argument('--top', type=int, default=30, help='En çok kaç imza gönderilsin')
    llm.add_arguments(p)
    args = p.parse_args()
    records, sigs, levels = parse(args.input, args.top)
    if not sigs:
        print(f'[OK] {len(records)} kayıtta hata yok; model çağrılmadı.')
        return
    out = output_dir(args.output)
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    lines = [f"Log: {len(records)} kayıt, {records[0]['zaman']} – {records[-1]['zaman']}; seviyeler: " + ', '.join(f'{k}={v}' for k, v in levels.most_common())]
    for g in sigs:
        lines.append(f"{g['id']}: [{g['seviye']}] ×{g['adet']} ({g['ilk']} → {g['son']}) {g['imza']}"
                     + (f"; istisna: {g['istisna']}" if g['istisna'] else '') + (f"; iz: {g['iz']}" if g['iz'] else ''))
    user = 'Hata imzaları:\n' + '\n'.join(lines) + f"\n\nSistem bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(sigs))
    if result is None:
        return
    write_json(out / 'olay.json', result)
    tabular.write_csv(out / 'olay-imzalari.csv', ['id', 'oncelik', 'kategori', 'seviye', 'adet', 'ilk', 'son', 'imza', 'istisna', 'olasi_neden', 'aksiyonlar', 'kod_notu'],
                      [{**r, 'aksiyonlar': ' | '.join(r['aksiyonlar'])} for r in result['rows']])
    md = ['# Olay değerlendirmesi', '', result['summary'], '',
          md_table(['Öncelik', 'İmza', 'Adet', 'Kategori', 'Olası neden'], [[r['oncelik'], f"{r['id']}: {r['imza'][:90]}", r['adet'], r['kategori'], r['olasi_neden']] for r in result['rows']])]
    for r in result['rows']:
        md += ['', f"## {r['id']} · {r['oncelik']} · {r['kategori']}", '', f"`{r['imza']}`", '', f"- {r['adet']} kez, {r['ilk']} → {r['son']}" + (f"; istisna: {r['istisna']}" if r['istisna'] else '')]
        md += [f'- [ ] {a}' for a in r['aksiyonlar']]
        if r['kod_notu']:
            md.append(f"- _Not: {r['kod_notu']}_")
    if result['questions']:
        md += ['', '## Doğrulanması gerekenler', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Nedenler hipotezdir; log satırlarıyla doğrulamadan kapanış notu yazmayın._"]
    write_text(out / 'olay-raporu.md', '\n'.join(md))
    p1 = sum(r['oncelik'] == 'P1' for r in result['rows'])
    print(f"[{'!' if p1 else 'OK'}] {len(result['rows'])} hata imzası değerlendirildi, {p1} P1. Çıktı: {out / 'olay-raporu.md'}")


if __name__ == '__main__':
    llm.run(main)
