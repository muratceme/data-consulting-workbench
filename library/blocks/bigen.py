"""BI ve modelleme üreticileri: DAX zaman zekâsı, Power Query M, BigQuery şeması, Qlik LOAD, Power BI RLS, KPI durum tablosu, dbt iskeleti.
Üretilen kod hiçbir platforma yüklenmez; kopyalanıp ilgili araçta gözden geçirilerek kullanılır."""
import json, re
from collections import defaultdict
import tabular
from sqlgen import infer_column, snake, read_sql_files, relationships
from sema_farki import load as load_ddl
from islem import op, arg, Result, need, numbers, fmt, tr_money

REQUIRES = ['sema_farki', 'sqlgen']
PBI = 'Power BI Desktop'
SATIS = {'satislar.csv': 'satislar.csv'}


@op('dax-zaman-zekasi', title='DAX zaman zekâsı ölçülerini üret', domain='bi', product=PBI, tag='DAX', kind='none', samples={},
    args=[arg('--table', default='Satislar'), arg('--column', default='Tutar'), arg('--date-table', default='Tarih'), arg('--date-column', default='Tarih'),
          arg('--name', default='Ciro'), arg('--fiscal-year-end', default='12-31', help='Mali yıl sonu AA-GG (TOTALYTD için)'), arg('--format', default='#,0')],
    description='Bir ölçü için toplam, YTD, QTD, MTD, geçen yıl, geçen yıl YTD, yıllık değişim ve yüzdesi, önceki ay, 3 aylık hareketli ortalama ve kümülatif toplam DAX ölçülerini yazar. Ölçüler DAX sorgu görünümünde “DEFINE MEASURE” ile modele eklenebilir.',
    input='Tablo, sütun, tarih tablosu adı', output='olculer.dax + olculer.csv', command='python main.py --table Satislar --column Tutar --date-table Tarih --name Ciro --fiscal-year-end 03-31',
    expect={'olcu': 11})
def dax(args):
    if not re.fullmatch(r'\d{2}-\d{2}', args.fiscal_year_end):
        raise ValueError('--fiscal-year-end AA-GG biçiminde olmalı, örn. 03-31.')
    t, d, n = f"'{args.table}'", f"'{args.date_table}'[{args.date_column}]", args.name
    base = f'[{n}]'
    m = [(n, f'SUM ( {t}[{args.column}] )', 'Temel toplam'),
         (f'{n} YTD', f'TOTALYTD ( {base}, {d}, "{args.fiscal_year_end}" )', 'Yıl başından bugüne'),
         (f'{n} QTD', f'TOTALQTD ( {base}, {d} )', 'Çeyrek başından bugüne'),
         (f'{n} MTD', f'TOTALMTD ( {base}, {d} )', 'Ay başından bugüne'),
         (f'{n} GY', f'CALCULATE ( {base}, SAMEPERIODLASTYEAR ( {d} ) )', 'Geçen yılın aynı dönemi'),
         (f'{n} GY YTD', f'CALCULATE ( [{n} YTD], SAMEPERIODLASTYEAR ( {d} ) )', 'Geçen yılın YTD değeri'),
         (f'{n} YoY', f'VAR Cari = {base}\nVAR Onceki = [{n} GY]\nRETURN IF ( NOT ISBLANK ( Cari ) && NOT ISBLANK ( Onceki ), Cari - Onceki )', 'Yıllık fark'),
         (f'{n} YoY %', f'DIVIDE ( [{n} YoY], [{n} GY] )', 'Yıllık değişim oranı'),
         (f'{n} Önceki Ay', f'CALCULATE ( {base}, DATEADD ( {d}, -1, MONTH ) )', 'Bir önceki ay'),
         (f'{n} 3A Ort', f'AVERAGEX ( DATESINPERIOD ( {d}, MAX ( {d} ), -3, MONTH ), {base} )', 'Son 3 ayın günlük ortalaması (ay için SUMMARIZE ile uyarlayın)'),
         (f'{n} Kümülatif', f'CALCULATE ( {base}, FILTER ( ALL ( {d} ), {d} <= MAX ( {d} ) ) )', 'Başlangıçtan bugüne')]
    lines = ['DEFINE']
    for name, expr, _ in m:
        body = expr.replace('\n', '\n        ')
        lines.append(f"    MEASURE {t}[{name}] =\n        {body}")
    lines.append('EVALUATE\n    ROW ( "Kontrol", [' + n + '] )')
    rows = [{'olcu': a, 'dax': b.replace('\n', ' '), 'aciklama': c, 'bicim': '0.0%' if a.endswith('%') else args.format} for a, b, c in m]
    return Result(f'{len(m)} DAX ölçüsü üretildi ({n})', ['olcu', 'dax', 'aciklama', 'bicim'], rows, {'olcu': len(m)}, files={'olculer.dax': '\n'.join(lines)},
                  notes=[f'Tarih tablosu {args.date_table} sürekli günler içermeli ve “Tarih tablosu olarak işaretle” ile işaretlenmelidir (tarih-tablosu bloğu üretir).'])


M_TYPES = {'int': 'Int64.Type', 'bigint': 'Int64.Type', 'decimal': 'type number', 'date': 'type date', 'datetime': 'type datetime', 'bool': 'type logical', 'text': 'type text'}


def m_name(c):
    return c if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', c) else '#"' + c.replace('"', '""') + '"'


@op('power-query-m', title='CSV/Excel için Power Query (M) sorgusu üret', domain='bi', product=PBI, tag='Power Query', samples=SATIS,
    args=[arg('--path', default='C:\\Veri\\satislar.csv', help='Power BI’ın okuyacağı dosya yolu'), arg('--culture', default='tr-TR')],
    description='Dosyanın ayırıcısını, kodlamasını ve sütun tiplerini tespit ederek Csv.Document / Excel.Workbook ile okuyan, başlıkları yükselten, boşlukları temizleyen ve tipleri Türkçe kültürle (1.234,56) dönüştüren M sorgusunu yazar. Gelişmiş Düzenleyici’ye yapıştırılır.',
    input='CSV/XLSX', output='sorgu.m', command='python main.py --input satis.csv --path "\\\\sunucu\\paylasim\\satis.csv"',
    expect={'sutun': 11})
def power_query(columns, rows, args):
    from pathlib import Path
    src = Path(args.input)
    kinds = {c: infer_column([r[c] for r in rows])[0] for c in columns}
    path = args.path.replace('"', '""')
    if src.suffix.lower() in ('.xlsx', '.xlsm'):
        sheet = (args.sheet or 'Sayfa1').replace('"', '""')
        steps = [f'Kaynak = Excel.Workbook(File.Contents("{path}"), null, true)', f'Sayfa = Kaynak{{[Item="{sheet}",Kind="Sheet"]}}[Data]',
                 'Basliklar = Table.PromoteHeaders(Sayfa, [PromoteAllScalars=true])']
    else:
        raw = src.read_text(encoding='utf-8-sig', errors='replace')[:20000]
        delim = max([';', ',', '\t', '|'], key=raw.count)
        steps = [f'Kaynak = Csv.Document(File.Contents("{path}"), [Delimiter="{"#(tab)" if delim == chr(9) else delim}", Encoding=65001, QuoteStyle=QuoteStyle.Csv])',
                 'Basliklar = Table.PromoteHeaders(Kaynak, [PromoteAllScalars=true])']
    text_cols = [c for c in columns if kinds[c] == 'text']
    if text_cols:
        steps.append('Temiz = Table.TransformColumns(Basliklar, {' + ', '.join(f'{{"{c}", Text.Trim, type text}}' for c in text_cols) + '})')
    prev = 'Temiz' if text_cols else 'Basliklar'
    steps.append(f'Tipler = Table.TransformColumnTypes({prev}, {{' + ', '.join(f'{{"{c}", {M_TYPES[kinds[c]]}}}' for c in columns) + f'}}, "{args.culture}")')
    m = 'let\n    ' + ',\n    '.join(steps) + '\nin\n    Tipler'
    rows_out = [{'sutun': c, 'm_tipi': M_TYPES[kinds[c]]} for c in columns]
    return Result(f'{len(columns)} sütunlu M sorgusu üretildi', ['sutun', 'm_tipi'], rows_out, {'sutun': len(columns)}, files={'sorgu.m': m},
                  notes=['Tip dönüşümü kültür parametresiyle yapılır; nokta/virgül hatası olmaması için dosya ve kültür uyumlu olmalı.'])


BQ = {'int': 'INT64', 'bigint': 'INT64', 'decimal': 'NUMERIC', 'date': 'DATE', 'datetime': 'DATETIME', 'bool': 'BOOL', 'text': 'STRING'}


@op('bigquery-sema', title='CSV/Excel’den BigQuery şema JSON’u üret', domain='warehouse', product='BigQuery', tag='şema', samples=SATIS,
    args=[arg('--table', default='proje.veri_seti.satislar')],
    description='Sütun tiplerini değerlerden çıkararak bq load / Console için BigQuery şema JSON’unu (name, type, mode) ve aynı tablo için CREATE TABLE ifadesini yazar; sütun adlarını BigQuery kurallarına (harf, rakam, alt çizgi) uygun hâle getirir.',
    input='CSV/XLSX', output='schema.json + create_table.sql', command='python main.py --table proje.veri_seti.satislar',
    expect={'alan': 11, 'zorunlu': 9})
def bq(columns, rows, args):
    fields = []
    for c in columns:
        kind = infer_column([r[c] for r in rows])[0]
        fields.append({'name': snake(c)[:300], 'type': BQ[kind], 'mode': 'REQUIRED' if rows and all(r[c].strip() for r in rows) else 'NULLABLE', 'description': f'Kaynak sütun: {c}'})
    ddl = f'CREATE TABLE `{args.table}` (\n' + ',\n'.join(f"  {f['name']} {f['type']}{' NOT NULL' if f['mode'] == 'REQUIRED' else ''} OPTIONS(description=\"{f['description']}\")" for f in fields) + '\n);'
    return Result(f'{len(fields)} alanlı BigQuery şeması', ['name', 'type', 'mode', 'description'], fields, {'alan': len(fields), 'zorunlu': sum(f['mode'] == 'REQUIRED' for f in fields)},
                  files={'schema.json': json.dumps(fields, ensure_ascii=False, indent=2), 'create_table.sql': ddl},
                  notes=['Türkçe ondalık (1.234,56) içeren CSV’ler bq load ile doğrudan NUMERIC okunamaz; önce sayi-bicimi bloğuyla düz biçime çevirin.'])


@op('qlik-load-script', title='CSV/Excel için Qlik LOAD script’i üret', domain='bi', product='Qlik Sense Enterprise', tag='Qlik', samples=SATIS,
    args=[arg('--table', default='Satislar'), arg('--lib', default='lib://Veri/satislar.csv')],
    description='Dosyanın ayırıcı ve kodlamasına uygun LOAD ifadesini yazar; tarihleri Date#(), Türkçe ondalıkları Num#() ile yorumlar, metinleri Trim() eder ve alanları okunur adlarla yeniden adlandırır. Veri yükleme düzenleyicisine yapıştırılır.',
    input='CSV/XLSX', output='yukleme.qvs', command='python main.py --table Satislar --lib "lib://Kaynak/satis.csv"',
    expect={'alan': 11})
def qlik(columns, rows, args):
    from pathlib import Path
    src = Path(args.input)
    raw = src.read_text(encoding='utf-8-sig', errors='replace')[:20000] if src.suffix.lower() not in ('.xlsx', '.xlsm') else ''
    delim = max([';', ',', '\t', '|'], key=raw.count) if raw else None
    lines = []
    for c in columns:
        kind, _ = infer_column([r[c] for r in rows])
        src_name = f'[{c}]'
        label = ' '.join(w.capitalize() for w in snake(c).split('_'))
        if kind in ('date', 'datetime'):
            formats = {tabular.parse_date(r[c])[1] for r in rows if r[c].strip() and tabular.parse_date(r[c])}
            qfmt = {'%Y-%m-%d': 'YYYY-MM-DD', '%d.%m.%Y': 'DD.MM.YYYY', '%d/%m/%Y': 'DD/MM/YYYY'}.get(next(iter(formats)) if len(formats) == 1 else '', 'YYYY-MM-DD')
            expr = f"Date(Date#({src_name}, '{qfmt}'))" if len(formats) == 1 else f"Date(Alt(Date#({src_name}, 'YYYY-MM-DD'), Date#({src_name}, 'DD.MM.YYYY')))"
        elif kind == 'decimal':
            style = tabular.number_style([r[c] for r in rows])
            expr = f"Num#({src_name}, '', ',', '.')" if style == 'tr' else f"Num#({src_name}, '', '.', ',')"
        elif kind in ('int', 'bigint'):
            expr = f'Num#({src_name})'
        else:
            expr = f'Trim({src_name})'
        lines.append(f'    {expr} as [{label}]')
    fmt_clause = f"(txt, utf8, embedded labels, delimiter is '{delim if delim != chr(9) else chr(9)}', msq)" if delim else '(ooxml, embedded labels)'
    script = f"// {src.name} dosyasından üretildi\n[{args.table}]:\nLOAD\n" + ',\n'.join(lines) + f"\nFROM [{args.lib}]\n{fmt_clause};\n"
    return Result(f'{len(columns)} alanlı LOAD ifadesi üretildi', None, None, {'alan': len(columns)}, files={'yukleme.qvs': script},
                  notes=['lib:// bağlantısının Qlik’te tanımlı olması gerekir. Karışık tarih biçimlerinde Alt() ile iki biçim denenir.'])


@op('rls-rolleri', title='Power BI satır düzeyi güvenlik (RLS) rolleri üret', domain='bi', product=PBI, tag='RLS', samples={'kullanici_yetki.csv': 'kullanici_yetki.csv'},
    args=[arg('--user', default='kullanici'), arg('--value', default='bolge'), arg('--table', default='Satislar'), arg('--column', default='Bolge')],
    description='Kullanıcı–yetki eşleme dosyasından dinamik RLS tasarımını üretir: güvenlik tablosu (CSV), USERPRINCIPALNAME() ile filtreleyen DAX rol ifadesi ve “*” yetkili kullanıcılar için tüm veri erişimi. Statik rol listesi alternatifini de yazar.',
    input='Kullanıcı–değer eşleme CSV’si', output='guvenlik_tablosu.csv + rls.dax + rapor', command='python main.py --user eposta --value bolge --table Satislar --column Bolge',
    expect={'kullanici': 5, 'tam_yetkili': 1, 'eslesme': 6})
def rls(columns, rows, args):
    u, v = need(args.user, columns, '--user'), need(args.value, columns, '--value')
    access = defaultdict(set)
    for r in rows:
        if r[u].strip():
            access[r[u].strip().lower()].add(r[v].strip())
    full = sorted(k for k, s in access.items() if '*' in s)
    table = [{'kullanici': k, 'deger': x} for k, s in sorted(access.items()) for x in sorted(s) if x != '*']
    table += [{'kullanici': k, 'deger': '*'} for k in full]
    dax_rule = (f"VAR Kullanici = LOWER ( USERPRINCIPALNAME () )\n"
                f"VAR TumYetki = CALCULATE ( COUNTROWS ( 'Guvenlik' ), 'Guvenlik'[kullanici] = Kullanici, 'Guvenlik'[deger] = \"*\" ) > 0\n"
                f"RETURN TumYetki || '{args.table}'[{args.column}] IN CALCULATETABLE ( VALUES ( 'Guvenlik'[deger] ), 'Guvenlik'[kullanici] = Kullanici )")
    static = '\n\n'.join(f"// Rol: {val}\n'{args.table}'[{args.column}] = \"{val}\"" for val in sorted({x['deger'] for x in table if x['deger'] != '*'}))
    text = f"// Dinamik RLS — Modelleme > Rolleri yönet > yeni rol > '{args.table}' tablosuna:\n{dax_rule}\n\n// Guvenlik tablosunu modele ilişkisiz (veya tek yönlü) ekleyin.\n\n// --- Statik alternatif ---\n{static}"
    return Result(f'{len(access)} kullanıcı, {len(table)} yetki satırı; {len(full)} kullanıcı tüm veriyi görür', ['kullanici', 'deger'], table,
                  {'kullanici': len(access), 'tam_yetkili': len(full), 'eslesme': len(table)}, files={'guvenlik_tablosu.csv': (['kullanici', 'deger'], table), 'rls.dax': text},
                  notes=['RLS rolleri Power BI Service’te kullanıcı/gruplara atanmalı ve “Farklı görüntüle” ile test edilmelidir.', 'Yetki dosyası kişisel veri (e-posta) içerir; erişimi sınırlayın.'])


@op('kpi-durum', title='KPI hedef–gerçekleşen durum tablosu (trafik ışığı)', domain='bi', product='Rapor gereksinimleri', tag='KPI', samples={'kpi_durum.csv': 'kpi_durum.csv'},
    args=[arg('--target', default='hedef'), arg('--actual', default='gerceklesen'), arg('--direction', default='yon'), arg('--yellow', type=float, default=5.0, help='Hedefe bu yüzde kadar uzaksa sarı')],
    description='Her KPI için gerçekleşmeyi hedefle yönüne göre (yukarı iyi / aşağı iyi) karşılaştırır; sapma yüzdesini ve yeşil/sarı/kırmızı durumunu hesaplar, kırmızıları başa alır. Yönetim sunumu ve otomatik e-posta özeti için.',
    input='KPI listesi (hedef, gerçekleşen, yön)', output='sonuc.csv + rapor', command='python main.py --yellow 5',
    expect={'kpi': 8, 'yesil': 2, 'sari': 3, 'kirmizi': 3})
def kpi(columns, rows, args):
    t, a = numbers(rows, need(args.target, columns, '--target')), numbers(rows, need(args.actual, columns, '--actual'))
    out = []
    for r, target, actual in zip(rows, t, a):
        if target is None or actual is None:
            out.append({**r, 'sapma_%': '', 'durum': 'veri yok'})
            continue
        up = not r.get(args.direction, 'yukari').strip().lower().startswith(('a', 'd'))
        gap = (actual - target) / abs(target) * 100 if target else 0.0
        good = gap >= 0 if up else gap <= 0
        status = 'yeşil' if good else 'sarı' if abs(gap) <= args.yellow else 'kırmızı'
        out.append({**r, 'sapma_%': round(gap, 1), 'durum': status})
    rank = {'kırmızı': 0, 'sarı': 1, 'veri yok': 2, 'yeşil': 3}
    out.sort(key=lambda x: rank[x['durum']])
    c = defaultdict(int)
    for x in out:
        c[x['durum']] += 1
    return Result(f"{c['yeşil']} yeşil, {c['sarı']} sarı, {c['kırmızı']} kırmızı KPI", columns + ['sapma_%', 'durum'], out,
                  {'kpi': len(out), 'yesil': c['yeşil'], 'sari': c['sarı'], 'kirmizi': c['kırmızı']})


@op('dbt-iskelet', title='DDL’den dbt kaynak ve staging model iskeleti üret', domain='transform', product='dbt Core', tag='dbt', kind='file', samples={'sema.sql': 'sema.sql'},
    args=[arg('--source', default='erp')],
    description='CREATE TABLE betiklerinden dbt sources.yml (anahtar ve not_null testleriyle, ilişki testleriyle), her tablo için stg_ modeli (snake_case yeniden adlandırma) ve modellerin schema.yml dosyasını üretir. Yeni bir dbt projesinin ilk katmanını dakikalar içinde kurar.',
    input='.sql dosyası veya klasörü (CREATE TABLE)', output='models/staging/*.sql + _sources.yml + _models.yml', command='python main.py --input ddl/ --source erp',
    expect={'model': 4, 'test': 12})
def dbt(args):
    tables = load_ddl(args.input)
    rels = relationships('\n'.join(t for _, t in read_sql_files(args.input)), tables)
    files, tests = {}, 0
    src = ['version: 2', '', 'sources:', f'  - name: {args.source}', '    tables:']
    mdl = ['version: 2', '', 'models:']
    for t, cols in tables.items():
        schema, name = (t.split('.')[-2] if '.' in t else ''), t.split('.')[-1]
        model = f'stg_{args.source}__{snake(name)}'
        select = ',\n'.join(f"        {c['name']} as {snake(c['name'])}" for c in cols)
        files[f'models/staging/{model}.sql'] = f"with source as (\n    select * from {{{{ source('{args.source}', '{name}') }}}}\n),\n\nrenamed as (\n    select\n{select}\n    from source\n)\n\nselect * from renamed\n"
        src += [f'      - name: {name}'] + ([f'        schema: {schema}'] if schema else []) + ['        columns:']
        mdl += [f'  - name: {model}', '    columns:']
        for c in cols:
            ctests = (['unique'] if c['pk'] and sum(x['pk'] for x in cols) == 1 else []) + ([] if c['nullable'] else ['not_null'])
            for child, col, parent, key, _ in rels:
                if child == t and col.lower() == c['name'].lower():
                    ctests.append(f"relationships:\n                to: ref('stg_{args.source}__{snake(parent.split('.')[-1])}')\n                field: {snake(key)}")
            tests += len(ctests)
            src.append(f"          - name: {c['name']}")
            mdl.append(f"      - name: {snake(c['name'])}")
            if ctests:
                mdl.append('        tests:')
                mdl += [f'          - {x}' for x in ctests]
    files['models/staging/_sources.yml'] = '\n'.join(src)
    files['models/staging/_models.yml'] = '\n'.join(mdl)
    return Result(f'{len(tables)} staging modeli ve {tests} test üretildi', None, None, {'model': len(tables), 'test': tests}, files=files,
                  tables=[('Modeller', ['Kaynak tablo', 'Model'], [[t, f"stg_{args.source}__{snake(t.split('.')[-1])}"] for t in tables])])
