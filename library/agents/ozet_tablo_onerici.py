"""Özet tablo önerici (AI agent): BI sorgu günlüğündeki (boyutlar, ölçümler, çalışma sayısı) sorgular için olgu tablosundan önceden
hesaplanmış özet (aggregate) tablolar önerir. Olgu tablosunun satır sayısı, sütunlar arası işlevsel bağımlılıklar (magaza → bolge,
urun → kategori, tarih → ay) ve her önerinin satır sayısı kodla veriden hesaplanır; modele yalnızca bu özet ve sorgu günlüğü gider.
Öneri kodla denetlenir: özet tablo bir sorguyu ancak sorgunun boyutları özetin boyutlarından türetilebiliyorsa ve ölçümleri yeniden
toplanabiliyorsa karşılar (count_distinct yalnızca aynı boyutlarda); her özet en az bir sorguyu karşılamalı, olgu tablosunun belirli
payından küçük olmalı ve özetler birlikte sorgu çalışmalarının belirli payını karşılamalı. Çıktı: ozet_tablolar.sql, sorgu_yonlendirme.csv, ozet-tablolar.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

FUNCS = ('sum', 'count', 'max', 'min', 'count_distinct')


def parse_measure(m):
    x = re.fullmatch(r'\s*(\w+)\s*\(\s*([\w*]+)\s*\)\s*', m)
    if not x or x.group(1).lower() not in FUNCS:
        raise ValueError(f'ölçüm “{m}” fonksiyon(sütun) biçiminde olmalı ({", ".join(FUNCS)})')
    return x.group(1).lower(), x.group(2)


def dependencies(rows, cols):
    """x → y when every value of x maps to a single value of y."""
    fd = {c: {c} for c in cols}
    for x in cols:
        for y in cols:
            if x != y:
                seen = {}
                if all(seen.setdefault(r[x], r[y]) == r[y] for r in rows):
                    fd[x].add(y)
    return fd


def closure(dims, fd):
    return set().union(*(fd[d] for d in dims)) if dims else set()


def answers(agg, q, fd):
    dims, meas = agg
    if not set(q['boyutlar']) <= closure(dims, fd):
        return False
    for f, c in q['olcumler']:
        if f == 'count_distinct':
            if (f, c) not in meas or closure(q['boyutlar'], fd) != closure(dims, fd):
                return False
        elif (f, c) not in meas:
            return False
    return True


def validator(rows, queries, fd, columns, max_tables, max_ratio, min_cover):
    total = sum(q['n'] for q in queries.values())

    def validate(obj):
        problems, aggs = [], {}
        items = obj.get('ozet_tablolar') or []
        if not items or len(items) > max_tables:
            raise ValueError(f'1–{max_tables} özet tablo önerilmeli')
        for e in items:
            name = str(e.get('ad', '')).strip()
            if not re.fullmatch(r'[a-z_][a-z0-9_]*', name) or name in aggs:
                problems.append(f'özet tablo adı “{name}” geçersiz ya da tekrar ediyor (küçük harf, rakam, _)')
                continue
            dims = [str(d).strip() for d in e.get('boyutlar') or []]
            bad = [d for d in dims if d not in columns]
            try:
                meas = {parse_measure(str(m)) for m in e.get('olcumler') or []}
            except ValueError as exc:
                problems.append(f'{name}: {exc}')
                continue
            bad += [c for _, c in meas if c != '*' and c not in columns]
            if bad or not dims:
                problems.append(f'{name}: olgu tablosunda olmayan sütun: {", ".join(bad) or "boyut yok"}')
                continue
            size = len({tuple(r[d] for d in dims) for r in rows})
            if size > max_ratio * len(rows):
                problems.append(f'{name}: {size} satır, olgu tablosunun %{100 * size / len(rows):.0f}’i (en fazla %{100 * max_ratio:g}); özet kazanç sağlamaz')
            served = [k for k, q in queries.items() if answers((dims, meas), q, fd)]
            if not served:
                problems.append(f'{name}: hiçbir sorguyu karşılamıyor')
            aggs[name] = (dims, meas, size, served)
        if problems:
            raise ValueError('; '.join(problems))
        covered = {k for v in aggs.values() for k in v[3]}
        share = sum(queries[k]['n'] for k in covered) / total
        if share < min_cover:
            rest = sorted((k for k in queries if k not in covered), key=lambda k: -queries[k]['n'])
            raise ValueError(f'özetler sorgu çalışmalarının %{100 * share:.0f}’ini karşılıyor (en az %{100 * min_cover:g}); karşılanmayan en sık sorgular: '
                             + ', '.join(f'{k} ({"|".join(queries[k]["boyutlar"])}; {queries[k]["n"]})' for k in rest[:4]))
        return aggs, share
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri ambarı mimarısın. BI sorgu günlüğündeki sık sorguları karşılayacak az sayıda özet (aggregate) tablo öner. Bir özet tablo, '
          'sorgunun boyutları kendi boyutlarından türetilebiliyorsa (ör. magaza → bolge) ve ölçümleri yeniden toplanabiliyorsa sorguyu karşılar; count_distinct '
          'yalnızca aynı boyutlarda yeniden kullanılabilir. Özet tablo küçük olmalı. Yanıt şeması: {"ozet_tablolar": [{"ad": "agg_ay_bolge", "boyutlar": ["ay", "bolge"], '
          '"olcumler": ["sum(tutar)", "count(*)"], "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='BI sorgu günlüğünden özet tablo öneren ve karşılama oranını olgu verisiyle kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'satis_olgu.csv')), help='Olgu tablosu dışa aktarımı (CSV/XLSX)')
    p.add_argument('--queries', default=str(here('ornek', 'bi_sorgu_gunlugu.csv')), help='sorgu, boyutlar (| ile), olcumler (| ile), calisma_sayisi')
    p.add_argument('--table', default='satis_olgu', help='Olgu tablosunun ambardaki adı')
    p.add_argument('--max-tables', type=int, default=3)
    p.add_argument('--max-ratio', type=float, default=0.2, help='Özet tablonun olgu tablosuna oranı en fazla')
    p.add_argument('--min-coverage', type=float, default=0.8, help='Karşılanması gereken sorgu çalışması payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    _, qrows = tabular.read(args.queries)
    queries = {}
    for r in qrows:
        dims = [d.strip() for d in r['boyutlar'].split('|') if d.strip()]
        meas = [parse_measure(m) for m in r['olcumler'].split('|') if m.strip()]
        if [d for d in dims if d not in columns]:
            raise ValueError(f'{r["sorgu"]}: olgu tablosunda olmayan boyut')
        queries[r['sorgu'].strip()] = {'boyutlar': dims, 'olcumler': meas, 'n': int(float(r['calisma_sayisi'].replace(',', '.'))), 'ham': r}
    dimcols = [c for c in columns if len({r[c] for r in rows}) < len(rows) * 0.5 or c in {d for q in queries.values() for d in q['boyutlar']}]
    fd = dependencies(rows, dimcols)
    for c in columns:
        fd.setdefault(c, {c})
    out = output_dir(args.output)
    user = (f'Olgu tablosu {args.table}: {len(rows)} satır, sütunlar {", ".join(columns)}.\nBoyutların farklı değer sayısı: '
            + ', '.join(f'{c} {len({r[c] for r in rows})}' for c in dimcols)
            + '\nİşlevsel bağımlılıklar (x → türetilebilen): ' + '; '.join(f'{x} → {", ".join(sorted(v - {x}))}' for x, v in fd.items() if len(v) > 1)
            + f'\n\nSorgu günlüğü (sorgu · boyutlar · ölçümler · çalışma sayısı), toplam {sum(q["n"] for q in queries.values())} çalışma:\n'
            + '\n'.join(f'- {k}: {q["ham"]["boyutlar"]} · {q["ham"]["olcumler"]} · {q["n"]}' for k, q in queries.items())
            + f'\n\nEn fazla {args.max_tables} özet; her biri olgu satırlarının en fazla %{100 * args.max_ratio:g}’i; sorgu çalışmalarının en az %{100 * args.min_coverage:g}’i karşılanmalı.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, queries, fd, columns, args.max_tables, args.max_ratio, args.min_coverage))
    if result is None:
        return
    aggs, share = result
    alias = lambda f, c: 'satir_sayisi' if (f, c) == ('count', '*') else f'{c}_{f}'
    expr = lambda f, c: 'COUNT(*)' if (f, c) == ('count', '*') else f'COUNT(DISTINCT {c})' if f == 'count_distinct' else f'{f.upper()}({c})'
    sql = [f'-- {args.table} için özet tablolar; BI aracında sorgular aşağıdaki tablolara yönlendirilebilir.']
    for name, (dims, meas, size, served) in aggs.items():
        sql += ['', f'-- ~{size} satır; karşıladığı sorgular: {", ".join(served)}', f'CREATE TABLE {name} AS', 'SELECT ' + ', '.join(dims) + ', '
                + ', '.join(f'{expr(f, c)} AS {alias(f, c)}' for f, c in sorted(meas)), f'FROM {args.table}', 'GROUP BY ' + ', '.join(dims) + ';']
    write_text(out / 'ozet_tablolar.sql', '\n'.join(sql))
    route = []
    for k, q in queries.items():
        best = min((v[2], n) for n, v in aggs.items() if k in v[3]) if any(k in v[3] for v in aggs.values()) else None
        route.append({'sorgu': k, 'boyutlar': q['ham']['boyutlar'], 'calisma_sayisi': q['n'], 'ozet_tablo': best[1] if best else '', 'durum': 'özetten' if best else f'{args.table} (olgu)'})
    tabular.write_csv(out / 'sorgu_yonlendirme.csv', list(route[0]), route)
    md = ['# Özet tablolar', '', f'{len(aggs)} özet tablo sorgu çalışmalarının %{100 * share:.0f}’ini karşılıyor ({len(rows)} satırlık olgu tablosu yerine).', '',
          md_table(['Özet', 'Boyutlar', 'Ölçümler', 'Satır', 'Karşıladığı sorgular'],
                   [[n, ', '.join(d), ', '.join(f'{f}({c})' for f, c in sorted(m)), s, ', '.join(sv)] for n, (d, m, s, sv) in aggs.items()]), '',
          md_table(['Sorgu', 'Boyutlar', 'Çalışma', 'Kaynak'], [[r['sorgu'], r['boyutlar'], r['calisma_sayisi'], r['ozet_tablo'] or r['durum']] for r in route]), '',
          f'_Model: {meta["model"]} · Modele olgu özeti ve sorgu günlüğü gitti; işlevsel bağımlılıklar, özet satır sayıları ve hangi sorgunun karşılandığı veriden kodla hesaplandı._']
    write_text(out / 'ozet-tablolar.md', '\n'.join(md))
    print(f'[OK] {len(aggs)} özet tablo, karşılama %{100 * share:.0f}. Çıktı: {out / "ozet-tablolar.md"}')


if __name__ == '__main__':
    llm.run(main)
