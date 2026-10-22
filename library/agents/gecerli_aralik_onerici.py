"""Geçerli aralık önerici (AI agent): sayısal sütunlar için iş anlamına uygun geçerli değer aralığı (min/max) önerir. Sütun profili
(yüzdelikler, negatif/sıfır sayısı, Tukey dış sınırlarının ötesindeki uç değerler) kodla çıkarılır ve modele yalnızca bu profil gider;
satırlar gönderilmez, kişisel veri içeren sütunlar profillenmez. Öneri kodla denetlenir: her sütun cevaplanmalı, min ≤ max olmalı, aralık
verinin belirli bir payından fazlasını dışarıda bırakmamalı (normal veriyi hata saymamalı) ve uç değerleri ancak gerekçesiyle kabul
etmeli. Çıktı: aralik_kurallari.json (Great Expectations), aralik_kontrolleri.sql (CHECK), aralik_ihlalleri.csv, gecerli-araliklar.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table, pii_kind
from analiz import quantile


def profile(columns, rows):
    out = {}
    for c in columns:
        raw = [r[c] for r in rows if r[c].strip()]
        if not raw or any(pii_kind(v) for v in raw[:200]):
            continue
        style = tabular.number_style(raw)
        vals = [tabular.parse_number(v, style) for v in raw]
        if sum(v is not None for v in vals) < 0.95 * len(raw):
            continue
        v = sorted(x for x in vals if x is not None)
        q1, q3 = quantile(v, .25), quantile(v, .75)
        lo, hi = (q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)) if q3 > q1 else (quantile(v, .01), quantile(v, .99))  # constant middle half: fall back to p1–p99
        out[c] = {'values': [tabular.parse_number(r[c], style) if r[c].strip() else None for r in rows], 'n': len(v), 'min': v[0], 'max': v[-1],
                  'p': {q: quantile(v, q) for q in (.01, .05, .25, .5, .75, .95, .99)}, 'negatif': sum(x < 0 for x in v), 'sifir': sum(x == 0 for x in v),
                  'tam_sayi': all(x == int(x) for x in v), 'fence': (lo, hi), 'uc': sorted({x for x in v if x < lo or x > hi})}
    return out


def outside(vals, lo, hi):
    return [i for i, x in enumerate(vals) if x is not None and ((lo is not None and x < lo) or (hi is not None and x > hi))]


def num(v):
    return None if v is None or v == '' else float(v)


def validator(prof, max_out):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('kolonlar') or []:
            c = str(e.get('kolon', '')).strip()
            if c not in prof or c in seen:
                problems.append(f'kolon “{c}” profilde yok ya da iki kez yazıldı')
                continue
            seen.add(c)
            try:
                lo, hi = num(e.get('min')), num(e.get('max'))
            except (TypeError, ValueError):
                problems.append(f'{c}: min/max sayı ya da null olmalı')
                continue
            p, why, ok_uc = prof[c], str(e.get('gerekce', '')).strip(), bool(e.get('uc_degerler_gecerli'))
            if lo is not None and hi is not None and lo > hi:
                problems.append(f'{c}: min {lo:g} > max {hi:g}')
                continue
            share = len(outside(p['values'], lo, hi)) / p['n']
            if share > max_out:
                problems.append(f'{c}: aralık dışında kalan satır payı %{100 * share:.1f} (en fazla %{100 * max_out:g}); normal değerler hata sayılır')
            kept = [x for x in p['uc'] if not outside([x], lo, hi)]
            if kept and not ok_uc:
                problems.append(f'{c}: aralık uç değerleri kabul ediyor ({", ".join(f"{x:g}" for x in kept[:5])}; Tukey dış sınırı {p["fence"][0]:g}–{p["fence"][1]:g}); '
                                'uç değerler geçerliyse uc_degerler_gecerli=true ve gerekçe yaz')
            if ok_uc and not why:
                problems.append(f'{c}: uç değerler geçerli denmiş ama gerekçe yok')
            out[c] = (lo, hi, why)
        missing = [c for c in prof if c not in seen]
        if missing:
            problems.append('aralığı yazılmayan kolon: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri kalitesi analistisin. Her sayısal sütun için iş anlamına uygun geçerli değer aralığı öner (fiyat negatif olamaz, oran 0–100 '
          'gibi). Profildeki yüzdelikler normal veriyi gösterir; uç değerler hata olabilir. Sınır yoksa null yaz. Uç değerleri gerçekten geçerli sayıyorsan '
          '"uc_degerler_gecerli": true ve gerekçe yaz. Yanıt şeması: {"kolonlar": [{"kolon": "...", "min": 0, "max": null, "uc_degerler_gecerli": false, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sayısal sütunlara geçerli değer aralığı öneren ve aralığı veriyle denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'urun_olcu_kayitlari.csv')), help='CSV/XLSX')
    p.add_argument('--key', default='', help='İhlal listesinde gösterilecek anahtar sütun (boşsa ilk sütun)')
    p.add_argument('--table', default='urun', help='SQL CHECK kısıtlarının tablo adı')
    p.add_argument('--max-outside', type=float, default=0.02, help='Aralığın dışında kalabilecek en yüksek satır payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    key = args.key or columns[0]
    prof = profile([c for c in columns if c != key], rows)
    if not prof:
        raise ValueError('Profillenecek sayısal sütun yok.')
    out = output_dir(args.output)
    g = lambda x: f'{x:g}'
    user = (f'{len(rows)} satır. Sayısal sütun profilleri (n · min · p1/p5/q1/medyan/q3/p95/p99 · max · negatif · sıfır · tam sayı · Tukey dış sınırı ötesindeki uç değerler):\n'
            + '\n'.join(f'- {c}: {v["n"]} · {g(v["min"])} · {"/".join(g(round(v["p"][q], 4)) for q in (.01, .05, .25, .5, .75, .95, .99))} · {g(v["max"])} · {v["negatif"]} · '
                        f'{v["sifir"]} · {"evet" if v["tam_sayi"] else "hayır"} · {", ".join(g(x) for x in v["uc"][:8]) or "yok"}' for c, v in prof.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(prof, args.max_outside))
    if result is None:
        return
    viol, suite, checks, table = [], [], [], []
    for c, (lo, hi, why) in result.items():
        bad = outside(prof[c]['values'], lo, hi)
        viol += [{'satir': i + 2, 'anahtar': rows[i][key], 'kolon': c, 'deger': rows[i][c], 'kural': f'{g(lo) if lo is not None else "-∞"} – {g(hi) if hi is not None else "+∞"}'} for i in bad]
        suite.append({'expectation_type': 'expect_column_values_to_be_between', 'kwargs': {'column': c, 'min_value': lo, 'max_value': hi}, 'meta': {'gerekce': why}})
        cond = ' AND '.join(x for x in ((f'{c} >= {g(lo)}' if lo is not None else ''), (f'{c} <= {g(hi)}' if hi is not None else '')) if x)
        if cond:
            checks.append(f'ALTER TABLE {args.table} ADD CONSTRAINT chk_{args.table}_{c} CHECK ({c} IS NULL OR ({cond}));')
        table.append([c, g(lo) if lo is not None else '—', g(hi) if hi is not None else '—', len(bad), why])
    viol.sort(key=lambda v: v['satir'])
    write_json(out / 'aralik_kurallari.json', {'expectation_suite_name': f'{args.table}_gecerli_araliklar', 'expectations': suite})
    write_text(out / 'aralik_kontrolleri.sql', '\n'.join(['-- Geçerli değer aralıkları (önce ihlalleri düzeltin; aksi halde ADD CONSTRAINT başarısız olur)'] + checks))
    tabular.write_csv(out / 'aralik_ihlalleri.csv', ['satir', 'anahtar', 'kolon', 'deger', 'kural'], viol)
    md = ['# Geçerli değer aralıkları', '', f'{len(rows)} satır, {len(result)} sayısal sütun; {len(viol)} değer aralık dışında.', '',
          md_table(['Kolon', 'Min', 'Max', 'Aralık dışı', 'Gerekçe'], table), '',
          md_table(['Satır', 'Anahtar', 'Kolon', 'Değer', 'Kural'], [list(v.values()) for v in viol]) if viol else 'Aralık dışı değer yok.', '',
          f'_Model: {meta["model"]} · Modele yalnızca sütun profilleri gitti; aralıkların dışarıda bıraktığı pay ve uç değerlerin durumu kodla denetlendi._']
    write_text(out / 'gecerli-araliklar.md', '\n'.join(md))
    print(f'[OK] {len(result)} kolon, {len(viol)} aralık dışı değer. Çıktı: {out / "gecerli-araliklar.md"}')


if __name__ == '__main__':
    llm.run(main)
