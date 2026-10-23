"""Satış ve müşteri analitiği blokları: RFM, kohort, ABC/XYZ, sepet, churn riski, müşteri değeri, huni, oturum, ürün performansı, stok politikası."""
import itertools, json, math, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
import tabular
from islem import op, arg, Result, need, numbers, fmt, tr_money, tr_sort_key

D, P = 'product', 'Satış ve müşteri analitiği'
SIP = {'siparis_satirlari.csv': 'siparis_satirlari.csv'}
WEB = {'web_olaylari.csv': 'web_olaylari.csv'}
ORDER_ARGS = [arg('--order', default='siparis_no'), arg('--date', default='tarih'), arg('--customer', default='musteri_no'), arg('--amount', default='tutar')]


def orders(columns, rows, args):
    """{order: (customer, date, amount)} from order lines."""
    for c in (args.order, args.date, args.customer, args.amount):
        need(c, columns, 'sipariş sütunları')
    amounts = numbers(rows, args.amount)
    out = {}
    for r, a in zip(rows, amounts):
        d = tabular.parse_date(r[args.date])
        if not d:
            continue
        o = out.setdefault(r[args.order].strip(), [r[args.customer].strip(), d[0], 0.0])
        o[2] += a or 0.0
    if not out:
        raise ValueError('Tarihi okunabilen sipariş yok.')
    return out


def quintile(values, reverse=False):
    order = sorted(values, key=lambda kv: kv[1], reverse=reverse)
    return {k: i * 5 // len(order) + 1 for i, (k, _) in enumerate(order)}


def segment(r, f):
    if r >= 4 and f >= 4:
        return 'Şampiyon'
    if f >= 4:
        return 'Sadık'
    if r == 5 and f == 1:
        return 'Yeni'
    if r >= 4:
        return 'Potansiyel sadık'
    if r <= 2 and f >= 3:
        return 'Risk altında'
    if r <= 2:
        return 'Uykuda'
    return 'İlgi bekleyen'


@op('rfm-segment', title='RFM müşteri segmentasyonu', domain=D, product=P, tag='RFM', samples=SIP, args=ORDER_ARGS + [arg('--as-of', help='Rapor tarihi (varsayılan son sipariş + 1 gün)')],
    description='Sipariş satırlarından her müşterinin son alışverişten bu yana geçen günü (R), sipariş sayısını (F) ve toplam harcamasını (M) hesaplar; 1-5 puanlar ve Şampiyon, Sadık, Yeni, Risk altında, Uykuda gibi aksiyon odaklı segmentlere ayırır.',
    input='Sipariş satırları (sipariş no, tarih, müşteri, tutar)', output='sonuc.csv (müşteri bazında) + segment özeti', command='python main.py --as-of 2026-10-01',
    expect={'musteri': 120, 'segment': 6})
def rfm(columns, rows, args):
    o = orders(columns, rows, args)
    as_of = tabular.parse_date(args.as_of)[0] if args.as_of else max(d for _, d, _ in o.values()) + timedelta(days=1)
    cust = defaultdict(lambda: [None, 0, 0.0])
    for c, d, a in o.values():
        x = cust[c]
        x[0] = max(x[0], d) if x[0] else d
        x[1] += 1
        x[2] += a
    rec = {c: (as_of - v[0]).days for c, v in cust.items()}
    rs = quintile(rec.items(), reverse=True)
    fs = quintile([(c, v[1] + v[2] / 1e12) for c, v in cust.items()])
    ms = quintile([(c, v[2]) for c, v in cust.items()])
    out = []
    for c, v in sorted(cust.items()):
        out.append({'musteri': c, 'son_siparis': v[0].date().isoformat(), 'recency_gun': rec[c], 'frequency': v[1], 'monetary': round(v[2], 2),
                    'R': rs[c], 'F': fs[c], 'M': ms[c], 'rfm': f'{rs[c]}{fs[c]}{ms[c]}', 'segment': segment(rs[c], fs[c])})
    seg = Counter(o_['segment'] for o_ in out)
    money = defaultdict(float)
    for x in out:
        money[x['segment']] += x['monetary']
    total = sum(money.values())
    return Result(f'{len(out)} müşteri {len(seg)} segmentte ({as_of.date().isoformat()} itibarıyla)', list(out[0]), out, {'musteri': len(out), 'segment': len(seg)},
                  tables=[('Segmentler', ['Segment', 'Müşteri', 'Ciro payı %'], [[s, n, round(100 * money[s] / total, 1)] for s, n in seg.most_common()])])


@op('kohort-tutundurma', title='Kohort bazında müşteri tutundurma matrisi', domain=D, product=P, tag='kohort', samples=SIP, args=ORDER_ARGS,
    description='Müşterileri ilk alışveriş ayına göre kohortlara ayırır; sonraki her ayda kohortun yüzde kaçının tekrar alışveriş yaptığını matris olarak çıkarır. Sadakat programı ve ürün değişikliklerinin etkisini izlemek için.',
    input='Sipariş satırları', output='sonuc.csv (kohort × ay yüzdeleri)', command='python main.py',
    expect={'kohort': 11})
def kohort(columns, rows, args):
    o = orders(columns, rows, args)
    first = {}
    active = defaultdict(set)
    for c, d, _ in o.values():
        m = d.year * 12 + d.month - 1
        first[c] = min(first.get(c, m), m)
        active[c].add(m)
    cohorts = defaultdict(list)
    for c, m in first.items():
        cohorts[m].append(c)
    last = max(m for s in active.values() for m in s)
    width = last - min(cohorts) + 1
    out = []
    for m in sorted(cohorts):
        members = cohorts[m]
        row = {'kohort': f'{m // 12}-{m % 12 + 1:02d}', 'musteri': len(members)}
        for k in range(width):
            if m + k > last:
                row[f'ay_{k}'] = ''
            else:
                row[f'ay_{k}'] = round(100 * sum(1 for c in members if m + k in active[c]) / len(members), 1)
        out.append(row)
    avg1 = [r['ay_1'] for r in out if r.get('ay_1') not in ('', None)]
    return Result(f'{len(out)} kohort; ortalama 1. ay tutundurma %{sum(avg1) / len(avg1):.1f}' if avg1 else f'{len(out)} kohort', list(out[0]), out,
                  {'kohort': len(out), 'ilk_ay_tutundurma_%': round(sum(avg1) / len(avg1), 1) if avg1 else None})


@op('abc-analizi', title='ABC analizi (ciro/hacim payına göre)', domain=D, product=P, tag='ABC', samples=SIP,
    args=[arg('--item', default='urun_adi'), arg('--value', default='tutar'), arg('--a', type=float, default=80.0), arg('--b', type=float, default=95.0)],
    description='Ürünleri (veya müşterileri) ciroya katkılarına göre sıralar; kümülatif payı ilk %80’e giren A, %95’e kadar B, kalan C sınıfına atar. Stok, fiyat ve satış ekibi önceliklendirmesi için.',
    input='Satır verisi + kalem ve değer sütunu', output='sonuc.csv (kalem bazında sınıf)', command='python main.py --item urun_kodu --value tutar',
    expect={'A': 5, 'B': 3, 'C': 1})
def abc(columns, rows, args):
    vals = numbers(rows, need(args.value, columns, '--value'))
    totals = defaultdict(float)
    for r, v in zip(rows, vals):
        totals[r[need(args.item, columns, '--item')].strip()] += v or 0.0
    grand = sum(totals.values())
    out, cum = [], 0.0
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        prev = cum
        cum += v
        cls = 'A' if prev < args.a / 100 * grand else 'B' if prev < args.b / 100 * grand else 'C'
        out.append({args.item: k, 'deger': round(v, 2), 'pay_%': round(100 * v / grand, 2), 'kumulatif_%': round(100 * cum / grand, 2), 'sinif': cls})
    c = Counter(x['sinif'] for x in out)
    return Result(f"A {c['A']}, B {c['B']}, C {c['C']} kalem", list(out[0]), out, {'A': c['A'], 'B': c['B'], 'C': c['C']},
                  notes=['Sınıf, kalem eklenmeden önceki kümülatif paya göre atanır; böylece eşiği aşan kalem üst sınıfta kalır.'])


@op('sepet-analizi', title='Sepet analizi: birlikte alınan ürünler', domain=D, product=P, tag='sepet', samples=SIP,
    args=[arg('--order', default='siparis_no'), arg('--item', default='urun_adi'), arg('--min-support', type=float, default=1.0, help='En az sipariş yüzdesi'),
          arg('--min-lift', type=float, default=1.2)],
    description='Aynı siparişte birlikte alınan ürün çiftlerini bulur; destek (siparişlerin yüzde kaçı), güven (A alanların yüzde kaçı B de aldı) ve lift (tesadüfe göre kaç kat) değerlerini hesaplar. Çapraz satış ve raf yerleşimi için.',
    input='Sipariş satırları (sipariş no, ürün)', output='sonuc.csv (kural listesi)', command='python main.py --min-support 1 --min-lift 1.2',
    expect={'en_guclu_kural': 'Laptop → Mouse'})
def sepet(columns, rows, args):
    baskets = defaultdict(set)
    for r in rows:
        baskets[r[need(args.order, columns, '--order')].strip()].add(r[need(args.item, columns, '--item')].strip())
    n = len(baskets)
    single = Counter(i for b in baskets.values() for i in b)
    pair = Counter(p for b in baskets.values() for p in itertools.combinations(sorted(b), 2))
    out = []
    for (a, b), k in pair.items():
        sup = k / n
        if 100 * sup < args.min_support:
            continue
        for x, y in ((a, b), (b, a)):
            conf = k / single[x]
            lift = conf / (single[y] / n)
            if lift >= args.min_lift:
                out.append({'kural': f'{x} → {y}', 'birlikte_siparis': k, 'destek_%': round(100 * sup, 2), 'guven_%': round(100 * conf, 1), 'lift': round(lift, 2)})
    out.sort(key=lambda o: (-o['lift'], -o['birlikte_siparis']))
    if not out:
        return Result('Eşikleri geçen kural yok', ['kural', 'birlikte_siparis', 'destek_%', 'guven_%', 'lift'], [], {'kural': 0, 'en_guclu_kural': ''})
    strongest = max(out, key=lambda o: o['guven_%'] * o['lift'])
    return Result(f"{n} sipariş, {len(out)} kural; en güçlü: {strongest['kural']} (güven %{strongest['guven_%']}, lift {strongest['lift']})", list(out[0]), out,
                  {'siparis': n, 'kural': len(out), 'en_guclu_kural': strongest['kural']},
                  notes=['Lift > 1 birlikte alımın tesadüften sık olduğunu gösterir; az siparişli kurallarda dikkatli olun.'])


@op('churn-riski', title='Müşteri kaybı (churn) riski', domain=D, product=P, tag='churn', samples=SIP, args=ORDER_ARGS + [arg('--as-of'), arg('--factor', type=float, default=2.0), arg('--min-days', type=int, default=60)],
    description='Her müşterinin kendi alışveriş aralığına göre beklenenden uzun süredir alışveriş yapmadığını tespit eder (son alışverişten bu yana geçen gün > ortalama aralık × katsayı); kayıp riskindeki ciroyu ve kazanım listesini verir.',
    input='Sipariş satırları', output='sonuc.csv (riskteki müşteriler önce)', command='python main.py --factor 2 --min-days 60',
    expect={'musteri': 120})
def churn(columns, rows, args):
    o = orders(columns, rows, args)
    as_of = tabular.parse_date(args.as_of)[0] if args.as_of else max(d for _, d, _ in o.values()) + timedelta(days=1)
    by = defaultdict(list)
    for c, d, a in o.values():
        by[c].append((d, a))
    out = []
    for c, items in by.items():
        items.sort()
        days = [(b[0] - a[0]).days for a, b in zip(items, items[1:]) if (b[0] - a[0]).days > 0]
        gap = sum(days) / len(days) if days else None
        since = (as_of - items[-1][0]).days
        limit = max(args.min_days, gap * args.factor) if gap else args.min_days * 1.5
        risk = 'yüksek' if since > limit else 'orta' if since > limit * 0.6 else 'düşük'
        out.append({'musteri': c, 'siparis': len(items), 'ort_aralik_gun': round(gap, 1) if gap else '', 'son_siparisten_gun': since, 'risk': risk,
                    'yillik_ciro': round(sum(a for _, a in items) * 365 / max(30, (as_of - items[0][0]).days), 2)})
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2}
    out.sort(key=lambda x: (order[x['risk']], -x['yillik_ciro']))
    risk = Counter(x['risk'] for x in out)
    at_risk = sum(x['yillik_ciro'] for x in out if x['risk'] == 'yüksek')
    return Result(f"{risk['yüksek']} yüksek, {risk['orta']} orta riskli müşteri; riskteki yıllık ciro {tr_money(at_risk)}", list(out[0]), out,
                  {'musteri': len(out), 'yuksek_risk': risk['yüksek'], 'orta_risk': risk['orta'], 'riskteki_yillik_ciro': round(at_risk, 2)},
                  notes=['Tek siparişli müşterilerde aralık bilinmediğinden sabit eşik (min-days × 1,5) kullanılır.'])


@op('musteri-degeri', title='Müşteri yaşam boyu değeri (tarihsel CLV)', domain=D, product=P, tag='CLV', samples=SIP, args=ORDER_ARGS + [arg('--margin', type=float, default=100.0, help='Brüt kâr marjı %%')],
    description='Her müşterinin ortalama sepet tutarını, yıllık sipariş sıklığını, müşteri ömrünü ve marjla düzeltilmiş tarihsel değerini hesaplar; en değerli %20’nin ciro payını gösterir. Basit, açıklanabilir bir CLV başlangıcıdır.',
    input='Sipariş satırları + isteğe bağlı marj', output='sonuc.csv (müşteri bazında)', command='python main.py --margin 25',
    expect={'musteri': 120})
def clv(columns, rows, args):
    o = orders(columns, rows, args)
    end = max(d for _, d, _ in o.values())
    by = defaultdict(list)
    for c, d, a in o.values():
        by[c].append((d, a))
    out = []
    for c, items in by.items():
        items.sort()
        total = sum(a for _, a in items)
        tenure = max(30, (end - items[0][0]).days + 1)
        out.append({'musteri': c, 'siparis': len(items), 'ortalama_sepet': round(total / len(items), 2), 'yillik_siklik': round(len(items) * 365 / tenure, 2),
                    'omur_gun': tenure, 'toplam_ciro': round(total, 2), 'tarihsel_deger': round(total * args.margin / 100, 2),
                    'yillik_deger': round(total / len(items) * len(items) * 365 / tenure * args.margin / 100, 2)})
    out.sort(key=lambda x: -x['toplam_ciro'])
    top = out[:max(1, len(out) // 5)]
    share = 100 * sum(x['toplam_ciro'] for x in top) / sum(x['toplam_ciro'] for x in out)
    return Result(f'{len(out)} müşteri; en değerli %20 cironun %{share:.1f}’ini getiriyor', list(out[0]), out,
                  {'musteri': len(out), 'ilk_yuzde20_pay': round(share, 1), 'ortalama_sepet': round(sum(a for _, _, a in o.values()) / len(o), 2)})


@op('donusum-hunisi', title='Dönüşüm hunisi', domain=D, product=P, tag='huni', samples=WEB,
    args=[arg('--user', default='kullanici'), arg('--event', default='olay'), arg('--time', default='zaman'),
          arg('--steps', default='ziyaret,urun_goruntuleme,sepete_ekleme,odeme_baslatma,satin_alma'), arg('--by', help='Kırılım sütunu, örn. cihaz')],
    description='Olay kayıtlarından kullanıcıların huni adımlarını sırasıyla tamamlama oranlarını hesaplar (bir adım ancak önceki adımdan sonra gelirse sayılır); adım adım ve toplam dönüşümü, en çok kayıp verilen adımı ve isteğe bağlı kırılımı verir.',
    input='Olay kayıtları (kullanıcı, zaman, olay)', output='sonuc.csv (adım tablosu)', command='python main.py --steps "ziyaret,sepete_ekleme,satin_alma" --by cihaz',
    expect={'adim': 5, 'baslayan': 200})
def huni(columns, rows, args):
    steps = [s.strip() for s in args.steps.split(',') if s.strip()]
    u, e, t = need(args.user, columns, '--user'), need(args.event, columns, '--event'), need(args.time, columns, '--time')
    events = defaultdict(list)
    seg = {}
    for r in rows:
        d = tabular.parse_date(r[t])
        events[r[u]].append((d[0] if d else datetime.min, r[e].strip()))
        if args.by:
            seg.setdefault(r[u], r[need(args.by, columns, '--by')].strip())
    reached = defaultdict(Counter)
    for user, evs in events.items():
        evs.sort()
        k = 0
        for _, name in evs:
            if k < len(steps) and name == steps[k]:
                k += 1
        for i in range(k):
            reached[seg.get(user, 'tümü')][i] += 1
            if args.by:
                reached['tümü'][i] += 1
    out = []
    for g in sorted(reached, key=lambda x: (x != 'tümü', tr_sort_key(x))):
        base = reached[g][0] or 1
        for i, s in enumerate(steps):
            n = reached[g][i]
            prev = reached[g][i - 1] if i else n
            out.append({'grup': g, 'adim': i + 1, 'olay': s, 'kullanici': n, 'adim_donusumu_%': round(100 * n / prev, 1) if prev else 0,
                        'toplam_donusum_%': round(100 * n / base, 1)})
    main = [o for o in out if o['grup'] == 'tümü']
    worst = min(main[1:], key=lambda o: o['adim_donusumu_%'])
    return Result(f"{main[0]['kullanici']} kullanıcı → {main[-1]['kullanici']} {steps[-1]} (%{main[-1]['toplam_donusum_%']}); en büyük kayıp: {worst['olay']} adımı (%{worst['adim_donusumu_%']})",
                  list(out[0]), out, {'adim': len(steps), 'baslayan': main[0]['kullanici'], 'tamamlayan': main[-1]['kullanici'], 'en_zayif_adim': worst['olay']})


@op('oturumlar', title='Olayları oturumlara böl (sessionize)', domain=D, product=P, tag='oturum', samples=WEB,
    args=[arg('--user', default='kullanici'), arg('--time', default='zaman'), arg('--gap', type=int, default=30, help='Dakika cinsinden oturum zaman aşımı')],
    description='Kullanıcı olaylarını aralarındaki boşluk belirli bir süreyi (varsayılan 30 dk) aşınca yeni oturum sayacak şekilde gruplar; her olaya oturum numarası ekler, oturum süresi ve olay sayısı dağılımını verir. GA4 dışı log ve uygulama verisi için.',
    input='Olay kayıtları (kullanıcı, zaman)', output='sonuc.csv (oturum no eklenmiş) + oturumlar.csv', command='python main.py --gap 30',
    expect={'kullanici': 200})
def oturum(columns, rows, args):
    u, t = need(args.user, columns, '--user'), need(args.time, columns, '--time')
    idx = sorted(range(len(rows)), key=lambda i: (rows[i][u], tabular.parse_date(rows[i][t])[0] if tabular.parse_date(rows[i][t]) else datetime.min))
    out, sessions = [], []
    last_user, last_time, sid = None, None, 0
    for i in idx:
        r = rows[i]
        d = tabular.parse_date(r[t])
        when = d[0] if d else None
        if r[u] != last_user or when is None or last_time is None or (when - last_time).total_seconds() > args.gap * 60:
            sid += 1
            sessions.append({'oturum': sid, 'kullanici': r[u], 'baslangic': when, 'bitis': when, 'olay': 0})
        s = sessions[-1]
        s['bitis'], s['olay'] = when or s['bitis'], s['olay'] + 1
        out.append({**r, 'oturum': sid})
        last_user, last_time = r[u], when
    srows = [{'oturum': s['oturum'], 'kullanici': s['kullanici'], 'baslangic': s['baslangic'].isoformat(sep=' ') if s['baslangic'] else '',
              'sure_dk': round((s['bitis'] - s['baslangic']).total_seconds() / 60, 1) if s['baslangic'] else '', 'olay': s['olay']} for s in sessions]
    users = len({s['kullanici'] for s in sessions})
    avg = sum(s['sure_dk'] or 0 for s in srows) / len(srows)
    return Result(f'{len(srows)} oturum, {users} kullanıcı; ortalama süre {avg:.1f} dk', columns + ['oturum'], out,
                  {'oturum': len(srows), 'kullanici': users, 'ortalama_sure_dk': round(avg, 1), 'oturum_basina_olay': round(len(rows) / len(srows), 2)},
                  files={'oturumlar.csv': (['oturum', 'kullanici', 'baslangic', 'sure_dk', 'olay'], srows)})


def monthly(columns, rows, item, value, date_col):
    vals = numbers(rows, need(value, columns, '--value'))
    grid = defaultdict(lambda: defaultdict(float))
    months = set()
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[need(date_col, columns, '--date')])
        if d:
            m = d[0].strftime('%Y-%m')
            months.add(m)
            grid[r[need(item, columns, '--item')].strip()][m] += v or 0.0
    return grid, sorted(months)


@op('xyz-analizi', title='XYZ talep değişkenliği analizi', domain=D, product=P, tag='XYZ', samples=SIP,
    args=[arg('--item', default='urun_adi'), arg('--value', default='adet'), arg('--date', default='tarih'), arg('--x', type=float, default=0.5), arg('--y', type=float, default=1.0)],
    description='Her ürünün aylık talebinin değişim katsayısını (std/ortalama) hesaplar: düzenli (X), dalgalı (Y), düzensiz (Z). ABC ile birlikte kullanıldığında stok ve tahmin politikasını belirler (AX: otomatik, CZ: siparişe göre).',
    input='Satış satırları (tarih, ürün, miktar)', output='sonuc.csv (ürün bazında sınıf)', command='python main.py --value adet',
    expect={'urun': 9})
def xyz(columns, rows, args):
    grid, months = monthly(columns, rows, args.item, args.value, args.date)
    out = []
    for item, series in grid.items():
        vals = [series.get(m, 0.0) for m in months]
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
        cv = sd / mean if mean else float('inf')
        out.append({args.item: item, 'ay': len(vals), 'ortalama_talep': round(mean, 2), 'std': round(sd, 2), 'cv': round(cv, 3),
                    'sinif': 'X' if cv <= args.x else 'Y' if cv <= args.y else 'Z'})
    out.sort(key=lambda o: o['cv'])
    c = Counter(o['sinif'] for o in out)
    return Result(f"X {c['X']}, Y {c['Y']}, Z {c['Z']} ürün ({len(months)} ay)", list(out[0]), out, {'urun': len(out), 'X': c['X'], 'Y': c['Y'], 'Z': c['Z']})


@op('urun-performansi', title='Ürün performansı: son dönem ve önceki dönem', domain=D, product=P, tag='ürün performansı', samples=SIP,
    args=[arg('--item', default='urun_adi'), arg('--value', default='tutar'), arg('--date', default='tarih'), arg('--months', type=int, default=3), arg('--drop', type=float, default=-20.0)],
    description='Son N ayı önceki N ayla ürün bazında karşılaştırır: ciro, büyüme %, pay ve pay değişimi; belirgin düşen, yükselen ve yeni/kaybolan ürünleri işaretler. Aylık ürün toplantısı için.',
    input='Satış satırları (tarih, ürün, tutar)', output='sonuc.csv (ürün bazında)', command='python main.py --months 3 --drop -20',
    expect={'urun': 9})
def urun(columns, rows, args):
    grid, months = monthly(columns, rows, args.item, args.value, args.date)
    if len(months) < 2 * args.months:
        raise ValueError(f'En az {2 * args.months} ay veri gerekir; dosyada {len(months)} ay var.')
    cur, prev = months[-args.months:], months[-2 * args.months:-args.months]
    tc = sum(sum(s.get(m, 0) for m in cur) for s in grid.values())
    tp = sum(sum(s.get(m, 0) for m in prev) for s in grid.values())
    out = []
    for item, s in grid.items():
        a, b = sum(s.get(m, 0) for m in cur), sum(s.get(m, 0) for m in prev)
        g = 100 * (a - b) / b if b else None
        flag = 'yeni' if not b and a else 'kayboldu' if b and not a else 'düşüşte' if g is not None and g <= args.drop else 'yükselişte' if g is not None and g >= -args.drop else ''
        out.append({args.item: item, 'son_donem': round(a, 2), 'onceki_donem': round(b, 2), 'buyume_%': round(g, 1) if g is not None else '',
                    'pay_%': round(100 * a / tc, 2) if tc else 0, 'pay_degisimi_puan': round(100 * a / tc - 100 * b / tp, 2) if tc and tp else '', 'durum': flag})
    out.sort(key=lambda o: -o['son_donem'])
    c = Counter(o['durum'] for o in out)
    return Result(f"{cur[0]}…{cur[-1]} vs {prev[0]}…{prev[-1]}: toplam %{100 * (tc - tp) / tp:+.1f}; {c['düşüşte']} ürün düşüşte, {c['yükselişte']} yükselişte", list(out[0]), out,
                  {'urun': len(out), 'dususte': c['düşüşte'], 'yukseliste': c['yükselişte'], 'toplam_buyume_%': round(100 * (tc - tp) / tp, 1) if tp else None})


@op('donem-karsilastir', title='Dönem karşılaştırması (aylık, çeyreklik, yıllık)', domain=D, product=P, tag='MoM/YoY', samples={'gunluk_satis.csv': 'gunluk_satis.csv'},
    args=[arg('--date', default='tarih'), arg('--value', default='ciro'), arg('--by', default='bolge'), arg('--period', choices=['ay', 'ceyrek', 'yil'], default='ay')],
    description='Seçilen ölçüyü dönemlere ve kırılıma göre toplar; her dönem için bir önceki döneme göre değişimi (MoM/QoQ) ve veri yetiyorsa geçen yılın aynı dönemine göre değişimi (YoY) hesaplar. Yönetim raporlarındaki tekrar eden tabloyu üretir.',
    input='Tarihli işlem verisi + ölçü + kırılım', output='sonuc.csv (dönem × kırılım)', command='python main.py --period ceyrek --by kanal',
    expect={'donem': 3, 'kirilim': 4})
def donem(columns, rows, args):
    vals = numbers(rows, need(args.value, columns, '--value'))
    keyf = {'ay': lambda d: d.strftime('%Y-%m'), 'ceyrek': lambda d: f'{d.year}-Ç{(d.month - 1) // 3 + 1}', 'yil': lambda d: str(d.year)}[args.period]
    lag = {'ay': 12, 'ceyrek': 4, 'yil': 1}[args.period]
    grid = defaultdict(lambda: defaultdict(float))
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[need(args.date, columns, '--date')])
        if d:
            g = r[need(args.by, columns, '--by')].strip() if args.by else 'toplam'
            grid[g][keyf(d[0])] += v or 0.0
            if args.by:
                grid['TOPLAM'][keyf(d[0])] += v or 0.0
    periods = sorted({p for s in grid.values() for p in s})
    out = []
    for g in sorted(grid, key=lambda x: (x == 'TOPLAM', tr_sort_key(x))):
        for i, p in enumerate(periods):
            v = grid[g].get(p, 0.0)
            prev = grid[g].get(periods[i - 1]) if i else None
            yoy = grid[g].get(periods[i - lag]) if i >= lag else None
            out.append({'kirilim': g, 'donem': p, 'deger': round(v, 2), 'onceki_doneme_gore_%': round(100 * (v - prev) / prev, 1) if prev else '',
                        'gecen_yila_gore_%': round(100 * (v - yoy) / yoy, 1) if yoy else ''})
    return Result(f"{len(periods)} dönem × {len(grid) - (1 if args.by else 0)} kırılım", list(out[0]), out, {'donem': len(periods), 'kirilim': len(grid) - (1 if args.by else 0)},
                  notes=['Geçen yıl karşılaştırması için verinin bir yıl öncesini de içermesi gerekir.'])


@op('yeniden-siparis-noktasi', title='Emniyet stoku ve yeniden sipariş noktası', domain=D, product=P, tag='stok politikası', samples=SIP,
    args=[arg('--item', default='urun_kodu'), arg('--qty', default='adet'), arg('--date', default='tarih'), arg('--lead-time', type=float, default=7.0, help='Tedarik süresi (gün)'),
          arg('--service', type=float, default=95.0, help='Hedef hizmet düzeyi %%'), arg('--days', type=int, default=90, help='Son kaç günün talebi kullanılsın')],
    description='Son dönemdeki günlük satışlardan (satış olmayan günler sıfır sayılarak) ürün başına ortalama ve standart sapmayı bulur; tedarik süresi ve hedef hizmet düzeyine göre emniyet stoku ve yeniden sipariş noktasını hesaplar.',
    input='Satış satırları (tarih, ürün, miktar) + tedarik süresi', output='sonuc.csv (ürün bazında stok politikası)', command='python main.py --lead-time 10 --service 97.5',
    expect={'urun': 9})
def rop(columns, rows, args):
    qty = numbers(rows, need(args.qty, columns, '--qty'))
    dated = [(tabular.parse_date(r[need(args.date, columns, '--date')]), r[need(args.item, columns, '--item')].strip(), q) for r, q in zip(rows, qty)]
    dated = [(d[0].date(), i, q or 0.0) for d, i, q in dated if d]
    end = max(d for d, _, _ in dated)
    start = end - timedelta(days=args.days - 1)
    daily = defaultdict(lambda: defaultdict(float))
    for d, i, q in dated:
        if d >= start:
            daily[i][d] += q
    z = NormalDist().inv_cdf(args.service / 100)
    out = []
    for item in sorted({i for _, i, _ in dated}):
        series = [daily[item].get(start + timedelta(days=k), 0.0) for k in range(args.days)]
        mean = sum(series) / len(series)
        sd = math.sqrt(sum((x - mean) ** 2 for x in series) / (len(series) - 1))
        safety = z * sd * math.sqrt(args.lead_time)
        out.append({'urun': item, 'gunluk_ort': round(mean, 3), 'gunluk_std': round(sd, 3), 'tedarik_suresi_talebi': round(mean * args.lead_time, 2),
                    'emniyet_stoku': math.ceil(safety), 'yeniden_siparis_noktasi': math.ceil(mean * args.lead_time + safety)})
    return Result(f'{len(out)} ürün için politika (hizmet düzeyi %{args.service:g}, tedarik {args.lead_time:g} gün, z={z:.2f})', list(out[0]), out, {'urun': len(out), 'z': round(z, 3)},
                  notes=['Talep dağılımının normal ve tedarik süresinin sabit olduğu varsayılır; kampanya dönemlerini ayrıca değerlendirin.'])


@op('ozellik-benimseme', title='Ürün özelliklerinin benimsenme oranı ve tekrar kullanımı', domain=D, product=P, tag='özellik benimseme',
    samples={'kullanim_olaylari.csv': 'kullanim_olaylari.csv'},
    args=[arg('--user', default='kullanici'), arg('--date', default='tarih'), arg('--feature', default='ozellik'),
          arg('--ignore', default='giris', help='Özellik sayılmayan olaylar (giriş, sayfa görüntüleme…), virgülle'), arg('--low', type=float, default=25, help='Bu yüzdenin altı düşük benimseme')],
    description='Kullanıcı-olay dökümünden her özelliği kullanan kullanıcı oranını (tüm aktif kullanıcılara göre), son haftadaki benimsemeyi ve en az iki farklı haftada kullananların payını (tekrar kullanım) hesaplar; düşük benimsenen ve bir kez denenip bırakılan özellikleri işaretler. Yol haritası ve özellik kaldırma kararları için.',
    input='Kullanım olayları (kullanıcı, tarih, özellik/olay adı)', output='sonuc.csv (özellik bazında benimseme)', command='python main.py --input events.csv --user user_id --date ts --feature event_name --ignore login,page_view',
    expect={'aktif_kullanici': 10, 'ozellik': 3, 'son_hafta_aktif': 8, 'en_cok_benimsenen': 'rapor', 'dusuk_benimseme': 1})
def adoption(columns, rows, args):
    uc, dc, fc = need(args.user, columns, '--user'), need(args.date, columns, '--date'), need(args.feature, columns, '--feature')
    ignore = {x.strip().lower() for x in args.ignore.split(',') if x.strip()}
    events = []
    for r in rows:
        d = tabular.parse_date(r[dc])
        if d and r[uc].strip():
            events.append((r[uc].strip(), d[0], r[fc].strip()))
    if not events:
        raise ValueError('Tarihi okunabilen olay yok.')
    week = lambda d: d.isocalendar()[:2]
    last = max(week(d) for _, d, _ in events)
    active = {u for u, _, _ in events}
    last_active = {u for u, d, _ in events if week(d) == last}
    users, weeks, last_users = defaultdict(set), defaultdict(lambda: defaultdict(set)), defaultdict(set)
    for u, d, f in events:
        if f.lower() in ignore or not f:
            continue
        users[f].add(u)
        weeks[f][u].add(week(d))
        if week(d) == last:
            last_users[f].add(u)
    out = []
    for f, us in users.items():
        repeat = sum(len(w) >= 2 for w in weeks[f].values())
        rate = round(100 * len(us) / len(active), 1)
        out.append({'ozellik': f, 'kullanici': len(us), 'benimseme_%': rate, 'son_hafta_%': round(100 * len(last_users[f]) / len(last_active), 1) if last_active else 0,
                    'tekrar_kullanan_%': round(100 * repeat / len(us), 1),
                    'durum': 'düşük benimseme' if rate < args.low else 'denenip bırakılıyor' if repeat / len(us) < 0.3 else ''})
    out.sort(key=lambda o: -o['benimseme_%'])
    return Result(f'{len(active)} aktif kullanıcı, {len(out)} özellik; en çok benimsenen {out[0]["ozellik"]} (%{fmt(out[0]["benimseme_%"])})', list(out[0]), out,
                  {'aktif_kullanici': len(active), 'ozellik': len(out), 'son_hafta_aktif': len(last_active), 'en_cok_benimsenen': out[0]['ozellik'],
                   'dusuk_benimseme': sum(o['durum'] == 'düşük benimseme' for o in out)},
                  notes=['Hafta ISO haftasıdır. Benimseme oranı dönem içinde en az bir olayı olan tüm kullanıcılara göredir; özelliğe erişimi olmayan kullanıcı grubu varsa paydayı daraltın.'])


@op('nps-analizi', title='NPS: tavsiye eden / kötüleyen oranı, segment kırılımı ve hata payı', domain=D, product=P, tag='NPS', samples={'nps_anketi.csv': 'nps_anketi.csv'},
    args=[arg('--score', default='puan'), arg('--segment', default='segment', help='Kırılım (boş bırakılabilir)')],
    description='0–10 tavsiye puanlarından Net Promoter Score’u (tavsiye eden 9–10 yüzdesi − kötüleyen 0–6 yüzdesi) hesaplar; segment bazında NPS, yanıt sayısı ve %95 hata payını verir, aralık dışı puanları ayıklar. İki segment arasındaki farkın hata payları içinde kalıp kalmadığını görmek için.',
    input='Anket yanıtları (puan 0–10; isteğe bağlı segment)', output='sonuc.csv (segment bazında NPS)', command='python main.py --input nps_q3.xlsx --score skor --segment kanal',
    expect={'yanit': 30, 'nps': 16.67, 'tesvikci_%': 43.33, 'kotuleyen_%': 26.67, 'hata_payi': 29.34, 'gecersiz': 1})
def nps(columns, rows, args):
    scores = numbers(rows, need(args.score, columns, '--score'))
    seg = need(args.segment, columns, '--segment') if args.segment else None
    groups, bad = defaultdict(list), 0
    for r, s in zip(rows, scores):
        if s is None or not 0 <= s <= 10 or s != int(s):
            bad += 1
            continue
        groups['(tümü)'].append(s)
        if seg:
            groups[r[seg].strip() or '(boş)'].append(s)
    if not groups:
        raise ValueError('0–10 arası geçerli puan yok.')

    def stats(v):
        n = len(v)
        p, d = sum(x >= 9 for x in v) / n, sum(x <= 6 for x in v) / n
        moe = 1.96 * math.sqrt(max(0.0, p + d - (p - d) ** 2) / n) * 100
        return {'yanit': n, 'tesvikci_%': round(100 * p, 2), 'pasif_%': round(100 * (1 - p - d), 2), 'kotuleyen_%': round(100 * d, 2), 'nps': round(100 * (p - d), 2), 'hata_payi': round(moe, 2)}
    out = [{'segment': g, **stats(v)} for g, v in groups.items()]
    out.sort(key=lambda o: (o['segment'] != '(tümü)', -o['nps']))
    t = out[0]
    return Result(f'NPS {fmt(t["nps"])} (±{fmt(t["hata_payi"])}), {t["yanit"]} yanıt', ['segment', 'yanit', 'nps', 'hata_payi', 'tesvikci_%', 'pasif_%', 'kotuleyen_%'], out,
                  {'yanit': t['yanit'], 'nps': t['nps'], 'tesvikci_%': t['tesvikci_%'], 'kotuleyen_%': t['kotuleyen_%'], 'hata_payi': t['hata_payi'], 'gecersiz': bad},
                  notes=['Hata payı %95 güvenle NPS’in ± kaç puan oynayabileceğidir; iki segmentin farkı hata paylarından küçükse fark kanıtlanmış sayılmaz. Az yanıtlı segmentleri yorumlarken dikkatli olun.'])


GA4_RESERVED = ('ga_', 'google_', 'firebase_')
PII_URL = re.compile(r'[?&][^=&]*=([^&]*%40|[^&]*@)[^&]*|[?&](email|eposta|e-posta|telefon|phone|tckn)=', re.I)


@op('ga4-export-denetimi', title='GA4 BigQuery dışa aktarım denetimi: boş günler, eksik oturum ve satın alma alanları, URL’de kişisel veri', domain='product', product='GA4', tag='GA4 export',
    samples={'ga4_olaylar.csv': 'ga4_olaylar.csv'},
    args=[arg('--drop', type=float, default=50.0, help='Günlük olay sayısı medyanın bu yüzdesinin altına düşerse eksik gün sayılır')],
    description='GA4’ün BigQuery dışa aktarımından düzleştirilmiş olay satırlarını (event_date, event_name, user_pseudo_id, ga_session_id, page_location, transaction_id, value, currency) denetler: hiç veri gelmeyen veya olay sayısı medyanın altına düşen günler, oturum kimliği eksik olaylar, transaction_id’si boş veya tekrar eden purchase olayları, değeri ya da para birimi olmayan satın almalar, GA4 adlandırma kurallarını çiğneyen olay adları (40 karakter, harf ile başlama, ayrılmış önekler) ve page_location’da e-posta/telefon gibi kişisel veri (Google politikası gereği gönderilmemeli). Denetim GA4 arayüzü yerine ham veri üzerinde yapılır.',
    input='GA4 olay dışa aktarımı (düzleştirilmiş CSV)', output='sonuc.csv (bulgular) + günlük olay sayısı', command='python main.py --input ga4_events_20261001_20261014.csv',
    expect={'olay': 120, 'gun': 14, 'eksik_gun': 2, 'bulgu': 9})
def ga4_audit(columns, rows, args):
    for c in ('event_date', 'event_name', 'user_pseudo_id'):
        need(c, columns, 'GA4 dışa aktarımı')
    g = lambda r, c: r.get(c, '').strip()
    days = Counter()
    for r in rows:
        d = g(r, 'event_date').replace('-', '')
        days[d] += 1
    parsed = sorted(datetime.strptime(d, '%Y%m%d') for d in days if re.fullmatch(r'\d{8}', d))
    span = [parsed[0] + timedelta(i) for i in range((parsed[-1] - parsed[0]).days + 1)] if parsed else []
    counts = [days.get(d.strftime('%Y%m%d'), 0) for d in span]
    nonzero = sorted(c for c in counts if c)
    median = nonzero[len(nonzero) // 2] if nonzero else 0
    low = [(d, c) for d, c in zip(span, counts) if c < median * args.drop / 100]
    out = []
    add = lambda sev, check, detail, n: out.append({'onem': sev, 'kontrol': check, 'ayrinti': detail, 'etkilenen': n})
    for d, c in low:
        add('yüksek' if c == 0 else 'orta', 'eksik gün', f'{d:%Y-%m-%d}: {c} olay (medyan {median}); dışa aktarım veya etiket kesintisi', c)
    if 'ga_session_id' in columns:
        miss = sum(1 for r in rows if not g(r, 'ga_session_id') and g(r, 'event_name') not in ('first_visit',))
        if miss:
            add('orta', 'oturum kimliği yok', f'{miss} olayda ga_session_id boş: oturum ve kaynak/ortam raporları eksik kalır', miss)
    purchases = [r for r in rows if g(r, 'event_name') == 'purchase']
    if purchases and 'transaction_id' in columns:
        no_id = sum(1 for r in purchases if not g(r, 'transaction_id') or g(r, 'transaction_id') == '(not set)')
        if no_id:
            add('yüksek', 'transaction_id yok', f'{no_id}/{len(purchases)} satın almada transaction_id boş: tekilleştirilemez, gelir şişebilir', no_id)
        dup = Counter(g(r, 'transaction_id') for r in purchases if g(r, 'transaction_id'))
        dups = {k: v for k, v in dup.items() if v > 1}
        if dups:
            add('yüksek', 'tekrar eden satın alma', f'{len(dups)} transaction_id birden çok kez gönderilmiş ({", ".join(list(dups)[:5])}); sipariş onay sayfası yenilemesi', sum(v - 1 for v in dups.values()))
    if purchases and 'value' in columns:
        bad = sum(1 for r in purchases if not g(r, 'value') or ('currency' in columns and not g(r, 'currency')))
        if bad:
            add('orta', 'değer/para birimi yok', f'{bad} satın almada value veya currency boş: gelir raporu eksik', bad)
    names = Counter(g(r, 'event_name') for r in rows)
    wrong = [n for n in names if len(n) > 40 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', n) or n.lower().startswith(GA4_RESERVED)]
    if wrong:
        add('orta', 'olay adı kuralı', f'{", ".join(wrong)}: en fazla 40 karakter, harfle başlayan harf/rakam/alt çizgi; ga_/google_/firebase_ önekleri ayrılmış', sum(names[n] for n in wrong))
    case = defaultdict(set)
    for n in names:
        case[n.lower()].add(n)
    split = [sorted(v) for v in case.values() if len(v) > 1]
    if split:
        add('düşük', 'büyük/küçük harf farkıyla ayrı olay', '; '.join(' / '.join(v) for v in split) + ': GA4 bunları farklı olay sayar', sum(names[n] for v in split for n in v))
    if 'page_location' in columns:
        pii = sum(1 for r in rows if PII_URL.search(g(r, 'page_location')))
        if pii:
            add('yüksek', 'URL’de kişisel veri', f'{pii} olayın page_location’ında e-posta/telefon parametresi var: Google politikasına aykırı, etiketi düzeltin ve geçmiş veriyi silme talebi açın', pii)
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2}
    out.sort(key=lambda o: (order[o['onem']], o['kontrol']))
    return Result(f'{len(rows)} olay, {len(span)} gün, {len(names)} olay adı; {len(out)} bulgu', ['onem', 'kontrol', 'ayrinti', 'etkilenen'], out,
                  {'olay': len(rows), 'gun': len(span), 'eksik_gun': len(low), 'bulgu': len(out)},
                  tables=[('Günlük olay sayısı', ['Gün', 'Olay'], [[f'{d:%Y-%m-%d}', c] for d, c in zip(span, counts)]),
                          ('Olay adları', ['Olay', 'Adet'], names.most_common())],
                  notes=['BigQuery dışa aktarımı yalnızca bağlantının açıldığı günden itibaren veri tutar; geçmiş geri gelmez. Günlük dışa aktarımda tablolar events_YYYYMMDD adıyla gelir.'])


TYPES = {'string': str, 'metin': str, 'number': (int, float), 'sayi': (int, float), 'sayı': (int, float), 'integer': int, 'boolean': bool, 'bool': bool, 'array': list, 'object': dict}


def title_case_event(name):
    return bool(re.fullmatch(r'[A-Z][a-z0-9]*( [A-Z][a-z0-9]*)+', name))


@op('tracking-plan-uyumu', title='Tracking plan uyumu: plansız olaylar, eksik ve yanlış tipli özellikler', domain='product', product='Segment', tag='tracking plan', kind='file',
    samples={'segment_olaylar.jsonl': 'segment_olaylar.jsonl', 'tracking_plan.csv': 'tracking_plan.csv'},
    args=[arg('--plan', default='@ornek/tracking_plan.csv', help='Tracking plan (olay, ozellik, tip, zorunlu, izinli_degerler)'),
          arg('--style', default='object-action', choices=['object-action', 'snake'], help='Olay adı kuralı: "Order Completed" veya "order_completed"')],
    description='Segment / Amplitude / Mixpanel olay dışa aktarımını (JSON Lines: event, properties) tracking plan tablosuyla karşılaştırır: plana hiç girmemiş olaylar, plandaki ama hiç gelmeyen olaylar, zorunlu özelliği eksik olaylar, plandakinden farklı tipte gelen özellikler, izinli değer listesi dışındaki değerler, plansız (fazladan) özellikler ve adlandırma kuralına (Object Action veya snake_case) uymayan olay adları. Olay bazında uyum yüzdesi verir.',
    input='Olaylar JSON Lines + tracking plan CSV’si', output='sonuc.csv (olay bazında uyum) + bulgular', command='python main.py --input events.jsonl --plan tracking_plan.csv --style snake',
    expect={'olay': 30, 'olay_adi': 5, 'uyum_%': 63.3, 'plansiz_olay': 2, 'gelmeyen_olay': 1})
def tracking_plan(args):
    pcols, prows = tabular.read(args.plan)
    for c in ('olay', 'ozellik', 'tip'):
        need(c, pcols, '--plan')
    plan = defaultdict(dict)
    for r in prows:
        allowed = [x.strip() for x in r.get('izinli_degerler', '').split('|') if x.strip()]
        plan[r['olay'].strip()][r['ozellik'].strip()] = (r['tip'].strip().lower(), r.get('zorunlu', '').strip().lower() in ('evet', 'true', '1', 'e', 'yes'), allowed)
    events = []
    for n, line in enumerate(Path(args.input).read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.strip():
            try:
                events.append(json.loads(line))
            except ValueError:
                raise ValueError(f'{n}. satır JSON değil.') from None
    stats = defaultdict(lambda: {'n': 0, 'ok': 0, 'issues': Counter()})
    for e in events:
        name, props = e.get('event', ''), e.get('properties') or {}
        s = stats[name]
        s['n'] += 1
        if name not in plan:
            s['issues']['plansız olay'] += 1
            continue
        problems = []
        for prop, (typ, required, allowed) in plan[name].items():
            if prop not in props or props[prop] in (None, ''):
                if required:
                    problems.append(f'zorunlu “{prop}” eksik')
                continue
            v = props[prop]
            want = TYPES.get(typ)
            if want and (not isinstance(v, want) or isinstance(v, bool) and want in (int, (int, float))):
                problems.append(f'“{prop}” {typ} olmalı ({type(v).__name__} geldi)')
            elif allowed and str(v) not in allowed:
                problems.append(f'“{prop}” izinli değil: {v}')
        extra = sorted(set(props) - set(plan[name]))
        if extra:
            problems.append('plansız özellik: ' + ', '.join(extra))
        for p_ in problems:
            s['issues'][p_] += 1
        if not problems:
            s['ok'] += 1
    style_ok = title_case_event if args.style == 'object-action' else (lambda n: bool(re.fullmatch(r'[a-z][a-z0-9]*(_[a-z0-9]+)+', n)))
    out = []
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]['n']):
        notes = [f'{k} ({v})' for k, v in s['issues'].most_common()]
        if not style_ok(name):
            notes.append(f'ad {args.style} kuralına uymuyor')
        out.append({'olay': name, 'adet': s['n'], 'planda': 'evet' if name in plan else 'hayır', 'uyumlu': s['ok'], 'uyum_%': round(100 * s['ok'] / s['n'], 1), 'sorunlar': '; '.join(notes)})
    missing = sorted(set(plan) - set(stats))
    for name in missing:
        out.append({'olay': name, 'adet': 0, 'planda': 'evet', 'uyumlu': 0, 'uyum_%': '', 'sorunlar': 'planda var ama hiç gelmedi: etiket eksik veya kaldırılmış'})
    total_ok = sum(s['ok'] for s in stats.values())
    return Result(f'{len(events)} olay, {len(stats)} olay adı; genel uyum %{100 * total_ok / (len(events) or 1):.1f}, {sum(1 for n in stats if n not in plan)} plansız olay, {len(missing)} gelmeyen olay',
                  list(out[0]), out,
                  {'olay': len(events), 'olay_adi': len(stats), 'uyum_%': round(100 * total_ok / (len(events) or 1), 1),
                   'plansiz_olay': sum(1 for n in stats if n not in plan), 'gelmeyen_olay': len(missing)},
                  notes=['Segment Protocols, Amplitude Data ve Mixpanel Lexicon aynı kontrolleri canlı olarak yapar; bu blok dışa aktarılmış örnek üzerinde geçiş öncesi denetim içindir.'])
