"""Konum blokları: noktaları GeoJSON bölgelerine atama (nokta–çokgen) ve araç GPS izlerinden rota, durak ve GPS sıçraması özeti.
Harita servisine bağlanmaz; mesafeler büyük daire (haversine) formülüyle hesaplanır."""
import json, math
from collections import Counter, defaultdict
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fmt

D, P = 'spatial', 'Konum ve sensör dosyaları'
R_KM = 6371.0088


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_KM * math.asin(math.sqrt(h))


def in_ring(lon, lat, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_polygon(lon, lat, rings):
    """GeoJSON polygon: first ring is the outline, the rest are holes."""
    return in_ring(lon, lat, rings[0]) and not any(in_ring(lon, lat, h) for h in rings[1:])


def load_regions(path, name_key):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'GeoJSON okunamadı: {exc}') from None
    regions = []
    for i, f in enumerate(data.get('features', []) if data.get('type') == 'FeatureCollection' else [data], 1):
        g = f.get('geometry') or {}
        name = (f.get('properties') or {}).get(name_key) or f'bölge {i}'
        polys = [g['coordinates']] if g.get('type') == 'Polygon' else g.get('coordinates', []) if g.get('type') == 'MultiPolygon' else []
        polys = [[[tuple(pt[:2]) for pt in ring] for ring in poly] for poly in polys]
        if polys:
            regions.append((name, polys))
    if not regions:
        raise ValueError('GeoJSON içinde Polygon/MultiPolygon bulunamadı.')
    return regions


@op('nokta-bolge-eslestir', title='Noktaları GeoJSON bölgelerine ata (nokta–çokgen)', domain=D, product=P, tag='point in polygon',
    samples={'musteri_konumlari.csv': 'musteri_konumlari.csv', 'bolgeler.geojson': 'bolgeler.geojson'},
    args=[arg('--regions', default='@ornek/bolgeler.geojson'), arg('--name', default='ad', help='Bölge adının olduğu GeoJSON özelliği'), arg('--lat', default='enlem'), arg('--lon', default='boylam')],
    description='Müşteri, mağaza veya cihaz koordinatlarını satış bölgesi, ilçe ya da hizmet alanı çokgenlerine (GeoJSON Polygon/MultiPolygon, delikli alanlar dahil) atar; hiçbir bölgeye düşmeyen ve birden çok bölgeye düşen (çakışan sınır) noktaları işaretler, bölge başına nokta sayısını verir.',
    input='Koordinat listesi + GeoJSON bölgeler', output='sonuc.csv (nokta → bölge)', command='python main.py --input bayiler.csv --regions satis_bolgeleri.geojson --name bolge_adi',
    expect={'nokta': 6, 'eslesen': 5, 'disinda': 1, 'bolge': 4})
def point_region(columns, rows, args):
    lats, lons = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    regions = load_regions(args.regions, args.name)
    out = []
    for r, la, lo in zip(rows, lats, lons):
        if la is None or lo is None:
            out.append({**r, 'bolge': '', 'durum': 'koordinat yok'})
            continue
        hits = [n for n, polys in regions if any(in_polygon(lo, la, p) for p in polys)]
        out.append({**r, 'bolge': ', '.join(hits), 'durum': 'dışında' if not hits else 'çakışma' if len(hits) > 1 else 'eşleşti'})
    counts = Counter(h for o in out for h in o['bolge'].split(', ') if h)
    return Result(f'{len(out)} nokta; {sum(bool(o["bolge"]) for o in out)} bölgeye atandı, {sum(o["durum"] == "dışında" for o in out)} dışında',
                  [*columns, 'bolge', 'durum'], out,
                  {'nokta': len(out), 'eslesen': sum(bool(o['bolge']) for o in out), 'disinda': sum(o['durum'] == 'dışında' for o in out),
                   'cakisan': sum(o['durum'] == 'çakışma' for o in out), 'bolge': len(regions)},
                  tables=[('Bölge başına nokta', ['Bölge', 'Nokta'], [[n, counts[n]] for n, _ in regions])],
                  notes=['GeoJSON koordinat sırası [boylam, enlem] olmalıdır; sınırın tam üzerindeki noktalar bölgelerden birine atanır.'])


@op('arac-rota-ozeti', title='Araç GPS izlerinden rota, durak ve sıçrama özeti', domain=D, product=P, tag='GPS', samples={'arac_konumlari.csv': 'arac_konumlari.csv'},
    args=[arg('--vehicle', default='arac'), arg('--time', default='zaman'), arg('--lat', default='enlem'), arg('--lon', default='boylam'),
          arg('--max-kmh', type=float, default=200.0, help='Bu hızın üstü GPS sıçraması sayılır'), arg('--stop-kmh', type=float, default=3.0), arg('--stop-min', type=float, default=5.0)],
    description='Araç/kurye GPS kayıtlarını zamana göre sıralayıp araç bazında toplam mesafeyi (haversine), süreyi, ortalama ve en yüksek hızı, belirli süreden uzun durakları ve fiziksel olarak imkânsız konum sıçramalarını (mesafeye katmadan) hesaplar. Lojistik raporları, kilometre mutabakatı ve sürüş analizi için.',
    input='GPS kayıtları (araç, zaman, enlem, boylam)', output='sonuc.csv (araç bazında özet) + durak listesi', command='python main.py --input telematik.csv --vehicle plaka --time ts --stop-min 10',
    expect={'arac': 2, 'toplam_km': 21.51, 'durak': 1, 'gps_sicrama': 1})
def route(columns, rows, args):
    lats, lons = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    tracks = defaultdict(list)
    for r, la, lo in zip(rows, lats, lons):
        t = tabular.parse_date(r[need(args.time, columns, '--time')])
        if t and la is not None and lo is not None:
            tracks[r[need(args.vehicle, columns, '--vehicle')].strip()].append((t[0], la, lo))
    out, stops = [], []
    jumps_total = 0
    for v, pts in sorted(tracks.items()):
        pts.sort()
        last, km, top, jumps = pts[0], 0.0, 0.0, 0
        still_since = None
        n_stops = 0
        for p_ in pts[1:]:
            hours = (p_[0] - last[0]).total_seconds() / 3600
            d = haversine(last[1:], p_[1:])
            speed = d / hours if hours else (0.0 if d < 0.05 else float('inf'))
            if speed > args.max_kmh:
                jumps += 1
                continue
            km += d
            top = max(top, speed)
            if speed < args.stop_kmh:
                still_since = still_since or last[0]
            elif still_since:
                dur = (last[0] - still_since).total_seconds() / 60
                if dur >= args.stop_min:
                    n_stops += 1
                    stops.append([v, still_since.isoformat(sep=' '), round(dur, 1), round(last[1], 5), round(last[2], 5)])
                still_since = None
            last = p_
        if still_since and (last[0] - still_since).total_seconds() / 60 >= args.stop_min:
            n_stops += 1
            stops.append([v, still_since.isoformat(sep=' '), round((last[0] - still_since).total_seconds() / 60, 1), round(last[1], 5), round(last[2], 5)])
        hours = (pts[-1][0] - pts[0][0]).total_seconds() / 3600
        jumps_total += jumps
        out.append({'arac': v, 'nokta': len(pts), 'baslangic': pts[0][0].isoformat(sep=' '), 'bitis': pts[-1][0].isoformat(sep=' '), 'sure_dk': round(hours * 60, 1),
                    'mesafe_km': round(km, 3), 'ort_hiz_kmh': round(km / hours, 1) if hours else '', 'max_hiz_kmh': round(top, 1), 'durak': n_stops, 'gps_sicrama': jumps})
    if not out:
        raise ValueError('Zamanı ve koordinatı okunabilen GPS kaydı yok.')
    return Result(f'{len(out)} araç, toplam {sum(o["mesafe_km"] for o in out):.2f} km; {len(stops)} durak, {jumps_total} GPS sıçraması', list(out[0]), out,
                  {'arac': len(out), 'toplam_km': round(sum(o['mesafe_km'] for o in out), 2), 'durak': len(stops), 'gps_sicrama': jumps_total},
                  tables=[('Duraklar', ['Araç', 'Başlangıç', 'Süre (dk)', 'Enlem', 'Boylam'], stops)],
                  notes=['Mesafe ardışık noktalar arasındaki kuş uçuşu toplamıdır; seyrek örneklenmiş izlerde gerçek yol mesafesinin altında kalır.'])


@op('mesafe-matrisi', title='Noktalar arası mesafe matrisi ve en yakın komşu', domain=D, product=P, tag='mesafe matrisi', samples={'depolar.csv': 'depolar.csv'},
    args=[arg('--id', default='kod'), arg('--lat', default='enlem'), arg('--lon', default='boylam')],
    description='Depo, mağaza veya müşteri noktalarının tüm ikili kuş uçuşu (haversine) mesafelerini kilometre olarak matris hâlinde (mesafe_matrisi.csv) yazar; her nokta için en yakın ve en uzak noktayı, ortalama mesafeyi verir. Rota optimizasyonu, bölge ataması ve lojistik modellerine girdi hazırlamak için.',
    input='Nokta listesi (kod, enlem, boylam)', output='sonuc.csv (nokta bazında en yakın komşu) + mesafe_matrisi.csv', command='python main.py --input bayiler.csv --id bayi_kodu',
    expect={'nokta': 4, 'en_yakin_cift': 'D1–D2', 'en_yakin_km': 13.93})
def matrix(columns, rows, args):
    lats, lons = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    icol = need(args.id, columns, '--id')
    pts = [(r[icol].strip(), la, lo) for r, la, lo in zip(rows, lats, lons) if la is not None and lo is not None and -90 <= la <= 90 and -180 <= lo <= 180]
    if len(pts) < 2:
        raise ValueError('En az iki geçerli koordinat gerekir.')
    dist = {(a[0], b[0]): haversine(a[1:], b[1:]) for a in pts for b in pts}
    out = []
    for a, *_ in pts:
        others = [(dist[(a, b)], b) for b, *_ in pts if b != a]
        near, far = min(others), max(others)
        out.append({'nokta': a, 'en_yakin': near[1], 'en_yakin_km': round(near[0], 2), 'en_uzak': far[1], 'en_uzak_km': round(far[0], 2),
                    'ortalama_km': round(sum(d for d, _ in others) / len(others), 2)})
    best = min(((dist[(a[0], b[0])], a[0], b[0]) for i, a in enumerate(pts) for b in pts[i + 1:]))
    ids = [p_[0] for p_ in pts]
    mat = [{'nokta': a, **{b: round(dist[(a, b)], 2) for b in ids}} for a in ids]
    return Result(f'{len(pts)} nokta; en yakın çift {best[1]}–{best[2]} ({best[0]:.2f} km)', list(out[0]), out,
                  {'nokta': len(pts), 'gecersiz': len(rows) - len(pts), 'en_yakin_cift': f'{best[1]}–{best[2]}', 'en_yakin_km': round(best[0], 2)},
                  files={'mesafe_matrisi.csv': (['nokta', *ids], mat)},
                  notes=['Kuş uçuşu mesafedir; karayolu mesafesi şehir içinde genelde 1,3–1,5 katıdır.'])


@op('bolge-giris-cikis', title='GPS izinden çember bölgelere (geofence) giriş, çıkış ve kalış süresi', domain=D, product=P, tag='geofence',
    samples={'arac_konumlari.csv': 'arac_konumlari.csv', 'bolge_cemberleri.csv': 'bolge_cemberleri.csv'},
    args=[arg('--zones', default='@ornek/bolge_cemberleri.csv', help='Bölgeler (ad, enlem, boylam, yaricap_m)'), arg('--vehicle', default='arac'), arg('--time', default='zaman'),
          arg('--lat', default='enlem'), arg('--lon', default='boylam')],
    description='Araç veya kurye GPS kayıtlarını depo, müşteri ve şantiye gibi merkez–yarıçap ile tanımlı bölgelerle karşılaştırır; her bölgeye giriş ve çıkış zamanını, ziyaret başına kalış süresini ve bölge bazında toplam ziyareti çıkarır. Teslimat kanıtı, bekleme süresi ve hizmet süresi raporları için.',
    input='GPS kayıtları + bölge listesi', output='sonuc.csv (ziyaretler)', command='python main.py --input telematik.csv --zones musteri_lokasyonlari.csv',
    expect={'ziyaret': 2, 'toplam_kalis_dk': 20.0, 'bolge': 2})
def geofence(columns, rows, args):
    zcols, zrows = tabular.read(args.zones)
    for c in ('ad', 'enlem', 'boylam', 'yaricap_m'):
        need(c, zcols, '--zones')
    zones = [(z['ad'], la, lo, r) for z, la, lo, r in zip(zrows, numbers(zrows, 'enlem'), numbers(zrows, 'boylam'), numbers(zrows, 'yaricap_m')) if None not in (la, lo, r)]
    lats, lons = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    tracks = defaultdict(list)
    for r, la, lo in zip(rows, lats, lons):
        t = tabular.parse_date(r[need(args.time, columns, '--time')])
        if t and la is not None and lo is not None:
            tracks[r[need(args.vehicle, columns, '--vehicle')].strip()].append((t[0], la, lo))
    out = []
    for v, pts in sorted(tracks.items()):
        pts.sort()
        for name, zla, zlo, rad in zones:
            inside_since = None
            for t, la, lo in pts:
                inside = haversine((la, lo), (zla, zlo)) * 1000 <= rad
                if inside and inside_since is None:
                    inside_since = t
                elif not inside and inside_since is not None:
                    out.append({'arac': v, 'bolge': name, 'giris': inside_since.isoformat(sep=' '), 'cikis': t.isoformat(sep=' '), 'kalis_dk': round((t - inside_since).total_seconds() / 60, 1)})
                    inside_since = None
            if inside_since is not None:
                out.append({'arac': v, 'bolge': name, 'giris': inside_since.isoformat(sep=' '), 'cikis': 'iz bitti (içeride)', 'kalis_dk': round((pts[-1][0] - inside_since).total_seconds() / 60, 1)})
    out.sort(key=lambda o: (o['arac'], o['giris']))
    per = Counter(o['bolge'] for o in out)
    return Result(f'{len(out)} ziyaret, toplam kalış {sum(o["kalis_dk"] for o in out):.0f} dk', ['arac', 'bolge', 'giris', 'cikis', 'kalis_dk'], out,
                  {'ziyaret': len(out), 'toplam_kalis_dk': round(sum(o['kalis_dk'] for o in out), 1), 'bolge': len(zones)},
                  tables=[('Bölge bazında ziyaret', ['Bölge', 'Ziyaret'], [[z[0], per[z[0]]] for z in zones])],
                  notes=['Giriş ve çıkış, bölge içindeki ilk ve dışındaki ilk GPS kaydının zamanıdır; seyrek örneklenmiş izlerde süre örnekleme aralığı kadar sapabilir.'])
