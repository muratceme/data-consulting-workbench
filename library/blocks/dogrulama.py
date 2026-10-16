"""Veri doğrulama blokları: Türkiye'ye özgü kimlik/finans alanları ve tablolar arası tutarlılık kuralları."""
import hashlib, itertools, json, re
from collections import Counter, defaultdict
from pathlib import Path
import tabular
import turkiye as tr
from islem import op, arg, Result, col_list, need, numbers, fmt, fold

D, P = 'quality', 'Veri doğrulama kuralları'
KIMLIK = {'kimlik_listesi.csv': 'kimlik_listesi.csv'}
SATIS = {'satislar.csv': 'satislar.csv'}
REQ = []
REQUIRES = ['turkiye']  # imported at module level, so every package of this module ships it


def check_column(columns, rows, column, check, label, extra=None):
    need(column, columns, '--column')
    out, bad, blank = [], [], 0
    for i, r in enumerate(rows, start=2):
        v = r[column].strip()
        new = dict(r)
        if not v:
            blank += 1
            new[f'{label}_gecerli'], new[f'{label}_hata'] = '', 'boş'
        else:
            err = check(v)
            new[f'{label}_gecerli'], new[f'{label}_hata'] = ('evet', '') if not err else ('hayır', err)
            if err:
                bad.append([i, r.get(columns[0], ''), err])
        if extra:
            new.update(extra(v))
        out.append(new)
    return out, bad, blank


def validity(columns, rows, args, check, label, what, extra=None, extra_cols=()):
    out, bad, blank = check_column(columns, rows, args.column, check, label, extra)
    valid = len(rows) - len(bad) - blank
    cols = columns + [f'{label}_gecerli', f'{label}_hata', *extra_cols]
    return Result(f'{valid} geçerli, {len(bad)} geçersiz {what}' + (f', {blank} boş' if blank else ''), cols, out,
                  {'gecerli': valid, 'gecersiz': len(bad), 'bos': blank},
                  tables=[('Geçersiz değerler', ['Satır', columns[0], 'Neden'], bad)] if bad else [])


@op('tckn-dogrula', title='T.C. kimlik numaralarını doğrula', domain=D, product=P, tag='TCKN', samples=KIMLIK, requires=REQ,
    description='TCKN sütununu 11 hane, ilk hane ve iki kontrol hanesi algoritmasıyla denetler; her satıra geçerli/geçersiz ve nedenini ekler. Numara hiçbir servise sorgulanmaz, yalnızca yapısal geçerlilik kontrol edilir.',
    input='CSV/XLSX + TCKN sütunu', output='sonuc.csv + geçersiz liste', command='python main.py --column tckn',
    args=[arg('--column', default='tckn')], expect={'gecerli': 6, 'gecersiz': 2})
def tckn_dogrula(columns, rows, args):
    return validity(columns, rows, args, tr.tckn, 'tckn', 'TCKN')


@op('vkn-dogrula', title='Vergi kimlik numaralarını (VKN) doğrula', domain=D, product=P, tag='VKN', samples=KIMLIK, requires=REQ,
    description='10 haneli vergi kimlik numaralarını Gelir İdaresi kontrol hanesi algoritmasıyla denetler. Cari kart ve e-fatura ana verisi temizliği için; numara dış servise sorulmaz.',
    input='CSV/XLSX + VKN sütunu', output='sonuc.csv', command='python main.py --column vkn',
    args=[arg('--column', default='vkn')], expect={'gecerli': 7, 'gecersiz': 1})
def vkn_dogrula(columns, rows, args):
    return validity(columns, rows, args, tr.vkn, 'vkn', 'VKN')


@op('iban-dogrula', title='IBAN doğrula ve bankayı bul', domain=D, product=P, tag='IBAN', samples=KIMLIK, requires=REQ,
    description='IBAN’ları uzunluk ve mod-97 kontrol haneleriyle doğrular, TR IBAN’larda banka kodundan banka adını çıkarır, boşluksuz standart biçimi ekler. Ödeme listesi göndermeden önce hatalı IBAN’ı yakalar.',
    input='CSV/XLSX + IBAN sütunu', output='sonuc.csv', command='python main.py --column iban',
    args=[arg('--column', default='iban')], expect={'gecerli': 6, 'gecersiz': 2, 'bilinmeyen_banka': 1})
def iban_dogrula(columns, rows, args):
    res = validity(columns, rows, args, tr.iban, 'iban', 'IBAN',
                   lambda v: {'iban_standart': re.sub(r'\s+', '', v).upper(), 'banka': tr.iban_bank(v) if not tr.iban(v) else ''}, ('iban_standart', 'banka'))
    res.summary['bilinmeyen_banka'] = sum(r['banka'] == 'bilinmiyor' for r in res.rows)
    res.notes.append('Banka adı listesi büyük bankalarla sınırlıdır; “bilinmiyor” IBAN’ın hatalı olduğu anlamına gelmez.')
    return res


def phone(v):
    d = tr.digits(v)
    if d.startswith('0090'):
        d = d[4:]
    elif d.startswith('90') and len(d) == 12:
        d = d[2:]
    elif d.startswith('0') and len(d) == 11:
        d = d[1:]
    if len(d) != 10:
        return '', '', 'hane sayısı hatalı'
    kind = 'cep' if d[0] == '5' else 'sabit' if d[0] in '234' else 'özel/ücretsiz' if d[0] == '8' else ''
    if not kind:
        return '', '', 'Türkiye numarası değil'
    return f'+90{d}', kind, ''


@op('telefon-normalize', title='Telefon numaralarını standart biçime getir', domain=D, product=P, tag='telefon', samples=KIMLIK, requires=REQ,
    description='“0532 111 22 33”, “+90 (216) 555 44 33”, “5321234567” gibi yazımları uluslararası standart +905321112233 biçimine çevirir; cep/sabit ayrımını yapar, hatalı numaraları işaretler. SMS ve çağrı merkezi listeleri için.',
    input='CSV/XLSX + telefon sütunu', output='sonuc.csv', command='python main.py --column telefon',
    args=[arg('--column', default='telefon')], expect={'gecerli': 7, 'gecersiz': 1, 'cep': 5})
def telefon(columns, rows, args):
    def extra(v):
        e164, kind, _ = phone(v) if v else ('', '', '')
        return {'telefon_e164': e164, 'telefon_turu': kind}
    res = validity(columns, rows, args, lambda v: phone(v)[2], 'telefon', 'telefon', extra, ('telefon_e164', 'telefon_turu'))
    res.summary['cep'] = sum(r['telefon_turu'] == 'cep' for r in res.rows)
    return res


TYPO = {'gmial.com': 'gmail.com', 'gmai.com': 'gmail.com', 'gmail.co': 'gmail.com', 'gmal.com': 'gmail.com', 'hotmial.com': 'hotmail.com',
        'hotmal.com': 'hotmail.com', 'hotmail.co': 'hotmail.com', 'yaho.com': 'yahoo.com', 'yahoo.co': 'yahoo.com', 'outlok.com': 'outlook.com',
        'icloud.co': 'icloud.com', 'gmail.com.tr': 'gmail.com'}
EMAIL = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$')


def email_error(v):
    if v.count('@') != 1:
        return '@ sayısı 1 değil'
    if ' ' in v:
        return 'boşluk içeriyor'
    if not EMAIL.match(v):
        return 'biçim hatalı (alan adı/uzantı)'
    return ''


@op('eposta-dogrula', title='E-posta adreslerini doğrula ve yazım hatalarını öner', domain=D, product=P, tag='e-posta', samples=KIMLIK, requires=REQ,
    description='E-posta adreslerinin biçimini denetler, küçük harfe indirir ve “gmial.com → gmail.com” gibi sık alan adı yazım hatalarına düzeltme önerir. Adreslere e-posta gönderilmez; yalnızca yazım kontrol edilir.',
    input='CSV/XLSX + e-posta sütunu', output='sonuc.csv', command='python main.py --column eposta',
    args=[arg('--column', default='eposta')], expect={'gecerli': 5, 'gecersiz': 3, 'duzeltme_onerisi': 2})
def eposta(columns, rows, args):
    def extra(v):
        domain = v.lower().rsplit('@', 1)[-1] if '@' in v else ''
        fix = v.lower().rsplit('@', 1)[0] + '@' + TYPO[domain] if domain in TYPO else ''
        return {'eposta_standart': v.strip().lower() if not email_error(v) else '', 'oneri': fix}
    res = validity(columns, rows, args, email_error, 'eposta', 'e-posta', extra, ('eposta_standart', 'oneri'))
    res.summary['duzeltme_onerisi'] = sum(bool(r['oneri']) for r in res.rows)
    return res


@op('plaka-dogrula', title='Araç plakalarını doğrula', domain=D, product=P, tag='plaka', samples=KIMLIK, requires=REQ,
    description='Türk plakalarını il kodu (01-81), harf grubu (Q, W, X ve Türkçe harf yok) ve harf/rakam sayısı kurallarına göre denetler; “34abc123” gibi yazımları “34 ABC 123” standardına çevirir ve ili ekler. Filo ve otopark verileri için.',
    input='CSV/XLSX + plaka sütunu', output='sonuc.csv', command='python main.py --column plaka',
    args=[arg('--column', default='plaka')], expect={'gecerli': 4, 'gecersiz': 4})
def plaka(columns, rows, args):
    def extra(v):
        std, err = tr.plaka(v) if v else ('', '')
        return {'plaka_standart': std, 'plaka_ili': tr.PLAKA[int(std[:2])] if std else ''}
    return validity(columns, rows, args, lambda v: tr.plaka(v)[1], 'plaka', 'plaka', extra, ('plaka_standart', 'plaka_ili'))


@op('il-normalize', title='İl adlarını 81 il listesine eşle', domain=D, product=P, tag='il', samples=KIMLIK, requires=REQ,
    description='“ISTANBUL”, “izmir”, “K.Maraş”, “İçel”, “34” gibi yazımları resmî il adına ve plaka koduna eşler; yazım farkı, eski ad ve yakın eşleşmeleri ayrı gösterir. Bulunamayanları değiştirmez.',
    input='CSV/XLSX + il sütunu', output='sonuc.csv', command='python main.py --column sehir',
    args=[arg('--column', default='il')], expect={'eslesen': 8, 'bulunamayan': 0})
def il_normalize(columns, rows, args):
    need(args.column, columns, '--column')
    out, methods, miss = [], Counter(), []
    for i, r in enumerate(rows, start=2):
        name, how = tr.il_bul(r[args.column])
        methods[how or 'boş'] += 1
        if how == 'bulunamadı':
            miss.append([i, r[args.column]])
        out.append({**r, 'il_standart': name, 'il_plaka': f'{tr.ILLER.index(name) + 1:02d}' if name else '', 'eslesme': how})
    found = sum(v for k, v in methods.items() if k not in ('bulunamadı', 'boş'))
    return Result(f'{found} il eşlendi, {len(miss)} bulunamadı', columns + ['il_standart', 'il_plaka', 'eslesme'], out,
                  {'eslesen': found, 'bulunamayan': len(miss)}, tables=[('Eşleşme türü', ['Tür', 'Satır'], methods.most_common())] +
                  ([('Bulunamayanlar', ['Satır', 'Değer'], miss)] if miss else []))


@op('posta-kodu-dogrula', title='Posta kodlarını il ile karşılaştır', domain=D, product=P, tag='posta kodu', samples=KIMLIK, requires=REQ,
    description='Posta kodunun 5 hane olduğunu ve ilk iki hanesinin ilin plaka koduyla aynı olduğunu denetler (ör. 35xxx İzmir). Adres verisinde il ile posta kodu çelişkilerini bulur.',
    input='CSV/XLSX + posta kodu ve il sütunu', output='sonuc.csv', command='python main.py --column posta_kodu --city il',
    args=[arg('--column', default='posta_kodu'), arg('--city', default='il')], expect={'gecerli': 7, 'gecersiz': 1})
def posta(columns, rows, args):
    need(args.city, columns, '--city')
    lookup = {id(r): tr.il_bul(r[args.city])[0] for r in rows}
    current = {}

    def check(v):
        d = tr.digits(v)
        if len(d) != 5 or d != v.strip():
            return '5 hane değil'
        if not 1 <= int(d[:2]) <= 81:
            return 'il kodu 01-81 dışında'
        il = current.get('il')
        if il and tr.ILLER.index(il) + 1 != int(d[:2]):
            return f'il {il} ({tr.ILLER.index(il) + 1:02d}) ile uyuşmuyor'
        return ''
    out, bad, blank = [], [], 0
    for i, r in enumerate(rows, start=2):
        current['il'] = lookup[id(r)]
        v = r[need(args.column, columns, '--column')].strip()
        err = check(v) if v else 'boş'
        blank += not v
        if v and err:
            bad.append([i, r[columns[0]], err])
        out.append({**r, 'posta_gecerli': '' if not v else 'hayır' if err else 'evet', 'posta_hata': err})
    valid = len(rows) - len(bad) - blank
    return Result(f'{valid} geçerli, {len(bad)} çelişkili/hatalı posta kodu', columns + ['posta_gecerli', 'posta_hata'], out,
                  {'gecerli': valid, 'gecersiz': len(bad)}, tables=[('Hatalı', ['Satır', columns[0], 'Neden'], bad)] if bad else [])


@op('kart-luhn', title='Kart numaralarını Luhn ile kontrol et ve maskele', domain=D, product=P, tag='kart no', samples=KIMLIK, requires=REQ,
    description='Kart numaralarını Luhn algoritması ve uzunlukla denetler, kart ağını (Visa, Mastercard, Amex, Troy…) çıkarır ve çıktıda numarayı yalnızca ilk 6 / son 4 hane görünecek şekilde maskeler. Tam numara hiçbir çıktıya yazılmaz.',
    input='CSV/XLSX + kart numarası sütunu', output='sonuc.csv (maskeli)', command='python main.py --column kart_no',
    args=[arg('--column', default='kart_no')], expect={'gecerli': 6, 'gecersiz': 1, 'bos': 1})
def kart(columns, rows, args):
    def network(d):
        return ('Visa' if d[0] == '4' else 'Mastercard' if d[:2] in ('51', '52', '53', '54', '55') or '2221' <= d[:4] <= '2720' else
                'American Express' if d[:2] in ('34', '37') else 'Troy' if d[:4] in ('9792', '6500') else 'Discover' if d[:4] == '6011' else 'diğer')
    res = validity(columns, rows, args, tr.luhn, 'kart', 'kart numarası',
                   lambda v: {'kart_agi': network(tr.digits(v)) if tr.digits(v) else ''}, ('kart_agi',))
    for r in res.rows:
        d = tr.digits(r[args.column])
        r[args.column] = d[:6] + '*' * (len(d) - 10) + d[-4:] if len(d) >= 12 else ('*' * len(d))
    res.notes.append('Çıktıda kart numaraları maskelidir (PCI DSS: ilk 6 / son 4 hane).')
    return res


# ---------------------------------------------------------------- tablo tutarlılığı
@op('referans-butunlugu', title='Yabancı anahtar (referans) bütünlüğünü kontrol et', domain=D, product=P, tag='FK', samples={**SATIS, 'urunler.csv': 'urunler.csv'},
    description='Alt tablodaki kodların ana tabloda bulunup bulunmadığını (yetim kayıt) ve ana tablodaki hiç kullanılmayan kayıtları bulur. Sipariş→ürün, fatura→cari gibi ilişkiler için veritabanı kısıtı olmayan dosyalarda kullanılır.',
    input='Alt tablo + ana tablo + anahtar sütunlar', output='yetimler.csv + rapor', command='python main.py --parent urunler.csv --key urun_kodu',
    args=[arg('--parent', default='@ornek/urunler.csv'), arg('--key', default='urun_kodu'), arg('--parent-key')],
    expect={'yetim_satir': 6, 'yetim_anahtar': 1, 'kullanilmayan_ana': 1}, exit_code=0)
def referans(columns, rows, args):
    need(args.key, columns, '--key')
    pc, pr = tabular.read(args.parent)
    pk = need(args.parent_key or args.key, pc, '--parent-key')
    parents = Counter(r[pk].strip() for r in pr)
    orphans = [{'satir': i, **r} for i, r in enumerate(rows, start=2) if r[args.key].strip() and r[args.key].strip() not in parents]
    used = {r[args.key].strip() for r in rows}
    unused = sorted(k for k in parents if k not in used)
    dup_parent = sorted(k for k, n in parents.items() if n > 1)
    keys = Counter(r[args.key].strip() for r in orphans)
    notes = [f'Ana tabloda tekrar eden anahtar: {", ".join(dup_parent)}'] if dup_parent else []
    return Result(f'{len(orphans)} yetim satır ({len(keys)} farklı kod), ana tabloda {len(unused)} kullanılmayan kayıt', ['satir'] + columns, orphans,
                  {'yetim_satir': len(orphans), 'yetim_anahtar': len(keys), 'kullanilmayan_ana': len(unused)}, notes=notes,
                  tables=[('Yetim kodlar', ['Kod', 'Satır'], keys.most_common()), ('Kullanılmayan ana kayıtlar', ['Kod'], [[k] for k in unused])])


@op('anahtar-bul', title='Benzersiz anahtar adaylarını bul', domain=D, product=P, tag='anahtar', samples=SATIS,
    description='Tek başına veya 2-3 sütunluk birleşimle her satırı benzersiz tanımlayan en küçük sütun kümelerini bulur; neredeyse benzersiz sütunlarda çakışan değerleri gösterir. Tablo grain’ini ve birleştirme anahtarını belirlemek için.',
    input='CSV/XLSX', output='rapor.md + anahtar-adaylari.csv', command='python main.py --max-columns 3',
    args=[arg('--max-columns', type=int, default=2), arg('--include', help='Yalnızca bu sütunlar arasında ara')], expect={'tekli_anahtar': 0, 'aday': 0, 'mukerrer_satir': 1})
def anahtar(columns, rows, args):
    cols = col_list(args.include, columns, '--include', required=False) or columns
    cols = [c for c in cols if all(r[c].strip() for r in rows)]
    found, near = [], []
    for size in range(1, args.max_columns + 1):
        for combo in itertools.combinations(cols, size):
            if any(set(f).issubset(combo) for f in found):
                continue
            seen = Counter(tuple(r[c] for c in combo) for r in rows)
            if len(seen) == len(rows):
                found.append(combo)
            elif size == 1 and len(seen) >= 0.9 * len(rows):
                near.append([combo[0], len(rows) - len(seen), ', '.join(k[0] for k, n in seen.items() if n > 1)[:80]])
    rows_out = [{'sutunlar': ' + '.join(f), 'sutun_sayisi': len(f)} for f in found]
    dup = sum(n - 1 for n in Counter(tuple(r[c] for c in columns) for r in rows).values() if n > 1)
    head = f'{len(found)} anahtar adayı bulundu' + (f'; dosyada {dup} tamamen aynı satır var, önce mükerrerleri giderin' if dup else '')
    return Result(head, None, None, {'tekli_anahtar': sum(len(f) == 1 for f in found), 'aday': len(found), 'mukerrer_satir': dup},
                  tables=[('Anahtar adayları', ['Sütunlar', 'Sütun sayısı'], [[r['sutunlar'], r['sutun_sayisi']] for r in rows_out]),
                          ('Neredeyse benzersiz sütunlar', ['Sütun', 'Fazla tekrar', 'Tekrar eden değer'], near)],
                  files={'anahtar-adaylari.csv': (['sutunlar', 'sutun_sayisi'], rows_out)},
                  notes=['Boş değer içeren sütunlar anahtar olarak değerlendirilmez.'])


@op('bagimlilik-kontrol', title='Sütunlar arası tutarlılık (A belirler B)', domain=D, product=P, tag='tutarlılık', samples=SATIS,
    description='“urun_kodu her zaman aynı urun_adi’na karşılık gelir” gibi kuralları denetler; aynı kod için farklı değer taşıyan kayıtları listeler. Ana veri çelişkilerini ve normalizasyon ihtiyacını gösterir.',
    input='CSV/XLSX + kurallar (A>B)', output='celiskiler.csv + rapor', command='python main.py --rules "urun_kodu>urun_adi,musteri_no>musteri_eposta"',
    args=[arg('--rules', default='urun_kodu>urun_adi,urun_kodu>birim_fiyat,musteri_no>musteri_eposta')], expect={'celiskili_anahtar': 10})
def bagimlilik(columns, rows, args):
    out, table = [], []
    for rule in [x.strip() for x in args.rules.split(',') if x.strip()]:
        a, _, b = rule.partition('>')
        a, b = need(a.strip(), columns, '--rules'), need(b.strip(), columns, '--rules')
        values = defaultdict(Counter)
        for r in rows:
            if r[a].strip():
                values[r[a].strip()][r[b].strip()] += 1
        bad = {k: v for k, v in values.items() if len(v) > 1}
        table.append([f'{a} → {b}', len(values), len(bad)])
        for k, v in bad.items():
            out.append({'kural': f'{a}>{b}', 'anahtar': k, 'degerler': ' | '.join(f'{x or "(boş)"} ({n})' for x, n in v.most_common())})
    return Result(f'{len(out)} anahtarda çelişki', ['kural', 'anahtar', 'degerler'], out, {'celiskili_anahtar': len(out)},
                  tables=[('Kurallar', ['Kural', 'Anahtar', 'Çelişkili'], table)])


def shape(v):
    s = re.sub(r'[A-Za-zÇĞİÖŞÜçğıöşü]', 'A', v.strip())
    s = re.sub(r'\d', '9', s)
    return re.sub(r'(A)A+', r'\1+', s)


@op('desen-profili', title='Değer desenlerini çıkar ve aykırı biçimleri bul', domain=D, product=P, tag='desen', samples=KIMLIK,
    description='Her değeri harf=A, rakam=9 desenine indirger (ör. “99 A+ 999”), sütun başına desen dağılımını çıkarır ve azınlıkta kalan biçimleri satır numarasıyla listeler. Kod, telefon ve tarih alanlarında biçim tutarsızlığını bulur.',
    input='CSV/XLSX', output='rapor.md + aykiri-desenler.csv', command='python main.py --columns telefon,plaka --threshold 20',
    args=[arg('--columns', help='Varsayılan tüm sütunlar'), arg('--threshold', type=float, default=20.0, help='Bu yüzdenin altındaki desenler aykırı sayılır')],
    expect={'aykiri_deger': 17})
def desen(columns, rows, args):
    cols = col_list(args.columns, columns, required=False) or columns
    out, table = [], []
    for c in cols:
        shapes = Counter(shape(r[c]) for r in rows if r[c].strip())
        total = sum(shapes.values())
        if not total:
            continue
        table.append([c, len(shapes), ', '.join(f'{s} ({n})' for s, n in shapes.most_common(3))])
        if len(shapes) == 1:
            continue
        for i, r in enumerate(rows, start=2):
            s = shape(r[c]) if r[c].strip() else None
            if s and 100 * shapes[s] / total < args.threshold:
                out.append({'satir': i, 'sutun': c, 'deger': r[c], 'desen': s, 'baskin_desen': shapes.most_common(1)[0][0]})
    return Result(f'{len(out)} aykırı biçimli değer', ['satir', 'sutun', 'deger', 'desen', 'baskin_desen'], out, {'aykiri_deger': len(out)},
                  tables=[('Sütun desenleri', ['Sütun', 'Desen sayısı', 'En sık desenler'], table)])


@op('kontrol-toplami', title='Aktarım kontrol toplamlarını hesapla ve karşılaştır', domain=D, product=P, tag='kontrol toplamı', samples=SATIS,
    description='Satır sayısı, sayısal sütun toplamları, farklı değer sayıları ve sıralamadan bağımsız içerik özeti (SHA-256) üretir. --compare ile önceki/karşı sistemin kontrol dosyasıyla karşılaştırır; fark varsa 2 koduyla çıkar.',
    input='CSV/XLSX + isteğe bağlı karşılaştırma dosyası', output='kontrol.json + rapor', command='python main.py --input hedef.csv --compare kaynak-kontrol.json',
    args=[arg('--compare', help='Daha önce üretilmiş kontrol.json')], expect={'satir_sayisi': 41})
def kontrol(columns, rows, args):
    sums = {}
    for c in columns:
        nums = numbers(rows, c)
        filled = [n for n, r in zip(nums, rows) if r[c].strip()]
        if filled and all(n is not None for n in filled):
            sums[c] = round(sum(filled), 4)
    digest = hashlib.sha256('\n'.join(sorted('\x1f'.join(r[c] for c in columns) for r in rows)).encode('utf-8')).hexdigest()
    totals = {'satir_sayisi': len(rows), 'sutunlar': columns, 'toplamlar': sums, 'farkli': {c: len({r[c] for r in rows}) for c in columns}, 'icerik_ozeti': digest}
    diffs = []
    if args.compare:
        other = json.loads(Path(args.compare).read_text(encoding='utf-8'))
        for key in ('satir_sayisi', 'icerik_ozeti'):
            if other.get(key) != totals[key]:
                diffs.append([key, other.get(key), totals[key]])
        for c, v in totals['toplamlar'].items():
            if c in other.get('toplamlar', {}) and abs(other['toplamlar'][c] - v) > 1e-6:
                diffs.append([f'toplam: {c}', other['toplamlar'][c], v])
    return Result(f'{len(rows)} satır, {len(sums)} sayısal sütun toplamı' + (f'; {len(diffs)} fark' if args.compare else ''), None, None,
                  {'satir_sayisi': len(rows), 'icerik_ozeti': digest[:16], 'fark': len(diffs)},
                  tables=[('Sayısal toplamlar', ['Sütun', 'Toplam'], [[c, fmt(v)] for c, v in sums.items()])] +
                         ([('Karşılaştırma farkları', ['Ölçü', 'Beklenen', 'Bulunan'], diffs)] if args.compare else []),
                  files={'kontrol.json': json.dumps(totals, ensure_ascii=False, indent=2)}, code=2 if diffs else 0)


@op('satir-kurallari', title='Satır düzeyi iş kurallarını denetle', domain=D, product=P, tag='iş kuralı', samples=SATIS, requires=['temizlik'],
    description='“tutar = adet * birim_fiyat”, “adet > 0”, “bitis >= baslangic” gibi sütunlar arası kuralları her satırda çalıştırır ve bozan satırları kural adıyla listeler. Kurallar okunur ifade olarak yazılır; güvenli ifade alt kümesi kullanılır.',
    input='CSV/XLSX + kurallar (; ile ayrılmış)', output='ihlaller.csv + rapor', command='python main.py --rules "adet > 0; tutar = adet * birim_fiyat"',
    args=[arg('--rules', default='adet > 0; abs(tutar - adet * birim_fiyat) < 0.01; tutar >= 0'), arg('--strict', action='store_true')],
    expect={'ihlal': 2})
def satir_kurallari(columns, rows, args):
    from temizlik import Expr
    rules = [(r.strip(), Expr(r.strip(), columns)) for r in args.rules.split(';') if r.strip()]
    out, table = [], []
    for text, expr in rules:
        bad = 0
        for i, r in enumerate(rows, start=2):
            if expr(r, rows) is not True:
                bad += 1
                out.append({'satir': i, 'kural': text, columns[0]: r[columns[0]]})
        table.append([text, bad])
    return Result(f'{len(out)} kural ihlali', ['satir', 'kural', columns[0]], out, {'ihlal': len(out)},
                  tables=[('Kurallar', ['Kural', 'İhlal'], table)], code=2 if out and args.strict else 0)
