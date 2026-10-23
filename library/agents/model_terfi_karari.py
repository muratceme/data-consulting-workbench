"""Model terfi kararı (AI agent): canlıdaki (şampiyon) ve aday sınıflandırma modelinin aynı test kümesindeki skorlarından terfi kararını
verir ve kararı açıklar. AUC farkı, eşleştirilmiş bootstrap güven aralığı ve segment bazında AUC farkları ml bloğunun AUC hesabıyla kodla
çıkarılır; modele yalnızca bu tablo gider, müşteri satırları gönderilmez. Karar kodla denetlenir: güven aralığının alt sınırı sıfırın
üstündeyse ve hiçbir yeterli büyüklükteki segment belirgin kötüleşmiyorsa “terfi”, üst sınırı sıfırın altındaysa “reddet”, diğer
durumlarda “bekle” olmalı; açıklamadaki sayılar tablodan gelmeli. Çıktı: terfi_karari.json, segment_karsilastirma.csv, model-terfi.md."""
import argparse, random
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from ml import auc
from donem_yorumu import unknown_numbers


def expected(lo, hi, drops):
    if hi < 0:
        return 'reddet'
    return 'terfi' if lo > 0 and not drops else 'bekle'


def validator(lo, hi, drops, nums, segs):
    want = expected(lo, hi, drops)

    def validate(obj):
        k = str(obj.get('karar', '')).strip().lower()
        text = str(obj.get('aciklama', '')).strip()
        problems = []
        if k not in ('terfi', 'bekle', 'reddet'):
            raise ValueError('karar terfi, bekle ya da reddet olmalı')
        if k != want:
            why = ('güven aralığı ' + f'[{lo:.4f}, {hi:.4f}]' + (f', kötüleşen segment: {", ".join(drops)}' if drops else ''))
            problems.append(f'karar “{k}” olamaz; {why} → “{want}”')
        if not text:
            problems.append('aciklama boş')
        bad = unknown_numbers(text, nums, segs)
        if bad:
            problems.append('açıklamada tabloda olmayan sayılar: ' + ', '.join(bad[:8]))
        if problems:
            raise ValueError('; '.join(problems))
        return k, text
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir makine öğrenmesi platform sorumlususun. Aday model canlıdaki modelin yerine geçmeli mi? Karar ver: terfi (aday kesin daha iyi ve '
          'hiçbir segmentte belirgin kötüleşme yok), reddet (aday kesin daha kötü) ya da bekle (fark belirsiz ya da bir segment kötüleşiyor). Kararı iş birimine '
          '2–4 cümleyle açıkla; yalnızca tablodaki sayıları kullan. Yanıt şeması: {"karar": "bekle", "aciklama": "..."}')


def main():
    p = argparse.ArgumentParser(description='Şampiyon ve aday model skorlarından terfi kararı veren; kararı bootstrap ve segment analiziyle kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'model_karsilastirma.csv')), help='segment, gercek (0/1), skor_mevcut, skor_aday')
    p.add_argument('--segment', default='segment')
    p.add_argument('--actual', default='gercek')
    p.add_argument('--current', default='skor_mevcut')
    p.add_argument('--candidate', default='skor_aday')
    p.add_argument('--boot', type=int, default=400, help='Bootstrap tekrar sayısı')
    p.add_argument('--min-segment', type=int, default=100, help='Kötüleşme denetimi için en küçük segment')
    p.add_argument('--max-drop', type=float, default=0.02, help='Segment AUC’sinde kabul edilen en fazla düşüş')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.actual, args.current, args.candidate):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    num = lambda v: float(v.replace(',', '.'))
    data = [(r.get(args.segment, '').strip() or 'tümü', int(num(r[args.actual])), num(r[args.current]), num(r[args.candidate])) for r in rows]
    y, a, b = [d[1] for d in data], [d[2] for d in data], [d[3] for d in data]
    base, cand = auc(y, a), auc(y, b)
    rng = random.Random(7)
    diffs = []
    for _ in range(args.boot):
        idx = [rng.randrange(len(data)) for _ in data]
        yy = [y[i] for i in idx]
        if 0 < sum(yy) < len(yy):
            diffs.append(auc(yy, [b[i] for i in idx]) - auc(yy, [a[i] for i in idx]))
    diffs.sort()
    lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]
    groups = defaultdict(list)
    for d in data:
        groups[d[0]].append(d)
    seg = []
    for g, ds in sorted(groups.items()):
        ys = [d[1] for d in ds]
        sa, sb = auc(ys, [d[2] for d in ds]), auc(ys, [d[3] for d in ds])
        if sa is not None and sb is not None:
            seg.append({'segment': g, 'n': len(ds), 'pozitif': sum(ys), 'auc_mevcut': round(sa, 4), 'auc_aday': round(sb, 4), 'fark': round(sb - sa, 4)})
    drops = [s['segment'] for s in seg if s['n'] >= args.min_segment and s['fark'] < -args.max_drop]
    nums = {round(base, 4), round(cand, 4), round(cand - base, 4), round(lo, 4), round(hi, 4), len(data), sum(y), args.max_drop, args.min_segment}
    nums |= {v for s in seg for k, v in s.items() if k != 'segment'} | {100 * abs(v) for v in list(nums)}
    out = output_dir(args.output)
    user = (f'{len(data)} test örneği, {sum(y)} pozitif. AUC mevcut {base:.4f}, aday {cand:.4f}, fark {cand - base:.4f}; eşleştirilmiş bootstrap %95 güven aralığı '
            f'[{lo:.4f}, {hi:.4f}] ({len(diffs)} tekrar).\nSegmentler (n · pozitif · AUC mevcut · AUC aday · fark):\n'
            + '\n'.join(f'- {s["segment"]}: {s["n"]} · {s["pozitif"]} · {s["auc_mevcut"]} · {s["auc_aday"]} · {s["fark"]}' for s in seg)
            + f'\n\n{args.min_segment}+ örnekli bir segmentte {args.max_drop:g}’den fazla AUC düşüşü belirgin kötüleşme sayılır.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(lo, hi, drops, nums, [s['segment'] for s in seg]))
    if result is None:
        return
    karar, text = result
    tabular.write_csv(out / 'segment_karsilastirma.csv', list(seg[0]), seg)
    write_json(out / 'terfi_karari.json', {'karar': karar, 'auc_mevcut': round(base, 4), 'auc_aday': round(cand, 4), 'guven_araligi': [round(lo, 4), round(hi, 4)],
                                           'kotulesen_segmentler': drops, 'aciklama': text})
    md = ['# Model terfi kararı', '', f'Karar: **{karar}**. AUC {base:.4f} → {cand:.4f}; fark için %95 güven aralığı [{lo:.4f}, {hi:.4f}]'
          + (f'; kötüleşen segment: {", ".join(drops)}' if drops else '') + '.', '', text, '',
          md_table(['Segment', 'n', 'Pozitif', 'AUC mevcut', 'AUC aday', 'Fark'], [list(s.values()) for s in seg]), '',
          f'_Model: {meta["model"]} · AUC, bootstrap güven aralığı ve segment farkları kodla hesaplandı; karar bu kurallara göre denetlendi._']
    write_text(out / 'model-terfi.md', '\n'.join(md))
    print(f'[OK] Karar: {karar}. Çıktı: {out / "model-terfi.md"}')


if __name__ == '__main__':
    llm.run(main)
