"""Veri migrasyonu blokları: kaynak–hedef alan eşleme kontrolü, tablo satır sayısı mutabakatı, kodlama ve uzunluk riski, sütun profili karşılaştırması,
SQL sorgularını PostgreSQL’e çevirme ve geçiş dalgası planı. Hiçbir veritabanına bağlanmaz."""
import heapq, re
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
import tabular
from sema_farki import load as load_ddl, narrowing, base_type
from islem import op, arg, Result, need, numbers, fmt, fold, col_list

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


def comparable(rows, col):
    """Per-row canonical values: numbers with the column's decimal style, dates as ISO, text stripped."""
    vals = [r[col].strip() for r in rows]
    nums = numbers(rows, col)
    if any(vals) and all(n is not None for v, n in zip(vals, nums) if v):
        return [repr(round(n, 9)) if n is not None else '' for n in nums]
    if any(vals) and all(tabular.parse_date(v) for v in vals if v):
        return [tabular.parse_date(v)[0].isoformat() if v else '' for v in vals]
    return vals


@op('anahtar-mutabakati', title='Kaynak ve hedefi anahtara göre satır satır karşılaştır', domain=D, product=P, tag='satır mutabakatı',
    samples={'kaynak_siparis.csv': 'kaynak_siparis.csv', 'hedef_siparis.csv': 'hedef_siparis.csv'},
    args=[arg('--target', default='@ornek/hedef_siparis.csv'), arg('--key', default='id', help='Anahtar sütun(lar), virgülle'), arg('--ignore-case', action='store_true')],
    description='Kaynak ve hedef dışa aktarımlarını anahtar sütun(lar)la eşleştirip hedefte eksik, hedefte fazla ve değeri değişmiş satırları, değişen sütunları eski/yeni değerleriyle listeler. Ondalık ayırıcı (100,50 / 100.5) ve tarih biçimi (01.10.2026 / 2026-10-01) farkını değişiklik saymaz; sütun adlarını büyük/küçük harf farkı gözetmeden eşler.',
    input='İki CSV/XLSX (kaynak ve hedef) + anahtar', output='sonuc.csv (farklar) + sütun bazında fark sayısı', command='python main.py --input kaynak.csv --target hedef.csv --key siparis_no,satir_no',
    expect={'eksik': 1, 'fazla': 1, 'degisen': 2, 'ayni': 2})
def key_recon(columns, rows, args):
    tcols, trows = tabular.read(args.target)
    tmap = {c.strip().lower(): c for c in tcols}
    keys = [k.strip() for k in args.key.split(',') if k.strip()]
    for k in keys:
        need(k, columns, '--key')
        if k.lower() not in tmap:
            raise ValueError(f'--key: "{k}" hedef dosyada yok.')
    common = [c for c in columns if c.lower() in tmap and c not in keys]
    sv = {c: comparable(rows, c) for c in keys + common}
    tv = {c: comparable(trows, tmap[c.lower()]) for c in keys + common}
    norm = (lambda v: fold(v)) if args.ignore_case else (lambda v: v)
    src = {tuple(sv[k][i] for k in keys): i for i in range(len(rows))}
    tgt = {tuple(tv[k][i] for k in keys): i for i in range(len(trows))}
    out, per_col, same = [], Counter(), 0
    for key in sorted(set(src) | set(tgt)):
        label = ', '.join(rows[src[key]][k] if key in src else trows[tgt[key]][tmap[k.lower()]] for k in keys)
        if key not in tgt:
            out.append({'anahtar': label, 'durum': 'HEDEFTE EKSİK', 'sutun': '', 'kaynak': '', 'hedef': ''})
            continue
        if key not in src:
            out.append({'anahtar': label, 'durum': 'hedefte fazla', 'sutun': '', 'kaynak': '', 'hedef': ''})
            continue
        i, j = src[key], tgt[key]
        diffs = [c for c in common if norm(sv[c][i]) != norm(tv[c][j])]
        if not diffs:
            same += 1
        for c in diffs:
            per_col[c] += 1
            out.append({'anahtar': label, 'durum': 'değişti', 'sutun': c, 'kaynak': rows[i][c], 'hedef': trows[j][tmap[c.lower()]]})
    changed = len({o['anahtar'] for o in out if o['durum'] == 'değişti'})
    missing = sum(o['durum'] == 'HEDEFTE EKSİK' for o in out)
    extra = sum(o['durum'] == 'hedefte fazla' for o in out)
    return Result(f'{len(rows)} → {len(trows)} satır; {same} aynı, {changed} değişmiş, {missing} hedefte eksik, {extra} hedefte fazla', ['anahtar', 'durum', 'sutun', 'kaynak', 'hedef'], out,
                  {'kaynak_satir': len(rows), 'hedef_satir': len(trows), 'ayni': same, 'degisen': changed, 'eksik': missing, 'fazla': extra},
                  tables=[('Sütun bazında değişiklik', ['Sütun', 'Satır'], per_col.most_common())])


@op('sequence-kontrol', title='Geçiş sonrası identity/sequence çakışma kontrolü', domain=D, product=P, tag='sequence',
    samples={'sequence_durumu.csv': 'sequence_durumu.csv'}, args=[arg('--dialect', choices=['postgres', 'sqlserver', 'oracle'], default='postgres')],
    description='Veri taşındıktan sonra her tablonun en büyük kimlik değerini hedefteki sequence/identity’nin vereceği sonraki değerle karşılaştırır; sonraki değer mevcut en büyük kimliğe eşit veya küçükse ilk yeni kayıtta birincil anahtar çakışması olacağını işaretler ve seçilen veritabanı için düzeltme komutunu (setval, RESEED, RESTART) yazar.',
    input='Tablo listesi (tablo, sequence, kaynak_max_id, hedef_sonraki_deger)', output='sonuc.csv + duzeltme.sql', command='python main.py --input seq_check.csv --dialect sqlserver',
    expect={'tablo': 5, 'cakisma': 2, 'bilinmeyen': 1})
def sequences(columns, rows, args):
    for c in ('tablo', 'kaynak_max_id', 'hedef_sonraki_deger'):
        need(c, columns, 'sequence listesi')
    mx, nxt = numbers(rows, 'kaynak_max_id'), numbers(rows, 'hedef_sonraki_deger')
    out, sql = [], []
    for r, m, n in zip(rows, mx, nxt):
        seq = r.get('sequence', '').strip() or f"{r['tablo'].strip()}_id_seq"
        if m is None or n is None:
            state = 'bilinmiyor'
        elif n <= m:
            state = 'ÇAKIŞMA'
            target = int(m) + 1
            sql.append({'postgres': f"SELECT setval('{seq}', {int(m)}, true);  -- {r['tablo']}: sonraki değer {target}",
                        'sqlserver': f"DBCC CHECKIDENT ('{r['tablo'].strip()}', RESEED, {int(m)});  -- sonraki değer {target}",
                        'oracle': f'ALTER SEQUENCE {seq} RESTART START WITH {target};'}[args.dialect])
        else:
            state = 'uygun'
        out.append({'tablo': r['tablo'], 'sequence': seq, 'kaynak_max_id': fmt(m), 'hedef_sonraki_deger': fmt(n), 'pay': fmt(n - m) if m is not None and n is not None else '', 'durum': state})
    out.sort(key=lambda o: (o['durum'] != 'ÇAKIŞMA', o['durum'] != 'bilinmiyor', o['tablo']))
    c = Counter(o['durum'] for o in out)
    return Result(f'{len(out)} tablo; {c["ÇAKIŞMA"]} çakışma, {c["bilinmiyor"]} bilinmiyor', list(out[0]), out,
                  {'tablo': len(out), 'cakisma': c['ÇAKIŞMA'], 'bilinmeyen': c['bilinmiyor']},
                  files={'duzeltme.sql': f'-- {args.dialect}: sequence/identity düzeltmeleri. Çalıştırmadan önce gözden geçirin.\n' + '\n'.join(sql) + '\n'} if sql else {},
                  notes=['SQL Server identity için RESEED değeri son kullanılan değerdir; sonraki kayıt bir fazlasını alır. Sequence nesnelerinde ALTER SEQUENCE … RESTART WITH kullanın.'])


INT_RANGE = {'tinyint': (0, 255), 'smallint': (-32768, 32767), 'int': (-2 ** 31, 2 ** 31 - 1), 'integer': (-2 ** 31, 2 ** 31 - 1), 'bigint': (-2 ** 63, 2 ** 63 - 1)}


def cast_issue(value, base, params, n=None):
    """('hata'|'uyari', reason) when value cannot be stored in the target type, otherwise None."""
    v = value.strip()
    if base in INT_RANGE:
        if not re.fullmatch(r'[+-]?\d+', v):
            return 'hata', f'tam sayı değil ({base})'
        lo, hi = INT_RANGE[base]
        return ('hata', f'{base} aralığı dışında') if not lo <= int(v) <= hi else None
    if base in ('decimal', 'numeric', 'number'):
        n = n if n is not None else tabular.parse_number(v)
        if n is None:
            return 'hata', 'sayı değil'
        p_, s_ = (int(params[0]), int(params[1]) if len(params) > 1 else 0) if params and params[0].isdigit() else (38, 0)
        whole = len(str(int(abs(n)))) if abs(n) >= 1 else 0
        if whole > p_ - s_:
            return 'hata', f'{base}({p_},{s_}) için tam kısım çok büyük'
        frac = re.split(r'[.,]', v)[-1] if re.search(r'[.,]\d+$', v) else ''
        return ('uyari', f'{len(frac)} ondalık > {s_}: yuvarlanır') if len(frac) > s_ else None
    if base in ('date', 'datetime', 'datetime2', 'timestamp', 'smalldatetime'):
        return None if tabular.parse_date(v) else ('hata', 'geçersiz tarih')
    if base in ('bit', 'boolean', 'bool'):
        return None if v.lower() in ('0', '1', 'true', 'false') else ('hata', 'mantıksal değer değil (0/1/true/false)')
    if base in ('varchar', 'nvarchar', 'char', 'nchar', 'varchar2', 'character varying') and params and params[0].isdigit():
        return ('hata', f'{len(v)} karakter > {params[0]}') if len(v) > int(params[0]) else None
    return None


@op('veri-tipi-uyumu', title='Taşınacak değerlerin hedef sütun tiplerine sığıp sığmadığını kontrol et', domain=D, product=P, tag='tip uyumu',
    samples={'gecis_siparis.csv': 'gecis_siparis.csv', 'hedef_siparis_ddl.sql': 'hedef_siparis_ddl.sql'},
    args=[arg('--target-ddl', default='@ornek/hedef_siparis_ddl.sql'), arg('--table', default='satis.siparis')],
    description='Yükleme öncesi her değeri hedef DDL’deki sütun tipine göre dener: tam sayı aralığı (tinyint/smallint/int/bigint taşması), DECIMAL(p,s) tam kısım taşması ve ondalık yuvarlama, geçersiz tarih, mantıksal değer, metin uzunluğu ve NOT NULL sütunda boş değer. Yükleme işini yarıda kesecek veya sessizce yuvarlanacak satırları satır numarasıyla bulur.',
    input='Taşınacak veri (CSV/XLSX) + hedef DDL + tablo', output='sonuc.csv (sorunlu değerler)', command='python main.py --input siparis_extract.csv --target-ddl hedef.sql --table dbo.siparis',
    expect={'hata': 6, 'uyari': 1, 'hatali_satir': 5, 'kontrol_edilen_sutun': 6})
def type_fit(columns, rows, args):
    ddl = load_ddl(args.target_ddl)
    table = next((t for t in ddl if t.lower() == args.table.lower()), None)
    if not table:
        raise ValueError(f'"{args.table}" hedef DDL’de yok. Tablolar: {", ".join(ddl)}')
    cols = {c['name'].lower(): c for c in ddl[table]}
    checked = [c for c in columns if c.lower() in cols]
    nums = {c: numbers(rows, c) for c in checked}
    out = []
    for i, r in enumerate(rows, 2):
        for c in checked:
            spec = cols[c.lower()]
            v = r[c]
            if not v.strip():
                if not spec['nullable']:
                    out.append({'satir': i, 'sutun': c, 'hedef_tip': spec['type'], 'deger': '', 'tur': 'hata', 'sorun': 'NOT NULL sütunda boş değer'})
                continue
            base, params = base_type(spec['type'])
            issue = cast_issue(v, base, params, nums[c][i - 2])
            if issue:
                out.append({'satir': i, 'sutun': c, 'hedef_tip': spec['type'], 'deger': v[:40], 'tur': issue[0], 'sorun': issue[1]})
    errs = [o for o in out if o['tur'] == 'hata']
    return Result(f'{len(checked)} sütun kontrol edildi; {len(errs)} hata, {len(out) - len(errs)} uyarı, {len({o["satir"] for o in errs})} satır yüklenemez', list(out[0]) if out else ['satir'], out,
                  {'hata': len(errs), 'uyari': len(out) - len(errs), 'hatali_satir': len({o['satir'] for o in errs}), 'kontrol_edilen_sutun': len(checked),
                   'eslesmeyen_sutun': [c for c in columns if c.lower() not in cols]},
                  tables=[('Sütun bazında', ['Sütun', 'Sorun'], Counter(o['sutun'] for o in out).most_common())])


@op('gecis-sure-tahmini', title='Dalga bazında veri taşıma süresi ve kesinti penceresi tahmini', domain=D, product=P, tag='süre tahmini', samples={'gecis_dalgalari.csv': 'gecis_dalgalari.csv'},
    args=[arg('--throughput', type=float, default=100.0, help='Tek akış hızı (GB/saat)'), arg('--parallel', type=int, default=4, help='Paralel akış sayısı'),
          arg('--window', type=float, default=6.0, help='Dalga başına kesinti penceresi (saat)'), arg('--wave', default='dalga'), arg('--size', default='boyut_gb')],
    description='Dalga planındaki tabloların boyutundan, ölçülen tek akış hızından ve paralel akış sayısından her dalganın yükleme süresini tahmin eder: toplam hacim/paralel kapasite ile en büyük tablonun tek başına süresinden büyük olanı alır. Kesinti penceresini aşan dalgaları ve süreyi belirleyen tabloyu işaretler.',
    input='Dalga planı (tablo, boyut GB, dalga)', output='sonuc.csv (dalga bazında süre)', command='python main.py --input dalga_plani.csv --throughput 150 --parallel 8 --window 4',
    expect={'dalga': 3, 'toplam_saat': 13.0, 'pencere_asan': 1})
def duration(columns, rows, args):
    wcol, scol = need(args.wave, columns, '--wave'), need(args.size, columns, '--size')
    tcol = 'tablo' if 'tablo' in columns else columns[0]
    waves = defaultdict(list)
    for r, s in zip(rows, numbers(rows, scol)):
        waves[r[wcol].strip()].append((r[tcol], s or 0.0))
    out = []
    for w, items in sorted(waves.items(), key=lambda kv: (len(kv[0]), kv[0])):
        total = sum(s for _, s in items)
        big = max(items, key=lambda x: x[1])
        hours = max(total / (args.throughput * args.parallel), big[1] / args.throughput)
        limit = 'en büyük tablo' if big[1] / args.throughput >= total / (args.throughput * args.parallel) else 'toplam hacim'
        out.append({'dalga': w, 'tablo': len(items), 'toplam_gb': round(total, 2), 'en_buyuk_tablo': big[0], 'tahmini_saat': round(hours, 2), 'belirleyen': limit,
                    'durum': 'PENCEREYİ AŞIYOR' if hours > args.window else 'sığıyor'})
    return Result(f'{len(out)} dalga, toplam ≈{sum(o["tahmini_saat"] for o in out):.1f} saat; {sum(o["durum"] != "sığıyor" for o in out)} dalga {fmt(args.window)} saatlik pencereyi aşıyor', list(out[0]), out,
                  {'dalga': len(out), 'toplam_saat': round(sum(o['tahmini_saat'] for o in out), 2), 'pencere_asan': sum(o['durum'] != 'sığıyor' for o in out)},
                  notes=['Hız, gerçek ortamda örnek bir tabloyla ölçülmelidir; indeks/kısıt oluşturma ve doğrulama süreleri bu tahmine dahil değildir. En büyük tablo belirleyiciyse bölümlü (paralel) taşımayı değerlendirin.'])


@op('saat-dilimi-kaymasi', title='Kaynak ve hedef zaman damgalarında saat dilimi / sabit kayma tespiti', domain=D, product=P, tag='saat dilimi',
    samples={'kaynak_zaman.csv': 'kaynak_zaman.csv', 'hedef_zaman.csv': 'hedef_zaman.csv'},
    args=[arg('--target', default='@ornek/hedef_zaman.csv', help='Hedef dışa aktarım'), arg('--key', default='siparis_no'), arg('--columns', default='olusturma,guncelleme', help='Karşılaştırılacak zaman sütunları'),
          arg('--tolerance', type=float, default=1, help='Bu kadar saniyelik fark eşit sayılır')],
    description='Geçiş sonrası kaynak ve hedefteki zaman damgası sütunlarını anahtarla eşleştirip her satırda farkı saniye cinsinden hesaplar; farkların çoğunun aynı sabit değer olduğunu (ör. +3 saat: UTC ↔ Türkiye saati, ya da tam gün kayması) tespit eder, bu kaymaya uymayan satırları ve yaz/kış saati geçişine denk gelenleri ayrı listeler. Sütun bazında baskın kaymayı ve etkilenen satır oranını verir.',
    input='Kaynak ve hedef dışa aktarım (anahtar + zaman sütunları)', output='sonuc.csv (sütun bazında kayma) + farklı satırlar', command='python main.py --input erp.csv --target dwh.csv --key id --columns created_at,updated_at',
    expect={'eslesen': 10, 'sutun': 2, 'kaymali_sutun': 1, 'baskin_kayma_saat': 3.0, 'kaymaya_uymayan': 1})
def tz_shift(columns, rows, args):
    k = need(args.key, columns, '--key')
    cols = col_list(args.columns, columns, '--columns') if args.columns else []
    tcols, trows = tabular.read(args.target)
    need(args.key, tcols, '--target --key')
    for c in cols:
        need(c, tcols, f'--target {c}')
    target = {r[k].strip(): r for r in trows if r[k].strip()}
    pairs = [(r, target[r[k].strip()]) for r in rows if r[k].strip() in target]
    if not pairs:
        raise ValueError('Kaynak ve hedefte ortak anahtar yok.')
    out, odd, worst = [], [], 0.0
    for c in cols:
        diffs = []
        for a, b in pairs:
            da, db = tabular.parse_date(a[c]), tabular.parse_date(b[c])
            if da and db:
                diffs.append((a[k].strip(), round((db[0] - da[0]).total_seconds())))
        if not diffs:
            continue
        mode, n = Counter(d for _, d in diffs).most_common(1)[0]
        same = sum(abs(d) <= args.tolerance for _, d in diffs)
        shifted = abs(mode) > args.tolerance
        for key, d in diffs:
            if abs(d - mode) > args.tolerance and abs(d) > args.tolerance:
                odd.append({'sutun': c, 'anahtar': key, 'fark_sn': d, 'not': f'baskın kayma {mode} sn' if shifted else 'tekil fark'})
        out.append({'sutun': c, 'eslesen': len(diffs), 'esit': same, 'baskin_kayma_sn': mode, 'baskin_kayma_saat': round(mode / 3600, 2), 'kaymali_satir': n if shifted else len(diffs) - same,
                    'durum': f'sabit kayma: {fmt(round(mode / 3600, 2))} saat — saat dilimi dönüşümünü kontrol edin' if shifted else ('uygun' if same == len(diffs) else 'tekil farklar var')})
        if shifted:
            worst = mode / 3600
    out.sort(key=lambda o: -abs(o['baskin_kayma_sn']))
    return Result(f'{len(pairs)} eşleşen satır, {len(out)} zaman sütunu; {sum(o["durum"].startswith("sabit") for o in out)} sütunda sabit kayma', list(out[0]), out,
                  {'eslesen': len(pairs), 'sutun': len(out), 'kaymali_sutun': sum(o['durum'].startswith('sabit') for o in out), 'baskin_kayma_saat': round(worst, 2),
                   'kaymaya_uymayan': len(odd)},
                  files={'farkli_satirlar.csv': (['sutun', 'anahtar', 'fark_sn', 'not'], odd)},
                  notes=['+3 saat genelde kaynağın Türkiye saatiyle, hedefin UTC ile yazılmasından (veya tersi) gelir; tam gün kaymaları DATE ↔ TIMESTAMP dönüşümüne işaret eder.'])


@op('bos-deger-donusumu', title='Geçişte boş metin, NULL ve boşluk değerlerinin dönüşümünü sütun bazında say', domain=D, product=P, tag='NULL dönüşümü',
    samples={'kaynak_bosluk.csv': 'kaynak_bosluk.csv', 'hedef_bosluk.csv': 'hedef_bosluk.csv'},
    args=[arg('--target', default='@ornek/hedef_bosluk.csv'), arg('--key', default='musteri_no'),
          arg('--null-tokens', default='NULL,null,\\N,None', help='NULL olarak dışa aktarılan metinler (virgülle)')],
    description='Kaynak ve hedef dışa aktarımını anahtarla eşleştirip her sütunda boş metin (\'\'), NULL ve yalnızca boşluk değerlerinin nasıl dönüştüğünü sayar: boş → NULL, NULL → boş, boşluk → boş (kırpma), dolu → boş (veri kaybı) ve boş → dolu (varsayılan değer). Oracle’ın boş metni NULL sayması, CSV aktarımında NULL’un “NULL” metnine dönmesi gibi sorunları erken yakalamak için.',
    input='Kaynak ve hedef dışa aktarım (aynı sütunlar)', output='sonuc.csv (sütun bazında dönüşüm sayıları)', command='python main.py --input oracle.csv --target postgres.csv --key id',
    expect={'eslesen': 8, 'sutun': 3, 'bos_null': 2, 'null_bos': 1, 'kirpma': 1, 'veri_kaybi': 1, 'varsayilan': 1})
def null_mapping(columns, rows, args):
    k = need(args.key, columns, '--key')
    tcols, trows = tabular.read(args.target)
    need(args.key, tcols, '--target --key')
    tokens = {t.strip() for t in args.null_tokens.split(',') if t.strip()}
    target = {r[k].strip(): r for r in trows if r[k].strip()}
    pairs = [(r, target[r[k].strip()]) for r in rows if r[k].strip() in target]
    cols = [c for c in columns if c != k and c in tcols]

    def kind(v):
        return 'null' if v in tokens else 'bos' if v == '' else 'bosluk' if not v.strip() else 'dolu'
    out, totals = [], Counter()
    names = {('bos', 'null'): 'bos_null', ('null', 'bos'): 'null_bos', ('bosluk', 'bos'): 'kirpma', ('bosluk', 'null'): 'kirpma'}
    for c in cols:
        cnt = Counter()
        for a, b in pairs:
            ka, kb = kind(a[c]), kind(b[c])
            if ka == kb:
                continue
            if ka == 'dolu':
                cnt['veri_kaybi'] += kb != 'dolu'
            elif kb == 'dolu':
                cnt['varsayilan'] += 1
            else:
                cnt[names.get((ka, kb), f'{ka}_{kb}')] += 1
        totals.update(cnt)
        out.append({'sutun': c, **{x: cnt[x] for x in ('bos_null', 'null_bos', 'kirpma', 'veri_kaybi', 'varsayilan')}, 'toplam_degisim': sum(cnt.values())})
    out.sort(key=lambda o: (-o['veri_kaybi'], -o['toplam_degisim'], o['sutun']))
    return Result(f'{len(pairs)} eşleşen satır, {len(cols)} sütun; {sum(o["toplam_degisim"] for o in out)} boş/NULL dönüşümü, {totals["veri_kaybi"]} veri kaybı', list(out[0]), out,
                  {'eslesen': len(pairs), 'sutun': len(cols), **{x: totals[x] for x in ('bos_null', 'null_bos', 'kirpma', 'veri_kaybi', 'varsayilan')}},
                  notes=['Dolu değerin boşa dönmesi (veri kaybı) her zaman incelenmelidir. Boş ↔ NULL dönüşümü raporlarda COUNT ve WHERE sonuçlarını değiştirir; hedef sistemin kuralını belirleyip tek tipe çevirin.'])


MOJIBAKE = re.compile(r'Ã[\x80-\xbf¡-¿]|Ä[±°\x9f\x9e]|Å[\x9f\x9eŸŠžŽ]|Ã¶|Ã§|Ã¼|Ä±|Ä°|ÅŸ|Åž|ÄŸ|Äž|[ÝÞðýþ]|�')


def repair(v):
    """Undo the two common Turkish mis-decodings; return '' when no clean repair exists."""
    for wrong, right in (('cp1252', 'utf-8'), ('latin-1', 'utf-8'), ('latin-1', 'cp1254')):
        try:
            fixed = v.encode(wrong).decode(right)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != v and not MOJIBAKE.search(fixed):
            return fixed
    return ''


@op('mojibake-tespiti', title='Geçiş sonrası bozuk Türkçe karakterleri (mojibake) bul ve onarım öner', domain=D, product=P, tag='karakter bozulması',
    samples={'hedef_musteri_kodlama.csv': 'hedef_musteri_kodlama.csv'}, args=[arg('--columns', default='', help='Taranacak sütunlar (boşsa tüm metin sütunları)')],
    description='Hedef sistemden alınan dışa aktarımda UTF-8 metnin Windows-1252/Latin-1 sanılarak okunmasıyla oluşan (AyÅŸe, YÄ±lmaz) ve Windows-1254 metnin Latin-1 sanılmasıyla oluşan (ÞÝ, ýð) bozulmaları ve değiştirme karakterini (�) bulur; sütun bazında etkilenen satır sayısını ve geri çevrilebilen değerler için onarılmış hâlini verir. Karakter kümesi ayarı yanlış yapılmış ETL/geçişleri yakalamak için.',
    input='Hedef dışa aktarım (CSV/XLSX)', output='sonuc.csv (bozuk değerler ve onarım önerisi) + sütun özeti', command='python main.py --input hedef_musteri.csv --columns ad_soyad,adres',
    expect={'satir': 8, 'bozuk_deger': 4, 'onarilabilen': 3, 'sutun': 2})
def mojibake(columns, rows, args):
    cols = col_list(args.columns, columns, '--columns') if args.columns else columns
    out, per_col = [], Counter()
    for i, r in enumerate(rows, start=2):
        for c in cols:
            v = r[c]
            if v and MOJIBAKE.search(v):
                fixed = repair(v)
                per_col[c] += 1
                out.append({'satir': i, 'sutun': c, 'deger': v[:60], 'onarim': fixed[:60], 'durum': 'onarılabilir' if fixed else 'elle düzeltilmeli'})
    return Result(f'{len(rows)} satırda {len(out)} bozuk değer ({sum(bool(o["onarim"]) for o in out)} tanesi otomatik onarılabilir)', ['satir', 'sutun', 'deger', 'onarim', 'durum'], out,
                  {'satir': len(rows), 'bozuk_deger': len(out), 'onarilabilen': sum(bool(o['onarim']) for o in out), 'sutun': len(per_col)},
                  tables=[('Sütun bazında', ['Sütun', 'Bozuk değer'], per_col.most_common())],
                  notes=['Onarım önerisi yalnızca tahmindir: ÃŸ gibi diziler UTF-8 metnin yanlış okunduğunu gösterir; asıl çözüm, aktarımın karakter kümesi ayarını (UTF-8) düzeltip veriyi yeniden yüklemektir. “�” içeren değerlerde bilgi kaybolmuştur, kaynaktan yeniden alınmalıdır.'])


@op('sap-sablon-dogrulama', title='SAP Migration Cockpit şablonunu yükleme öncesi alan kurallarıyla doğrula', domain='migration', product='Migration Cockpit', tag='S/4HANA geçiş şablonu',
    samples={'sap_musteri_sablonu.csv': 'sap_musteri_sablonu.csv', 'sap_musteri_alanlari.csv': 'sap_musteri_alanlari.csv'},
    args=[arg('--spec', default='@ornek/sap_musteri_alanlari.csv', help='Alan kuralları (alan, aciklama, zorunlu, tip C/N/D, uzunluk, izinli_degerler, anahtar)')],
    description='Migration Cockpit (Migrate Your Data) şablonunun doldurulmuş sayfasını (CSV/XLSX olarak kaydedilmiş) alan kuralları tablosuyla satır satır denetler: zorunlu alanın boş olması, SAP alan uzunluğunu aşan değerler, sayısal (N) alanda harf, tarih (D) alanında YYYYMMDD dışı biçim, izinli değer listesinde (ülke, ödeme koşulu, mutabakat hesabı gibi kontrol tablosu değerleri) olmayan değerler, anahtar alanda tekrar ve şablonda olup kural tablosunda olmayan sütunlar. Hatasız satır oranını ve alan bazında hata sayısını verir; yükleme simülasyonundan önce hataları kaynakta düzeltmek için.',
    input='Doldurulmuş şablon sayfası + alan kuralları CSV’si', output='sonuc.csv (satır × alan hataları) + alan özeti', command='python main.py --input "Customer - General Data.csv" --spec musteri_alanlari.csv',
    expect={'satir': 12, 'hatasiz_satir': 4, 'hata': 9, 'alan': 12})
def sap_template(columns, rows, args):
    scols, srows = tabular.read(args.spec)
    for c in ('alan', 'zorunlu', 'tip', 'uzunluk'):
        need(c, scols, '--spec')
    yes = lambda v: v.strip().lower() in ('evet', 'x', 'true', '1', 'e', 'yes')
    spec = {r['alan'].strip(): {'req': yes(r['zorunlu']), 'type': r['tip'].strip().upper()[:1], 'len': int(tabular.parse_number(r['uzunluk']) or 0),
                                'allowed': {x.strip() for x in r.get('izinli_degerler', '').split('|') if x.strip()}, 'key': yes(r.get('anahtar', '')),
                                'desc': r.get('aciklama', '').strip()} for r in srows if r['alan'].strip()}
    unknown = [c for c in columns if c not in spec]
    missing_cols = [f for f, s in spec.items() if s['req'] and f not in columns]
    if missing_cols:
        raise ValueError(f'Şablonda zorunlu sütun yok: {", ".join(missing_cols)}')
    out, bad_rows = [], set()
    keys = Counter()
    key_fields = [f for f, s in spec.items() if s['key'] and f in columns]
    for r in rows:
        if key_fields:
            keys[tuple(r[f].strip() for f in key_fields)] += 1
    for i, r in enumerate(rows, 2):
        for f, s in spec.items():
            if f not in columns:
                continue
            v = r[f].strip()
            err = None
            if not v:
                err = 'zorunlu alan boş' if s['req'] else None
            elif s['type'] == 'N' and not re.fullmatch(r'-?\d+([.,]\d+)?', v):
                err = 'sayısal alanda sayı dışı değer'
            elif s['type'] == 'D' and not (re.fullmatch(r'\d{8}', v) and _valid_ymd(v)):
                err = 'tarih YYYYMMDD olmalı'
            elif s['len'] and len(v) > s['len']:
                err = f'uzunluk {len(v)} > {s["len"]}'
            elif s['allowed'] and v not in s['allowed']:
                err = 'izinli değerlerde yok'
            if err:
                out.append({'satir': i, 'alan': f, 'aciklama': s['desc'], 'deger': v, 'hata': err})
                bad_rows.add(i)
        if key_fields and keys[tuple(r[f].strip() for f in key_fields)] > 1:
            out.append({'satir': i, 'alan': '+'.join(key_fields), 'aciklama': 'anahtar', 'deger': '/'.join(r[f].strip() for f in key_fields), 'hata': 'anahtar tekrar ediyor'})
            bad_rows.add(i)
    per_field = Counter(o['alan'] for o in out)
    clean = len(rows) - len(bad_rows)
    notes = ['Kontrol tablosu değerleri (ülke, ödeme koşulu, mutabakat hesabı, şirket kodu) hedef sistemden alınmalıdır; Migration Cockpit simülasyonu ayrıca değer eşleme (mapping) adımında hata verebilir.']
    if unknown:
        notes.append('Kural tablosunda olmayan sütun (denetlenmedi): ' + ', '.join(unknown))
    return Result(f'{len(rows)} satır, {len(spec)} alan kuralı; {clean} satır hatasız (%{100 * clean / (len(rows) or 1):.0f}), {len(out)} hata', ['satir', 'alan', 'aciklama', 'deger', 'hata'], out,
                  {'satir': len(rows), 'hatasiz_satir': clean, 'hata': len(out), 'alan': len(spec)},
                  tables=[('Alan bazında hata', ['Alan', 'Açıklama', 'Hata'], [[f, spec.get(f, {}).get('desc', ''), n] for f, n in per_field.most_common()])], notes=notes)


def _valid_ymd(v):
    try:
        datetime.strptime(v, '%Y%m%d')
        return True
    except ValueError:
        return False
