"""Bütçe ve planlama blokları: bütçe–gerçekleşen, bütçe dağıtımı, yıl sonu tahmini, senaryo analizi, konsolidasyon ve grup içi eliminasyon."""
import math, re
from collections import defaultdict
from pathlib import Path
import tabular
from islem import op, arg, Result, need, col_list, numbers, fmt, tr_money, tr_sort_key

D, P = 'planning', 'Bütçe ve tahmin dosyaları'
BUTCE = {'yillik_butce.csv': 'yillik_butce.csv', 'gerceklesen.csv': 'gerceklesen.csv'}
AYLAR = ['ocak', 'subat', 'mart', 'nisan', 'mayis', 'haziran', 'temmuz', 'agustos', 'eylul', 'ekim', 'kasim', 'aralik']


def weights_from(path):
    if not path:
        return [1 / 12] * 12
    c, r = tabular.read(path)
    need('agirlik', c, '--weights')
    w = numbers(r, 'agirlik')
    if len(w) != 12 or any(x is None or x < 0 for x in w):
        raise ValueError('Ağırlık dosyasında 12 ay için negatif olmayan "agirlik" değeri olmalı.')
    s = sum(w)
    return [x / s for x in w]


def month_no(value):
    v = value.strip()
    m = re.match(r'^(\d{4})-(\d{1,2})', v)
    if m:
        return int(m.group(2))
    d = tabular.parse_date(v)
    if d:
        return d[0].month
    key = v.lower().translate(str.maketrans('çğıöşüİ', 'cgiosui'))
    return AYLAR.index(key) + 1 if key in AYLAR else None


def budget_lines(columns, rows, key_cols, amount):
    vals = numbers(rows, need(amount, columns, '--amount'))
    out = {}
    for r, v in zip(rows, vals):
        out[tuple(r[k].strip() for k in key_cols)] = (v or 0.0, r)
    return out


@op('butce-gerceklesen', title='Bütçe – gerçekleşen sapma analizi', domain=D, product=P, tag='sapma', samples={**BUTCE, 'mevsim_agirliklari.csv': 'mevsim_agirliklari.csv'},
    args=[arg('--actuals', default='@ornek/gerceklesen.csv'), arg('--keys', default='hesap,departman'), arg('--budget-col', default='yillik_butce'), arg('--actual-col', default='gerceklesen'),
          arg('--month-col', default='ay'), arg('--weights', default='@ornek/mevsim_agirliklari.csv', help='Aylık dağıtım ağırlıkları; "" eşit dağıtır'),
          arg('--threshold', type=float, default=10.0, help='Bu yüzdeyi aşan sapmaları işaretle'), arg('--expense-prefixes', default='6,7', help='Hesap kodu bu rakamlarla başlarsa (6 hariç satışlar) gider sayılır')],
    description='Yıllık bütçeyi mevsim ağırlıklarıyla aylara dağıtır, aylık gerçekleşenle karşılaştırır; hesap/departman bazında ay ve yılbaşından bugüne (YTD) sapmayı tutar ve yüzde olarak verir. Gider kalemlerinde aşımı, gelir kalemlerinde eksikliği olumsuz işaretler.',
    input='Yıllık bütçe + aylık gerçekleşen (+ isteğe bağlı mevsim ağırlıkları)', output='sonuc.csv (kalem bazında YTD) + aylik.csv', command='python main.py --input butce.xlsx --actuals fiili.xlsx --keys hesap,departman',
    expect={'kalem': 4, 'son_ay': 9, 'olumsuz': 1})
def butce_gercek(columns, rows, args):
    keys = col_list(args.keys, columns, '--keys')
    w = weights_from(args.weights)
    budget = budget_lines(columns, rows, keys, args.budget_col) if args.budget_col in columns else None
    if budget is None:
        raise ValueError(f'"{args.budget_col}" sütunu bütçe dosyasında yok.')
    ac, ar = tabular.read(args.actuals)
    for k in keys + [args.month_col, args.actual_col]:
        need(k, ac, '--actuals')
    actual = defaultdict(float)
    months = set()
    for r, v in zip(ar, numbers(ar, args.actual_col)):
        m = month_no(r[args.month_col])
        if m:
            months.add(m)
            actual[(tuple(r[k].strip() for k in keys), m)] += v or 0.0
    last = max(months)
    out, monthly = [], []
    for key, (annual, r) in sorted(budget.items()):
        ytd_b = sum(annual * w[m - 1] for m in range(1, last + 1))
        ytd_a = sum(actual[(key, m)] for m in range(1, last + 1))
        code = key[0]
        expense = code[:1] in args.expense_prefixes.split(',') and not code.startswith(('60', '64', '67'))
        diff = ytd_a - ytd_b
        pct = 100 * diff / ytd_b if ytd_b else 0.0
        bad = (pct > args.threshold) if expense else (pct < -args.threshold)
        out.append({**{k: v for k, v in zip(keys, key)}, 'tur': 'gider' if expense else 'gelir', 'ytd_butce': round(ytd_b, 2), 'ytd_gerceklesen': round(ytd_a, 2),
                    'sapma': round(diff, 2), 'sapma_%': round(pct, 1), 'durum': 'olumsuz' if bad else 'eşik içinde'})
        for m in range(1, last + 1):
            b = annual * w[m - 1]
            a = actual[(key, m)]
            monthly.append({**{k: v for k, v in zip(keys, key)}, 'ay': m, 'butce': round(b, 2), 'gerceklesen': round(a, 2), 'sapma_%': round(100 * (a - b) / b, 1) if b else ''})
    neg = sum(o['durum'] == 'olumsuz' for o in out)
    return Result(f'{len(out)} kalem, {last}. ay sonu itibarıyla; {neg} kalemde %{args.threshold:g} üzeri olumsuz sapma', list(out[0]), out,
                  {'kalem': len(out), 'son_ay': last, 'olumsuz': neg}, files={'aylik.csv': (list(monthly[0]), monthly)},
                  notes=['Gelir/gider ayrımı hesap koduna göre yapılır (6xx satış ve gelir hesapları gelir, diğer 6-7 gider).'])


@op('butce-dagit', title='Yıllık bütçeyi aylara dağıt', domain=D, product=P, tag='dağıtım', samples={'yillik_butce.csv': 'yillik_butce.csv', 'mevsim_agirliklari.csv': 'mevsim_agirliklari.csv'},
    args=[arg('--amount', default='yillik_butce'), arg('--weights', default='@ornek/mevsim_agirliklari.csv'), arg('--year', type=int, default=2027)],
    description='Yıllık bütçe satırlarını eşit, mevsim ağırlıklı (dosyadan) olarak 12 aya dağıtır; kuruş yuvarlama farkını son aya ekleyerek toplamı korur. Planlama aracına yüklenecek uzun (satır × ay) tabloyu üretir.',
    input='Yıllık bütçe + isteğe bağlı ağırlık dosyası', output='sonuc.csv (satır × ay)', command='python main.py --weights agirliklar.csv --year 2027',
    expect={'satir': 48, 'fark': 0.0})
def dagit(columns, rows, args):
    w = weights_from(args.weights)
    vals = numbers(rows, need(args.amount, columns, '--amount'))
    out, gap = [], 0.0
    for r, v in zip(rows, vals):
        v = v or 0.0
        parts = [round(v * x, 2) for x in w]
        parts[-1] = round(v - sum(parts[:-1]), 2)
        gap += round(sum(parts) - v, 2)
        base = {c: r[c] for c in columns if c != args.amount}
        out += [{**base, 'donem': f'{args.year}-{i + 1:02d}', 'tutar': p} for i, p in enumerate(parts)]
    return Result(f'{len(rows)} satır {len(out)} aylık satıra dağıtıldı', [c for c in columns if c != args.amount] + ['donem', 'tutar'], out,
                  {'satir': len(out), 'fark': round(gap, 2)})


@op('yil-sonu-tahmini', title='Yıl sonu tahmini (gerçekleşen + kalan aylar)', domain=D, product=P, tag='forecast', samples=BUTCE,
    args=[arg('--actuals', default='@ornek/gerceklesen.csv'), arg('--keys', default='hesap,departman'), arg('--budget-col', default='yillik_butce'), arg('--actual-col', default='gerceklesen'),
          arg('--month-col', default='ay'), arg('--method', choices=['son3', 'ytd'], default='son3', help='Kalan aylar: son 3 ay ortalaması veya YTD ortalaması')],
    description='Yılbaşından bugüne gerçekleşeni, kalan aylar için son 3 ay (veya YTD) ortalamasıyla tamamlayarak yıl sonu tahmini (forecast) üretir ve yıllık bütçeyle farkını çıkarır. Aylık kapanış sonrası “yılı nasıl kapatırız” sorusu için.',
    input='Yıllık bütçe + aylık gerçekleşen', output='sonuc.csv', command='python main.py --method son3',
    expect={'kalem': 4, 'kalan_ay': 3})
def forecast(columns, rows, args):
    keys = col_list(args.keys, columns, '--keys')
    budget = budget_lines(columns, rows, keys, args.budget_col)
    ac, ar = tabular.read(args.actuals)
    actual = defaultdict(lambda: defaultdict(float))
    for r, v in zip(ar, numbers(ar, need(args.actual_col, ac, '--actual-col'))):
        m = month_no(r[need(args.month_col, ac, '--month-col')])
        if m:
            actual[tuple(r[k].strip() for k in keys)][m] += v or 0.0
    last = max(m for s in actual.values() for m in s)
    out = []
    for key, (annual, _) in sorted(budget.items()):
        s = actual.get(key, {})
        ytd = sum(s.get(m, 0.0) for m in range(1, last + 1))
        window = [s.get(m, 0.0) for m in range(max(1, last - 2), last + 1)] if args.method == 'son3' else [s.get(m, 0.0) for m in range(1, last + 1)]
        rate = sum(window) / len(window)
        fc = ytd + rate * (12 - last)
        out.append({**dict(zip(keys, key)), 'ytd': round(ytd, 2), 'aylik_hiz': round(rate, 2), 'yil_sonu_tahmini': round(fc, 2), 'yillik_butce': round(annual, 2),
                    'fark': round(fc - annual, 2), 'fark_%': round(100 * (fc - annual) / annual, 1) if annual else ''})
    return Result(f'{last} ay gerçekleşen + {12 - last} ay tahmin ({args.method}) ile {len(out)} kalem', list(out[0]), out, {'kalem': len(out), 'kalan_ay': 12 - last})


@op('senaryo-analizi', title='İyimser / baz / kötümser senaryo hesabı', domain=D, product=P, tag='senaryo', samples={'yillik_butce.csv': 'yillik_butce.csv'},
    args=[arg('--amount', default='yillik_butce'), arg('--key', default='hesap'), arg('--scenarios', default='iyimser:600=+10,621=+8,760=+5;baz:;kotumser:600=-15,621=-10,770=+5'),
          arg('--profit', default='600,-621,-760,-770', help='Kâr formülü: hesaplar, eksi işaretliler gider')],
    description='Baz bütçe üzerine senaryo başına kalem bazında yüzde değişimler uygular (ör. satış −%15, maliyet −%10) ve her senaryonun kalem değerlerini ve tanımlanan kâr formülünün sonucunu yan yana gösterir.',
    input='Baz bütçe + senaryo tanımı', output='sonuc.csv (kalem × senaryo) + kâr özeti', command='python main.py --scenarios "iyimser:600=+10;kotumser:600=-15,621=-10"',
    expect={'senaryo': 3, 'kalem': 4})
def senaryo(columns, rows, args):
    vals = numbers(rows, need(args.amount, columns, '--amount'))
    base = {r[need(args.key, columns, '--key')].strip(): v or 0.0 for r, v in zip(rows, vals)}
    scen = {}
    for part in [p for p in args.scenarios.split(';') if p.strip()]:
        name, _, spec = part.partition(':')
        changes = {}
        for item in [x for x in spec.split(',') if x.strip()]:
            k, _, pct = item.partition('=')
            if k.strip() not in base:
                raise ValueError(f'Senaryo "{name}": "{k.strip()}" kalemi bütçede yok.')
            changes[k.strip()] = float(pct.replace(',', '.'))
        scen[name.strip()] = changes
    out = []
    for k in sorted(base, key=tr_sort_key):
        row = {args.key: k, 'baz': round(base[k], 2)}
        for name, ch in scen.items():
            row[name] = round(base[k] * (1 + ch.get(k, 0.0) / 100), 2)
        out.append(row)
    terms = [(t.strip().lstrip('-'), -1 if t.strip().startswith('-') else 1) for t in args.profit.split(',') if t.strip()]
    profit = {name: sum(s * next((r[name] for r in out if r[args.key] == k), 0.0) for k, s in terms) for name in ['baz'] + list(scen)}
    return Result('Kâr: ' + ', '.join(f'{n} {tr_money(v)}' for n, v in profit.items()), [args.key, 'baz'] + list(scen), out,
                  {'senaryo': len(scen), 'kalem': len(out), 'kar': {k: round(v, 2) for k, v in profit.items()}},
                  tables=[('Kâr özeti', ['Senaryo', 'Kâr', 'Baza göre'], [[n, tr_money(v), tr_money(v - profit['baz'])] for n, v in profit.items()])])


@op('konsolidasyon', title='Şirket mizanlarını konsolide et ve grup içi işlemleri elimine et', domain=D, product=P, tag='konsolidasyon', kind='file',
    samples={'konsolidasyon/sirket_a.csv': 'konsolidasyon/sirket_a.csv', 'konsolidasyon/sirket_b.csv': 'konsolidasyon/sirket_b.csv', 'grup_ici_eslesme.csv': 'grup_ici_eslesme.csv'},
    sample_input='konsolidasyon', args=[arg('--pairs', default='@ornek/grup_ici_eslesme.csv', help='Grup içi karşılık hesap çiftleri (hesap_a, hesap_b)'),
                                        arg('--account', default='hesap'), arg('--amount', default='bakiye'), arg('--tolerance', type=float, default=1.0)],
    description='Klasördeki şirket bakiye dosyalarını hesap bazında toplar; grup içi alacak–borç ve satış–maliyet çiftlerini eliminasyon tablosuna göre siler ve karşılıkların birbirini tutup tutmadığını (mutabakat farkı) kontrol eder.',
    input='Şirket bakiye dosyaları klasörü + grup içi hesap eşleme dosyası', output='sonuc.csv (konsolide bakiye) + eliminasyon.csv', command='python main.py --input konsolidasyon/ --pairs eslesme.csv',
    expect={'sirket': 2, 'eliminasyon': 2, 'mutabik_olmayan': 0})
def konsolidasyon(args):
    folder = Path(args.input)
    files = sorted(f for f in folder.glob('*.csv') if f.is_file()) if folder.is_dir() else []
    if len(files) < 2:
        raise ValueError('Klasörde en az iki şirket dosyası (CSV) olmalı.')
    pc, pr = tabular.read(args.pairs)
    need('hesap_a', pc, '--pairs')
    need('hesap_b', pc, '--pairs')
    totals, names, by_company = defaultdict(float), {}, {}
    for f in files:
        c, r = tabular.read(f)
        acc, amt = need(args.account, c, f.name), need(args.amount, c, f.name)
        vals = numbers(r, amt)
        by_company[f.stem] = {}
        for row, v in zip(r, vals):
            k = row[acc].strip()
            totals[k] += v or 0.0
            by_company[f.stem][k] = by_company[f.stem].get(k, 0.0) + (v or 0.0)
            names.setdefault(k, row.get('hesap_adi', ''))
    elim, issues = [], 0
    for p in pr:
        a, b = p['hesap_a'].strip(), p['hesap_b'].strip()
        va, vb = totals.get(a, 0.0), totals.get(b, 0.0)
        diff = va + vb if (va > 0) != (vb > 0) else va - vb
        ok = abs(abs(va) - abs(vb)) <= args.tolerance
        issues += not ok
        elim.append({'hesap_a': a, 'tutar_a': round(va, 2), 'hesap_b': b, 'tutar_b': round(vb, 2), 'mutabakat_farki': round(abs(va) - abs(vb), 2), 'durum': 'mutabık' if ok else 'FARK VAR'})
        totals[a], totals[b] = 0.0, 0.0
    companies = list(by_company)
    out = [{'hesap': k, 'hesap_adi': names.get(k, ''), **{c: round(by_company[c].get(k, 0.0), 2) for c in companies}, 'konsolide': round(v, 2)}
           for k, v in sorted(totals.items(), key=lambda kv: tr_sort_key(kv[0])) if abs(v) > 0.005]
    return Result(f'{len(files)} şirket konsolide edildi; {len(elim)} grup içi çift elimine edildi, {issues} çiftte mutabakat farkı', ['hesap', 'hesap_adi'] + companies + ['konsolide'], out,
                  {'sirket': len(files), 'eliminasyon': len(elim), 'mutabik_olmayan': issues}, files={'eliminasyon.csv': (list(elim[0]), elim)},
                  notes=['Kur çevrimi, azınlık payı ve yatırım–özkaynak eliminasyonu bu blokta yapılmaz.'])


def erlang_c(n, a):
    """Probability that a call waits (Erlang C) with n agents and a Erlangs of traffic; computed via the Erlang B recursion."""
    b = 1.0
    for k in range(1, n + 1):
        b = a * b / (k + a * b)
    return n * b / (n - a * (1 - b))


@op('cagri-merkezi-personel', title='Çağrı merkezi personel ihtiyacı (Erlang C)', domain=D, product=P, tag='Erlang C', samples={'cagri_hacmi.csv': 'cagri_hacmi.csv'},
    args=[arg('--interval', default='aralik'), arg('--calls', default='cagri'), arg('--aht', type=float, default=180.0, help='Ortalama görüşme + iş süresi (sn)'),
          arg('--length', type=float, default=1800.0, help='Aralık uzunluğu (sn)'), arg('--sl', type=float, default=0.80, help='Hedef hizmet seviyesi'),
          arg('--target', type=float, default=20.0, help='Hedef cevaplama süresi (sn)'), arg('--shrinkage', type=float, default=0.30, help='Mola, eğitim, devamsızlık payı')],
    description='Aralık bazında çağrı tahmininden Erlang C modeliyle hedef hizmet seviyesini (ör. çağrıların %80’i 20 saniyede) sağlayan en az temsilci sayısını, beklenen hizmet seviyesini, ortalama bekleme süresini (ASA) ve doluluk oranını hesaplar; mola/devamsızlık payıyla vardiyaya yazılacak kişi sayısını verir.',
    input='Aralık bazında çağrı tahmini (aralık, çağrı)', output='sonuc.csv (aralık bazında personel)', command='python main.py --aht 240 --sl 0.9 --target 15 --shrinkage 0.35',
    expect={'aralik': 8, 'toplam_cagri': 780, 'en_yogun_temsilci': 17, 'en_yogun_vardiya': 25})
def erlang(columns, rows, args):
    calls = numbers(rows, need(args.calls, columns, '--calls'))
    icol = need(args.interval, columns, '--interval')
    out = []
    for r, c in zip(rows, calls):
        c = c or 0.0
        a = c * args.aht / args.length
        n = max(1, math.floor(a) + 1) if a else 0
        sl = asa = 1.0
        while n:
            pw = erlang_c(n, a)
            sl = 1 - pw * math.exp(-(n - a) * args.target / args.aht)
            asa = pw * args.aht / (n - a)
            if sl >= args.sl:
                break
            n += 1
        out.append({'aralik': r[icol], 'cagri': fmt(c), 'trafik_erlang': round(a, 2), 'temsilci': n, 'hizmet_seviyesi_%': round(100 * sl, 1) if n else '',
                    'ort_bekleme_sn': round(asa, 1) if n else '', 'doluluk_%': round(100 * a / n, 1) if n else '', 'vardiya_kisi': math.ceil(n / (1 - args.shrinkage)) if n else 0})
    peak = max(out, key=lambda o: o['temsilci'])
    return Result(f'{len(out)} aralık; en yoğun aralık {peak["aralik"]}: {peak["temsilci"]} temsilci ({peak["vardiya_kisi"]} kişi vardiyada)', list(out[0]), out,
                  {'aralik': len(out), 'toplam_cagri': round(sum(c or 0 for c in calls)), 'en_yogun_temsilci': peak['temsilci'], 'en_yogun_vardiya': peak['vardiya_kisi'],
                   'temsilci_saat': round(sum(o['temsilci'] for o in out) * args.length / 3600, 1)},
                  notes=['Erlang C çağrıyı bırakıp gidenleri (abandon) hesaba katmaz; yoğun aralıklarda biraz fazla personel önerir.',
                         'Doluluk %85–90’ın üzerindeyse temsilci yorgunluğu artar; hedef hizmet seviyesini ve AHT’yi gerçekleşen değerlerle güncelleyin.'])


@op('fiyat-hacim-karma', title='Fiyat–hacim–karma (price-volume-mix) sapma analizi', domain=D, product=P, tag='PVM', samples={'pvm_satis.csv': 'pvm_satis.csv'},
    args=[arg('--item', default='urun'), arg('--bq', default='butce_adet'), arg('--bp', default='butce_fiyat'), arg('--aq', default='gercek_adet'), arg('--ap', default='gercek_fiyat')],
    description='Bütçe (veya önceki dönem) ile gerçekleşen ciro arasındaki farkı ürün bazında hacim, karma (ürün sepeti değişimi) ve fiyat etkisine ayırır; üç etkinin toplamı ciro farkına eşittir. Yeni ve bırakılan ürünleri karma etkisinde gösterir. Yönetim raporunda “ciro neden saptı” sorusunu sayılarla cevaplamak için.',
    input='Ürün bazında bütçe/gerçekleşen adet ve fiyat', output='sonuc.csv (ürün bazında etkiler)', command='python main.py --input pvm.xlsx --bq onceki_adet --bp onceki_fiyat',
    expect={'toplam_fark': 230.0, 'hacim_etkisi': 450.0, 'fiyat_etkisi': 80.0, 'karma_etkisi': -300.0})
def pvm(columns, rows, args):
    icol = need(args.item, columns, '--item')
    bq, bp, aq, ap = (numbers(rows, need(c, columns, f)) for c, f in ((args.bq, '--bq'), (args.bp, '--bp'), (args.aq, '--aq'), (args.ap, '--ap')))
    items = []
    for r, q1, p1, q2, p2 in zip(rows, bq, bp, aq, ap):
        q1, q2 = q1 or 0.0, q2 or 0.0
        p1 = p1 if p1 is not None else (p2 or 0.0)
        p2 = p2 if p2 is not None else p1
        items.append((r[icol], q1, p1, q2, p2))
    tb, ta = sum(i[1] for i in items), sum(i[3] for i in items)
    if not tb:
        raise ValueError('Bütçe adet toplamı sıfır; hacim/karma ayrıştırılamaz.')
    out = []
    for name, q1, p1, q2, p2 in items:
        vol = (ta - tb) * (q1 / tb) * p1
        mix = (q2 - ta * q1 / tb) * p1
        price = (p2 - p1) * q2
        out.append({'urun': name, 'butce_ciro': round(q1 * p1, 2), 'gercek_ciro': round(q2 * p2, 2), 'fark': round(q2 * p2 - q1 * p1, 2), 'hacim': round(vol, 2), 'karma': round(mix, 2),
                    'fiyat': round(price, 2), 'not': 'yeni ürün' if not q1 and q2 else 'bırakılan ürün' if q1 and not q2 else ''})
    tot = {k: round(sum(o[k] for o in out), 2) for k in ('fark', 'hacim', 'karma', 'fiyat')}
    return Result(f'Ciro farkı {tr_money(tot["fark"])} = hacim {tr_money(tot["hacim"])} + karma {tr_money(tot["karma"])} + fiyat {tr_money(tot["fiyat"])}', list(out[0]), out,
                  {'butce_ciro': round(sum(o['butce_ciro'] for o in out), 2), 'gercek_ciro': round(sum(o['gercek_ciro'] for o in out), 2), 'toplam_fark': tot['fark'],
                   'hacim_etkisi': tot['hacim'], 'karma_etkisi': tot['karma'], 'fiyat_etkisi': tot['fiyat']},
                  notes=['Hacim: toplam adet değişimi × bütçe ortalama fiyatı. Karma: ürün payı değişimi × bütçe fiyatı. Fiyat: (gerçek − bütçe fiyat) × gerçek adet.',
                         'Bütçesi olmayan yeni ürünlerde bütçe fiyatı olarak gerçek fiyat kullanılır; etkisi karmada görünür.'])


@op('basa-bas-analizi', title='Çok ürünlü başa baş noktası ve güvenlik marjı', domain=D, product=P, tag='başa baş', samples={'basabas_urunler.csv': 'basabas_urunler.csv'},
    args=[arg('--fixed', type=float, default=19000.0, help='Dönem sabit giderleri'), arg('--item', default='urun'), arg('--price', default='fiyat'), arg('--var', default='birim_degisken_maliyet'),
          arg('--qty', default='planlanan_adet', help='Satış karmasını belirleyen planlanan adet')],
    description='Ürün fiyatı, birim değişken maliyet ve planlanan satış karmasından ağırlıklı katkı payını hesaplar; sabit giderleri karşılayan toplam ve ürün bazında başa baş adedini, başa baş cirosunu, planlanan kârı ve güvenlik marjını (planlanan cironun başa başın ne kadar üstünde olduğu) verir. Negatif katkı paylı ürünleri işaretler.',
    input='Ürün listesi (fiyat, değişken maliyet, planlanan adet) + sabit gider', output='sonuc.csv (ürün bazında başa baş)', command='python main.py --fixed 250000 --input fiyat_plani.xlsx',
    expect={'basabas_adet': 500, 'basabas_ciro': 47500.0, 'guvenlik_marji_%': 50.0, 'planlanan_kar': 19000.0})
def breakeven(columns, rows, args):
    icol = need(args.item, columns, '--item')
    prices, varc, qty = (numbers(rows, need(c, columns, f)) for c, f in ((args.price, '--price'), (args.var, '--var'), (args.qty, '--qty')))
    items = [(r[icol], p or 0.0, v or 0.0, q or 0.0) for r, p, v, q in zip(rows, prices, varc, qty)]
    total_q = sum(q for *_, q in items)
    if not total_q:
        raise ValueError('Planlanan adet toplamı sıfır; satış karması hesaplanamaz.')
    wcm = sum(q / total_q * (p - v) for _, p, v, q in items)
    if wcm <= 0:
        raise ValueError('Ağırlıklı katkı payı sıfır veya negatif; bu karmayla başa baş noktası yok.')
    be_units = args.fixed / wcm
    out = []
    for name, p, v, q in items:
        units = be_units * q / total_q
        out.append({'urun': name, 'fiyat': fmt(p), 'katki_payi': fmt(round(p - v, 4)), 'katki_orani_%': round(100 * (p - v) / p, 1) if p else '', 'karma_%': round(100 * q / total_q, 1),
                    'basabas_adet': round(units, 1), 'basabas_ciro': round(units * p, 2), 'planlanan_adet': fmt(q), 'not': 'negatif katkı payı' if p < v else ''})
    be_rev = sum(o['basabas_ciro'] for o in out)
    plan_rev = sum(p * q for _, p, _, q in items)
    profit = sum((p - v) * q for _, p, v, q in items) - args.fixed
    mos = 100 * (plan_rev - be_rev) / plan_rev if plan_rev else 0.0
    return Result(f'Başa baş {be_units:,.0f} adet / {tr_money(be_rev)} ciro; planlanan kâr {tr_money(profit)}, güvenlik marjı %{mos:.1f}', list(out[0]), out,
                  {'agirlikli_katki_payi': round(wcm, 4), 'basabas_adet': round(be_units, 2), 'basabas_ciro': round(be_rev, 2), 'planlanan_ciro': round(plan_rev, 2),
                   'planlanan_kar': round(profit, 2), 'guvenlik_marji_%': round(mos, 1)},
                  notes=['Başa baş noktası satış karmasının planlandığı gibi gerçekleşeceğini varsayar; karma katkı payı düşük ürünlere kayarsa nokta yükselir.'])


@op('duyarlilik-analizi', title='Formül tabanlı duyarlılık (tornado) analizi', domain=D, product=P, tag='tornado', samples={'surucular.csv': 'surucular.csv'},
    args=[arg('--formula', default='adet * (fiyat - birim_maliyet) - sabit_gider', help='Sürücü adlarıyla sonuç formülü'), arg('--change', type=float, default=10.0, help='± yüzde değişim'),
          arg('--name', default='surucu'), arg('--value', default='deger')],
    requires=['temizlik'],
    description='Bir sonuç formülünün (kâr, nakit, marj…) her sürücüyü tek tek ±% değiştirdiğinizde (veya dosyada verdiğiniz düşük/yüksek değerlere çektiğinizde) ne kadar oynadığını hesaplar ve sürücüleri etki aralığına göre sıralar (tornado grafiği verisi). Bütçe sunumlarında “hangi varsayım sonucu en çok etkiler” sorusu için.',
    input='Sürücü listesi (sürücü, değer, isteğe bağlı dusuk/yuksek) + formül', output='sonuc.csv (sürücü × düşük/yüksek sonuç)', command='python main.py --formula "hacim * marj - opex" --change 15',
    expect={'baz': 8000.0, 'en_etkili': 'fiyat', 'en_etkili_aralik': 10000.0})
def tornado(columns, rows, args):
    from temizlik import Expr
    ncol, vcol = need(args.name, columns, '--name'), need(args.value, columns, '--value')
    vals = numbers(rows, vcol)
    base = {r[ncol].strip(): v for r, v in zip(rows, vals) if r[ncol].strip() and v is not None}
    expr = Expr(args.formula, list(base))
    calc = lambda env: expr({k: repr(float(x)) for k, x in env.items()}, [{k: repr(float(x)) for k, x in base.items()}])
    b = calc(base)
    if not isinstance(b, (int, float)):
        raise ValueError('Formül baz değerlerle hesaplanamadı; sürücü adlarını kontrol edin.')
    lo_col, hi_col = ('dusuk' if 'dusuk' in columns else None), ('yuksek' if 'yuksek' in columns else None)
    lows = numbers(rows, lo_col) if lo_col else [None] * len(rows)
    highs = numbers(rows, hi_col) if hi_col else [None] * len(rows)
    out = []
    for r, lo_v, hi_v in zip(rows, lows, highs):
        k = r[ncol].strip()
        if k not in base:
            continue
        lo_in = lo_v if lo_v is not None else base[k] * (1 - args.change / 100)
        hi_in = hi_v if hi_v is not None else base[k] * (1 + args.change / 100)
        lo_out, hi_out = calc({**base, k: lo_in}), calc({**base, k: hi_in})
        out.append({'surucu': k, 'baz_deger': fmt(base[k]), 'dusuk_girdi': fmt(round(lo_in, 4)), 'yuksek_girdi': fmt(round(hi_in, 4)), 'dusuk_sonuc': round(lo_out, 2),
                    'yuksek_sonuc': round(hi_out, 2), 'aralik': round(abs(hi_out - lo_out), 2)})
    out.sort(key=lambda o: -o['aralik'])
    return Result(f'Baz sonuç {tr_money(b)}; en etkili sürücü {out[0]["surucu"]} (aralık {tr_money(out[0]["aralik"])})', list(out[0]), out,
                  {'baz': round(b, 2), 'surucu': len(out), 'en_etkili': out[0]['surucu'], 'en_etkili_aralik': out[0]['aralik']},
                  notes=['Her sürücü diğerleri sabitken değiştirilir; sürücüler arası ilişki (fiyat artınca adedin düşmesi) için senaryo-analizi bloğunu kullanın.'])
