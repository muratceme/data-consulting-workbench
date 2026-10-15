"""Shared, dependency-free readers for CSV and XLSX exports. Inputs are never modified."""
import csv, io, math, re, zipfile
from datetime import datetime
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}
MAX_BYTES = 200 * 1024 * 1024


class InputError(ValueError):
    """Readable problem with an input file; shown to the user as is."""


def _decode(raw):
    for encoding in ('utf-8-sig', 'cp1254'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InputError('Dosya UTF-8 veya Windows-1254 olarak okunamadı.')


def _rows_to_records(table, source):
    table = [r for r in table if any(str(c).strip() for c in r)]
    if not table:
        return [], []
    header = [str(h).strip() for h in table[0]]
    while header and not header[-1]:
        header.pop()
    if not header or any(not h for h in header):
        raise InputError(f'{source}: başlık satırında boş sütun adı var.')
    if len(header) != len(set(header)):
        dupes = sorted({h for h in header if header.count(h) > 1})
        raise InputError(f'{source}: mükerrer sütun adı: {", ".join(dupes)}')
    records = []
    for number, row in enumerate(table[1:], start=2):
        extra = [c for c in row[len(header):] if str(c).strip()]
        if extra:
            raise InputError(f'{source}: {number}. satırda başlıktan fazla değer var.')
        values = list(row[:len(header)]) + [''] * (len(header) - len(row))
        records.append({h: str(v) for h, v in zip(header, values)})
    return header, records


def read_csv(path):
    raw = Path(path).read_bytes()
    text = _decode(raw)
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
    except csv.Error:
        delimiter = ','
    return _rows_to_records(list(csv.reader(io.StringIO(text, newline=''), delimiter=delimiter)), Path(path).name)


def _column_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def read_xlsx(path, sheet=None):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise InputError(f'{Path(path).name}: geçerli bir XLSX dosyası değil.') from None
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_BYTES:
            raise InputError('XLSX açıldığında 200 MB sınırını aşıyor.')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
        book = ET.fromstring(z.read('xl/workbook.xml'))
        sheets = book.findall('m:sheets/m:sheet', NS)
        if not sheets:
            raise InputError('XLSX içinde sayfa yok.')
        chosen = sheets[0] if sheet is None else next((s for s in sheets if s.get('name') == sheet), None)
        if chosen is None:
            raise InputError(f'"{sheet}" adlı sayfa yok. Sayfalar: ' + ', '.join(s.get('name') for s in sheets))
        rel_id = chosen.get('{%s}id' % NS['r'])
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels.findall('p:Relationship', NS) if r.get('Id') == rel_id)
        target = target.lstrip('/')
        target = target if target.startswith('xl/') else 'xl/' + target
        data = ET.fromstring(z.read(target))
    table = []
    for row in data.iter('{%s}row' % NS['m']):
        values = {}
        for c in row.findall('m:c', NS):
            kind, ref = c.get('t'), c.get('r')
            if kind == 'inlineStr':
                value = ''.join(t.text or '' for t in c.iter('{%s}t' % NS['m']))
            else:
                v = c.find('m:v', NS)
                value = '' if v is None or v.text is None else v.text
                if kind == 's' and value:
                    value = shared[int(value)]
                elif kind == 'b':
                    value = 'TRUE' if value == '1' else 'FALSE'
            values[_column_index(ref) if ref else len(values)] = value
        if values:
            width = max(values) + 1
            table.append([values.get(i, '') for i in range(width)])
    return _rows_to_records(table, Path(path).name)


def read(path, sheet=None):
    """Return (columns, rows) where rows are dicts of strings, for .csv/.txt/.tsv or .xlsx."""
    path = Path(path)
    if not path.is_file():
        raise InputError(f'Girdi dosyası bulunamadı: {path}')
    if path.stat().st_size > MAX_BYTES:
        raise InputError('Dosya 200 MB sınırını aşıyor; daha küçük bir dışa aktarım kullanın.')
    if path.suffix.lower() in ('.xlsx', '.xlsm'):
        return read_xlsx(path, sheet)
    if path.suffix.lower() == '.xls':
        raise InputError('Eski .xls biçimi desteklenmez; dosyayı Excel’de .xlsx veya .csv olarak kaydedin.')
    return read_csv(path)


def write_csv(path, columns, rows):
    """Excel-friendly UTF-8 CSV with BOM. Cells starting with = + - @ are quoted as text."""
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(columns)
        for row in rows:
            w.writerow([_safe(row.get(c, '')) for c in columns])


def _safe(value):
    text = '' if value is None else str(value)
    # Prevent spreadsheet formula injection when a report is opened in Excel.
    return "'" + text if text[:1] in ('=', '+', '-', '@') and not re.fullmatch(r'-?\d+([.,]\d+)?', text) else text


# ---------- Value parsing shared by profile and quality checks ----------
DATE_FORMATS = ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y %H:%M:%S', '%Y%m%d')
NUMBER = re.compile(r'^[+-]?(\d+|\d{1,3}([.,]\d{3})+)([.,]\d+)?$')


def number_style(values):
    """'tr' when the column writes decimals with a comma (1.234,56), 'en' for 1,234.56, None when it cannot tell."""
    tr = en = 0
    for v in values[:5000]:
        v = v.strip()
        if re.search(r'\d,\d{1,2}$', v) or re.search(r'\.\d{3},', v):
            tr += 1
        elif re.search(r'\d\.\d{1,2}$', v) or re.search(r',\d{3}\.', v):
            en += 1
    return 'tr' if tr > en else 'en' if en > tr else None


def parse_number(value, style=None):
    """Accept 1234.5, 1234,5, 1.234,56 and 1,234.56; currency/percent signs are ignored.
    style ('tr'/'en', from number_style) settles ambiguous values such as 2.500 for a whole column."""
    s = value.strip().replace(' ', '').replace(' ', '')
    currency = any(token in s for token in ('₺', 'TL', 'TRY'))
    for token in ('₺', 'TL', 'TRY', '$', '€', '%'):
        s = s.replace(token, '')
    if not s or not NUMBER.match(s):
        return None
    if style == 'tr' or (style is None and currency and re.fullmatch(r'[+-]?\d{1,3}(\.\d{3})+(,\d+)?', s)):
        s = s.replace('.', '').replace(',', '.')
    elif style == 'en':
        s = s.replace(',', '')
    elif ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        # One comma is a Turkish decimal separator; several are thousands separators.
        s = s.replace(',', '.') if s.count(',') == 1 else s.replace(',', '')
    elif s.count('.') > 1:
        s = s.replace('.', '')
    try:
        n = float(s)
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def parse_date(value):
    s = value.strip()
    if len(s) < 6 or not s[0].isdigit():
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt), fmt
        except ValueError:
            continue
    return None
