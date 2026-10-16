"""Muhasebe ve finans blokları: mizan, yevmiye, cari, banka, e-fatura, KDV, kur, amortisman, kredi ve stok maliyeti.
Tek Düzen Hesap Planı (TDHP) kuralları kodda yazılıdır; sonuçlar kontrol amaçlıdır, mali müşavir onayının yerini tutmaz."""
import math, re, xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
import tabular
import turkiye as tr
from islem import op, arg, Result, col_list, need, numbers, fmt, tr_money, fold, tr_sort_key

REQUIRES = ['turkiye']
D, P = 'erp', 'Muhasebe dışa aktarımları'
NOTE = 'Kontrol amaçlıdır; muhasebe kaydı veya beyan yerine geçmez. Sonuçları mali müşavirinizle doğrulayın.'


def money(rows, column):
    return [n or 0.0 for n in numbers(rows, column)]


def day(value, label):
    d = tabular.parse_date(value or '')
    if not d:
        raise ValueError(f'{label}: tarih okunamadı ({value}).')
    return d[0].date()


# ---------------------------------------------------------------- mizan
CONTRA = {'103', '122', '124', '129', '137', '139', '158', '222', '224', '229', '237', '239', '241', '244', '246', '247', '249', '257', '268', '278',
          '298', '299', '302', '308', '322', '337', '371', '402', '408', '422', '437', '501', '580', '591'}
STRICT = {'100': 'Kasa hesabı alacak bakiye veremez', '153': 'Stok hesabı alacak bakiye veremez', '150': 'Stok hesabı alacak bakiye veremez',
          '151': 'Stok hesabı alacak bakiye veremez', '152': 'Stok hesabı alacak bakiye veremez'}


def normal_side(code):
    c = code[:3]
    if not c.isdigit():
        return None
    k = c[0]
    side = 'B' if k in '127' else 'A' if k in '345' else ('A' if c[:2] in ('60', '64', '67') else None if c[:2] == '69' else 'B') if k == '6' else None
    if side and c in CONTRA:
        side = 'A' if side == 'B' else 'B'
    return side


def read_mizan(columns, rows, args):
    code, debit, credit = need(args.code, columns, '--code'), need(args.debit, columns, '--debit'), need(args.credit, columns, '--credit')
    return [(r[code].strip(), r.get(args.name, ''), b, a) for r, b, a in zip(rows, money(rows, debit), money(rows, credit)) if r[code].strip()]


MIZAN_ARGS = [arg('--code', default='hesap_kodu'), arg('--name', default='hesap_adi'), arg('--debit', default='borc'), arg('--credit', default='alacak')]
MIZAN = {'mizan.csv': 'mizan.csv'}


@op('mizan-kontrol', title='Mizanı TDHP kurallarıyla kontrol et', domain=D, product=P, tag='mizan', samples=MIZAN, args=MIZAN_ARGS,
    description='Mizanda borç ve alacak toplamlarının eşitliğini, hesap kodlarının Tek Düzen Hesap Planı sınıflarına uygunluğunu ve beklenmeyen bakiye yönlerini (alacak bakiyeli kasa, borç bakiyeli satıcılar, ters bakiyeli düzenleyici hesaplar) bulur.',
    input='Mizan CSV/XLSX (hesap kodu, adı, borç, alacak)', output='bulgular.csv + rapor', command='python main.py --input mizan.xlsx',
    expect={'borc_alacak_farki': 0, 'ters_bakiye': 2, 'hesap': 19})
def mizan_kontrol(columns, rows, args):
    accounts = read_mizan(columns, rows, args)
    tb, ta = sum(a[2] for a in accounts), sum(a[3] for a in accounts)
    findings = []
    for code, name, b, a in accounts:
        bal = b - a
        if not re.match(r'^[1-9]\d{2}', code):
            findings.append({'hesap': code, 'ad': name, 'bakiye': fmt(round(bal, 2)), 'onem': 'orta', 'bulgu': 'TDHP hesap kodu biçiminde değil (3 haneli ana hesap)'})
            continue
        side = normal_side(code)
        actual = 'B' if bal > 0.005 else 'A' if bal < -0.005 else ''
        if side and actual and actual != side:
            msg = STRICT.get(code[:3], f"Beklenen {'borç' if side == 'B' else 'alacak'} bakiye, mizanda {'borç' if actual == 'B' else 'alacak'} bakiye")
            findings.append({'hesap': code, 'ad': name, 'bakiye': fmt(round(bal, 2)), 'onem': 'yüksek' if code[:3] in STRICT else 'orta', 'bulgu': msg})
    if abs(tb - ta) > 0.01:
        findings.insert(0, {'hesap': 'TOPLAM', 'ad': '', 'bakiye': fmt(round(tb - ta, 2)), 'onem': 'yüksek', 'bulgu': 'Borç ve alacak toplamları eşit değil'})
    classes = Counter(c[0][0] for c in accounts)
    return Result(f"{len(accounts)} hesap · borç {tr_money(tb)} / alacak {tr_money(ta)} · {len(findings)} bulgu", ['hesap', 'ad', 'bakiye', 'onem', 'bulgu'], findings,
                  {'hesap': len(accounts), 'borc_toplam': round(tb, 2), 'alacak_toplam': round(ta, 2), 'borc_alacak_farki': round(tb - ta, 2),
                   'ters_bakiye': sum('bakiye' in f['bulgu'] for f in findings)},
                  tables=[('Hesap sınıfları', ['Sınıf', 'Hesap sayısı'], sorted(classes.items()))], notes=[NOTE])


@op('mizan-tablolar', title='Mizandan özet bilanço ve gelir tablosu çıkar', domain=D, product=P, tag='bilanço', samples=MIZAN, args=MIZAN_ARGS,
    description='Mizan bakiyelerini TDHP gruplarına göre özet bilanço (dönen/duran varlık, kısa/uzun vadeli yabancı kaynak, özkaynak) ve gelir tablosuna (net satış, brüt kâr, faaliyet kârı, dönem kârı) dönüştürür; aktif-pasif denkliğini açıklar.',
    input='Mizan CSV/XLSX', output='bilanco.csv + gelir-tablosu.csv + rapor', command='python main.py --input mizan.xlsx',
    expect={'aktif': 1960500.0, 'pasif': 1115000.0, 'donem_kari': 851500.0, 'yansitilmamis_7': 6000.0, 'denklik_farki': 0.0})
def mizan_tablolar(columns, rows, args):
    accounts = read_mizan(columns, rows, args)
    bal = defaultdict(float)
    for code, _, b, a in accounts:
        bal[code[:3]] += b - a
    by = lambda pred: sum(v for k, v in bal.items() if pred(k))
    groups = [('Dönen varlıklar', by(lambda k: k[0] == '1')), ('Duran varlıklar', by(lambda k: k[0] == '2')),
              ('Kısa vadeli yabancı kaynaklar', -by(lambda k: k[0] == '3')), ('Uzun vadeli yabancı kaynaklar', -by(lambda k: k[0] == '4')),
              ('Özkaynaklar (dönem kârı hariç)', -by(lambda k: k[0] == '5'))]
    aktif = groups[0][1] + groups[1][1]
    pasif = groups[2][1] + groups[3][1] + groups[4][1]
    g = lambda two: -by(lambda k: k[:2] == two)
    lines = [('Brüt satışlar', g('60')), ('Satış indirimleri (-)', g('61'))]
    net = lines[0][1] + lines[1][1]
    lines += [('Net satışlar', net), ('Satışların maliyeti (-)', g('62'))]
    gross = net + g('62')
    lines += [('Brüt satış kârı', gross), ('Faaliyet giderleri (-)', g('63'))]
    oper = gross + g('63')
    lines += [('Faaliyet kârı', oper), ('Diğer faaliyet gelirleri', g('64')), ('Diğer faaliyet giderleri (-)', g('65')), ('Finansman giderleri (-)', g('66'))]
    ordinary = oper + g('64') + g('65') + g('66')
    lines += [('Olağan kâr', ordinary), ('Olağan dışı gelirler', g('67')), ('Olağan dışı giderler (-)', g('68'))]
    profit = ordinary + g('67') + g('68') + g('69')
    lines.append(('Dönem kârı / zararı', profit))
    seven = by(lambda k: k[0] == '7')
    gap = round(aktif - (pasif + profit - seven), 2)
    notes = [NOTE]
    if abs(seven) > 0.005:
        notes.insert(0, f'7xx maliyet hesaplarında {tr_money(seven)} yansıtılmamış bakiye var; dönem sonu yansıtma kayıtları yapılmamış olabilir.')
    r2 = lambda x: round(x, 2)
    return Result(f'Aktif {tr_money(aktif)} · Pasif {tr_money(pasif)} · Dönem kârı {tr_money(profit)}', None, None,
                  {'aktif': r2(aktif), 'pasif': r2(pasif), 'donem_kari': r2(profit), 'yansitilmamis_7': r2(seven), 'denklik_farki': gap},
                  tables=[('Özet bilanço', ['Grup', 'Tutar'], [[n, tr_money(v)] for n, v in groups] + [['AKTİF TOPLAMI', tr_money(aktif)], ['PASİF TOPLAMI (dönem kârı hariç)', tr_money(pasif)]]),
                          ('Gelir tablosu', ['Kalem', 'Tutar'], [[n, tr_money(v)] for n, v in lines])],
                  files={'bilanco.csv': (['grup', 'tutar'], [{'grup': n, 'tutar': r2(v)} for n, v in groups]),
                         'gelir-tablosu.csv': (['kalem', 'tutar'], [{'kalem': n, 'tutar': r2(v)} for n, v in lines])},
                  notes=notes + ['Denklik: aktif = pasif + dönem kârı − yansıtılmamış 7xx bakiyesi.'])


@op('yevmiye-kontrol', title='Yevmiye fişlerini denetle', domain=D, product=P, tag='yevmiye', samples={'yevmiye.csv': 'yevmiye.csv'},
    description='Her fişin borç-alacak dengesini, fiş numarası boşluklarını, hafta sonu kayıtlarını, dönem dışı tarihleri ve büyük yuvarlak tutarları (iç denetim sinyali) bulur. Defter dışa aktarımı üzerinde çalışır.',
    input='Yevmiye CSV/XLSX (fiş no, tarih, hesap, borç, alacak)', output='bulgular.csv + rapor', command='python main.py --period 2026-09',
    args=[arg('--voucher', default='fis_no'), arg('--date', default='tarih'), arg('--debit', default='borc'), arg('--credit', default='alacak'),
          arg('--period', help='Beklenen dönem YYYY-AA'), arg('--round', type=float, default=10000.0, help='Bu tutar ve katları "yuvarlak" sayılır')],
    test_args=['--period', '2026-09'], expect={'fis': 24, 'dengesiz': 1, 'eksik_numara': 1, 'hafta_sonu': 1, 'yuvarlak_tutar': 1, 'donem_disi': 0})
def yevmiye(columns, rows, args):
    v, dcol = need(args.voucher, columns, '--voucher'), need(args.date, columns, '--date')
    deb, cre = money(rows, need(args.debit, columns, '--debit')), money(rows, need(args.credit, columns, '--credit'))
    fis = defaultdict(lambda: {'b': 0.0, 'a': 0.0, 'dates': set(), 'amounts': []})
    for r, b, a in zip(rows, deb, cre):
        f = fis[r[v].strip()]
        f['b'] += b
        f['a'] += a
        f['dates'].add(r[dcol].strip())
        f['amounts'] += [x for x in (b, a) if x]
    out = []
    for no, f in fis.items():
        if abs(f['b'] - f['a']) > 0.005:
            out.append({'fis_no': no, 'bulgu': 'dengesiz fiş', 'ayrinti': f"borç {tr_money(f['b'])} / alacak {tr_money(f['a'])}"})
        for ds in f['dates']:
            d = tabular.parse_date(ds)
            if d and d[0].weekday() >= 5:
                out.append({'fis_no': no, 'bulgu': 'hafta sonu kaydı', 'ayrinti': ds})
            if d and args.period and d[0].strftime('%Y-%m') != args.period:
                out.append({'fis_no': no, 'bulgu': 'dönem dışı tarih', 'ayrinti': ds})
        if len(f['dates']) > 1:
            out.append({'fis_no': no, 'bulgu': 'fişte birden çok tarih', 'ayrinti': ', '.join(sorted(f['dates']))})
        big = [x for x in set(f['amounts']) if x >= args.round and x % args.round == 0]
        if big:
            out.append({'fis_no': no, 'bulgu': 'yuvarlak tutar', 'ayrinti': ', '.join(tr_money(x) for x in sorted(big))})
    nums = sorted(int(n) for n in fis if n.isdigit())
    missing = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)] if nums else []
    out += [{'fis_no': str(n), 'bulgu': 'eksik fiş numarası', 'ayrinti': ''} for n in missing]
    kinds = Counter(o['bulgu'] for o in out)
    return Result(f'{len(fis)} fiş, {len(out)} bulgu', ['fis_no', 'bulgu', 'ayrinti'], out,
                  {'fis': len(fis), 'dengesiz': kinds['dengesiz fiş'], 'eksik_numara': len(missing), 'hafta_sonu': kinds['hafta sonu kaydı'],
                   'yuvarlak_tutar': kinds['yuvarlak tutar'], 'donem_disi': kinds['dönem dışı tarih']},
                  tables=[('Bulgu türleri', ['Bulgu', 'Adet'], kinds.most_common())], notes=[NOTE])


# ---------------------------------------------------------------- cari
def fifo(invoices, payments):
    """invoices: list of dicts with open amount in 'acik'; payments applied oldest-first per cari. Returns unapplied payments."""
    by_cari = defaultdict(list)
    for inv in sorted(invoices, key=lambda x: x['tarih']):
        by_cari[inv['cari']].append(inv)
    left = defaultdict(float)
    for p in sorted(payments, key=lambda x: x['tarih']):
        amount = p['tutar']
        for inv in by_cari.get(p['cari'], []):
            if amount <= 0.005:
                break
            use = min(inv['acik'], amount)
            if use > 0:
                inv['acik'] -= use
                inv['kapanis'] = p['tarih'].isoformat()
                amount -= use
        left[p['cari']] += amount
    return {k: v for k, v in left.items() if v > 0.005}


def load_invoices(columns, rows, args):
    for c in (args.party, args.invoice_date, args.due, args.amount):
        need(c, columns, 'fatura sütunları')
    amounts = money(rows, args.amount)
    inv = [{'cari': r[args.party].strip(), 'no': r.get(args.number, ''), 'tarih': day(r[args.invoice_date], 'fatura tarihi'),
            'vade': day(r[args.due], 'vade tarihi'), 'tutar': a, 'acik': a, 'kapanis': ''} for r, a in zip(rows, amounts)]
    pays = []
    if args.payments:
        pc, pr = tabular.read(args.payments)
        for c in (args.party, 'tarih', 'tutar'):
            need(c, pc, '--payments')
        pays = [{'cari': r[args.party].strip(), 'tarih': day(r['tarih'], 'tahsilat tarihi'), 'tutar': t} for r, t in zip(pr, money(pr, 'tutar'))]
    return inv, fifo(inv, pays)


CARI_ARGS = [arg('--party', default='cari'), arg('--number', default='fatura_no'), arg('--invoice-date', default='fatura_tarihi'), arg('--due', default='vade_tarihi'),
             arg('--amount', default='tutar'), arg('--payments', default='@ornek/tahsilatlar.csv', help='Tahsilat/ödeme dosyası (cari, tarih, tutar); "" ile kapatılır')]
CARI = {'cari_faturalar.csv': 'cari_faturalar.csv', 'tahsilatlar.csv': 'tahsilatlar.csv'}


@op('cari-yaslandirma', title='Cari alacak yaşlandırma raporu', domain=D, product=P, tag='yaşlandırma', samples=CARI, args=CARI_ARGS + [arg('--as-of', default='2026-09-30')],
    description='Faturaları ve tahsilatları cari bazında en eski faturadan kapatır (FIFO), açık bakiyeyi vadesine göre “vadesi gelmemiş, 1-30, 31-60, 61-90, 90+ gün” dilimlerine ayırır. Fazla tahsilatları ayrıca gösterir.',
    input='Fatura listesi + isteğe bağlı tahsilat listesi + rapor tarihi', output='sonuc.csv (cari bazında) + acik-faturalar.csv', command='python main.py --input faturalar.xlsx --payments tahsilat.xlsx --as-of 2026-09-30',
    expect={'cari': 6, 'acik_fatura': 21})
def yaslandirma(columns, rows, args):
    inv, extra = load_invoices(columns, rows, args)
    as_of = day(args.as_of, '--as-of')
    buckets = ['vadesi_gelmemis', '1_30', '31_60', '61_90', '90_ustu']
    table = defaultdict(lambda: dict.fromkeys(buckets, 0.0))
    open_rows = []
    for i in inv:
        if i['acik'] <= 0.005:
            continue
        late = (as_of - i['vade']).days
        b = 'vadesi_gelmemis' if late <= 0 else '1_30' if late <= 30 else '31_60' if late <= 60 else '61_90' if late <= 90 else '90_ustu'
        table[i['cari']][b] += i['acik']
        open_rows.append({'cari': i['cari'], 'fatura_no': i['no'], 'vade': i['vade'].isoformat(), 'gecikme_gun': max(late, 0), 'acik_tutar': round(i['acik'], 2), 'dilim': b})
    out = []
    for cari in sorted(table, key=tr_sort_key):
        row = {'cari': cari, **{b: round(v, 2) for b, v in table[cari].items()}}
        row['toplam'] = round(sum(table[cari].values()), 2)
        row['fazla_tahsilat'] = round(extra.get(cari, 0.0), 2)
        out.append(row)
    total = {b: sum(r[b] for r in out) for b in buckets}
    return Result(f'{len(out)} cari, açık bakiye {tr_money(sum(total.values()))} ({as_of.isoformat()} itibarıyla)', ['cari'] + buckets + ['toplam', 'fazla_tahsilat'], out,
                  {'cari': len(out), 'acik_fatura': len(open_rows), 'acik_bakiye': round(sum(total.values()), 2), '90_ustu': round(total['90_ustu'], 2)},
                  tables=[('Dilim toplamları', ['Dilim', 'Tutar'], [[b, tr_money(v)] for b, v in total.items()])],
                  files={'acik-faturalar.csv': (['cari', 'fatura_no', 'vade', 'gecikme_gun', 'acik_tutar', 'dilim'], open_rows)},
                  notes=['Tahsilatlar aynı carinin en eski faturasından başlayarak kapatıldı (FIFO). Fatura bazlı eşleşme varsa sonuç farklı olabilir.'])


@op('tahsilat-eslestir', title='Tahsilatları faturalara FIFO ile eşleştir', domain=D, product=P, tag='eşleştirme', samples=CARI, args=CARI_ARGS,
    description='Her carinin tahsilatlarını en eski faturadan başlayarak kapatır; fatura bazında kapanan, kısmen kapanan ve açık kalan tutarları ve kapanış tarihini yazar. Fazla ödemeleri cari bazında raporlar.',
    input='Fatura listesi + tahsilat listesi', output='sonuc.csv (fatura bazında)', command='python main.py --input faturalar.xlsx --payments tahsilat.xlsx',
    expect={'kapanan': 15, 'kismi': 5, 'acik': 16})
def tahsilat(columns, rows, args):
    inv, extra = load_invoices(columns, rows, args)
    out = []
    for i in sorted(inv, key=lambda x: (tr_sort_key(x['cari']), x['tarih'])):
        state = 'kapandı' if i['acik'] <= 0.005 else 'kısmi' if i['acik'] < i['tutar'] - 0.005 else 'açık'
        out.append({'cari': i['cari'], 'fatura_no': i['no'], 'fatura_tarihi': i['tarih'].isoformat(), 'tutar': round(i['tutar'], 2),
                    'kapanan': round(i['tutar'] - i['acik'], 2), 'acik': round(i['acik'], 2), 'durum': state, 'son_tahsilat': i['kapanis']})
    states = Counter(o['durum'] for o in out)
    return Result(f"{states['kapandı']} fatura kapandı, {states['kısmi']} kısmi, {states['açık']} açık", list(out[0]) if out else [], out,
                  {'kapanan': states['kapandı'], 'kismi': states['kısmi'], 'acik': states['açık']},
                  tables=[('Fazla tahsilat', ['Cari', 'Tutar'], [[k, tr_money(v)] for k, v in extra.items()])] if extra else [])


# ---------------------------------------------------------------- banka
@op('banka-mutabakat', title='Banka ekstresi ile defter kayıtlarını mutabık et', domain=D, product=P, tag='banka', samples={'banka_ekstresi.csv': 'banka_ekstresi.csv', 'defter_banka.csv': 'defter_banka.csv'},
    description='Banka ekstresindeki hareketleri muhasebedeki banka hesabı kayıtlarıyla tutar (kuruşu kuruşuna) ve tarih toleransına göre eşleştirir; yalnız bankada (masraf, kayıtsız tahsilat) ve yalnız defterde olan kalemleri ve bakiye farkını çıkarır.',
    input='Ekstre + defter (tarih, açıklama, tutar)', output='sonuc.csv (eşleşmeyenler) + eslesenler.csv', command='python main.py --input ekstre.xlsx --ledger defter.xlsx --days 3',
    args=[arg('--ledger', default='@ornek/defter_banka.csv'), arg('--date', default='tarih'), arg('--amount', default='tutar'), arg('--days', type=int, default=3)],
    expect={'eslesen': 12, 'sadece_banka': 3, 'sadece_defter': 1})
def banka(columns, rows, args):
    lc, lr = tabular.read(args.ledger)
    for c in (args.date, args.amount):
        need(c, columns, 'ekstre')
        need(c, lc, '--ledger')
    bank = [(i, day(r[args.date], 'ekstre tarihi'), round(a, 2), r) for i, (r, a) in enumerate(zip(rows, money(rows, args.amount)))]
    book = [(i, day(r[args.date], 'defter tarihi'), round(a, 2), r) for i, (r, a) in enumerate(zip(lr, money(lr, args.amount)))]
    used, pairs = set(), []
    for i, d, a, r in bank:
        options = [(abs((d2 - d).days), j) for j, d2, a2, _ in book if j not in used and abs(a2 - a) < 0.005 and abs((d2 - d).days) <= args.days]
        if options:
            _, j = min(options)
            used.add(j)
            pairs.append((i, j))
    mb = {i for i, _ in pairs}
    out = [{'kaynak': 'yalnız banka', 'tarih': d.isoformat(), 'tutar': a, 'aciklama': r.get('aciklama', '')} for i, d, a, r in bank if i not in mb]
    out += [{'kaynak': 'yalnız defter', 'tarih': d.isoformat(), 'tutar': a, 'aciklama': r.get('aciklama', '')} for j, d, a, r in book if j not in used]
    matched = [{'banka_tarihi': bank[i][1].isoformat(), 'defter_tarihi': book[j][1].isoformat(), 'tutar': bank[i][2]} for i, j in pairs]
    diff = round(sum(x[2] for x in bank) - sum(x[2] for x in book), 2)
    return Result(f'{len(pairs)} eşleşti; yalnız bankada {len(bank) - len(pairs)}, yalnız defterde {len(book) - len(used)}; hareket farkı {tr_money(diff)}',
                  ['kaynak', 'tarih', 'tutar', 'aciklama'], out,
                  {'eslesen': len(pairs), 'sadece_banka': len(bank) - len(pairs), 'sadece_defter': len(book) - len(used), 'hareket_farki': diff},
                  files={'eslesenler.csv': (['banka_tarihi', 'defter_tarihi', 'tutar'], matched)})


MT_LINE = re.compile(r':61:(\d{6})(\d{4})?(R?[CD])([A-Z])?([\d,]+)([A-Z]\w{3})?(.*)')


@op('mt940-csv', title='MT940 banka ekstresini CSV’ye çevir', domain=D, product=P, tag='MT940', kind='file', samples={'ekstre.sta': 'ekstre.sta'},
    description='SWIFT MT940 ekstre dosyasındaki hesap, açılış/kapanış bakiyesi ve hareketleri (:61: / :86:) satırlara çevirir; açılış + hareketler = kapanış bakiyesi kontrolünü yapar. Muhasebe programına veya mutabakat bloğuna girdi hazırlar.',
    input='.sta / .mt940 / .txt ekstre dosyası', output='sonuc.csv (hareketler) + rapor', command='python main.py --input ekstre.sta',
    expect={'hareket': 6, 'bakiye_tutarli': True, 'kapanis': 113865.25})
def mt940(args):
    text = Path(args.input).read_text(encoding='utf-8-sig', errors='replace')
    lines = []
    for raw in text.splitlines():
        if raw.startswith(':') or not lines:
            lines.append(raw.strip())
        else:
            lines[-1] += ' ' + raw.strip()
    amount = lambda s: float(s.replace(',', '.'))
    account, opening, closing, tx = '', None, None, []
    for l in lines:
        if l.startswith(':25:'):
            account = l[4:]
        elif l[:5] in (':60F:', ':60M:'):
            m = re.match(r':60[FM]:([CD])(\d{6})([A-Z]{3})([\d,]+)', l)
            opening = (amount(m.group(4)) * (1 if m.group(1) == 'C' else -1), m.group(3)) if m else None
        elif l[:5] in (':62F:', ':62M:'):
            m = re.match(r':62[FM]:([CD])(\d{6})([A-Z]{3})([\d,]+)', l)
            closing = amount(m.group(4)) * (1 if m.group(1) == 'C' else -1) if m else None
        elif l.startswith(':61:'):
            m = MT_LINE.match(l)
            if not m:
                raise ValueError(f':61: satırı okunamadı: {l[:60]}')
            sign = -1 if m.group(3) in ('D', 'RC') else 1
            d = datetime.strptime(m.group(1), '%y%m%d').date()
            tx.append({'tarih': d.isoformat(), 'tutar': round(sign * amount(m.group(5)), 2), 'islem_kodu': (m.group(6) or '')[1:], 'referans': m.group(7).strip(), 'aciklama': ''})
        elif l.startswith(':86:') and tx:
            tx[-1]['aciklama'] = l[4:].strip()
    if opening is None or not tx:
        raise ValueError('Dosyada MT940 açılış bakiyesi (:60F:) veya hareket (:61:) bulunamadı.')
    computed = round(opening[0] + sum(t['tutar'] for t in tx), 2)
    ok = closing is not None and abs(computed - closing) < 0.005
    return Result(f"{len(tx)} hareket · açılış {tr_money(opening[0])} → kapanış {tr_money(closing or 0)} {opening[1]} · bakiye {'tutarlı' if ok else 'TUTARSIZ'}",
                  ['tarih', 'tutar', 'islem_kodu', 'referans', 'aciklama'], tx,
                  {'hesap': account, 'hareket': len(tx), 'acilis': opening[0], 'kapanis': closing, 'hesaplanan_kapanis': computed, 'bakiye_tutarli': ok},
                  code=0 if ok else 2)


# ---------------------------------------------------------------- e-fatura
UBL = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2', 'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}


def ubl_files(path):
    p = Path(path)
    files = sorted(p.rglob('*.xml')) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'XML bulunamadı: {p}')
    return files


@op('efatura-csv', title='e-Fatura (UBL-TR) XML’lerini tabloya çevir', domain=D, product=P, tag='e-fatura', kind='file',
    samples={'efatura/ABC2026000000101.xml': 'efatura/ABC2026000000101.xml', 'efatura/ABC2026000000102.xml': 'efatura/ABC2026000000102.xml'}, sample_input='efatura',
    description='Klasördeki UBL-TR e-Fatura/e-Arşiv XML dosyalarından başlık (no, tarih, satıcı/alıcı VKN, tutarlar) ve satır (mal/hizmet, miktar, fiyat, KDV oranı) tabloları üretir. Satır toplamı, KDV ve ödenecek tutar tutarlılığını ve VKN geçerliliğini denetler.',
    input='XML dosyası veya klasörü', output='sonuc.csv (fatura başlıkları) + satirlar.csv', command='python main.py --input C:\\efaturalar\\2026-09',
    expect={'fatura': 2, 'satir_sayisi': 4, 'tutarsiz': 1})
def efatura(args):
    heads, lines, problems = [], [], []
    for f in ubl_files(args.input):
        raw = f.read_bytes()
        if b'<!DOCTYPE' in raw[:2000] or b'<!ENTITY' in raw[:5000]:
            raise ValueError(f'{f.name}: DTD/ENTITY içeren XML güvenlik nedeniyle işlenmez.')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f'{f.name}: XML okunamadı ({exc}).') from None
        t = lambda path, node=root: (node.findtext(path, default='', namespaces=UBL) or '').strip()
        n = lambda path, node=root: float(t(path, node) or 0)
        party = lambda role: root.find(f'cac:{role}/cac:Party', UBL)
        ids = {}
        for role in ('AccountingSupplierParty', 'AccountingCustomerParty'):
            node = party(role)
            ident = node.find('cac:PartyIdentification/cbc:ID', UBL) if node is not None else None
            ids[role] = (ident.text.strip() if ident is not None and ident.text else '', ident.get('schemeID', '') if ident is not None else '')
        line_sum, tax_sum = 0.0, 0.0
        for ln in root.findall('cac:InvoiceLine', UBL):
            amt = n('cbc:LineExtensionAmount', ln)
            tax = n('cac:TaxTotal/cbc:TaxAmount', ln)
            line_sum += amt
            tax_sum += tax
            lines.append({'fatura_no': t('cbc:ID'), 'satir': t('cbc:ID', ln), 'urun': t('cac:Item/cbc:Name', ln), 'miktar': t('cbc:InvoicedQuantity', ln),
                          'birim_fiyat': t('cac:Price/cbc:PriceAmount', ln), 'tutar': amt, 'kdv_orani': t('cac:TaxTotal/cac:TaxSubtotal/cbc:Percent', ln), 'kdv': tax})
        head = {'dosya': f.name, 'fatura_no': t('cbc:ID'), 'tarih': t('cbc:IssueDate'), 'profil': t('cbc:ProfileID'), 'tip': t('cbc:InvoiceTypeCode'),
                'para_birimi': t('cbc:DocumentCurrencyCode'), 'satici_no': ids['AccountingSupplierParty'][0], 'alici_no': ids['AccountingCustomerParty'][0],
                'mal_hizmet_toplam': n('cac:LegalMonetaryTotal/cbc:LineExtensionAmount'), 'kdv_toplam': n('cac:TaxTotal/cbc:TaxAmount'),
                'odenecek': n('cac:LegalMonetaryTotal/cbc:PayableAmount'), 'kontrol': ''}
        issues = []
        if abs(line_sum - head['mal_hizmet_toplam']) > 0.01:
            issues.append(f"satır toplamı {line_sum:.2f} ≠ {head['mal_hizmet_toplam']:.2f}")
        if abs(tax_sum - head['kdv_toplam']) > 0.01:
            issues.append(f"satır KDV toplamı {tax_sum:.2f} ≠ {head['kdv_toplam']:.2f}")
        incl = n('cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount')
        if abs(head['mal_hizmet_toplam'] + head['kdv_toplam'] - incl) > 0.01 or abs(incl - head['odenecek']) > 0.01:
            issues.append(f"vergiler dahil {incl:.2f} / ödenecek {head['odenecek']:.2f} uyuşmuyor (iskonto/kesinti olabilir)")
        for role, (no, scheme) in ids.items():
            err = tr.vkn(no) if scheme == 'VKN' else tr.tckn(no) if scheme == 'TCKN' else ''
            if err:
                issues.append(f"{'satıcı' if 'Supplier' in role else 'alıcı'} {scheme} geçersiz ({err})")
        head['kontrol'] = '; '.join(issues) or 'tutarlı'
        problems += [[f.name, i] for i in issues]
        heads.append(head)
    return Result(f'{len(heads)} fatura, {len(lines)} satır; {sum(h["kontrol"] != "tutarlı" for h in heads)} faturada tutarsızlık', list(heads[0]), heads,
                  {'fatura': len(heads), 'satir_sayisi': len(lines), 'tutarsiz': sum(h['kontrol'] != 'tutarlı' for h in heads)},
                  tables=[('Tutarsızlıklar', ['Dosya', 'Sorun'], problems)] if problems else [],
                  files={'satirlar.csv': (['fatura_no', 'satir', 'urun', 'miktar', 'birim_fiyat', 'tutar', 'kdv_orani', 'kdv'], lines)},
                  notes=['İmza, GİB kabul durumu ve şema doğrulaması yapılmaz; yalnızca içerik ve tutar tutarlılığı kontrol edilir.'])


# ---------------------------------------------------------------- vergi ve kur
@op('kdv-kontrol', title='KDV hesaplarını ve oranlarını kontrol et', domain=D, product=P, tag='KDV', samples={'kdv_satirlari.csv': 'kdv_satirlari.csv'},
    description='Her satırda matrah × KDV oranının beyan edilen KDV tutarına eşit olup olmadığını (kuruş toleransıyla) ve oranın güncel oranlardan (%0, %1, %10, %20) biri olup olmadığını kontrol eder; 2023 öncesi %8/%18 oranlarını ayrıca işaretler.',
    input='CSV/XLSX (matrah, KDV oranı, KDV tutarı)', output='sonuc.csv', command='python main.py --base matrah --rate kdv_orani --vat kdv_tutari',
    args=[arg('--base', default='matrah'), arg('--rate', default='kdv_orani'), arg('--vat', default='kdv_tutari'), arg('--tolerance', type=float, default=0.05)],
    expect={'hatali_tutar': 1, 'eski_oran': 1, 'gecersiz_oran': 0})
def kdv(columns, rows, args):
    base, rate, vat = (money(rows, need(c, columns, f'--{n}')) for c, n in ((args.base, 'base'), (args.rate, 'rate'), (args.vat, 'vat')))
    out, kinds = [], Counter()
    for r, b, rt, v in zip(rows, base, rate, vat):
        expected = round(b * rt / 100, 2)
        issues = []
        if abs(expected - v) > args.tolerance:
            issues.append(f'beklenen {tr_money(expected)}, fark {tr_money(v - expected)}')
            kinds['hatali_tutar'] += 1
        if rt in (8, 18):
            issues.append('10.07.2023 öncesi oran (%8/%18)')
            kinds['eski_oran'] += 1
        elif rt not in (0, 1, 10, 20):
            issues.append('genel oranlardan biri değil')
            kinds['gecersiz_oran'] += 1
        out.append({**r, 'beklenen_kdv': expected, 'kontrol': '; '.join(issues) or 'doğru'})
    return Result(f"{len(rows)} satır; {kinds['hatali_tutar']} hatalı tutar, {kinds['eski_oran']} eski oran", columns + ['beklenen_kdv', 'kontrol'], out,
                  {'hatali_tutar': kinds['hatali_tutar'], 'eski_oran': kinds['eski_oran'], 'gecersiz_oran': kinds['gecersiz_oran']},
                  notes=['İstisna, tevkifat ve özel matrah durumları dikkate alınmaz.', NOTE])


@op('kur-cevir', title='Döviz tutarlarını tarihli kurla TL’ye çevir', domain=D, product=P, tag='kur', samples={'doviz_islemleri.csv': 'doviz_islemleri.csv', 'kurlar.csv': 'kurlar.csv'},
    description='Her işlemi kendi tarihindeki kurla, o gün kur yoksa (hafta sonu, tatil) en yakın önceki iş günü kuruyla TL’ye çevirir; kullanılan kur tarihini yazar. Kur bulunamayan işlemleri tahmin etmeden listeler.',
    input='İşlemler (tarih, döviz, tutar) + kur dosyası (tarih, döviz, kur)', output='sonuc.csv', command='python main.py --rates tcmb_kurlar.csv --max-gap 5',
    args=[arg('--rates', default='@ornek/kurlar.csv'), arg('--date', default='tarih'), arg('--currency', default='doviz'), arg('--amount', default='tutar'),
          arg('--max-gap', type=int, default=4, help='En fazla kaç gün önceki kur kullanılabilir')],
    expect={'cevrilen': 4, 'kur_yok': 2})
def kur(columns, rows, args):
    rc, rr = tabular.read(args.rates)
    for c in ('tarih', 'doviz', 'kur'):
        need(c, rc, '--rates')
    table = defaultdict(dict)
    for r, k in zip(rr, numbers(rr, 'kur')):
        if k:
            table[r['doviz'].strip().upper()][day(r['tarih'], 'kur tarihi')] = k
    amounts = money(rows, need(args.amount, columns, '--amount'))
    out, miss = [], 0
    for r, a in zip(rows, amounts):
        cur = r[need(args.currency, columns, '--currency')].strip().upper()
        d = day(r[need(args.date, columns, '--date')], 'işlem tarihi')
        rate, used = (1.0, d) if cur in ('TRY', 'TL') else (None, None)
        if rate is None:
            for back in range(args.max_gap + 1):
                k = table.get(cur, {}).get(d - timedelta(days=back))
                if k:
                    rate, used = k, d - timedelta(days=back)
                    break
        if rate is None:
            miss += 1
            out.append({**r, 'kur': '', 'kur_tarihi': '', 'tutar_tl': '', 'not': 'kur bulunamadı'})
        else:
            out.append({**r, 'kur': rate, 'kur_tarihi': used.isoformat(), 'tutar_tl': round(a * rate, 2), 'not': '' if used == d else 'önceki iş günü kuru'})
    return Result(f'{len(rows) - miss} işlem çevrildi, {miss} işlemde kur yok', columns + ['kur', 'kur_tarihi', 'tutar_tl', 'not'], out,
                  {'cevrilen': len(rows) - miss, 'kur_yok': miss, 'toplam_tl': round(sum(o['tutar_tl'] or 0 for o in out if o['tutar_tl'] != ''), 2)})


# ---------------------------------------------------------------- denetim analitiği
BENFORD = [math.log10(1 + 1 / d) for d in range(1, 10)]


@op('benford-analizi', title='Benford yasası ile tutar dağılımı testi', domain=D, product=P, tag='Benford', samples={'fatura_tutarlari.csv': 'fatura_tutarlari.csv'},
    description='Tutarların ilk hane dağılımını Benford yasasıyla karşılaştırır; ki-kare ve ortalama mutlak sapma (MAD, Nigrini eşikleri) ile uyumu sınıflandırır ve en çok sapan haneleri gösterir. İç denetimde inceleme önceliği belirlemek içindir; tek başına hile kanıtı değildir.',
    input='CSV/XLSX + tutar sütunu (en az ~300 kayıt önerilir)', output='sonuc.csv (hane dağılımı) + rapor', command='python main.py --column tutar',
    args=[arg('--column', default='tutar'), arg('--min', type=float, default=10.0, help='Bu değerin altındaki tutarlar dışarıda kalır')],
    expect={'kayit': 960, 'en_sapan_hane': 4, 'uyum': 'kabul edilebilir'})
def benford(columns, rows, args):
    vals = [abs(n) for n in numbers(rows, need(args.column, columns, '--column')) if n is not None and abs(n) >= args.min]
    if len(vals) < 50:
        raise ValueError(f'Benford testi için çok az kayıt ({len(vals)}); en az 50, tercihen 300+ kayıt gerekir.')
    first = Counter(int(f'{v:e}'[0]) for v in vals)
    n = len(vals)
    out, chi, mad = [], 0.0, 0.0
    for d in range(1, 10):
        obs = first[d] / n
        exp = BENFORD[d - 1]
        chi += n * (obs - exp) ** 2 / exp
        mad += abs(obs - exp) / 9
        z = (abs(obs - exp) - 1 / (2 * n)) / math.sqrt(exp * (1 - exp) / n)
        out.append({'hane': d, 'adet': first[d], 'gozlenen_%': round(100 * obs, 2), 'beklenen_%': round(100 * exp, 2), 'fark_%': round(100 * (obs - exp), 2), 'z': round(z, 2)})
    conform = 'yakın uyum' if mad < 0.006 else 'kabul edilebilir' if mad < 0.012 else 'sınırda' if mad < 0.015 else 'uyumsuz'
    worst = max(out, key=lambda o: o['z'])
    return Result(f'{n} tutar · MAD {mad:.4f} → {conform} · en çok sapan hane {worst["hane"]} (z={worst["z"]})', list(out[0]), out,
                  {'kayit': n, 'mad': round(mad, 4), 'ki_kare': round(chi, 2), 'uyum': conform, 'en_sapan_hane': worst['hane']},
                  notes=['Ki-kare kritik değeri (8 sd, %5): 15,51. z > 1,96 olan haneler incelenmeye değer.',
                         'Benford sapması hileyi kanıtlamaz; fiyat listeleri, eşik altı onay limitleri ve sabit tutarlı ödemeler de sapma yaratır.'])


def inv_key(v):
    return re.sub(r'[^0-9a-z]', '', fold(v))


@op('mukerrer-odeme', title='Mükerrer ödeme şüphelerini bul', domain=D, product=P, tag='mükerrer ödeme', samples={'tedarikci_odemeleri.csv': 'tedarikci_odemeleri.csv'},
    description='Aynı tedarikçiye aynı fatura numarasıyla (A-2003 / A2003 gibi yazım farkları dahil) veya kısa süre içinde birebir aynı tutarla yapılmış ödemeleri eşleştirir. İç denetim ve ödeme öncesi kontrol için şüpheli çift listesi üretir.',
    input='Ödeme listesi (tedarikçi, tarih, fatura no, tutar)', output='sonuc.csv (şüpheli çiftler)', command='python main.py --days 30',
    args=[arg('--party', default='tedarikci'), arg('--date', default='tarih'), arg('--invoice', default='fatura_no'), arg('--amount', default='tutar'),
          arg('--days', type=int, default=30), arg('--id', default='odeme_no')],
    expect={'supheli_cift': 2, 'ayni_fatura': 1, 'ayni_tutar': 1})
def mukerrer(columns, rows, args):
    amounts = money(rows, need(args.amount, columns, '--amount'))
    items = [(r, day(r[need(args.date, columns, '--date')], 'ödeme tarihi'), round(a, 2)) for r, a in zip(rows, amounts)]
    pid = args.id if args.id in columns else columns[0]
    out = []
    for (r1, d1, a1), (r2, d2, a2) in itertools_pairs(items):
        if r1[args.party].strip() != r2[need(args.party, columns, '--party')].strip():
            continue
        same_inv = args.invoice in columns and inv_key(r1[args.invoice]) and inv_key(r1[args.invoice]) == inv_key(r2[args.invoice])
        same_amt = abs(a1 - a2) < 0.005 and abs((d2 - d1).days) <= args.days
        if same_inv or same_amt:
            out.append({'odeme_1': r1[pid], 'odeme_2': r2[pid], 'tedarikci': r1[args.party], 'tutar_1': a1, 'tutar_2': a2,
                        'gun_farki': abs((d2 - d1).days), 'neden': 'aynı fatura no' if same_inv else 'aynı tutar, yakın tarih'})
    kinds = Counter(o['neden'] for o in out)
    return Result(f'{len(out)} şüpheli ödeme çifti', ['odeme_1', 'odeme_2', 'tedarikci', 'tutar_1', 'tutar_2', 'gun_farki', 'neden'], out,
                  {'supheli_cift': len(out), 'ayni_fatura': kinds['aynı fatura no'], 'ayni_tutar': kinds['aynı tutar, yakın tarih'],
                   'risk_tutari': round(sum(o['tutar_2'] for o in out), 2)},
                  notes=['Şüpheli çiftler kaynak belgeyle kontrol edilmelidir; taksitli ve düzenli ödemeler aynı tutarda olabilir.'])


def itertools_pairs(items):
    import itertools
    return itertools.combinations(items, 2)


# ---------------------------------------------------------------- planlar
@op('amortisman-plani', title='Amortisman planı (normal / azalan bakiyeler)', domain=D, product=P, tag='amortisman', samples={'duran_varliklar.csv': 'duran_varliklar.csv'},
    description='Duran varlık listesinden yıllık amortisman planı çıkarır: normal (doğrusal) yöntemde maliyet/ömür, azalan bakiyelerde net değer × (2/ömür, en çok %50) ve son yılda kalan tutarın tamamı. Birikmiş amortisman ve net defter değerini yıl yıl yazar.',
    input='Varlık listesi (varlık, maliyet, başlangıç tarihi, ömür yılı, yöntem)', output='sonuc.csv (varlık × yıl) + rapor', command='python main.py --year 2026',
    args=[arg('--year', type=int, default=2026, help='Rapor yılı (o yılın giderini özetler)')],
    expect={'plan_satiri': 24, 'yil_gideri': 762000.0})
def amortisman(columns, rows, args):
    for c in ('varlik', 'maliyet', 'baslangic', 'omur_yil'):
        need(c, columns, 'varlık listesi')
    costs = money(rows, 'maliyet')
    out, year_total = [], 0.0
    for r, cost in zip(rows, costs):
        life = int(float(r['omur_yil'].replace(',', '.')))
        if life <= 0:
            raise ValueError(f"{r['varlik']}: faydalı ömür pozitif olmalı.")
        start = day(r['baslangic'], 'başlangıç').year
        method = r.get('yontem', 'normal').strip().lower() or 'normal'
        nbv, acc = cost, 0.0
        for i in range(life):
            if method.startswith('azalan'):
                amount = nbv if i == life - 1 else round(nbv * min(2 / life, 0.5), 2)
            else:
                amount = cost - acc if i == life - 1 else round(cost / life, 2)
            acc += amount
            nbv = cost - acc
            if start + i == args.year:
                year_total += amount
            out.append({'varlik': r['varlik'], 'yil': start + i, 'yontem': method, 'amortisman': round(amount, 2), 'birikmis': round(acc, 2), 'net_defter_degeri': round(nbv, 2)})
    return Result(f'{len(rows)} varlık için {len(out)} satırlık plan; {args.year} gideri {tr_money(year_total)}', ['varlik', 'yil', 'yontem', 'amortisman', 'birikmis', 'net_defter_degeri'], out,
                  {'plan_satiri': len(out), 'yil_gideri': round(year_total, 2)},
                  notes=['Edinim yılında tam yıl amortisman ayrılmıştır (VUK genel uygulaması); binek otomobillerde kıst amortisman uygulanır, ayrıca hesaplayın.',
                         'Faydalı ömürler için VUK amortisman listesini kontrol edin.', NOTE])


@op('kredi-odeme-plani', title='Eşit taksitli kredi ödeme planı', domain=D, product=P, tag='kredi', kind='none', samples={},
    description='Anapara, aylık faiz oranı ve vadeden eşit taksitli ödeme planı üretir; her taksitte faiz, faiz üzerinden vergi (BSMV/KKDF), anapara ve kalan borcu ayırır. Kuruş yuvarlaması son taksitte kapatılır.',
    input='Anapara, aylık faiz %, vade (ay), isteğe bağlı vergi %', output='sonuc.csv (taksit planı)', command='python main.py --principal 1000000 --rate 3.29 --months 24 --tax 0',
    args=[arg('--principal', type=float, default=1000000.0), arg('--rate', type=float, default=3.5, help='Aylık faiz oranı (%%)'), arg('--months', type=int, default=12),
          arg('--tax', type=float, default=0.0, help='Faiz üzerinden alınan vergi toplamı (%%), örn. bireysel krediler için BSMV+KKDF'), arg('--start', default='2026-11-01')],
    expect={'taksit_sayisi': 12, 'toplam_anapara': 1000000.0, 'taksit': 103484.0})
def kredi(args):
    if args.principal <= 0 or args.months <= 0 or args.rate < 0:
        raise ValueError('Anapara ve vade pozitif, faiz negatif olmamalı.')
    r = args.rate / 100 * (1 + args.tax / 100)
    payment = round(args.principal / args.months if r == 0 else args.principal * r / (1 - (1 + r) ** -args.months), 2)
    start = day(args.start, '--start')
    left, out = args.principal, []
    for i in range(1, args.months + 1):
        interest = round(left * args.rate / 100, 2)
        tax = round(interest * args.tax / 100, 2)
        principal = round(payment - interest - tax, 2) if i < args.months else round(left, 2)
        pay = principal + interest + tax
        left = round(left - principal, 2)
        month = (start.month - 1 + i - 1)
        due = date(start.year + month // 12, month % 12 + 1, min(start.day, 28))
        out.append({'taksit': i, 'tarih': due.isoformat(), 'taksit_tutari': round(pay, 2), 'faiz': interest, 'vergi': tax, 'anapara': principal, 'kalan': left})
    total_int = round(sum(o['faiz'] + o['vergi'] for o in out), 2)
    return Result(f'{args.months} taksit × {tr_money(payment)}; toplam faiz+vergi {tr_money(total_int)}', list(out[0]), out,
                  {'taksit_sayisi': len(out), 'taksit': round(payment), 'toplam_anapara': round(sum(o['anapara'] for o in out), 2), 'toplam_faiz_vergi': total_int},
                  notes=['Dosya masrafı, sigorta ve ara ödemeler dahil değildir. Bankanın ödeme planıyla karşılaştırın.'])


@op('stok-maliyeti', title='Stok maliyeti: FIFO ve ağırlıklı ortalama', domain=D, product=P, tag='stok maliyeti', samples={'stok_hareketleri.csv': 'stok_hareketleri.csv'},
    description='Giriş/çıkış hareketlerinden ürün bazında satılan malın maliyetini ve dönem sonu stok değerini hem FIFO hem hareketli ağırlıklı ortalama yöntemiyle hesaplar; stok eksiye düşen çıkışları işaretler.',
    input='Stok hareketleri (tarih, ürün, tür giriş/çıkış, miktar, birim maliyet)', output='sonuc.csv (ürün özeti) + rapor', command='python main.py --input hareketler.xlsx',
    expect={'urun': 3, 'eksi_stok': 2, 'smm_fifo': 454600.0})
def stok(columns, rows, args):
    for c in ('tarih', 'urun', 'tur', 'miktar', 'birim_maliyet'):
        need(c, columns, 'hareket dosyası')
    qty, cost = numbers(rows, 'miktar'), numbers(rows, 'birim_maliyet')
    moves = sorted(zip(rows, qty, cost), key=lambda x: (x[0]['urun'], day(x[0]['tarih'], 'tarih')))
    state = defaultdict(lambda: {'fifo': deque(), 'avg_q': 0.0, 'avg_v': 0.0, 'cogs_fifo': 0.0, 'cogs_avg': 0.0, 'neg': 0})
    for r, q, c in moves:
        s = state[r['urun'].strip()]
        q = q or 0.0
        if fold(r['tur']).startswith('g'):
            if c is None:
                raise ValueError(f"{r['urun']} {r['tarih']}: girişte birim maliyet yok.")
            s['fifo'].append([q, c])
            s['avg_q'] += q
            s['avg_v'] += q * c
            continue
        need_q = q
        while need_q > 1e-9 and s['fifo']:
            lot = s['fifo'][0]
            use = min(lot[0], need_q)
            s['cogs_fifo'] += use * lot[1]
            lot[0] -= use
            need_q -= use
            if lot[0] <= 1e-9:
                s['fifo'].popleft()
        avg = s['avg_v'] / s['avg_q'] if s['avg_q'] > 1e-9 else 0.0
        take = min(q, s['avg_q'])
        s['cogs_avg'] += take * avg
        s['avg_q'] -= take
        s['avg_v'] -= take * avg
        if need_q > 1e-9:
            s['neg'] += 1
    out = []
    for u, s in sorted(state.items()):
        end_q = sum(l[0] for l in s['fifo'])
        out.append({'urun': u, 'smm_fifo': round(s['cogs_fifo'], 2), 'smm_ortalama': round(s['cogs_avg'], 2), 'kalan_miktar': round(end_q, 3),
                    'stok_degeri_fifo': round(sum(l[0] * l[1] for l in s['fifo']), 2), 'stok_degeri_ortalama': round(s['avg_v'], 2),
                    'eksi_stok_cikisi': s['neg']})
    return Result(f'{len(out)} ürün; SMM (FIFO) {tr_money(sum(o["smm_fifo"] for o in out))}', list(out[0]), out,
                  {'urun': len(out), 'smm_fifo': round(sum(o['smm_fifo'] for o in out), 2), 'smm_ortalama': round(sum(o['smm_ortalama'] for o in out), 2),
                   'eksi_stok': sum(o['eksi_stok_cikisi'] for o in out)},
                  notes=['Stoku aşan çıkışların eksik kısmı maliyetlendirilmedi; hareket sırasını ve açılış stokunu kontrol edin.'])


@op('gider-siniflandir', title='Giderleri anahtar kelime kurallarıyla sınıflandır', domain=D, product=P, tag='gider', samples={'giderler.csv': 'giderler.csv', 'gider_kurallari.csv': 'gider_kurallari.csv'},
    description='Gider açıklamalarını bir kural dosyasındaki anahtar kelimelere göre (Türkçe karakter ve büyük/küçük harf duyarsız) kategorilere atar; kategori toplamlarını ve sınıflanamayan kalemleri çıkarır. Kural dosyası düzenlenerek tekrar çalıştırılır.',
    input='Gider listesi + kural dosyası (anahtar_kelime, kategori)', output='sonuc.csv + kategori toplamları', command='python main.py --rules kurallar.csv --text aciklama',
    args=[arg('--rules', default='@ornek/gider_kurallari.csv'), arg('--text', default='aciklama'), arg('--amount', default='tutar')],
    expect={'siniflanan': 9, 'siniflanmayan': 1})
def gider(columns, rows, args):
    rc, rr = tabular.read(args.rules)
    need('anahtar_kelime', rc, '--rules')
    need('kategori', rc, '--rules')
    rules = sorted(((fold(r['anahtar_kelime']), r['kategori'].strip()) for r in rr if r['anahtar_kelime'].strip()), key=lambda x: -len(x[0]))
    amounts = money(rows, need(args.amount, columns, '--amount')) if args.amount in columns else [0.0] * len(rows)
    out, totals = [], defaultdict(float)
    for r, a in zip(rows, amounts):
        text = fold(r[need(args.text, columns, '--text')])
        hit = next(((k, c) for k, c in rules if k in text), None)
        cat = hit[1] if hit else 'SINIFLANMADI'
        totals[cat] += a
        out.append({**r, 'kategori': cat, 'kural': hit[0] if hit else ''})
    unclassified = sum(o['kategori'] == 'SINIFLANMADI' for o in out)
    return Result(f'{len(rows) - unclassified} kalem sınıflandı, {unclassified} sınıflanamadı', columns + ['kategori', 'kural'], out,
                  {'siniflanan': len(rows) - unclassified, 'siniflanmayan': unclassified},
                  tables=[('Kategori toplamları', ['Kategori', 'Tutar'], [[k, tr_money(v)] for k, v in sorted(totals.items(), key=lambda kv: -kv[1])])])


@op('nakit-akisi', title='Haftalık/aylık nakit akışı ve bakiye', domain=D, product=P, tag='nakit akışı', samples={'nakit_hareketleri.csv': 'nakit_hareketleri.csv'},
    description='İşaretli nakit hareketlerinden (giriş +, çıkış −) hafta veya ay bazında giriş, çıkış, net akış ve açılış bakiyesinden yürüyen kapanış bakiyesi tablosu üretir; bakiyenin eksiye düştüğü dönemleri işaretler.',
    input='Hareketler (tarih, tutar, isteğe bağlı kategori) + açılış bakiyesi', output='sonuc.csv (dönem tablosu)', command='python main.py --opening 50000 --period hafta',
    args=[arg('--date', default='tarih'), arg('--amount', default='tutar'), arg('--opening', type=float, default=50000.0), arg('--period', choices=['hafta', 'ay'], default='hafta')],
    expect={'donem': 3, 'eksi_donem': 2})
def nakit(columns, rows, args):
    amounts = money(rows, need(args.amount, columns, '--amount'))
    buckets = defaultdict(lambda: [0.0, 0.0])
    for r, a in zip(rows, amounts):
        d = day(r[need(args.date, columns, '--date')], 'tarih')
        key = '{}-H{:02d}'.format(*d.isocalendar()[:2]) if args.period == 'hafta' else d.strftime('%Y-%m')
        buckets[key][0 if a >= 0 else 1] += a
    bal, out = args.opening, []
    for k in sorted(buckets):
        inflow, outflow = buckets[k]
        opening = bal
        bal += inflow + outflow
        out.append({'donem': k, 'acilis': round(opening, 2), 'giris': round(inflow, 2), 'cikis': round(outflow, 2), 'net': round(inflow + outflow, 2),
                    'kapanis': round(bal, 2), 'uyari': 'bakiye eksi' if bal < 0 else ''})
    neg = sum(bool(o['uyari']) for o in out)
    return Result(f'{len(out)} dönem; kapanış bakiyesi {tr_money(bal)}; {neg} dönemde bakiye eksi', list(out[0]), out,
                  {'donem': len(out), 'kapanis': round(bal, 2), 'eksi_donem': neg, 'en_dusuk': round(min(o['kapanis'] for o in out), 2)})
