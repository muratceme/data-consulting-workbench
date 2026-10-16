"""Veri migrasyonu blokları: kaynak–hedef alan eşleme kontrolü, tablo satır sayısı mutabakatı, kodlama ve uzunluk riski, sütun profili karşılaştırması,
SQL sorgularını PostgreSQL’e çevirme ve geçiş dalgası planı. Hiçbir veritabanına bağlanmaz."""
import heapq, re
from pathlib import Path
from collections import Counter, defaultdict
import tabular
from sema_farki import load as load_ddl, narrowing, base_type
from islem import op, arg, Result, need, numbers, fmt

REQUIRES = ['sema_farki']
D, P = 'migration', 'Geçiş eşleme dosyaları'


def norm_table(t):
    return t.split('.')[-1].strip('[]"`').lower().replace('_', '')


@op('alan-esleme-kontrol', title='Kaynak–hedef alan eşleme dosyasını doğrula', domain=D, product=P, tag='eşleme', samples={'gecis_esleme.csv': 'gecis_esleme.csv', 'sema.sql': 'sema.sql', 'hedef_ddl.sql': 'hedef_ddl.sql'},
    args=[arg('--source-ddl', default='@ornek/sema.sql'), arg('--target-ddl', default='@ornek/hedef_ddl.sql')],
    description='Migrasyon eşleme tablosunu (kaynak tablo/sütun → hedef tablo/sütun) iki DDL ile karşılaştırır: kaynakta veya hedefte olmayan sütunları, eşlenmemiş kaynak sütunları (veri kaybı), aynı hedefe birden çok eşlemeyi, tip daralmasını ve NOT NULL hedefe boş olabilen kaynağı raporlar.',
    input='Eşleme CSV’si + kaynak DDL + hedef DDL', output='sonuc.csv (eşleme satırı bazında kontrol) + eşlenmemiş sütunlar', command='python main.py --input esleme.xlsx --source-ddl kaynak.sql --target-ddl hedef.sql',
    expect={'esleme': 9, 'hatali': 2, 'eslenmemis_kaynak': 9})
def esleme(columns, rows, args):
    for c in ('kaynak_tablo', 'kaynak_sutun', 'hedef_tablo', 'hedef_sutun'):
        need(c, columns, 'eşleme dosyası')
    src, tgt = load_ddl(args.source_ddl), load_ddl(args.target_ddl)
    sidx = {(t.lower(), c['name'].lower()): c for t, cols in src.items() for c in cols}
    tidx = {(t.lower(), c['name'].lower()): c for t, cols in tgt.items() for c in cols}
    targets = Counter((r['hedef_tablo'].strip().lower(), r['hedef_sutun'].strip().lower()) for r in rows)
    mapped = set()
    out = []
    for r in rows:
        sk, tk = (r['kaynak_tablo'].strip().lower(), r['kaynak_sutun'].strip().lower()), (r['hedef_tablo'].strip().lower(), r['hedef_sutun'].strip().lower())
        mapped.add(sk)
        s, t = sidx.get(sk), tidx.get(tk)
        issues = []
        if not s:
            issues.append('kaynak sütun DDL’de yok')
        if not t:
            issues.append('hedef sütun DDL’de yok')
        if targets[tk] > 1:
            issues.append('aynı hedefe birden çok eşleme')
        warn = []
        if s and t:
            if narrowing(s['type'], t['type']):
                warn.append(f"tip daralması {s['type']} → {t['type']}")
            if base_type(s['type'])[0] in ('nvarchar', 'nchar') and base_type(t['type'])[0] in ('varchar', 'char'):
                warn.append('Unicode (N) → Unicode olmayan tip: hedef kodlaması UTF-8 değilse Türkçe karakter bozulur')
            if s['nullable'] and not t['nullable']:
                warn.append('kaynak NULL olabilir, hedef NOT NULL')
        out.append({**r, 'kaynak_tip': s['type'] if s else '', 'hedef_tip': t['type'] if t else '', 'hata': '; '.join(issues), 'uyari': '; '.join(warn)})
    src_tables = {k[0] for k in mapped}
    unmapped = [[t, c['name'], c['type']] for t, cols in src.items() for c in cols if t.lower() in src_tables and (t.lower(), c['name'].lower()) not in mapped] + \
               [[t, '(tüm tablo)', ''] for t in src if t.lower() not in src_tables]
    unmapped_cols = sum(1 for x in unmapped if x[1] != '(tüm tablo)') + sum(len(src[x[0]]) for x in unmapped if x[1] == '(tüm tablo)')
    return Result(f"{len(rows)} eşleme; {sum(bool(o['hata']) for o in out)} hatalı, {sum(bool(o['uyari']) for o in out)} uyarılı; {unmapped_cols} kaynak sütun eşlenmemiş",
                  columns + ['kaynak_tip', 'hedef_tip', 'hata', 'uyari'], out,
                  {'esleme': len(rows), 'hatali': sum(bool(o['hata']) for o in out), 'uyarili': sum(bool(o['uyari']) for o in out), 'eslenmemis_kaynak': unmapped_cols},
                  tables=[('Eşlenmemiş kaynak sütun/tablolar', ['Tablo', 'Sütun', 'Tip'], unmapped)])


@op('tablo-sayim-mutabakati', title='Kaynak ve hedef tablo satır sayılarını karşılaştır', domain=D, product=P, tag='satır sayısı', samples={'kaynak_sayimlari.csv': 'kaynak_sayimlari.csv', 'hedef_sayimlari.csv': 'hedef_sayimlari.csv'},
    args=[arg('--target', default='@ornek/hedef_sayimlari.csv'), arg('--table', default='tablo'), arg('--count', default='satir'), arg('--tolerance', type=float, default=0.0, help='Kabul edilen fark yüzdesi')],
    description='Geçiş öncesi/sonrası (veya kaynak–hedef) tablo satır sayılarını şema öneki, büyük/küçük harf ve alt çizgi farkına bakmadan eşleştirir; eksik/fazla satırları, hedefte olmayan ve kaynakta olmayan tabloları, tolerans dışındaki farkları listeler.',
    input='İki CSV (tablo, satır sayısı)', output='sonuc.csv', command='python main.py --input kaynak.csv --target hedef.csv --tolerance 0.01',
    expect={'tablo': 6, 'farkli': 1, 'hedefte_yok': 1, 'kaynakta_yok': 1})
def sayim(columns, rows, args):
    tc, tr_ = tabular.read(args.target)
    s = {norm_table(r[need(args.table, columns, '--table')]): (r[args.table], n or 0) for r, n in zip(rows, numbers(rows, need(args.count, columns, '--count')))}
    t = {norm_table(r[need(args.table, tc, '--target')]): (r[args.table], n or 0) for r, n in zip(tr_, numbers(tr_, need(args.count, tc, '--target')))}
    out = []
    for k in sorted(set(s) | set(t)):
        a, b = s.get(k), t.get(k)
        if a and b:
            diff = b[1] - a[1]
            pct = 100 * diff / a[1] if a[1] else 0.0
            state = 'tamam' if abs(pct) <= args.tolerance and (diff == 0 or args.tolerance) else 'FARK'
        else:
            diff, pct, state = '', '', 'hedefte yok' if a else 'kaynakta yok'
        out.append({'kaynak_tablo': a[0] if a else '', 'hedef_tablo': b[0] if b else '', 'kaynak_satir': int(a[1]) if a else '', 'hedef_satir': int(b[1]) if b else '',
                    'fark': int(diff) if diff != '' else '', 'fark_%': round(pct, 4) if pct != '' else '', 'durum': state})
    c = Counter(o['durum'] for o in out)
    return Result(f"{len(out)} tablo; {c['tamam']} tamam, {c['FARK']} farklı, {c['hedefte yok']} hedefte yok, {c['kaynakta yok']} kaynakta yok", list(out[0]), out,
                  {'tablo': len(out), 'farkli': c['FARK'], 'hedefte_yok': c['hedefte yok'], 'kaynakta_yok': c['kaynakta yok']}, code=0)


@op('kodlama-uzunluk-riski', title='Hedef sütun uzunluğu ve karakter kodlaması riski', domain=D, product=P, tag='kodlama', samples={'gecis_verisi.csv': 'gecis_verisi.csv', 'hedef_ddl.sql': 'hedef_ddl.sql'},
    args=[arg('--target-ddl', default='@ornek/hedef_ddl.sql'), arg('--table', default='public.musteriler'), arg('--bytes', action='store_true', help='Uzunluğu bayt (UTF-8) olarak say (MySQL latin1/Oracle BYTE semantiği)')],
    description='Taşınacak verideki her değeri hedef tablonun sütun uzunluğuyla karşılaştırır; karakter veya UTF-8 bayt uzunluğu (Türkçe karakterler 2 bayt) sınırı aşan değerleri ve Unicode olmayan (VARCHAR/latin1) sütunlara giden Türkçe karakterleri yükleme öncesinde bulur.',
    input='Taşınacak veri (CSV/XLSX) + hedef DDL + tablo adı', output='sonuc.csv (riskli değerler)', command='python main.py --input musteri.csv --target-ddl hedef.sql --table public.musteriler --bytes',
    test_args=['--bytes'], expect={'riskli_deger': 3, 'kontrol_edilen_sutun': 3})
def kodlama(columns, rows, args):
    ddl = load_ddl(args.target_ddl)
    table = next((t for t in ddl if t.lower() == args.table.lower()), None)
    if not table:
        raise ValueError(f'"{args.table}" hedef DDL’de yok. Tablolar: {", ".join(ddl)}')
    cols = {c['name'].lower(): c for c in ddl[table]}
    checked, out = [], []
    for col in columns:
        c = cols.get(col.lower())
        if not c:
            continue
        b, a = base_type(c['type'])
        limit = int(a[0]) if a and a[0].isdigit() else None
        if b not in ('char', 'varchar', 'nchar', 'nvarchar', 'varchar2', 'nvarchar2', 'character varying') or limit is None:
            continue
        checked.append(col)
        unicode_col = b.startswith('n')
        for i, r in enumerate(rows, start=2):
            v = r[col]
            n = len(v.encode('utf-8')) if args.bytes else len(v)
            issues = []
            if n > limit:
                issues.append(f"{n} {'bayt' if args.bytes else 'karakter'} > {limit}")
            elif not args.bytes and len(v.encode('utf-8')) > limit and not unicode_col:
                issues.append(f'karakter sayısı sığıyor ama UTF-8 {len(v.encode("utf-8"))} bayt > {limit} (bayt semantiğinde kesilir)')
            if issues:
                out.append({'satir': i, 'sutun': col, 'hedef_tip': c['type'], 'uzunluk': n, 'deger': v[:60], 'sorun': '; '.join(issues)})
    return Result(f'{len(checked)} metin sütunu kontrol edildi; {len(out)} riskli değer', ['satir', 'sutun', 'hedef_tip', 'uzunluk', 'deger', 'sorun'], out,
                  {'riskli_deger': len(out), 'kontrol_edilen_sutun': len(checked)},
                  notes=['PostgreSQL ve SQL Server NVARCHAR karakter sayar; MySQL utf8mb4 VARCHAR karakter, Oracle VARCHAR2(n BYTE) bayt sayar. Hedefinize göre --bytes kullanın.'])


def profile(rows, col):
    vals = [r[col].strip() for r in rows]
    filled = [v for v in vals if v]
    p = {'dolu': len(filled), 'farkli': len(set(filled))}
    nums = numbers(rows, col)
    if filled and all(n is not None for v, n in zip(vals, nums) if v):
        xs = [n for n in nums if n is not None]
        p.update(tur='sayı', toplam=round(sum(xs), 6), min=min(xs), max=max(xs))
    elif filled and all(tabular.parse_date(v) for v in filled):
        ds = sorted(tabular.parse_date(v)[0] for v in filled)
        p.update(tur='tarih', toplam='', min=ds[0].isoformat(sep=' ').replace(' 00:00:00', ''), max=ds[-1].isoformat(sep=' ').replace(' 00:00:00', ''))
    else:
        lens = [len(v) for v in filled] or [0]
        p.update(tur='metin', toplam='', min=f'uzunluk {min(lens)}', max=f'uzunluk {max(lens)}')
    return p


@op('kolon-profil-karsilastir', title='Kaynak ve hedef çıktılarını sütun profiliyle karşılaştır', domain=D, product=P, tag='profil mutabakatı',
    samples={'kaynak_aktarim.csv': 'kaynak_aktarim.csv', 'hedef_aktarim.csv': 'hedef_aktarim.csv'},
    args=[arg('--target', default='@ornek/hedef_aktarim.csv'), arg('--tolerance', type=float, default=0.0, help='Sayısal toplamlarda kabul edilen mutlak fark')],
    description='Aynı tablonun kaynak ve hedef dışa aktarımlarını satır anahtarı gerekmeden sütun profiliyle karşılaştırır: satır sayısı, dolu ve farklı değer sayısı, sayısal sütunlarda toplam/min/max, tarihlerde aralık, metinlerde uzunluk. Ondalık ayırıcı farkını (1.500,50 / 1500.50) kendisi çözer; yuvarlama, karakter dönüşümü (İ→I) ve kırpılma gibi sessiz migrasyon hatalarını yakalar.',
    input='İki CSV/XLSX (kaynak ve hedef dışa aktarımı)', output='sonuc.csv (sütun bazında profil ve farklar)', command='python main.py --input kaynak_musteri.csv --target hedef_musteri.csv --tolerance 0.01',
    expect={'ortak_sutun': 5, 'farkli_sutun': 2, 'hedefte_yok': 1, 'kaynakta_yok': 1})
def profil(columns, rows, args):
    tcols, trows = tabular.read(args.target)
    tmap = {c.strip().lower(): c for c in tcols}
    smap = {c.strip().lower(): c for c in columns}
    out, diff_cols = [], 0
    for key in [k for k in smap if k in tmap]:
        a, b = profile(rows, smap[key]), profile(trows, tmap[key])
        diffs = [m for m in ('dolu', 'farkli', 'min', 'max') if a[m] != b[m]]
        if a['tur'] == b['tur'] == 'sayı' and abs(a['toplam'] - b['toplam']) > args.tolerance + 1e-9:
            diffs.insert(0, 'toplam')
        if a['tur'] != b['tur']:
            diffs.insert(0, 'tür')
        diff_cols += bool(diffs)
        out.append({'sutun': smap[key], 'tur': a['tur'] if a['tur'] == b['tur'] else f"{a['tur']} → {b['tur']}", 'durum': 'FARK' if diffs else 'aynı', 'farklar': ', '.join(diffs),
                    'kaynak_dolu': a['dolu'], 'hedef_dolu': b['dolu'], 'kaynak_farkli': a['farkli'], 'hedef_farkli': b['farkli'], 'kaynak_toplam': a['toplam'], 'hedef_toplam': b['toplam'],
                    'kaynak_min': a['min'], 'hedef_min': b['min'], 'kaynak_max': a['max'], 'hedef_max': b['max']})
    only_s = [smap[k] for k in smap if k not in tmap]
    only_t = [tmap[k] for k in tmap if k not in smap]
    out.sort(key=lambda o: o['durum'] != 'FARK')
    return Result(f'{len(rows)} → {len(trows)} satır; {len(out)} ortak sütunun {diff_cols} tanesinde fark', list(out[0]) if out else ['sutun'], out,
                  {'kaynak_satir': len(rows), 'hedef_satir': len(trows), 'ortak_sutun': len(out), 'farkli_sutun': diff_cols, 'hedefte_yok': len(only_s), 'kaynakta_yok': len(only_t)},
                  tables=[('Yalnızca bir tarafta olan sütunlar', ['Taraf', 'Sütun'], [['kaynak', c] for c in only_s] + [['hedef', c] for c in only_t])])


DATE_PARTS = {'day': 'day', 'dd': 'day', 'd': 'day', 'month': 'month', 'mm': 'month', 'm': 'month', 'year': 'year', 'yy': 'year', 'yyyy': 'year',
              'hour': 'hour', 'hh': 'hour', 'minute': 'minute', 'mi': 'minute', 'n': 'minute', 'second': 'second', 'ss': 'second', 's': 'second', 'week': 'week', 'wk': 'week', 'ww': 'week'}


def call_args(sql, start):
    """Arguments of the call whose '(' is at sql[start-1]; returns (args, index of closing ')')."""
    depth, buf, out, i = 1, '', [], start
    while i < len(sql):
        ch = sql[i]
        depth += (ch == '(') - (ch == ')')
        if not depth:
            break
        if ch == ',' and depth == 1:
            out.append(buf.strip())
            buf = ''
        else:
            buf += ch
        i += 1
    out.append(buf.strip())
    return out, i


def rewrite(sql, name, fn, stats, manual):
    rx = re.compile(r'\b' + name + r'\s*\(', re.I)
    while True:
        ms = list(rx.finditer(sql))
        if not ms:
            return sql.replace(f'ZZELLE_{name}', name)
        m = ms[-1]
        args, end = call_args(sql, m.end())
        new = fn([a for a in args if a != ''] if args != [''] else [])
        if new is None:
            manual.append((name, f'{name}({", ".join(args)})'))
            sql = sql[:m.start()] + f'ZZELLE_{name}' + sql[m.start() + len(name):]
        else:
            stats[name] += 1
            sql = sql[:m.start()] + new + sql[end + 1:]


def datediff(a):
    part = DATE_PARTS.get(a[0].lower().strip()) if len(a) == 3 else None
    return f'({a[2]}::date - {a[1]}::date)' if part == 'day' else None


def decode(a):
    if len(a) < 3:
        return None
    pairs, default = a[1:len(a) - (len(a) - 1) % 2], (a[-1] if (len(a) - 1) % 2 else None)
    whens = ' '.join(f'WHEN {pairs[i]} THEN {pairs[i + 1]}' for i in range(0, len(pairs), 2))
    return f'CASE {a[0]} {whens}' + (f' ELSE {default}' if default else '') + ' END'


FUNCS = {'tsql': [('ISNULL', lambda a: f'COALESCE({", ".join(a)})' if len(a) == 2 else None),
                  ('LEN', lambda a: f'LENGTH({a[0]})' if len(a) == 1 else None),
                  ('CHARINDEX', lambda a: f'POSITION({a[0]} IN {a[1]})' if len(a) == 2 else None),
                  ('IIF', lambda a: f'CASE WHEN {a[0]} THEN {a[1]} ELSE {a[2]} END' if len(a) == 3 else None),
                  ('CONVERT', lambda a: f'CAST({a[1]} AS {a[0]})' if len(a) == 2 else None),
                  ('DATEADD', lambda a: f"({a[2]} + ({a[1]}) * INTERVAL '1 {DATE_PARTS[a[0].lower()]}')" if len(a) == 3 and a[0].lower() in DATE_PARTS else None),
                  ('DATEDIFF', datediff),
                  ('GETDATE', lambda a: 'CURRENT_TIMESTAMP' if not a else None),
                  ('SYSDATETIME', lambda a: 'CURRENT_TIMESTAMP' if not a else None),
                  ('GETUTCDATE', lambda a: "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if not a else None),
                  ('NEWID', lambda a: 'gen_random_uuid()' if not a else None)],
         'oracle': [('NVL', lambda a: f'COALESCE({", ".join(a)})' if len(a) == 2 else None),
                    ('NVL2', lambda a: f'CASE WHEN {a[0]} IS NOT NULL THEN {a[1]} ELSE {a[2]} END' if len(a) == 3 else None),
                    ('DECODE', decode),
                    ('INSTR', lambda a: f'STRPOS({a[0]}, {a[1]})' if len(a) == 2 else None)]}
WORDS = {'tsql': [(r'\s*WITH\s*\(\s*NOLOCK\s*\)', '', 'WITH (NOLOCK)'), (r'\[([^\]\x00]+)\]', r'"\1"', '[ad] → "ad"')],
         'oracle': [(r'\bSYS(?:DATE|TIMESTAMP)\s*([-+])\s*(\d+(?:\.\d+)?)\b', r"CURRENT_TIMESTAMP \1 INTERVAL '\2 day'", 'SYSDATE ± gün → INTERVAL'),
                    (r'\bSYS(?:DATE|TIMESTAMP)\b', 'CURRENT_TIMESTAMP', 'SYSDATE'), (r'\s+FROM\s+DUAL\b', '', 'FROM DUAL'), (r'\bMINUS\b', 'EXCEPT', 'MINUS → EXCEPT'),
                    (r'\bVARCHAR2\b', 'VARCHAR', 'VARCHAR2'), (r'\bNUMBER\b', 'NUMERIC', 'NUMBER → NUMERIC')]}
MANUAL = {'tsql': [(r'@\w+|\bDECLARE\b', 'değişken', 'T-SQL değişkeni: PL/pgSQL (DO bloğu/fonksiyon) veya sorgu parametresine çevrilmeli'),
                   (r'(?<![\w"])#\w+', 'geçici tablo', 'Geçici tablo (#ad): CREATE TEMP TABLE … AS ile yeniden yazılmalı'),
                   (r'\bEXEC(?:UTE)?\b', 'EXEC', 'Saklı yordam çağrısı: CALL veya SELECT fonksiyon() kullanılmalı'),
                   (r'\b(?:CROSS|OUTER)\s+APPLY\b', 'APPLY', 'APPLY: CROSS/LEFT JOIN LATERAL ile yazılmalı'), (r'\bPIVOT\b', 'PIVOT', 'PIVOT: CASE + GROUP BY veya crosstab()'),
                   (r'\bMERGE\b', 'MERGE', 'MERGE: PostgreSQL 15+ destekler; OUTPUT ve eşleşme koşullarını kontrol edin')],
          'oracle': [(r'\bROWNUM\b', 'ROWNUM', 'ROWNUM: LIMIT/OFFSET veya ROW_NUMBER() ile yazılmalı'), (r'\(\+\)', '(+)', 'Oracle (+) dış birleşimi: LEFT/RIGHT JOIN ile yazılmalı'),
                     (r'\bCONNECT\s+BY\b', 'CONNECT BY', 'Hiyerarşik sorgu: WITH RECURSIVE ile yazılmalı'), (r'\bLISTAGG\b', 'LISTAGG', 'LISTAGG: STRING_AGG(x, ayraç ORDER BY …)'),
                     (r'\.NEXTVAL\b|\.CURRVAL\b', 'sequence', 'Sequence: nextval(\'ad\') / currval(\'ad\')'), (r'\bMERGE\b', 'MERGE', 'MERGE: PostgreSQL 15+ destekler; sözdizimini kontrol edin')]}


def mask(sql):
    saved = []

    def keep(m):
        saved.append(m.group(0))
        return f'\x00{len(saved) - 1}\x00'
    return re.sub(r"/\*.*?\*/|--[^\n]*|N?'(?:[^']|'')*'", keep, sql, flags=re.S), saved


@op('sql-lehce-cevir', title='T-SQL veya Oracle sorgularını PostgreSQL’e çevir (taslak)', domain=D, product=P, tag='SQL çeviri', kind='file',
    samples={'tsql_sorgular.sql': 'tsql_sorgular.sql', 'oracle_sorgular.sql': 'oracle_sorgular.sql'},
    args=[arg('--from', dest='src', choices=['tsql', 'oracle'], default='tsql')],
    description='Sorgu ve görünüm betiklerindeki lehçeye özgü yapıları PostgreSQL karşılıklarına çevirir: T-SQL’de TOP→LIMIT, ISNULL, LEN, IIF, CONVERT, DATEADD/DATEDIFF(day), CHARINDEX, GETDATE, NEWID, [ad], NOLOCK ve metin birleştirme +; Oracle’da NVL/NVL2, DECODE→CASE, SYSDATE, FROM DUAL, MINUS, INSTR. Yorum ve metin sabitlerine dokunmaz; otomatik çevrilemeyen yapıları (değişkenler, geçici tablolar, ROWNUM, (+), CONNECT BY…) satır numarasıyla listeler.',
    input='.sql dosyası', output='<ad>_postgres.sql + sonuc.csv (çevrilen ve elle bakılacak yapılar)', command='python main.py --input rapor_sorgulari.sql --from oracle',
    expect={'otomatik': 16, 'elle': 5})
def sql_cevir(args):
    raw = Path(args.input).read_text(encoding='utf-8-sig', errors='replace')
    sql, saved = mask(raw)
    stats, manual = Counter(), []
    if args.src == 'tsql':
        sql, n = re.subn(r"(\x00\d+\x00)\s*\+(?!\s*\d)|(?<![\d\s])(\s*)\+\s*(?=\x00\d+\x00)", lambda m: (m.group(1) + ' ||') if m.group(1) else ' || ', sql)
        stats['metin birleştirme + → ||'] += n
        parts = []
        for stmt in sql.split(';'):
            m = re.search(r'\bSELECT\s+(DISTINCT\s+)?TOP\s*\(?\s*(\d+)\s*\)?\s+', stmt, re.I)
            if m and stmt[:m.start()].upper().count('SELECT') == 0:
                stmt = stmt[:m.start()] + 'SELECT ' + (m.group(1) or '') + stmt[m.end():].rstrip() + f'\nLIMIT {m.group(2)}'
                stats['TOP n → LIMIT n'] += 1
            elif m:
                manual.append(('TOP', 'alt sorguda TOP: LIMIT ile elle taşınmalı'))
            parts.append(stmt)
        sql = ';'.join(parts)
    for rx, new, label in WORDS[args.src]:
        sql, n = re.subn(rx, new, sql, flags=re.I)
        stats[label] += n
    for name, fn in FUNCS[args.src]:
        sql = rewrite(sql, name, fn, stats, manual)
    sql = re.sub(r'\x00(\d+)\x00', lambda m: saved[int(m.group(1))][1:] if saved[int(m.group(1))].startswith("N'") else saved[int(m.group(1))], sql)
    rows = [{'tur': 'otomatik', 'kural': k, 'adet': v, 'satir': '', 'aciklama': ''} for k, v in stats.items() if v]
    rows += [{'tur': 'elle', 'kural': name, 'adet': 1, 'satir': '', 'aciklama': f'{text} — otomatik çevrilemedi'} for name, text in manual]
    plain = re.sub(r"/\*.*?\*/|--[^\n]*|'(?:[^']|'')*'", lambda m: "''" if m.group(0).startswith("'") else '\n' * m.group(0).count('\n'), raw, flags=re.S)
    for no, line in enumerate(plain.splitlines(), 1):
        for rx, label, msg in MANUAL[args.src]:
            if re.search(rx, line, re.I):
                rows.append({'tur': 'elle', 'kural': label, 'adet': 1, 'satir': no, 'aciklama': msg})
    auto = sum(r['adet'] for r in rows if r['tur'] == 'otomatik')
    hand = sum(r['tur'] == 'elle' for r in rows)
    name = f'{Path(args.input).stem}_postgres.sql'
    header = f'-- {Path(args.input).name} dosyasından {args.src} → PostgreSQL taslak çeviri. {hand} yapı elle gözden geçirilmeli (sonuc.csv).\n'
    return Result(f'{auto} yapı otomatik çevrildi, {hand} yapı elle bakılmalı', ['tur', 'kural', 'adet', 'satir', 'aciklama'], rows,
                  {'otomatik': auto, 'elle': hand}, files={name: header + sql},
                  notes=['LEN sondaki boşlukları saymaz, LENGTH sayar; SYSDATE saniye hassasiyetindedir. Çeviriyi örnek verilerle iki sistemde çalıştırıp sonuçları karşılaştırın.'])


@op('gecis-dalga-plani', title='Tablo bağımlılıklarına ve boyutuna göre geçiş dalgaları planla', domain=D, product=P, tag='dalga planı', samples={'gecis_tablolari.csv': 'gecis_tablolari.csv'},
    args=[arg('--table', default='tablo'), arg('--size', default='boyut_gb'), arg('--deps', default='bagimliliklar', help='Virgülle ayrılmış üst (FK) tablolar'),
          arg('--max-gb', type=float, default=500.0, help='Bir dalgada taşınacak en fazla veri')],
    description='Tabloları yabancı anahtar bağımlılıklarına göre sıralar (üst tablo önce veya aynı dalgada) ve her dalganın veri hacmini sınırlayarak geçiş dalgalarına böler. Döngüsel bağımlılıkları birlikte taşınacak grup olarak, listede olmayan bağımlılıkları ve kapasiteyi tek başına aşan tabloları işaretler; cutover planı ve kesinti penceresi tahmini için.',
    input='Tablo listesi (tablo, boyut, bağımlılıklar)', output='sonuc.csv (dalga ve sıra) + dalga özeti', command='python main.py --input envanter.xlsx --max-gb 300',
    expect={'tablo': 13, 'dalga': 3, 'dongu_tablo': 2, 'bilinmeyen_bagimlilik': 1, 'kapasite_asan_dalga': 1})
def dalga(columns, rows, args):
    tcol, scol, dcol = need(args.table, columns, '--table'), need(args.size, columns, '--size'), need(args.deps, columns, '--deps')
    sizes = {r[tcol].strip(): (n or 0.0) for r, n in zip(rows, numbers(rows, scol)) if r[tcol].strip()}
    deps = {r[tcol].strip(): [d.strip() for d in re.split(r'[,;]', r[dcol]) if d.strip()] for r in rows if r[tcol].strip()}
    unknown = sorted({d for ds in deps.values() for d in ds if d not in sizes})
    parents = {t: [d for d in ds if d in sizes and d != t] for t, ds in deps.items()}
    children = defaultdict(list)
    for t, ps in parents.items():
        for p_ in ps:
            children[p_].append(t)
    indeg = {t: len(ps) for t, ps in parents.items()}
    heap = [t for t, n in indeg.items() if n == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        t = heapq.heappop(heap)
        order.append([t])
        for c in children[t]:
            indeg[c] -= 1
            if not indeg[c]:
                heapq.heappush(heap, c)
    cyclic = sorted(t for t, n in indeg.items() if n)
    if cyclic:
        order.append(cyclic)
    wave, load, seq, out = {}, defaultdict(float), defaultdict(int), []
    for unit in order:
        size = sum(sizes[t] for t in unit)
        w = max([wave[p_] for t in unit for p_ in parents[t] if p_ in wave] or [1])
        while load[w] and load[w] + size > args.max_gb:
            w += 1
        load[w] += size
        for t in unit:
            wave[t] = w
            seq[w] += 1
            note = []
            if t in cyclic:
                note.append('döngüsel bağımlılık: grupla birlikte taşınmalı, FK’ler yükleme sonrası etkinleştirilmeli')
            if any(d in unknown for d in deps[t]):
                note.append('listede olmayan bağımlılık: ' + ', '.join(d for d in deps[t] if d in unknown))
            if sizes[t] > args.max_gb:
                note.append('tek başına dalga kapasitesini aşıyor')
            out.append({'dalga': w, 'sira': seq[w], 'tablo': t, 'boyut_gb': sizes[t], 'bagimliliklar': ', '.join(deps[t]), 'not': '; '.join(note)})
    out.sort(key=lambda o: (o['dalga'], o['sira']))
    summary_rows = [[w, seq[w], round(load[w], 2), 'kapasite aşıldı' if load[w] > args.max_gb else ''] for w in sorted(load)]
    return Result(f'{len(out)} tablo {len(load)} dalgada; {len(cyclic)} tablo döngüde', ['dalga', 'sira', 'tablo', 'boyut_gb', 'bagimliliklar', 'not'], out,
                  {'tablo': len(out), 'dalga': len(load), 'dongu_tablo': len(cyclic), 'bilinmeyen_bagimlilik': len(unknown), 'kapasite_asan_dalga': sum(load[w] > args.max_gb for w in load)},
                  tables=[('Dalga özeti', ['Dalga', 'Tablo', 'Toplam GB', 'Not'], summary_rows)])
