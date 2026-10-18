"""Birleştirme anahtarı bulucu (AI agent): farklı sistemlerden gelen iki tabloyu (ERP cari listesi ile CRM müşteri listesi gibi) hangi
kolonlarla ve hangi temizlikle birleştireceğinizi bulur. Modele kolon adları, birkaç örnek değer (kişisel veriler maskelenir) ve kodla
hesaplanmış ham değer örtüşmesi gider. Model anahtar çiftlerini ve iki taraf için dönüşüm adımlarını (trim, upper, sıfır doldurma, önek
silme...) önerir; dönüşümler yerelde iki tablonun TAMAMINA uygulanır ve gerçek eşleşme oranı ölçülür. Eşleşme oranı eşiğin altında
kalan, var olmayan kolon ya da bilinmeyen dönüşüm kullanan, ilişkiyi (1-1, n-1) yanlış söyleyen öneri, eşleşmeyen örneklerle geri
gönderilir. Çıktı: birlesik.csv (sol birleştirme), eslesmeyen.csv, birlestirme.sql (PostgreSQL), birlestirme.md."""
import argparse, re
from collections import Counter, defaultdict
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table, mask_text

STEPS = {'trim': 'baş/son boşluk sil', 'upper': 'büyük harf', 'lower': 'küçük harf', 'fold': 'Türkçe karakterleri ASCII’ye çevir + küçük harf',
         'digits': 'yalnızca rakamlar', 'alnum': 'yalnızca harf ve rakam', 'sifir_sil': 'baştaki sıfırları sil', 'sifir_doldur:N': 'soldan sıfırla N haneye tamamla',
         'onek_sil:X': 'baştaki X önekini sil', 'ilk:N': 'ilk N karakter'}


def apply(steps, value):
    v = value
    for s in steps:
        name, _, arg = s.partition(':')
        if name == 'trim':
            v = v.strip()
        elif name == 'upper':
            v = v.upper()
        elif name == 'lower':
            v = v.lower()
        elif name == 'fold':
            v = tr.fold(v)
        elif name == 'digits':
            v = re.sub(r'\D', '', v)
        elif name == 'alnum':
            v = re.sub(r'[^0-9A-Za-zÇĞİÖŞÜçğıöşü]', '', v)
        elif name == 'sifir_sil':
            v = v.lstrip('0') or ('0' if v else '')
        elif name == 'sifir_doldur':
            v = v.zfill(int(arg)) if v else v
        elif name == 'onek_sil':
            v = v[len(arg):] if v.startswith(arg) else v
        elif name == 'ilk':
            v = v[:int(arg)]
    return v


def check_steps(steps):
    bad = []
    for s in steps:
        name, sep, arg = s.partition(':')
        if name in ('sifir_doldur', 'ilk'):
            if not sep or not arg.isdigit() or not 0 < int(arg) <= 50:
                bad.append(s)
        elif name == 'onek_sil':
            if not arg:
                bad.append(s)
        elif name not in STEPS or sep:
            bad.append(s)
    return bad


def sql_expr(col, steps):
    e = f'"{col}"'
    for s in steps:
        name, _, arg = s.partition(':')
        e = {'trim': f'TRIM({e})', 'upper': f'UPPER({e})', 'lower': f'LOWER({e})', 'fold': f'LOWER(TRANSLATE({e}, \'ÇĞİÖŞÜçğıöşü\', \'CGIOSUcgiosu\'))',
             'digits': f"REGEXP_REPLACE({e}, '[^0-9]', '', 'g')", 'alnum': f"REGEXP_REPLACE({e}, '[^0-9A-Za-zÇĞİÖŞÜçğıöşü]', '', 'g')",
             'sifir_sil': f"COALESCE(NULLIF(LTRIM({e}, '0'), ''), '0')", 'sifir_doldur': f"LPAD({e}, {arg}, '0')",
             'onek_sil': f"CASE WHEN {e} LIKE '{arg.replace(chr(39), chr(39) * 2)}%' THEN SUBSTRING({e} FROM {len(arg) + 1}) ELSE {e} END",
             'ilk': f'LEFT({e}, {arg})'}[name]
    return e


def keys_of(rows, pairs, side):
    return [tuple(apply(p[f'donusum_{side}'], r[p[side]]) for p in pairs) for r in rows]


def validator(lc, lrows, rc, rrows, min_match):
    def validate(obj):
        pairs, problems = [], []
        for i, k in enumerate(obj.get('anahtarlar') or [], 1):
            if not isinstance(k, dict):
                continue
            l, r = str(k.get('sol', '')).strip(), str(k.get('sag', '')).strip()
            dl, dr = [str(x).strip() for x in k.get('donusum_sol') or []], [str(x).strip() for x in k.get('donusum_sag') or []]
            if l not in lc:
                problems.append(f'{i}. anahtar: sol tabloda “{l}” kolonu yok')
            if r not in rc:
                problems.append(f'{i}. anahtar: sağ tabloda “{r}” kolonu yok')
            bad = check_steps(dl) + check_steps(dr)
            if bad:
                problems.append(f'{i}. anahtar: bilinmeyen/eksik dönüşüm: {", ".join(bad)} (izinli: {", ".join(STEPS)})')
            pairs.append({'sol': l, 'sag': r, 'donusum_sol': dl, 'donusum_sag': dr})
        if not pairs:
            problems.append('en az bir anahtar çifti gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        lk, rk = keys_of(lrows, pairs, 'sol'), keys_of(rrows, pairs, 'sag')
        right = Counter(k for k in rk if any(k))
        usable = [k for k in lk if any(k)]
        matched = [k for k in usable if k in right]
        rate = len(matched) / max(len(lrows), 1)
        dup_right = sum(1 for k, n in right.items() if n > 1)
        dup_left = sum(1 for k, n in Counter(usable).items() if n > 1)
        relation = 'n-' if dup_left else '1-'
        relation += 'n' if dup_right else '1'
        said = str(obj.get('iliski', '')).strip()
        if rate < min_match:
            miss = [k for k in lk if k not in right][:6]
            problems.append(f'eşleşme oranı %{rate * 100:.1f} (gereken %{min_match * 100:.0f}); dönüşüm sonrası eşleşmeyen sol anahtar örnekleri: '
                            + ', '.join(mask_text('|'.join(k))[0] or '(boş)' for k in miss)
                            + '; sağ taraftaki örnekler: ' + ', '.join(mask_text('|'.join(k))[0] for k in list(right)[:4]))
        if said not in ('1-1', 'n-1', '1-n', 'n-n'):
            problems.append('iliski 1-1, n-1, 1-n ya da n-n olmalı')
        elif said != relation:
            problems.append(f'iliski {said} denmiş ama dönüşüm sonrası ölçülen {relation} (sol tekrar eden anahtar {dup_left}, sağ {dup_right})')
        if problems:
            raise ValueError('; '.join(problems))
        return pairs, lk, rk, rate, relation, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen veri entegrasyonu yapan bir veri mühendisisin. İki tabloyu birleştirmek için anahtar kolon çiftlerini ve her iki taraf için '
          'değerleri aynı biçime getiren dönüşüm adımlarını öner. Yalnızca izinli dönüşümleri kullan. Dönüşümler tabloların tamamında denenecek ve '
          'eşleşme oranı ölçülecek; ilişkiyi (1-1, n-1, 1-n, n-n: sol-sağ tekrar durumu) dönüşüm sonrası hâline göre yaz. '
          'Yanıt şeması: {"anahtarlar": [{"sol": "cari_kodu", "sag": "musteri_id", "donusum_sol": ["trim", "sifir_sil"], "donusum_sag": ["trim"]}], '
          '"iliski": "1-1", "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='İki tablo için birleştirme anahtarı ve temizlik dönüşümü bulan agent; eşleşme oranı tüm veride ölçülür.')
    p.add_argument('--left', default=str(here('ornek', 'erp_cari_listesi.csv')))
    p.add_argument('--right', default=str(here('ornek', 'crm_musteri_listesi.csv')))
    p.add_argument('--min-match', type=float, default=0.9, help='Sol satırların en az bu oranı eşleşmeli (0-1)')
    llm.add_arguments(p)
    args = p.parse_args()
    lc, lrows = tabular.read(args.left)
    rc, rrows = tabular.read(args.right)
    out = output_dir(args.output)
    distinct = lambda rows, c: list(dict.fromkeys(r[c] for r in rows if r[c].strip()))
    overlap = []
    for a in lc:
        la = set(v.strip() for v in distinct(lrows, a))
        for b in rc:
            rb = set(v.strip() for v in distinct(rrows, b))
            if la and rb and la & rb:
                overlap.append(f'{a} ↔ {b}: {len(la & rb)} ortak değer')
    describe = lambda name, cols, rows: (f'{name}: {len(rows)} satır\n' + '\n'.join(
        f'- {c}: {len(distinct(rows, c))} farklı değer; örnekler: ' + ' | '.join(repr(v) for v in distinct(rows, c)[:5]) for c in cols))
    user = (describe('Sol tablo', lc, lrows) + '\n\n' + describe('Sağ tablo', rc, rrows)
            + '\n\nHam (dönüşümsüz) örtüşme: ' + ('; '.join(overlap) or 'hiçbir kolon çiftinde ortak değer yok')
            + '\n\nİzinli dönüşümler: ' + ', '.join(f'{k} ({v})' for k, v in STEPS.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(lc, lrows, rc, rrows, args.min_match))
    if result is None:
        return
    pairs, lk, rk, rate, relation, why = result
    index = defaultdict(list)
    for r, k in zip(rrows, rk):
        if any(k):
            index[k].append(r)
    rcols = [c if c not in lc else f'sag_{c}' for c in rc]
    merged, missing = [], []
    for r, k in zip(lrows, lk):
        hits = index.get(k) or [None]
        if hits == [None]:
            missing.append(r)
        for h in hits:
            merged.append({**r, **{rc_: (h[c] if h else '') for c, rc_ in zip(rc, rcols)}, 'eslesti': 'evet' if h else 'hayır'})
    tabular.write_csv(out / 'birlesik.csv', lc + rcols + ['eslesti'], merged)
    tabular.write_csv(out / 'eslesmeyen.csv', lc, missing)
    on = '\n  AND '.join(f'{sql_expr(p["sol"], p["donusum_sol"]).replace(chr(34) + p["sol"] + chr(34), "s." + chr(34) + p["sol"] + chr(34))} = '
                         f'{sql_expr(p["sag"], p["donusum_sag"]).replace(chr(34) + p["sag"] + chr(34), "g." + chr(34) + p["sag"] + chr(34))}' for p in pairs)
    write_text(out / 'birlestirme.sql', f'-- PostgreSQL · sol tablo s, sağ tablo g; eşleşme oranı örnek veride %{rate * 100:.1f}, ilişki {relation}\n'
               f'SELECT s.*, g.*\nFROM sol_tablo s\nLEFT JOIN sag_tablo g\n  ON {on};\n')
    rmatched = sum(1 for k in rk if any(k) and k in set(lk))
    md = ['# Birleştirme anahtarı', '', f'Sol {len(lrows)} satırın {len(lrows) - len(missing)}’i eşleşti (%{rate * 100:.1f}); sağ {len(rrows)} satırın {rmatched}’i kullanıldı. İlişki: {relation}.', '',
          md_table(['Sol kolon', 'Sol dönüşüm', 'Sağ kolon', 'Sağ dönüşüm'], [[p['sol'], ' → '.join(p['donusum_sol']) or '—', p['sag'], ' → '.join(p['donusum_sag']) or '—'] for p in pairs]), '']
    if why:
        md += [why, '']
    if missing:
        md += [f'Eşleşmeyen {len(missing)} sol satır eslesmeyen.csv’de.', '']
    md += [f'_Model: {meta["model"]} · Dönüşümler iki tablonun tamamında kodla uygulandı, eşleşme oranı ve ilişki ölçüldü. birlestirme.sql PostgreSQL içindir; '
           'veritabanında çalıştırılarak test edilmedi._']
    write_text(out / 'birlestirme.md', '\n'.join(md))
    print(f'[OK] %{rate * 100:.1f} eşleşme, ilişki {relation}. Çıktı: {out / "birlestirme.md"}')


if __name__ == '__main__':
    llm.run(main)
