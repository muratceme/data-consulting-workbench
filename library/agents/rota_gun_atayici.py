"""Teslimat günü atayıcı (AI agent): haftalık sabit teslimat yapılan müşterileri (bayi, mağaza, şube) araç kapasitesine göre haftanın günlerine
dağıtır; aynı gün teslim edilenler birbirine yakın olmalıdır. Modele müşteri kodu, koordinat (3 ondalık, yaklaşık 100 m), yük ve zorunlu
gün gider. Atama kodla denetlenir: her müşteri bir kez ve izinli bir güne atanmalı, zorunlu günler korunmalı, günlük yük kapasiteyi
aşmamalı; günler coğrafi olarak toplu olmalı: her günün müşterilerinin gün merkezine ortalama uzaklığı toplamı, kodun kapasiteye uyan
süpürme (sweep) çözümünün en fazla --tolerance katı olabilir. Çıktı: gun_atamasi.csv, gunler.geojson, teslimat-gunleri.md."""
import argparse, json, math
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def spread(assign, pts):
    groups = defaultdict(list)
    for c, d in assign.items():
        groups[d].append(pts[c])
    total = 0.0
    for members in groups.values():
        cen = (sum(p[0] for p in members) / len(members), sum(p[1] for p in members) / len(members))
        total += sum(km(p, cen) for p in members) / len(members)
    return total


def sweep(pts, loads, days, cap, fixed):
    """Baseline: order customers by angle around the centroid and fill days in turn, keeping fixed days."""
    cen = (sum(p[0] for p in pts.values()) / len(pts), sum(p[1] for p in pts.values()) / len(pts))
    order = sorted(pts, key=lambda c: math.atan2(pts[c][0] - cen[0], pts[c][1] - cen[1]))
    best = None
    for shift in range(len(order)):
        seq = order[shift:] + order[:shift]
        assign, load, i = dict(fixed), defaultdict(float), 0
        for c, d in fixed.items():
            load[d] += loads[c]
        ok = True
        for c in seq:
            if c in assign:
                continue
            while i < len(days) and load[days[i]] + loads[c] > cap:
                i += 1
            if i == len(days):
                ok = False
                break
            assign[c] = days[i]
            load[days[i]] += loads[c]
        if ok:
            s = spread(assign, pts)
            best = s if best is None or s < best else best
    return best


def validator(pts, loads, days, cap, fixed, tolerance):
    base = sweep(pts, loads, days, cap, fixed)

    def validate(obj):
        assign, problems, seen = {}, [], set()
        for e in obj.get('atamalar') or []:
            c, d = str(e.get('musteri', '')).strip(), str(e.get('gun', '')).strip()
            dup = c in seen
            seen.add(c)
            if c not in pts or dup:
                problems.append(f'müşteri “{c}” yok ya da iki kez atandı')
            elif d not in days:
                problems.append(f'{c}: gün {d} izinli değil ({", ".join(days)})')
            elif c in fixed and fixed[c] != d:
                problems.append(f'{c}: zorunlu gün {fixed[c]}')
            else:
                assign[c] = d
        missing = [c for c in pts if c not in seen]
        if missing:
            problems.append('atanmayan müşteri: ' + ', '.join(missing[:15]))
        if problems:
            raise ValueError(' | '.join(problems))
        load = defaultdict(float)
        for c, d in assign.items():
            load[d] += loads[c]
        over = [f'{d} {load[d]:g} kg' for d in days if load[d] > cap]
        if over:
            problems.append(f'kapasite ({cap:g} kg) aşılıyor: ' + ', '.join(over))
        s = spread(assign, pts)
        if base is not None and s > base * tolerance:
            problems.append(f'günler coğrafi olarak dağınık: toplam ortalama uzaklık {s:.1f} km, kapasiteye uyan basit süpürme çözümü {base:.1f} km '
                            f'(en fazla {tolerance:g} katı); yakın müşterileri aynı güne topla')
        if problems:
            raise ValueError(' | '.join(problems))
        return assign, load, s, base
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir dağıtım planlamacısısın. Müşterileri haftanın günlerine ata: her gün tek araç çıkar ve günlük yük kapasiteyi aşmamalı; aynı güne '
          'birbirine yakın müşterileri koy (bölge bölge düşün), zorunlu günlere uy. Yanıt şeması: {"atamalar": [{"musteri": "B01", "gun": "Pzt"}], "aciklama": "..."}')


def main():
    p = argparse.ArgumentParser(description='Müşterileri kapasite ve coğrafi yakınlıkla teslimat günlerine atayan, kodla denetlenen agent.')
    p.add_argument('--input', default=str(here('ornek', 'haftalik_teslimat_musterileri.csv')), help='musteri, enlem, boylam, yuk_kg[, zorunlu_gun]')
    p.add_argument('--days', default='Pzt,Sal,Çar,Per,Cum')
    p.add_argument('--capacity', type=float, default=3000.0, help='Günlük araç kapasitesi (kg)')
    p.add_argument('--tolerance', type=float, default=1.15)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('musteri', 'enlem', 'boylam', 'yuk_kg'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    days = [d.strip() for d in args.days.split(',') if d.strip()]
    num = lambda v: float(v.replace(',', '.'))
    pts = {r['musteri'].strip(): (num(r['enlem']), num(r['boylam'])) for r in rows}
    loads = {r['musteri'].strip(): num(r['yuk_kg']) for r in rows}
    fixed = {r['musteri'].strip(): r['zorunlu_gun'].strip() for r in rows if r.get('zorunlu_gun', '').strip()}
    if sum(loads.values()) > args.capacity * len(days):
        raise ValueError(f'Toplam yük {sum(loads.values()):g} kg haftalık kapasiteyi ({args.capacity * len(days):g} kg) aşıyor.')
    out = output_dir(args.output)
    user = (f'Günler: {", ".join(days)}; günlük kapasite {args.capacity:g} kg; toplam yük {sum(loads.values()):g} kg.\nMüşteriler (kod · enlem · boylam · yük kg · zorunlu gün):\n'
            + '\n'.join(f'{c} · {pts[c][0]:.3f} · {pts[c][1]:.3f} · {loads[c]:g} · {fixed.get(c, "—")}' for c in pts))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(pts, loads, days, args.capacity, fixed, args.tolerance))
    if result is None:
        return
    assign, load, s, base = result
    tabular.write_csv(out / 'gun_atamasi.csv', ['musteri', 'gun', 'enlem', 'boylam', 'yuk_kg'],
                      [{'musteri': c, 'gun': assign[c], 'enlem': pts[c][0], 'boylam': pts[c][1], 'yuk_kg': loads[c]} for c in sorted(pts, key=lambda c: (days.index(assign[c]), c))])
    gj = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [pts[c][1], pts[c][0]]},
                                                      'properties': {'musteri': c, 'gun': assign[c], 'yuk_kg': loads[c]}} for c in pts]}
    write_text(out / 'gunler.geojson', json.dumps(gj, ensure_ascii=False))
    md = ['# Teslimat günleri', '', f'{len(pts)} müşteri {len(days)} güne dağıtıldı; toplam ortalama uzaklık {s:.1f} km (basit süpürme çözümü {base:.1f} km).', '',
          md_table(['Gün', 'Müşteri', 'Yük (kg)', 'Doluluk %'], [[d, sum(1 for c in assign if assign[c] == d), f'{load[d]:g}', round(load[d] / args.capacity * 100)] for d in days]), '',
          f'_Model: {meta["model"]} · Kapasite, zorunlu gün ve coğrafi toplanma kodla denetlendi. Gün içi sıralama (rota) ve trafik bu planın dışındadır; gunler.geojson haritada açılabilir._']
    write_text(out / 'teslimat-gunleri.md', '\n'.join(md))
    print(f'[OK] {len(pts)} müşteri atandı. Çıktı: {out / "teslimat-gunleri.md"}')


if __name__ == '__main__':
    llm.run(main)
