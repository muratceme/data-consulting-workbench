"""Qlik Sense ve Power BI model üreticileri: zaman zekâsı (Qlik set analizi ana ölçüleri, Power BI hesaplama grubu), satır düzeyi güvenlik
(Qlik Section Access, Power BI TMDL rolü) ve Qlik artımlı QVD yükleme script'i. Her blok, ürettiği ifadenin örnek veride vermesi gereken
sonucu (ölçü değeri veya kullanıcı başına görünen satır) Python'la ayrıca hesaplar; uygulamada açınca bu değerlerle karşılaştırılır.
Hiçbir platforma bağlanmaz."""
import calendar, json, re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import tabular
from sqlgen import snake
from islem import op, arg, Result, need, numbers, fold, tr_lower

REQUIRES = ['sema_farki', 'sqlgen']
D, Q, PBI = 'bi', 'Qlik Sense Enterprise', 'Power BI Desktop'
SATIS = {'satis_detay.csv': 'satis_detay.csv'}
YETKI = {'kullanici_yetki.csv': 'kullanici_yetki.csv', 'satis_detay.csv': 'satis_detay.csv'}


def label(c):
    """Same field naming as the qlik-load-script and report template blocks."""
    return ' '.join(w.capitalize() for w in snake(c).split('_'))


def tr(v, dec=2):
    if v is None:
        return ''
    return f'{v:,.{dec}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


# ---------- time intelligence ----------
def add_months(d, n):
    """AddMonths/EDATE: same day n months later, clamped to the month's last day."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def periods(last, fm):
    """Date ranges of every time-intelligence measure as of `last`; fm = first month of the fiscal year."""
    ys = date(last.year - (last.month < fm), fm, 1)
    months_in = (last.month - fm) % 12
    qs = add_months(ys, months_in - months_in % 3)
    ms = last.replace(day=1)
    prev_end = ms - timedelta(days=1)
    return {'YTD': (ys, last), 'QTD': (qs, last), 'MTD': (ms, last), 'GY YTD': (add_months(ys, -12), add_months(last, -12)),
            'GY Aynı Ay': (add_months(ms, -12), add_months(last, -12)), 'Önceki Ay': (prev_end.replace(day=1), prev_end),
            'Son 12 Ay': (add_months(last, -12) + timedelta(days=1), last)}


ORDER = ['Cari', 'YTD', 'QTD', 'MTD', 'Önceki Ay', 'GY YTD', 'GY Aynı Ay', 'Son 12 Ay', 'YTD Değişim %']
EXPLAIN = {'Cari': 'seçimdeki değer', 'YTD': 'mali yıl başından son tarihe', 'QTD': 'çeyrek başından son tarihe', 'MTD': 'ay başından son tarihe',
           'Önceki Ay': 'bir önceki tam ay', 'GY YTD': 'geçen yılın aynı YTD dönemi', 'GY Aynı Ay': 'geçen yılın aynı ayı (aynı güne kadar)',
           'Son 12 Ay': 'son tarihten geriye 12 ay', 'YTD Değişim %': '(YTD − GY YTD) / GY YTD'}


def ti_data(columns, rows, args):
    dcol, vcol = need(args.date, columns, '--date'), need(args.value, columns, '--value')
    if not 1 <= args.fiscal_start <= 12:
        raise ValueError('--fiscal-start 1–12 arasında bir ay olmalı.')
    ds = [(tabular.parse_date(r[dcol]) or (None,))[0] for r in rows]
    vals = numbers(rows, vcol) if args.agg != 'countd' else [r[vcol].strip() or None for r in rows]
    pairs = [(d.date(), v) for d, v in zip(ds, vals) if d is not None and v is not None]
    if not pairs:
        raise ValueError(f'{dcol} ve {vcol} sütunlarında okunabilen satır yok.')
    last = date.fromisoformat(args.as_of) if args.as_of else max(d for d, _ in pairs)

    def agg(lo, hi):
        xs = [v for d, v in pairs if (lo is None or d >= lo) and (hi is None or d <= hi)]
        if args.agg == 'sum':
            return round(sum(xs), 4)
        if args.agg == 'avg':
            return round(sum(xs) / len(xs), 4) if xs else None
        return len(set(xs))
    per = periods(last, args.fiscal_start)
    expected = {'Cari': (None, None, agg(None, None))}
    for k, (lo, hi) in per.items():
        expected[k] = (lo, hi, agg(lo, hi))
    y, ly = expected['YTD'][2], expected['GY YTD'][2]
    expected['YTD Değişim %'] = (None, None, round((y - ly) / ly, 6) if ly else None)
    return dcol, vcol, last, expected


TI_ARGS = [arg('--date', default='siparis_tarihi', help='Tarih sütunu'), arg('--value', default='tutar', help='Ölçü sütunu (countd için anahtar)'),
           arg('--agg', default='sum', choices=['sum', 'avg', 'countd']), arg('--name', default='Ciro', help='Ölçü adı'),
           arg('--fiscal-start', type=int, default=1, help='Mali yılın ilk ayı (1 = Ocak)'), arg('--as-of', default='', help='Beklenen değerlerin tarihi (boşsa verideki son tarih)')]


def expected_rows(expected, name, exprs):
    return [{'olcu': f'{name} {k}' if k != 'Cari' else name, 'donem': EXPLAIN[k], 'baslangic': lo.isoformat() if lo else '', 'bitis': hi.isoformat() if hi else '',
             'beklenen': v if v is not None else '', 'ifade': exprs[k]} for k, (lo, hi, v) in expected.items()]


@op('qlik-zaman-zekasi', title='Qlik set analizi zaman zekâsı ölçüleri (YTD, QTD, MTD, geçen yıl, son 12 ay) ve beklenen değerleri', domain=D, product=Q, tag='set analizi',
    samples=SATIS, args=TI_ARGS + [arg('--clear', default='Yıl,Çeyrek,Ay,Yıl Ay', help='İfadede temizlenecek takvim alanları')],
    description='Qlik ana ölçü kütüphanesi üretir: cari, YTD, QTD, MTD, önceki ay, geçen yıl YTD, geçen yılın aynı ayı, son 12 ay ve YTD değişim yüzdesi. İfadeler tarih alanını '
                'set analiziyle aralığa çeker, takvim seçimlerini temizler ve mali yıl başlangıcını destekler. Aynı ölçülerin örnek veride (son tarih itibarıyla) vermesi gereken '
                'değerler Python’la hesaplanır; Qlik’te kurup karşılaştırırsınız. measures.json qlik-cli/corectl build biçimindedir.',
    input='CSV/XLSX (tarih + ölçü sütunu)', output='measures.json + olculer.qvs + sonuc.csv (ifade ve beklenen değer)', command='python main.py --input satis.csv --date tarih --value tutar --fiscal-start 4',
    expect={'olcu': 9, 'son_tarih': '2026-09-30', 'ytd': 2678560.0, 'gy_ytd': 2412000.0})
def qlik_ti(columns, rows, args):
    dcol, vcol, last, expected = ti_data(columns, rows, args)
    f, v, fm = f'[{label(dcol)}]', f'[{label(vcol)}]', args.fiscal_start
    clear = ', '.join(f'[{c.strip()}]=' for c in args.clear.split(',') if c.strip())
    mx = f'Max({f})'
    bounds = {'YTD': (f'YearStart({mx}, 0, {fm})', mx), 'QTD': (f'QuarterStart({mx}, 0, {fm})', mx), 'MTD': (f'MonthStart({mx})', mx),
              'GY YTD': (f'AddMonths(YearStart({mx}, 0, {fm}), -12)', f'AddMonths({mx}, -12)'), 'GY Aynı Ay': (f'AddMonths(MonthStart({mx}), -12)', f'AddMonths({mx}, -12)'),
              'Önceki Ay': (f'MonthStart({mx}, -1)', f'MonthEnd({mx}, -1)'), 'Son 12 Ay': (f'AddMonths({mx}, -12) + 1', mx)}
    fn = {'sum': 'Sum({set}{v})', 'avg': 'Avg({set}{v})', 'countd': 'Count({set}DISTINCT {v})'}[args.agg]
    exprs = {'Cari': fn.format(set='', v=v)}
    for k, (lo, hi) in bounds.items():
        s = f'{{<{clear}{", " if clear else ""}{f}={{">=$(=Date({lo}))<=$(=Date({hi}))"}}>}} '
        exprs[k] = fn.format(set=s, v=v)
    exprs['YTD Değişim %'] = f"({exprs['YTD']} - {exprs['GY YTD']}) / {exprs['GY YTD']}"
    out = expected_rows(expected, args.name, exprs)
    measures = []
    for k, o in zip(expected, out):
        pct = k == 'YTD Değişim %'
        measures.append({'qInfo': {'qId': f'zaman-{snake(o["olcu"])}', 'qType': 'measure'},
                         'qMeasure': {'qLabel': o['olcu'], 'qDef': o['ifade'], 'qGrouping': 'N', 'qExpressions': [], 'qActiveExpression': 0,
                                      'qNumFormat': ({'qType': 'F', 'qnDec': 1, 'qUseThou': 0, 'qFmt': '0,0%', 'qDec': ',', 'qThou': '.'} if pct else
                                                     {'qType': 'F', 'qnDec': 2, 'qUseThou': 1, 'qFmt': '#.##0,00', 'qDec': ',', 'qThou': '.'}), 'isCustomFormatted': True},
                         'qMetaDef': {'title': o['olcu'], 'description': EXPLAIN[k], 'tags': ['zaman-zekasi']}})
    # In the script $( would expand at reload time, so the text is written with @( and turned back with Replace().
    qvs = (f'// Ana öğe yerine değişken kullanmak isterseniz bu satırları script’in sonuna ekleyin; grafikte $(v{snake(out[1]["olcu"])}) gibi çağırın.\n'
           + '\n'.join(f"LET v{snake(o['olcu'])} = Replace('{o['ifade'].replace('$(', '@(')}', '@(', '$' & '(');" for o in out) + '\n')
    for problem in check_qlik_expressions([o['ifade'] for o in out]):
        raise ValueError(problem)
    return Result(f'{len(out)} zaman zekâsı ölçüsü; son tarih {last.isoformat()}, YTD {tr(expected["YTD"][2])}', list(out[0]), out,
                  {'olcu': len(out), 'son_tarih': last.isoformat(), 'ytd': expected['YTD'][2], 'gy_ytd': expected['GY YTD'][2]},
                  files={'measures.json': json.dumps(measures, ensure_ascii=False, indent=2), 'olculer.qvs': qvs},
                  notes=[f'İfadeler {f} alanının Qlik’te tarih (dual) olduğunu ve DateFormat ile gösterildiğini varsayar; qlik-rapor-sablonu bloğunun script’i bu şekilde yükler.',
                         'Beklenen değerler hiçbir seçim yokken geçerlidir (son tarih = verideki en büyük tarih). Değişkenli kullanımda SET içindeki $( genişletmesi grafik çizilirken çalışır.',
                         'measures.json qlik-uygulama klasöründeki measures.json’a eklenebilir veya qlik app build --measures ile tek başına yüklenebilir.'])


def check_qlik_expressions(exprs):
    """Balanced brackets/quotes per expression; returns problems."""
    out = []
    pairs = {')': '(', ']': '[', '}': '{', '>': '<'}
    for e in exprs:
        stack, quote = [], None
        for ch in e:
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in '"\'':
                quote = ch
            elif ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if not stack or stack.pop() != pairs[ch]:
                    out.append(f'dengesiz parantez: {e}')
                    break
        if stack or quote:
            out.append(f'kapanmamış parantez/tırnak: {e}')
    return out


def dax_name(n):
    return n if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) else "'" + n.replace("'", "''") + "'"


@op('pbi-zaman-hesaplama-grubu', title='Power BI zaman zekâsı hesaplama grubu (TMDL) ve beklenen değerleri', domain=D, product=PBI, tag='calculation group',
    samples=SATIS, args=TI_ARGS + [arg('--table', default='SatisDetay', help='Olgu tablosu'), arg('--date-table', default='Takvim'), arg('--date-column', default='Date'),
                                   arg('--group', default='Zaman Hesaplamaları', help='Hesaplama grubu tablosu adı')],
    description='Her ölçüye uygulanan bir hesaplama grubu yazar: cari, YTD, QTD, MTD, önceki ay, geçen yıl YTD, geçen yılın aynı ayı, son 12 ay ve YTD değişim yüzdesi. Dönem sonu, '
                'seçili dönemin son günüyle verideki son tarihin küçüğüdür; böylece geçen yıl karşılaştırması verinin bittiği güne kadar yapılır (DATESYTD tüm yılı alırdı). '
                'Mali yıl başlangıcı desteklenir. Dosya PBIP projesinin tables klasörüne kopyalanır; örnek verideki beklenen değerler ayrıca hesaplanır.',
    input='CSV/XLSX (tarih + ölçü sütunu)', output='<grup>.tmdl + sonuc.csv (öğe, DAX, beklenen değer)', command='python main.py --input satis.csv --table Satislar --date tarih --fiscal-start 4',
    expect={'oge': 9, 'son_tarih': '2026-09-30', 'ytd': 2678560.0, 'gy_ytd': 2412000.0})
def pbi_calc_group(columns, rows, args):
    dcol, vcol, last, expected = ti_data(columns, rows, args)
    fm = args.fiscal_start
    dt, dc = dax_name(args.date_table), f'{dax_name(args.date_table)}[{args.date_column}]'
    fact_date = f'{dax_name(args.table)}[{label(dcol)}]'
    head = [f'VAR SonGun = MIN ( MAX ( {dc} ), CALCULATE ( MAX ( {fact_date} ), REMOVEFILTERS () ) )',
            f'VAR YilBasi = DATE ( YEAR ( SonGun ) - IF ( MONTH ( SonGun ) < {fm}, 1, 0 ), {fm}, 1 )']
    rng = {'YTD': ('YilBasi', 'SonGun'), 'QTD': (f'EDATE ( YilBasi, MOD ( MONTH ( SonGun ) - {fm} + 12, 12 ) - MOD ( MOD ( MONTH ( SonGun ) - {fm} + 12, 12 ), 3 ) )', 'SonGun'),
           'MTD': ('DATE ( YEAR ( SonGun ), MONTH ( SonGun ), 1 )', 'SonGun'), 'GY YTD': ('EDATE ( YilBasi, -12 )', 'EDATE ( SonGun, -12 )'),
           'GY Aynı Ay': ('EDATE ( DATE ( YEAR ( SonGun ), MONTH ( SonGun ), 1 ), -12 )', 'EDATE ( SonGun, -12 )'),
           'Önceki Ay': ('EOMONTH ( SonGun, -2 ) + 1', 'EOMONTH ( SonGun, -1 )'), 'Son 12 Ay': ('EDATE ( SonGun, -12 ) + 1', 'SonGun')}
    calc = lambda lo, hi: f'CALCULATE ( SELECTEDMEASURE (), REMOVEFILTERS ( {dt} ), DATESBETWEEN ( {dc}, {lo}, {hi} ) )'
    items = {'Cari': ['SELECTEDMEASURE ()']}
    for k, (lo, hi) in rng.items():
        items[k] = head + [f'RETURN {calc(lo, hi)}']
    items['YTD Değişim %'] = head + [f'VAR Cari = {calc("YilBasi", "SonGun")}', f'VAR Onceki = {calc("EDATE ( YilBasi, -12 )", "EDATE ( SonGun, -12 )")}',
                                     'RETURN DIVIDE ( Cari - Onceki, Onceki )']
    lines = [f'table {dax_name(args.group)}', '\tcalculationGroup', '\t\tprecedence: 10', '']
    for i, (k, body) in enumerate(items.items()):
        if len(body) == 1:
            lines.append(f'\t\tcalculationItem {dax_name(k)} = {body[0]}')
        else:
            lines += [f'\t\tcalculationItem {dax_name(k)} ='] + [f'\t\t\t\t{b}' for b in body]
        lines.append(f'\t\t\tordinal: {i}')
        if k == 'YTD Değişim %':
            lines.append('\t\t\tformatStringDefinition = "0.0%"')
        lines.append('')
    lines += ['\tcolumn Hesap', '\t\tdataType: string', '\t\tsummarizeBy: none', '\t\tsourceColumn: Name', '\t\tsortByColumn: Sira', '',
              '\tcolumn Sira', '\t\tdataType: int64', '\t\tformatString: 0', '\t\tsummarizeBy: none', '\t\tsourceColumn: Ordinal', '']
    tmdl = '\n'.join(lines)
    exprs = {k: ' '.join(v) for k, v in items.items()}
    out = expected_rows(expected, args.name, exprs)
    for o, k in zip(out, expected):
        o['olcu'] = k
    return Result(f'{len(items)} hesaplama öğesi; son tarih {last.isoformat()}, YTD {tr(expected["YTD"][2])}', list(out[0]), out,
                  {'oge': len(items), 'son_tarih': last.isoformat(), 'ytd': expected['YTD'][2], 'gy_ytd': expected['GY YTD'][2]},
                  files={f'{args.group}.tmdl': tmdl},
                  notes=[f'Dosyayı <Proje>.SemanticModel/definition/tables/ klasörüne kopyalayın, model.tmdl’e “ref table {dax_name(args.group)}” ve model düzeyinde '
                         '“discourageImplicitMeasures” ekleyin (hesaplama grupları için Power BI bunu ister). Beklenen değerler “Hesap” sütununu bir tabloya koyup ana ölçüyü '
                         f'(örneğin SUM({vcol})) yanına eklediğinizde, hiçbir filtre yokken görülmesi gereken sayılardır.',
                         f'{args.date_table} tablosu tarih tablosu olarak işaretli ve {args.table} ile ilişkili olmalı (pbi-rapor-sablonu böyle kurar).'])


# ---------- row-level security ----------
def code(value):
    """Section Access value: Turkish letters folded to ASCII, upper case. Qlik upper-cases section access data with invariant rules,
    so 'i' would become 'I' and 'İ' would not match; ASCII codes avoid that. The link table maps each code back to the real value."""
    return fold(value).upper()


def ascii_upper(user):
    """User ids as Qlik compares them: invariant upper case (ali → ALI, never ALİ)."""
    return user.lower().upper() if user.isascii() else fold(user).upper()


def access_map(columns, rows, args):
    u, v = need(args.user, columns, '--user'), need(args.value, columns, '--value')
    access = defaultdict(set)
    for r in rows:
        if r[u].strip() and r[v].strip():
            access[r[u].strip().lower()].add(r[v].strip())
    if not access:
        raise ValueError('Yetki dosyasında kullanıcı–değer satırı yok.')
    return access


def visibility(access, args):
    """Rows and total each user sees in --data (case-insensitive value match, like Power BI)."""
    if not args.data:
        return None, set(), []
    cols, rows = tabular.read(args.data)
    dc = need(args.data_column, cols, '--data-column')
    vals = numbers(rows, args.data_value) if args.data_value in cols else [None] * len(rows)
    data_values = {r[dc].strip() for r in rows if r[dc].strip()}
    out = []
    for user, allowed in sorted(access.items()):
        keys = {tr_lower(x) for x in allowed}
        idx = [i for i, r in enumerate(rows) if '*' in allowed or tr_lower(r[dc].strip()) in keys]
        out.append({'kullanici': user, 'yetki': ', '.join(sorted(allowed)), 'gorunen_satir': len(idx),
                    'gorunen_toplam': round(sum(vals[i] or 0 for i in idx), 2) if args.data_value in cols else ''})
    mapped = {tr_lower(x) for s in access.values() for x in s if x != '*'}
    warns = [f'Veride olup hiçbir kullanıcıya (tam yetkililer dışında) atanmamış değer: {", ".join(sorted(x for x in data_values if tr_lower(x) not in mapped))}']\
        if any(tr_lower(x) not in mapped for x in data_values) else []
    unknown = sorted(x for s in access.values() for x in s if x != '*' and tr_lower(x) not in {tr_lower(d) for d in data_values})
    if unknown:
        warns.append(f'Yetki dosyasında olup veride bulunmayan değer (yazım hatası olabilir): {", ".join(unknown)}')
    return out, data_values, warns


RLS_ARGS = [arg('--user', default='kullanici'), arg('--value', default='bolge'), arg('--data', default='@ornek/satis_detay.csv', help='Görünürlüğü hesaplanacak veri (boş bırakılabilir)'),
            arg('--data-column', default='bolge', help='Veride kısıtlanan sütun'), arg('--data-value', default='tutar', help='Kullanıcı başına toplamı hesaplanacak sütun')]


@op('qlik-section-access', title='Qlik Section Access script’i (satır düzeyi güvenlik) ve kullanıcı başına beklenen görünüm', domain=D, product=Q, tag='Section Access',
    samples=YETKI, args=RLS_ARGS + [arg('--field', default='', help='Uygulamadaki alan adı (boşsa --data-column etiketi, örn. Bolge)'),
                                    arg('--platform', default='enterprise', choices=['enterprise', 'cloud']), arg('--domain', default='', help='Enterprise: e-postayı DOMAIN\\kullanıcı yapar'),
                                    arg('--admin', default='', help='Script’i yeniden yükleyecek yönetici (Enterprise varsayılanı INTERNAL\\SA_SCHEDULER)')],
    description='Kullanıcı–değer eşleme dosyasından Section Access script’i yazar: büyük harfli erişim tablosu, uygulamadaki gerçek değerlere bağlanan büyük harfli bağlantı tablosu, '
                'yeniden yükleme için yönetici satırı. “*” yetkisi, Qlik’te yalnızca tabloda listelenen değerleri kapsadığı için tüm değerlere açık satırlar olarak yazılır. '
                'Veri dosyası verilirse her kullanıcının göreceği satır sayısı ve toplamı hesaplanır; kimseye atanmamış ve veride olmayan değerler uyarılır.',
    input='Kullanıcı–değer eşleme CSV’si (+ veri dosyası)', output='section_access.qvs + sonuc.csv (kullanıcı başına görünüm)',
    command='python main.py --input yetkiler.csv --data satis.csv --data-column bolge --domain ORNEK', sample_input='kullanici_yetki.csv',
    expect={'kullanici': 5, 'erisim_satiri': 11, 'tam_yetkili': 1, 'gorunen_toplam_ali.celik@ornek.test': 5912910.0})
def qlik_section_access(columns, rows, args):
    access = access_map(columns, rows, args)
    view, data_values, warns = visibility(access, args)
    field = args.field or label(args.data_column)
    red = 'ERISIM_' + re.sub(r'[^A-Z0-9]', '_', code(snake(field)))
    values = sorted({x for s in access.values() for x in s if x != '*'} | data_values, key=tr_lower)
    by_key = {}
    for x in values:  # one spelling per case-insensitive value
        by_key.setdefault(code(x), x)
    if args.platform == 'enterprise':
        uid = lambda e: ascii_upper(f'{args.domain}\\{e.split("@")[0]}' if args.domain else e)
        admin = args.admin or 'INTERNAL\\SA_SCHEDULER'
        id_col = 'USERID'
    else:
        uid, admin, id_col = ascii_upper, ascii_upper(args.admin), 'USER.EMAIL'
    lines = []
    full = sorted(u for u, s in access.items() if '*' in s)
    if admin:
        lines.append(f'ADMIN;{admin};*')
    for u, allowed in sorted(access.items()):
        targets = sorted(by_key) if '*' in allowed else sorted({code(x) for x in allowed})
        lines += [f'USER;{uid(u)};{t}' for t in targets]
    link = sorted({f'{code(x)};{x}' for x in values})  # every spelling in the data: Qlik values are case-sensitive
    script = (f'// Section Access: {len(access)} kullanıcı. Erişim tablosundaki alan adları ve değerler BÜYÜK HARF olmalıdır.\n'
              f'// “*” yalnızca bu tabloda listelenen değerleri kapsar; bu yüzden tam yetkililer için her değer ayrı satırdır.\n'
              'Section Access;\n[Erisim]:\nLOAD * INLINE [\n' + f'ACCESS;{id_col};{red}\n' + '\n'.join(lines) + '\n] (delimiter is \';\');\n\n'
              'Section Application;\n'
              f'// {red} alanı uygulamadaki [{field}] değerlerine bağlanır; [{field}] alanı modelde bu adla yüklenmiş olmalı.\n'
              f'[Erisim Esleme]:\nLOAD * INLINE [\n{red};{field}\n' + '\n'.join(link) + '\n] (delimiter is \';\');\n')
    if any(';' in x for x in values) or any(',' in x for x in ()):
        raise ValueError('Değerlerde “;” var; INLINE ayırıcısıyla çakışır.')
    notes = ['Yayımlamadan önce uygulamanın bir kopyasında deneyin: yanlış Section Access sizi de uygulamanın dışında bırakabilir. Yükleme yapan hesabın ADMIN satırında olduğundan emin olun.']
    if args.platform == 'cloud':
        notes.append('Qlik Cloud’da kimlik USER.EMAIL ile eşleştirilir; uygulamanın sahibini --admin ile ADMIN olarak ekleyin. Kiracınızın e-posta eşleşmesini bir test kullanıcısıyla doğrulayın.')
    elif not args.domain:
        notes.append('Enterprise’ta USERID “DOMAIN\\kullanıcı” biçimindedir; --domain ile e-postaların yerel kısmından üretin.')
    summary = {'kullanici': len(access), 'erisim_satiri': len(lines), 'tam_yetkili': len(full), 'deger': len(by_key)}
    for v in view or []:
        summary[f'gorunen_toplam_{v["kullanici"]}'] = v['gorunen_toplam']
    return Result(f'{len(access)} kullanıcı, {len(lines)} erişim satırı, {len(by_key)} değer; {len(full)} tam yetkili', list(view[0]) if view else None, view,
                  summary, files={'section_access.qvs': script}, notes=notes + warns)


@op('pbi-rls-tmdl', title='Power BI satır düzeyi güvenlik: TMDL rolü, güvenlik tablosu ve kullanıcı başına beklenen görünüm', domain=D, product=PBI, tag='RLS TMDL',
    samples=YETKI, args=RLS_ARGS + [arg('--table', default='SatisDetay', help='Kısıtlanan tablo'), arg('--column', default='', help='Kısıtlanan sütun (boşsa --data-column etiketi)'),
                                    arg('--role', default='Bölge Erişimi')],
    description='PBIP projesine kopyalanacak TMDL dosyaları yazar: kullanıcı–değer eşlemesini taşıyan gizli güvenlik tablosu (Power Query #table) ve USERPRINCIPALNAME() ile dinamik '
                'filtreleyen rol; “*” yetkili kullanıcılar tüm satırları görür. Veri dosyası verilirse her kullanıcının “Farklı görüntüle” ile göreceği satır sayısı ve toplam hesaplanır.',
    input='Kullanıcı–değer eşleme CSV’si (+ veri dosyası)', output='tables/Guvenlik.tmdl + roles/<rol>.tmdl + sonuc.csv (kullanıcı başına görünüm)',
    command='python main.py --input yetkiler.csv --table Satislar --column Bolge --data satis.csv', sample_input='kullanici_yetki.csv',
    expect={'kullanici': 5, 'eslesme': 6, 'tam_yetkili': 1, 'gorunen_satir_ayse.yilmaz@ornek.test': 52})
def pbi_rls_tmdl(columns, rows, args):
    access = access_map(columns, rows, args)
    view, _, warns = visibility(access, args)
    col = args.column or label(args.data_column)
    pairs = sorted((u, x) for u, s in access.items() for x in s)
    m_rows = ', '.join('{' + json.dumps(u, ensure_ascii=False) + ', ' + json.dumps(x, ensure_ascii=False) + '}' for u, x in pairs)
    sec = ['table Guvenlik', '\tisHidden', '',
           '\tcolumn kullanici', '\t\tdataType: string', '\t\tisHidden', '\t\tsummarizeBy: none', '\t\tsourceColumn: kullanici', '',
           '\tcolumn deger', '\t\tdataType: string', '\t\tisHidden', '\t\tsummarizeBy: none', '\t\tsourceColumn: deger', '',
           '\tpartition Guvenlik = m', '\t\tmode: import', '\t\tsource =', '\t\t\t\tlet',
           f'\t\t\t\t    Kaynak = #table(type table [kullanici = text, deger = text], {{{m_rows}}})', '\t\t\t\tin', '\t\t\t\t    Kaynak', '']
    rule = [f"VAR Kullanici = LOWER ( USERPRINCIPALNAME () )",
            "VAR Yetkiler = CALCULATETABLE ( VALUES ( Guvenlik[deger] ), Guvenlik[kullanici] = Kullanici )",
            f"RETURN \"*\" IN Yetkiler || {dax_name(args.table)}[{col}] IN Yetkiler"]
    role = [f'role {dax_name(args.role)}', '\tmodelPermission: read', '', f'\ttablePermission {dax_name(args.table)} ='] + [f'\t\t\t\t{r}' for r in rule] + ['']
    full = sorted(u for u, s in access.items() if '*' in s)
    summary = {'kullanici': len(access), 'eslesme': len(pairs), 'tam_yetkili': len(full)}
    for v in view or []:
        summary[f'gorunen_satir_{v["kullanici"]}'] = v['gorunen_satir']
    return Result(f'{len(access)} kullanıcı, {len(pairs)} yetki satırı; rol “{args.role}”', list(view[0]) if view else None, view, summary,
                  files={'tables/Guvenlik.tmdl': '\n'.join(sec), f'roles/{args.role}.tmdl': '\n'.join(role)},
                  notes=['tables ve roles klasörlerini <Proje>.SemanticModel/definition/ altına kopyalayın ve model.tmdl’e “ref table Guvenlik” ekleyin. Rol üyeleri Power BI '
                         'Service’te atanır; Desktop’ta Modelleme > Farklı görüntüle ile her kullanıcıyı sonuc.csv’deki satır sayısıyla karşılaştırın.',
                         'DAX karşılaştırması büyük/küçük harfe duyarsızdır; güvenlik tablosu e-posta içerir, PBIP klasörünün erişimini sınırlayın.'] + warns)


# ---------- incremental load ----------
@op('qlik-artimli-yukleme', title='Qlik artımlı yükleme script’i: QVD’ye ekle, güncelle, silineni çıkar', domain=D, product=Q, tag='artımlı QVD', kind='none', samples={},
    args=[arg('--table', default='Siparis', help='Qlik tablo adı'), arg('--source', default='dbo.Siparis', help='Kaynak tablo (SQL)'),
          arg('--key', default='siparis_id', help='Birincil anahtar (virgülle birden fazla)'), arg('--modified', default='guncelleme_zamani', help='Değişiklik zamanı sütunu'),
          arg('--columns', default='*', help='SELECT edilecek sütunlar'), arg('--qvd', default='lib://Veri/QVD/Siparis.qvd'),
          arg('--connection', default='SatisDB', help='LIB CONNECT bağlantısı'), arg('--dialect', default='sqlserver', choices=['sqlserver', 'postgres', 'oracle', 'mysql']),
          arg('--deletes', default='evet', choices=['evet', 'hayır'], help='Kaynakta silinen kayıtları QVD’den çıkar')],
    description='Değişiklik zamanı sütunu olan bir kaynak tablo için klasik artımlı yükleme script’ini yazar: QVD’deki son değişiklik zamanını okur, yalnızca o andan sonra '
                'değişen satırları çeker, eski satırları anahtara göre NOT Exists ile ekler (güncellenen satır tekrarlanmaz), istenirse kaynaktaki anahtar listesiyle Inner Join '
                'yapıp silinenleri çıkarır ve QVD’yi yeniden yazar. İlk çalıştırmada (QVD yokken) tam yükleme yapar. Bileşik anahtar ve dört SQL lehçesi desteklenir.',
    input='Tablo, anahtar ve değişiklik zamanı sütunu adları', output='artimli_yukleme.qvs', command='python main.py --table Fatura --source erp.fatura --key fatura_no,satir_no --modified degisti --dialect oracle',
    expect={'adim': 5, 'anahtar': 1})
def qlik_incremental(args):
    keys = [k.strip() for k in args.key.split(',') if k.strip()]
    if not keys:
        raise ValueError('--key ile en az bir anahtar sütunu verin.')
    t, q = args.table, args.qvd
    key_expr = f'[{keys[0]}]' if len(keys) == 1 else " & '|' & ".join(f'[{k}]' for k in keys)
    key_field = keys[0] if len(keys) == 1 else '%Anahtar'
    add_key = '' if len(keys) == 1 else f",\n    {key_expr} as [%Anahtar]"
    lit = {'sqlserver': "CONVERT(datetime2, '$(vSonDegisiklik)', 120)", 'postgres': "TIMESTAMP '$(vSonDegisiklik)'",
           'oracle': "TO_TIMESTAMP('$(vSonDegisiklik)', 'YYYY-MM-DD HH24:MI:SS')", 'mysql': "TIMESTAMP('$(vSonDegisiklik)')"}[args.dialect]
    cols = args.columns
    keycols = ', '.join(keys)
    script = f"""// Artımlı yükleme: {args.source} → {q}
// Anahtar: {', '.join(keys)} · Değişiklik zamanı: {args.modified} · Lehçe: {args.dialect}
LIB CONNECT TO '{args.connection}';
SET vQvd = '{q}';

// 1) QVD’deki son değişiklik zamanı (QVD yoksa tam yükleme)
IF Not IsNull(FileSize('$(vQvd)')) THEN
    [_SonZaman]:
    LOAD Max([{args.modified}]) as [_Son] FROM [$(vQvd)] (qvd);
    LET vSonDegisiklik = Timestamp(Peek('_Son', 0, '_SonZaman'), 'YYYY-MM-DD hh:mm:ss');
    DROP TABLE [_SonZaman];
    LET vTamYukleme = 0;
ELSE
    LET vSonDegisiklik = '1900-01-01 00:00:00';
    LET vTamYukleme = 1;
ENDIF
TRACE Son değişiklik: $(vSonDegisiklik);

// 2) Yeni ve değişen satırlar (>= ile aynı saniyede yazılanlar kaçmaz; tekrarları 3. adım ayıklar)
[{t}]:
LOAD *{add_key};
SQL SELECT {cols}
FROM {args.source}
WHERE {args.modified} >= {lit};

// 3) QVD’deki eski satırlar: yalnızca yeni yüklenmemiş anahtarlar
IF $(vTamYukleme) = 0 THEN
    Concatenate ([{t}])
    LOAD * FROM [$(vQvd)] (qvd)
    WHERE Not Exists([{key_field}]{'' if len(keys) == 1 else f', {key_expr}'});
ENDIF
"""
    if args.deletes == 'evet':
        script += f"""
// 4) Kaynakta silinen satırları çıkar: kaynaktaki güncel anahtar listesiyle Inner Join
Inner Join ([{t}])
LOAD {'[' + keys[0] + ']' if len(keys) == 1 else key_expr + ' as [%Anahtar]'};
SQL SELECT {keycols} FROM {args.source};
"""
    script += f"""
// 5) QVD’yi yeniden yaz
STORE [{t}] INTO [$(vQvd)] (qvd);
TRACE $(=NoOfRows('{t}')) satır QVD’ye yazıldı;
"""
    problems = check_qlik_expressions(['\n'.join(ln for ln in script.split('\n') if not ln.lstrip().startswith('//'))])
    if problems:
        raise ValueError('Üretilen script dengesiz: ' + problems[0][:200])
    steps = 5 if args.deletes == 'evet' else 4
    return Result(f'{t}: {steps} adımlı artımlı yükleme ({args.dialect}, {len(keys)} anahtar)', None, None, {'adim': steps, 'anahtar': len(keys)},
                  files={'artimli_yukleme.qvs': script},
                  notes=['Değişiklik zamanı sütunu kaynakta her güncellemede güncellenmeli ve indeksli olmalı. Saat dilimi farkı varsa vSonDegisiklik’ten birkaç dakika geri gidin.',
                         'Silme adımı her çalıştırmada tüm anahtarları çeker; çok büyük tablolarda yalnızca son N günün anahtarlarıyla sınırlayın veya silme işaretli (soft delete) sütun kullanın.'])
