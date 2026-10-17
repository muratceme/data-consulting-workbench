"""Şube yeri planlayıcı (AI agent): müşteri ve şube konumlarından kapsamayı (kapsama-alani bloğu) yerelde hesaplar; kapsama dışındaki
müşterileri yoğunluğa göre kümeler (konum-kumeleme bloğu, DBSCAN) ve her kümeyi numaralı olguya çevirir: müşteri sayısı, merkez,
yarıçap, en yakın mevcut şube. Model en çok N yeni şube yeri için küme seçer ve gerekçesini yazar. Seçimler kodla denetlenir (var olan
küme, tekrar yok, en çok N); önerilen merkezler eklendiğinde yeni kapsama oranını ve kazanılan müşteriyi kod hesaplar, modelin metnindeki
her sayı olgularla eşleştirilir. Koordinatlar ve müşteri kimlikleri modele gönderilmez; yalnızca küme özetleri gider.
Çıktı: sube-plani.md, oneriler.csv, olgular.csv, sube-plani.json."""
import argparse
from argparse import Namespace
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from konum import coverage, dbscan, haversine
from islem import numbers
from donem_yorumu import allowed_numbers, unknown_numbers, tr


def build_facts(columns, rows, args):
    cov = coverage(columns, rows, Namespace(sites=args.sites, id=args.id, site_id=args.site_id, lat=args.lat, lon=args.lon, radius=args.radius))
    site_cols, site_rows = tabular.read(args.sites)
    names = {r[args.site_id].strip(): (r.get(args.site_name, '') or r[args.site_id]).strip() for r in site_rows} if args.site_id in site_cols else {}
    pos = {r[args.id].strip(): r for r in rows}
    outside = [pos[o['kimlik']] for o in cov.rows if o['kapsamda'] == 'hayır']
    s = cov.summary
    facts, clusters = [], {}

    def add(text):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text})
    add(f"{s['musteri']} müşterinin {s['kapsanan']} tanesi mevcut şubelere {tr(args.radius, 0)} km içinde (kapsama %{tr(s['kapsama_%'], 1)}); kapsama dışında {s['musteri'] - s['kapsanan']} müşteri var")
    if len(outside) >= args.min_points:
        res = dbscan(columns, outside, Namespace(id=args.id, lat=args.lat, lon=args.lon, eps=args.eps, min_points=args.min_points))
        for k, n, lat, lon, radius in res.tables[0][2]:
            nearest = min(((haversine((lat, lon), (r_lat, r_lon)), sid) for sid, r_lat, r_lon in sites(site_rows, args)), default=(None, ''))
            clusters[k] = {'kume': k, 'musteri': n, 'enlem': lat, 'boylam': lon, 'yaricap_km': radius, 'en_yakin_sube': names.get(nearest[1], nearest[1]),
                           'en_yakin_km': round(nearest[0], 1) if nearest[0] is not None else None}
            add(f"Küme {k}: kapsama dışındaki {n} müşteri, küme yarıçapı {tr(radius, 1)} km; en yakın mevcut şube {clusters[k]['en_yakin_sube']} ({tr(clusters[k]['en_yakin_km'], 1)} km)")
        add(f"Hiçbir kümeye girmeyen dağınık müşteri: {res.summary['gurultu']}")
    return facts, clusters, cov, outside


def sites(site_rows, args):
    for r, lat, lon in zip(site_rows, numbers(site_rows, args.lat), numbers(site_rows, args.lon)):
        if lat is not None and lon is not None:
            yield r[args.site_id].strip(), lat, lon


def gain(cov, rows, args, picked):
    """Customers newly covered when sites are opened at the picked cluster centres."""
    out = {o['kimlik'] for o in cov.rows if o['kapsamda'] == 'hayır'}
    won = {}
    for r, lat, lon in zip(rows, numbers(rows, args.lat), numbers(rows, args.lon)):
        cid = r[args.id].strip()
        if cid not in out or lat is None or lon is None:
            continue
        p = (lat, lon)
        best = min(((haversine(p, (c['enlem'], c['boylam'])), c['kume']) for c in picked), default=(None, None))
        if best[0] is not None and best[0] <= args.radius:
            won[cid] = best[1]
    return won


def validator(facts, clusters, max_sites):
    ids = {f['id'] for f in facts}
    values = allowed_numbers(facts, list(clusters))

    def validate(obj):
        problems, picked = [], []
        for o in obj.get('oneriler') or []:
            k = str(o.get('kume', '')).strip().upper() if isinstance(o, dict) else ''
            if k not in clusters:
                problems.append(f'olmayan küme: {k or "?"} (seçenekler: {", ".join(clusters) or "yok"})')
                continue
            if k in [p['kume'] for p in picked]:
                problems.append(f'{k} iki kez önerilmiş')
                continue
            refs = [str(x).strip().upper() for x in o.get('kanit') or []]
            if not refs or any(r not in ids for r in refs):
                problems.append(f'{k}: kanit geçerli olgu numaraları içermeli')
            picked.append({'kume': k, 'gerekce': str(o.get('gerekce', '')).strip(), 'kanit': ', '.join(refs)})
        if len(picked) > max_sites:
            problems.append(f'en çok {max_sites} şube önerilebilir')
        if clusters and not picked:
            problems.append('kapsama dışında küme var; en az bir öneri yap')
        text = ' '.join([str(obj.get('ozet', ''))] + [p['gerekce'] for p in picked] + [str(x) for x in obj.get('riskler') or []])
        bad = unknown_numbers(text, values, list(clusters))
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)) + ' (yeni kapsama oranını kod hesaplar, yazma)')
        if not str(obj.get('ozet', '')).strip():
            problems.append('"ozet" boş')
        if problems:
            raise ValueError('; '.join(problems))
        return {'ozet': str(obj['ozet']).strip(), 'oneriler': picked, 'riskler': [str(x).strip() for x in obj.get('riskler') or [] if str(x).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen perakende ağ planlaması yapan bir konum analistisin. Kodla hesaplanmış numaralı olgularda mevcut şube kapsaması ve kapsama dışındaki '
          'müşteri kümeleri var. Yeni şube açılacak en çok N kümeyi seç; müşteri yoğunluğu, küme yarıçapı ve en yakın şubeye uzaklığı tart. Kiralama maliyeti, rakip ve trafik '
          'gibi olgularda olmayan bilgileri varsayım olarak riskler’e yaz. Yeni kapsama oranını kod hesaplayacak; sen hesaplama ve olgularda olmayan sayı yazma. '
          'Yanıt şeması: {"ozet": "...", "oneriler": [{"kume": "K1", "gerekce": "...", "kanit": ["F2"]}], "riskler": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Kapsama dışındaki müşteri kümelerinden yeni şube yeri öneren agent (koordinatlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'musteri_noktalari.csv')), help='Müşteri konumları')
    p.add_argument('--sites', default=str(here('ornek', 'subeler.csv')), help='Mevcut şubeler')
    p.add_argument('--id', default='musteri')
    p.add_argument('--site-id', default='sube')
    p.add_argument('--site-name', default='ad')
    p.add_argument('--lat', default='enlem')
    p.add_argument('--lon', default='boylam')
    p.add_argument('--radius', type=float, default=10, help='Hizmet yarıçapı (km)')
    p.add_argument('--eps', type=float, default=8, help='Kümeleme komşuluk yarıçapı (km)')
    p.add_argument('--min-points', type=int, default=5, help='Küme için en az müşteri')
    p.add_argument('--max-sites', type=int, default=2, help='Önerilecek en çok yeni şube')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    facts, clusters, cov, outside = build_facts(columns, rows, args)
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu'], facts)
    user = f'En çok {args.max_sites} yeni şube önerilebilir; hizmet yarıçapı {tr(args.radius, 0)} km.\n\nOlgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, clusters, args.max_sites))
    if result is None:
        return
    picked = [clusters[o['kume']] for o in result['oneriler']]
    won = gain(cov, rows, args, picked)
    s = cov.summary
    new_cov = s['kapsanan'] + len(won)
    rows_out = [{**clusters[o['kume']], 'kazanilan_musteri': sum(v == o['kume'] for v in won.values()), 'gerekce': o['gerekce'], 'kanit': o['kanit']} for o in result['oneriler']]
    tabular.write_csv(out / 'oneriler.csv', list(rows_out[0]) if rows_out else ['kume'], rows_out)
    after = {'kapsanan_once': s['kapsanan'], 'kapsanan_sonra': new_cov, 'kapsama_once_%': s['kapsama_%'], 'kapsama_sonra_%': round(100 * new_cov / s['musteri'], 2)}
    write_json(out / 'sube-plani.json', {**result, 'etki': after, 'kumeler': list(clusters.values()), 'olgular': facts})
    md = ['# Yeni şube planı', '', result['ozet'], '', f"**Kodla hesaplanan etki:** kapsama %{tr(after['kapsama_once_%'], 1)} → %{tr(after['kapsama_sonra_%'], 1)} "
          f"({s['kapsanan']} → {new_cov} müşteri, {tr(args.radius, 0)} km).", '',
          md_table(['Küme', 'Merkez', 'Müşteri', 'Kazanılan', 'En yakın şube', 'Gerekçe'],
                   [[r['kume'], f"{r['enlem']}, {r['boylam']}", r['musteri'], r['kazanilan_musteri'], f"{r['en_yakin_sube']} ({tr(r['en_yakin_km'], 1)} km)", r['gerekce']] for r in rows_out])]
    if result['riskler']:
        md += ['', '## Riskler ve varsayımlar', ''] + [f'- {x}' for x in result['riskler']]
    md += ['', '## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Mesafeler kuş uçuşudur; önerilen merkez kümenin ağırlık merkezidir, kesin adres değildir._"]
    write_text(out / 'sube-plani.md', '\n'.join(md))
    print(f"[OK] {len(picked)} şube önerildi; kapsama %{tr(after['kapsama_once_%'], 1)} → %{tr(after['kapsama_sonra_%'], 1)}. Çıktı: {out / 'sube-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
