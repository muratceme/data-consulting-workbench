"""Veri migrasyonu blokları: kaynak–hedef alan eşleme kontrolü, tablo satır sayısı mutabakatı, kodlama ve uzunluk riski. Hiçbir veritabanına bağlanmaz."""
import re
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
