"""Bütçe ve planlama blokları: bütçe–gerçekleşen, bütçe dağıtımı, yıl sonu tahmini, senaryo analizi, konsolidasyon ve grup içi eliminasyon."""
import re
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
