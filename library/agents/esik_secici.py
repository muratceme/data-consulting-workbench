"""Eşik seçici (AI agent): ikili sınıflandırma modelinin (churn, fraud, temerrüt) karar eşiğini iş maliyetine göre seçer ve kararı iş
birimine açıklar. Eşik taraması (kesinlik, duyarlılık, alarm sayısı, yanlış alarm ve kaçan vaka maliyeti) siniflandirma-metrikleri
bloğuyla aynı karışıklık matrisi hesabıyla kodla yapılır; modele yalnızca tarama tablosu gider, müşteri satırları gönderilmez. Seçim kodla
denetlenir: duyarlılık alt sınırı ve alarm kapasitesi sağlanmalı, toplam maliyet bu kısıtlar altındaki en düşük maliyetin belirli payı
içinde olmalı, açıklamadaki sayılar taramadan gelmeli. Çıktı: esik.json, esik_taramasi.csv, esik-secimi.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from ml import confusion
from donem_yorumu import unknown_numbers


def evaluate(y, s, t, fp_cost, fn_cost):
    tp, fp, fn, tn, prec, rec, f1 = confusion(y, [x >= t for x in s])
    return {'esik': round(t, 2), 'alarm': tp + fp, 'dogru_alarm': tp, 'yanlis_alarm': fp, 'kacan': fn, 'kesinlik': round(prec, 4), 'duyarlilik': round(rec, 4),
            'f1': round(f1, 4), 'maliyet': round(fp * fp_cost + fn * fn_cost, 2)}


def validator(y, s, grid, args):
    feasible = [g for g in grid if g['duyarlilik'] >= args.min_recall and (not args.max_alerts or g['alarm'] <= args.max_alerts)]
    best = min(feasible, key=lambda g: g['maliyet']) if feasible else None
    nums = {v for g in grid for k, v in g.items()} | {100 * g[k] for g in grid for k in ('kesinlik', 'duyarlilik', 'f1')} | {args.fp_cost, args.fn_cost, args.min_recall,
                                                                                                                           100 * args.min_recall, args.max_alerts, len(y), sum(y)}

    def validate(obj):
        if best is None:
            raise ValueError('kısıtları (duyarlılık, alarm kapasitesi) sağlayan eşik yok')
        try:
            t = float(obj.get('esik'))
        except (TypeError, ValueError):
            raise ValueError('esik 0–1 arasında bir sayı olmalı') from None
        if not 0 < t < 1:
            raise ValueError('esik 0–1 arasında olmalı')
        r = evaluate(y, s, t, args.fp_cost, args.fn_cost)
        problems = []
        if r['duyarlilik'] < args.min_recall:
            problems.append(f'eşik {t:g}: duyarlılık {r["duyarlilik"]:.3f} < {args.min_recall:g}')
        if args.max_alerts and r['alarm'] > args.max_alerts:
            problems.append(f'eşik {t:g}: {r["alarm"]} alarm, kapasite {args.max_alerts}')
        if r['maliyet'] > best['maliyet'] * (1 + args.tolerance):
            problems.append(f'eşik {t:g}: maliyet {r["maliyet"]:g}, kısıtlar altındaki en düşük maliyet {best["maliyet"]:g} (eşik {best["esik"]:g}); fark %{100 * args.tolerance:g}’den büyük')
        text = str(obj.get('aciklama', '')).strip()
        if not text:
            problems.append('aciklama boş')
        bad = unknown_numbers(text, nums | set(r.values()), [])
        if bad:
            problems.append('açıklamada taramada olmayan sayılar: ' + ', '.join(bad[:8]))
        if problems:
            raise ValueError('; '.join(problems))
        return r, text, best
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir makine öğrenmesi ürün analistisin. Tarama tablosuna bakarak modelin karar eşiğini seç: yanlış alarm ve kaçan vaka maliyetini, '
          'duyarlılık alt sınırını ve alarm kapasitesini dikkate al. Kararı iş birimine 2–4 cümleyle açıkla; yalnızca tablodaki sayıları kullan. '
          'Yanıt şeması: {"esik": 0.35, "aciklama": "..."}')


def main():
    p = argparse.ArgumentParser(description='Sınıflandırma modelinin karar eşiğini maliyet ve kısıtlara göre seçen, seçimi taramayla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'model_tahminleri.csv')), help='Tahmin dosyası (gerçek 0/1, skor 0–1)')
    p.add_argument('--actual', default='gercek')
    p.add_argument('--score', default='skor')
    p.add_argument('--fp-cost', type=float, default=20, help='Bir yanlış alarmın maliyeti (ör. gereksiz kampanya)')
    p.add_argument('--fn-cost', type=float, default=150, help='Bir kaçan vakanın maliyeti (ör. kaybedilen müşteri)')
    p.add_argument('--min-recall', type=float, default=0.7, help='Duyarlılık alt sınırı')
    p.add_argument('--max-alerts', type=int, default=0, help='Alarm kapasitesi (0 = sınırsız)')
    p.add_argument('--tolerance', type=float, default=0.05, help='En düşük maliyetten kabul edilebilir sapma')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.actual, args.score):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    y = [int(float(r[args.actual].replace(',', '.'))) for r in rows]
    s = [float(r[args.score].replace(',', '.')) for r in rows]
    if set(y) - {0, 1}:
        raise ValueError('Gerçek etiket 0/1 olmalı.')
    grid = [evaluate(y, s, k / 100, args.fp_cost, args.fn_cost) for k in range(5, 96)]
    shown = [g for g in grid if round(g['esik'] * 100) % 5 == 0]
    out = output_dir(args.output)
    user = (f'{len(y)} tahmin, {sum(y)} pozitif. Yanlış alarm maliyeti {args.fp_cost:g}, kaçan vaka maliyeti {args.fn_cost:g}; duyarlılık en az {args.min_recall:g}'
            + (f', alarm kapasitesi {args.max_alerts}' if args.max_alerts else '') + '.\nEşik taraması (eşik · alarm · doğru alarm · yanlış alarm · kaçan · kesinlik · duyarlılık · F1 · maliyet):\n'
            + '\n'.join(' · '.join(f'{v:g}' for v in g.values()) for g in shown))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(y, s, grid, args))
    if result is None:
        return
    r, text, best = result
    tabular.write_csv(out / 'esik_taramasi.csv', list(grid[0]), grid)
    write_json(out / 'esik.json', {'esik': r['esik'], 'metrikler': r, 'en_dusuk_maliyetli_esik': best['esik'], 'fp_maliyet': args.fp_cost, 'fn_maliyet': args.fn_cost,
                                   'min_duyarlilik': args.min_recall, 'aciklama': text})
    f05 = next(g for g in grid if g['esik'] == 0.5)
    md = ['# Karar eşiği', '', f'Seçilen eşik **{r["esik"]:g}**: {r["alarm"]} alarm, kesinlik {r["kesinlik"]:.3f}, duyarlılık {r["duyarlilik"]:.3f}, maliyet {r["maliyet"]:g} '
          f'(0,5 eşiğinde {f05["maliyet"]:g}; kısıtlar altında en düşük {best["maliyet"]:g}, eşik {best["esik"]:g}).', '', text, '',
          md_table(['Eşik', 'Alarm', 'Yanlış alarm', 'Kaçan', 'Kesinlik', 'Duyarlılık', 'Maliyet'],
                   [[g['esik'], g['alarm'], g['yanlis_alarm'], g['kacan'], g['kesinlik'], g['duyarlilik'], g['maliyet']] for g in shown]), '',
          f'_Model: {meta["model"]} · Modele yalnızca eşik taraması gitti; duyarlılık, kapasite ve maliyet kısıtları kodla denetlendi, açıklamadaki sayılar taramayla karşılaştırıldı._']
    write_text(out / 'esik-secimi.md', '\n'.join(md))
    print(f'[OK] Eşik {r["esik"]:g}, maliyet {r["maliyet"]:g}. Çıktı: {out / "esik-secimi.md"}')


if __name__ == '__main__':
    llm.run(main)
