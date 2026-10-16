"""SQL üretici ve denetim blokları. Hiçbiri veritabanına bağlanmaz veya SQL çalıştırmaz; yalnızca metin üretir/denetler."""
import re
from collections import Counter, defaultdict
from pathlib import Path
import tabular
from sema_farki import load as load_ddl, schema_from_ddl, base_type
from islem import op, arg, Result, need, col_list, numbers, fold

REQUIRES = ['sema_farki']
D, P = 'files', 'SQL dosyaları'
DIALECTS = ['tsql', 'postgres', 'mysql', 'oracle', 'bigquery', 'snowflake']
SATIS = {'satislar.csv': 'satislar.csv'}
SEMA = {'sema.sql': 'sema.sql'}
SQLS = {'sql/01_staging.sql': 'sql/01_staging.sql', 'sql/02_mart.sql': 'sql/02_mart.sql', 'sql/03_bakim.sql': 'sql/03_bakim.sql'}
SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def quote(name, dialect):
    if SAFE_NAME.match(name) and name.upper() not in RESERVED:
        return name
    return {'tsql': f'[{name}]', 'mysql': f'`{name}`', 'bigquery': f'`{name}`'}.get(dialect, f'"{name}"')


RESERVED = {'ORDER', 'GROUP', 'SELECT', 'FROM', 'WHERE', 'TABLE', 'USER', 'DATE', 'KEY', 'INDEX', 'VALUE', 'VALUES', 'COLUMN', 'DESC', 'ASC', 'CHECK', 'LIMIT'}
TYPES = {'int': {'tsql': 'INT', 'postgres': 'INTEGER', 'mysql': 'INT', 'oracle': 'NUMBER(10)', 'bigquery': 'INT64', 'snowflake': 'NUMBER(38,0)'},
         'bigint': {'tsql': 'BIGINT', 'postgres': 'BIGINT', 'mysql': 'BIGINT', 'oracle': 'NUMBER(19)', 'bigquery': 'INT64', 'snowflake': 'NUMBER(38,0)'},
         'decimal': {'tsql': 'DECIMAL({p},{s})', 'postgres': 'NUMERIC({p},{s})', 'mysql': 'DECIMAL({p},{s})', 'oracle': 'NUMBER({p},{s})', 'bigquery': 'NUMERIC', 'snowflake': 'NUMBER({p},{s})'},
         'date': {'tsql': 'DATE', 'postgres': 'DATE', 'mysql': 'DATE', 'oracle': 'DATE', 'bigquery': 'DATE', 'snowflake': 'DATE'},
         'datetime': {'tsql': 'DATETIME2', 'postgres': 'TIMESTAMP', 'mysql': 'DATETIME', 'oracle': 'TIMESTAMP', 'bigquery': 'DATETIME', 'snowflake': 'TIMESTAMP_NTZ'},
         'bool': {'tsql': 'BIT', 'postgres': 'BOOLEAN', 'mysql': 'BOOLEAN', 'oracle': 'NUMBER(1)', 'bigquery': 'BOOL', 'snowflake': 'BOOLEAN'},
         'text': {'tsql': 'NVARCHAR({n})', 'postgres': 'VARCHAR({n})', 'mysql': 'VARCHAR({n})', 'oracle': 'NVARCHAR2({n})', 'bigquery': 'STRING', 'snowflake': 'VARCHAR({n})'}}


def infer_column(values):
    """Return (kind, info) for a column of strings."""
    filled = [v.strip() for v in values if v.strip()]
    if not filled:
        return 'text', {'n': 50}
    low = {v.lower() for v in filled}
    if low <= {'true', 'false', '0', '1', 'evet', 'hayır', 'yes', 'no'} and len(low) <= 2 and not low <= {'0', '1'}:
        return 'bool', {}
    if not any(re.fullmatch(r'0\d+', v) for v in filled):
        style = tabular.number_style(filled)
        nums = [tabular.parse_number(v, style) for v in filled]
        if all(n is not None for n in nums):
            if all(float(n).is_integer() for n in nums) and not any(re.search(r'[.,]\d{1,2}$', v) for v in filled):
                return ('bigint' if max(abs(n) for n in nums) > 2_000_000_000 else 'int'), {}
            scale = max(min(len(re.split(r'[.,]', v)[-1]), 6) if re.search(r'[.,]\d+$', v) else 0 for v in filled) or 2
            digits = max(len(str(int(abs(n)))) for n in nums)
            return 'decimal', {'p': min(38, max(18, digits + scale)), 's': scale}
    dates = [tabular.parse_date(v) for v in filled]
    if all(dates):
        return ('datetime' if any(d[0].hour or d[0].minute or ':' in v for d, v in zip(dates, filled)) else 'date'), {}
    longest = max(len(v) for v in filled)
    size = next(s for s in (10, 20, 50, 100, 255, 1000, 4000) if longest <= s) if longest <= 4000 else 'MAX'
    return 'text', {'n': size}


def sql_type(kind, info, dialect):
    t = TYPES[kind][dialect].format(**{'p': 18, 's': 2, 'n': 255, **info})
    if info.get('n') == 'MAX':
        t = {'tsql': 'NVARCHAR(MAX)', 'postgres': 'TEXT', 'mysql': 'TEXT', 'oracle': 'CLOB', 'bigquery': 'STRING', 'snowflake': 'VARCHAR'}[dialect]
    return t


DIALECT_ARG = arg('--dialect', choices=DIALECTS, default='tsql')


@op('csv-ddl', title='CSV/Excel dosyasından CREATE TABLE üret', domain=D, product=P, tag='DDL', samples=SATIS,
    args=[arg('--table', default='stg.satislar'), DIALECT_ARG, arg('--key', help='Birincil anahtar sütun(lar)')],
    description='Dosyadaki her sütunun tipini değerlerden çıkarır (tam sayı, ondalık hassasiyeti, tarih, saat, mantıksal, metin uzunluğu); seçilen lehçede (SQL Server, PostgreSQL, MySQL, Oracle, BigQuery, Snowflake) CREATE TABLE yazar. Boş değer içermeyen sütunları NOT NULL yapar, anahtar tekilliğini kontrol eder.',
    input='CSV/XLSX + tablo adı + lehçe', output='create_table.sql + sutun-tipleri.csv', command='python main.py --table stg.satislar --dialect postgres --key siparis_no',
    expect={'sutun': 11, 'not_null': 9})
def csv_ddl(columns, rows, args):
    keys = col_list(args.key, columns, '--key', required=False)
    out, lines = [], []
    for c in columns:
        kind, info = infer_column([r[c] for r in rows])
        t = sql_type(kind, info, args.dialect)
        nn = all(r[c].strip() for r in rows) and bool(rows)
        out.append({'sutun': c, 'sql_adi': quote(snake(c), args.dialect), 'tip': t, 'not_null': 'evet' if nn else '', 'bos_deger': sum(not r[c].strip() for r in rows)})
        lines.append(f"    {quote(snake(c), args.dialect)} {t}{' NOT NULL' if nn or c in keys else ''}")
    notes = []
    if keys:
        dup = sum(n - 1 for n in Counter(tuple(r[k] for k in keys) for r in rows).values() if n > 1)
        if dup:
            notes.append(f'Anahtar ({", ".join(keys)}) dosyada {dup} kez tekrar ediyor; PRIMARY KEY yüklemede hata verir.')
        if args.dialect in ('bigquery',):
            notes.append('BigQuery birincil anahtarı zorlamaz (NOT ENFORCED).')
        lines.append(f"    {'PRIMARY KEY (' + ', '.join(quote(snake(k), args.dialect) for k in keys) + ')'}{' NOT ENFORCED' if args.dialect == 'bigquery' else ''}")
    sql = f'-- {Path(args.input).name} dosyasından üretildi ({len(rows)} satır incelendi). Tipleri gözden geçirin.\nCREATE TABLE {args.table} (\n' + ',\n'.join(lines) + '\n);'
    return Result(f'{len(columns)} sütunlu CREATE TABLE ({args.dialect})', None, None, {'sutun': len(columns), 'not_null': sum(o['not_null'] == 'evet' for o in out)},
                  tables=[('Sütun tipleri', ['Sütun', 'SQL adı', 'Tip', 'NOT NULL', 'Boş'], [list(o.values()) for o in out])],
                  files={'create_table.sql': sql, 'sutun-tipleri.csv': (list(out[0]), out)}, notes=notes)


def snake(name):
    name = re.sub(r'(?<=[a-zçğıöşü0-9])(?=[A-ZÇĞİÖŞÜ])', '_', name)  # MusteriNo → Musteri_No
    s = fold(name).translate(str.maketrans('çğıöşü', 'cgiosu'))
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s if s and not s[0].isdigit() else f'c_{s}'


def literal(value, kind, dialect, style):
    v = value.strip()
    if not v:
        return 'NULL'
    if kind in ('int', 'bigint', 'decimal'):
        n = tabular.parse_number(v, style)
        return 'NULL' if n is None else (str(int(n)) if kind != 'decimal' else repr(round(n, 10)))
    if kind in ('date', 'datetime'):
        d = tabular.parse_date(v)
        if d:
            text = d[0].strftime('%Y-%m-%d' if kind == 'date' else '%Y-%m-%d %H:%M:%S')
            return f"DATE '{text}'" if kind == 'date' and dialect in ('postgres', 'oracle', 'bigquery', 'snowflake') else f"'{text}'"
    if kind == 'bool':
        t = v.lower() in ('true', '1', 'evet', 'yes')
        return ('1' if t else '0') if dialect in ('tsql', 'oracle') else ('TRUE' if t else 'FALSE')
    prefix = 'N' if dialect == 'tsql' and any(ord(ch) > 127 for ch in v) else ''
    return prefix + "'" + v.replace("'", "''") + "'"


@op('csv-insert', title='CSV/Excel satırlarından INSERT ifadeleri üret', domain=D, product=P, tag='INSERT', samples=SATIS,
    args=[arg('--table', default='stg.satislar'), DIALECT_ARG, arg('--batch', type=int, default=500)],
    description='Dosya satırlarını seçilen lehçede toplu INSERT ifadelerine çevirir; sayıları sütunun ondalık biçimine göre, tarihleri ISO biçimine, boşları NULL’a dönüştürür, tırnakları kaçışlar. Küçük referans tablolarını bir betikle yüklemek için.',
    input='CSV/XLSX + tablo adı', output='insert.sql', command='python main.py --table ref.urunler --dialect postgres --batch 1000',
    expect={'satir': 41, 'ifade': 1})
def csv_insert(columns, rows, args):
    kinds = {c: infer_column([r[c] for r in rows]) for c in columns}
    styles = {c: tabular.number_style([r[c] for r in rows]) for c in columns}
    cols = ', '.join(quote(snake(c), args.dialect) for c in columns)
    parts = []
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        vals = ',\n'.join('(' + ', '.join(literal(r[c], kinds[c][0], args.dialect, styles[c]) for c in columns) + ')' for r in chunk)
        parts.append(f'INSERT INTO {args.table} ({cols}) VALUES\n{vals};')
    if args.dialect == 'oracle' and len(rows) > 1:
        parts = [f'INSERT INTO {args.table} ({cols}) ' + '\n'.join(('SELECT ' if j == 0 else 'UNION ALL SELECT ') + ', '.join(literal(r[c], kinds[c][0], args.dialect, styles[c]) for c in columns) + ' FROM dual'
                                                           for j, r in enumerate(rows[i:i + args.batch])) + ';' for i in range(0, len(rows), args.batch)]
    return Result(f'{len(rows)} satır {len(parts)} INSERT ifadesine çevrildi', None, None, {'satir': len(rows), 'ifade': len(parts)},
                  files={'insert.sql': f'-- {Path(args.input).name}: {len(rows)} satır\n' + '\n\n'.join(parts)})


@op('merge-sql', title='Anahtara göre MERGE / UPSERT ifadesi üret', domain=D, product=P, tag='MERGE', samples=SATIS,
    args=[arg('--target', default='dwh.satislar'), arg('--source', default='stg.satislar'), arg('--keys', default='siparis_no'), DIALECT_ARG,
          arg('--delete', action='store_true', help='Kaynakta olmayan hedef satırları sil (tsql/snowflake/oracle MERGE)')],
    description='Hedef tabloyu kaynak tablodan anahtar sütunlarla güncelleyen MERGE (SQL Server, Snowflake, BigQuery, Oracle) veya INSERT … ON CONFLICT (PostgreSQL) / ON DUPLICATE KEY (MySQL) ifadesini dosya başlığındaki sütunlardan yazar; yalnızca değişen satırları günceller.',
    input='Sütunları belirleyen CSV/XLSX (yalnızca başlık okunur) + hedef/kaynak tablo + anahtar', output='merge.sql', command='python main.py --target dwh.musteri --source stg.musteri --keys musteri_no --dialect snowflake',
    expect={'anahtar': 1, 'guncellenen_sutun': 10})
def merge_sql(columns, rows, args):
    keys = col_list(args.keys, columns, '--keys')
    q = lambda c: quote(snake(c), args.dialect)
    others = [c for c in columns if c not in keys]
    on = ' AND '.join(f't.{q(k)} = s.{q(k)}' for k in keys)
    if args.dialect == 'postgres':
        sql = (f"INSERT INTO {args.target} ({', '.join(q(c) for c in columns)})\nSELECT {', '.join(q(c) for c in columns)} FROM {args.source}\n"
               f"ON CONFLICT ({', '.join(q(k) for k in keys)}) DO UPDATE SET\n    " + ',\n    '.join(f'{q(c)} = EXCLUDED.{q(c)}' for c in others) +
               f"\nWHERE ({', '.join(f'{args.target.split(".")[-1]}.{q(c)}' for c in others)}) IS DISTINCT FROM ({', '.join(f'EXCLUDED.{q(c)}' for c in others)});")
    elif args.dialect == 'mysql':
        sql = (f"INSERT INTO {args.target} ({', '.join(q(c) for c in columns)})\nSELECT {', '.join(q(c) for c in columns)} FROM {args.source}\n"
               f"ON DUPLICATE KEY UPDATE\n    " + ',\n    '.join(f'{q(c)} = VALUES({q(c)})' for c in others) + ';')
    else:
        changed = '\n      OR '.join(f'NOT (t.{q(c)} = s.{q(c)} OR (t.{q(c)} IS NULL AND s.{q(c)} IS NULL))' for c in others)
        sql = (f'MERGE INTO {args.target} t\nUSING {args.source} s\n    ON {on}\nWHEN MATCHED AND (\n         {changed}\n) THEN UPDATE SET\n    ' +
               ',\n    '.join(f'{"" if args.dialect == "oracle" else "t."}{q(c)} = s.{q(c)}' for c in others) +
               f"\nWHEN NOT MATCHED{' BY TARGET' if args.dialect in ('tsql', 'bigquery') else ''} THEN INSERT ({', '.join(q(c) for c in columns)})\n    VALUES ({', '.join('s.' + q(c) for c in columns)})")
        if args.delete and args.dialect in ('tsql', 'bigquery'):
            sql += '\nWHEN NOT MATCHED BY SOURCE THEN DELETE'
        sql += ';'
    notes = ['Anahtar kaynakta tekil olmalıdır; aksi hâlde MERGE hata verir veya satırları birden çok kez günceller.']
    if args.delete and args.dialect not in ('tsql', 'bigquery'):
        notes.append(f'{args.dialect} MERGE “NOT MATCHED BY SOURCE” desteklemez; silme için ayrı DELETE … WHERE NOT EXISTS yazın.')
    return Result(f'{args.dialect} için {len(keys)} anahtarlı MERGE/UPSERT üretildi', None, None, {'anahtar': len(keys), 'guncellenen_sutun': len(others)},
                  files={'merge.sql': sql}, notes=notes)


KEYWORDS = ['SELECT', 'DISTINCT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL OUTER JOIN', 'CROSS JOIN', 'JOIN',
            'ON', 'AND', 'OR', 'UNION ALL', 'UNION', 'INSERT INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE FROM', 'WITH', 'AS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
            'IN', 'IS', 'NOT', 'NULL', 'LIKE', 'BETWEEN', 'EXISTS', 'LIMIT', 'TOP', 'OVER', 'PARTITION BY', 'MERGE INTO', 'USING', 'TRUNCATE TABLE', 'CREATE TABLE',
            'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'CAST', 'DESC', 'ASC']
BREAK = ['WITH', 'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL OUTER JOIN', 'CROSS JOIN', 'JOIN', 'UNION ALL',
         'UNION', 'INSERT INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE FROM', 'MERGE INTO', 'USING', 'LIMIT']


def protect(sql):
    """Replace strings and comments with placeholders so formatting never touches them."""
    saved = []

    def keep(m):
        saved.append(m.group(0))
        return f'\x00{len(saved) - 1}\x00'
    return re.sub(r"'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/", keep, sql, flags=re.S), saved


def restore(sql, saved):
    def put(m):
        text = saved[int(m.group(1))]
        # A line comment must stay the last thing on its line, or the code after it would become part of the comment.
        return text + '\n' if text.startswith(('--', '/*')) and m.end() < len(sql) else text
    return re.sub(r'\x00(\d+)\x00 ?', put, sql)


BREAK_RX = re.compile(r'(?<!\bLEFT)(?<!\bRIGHT)(?<!\bINNER)(?<!\bOUTER)(?<!\bCROSS)(?<!\bFULL)(?<!\bTHEN)(?<!\bDELETE)\s+(?=(?:'
                      + '|'.join(re.escape(k) for k in sorted(BREAK + ['WHEN MATCHED', 'WHEN NOT MATCHED'], key=len, reverse=True)) + r')(?![\w]))')


def format_sql(sql):
    body, saved = protect(sql)
    statements = [s.strip() for s in body.split(';') if s.strip()]
    out = []
    for st in statements:
        if re.fullmatch(r'(?:\x00\d+\x00\s*)+', st):  # only comments (e.g. a trailing note): keep them, no semicolon
            out.append(st.strip())
            continue
        st = re.sub(r'\s+', ' ', st)
        for kw in sorted(KEYWORDS + ['REPLACE', 'MATCHED'], key=len, reverse=True):
            st = re.sub(r'(?<![\w.])' + kw.replace(' ', r'\s+') + r'(?![\w])', kw, st, flags=re.I)
        st = BREAK_RX.sub('\n', st)
        st = re.sub(r'\s+(AND|OR)\s+(?!REPLACE\b)', r'\n    \1 ', st)
        lines = []
        for line in st.split('\n'):
            line = line.strip()
            if line.startswith('SELECT ') and ',' in line:
                head, rest = line[:7], line[7:]
                parts, depth, buf = [], 0, ''
                for ch in rest:
                    depth += (ch == '(') - (ch == ')')
                    if ch == ',' and depth == 0:
                        parts.append(buf.strip())
                        buf = ''
                    else:
                        buf += ch
                parts.append(buf.strip())
                line = head + (',\n       '.join(parts))
            lines.append(line)
        out.append('\n'.join(lines) + ';')
    return restore('\n\n'.join(out), saved)


def read_sql_files(path):
    p = Path(path)
    files = sorted(p.rglob('*.sql')) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'.sql dosyası bulunamadı: {p}')
    return [(f.relative_to(p).as_posix() if p.is_dir() else f.name, f.read_text(encoding='utf-8-sig', errors='replace')) for f in files]


@op('sql-bicimlendir', title='SQL kodunu okunur biçime getir', domain=D, product=P, tag='biçimlendirme', kind='file', samples=SQLS, sample_input='sql',
    description='SQL dosyalarında anahtar kelimeleri büyük harfe çevirir, SELECT listesini, JOIN/WHERE/GROUP BY gibi ana bölümleri ve AND/OR koşullarını ayrı satırlara böler. Metin sabitlerine ve yorumlara dokunmaz; dosyanın yalnızca biçimli kopyasını yazar.',
    input='.sql dosyası veya klasörü', output='bicimli/*.sql', command='python main.py --input C:\\proje\\sql',
    expect={'dosya': 3, 'atlanan': 0})
def bicim(args):
    files = read_sql_files(args.input)
    out, table = {}, []
    same = lambda s: re.sub(r'[\s;]+', '', s).upper()
    skipped = 0
    for name, text in files:
        formatted = format_sql(text)
        if same(formatted) != same(text):  # safety net: formatting may only change whitespace and keyword case
            formatted, skipped = text, skipped + 1
            table.append([name, len(text.splitlines()), 'DEĞİŞTİRİLMEDİ (güvenli biçimlendirilemedi)'])
        else:
            table.append([name, len(text.splitlines()), len(formatted.splitlines())])
        out[f'bicimli/{name}'] = formatted
    return Result(f'{len(files) - skipped} dosya biçimlendirildi' + (f', {skipped} dosya olduğu gibi bırakıldı' if skipped else ''), None, None,
                  {'dosya': len(files), 'atlanan': skipped}, tables=[('Dosyalar', ['Dosya', 'Önce satır', 'Sonra satır'], table)], files=out,
                  notes=['Biçimlendirici bir SQL ayrıştırıcısı değildir; karmaşık lehçe sözdiziminde sonucu gözle kontrol edin.'])


LINT = [('select-yildiz', 'orta', r'\bSELECT\s+(?:DISTINCT\s+|TOP\s+\d+\s+)?\*', 'SELECT * — şema değişince kırılır, gereksiz sütun taşır'),
        ('nolock', 'yüksek', r'\bNOLOCK\b|READ\s+UNCOMMITTED', 'NOLOCK/READ UNCOMMITTED — kirli okuma, tutarsız sonuç'),
        ('where-siz-update', 'yüksek', r'\bUPDATE\s+[\w.\[\]"]+\s+SET\b(?:(?!\bWHERE\b)[^;])*(?:;|$)', 'WHERE’siz UPDATE — tüm satırları değiştirir'),
        ('where-siz-delete', 'yüksek', r'\bDELETE\s+FROM\s+[\w.\[\]"]+\s*(?:;|$)', 'WHERE’siz DELETE — tüm satırları siler'),
        ('virgul-join', 'orta', r'\bFROM\s+[\w.\[\]"]+(?:\s+\w+)?\s*,\s*[\w.\[\]"]+', 'Virgüllü (örtük) JOIN — koşul unutulursa kartezyen çarpım'),
        ('kolonda-fonksiyon', 'orta', r'\bWHERE\b[^;]*?\b(?:YEAR|MONTH|UPPER|LOWER|CAST|CONVERT|DATEPART|TRIM|LTRIM|RTRIM|SUBSTRING|ISNULL|COALESCE)\s*\(\s*[\w.]+\s*\)\s*(?:=|<|>|IN\b)', 'WHERE’de sütun üzerinde fonksiyon — indeks kullanılamaz'),
        ('not-in-alt-sorgu', 'orta', r'\bNOT\s+IN\s*\(\s*SELECT\b', 'NOT IN (SELECT …) — alt sorgu NULL döndürürse hiç satır gelmez; NOT EXISTS kullanın'),
        ('order-by-sira', 'düşük', r'\b(?:ORDER|GROUP)\s+BY\s+\d+\b', 'ORDER/GROUP BY sütun sırası — sütun eklenince anlam değişir'),
        ('basta-joker', 'düşük', r"\bLIKE\s+N?'%", 'LIKE \'%…\' — baştaki joker indeks kullanımını engeller'),
        ('distinct', 'düşük', r'\bSELECT\s+DISTINCT\b', 'DISTINCT — yinelenen satırlar JOIN hatasını gizliyor olabilir'),
        ('union', 'düşük', r'\bUNION\b(?!\s+ALL)', 'UNION (ALL değil) — gereksiz sıralama/tekilleştirme maliyeti'),
        ('sabit-tarih', 'düşük', r"'\d{4}-?\d{2}-?\d{2}'", 'Sabit tarih — parametre veya tarih tablosu kullanın')]


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


@op('sql-denetim', title='SQL kodunda riskli kalıpları denetle (lint)', domain=D, product=P, tag='lint', kind='file', samples=SQLS, sample_input='sql',
    args=[arg('--strict', action='store_true', help='Yüksek önemli bulgu varsa 2 koduyla çık (CI)')],
    description='SQL dosyalarında SELECT *, NOLOCK, WHERE’siz UPDATE/DELETE, örtük JOIN, WHERE’de sütun üzerinde fonksiyon, NOT IN alt sorgusu, ORDER BY sıra no, baştaki LIKE jokeri ve sabit tarih gibi kalıpları satır numarasıyla raporlar. Kod incelemesi ve CI için.',
    input='.sql dosyası veya klasörü', output='sonuc.csv (bulgular)', command='python main.py --input C:\\proje\\sql --strict',
    expect={'bulgu': 4, 'yuksek': 1})
def lint(args):
    out = []
    for name, text in read_sql_files(args.input):
        body, saved = protect(text)
        # keep string positions for line numbers but drop comment content
        body = re.sub(r'\x00(\d+)\x00', lambda m: saved[int(m.group(1))] if saved[int(m.group(1))].startswith("'") else ' ' * len(saved[int(m.group(1))]), body)
        for rule, sev, rx, msg in LINT:
            for m in re.finditer(rx, body, re.I | re.S):
                out.append({'dosya': name, 'satir': line_of(body, m.start()), 'kural': rule, 'onem': sev, 'aciklama': msg, 'kod': re.sub(r'\s+', ' ', m.group(0))[:80]})
    sev = Counter(o['onem'] for o in out)
    return Result(f"{len(out)} bulgu ({sev['yüksek']} yüksek, {sev['orta']} orta, {sev['düşük']} düşük)", ['dosya', 'satir', 'kural', 'onem', 'aciklama', 'kod'], out,
                  {'bulgu': len(out), 'yuksek': sev['yüksek'], 'orta': sev['orta'], 'dusuk': sev['düşük']}, code=2 if args.strict and sev['yüksek'] else 0)


FK = re.compile(r'(?:FOREIGN\s+KEY\s*\(([^)]+)\)\s*)?REFERENCES\s+([\w.\[\]"`]+)\s*\(([^)]+)\)', re.I)


def relationships(text, tables):
    """Explicit REFERENCES plus inferred ones (column named like another table's single-column primary key)."""
    rels = []
    clean = re.sub(r'--[^\n]*|/\*.*?\*/', '', text, flags=re.S)
    for m in re.finditer(r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\[\]"`]+)\s*\((.*?)\)\s*;', clean, re.I | re.S):
        child = '.'.join(p.strip('[]"`') for p in re.split(r'\s*\.\s*', m.group(1)))
        for line in re.split(r',\s*\n', m.group(2)):
            for f in FK.finditer(line):
                col = (f.group(1) or line.strip().split()[0]).strip().strip('[]"`')
                rels.append((child, col, '.'.join(p.strip('[]"`') for p in f.group(2).split('.')), f.group(3).strip().strip('[]"`'), 'tanımlı'))
    pks = {t: [c['name'] for c in cols if c['pk']] for t, cols in tables.items()}
    explicit = {(r[0], r[1].lower()) for r in rels}
    for t, cols in tables.items():
        for c in cols:
            for parent, keys in pks.items():
                if parent != t and len(keys) == 1 and keys[0].lower() == c['name'].lower() and not c['pk'] and (t, c['name'].lower()) not in explicit:
                    rels.append((t, c['name'], parent, keys[0], 'ad benzerliğinden çıkarıldı'))
    return rels


@op('ddl-veri-sozlugu', title='DDL’den veri sözlüğü üret', domain=D, product=P, tag='sözlük', kind='file', samples=SEMA,
    description='CREATE TABLE betiklerinden tablo ve sütun listesini; tip, NULL kabulü, birincil anahtar ve (tanımlı ya da ad benzerliğinden çıkarılan) ilişkilerle birlikte veri sözlüğüne dönüştürür. Açıklama sütunu doldurulmak üzere boş bırakılır.',
    input='.sql dosyası veya klasörü (CREATE TABLE)', output='sonuc.csv (sözlük) + rapor', command='python main.py --input ddl/',
    expect={'tablo': 4, 'sutun': 18, 'iliski': 2})
def ddl_sozluk(args):
    tables = load_ddl(args.input)
    text = '\n'.join(t for _, t in read_sql_files(args.input))
    rels = relationships(text, tables)
    ref = {(r[0], r[1].lower()): f'{r[2]}.{r[3]} ({r[4]})' for r in rels}
    out = [{'tablo': t, 'sutun': c['name'], 'tip': c['type'], 'null_kabul': 'evet' if c['nullable'] else 'hayır', 'birincil_anahtar': 'evet' if c['pk'] else '',
            'iliski': ref.get((t, c['name'].lower()), ''), 'aciklama': ''} for t, cols in tables.items() for c in cols]
    return Result(f'{len(tables)} tablo, {len(out)} sütun, {len(rels)} ilişki', list(out[0]), out, {'tablo': len(tables), 'sutun': len(out), 'iliski': len(rels)},
                  tables=[('İlişkiler', ['Alt tablo', 'Sütun', 'Üst tablo', 'Anahtar', 'Kaynak'], [list(r) for r in rels])])


@op('ddl-er-diyagrami', title='DDL’den ER diyagramı (Mermaid) çiz', domain=D, product=P, tag='ER', kind='file', samples=SEMA,
    description='CREATE TABLE betiklerinden tabloları, anahtarları ve tanımlı/çıkarılan ilişkileri Mermaid erDiagram olarak yazar; GitHub, GitLab, Notion ve dokümantasyon araçlarında doğrudan görüntülenir.',
    input='.sql dosyası veya klasörü', output='er-diyagrami.md (Mermaid)', command='python main.py --input ddl/',
    expect={'tablo': 4, 'iliski': 2})
def er(args):
    tables = load_ddl(args.input)
    rels = relationships('\n'.join(t for _, t in read_sql_files(args.input)), tables)
    ident = lambda t: re.sub(r'\W', '_', t)
    lines = ['erDiagram']
    for t, cols in tables.items():
        lines.append(f'    {ident(t)} {{')
        for c in cols:
            fk = any(r[0] == t and r[1].lower() == c['name'].lower() for r in rels)
            mark = ' PK' if c['pk'] else ' FK' if fk else ''
            lines.append(f"        {re.sub(r'[^A-Za-z0-9_]', '_', base_type(c['type'])[0] or 'tip')} {c['name']}{mark}")
        lines.append('    }')
    for child, col, parent, key, src in rels:
        lines.append(f'    {ident(parent)} ||--o{{ {ident(child)} : "{col}"')
    md = '# ER diyagramı\n\n```mermaid\n' + '\n'.join(lines) + '\n```\n\n' + '\n'.join(f'- {c}.{k} → {p}.{q} ({s})' for c, k, p, q, s in rels)
    return Result(f'{len(tables)} tablo, {len(rels)} ilişki çizildi', None, None, {'tablo': len(tables), 'iliski': len(rels)}, files={'er-diyagrami.md': md})


TYPE_MAP = {('nvarchar', 'postgres'): 'VARCHAR({0})', ('nvarchar', 'bigquery'): 'STRING', ('nvarchar', 'snowflake'): 'VARCHAR({0})', ('nvarchar', 'mysql'): 'VARCHAR({0})', ('nvarchar', 'oracle'): 'NVARCHAR2({0})',
            ('varchar', 'bigquery'): 'STRING', ('varchar', 'oracle'): 'VARCHAR2({0})', ('char', 'bigquery'): 'STRING', ('nchar', 'bigquery'): 'STRING',
            ('datetime', 'postgres'): 'TIMESTAMP', ('datetime2', 'postgres'): 'TIMESTAMP', ('datetime', 'bigquery'): 'DATETIME', ('datetime2', 'bigquery'): 'DATETIME',
            ('datetime', 'snowflake'): 'TIMESTAMP_NTZ', ('datetime2', 'snowflake'): 'TIMESTAMP_NTZ', ('datetime', 'oracle'): 'TIMESTAMP', ('datetime2', 'oracle'): 'TIMESTAMP',
            ('datetime2', 'mysql'): 'DATETIME', ('bit', 'postgres'): 'BOOLEAN', ('bit', 'bigquery'): 'BOOL', ('bit', 'snowflake'): 'BOOLEAN', ('bit', 'oracle'): 'NUMBER(1)',
            ('int', 'bigquery'): 'INT64', ('bigint', 'bigquery'): 'INT64', ('smallint', 'bigquery'): 'INT64', ('tinyint', 'bigquery'): 'INT64', ('tinyint', 'postgres'): 'SMALLINT',
            ('int', 'oracle'): 'NUMBER(10)', ('bigint', 'oracle'): 'NUMBER(19)', ('decimal', 'bigquery'): 'NUMERIC', ('numeric', 'bigquery'): 'NUMERIC',
            ('decimal', 'oracle'): 'NUMBER({0},{1})', ('money', 'postgres'): 'NUMERIC(19,4)', ('money', 'bigquery'): 'NUMERIC', ('money', 'snowflake'): 'NUMBER(19,4)',
            ('uniqueidentifier', 'postgres'): 'UUID', ('uniqueidentifier', 'bigquery'): 'STRING', ('uniqueidentifier', 'snowflake'): 'VARCHAR(36)', ('uniqueidentifier', 'oracle'): 'RAW(16)',
            ('float', 'bigquery'): 'FLOAT64', ('real', 'bigquery'): 'FLOAT64', ('text', 'bigquery'): 'STRING', ('ntext', 'postgres'): 'TEXT', ('date', 'oracle'): 'DATE'}


def convert_type(t, dialect):
    b, a = base_type(t)
    if 'max' in a:
        return {'postgres': 'TEXT', 'bigquery': 'STRING', 'snowflake': 'VARCHAR', 'mysql': 'LONGTEXT', 'oracle': 'CLOB', 'tsql': t}[dialect], True
    pattern = TYPE_MAP.get((b, dialect))
    if pattern:
        return pattern.format(*(a + ['', ''])).replace('()', '').replace('(,)', ''), False
    return t, False


@op('ddl-lehce-cevir', title='DDL tiplerini başka bir veritabanı lehçesine çevir', domain=D, product=P, tag='DDL çeviri', kind='file', samples=SEMA,
    args=[arg('--to', choices=[d for d in DIALECTS if d != 'tsql'], default='postgres')],
    description='SQL Server CREATE TABLE betiklerini PostgreSQL, MySQL, Oracle, BigQuery veya Snowflake tiplerine çevirir (NVARCHAR, DATETIME2, BIT, MONEY, UNIQUEIDENTIFIER, MAX…); anahtar ve NOT NULL bilgisini korur, çeviride kayıp olabilecek tipleri işaretler.',
    input='.sql dosyası veya klasörü (SQL Server DDL)', output='ddl-<lehçe>.sql + tip eşleme tablosu', command='python main.py --input ddl/ --to bigquery',
    expect={'tablo': 4, 'cevrilen_tip': 6})
def lehce(args):
    tables = load_ddl(args.input)
    out, mapping, changed = [], [], 0
    for t, cols in tables.items():
        lines, pk = [], [c['name'] for c in cols if c['pk']]
        for c in cols:
            new, lossy = convert_type(c['type'], args.to)
            changed += new.upper() != c['type'].upper()
            mapping.append([t, c['name'], c['type'], new, 'uzun metin (sınırsız)' if lossy else ''])
            lines.append(f"    {c['name']} {new}{'' if c['nullable'] else ' NOT NULL'}")
        if pk:
            lines.append(f"    PRIMARY KEY ({', '.join(pk)}){' NOT ENFORCED' if args.to == 'bigquery' else ''}")
        out.append(f'CREATE TABLE {t} (\n' + ',\n'.join(lines) + '\n);')
    return Result(f'{len(tables)} tablo {args.to} lehçesine çevrildi; {changed} sütun tipi değişti', None, None, {'tablo': len(tables), 'cevrilen_tip': changed},
                  tables=[('Tip eşlemesi', ['Tablo', 'Sütun', 'Kaynak tip', 'Hedef tip', 'Not'], mapping)],
                  files={f'ddl-{args.to}.sql': f'-- {args.to} için çevrildi; varsayılan değerler, indeksler ve kısıtlar ayrıca taşınmalı.\n\n' + '\n\n'.join(out)})


@op('sql-veri-testleri', title='DDL’den veri testi sorguları üret', domain=D, product=P, tag='veri testi', kind='file', samples=SEMA,
    args=[arg('--style', choices=['sorgu', 'dbt'], default='sorgu')],
    description='Her tablo için birincil anahtar tekilliği, NOT NULL sütunlarda boş değer ve (tanımlı/çıkarılan) yabancı anahtar yetim kaydı testlerini yazar; her sorgu ihlal sayısını döndürür. --style dbt ile dbt schema.yml testlerini üretir.',
    input='.sql dosyası veya klasörü (CREATE TABLE)', output='veri-testleri.sql veya schema.yml', command='python main.py --input ddl/ --style dbt',
    expect={'test': 12})
def veri_testleri(args):
    tables = load_ddl(args.input)
    rels = relationships('\n'.join(t for _, t in read_sql_files(args.input)), tables)
    tests = []
    for t, cols in tables.items():
        pk = [c['name'] for c in cols if c['pk']]
        if pk:
            tests.append((t, 'unique', pk, f"SELECT COUNT(*) AS ihlal FROM (SELECT {', '.join(pk)} FROM {t} GROUP BY {', '.join(pk)} HAVING COUNT(*) > 1) x"))
        for c in cols:
            if not c['nullable']:
                tests.append((t, 'not_null', [c['name']], f"SELECT COUNT(*) AS ihlal FROM {t} WHERE {c['name']} IS NULL"))
    for child, col, parent, key, src in rels:
        tests.append((child, 'relationship', [col, parent, key], f'SELECT COUNT(*) AS ihlal FROM {child} c WHERE c.{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{key} = c.{col})'))
    if args.style == 'dbt':
        by = defaultdict(lambda: defaultdict(list))
        for t, kind, cols, _ in tests:
            if kind == 'relationship':
                by[t][cols[0]].append(f"relationships:\n              to: source('kaynak', '{cols[1].split('.')[-1]}')\n              field: {cols[2]}")
            elif kind == 'unique' and len(cols) == 1:
                by[t][cols[0]].append('unique')
            elif kind == 'not_null':
                by[t][cols[0]].append('not_null')
        yml = ['version: 2', '', 'sources:', '  - name: kaynak', '    tables:']
        for t, cols in by.items():
            yml += [f"      - name: {t.split('.')[-1]}", '        columns:']
            for c, ts in cols.items():
                yml += [f'          - name: {c}', '            tests:'] + [f'              - {x}' for x in ts]
        files = {'schema.yml': '\n'.join(yml)}
    else:
        files = {'veri-testleri.sql': '\n\n'.join(f'-- {t} · {k} · {", ".join(c)}\n{q};' for t, k, c, q in tests)}
    kinds = Counter(k for _, k, _, _ in tests)
    return Result(f"{len(tests)} test ({kinds['unique']} tekillik, {kinds['not_null']} boş değer, {kinds['relationship']} ilişki)", None, None,
                  {'test': len(tests), 'tekillik': kinds['unique'], 'bos_deger': kinds['not_null'], 'iliski': kinds['relationship']}, files=files,
                  notes=['Sorgular salt okunurdur ve 0 döndürmesi beklenir. Bu blok sorguları çalıştırmaz.'])


@op('scd2-sql', title='SCD Tip 2 (tarihçeli boyut) yükleme SQL’i üret', domain=D, product=P, tag='SCD2', kind='none', samples={},
    args=[arg('--target', default='dwh.dim_musteri'), arg('--source', default='stg.musteri'), arg('--key', default='musteri_no'),
          arg('--columns', default='musteri_adi,sehir,segment'), arg('--dialect', choices=['tsql', 'snowflake', 'postgres', 'bigquery'], default='tsql')],
    description='Doğal anahtar ve izlenecek sütunlardan, değişen kaydı kapatıp (gecerli_bitis, aktif=0) yeni sürümü ekleyen iki adımlı SCD Tip 2 yükleme SQL’ini ve hedef tablo DDL’ini yazar; değişiklik tespiti NULL güvenlidir.',
    input='Hedef/kaynak tablo, anahtar ve izlenecek sütunlar', output='scd2.sql', command='python main.py --target dwh.dim_urun --source stg.urun --key urun_kodu --columns urun_adi,kategori --dialect snowflake',
    expect={'izlenen_sutun': 3})
def scd2(args):
    cols = [c.strip() for c in args.columns.split(',') if c.strip()]
    if not cols:
        raise ValueError('--columns ile izlenecek en az bir sütun verin.')
    now = {'tsql': 'SYSDATETIME()', 'snowflake': 'CURRENT_TIMESTAMP()', 'postgres': 'CURRENT_TIMESTAMP', 'bigquery': 'CURRENT_DATETIME()'}[args.dialect]
    hi = {'tsql': "'9999-12-31'", 'snowflake': "'9999-12-31'::TIMESTAMP_NTZ", 'postgres': "TIMESTAMP '9999-12-31'", 'bigquery': "DATETIME '9999-12-31 00:00:00'"}[args.dialect]
    diff = ' OR '.join(f'NOT (t.{c} = s.{c} OR (t.{c} IS NULL AND s.{c} IS NULL))' for c in cols)
    ts = {'tsql': 'DATETIME2', 'snowflake': 'TIMESTAMP_NTZ', 'postgres': 'TIMESTAMP', 'bigquery': 'DATETIME'}[args.dialect]
    sql = f"""-- SCD Tip 2: {args.target} ← {args.source} · anahtar: {args.key} · izlenen: {', '.join(cols)}
-- Hedef tablo (bir kez):
-- CREATE TABLE {args.target} (sk BIGINT /* otomatik artan veya sequence */, {args.key} ..., {', '.join(c + ' ...' for c in cols)},
--     gecerli_baslangic {ts} NOT NULL, gecerli_bitis {ts} NOT NULL, aktif {'BIT' if args.dialect == 'tsql' else 'BOOLEAN'} NOT NULL);

-- 1) Değişen kayıtların aktif sürümünü kapat
UPDATE {args.target}{' t' if args.dialect != 'tsql' else ''}
SET gecerli_bitis = {now}, aktif = {'0' if args.dialect == 'tsql' else 'FALSE'}
{'FROM ' + args.target + ' t JOIN ' + args.source + ' s ON s.' + args.key + ' = t.' + args.key if args.dialect == 'tsql' else 'FROM ' + args.source + ' s'}
WHERE {'t.aktif = 1' if args.dialect == 'tsql' else 't.aktif = TRUE AND s.' + args.key + ' = t.' + args.key}
  AND ({diff});

-- 2) Yeni ve değişen kayıtların yeni sürümünü ekle
INSERT INTO {args.target} ({args.key}, {', '.join(cols)}, gecerli_baslangic, gecerli_bitis, aktif)
SELECT s.{args.key}, {', '.join('s.' + c for c in cols)}, {now}, {hi}, {'1' if args.dialect == 'tsql' else 'TRUE'}
FROM {args.source} s
LEFT JOIN {args.target} t ON t.{args.key} = s.{args.key} AND t.aktif = {'1' if args.dialect == 'tsql' else 'TRUE'}
WHERE t.{args.key} IS NULL;
"""
    return Result(f'{args.dialect} için SCD2 yükleme SQL’i ({len(cols)} izlenen sütun)', None, None, {'izlenen_sutun': len(cols), 'lehce': args.dialect},
                  files={'scd2.sql': sql}, notes=['İki adım aynı işlem (transaction) içinde çalıştırılmalıdır. Kaynakta silinen kayıtlar bu şablonda kapatılmaz.'])
