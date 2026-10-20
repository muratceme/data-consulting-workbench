"""Masraf politikası denetçisi (AI agent): çalışan masraf satırlarının serbest açıklamalarını (“Ankara otel 2 gece”, “müşteri yemeği”, “taksi
havalimanı”) şirket masraf politikasındaki kategorilere atar; politika kuralları (kişi–gün başına limit, fiş zorunluluğu, kapsam dışı
harcama) KODLA uygulanır. Modele yalnızca tekil açıklamalar gider; çalışan adı, tarih ve tutar gönderilmez. Atama kodla denetlenir: kategori
politikada olmalı, her açıklama cevaplanmalı, kapsam dışı denen açıklamanın gerekçesi olmalı, aynı yazıma indirgenen açıklamalar aynı
kategoriye gitmeli. Çıktı: masraf_kategorileri.csv, ihlaller.csv, masraf-denetimi.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table

OUT_OF_SCOPE = 'kapsam_disi'


def key(v):
    return ' '.join(re.split(r'[^0-9a-z]+', tr.fold(v))).strip()


def validator(items, cats):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('atamalar') or []:
            d, c, why = str(e.get('aciklama', '')).strip(), str(e.get('kategori', '')).strip(), str(e.get('gerekce', '')).strip()
            seen.add(d)
            if d not in items:
                problems.append(f'listede olmayan açıklama: {d or "?"}')
            elif c == OUT_OF_SCOPE:
                if not why:
                    problems.append(f'“{d}” kapsam dışı denmiş ama gerekçe yok')
                out[d] = (c, why)
            elif c not in cats:
                problems.append(f'“{d}”: {c} politikada yok ({", ".join(cats)}, {OUT_OF_SCOPE})')
            else:
                out[d] = (c, why)
        missing = [d for d in items if d not in seen]
        if missing:
            problems.append('cevapsız açıklama: ' + ', '.join(missing[:10]))
        groups = defaultdict(set)
        for d, (c, _) in out.items():
            groups[key(d)].add(c)
        split = [k for k, v in groups.items() if len(v) > 1]
        if split:
            problems.append('aynı açıklama farklı kategoriye gitmiş: ' + ', '.join(split))
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir şirketin masraf denetçisisin. Her masraf açıklamasını politikadaki en uygun kategoriye ata. Şirket politikasına göre karşılanmayan '
          'harcamaları (kişisel alışveriş, alkol, trafik cezası gibi) "kapsam_disi" yap ve nedenini yaz. Limit ve fiş kurallarını sen uygulama; kod uygular. '
          'Yanıt şeması: {"atamalar": [{"aciklama": "...", "kategori": "konaklama", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Masraf açıklamalarını politika kategorilerine atayan, limit ve fiş kurallarını kodla uygulayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'masraf_satirlari.csv')), help='personel, tarih, aciklama, tutar, fis_var')
    p.add_argument('--policy', default=str(here('ornek', 'masraf_politikasi.csv')), help='kategori, gunluk_limit, fis_zorunlu, aciklama')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('personel', 'tarih', 'aciklama', 'tutar', 'fis_var'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    _, prow = tabular.read(args.policy)
    style = tabular.number_style([r['gunluk_limit'] for r in prow if r['gunluk_limit'].strip()]) or 'tr'  # “3.500” alone is ambiguous; policies are Turkish
    policy = {r['kategori'].strip(): {'limit': tabular.parse_number(r['gunluk_limit'], style) if r['gunluk_limit'].strip() else None,
                                      'fis': r['fis_zorunlu'].strip().lower() in ('evet', 'e', '1'), 'aciklama': r.get('aciklama', '').strip()} for r in prow}
    items = list(dict.fromkeys(r['aciklama'].strip() for r in rows if r['aciklama'].strip()))
    out = output_dir(args.output)
    user = ('Masraf politikası (kategori · kişi-gün limiti · fiş zorunlu · kapsam):\n' + '\n'.join(
        f'- {c}: {v["limit"] if v["limit"] is not None else "limitsiz"} · {"evet" if v["fis"] else "hayır"} · {v["aciklama"]}' for c, v in policy.items())
        + '\n\nAtanacak masraf açıklamaları:\n' + '\n'.join(f'- {d}' for d in items))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(items, list(policy)))
    if result is None:
        return
    ts = tabular.number_style([r['tutar'] for r in rows])
    spent = defaultdict(float)
    table, viol = [], []
    for n, r in enumerate(rows, 2):
        cat, why = result.get(r['aciklama'].strip(), ('', ''))
        amount = tabular.parse_number(r['tutar'], ts) or 0.0
        issues = []
        if cat == OUT_OF_SCOPE:
            issues.append(f'kapsam dışı: {why}')
        elif cat:
            if policy[cat]['fis'] and r['fis_var'].strip().lower() not in ('evet', 'e', '1'):
                issues.append('fiş yok')
            day = (r['personel'].strip(), r['tarih'].strip(), cat)
            spent[day] += amount
            lim = policy[cat]['limit']
            if lim is not None and spent[day] > lim:
                issues.append(f'{cat} kişi-gün limiti {lim:g} aşıldı (gün toplamı {spent[day]:g})')
        table.append({**r, 'kategori': cat, 'sorun': '; '.join(issues)})
        viol += [{'satir': n, 'personel': r['personel'], 'tarih': r['tarih'], 'aciklama': r['aciklama'], 'tutar': r['tutar'], 'sorun': i} for i in issues]
    tabular.write_csv(out / 'masraf_kategorileri.csv', columns + ['kategori', 'sorun'], table)
    tabular.write_csv(out / 'ihlaller.csv', ['satir', 'personel', 'tarih', 'aciklama', 'tutar', 'sorun'], viol)
    by_cat = defaultdict(float)
    for t in table:
        by_cat[t['kategori'] or '—'] += tabular.parse_number(t['tutar'], ts) or 0.0
    md = ['# Masraf denetimi', '', f'{len(rows)} masraf satırı, {len(items)} tekil açıklama; {len(viol)} kural ihlali.', '',
          md_table(['Kategori', 'Toplam'], [[c, f'{v:,.2f}'] for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]), '',
          md_table(['Satır', 'Personel', 'Tarih', 'Açıklama', 'Tutar', 'Sorun'], [list(v.values()) for v in viol]) if viol else 'İhlal yok.', '',
          f'_Model: {meta["model"]} · Modele yalnızca açıklamalar gitti; kategoriler politikayla denetlendi, limit ve fiş kuralları kodla uygulandı. Sonuçlar onay öncesi ön incelemedir._']
    write_text(out / 'masraf-denetimi.md', '\n'.join(md))
    print(f'[OK] {len(rows)} satır, {len(viol)} ihlal. Çıktı: {out / "masraf-denetimi.md"}')


if __name__ == '__main__':
    llm.run(main)
