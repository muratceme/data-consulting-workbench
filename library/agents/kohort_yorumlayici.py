"""Kohort yorumlayıcı (AI agent): siparişlerden ilk alışveriş ayına göre müşteri kohortlarını ve aylık tutundurma matrisini yerelde kurar
(kohort-tutundurma bloğuyla aynı hesap) ve modele yalnızca matrisi verir. Model bulguları yazar ve her iddia kodla denetlenir: “en iyi”
ve “en kötü” kohort 1. ay tutundurmasına göre gerçekten en yüksek/en düşük olan olmalı (en az --min-size müşterili kohortlar arasında),
1. ay tutundurmasının eğilimi (artıyor/azalıyor/yatay) kohortlar üzerindeki doğrusal eğimin işaretine uymalı, metindeki sayılar matristen
gelmeli. Çıktı: kohort.csv, kohort-yorumu.md."""
import argparse
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from musteri import kohort
from donem_yorumu import unknown_numbers


def trend(points):
    n = len(points)
    if n < 3:
        return 'yatay', 0.0
    mx, my = (n - 1) / 2, sum(points) / n
    slope = sum((i - mx) * (y - my) for i, y in enumerate(points)) / sum((i - mx) ** 2 for i in range(n))
    return ('artıyor' if slope > 0.5 else 'azalıyor' if slope < -0.5 else 'yatay'), slope


def validator(rows, min_size):
    eligible = [r for r in rows if r['musteri'] >= min_size and r.get('ay_1') not in ('', None)]
    best = max(eligible, key=lambda r: r['ay_1'])['kohort']
    worst = min(eligible, key=lambda r: r['ay_1'])['kohort']
    direction, slope = trend([r['ay_1'] for r in eligible])
    nums = {abs(float(v)) for r in rows for k, v in r.items() if k != 'kohort' and v not in ('', None)} | {1, 2, 3}

    def validate(obj):
        problems = []
        if str(obj.get('en_iyi_kohort', '')).strip() != best:
            problems.append(f'en iyi kohort {obj.get("en_iyi_kohort")} değil (1. ay tutundurmasına bak, {min_size}+ müşterili kohortlar)')
        if str(obj.get('en_kotu_kohort', '')).strip() != worst:
            problems.append(f'en kötü kohort {obj.get("en_kotu_kohort")} değil')
        said = str(obj.get('egilim', '')).strip().lower().replace('artiyor', 'artıyor').replace('azaliyor', 'azalıyor')
        if said != direction:
            problems.append(f'1. ay tutundurma eğilimi “{said}” değil (kohort başına eğim {slope:+.2f} puan)')
        text = ' '.join([str(obj.get('ozet', ''))] + [str(x) for x in obj.get('oneriler') or []])
        bad = unknown_numbers(text, nums, [r['kohort'] for r in rows])
        if bad:
            problems.append('matriste olmayan sayılar: ' + ', '.join(bad[:8]))
        if not str(obj.get('ozet', '')).strip():
            problems.append('ozet gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        return obj, best, worst, direction
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir CRM analistisin. Kodla hesaplanmış kohort tutundurma matrisini (kohort = ilk alışveriş ayı; ay_k = k ay sonra tekrar alan '
          'müşteri yüzdesi) yorumla: 1. ay tutundurmasına göre en iyi ve en kötü kohort (küçük kohortları dışarıda bırak), 1. ay tutundurmasının kohortlar '
          'boyunca eğilimi (artıyor, azalıyor, yatay), kısa özet ve öneriler. Yalnızca matristeki sayıları kullan. Yanıt şeması: {"en_iyi_kohort": "2025-11", '
          '"en_kotu_kohort": "2026-02", "egilim": "azalıyor", "ozet": "...", "oneriler": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Kohort tutundurma matrisini yerelde kurup bulguları kodla denetlenen biçimde yorumlatan agent.')
    p.add_argument('--input', default=str(here('ornek', 'siparis_satirlari.csv')))
    p.add_argument('--order', default='siparis_no')
    p.add_argument('--date', default='tarih')
    p.add_argument('--customer', default='musteri_no')
    p.add_argument('--amount', default='tutar')
    p.add_argument('--min-size', type=int, default=3, help='Karşılaştırmaya girecek en küçük kohort')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    res = kohort(columns, rows, SimpleNamespace(order=args.order, date=args.date, customer=args.customer, amount=args.amount))
    matrix = res.rows
    out = output_dir(args.output)
    cols = [c for c in matrix[0] if c.startswith('ay_')]
    user = (f'{len(matrix)} kohort; karşılaştırmada en az {args.min_size} müşterili kohortları kullan.\nKohort · müşteri · ' + ' · '.join(cols) + '\n'
            + '\n'.join(f'{r["kohort"]} · {r["musteri"]} · ' + ' · '.join(str(r[c]) if r[c] != '' else '—' for c in cols) for r in matrix))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(matrix, args.min_size))
    if result is None:
        return
    obj, best, worst, direction = result
    tabular.write_csv(out / 'kohort.csv', list(matrix[0]), matrix)
    md = ['# Kohort yorumu', '', f'{len(matrix)} kohort. En iyi 1. ay tutundurması: **{best}**, en kötü: **{worst}**; eğilim: **{direction}**.', '', obj['ozet'], '']
    md += [f'- {x}' for x in obj.get('oneriler') or []]
    md += ['', md_table(['Kohort', 'Müşteri'] + cols, [[r['kohort'], r['musteri']] + [r[c] for c in cols] for r in matrix]), '',
           f'_Model: {meta["model"]} · Matris kodla hesaplandı; en iyi/en kötü kohort, eğilim ve metindeki sayılar matrisle denetlendi. Son kohortların sonraki ayları henüz oluşmadı._']
    write_text(out / 'kohort-yorumu.md', '\n'.join(md))
    print(f'[OK] {len(matrix)} kohort yorumlandı. Çıktı: {out / "kohort-yorumu.md"}')


if __name__ == '__main__':
    llm.run(main)
