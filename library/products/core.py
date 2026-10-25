"""Shared layer of the product packages: platform readers, deterministic engines and evidence-bound agents.

Every product package reads from its platform through the vendor's documented API, SQL interface or official
driver, then runs one of the deterministic engines below. Agents send only metadata/evidence (never raw rows)
to the selected model; the answer is parsed as JSON and every table/column/object it names is checked against
the evidence before anything is written. Nothing a model writes is executed on a platform.
"""
import csv
import datetime as dt
import difflib
import hashlib
import hmac
import importlib
import io
import json
import math
import os
import re
import statistics
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlencode

import native
from native import NativeError, required, secret, ident, journal, receipt, write_artifact, csv_artifact

MAX_ROWS = 100000
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_$#@-]{0,127}')


# ---------- Config helpers ----------
def opt_int(cfg, key, default, low, high):
    value = cfg.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise NativeError(f'{key} {low}–{high} arası tamsayı olmalı.')
    return value


def opt_float(cfg, key, default, low, high):
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise NativeError(f'{key} {low}–{high} arası sayı olmalı.')
    return float(value)


def names(cfg, key, label=None, *, limit=200, pattern=None, optional=False):
    """A list of identifiers from config; each is validated before it can reach a query or URL."""
    value = cfg.get(key)
    if optional and not value:
        return []
    if isinstance(value, str):
        value = [x.strip() for x in value.split(',') if x.strip()]
    if not isinstance(value, list) or not 1 <= len(value) <= limit or not all(isinstance(v, str) and v.strip() and 'YOUR_' not in v for v in value):
        raise NativeError(f'{label or key} 1–{limit} adlık liste olmalı.')
    rule = pattern or NAME
    for v in value:
        if not all(rule.fullmatch(part) for part in v.split('.')):
            raise NativeError(f'{label or key} geçersiz ad içeriyor: {v[:60]}')
    return value


def name(cfg, key, pattern=None):
    value = required(cfg, key)
    if not all((pattern or NAME).fullmatch(part) for part in value.split('.')):
        raise NativeError(f'{key} geçersiz ad: {value[:60]}')
    return value


def literal(value):
    """SQL string literal for validated names only (never for free text)."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_in(values):
    return ', '.join(literal(v) for v in values)


def flags(cfg):
    return cfg.get('_flags') or {}


# ---------- Local input files ----------
def read_table(path, *, limit_mb=64):
    """CSV (comma, semicolon, tab; UTF-8/BOM) or JSON list of objects → (columns, rows)."""
    file = Path(path)
    raw = file.read_bytes()
    if len(raw) > limit_mb * 1024 * 1024:
        raise NativeError(f'{file.name} en fazla {limit_mb} MB olabilir.')
    if file.suffix.lower() in ('.json', '.jsonl'):
        try:
            text = raw.decode('utf-8-sig')
            rows = [json.loads(line) for line in text.splitlines() if line.strip()] if file.suffix.lower() == '.jsonl' else json.loads(text)
        except ValueError:
            raise NativeError(f'{file.name} geçerli JSON değil.') from None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise NativeError(f'{file.name} nesne listesi olmalı.')
        cols = list(dict.fromkeys(k for r in rows for k in r))
        return cols, rows
    text = raw.decode('utf-8-sig')
    first = text.split('\n', 1)[0]
    delimiter = max(',;\t|', key=first.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        if None in row:
            raise NativeError(f'{file.name}: başlıktan fazla alan içeren satır var ({reader.line_num}).')
        rows.append(row)
        if len(rows) > MAX_ROWS * 5:
            raise NativeError(f'{file.name} satır sınırını aşıyor.')
    return list(reader.fieldnames or []), rows


def context_text(cfg):
    path = Path(cfg.get('context_file', 'context.md'))
    return path.read_text(encoding='utf-8')[:20000] if path.exists() else ''


def write_csv(out, filename, rows, columns=None):
    columns = columns or list(dict.fromkeys(k for r in rows for k in r)) or ['bilgi']
    return csv_artifact(out, filename, columns, rows)


def write_text(out, filename, text):
    return write_artifact(out, filename, text.rstrip().encode('utf-8') + b'\n')


def md_table(headers, rows):
    if not rows:
        return '_Kayıt yok._\n'
    cell = lambda v: str('' if v is None else v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)] +
                     ['| ' + ' | '.join(cell(r.get(h, '')) for h in headers) + ' |' for r in rows]) + '\n'


# ---------- Values ----------
def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value or '').strip().replace(' ', '')
    if not text:
        return None
    # 1.234,56 / 1.234.567 / 12,5 are Turkish formats; a single dot group (10.004) stays a decimal point.
    if re.fullmatch(r'-?\d{1,3}(\.\d{3})+,\d+', text) or re.fullmatch(r'-?\d{1,3}(\.\d{3}){2,}', text) or re.fullmatch(r'-?\d+,\d+', text):
        text = text.replace('.', '').replace(',', '.')
    try:
        result = float(text.replace(',', ''))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def parse_time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
    text = str(value or '').strip()
    if not text:
        return None
    try:
        result = dt.datetime.fromisoformat(text.replace('Z', '+00:00').replace(' ', 'T', 1) if 'T' not in text and ' ' in text else text.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y', '%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y'):
            try:
                result = dt.datetime.strptime(text, fmt)
                break
            except ValueError:
                result = None
        if result is None:
            return None
    return result if result.tzinfo else result.replace(tzinfo=dt.timezone.utc)


def plain(value):
    """JSON/CSV-safe scalar from driver values (Decimal, datetime, bytes, …)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return '0x' + bytes(value)[:32].hex()
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)[:4000]
    return str(value)


def fold(value):
    """Case/diacritic/space-insensitive comparison key (Turkish aware)."""
    text = str(value or '').translate(str.maketrans('ıİşŞğĞçÇöÖüÜ', 'iIsSgGcCoOuU'))
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def kind_of(values):
    kinds = Counter()
    for v in values[:5000]:
        if isinstance(v, bool) or str(v).lower() in ('true', 'false'):
            kinds['mantıksal'] += 1
        elif number(v) is not None:
            kinds['tamsayı' if re.fullmatch(r'-?\d+', str(v).strip()) else 'ondalık'] += 1
        elif parse_time(v) is not None and re.search(r'\d{4}|\d{2}\.\d{2}\.', str(v)):
            kinds['tarih'] += 1
        else:
            kinds['metin'] += 1
    if not kinds:
        return 'boş'
    if set(kinds) <= {'tamsayı', 'ondalık'}:
        return 'ondalık' if kinds['ondalık'] else 'tamsayı'
    return kinds.most_common(1)[0][0]


def pii(value):
    try:
        from common import pii_kind
    except ImportError:  # common.py ships with every package; tests import from the source tree
        return None
    return pii_kind(value)


# ---------- Deterministic engines ----------
def profile(columns, rows, *, values=False):
    """Column statistics; with values=False no raw value leaves this function (agents send this)."""
    result = []
    for col in columns:
        data = [r.get(col) for r in rows]
        filled = [v for v in data if v not in (None, '')]
        nums = [n for n in (number(v) for v in filled) if n is not None]
        kind = kind_of(filled)
        item = {'kolon': col, 'tip': kind, 'satir': len(data), 'dolu': len(filled), 'bos': len(data) - len(filled),
                'bos_orani': round((len(data) - len(filled)) / len(data), 4) if data else 0,
                'farkli': len({str(v) for v in filled}), 'kisisel_veri': next((k for k in (pii(v) for v in filled[:200]) if k), '')}
        if kind in ('tamsayı', 'ondalık') and nums:
            item.update(min=min(nums), max=max(nums), ortalama=round(statistics.fmean(nums), 6),
                        std=round(statistics.pstdev(nums), 6) if len(nums) > 1 else 0)
        elif kind == 'tarih':
            times = sorted(t for t in (parse_time(v) for v in filled) if t)
            if times:
                item.update(min=times[0].isoformat(), max=times[-1].isoformat())
        elif filled:
            lengths = [len(str(v)) for v in filled]
            item.update(min_uzunluk=min(lengths), max_uzunluk=max(lengths))
        if values and filled and not item['kisisel_veri']:
            item['en_sik'] = '; '.join(f'{k} ({n})' for k, n in Counter(str(v)[:40] for v in filled).most_common(3))
        result.append(item)
    return result


RULES = {'required', 'unique', 'min', 'max', 'allowed', 'regex', 'max_length', 'not_future'}


def check_rules(rules, columns):
    if not isinstance(rules, dict) or not rules:
        raise NativeError('Kalite kuralları {kolon: {kural: değer}} biçiminde olmalı.')
    for col, spec in rules.items():
        if col not in columns:
            raise NativeError('Kural, okunan veride olmayan kolonu gösteriyor: ' + str(col)[:60])
        if not isinstance(spec, dict) or not set(spec) <= RULES:
            raise NativeError('Desteklenen kurallar: ' + ', '.join(sorted(RULES)))
        if 'regex' in spec:
            try:
                re.compile(spec['regex'])
            except (re.error, TypeError):
                raise NativeError('Geçersiz regex: ' + col) from None
    return rules


def quality(rows, rules, *, key=None, limit=10000):
    """Row-level rule violations; values are truncated and personal data is never copied."""
    issues, seen = [], defaultdict(dict)
    now = dt.datetime.now(dt.timezone.utc)
    for index, row in enumerate(rows, 1):
        ref = row.get(key) if key else index
        for col, spec in rules.items():
            value = row.get(col)
            empty = value in (None, '')
            shown = '' if empty or pii(value) else str(value)[:40]
            def add(rule):
                if len(issues) < limit:
                    issues.append({'satir': index, 'anahtar': ref, 'kolon': col, 'kural': rule, 'deger': shown})
            if empty:
                if spec.get('required'):
                    add('zorunlu')
                continue
            if spec.get('unique'):
                if value in seen[col]:
                    add('benzersiz')
                seen[col][value] = index
            n = number(value)
            if 'min' in spec and (n is None or n < spec['min']):
                add('min ' + str(spec['min']))
            if 'max' in spec and (n is None or n > spec['max']):
                add('max ' + str(spec['max']))
            if 'allowed' in spec and str(value) not in {str(a) for a in spec['allowed']}:
                add('izinli değer')
            if 'regex' in spec and not re.fullmatch(spec['regex'], str(value)):
                add('biçim')
            if 'max_length' in spec and len(str(value)) > spec['max_length']:
                add('uzunluk')
            if spec.get('not_future'):
                t = parse_time(value)
                if t is None or t > now:
                    add('gelecek tarih')
    return issues


def schema_rows(items):
    """Normalise column metadata to {tablo, kolon, tip, bos_olabilir, sira}."""
    rows = []
    for r in items:
        table = str(r.get('tablo') or '').strip()
        column = str(r.get('kolon') or '').strip()
        if table and column:
            rows.append({'tablo': table, 'kolon': column, 'tip': str(r.get('tip') or '').upper().strip(),
                         'bos_olabilir': str(r.get('bos_olabilir', '')).upper().strip() in ('YES', 'Y', 'TRUE', '1', 'EVET'),
                         'sira': int(number(r.get('sira')) or 0)})
    rows.sort(key=lambda r: (r['tablo'].lower(), r['sira'], r['kolon'].lower()))
    return rows


def schema_diff(baseline, current):
    def index(items):
        out = defaultdict(dict)
        for r in items:
            out[r['tablo'].lower()][r['kolon'].lower()] = r
        return out
    old, new = index(baseline), index(current)
    diff = []
    for table in sorted(set(old) | set(new)):
        if table not in new:
            diff.append({'tablo': table, 'kolon': '', 'degisiklik': 'tablo kaldırıldı', 'onceki': f'{len(old[table])} kolon', 'yeni': ''})
            continue
        if table not in old:
            diff.append({'tablo': table, 'kolon': '', 'degisiklik': 'tablo eklendi', 'onceki': '', 'yeni': f'{len(new[table])} kolon'})
            continue
        for col in sorted(set(old[table]) | set(new[table])):
            a, b = old[table].get(col), new[table].get(col)
            if not b:
                diff.append({'tablo': table, 'kolon': col, 'degisiklik': 'kolon kaldırıldı', 'onceki': a['tip'], 'yeni': ''})
            elif not a:
                diff.append({'tablo': table, 'kolon': col, 'degisiklik': 'kolon eklendi', 'onceki': '', 'yeni': b['tip']})
            else:
                if a['tip'] != b['tip']:
                    diff.append({'tablo': table, 'kolon': col, 'degisiklik': 'tip değişti', 'onceki': a['tip'], 'yeni': b['tip']})
                if a['bos_olabilir'] != b['bos_olabilir']:
                    diff.append({'tablo': table, 'kolon': col, 'degisiklik': 'null kuralı değişti',
                                 'onceki': 'NULL' if a['bos_olabilir'] else 'NOT NULL', 'yeni': 'NULL' if b['bos_olabilir'] else 'NOT NULL'})
    return diff


BREAKING = {'tablo kaldırıldı', 'kolon kaldırıldı', 'tip değişti'}


def schema_task(cfg, out, columns, *, scope):
    """Snapshot the live schema; compare with a previous snapshot when one is given."""
    current = schema_rows(columns)
    if not current:
        raise NativeError('Platform seçilen kapsamda kolon döndürmedi; ad ve yetkiyi kontrol edin.')
    snap = receipt(out, 'sema.json', {'kapsam': scope, 'alindi': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'kolonlar': current})
    baseline_file = cfg.get('baseline_file')
    summary = {'kapsam': scope, 'tablo': len({r['tablo'] for r in current}), 'kolon': len(current), 'snapshot': snap}
    if baseline_file and Path(baseline_file).exists():
        try:
            base = json.loads(Path(baseline_file).read_text(encoding='utf-8'))
            base = base['kolonlar'] if isinstance(base, dict) else base
        except (ValueError, KeyError, TypeError):
            raise NativeError('baseline_file önceki çalışmanın sema.json dosyası olmalı.') from None
        diff = schema_diff(schema_rows(base), current)
        write_csv(out, 'sema_farki.csv', diff, ['tablo', 'kolon', 'degisiklik', 'onceki', 'yeni'])
        summary.update(degisiklik=len(diff), kirici=sum(1 for d in diff if d['degisiklik'] in BREAKING))
    else:
        summary['not'] = 'İlk çalışma: karşılaştırma için bu sema.json dosyasını sonraki çalışmada baseline_file olarak verin.'
    journal(out, state='verified')
    return receipt(out, 'ozet.json', summary)


def reconcile(source, target, keys, fields=None, tolerance=0.0):
    def keyed(rows, label):
        out = {}
        for r in rows:
            k = tuple(str(r.get(x, '')).strip() for x in keys)
            if not any(k):
                continue
            if k in out:
                raise NativeError(f'{label} anahtarı benzersiz değil: ' + '|'.join(k)[:60])
            out[k] = r
        return out
    a, b = keyed(source, 'Kaynak'), keyed(target, 'Hedef')
    fields = fields or sorted(({k for r in source[:1] for k in r} & {k for r in target[:1] for k in r}) - set(keys))
    diff, changed = [], 0
    for k in sorted(set(a) | set(b)):
        ref = '|'.join(k)
        if k not in b:
            diff.append({'anahtar': ref, 'durum': 'yalnız kaynakta', 'alan': '', 'kaynak': '', 'hedef': '', 'fark': ''})
            continue
        if k not in a:
            diff.append({'anahtar': ref, 'durum': 'yalnız hedefte', 'alan': '', 'kaynak': '', 'hedef': '', 'fark': ''})
            continue
        row_changed = False
        for f in fields:
            x, y = a[k].get(f), b[k].get(f)
            nx, ny = number(x), number(y)
            if nx is not None and ny is not None:
                if abs(nx - ny) > tolerance:
                    diff.append({'anahtar': ref, 'durum': 'farklı', 'alan': f, 'kaynak': x, 'hedef': y, 'fark': round(ny - nx, 6)})
                    row_changed = True
            elif fold(x) != fold(y):
                diff.append({'anahtar': ref, 'durum': 'farklı', 'alan': f, 'kaynak': str(x)[:80], 'hedef': str(y)[:80], 'fark': ''})
                row_changed = True
        changed += row_changed
    totals = []
    for f in fields:
        sa = [number(r.get(f)) for r in a.values()]
        sb = [number(r.get(f)) for r in b.values()]
        if sa and all(v is not None for v in sa) and sb and all(v is not None for v in sb):
            totals.append({'alan': f, 'kaynak_toplam': round(sum(sa), 6), 'hedef_toplam': round(sum(sb), 6), 'fark': round(sum(sb) - sum(sa), 6)})
    summary = {'kaynak_kayit': len(a), 'hedef_kayit': len(b), 'eslesen': len(set(a) & set(b)), 'yalniz_kaynakta': len(set(a) - set(b)),
               'yalniz_hedefte': len(set(b) - set(a)), 'farkli_kayit': changed, 'karsilastirilan_alanlar': fields, 'tolerans': tolerance}
    return summary, diff, totals


def reconcile_task(cfg, out, source, target, *, source_label, target_label):
    keys = names(cfg, 'keys', 'Anahtar kolonları', limit=5, pattern=re.compile(r'[^\x00-\x1f]{1,128}'))
    fields = names(cfg, 'compare_fields', pattern=re.compile(r'[^\x00-\x1f]{1,128}'), optional=True)
    tolerance = opt_float(cfg, 'tolerance', 0.0, 0, 1e12)
    for label, rows in ((source_label, source), (target_label, target)):
        if rows and any(k not in rows[0] for k in keys):
            raise NativeError(f'{label} anahtar kolonlarını içermiyor: ' + ', '.join(keys))
    summary, diff, totals = reconcile(source, target, keys, fields, tolerance)
    write_csv(out, 'mutabakat_farklari.csv', diff, ['anahtar', 'durum', 'alan', 'kaynak', 'hedef', 'fark'])
    write_csv(out, 'toplam_karsilastirma.csv', totals, ['alan', 'kaynak_toplam', 'hedef_toplam', 'fark'])
    journal(out, state='verified')
    return receipt(out, 'mutabakat.json', {'kaynak': source_label, 'hedef': target_label, **summary,
                                           'mutabik': not diff and not any(t['fark'] for t in totals)})


def count_reconcile(rows, *, source='okunan', target='yazilan', allowed='atlanan'):
    """Per-run/table counter check used by integration and migration platforms."""
    result = []
    for r in rows:
        a, b, c = number(r.get(source)), number(r.get(target)), number(r.get(allowed)) or 0
        if a is None or b is None:
            status = 'sayaç yok'
        elif a == b:
            status = 'eşit'
        elif a == b + c:
            status = 'fark atlanan satırlarla açıklanıyor'
        else:
            status = 'FARK'
        result.append({**r, 'fark': '' if a is None or b is None else b - a, 'durum': status})
    return result


def dedupe(rows, fields, *, threshold=0.92, key=None, block=3, max_pairs=20000):
    """Exact (normalised) duplicate groups and fuzzy candidate pairs within blocking groups."""
    exact, normal = defaultdict(list), {}
    for i, r in enumerate(rows):
        norm = ' | '.join(fold(r.get(f)) for f in fields)
        if not norm.replace('|', '').strip():
            continue
        normal[i] = norm
        exact[norm].append(i)
    groups = [ids for ids in exact.values() if len(ids) > 1]
    ref = (lambda i: rows[i].get(key)) if key else (lambda i: i + 1)
    group_rows = [{'grup': g, 'kayit': ref(i), 'esleme': 'birebir (normalize)', 'benzerlik': 1.0} for g, ids in enumerate(groups, 1) for i in ids]
    reps = {ids[0] for ids in exact.values()}
    blocks = defaultdict(list)
    for i in sorted(reps):
        blocks[normal[i].replace(' ', '')[:block]].append(i)
    pairs = []
    for ids in blocks.values():
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                if len(pairs) >= max_pairs:
                    break
                score = difflib.SequenceMatcher(None, normal[ids[x]], normal[ids[y]]).ratio()
                if score >= threshold:
                    pairs.append({'kayit_1': ref(ids[x]), 'kayit_2': ref(ids[y]), 'benzerlik': round(score, 4)})
    pairs.sort(key=lambda p: -p['benzerlik'])
    return group_rows, pairs, {'kayit': len(rows), 'birebir_grup': len(groups), 'birebir_fazla_kayit': sum(len(g) - 1 for g in groups),
                               'benzer_aday_cifti': len(pairs), 'esik': threshold, 'alanlar': fields}


def dedupe_task(cfg, out, rows, *, fields, key=None, label=''):
    if not rows:
        raise NativeError('Platform kayıt döndürmedi; filtre ve yetkiyi kontrol edin.')
    missing = [f for f in fields if f not in rows[0]]
    if missing:
        raise NativeError('Okunan kayıtlarda olmayan alan: ' + ', '.join(missing))
    threshold = opt_float(cfg, 'similarity_threshold', 0.92, 0.5, 1.0)
    groups, pairs, summary = dedupe(rows, fields, threshold=threshold, key=key)
    write_csv(out, 'birebir_mukerrer.csv', groups, ['grup', 'kayit', 'esleme', 'benzerlik'])
    write_csv(out, 'benzer_adaylar.csv', pairs, ['kayit_1', 'kayit_2', 'benzerlik'])
    journal(out, state='verified')
    return receipt(out, 'mukerrer.json', {'kaynak': label, **summary, 'not': 'Adaylar insan onayıyla birleştirilmelidir; platformda birleştirme yapılmadı.'})


MASKS = {'hash', 'redact', 'partial', 'null'}


def mask_rows(rows, fields, method='hash'):
    if method not in MASKS:
        raise NativeError('mask_method: ' + ', '.join(sorted(MASKS)))
    salt = os.environ.get('MASK_SALT', '') if method == 'hash' else ''
    if method == 'hash' and len(salt) < 16:
        raise NativeError('hash maskeleme için .env içinde en az 16 karakterlik MASK_SALT gerekir.')
    def mask(v):
        if v in (None, ''):
            return v
        if method == 'hash':
            return hmac.new(salt.encode(), str(v).encode('utf-8'), hashlib.sha256).hexdigest()[:20]
        if method == 'partial':
            text = str(v)
            return '*' * max(0, len(text) - 4) + text[-4:] if len(text) > 4 else '****'
        return '' if method == 'null' else '***'
    return [{k: (mask(v) if k in fields else v) for k, v in r.items()} for r in rows]


def mask_task(cfg, out, sensitive, *, source_label):
    """Mask the columns a platform classified as sensitive in a local extract (the source file is never changed)."""
    path = Path(cfg.get('input_file', 'veri.csv'))
    columns, rows = read_table(path)
    method = cfg.get('mask_method', 'hash')
    extra = names(cfg, 'extra_fields', pattern=re.compile(r'[^\x00-\x1f]{1,128}'), optional=True)
    by_fold = {fold(c): c for c in columns}
    found = []
    for s in sensitive:
        col = by_fold.get(fold(s['kolon']))
        if col and col not in [f['kolon'] for f in found]:
            found.append({**s, 'kolon': col})
    for e in extra:
        if e not in columns:
            raise NativeError('extra_fields dosyada yok: ' + e)
        if e not in [f['kolon'] for f in found]:
            found.append({'kolon': e, 'siniflandirma': 'kullanıcı seçimi', 'kaynak': 'config'})
    fields = [f['kolon'] for f in found]
    if not fields:
        raise NativeError('Platform sınıflandırması dosyadaki hiçbir kolonla eşleşmedi; extra_fields ile seçin.')
    masked = mask_rows(rows, set(fields), method)
    write_csv(out, 'maskelenmis.csv', masked, columns)
    write_csv(out, 'maskelenen_kolonlar.csv', [{**f, 'yontem': method} for f in found], ['kolon', 'siniflandirma', 'kaynak', 'yontem'])
    journal(out, state='verified')
    return receipt(out, 'maskeleme.json', {'kaynak': source_label, 'dosya': path.name, 'satir': len(rows), 'maskelenen_kolon': fields,
                                           'platform_siniflandirmasi': len(sensitive), 'yontem': method,
                                           'not': 'Kaynak dosya değiştirilmedi; platformda politika oluşturulmadı.'})


def dictionary_task(out, assets, *, source_label):
    """assets: [{tablo, kolon, tip, aciklama, sahip, etiketler, terim}] → data dictionary + gaps."""
    rows = [{k: plain(a.get(k, '')) or '' for k in ('tablo', 'kolon', 'tip', 'aciklama', 'sahip', 'etiketler', 'terim')} for a in assets]
    if not rows:
        raise NativeError('Katalog seçilen kapsamda varlık döndürmedi.')
    gaps = [{'tablo': r['tablo'], 'kolon': r['kolon'], 'eksik': ', '.join(x for x, ok in (('açıklama', r['aciklama']), ('sahip', r['sahip'] or r['kolon'])) if not ok)}
            for r in rows]
    gaps = [g for g in gaps if g['eksik']]
    write_csv(out, 'veri_sozlugu.csv', rows, ['tablo', 'kolon', 'tip', 'aciklama', 'sahip', 'etiketler', 'terim'])
    tables = sorted({r['tablo'] for r in rows})
    md = [f'# Veri sözlüğü — {source_label}', '', f'{len(tables)} tablo, {sum(1 for r in rows if r["kolon"])} kolon. Kaynak: platform kataloğu (okuma).', '']
    for t in tables:
        items = [r for r in rows if r['tablo'] == t]
        head = next((r for r in items if not r['kolon']), {})
        md += [f'## {t}', head.get('aciklama') or '_Açıklama yok._', '', md_table(['kolon', 'tip', 'aciklama', 'etiketler', 'terim'], [r for r in items if r['kolon']]), '']
    write_text(out, 'veri_sozlugu.md', '\n'.join(md))
    write_csv(out, 'eksikler.csv', gaps, ['tablo', 'kolon', 'eksik'])
    journal(out, state='verified')
    return receipt(out, 'sozluk.json', {'kaynak': source_label, 'tablo': len(tables), 'kayit': len(rows), 'aciklamasiz': sum(1 for r in rows if not r['aciklama']),
                                        'not': 'Katalogda hiçbir şey değiştirilmedi.'})


def summarize(rows, dims, measures):
    groups = defaultdict(lambda: defaultdict(float))
    counts = Counter()
    for r in rows:
        key = tuple(str(r.get(d, '')) for d in dims)
        counts[key] += 1
        for m in measures:
            n = number(r.get(m))
            if n is not None:
                groups[key][m] += n
    return [{**dict(zip(dims, k)), 'kayit': counts[k], **{m: round(groups[k][m], 6) for m in measures}} for k in sorted(counts)]


def durations(runs):
    """runs: [{pipeline, durum, baslangic, bitis|sure_sn}] → per pipeline metrics."""
    per = defaultdict(list)
    for r in runs:
        per[r['pipeline']].append(r)
    result = []
    for name_, items in sorted(per.items()):
        secs = []
        for r in items:
            s = number(r.get('sure_sn'))
            if s is None:
                a, b = parse_time(r.get('baslangic')), parse_time(r.get('bitis'))
                s = (b - a).total_seconds() if a and b else None
            if s is not None and s >= 0:
                secs.append(s)
        states = Counter(r['durum'] for r in items)
        ok = sum(n for s, n in states.items() if s == 'başarılı')
        failed = [r for r in items if r['durum'] == 'başarısız']
        last_fail = max((str(r.get('baslangic') or '') for r in failed), default='')
        secs.sort()
        q = lambda p: round(secs[min(len(secs) - 1, int(p * len(secs)))], 1) if secs else ''
        result.append({'pipeline': name_, 'calisma': len(items), 'basarili': ok, 'basarisiz': len(failed),
                       'diger': len(items) - ok - len(failed), 'basari_orani': round(ok / len(items), 4),
                       'p50_sn': q(0.5), 'p95_sn': q(0.95), 'max_sn': round(secs[-1], 1) if secs else '', 'son_hata': last_fail})
    return result


def metrics_task(out, runs, *, source_label, days):
    if not runs:
        raise NativeError('Seçilen dönemde çalışma bulunamadı.')
    metrics = durations(runs)
    write_csv(out, 'pipeline_metrikleri.csv', metrics, ['pipeline', 'calisma', 'basarili', 'basarisiz', 'diger', 'basari_orani', 'p50_sn', 'p95_sn', 'max_sn', 'son_hata'])
    write_csv(out, 'calismalar.csv', runs, ['pipeline', 'calisma_id', 'durum', 'ham_durum', 'baslangic', 'bitis', 'sure_sn'])
    journal(out, state='verified')
    total = len(runs)
    ok = sum(1 for r in runs if r['durum'] == 'başarılı')
    return receipt(out, 'ozet.json', {'kaynak': source_label, 'gun': days, 'pipeline': len(metrics), 'calisma': total,
                                      'basari_orani': round(ok / total, 4), 'en_cok_hata': sorted(metrics, key=lambda m: -m['basarisiz'])[0]['pipeline']})


SQL_STRIP = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.S)
SQL_REF = re.compile(r'\b(from|join|into|update|merge\s+into|table)\s+((?:[`"\[]?[\w$-]+[`"\]]?\.){0,3}[`"\[]?[\w$-]+[`"\]]?)', re.I)
SQL_CTE = re.compile(r'(?:\bwith|,)\s*(?:recursive\s+)?([\w$]+)\s+as\s*\(', re.I)
SQL_KEYWORDS = {'select', 'lateral', 'unnest', 'values', 'table', 'dual', 'the', 'each', 'set'}


def sql_refs(sql):
    text = SQL_STRIP.sub(' ', sql or '')
    ctes = {c.lower() for c in SQL_CTE.findall(text)}
    refs = []
    for kind, ref in SQL_REF.findall(text):
        clean = re.sub(r'[`"\[\]]', '', ref)
        if clean.lower() in ctes or clean.lower() in SQL_KEYWORDS or clean.startswith('$') or clean.isdigit():
            continue
        role = 'yazar' if kind.lower() in ('into', 'update', 'merge into', 'table') and not re.search(r'\bfrom\b', kind, re.I) else 'okur'
        refs.append((clean, role))
    return list(dict.fromkeys(refs))


def inventory_task(out, models, *, source_label):
    """models: [{model, tur, materyalizasyon, sql, bagimliliklar(list)}] → inventory + lineage edges."""
    if not models:
        raise NativeError('Platform model döndürmedi.')
    known = {m['model'].lower(): m['model'] for m in models}
    inv, edges = [], []
    for m in models:
        refs = sql_refs(m.get('sql', ''))
        reads = sorted({r for r, role in refs if role == 'okur'} | set(m.get('bagimliliklar') or []))
        for r in reads:
            edges.append({'kaynak': r, 'hedef': m['model'], 'tur': 'model' if r.lower() in known or r.split('.')[-1].lower() in known else 'dış tablo'})
        flags_ = [x for x, ok in (('SELECT *', not re.search(r'select\s+\*', SQL_STRIP.sub(' ', m.get('sql', '')), re.I)),
                                   ('SQL alınamadı', bool(m.get('sql') or m.get('bagimliliklar'))),
                                   ('açıklama yok', bool(m.get('aciklama', True)))) if not ok]
        inv.append({'model': m['model'], 'tur': m.get('tur', ''), 'materyalizasyon': m.get('materyalizasyon', ''), 'okudugu': len(reads),
                    'satir': len((m.get('sql') or '').splitlines()), 'uyarilar': ', '.join(flags_)})
    used = {e['kaynak'].split('.')[-1].lower() for e in edges}
    for row in inv:
        row['kullanan_model'] = sum(1 for e in edges if e['kaynak'].split('.')[-1].lower() == row['model'].split('.')[-1].lower())
        if row['kullanan_model'] == 0 and row['model'].split('.')[-1].lower() not in used:
            row['uyarilar'] = ', '.join(x for x in (row['uyarilar'], 'yaprak model') if x)
    write_csv(out, 'model_envanteri.csv', inv, ['model', 'tur', 'materyalizasyon', 'okudugu', 'kullanan_model', 'satir', 'uyarilar'])
    write_csv(out, 'soy_agaci.csv', edges, ['kaynak', 'hedef', 'tur'])
    journal(out, state='verified')
    return receipt(out, 'envanter.json', {'kaynak': source_label, 'model': len(inv), 'baglanti': len(edges),
                                          'dis_tablo': len({e['kaynak'] for e in edges if e['tur'] == 'dış tablo'})})


# ---------- Platform transports ----------
def bearer(factory, base, env, extra=None):
    return factory(base, {'Authorization': 'Bearer ' + secret(env), **(extra or {})})


def basic(factory, base, user_env, password_env, extra=None):
    return factory(base, {'Authorization': native.basic_auth(secret(user_env), secret(password_env)), **(extra or {})})


def pages(client, path, *, items='value', next_key='@odata.nextLink', limit=MAX_ROWS):
    """Follow absolute/relative next links (same origin enforced by HTTP.url)."""
    rows = []
    for _ in range(2000):
        r = client.json('GET', path)
        data = r
        for part in items.split('.'):
            data = data.get(part) if isinstance(data, dict) else None
        rows += [x for x in (data or []) if isinstance(x, dict)]
        if len(rows) > limit:
            raise NativeError('Satır sınırı aşıldı; filtreyi daraltın.')
        nxt = r
        for part in next_key.split('.'):
            nxt = nxt.get(part) if isinstance(nxt, dict) else None
        if not nxt:
            return rows
        path = nxt
    raise NativeError('Sayfalama sınırı aşıldı.')


def odata_rows(client, path, *, select=None, filter_=None, top=None, extra=None, v2=None):
    """OData v2/v4 entity set reader with server paging."""
    query = {}
    if select:
        query['$select'] = ','.join(select)
    if filter_:
        query['$filter'] = filter_
    if top:
        query['$top'] = str(top)
    query.update(extra or {})
    if v2 is None:
        v2 = '/sap/opu/odata/' in path and '/sap/opu/odata4/' not in path
    if v2:
        query['$format'] = 'json'
        query['$inlinecount'] = 'none'
    url = path + ('&' if '?' in path else '?') + urlencode(query, quote_via=quote, safe="$,'()/") if query else path
    rows = []
    for _ in range(2000):
        r = client.json('GET', url)
        if v2:
            d = r.get('d') or {}
            batch = d.get('results', []) if isinstance(d, dict) else []
            nxt = d.get('__next') if isinstance(d, dict) else None
        else:
            batch, nxt = r.get('value', []), r.get('@odata.nextLink') or r.get('odata.nextLink')
        rows += [{k: plain(v) for k, v in x.items() if not k.startswith('__') and not isinstance(v, dict)} for x in batch if isinstance(x, dict)]
        if len(rows) > MAX_ROWS:
            raise NativeError('Satır sınırı aşıldı; filtreyi daraltın.')
        if not nxt:
            return rows
        url = nxt
    raise NativeError('OData sayfalama sınırı aşıldı.')


EDMX = {'edmx': 'http://docs.oasis-open.org/odata/ns/edmx', 'edmx1': 'http://schemas.microsoft.com/ado/2007/06/edmx'}


def odata_metadata(raw, entities=None):
    """$metadata (EDMX v2/v4) → [{varlik, alan, tip, zorunlu, anahtar, uzunluk}] for the requested entity types/sets."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise NativeError('$metadata XML okunamadı.') from None
    local = lambda tag: tag.rsplit('}', 1)[-1]
    sets = {}
    for el in root.iter():
        if local(el.tag) == 'EntitySet':
            sets[el.get('Name')] = (el.get('EntityType') or '').rsplit('.', 1)[-1]
    wanted = None
    if entities:
        wanted = {sets.get(e, e) for e in entities}
    result = []
    for et in root.iter():
        if local(et.tag) != 'EntityType':
            continue
        type_name = et.get('Name')
        if wanted is not None and type_name not in wanted:
            continue
        keys = {p.get('Name') for k in et if local(k.tag) == 'Key' for p in k}
        label = next((s for s, t in sets.items() if t == type_name and (not entities or s in entities)), type_name)
        for p in et:
            if local(p.tag) == 'Property':
                result.append({'varlik': label, 'alan': p.get('Name'), 'tip': (p.get('Type') or '').replace('Edm.', ''),
                               'zorunlu': p.get('Nullable') == 'false', 'anahtar': p.get('Name') in keys, 'uzunluk': p.get('MaxLength') or ''})
    if not result:
        raise NativeError('$metadata içinde istenen varlık bulunamadı: ' + ', '.join(entities or []))
    return result


# ---------- AWS Signature Version 4 ----------
def aws_keys():
    key, secret_ = secret('AWS_ACCESS_KEY_ID'), secret('AWS_SECRET_ACCESS_KEY')
    return key, secret_, os.environ.get('AWS_SESSION_TOKEN', '')


def region_of(cfg):
    region = required(cfg, 'region')
    if not re.fullmatch(r'[a-z]{2}(-[a-z]+)+-\d', region):
        raise NativeError('region örn. eu-central-1 olmalı.')
    return region


def aws_call(factory, service, region, *, host=None, target=None, payload=None, method='POST', path='/', query='',
             content_type='application/x-amz-json-1.1', raw=None, accept='application/json', signing_service=None):
    """Signed AWS request; credentials never leave the signing step except as the SigV4 header."""
    key, secret_, token = aws_keys()
    host = host or f'{service}.{region}.amazonaws.com'
    body = raw if raw is not None else (b'' if payload is None else json.dumps(payload).encode('utf-8'))
    now = dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    headers = {'host': host, 'x-amz-date': amz_date}
    if body or method == 'POST':
        headers['content-type'] = content_type
    if target:
        headers['x-amz-target'] = target
    if token:
        headers['x-amz-security-token'] = token
    sign_service = signing_service or service
    payload_hash = hashlib.sha256(body).hexdigest()
    if sign_service == 's3':
        headers['x-amz-content-sha256'] = payload_hash
    canonical_query = '&'.join(sorted(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in
                                      ((unquote(x) for x in p.split('=', 1)) if '=' in p else (unquote(p), '') for p in query.split('&') if p)))
    signed = ';'.join(sorted(headers))
    canonical = '\n'.join([method, quote(path, safe='/-_.~'), canonical_query,
                           ''.join(f'{k}:{headers[k].strip()}\n' for k in sorted(headers)), signed, payload_hash])
    scope = f'{day}/{region}/{sign_service}/aws4_request'
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ('AWS4' + secret_).encode()
    for part in (day, region, sign_service, 'aws4_request'):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    send = {h: v for h, v in headers.items() if h not in ('host', 'content-type')}
    send['Authorization'] = f'AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, Signature={signature}'
    client = factory('https://' + host, send)
    data, _ = client.request(method, path + ('?' + query if query else ''), raw=body if body else None,
                             content_type=content_type if body else None, accept=accept)
    return data


def aws_json(factory, service, region, target, payload, **kw):
    return native.json_object(aws_call(factory, service, region, target=target, payload=payload, **kw))


# ---------- SQL sources ----------
class Sql:
    """Uniform read interface: query(sql, stage) -> (columns, rows as dicts)."""
    dialect = 'ansi'

    def query(self, sql, stage='query'):
        raise NotImplementedError


class SnowflakeSql(Sql):
    dialect = 'snowflake'

    def __init__(self, cfg, factory, out):
        self.client, self.timeout, self.warehouse = native.snowflake_client(cfg, factory)
        self.role, self.out = required(cfg, 'role'), out

    def query(self, sql, stage='query'):
        return native.snowflake_rows(native.snowflake_statement(self.client, self.out, sql, stage, self.timeout, self.warehouse, self.role))


class DatabricksSql(Sql):
    dialect = 'databricks'

    def __init__(self, cfg, factory, out):
        self.client = bearer(factory, required(cfg, 'workspace_url'), 'DATABRICKS_ACCESS_TOKEN')
        self.warehouse, self.out = required(cfg, 'warehouse_id'), out

    def query(self, sql, stage='query'):
        cols, rows = native.databricks_sql(self.client, self.out, self.warehouse, sql, stage)
        return [str(c).lower() for c in cols], [{str(k).lower(): v for k, v in r.items()} for r in rows]


class BigQuerySql(Sql):
    dialect = 'bigquery'

    def __init__(self, cfg, factory, out):
        self.client = bearer(factory, 'https://bigquery.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
        self.project, self.location, self.out = required(cfg, 'project_id'), required(cfg, 'location'), out
        self.budget = opt_int(cfg, 'maximum_bytes_billed', 10 * 1024 ** 3, 1, 10 ** 15)

    def query(self, sql, stage='query'):
        base = '/bigquery/v2/projects/' + ident(self.project) + '/queries'
        r = self.client.json('POST', base, {'query': sql, 'useLegacySql': False, 'location': self.location, 'timeoutMs': 30000,
                                            'maxResults': 1000, 'maximumBytesBilled': str(self.budget)})
        job = (r.get('jobReference') or {}).get('jobId')
        journal(self.out, **{stage + '_job_id': job})
        for _ in range(120):
            if r.get('jobComplete'):
                break
            time.sleep(2)
            if not job:
                raise NativeError('BigQuery iş kimliği dönmedi.')
            r = self.client.json('GET', base + '/' + ident(job) + '?location=' + ident(self.location) + '&maxResults=1000')
        else:
            raise NativeError('BigQuery sorgusu zaman aşımına uğradı; job_id run-state.json içinde.')
        if r.get('errors'):
            raise NativeError('BigQuery sorgusu hata döndürdü: ' + stage)
        cols = [f.get('name') for f in (r.get('schema') or {}).get('fields', [])]
        rows = []
        for _ in range(500):
            rows += [dict(zip(cols, [c.get('v') for c in row.get('f', [])])) for row in r.get('rows') or []]
            token = r.get('pageToken')
            if not token or len(rows) >= MAX_ROWS:
                return cols, rows
            r = self.client.json('GET', base + '/' + ident(job) + '?' + urlencode({'location': self.location, 'pageToken': token, 'maxResults': 1000}))
        raise NativeError('BigQuery sayfa sınırı aşıldı.')


def aws_records(record):
    for k in ('stringValue', 'longValue', 'doubleValue', 'booleanValue'):
        if k in record:
            return record[k]
    return None


class RedshiftSql(Sql):
    dialect = 'redshift'

    def __init__(self, cfg, factory, out):
        self.factory, self.region, self.out = factory, region_of(cfg), out
        self.target = {'Database': required(cfg, 'database')}
        if cfg.get('workgroup_name'):
            self.target['WorkgroupName'] = name(cfg, 'workgroup_name')
        else:
            self.target['ClusterIdentifier'] = name(cfg, 'cluster_identifier')
            if cfg.get('db_user'):
                self.target['DbUser'] = name(cfg, 'db_user')
        if cfg.get('secret_arn'):
            self.target['SecretArn'] = required(cfg, 'secret_arn')

    def call(self, action, payload):
        return aws_json(self.factory, 'redshift-data', self.region, 'RedshiftData.' + action, payload)

    def query(self, sql, stage='query'):
        sid = required(self.call('ExecuteStatement', {**self.target, 'Sql': sql, 'StatementName': 'datawork-' + stage[:40]}), 'Id')
        journal(self.out, **{stage + '_statement_id': sid})
        for _ in range(150):
            d = self.call('DescribeStatement', {'Id': sid})
            if d.get('Status') == 'FINISHED':
                break
            if d.get('Status') in ('FAILED', 'ABORTED'):
                raise NativeError('Redshift sorgusu tamamlanamadı (' + d['Status'] + '): ' + stage)
            time.sleep(2)
        else:
            raise NativeError('Redshift zaman aşımı; statement Id run-state.json içinde.')
        if not d.get('HasResultSet'):
            return [], []
        cols, rows, token = [], [], None
        for _ in range(1000):
            r = self.call('GetStatementResult', {'Id': sid, **({'NextToken': token} if token else {})})
            cols = cols or [c.get('name') for c in r.get('ColumnMetadata', [])]
            rows += [dict(zip(cols, [aws_records(v) for v in rec])) for rec in r.get('Records', [])]
            token = r.get('NextToken')
            if not token or len(rows) >= MAX_ROWS:
                return cols, rows
        raise NativeError('Redshift sonuç sayfası sınırı aşıldı.')


class AthenaSql(Sql):
    dialect = 'athena'

    def __init__(self, cfg, factory, out):
        self.factory, self.region, self.out = factory, region_of(cfg), out
        self.request = {'WorkGroup': cfg.get('workgroup', 'primary'), 'QueryExecutionContext': {'Catalog': cfg.get('catalog', 'AwsDataCatalog')}}
        if cfg.get('database'):
            self.request['QueryExecutionContext']['Database'] = name(cfg, 'database')
        if cfg.get('output_location'):
            location = required(cfg, 'output_location')
            if not location.startswith('s3://'):
                raise NativeError('output_location s3:// ile başlamalı.')
            self.request['ResultConfiguration'] = {'OutputLocation': location}

    def call(self, action, payload):
        return aws_json(self.factory, 'athena', self.region, 'AmazonAthena.' + action, payload)

    def query(self, sql, stage='query'):
        qid = required(self.call('StartQueryExecution', {'QueryString': sql, **self.request}), 'QueryExecutionId')
        journal(self.out, **{stage + '_query_execution_id': qid})
        for _ in range(150):
            state = ((self.call('GetQueryExecution', {'QueryExecutionId': qid}).get('QueryExecution') or {}).get('Status') or {}).get('State')
            if state == 'SUCCEEDED':
                break
            if state in ('FAILED', 'CANCELLED'):
                raise NativeError('Athena sorgusu tamamlanamadı (' + state + '): ' + stage)
            time.sleep(2)
        else:
            raise NativeError('Athena zaman aşımı; QueryExecutionId run-state.json içinde.')
        cols, rows, token, first = [], [], None, True
        for _ in range(1000):
            r = self.call('GetQueryResults', {'QueryExecutionId': qid, 'MaxResults': 1000, **({'NextToken': token} if token else {})})
            rs = r.get('ResultSet') or {}
            cols = cols or [c.get('Name') for c in (rs.get('ResultSetMetadata') or {}).get('ColumnInfo', [])]
            data = [[c.get('VarCharValue') for c in row.get('Data', [])] for row in rs.get('Rows', [])]
            if first and data and data[0] == cols:
                data = data[1:]  # SELECT results repeat the header as the first row
            first = False
            rows += [dict(zip(cols, d)) for d in data]
            token = r.get('NextToken')
            if not token or len(rows) >= MAX_ROWS:
                return cols, rows
        raise NativeError('Athena sonuç sayfası sınırı aşıldı.')


class ClickHouseSql(Sql):
    dialect = 'clickhouse'

    def __init__(self, cfg, factory, out):
        self.client = factory(required(cfg, 'server'), {'X-ClickHouse-User': secret('CLICKHOUSE_USER'), 'X-ClickHouse-Key': secret('CLICKHOUSE_PASSWORD')})
        self.out = out

    def query(self, sql, stage='query'):
        raw, _ = self.client.request('POST', '/?readonly=1&default_format=JSON&max_result_rows=' + str(MAX_ROWS) + '&result_overflow_mode=break',
                                     raw=sql.encode('utf-8'), content_type='text/plain; charset=utf-8')
        r = native.json_object(raw)
        cols = [m.get('name') for m in r.get('meta', [])]
        return cols, [x for x in r.get('data', []) if isinstance(x, dict)]


class DremioSql(Sql):
    dialect = 'dremio'

    def __init__(self, cfg, factory, out):
        self.client = bearer(factory, required(cfg, 'server'), 'DREMIO_TOKEN')
        project = cfg.get('project_id')
        self.prefix = '/v0/projects/' + ident(project) if project else '/api/v3'
        self.out = out

    def query(self, sql, stage='query'):
        job = required(self.client.json('POST', self.prefix + '/sql', {'sql': sql}), 'id')
        journal(self.out, **{stage + '_job_id': job})
        for _ in range(150):
            state = self.client.json('GET', self.prefix + '/job/' + ident(job)).get('jobState')
            if state == 'COMPLETED':
                break
            if state in ('FAILED', 'CANCELED', 'CANCELLED'):
                raise NativeError('Dremio işi tamamlanamadı (' + state + '): ' + stage)
            time.sleep(2)
        else:
            raise NativeError('Dremio zaman aşımı; job id run-state.json içinde.')
        cols, rows, offset = [], [], 0
        for _ in range(400):
            r = self.client.json('GET', self.prefix + '/job/' + ident(job) + '/results?offset=' + str(offset) + '&limit=500')
            cols = cols or [c.get('name') for c in r.get('schema', [])]
            batch = [x for x in r.get('rows', []) if isinstance(x, dict)]
            rows += batch
            offset += len(batch)
            if not batch or offset >= int(r.get('rowCount') or 0) or len(rows) >= MAX_ROWS:
                return cols, rows
        raise NativeError('Dremio sonuç sınırı aşıldı.')


class OrdsSql(Sql):
    """Oracle REST Data Services REST-enabled SQL (Autonomous Database)."""
    dialect = 'oracle'

    def __init__(self, cfg, factory, out):
        self.client = basic(factory, required(cfg, 'ords_url'), 'ORACLE_USER', 'ORACLE_PASSWORD')
        self.out = out

    def query(self, sql, stage='query'):
        cols, rows, offset = [], [], 0
        for _ in range(200):
            r = self.client.json('POST', '_/sql', {'statementText': sql, 'offset': offset, 'limit': 5000})
            item = next((i for i in r.get('items', []) if isinstance(i, dict)), {})
            if item.get('errorCode') or item.get('errorMessage'):
                raise NativeError('ORDS SQL hata döndürdü: ' + stage)
            result = item.get('resultSet') or {}
            cols = cols or [str(m.get('columnName', '')).lower() for m in result.get('metadata', [])]
            batch = [{str(k).lower(): v for k, v in x.items()} for x in result.get('items', []) if isinstance(x, dict)]
            rows += batch
            offset += len(batch)
            if not result.get('hasMore') or len(rows) >= MAX_ROWS:
                return cols, rows
        raise NativeError('ORDS sonuç sınırı aşıldı.')


DRIVERS = {'postgres': 'psycopg', 'mysql': 'pymysql', 'mssql': 'pyodbc', 'oracle': 'oracledb', 'teradata': 'teradatasql', 'duckdb': 'duckdb'}


def driver(module):
    """Official/standard Python driver, imported only on --execute (plan works without it)."""
    try:
        return importlib.import_module(module)
    except ImportError:
        raise NativeError(f'{module} paketi kurulu değil: pip install -r requirements.txt') from None


class DbapiSql(Sql):
    def __init__(self, kind, cfg, out):
        self.dialect, self.out = kind, out
        mod = driver(DRIVERS[kind])
        password = os.environ.get('DB_PASSWORD', '')
        if kind == 'postgres':
            self.conn = mod.connect(host=required(cfg, 'host'), port=opt_int(cfg, 'port', 5432, 1, 65535), dbname=required(cfg, 'database'),
                                    user=required(cfg, 'user'), password=password, sslmode=cfg.get('sslmode', 'require'), connect_timeout=20)
            self.conn.read_only = True
        elif kind == 'mysql':
            self.conn = mod.connect(host=required(cfg, 'host'), port=opt_int(cfg, 'port', 3306, 1, 65535), database=required(cfg, 'database'),
                                    user=required(cfg, 'user'), password=password, ssl={'ssl': {}} if cfg.get('ssl', True) else None, connect_timeout=20)
        elif kind == 'mssql':
            odbc = required(cfg, 'odbc')
            if re.search(r'(?i)\b(pwd|password)\s*=', odbc):
                raise NativeError('Parolayı odbc dizesine değil .env içindeki DB_PASSWORD alanına yazın.')
            self.conn = mod.connect(odbc + (';PWD=' + password if password else ''), timeout=20, readonly=True)
        elif kind == 'oracle':
            self.conn = mod.connect(user=required(cfg, 'user'), password=password, dsn=required(cfg, 'dsn'))
        elif kind == 'teradata':
            self.conn = mod.connect(host=required(cfg, 'host'), user=required(cfg, 'user'), password=password, logmech=cfg.get('logmech', 'TD2'))
        elif kind == 'duckdb':
            self.conn = mod.connect(cfg.get('database_file', ':memory:'), read_only=bool(cfg.get('database_file')))

    def query(self, sql, stage='query', params=None):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params) if params is not None else cur.execute(sql)
            if not cur.description:
                return [], []
            cols = [str(d[0]).lower() for d in cur.description]
            rows = []
            while len(rows) < MAX_ROWS:
                batch = cur.fetchmany(5000)
                if not batch:
                    break
                rows += [dict(zip(cols, (plain(v) for v in rec))) for rec in batch]
            return cols, rows
        except NativeError:
            raise
        except Exception as exc:  # driver errors can echo connection details; show only the type
            raise NativeError(f'Veritabanı sorgusu başarısız ({type(exc).__name__}): {stage}') from None
        finally:
            try:
                cur.close()
            except Exception:
                pass


SOURCES = {'snowflake': SnowflakeSql, 'databricks': DatabricksSql, 'bigquery': BigQuerySql, 'redshift': RedshiftSql, 'athena': AthenaSql,
           'clickhouse': ClickHouseSql, 'dremio': DremioSql, 'ords': OrdsSql}


def sql_source(kind, cfg, factory, out):
    if kind in SOURCES:
        return SOURCES[kind](cfg, factory, out)
    if kind in DRIVERS:
        return DbapiSql(kind, cfg, out)
    raise NativeError('Bilinmeyen SQL kaynağı: ' + kind)


def guard_select(sql):
    """User-supplied queries must be one read-only SELECT/WITH statement."""
    text = SQL_STRIP.sub(' ', sql or '').strip().rstrip(';').strip()
    if not re.match(r'(?is)^(select|with)\b', text) or ';' in text or native.READ_ONLY_SQL.search(text):
        raise NativeError('Sorgu tek bir salt-okunur SELECT/WITH ifadesi olmalı.')
    return text


def query_file(cfg, key, fallback):
    path = Path(cfg.get(key, fallback))
    return guard_select(path.read_text(encoding='utf-8'))


# ---------- Evidence-bound agent ----------
def evidence_names(evidence, *keys):
    """Collect allowed reference names from evidence lists (case-insensitive lookup)."""
    allowed = {}
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in keys and isinstance(v, (str, int)):
                    allowed[str(v).lower()] = str(v)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(evidence)
    return allowed


def check_refs(values, allowed, label):
    bad = [str(v) for v in values if str(v).lower() not in allowed]
    if bad:
        raise ValueError(f'{label} kanıtta yok (uydurma başvuru): ' + ', '.join(sorted(set(bad))[:10]))


def need_list(obj, key, label, *, minimum=1, maximum=200):
    value = obj.get(key)
    if not isinstance(value, list) or not minimum <= len(value) <= maximum or not all(isinstance(v, dict) for v in value):
        raise ValueError(f'"{key}" {minimum}–{maximum} nesneli liste olmalı ({label}).')
    return value


def need_text(item, key, label, *, choices=None):
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: "{key}" boş olamaz.')
    if choices and value not in choices:
        raise ValueError(f'{label}: "{key}" şunlardan biri olmalı: ' + ', '.join(sorted(choices)))
    return value.strip()


def agent_task(cfg, out, *, product, title, evidence, schema, validate, render, rules=''):
    """Collect → mask → preview/consent → model → JSON validation against evidence → deterministic files."""
    import llm
    receipt(out, 'kanit.json', evidence)
    system = (f'Sen {product} uzmanı bir veri danışmanısın. Görev: {title}. '
              'Kanıt, platformun API/SQL arayüzünden okunmuş metadata ve istatistiklerdir; ham satır içermez. '
              'Yalnızca kanıtta geçen tablo, kolon, nesne ve kimlikleri kullan; yeni bir şey öneriyorsan "oneri" alanında açıkça yeni olarak işaretle. '
              + rules + ' Yanıt JSON şeması: ' + schema + ' ' + llm.SAFETY)
    user = json.dumps({'gorev': title, 'urun': product, 'baglam': context_text(cfg), 'kanit': evidence}, ensure_ascii=False, default=str)
    if len(user) > 400000:
        raise NativeError('Kanıt modele gönderilemeyecek kadar büyük; kapsamı daraltın.')
    args = SimpleNamespace(dry_run=bool(flags(cfg).get('dry_run')), yes=bool(flags(cfg).get('yes')), provider=None, model=None)
    try:
        result, meta = llm.ask(args, out, system, user, validate)
    except llm.AgentError as exc:
        raise NativeError(str(exc)) from None
    if result is None:
        journal(out, state='dry_run')
        return receipt(out, 'kuru-calisma.json', {'model_cagrildi': False, 'istek': 'gonderilecek-istek.json', 'kanit': 'kanit.json'})
    files = render(result)
    written = [write_artifact(out, fname, data if isinstance(data, bytes) else data.encode('utf-8')) for fname, data in files.items()]
    journal(out, state='validated', model=meta.get('model'), attempts=meta.get('attempts'))
    return receipt(out, 'agent-sonucu.json', {'gorev': title, 'dosyalar': [w['file'] for w in written], 'model': meta.get('model'),
                                              'dogrulama': 'JSON şeması ve kanıttaki adlara karşı yerelde doğrulandı; platformda hiçbir şey çalıştırılmadı.',
                                              'insan_onayi': 'gerekli'})


def plan_json(result):
    return json.dumps(result, ensure_ascii=False, indent=2)


def rows_md(title, sections):
    """Render validated agent JSON sections into Markdown: sections = [(heading, rows, headers)]."""
    lines = ['# ' + title, '', '_Model önerisi; kanıta karşı doğrulandı. Uygulamadan önce insan incelemesi gerekir._', '']
    for heading, rows, headers in sections:
        lines += ['## ' + heading, '', md_table(headers, [{h: (', '.join(map(str, r.get(h))) if isinstance(r.get(h), list) else r.get(h, '')) for h in headers} for r in rows]), '']
    return '\n'.join(lines)
