"""Konum ve zaman serisi blokları: koordinat kontrolü, en yakın nokta, sensör eşik ihlali, veri boşluğu, yeniden örnekleme, iş saati SLA."""
import math
from collections import defaultdict
from datetime import datetime, time, timedelta
import tabular
from islem import op, arg, Result, need, numbers, fmt

D, P = 'spatial', 'Konum ve sensör dosyaları'
SENSOR = {'sensor_okumalari.csv': 'sensor_okumalari.csv'}
SENSOR_ARGS = [arg('--id', default='sensor'), arg('--time', default='zaman'), arg('--value', default='sicaklik')]
TR_BOX = (35.8, 42.2, 25.6, 44.9)  # lat min/max, lon min/max (Türkiye, kara + karasuları yaklaşık)


def haversine(a, b):
    r = 6371.0088
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def coord_issue(lat, lon, box=TR_BOX):
    if lat is None or lon is None:
        return 'boş veya sayı değil'
    if lat == 0 and lon == 0:
        return '0,0 (varsayılan değer)'
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return 'geçerli aralık dışında'
    inside = box[0] <= lat <= box[1] and box[2] <= lon <= box[3]
    if not inside and box[0] <= lon <= box[1] and box[2] <= lat <= box[3]:
        return 'enlem/boylam yer değiştirmiş'
    if not inside:
        return 'Türkiye sınırları dışında'
    return ''


@op('koordinat-kontrol', title='Koordinatları doğrula (yer değiştirme, 0,0, ülke dışı)', domain=D, product=P, tag='koordinat', samples={'magazalar.csv': 'magazalar.csv'},
    args=[arg('--lat', default='enlem'), arg('--lon', default='boylam'), arg('--box', help='Sınır kutusu: enlem_min,enlem_max,boylam_min,boylam_max (varsayılan Türkiye)')],
    description='Enlem/boylam sütunlarında boş ve geçersiz değerleri, 0,0 varsayılanını, yer değiştirmiş enlem-boylamı ve ülke sınır kutusu dışında kalan noktaları bulur; yer değiştirmiş olanlar için düzeltilmiş değeri önerir.',
    input='CSV/XLSX + enlem ve boylam sütunu', output='sonuc.csv', command='python main.py --lat lat --lon lng',
    expect={'gecerli': 4, 'hatali': 2, 'yer_degistirmis': 1})
def koordinat(columns, rows, args):
    box = tuple(float(x) for x in args.box.split(',')) if args.box else TR_BOX
    la, lo = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    out = []
    for r, a, b in zip(rows, la, lo):
        issue = coord_issue(a, b, box)
        fix = f'{b}, {a}' if issue == 'enlem/boylam yer değiştirmiş' else ''
        out.append({**r, 'durum': issue or 'geçerli', 'oneri': fix})
    bad = [o for o in out if o['durum'] != 'geçerli']
    return Result(f'{len(rows) - len(bad)} geçerli, {len(bad)} hatalı koordinat', columns + ['durum', 'oneri'], out,
                  {'gecerli': len(rows) - len(bad), 'hatali': len(bad), 'yer_degistirmis': sum(o['durum'] == 'enlem/boylam yer değiştirmiş' for o in out)})


@op('en-yakin-nokta', title='Her kayda en yakın noktayı bul (mağaza, depo)', domain=D, product=P, tag='mesafe', samples={'musteri_konumlari.csv': 'musteri_konumlari.csv', 'magazalar.csv': 'magazalar.csv'},
    args=[arg('--points', default='@ornek/magazalar.csv', help='Hedef noktalar dosyası (ör. mağazalar)'), arg('--id', default='magaza'), arg('--lat', default='enlem'), arg('--lon', default='boylam'),
          arg('--max-km', type=float, default=50.0, help='Bu mesafeden uzaksa “kapsam dışı” işaretle')],
    description='Her müşteri/adres için hedef noktalar arasından kuş uçuşu (haversine) en yakın olanı ve mesafeyi bulur; geçersiz koordinatlı noktaları hesaba katmaz, belirli mesafenin dışında kalanları “kapsam dışı” işaretler. Mağaza atama ve bölge planlaması için.',
    input='Kaynak noktalar + hedef noktalar (enlem, boylam)', output='sonuc.csv', command='python main.py --points depolar.csv --id depo --max-km 30',
    expect={'kayit': 6, 'kapsam_disi': 1, 'kullanilan_hedef': 4})
def en_yakin(columns, rows, args):
    pc, pr = tabular.read(args.points)
    pid = need(args.id, pc, '--id')
    targets = [(r[pid], (a, b)) for r, a, b in zip(pr, numbers(pr, need(args.lat, pc, '--lat')), numbers(pr, need(args.lon, pc, '--lon'))) if not coord_issue(a, b)]
    if not targets:
        raise ValueError('Hedef dosyasında geçerli koordinat yok.')
    la, lo = numbers(rows, need(args.lat, columns, '--lat')), numbers(rows, need(args.lon, columns, '--lon'))
    out = []
    for r, a, b in zip(rows, la, lo):
        if coord_issue(a, b, (-90, 90, -180, 180)):
            out.append({**r, 'en_yakin': '', 'mesafe_km': '', 'durum': 'koordinat hatalı'})
            continue
        best = min(targets, key=lambda t: haversine((a, b), t[1]))
        km = haversine((a, b), best[1])
        out.append({**r, 'en_yakin': best[0], 'mesafe_km': round(km, 2), 'durum': 'kapsam dışı' if km > args.max_km else ''})
    return Result(f'{len(rows)} kayıt {len(targets)} geçerli hedefe atandı; {sum(o["durum"] == "kapsam dışı" for o in out)} kayıt {args.max_km:g} km dışında',
                  columns + ['en_yakin', 'mesafe_km', 'durum'], out,
                  {'kayit': len(rows), 'kapsam_disi': sum(o['durum'] == 'kapsam dışı' for o in out), 'kullanilan_hedef': len(targets)},
                  notes=['Mesafe kuş uçuşudur; yol mesafesi ve süre için harita servisi gerekir.'])


def readings(columns, rows, args):
    vals = numbers(rows, need(args.value, columns, '--value'))
    series = defaultdict(list)
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[need(args.time, columns, '--time')])
        if d and v is not None:
            series[r[need(args.id, columns, '--id')].strip()].append((d[0], v))
    for s in series.values():
        s.sort()
    if not series:
        raise ValueError('Zamanı ve değeri okunabilen ölçüm yok.')
    return series


@op('sensor-esik', title='Sensör eşik ihlallerini süreleriyle bul', domain=D, product=P, tag='eşik', samples=SENSOR, args=SENSOR_ARGS + [arg('--max', type=float, default=8.0), arg('--min', type=float),
                                                                                                                      arg('--min-minutes', type=float, default=15.0, help='Bundan kısa ihlalleri sayma')],
    description='Sıcaklık, nem, basınç gibi ölçümlerde alt/üst eşiğin aşıldığı ardışık dönemleri birleştirir; her ihlalin başlangıç, bitiş, süre ve en uç değerini verir. Soğuk zincir ve ekipman izleme raporları için.',
    input='Ölçümler (sensör, zaman, değer) + eşikler', output='sonuc.csv (ihlal olayları)', command='python main.py --max 8 --min 2 --min-minutes 15',
    expect={'ihlal': 1, 'sensor': 2})
def esik(columns, rows, args):
    series = readings(columns, rows, args)
    out = []
    for sid, pts in sorted(series.items()):
        cur = None
        for t, v in pts + [(None, None)]:
            bad = v is not None and ((args.max is not None and v > args.max) or (args.min is not None and v < args.min))
            if bad:
                cur = cur or {'sensor': sid, 'baslangic': t, 'bitis': t, 'uc_deger': v}
                cur['bitis'] = t
                cur['uc_deger'] = max(cur['uc_deger'], v) if args.max is not None and v > args.max else min(cur['uc_deger'], v)
            elif cur:
                minutes = (cur['bitis'] - cur['baslangic']).total_seconds() / 60 + (pts[1][0] - pts[0][0]).total_seconds() / 60 if len(pts) > 1 else 0
                if minutes >= args.min_minutes:
                    out.append({'sensor': sid, 'baslangic': cur['baslangic'].isoformat(sep=' '), 'bitis': cur['bitis'].isoformat(sep=' '), 'sure_dk': round(minutes, 1), 'uc_deger': cur['uc_deger']})
                cur = None
    return Result(f'{len(out)} eşik ihlali ({len(series)} sensör)', ['sensor', 'baslangic', 'bitis', 'sure_dk', 'uc_deger'], out, {'ihlal': len(out), 'sensor': len(series),
                  'toplam_ihlal_dk': round(sum(o['sure_dk'] for o in out), 1)}, notes=['Süreye bir ölçüm aralığı eklenir (son ihlal ölçümünün kapsadığı süre).'])


@op('veri-boslugu', title='Zaman serisinde eksik dönemleri (boşlukları) bul', domain=D, product=P, tag='boşluk', samples=SENSOR, args=SENSOR_ARGS + [arg('--interval', type=float, help='Beklenen aralık (dk); verilmezse en sık aralık')],
    description='Her sensör/kaynak için ardışık kayıtlar arasındaki beklenenden uzun aralıkları (cihaz kapalı, aktarım hatası) listeler; eksik kayıt sayısını ve tamlık yüzdesini hesaplar. Günlük satış veya log serilerinde de kullanılır.',
    input='Zaman damgalı kayıtlar', output='sonuc.csv (boşluklar)', command='python main.py --interval 5',
    expect={'bosluk': 2, 'sensor': 2})
def bosluk(columns, rows, args):
    series = readings(columns, rows, args)
    out, table = [], []
    for sid, pts in sorted(series.items()):
        gaps = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(pts, pts[1:])]
        step = args.interval or max(set(round(g, 3) for g in gaps), key=lambda g: sum(round(x, 3) == g for x in gaps)) if gaps else 0
        missing = 0
        for (a, _), (b, _) in zip(pts, pts[1:]):
            g = (b - a).total_seconds() / 60
            if step and g > step * 1.5:
                lost = round(g / step) - 1
                missing += lost
                out.append({'sensor': sid, 'son_kayit': a.isoformat(sep=' '), 'sonraki_kayit': b.isoformat(sep=' '), 'bosluk_dk': round(g, 1), 'eksik_kayit': lost})
        table.append([sid, len(pts), fmt(step), missing, round(100 * len(pts) / (len(pts) + missing), 2) if pts else 0])
    return Result(f'{len(out)} boşluk, {sum(o["eksik_kayit"] for o in out)} eksik kayıt', ['sensor', 'son_kayit', 'sonraki_kayit', 'bosluk_dk', 'eksik_kayit'], out,
                  {'bosluk': len(out), 'sensor': len(series), 'eksik_kayit': sum(o['eksik_kayit'] for o in out)},
                  tables=[('Tamlık', ['Kaynak', 'Kayıt', 'Aralık (dk)', 'Eksik', 'Tamlık %'], table)])


@op('yeniden-ornekle', title='Zaman serisini yeniden örnekle (15 dk, saatlik, günlük)', domain=D, product=P, tag='resample', samples=SENSOR,
    args=SENSOR_ARGS + [arg('--every', default='60', help='Dakika cinsinden pencere (60 = saatlik, 1440 = günlük)')],
    description='Düzensiz veya sık ölçümleri sabit pencerelere toplar; her pencere için ortalama, en küçük, en büyük, adet ve ilk/son değeri verir, veri olmayan pencereleri boş bırakarak gösterir. Raporlama ve BI yükü için veri hacmini küçültür.',
    input='Zaman damgalı ölçümler', output='sonuc.csv', command='python main.py --every 15',
    expect={'pencere': 48, 'sensor': 2})
def ornekle(columns, rows, args):
    series = readings(columns, rows, args)
    every = float(args.every)
    if every <= 0:
        raise ValueError('--every pozitif olmalı.')
    out = []
    for sid, pts in sorted(series.items()):
        start = pts[0][0].replace(second=0, microsecond=0)
        start = start - timedelta(minutes=(start.hour * 60 + start.minute) % every) if every <= 1440 else start
        buckets = defaultdict(list)
        for t, v in pts:
            k = int((t - start).total_seconds() // (every * 60))
            buckets[k].append(v)
        for k in range(max(buckets) + 1):
            vals = buckets.get(k, [])
            out.append({'sensor': sid, 'pencere': (start + timedelta(minutes=every * k)).isoformat(sep=' '), 'adet': len(vals),
                        'ortalama': round(sum(vals) / len(vals), 4) if vals else '', 'en_kucuk': min(vals) if vals else '', 'en_buyuk': max(vals) if vals else '',
                        'ilk': vals[0] if vals else '', 'son': vals[-1] if vals else ''})
    return Result(f'{len(out)} pencere ({len(series)} kaynak, {every:g} dk)', list(out[0]), out, {'pencere': len(out), 'sensor': len(series),
                  'bos_pencere': sum(o['adet'] == 0 for o in out)})


def business_minutes(a, b, start=time(9), end=time(18), holidays=()):
    total, day = 0.0, a.date()
    while day <= b.date():
        if day.weekday() < 5 and day not in holidays:
            lo = max(a, datetime.combine(day, start))
            hi = min(b, datetime.combine(day, end))
            if hi > lo:
                total += (hi - lo).total_seconds() / 60
        day += timedelta(days=1)
    return total


@op('is-saati-sla', title='İş saatine göre çözüm süresi ve SLA', domain=D, product=P, tag='SLA', samples={'destek_talepleri.csv': 'destek_talepleri.csv'},
    args=[arg('--open', default='acilis'), arg('--close', default='kapanis'), arg('--priority', default='oncelik'), arg('--sla', default='yüksek:8,normal:24', help='Öncelik:iş saati'),
          arg('--hours', default='09:00-18:00'), arg('--as-of', default='2026-09-21 18:00', help='Açık talepler için hesap anı')],
    description='Talep/iş emri açılış ve kapanış zamanları arasında yalnızca iş saatlerini (hafta içi, varsayılan 09:00-18:00) sayar; önceliğe göre SLA hedefini aşanları ve hâlâ açık olup süresi dolanları işaretler.',
    input='Talep listesi (açılış, kapanış, öncelik)', output='sonuc.csv', command='python main.py --sla "kritik:4,yüksek:8,normal:24" --hours 08:30-17:30',
    expect={'talep': 12, 'acik': 1})
def sla(columns, rows, args):
    h1, h2 = (datetime.strptime(x, '%H:%M').time() for x in args.hours.split('-'))
    targets = {k.strip(): float(v) for k, v in (p.split(':') for p in args.sla.split(',') if ':' in p)}
    now = tabular.parse_date(args.as_of)[0] if tabular.parse_date(args.as_of) else datetime.now()
    out = []
    for r in rows:
        a = tabular.parse_date(r[need(args.open, columns, '--open')])
        b = tabular.parse_date(r[need(args.close, columns, '--close')]) if r.get(args.close, '').strip() else None
        if not a:
            out.append({**r, 'is_saati': '', 'hedef_saat': '', 'durum': 'açılış okunamadı'})
            continue
        end = b[0] if b else now
        hrs = business_minutes(a[0], end, h1, h2) / 60
        target = targets.get(r.get(args.priority, '').strip())
        state = ('açık, ' if not b else '') + ('SLA aşıldı' if target is not None and hrs > target else 'SLA içinde' if target is not None else 'hedef tanımsız')
        out.append({**r, 'is_saati': round(hrs, 2), 'hedef_saat': target if target is not None else '', 'durum': state})
    breach = sum('aşıldı' in o['durum'] for o in out)
    return Result(f'{len(rows)} talep; {breach} SLA aşımı, {sum(o["durum"].startswith("açık") for o in out)} açık', columns + ['is_saati', 'hedef_saat', 'durum'], out,
                  {'talep': len(rows), 'sla_asimi': breach, 'acik': sum(o['durum'].startswith('açık') for o in out),
                   'ort_is_saati': round(sum(o['is_saati'] for o in out if o['is_saati'] != '') / max(1, sum(o['is_saati'] != '' for o in out)), 2)},
                  notes=['Resmî tatiller hariç tutulmaz; gerekirse tarih-tablosu bloğundaki tatil listesini kullanın.'])
