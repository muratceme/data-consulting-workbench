"""İstatistik ve analiz blokları. Dağılım fonksiyonları (normal, t, ki-kare) standart kütüphaneyle hesaplanır; sonuçlar
istatistik paketleriyle karşılaştırılabilir. Nedensellik iddiası üretmez."""
import math, random
from collections import Counter, defaultdict
from statistics import NormalDist
import tabular
from islem import op, arg, Result, col_list, need, numbers, fmt, tr_sort_key

D, P = 'science', 'İstatistiksel analiz'
PAZ = {'pazarlama_haftalik.csv': 'pazarlama_haftalik.csv'}
SERI = {'aylik_satis_serisi.csv': 'aylik_satis_serisi.csv'}
N = NormalDist()


# ---------------------------------------------------------------- distributions
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return front * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * _betacf(b, a, 1 - x) / b


def t_two_sided(t, df):
    return betainc(df / 2, 0.5, df / (df + t * t)) if df > 0 else float('nan')


def gammaq(a, x):
    """Regularized upper incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    if x < a + 1:
        term = total = 1 / a
        ap = a
        for _ in range(500):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return 1 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = b + an / c if abs(b + an / c) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x, k):
    return gammaq(k / 2, x / 2)


def stars(p):
    return 'anlamlı (p<0,05)' if p < 0.05 else 'anlamlı değil'


# ---------------------------------------------------------------- helpers
def numeric_columns(columns, rows, chosen=None):
    cols = chosen or columns
    out = {}
    for c in cols:
        vals = numbers(rows, c)
        filled = [v for v, r in zip(vals, rows) if r[c].strip()]
        if filled and sum(v is not None for v in filled) >= 0.95 * len(filled):
            out[c] = vals
    return out


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def describe(vals):
    v = sorted(x for x in vals if x is not None)
    n = len(v)
    mean = sum(v) / n
    var = sum((x - mean) ** 2 for x in v) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    skew = (sum((x - mean) ** 3 for x in v) / n) / (sd ** 3) if sd else 0.0
    kurt = (sum((x - mean) ** 4 for x in v) / n) / (sd ** 4) - 3 if sd else 0.0
    return {'n': n, 'ortalama': mean, 'std': sd, 'min': v[0], 'q1': quantile(v, .25), 'medyan': quantile(v, .5), 'q3': quantile(v, .75), 'max': v[-1],
            'carpiklik': skew, 'basiklik': kurt, 'degisim_katsayisi': sd / mean if mean else None}


def pearson(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    mx, my = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx, syy = sum((a - mx) ** 2 for a, _ in pairs), sum((b - my) ** 2 for _, b in pairs)
    return (sxy / math.sqrt(sxx * syy) if sxx and syy else None), n


def ranks(vals):
    order = sorted((v, i) for i, v in enumerate(vals) if v is not None)
    out = [None] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j += 1
        for k in range(i, j + 1):
            out[order[k][1]] = (i + j) / 2 + 1
        i = j + 1
    return out


def series(columns, rows, args):
    need(args.period, columns, '--period')
    vals = numbers(rows, need(args.value, columns, '--value'))
    pts = sorted((r[args.period].strip(), v) for r, v in zip(rows, vals) if v is not None)
    if len(pts) < 8:
        raise ValueError('Seri analizi için en az 8 dönem gerekir.')
    return [p for p, _ in pts], [v for _, v in pts]


SERIES_ARGS = [arg('--period', default='ay'), arg('--value', default='satis'), arg('--season', type=int, default=12, help='Mevsim uzunluğu (aylık veri için 12)')]


# ---------------------------------------------------------------- blocks
@op('tanimlayici-istatistik', title='Tanımlayıcı istatistikler', domain=D, product=P, tag='istatistik', samples=PAZ,
    description='Sayısal sütunlar için adet, ortalama, standart sapma, çeyrekler, medyan, çarpıklık, basıklık ve değişim katsayısını hesaplar; sayı olmayan sütunları otomatik ayırır. Analize başlamadan önce dağılımı tanımak için.',
    input='CSV/XLSX', output='sonuc.csv (sütun başına bir satır)', command='python main.py --columns satis,reklam_harcamasi',
    args=[arg('--columns', help='Varsayılan: tüm sayısal sütunlar')], expect={'sayisal_sutun': 4})
def tanimlayici(columns, rows, args):
    nums = numeric_columns(columns, rows, col_list(args.columns, columns, required=False) or None)
    if not nums:
        raise ValueError('Sayısal sütun bulunamadı.')
    out = [{'sutun': c, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in describe(vals).items()}} for c, vals in nums.items()]
    return Result(f'{len(out)} sayısal sütun özetlendi', list(out[0]), out, {'sayisal_sutun': len(out)})


@op('korelasyon-matrisi', title='Korelasyon matrisi (Pearson ve Spearman)', domain=D, product=P, tag='korelasyon', samples=PAZ,
    description='Sayısal sütun çiftleri arasında Pearson (doğrusal) ve Spearman (sıra) korelasyonunu, anlamlılık değeriyle hesaplar; en güçlü ilişkileri sıralar. Korelasyonun nedensellik olmadığını raporda hatırlatır.',
    input='CSV/XLSX', output='sonuc.csv (çiftler) + matris', command='python main.py --columns reklam_harcamasi,ziyaret,satis',
    args=[arg('--columns', help='Varsayılan: tüm sayısal sütunlar')], expect={'cift': 6, 'en_guclu': 'reklam_harcamasi ~ ziyaret'})
def korelasyon(columns, rows, args):
    nums = numeric_columns(columns, rows, col_list(args.columns, columns, required=False) or None)
    names = list(nums)
    if len(names) < 2:
        raise ValueError('En az iki sayısal sütun gerekir.')
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r, n = pearson(nums[a], nums[b])
            rs, _ = pearson(ranks(nums[a]), ranks(nums[b]))
            if r is None:
                continue
            t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
            out.append({'cift': f'{a} ~ {b}', 'pearson': round(r, 4), 'spearman': round(rs, 4) if rs is not None else '', 'p_degeri': round(t_two_sided(t, n - 2), 6), 'n': n,
                        'guc': 'güçlü' if abs(r) >= 0.7 else 'orta' if abs(r) >= 0.4 else 'zayıf'})
    out.sort(key=lambda o: -abs(o['pearson']))
    matrix = [[a] + [round(pearson(nums[a], nums[b])[0] or 0, 2) if a != b else 1 for b in names] for a in names]
    return Result(f"{len(out)} çift; en güçlü: {out[0]['cift']} (r={out[0]['pearson']})", list(out[0]), out, {'cift': len(out), 'en_guclu': out[0]['cift']},
                  tables=[('Pearson matrisi', [''] + names, matrix)], notes=['Korelasyon nedensellik göstermez; ortak bir üçüncü etken olabilir.'])


@op('aykiri-deger', title='Aykırı değerleri bul (IQR ve robust z)', domain=D, product=P, tag='aykırı değer', samples=PAZ,
    description='Seçilen sayısal sütunlarda Tukey çitleri (Q1−1,5×IQR, Q3+1,5×IQR) ve medyan mutlak sapmaya dayalı robust z-skoru (|z|>3,5) ile aykırı değerleri satır numarasıyla listeler; değerleri değiştirmez.',
    input='CSV/XLSX', output='sonuc.csv (aykırı hücreler)', command='python main.py --columns satis --method iqr',
    args=[arg('--columns', help='Varsayılan: tüm sayısal sütunlar'), arg('--method', choices=['iqr', 'robust', 'ikisi'], default='ikisi'), arg('--k', type=float, default=1.5)],
    expect={'aykiri': 1})
def aykiri(columns, rows, args):
    nums = numeric_columns(columns, rows, col_list(args.columns, columns, required=False) or None)
    out = []
    for c, vals in nums.items():
        v = sorted(x for x in vals if x is not None)
        q1, q3, med = quantile(v, .25), quantile(v, .75), quantile(v, .5)
        iqr = q3 - q1
        mad = quantile(sorted(abs(x - med) for x in v), .5)
        for i, x in enumerate(vals, start=2):
            if x is None:
                continue
            by_iqr = x < q1 - args.k * iqr or x > q3 + args.k * iqr
            rz = 0.6745 * (x - med) / mad if mad else 0.0
            by_rz = abs(rz) > 3.5
            hit = by_iqr and by_rz if args.method == 'ikisi' else by_iqr if args.method == 'iqr' else by_rz
            if hit:
                out.append({'satir': i, columns[0]: rows[i - 2][columns[0]], 'sutun': c, 'deger': fmt(x), 'alt_sinir': fmt(round(q1 - args.k * iqr, 2)),
                            'ust_sinir': fmt(round(q3 + args.k * iqr, 2)), 'robust_z': round(rz, 2)})
    return Result(f'{len(out)} aykırı değer', ['satir', columns[0], 'sutun', 'deger', 'alt_sinir', 'ust_sinir', 'robust_z'], out, {'aykiri': len(out)},
                  notes=['“ikisi” yöntemi yalnızca iki ölçütün de işaretlediği değerleri listeler (daha az yanlış alarm).'])


@op('histogram', title='Frekans dağılımı ve histogram', domain=D, product=P, tag='histogram', samples=PAZ,
    description='Sayısal bir sütunu eşit genişlikte aralıklara (Freedman–Diaconis veya istenen sayıda) böler; her aralığın adedini, yüzdesini ve kümülatif yüzdesini verir, raporda metin grafiği çizer. Kategorik sütunlarda frekans tablosu üretir.',
    input='CSV/XLSX + sütun', output='sonuc.csv (aralık tablosu)', command='python main.py --column satis --bins 10',
    args=[arg('--column', default='satis'), arg('--bins', type=int, default=0, help='0: otomatik (Freedman–Diaconis)')], expect={'aralik': 10, 'n': 52})
def histogram(columns, rows, args):
    need(args.column, columns, '--column')
    vals = [v for v in numbers(rows, args.column) if v is not None]
    if len(vals) < 0.95 * sum(bool(r[args.column].strip()) for r in rows):
        counts = Counter(r[args.column].strip() for r in rows if r[args.column].strip())
        out = [{'deger': k, 'adet': n, 'yuzde': round(100 * n / sum(counts.values()), 2)} for k, n in counts.most_common()]
        return Result(f'{len(out)} farklı değer (kategorik)', ['deger', 'adet', 'yuzde'], out, {'aralik': len(out), 'n': sum(counts.values())})
    v = sorted(vals)
    iqr = quantile(v, .75) - quantile(v, .25)
    bins = args.bins or max(1, min(50, math.ceil((v[-1] - v[0]) / (2 * iqr * len(v) ** (-1 / 3))) if iqr else 10))
    width = (v[-1] - v[0]) / bins or 1
    counts = [0] * bins
    for x in v:
        counts[min(bins - 1, int((x - v[0]) / width))] += 1
    out, cum = [], 0
    for i, n in enumerate(counts):
        cum += n
        out.append({'alt': round(v[0] + i * width, 2), 'ust': round(v[0] + (i + 1) * width, 2), 'adet': n, 'yuzde': round(100 * n / len(v), 2),
                    'kumulatif_yuzde': round(100 * cum / len(v), 2), 'grafik': '█' * round(30 * n / max(counts))})
    return Result(f'{len(v)} değer {bins} aralıkta', list(out[0]), out, {'aralik': bins, 'n': len(v)})


@op('capraz-tablo', title='Çapraz tablo ve ki-kare bağımsızlık testi', domain=D, product=P, tag='ki-kare', samples={'web_olaylari.csv': 'web_olaylari.csv'},
    description='İki kategorik sütunun çapraz frekans tablosunu (adet ve satır yüzdesi) çıkarır; ki-kare bağımsızlık testi, serbestlik derecesi, p-değeri ve Cramér’s V etki büyüklüğünü hesaplar. Beklenen frekansı 5’in altında olan hücreleri uyarır.',
    input='CSV/XLSX + iki kategorik sütun', output='sonuc.csv (tablo) + test sonucu', command='python main.py --rows cihaz --cols olay',
    args=[arg('--rows', default='cihaz'), arg('--cols', default='olay')], expect={'satir_kategori': 2, 'sutun_kategori': 5, 'serbestlik': 4})
def capraz(columns, rows, args):
    a, b = need(args.rows, columns, '--rows'), need(args.cols, columns, '--cols')
    table = Counter((r[a].strip(), r[b].strip()) for r in rows if r[a].strip() and r[b].strip())
    ra = sorted({k[0] for k in table}, key=tr_sort_key)
    cb = sorted({k[1] for k in table}, key=tr_sort_key)
    n = sum(table.values())
    rt = {x: sum(table[(x, y)] for y in cb) for x in ra}
    ct = {y: sum(table[(x, y)] for x in ra) for y in cb}
    chi, low = 0.0, 0
    for x in ra:
        for y in cb:
            e = rt[x] * ct[y] / n
            low += e < 5
            chi += (table[(x, y)] - e) ** 2 / e if e else 0
    dof = (len(ra) - 1) * (len(cb) - 1)
    p = chi2_sf(chi, dof) if dof else 1.0
    v = math.sqrt(chi / (n * max(1, min(len(ra), len(cb)) - 1)))
    out = [{a: x, **{y: table[(x, y)] for y in cb}, 'toplam': rt[x], **{f'{y}_%': round(100 * table[(x, y)] / rt[x], 1) for y in cb}} for x in ra]
    notes = [f'{low} hücrede beklenen frekans 5’ten küçük; test sonucu güvenilir olmayabilir.'] if low else []
    return Result(f'χ²={chi:.2f}, sd={dof}, p={p:.4f} → {stars(p)}; Cramér V={v:.3f}', list(out[0]), out,
                  {'satir_kategori': len(ra), 'sutun_kategori': len(cb), 'ki_kare': round(chi, 3), 'serbestlik': dof, 'p_degeri': round(p, 6), 'cramer_v': round(v, 4)}, notes=notes)


@op('ab-testi', title='A/B testi: dönüşüm oranı ve gelir karşılaştırması', domain=D, product=P, tag='A/B test', samples={'ab_testi.csv': 'ab_testi.csv'},
    description='İki grubun dönüşüm oranını iki oran z-testiyle, ziyaretçi başı geliri Welch t-testiyle karşılaştırır; fark, göreli artış (lift), %95 güven aralığı ve p-değeri verir. Örneklem yetersizse bunu açıkça söyler.',
    input='Ziyaretçi bazlı CSV/XLSX (grup, dönüşüm 0/1, isteğe bağlı gelir)', output='sonuc.csv (grup özeti) + test sonucu', command='python main.py --group grup --converted donusum --revenue gelir',
    args=[arg('--group', default='grup'), arg('--converted', default='donusum'), arg('--revenue', default='gelir'), arg('--control', default='A')],
    expect={'grup': 2, 'kontrol_n': 2000, 'deney_n': 2000})
def ab(columns, rows, args):
    g, c = need(args.group, columns, '--group'), need(args.converted, columns, '--converted')
    conv = numbers(rows, c)
    rev = numbers(rows, args.revenue) if args.revenue in columns else [None] * len(rows)
    groups = defaultdict(lambda: {'n': 0, 'x': 0, 'rev': []})
    for r, x, m in zip(rows, conv, rev):
        k = r[g].strip()
        groups[k]['n'] += 1
        groups[k]['x'] += 1 if x else 0
        groups[k]['rev'].append(m or 0.0)
    if len(groups) != 2:
        raise ValueError(f'Tam iki grup olmalı; bulunan: {", ".join(groups)}')
    ctrl = args.control if args.control in groups else sorted(groups)[0]
    test = next(k for k in groups if k != ctrl)
    A, B = groups[ctrl], groups[test]
    pa, pb = A['x'] / A['n'], B['x'] / B['n']
    pool = (A['x'] + B['x']) / (A['n'] + B['n'])
    se0 = math.sqrt(pool * (1 - pool) * (1 / A['n'] + 1 / B['n']))
    z = (pb - pa) / se0 if se0 else 0.0
    p = 2 * (1 - N.cdf(abs(z)))
    se = math.sqrt(pa * (1 - pa) / A['n'] + pb * (1 - pb) / B['n'])
    lo, hi = pb - pa - 1.96 * se, pb - pa + 1.96 * se
    summary = {'grup': 2, 'kontrol': ctrl, 'deney': test, 'kontrol_n': A['n'], 'deney_n': B['n'], 'kontrol_oran': round(pa, 5), 'deney_oran': round(pb, 5),
               'lift_%': round(100 * (pb - pa) / pa, 2) if pa else None, 'z': round(z, 3), 'p_degeri': round(p, 5), 'fark_ga_alt': round(lo, 5), 'fark_ga_ust': round(hi, 5)}
    notes = []
    if args.revenue in columns:
        ma, mb = sum(A['rev']) / A['n'], sum(B['rev']) / B['n']
        va = sum((x - ma) ** 2 for x in A['rev']) / (A['n'] - 1)
        vb = sum((x - mb) ** 2 for x in B['rev']) / (B['n'] - 1)
        se_t = math.sqrt(va / A['n'] + vb / B['n'])
        t = (mb - ma) / se_t if se_t else 0.0
        df = (va / A['n'] + vb / B['n']) ** 2 / ((va / A['n']) ** 2 / (A['n'] - 1) + (vb / B['n']) ** 2 / (B['n'] - 1)) if se_t else 0
        summary.update(gelir_kontrol=round(ma, 2), gelir_deney=round(mb, 2), gelir_p=round(t_two_sided(t, df), 5))
    if min(A['x'], B['x'], A['n'] - A['x'], B['n'] - B['x']) < 10:
        notes.append('Bazı hücrelerde 10’dan az gözlem var; normal yaklaşım güvenilir değil.')
    notes.append('Testi önceden belirlenen örneklem büyüklüğüne ulaşmadan durdurmak (peeking) yanlış pozitif oranını artırır.')
    out = [{'grup': k, 'ziyaretci': v['n'], 'donusum': v['x'], 'oran_%': round(100 * v['x'] / v['n'], 3), 'ziyaretci_basi_gelir': round(sum(v['rev']) / v['n'], 2)}
           for k, v in ((ctrl, A), (test, B))]
    return Result(f"{test} grubunda dönüşüm %{100 * pb:.2f} vs %{100 * pa:.2f} (lift %{summary['lift_%']}); p={p:.4f} → {stars(p)}", list(out[0]), out, summary, notes=notes)


@op('orneklem-buyuklugu', title='A/B testi için örneklem büyüklüğü', domain=D, product=P, tag='örneklem', kind='none', samples={},
    description='Mevcut dönüşüm oranı, yakalanmak istenen en küçük fark, anlamlılık düzeyi ve güçten grup başına gereken ziyaretçi sayısını ve tahmini süreyi hesaplar. Testi başlatmadan önce yeterli trafik olup olmadığını gösterir.',
    input='Taban oran, en küçük fark, α, güç, günlük trafik', output='rapor + sonuc.csv (fark senaryoları)', command='python main.py --baseline 0.05 --mde 0.01 --daily 4000',
    args=[arg('--baseline', type=float, default=0.05), arg('--mde', type=float, default=0.01, help='Mutlak fark (0.01 = 1 puan)'), arg('--alpha', type=float, default=0.05),
          arg('--power', type=float, default=0.8), arg('--daily', type=int, default=4000, help='Teste giren günlük toplam ziyaretçi')],
    expect={'grup_basina': 8158})
def orneklem(args):
    if not 0 < args.baseline < 1 or args.mde <= 0 or args.baseline + args.mde >= 1:
        raise ValueError('Taban oran 0-1 arasında olmalı ve fark pozitif olmalı.')

    def size(mde):
        p1, p2 = args.baseline, args.baseline + mde
        pbar = (p1 + p2) / 2
        za, zb = N.inv_cdf(1 - args.alpha / 2), N.inv_cdf(args.power)
        return math.ceil((za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / mde ** 2)
    n = size(args.mde)
    out = [{'fark': round(m, 4), 'grup_basina': size(m), 'toplam': 2 * size(m), 'gun': math.ceil(2 * size(m) / args.daily)}
           for m in sorted({args.mde / 2, args.mde * 0.75, args.mde, args.mde * 1.5, args.mde * 2}) if args.baseline + m < 1]
    return Result(f'Grup başına {n:,} ziyaretçi (toplam {2 * n:,}); günlük {args.daily:,} trafikle ≈ {math.ceil(2 * n / args.daily)} gün'.replace(',', '.'),
                  list(out[0]), out, {'grup_basina': n, 'toplam': 2 * n, 'gun': math.ceil(2 * n / args.daily)},
                  notes=['İki yönlü test, eşit grup büyüklüğü varsayılmıştır. Haftalık döngüyü yakalamak için en az bir tam hafta çalıştırın.'])


def solve(a, b):
    """Gaussian elimination for small dense systems."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ValueError('Değişkenler arasında tam doğrusal bağımlılık var (çoklu doğrusallık).')
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def inverse(a):
    n = len(a)
    return [list(col) for col in zip(*[solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)])]


@op('dogrusal-regresyon', title='Çoklu doğrusal regresyon (EKK)', domain=D, product=P, tag='regresyon', samples=PAZ,
    description='Bağımlı değişkeni bir veya birkaç açıklayıcı değişkenle en küçük kareler yöntemiyle modeller; katsayılar, standart hatalar, t ve p-değerleri, R², düzeltilmiş R² ve her satırın tahmin/artığını verir. Basit etki tahmini içindir; nedensellik kurmaz.',
    input='CSV/XLSX + hedef ve açıklayıcı sütunlar', output='sonuc.csv (tahmin ve artıklar) + katsayı tablosu', command='python main.py --y satis --x reklam_harcamasi,indirim_orani',
    args=[arg('--y', default='satis'), arg('--x', default='reklam_harcamasi,indirim_orani')], expect={'n': 52, 'degisken': 2})
def regresyon(columns, rows, args):
    xs = col_list(args.x, columns, '--x')
    y = numbers(rows, need(args.y, columns, '--y'))
    xv = {c: numbers(rows, c) for c in xs}
    data = [(y[i], [1.0] + [xv[c][i] for c in xs]) for i in range(len(rows)) if y[i] is not None and all(xv[c][i] is not None for c in xs)]
    n, k = len(data), len(xs) + 1
    if n <= k + 1:
        raise ValueError('Gözlem sayısı değişken sayısından fazla olmalı.')
    xtx = [[sum(d[1][i] * d[1][j] for d in data) for j in range(k)] for i in range(k)]
    xty = [sum(d[1][i] * d[0] for d in data) for i in range(k)]
    beta = solve(xtx, xty)
    pred = [sum(b * x for b, x in zip(beta, d[1])) for d in data]
    resid = [d[0] - p for d, p in zip(data, pred)]
    ybar = sum(d[0] for d in data) / n
    sse, sst = sum(e * e for e in resid), sum((d[0] - ybar) ** 2 for d in data)
    r2 = 1 - sse / sst if sst else 0.0
    sigma2 = sse / (n - k)
    inv = inverse(xtx)
    coef = []
    for i, name in enumerate(['sabit'] + xs):
        se = math.sqrt(max(0.0, sigma2 * inv[i][i]))
        t = beta[i] / se if se else 0.0
        coef.append([name, round(beta[i], 4), round(se, 4), round(t, 3), round(t_two_sided(t, n - k), 5)])
    out = [{'satir': i + 2, 'gercek': round(d[0], 4), 'tahmin': round(p, 4), 'artik': round(e, 4)} for i, (d, p, e) in enumerate(zip(data, pred, resid))]
    return Result(f'R²={r2:.3f}, düzeltilmiş R²={1 - (1 - r2) * (n - 1) / (n - k):.3f}, n={n}', ['satir', 'gercek', 'tahmin', 'artik'], out,
                  {'n': n, 'degisken': len(xs), 'r2': round(r2, 4), 'duzeltilmis_r2': round(1 - (1 - r2) * (n - 1) / (n - k), 4),
                   'katsayilar': {c[0]: c[1] for c in coef}},
                  tables=[('Katsayılar', ['Değişken', 'Katsayı', 'Std. hata', 't', 'p'], coef)],
                  notes=['Artıkların büyük olduğu satırlar aykırı değer olabilir; aykiri-deger bloğuyla kontrol edin.'])


def decompose(vals, m):
    n = len(vals)
    trend = [None] * n
    half = m // 2
    for i in range(half, n - half):
        window = vals[i - half:i + half + 1]
        trend[i] = (sum(window) - (window[0] + window[-1]) / 2) / m if m % 2 == 0 else sum(window) / m
    ratios = defaultdict(list)
    for i, (v, t) in enumerate(zip(vals, trend)):
        if t:
            ratios[i % m].append(v / t)
    raw = {s: sorted(r)[len(r) // 2] if r else 1.0 for s, r in ratios.items()}
    norm = m / sum(raw.get(s, 1.0) for s in range(m))
    return trend, [raw.get(s, 1.0) * norm for s in range(m)]


@op('mevsimsellik-ayristir', title='Zaman serisini trend ve mevsimselliğe ayır', domain=D, product=P, tag='mevsimsellik', samples=SERI, args=SERIES_ARGS,
    description='Aylık/haftalık seriyi klasik çarpımsal ayrıştırmayla merkezi hareketli ortalama trendine, mevsim endekslerine ve düzensiz bileşene ayırır; mevsimsellikten arındırılmış seriyi üretir. “Kasım her yıl %17 yüksek” gibi bulguları sayıya döker.',
    input='Dönem ve değer sütunu (en az 2 tam mevsim)', output='sonuc.csv (bileşenler) + mevsim endeksleri', command='python main.py --period ay --value satis --season 12',
    expect={'donem': 36, 'en_guclu_mevsim': 12})
def mevsim(columns, rows, args):
    labels, vals = series(columns, rows, args)
    if len(vals) < 2 * args.season:
        raise ValueError(f'En az iki tam mevsim ({2 * args.season} dönem) gerekir.')
    trend, idx = decompose(vals, args.season)
    out = [{'donem': l, 'deger': v, 'trend': round(t, 2) if t else '', 'mevsim_endeksi': round(idx[i % args.season], 4),
            'arindirilmis': round(v / idx[i % args.season], 2), 'duzensiz': round(v / (t * idx[i % args.season]), 4) if t else ''}
           for i, (l, v, t) in enumerate(zip(labels, vals, trend))]
    strongest = max(range(args.season), key=lambda s: idx[s]) + 1
    return Result(f'{len(vals)} dönem ayrıştırıldı; en yüksek mevsim {strongest}. dönem (endeks {max(idx):.3f})', list(out[0]), out,
                  {'donem': len(vals), 'en_guclu_mevsim': strongest, 'en_zayif_mevsim': min(range(args.season), key=lambda s: idx[s]) + 1},
                  tables=[('Mevsim endeksleri', ['Mevsim', 'Endeks'], [[s + 1, round(v, 3)] for s, v in enumerate(idx)])],
                  notes=['Mevsim sırası serinin ilk döneminden başlar; ilk dönem Ocak ise 12 = Aralık.'])


@op('basit-tahmin', title='Basit tahmin ve geriye dönük doğruluk testi', domain=D, product=P, tag='tahmin', samples=SERI, args=SERIES_ARGS + [arg('--horizon', type=int, default=6), arg('--holdout', type=int, default=6)],
    description='Mevsimsel naif, hareketli ortalama ve “doğrusal trend × mevsim endeksi” yöntemlerini son dönemleri saklayarak sınar (MAPE), en isabetliyle sonraki dönemleri tahmin eder. Bütçe öncesi hızlı ve açıklanabilir bir başlangıç noktasıdır.',
    input='Dönem ve değer sütunu', output='sonuc.csv (tahminler) + yöntem karşılaştırması', command='python main.py --horizon 6 --holdout 6',
    expect={'tahmin_donemi': 6})
def tahmin(columns, rows, args):
    labels, vals = series(columns, rows, args)
    m = args.season

    def methods(hist, h):
        out = {'mevsimsel naif': [hist[-m + (i % m)] for i in range(h)] if len(hist) >= m else None,
               'hareketli ortalama (3)': [sum(hist[-3:]) / 3] * h}
        if len(hist) >= 2 * m:
            trend, idx = decompose(hist, m)
            pts = [(i, hist[i] / idx[i % m]) for i in range(len(hist))]
            mx, my = sum(i for i, _ in pts) / len(pts), sum(v for _, v in pts) / len(pts)
            slope = sum((i - mx) * (v - my) for i, v in pts) / sum((i - mx) ** 2 for i, _ in pts)
            out['trend × mevsim'] = [(my + slope * (len(hist) + j - mx)) * idx[(len(hist) + j) % m] for j in range(h)]
        return {k: v for k, v in out.items() if v}
    hist, test = vals[:-args.holdout], vals[-args.holdout:]
    scores = {k: sum(abs(a - f) / abs(a) for a, f in zip(test, fc)) / len(test) * 100 for k, fc in methods(hist, args.holdout).items() if all(test)}
    best = min(scores, key=scores.get)
    fc = methods(vals, args.horizon)[best]
    last = labels[-1]
    out = []
    for j, v in enumerate(fc, start=1):
        if len(last) == 7 and last[4] == '-':
            y, mth = int(last[:4]), int(last[5:]) + j
            label = f'{y + (mth - 1) // 12}-{(mth - 1) % 12 + 1:02d}'
        else:
            label = f'+{j}'
        out.append({'donem': label, 'tahmin': round(v, 2), 'yontem': best})
    return Result(f'En isabetli yöntem: {best} (MAPE %{scores[best]:.1f}); {args.horizon} dönem tahmin edildi', ['donem', 'tahmin', 'yontem'], out,
                  {'tahmin_donemi': len(out), 'yontem': best, 'mape': round(scores[best], 2)},
                  tables=[('Geriye dönük test (son dönemler saklandı)', ['Yöntem', 'MAPE %'], [[k, round(v, 2)] for k, v in sorted(scores.items(), key=lambda kv: kv[1])])],
                  notes=['Tahmin aralığı verilmez; kampanya, fiyat ve kur etkileri modele dahil değildir.'])


@op('seri-anomali', title='Zaman serisinde anormal dönemleri bul', domain=D, product=P, tag='anomali', samples=SERI, args=SERIES_ARGS + [arg('--threshold', type=float, default=3.5)],
    description='Seriyi mevsimsellikten arındırıp yerel medyan ve medyan mutlak sapmayla (robust z) karşılaştırır; beklenenden anlamlı sapan dönemleri yön ve büyüklüğüyle listeler. Veri yükleme hatası, kayıp gün veya gerçek iş olayı ayrımı için başlangıç noktasıdır.',
    input='Dönem ve değer sütunu', output='sonuc.csv (tüm dönemler, işaretli)', command='python main.py --threshold 3.5',
    expect={'anomali': 1, 'ilk_anomali': '2025-06'})
def anomali(columns, rows, args):
    labels, vals = series(columns, rows, args)
    adj = vals
    if len(vals) >= 2 * args.season:
        _, idx = decompose(vals, args.season)
        adj = [v / idx[i % args.season] for i, v in enumerate(vals)]
    # Local median as the expected level; the spread comes from all residuals, so a series edge with few
    # neighbours does not produce a tiny MAD and a false alarm.
    # Expected level: Theil–Sen line through up to 12 neighbours (robust to the anomaly itself and to trend at the edges).
    meds = []
    for i in range(len(adj)):
        lo = max(0, min(i - 6, len(adj) - 13))
        idx_w = [j for j in range(lo, min(len(adj), lo + 13)) if j != i]
        slopes = sorted((adj[b] - adj[a]) / (b - a) for k, a in enumerate(idx_w) for b in idx_w[k + 1:])
        slope = quantile(slopes, .5) or 0.0
        level = quantile(sorted(adj[j] - slope * j for j in idx_w), .5)
        meds.append(level + slope * i)
    resid = [(v - m) / m if m else 0.0 for v, m in zip(adj, meds)]
    mad = quantile(sorted(abs(r - quantile(sorted(resid), .5)) for r in resid), .5) or 1e-9
    out, hits = [], []
    for i, v in enumerate(adj):
        med = meds[i]
        z = 0.6745 * resid[i] / mad
        flag = abs(z) > args.threshold
        if flag:
            hits.append(labels[i])
        out.append({'donem': labels[i], 'deger': vals[i], 'beklenen': round(med * (vals[i] / v if v else 1), 2), 'robust_z': round(z, 2),
                    'anomali': ('düşüş' if z < 0 else 'artış') if flag else ''})
    return Result(f'{len(hits)} anormal dönem: {", ".join(hits) or "—"}', list(out[0]), out, {'anomali': len(hits), 'ilk_anomali': hits[0] if hits else ''})


@op('yuzdelik-dilim', title='Yüzdelik dilimlere ayır (çeyrek, ondalık)', domain=D, product=P, tag='dilim', samples=PAZ,
    description='Sayısal bir sütunu eşit adetli dilimlere (çeyrek, beşli, ondalık…) ayırır; her satıra dilim numarasını ekler ve dilim başına adet, sınır, ortalama ve toplam payını verir. Müşteri/ürün segmentasyonu ve hedef belirleme için.',
    input='CSV/XLSX + sayısal sütun', output='sonuc.csv (dilim no eklenmiş)', command='python main.py --column satis --groups 10',
    args=[arg('--column', default='satis'), arg('--groups', type=int, default=4)], expect={'dilim': 4})
def dilim(columns, rows, args):
    vals = numbers(rows, need(args.column, columns, '--column'))
    order = sorted((v, i) for i, v in enumerate(vals) if v is not None)
    tile = {}
    for rank, (v, i) in enumerate(order):
        tile[i] = rank * args.groups // len(order) + 1
    out = [{**r, 'dilim': tile.get(i, '')} for i, r in enumerate(rows)]
    total = sum(v for v, _ in order)
    table = []
    for g in range(1, args.groups + 1):
        members = [vals[i] for i, t in tile.items() if t == g]
        if members:
            table.append([g, len(members), fmt(round(min(members), 2)), fmt(round(max(members), 2)), fmt(round(sum(members) / len(members), 2)), round(100 * sum(members) / total, 1) if total else ''])
    return Result(f'{len(order)} değer {args.groups} dilime ayrıldı', columns + ['dilim'], out, {'dilim': len(table)},
                  tables=[('Dilimler', ['Dilim', 'Adet', 'En küçük', 'En büyük', 'Ortalama', 'Toplam payı %'], table)])


@op('olcekle', title='Sayısal sütunları ölçekle (min-max, z-skoru)', domain=D, product=P, tag='ölçekleme', samples=PAZ,
    description='Seçilen sayısal sütunları 0-1 aralığına (min-max), ortalama 0 / standart sapma 1’e (z-skoru) veya medyan/IQR’a göre (robust) ölçekler; kullanılan parametreleri yeni veriye uygulamak için ayrı dosyaya yazar.',
    input='CSV/XLSX + sütunlar', output='sonuc.csv + olcek-parametreleri.csv', command='python main.py --columns reklam_harcamasi,ziyaret --method zskor',
    args=[arg('--columns', default='reklam_harcamasi,ziyaret,satis'), arg('--method', choices=['minmax', 'zskor', 'robust'], default='minmax')],
    expect={'olceklenen_sutun': 3})
def olcekle(columns, rows, args):
    cols = col_list(args.columns, columns)
    out, params = [dict(r) for r in rows], []
    for c in cols:
        vals = numbers(rows, c)
        v = sorted(x for x in vals if x is not None)
        if not v:
            raise ValueError(f'"{c}" sayısal değil.')
        if args.method == 'minmax':
            a, b = v[0], (v[-1] - v[0]) or 1
        elif args.method == 'zskor':
            d = describe(v)
            a, b = d['ortalama'], d['std'] or 1
        else:
            a, b = quantile(v, .5), (quantile(v, .75) - quantile(v, .25)) or 1
        for r, x in zip(out, vals):
            r[f'{c}_olcekli'] = round((x - a) / b, 6) if x is not None else ''
        params.append({'sutun': c, 'yontem': args.method, 'merkez': a, 'olcek': b})
    return Result(f'{len(cols)} sütun {args.method} ile ölçeklendi', columns + [f'{c}_olcekli' for c in cols], out, {'olceklenen_sutun': len(cols)},
                  files={'olcek-parametreleri.csv': (['sutun', 'yontem', 'merkez', 'olcek'], params)})


@op('pareto-analizi', title='Pareto (80/20) analizi', domain=D, product=P, tag='Pareto', samples={'siparis_satirlari.csv': 'siparis_satirlari.csv'},
    description='Bir ölçünün (ciro, şikâyet, arıza süresi) kalemlere göre toplamını büyükten küçüğe sıralar, kümülatif payı hesaplar ve toplamın %80’ini kaç kalemin oluşturduğunu gösterir.',
    input='CSV/XLSX + kalem ve ölçü sütunu', output='sonuc.csv (sıralı kümülatif tablo)', command='python main.py --item urun_adi --value tutar --cutoff 80',
    args=[arg('--item', default='urun_adi'), arg('--value', default='tutar'), arg('--cutoff', type=float, default=80.0)],
    expect={'kalem': 9, 'esik_kalem': 5})
def pareto(columns, rows, args):
    vals = numbers(rows, need(args.value, columns, '--value'))
    totals = defaultdict(float)
    for r, v in zip(rows, vals):
        totals[r[need(args.item, columns, '--item')].strip() or '(boş)'] += v or 0.0
    grand = sum(totals.values())
    out, cum, cut = [], 0.0, None
    for i, (k, v) in enumerate(sorted(totals.items(), key=lambda kv: -kv[1]), start=1):
        cum += v
        share = 100 * cum / grand if grand else 0
        if cut is None and share >= args.cutoff:
            cut = i
        out.append({'sira': i, args.item: k, 'toplam': round(v, 2), 'pay_%': round(100 * v / grand, 2) if grand else 0, 'kumulatif_%': round(share, 2)})
    return Result(f'{len(out)} kalemin {cut} tanesi ({100 * cut / len(out):.0f}%) toplamın %{args.cutoff:.0f}’ini oluşturuyor', list(out[0]), out,
                  {'kalem': len(out), 'esik_kalem': cut, 'esik_kalem_%': round(100 * cut / len(out), 1)})


@op('guven-araligi', title='Ortalama ve medyan için güven aralığı (bootstrap)', domain=D, product=P, tag='güven aralığı', samples=PAZ,
    description='Bir sütunun ortalaması için t-dağılımı ve bootstrap, medyanı için bootstrap %95 güven aralığı hesaplar (tohumlu, tekrarlanabilir). “Ortalama sepet 1.250 TL” yerine “1.180–1.320 TL arası” demek için.',
    input='CSV/XLSX + sayısal sütun', output='rapor + sonuc.csv', command='python main.py --column satis --resamples 5000',
    args=[arg('--column', default='satis'), arg('--resamples', type=int, default=4000), arg('--level', type=float, default=0.95), arg('--seed', type=int, default=42)],
    expect={'n': 52})
def guven(columns, rows, args):
    v = [x for x in numbers(rows, need(args.column, columns, '--column')) if x is not None]
    if len(v) < 5:
        raise ValueError('En az 5 değer gerekir.')
    rng = random.Random(args.seed)
    d = describe(v)
    alpha = 1 - args.level
    lo_t = hi_t = None
    # t critical value by bisection on the two-sided p
    lo, hi = 0.0, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if t_two_sided(mid, len(v) - 1) > alpha else (lo, mid)
    half = hi * d['std'] / math.sqrt(len(v))
    lo_t, hi_t = d['ortalama'] - half, d['ortalama'] + half
    means, medians = [], []
    for _ in range(args.resamples):
        s = sorted(rng.choice(v) for _ in v)
        means.append(sum(s) / len(s))
        medians.append(quantile(s, .5))
    means.sort()
    medians.sort()
    q = lambda arr: (quantile(arr, alpha / 2), quantile(arr, 1 - alpha / 2))
    out = [{'olcu': 'ortalama (t)', 'tahmin': round(d['ortalama'], 2), 'alt': round(lo_t, 2), 'ust': round(hi_t, 2)},
           {'olcu': 'ortalama (bootstrap)', 'tahmin': round(d['ortalama'], 2), 'alt': round(q(means)[0], 2), 'ust': round(q(means)[1], 2)},
           {'olcu': 'medyan (bootstrap)', 'tahmin': round(d['medyan'], 2), 'alt': round(q(medians)[0], 2), 'ust': round(q(medians)[1], 2)}]
    return Result(f"Ortalama {fmt(round(d['ortalama'], 2))}; %{100 * args.level:.0f} GA {fmt(round(lo_t, 2))} – {fmt(round(hi_t, 2))}", list(out[0]), out,
                  {'n': len(v), 'ortalama': round(d['ortalama'], 2), 'ga_alt': round(lo_t, 2), 'ga_ust': round(hi_t, 2)})


@op('normallik-testi', title='Sayısal sütunlarda normallik: çarpıklık, basıklık ve Jarque-Bera testi', domain=D, product=P, tag='normallik', samples={'olcumler.csv': 'olcumler.csv'},
    args=[arg('--columns', default='', help='Test edilecek sütunlar (boşsa tüm sayısal sütunlar)'), arg('--alpha', type=float, default=0.05)],
    description='Her sayısal sütun için çarpıklığı, fazla basıklığı ve Jarque-Bera istatistiğini hesaplar; p-değeri anlamlılık düzeyinin altındaysa normal dağılım varsayımını reddeder ve ortalama/medyan farkıyla yönü (sağa/sola çarpık, kalın kuyruk) yazar. t-testi, ANOVA veya doğrusal regresyon öncesi varsayım kontrolü ve parametrik/parametrik olmayan test seçimi için.',
    input='CSV/XLSX + sayısal sütunlar', output='sonuc.csv (sütun bazında normallik)', command='python main.py --input olcum.csv --columns sure,tutar --alpha 0.01',
    expect={'sutun': 2, 'normal_degil': 1})
def normality(columns, rows, args):
    cols = col_list(args.columns, columns, '--columns') if args.columns else columns
    out = []
    for c in cols:
        vals = [v for v in numbers(rows, c) if v is not None]
        filled = sum(bool(r[c].strip()) for r in rows)
        if len(vals) < 8 or len(vals) < 0.8 * max(filled, 1):
            continue
        n = len(vals)
        m = sum(vals) / n
        m2 = sum((x - m) ** 2 for x in vals) / n
        if m2 == 0:
            continue
        skew = sum((x - m) ** 3 for x in vals) / n / m2 ** 1.5
        kurt = sum((x - m) ** 4 for x in vals) / n / m2 ** 2 - 3
        jb = n / 6 * (skew ** 2 + kurt ** 2 / 4)
        p = math.exp(-jb / 2)
        med = sorted(vals)[n // 2] if n % 2 else sum(sorted(vals)[n // 2 - 1:n // 2 + 1]) / 2
        shape = ('sağa çarpık' if skew > 0.5 else 'sola çarpık' if skew < -0.5 else 'simetrik') + (', kalın kuyruklu' if kurt > 1 else ', ince kuyruklu' if kurt < -1 else '')
        out.append({'sutun': c, 'n': n, 'ortalama': round(m, 4), 'medyan': round(med, 4), 'carpiklik': round(skew, 4), 'basiklik': round(kurt, 4), 'jarque_bera': round(jb, 4),
                    'p_degeri': round(p, 6), 'sonuc': 'normal değil' if p < args.alpha else 'normal reddedilemez', 'sekil': shape})
    if not out:
        raise ValueError('En az 8 sayısal değeri olan sütun yok.')
    out.sort(key=lambda o: o['p_degeri'])
    bad = sum(o['sonuc'] == 'normal değil' for o in out)
    return Result(f'{len(out)} sütun; {bad} sütunda normallik reddedildi (α={fmt(args.alpha)})', list(out[0]), out, {'sutun': len(out), 'normal_degil': bad},
                  notes=['Jarque-Bera büyük örneklemde küçük sapmaları da anlamlı bulur, küçük örneklemde (n < 30) gücü düşüktür; histogram bloğuyla dağılıma bakın. Normal değilse Mann-Whitney gibi parametrik olmayan testleri kullanın.'])


@op('mann-whitney', title='İki grup için Mann-Whitney U testi (parametrik olmayan) ve etki büyüklüğü', domain=D, product=P, tag='Mann-Whitney', samples={'teslim_sureleri.csv': 'teslim_sureleri.csv'},
    args=[arg('--group', default='depo'), arg('--value', default='sure_saat'), arg('--a', default='', help='Birinci grup (boşsa alfabetik ilk)'), arg('--b', default='', help='İkinci grup')],
    description='Normal dağılmayan veya aykırı değer içeren ölçümlerde (teslim süresi, sepet tutarı, bekleme süresi) iki grubun dağılımını sıralara dayalı Mann-Whitney U testiyle karşılaştırır; bağ (tie) düzeltmeli normal yaklaşımla iki yönlü p-değeri, medyanları, üstünlük olasılığını (A grubundan rastgele bir değerin B’den büyük olma olasılığı) ve r etki büyüklüğünü verir.',
    input='Grup ve ölçüm sütunu olan CSV/XLSX', output='sonuc.csv (grup özeti) + test sonucu', command='python main.py --group kanal --value sepet --a web --b magaza',
    expect={'n_a': 12, 'n_b': 12, 'u': 7.5, 'p_degeri': 0.0002, 'ustunluk': 0.0521})
def mann_whitney(columns, rows, args):
    g, v = need(args.group, columns, '--group'), need(args.value, columns, '--value')
    groups = defaultdict(list)
    for r, x in zip(rows, numbers(rows, v)):
        if x is not None and r[g].strip():
            groups[r[g].strip()].append(x)
    names = sorted(groups)
    a = args.a or (names[0] if names else '')
    b = args.b or next((n for n in names if n != a), '')
    if a not in groups or b not in groups:
        raise ValueError(f'İki grup bulunamadı; gruplar: {", ".join(names)}')
    xa, xb = groups[a], groups[b]
    n1, n2 = len(xa), len(xb)
    pooled = sorted([(x, 0) for x in xa] + [(x, 1) for x in xb])
    ranks, i, ties = [0.0] * len(pooled), 0, 0.0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    r1 = sum(r for r, (_, grp) in zip(ranks, pooled) if grp == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    n = n1 + n2
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1))))
    z = (u1 - mu - math.copysign(0.5, u1 - mu)) / sigma if sigma and u1 != mu else 0.0
    p = 2 * (1 - N.cdf(abs(z)))
    med = lambda s: sorted(s)[len(s) // 2] if len(s) % 2 else sum(sorted(s)[len(s) // 2 - 1:len(s) // 2 + 1]) / 2
    out = [{'grup': k, 'n': len(s), 'medyan': round(med(s), 4), 'ortalama': round(sum(s) / len(s), 4), 'ortalama_sira': round(sum(r for r, (_, grp) in zip(ranks, pooled) if grp == idx) / len(s), 2)}
           for idx, (k, s) in enumerate(((a, xa), (b, xb)))]
    sup = u1 / (n1 * n2)
    return Result(f'{a} vs {b}: U={fmt(u1)}, p={p:.4f} → {stars(p)}; {a} değerinin {b} değerinden büyük olma olasılığı %{fmt(round(100 * sup, 1))}', list(out[0]), out,
                  {'grup_a': a, 'grup_b': b, 'n_a': n1, 'n_b': n2, 'u': round(u1, 2), 'z': round(z, 4), 'p_degeri': round(p, 4), 'ustunluk': round(sup, 4),
                   'etki_r': round(abs(z) / math.sqrt(n), 4)},
                  notes=['U, birinci grubun (A) sıra toplamından hesaplanır; üstünlük = U / (nA·nB). r etki büyüklüğü ≈ 0,1 küçük, 0,3 orta, 0,5 büyük. Normal yaklaşım bağ düzeltmeli ve süreklilik düzeltmelidir; çok küçük gruplarda (n < 8) kesin test tercih edilmelidir.'])
