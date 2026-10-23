"""Boş değer politikası (AI agent): bir tablonun her sütunu için boş (NULL) değer politikası önerir: zorunlu (NOT NULL), varsayılanla
doldur (DEFAULT + geçmiş boşları güncelle) ya da boşa izin ver (iş anlamı gereği isteğe bağlı). Sütun profili (boş oranı, tür, tekillik,
örnek kategoriler) ve boşların başka bir sütuna bağlı olup olmadığı (ör. mağaza satışında kargo ücreti boş) kodla çıkarılır; modele yalnızca
bu profil gider. Öneri kodla denetlenir: boş içeren sütun varsayılansız zorunlu yapılamaz (kısıt eklenemez), tekil anahtar sütun boşa izin
veremez, varsayılan değer sütunun türüne ve mevcut değer kümesine uymalı, boşları tek bir kategoriye bağlı sütun için gerekçe yazılmalı.
Çıktı: bos_deger.sql (UPDATE + ALTER), bos_deger_plani.csv, bos-deger-politikasi.md."""
import argparse
from collections import Counter
import llm
import tabular
from common import here, output_dir, write_text, md_table

POLICIES = ('zorunlu', 'varsayilan', 'izin')


def profile(columns, rows):
    prof = {}
    for c in columns:
        vals = [r[c].strip() for r in rows]
        filled = [v for v in vals if v]
        style = tabular.number_style(filled)
        nums = [tabular.parse_number(v, style) for v in filled]
        kind = 'sayi' if filled and all(x is not None for x in nums) else 'tarih' if filled and all(tabular.parse_date(v) for v in filled) else 'metin'
        cats = Counter(filled)
        prof[c] = {'bos': len(vals) - len(filled), 'tur': kind, 'tekil': kind != 'sayi' and len(cats) == len(filled) == len(vals), 'farkli': len(cats),
                   'kategoriler': sorted(cats) if len(cats) <= 12 else [], 'aralik': (min(nums), max(nums)) if kind == 'sayi' else None, 'style': style}
    for c, p in prof.items():
        p['bagli'] = None
        if p['bos']:
            empty = [r for r in rows if not r[c].strip()]
            for d, q in prof.items():
                if d != c and q['kategoriler']:
                    top, n = Counter(r[d].strip() for r in empty).most_common(1)[0]
                    if n / len(empty) >= 0.9 and top:
                        p['bagli'] = (d, top, round(n / len(empty), 2))
                        break
    return prof


def validator(prof, n_rows):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('kolonlar') or []:
            c = str(e.get('kolon', '')).strip()
            if c not in prof or c in seen:
                problems.append(f'kolon “{c}” yok ya da iki kez yazıldı')
                continue
            seen.add(c)
            pol, dv, why = str(e.get('politika', '')).strip(), e.get('varsayilan'), str(e.get('gerekce', '')).strip()
            p = prof[c]
            if pol not in POLICIES:
                problems.append(f'{c}: politika {", ".join(POLICIES)} olmalı')
                continue
            if pol == 'zorunlu' and p['bos']:
                problems.append(f'{c}: {p["bos"]} boş satır var; varsayılansız NOT NULL eklenemez (varsayilan ile doldur ya da izin ver)')
            if pol == 'izin' and p['tekil']:
                problems.append(f'{c}: her satırda tekil, anahtar sütun; boşa izin verilemez')
            if pol == 'varsayilan':
                dv = '' if dv is None else str(dv).strip()
                if p['tur'] == 'sayi':
                    x = tabular.parse_number(dv, p['style']) if dv else None
                    if x is None:
                        problems.append(f'{c}: varsayılan “{dv}” sayı değil')
                    elif not min(0, p['aralik'][0]) <= x <= p['aralik'][1]:  # 0 is a valid neutral default
                        problems.append(f'{c}: varsayılan {dv} mevcut aralığın ({p["aralik"][0]:g}–{p["aralik"][1]:g}) dışında')
                elif p['kategoriler'] and dv not in p['kategoriler']:
                    problems.append(f'{c}: varsayılan “{dv}” mevcut değerlerden biri olmalı ({", ".join(p["kategoriler"])})')
                elif not dv:
                    problems.append(f'{c}: varsayılan değer yazılmamış')
            if p['bagli'] and pol != 'zorunlu' and not why:
                problems.append(f'{c}: boşların %{100 * p["bagli"][2]:.0f} kadarı {p["bagli"][0]}={p["bagli"][1]} satırlarında; politikanın gerekçesini yaz')
            out[c] = (pol, dv if pol == 'varsayilan' else None, why)
        missing = [c for c in prof if c not in seen]
        if missing:
            problems.append('politikası yazılmayan kolon: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri kalitesi mühendisisin. Her sütun için boş değer politikası seç: zorunlu (NOT NULL; mevcut boş olmamalı), varsayilan (geçmiş '
          'boşlar bu değerle doldurulur ve DEFAULT tanımlanır) ya da izin (iş anlamı gereği isteğe bağlı alan). Boşlar belirli bir kategoriye bağlıysa (ör. mağaza '
          'satışında kargo ücreti) bunu gerekçede açıkla. Yanıt şeması: {"kolonlar": [{"kolon": "...", "politika": "varsayilan", "varsayilan": "TRY", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sütun bazında NULL politikası öneren ve uygulanabilirliğini veriyle kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'siparis_basliklari.csv')), help='CSV/XLSX')
    p.add_argument('--table', default='siparis_baslik')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    prof = profile(columns, rows)
    out = output_dir(args.output)
    user = (f'{args.table}: {len(rows)} satır. Sütunlar (tür · boş · farklı değer · tekil mi · kategoriler/aralık · boşların bağlı olduğu kategori):\n'
            + '\n'.join(f'- {c}: {v["tur"]} · {v["bos"]} · {v["farkli"]} · {"evet" if v["tekil"] else "hayır"} · '
                        + (', '.join(v['kategoriler']) if v['kategoriler'] else f'{v["aralik"][0]:g}–{v["aralik"][1]:g}' if v['aralik'] else '—') + ' · '
                        + (f'%{100 * v["bagli"][2]:.0f} {v["bagli"][0]}={v["bagli"][1]}' if v['bagli'] else '—') for c, v in prof.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(prof, len(rows)))
    if result is None:
        return
    sql, plan = [f'-- {args.table}: boş değer politikası. Önce UPDATE’ler, sonra kısıtlar.'], []
    for c, (pol, dv, why) in result.items():
        lit = (dv if prof[c]['tur'] == 'sayi' else "'" + str(dv).replace("'", "''") + "'") if dv is not None else None
        if pol == 'varsayilan':
            sql += [f'UPDATE {args.table} SET {c} = {lit} WHERE {c} IS NULL;  -- {prof[c]["bos"]} satır', f'ALTER TABLE {args.table} ALTER COLUMN {c} SET DEFAULT {lit};',
                    f'ALTER TABLE {args.table} ALTER COLUMN {c} SET NOT NULL;']
        elif pol == 'zorunlu':
            sql.append(f'ALTER TABLE {args.table} ALTER COLUMN {c} SET NOT NULL;')
        else:
            sql.append(f'-- {c}: boşa izin veriliyor ({why or "isteğe bağlı alan"})')
        plan.append({'kolon': c, 'politika': pol, 'varsayilan': dv if dv is not None else '', 'bos_satir': prof[c]['bos'], 'gerekce': why})
    write_text(out / 'bos_deger.sql', '\n'.join(sql))
    tabular.write_csv(out / 'bos_deger_plani.csv', list(plan[0]), plan)
    md = ['# Boş değer politikası', '', f'{args.table}: {len(rows)} satır, {len(plan)} sütun; {sum(p_["politika"] == "varsayilan" for p_ in plan)} sütunda geçmiş boşlar doldurulacak '
          f'({sum(p_["bos_satir"] for p_ in plan if p_["politika"] == "varsayilan")} satır).', '',
          md_table(['Kolon', 'Politika', 'Varsayılan', 'Boş satır', 'Gerekçe'], [list(p_.values()) for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca sütun profili gitti; kısıtın eklenebilirliği, anahtar sütunlar ve varsayılanın tür/değer uyumu kodla denetlendi._']
    write_text(out / 'bos-deger-politikasi.md', '\n'.join(md))
    print(f'[OK] {len(plan)} sütun. Çıktı: {out / "bos-deger-politikasi.md"}')


if __name__ == '__main__':
    llm.run(main)
