"""Z-order kolon seçici (AI agent): Delta/Iceberg tabloları için sorgu filtre günlüğünden Z-order (veya liquid clustering) kolonlarını
seçer. Kolon başına farklı değer sayısı, bölümleme kolonları ve her kolonun filtrelendiği sorgu çalışması payı kodla hesaplanır; modele
yalnızca bu özet gider. Seçim kodla denetlenir: kolon tabloda olmalı, bölümleme kolonu Z-order’a girmemeli (zaten dosyaları ayırıyor),
düşük kardinaliteli kolon (veri atlama sağlamaz) seçilmemeli, tablo başına kolon sınırı aşılmamalı ve seçilen kolonlar filtreli sorgu
çalışmalarının belirli payını kapsamalı. Çıktı: zorder.sql (OPTIMIZE … ZORDER BY ve CLUSTER BY), zorder_plani.csv, zorder.md."""
import argparse
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table


def coverage(queries, cols):
    total = sum(n for _, n in queries)
    return sum(n for f, n in queries if f & cols) / total if total else 0.0


def validator(prof, queries, max_cols, min_card, min_cover):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('tablolar') or []:
            t = str(e.get('tablo', '')).strip()
            if t not in prof or t in seen:
                problems.append(f'tablo “{t}” yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            cols = [str(c).strip() for c in e.get('kolonlar') or []]
            bad = [c for c in cols if c not in prof[t]]
            if bad or not cols:
                problems.append(f'{t}: tabloda olmayan kolon: {", ".join(bad) or "kolon yok"}')
                continue
            if len(cols) > max_cols:
                problems.append(f'{t}: {len(cols)} kolon; Z-order {max_cols} kolondan sonra her kolonun etkisini azaltır')
            for c in cols:
                if prof[t][c]['part']:
                    problems.append(f'{t}.{c} bölümleme kolonu; dosyalar zaten bu kolona göre ayrılıyor, Z-order’a eklenmez')
                elif prof[t][c]['card'] < min_card:
                    problems.append(f'{t}.{c} yalnızca {prof[t][c]["card"]} farklı değer; veri atlama sağlamaz')
            cov = coverage(queries[t], set(cols))
            if cov < min_cover:
                missed = sorted(((f, n) for f, n in queries[t] if not f & set(cols)), key=lambda x: -x[1])
                problems.append(f'{t}: seçilen kolonlar sorgu çalışmalarının %{100 * cov:.0f} kadarını kapsıyor (en az %{100 * min_cover:g}); kapsanmayan: '
                                + ', '.join(f'{"|".join(sorted(f))} ({n})' for f, n in missed[:3]))
            out[t] = (cols, cov, str(e.get('gerekce', '')).strip())
        missing = [t for t in prof if t not in seen]
        if missing:
            problems.append('kolonu yazılmayan tablo: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir lakehouse performans mühendisisin. Her tablo için sorgu filtrelerine göre Z-order (liquid clustering) kolonlarını seç: sık '
          'filtrelenen, kardinalitesi yüksek kolonlar; bölümleme kolonu ve az değerli kolonlar seçilmez; en sık filtre önce. Yanıt şeması: '
          '{"tablolar": [{"tablo": "...", "kolonlar": ["musteri_id", "urun_id"], "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sorgu filtre günlüğünden Z-order / liquid clustering kolonları seçen ve kapsamayı kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'lakehouse_sorgu_filtreleri.csv')), help='tablo, filtre_kolonlari (| ile), calisma')
    p.add_argument('--columns', default=str(here('ornek', 'lakehouse_kolon_profili.csv')), help='tablo, kolon, farkli_deger, bolumleme')
    p.add_argument('--max-cols', type=int, default=4)
    p.add_argument('--min-cardinality', type=int, default=100)
    p.add_argument('--min-coverage', type=float, default=0.8)
    llm.add_arguments(p)
    args = p.parse_args()
    _, crows = tabular.read(args.columns)
    prof = defaultdict(dict)
    for r in crows:
        prof[r['tablo'].strip()][r['kolon'].strip()] = {'card': int(float(r['farkli_deger'].replace(',', '.'))), 'part': r['bolumleme'].strip().lower() in ('evet', 'e', 'true', '1')}
    _, qrows = tabular.read(args.input)
    queries = defaultdict(list)
    for r in qrows:
        queries[r['tablo'].strip()].append((frozenset(c.strip() for c in r['filtre_kolonlari'].split('|') if c.strip()), int(float(r['calisma']))))
    prof = {t: v for t, v in prof.items() if t in queries}
    out = output_dir(args.output)
    lines = []
    for t, cols in prof.items():
        total = sum(n for _, n in queries[t])
        lines.append(f'- {t} ({total} filtreli sorgu çalışması): ' + '; '.join(
            f'{c} {v["card"]} farklı{" (bölümleme)" if v["part"] else ""}, filtre payı %{100 * coverage(queries[t], {c}):.0f}' for c, v in cols.items()))
        lines += [f'  - filtre {"|".join(sorted(f))}: {n}' for f, n in sorted(queries[t], key=lambda x: -x[1])]
    user = (f'Tablolar (kolon · farklı değer · filtrelendiği çalışma payı) ve filtre kombinasyonları. En fazla {args.max_cols} kolon, en az {args.min_cardinality} farklı değer, '
            f'kapsama en az %{100 * args.min_coverage:g}.\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(prof, queries, args.max_cols, args.min_cardinality, args.min_coverage))
    if result is None:
        return
    sql = ['-- Delta Lake: Z-order (klasik) ya da liquid clustering (Databricks/Delta 3.1+) — ikisinden birini kullanın.']
    for t, (cols, cov, why) in result.items():
        sql += ['', f'-- {t}: {why}' if why else f'-- {t}', f'OPTIMIZE {t} ZORDER BY ({", ".join(cols)});', f'-- ALTER TABLE {t} CLUSTER BY ({", ".join(cols)}); OPTIMIZE {t};']
    write_text(out / 'zorder.sql', '\n'.join(sql))
    plan = [{'tablo': t, 'kolonlar': ', '.join(c), 'kapsama': round(cov, 3)} for t, (c, cov, _) in result.items()]
    tabular.write_csv(out / 'zorder_plani.csv', list(plan[0]), plan)
    md = ['# Z-order kolonları', '', f'{len(plan)} tablo; seçilen kolonlar her tabloda filtreli sorgu çalışmalarının en az %{100 * args.min_coverage:g}’ini kapsıyor.', '',
          md_table(['Tablo', 'Kolonlar', 'Kapsama', 'Gerekçe'], [[t, ', '.join(c), f'%{100 * cov:.0f}', why] for t, (c, cov, why) in result.items()]), '',
          f'_Model: {meta["model"]} · Modele kolon profilleri ve filtre özetleri gitti; bölümleme, kardinalite, kolon sınırı ve kapsama kodla denetlendi._']
    write_text(out / 'zorder.md', '\n'.join(md))
    print(f'[OK] {len(plan)} tablo. Çıktı: {out / "zorder.md"}')


if __name__ == '__main__':
    llm.run(main)
