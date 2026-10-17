"""Makine öğrenmesi değerlendirme blokları: sınıflandırma ve regresyon metrikleri, veri kayması (PSI), sızıntısız ayırma, sızıntı kontrolü.
Model eğitmez; modelin ürettiği tahmin dosyalarını ve eğitim/canlı veri dışa aktarımlarını değerlendirir."""
import math, random
from collections import Counter, defaultdict
import tabular
from islem import op, arg, Result, need, numbers, fmt, col_list

D, P = 'ml', 'Model değerlendirme dosyaları'


def auc(labels, scores):
    """ROC AUC via the rank-sum (Mann–Whitney) formula, ties averaged."""
    pairs = sorted(zip(scores, labels))
    rank, i = {}, 0
    ranks = [0.0] * len(pairs)
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    pos = sum(l for _, l in pairs)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    return (sum(r for r, (_, l) in zip(ranks, pairs) if l) - pos * (pos + 1) / 2) / (pos * neg)


def confusion(y, p):
    tp = sum(1 for a, b in zip(y, p) if a and b)
    fp = sum(1 for a, b in zip(y, p) if not a and b)
    fn = sum(1 for a, b in zip(y, p) if a and not b)
    tn = len(y) - tp - fp - fn
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return tp, fp, fn, tn, prec, rec, f1


@op('siniflandirma-metrikleri', title='Sınıflandırma modeli metrikleri ve eşik seçimi', domain=D, product=P, tag='sınıflandırma', samples={'model_tahminleri.csv': 'model_tahminleri.csv'},
    args=[arg('--actual', default='gercek'), arg('--score', default='skor'), arg('--threshold', type=float, default=0.5), arg('--segment', default='segment')],
    description='Gerçek etiket ve model skorundan karışıklık matrisi, doğruluk, kesinlik, duyarlılık, F1 ve ROC AUC hesaplar; F1’i en yükselten eşiği ve kalibrasyon tablosunu (skor dilimine göre gerçek oran) çıkarır, isteğe bağlı segment bazında metrik verir. İkili sınıflandırma (churn, fraud, temerrüt) içindir.',
    input='Tahmin dosyası (gerçek 0/1, skor 0-1)', output='sonuc.csv (eşik taraması) + rapor', command='python main.py --actual label --score proba --threshold 0.4 --segment kanal',
    expect={'n': 600, 'en_iyi_esik': 0.6})
def classify(columns, rows, args):
    y = [int(v or 0) for v in numbers(rows, need(args.actual, columns, '--actual'))]
    s = [v if v is not None else 0.0 for v in numbers(rows, need(args.score, columns, '--score'))]
    if set(y) - {0, 1}:
        raise ValueError('Gerçek etiket 0/1 olmalı.')
    tp, fp, fn, tn, prec, rec, f1 = confusion(y, [x >= args.threshold for x in s])
    a = auc(y, s)
    sweep = []
    for k in range(5, 96, 5):
        t = k / 100
        r = confusion(y, [x >= t for x in s])
        sweep.append({'esik': t, 'kesinlik': round(r[4], 4), 'duyarlilik': round(r[5], 4), 'f1': round(r[6], 4), 'pozitif_tahmin': r[0] + r[1]})
    best = max(sweep, key=lambda r: r['f1'])
    calib = []
    for b in range(10):
        members = [(yy, ss) for yy, ss in zip(y, s) if b / 10 <= ss < (b + 1) / 10 or (b == 9 and ss == 1.0)]
        if members:
            calib.append([f'{b / 10:.1f}-{(b + 1) / 10:.1f}', len(members), round(sum(ss for _, ss in members) / len(members), 3), round(sum(yy for yy, _ in members) / len(members), 3)])
    segs = []
    if args.segment in columns:
        groups = defaultdict(list)
        for r, yy, ss in zip(rows, y, s):
            groups[r[args.segment]].append((yy, ss))
        for g, m in sorted(groups.items()):
            c = confusion([a_ for a_, _ in m], [b_ >= args.threshold for _, b_ in m])
            sa = auc([a_ for a_, _ in m], [b_ for _, b_ in m])
            segs.append([g, len(m), round(c[4], 3), round(c[5], 3), round(c[6], 3), round(sa, 3) if sa else ''])
    return Result(f'AUC {a:.3f}; eşik {args.threshold}: kesinlik {prec:.3f}, duyarlılık {rec:.3f}, F1 {f1:.3f}; en iyi F1 eşiği {best["esik"]}', list(sweep[0]), sweep,
                  {'n': len(y), 'pozitif_oran': round(sum(y) / len(y), 4), 'auc': round(a, 4) if a else None, 'kesinlik': round(prec, 4), 'duyarlilik': round(rec, 4), 'f1': round(f1, 4),
                   'en_iyi_esik': best['esik']},
                  tables=[('Karışıklık matrisi', ['', 'Tahmin 1', 'Tahmin 0'], [['Gerçek 1', tp, fn], ['Gerçek 0', fp, tn]]),
                          ('Kalibrasyon', ['Skor dilimi', 'Adet', 'Ortalama skor', 'Gerçek oran'], calib), ('Segment bazında', ['Segment', 'n', 'Kesinlik', 'Duyarlılık', 'F1', 'AUC'], segs)],
                  notes=['Eşik seçimi iş maliyetine göre yapılmalıdır (yanlış alarm ile kaçan vakanın maliyeti farklıysa F1 en iyi ölçüt olmayabilir).'])


@op('regresyon-metrikleri', title='Regresyon/tahmin modeli hata metrikleri', domain=D, product=P, tag='regresyon', samples={'regresyon_tahminleri.csv': 'regresyon_tahminleri.csv'},
    args=[arg('--actual', default='gercek'), arg('--pred', default='tahmin'), arg('--segment', default='bolge')],
    description='Gerçek ve tahmin değerlerinden MAE, RMSE, MAPE, sMAPE, R² ve yanlılık (ortalama hata yönü) hesaplar; segment bazında aynı metrikleri vererek modelin sistematik olarak fazla/eksik tahmin ettiği grupları gösterir.',
    input='Tahmin dosyası (gerçek, tahmin, isteğe bağlı segment)', output='sonuc.csv (segment metrikleri) + rapor', command='python main.py --actual satis --pred tahmin --segment magaza',
    expect={'n': 300, 'yanli_segment': 'Akdeniz'})
def regress(columns, rows, args):
    y, p = numbers(rows, need(args.actual, columns, '--actual')), numbers(rows, need(args.pred, columns, '--pred'))
    pairs = [(a, b, r.get(args.segment, '')) for a, b, r in zip(y, p, rows) if a is not None and b is not None]

    def metrics(pp):
        n = len(pp)
        err = [b - a for a, b, _ in pp]
        mean = sum(a for a, _, _ in pp) / n
        sst = sum((a - mean) ** 2 for a, _, _ in pp)
        nz = [(a, b) for a, b, _ in pp if a]
        return {'n': n, 'mae': sum(abs(e) for e in err) / n, 'rmse': math.sqrt(sum(e * e for e in err) / n),
                'mape_%': 100 * sum(abs(b - a) / abs(a) for a, b in nz) / len(nz) if nz else None,
                'smape_%': 100 * sum(2 * abs(b - a) / (abs(a) + abs(b)) for a, b, _ in pp if abs(a) + abs(b)) / n,
                'r2': 1 - sum(e * e for e in err) / sst if sst else None, 'yanlilik_%': 100 * sum(err) / sum(a for a, _, _ in pp)}
    total = metrics(pairs)
    groups = defaultdict(list)
    for a, b, g in pairs:
        groups[g].append((a, b, g))
    out = [{'segment': g, **{k: round(v, 4) if isinstance(v, float) else v for k, v in metrics(m).items()}} for g, m in sorted(groups.items())] if args.segment in columns else []
    worst = max(out, key=lambda o: abs(o['yanlilik_%'])) if out else None
    return Result(f"MAE {total['mae']:.2f}, RMSE {total['rmse']:.2f}, MAPE %{total['mape_%']:.2f}, R² {total['r2']:.3f}; yanlılık %{total['yanlilik_%']:+.2f}", list(out[0]) if out else [], out,
                  {'n': total['n'], **{k: round(v, 4) for k, v in total.items() if k != 'n' and v is not None}, 'yanli_segment': worst['segment'] if worst and abs(worst['yanlilik_%']) > 5 else ''},
                  notes=['Yanlılık pozitifse model fazla tahmin ediyor. MAPE sıfıra yakın gerçek değerlerde şişer; sMAPE ile birlikte okuyun.'])


def psi(base, cur):
    eps = 1e-4
    return sum((c - b) * math.log((c + eps) / (b + eps)) for b, c in zip(base, cur))


@op('veri-kaymasi-psi', title='Veri kayması: eğitim ve canlı veri karşılaştırması (PSI)', domain=D, product=P, tag='drift', samples={'egitim_verisi.csv': 'egitim_verisi.csv', 'canli_veri.csv': 'canli_veri.csv'},
    args=[arg('--current', default='@ornek/canli_veri.csv'), arg('--columns', help='Varsayılan: iki dosyadaki ortak sütunlar (kimlik ve hedef hariç)'), arg('--exclude', default='musteri_id,churn')],
    description='Eğitim (referans) ve canlı veri arasında her özelliğin dağılım değişimini Population Stability Index ile ölçer: sayısal sütunlarda referansın ondalık dilimleri, kategoriklerde kategori payları kullanılır. PSI < 0,1 kararlı, 0,1–0,25 izlenmeli, > 0,25 belirgin kayma.',
    input='Referans (eğitim) CSV + güncel (canlı) CSV', output='sonuc.csv (özellik bazında PSI) + dilim tablosu', command='python main.py --input egitim.csv --current canli.csv --exclude id,hedef',
    expect={'ozellik': 4, 'belirgin_kayma': 2})
def drift(columns, rows, args):
    cc, cr = tabular.read(args.current)
    excl = {c.strip() for c in args.exclude.split(',') if c.strip()}
    cols = col_list(args.columns, columns, required=False) or [c for c in columns if c in cc and c not in excl]
    out, detail = [], []
    for c in cols:
        need(c, cc, '--current')
        b, a = numbers(rows, c), numbers(cr, c)
        bn, an = [x for x in b if x is not None], [x for x in a if x is not None]
        if len(bn) >= 0.9 * len(rows) and len(an) >= 0.9 * len(cr) and len(set(bn)) > 10:
            sb = sorted(bn)
            edges = sorted(set(sb[int(len(sb) * k / 10)] for k in range(1, 10)))
            bucket = lambda x: sum(x > e for e in edges)
            nb = len(edges) + 1
            pb, pa = [0] * nb, [0] * nb
            for x in bn:
                pb[bucket(x)] += 1
            for x in an:
                pa[bucket(x)] += 1
            kind = 'sayısal'
            labels = [f'≤{fmt(e)}' for e in edges] + [f'>{fmt(edges[-1])}' if edges else 'tümü']
        else:
            cats = sorted({r[c].strip() for r in rows} | {r[c].strip() for r in cr})
            pb = [sum(1 for r in rows if r[c].strip() == k) for k in cats]
            pa = [sum(1 for r in cr if r[c].strip() == k) for k in cats]
            kind, labels = 'kategorik', cats
        fb, fa = [x / sum(pb) for x in pb], [x / sum(pa) for x in pa]
        v = psi(fb, fa)
        out.append({'ozellik': c, 'tur': kind, 'psi': round(v, 4), 'durum': 'belirgin kayma' if v > 0.25 else 'izlenmeli' if v > 0.1 else 'kararlı'})
        detail += [[c, l, round(100 * x, 1), round(100 * y, 1)] for l, x, y in zip(labels, fb, fa)]
    out.sort(key=lambda o: -o['psi'])
    return Result(f"{len(out)} özellik; {sum(o['durum'] == 'belirgin kayma' for o in out)} belirgin kayma, {sum(o['durum'] == 'izlenmeli' for o in out)} izlenmeli", list(out[0]), out,
                  {'ozellik': len(out), 'belirgin_kayma': sum(o['durum'] == 'belirgin kayma' for o in out), 'izlenmeli': sum(o['durum'] == 'izlenmeli' for o in out)},
                  tables=[('Dilim payları (%)', ['Özellik', 'Dilim', 'Referans', 'Güncel'], detail)])


@op('egitim-test-ayir', title='Eğitim/test ayırma (katmanlı, grup sızıntısız)', domain=D, product=P, tag='train/test', samples={'egitim_verisi.csv': 'egitim_verisi.csv'},
    args=[arg('--target', default='churn'), arg('--group', default='musteri_id', help='Aynı gruptaki satırlar aynı tarafta kalır'), arg('--test-size', type=float, default=0.2), arg('--seed', type=int, default=42)],
    description='Veriyi hedef sınıf oranını koruyarak (katmanlı) ve isteğe bağlı olarak aynı müşteri/oturum gibi grupları bölmeden eğitim ve test dosyalarına ayırır; iki taraftaki sınıf oranlarını ve grup çakışmasının sıfır olduğunu raporlar. Tohumlu ve tekrarlanabilir.',
    input='CSV/XLSX + hedef sütunu (+ grup sütunu)', output='egitim.csv + test.csv + rapor', command='python main.py --target hedef --group musteri_id --test-size 0.25 --seed 7',
    expect={'egitim': 960, 'test': 240, 'grup_cakismasi': 0})
def split(columns, rows, args):
    target = need(args.target, columns, '--target')
    rng = random.Random(args.seed)
    gcol = args.group if args.group and args.group in columns else None
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[r[gcol] if gcol else i].append(i)
    by_class = defaultdict(list)
    for g, idx in groups.items():
        by_class[Counter(rows[i][target] for i in idx).most_common(1)[0][0]].append(g)
    test_groups = set()
    for cls, gs in sorted(by_class.items()):
        rng.shuffle(gs)
        test_groups.update(gs[:round(len(gs) * args.test_size)])
    train = [rows[i] for g, idx in groups.items() if g not in test_groups for i in idx]
    test = [rows[i] for g, idx in groups.items() if g in test_groups for i in idx]
    overlap = len({r[gcol] for r in train} & {r[gcol] for r in test}) if gcol else 0
    rate = lambda part: Counter(r[target] for r in part)
    table = [[k, rate(rows)[k], rate(train)[k], rate(test)[k]] for k in sorted(rate(rows))]
    return Result(f'{len(train)} eğitim / {len(test)} test satırı; grup çakışması {overlap}', None, None,
                  {'egitim': len(train), 'test': len(test), 'grup_cakismasi': overlap}, tables=[('Sınıf dağılımı', ['Sınıf', 'Tümü', 'Eğitim', 'Test'], table)],
                  files={'egitim.csv': (columns, train), 'test.csv': (columns, test)})


@op('sizinti-kontrol', title='Veri sızıntısı kontrolü (eğitim–test)', domain=D, product=P, tag='leakage', samples={'egitim_verisi.csv': 'egitim_verisi.csv', 'test_verisi.csv': 'test_verisi.csv'},
    args=[arg('--test', default='@ornek/test_verisi.csv'), arg('--target', default='churn'), arg('--id', default='musteri_id')],
    description='Eğitim ve test dosyaları arasında aynı kimliğin veya birebir aynı satırın bulunup bulunmadığını; hedefi neredeyse kusursuz ayıran (tek başına hedefi ele veren) sütunları ve yalnızca hedef gerçekleşince dolan alanları (iptal tarihi gibi) tespit eder.',
    input='Eğitim CSV + test CSV + hedef ve kimlik sütunu', output='sonuc.csv (bulgular)', command='python main.py --input train.csv --test test.csv --target label --id customer_id',
    expect={'kimlik_cakismasi': 20, 'supheli_ozellik': 1})
def leak(columns, rows, args):
    tc, tr_ = tabular.read(args.test)
    target = need(args.target, columns, '--target')
    out = []
    ids = 0
    if args.id in columns and args.id in tc:
        ids = len({r[args.id] for r in rows} & {r[args.id] for r in tr_})
        if ids:
            out.append({'tur': 'kimlik çakışması', 'sutun': args.id, 'ayrinti': f'{ids} kimlik hem eğitimde hem testte — test skoru iyimser olur'})
    shared = [c for c in columns if c in tc]
    rows_a = {tuple(r[c] for c in shared if c != args.id) for r in rows}
    dup = sum(1 for r in tr_ if tuple(r[c] for c in shared if c != args.id) in rows_a)
    if dup:
        out.append({'tur': 'birebir aynı satır', 'sutun': '', 'ayrinti': f'{dup} test satırı eğitimde de var'})
    suspicious = 0
    for c in columns:
        if c in (target, args.id):
            continue
        filled = [(r[c].strip() != '', r[target]) for r in rows]
        by = defaultdict(Counter)
        for has, t in filled:
            by[has][t] += 1
        if len(by) == 2 and all(len(v) == 1 for v in by.values()) and len({next(iter(v)) for v in by.values()}) == 2:
            suspicious += 1
            out.append({'tur': 'hedefi ele veren alan', 'sutun': c, 'ayrinti': 'alanın dolu/boş olması hedefi kusursuz ayırıyor — tahmin anında bilinmeyen bilgi olabilir'})
            continue
        values = defaultdict(Counter)
        for r in rows:
            values[r[c].strip()][r[target]] += 1
        if 2 <= len(values) <= len(rows) * 0.5 and sum(max(v.values()) for v in values.values()) == len(rows) and min(sum(v.values()) for v in values.values()) >= 5:
            suspicious += 1
            out.append({'tur': 'hedefi ele veren alan', 'sutun': c, 'ayrinti': 'her değer tek bir hedef sınıfına karşılık geliyor'})
    return Result(f'{len(out)} sızıntı bulgusu', ['tur', 'sutun', 'ayrinti'], out, {'kimlik_cakismasi': ids, 'ayni_satir': dup, 'supheli_ozellik': suspicious})


def labeled(columns, rows, args):
    ys = numbers(rows, need(args.actual, columns, '--actual'))
    ps = numbers(rows, need(args.score, columns, '--score'))
    pairs = [(int(y), p) for y, p in zip(ys, ps) if y in (0, 1) and p is not None]
    if not pairs:
        raise ValueError('0/1 gerçek değer ve sayısal skor içeren satır yok.')
    if any(not 0 <= p <= 1 for _, p in pairs):
        raise ValueError('Skorlar 0–1 aralığında olasılık olmalı.')
    return pairs


@op('esik-optimizasyonu', title='Sınıflandırma eşiğini F1 veya hata maliyetine göre seç', domain=D, product=P, tag='eşik', samples={'model_tahminleri.csv': 'model_tahminleri.csv'},
    args=[arg('--actual', default='gercek'), arg('--score', default='skor'), arg('--fp-cost', type=float, default=1.0, help='Yanlış pozitif başına maliyet'),
          arg('--fn-cost', type=float, default=5.0, help='Yanlış negatif başına maliyet'), arg('--step', type=float, default=0.05)],
    description='Model olasılık skorları için 0–1 arasındaki eşiklerde karışıklık matrisini, kesinlik, duyarlılık ve F1’i hesaplar; F1’i en yüksek ve iş maliyetini (yanlış pozitif × maliyet + yanlış negatif × maliyet) en düşük yapan eşiği önerir. Varsayılan 0,5 eşiğinin dengesiz sınıflarda neden yetersiz kaldığını gösterir.',
    input='Tahmin dosyası (gerçek 0/1, skor 0–1)', output='sonuc.csv (eşik × metrikler)', command='python main.py --actual churn --score olasilik --fp-cost 50 --fn-cost 400',
    expect={'kayit': 600, 'en_iyi_f1_esik': 0.6, 'en_dusuk_maliyet_esik': 0.5, 'en_dusuk_maliyet': 171.0})
def threshold(columns, rows, args):
    pairs = labeled(columns, rows, args)
    out = []
    for i in range(1, int(round(1 / args.step))):
        t = round(i * args.step, 4)
        tp = sum(1 for y, p in pairs if p >= t and y)
        fp = sum(1 for y, p in pairs if p >= t and not y)
        fn = sum(1 for y, p in pairs if p < t and y)
        tn = len(pairs) - tp - fp - fn
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out.append({'esik': t, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'kesinlik': round(prec, 4), 'duyarlilik': round(rec, 4), 'f1': round(f1, 4),
                    'maliyet': round(fp * args.fp_cost + fn * args.fn_cost, 2)})
    best_f1 = max(out, key=lambda o: (o['f1'], -o['esik']))
    best_cost = min(out, key=lambda o: (o['maliyet'], o['esik']))
    half = next((o for o in out if abs(o['esik'] - 0.5) < 1e-9), None)
    for o in out:
        o['oneri'] = ', '.join(x for x, b in (('en iyi F1', best_f1), ('en düşük maliyet', best_cost)) if o is b)
    return Result(f'En iyi F1 {best_f1["f1"]} (eşik {best_f1["esik"]}); en düşük maliyet {best_cost["maliyet"]:,} (eşik {best_cost["esik"]})', list(out[0]), out,
                  {'kayit': len(pairs), 'pozitif_orani_%': round(100 * sum(y for y, _ in pairs) / len(pairs), 1), 'en_iyi_f1_esik': best_f1['esik'], 'en_iyi_f1': best_f1['f1'],
                   'en_dusuk_maliyet_esik': best_cost['esik'], 'en_dusuk_maliyet': best_cost['maliyet'], 'esik_0_5_maliyet': half['maliyet'] if half else ''},
                  notes=['Eşik, doğrulama verisinde seçilip test verisinde kontrol edilmelidir; aynı veride seçmek iyimser sonuç verir.'])


@op('kalibrasyon', title='Olasılık kalibrasyonu: güvenilirlik tablosu, Brier ve ECE', domain=D, product=P, tag='kalibrasyon', samples={'model_tahminleri.csv': 'model_tahminleri.csv'},
    args=[arg('--actual', default='gercek'), arg('--score', default='skor'), arg('--bins', type=int, default=10)],
    description='Modelin verdiği olasılıkların gerçekleşen oranlarla uyumunu ölçer: eşit genişlikte skor dilimlerinde ortalama tahmin ve gerçek oranı (güvenilirlik eğrisi), Brier skoru, log loss ve beklenen kalibrasyon hatasını (ECE) hesaplar; aşırı iyimser/kötümser dilimleri işaretler. Olasılığı fiyatlama veya bütçe hesabında kullanılan modeller için.',
    input='Tahmin dosyası (gerçek 0/1, skor 0–1)', output='sonuc.csv (dilim bazında kalibrasyon)', command='python main.py --actual temerrut --score pd --bins 20',
    expect={'kayit': 600, 'dilim': 10, 'brier': 0.1324})
def calibration(columns, rows, args):
    pairs = labeled(columns, rows, args)
    bins = [[] for _ in range(args.bins)]
    for y, p in pairs:
        bins[min(args.bins - 1, int(p * args.bins))].append((y, p))
    out, ece = [], 0.0
    for i, b in enumerate(bins):
        if not b:
            continue
        conf = sum(p for _, p in b) / len(b)
        acc = sum(y for y, _ in b) / len(b)
        ece += len(b) / len(pairs) * abs(acc - conf)
        gap = acc - conf
        out.append({'dilim': f'{i / args.bins:.2f}–{(i + 1) / args.bins:.2f}', 'kayit': len(b), 'ort_tahmin': round(conf, 4), 'gercek_oran': round(acc, 4), 'fark': round(gap, 4),
                    'yorum': 'iyimser (tahmin yüksek)' if gap < -0.1 else 'kötümser (tahmin düşük)' if gap > 0.1 else ''})
    brier = sum((p - y) ** 2 for y, p in pairs) / len(pairs)
    eps = 1e-15
    logloss = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for y, p in pairs) / len(pairs)
    base = sum(y for y, _ in pairs) / len(pairs)
    return Result(f'Brier {brier:.4f} (taban {base * (1 - base):.4f}), ECE {ece:.4f}, log loss {logloss:.4f}', list(out[0]), out,
                  {'kayit': len(pairs), 'dilim': len(out), 'brier': round(brier, 4), 'brier_taban': round(base * (1 - base), 4), 'ece': round(ece, 4), 'log_loss': round(logloss, 4)},
                  notes=['Brier taban değeri, herkese ortalama oranı veren modelin skorudur; model bunun altında kalmalıdır.',
                         'Kalibrasyonu bozuk ama sıralaması iyi modeller Platt/izotonik kalibrasyonla düzeltilebilir.'])


def woe_table(groups, total_good, total_bad):
    rows, iv = [], 0.0
    for label, (good, bad) in groups:
        dg, db = (good or 0.5) / total_good, (bad or 0.5) / total_bad
        woe = math.log(dg / db)
        iv += (dg - db) * woe
        rows.append((label, good + bad, bad, round(100 * bad / (good + bad), 1) if good + bad else 0, round(woe, 4)))
    return rows, iv


def strength(iv):
    return 'şüpheli: sızıntı olabilir' if iv > 0.5 else 'güçlü' if iv > 0.3 else 'orta' if iv > 0.1 else 'zayıf' if iv > 0.02 else 'işe yaramaz'


@op('woe-iv', title='Değişken gücü: WoE ve Information Value (IV)', domain=D, product=P, tag='WoE/IV', samples={'egitim_verisi.csv': 'egitim_verisi.csv'},
    args=[arg('--target', default='churn', help='0/1 hedef sütunu'), arg('--features', help='Virgülle sütunlar (varsayılan: hedef ve kimlik dışındakiler)'),
          arg('--bins', type=int, default=5, help='Sayısal değişkenler için eşit frekanslı dilim'), arg('--max-cats', type=int, default=20)],
    description='İkili hedef için her değişkenin dilimlerinde olay oranını, Weight of Evidence (WoE) değerini ve değişkenin toplam Information Value’sunu hesaplar; değişkenleri gücüne göre sıralar. Sayısal değişkenleri eşit frekanslı dilimlere böler, çok kategorili alanları dolu/boş olarak değerlendirir ve IV’si 0,5’i aşan (hedefi sızdıran) alanları uyarır. Kredi skorlama ve churn modellerinde değişken seçimi için.',
    input='Eğitim verisi (hedef 0/1 + değişkenler)', output='sonuc.csv (değişken × dilim WoE) + IV sıralaması', command='python main.py --input basvurular.csv --target temerrut --bins 10',
    expect={'ozellik': 5, 'en_guclu': 'iptal_tarihi'})
def woe_iv(columns, rows, args):
    tcol = need(args.target, columns, '--target')
    ys = numbers(rows, tcol)
    data = [(r, int(y)) for r, y in zip(rows, ys) if y in (0, 1)]
    bad_total = sum(y for _, y in data)
    good_total = len(data) - bad_total
    if not bad_total or not good_total:
        raise ValueError('Hedefte hem 0 hem 1 olmalı.')
    feats = col_list(args.features, columns, '--features') if args.features else [c for c in columns if c != tcol and len({r[c] for r, _ in data}) < len(data)]
    out, ivs = [], []
    for f in feats:
        vals = numbers([r for r, _ in data], f)
        filled = [v for v, (r, _) in zip(vals, data) if r[f].strip()]
        numeric = filled and all(v is not None for v in filled)
        groups = defaultdict(lambda: [0, 0])
        if numeric:
            ordered = sorted(filled)
            edges = sorted({ordered[min(len(ordered) - 1, len(ordered) * k // args.bins)] for k in range(1, args.bins)})
            for v, (r, y) in zip(vals, data):
                if v is None:
                    key = (len(edges) + 1, 'boş')
                else:
                    k = sum(v >= e for e in edges)
                    lo = '-∞' if k == 0 else fmt(edges[k - 1])
                    hi = '∞' if k == len(edges) else fmt(edges[k])
                    key = (k, f'[{lo}, {hi})')
                groups[key][y] += 1
        else:
            cats = {r[f].strip() for r, _ in data}
            many = len(cats) > args.max_cats
            for r, y in data:
                v = r[f].strip()
                groups[(0, 'dolu' if v else 'boş') if many else (0, v or 'boş')][y] += 1
        table, iv = woe_table([(lbl, (g[0], g[1])) for (_, lbl), g in sorted(groups.items())], good_total, bad_total)
        ivs.append([f, 'sayısal' if numeric else 'kategorik', round(iv, 4), strength(iv)])
        out += [{'ozellik': f, 'dilim': lbl, 'kayit': n, 'olay': b, 'olay_orani_%': rate, 'woe': w} for lbl, n, b, rate, w in table]
    ivs.sort(key=lambda x: -x[2])
    return Result(f'{len(feats)} değişken; en güçlü {ivs[0][0]} (IV {ivs[0][2]}, {ivs[0][3]})', ['ozellik', 'dilim', 'kayit', 'olay', 'olay_orani_%', 'woe'], out,
                  {'kayit': len(data), 'olay_orani_%': round(100 * bad_total / len(data), 1), 'ozellik': len(feats), 'en_guclu': ivs[0][0], 'en_guclu_iv': ivs[0][2],
                   'supheli': [x[0] for x in ivs if x[2] > 0.5]},
                  tables=[('Information Value sıralaması', ['Değişken', 'Tür', 'IV', 'Güç'], ivs)],
                  notes=['WoE = ln(olay olmayan payı / olay payı); boş dilimlerde 0,5 düzeltmesi uygulanır.', 'IV > 0,5 genelde hedefin bilgisini taşıyan (ör. sonradan dolan tarih) alanlara işaret eder.'])


@op('cok-sinifli-metrikler', title='Çok sınıflı sınıflandırma: karışıklık matrisi ve sınıf bazında metrikler', domain=D, product=P, tag='çok sınıflı', samples={'cok_sinifli_tahmin.csv': 'cok_sinifli_tahmin.csv'},
    args=[arg('--actual', default='gercek'), arg('--predicted', default='tahmin')],
    description='Gerçek ve tahmin edilen sınıf etiketlerinden karışıklık matrisini, sınıf bazında kesinlik, duyarlılık, F1 ve destek (örnek sayısını), doğruluk ile makro ve ağırlıklı F1’i hesaplar; en çok karıştırılan sınıf çiftlerini listeler. Kategori, niyet (intent) veya ürün sınıflandırma modelleri için.',
    input='Tahmin dosyası (gerçek sınıf, tahmin sınıf)', output='sonuc.csv (sınıf bazında metrikler) + karışıklık matrisi', command='python main.py --actual kategori --predicted model_kategori',
    expect={'n': 12, 'dogruluk': 0.75, 'makro_f1': 0.7389, 'agirlikli_f1': 0.75})
def multiclass(columns, rows, args):
    a, p = need(args.actual, columns, '--actual'), need(args.predicted, columns, '--predicted')
    pairs = [(r[a].strip(), r[p].strip()) for r in rows if r[a].strip()]
    if not pairs:
        raise ValueError('Gerçek etiketi olan satır yok.')
    labels = sorted({x for pr in pairs for x in pr if x})
    cm = Counter(pairs)
    out = []
    for c in labels:
        tp = cm[(c, c)]
        pred = sum(cm[(x, c)] for x in labels)
        sup = sum(cm[(c, x)] for x in labels) + cm[(c, '')]
        prec = tp / pred if pred else 0.0
        rec = tp / sup if sup else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out.append({'sinif': c, 'destek': sup, 'kesinlik': round(prec, 4), 'duyarlilik': round(rec, 4), 'f1': round(f1, 4), '_f1': f1})
    n = len(pairs)
    macro = sum(o['_f1'] for o in out) / len(out)
    weighted = sum(o['_f1'] * o['destek'] for o in out) / n
    for o in out:
        o.pop('_f1')
    confused = sorted(((k, v) for k, v in cm.items() if k[0] != k[1]), key=lambda kv: -kv[1])[:10]
    matrix = [[c] + [cm[(c, x)] for x in labels] for c in labels]
    return Result(f'{n} örnek, {len(labels)} sınıf; doğruluk {sum(cm[(c, c)] for c in labels) / n:.3f}, makro F1 {macro:.3f}', list(out[0]), out,
                  {'n': n, 'sinif': len(labels), 'dogruluk': round(sum(cm[(c, c)] for c in labels) / n, 4), 'makro_f1': round(macro, 4), 'agirlikli_f1': round(weighted, 4)},
                  tables=[('Karışıklık matrisi (satır: gerçek, sütun: tahmin)', ['Gerçek \\ Tahmin', *labels], matrix),
                          ('En çok karıştırılanlar', ['Gerçek', 'Tahmin', 'Adet'], [[k[0], k[1] or '(boş)', v] for k, v in confused])])


@op('lift-tablosu', title='Skor dilimlerine göre lift, kazanç (gain) ve KS', domain=D, product=P, tag='lift/gain', samples={'model_tahminleri.csv': 'model_tahminleri.csv'},
    args=[arg('--actual', default='gercek'), arg('--score', default='skor'), arg('--groups', type=int, default=10)],
    description='Kayıtları model skoruna göre azalan sıralayıp eşit büyüklükte dilimlere (desil) böler; her dilimde olay oranını, kümülatif yakalanan olay yüzdesini (kazanç), lift’i ve Kolmogorov–Smirnov istatistiğini hesaplar. Kampanyada ilk %20’ye ulaşınca olayların ne kadarının yakalandığını iş birimine anlatmak için.',
    input='Tahmin dosyası (gerçek 0/1, skor)', output='sonuc.csv (dilim bazında lift/kazanç)', command='python main.py --actual satin_aldi --score olasilik --groups 20',
    expect={'kayit': 600, 'dilim': 10, 'ilk_dilim_lift': 4.32, 'ks': 0.6721, 'ilk_iki_dilim_kazanc_%': 74.8})
def lift(columns, rows, args):
    pairs = labeled(columns, rows, args)
    pairs.sort(key=lambda x: -x[1])
    n, events = len(pairs), sum(y for y, _ in pairs)
    if not events or events == n:
        raise ValueError('Hem olay hem olay-dışı kayıt gerekir.')
    base = events / n
    out, cum_e, cum_ne, ks = [], 0, 0, 0.0
    for g in range(args.groups):
        chunk = pairs[g * n // args.groups:(g + 1) * n // args.groups]
        e = sum(y for y, _ in chunk)
        cum_e += e
        cum_ne += len(chunk) - e
        gap = cum_e / events - cum_ne / (n - events)
        ks = max(ks, gap)
        out.append({'dilim': g + 1, 'kayit': len(chunk), 'min_skor': round(chunk[-1][1], 4), 'olay': e, 'olay_orani_%': round(100 * e / len(chunk), 1),
                    'lift': round(e / len(chunk) / base, 2), 'kumulatif_kazanc_%': round(100 * cum_e / events, 1), 'kumulatif_lift': round(cum_e / ((g + 1) * n // args.groups) / base, 2)})
    return Result(f'İlk dilim lift {out[0]["lift"]}; ilk %{100 * 2 // args.groups} kayıtta olayların %{out[1]["kumulatif_kazanc_%"]}’i; KS {ks:.3f}', list(out[0]), out,
                  {'kayit': n, 'dilim': len(out), 'ilk_dilim_lift': out[0]['lift'], 'ks': round(ks, 4), 'ilk_iki_dilim_kazanc_%': out[1]['kumulatif_kazanc_%']})
