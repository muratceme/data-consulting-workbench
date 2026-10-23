"""Depo yeri seçici (AI agent): aday depolar (konum, kapasite, sabit maliyet) ve talep noktaları (konum, haftalık talep) arasından açılacak
depoları seçer. Talep noktaları açık depolara kapasiteyi gözeterek en yakından başlayarak kodla atanır; haftalık maliyet = sabit maliyet
+ ton × km × taşıma ücreti. Kod tüm aday kombinasyonlarını (küçük aday sayısında) tarayıp en düşük maliyeti de bulur; modele aday ve
talep özeti ile mesafe tablosu gider. Seçim kodla denetlenir: aday listede olmalı, toplam kapasite talebi karşılamalı, her talep bir
depoya atanabilmeli, maliyet bulunan en iyi çözümün belirli payı içinde olmalı. Çıktı: depo_atamalari.csv, depolar.geojson, depo-secimi.md."""
import argparse, math
from itertools import combinations
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def assign(open_, sites, demand, dist, rate):
    """Greedy: biggest demand first goes to the nearest open site with room. Returns (cost, assignment) or (None, reason)."""
    room = {s: sites[s]['cap'] for s in open_}
    plan = {}
    for d in sorted(demand, key=lambda d: -demand[d]['t']):
        cands = sorted((dist[s][d], s) for s in open_ if room[s] >= demand[d]['t'])
        if not cands:
            return None, d
        km_, s = cands[0]
        room[s] -= demand[d]['t']
        plan[d] = (s, km_)
    cost = sum(sites[s]['fix'] for s in open_) + sum(demand[d]['t'] * km_ * rate for d, (s, km_) in plan.items())
    return cost, plan


def best_solution(sites, demand, dist, rate):
    best = (math.inf, None, None)
    names = list(sites)
    for k in range(1, len(names) + 1):
        for combo in combinations(names, k):
            cost, plan = assign(combo, sites, demand, dist, rate)
            if cost is not None and cost < best[0]:
                best = (cost, combo, plan)
    return best


def validator(sites, demand, dist, rate, best, tol):
    def validate(obj):
        chosen = [str(s).strip() for s in obj.get('depolar') or []]
        bad = [s for s in chosen if s not in sites]
        if bad or not chosen:
            raise ValueError(f'aday listesinde olmayan depo: {", ".join(bad) or "depo seçilmemiş"}')
        if len(set(chosen)) != len(chosen):
            raise ValueError('aynı depo iki kez seçilmiş')
        need = sum(d['t'] for d in demand.values())
        cap = sum(sites[s]['cap'] for s in chosen)
        if cap < need:
            raise ValueError(f'seçilen depoların kapasitesi {cap:g} ton, haftalık talep {need:g} ton')
        cost, plan = assign(chosen, sites, demand, dist, rate)
        if cost is None:
            raise ValueError(f'{plan} talebi kapasitesi kalan bir depoya atanamıyor')
        if cost > best[0] * (1 + tol):
            raise ValueError(f'haftalık maliyet {cost:,.0f}, bulunan en iyi çözüm {best[0]:,.0f} ({", ".join(best[1])}); fark %{100 * (cost / best[0] - 1):.1f} '
                             f'(en fazla %{100 * tol:g})')
        return chosen, cost, plan, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir lojistik ağ planlamacısısın. Aday depolardan hangilerinin açılacağını seç: sabit maliyet ile taşıma maliyeti (ton × km × ücret) '
          'arasında denge kur, kapasite talebi karşılasın. Talep noktaları kodla en yakın uygun depoya atanır. Yanıt şeması: {"depolar": ["D-..."], "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='Aday depolar arasından açılacakları seçen; atamayı, maliyeti ve en iyi çözüme yakınlığı kodla denetleyen agent.')
    p.add_argument('--sites', default=str(here('ornek', 'aday_depolar.csv')), help='depo, enlem, boylam, kapasite_ton, haftalik_sabit_maliyet')
    p.add_argument('--demand', default=str(here('ornek', 'talep_noktalari.csv')), help='nokta, enlem, boylam, haftalik_talep_ton')
    p.add_argument('--rate', type=float, default=0.3, help='Ton başına km taşıma ücreti')
    p.add_argument('--tolerance', type=float, default=0.03, help='En iyi çözümden kabul edilen maliyet farkı')
    llm.add_arguments(p)
    args = p.parse_args()
    num = lambda v: float(v.replace(',', '.'))
    _, srows = tabular.read(args.sites)
    sites = {r['depo'].strip(): {'pos': (num(r['enlem']), num(r['boylam'])), 'cap': num(r['kapasite_ton']), 'fix': num(r['haftalik_sabit_maliyet'])} for r in srows}
    _, drows = tabular.read(args.demand)
    demand = {r['nokta'].strip(): {'pos': (num(r['enlem']), num(r['boylam'])), 't': num(r['haftalik_talep_ton'])} for r in drows}
    if len(sites) > 14:
        raise ValueError('Tam tarama için en fazla 14 aday depo desteklenir.')
    dist = {s: {d: km(sites[s]['pos'], demand[d]['pos']) for d in demand} for s in sites}
    best = best_solution(sites, demand, dist, args.rate)
    if best[1] is None:
        raise ValueError('Tüm adaylar açılsa bile talep karşılanamıyor.')
    out = output_dir(args.output)
    user = (f'Taşıma ücreti ton×km başına {args.rate:g}. Haftalık toplam talep {sum(d["t"] for d in demand.values()):g} ton.\n'
            'Aday depolar (kapasite ton · haftalık sabit maliyet):\n' + '\n'.join(f'- {s}: {v["cap"]:g} · {v["fix"]:,.0f}' for s, v in sites.items())
            + '\nTalep noktaları (ton · depolara km):\n'
            + '\n'.join(f'- {d}: {v["t"]:g} · ' + ', '.join(f'{s} {dist[s][d]:.0f}' for s in sites) for d, v in demand.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(sites, demand, dist, args.rate, best, args.tolerance))
    if result is None:
        return
    chosen, cost, plan, why = result
    rows = [{'nokta': d, 'talep_ton': demand[d]['t'], 'depo': s, 'km': round(k, 1), 'tasima': round(demand[d]['t'] * k * args.rate)} for d, (s, k) in sorted(plan.items())]
    tabular.write_csv(out / 'depo_atamalari.csv', list(rows[0]), rows)
    feats = [{'type': 'Feature', 'properties': {'depo': s, 'acik': s in chosen}, 'geometry': {'type': 'Point', 'coordinates': [sites[s]['pos'][1], sites[s]['pos'][0]]}} for s in sites]
    feats += [{'type': 'Feature', 'properties': {'nokta': d, 'depo': s}, 'geometry': {'type': 'LineString', 'coordinates': [[demand[d]['pos'][1], demand[d]['pos'][0]],
                                                                                                                         [sites[s]['pos'][1], sites[s]['pos'][0]]]}} for d, (s, _) in plan.items()]
    write_json(out / 'depolar.geojson', {'type': 'FeatureCollection', 'features': feats})
    load = {s: sum(demand[d]['t'] for d, (x, _) in plan.items() if x == s) for s in chosen}
    md = ['# Depo yeri seçimi', '', f'Açılacak depolar: {", ".join(chosen)}. Haftalık maliyet {cost:,.0f} (taramada bulunan en iyi {best[0]:,.0f}: {", ".join(best[1])}).', '', why, '',
          md_table(['Depo', 'Kapasite', 'Atanan ton', 'Doluluk', 'Sabit maliyet'], [[s, f'{sites[s]["cap"]:g}', f'{load[s]:g}', f'%{100 * load[s] / sites[s]["cap"]:.0f}',
                                                                                     f'{sites[s]["fix"]:,.0f}'] for s in chosen]), '',
          md_table(['Nokta', 'Talep (ton)', 'Depo', 'km'], [[r['nokta'], f'{r["talep_ton"]:g}', r['depo'], r['km']] for r in rows]), '',
          f'_Model: {meta["model"]} · Atama, maliyet ve tüm kombinasyonlar içindeki en iyi çözüm kodla hesaplandı. Mesafeler kuş uçuşu; yol mesafesi için katsayı uygulanmalıdır._']
    write_text(out / 'depo-secimi.md', '\n'.join(md))
    print(f'[OK] {len(chosen)} depo, haftalık maliyet {cost:,.0f}. Çıktı: {out / "depo-secimi.md"}')


if __name__ == '__main__':
    llm.run(main)
