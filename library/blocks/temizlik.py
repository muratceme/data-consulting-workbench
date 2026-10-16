"""Veri temizleme ve dönüştürme blokları (CSV/XLSX). Her blok tek bir işi yapar ve sonucu sonuc.csv olarak yazar."""
import ast, math, random, re, unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import tabular
from islem import op, arg, Result, col_list, need, numbers, fmt, tr_upper, tr_lower, tr_title, fold, tr_sort_key

D, P = 'files', 'CSV / Excel'
SATIS = {'satislar.csv': 'satislar.csv'}
KIRLI = {'kirli_liste.csv': 'kirli_liste.csv'}


def copy(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- metin
INVISIBLE = re.compile('[​‌‍⁠﻿]')


@op('bosluk-temizle', title='Boşluk ve görünmez karakterleri temizle', domain=D, product=P, tag='temizlik', samples=KIRLI,
    description='Hücre başı/sonu boşlukları, çift boşlukları, bölünmez boşluğu ve sıfır genişlikli görünmez karakterleri temizler. Sütun adlarını da düzeltir; hangi sütunda kaç hücrenin değiştiğini raporlar.',
    input='CSV/XLSX', output='sonuc.csv + rapor.md', command='python main.py --input liste.xlsx',
    expect={'degisen_hucre': 2})
def bosluk(columns, rows, args):
    changed = Counter()
    out = []
    for r in rows:
        new = {}
        for c in columns:
            v = INVISIBLE.sub('', r[c]).replace(' ', ' ')
            v = re.sub(r'[ \t]+', ' ', v).strip()
            if v != r[c]:
                changed[c] += 1
            new[c] = v
        out.append(new)
    return Result(f'{sum(changed.values())} hücre temizlendi', columns, out, {'degisen_hucre': sum(changed.values())},
                  tables=[('Sütunlara göre', ['Sütun', 'Değişen hücre'], changed.most_common())])


def snake(name):
    s = tr_lower(name).translate(str.maketrans('çğıöşüâîû', 'cgiosuaiu'))
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return ('k_' + s if s[:1].isdigit() else s) or 'sutun'


@op('sutun-adlari', title='Sütun adlarını standartlaştır (snake_case)', domain=D, product=P, tag='sütun adı', samples=KIRLI,
    description='Sütun adlarını Türkçe karakterleri dönüştürerek küçük harf, alt çizgili ve veritabanına uygun hâle getirir; çakışan adları numaralandırır. Eski → yeni ad eşlemesini ayrıca yazar.',
    input='CSV/XLSX', output='sonuc.csv + sutun-eslemesi.csv', command='python main.py --input rapor.xlsx',
    expect={'yeniden_adlandirilan': 7})
def sutun_adlari(columns, rows, args):
    new, seen = [], Counter()
    for c in columns:
        base = snake(c)
        seen[base] += 1
        new.append(base if seen[base] == 1 else f'{base}_{seen[base]}')
    mapping = dict(zip(columns, new))
    out = [{mapping[c]: r[c] for c in columns} for r in rows]
    pairs = [[a, b] for a, b in mapping.items()]
    return Result(f'{sum(a != b for a, b in pairs)} sütun adı değişti', new, out, {'yeniden_adlandirilan': sum(a != b for a, b in pairs)},
                  tables=[('Eşleme', ['Eski', 'Yeni'], pairs)], files={'sutun-eslemesi.csv': (['eski', 'yeni'], [{'eski': a, 'yeni': b} for a, b in pairs])})


@op('harf-donustur', title='Türkçe uyumlu büyük/küçük harf dönüşümü', domain=D, product=P, tag='harf', samples=KIRLI,
    description='Seçilen sütunları Türkçe kurallarla (i→İ, ı→I) BÜYÜK, küçük veya Baş Harf Büyük yapar. Excel’in UPPER/PROPER fonksiyonlarının “istanbul → ISTANBUL” hatasını yapmaz.',
    input='CSV/XLSX + sütunlar + mod', output='sonuc.csv', command='python main.py --columns "Müşteri Adı,Şehir" --mode baslik',
    args=[arg('--columns', default='Müşteri Adı,Şehir'), arg('--mode', choices=['buyuk', 'kucuk', 'baslik'], default='baslik')],
    expect={'degisen_hucre': 8})
def harf(columns, rows, args):
    cols = col_list(args.columns, columns)
    f = {'buyuk': tr_upper, 'kucuk': tr_lower, 'baslik': lambda s: tr_title(re.sub(r'\s+', ' ', s.strip()))}[args.mode]
    out, n = copy(rows), 0
    for r in out:
        for c in cols:
            v = f(r[c])
            n += v != r[c]
            r[c] = v
    return Result(f'{n} hücre {args.mode} biçimine çevrildi', columns, out, {'degisen_hucre': n})


MOJIBAKE = {'Ã§': 'ç', 'Ã‡': 'Ç', 'ÄŸ': 'ğ', 'Äž': 'Ğ', 'Ä±': 'ı', 'Ä°': 'İ', 'Ã¶': 'ö', 'Ã–': 'Ö', 'ÅŸ': 'ş', 'Åž': 'Ş', 'Ã¼': 'ü', 'Ãœ': 'Ü',
            'Ã¢': 'â', 'Ã®': 'î', 'Ã»': 'û', 'â€™': '’', 'â€œ': '“', 'â€\x9d': '”', 'â€“': '–', 'Ý': 'İ', 'ý': 'ı', 'Þ': 'Ş', 'þ': 'ş', 'Ð': 'Ğ', 'ð': 'ğ'}


def fix_text(v):
    try:  # UTF-8 bytes read as cp1252/latin-1: undo the whole string when it round-trips cleanly.
        fixed = v.encode('cp1252').decode('utf-8')
        if fixed != v:
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    for bad, good in MOJIBAKE.items():
        v = v.replace(bad, good)
    return v


@op('karakter-onar', title='Bozuk Türkçe karakterleri onar (Ã¼, ÅŸ, Ý…)', domain=D, product=P, tag='karakter', samples=KIRLI,
    description='Yanlış kodlamayla açılmış dosyalardaki “Ã¼ → ü”, “ÅŸ → ş”, “Ý → İ” gibi bozulmaları onarır. İsteğe bağlı olarak Türkçe karakterleri ASCII’ye indirger (eski sistemlere aktarım için).',
    input='CSV/XLSX', output='sonuc.csv', command='python main.py --input eski_sistem.csv [--ascii]',
    args=[arg('--ascii', action='store_true', help='Onardıktan sonra Türkçe karakterleri ASCII’ye çevir')], expect={'onarilan_hucre': 4})
def karakter(columns, rows, args):
    out, n = [], 0
    for r in rows:
        new = {}
        for c in columns:
            v = fix_text(r[c])
            n += v != r[c]
            if args.ascii:
                v = unicodedata.normalize('NFKD', v.translate(str.maketrans('ıİ', 'iI'))).encode('ascii', 'ignore').decode()
            new[c] = v
        out.append(new)
    return Result(f'{n} hücrede bozuk karakter onarıldı', columns, out, {'onarilan_hucre': n})


# ---------------------------------------------------------------- tarih ve sayı
@op('tarih-bicimi', title='Karışık tarih biçimlerini tek biçime çevir', domain=D, product=P, tag='tarih', samples=KIRLI,
    description='Aynı sütundaki 05.02.2026, 2026/03/07, 2026-01-05 gibi biçimleri ISO (YYYY-AA-GG) veya seçtiğin biçime çevirir. Okunamayan değerleri satır numarasıyla listeler; değeri değiştirmez.',
    input='CSV/XLSX + tarih sütunları', output='sonuc.csv + okunamayan değer listesi', command='python main.py --columns "Kayıt Tarihi" --format %d.%m.%Y',
    args=[arg('--columns', default='Kayıt Tarihi'), arg('--format', default='%Y-%m-%d', help='Çıktı biçimi (strftime), örn. %%d.%%m.%%Y')],
    expect={'cevrilen': 4, 'okunamayan': 0})
def tarih_bicimi(columns, rows, args):
    cols = col_list(args.columns, columns)
    out, done, bad = copy(rows), 0, []
    for i, r in enumerate(out, start=2):
        for c in cols:
            v = r[c].strip()
            if not v:
                continue
            d = tabular.parse_date(v) or tabular.parse_date(v.replace('/', '-'))
            if d:
                new = d[0].strftime(args.format)
                done += new != v
                r[c] = new
            else:
                bad.append([i, c, v])
    return Result(f'{done} tarih çevrildi, {len(bad)} okunamadı', columns, out, {'cevrilen': done, 'okunamayan': len(bad)},
                  tables=[('Okunamayan değerler', ['Satır', 'Sütun', 'Değer'], bad)] if bad else [])


@op('sayi-bicimi', title='Türkçe/İngilizce sayı biçimlerini düzelt', domain=D, product=P, tag='sayı', samples=KIRLI,
    description='“1.250,50”, “12.000”, “₺ 2.500” gibi değerleri sütunun kendi ondalık ayırıcısını tespit ederek düz sayıya (1250.5) veya Türkçe biçime çevirir. Sayı olmayan değerleri raporlar.',
    input='CSV/XLSX + sayısal sütunlar', output='sonuc.csv', command='python main.py --columns Tutar --style tr',
    args=[arg('--columns', default='Tutar'), arg('--style', choices=['duz', 'tr'], default='duz', help='duz: 1234.5 · tr: 1.234,50'),
          arg('--decimals', type=int, default=2)], expect={'cevrilen': 6, 'sayi_olmayan': 0})
def sayi_bicimi(columns, rows, args):
    cols = col_list(args.columns, columns)
    out, done, bad = copy(rows), 0, []
    for c in cols:
        parsed = numbers(rows, c)
        for i, (r, n) in enumerate(zip(out, parsed), start=2):
            if not r[c].strip():
                continue
            if n is None:
                bad.append([i, c, r[c]])
                continue
            r[c] = (f'{n:,.{args.decimals}f}'.replace(',', 'X').replace('.', ',').replace('X', '.') if args.style == 'tr'
                    else f'{n:.{args.decimals}f}')
            done += 1
    return Result(f'{done} sayı çevrildi, {len(bad)} değer sayı değil', columns, out, {'cevrilen': done, 'sayi_olmayan': len(bad)},
                  tables=[('Sayı olmayan değerler', ['Satır', 'Sütun', 'Değer'], bad)] if bad else [])


@op('bos-doldur', title='Boş değerleri doldur', domain=D, product=P, tag='eksik veri', samples=SATIS,
    description='Seçilen sütunlardaki boşları sabit değer, bir önceki dolu değer, ortalama, medyan veya en sık değerle doldurur. Hangi hücrenin neyle doldurulduğunu ayrıca listeler; tahmin yapmaz.',
    input='CSV/XLSX + sütunlar + yöntem', output='sonuc.csv + doldurulanlar.csv', command='python main.py --columns bolge --method sabit --value Bilinmiyor',
    args=[arg('--columns', default='bolge,musteri_eposta'), arg('--method', choices=['sabit', 'onceki', 'ortalama', 'medyan', 'enSik'], default='sabit'),
          arg('--value', default='Bilinmiyor')], expect={'doldurulan': 2})
def bos_doldur(columns, rows, args):
    cols = col_list(args.columns, columns)
    out, log = copy(rows), []
    for c in cols:
        filled = [r[c] for r in rows if r[c].strip()]
        if args.method in ('ortalama', 'medyan'):
            nums = sorted(n for n in numbers(rows, c) if n is not None)
            if not nums:
                raise ValueError(f'"{c}" sütununda sayı yok; {args.method} hesaplanamaz.')
            mid = len(nums) // 2
            value = fmt(sum(nums) / len(nums)) if args.method == 'ortalama' else fmt(nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2)
        elif args.method == 'enSik':
            value = Counter(filled).most_common(1)[0][0] if filled else ''
        else:
            value = args.value
        last = ''
        for i, r in enumerate(out, start=2):
            if r[c].strip():
                last = r[c]
                continue
            new = last if args.method == 'onceki' else value
            if new:
                r[c] = new
                log.append({'satir': i, 'sutun': c, 'yeni_deger': new})
    return Result(f'{len(log)} boş hücre dolduruldu', columns, out, {'doldurulan': len(log)},
                  files={'doldurulanlar.csv': (['satir', 'sutun', 'yeni_deger'], log)})


# ---------------------------------------------------------------- satır işlemleri
@op('tekrar-sil', title='Mükerrer satırları temizle', domain=D, product=P, tag='mükerrer', samples=SATIS,
    description='Tamamen aynı satırları veya seçilen anahtar sütunlarda tekrar eden satırları kaldırır (ilkini ya da sonuncuyu tutar). Büyük/küçük harf ve boşluk farkını isteğe bağlı yok sayar; silinenleri ayrı dosyaya yazar.',
    input='CSV/XLSX + isteğe bağlı anahtar sütunlar', output='sonuc.csv + silinenler.csv', command='python main.py --keys siparis_no --keep last',
    args=[arg('--keys', help='Anahtar sütunlar (virgülle); verilmezse tüm satır karşılaştırılır'), arg('--keep', choices=['first', 'last'], default='first'),
          arg('--loose', action='store_true', help='Büyük/küçük harf ve boşluk farkını yok say')], expect={'silinen': 1, 'kalan': 40})
def tekrar_sil(columns, rows, args):
    keys = col_list(args.keys, columns, '--keys', required=False) or columns
    norm = (lambda v: fold(v)) if args.loose else (lambda v: v)
    order = range(len(rows)) if args.keep == 'first' else range(len(rows) - 1, -1, -1)
    seen, keep = set(), set()
    for i in order:
        k = tuple(norm(rows[i][c]) for c in keys)
        if k not in seen:
            seen.add(k)
            keep.add(i)
    kept = [r for i, r in enumerate(rows) if i in keep]
    dropped = [{'satir': i + 2, **r} for i, r in enumerate(rows) if i not in keep]
    return Result(f'{len(dropped)} mükerrer satır silindi, {len(kept)} satır kaldı', columns, kept, {'silinen': len(dropped), 'kalan': len(kept), 'anahtar': keys if args.keys else 'tüm satır'},
                  files={'silinenler.csv': (['satir'] + columns, dropped)})


@op('sutun-sec', title='Sütunları seç, sırala ve yeniden adlandır', domain=D, product=P, tag='sütun', samples=SATIS,
    description='Yalnızca istenen sütunları istenen sırayla tutar ve “eski:yeni” biçiminde yeniden adlandırır. Kişisel veri içeren sütunları paylaşım öncesi dışarıda bırakmanın en kısa yolu.',
    input='CSV/XLSX + sütun listesi', output='sonuc.csv', command='python main.py --columns "siparis_no:Sipariş,tutar:Tutar"',
    args=[arg('--columns', default='siparis_no:Sipariş No,siparis_tarihi:Tarih,tutar:Tutar')], expect={'sutun': 3})
def sutun_sec(columns, rows, args):
    pairs = []
    for part in [p.strip() for p in args.columns.split(',') if p.strip()]:
        old, _, new = part.partition(':')
        pairs.append((need(old.strip(), columns, '--columns'), (new or old).strip()))
    names = [n for _, n in pairs]
    if len(set(names)) != len(names):
        raise ValueError('Yeni sütun adları benzersiz olmalı.')
    out = [{n: r[o] for o, n in pairs} for r in rows]
    return Result(f'{len(pairs)} sütun seçildi ({len(columns) - len(pairs)} sütun çıkarıldı)', names, out, {'sutun': len(pairs)})


class Expr:
    """Safe expression over columns: [Sütun adı] or plain names, numbers, 'text', + - * / % **, comparisons, and/or/not,
    abs/round/min/max/len/lower/upper, contains(x, y), startswith(x, y). No attribute access, no other calls."""
    FUNCS = {'abs': abs, 'round': round, 'min': min, 'max': max, 'len': len, 'lower': tr_lower, 'upper': tr_upper,
             'contains': lambda a, b: str(b).casefold() in str(a).casefold(), 'startswith': lambda a, b: str(a).startswith(str(b)),
             'num': lambda v: tabular.parse_number(str(v)) or 0, 'empty': lambda v: not str(v).strip()}
    NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Call, ast.And, ast.Or, ast.Not,
             ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
             ast.List, ast.Tuple, ast.IfExp)

    def __init__(self, text, columns):
        self.names = {}
        def repl(m):
            name = m.group(1)
            need(name, columns, 'İfade')
            key = f'_c{len(self.names)}'
            self.names[key] = name
            return key
        src = re.sub(r'\[([^\]]+)\]', repl, text.replace('=<', '<=').replace('=>', '>='))
        src = re.sub(r'(?<![=!<>])=(?!=)', '==', src)
        try:
            tree = ast.parse(src, mode='eval')
        except SyntaxError as exc:
            raise ValueError(f'İfade okunamadı: {text} ({exc.msg})') from None
        for node in ast.walk(tree):
            if not isinstance(node, self.NODES):
                raise ValueError(f'İfadede izin verilmeyen yapı: {type(node).__name__}')
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in self.FUNCS):
                raise ValueError('İfadede yalnızca şu fonksiyonlar kullanılabilir: ' + ', '.join(self.FUNCS))
            if isinstance(node, ast.Name) and node.id not in self.FUNCS and node.id not in self.names:
                if node.id in columns:
                    self.names[node.id] = node.id
                elif node.id not in ('True', 'False'):
                    raise ValueError(f'İfadede bilinmeyen ad: {node.id}. Boşluklu sütunları [Sütun Adı] olarak yazın.')
        self.code = compile(tree, '<ifade>', 'eval')
        self.styles = {}

    def value(self, raw, column, rows):
        if column not in self.styles:
            self.styles[column] = tabular.number_style([r[column] for r in rows])
        n = tabular.parse_number(raw, self.styles[column]) if raw.strip() else None
        return n if n is not None else raw

    def __call__(self, row, rows):
        env = {k: self.value(row[c], c, rows) for k, c in self.names.items()}
        try:
            return eval(self.code, {'__builtins__': {}}, {**self.FUNCS, **env})  # noqa: S307 — AST is whitelisted above
        except (TypeError, ZeroDivisionError, ValueError):
            return None


@op('filtrele', title='Satırları koşula göre filtrele', domain=D, product=P, tag='filtre', samples=SATIS,
    description='“tutar > 5000 and durum != \'İptal\'” gibi okunur bir koşulla satırları süzer. Sayılar sütunun kendi biçimiyle okunur. İfade güvenli alt kümeyle sınırlıdır: dosya veya sistem erişimi yoktur.',
    input='CSV/XLSX + koşul ifadesi', output='sonuc.csv (+ --rest ile dışarıda kalanlar)', command='python main.py --where "tutar >= 10000 and bolge = \'Ege\'"',
    args=[arg('--where', default="tutar >= 10000 and durum != 'İptal'"), arg('--rest', action='store_true', help='Koşula uymayanları da yaz')],
    expect={'eslesen': 4})
def filtrele(columns, rows, args):
    expr = Expr(args.where, columns)
    hit = [r for r in rows if expr(r, rows) is True]
    files = {'kalanlar.csv': (columns, [r for r in rows if r not in hit])} if args.rest else {}
    return Result(f'{len(hit)} / {len(rows)} satır koşula uydu', columns, hit, {'eslesen': len(hit), 'kosul': args.where}, files=files)


@op('hesaplanan-sutun', title='Formülle yeni sütun hesapla', domain=D, product=P, tag='formül', samples=SATIS,
    description='“adet * birim_fiyat - tutar” gibi bir formülle yeni sütun ekler. Hesaplanamayan satırları (boş, sıfıra bölme) boş bırakıp listeler. Formül güvenli ifade alt kümesiyle çalışır.',
    input='CSV/XLSX + ad ve formül', output='sonuc.csv', command='python main.py --name fark --formula "adet * birim_fiyat - tutar"',
    args=[arg('--name', default='fark'), arg('--formula', default='adet * birim_fiyat - tutar'), arg('--decimals', type=int, default=2)],
    expect={'hesaplanan': 41, 'hesaplanamayan': 0})
def hesaplanan(columns, rows, args):
    if args.name in columns:
        raise ValueError(f'"{args.name}" sütunu zaten var; başka ad verin.')
    expr = Expr(args.formula, columns)
    out, bad = copy(rows), []
    for i, r in enumerate(out, start=2):
        v = expr(r, rows)
        if isinstance(v, bool) or v is None or isinstance(v, (int, float)) and not math.isfinite(v):
            r[args.name] = '' if v is None else str(v).lower() if isinstance(v, bool) else ''
            if v is None:
                bad.append([i])
        else:
            r[args.name] = fmt(round(v, args.decimals), args.decimals) if isinstance(v, (int, float)) else str(v)
    nonzero = sum(1 for r in out if r[args.name] not in ('', '0'))
    return Result(f'"{args.name}" {len(rows) - len(bad)} satırda hesaplandı ({nonzero} sıfırdan farklı)', columns + [args.name], out,
                  {'hesaplanan': len(rows) - len(bad), 'hesaplanamayan': len(bad), 'sifirdan_farkli': nonzero},
                  tables=[('Hesaplanamayan satırlar', ['Satır'], bad)] if bad else [])


@op('sirala', title='Türkçe alfabeye göre sırala', domain=D, product=P, tag='sıralama', samples=SATIS,
    description='Bir veya birden çok sütuna göre artan/azalan sıralar. Metinleri Türkçe alfabe sırasıyla (Ç, Ğ, İ, Ö, Ş, Ü doğru yerde), sayıları ve tarihleri değerine göre sıralar.',
    input='CSV/XLSX + sıralama sütunları', output='sonuc.csv', command='python main.py --by "bolge,tutar:desc"',
    args=[arg('--by', default='bolge,tutar:desc')], expect={'satir_sayisi': 41})
def sirala(columns, rows, args):
    out = list(rows)
    specs = [p.strip() for p in args.by.split(',') if p.strip()]
    for part in reversed(specs):
        name, _, direction = part.partition(':')
        name = need(name.strip(), columns, '--by')
        nums = numbers(rows, name)
        numeric = sum(n is not None for n in nums) >= 0.9 * sum(bool(r[name].strip()) for r in rows) and any(n is not None for n in nums)
        index = {id(r): n for r, n in zip(rows, nums)}

        def key(r):
            v = r[name]
            if not v.strip():
                return (1, 0)
            if numeric:
                return (0, index[id(r)] if index[id(r)] is not None else 0)
            d = tabular.parse_date(v)
            return (0, d[0].isoformat() if d else tr_sort_key(v))
        blanks = [r for r in out if not r[name].strip()]
        filled = sorted([r for r in out if r[name].strip()], key=key, reverse=direction.strip().lower() == 'desc')
        out = filled + blanks
    return Result(f'{len(out)} satır {args.by} ile sıralandı', columns, out, {'satir_sayisi': len(out)})


@op('ornekle', title='Rastgele veya katmanlı örneklem al', domain=D, product=P, tag='örneklem', samples=SATIS,
    description='Belirli sayıda ya da yüzde kadar satırı tekrarlanabilir (tohumlu) rastgele seçer. Katman sütunu verilirse her gruptan oranlı örnek alır; denetim örneklemi ve test verisi için.',
    input='CSV/XLSX + adet veya yüzde', output='sonuc.csv', command='python main.py --size 10%% --strata bolge --seed 42',
    args=[arg('--size', default='10', help='Satır sayısı veya yüzde (örn. 25%%)'), arg('--strata', help='Katman sütunu'), arg('--seed', type=int, default=42)],
    expect={'secilen': 10})
def ornekle(columns, rows, args):
    rng = random.Random(args.seed)
    size = args.size.strip()
    ratio = float(size[:-1].replace(',', '.')) / 100 if size.endswith('%') else None
    total = round(len(rows) * ratio) if ratio is not None else int(size)
    if not 0 < total <= len(rows):
        raise ValueError(f'Örneklem büyüklüğü 1 ile {len(rows)} arasında olmalı.')
    index = list(range(len(rows)))
    if args.strata:
        need(args.strata, columns, '--strata')
        groups = defaultdict(list)
        for i in index:
            groups[rows[i][args.strata]].append(i)
        picked = []
        for g, members in sorted(groups.items()):
            k = max(1, round(total * len(members) / len(rows)))
            picked += rng.sample(members, min(k, len(members)))
    else:
        picked = rng.sample(index, total)
    picked.sort()
    return Result(f'{len(picked)} satır seçildi (tohum {args.seed})', columns, [rows[i] for i in picked], {'secilen': len(picked), 'tohum': args.seed})


@op('sira-no', title='Sıra numarası ve grup içi sıra ekle', domain=D, product=P, tag='sıra no', samples=SATIS,
    description='Her satıra sıra numarası ekler; grup ve ölçü verilirse grup içinde büyükten küçüğe sıra (RANK/ROW_NUMBER) hesaplar. “Her bölgenin en büyük 3 siparişi” gibi sorular için.',
    input='CSV/XLSX + isteğe bağlı grup ve ölçü', output='sonuc.csv', command='python main.py --group bolge --by tutar --top 3',
    args=[arg('--group'), arg('--by', help='Sıralama ölçüsü'), arg('--top', type=int, help='Her gruptan yalnızca ilk N satır')],
    test_args=['--group', 'bolge', '--by', 'tutar', '--top', '2'], expect={'satir': 11})
def sira_no(columns, rows, args):
    out = copy(rows)
    for i, r in enumerate(out, start=1):
        r['sira_no'] = i
    cols = columns + ['sira_no']
    if args.by:
        need(args.by, columns, '--by')
        nums = numbers(rows, args.by)
        groups = defaultdict(list)
        for r, n in zip(out, nums):
            groups[r[args.group] if args.group else ''].append((n if n is not None else -math.inf, r))
        for members in groups.values():
            members.sort(key=lambda x: -x[0])
            for rank, (_, r) in enumerate(members, start=1):
                r['grup_ici_sira'] = rank
        cols.append('grup_ici_sira')
        if args.top:
            out = [r for r in out if r['grup_ici_sira'] <= args.top]
            out.sort(key=lambda r: (tr_sort_key(r[args.group]) if args.group else 0, r['grup_ici_sira']))
    if args.group:
        need(args.group, columns, '--group')
    return Result(f'{len(out)} satır numaralandı', cols, out, {'satir': len(out)})


# ---------------------------------------------------------------- sütun işlemleri
@op('sutun-bol', title='Bir sütunu ayırıcıya göre böl', domain=D, product=P, tag='böl', samples=KIRLI,
    description='“Ad Soyad”, “İl/İlçe”, “Kod - Açıklama” gibi birleşik değerleri ayırıcıya göre yeni sütunlara böler. En fazla parça sayısı verilebilir; fazlası son sütunda kalır.',
    input='CSV/XLSX + sütun + ayırıcı', output='sonuc.csv', command='python main.py --column "Ad Soyad" --sep " " --into ad,soyad --max 2',
    args=[arg('--column', default='Müşteri Adı'), arg('--sep', default=' '), arg('--into', default='ad,soyad'), arg('--max', type=int, default=0, help='0: parça sayısı kadar sütun')],
    expect={'bolunen': 7})
def sutun_bol(columns, rows, args):
    need(args.column, columns, '--column')
    names = [n.strip() for n in args.into.split(',') if n.strip()]
    clash = [n for n in names if n in columns]
    if clash:
        raise ValueError(f'Yeni sütun adı zaten var: {", ".join(clash)}')
    parts_max = args.max or len(names)
    out, n = copy(rows), 0
    for r in out:
        v = re.sub(r'\s+', ' ', r[args.column].strip()) if args.sep == ' ' else r[args.column]
        if not v:
            parts = []
        elif args.sep == ' ' and len(names) == 2:
            parts = v.rsplit(' ', 1)  # names: everything but the last word is the first name
        else:
            parts = [p.strip() for p in v.split(args.sep, parts_max - 1)]
        n += len(parts) > 1
        for i, name in enumerate(names):
            r[name] = parts[i] if i < len(parts) else ''
    return Result(f'{n} değer {len(names)} sütuna bölündü', columns + names, out, {'bolunen': n})


@op('sutun-birlestir', title='Sütunları tek sütunda birleştir', domain=D, product=P, tag='birleştir', samples=SATIS,
    description='Birden çok sütunu ayırıcıyla tek değere birleştirir (ör. bölge + ürün anahtarı, adres satırları). Boş parçaları atlar, böylece “İstanbul -  - 34” gibi değerler oluşmaz.',
    input='CSV/XLSX + sütunlar', output='sonuc.csv', command='python main.py --columns bolge,urun_kodu --sep " | " --name anahtar',
    args=[arg('--columns', default='bolge,urun_kodu'), arg('--sep', default='-'), arg('--name', default='birlesik')], expect={'satir': 41})
def sutun_birlestir(columns, rows, args):
    cols = col_list(args.columns, columns)
    if args.name in columns:
        raise ValueError(f'"{args.name}" sütunu zaten var.')
    out = copy(rows)
    for r in out:
        r[args.name] = args.sep.join(r[c].strip() for c in cols if r[c].strip())
    return Result(f'{len(cols)} sütun "{args.name}" sütununda birleştirildi', columns + [args.name], out, {'satir': len(out)})


@op('bul-degistir', title='Eşleme tablosuyla toplu bul-değiştir', domain=D, product=P, tag='bul/değiştir', samples={**SATIS, 'esleme.csv': 'esleme.csv'},
    description='Bir eşleme dosyasındaki (eski;yeni) değerleri seçilen sütunlarda değiştirir; tam eşleşme, büyük/küçük harf duyarsız veya düzenli ifade modunda çalışır. Hiç kullanılmayan eşleme satırlarını raporlar.',
    input='CSV/XLSX + eşleme dosyası (eski, yeni)', output='sonuc.csv', command='python main.py --columns durum --map esleme.csv --mode tam',
    args=[arg('--columns', default='durum'), arg('--map', default='@ornek/esleme.csv'), arg('--mode', choices=['tam', 'harfsiz', 'regex'], default='tam')],
    expect={'degisen_hucre': 41, 'kullanilmayan_esleme': 0})
def bul_degistir(columns, rows, args):
    cols = col_list(args.columns, columns)
    mc, mr = tabular.read(args.map)
    if 'eski' not in mc or 'yeni' not in mc:
        raise ValueError('Eşleme dosyasında "eski" ve "yeni" sütunları olmalı.')
    pairs = [(m['eski'], m['yeni']) for m in mr]
    used = Counter()
    out, n = copy(rows), 0
    for r in out:
        for c in cols:
            v = r[c]
            for old, new in pairs:
                if args.mode == 'regex':
                    nv = re.sub(old, new, v)
                    hit = nv != v
                elif args.mode == 'harfsiz':
                    hit = fold(v) == fold(old)
                    nv = new if hit else v
                else:
                    hit = v == old
                    nv = new if hit else v
                if hit:
                    used[old] += 1
                    v = nv
                    if args.mode != 'regex':
                        break
            n += v != r[c]
            r[c] = v
    unused = [o for o, _ in pairs if not used[o]]
    return Result(f'{n} hücre değişti', columns, out, {'degisen_hucre': n, 'kullanilmayan_esleme': len(unused)},
                  tables=[('Eşleme kullanımı', ['Eski', 'Yeni', 'Kullanım'], [[o, w, used[o]] for o, w in pairs])])


@op('deger-esle', title='Kod tablosundan açıklama getir (DÜŞEYARA)', domain=D, product=P, tag='lookup', samples={**SATIS, 'urunler.csv': 'urunler.csv'},
    description='Ana dosyadaki bir kod sütununu referans tablosuyla eşleştirip istenen sütunları ekler (Excel DÜŞEYARA/XLOOKUP karşılığı). Eşleşmeyen kodları ve referansta tekrar eden anahtarları raporlar.',
    input='CSV/XLSX + referans dosyası + anahtar', output='sonuc.csv + eslesmeyen kod listesi', command='python main.py --lookup urunler.csv --key urun_kodu --add kategori,birim_maliyet',
    args=[arg('--lookup', default='@ornek/urunler.csv'), arg('--key', default='urun_kodu'), arg('--lookup-key', help='Referanstaki anahtar adı (farklıysa)'),
          arg('--add', default='kategori,birim_maliyet')], expect={'eslesen': 35, 'eslesmeyen': 6})
def deger_esle(columns, rows, args):
    need(args.key, columns, '--key')
    lc, lr = tabular.read(args.lookup)
    lkey = need(args.lookup_key or args.key, lc, '--lookup-key')
    add = col_list(args.add, lc, '--add')
    clash = [a for a in add if a in columns]
    if clash:
        raise ValueError(f'Ana dosyada zaten olan sütun: {", ".join(clash)}')
    ref, dupes = {}, set()
    for r in lr:
        k = r[lkey].strip()
        if k in ref:
            dupes.add(k)
        ref.setdefault(k, r)
    out, miss = copy(rows), Counter()
    for r in out:
        hit = ref.get(r[args.key].strip())
        if not hit:
            miss[r[args.key]] += 1
        for a in add:
            r[a] = hit[a] if hit else ''
    notes = [f'Referansta tekrar eden anahtar (ilk satır kullanıldı): {", ".join(sorted(dupes))}'] if dupes else []
    return Result(f'{len(rows) - sum(miss.values())} satır eşleşti, {sum(miss.values())} eşleşmedi', columns + add, out,
                  {'eslesen': len(rows) - sum(miss.values()), 'eslesmeyen': sum(miss.values())}, notes=notes,
                  tables=[('Eşleşmeyen kodlar', ['Kod', 'Satır sayısı'], miss.most_common())] if miss else [])


@op('tablo-birlestir', title='İki tabloyu anahtarla birleştir (JOIN)', domain=D, product=P, tag='join', samples={**SATIS, 'urunler.csv': 'urunler.csv'},
    description='İki dosyayı bir veya birden çok anahtar sütunla INNER, LEFT, RIGHT veya FULL birleştirir. Çoklayan (bire-çok) eşleşmeleri ve eşleşmeyen satırları raporlar; satır patlamasını önceden gösterir.',
    input='İki CSV/XLSX + anahtar(lar) + birleşim türü', output='sonuc.csv', command='python main.py --right urunler.csv --keys urun_kodu --how left',
    args=[arg('--right', default='@ornek/urunler.csv'), arg('--keys', default='urun_kodu'), arg('--how', choices=['inner', 'left', 'right', 'full'], default='full')],
    expect={'satir': 42, 'sadece_sol': 6, 'sadece_sag': 1})
def tablo_birlestir(columns, rows, args):
    rc, rr = tabular.read(args.right)
    keys = col_list(args.keys, columns, '--keys')
    for k in keys:
        need(k, rc, '--keys (sağ dosya)')
    extra = [c for c in rc if c not in keys]
    renamed = {c: (c + '_sag' if c in columns else c) for c in extra}
    index = defaultdict(list)
    for r in rr:
        index[tuple(r[k].strip() for k in keys)].append(r)
    out, used, left_only, multi = [], set(), 0, 0
    for r in rows:
        k = tuple(r[c].strip() for c in keys)
        matches = index.get(k, [])
        multi += len(matches) > 1
        if matches:
            used.add(k)
            for m in matches:
                out.append({**r, **{renamed[c]: m[c] for c in extra}})
        else:
            left_only += 1
            if args.how in ('left', 'full'):
                out.append({**r, **{renamed[c]: '' for c in extra}})
    right_only = [r for k, group in index.items() if k not in used for r in group]
    if args.how in ('right', 'full'):
        out += [{**{c: '' for c in columns}, **{k: r[k] for k in keys}, **{renamed[c]: r[c] for c in extra}} for r in right_only]
    if args.how == 'right':
        out = [r for r in out if tuple(r[k].strip() for k in keys) in index]
    cols = columns + [renamed[c] for c in extra]
    return Result(f'{args.how.upper()} JOIN: {len(out)} satır', cols, out, {'satir': len(out), 'sadece_sol': left_only, 'sadece_sag': len(right_only), 'coklayan_anahtar': multi})


@op('grupla', title='Grupla ve özetle (pivot toplamları)', domain=D, product=P, tag='özet', samples=SATIS,
    description='Seçilen sütunlara göre gruplar; toplam, adet, ortalama, en küçük, en büyük, farklı değer sayısı ve pay (%) hesaplar. Excel özet tablosunun tekrar çalıştırılabilir karşılığı.',
    input='CSV/XLSX + grup sütunları + ölçüler', output='sonuc.csv', command='python main.py --by bolge --measures "tutar:sum,tutar:avg,siparis_no:count"',
    args=[arg('--by', default='bolge'), arg('--measures', default='tutar:sum,siparis_no:count,musteri_no:distinct'), arg('--where', help='Önce filtrele (filtrele bloğu sözdizimi)')],
    expect={'grup': 6})
def grupla(columns, rows, args):
    by = col_list(args.by, columns, '--by')
    if args.where:
        expr = Expr(args.where, columns)
        rows = [r for r in rows if expr(r, rows) is True]
    measures = []
    for part in [p.strip() for p in args.measures.split(',') if p.strip()]:
        name, _, fn = part.partition(':')
        fn = (fn or 'sum').strip().lower()
        if fn not in ('sum', 'count', 'avg', 'min', 'max', 'distinct', 'share'):
            raise ValueError(f'Bilinmeyen özet: {fn} (sum, count, avg, min, max, distinct, share)')
        measures.append((need(name.strip(), columns, '--measures'), fn))
    parsed = {m: numbers(rows, m) for m, fn in measures if fn in ('sum', 'avg', 'min', 'max', 'share')}
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[tuple(r[c].strip() for c in by)].append(i)
    totals = {m: sum(n for n in parsed[m] if n is not None) for m in parsed}
    out = []
    for key, idx in sorted(groups.items(), key=lambda kv: [tr_sort_key(x) for x in kv[0]]):
        row = dict(zip(by, key))
        for m, fn in measures:
            label = f'{m}_{fn}'
            vals = [parsed[m][i] for i in idx if parsed[m][i] is not None] if m in parsed else []
            row[label] = (fmt(sum(vals)) if fn == 'sum' else len(idx) if fn == 'count' else fmt(sum(vals) / len(vals)) if fn == 'avg' and vals else
                          fmt(min(vals)) if fn == 'min' and vals else fmt(max(vals)) if fn == 'max' and vals else
                          len({rows[i][m] for i in idx}) if fn == 'distinct' else fmt(100 * sum(vals) / totals[m], 1) if fn == 'share' and totals[m] else '')
        out.append(row)
    cols = by + [f'{m}_{fn}' for m, fn in measures]
    return Result(f'{len(out)} grup özetlendi', cols, out, {'grup': len(out), 'girdi_satir': len(rows)})


@op('pivot', title='Uzun tabloyu geniş tabloya çevir (pivot)', domain=D, product=P, tag='pivot', samples=SATIS,
    description='Satırlardaki bir sütunun değerlerini sütun başlığına çevirir ve ölçüyü toplar (ör. bölge × durum ciro matrisi). Satır ve sütun toplamlarını ekler.',
    input='CSV/XLSX + satır, sütun ve ölçü alanları', output='sonuc.csv', command='python main.py --rows bolge --cols durum --value tutar',
    args=[arg('--rows', default='bolge'), arg('--cols', default='durum'), arg('--value', default='tutar'), arg('--agg', choices=['sum', 'count', 'avg'], default='sum')],
    expect={'satir': 6, 'sutun': 4})
def pivot(columns, rows, args):
    rk, ck = need(args.rows, columns, '--rows'), need(args.cols, columns, '--cols')
    vals = numbers(rows, need(args.value, columns, '--value'))
    cells = defaultdict(list)
    for r, v in zip(rows, vals):
        cells[(r[rk].strip(), r[ck].strip())].append(v or 0.0)
    row_keys = sorted({k[0] for k in cells}, key=tr_sort_key)
    col_keys = sorted({k[1] for k in cells}, key=tr_sort_key)
    agg = {'sum': sum, 'count': len, 'avg': lambda x: sum(x) / len(x)}[args.agg]
    out = []
    for a in row_keys:
        row = {rk: a or '(boş)'}
        for b in col_keys:
            row[b or '(boş)'] = fmt(agg(cells[(a, b)])) if (a, b) in cells else ''
        if args.agg != 'avg':
            row['TOPLAM'] = fmt(agg([x for b in col_keys for x in cells.get((a, b), [])]))
        out.append(row)
    cols = [rk] + [b or '(boş)' for b in col_keys] + (['TOPLAM'] if args.agg != 'avg' else [])
    return Result(f'{len(row_keys)} satır × {len(col_keys)} sütun pivot', cols, out, {'satir': len(row_keys), 'sutun': len(col_keys)})


@op('unpivot', title='Geniş tabloyu uzun tabloya çevir (unpivot)', domain=D, product=P, tag='unpivot', samples={'aylik_hedef.csv': 'aylik_hedef.csv'},
    description='Ay, yıl veya ürün gibi sütun başlıklarına yayılmış değerleri “anahtar, değer” satırlarına çevirir (Power Query “Unpivot”). BI araçlarına ve veritabanına yüklemeye uygun biçim üretir.',
    input='CSV/XLSX + sabit kalacak sütunlar', output='sonuc.csv', command='python main.py --keep bolge --name ay --value hedef',
    args=[arg('--keep', default='bolge'), arg('--name', default='ay'), arg('--value', default='hedef'), arg('--drop-empty', action='store_true', default=True)],
    expect={'satir': 11})
def unpivot(columns, rows, args):
    keep = col_list(args.keep, columns, '--keep')
    melt = [c for c in columns if c not in keep]
    out = []
    for r in rows:
        for c in melt:
            if args.drop_empty and not r[c].strip():
                continue
            out.append({**{k: r[k] for k in keep}, args.name: c, args.value: r[c]})
    return Result(f'{len(melt)} sütun {len(out)} satıra açıldı', keep + [args.name, args.value], out, {'satir': len(out), 'acilan_sutun': len(melt)})


@op('kumulatif-toplam', title='Kümülatif toplam ve hareketli ortalama', domain=D, product=P, tag='kümülatif', samples={'gunluk_satis.csv': 'gunluk_satis.csv'},
    description='Tarihe göre sıralayıp (isteğe bağlı grup içinde) yürüyen toplam, yürüyen pay ve N dönemlik hareketli ortalama ekler. Yıl başından bugüne (YTD) ve hedefe ilerleme tabloları için.',
    input='CSV/XLSX + tarih ve ölçü sütunu', output='sonuc.csv', command='python main.py --date tarih --value ciro --group bolge --window 7',
    args=[arg('--date', default='tarih'), arg('--value', default='ciro'), arg('--group', default='bolge'), arg('--window', type=int, default=7)],
    expect={'satir': 736, 'grup': 4})
def kumulatif(columns, rows, args):
    need(args.date, columns, '--date')
    vals = numbers(rows, need(args.value, columns, '--value'))
    groups = defaultdict(list)
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[args.date])
        groups[r[need(args.group, columns, '--group')] if args.group else ''].append((d[0] if d else datetime.max, v or 0.0, r))
    out = []
    for g in sorted(groups, key=tr_sort_key):
        items = sorted(groups[g], key=lambda x: x[0])
        run, window = 0.0, []
        for d, v, r in items:
            run += v
            window = (window + [v])[-args.window:]
            out.append({**r, 'kumulatif': fmt(round(run, 2)), f'ort_{args.window}': fmt(round(sum(window) / len(window), 2))})
    return Result(f'{len(out)} satıra kümülatif toplam eklendi', columns + ['kumulatif', f'ort_{args.window}'], out, {'satir': len(out), 'grup': len(groups)})


@op('tarih-farki', title='İki tarih arasındaki gün ve iş günü farkı', domain=D, product=P, tag='gün farkı', samples=SATIS,
    description='İki tarih sütunu (veya bir sütun ile bugün/sabit tarih) arasındaki takvim günü, iş günü (hafta sonu hariç) ve ay farkını hesaplar. Teslim süresi, vade ve SLA ölçümleri için.',
    input='CSV/XLSX + başlangıç ve bitiş tarihi', output='sonuc.csv', command='python main.py --start siparis_tarihi --end teslim_tarihi',
    args=[arg('--start', default='siparis_tarihi'), arg('--end', default='2026-10-01', help='Sütun adı veya YYYY-AA-GG')],
    expect={'hesaplanan': 41, 'okunamayan': 0})
def tarih_farki(columns, rows, args):
    need(args.start, columns, '--start')
    fixed = None
    if args.end not in columns:
        d = tabular.parse_date(args.end)
        if not d:
            raise ValueError(f'--end bir sütun adı ya da tarih olmalı: {args.end}')
        fixed = d[0]
    out, bad = copy(rows), 0
    for r in out:
        a = tabular.parse_date(r[args.start])
        b = (fixed,) if fixed else tabular.parse_date(r[args.end])
        if not a or not b or b[0] is None:
            r.update(gun_farki='', is_gunu_farki='', ay_farki='')
            bad += 1
            continue
        a, b = a[0].date(), b[0].date()
        days = (b - a).days
        lo, hi = min(a, b), max(a, b)
        full_weeks, rest = divmod((hi - lo).days, 7)
        work = full_weeks * 5 + sum(1 for i in range(rest) if (lo.weekday() + i) % 7 < 5)
        r.update(gun_farki=days, is_gunu_farki=work if days >= 0 else -work, ay_farki=(b.year - a.year) * 12 + b.month - a.month - (b.day < a.day))
    return Result(f'{len(rows) - bad} satırda tarih farkı hesaplandı', columns + ['gun_farki', 'is_gunu_farki', 'ay_farki'], out,
                  {'hesaplanan': len(rows) - bad, 'okunamayan': bad},
                  notes=['İş günü hesabı hafta sonlarını çıkarır; resmî tatiller için tarih-tablosu bloğunun is_gunu sütununu kullanın.'])


@op('bos-satir-sutun', title='Boş ve sabit sütunları kaldır', domain=D, product=P, tag='sadeleştir', samples=SATIS,
    description='Tamamen boş sütunları, tek bir sabit değer taşıyan sütunları ve boş satırları bulur; isteğe bağlı olarak kaldırır. Sistem dışa aktarımlarındaki gereksiz kolonları ayıklamak için.',
    input='CSV/XLSX', output='sonuc.csv', command='python main.py --drop-constant',
    args=[arg('--drop-constant', action='store_true', help='Tek değerli sütunları da kaldır'), arg('--threshold', type=float, default=100.0, help='Boşluk yüzdesi bu değere eşit/üstündeyse kaldır')],
    expect={'kaldirilan_sutun': 0, 'sabit_sutun': 0})
def bos_satir(columns, rows, args):
    drop, constant = [], []
    for c in columns:
        filled = [r[c].strip() for r in rows if r[c].strip()]
        pct = 100 * (len(rows) - len(filled)) / len(rows) if rows else 100
        if pct >= args.threshold:
            drop.append((c, f'%{pct:.0f} boş'))
        elif len(set(filled)) == 1 and len(filled) == len(rows):
            constant.append(c)
            if args.drop_constant:
                drop.append((c, f'sabit: {filled[0]}'))
    keep = [c for c in columns if c not in dict(drop)]
    out = [{c: r[c] for c in keep} for r in rows if any(r[c].strip() for c in keep)]
    return Result(f'{len(drop)} sütun kaldırıldı', keep, out, {'kaldirilan_sutun': len(drop), 'sabit_sutun': len(constant), 'kalan_satir': len(out)},
                  tables=[('Kaldırılan sütunlar', ['Sütun', 'Neden'], drop)] if drop else [])


@op('dosya-bol', title='Dosyayı gruba veya satır sayısına göre böl', domain=D, product=P, tag='böl', samples=SATIS,
    description='Bir dosyayı sütun değerine göre (her bölge ayrı dosya) veya sabit satır sayısına göre parçalara ayırır. Dosya adları güvenli hâle getirilir; her parçanın satır sayısı raporlanır.',
    input='CSV/XLSX + grup sütunu veya parça boyu', output='parcalar/*.csv + rapor.md', command='python main.py --by bolge  |  python main.py --rows 50000',
    args=[arg('--by', default='bolge'), arg('--rows', type=int, help='Grup yerine her parçada en fazla bu kadar satır')], expect={'parca': 6})
def dosya_bol(columns, rows, args):
    parts = defaultdict(list)
    if args.rows:
        for i, r in enumerate(rows):
            parts[f'parca_{i // args.rows + 1:03d}'].append(r)
    else:
        need(args.by, columns, '--by')
        for r in rows:
            parts[r[args.by].strip() or 'bos'].append(r)
    files, table = {}, []
    for name, members in sorted(parts.items()):
        safe = re.sub(r'[^\w.-]+', '_', fold(name)).strip('_') or 'bos'
        files[f'parcalar/{safe}.csv'] = (columns, members)
        table.append([name, f'parcalar/{safe}.csv', len(members)])
    return Result(f'{len(parts)} parça yazıldı', None, None, {'parca': len(parts)}, tables=[('Parçalar', ['Grup', 'Dosya', 'Satır'], table)], files=files)


@op('csv-donustur', title='CSV ayırıcı ve kodlama dönüştür', domain=D, product=P, tag='kodlama', samples=SATIS,
    description='Dosyayı istenen ayırıcıya (virgül, noktalı virgül, sekme, dikey çizgi) ve kodlamaya (UTF-8, UTF-8 BOM, Windows-1254) çevirir. Excel’de Türkçe karakter bozulması ve tek sütunda açılma sorunlarını çözer.',
    input='CSV/XLSX', output='donusmus.csv', command='python main.py --sep ";" --encoding cp1254',
    args=[arg('--sep', default=',', help='Yeni ayırıcı: , ; tab |'), arg('--encoding', choices=['utf-8', 'utf-8-sig', 'cp1254'], default='utf-8-sig')],
    expect={'satir': 41})
def csv_donustur(columns, rows, args):
    import csv, io
    sep = '\t' if args.sep in ('tab', '\\t') else args.sep
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator='\r\n')
    w.writerow(columns)
    for r in rows:
        w.writerow([r[c] for c in columns])
    try:
        data = buf.getvalue().encode(args.encoding)
    except UnicodeEncodeError as exc:
        raise ValueError(f'Bazı karakterler {args.encoding} ile yazılamıyor ({exc.object[exc.start:exc.end]!r}); utf-8-sig seçin.') from None
    return Result(f'{len(rows)} satır "{args.sep}" ayırıcı ve {args.encoding} ile yazıldı', None, None, {'satir': len(rows), 'ayirici': args.sep, 'kodlama': args.encoding},
                  files={'donusmus.csv': data})
