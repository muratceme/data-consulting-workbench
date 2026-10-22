"""Geofence yarıçap önerici (AI agent): mağaza ziyaret konum sinyallerinden (ping) her mağaza için ziyaret algılama yarıçapı önerir. Pinglerin
mağazaya uzaklık yüzdelikleri ve en yakın komşu mağaza mesafesi kodla (haversine) hesaplanır; modele yalnızca bu özet gider, cihaz
kimlikleri ve konumlar gönderilmez. Öneri kodla denetlenir: yarıçap mağazanın kendi ziyaret pinglerinin belirli bir payını kapsamalı,
p90 mesafesinin çok üstüne çıkıp yoldan geçenleri saymamalı, komşu mağazaların çemberleri çakışmamalı (r₁ + r₂ ≤ mesafe).
Çıktı: geofence.geojson, geofence_plani.csv, geofence.md."""
import argparse, math
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from analiz import quantile

R = 6371008.8


def dist(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def circle(center, r, n=32):
    la, lo = center
    pts = [[round(lo + r * math.sin(2 * math.pi * i / n) / (111320 * math.cos(math.radians(la))), 6), round(la + r * math.cos(2 * math.pi * i / n) / 111320, 6)] for i in range(n)]
    return pts + [pts[0]]


def validator(stores, d, min_cov):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('magazalar') or []:
            k = str(e.get('magaza', '')).strip()
            if k not in stores or k in out:
                problems.append(f'mağaza “{k}” yok ya da iki kez yazıldı')
                continue
            try:
                r = float(e.get('yaricap_m'))
            except (TypeError, ValueError):
                problems.append(f'{k}: yaricap_m sayı olmalı')
                continue
            v = d[k]
            cov = sum(x <= r for x in v) / len(v)
            cap = max(1.5 * quantile(v, .9), 50)
            if not 25 <= r <= 1000:
                problems.append(f'{k}: yarıçap 25–1000 m arasında olmalı')
            if cov < min_cov:
                problems.append(f'{k}: {r:g} m yarıçapın kapsadığı ziyaret pingi payı %{100 * cov:.0f} (en az %{100 * min_cov:g})')
            if r > cap:
                problems.append(f'{k}: {r:g} m, p90 mesafesinin ({quantile(v, .9):.0f} m) 1,5 katını aşıyor; yoldan geçenler ziyaret sayılır')
            out[k] = (r, cov, str(e.get('gerekce', '')).strip())
        missing = [k for k in stores if k not in out]
        if missing:
            problems.append('yarıçapı yazılmayan mağaza: ' + ', '.join(missing))
        keys = sorted(out)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                gap = dist(stores[a]['pos'], stores[b]['pos'])
                if out[a][0] + out[b][0] > gap:
                    problems.append(f'{a} ({out[a][0]:g} m) ile {b} ({out[b][0]:g} m) çakışıyor; mağazalar arası {gap:.0f} m')
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir konum analitiği uzmanısın. Her mağaza için ziyaret algılama yarıçapı (metre) öner: ziyaretlerin çoğunu kapsasın, GPS sapmasını '
          'tolere etsin ama yoldan geçenleri ziyaret saymasın; yakın mağazaların çemberleri çakışmasın. Yanıt şeması: '
          '{"magazalar": [{"magaza": "...", "yaricap_m": 150, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Ziyaret pinglerinden mağaza başına geofence yarıçapı öneren, kapsama ve çakışmayı kodla denetleyen agent.')
    p.add_argument('--stores', default=str(here('ornek', 'magaza_konumlari.csv')), help='magaza, ad, enlem, boylam')
    p.add_argument('--pings', default=str(here('ornek', 'magaza_ziyaret_pingleri.csv')), help='magaza, enlem, boylam (cihaz ve zaman gönderilmez)')
    p.add_argument('--min-coverage', type=float, default=0.85, help='Yarıçapın kapsaması gereken en az ziyaret pingi payı')
    llm.add_arguments(p)
    args = p.parse_args()
    _, srows = tabular.read(args.stores)
    num = lambda v: float(v.replace(',', '.'))
    stores = {r['magaza'].strip(): {'ad': r.get('ad', '').strip(), 'pos': (num(r['enlem']), num(r['boylam']))} for r in srows}
    _, prow = tabular.read(args.pings)
    d = defaultdict(list)
    for r in prow:
        k = r['magaza'].strip()
        if k in stores:
            d[k].append(dist(stores[k]['pos'], (num(r['enlem']), num(r['boylam']))))
    d = {k: sorted(v) for k, v in d.items()}
    stores = {k: v for k, v in stores.items() if k in d}
    near = {k: min((dist(v['pos'], w['pos']), j) for j, w in stores.items() if j != k) for k, v in stores.items()}
    out = output_dir(args.output)
    user = (f'{len(stores)} mağaza, {sum(map(len, d.values()))} ziyaret pingi. Mağaza (ad) · ping sayısı · mağazaya uzaklık p50/p80/p90/p95 m · en yakın mağaza ve mesafesi:\n'
            + '\n'.join(f'- {k} ({v["ad"]}): {len(d[k])} · ' + '/'.join(f'{quantile(d[k], q):.0f}' for q in (.5, .8, .9, .95))
                        + f' · {near[k][1]} {near[k][0]:.0f} m' for k, v in stores.items())
            + f'\n\nHedef: her mağazada ziyaret pinglerinin en az %{100 * args.min_coverage:g}’i kapsansın.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(stores, d, args.min_coverage))
    if result is None:
        return
    plan = [{'magaza': k, 'ad': stores[k]['ad'], 'yaricap_m': r, 'kapsama': round(cov, 3), 'p90_m': round(quantile(d[k], .9)), 'en_yakin': near[k][1], 'en_yakin_m': round(near[k][0])}
            for k, (r, cov, _) in result.items()]
    tabular.write_csv(out / 'geofence_plani.csv', list(plan[0]), plan)
    write_json(out / 'geofence.geojson', {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'magaza': k, 'ad': stores[k]['ad'], 'yaricap_m': r}, 'geometry': {'type': 'Polygon', 'coordinates': [circle(stores[k]['pos'], r)]}}
        for k, (r, _, _) in result.items()]})
    md = ['# Geofence yarıçapları', '', f'{len(result)} mağaza; yarıçaplar ziyaret pinglerinin en az %{100 * args.min_coverage:g}’ini kapsıyor ve çemberler çakışmıyor.', '',
          md_table(['Mağaza', 'Ad', 'Yarıçap (m)', 'Kapsama', 'p90 (m)', 'En yakın', 'Mesafe (m)', 'Gerekçe'],
                   [[p_['magaza'], p_['ad'], f'{p_["yaricap_m"]:g}', f'%{100 * p_["kapsama"]:.0f}', p_['p90_m'], p_['en_yakin'], p_['en_yakin_m'], result[p_['magaza']][2]] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca mesafe yüzdelikleri gitti; cihaz kimlikleri ve konumlar gönderilmedi. Kapsama ve çakışma kodla denetlendi._']
    write_text(out / 'geofence.md', '\n'.join(md))
    print(f'[OK] {len(result)} mağaza. Çıktı: {out / "geofence.md"}')


if __name__ == '__main__':
    llm.run(main)
