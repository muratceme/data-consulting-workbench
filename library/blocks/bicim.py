"""Dosya biçimi dönüştürme blokları: JSON, JSON Lines, XML, sabit genişlikli metin, Excel sayfaları, XLSX yazma, web logu, HTML tablo."""
import html, json, re, zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
import tabular
from islem import op, arg, Result, need, fmt

D, P = 'integration', 'Dosya biçimi dönüştürme'
MAX_BYTES = 200 * 1024 * 1024


def read_text(path, encoding='utf-8-sig'):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'Dosya bulunamadı: {p}')
    if p.stat().st_size > MAX_BYTES:
        raise ValueError('Dosya 200 MB sınırını aşıyor.')
    return p.read_text(encoding=encoding, errors='replace')


def flatten(obj, prefix='', out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f'{prefix}.{k}' if prefix else str(k), out)
    elif isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            out[prefix] = ' | '.join('' if x is None else str(x) for x in obj)
        else:
            out[prefix] = json.dumps(obj, ensure_ascii=False)
    else:
        out[prefix] = '' if obj is None else str(obj).lower() if isinstance(obj, bool) else fmt(obj) if isinstance(obj, float) else str(obj)
    return out


def find_records(data, path):
    if path:
        for part in path.split('.'):
            if not isinstance(data, dict) or part not in data:
                raise ValueError(f'--path "{path}" JSON içinde bulunamadı.')
            data = data[part]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lists = [(k, v) for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) == 1:
            return lists[0][1]
        if not lists:
            return [data]
        raise ValueError(f'Birden çok kayıt listesi var ({", ".join(k for k, _ in lists)}); --path ile seçin.')
    raise ValueError('JSON bir nesne veya liste olmalı.')


def columns_of(rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


@op('json-csv', title='İç içe JSON’u tabloya düzleştir', domain=D, product=P, tag='JSON', kind='file', samples={'siparisler.json': 'siparisler.json'},
    args=[arg('--path', help='Kayıt listesinin yolu, örn. veri.kayitlar (otomatik bulunur)'), arg('--explode', default='kalemler', help='Satırlara açılacak alt liste')],
    description='API dışa aktarımlarındaki iç içe JSON’u “musteri.ad” gibi noktalı sütunlara düzleştirir; seçilen alt listeyi (sipariş kalemleri gibi) üst kayıt bilgisiyle satırlara açar, değer listelerini “|” ile birleştirir. Kayıt listesini otomatik bulur.',
    input='.json dosyası', output='sonuc.csv', command='python main.py --input cevap.json --explode kalemler',
    expect={'kayit': 5, 'satir': 8})
def json_csv(args):
    try:
        data = json.loads(read_text(args.input))
    except json.JSONDecodeError as exc:
        raise ValueError(f'JSON okunamadı: {exc.msg} (satır {exc.lineno})') from None
    records = find_records(data, args.path)
    rows = []
    for rec in records:
        if not isinstance(rec, dict):
            rec = {'deger': rec}
        children = rec.get(args.explode) if args.explode else None
        base = flatten({k: v for k, v in rec.items() if not (args.explode and k == args.explode and isinstance(v, list))})
        if isinstance(children, list) and children:
            rows += [{**base, **flatten(c, args.explode)} for c in children]
        else:
            rows.append(base)
    cols = columns_of(rows)
    return Result(f'{len(records)} kayıt {len(rows)} satıra, {len(cols)} sütuna düzleştirildi', cols, rows, {'kayit': len(records), 'sutun': len(cols)})


def typed(v):
    s = v.strip()
    if not s:
        return None
    if s.lower() in ('true', 'false'):
        return s.lower() == 'true'
    if re.fullmatch(r'-?(0|[1-9]\d*)', s):
        return int(s)
    n = tabular.parse_number(s)
    if n is not None and not re.fullmatch(r'0\d+', s) and not re.search(r'[A-Za-z]', s):
        return n
    return v


@op('csv-json', title='CSV/Excel tablosunu JSON’a çevir', domain=D, product=P, tag='JSON', samples={'satislar.csv': 'satislar.csv'},
    args=[arg('--group-by', help='Bu sütuna göre iç içe JSON üret'), arg('--no-types', action='store_true', help='Tüm değerleri metin bırak'), arg('--lines', action='store_true', help='JSON Lines yaz')],
    description='Tabloyu kayıt listesi JSON’una (veya JSON Lines’a) çevirir; sayıları, mantıksal değerleri ve boşları (null) tipine göre yazar, baştaki sıfırlı kodları metin olarak korur. İsteğe bağlı olarak bir sütuna göre gruplanmış iç içe yapı üretir.',
    input='CSV/XLSX', output='veri.json veya veri.jsonl', command='python main.py --group-by bolge',
    expect={'kayit': 41})
def csv_json(columns, rows, args):
    conv = (lambda v: v) if args.no_types else typed
    recs = [{c: conv(r[c]) for c in columns} for r in rows]
    if args.group_by:
        need(args.group_by, columns, '--group-by')
        grouped = {}
        for rec in recs:
            grouped.setdefault(str(rec.pop(args.group_by) or ''), []).append(rec)
        payload = json.dumps(grouped, ensure_ascii=False, indent=2)
    elif args.lines:
        payload = '\n'.join(json.dumps(r, ensure_ascii=False) for r in recs)
    else:
        payload = json.dumps(recs, ensure_ascii=False, indent=2)
    name = 'veri.jsonl' if args.lines and not args.group_by else 'veri.json'
    return Result(f'{len(recs)} kayıt {name} dosyasına yazıldı', None, None, {'kayit': len(recs)}, files={name: payload})


@op('jsonl-csv', title='JSON Lines (NDJSON) logunu tabloya çevir', domain=D, product=P, tag='JSONL', kind='file', samples={'uygulama.jsonl': 'uygulama.jsonl'},
    description='Her satırı bir JSON nesnesi olan uygulama/olay loglarını iç içe alanları düzleştirerek tabloya çevirir; bozuk satırları satır numarasıyla ayrı listeler ve en sık değerleri (seviye, servis) özetler.',
    input='.jsonl / .ndjson / .log', output='sonuc.csv + hatali-satirlar.csv', command='python main.py --input app.jsonl',
    expect={'kayit': 40, 'hatali': 1})
def jsonl(args):
    rows, bad = [], []
    for n, line in enumerate(read_text(args.input).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append({'satir': n, 'hata': exc.msg, 'icerik': line[:120]})
            continue
        rows.append(flatten(obj if isinstance(obj, dict) else {'deger': obj}))
    cols = columns_of(rows)
    tables = []
    for c in cols:
        values = Counter(r.get(c, '') for r in rows)
        if 1 < len(values) <= 10:
            tables.append((f'{c} dağılımı', ['Değer', 'Adet'], values.most_common()))
    return Result(f'{len(rows)} kayıt okundu, {len(bad)} satır bozuk', cols, rows, {'kayit': len(rows), 'hatali': len(bad)}, tables=tables[:4],
                  files={'hatali-satirlar.csv': (['satir', 'hata', 'icerik'], bad)})


@op('xml-csv', title='XML’deki tekrar eden kayıtları tabloya çevir', domain=D, product=P, tag='XML', kind='file', samples={'urun_katalogu.xml': 'urun_katalogu.xml'},
    args=[arg('--record', help='Kayıt öğesinin adı, örn. urun (verilmezse en çok tekrar eden öğe)')],
    description='XML’de tekrar eden kayıt öğelerini (ürün, fatura, personel) satıra, özniteliklerini “@kod”, alt öğelerini ve onların özniteliklerini sütuna çevirir; ad alanlarını sadeleştirir. DTD/ENTITY içeren XML’i güvenlik nedeniyle işlemez.',
    input='.xml dosyası', output='sonuc.csv', command='python main.py --record urun',
    expect={'kayit': 5})
def xml_csv(args):
    raw = Path(args.input).read_bytes() if Path(args.input).is_file() else b''
    if not raw:
        raise ValueError(f'Dosya bulunamadı veya boş: {args.input}')
    if b'<!DOCTYPE' in raw[:5000] or b'<!ENTITY' in raw[:20000]:
        raise ValueError('DTD/ENTITY içeren XML güvenlik nedeniyle işlenmez.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f'XML okunamadı: {exc}') from None
    local = lambda tag: tag.split('}')[-1]
    name = args.record
    if not name:
        counts = Counter(local(e.tag) for e in root.iter() if e is not root and len(e))
        if not counts:
            raise ValueError('Tekrar eden kayıt öğesi bulunamadı; --record verin.')
        name = counts.most_common(1)[0][0]
    rows = []
    for e in root.iter():
        if local(e.tag) != name:
            continue
        row = {f'@{local(k)}': v for k, v in e.attrib.items()}
        if (e.text or '').strip() and not len(e):
            row[name] = e.text.strip()
        for child in e.iter():
            if child is e:
                continue
            path = local(child.tag)
            if (child.text or '').strip():
                row[path] = child.text.strip()
            for k, v in child.attrib.items():
                row[f'{path}@{local(k)}'] = v
        rows.append(row)
    if not rows:
        raise ValueError(f'"{name}" öğesi bulunamadı.')
    cols = columns_of(rows)
    return Result(f'{len(rows)} <{name}> kaydı {len(cols)} sütuna çevrildi', cols, rows, {'kayit': len(rows), 'kayit_ogesi': name})


@op('sabit-genislik', title='Sabit genişlikli metin dosyasını tabloya çevir', domain=D, product=P, tag='sabit genişlik', kind='file',
    samples={'sabit_genislik.txt': 'sabit_genislik.txt', 'sabit_genislik_tanim.csv': 'sabit_genislik_tanim.csv'},
    args=[arg('--spec', default='@ornek/sabit_genislik_tanim.csv', help='Alan tanımı: alan, baslangic (1’den), uzunluk, tip'), arg('--encoding', default='cp1254')],
    description='Banka, SGK ve eski ana bilgisayar dışa aktarımları gibi sabit genişlikli dosyaları alan tanım dosyasına göre sütunlara böler; “tarih:%Y%m%d”, “sayi:2” (son 2 hane kuruş) gibi tiplerle değerleri dönüştürür ve çevrilemeyen alanları satır numarasıyla raporlar.',
    input='Metin dosyası + alan tanımı CSV’si', output='sonuc.csv + hatalar', command='python main.py --input HESAP.TXT --spec tanim.csv --encoding cp1254',
    expect={'satir': 5, 'hatali_alan': 1})
def sabit(args):
    sc, sr = tabular.read(args.spec)
    for c in ('alan', 'baslangic', 'uzunluk'):
        need(c, sc, '--spec')
    fields = [(r['alan'].strip(), int(r['baslangic']) - 1, int(r['uzunluk']), r.get('tip', 'metin').strip() or 'metin') for r in sr]
    rows, errors = [], []
    for n, line in enumerate(read_text(args.input, args.encoding).splitlines(), start=1):
        if not line.strip():
            continue
        row = {}
        for name, start, length, kind in fields:
            raw = line[start:start + length]
            v = raw.strip()
            if kind.startswith('tarih') and v:
                fmt_ = kind.split(':', 1)[1] if ':' in kind else '%Y%m%d'
                try:
                    v = datetime.strptime(v, fmt_).date().isoformat()
                except ValueError:
                    errors.append([n, name, raw])
            elif kind.startswith('sayi') and v:
                scale = int(kind.split(':', 1)[1]) if ':' in kind else 0
                if re.fullmatch(r'[+-]?\d+', v):
                    v = fmt(int(v) / 10 ** scale, scale) if scale else str(int(v))
                else:
                    errors.append([n, name, raw])
            row[name] = v
        rows.append(row)
    return Result(f'{len(rows)} satır {len(fields)} alana bölündü; {len(errors)} alan çevrilemedi', [f[0] for f in fields], rows,
                  {'satir': len(rows), 'hatali_alan': len(errors)}, tables=[('Çevrilemeyen alanlar', ['Satır', 'Alan', 'Ham değer'], errors)] if errors else [])


NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


@op('excel-sayfalar', title='Excel dosyasındaki tüm sayfaları CSV’ye ayır', domain=D, product=P, tag='Excel', kind='file', samples={'rapor_dosyasi.xlsx': 'rapor_dosyasi.xlsx'},
    description='XLSX dosyasındaki her sayfayı ayrı UTF-8 CSV dosyasına yazar ve sayfa başına satır/sütun sayısını listeler. Excel yüklü olmadan, makro çalıştırmadan çalışır; .xls için önce .xlsx’e kaydedilmesi gerekir.',
    input='.xlsx / .xlsm', output='sayfalar/*.csv + rapor', command='python main.py --input rapor.xlsx',
    expect={'sayfa': 3})
def excel(args):
    p = Path(args.input)
    try:
        with zipfile.ZipFile(p) as z:
            book = ET.fromstring(z.read('xl/workbook.xml'))
    except (zipfile.BadZipFile, KeyError):
        raise ValueError(f'{p.name} geçerli bir XLSX değil.') from None
    names = [s.get('name') for s in book.findall('m:sheets/m:sheet', NS)]
    files, table = {}, []
    for name in names:
        cols, rows = tabular.read_xlsx(p, name)
        safe = re.sub(r'[^\w.-]+', '_', name).strip('_') or 'sayfa'
        files[f'sayfalar/{safe}.csv'] = (cols, rows)
        table.append([name, f'sayfalar/{safe}.csv', len(rows), len(cols)])
    return Result(f'{len(names)} sayfa CSV’ye ayrıldı', None, None, {'sayfa': len(names)}, tables=[('Sayfalar', ['Sayfa', 'Dosya', 'Satır', 'Sütun'], table)], files=files)


def col_letter(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(columns, rows, sheet='Veri'):
    """Minimal valid XLSX: bold header, frozen first row, numbers stored as numbers, autofilter."""
    styles = {c: tabular.number_style([r[c] for r in rows]) for c in columns}
    numeric = {c: all(tabular.parse_number(r[c], styles[c]) is not None for r in rows if r[c].strip()) and any(r[c].strip() for r in rows)
               and not any(re.fullmatch(r'0\d+', r[c].strip()) for r in rows) for c in columns}
    def cell(ref, value, c, header=False):
        if header:
            return f'<c r="{ref}" t="inlineStr" s="1"><is><t>{escape(value)}</t></is></c>'
        if not value.strip():
            return ''
        if numeric[c]:
            return f'<c r="{ref}"><v>{tabular.parse_number(value, styles[c])!r}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
    data = ['<row r="1">' + ''.join(cell(f'{col_letter(i)}1', c, c, True) for i, c in enumerate(columns)) + '</row>']
    for n, r in enumerate(rows, start=2):
        data.append(f'<row r="{n}">' + ''.join(cell(f'{col_letter(i)}{n}', r[c], c) for i, c in enumerate(columns)) + '</row>')
    last = f'{col_letter(len(columns) - 1)}{len(rows) + 1}'
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    sheet_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet {ns}><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 f'<sheetData>{"".join(data)}</sheetData><autoFilter ref="A1:{last}"/></worksheet>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<?xml version="1.0" encoding="UTF-8"?><workbook {ns}><sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets>'
                   f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'{escape(sheet[:31])}\'!$A$1:${last[:len(col_letter(len(columns) - 1))]}${len(rows) + 1}</definedName></definedNames></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml', '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>')
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
    return buf.getvalue(), sum(numeric.values())


@op('csv-xlsx', title='CSV’yi biçimli Excel (XLSX) dosyasına çevir', domain=D, product=P, tag='XLSX', samples={'satislar.csv': 'satislar.csv'},
    args=[arg('--sheet-name', default='Veri', help='Yeni XLSX sayfa adı')],
    description='CSV’yi Excel kurulumu gerektirmeden XLSX’e çevirir: başlık kalın ve dondurulmuş, filtre açık, sayısal sütunlar (Türkçe ondalık dahil) gerçek sayı olarak, baştaki sıfırlı kodlar metin olarak yazılır. “Excel’de sayılar metin görünüyor” sorununu çözer.',
    input='CSV', output='veri.xlsx', command='python main.py --input rapor.csv --sheet-name Rapor',
    expect={'satir': 41, 'sayisal_sutun': 3})
def csv_xlsx(columns, rows, args):
    data, numeric = build_xlsx(columns, rows, args.sheet_name)
    return Result(f'{len(rows)} satır XLSX’e yazıldı ({numeric} sayısal sütun)', None, None, {'satir': len(rows), 'sayisal_sutun': numeric}, files={'veri.xlsx': data})


LOG = re.compile(r'^(\S+) \S+ (\S+) \[([^\]]+)\] "(\S+) (\S+) ?([^"]*)" (\d{3}) (\d+|-)(?: "([^"]*)" "([^"]*)")?')
SUSPICIOUS = re.compile(r'wp-login|wp-admin|\.env|\.git/|phpmyadmin|/etc/passwd|\.\./|select%20|union%20|<script', re.I)


@op('erisim-logu', title='Web sunucusu erişim logunu çözümle', domain=D, product=P, tag='log', kind='file', samples={'erisim.log': 'erisim.log'},
    description='Apache/Nginx “combined” erişim loglarını tabloya çevirir; durum kodu dağılımını, en çok istenen yolları, 4xx/5xx oranını, saatlik trafiği ve wp-login, .env, ../ gibi şüpheli istekleri özetler. Hatalı satırları ayrıca sayar.',
    input='access.log (combined/common format)', output='sonuc.csv (istekler) + rapor', command='python main.py --input access.log',
    expect={'istek': 60, 'hatali_satir': 1})
def erisim(args):
    rows, bad = [], 0
    for line in read_text(args.input).splitlines():
        m = LOG.match(line)
        if not m:
            bad += line.strip() != ''
            continue
        ip, user, ts, method, path, proto, status, size, ref, agent = m.groups()
        try:
            when = datetime.strptime(ts.split()[0], '%d/%b/%Y:%H:%M:%S')
        except ValueError:
            when = None
        rows.append({'ip': ip, 'zaman': when.isoformat(sep=' ') if when else ts, 'yontem': method, 'yol': path, 'durum': status, 'boyut': '' if size == '-' else size,
                     'kaynak': ref or '', 'tarayici': agent or '', 'supheli': 'evet' if SUSPICIOUS.search(path) else ''})
    status = Counter(r['durum'] for r in rows)
    err = sum(n for s, n in status.items() if s[0] in '45')
    hours = Counter(r['zaman'][11:13] for r in rows if len(r['zaman']) > 13)
    sus = [r for r in rows if r['supheli']]
    return Result(f'{len(rows)} istek; hata oranı %{100 * err / max(1, len(rows)):.1f}; {len(sus)} şüpheli istek', list(rows[0]) if rows else [], rows,
                  {'istek': len(rows), 'hatali_satir': bad, 'hata_orani_%': round(100 * err / max(1, len(rows)), 2), 'supheli': len(sus)},
                  tables=[('Durum kodları', ['Kod', 'Adet'], sorted(status.items())), ('En çok istenen yollar', ['Yol', 'Adet'], Counter(r['yol'] for r in rows).most_common(10)),
                          ('Saatlik trafik', ['Saat', 'İstek'], sorted(hours.items())), ('Şüpheli istekler', ['IP', 'Yol', 'Durum'], [[r['ip'], r['yol'], r['durum']] for r in sus][:50])])


@op('csv-html-rapor', title='CSV’den etkileşimli HTML tablo raporu üret', domain=D, product=P, tag='HTML', samples={'satislar.csv': 'satislar.csv'},
    args=[arg('--title', default='Veri raporu')],
    description='Tabloyu tek dosyalık, internetsiz açılan bir HTML sayfasına çevirir: sütuna tıklayınca sıralama, metin arama, sayısal sütunlarda toplam ve satır sayısı. E-postayla paylaşılabilir; dış kaynak (CDN, font, izleme) kullanmaz.',
    input='CSV/XLSX', output='rapor.html', command='python main.py --input liste.xlsx --title "Eylül satışları"',
    expect={'satir': 41})
def csv_html(columns, rows, args):
    styles = {c: tabular.number_style([r[c] for r in rows]) for c in columns}
    numeric = [c for c in columns if any(r[c].strip() for r in rows) and all(tabular.parse_number(r[c], styles[c]) is not None for r in rows if r[c].strip())]
    head = ''.join(f'<th data-n="{int(c in numeric)}">{html.escape(c)}</th>' for c in columns)
    body = ''.join('<tr>' + ''.join(f'<td{" class=n" if c in numeric else ""}>{html.escape(r[c])}</td>' for c in columns) + '</tr>' for r in rows)
    totals = ''.join(f'<td class=n>{fmt(round(sum(tabular.parse_number(r[c], styles[c]) or 0 for r in rows), 2))}</td>' if c in numeric else '<td></td>' for c in columns)
    page = f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(args.title)}</title><style>
body{{font:14px system-ui,sans-serif;margin:16px;color:#1d2433;background:#fff}}h1{{font-size:20px}}input{{padding:6px 10px;width:min(360px,100%);margin:8px 0}}
.wrap{{overflow:auto}}table{{border-collapse:collapse;min-width:100%}}th,td{{border-bottom:1px solid #e3e6ee;padding:6px 10px;text-align:left;white-space:nowrap}}
th{{background:#f4f6fa;cursor:pointer;position:sticky;top:0}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}tfoot td{{font-weight:600;background:#f9fafc}}
@media (prefers-color-scheme:dark){{body{{background:#0f1420;color:#e6e9f0}}th{{background:#1a2131}}th,td{{border-color:#273046}}tfoot td{{background:#151b28}}}}
</style></head><body><h1>{html.escape(args.title)}</h1><p><span id="say">{len(rows)}</span> / {len(rows)} satır · {html.escape(Path(args.input).name)}</p>
<input id="ara" placeholder="Tabloda ara…" aria-label="Tabloda ara"><div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody><tfoot><tr>{totals}</tr></tfoot></table></div>
<script>
const tb=document.querySelector('tbody'),rows=[...tb.rows];
const num=s=>{{s=s.replace(/[^0-9,.-]/g,'');if(/,\\d{{1,2}}$/.test(s)||/\\.\\d{{3}},/.test(s))s=s.replace(/\\./g,'').replace(',','.');else s=s.replace(/,/g,'');return parseFloat(s)}};
document.getElementById('ara').oninput=e=>{{const q=e.target.value.toLocaleLowerCase('tr');let n=0;rows.forEach(r=>{{const h=r.textContent.toLocaleLowerCase('tr').includes(q);r.hidden=!h;n+=h}});document.getElementById('say').textContent=n}};
document.querySelectorAll('th').forEach((th,i)=>{{let asc=true;th.onclick=()=>{{const n=th.dataset.n==='1';rows.sort((a,b)=>{{const x=a.cells[i].textContent,y=b.cells[i].textContent;const r=n?(num(x)||0)-(num(y)||0):x.localeCompare(y,'tr');return asc?r:-r}});asc=!asc;rows.forEach(r=>tb.appendChild(r))}}}});
</script></body></html>'''
    return Result(f'{len(rows)} satırlık HTML rapor yazıldı ({len(numeric)} sayısal sütunda toplam)', None, None, {'satir': len(rows), 'sayisal_sutun': len(numeric)}, files={'rapor.html': page})
