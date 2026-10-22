"""Hipotez testi seçici (AI agent): iş sorusuna (“çözüm süresi kanala göre farklı mı?”) göre hedef ve grup sütununu ve uygun istatistiksel
testi seçer; testi kod hesaplar. Modele soru ile sütun profilleri (tür, düzey sayıları, grup bazında n/medyan/ortalama/std/çarpıklık)
gider; satırlar gönderilmez. Seçim kodla denetlenir: test sütun türlerine uymalı (iki kategorik → ki-kare, sayısal + 2 grup → t/Mann-Whitney,
sayısal + 3+ grup → ANOVA/Kruskal-Wallis), çarpık gruplarda parametrik test, varyansları farklı gruplarda eşit varyans varsayan test ve
beklenen frekansı düşük tabloda ki-kare kabul edilmez. Mann-Whitney ve ki-kare, analiz bloklarıyla aynı hesaptır; Kruskal-Wallis anlamlıysa
Bonferroni düzeltmeli ikili Mann-Whitney karşılaştırmaları eklenir. Çıktı: test_sonucu.json, grup_ozeti.csv, hipotez-testi.md."""
import argparse, math
from collections import Counter, defaultdict
from itertools import combinations
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from analiz import describe, ranks, t_two_sided, betainc, chi2_sf, capraz, mann_whitney, stars

TESTS = {'welch_t': 2, 'student_t': 2, 'mann_whitney': 2, 'anova': 3, 'kruskal': 3, 'ki_kare': 0}
PARAMETRIC = {'welch_t', 'student_t', 'anova'}


def column_types(columns, rows):
    types = {}
    for c in columns:
        raw = [r[c].strip() for r in rows if r[c].strip()]
        if not raw or len(set(raw)) == len(raw):
            continue  # empty or an identifier
        style = tabular.number_style(raw)
        nums = [tabular.parse_number(v, style) for v in raw]
        if all(x is not None for x in nums) and len(set(raw)) > 6:
            types[c] = ('sayisal', [tabular.parse_number(r[c], style) if r[c].strip() else None for r in rows])
        elif len(set(raw)) <= 12:
            types[c] = ('kategorik', [r[c].strip() or None for r in rows])
    return types


def groups_of(types, target, group):
    g = defaultdict(list)
    for x, k in zip(types[target][1], types[group][1]):
        if x is not None and k is not None:
            g[k].append(x)
    return dict(sorted(g.items()))


def validator(types, rows):
    def validate(obj):
        t, y, gcol = str(obj.get('test', '')).strip(), str(obj.get('hedef', '')).strip(), str(obj.get('grup', '')).strip()
        if t not in TESTS:
            raise ValueError(f'test şunlardan biri olmalı: {", ".join(TESTS)}')
        if y not in types or gcol not in types or y == gcol:
            raise ValueError(f'hedef ve grup profildeki iki farklı sütun olmalı ({", ".join(types)})')
        if types[gcol][0] != 'kategorik':
            raise ValueError(f'grup sütunu {gcol} kategorik değil')
        if t == 'ki_kare':
            if types[y][0] != 'kategorik':
                raise ValueError(f'ki-kare iki kategorik sütun ister; {y} sayısal')
            res = capraz(list(types), rows, SimpleNamespace(rows=gcol, cols=y))
            if res.notes:
                raise ValueError(f'ki-kare uygun değil: {res.notes[0]}')
            return t, y, gcol, None
        if types[y][0] != 'sayisal':
            raise ValueError(f'{t} sayısal hedef ister; {y} kategorik (ki_kare kullan)')
        groups = groups_of(types, y, gcol)
        k = len(groups)
        if TESTS[t] == 2 and k != 2:
            raise ValueError(f'{gcol} sütununda {k} grup var; {t} yalnızca 2 grup karşılaştırır (3+ grup için anova/kruskal)')
        if TESTS[t] == 3 and k < 3:
            raise ValueError(f'{gcol} sütununda {k} grup var; 2 grup için welch_t/mann_whitney kullan')
        d = {g: describe(v) for g, v in groups.items()}
        problems = []
        if t in PARAMETRIC:
            skewed = [f'{g} ({s["carpiklik"]:.2f})' for g, s in d.items() if abs(s['carpiklik']) > 1]
            if skewed:
                problems.append(f'{t} normal dağılım varsayar; çarpık gruplar: {", ".join(skewed)} (|çarpıklık| > 1) — parametrik olmayan test kullan')
        if t in ('student_t', 'anova'):
            ratio = max(s['std'] for s in d.values()) ** 2 / max(min(s['std'] for s in d.values()) ** 2, 1e-12)
            if ratio > 2:
                problems.append(f'{t} eşit varyans varsayar; en büyük/en küçük varyans oranı {ratio:.1f} (> 2)')
        if problems:
            raise ValueError('; '.join(problems))
        return t, y, gcol, groups
    return validate


def run_test(t, y, gcol, groups, types, rows):
    cols = list(types)
    if t == 'ki_kare':
        r = capraz(cols, rows, SimpleNamespace(rows=gcol, cols=y))
        return {'istatistik': 'χ²', 'deger': r.summary['ki_kare'], 'sd': r.summary['serbestlik'], 'p': r.summary['p_degeri'], 'etki': f'Cramér V {r.summary["cramer_v"]}'}, []
    if t == 'mann_whitney':
        a, b = list(groups)
        r = mann_whitney(cols, rows, SimpleNamespace(group=gcol, value=y, a=a, b=b))
        return {'istatistik': 'U', 'deger': r.summary['u'], 'sd': '', 'p': r.summary['p_degeri'], 'etki': f'r {r.summary["etki_r"]}'}, []
    vals = list(groups.values())
    if t in ('welch_t', 'student_t'):
        (a, b) = [describe(v) for v in vals]
        if t == 'welch_t':
            se2 = a['std'] ** 2 / a['n'] + b['std'] ** 2 / b['n']
            df = se2 ** 2 / ((a['std'] ** 2 / a['n']) ** 2 / (a['n'] - 1) + (b['std'] ** 2 / b['n']) ** 2 / (b['n'] - 1))
        else:
            df = a['n'] + b['n'] - 2
            se2 = ((a['n'] - 1) * a['std'] ** 2 + (b['n'] - 1) * b['std'] ** 2) / df * (1 / a['n'] + 1 / b['n'])
        tv = (a['ortalama'] - b['ortalama']) / math.sqrt(se2)
        return {'istatistik': 't', 'deger': round(tv, 4), 'sd': round(df, 2), 'p': round(t_two_sided(tv, df), 6), 'etki': f'fark {a["ortalama"] - b["ortalama"]:.3f}'}, []
    n, k = sum(len(v) for v in vals), len(vals)
    if t == 'anova':
        grand = sum(sum(v) for v in vals) / n
        ssb = sum(len(v) * (sum(v) / len(v) - grand) ** 2 for v in vals)
        ssw = sum(sum((x - sum(v) / len(v)) ** 2 for x in v) for v in vals)
        d1, d2 = k - 1, n - k
        f = (ssb / d1) / (ssw / d2)
        return {'istatistik': 'F', 'deger': round(f, 4), 'sd': f'{d1}, {d2}', 'p': round(betainc(d2 / 2, d1 / 2, d2 / (d2 + d1 * f)), 6), 'etki': f'η² {ssb / (ssb + ssw):.3f}'}, []
    flat = [x for v in vals for x in v]
    rk = ranks(flat)
    ties = sum(c ** 3 - c for c in Counter(flat).values())
    pos, h = 0, 0.0
    for v in vals:
        h += sum(rk[pos:pos + len(v)]) ** 2 / len(v)
        pos += len(v)
    h = (12 / (n * (n + 1)) * h - 3 * (n + 1)) / (1 - ties / (n ** 3 - n))
    p = chi2_sf(h, k - 1)
    pairs = []
    if p < 0.05:
        m = k * (k - 1) // 2
        for a, b in combinations(groups, 2):
            r = mann_whitney(cols, rows, SimpleNamespace(group=gcol, value=y, a=a, b=b))
            pairs.append([f'{a} – {b}', r.summary['u'], r.summary['p_degeri'], round(min(1.0, r.summary['p_degeri'] * m), 4), stars(min(1.0, r.summary['p_degeri'] * m))])
    return {'istatistik': 'H', 'deger': round(h, 4), 'sd': k - 1, 'p': round(p, 6), 'etki': f'ε² {(h - k + 1) / (n - k):.3f}'}, pairs


SYSTEM = (llm.SAFETY + ' Sen bir istatistikçisin. Soruya göre hedef (sonuç) ve grup sütununu seç ve uygun testi belirle: welch_t, student_t, mann_whitney '
          '(sayısal hedef, 2 grup), anova, kruskal (sayısal hedef, 3+ grup), ki_kare (iki kategorik sütun). Çarpık dağılımda parametrik olmayan, varyanslar '
          'farklıysa eşit varyans varsaymayan test seç. Testi sen hesaplama; kod hesaplar. Yanıt şeması: {"hedef": "...", "grup": "...", "test": "kruskal", "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='İş sorusuna uygun istatistiksel testi seçen, varsayımları kodla denetleyip testi hesaplayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'destek_cozum_sureleri.csv')), help='CSV/XLSX')
    p.add_argument('--question', default='Talep çözüm süresi kanala göre farklılaşıyor mu?', help='Cevaplanacak iş sorusu')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    types = column_types(columns, rows)
    cats = [c for c, (k, _) in types.items() if k == 'kategorik']
    lines = []
    for c, (kind, vals) in types.items():
        if kind == 'kategorik':
            lines.append(f'- {c} (kategorik): ' + ', '.join(f'{k} {n}' for k, n in sorted(Counter(v for v in vals if v).items())))
        else:
            d = describe(vals)
            lines.append(f'- {c} (sayısal): n {d["n"]}, medyan {d["medyan"]:.2f}, ortalama {d["ortalama"]:.2f}, çarpıklık {d["carpiklik"]:.2f}')
            for g in cats:
                lines += [f'  - {g}={k}: n {s["n"]}, medyan {s["medyan"]:.2f}, ortalama {s["ortalama"]:.2f}, std {s["std"]:.2f}, çarpıklık {s["carpiklik"]:.2f}'
                          for k, s in ((k, describe(v)) for k, v in groups_of(types, c, g).items())]
    out = output_dir(args.output)
    user = f'Soru: {args.question}\n\n{len(rows)} satır. Sütunlar:\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(types, rows))
    if result is None:
        return
    t, y, gcol, groups = result
    res, pairs = run_test(t, y, gcol, groups, types, rows)
    summary = [[k, s['n'], f'{s["medyan"]:.2f}', f'{s["ortalama"]:.2f}', f'{s["std"]:.2f}'] for k, s in ((k, describe(v)) for k, v in groups.items())] if groups else []
    if groups:
        tabular.write_csv(out / 'grup_ozeti.csv', ['grup', 'n', 'medyan', 'ortalama', 'std'], [dict(zip(['grup', 'n', 'medyan', 'ortalama', 'std'], s)) for s in summary])
    write_json(out / 'test_sonucu.json', {'soru': args.question, 'hedef': y, 'grup': gcol, 'test': t, **res, 'sonuc': stars(res['p']), 'ikili': pairs})
    md = ['# Hipotez testi', '', f'**Soru:** {args.question}', '', f'Seçilen test: **{t}** · hedef `{y}`, grup `{gcol}`.', '',
          f'{res["istatistik"]} = {res["deger"]}' + (f', sd = {res["sd"]}' if res['sd'] != '' else '') + f', p = {res["p"]:.4f} → **{stars(res["p"])}** ({res["etki"]}).', '']
    if summary:
        md += [md_table(['Grup', 'n', 'Medyan', 'Ortalama', 'Std'], summary), '']
    if pairs:
        md += ['İkili karşılaştırmalar (Mann-Whitney, Bonferroni düzeltmeli):', '', md_table(['Çift', 'U', 'p', 'Düzeltilmiş p', 'Sonuç'], pairs), '']
    md += [f'_Model: {meta["model"]} · Model yalnızca test seçti; varsayımlar (tür, grup sayısı, çarpıklık, varyans oranı, beklenen frekans) ve test kodla hesaplandı. '
           'Anlamlı fark nedensellik göstermez._']
    write_text(out / 'hipotez-testi.md', '\n'.join(md))
    print(f'[OK] {t}: p={res["p"]:.4f}. Çıktı: {out / "hipotez-testi.md"}')


if __name__ == '__main__':
    llm.run(main)
