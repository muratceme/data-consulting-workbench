"""k-anonimlik genelleştirici (AI agent): paylaşılacak bir veri setindeki yarı tanımlayıcılar (yaş, cinsiyet, posta kodu, meslek) için
genelleştirme önerir (yaşı aralığa çevir, posta kodunun ilk haneleri, değeri gizle, koru). Modele yalnızca sütun özetleri (değer sayısı,
aralık, uzunluk) gider; satırlar gönderilmez. Öneri yerelde uygulanır ve kodla denetlenir: her yarı tanımlayıcı kombinasyonu en az k
kişiyi kapsamalı (k’nın altındaki sınıflar bastırılır, bastırılan satır payı sınırı aşmamalı) ve genelleştirme gereğinden kaba olmamalı
(bir sütun bir adım daha ayrıntılı tutulduğunda k ve bastırma sınırı hâlâ sağlanıyorsa öneri reddedilir). Doğrudan tanımlayıcılar silinir.
Çıktı: anonim_veri.csv, genellestirme.json, k-anonimlik.md."""
import argparse
from collections import Counter
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

TYPES = ('koru', 'aralik', 'onek', 'gizle')


def apply(value, rule):
    t, n = rule
    v = value.strip()
    if t == 'koru' or not v:
        return v
    if t == 'gizle':
        return '*'
    if t == 'onek':
        return v[:n] + '*' * (len(v) - n)
    x = int(float(v.replace(',', '.')))
    lo = x // n * n
    return f'{lo}-{lo + n - 1}'


def measure(rows, qi, rules, k):
    keys = [tuple(apply(r[c], rules[c]) for c in qi) for r in rows]
    cnt = Counter(keys)
    small = sum(n for n in cnt.values() if n < k)
    return keys, cnt, small


def finer(rule, col_len):
    t, n = rule
    if t == 'aralik':
        return ('aralik', n // 2) if n >= 2 else ('koru', 0)
    if t == 'onek':
        return ('onek', n + 1) if n + 1 < col_len else ('koru', 0)
    if t == 'gizle':
        return ('koru', 0)
    return None


def show(rule):
    return f'{rule[0]} {rule[1]}' if rule[1] else rule[0]


def validator(rows, qi, prof, k, max_supp):
    def validate(obj):
        seen = set()
        problems, rules = [], {}
        for e in obj.get('kolonlar') or []:
            c, t = str(e.get('kolon', '')).strip(), str(e.get('tip', '')).strip()
            if c not in qi or c in seen:
                problems.append(f'kolon “{c}” yarı tanımlayıcı listesinde yok ya da iki kez yazıldı')
                continue
            seen.add(c)
            if t not in TYPES:
                problems.append(f'{c}: tip {", ".join(TYPES)} olmalı')
                continue
            try:
                n = int(e.get('deger') or 0)
            except (TypeError, ValueError):
                problems.append(f'{c}: deger tam sayı olmalı')
                continue
            if t == 'aralik' and (not prof[c]['sayisal'] or n < 2):
                problems.append(f'{c}: aralik yalnızca sayısal sütunda ve genişlik ≥ 2 ile kullanılır')
                continue
            if t == 'onek' and not 1 <= n < prof[c]['uzunluk']:
                problems.append(f'{c}: onek uzunluğu 1–{prof[c]["uzunluk"] - 1} olmalı')
                continue
            rules[c] = (t, n)
        missing = [c for c in qi if c not in seen]
        if missing:
            problems.append('kuralı yazılmayan yarı tanımlayıcı: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        _, cnt, small = measure(rows, qi, rules, k)
        if small / len(rows) > max_supp:
            raise ValueError(f'{len(cnt)} eşdeğerlik sınıfından {sum(n < k for n in cnt.values())} tanesi {k} kişiden küçük; bastırılacak satır payı %{100 * small / len(rows):.1f} '
                             f'(en fazla %{100 * max_supp:g}); daha kaba genelleştir')
        coarse = []
        for c in qi:
            f = finer(rules[c], prof[c]['uzunluk'])
            if f is None:
                continue
            _, _, s2 = measure(rows, qi, {**rules, c: f}, k)
            if s2 / len(rows) <= max_supp:
                coarse.append(f'{c} ({show(rules[c])} yerine {show(f)} ile de k={k} sağlanıyor, bastırma %{100 * s2 / len(rows):.1f})')
        if coarse:
            raise ValueError('gereğinden kaba genelleştirme, veri gereksiz bozuluyor: ' + '; '.join(coarse))
        return rules
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri gizliliği uzmanısın. Paylaşılacak veri setinde her yarı tanımlayıcı sütun için genelleştirme öner: "aralik" (sayıyı genişlik '
          'kadar aralığa çevir), "onek" (ilk deger karakteri tut), "gizle" (tamamen gizle) ya da "koru". Hedef: her kombinasyon en az k kişiyi kapsasın ama veri '
          'gereğinden fazla bozulmasın. Yanıt şeması: {"kolonlar": [{"kolon": "yas", "tip": "aralik", "deger": 10, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Yarı tanımlayıcıları k-anonimlik sağlayacak en az genelleştirmeyle anonimleştiren agent.')
    p.add_argument('--input', default=str(here('ornek', 'anket_katilimcilari.csv')), help='CSV/XLSX')
    p.add_argument('--qi', default='yas,cinsiyet,posta_kodu,meslek', help='Yarı tanımlayıcı sütunlar')
    p.add_argument('--drop', default='katilimci_id', help='Silinecek doğrudan tanımlayıcılar')
    p.add_argument('--k', type=int, default=5)
    p.add_argument('--max-suppression', type=float, default=0.03, help='Bastırılabilecek en yüksek satır payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    qi = [c.strip() for c in args.qi.split(',') if c.strip()]
    drop = [c.strip() for c in args.drop.split(',') if c.strip()]
    if [c for c in qi + drop if c not in columns]:
        raise ValueError('Sütun yok: ' + ', '.join(c for c in qi + drop if c not in columns))
    prof = {}
    for c in qi:
        vals = [r[c].strip() for r in rows if r[c].strip()]
        num = all(v.replace(',', '.').replace('.', '', 1).lstrip('-').isdigit() for v in vals) and not any(len(v) > 1 and v[0] == '0' for v in vals)  # 06420 is a code
        prof[c] = {'sayisal': num, 'farkli': len(set(vals)), 'uzunluk': max(map(len, vals)),
                   'aralik': (min(float(v.replace(',', '.')) for v in vals), max(float(v.replace(',', '.')) for v in vals)) if num else None}
    base = Counter(tuple(r[c].strip() for c in qi) for r in rows)
    out = output_dir(args.output)
    user = (f'{len(rows)} satır, k={args.k}, bastırma sınırı %{100 * args.max_suppression:g}. Ham haliyle {len(base)} kombinasyonun {sum(n < args.k for n in base.values())} tanesi '
            f'{args.k} kişiden küçük.\nYarı tanımlayıcılar (tür · farklı değer · uzunluk / aralık):\n'
            + '\n'.join(f'- {c}: {"sayısal" if v["sayisal"] else "metin"} · {v["farkli"]} · ' + (f'{v["aralik"][0]:g}–{v["aralik"][1]:g}' if v['sayisal'] else f'en fazla {v["uzunluk"]} karakter')
                        for c, v in prof.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, qi, prof, args.k, args.max_suppression))
    if result is None:
        return
    keys, cnt, small = measure(rows, qi, result, args.k)
    keep = [c for c in columns if c not in drop]
    anon = [{**{c: r[c] for c in keep}, **dict(zip(qi, key))} for r, key in zip(rows, keys) if cnt[key] >= args.k]
    tabular.write_csv(out / 'anonim_veri.csv', keep, anon)
    write_json(out / 'genellestirme.json', {'k': args.k, 'silinen': drop, 'kurallar': {c: {'tip': t, 'deger': n} for c, (t, n) in result.items()},
                                            'esdegerlik_sinifi': sum(1 for n in cnt.values() if n >= args.k), 'en_kucuk_sinif': min(n for n in cnt.values() if n >= args.k),
                                            'bastirilan_satir': small})
    md = ['# k-anonimlik', '', f'{len(rows)} satırdan {len(anon)} satır paylaşılabilir (k={args.k}); {small} satır bastırıldı (%{100 * small / len(rows):.1f}). '
          f'Silinen doğrudan tanımlayıcılar: {", ".join(drop) or "—"}.', '',
          md_table(['Kolon', 'Genelleştirme', 'Önce farklı değer', 'Sonra farklı değer'],
                   [[c, f'{t} {n}' if n else t, prof[c]['farkli'], len({apply(r[c], (t, n)) for r in rows})] for c, (t, n) in result.items()]), '',
          f'_Model: {meta["model"]} · Modele yalnızca sütun özetleri gitti. k, bastırma payı ve her sütunun bir adım ayrıntılı halinin de yetip yetmediği kodla denetlendi. '
          'k-anonimlik hassas sütunlarda (memnuniyet gibi) homojenlik saldırısını önlemez; gerekirse l-çeşitlilik ayrıca kontrol edilmelidir._']
    write_text(out / 'k-anonimlik.md', '\n'.join(md))
    print(f'[OK] {len(anon)} satır paylaşılabilir, {small} bastırıldı. Çıktı: {out / "k-anonimlik.md"}')


if __name__ == '__main__':
    llm.run(main)
