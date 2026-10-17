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


@op('durak-tespiti', title='GPS izlerinden durakları (belirli yarıçapta en az N dakika kalınan yerler) çıkar', domain=D, product=P, tag='durak tespiti',
    samples={'gps_izleri.csv': 'gps_izleri.csv'},
    args=[arg('--id', default='arac'), arg('--time', default='zaman'), arg('--lat', default='enlem'), arg('--lon', default='boylam'),
          arg('--radius', type=float, default=200, help='Durak yarıçapı (metre)'), arg('--min-minutes', type=float, default=10, help='Durak sayılması için en kısa süre (dakika)')],
    description='Araç, kurye veya saha personelinin GPS izinde, ilk noktadan belirli yarıçap içinde en az N dakika kalınan bölümleri durak olarak çıkarır (stay-point algoritması); her durağın başlangıç/bitiş zamanını, süresini, merkez koordinatını ve nokta sayısını verir. Teslimat noktası süresi, mola ve rota dışı bekleme analizi için.',
    input='GPS noktaları (kimlik, zaman, enlem, boylam)', output='sonuc.csv (duraklar)', command='python main.py --input gps.csv --radius 150 --min-minutes 5',
    expect={'arac': 2, 'durak': 3, 'toplam_durak_dk': 70.0})
def stay_points(columns, rows, args):
    ic, tc = need(args.id, columns, '--id'), need(args.time, columns, '--time')
    lat, lon = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    tracks = defaultdict(list)
    for r, a, b in zip(rows, lat, lon):
        d = tabular.parse_date(r[tc])
        if d and a is not None and b is not None:
            tracks[r[ic].strip()].append((d[0], a, b))
    if not tracks:
        raise ValueError('Zamanı ve koordinatı okunabilen nokta yok.')
    out = []
    for vid, pts in sorted(tracks.items()):
        pts.sort()
        i = 0
        while i < len(pts):
            j = i + 1
            while j < len(pts) and haversine(pts[i][1:], pts[j][1:]) * 1000 <= args.radius:
                j += 1
            minutes = (pts[j - 1][0] - pts[i][0]).total_seconds() / 60
            if j - i > 1 and minutes >= args.min_minutes:
                seg = pts[i:j]
                out.append({'arac': vid, 'baslangic': seg[0][0].strftime('%Y-%m-%d %H:%M'), 'bitis': seg[-1][0].strftime('%Y-%m-%d %H:%M'), 'sure_dk': round(minutes, 1),
                            'enlem': round(sum(p[1] for p in seg) / len(seg), 6), 'boylam': round(sum(p[2] for p in seg) / len(seg), 6), 'nokta': len(seg)})
                i = j
            else:
                i += 1
    total = round(sum(o['sure_dk'] for o in out), 1)
    return Result(f'{len(tracks)} araç; {len(out)} durak, toplam {fmt(total)} dakika', ['arac', 'baslangic', 'bitis', 'sure_dk', 'enlem', 'boylam', 'nokta'], out,
                  {'arac': len(tracks), 'durak': len(out), 'toplam_durak_dk': total},
                  tables=[('Araç bazında', ['Araç', 'Durak', 'Toplam dk'], [[v, sum(o['arac'] == v for o in out), round(sum(o['sure_dk'] for o in out if o['arac'] == v), 1)] for v in sorted(tracks)])],
                  notes=['Süre, durak içindeki ilk ve son nokta arasındadır; GPS örnekleme aralığı uzunsa gerçek süre biraz daha uzundur. Kapalı alanlarda sinyal sıçramaları durağı bölebilir; yarıçapı büyütün.'])


GEOHASH = '0123456789bcdefghjkmnpqrstuvwxyz'


def geohash(lat, lon, precision):
    lat_r, lon_r, bits, bit, ch, even, out = [-90.0, 90.0], [-180.0, 180.0], [16, 8, 4, 2, 1], 0, 0, True, []
    while len(out) < precision:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch |= bits[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(GEOHASH[ch])
            bit, ch = 0, 0
    return ''.join(out), (lat_r[0] + lat_r[1]) / 2, (lon_r[0] + lon_r[1]) / 2


@op('izgara-yogunluk', title='Konumları geohash ızgarasında topla (ısı haritası verisi)', domain=D, product=P, tag='geohash', samples={'teslimat_noktalari.csv': 'teslimat_noktalari.csv'},
    args=[arg('--lat', default='enlem'), arg('--lon', default='boylam'), arg('--value', default='', help='Toplanacak sayı sütunu (isteğe bağlı; boşsa adet)'),
          arg('--precision', type=int, default=6, help='Geohash uzunluğu: 5 ≈ 4,9 km, 6 ≈ 1,2 km, 7 ≈ 150 m')],
    description='Nokta verisini (müşteri, teslimat, olay, sensör) geohash hücrelerine atar; hücre başına nokta sayısını, isteğe bağlı değer toplamını, payını ve hücre merkez koordinatını verir. Kişisel konumu paylaşmadan ısı haritası üretmek, bölge yoğunluğunu karşılaştırmak ve BI aracına hafif bir konum katmanı vermek için.',
    input='Noktalar (enlem, boylam; isteğe bağlı değer)', output='sonuc.csv (hücre bazında yoğunluk)', command='python main.py --input siparis_konum.csv --precision 5 --value tutar',
    expect={'nokta': 12, 'hucre': 4, 'en_yogun_hucre': 'sxk97w', 'en_yogun_pay_%': 50.0, 'gecersiz': 1})
def grid(columns, rows, args):
    if not 1 <= args.precision <= 12:
        raise ValueError('--precision 1 ile 12 arasında olmalı.')
    lat, lon = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    val = numbers(rows, need(args.value, columns, '--value')) if args.value else [None] * len(rows)
    cells, bad = {}, 0
    for a, b, v in zip(lat, lon, val):
        if a is None or b is None or not (-90 <= a <= 90 and -180 <= b <= 180) or (a == 0 and b == 0):
            bad += 1
            continue
        h, clat, clon = geohash(a, b, args.precision)
        c = cells.setdefault(h, {'hucre': h, 'merkez_enlem': round(clat, 6), 'merkez_boylam': round(clon, 6), 'nokta': 0, 'deger': 0.0})
        c['nokta'] += 1
        c['deger'] += v or 0.0
    if not cells:
        raise ValueError('Geçerli koordinat yok.')
    n = sum(c['nokta'] for c in cells.values())
    out = sorted(cells.values(), key=lambda c: (-c['nokta'], c['hucre']))
    for c in out:
        c['pay_%'] = round(100 * c['nokta'] / n, 2)
        c['deger'] = round(c['deger'], 2) if args.value else ''
    return Result(f'{n} nokta {len(out)} hücrede; en yoğun hücre {out[0]["hucre"]} (%{fmt(out[0]["pay_%"])})', ['hucre', 'merkez_enlem', 'merkez_boylam', 'nokta', 'deger', 'pay_%'], out,
                  {'nokta': n, 'hucre': len(out), 'en_yogun_hucre': out[0]['hucre'], 'en_yogun_pay_%': out[0]['pay_%'], 'gecersiz': bad},
                  notes=['Geohash hücreleri dikdörtgendir ve kutuplara doğru daralır. Az noktalı hücreler kişiyi tanımlatabilir; paylaşmadan önce küçük hücreleri birleştirin veya kesinliği düşürün.',
                         '(0,0) ve aralık dışı koordinatlar geçersiz sayılır.'])
