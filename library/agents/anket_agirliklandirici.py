"""Anket ağırlıklandırıcı (AI agent): örneklemi nüfus dağılımına göre düzeltmek için hangi değişkenlerle ağırlıklandırılacağını (raking /
iteratif oransal uyarlama) ve ağırlık kırpma sınırlarını seçer. Örneklem ile nüfus arasındaki pay farkları kodla hesaplanır; modele
yalnızca bu karşılaştırma gider, yanıtlar gönderilmez. Seçim yerelde uygulanır ve kodla denetlenir: belirgin sapan (eşiğin üstünde) her
değişken ağırlıklandırmaya girmeli, ağırlıklı paylar nüfusa sınır içinde oturmalı, kırpma sınırları makul olmalı ve tasarım etkisi
(Kish, n·Σw²/(Σw)²) sınırı aşmamalı. Çıktı: agirliklar.csv, agirliklandirma.json, anket-agirliklari.md (ağırlıklı ve ağırlıksız sonuç)."""
import argparse
from collections import Counter, defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table


def rake(rows, margins, vars_, lo, hi, iters=50):
    w = [1.0] * len(rows)
    for _ in range(iters):
        for v in vars_:
            tot = defaultdict(float)
            for r, x in zip(rows, w):
                tot[r[v]] += x
            s = sum(w)
            w = [x * margins[v][r[v]] * s / tot[r[v]] if tot[r[v]] else x for r, x in zip(rows, w)]
        mean = sum(w) / len(w)
        w = [min(max(x / mean, lo), hi) for x in w]
    mean = sum(w) / len(w)
    return [x / mean for x in w]


def shares(rows, v, w):
    tot = defaultdict(float)
    for r, x in zip(rows, w):
        tot[r[v]] += x
    s = sum(w)
    return {k: tot[k] / s for k in tot}


def deff(w):
    return len(w) * sum(x * x for x in w) / sum(w) ** 2


def validator(rows, margins, gaps, need_gap, tol, max_deff):
    def validate(obj):
        vars_ = [str(v).strip() for v in obj.get('degiskenler') or []]
        try:
            lo, hi = float(obj.get('kirpma_alt')), float(obj.get('kirpma_ust'))
        except (TypeError, ValueError):
            raise ValueError('kirpma_alt ve kirpma_ust sayı olmalı') from None
        problems = []
        bad = [v for v in vars_ if v not in margins]
        if bad or not vars_:
            raise ValueError(f'nüfus payı olmayan değişken: {", ".join(bad) or "değişken seçilmemiş"}')
        if not (0.1 <= lo < 1 < hi <= 10):
            raise ValueError('kırpma sınırları 0,1 ≤ alt < 1 < üst ≤ 10 olmalı')
        left = [f'{v} (en büyük fark {100 * gaps[v]:.1f} puan)' for v in margins if v not in vars_ and gaps[v] > need_gap]
        if left:
            problems.append('belirgin sapan değişken ağırlıklandırmaya alınmamış: ' + ', '.join(left))
        w = rake(rows, margins, vars_, lo, hi)
        for v in vars_:
            sh = shares(rows, v, w)
            off = max(abs(sh.get(k, 0) - p) for k, p in margins[v].items())
            if off > tol:
                problems.append(f'{v}: ağırlıklı paylar nüfustan {100 * off:.1f} puan sapıyor (en fazla {100 * tol:g}); kırpma çok sıkı olabilir')
        d = deff(w)
        if d > max_deff:
            problems.append(f'tasarım etkisi {d:.2f} (en fazla {max_deff:g}); etkin örneklem {len(w) / d:.0f}; kırpma üst sınırını düşür')
        if problems:
            raise ValueError('; '.join(problems))
        return vars_, lo, hi, w, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir anket istatistikçisisin. Örneklemi nüfusa yaklaştırmak için raking ile ağırlıklandırılacak değişkenleri ve ağırlık kırpma sınırlarını '
          '(ortalama 1’e göre alt ve üst) seç: belirgin sapan değişkenleri dahil et, aşırı ağırlıklar etkin örneklemi küçültür. Yanıt şeması: '
          '{"degiskenler": ["cinsiyet", "yas_grubu"], "kirpma_alt": 0.3, "kirpma_ust": 3, "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='Anketi nüfus paylarına göre raking ile ağırlıklandıran, değişken ve kırpma seçimini kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'anket_demografik_yanitlar.csv')), help='Yanıtlar (demografik değişkenler + sonuç)')
    p.add_argument('--margins', default=str(here('ornek', 'nufus_marjinleri.csv')), help='degisken, kategori, oran')
    p.add_argument('--outcome', default='memnuniyet', help='Ağırlıklı ortalaması raporlanacak sayısal sütun')
    p.add_argument('--gap', type=float, default=0.05, help='Bu farkı aşan değişken ağırlıklandırmaya girmeli')
    p.add_argument('--tolerance', type=float, default=0.01, help='Ağırlıklı payların nüfustan izin verilen farkı')
    p.add_argument('--max-deff', type=float, default=2.0)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    _, mrows = tabular.read(args.margins)
    margins = defaultdict(dict)
    for r in mrows:
        margins[r['degisken'].strip()][r['kategori'].strip()] = float(r['oran'].replace(',', '.'))
    margins = {v: m for v, m in margins.items() if v in columns}
    for v, m in margins.items():
        if abs(sum(m.values()) - 1) > 0.01:
            raise ValueError(f'{v}: nüfus payları toplamı 1 değil')
    for r in rows:
        for v in margins:
            r[v] = r[v].strip()
    ones = [1.0] * len(rows)
    gaps = {v: max(abs(shares(rows, v, ones).get(k, 0) - p_) for k, p_ in m.items()) for v, m in margins.items()}
    out = output_dir(args.output)
    user = (f'{len(rows)} yanıt. Değişkenler (kategori: örneklem % / nüfus %):\n'
            + '\n'.join(f'- {v} (en büyük fark {100 * gaps[v]:.1f} puan): ' + ', '.join(f'{k} {100 * shares(rows, v, ones).get(k, 0):.1f}/{100 * p_:.1f}' for k, p_ in m.items())
                        for v, m in margins.items())
            + f'\n\n{100 * args.gap:g} puandan fazla sapan değişkenler ağırlıklandırılmalı; tasarım etkisi en fazla {args.max_deff:g}.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, margins, gaps, args.gap, args.tolerance, args.max_deff))
    if result is None:
        return
    vars_, lo, hi, w, why = result
    key = columns[0]
    tabular.write_csv(out / 'agirliklar.csv', [key, 'agirlik'], [{key: r[key], 'agirlik': round(x, 5)} for r, x in zip(rows, w)])
    y = [tabular.parse_number(r.get(args.outcome, '')) for r in rows] if args.outcome in columns else []
    est = {}
    if y:
        pairs = [(v, x) for v, x in zip(y, w) if v is not None]
        est = {'agirliksiz': round(sum(v for v, _ in pairs) / len(pairs), 3), 'agirlikli': round(sum(v * x for v, x in pairs) / sum(x for _, x in pairs), 3)}
    d = deff(w)
    write_json(out / 'agirliklandirma.json', {'degiskenler': vars_, 'kirpma': [lo, hi], 'tasarim_etkisi': round(d, 3), 'etkin_orneklem': round(len(w) / d),
                                              'agirlik_min': round(min(w), 3), 'agirlik_max': round(max(w), 3), args.outcome: est, 'gerekce': why})
    table = [[v, k, f'{100 * shares(rows, v, ones).get(k, 0):.1f}', f'{100 * shares(rows, v, w).get(k, 0):.1f}', f'{100 * p_:.1f}'] for v, m in margins.items() for k, p_ in m.items()]
    md = ['# Anket ağırlıklandırma', '', f'{len(rows)} yanıt; raking değişkenleri: {", ".join(vars_)}; kırpma {lo:g}–{hi:g}. Tasarım etkisi {d:.2f}, etkin örneklem {len(w) / d:.0f}.', '']
    if est:
        md += [f'**{args.outcome}** ortalaması: ağırlıksız {est["agirliksiz"]}, ağırlıklı {est["agirlikli"]}.', '']
    md += [md_table(['Değişken', 'Kategori', 'Örneklem %', 'Ağırlıklı %', 'Nüfus %'], table), '', why, '',
           f'_Model: {meta["model"]} · Modele yalnızca pay karşılaştırması gitti; raking, kırpma, nüfusa uyum ve tasarım etkisi kodla hesaplandı._']
    write_text(out / 'anket-agirliklari.md', '\n'.join(md))
    print(f'[OK] {len(vars_)} değişken, tasarım etkisi {d:.2f}. Çıktı: {out / "anket-agirliklari.md"}')


if __name__ == '__main__':
    llm.run(main)
