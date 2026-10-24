"""Sıfırdan kurulum: veri platformu bileşenlerini boş bir hesaptan/projeden başlayarak kuran betik ve projeler.
Düz dosyadan normalize veritabanı (SQLite'ta yüklenip geri birleştirilerek doğrulanır), çok tablolu dbt yıldız projesi, ekip ve katman listesinden
Snowflake hesabı, Unity Catalog ve BigQuery (Terraform) kurulumu, tablo listesinden Azure Data Factory metadata güdümlü yükleme hattı, görev listesinden
Airflow DAG’i ve eğitim dosyasından scikit-learn proje iskeleti. Yetki tasarımları üretilen komutların etkisi hesaplanarak istenen erişimle karşılaştırılır.
Hiçbir sisteme bağlanmaz; üretilen betikler insan onayıyla çalıştırılır."""
import ast, json, re, sqlite3
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
import tabular
from sqlgen import infer_column, snake, quote as sql_quote
from yildiz import SQL_TYPE
from rapor import IDLIKE, PII, label, yaml_dump
import kurulum
from entegrasyon import walk_activities
from islem import op, arg, Result, need, numbers

REQUIRES = ['sema_farki', 'sqlgen', 'yildiz', 'bigen', 'rapor', 'kurulum', 'entegrasyon']
EKIPLER = {'platform_ekipler.csv': 'platform_ekipler.csv'}
LEVELS = {'okuma': 1, 'yazma': 2, 'sahip': 3}


# ---------- normalizasyon ----------
@op('veritabani-normalizasyon', title='Düz Excel/CSV dosyasından sıfırdan normalize veritabanı: tablolar, anahtarlar, DDL ve yükleme (SQLite’ta doğrulanır)',
    domain='database', product='PostgreSQL', tag='3NF tasarım', samples={'siparis_duz.csv': 'siparis_duz.csv'},
    args=[arg('--dialect', choices=('postgres', 'sqlserver', 'snowflake', 'sqlite'), default='postgres'), arg('--schema', default='', help='Hedef şema (isteğe bağlı)'),
          arg('--keys', default='', help='Varlık anahtarı sütunları (boşsa no/id/kod ile biten sütunlar)'),
          arg('--tolerance', type=float, default=0.02, help='Bağımlılıkta kabul edilen tutarsız satır oranı (varsayılan %%2)')],
    description='Tek tabloya yığılmış bir dışa aktarımı (ör. sipariş satırları + müşteri + ürün bilgisi) veriden bulunan fonksiyonel bağımlılıklarla üçüncü normal forma ayırır: '
                'her varlık için anahtarlı tablo, satır tablosu, yabancı anahtarlar ve indeksler. DDL ve hazırlık tablosundan yükleme SQL’i yazar; SQLite’ta yükleyip tabloları '
                'yeniden birleştirerek orijinal satırlarla karşılaştırır. Aynı anahtara farklı değer yazılmış hücreleri (ör. İzmir / Izmir) ayrıca listeler.',
    input='CSV/XLSX (denormalize dışa aktarım)', output='ddl.sql + yukleme.sql + er-diyagrami.md + cakismalar.csv',
    command='python main.py --input siparisler.xlsx --dialect sqlserver --schema satis',
    expect={'tablo': 4, 'yabanci_anahtar': 3, 'cakisma': 1, 'yeniden_birlesim_farki': 1, 'satir_anahtari': 'siparis_no, urun_kodu'})
def normalize(columns, rows, args):
    if not rows:
        raise ValueError('Dosyada veri satırı yok.')
    vals = {c: [r[c].strip() for r in rows] for c in columns}
    kinds = {c: infer_column(vals[c])[0] for c in columns}
    distinct = {c: len(set(vals[c])) for c in columns}
    n = len(rows)
    keys = [c.strip() for c in args.keys.split(',') if c.strip()] or [c for c in columns if IDLIKE.search(c) and not PII.search(c) and all(vals[c])]
    for k in keys:
        need(k, columns, '--keys')
    if not keys:
        raise ValueError('Varlık anahtarı bulunamadı (no/id/kod ile biten dolu sütun); --keys ile verin.')

    def determines(x, y):
        """Rows that break x -> y (rows whose y differs from the most common y of their x group)."""
        groups = defaultdict(Counter)
        for a, b in zip(vals[x], vals[y]):
            groups[a][b] += 1
        return sum(sum(cnt.values()) - cnt.most_common(1)[0][1] for cnt in groups.values()), groups

    deps, conflicts = {}, []
    for k in keys:
        if distinct[k] == n:
            continue  # a unique column is a row key, not an entity
        deps[k] = []
        for c in columns:
            if c == k:
                continue
            bad, groups = determines(k, c)
            if bad <= args.tolerance * n and distinct[c] <= distinct[k]:
                deps[k].append(c)
                for key, cnt in groups.items():
                    if len(cnt) > 1:
                        conflicts.append({'anahtar': k, 'deger': key, 'sutun': c, 'secilen': cnt.most_common(1)[0][0],
                                          'diger': ', '.join(f'{v} ({x})' for v, x in cnt.most_common()[1:])})
    # Assign every column to the entity with the fewest distinct values that determines it.
    owner = {}
    for c in columns:
        if c in deps:
            continue
        cands = [k for k in deps if c in deps[k]]
        if cands:
            owner[c] = min(cands, key=lambda k: (distinct[k], columns.index(k)))
    # Entity keys determined by another entity become foreign keys there (siparis_no -> musteri_no).
    parent = {}
    for k in deps:
        cands = [o for o in deps if o != k and k in deps[o]]
        if cands:
            parent[k] = min(cands, key=lambda o: (distinct[o], columns.index(o)))
    # Row key: smallest set of entity keys (plus any unique column) that identifies a row.
    row_key = None
    for size in (1, 2, 3):
        for combo in combinations([k for k in keys if k in deps and k not in parent] + [k for k in keys if k in deps and k in parent], size):
            if len(set(zip(*[vals[c] for c in combo]))) == n:
                row_key = list(combo)
                break
        if row_key:
            break
    unique_key = next((k for k in keys if distinct[k] == n), None)
    if not row_key and unique_key:
        row_key = [unique_key]
    if not row_key:
        raise ValueError('Satırı tekil tanımlayan anahtar birleşimi bulunamadı; dosyada tam mükerrer satırlar olabilir (mukerrer bloğuyla temizleyin) veya --keys verin.')
    line_cols = [c for c in columns if c not in owner and c not in deps or c in row_key]
    tables = []  # (name, key cols, cols, fks [(col, table)])
    tname = {k: kurulum.entity(k) for k in deps}
    for k in sorted(deps, key=lambda k: distinct[k]):
        own = [c for c in columns if owner.get(c) == k]
        fks = [(c, tname[c]) for c in deps if parent.get(c) == k]
        tables.append((tname[k], [k], [k] + [c for c, _ in fks] + own, fks))
    line_name = f'{tname[row_key[0]]}_satir' if row_key[0] in tname else snake(Path(args.input).stem)
    row_fks = [(c, tname[c]) for c in row_key if c in tname]
    extra = [c for c in columns if c not in owner and c not in deps]
    if len(row_key) > 1 or extra:
        tables.append((line_name, row_key, row_key + extra, row_fks))
    q = lambda c: sql_quote(snake(c), {'sqlserver': 'tsql'}.get(args.dialect, 'postgres'))

    def ddl_for(dialect):
        tt = SQL_TYPE[dialect]
        p = f'{args.schema}.' if args.schema and dialect != 'sqlite' else ''
        out = []
        for name, key, cols, fks in tables:
            body = [f'    {q(c)} {tt[kinds[c]]} NOT NULL' if c in key else f'    {q(c)} {tt[kinds[c]]}' for c in cols]
            body.append(f'    CONSTRAINT pk_{name} PRIMARY KEY ({", ".join(q(c) for c in key)})')
            body += [f'    CONSTRAINT fk_{name}_{tbl} FOREIGN KEY ({q(c)}) REFERENCES {p}{tbl} ({q(c)})' for c, tbl in fks]
            out.append(f'CREATE TABLE {p}{name} (\n' + ',\n'.join(body) + '\n);')
            out += [f'CREATE INDEX ix_{name}_{snake(c)} ON {p}{name} ({q(c)});' for c, _ in fks if [c] != key[:1] or len(key) == 1]
        return '\n\n'.join(out) + '\n'

    def load_for(dialect):
        tt = SQL_TYPE[dialect]
        p = f'{args.schema}.' if args.schema and dialect != 'sqlite' else ''
        stg = f'{p}stg_{snake(Path(args.input).stem)}'
        out = [f'-- Hazırlık tablosu: dosya buraya olduğu gibi (tipli) yüklenir.\nCREATE TABLE {stg} (\n' + ',\n'.join(f'    {q(c)} {tt[kinds[c]]}' for c in columns) + '\n);']
        lit = lambda v: "'" + v.replace("'", "''") + "'"
        out += [f'-- Çakışma: {x["anahtar"]} = {x["deger"]} için {x["sutun"]} en sık değere ({x["secilen"]}) çekilir.\n'
                f'UPDATE {stg} SET {q(x["sutun"])} = {lit(x["secilen"])} WHERE {q(x["anahtar"])} = {lit(x["deger"])} AND {q(x["sutun"])} <> {lit(x["secilen"])};'
                for x in conflicts if kinds[x['sutun']] == 'text']
        for name, key, cols, _ in tables:
            rest = [c for c in cols if c not in key]
            sel = ', '.join([q(c) for c in key] + [f'MIN({q(c)}) AS {q(c)}' for c in rest])
            out.append(f'INSERT INTO {p}{name} ({", ".join(q(c) for c in cols)})\nSELECT {sel}\nFROM {stg}\nGROUP BY {", ".join(q(c) for c in key)};')
        return '\n\n'.join(out) + '\n', stg

    # SQLite: load, rebuild the flat rows with joins, compare.
    con = sqlite3.connect(':memory:')
    con.execute('PRAGMA foreign_keys = ON')
    nums = {c: numbers(rows, c) for c in columns if kinds[c] in ('int', 'bigint', 'decimal')}
    load_sqlite, stg = load_for('sqlite')
    con.executescript(load_sqlite.split('\n\n')[0])
    conv = [[nums[c][i] if c in nums else (tabular.parse_date(vals[c][i])[0].strftime('%Y-%m-%d') if kinds[c] == 'date' and tabular.parse_date(vals[c][i]) else vals[c][i] or None)
             for c in columns] for i in range(n)]
    con.executemany(f'INSERT INTO {stg} VALUES ({", ".join("?" * len(columns))})', conv)
    for stmt in load_sqlite.split('\n\n'):
        if stmt.startswith('-- Çakışma'):
            con.executescript(stmt)
    ddl_sqlite = ddl_for('sqlite')
    stmts = {}
    for stmt in ddl_sqlite.split(';\n'):
        mm = re.match(r'\s*CREATE TABLE (\w+)', stmt)
        if mm:
            stmts[mm.group(1)] = stmt + ';'
    created, pending = [], list(tables)
    while pending:  # parents first so foreign keys hold
        progress = False
        for tb in list(pending):
            if all(f[1] in created for f in tb[3]):
                con.executescript(stmts[tb[0]])
                created.append(tb[0])
                pending.remove(tb)
                progress = True
        if not progress:
            raise ValueError('İç hata: tablolar arasında döngüsel yabancı anahtar.')
    for name in created:
        ins = next(s for s in load_sqlite.split('\n\n') if s.startswith(f'INSERT INTO {name} '))
        con.executescript(ins)
    counts = {name: con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name, *_ in tables}
    # Rebuild: start from the row table (or the most detailed entity) and join every parent.
    root = tables[-1]
    alias, joins, seen, queue = {root[0]: 'r'}, [], {root[0]}, deque([root])
    by_name = {tb[0]: tb for tb in tables}
    while queue:
        tb = queue.popleft()
        for c, ptbl in tb[3]:
            if ptbl not in seen:
                seen.add(ptbl)
                alias[ptbl] = f'p{len(alias)}'
                joins.append(f'JOIN {ptbl} {alias[ptbl]} ON {alias[ptbl]}.{q(c)} = {alias[tb[0]]}.{q(c)}')
                queue.append(by_name[ptbl])
    where_col = {}
    for name, key, cols, _ in tables:
        for c in cols:
            where_col.setdefault(c, f'{alias[name]}.{q(c)}')
    rebuilt = con.execute(f'SELECT {", ".join(where_col[c] for c in columns)} FROM {root[0]} r ' + ' '.join(joins)).fetchall()
    norm = lambda v: '' if v is None else (f'{float(v):.6f}' if isinstance(v, (int, float)) else str(v))
    original = Counter(tuple(norm(v) for v in r) for r in conv)
    diff = sum((original - Counter(tuple(norm(v) for v in r) for r in rebuilt)).values())
    con.close()
    ddl_text = ddl_for(args.dialect)
    load_text, stg_name = load_for(args.dialect)
    mer = ['erDiagram']
    for name, key, cols, fks in tables:
        mer.append(f'    {name} {{')
        mer += [f'        {kinds[c]} {snake(c)}{" PK" if c in key else " FK" if any(c == f[0] for f in fks) else ""}' for c in cols]
        mer.append('    }')
    for name, key, cols, fks in tables:
        mer += [f'    {tbl} ||--o{{ {name} : "{snake(c)}"' for c, tbl in fks]
    out_rows = [{'tablo': name, 'anahtar': ', '.join(key), 'sutunlar': ', '.join(c for c in cols if c not in key), 'yabanci_anahtar': ', '.join(f'{c} → {tbl}' for c, tbl in fks),
                 'satir': counts[name]} for name, key, cols, fks in tables]
    notes = [f'Yükleme: dosyayı {stg_name} hazırlık tablosuna aktarın, sonra yukleme.sql’i çalıştırın (üst tablolar önce gelir). Aynı anahtara yazılmış farklı metinler '
             'UPDATE ile en sık değere çekilir; kalıcı çözüm cakismalar.csv’deki kayıtları kaynakta düzeltmektir.',
             f'Doğrulama SQLite’ta yapıldı: tablolar yüklendi, yabancı anahtarlar açıkken birleştirilip {n} orijinal satırla karşılaştırıldı; {diff} satır farklı çıktı '
             '(yalnızca çakışma düzeltmesiyle değişen satırlar).']
    if parent:
        notes.append('Geçişli bağımlılıklar ayrıldı: ' + '; '.join(f'{k} {parent[k]} tarafından belirleniyor, {tname[k]} tablosuna yabancı anahtar oldu' for k in parent) + '.')
    return Result(f'{len(tables)} tablo, {sum(len(tb[3]) for tb in tables)} yabancı anahtar; satır anahtarı {", ".join(row_key)}', list(out_rows[0]), out_rows,
                  {'tablo': len(tables), 'yabanci_anahtar': sum(len(tb[3]) for tb in tables), 'cakisma': len(conflicts), 'yeniden_birlesim_farki': diff,
                   'satir_anahtari': ', '.join(row_key)},
                  tables=[('Çakışan değerler', ['Anahtar', 'Değer', 'Sütun', 'Seçilen', 'Diğer'], [list(c.values()) for c in conflicts])] if conflicts else [],
                  files={'ddl.sql': ddl_text, 'yukleme.sql': load_text, 'er-diyagrami.md': '```mermaid\n' + '\n'.join(mer) + '\n```\n',
                         'cakismalar.csv': (['anahtar', 'deger', 'sutun', 'secilen', 'diger'], conflicts)}, notes=notes)


# ---------- dbt (çok tablolu) ----------
DBT_DATE = ('{# YYYY-AA-GG veya GG.AA.YYYY metnini DATE’e çevirir; veritabanına göre doğru fonksiyon seçilir. #}\n'
            "{% macro metin_tarih(sutun) %}{{ return(adapter.dispatch('metin_tarih')(sutun)) }}{% endmacro %}\n\n"
            "{% macro default__metin_tarih(sutun) %}\n    case when {{ sutun }} like '__.__.____' then to_date({{ sutun }}, 'DD.MM.YYYY') else cast({{ sutun }} as date) end\n{% endmacro %}\n\n"
            "{% macro duckdb__metin_tarih(sutun) %}\n    coalesce(try_cast({{ sutun }} as date), cast(try_strptime({{ sutun }}, '%d.%m.%Y') as date))\n{% endmacro %}\n\n"
            "{% macro bigquery__metin_tarih(sutun) %}\n    coalesce(safe_cast({{ sutun }} as date), safe.parse_date('%d.%m.%Y', {{ sutun }}))\n{% endmacro %}\n\n"
            "{% macro snowflake__metin_tarih(sutun) %}\n    coalesce(try_to_date({{ sutun }}, 'YYYY-MM-DD'), try_to_date({{ sutun }}, 'DD.MM.YYYY'))\n{% endmacro %}\n")


def render_sqlite(sql):
    """Turn the generated dbt SQL into plain SQLite SQL for the local check."""
    sql = re.sub(r"\{\{\s*ref\('(\w+)'\)\s*\}\}", r'\1', sql)
    sql = re.sub(r'\{\{\s*metin_tarih\("(\w+)"\)\s*\}\}', r'date(\1)', sql)
    sql = re.sub(r'\{\{.*?\}\}|\{#.*?#\}', '', sql, flags=re.S)
    sql = re.sub(r'numeric\(18, 4\)', 'REAL', sql)
    return re.sub(r"cast\(replace\(replace\(trim\((\w+)\), '\.', ''\), ',', '\.'\) as REAL\)", r"CAST(replace(replace(trim(\1), '.', ''), ',', '.') AS REAL)", sql)


@op('dbt-yildiz-projesi', title='Tablo klasöründen sıfırdan dbt projesi: kaynaklar, staging, boyut ve olgu modelleri, ilişki testleri (SQLite’ta çalıştırılır)',
    domain='transform', product='dbt Core', tag='dbt kurulum', kind='file', samples=kurulum.MAGAZA, sample_input='magaza', args=kurulum.MODEL_ARGS,
    description='Olgu ve boyut dosyalarından uçtan uca dbt projesi kurar: her tablo için seed ve tipleyen staging modeli, dim_ modelleri, olguyu boyutlara bağlayan fct_ modeli, '
                'aylık özet, birincil anahtar (unique/not_null) ve ilişki (relationships) testleri; eşleşmeyen satırı olan ilişki testi uyarı düzeyine alınır. Modeller ve testler '
                'SQLite’ta sırasıyla çalıştırılır; satır sayıları ve test sonuçları rapora yazılır.',
    input='Klasör: her tablo bir CSV/XLSX', output='dbt-proje/ (dbt_project.yml, seeds/, models/staging, models/marts, macros/, profiles.yml)',
    command='python main.py --input C:\\Veri\\magaza', expect={'tablo': 4, 'iliski': 3, 'model': 9, 'test': 19, 'kalan_test': 1, 'fct_satir': 600})
def dbt_star(args):
    m = kurulum.load_model(args)
    proj = snake(m.folder.name)
    files, models, tests, schema_models = {}, [], [], []
    stg = {t: f'stg_{m.tables[t].stem}' for t in m.tables}
    for t in m.order + m.loose:
        tb = m.tables[t]
        sel = []
        for c in tb.columns:
            k, col = tb.kinds[c], snake(c)
            if k == 'decimal':
                style = tabular.number_style([v for v in tb.vals[c] if v])
                expr = (f"cast(replace(replace(trim({c}), '.', ''), ',', '.') as numeric(18, 4))" if style == 'tr' else f'cast({c} as numeric(18, 4))')
            elif k in ('int', 'bigint'):
                expr = f'cast({c} as {"bigint" if k == "bigint" else "integer"})'
            elif k == 'date':
                expr = '{{ metin_tarih("' + c + '") }}'
            else:
                expr = f'trim({c})'
            sel.append(f'    {expr} as {col}')
        files[f'models/staging/{stg[t]}.sql'] = f'-- {tb.file.name}: metinler kırpılır, sayılar ve tarihler tiplenir.\nselect\n' + ',\n'.join(sel) + f"\nfrom {{{{ ref('{tb.stem}') }}}}\n"
        files[f'seeds/{tb.stem}.csv'] = tb.file.read_text(encoding='utf-8-sig', errors='replace') if tb.file.suffix.lower() not in ('.xlsx', '.xlsm') else \
            ','.join(tb.columns) + '\n' + ''.join(','.join('"' + r[c].replace('"', '""') + '"' for c in tb.columns) + '\n' for r in tb.rows)
        models.append(stg[t])
        cols = []
        if tb.pk:
            cols.append({'name': snake(tb.pk), 'data_tests': ['unique', 'not_null']})
            tests += [('unique', stg[t], snake(tb.pk), None), ('not_null', stg[t], snake(tb.pk), None)]
        schema_models.append({'name': stg[t], 'description': f'{tb.file.name} için tiplenmiş staging modeli.', 'columns': cols})
    dims = [t for t in m.order[1:]]
    for t in dims:
        tb = m.tables[t]
        files[f'models/marts/dim_{tb.stem}.sql'] = f"select *\nfrom {{{{ ref('{stg[t]}') }}}}\n"
        models.append(f'dim_{tb.stem}')
        schema_models.append({'name': f'dim_{tb.stem}', 'description': f'{t} boyutu.', 'columns': [{'name': snake(tb.pk), 'data_tests': ['unique', 'not_null']}]})
        tests += [('unique', f'dim_{tb.stem}', snake(tb.pk), None), ('not_null', f'dim_{tb.stem}', snake(tb.pk), None)]
    f = m.fact
    fct = f'fct_{f.stem}'
    month = f'    cast({{{{ dbt.date_trunc("month", "{snake(m.date)}") }}}} as date) as ay,\n' if m.date else ''
    files[f'models/marts/{fct}.sql'] = f"select\n{month}    *\nfrom {{{{ ref('{stg[f.name]}') }}}}\n"
    models.append(fct)
    fcols = [{'name': snake(f.pk), 'data_tests': ['unique', 'not_null']}] if f.pk else []
    tests += [('unique', fct, snake(f.pk), None), ('not_null', fct, snake(f.pk), None)] if f.pk else []
    for r in m.rels:
        if r.child == f.name and r.active:
            test = {'relationships': {'arguments': {'to': f"ref('dim_{m.tables[r.parent].stem}')", 'field': snake(r.pk)}}}
            if r.orphans:
                test['relationships']['config'] = {'severity': 'warn'}
            fcols.append({'name': snake(r.fk), 'data_tests': [test]})
            tests.append(('relationships', fct, snake(r.fk), (f'dim_{m.tables[r.parent].stem}', snake(r.pk), 'warn' if r.orphans else 'error')))
    schema_models.append({'name': fct, 'description': f'{f.name} olgu tablosu; yabancı anahtarlar boyutlara bağlıdır.', 'columns': fcols})
    if m.date:
        agg_sql = {'sum': 'sum({})', 'avg': 'avg({})', 'countd': 'count(distinct {})', 'count': 'count(*)'}
        metric = [f'    {agg_sql[x["agg"]].format(snake(x["col"]))} as {snake(x["name"])}' for x in m.measures + [m.count]]
        files[f'models/marts/{f.stem}_aylik.sql'] = 'select\n    ay,\n' + ',\n'.join(metric) + f"\nfrom {{{{ ref('{fct}') }}}}\ngroup by ay\n"
        models.append(f'{f.stem}_aylik')
        schema_models.append({'name': f'{f.stem}_aylik', 'description': 'Aylık özet.'})
    files['models/schema.yml'] = yaml_dump({'version': 2, 'models': schema_models})
    files['macros/metin_tarih.sql'] = DBT_DATE
    files['dbt_project.yml'] = yaml_dump({'name': proj, 'version': '1.0.0', 'profile': proj, 'model-paths': ['models'], 'seed-paths': ['seeds'], 'macro-paths': ['macros'],
                                          'clean-targets': ['target', 'dbt_packages'],
                                          'seeds': {proj: {t.stem: {'+column_types': {c: 'varchar' for c in t.columns}} for t in m.tables.values()}},
                                          'models': {proj: {'staging': {'+materialized': 'view'}, 'marts': {'+materialized': 'table'}}}})
    files['profiles.yml'] = ('# Yerel deneme: pip install dbt-duckdb, sonra bu klasörde: dbt build --profiles-dir .\n'
                             + yaml_dump({proj: {'target': 'yerel', 'outputs': {'yerel': {'type': 'duckdb', 'path': f'{proj}.duckdb', 'threads': 4}}}}))
    files['README.md'] = (f'# {m.title}: dbt projesi\n\nKatmanlar: seeds → staging (view) → dim_/fct_ (table)' + (f' → {f.stem}_aylik' if m.date else '') + '.\n\n'
                          '```\npip install dbt-duckdb\ndbt build --profiles-dir .\n```\n\nKendi ambarınızda seed yerine kaynak tabloları kullanın: staging modellerindeki ref() '
                          'çağrılarını source() ile değiştirip sources tanımı ekleyin.\n')
    # Local run in SQLite: seeds as text tables, models in order, then the tests.
    con = sqlite3.connect(':memory:')
    for t in m.tables.values():
        con.execute(f'CREATE TABLE {t.stem} (' + ', '.join(f'"{c}" TEXT' for c in t.columns) + ')')
        con.executemany(f'INSERT INTO {t.stem} VALUES ({", ".join("?" * len(t.columns))})', [[r[c].strip() or None for c in t.columns] for r in t.rows])
    rowcount = {}
    for name in models:
        path = next(k for k in files if k.endswith(f'/{name}.sql'))
        sql = render_sqlite(files[path])
        if m.date:  # dbt.date_trunc was stripped by the renderer
            sql = re.sub(r'cast\(\s*as date\)', f"date({snake(m.date)}, 'start of month')", sql)
        con.execute(f'CREATE TABLE {name} AS {sql}')
        rowcount[name] = con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
    results = []
    for kind, model, col, extra in tests:
        if kind == 'unique':
            fails = con.execute(f'SELECT COUNT(*) FROM (SELECT {col} FROM {model} GROUP BY {col} HAVING COUNT(*) > 1)').fetchone()[0]
        elif kind == 'not_null':
            fails = con.execute(f'SELECT COUNT(*) FROM {model} WHERE {col} IS NULL').fetchone()[0]
        else:
            fails = con.execute(f'SELECT COUNT(*) FROM {model} c LEFT JOIN {extra[0]} p ON p.{extra[1]} = c.{col} WHERE c.{col} IS NOT NULL AND p.{extra[1]} IS NULL').fetchone()[0]
        results.append({'test': f'{kind}_{model}_{col}', 'model': model, 'sonuc': 'geçti' if not fails else ('uyarı' if extra and extra[2] == 'warn' else 'hata'), 'hatali_satir': fails})
    con.close()
    if rowcount[fct] != len(f.rows):
        raise ValueError(f'İç hata: {fct} satır sayısı {rowcount[fct]}, kaynak {len(f.rows)}.')
    left = sum(r['sonuc'] != 'geçti' for r in results)
    return Result(f'dbt-proje: {len(models)} model, {len(tests)} test; {left} test uyarı/hata verdi', ['test', 'model', 'sonuc', 'hatali_satir'], results,
                  {'tablo': len(m.tables), 'iliski': sum(r.active for r in m.rels), 'model': len(models), 'test': len(tests), 'kalan_test': left, 'fct_satir': rowcount[fct]},
                  tables=kurulum.model_tables(m) + [('Model satır sayıları (SQLite)', ['Model', 'Satır'], [[k, v] for k, v in rowcount.items()])],
                  files={'dbt-proje/' + k: v for k, v in files.items()},
                  notes=['dbt-proje klasöründe: pip install dbt-duckdb, ardından dbt build --profiles-dir . Modeller ve testlerin SQL’i yerelde SQLite’a çevrilip çalıştırıldı; '
                         'dbt ile çalıştırılarak test edilmedi.', 'relationships testi dbt 1.10+ “arguments” biçimini kullanır.'] + m.warnings)


# ---------- erişim tasarımı (Snowflake, Unity Catalog, BigQuery) ----------
def read_teams(columns, rows):
    for c in ('ekip', 'katman', 'erisim'):
        need(c, columns, 'girdi')
    req, size, credit = {}, {}, {}
    for i, r in enumerate(rows, start=2):
        e, k, a = snake(r['ekip']), snake(r['katman']), r['erisim'].strip().lower()
        if a not in LEVELS:
            raise ValueError(f'{i}. satır: erisim okuma, yazma veya sahip olmalı (“{a}”).')
        req[(e, k)] = max(req.get((e, k), 0), LEVELS[a])
        if r.get('depo_boyutu', '').strip():
            size[e] = r['depo_boyutu'].strip().upper()
        if r.get('aylik_kredi', '').strip():
            credit[e] = numbers([r], 'aylik_kredi')[0]
    teams = sorted({e for e, _ in req})
    layers = sorted({k for _, k in req}, key=lambda k: (['raw', 'ham', 'bronze', 'staging', 'silver', 'mart', 'gold', 'sandbox'] + [k]).index(k))
    return req, teams, layers, size, credit


ENV_ARGS = [arg('--envs', default='dev,prod', help='Ortamlar, virgülle'),
            arg('--prod-human-write', action='store_true', help='Prod’da ekiplere de yazma ver (varsayılan: yazma servis kimliklerine)')]


def plan_access(req, envs, prod_human):
    """Who gets which level where: {(env, principal, layer): level}; principals are teams or their service identities."""
    out = {}
    for env in envs:
        for (team, layer), lvl in req.items():
            if env == 'prod' and lvl > 1 and not prod_human:
                out[(env, team, layer)] = 1
                out[(env, f'{team}_svc', layer)] = lvl
            else:
                out[(env, team, layer)] = lvl
    return out


SIZES = ['XSMALL', 'SMALL', 'MEDIUM', 'LARGE', 'XLARGE', 'XXLARGE']


@op('snowflake-hesap-kurulumu', title='Ekip ve katman listesinden sıfırdan Snowflake hesap kurulumu: veritabanları, rol hiyerarşisi, depolar ve bütçe izleme',
    domain='warehouse', product='Snowflake', tag='hesap kurulumu', samples=EKIPLER,
    args=ENV_ARGS + [arg('--dev-share', type=float, default=0.25, help='Dev ortamı kredi bütçesi, prod’un oranı')],
    description='“ekip, katman, erişim (okuma/yazma/sahip), depo boyutu, aylık kredi” listesinden çalıştırılmaya hazır kurulum SQL’i yazar: ortam × katman veritabanları, her '
                'veritabanı için okuma/yazma/sahip erişim rolleri (mevcut ve gelecek nesnelere hak), ekip başına işlevsel rol ve SYSADMIN’e bağlanan hiyerarşi, ekip depoları '
                '(otomatik askıya alma), aylık kredi bütçeli resource monitor’ler; prod’da yazma servis kullanıcısına (anahtar çiftiyle) verilir. Üretilen GRANT’lerin etkisi '
                'hesaplanıp istenen erişimle karşılaştırılır.',
    input='CSV/XLSX: ekip, katman, erisim, depo_boyutu, aylik_kredi', output='snowflake_kurulum.sql + yetki_matrisi.csv + rapor',
    command='python main.py --input ekipler.csv --envs dev,test,prod',
    expect={'veritabani': 8, 'erisim_rolu': 24, 'islevsel_rol': 8, 'servis_kullanicisi': 3, 'depo': 8, 'monitor': 8, 'fark': 0})
def snowflake_setup(columns, rows, args):
    req, teams, layers, size, credit = read_teams(columns, rows)
    envs = [snake(e) for e in args.envs.split(',') if e.strip()]
    U = lambda *p: '_'.join(x.upper() for x in p)
    access = plan_access(req, envs, args.prod_human_write)
    sql = ['-- Snowflake hesap kurulumu: bölümleri sırasıyla, yetkili rolle çalıştırın. Komutlar IF NOT EXISTS ile tekrar çalıştırılabilir.',
           '', '-- 1) Roller (USERADMIN)', 'USE ROLE USERADMIN;']
    grants_graph = defaultdict(set)   # role -> roles granted to it
    privs = defaultdict(set)         # access role -> (db, level)
    dbs = [U(env, layer) for env in envs for layer in layers]
    arole = lambda db, lvl: f'{db}_{["", "R", "RW", "OWN"][lvl]}'
    for db in dbs:
        for lvl in (1, 2, 3):
            sql.append(f'CREATE ROLE IF NOT EXISTS {arole(db, lvl)} COMMENT = \'{db} erişim rolü ({["", "okuma", "yazma", "sahip"][lvl]})\';')
            privs[arole(db, lvl)].add((db, lvl))
    principals = sorted({(env, p) for env, p, _ in access})
    frole = lambda env, p: U(env, p)
    for env, p in principals:
        sql.append(f'CREATE ROLE IF NOT EXISTS {frole(env, p)} COMMENT = \'{"servis kimliği" if p.endswith("_svc") else "ekip"} rolü ({env})\';')
    sql += ['', '-- 2) Veritabanları ve depolar (SYSADMIN)', 'USE ROLE SYSADMIN;']
    for db in dbs:
        sql.append(f'CREATE DATABASE IF NOT EXISTS {db}' + (' DATA_RETENTION_TIME_IN_DAYS = 7' if db.startswith('PROD') else ' DATA_RETENTION_TIME_IN_DAYS = 1') + ';')
    warehouses = {}
    for env in envs:
        for team in teams:
            s = size.get(team, 'XSMALL')
            if s not in SIZES:
                raise ValueError(f'{team}: depo_boyutu {", ".join(SIZES)} olmalı (“{s}”).')
            if env != 'prod':
                s = SIZES[max(0, SIZES.index(s) - 1)]
            wh = U(env, team, 'WH')
            warehouses[(env, team)] = wh
            sql.append(f"CREATE WAREHOUSE IF NOT EXISTS {wh} WAREHOUSE_SIZE = {s} AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE "
                       f"STATEMENT_TIMEOUT_IN_SECONDS = {3600 if env == 'prod' else 1800} COMMENT = '{team} ({env})';")
    sql += ['', '-- 3) Haklar ve rol hiyerarşisi (SECURITYADMIN)', 'USE ROLE SECURITYADMIN;']
    for db in dbs:
        r, rw, own = arole(db, 1), arole(db, 2), arole(db, 3)
        sql += [f'-- {db}',
                f'GRANT USAGE ON DATABASE {db} TO ROLE {r};', f'GRANT USAGE ON ALL SCHEMAS IN DATABASE {db} TO ROLE {r};', f'GRANT USAGE ON FUTURE SCHEMAS IN DATABASE {db} TO ROLE {r};',
                f'GRANT SELECT ON ALL TABLES IN DATABASE {db} TO ROLE {r};', f'GRANT SELECT ON FUTURE TABLES IN DATABASE {db} TO ROLE {r};',
                f'GRANT SELECT ON ALL VIEWS IN DATABASE {db} TO ROLE {r};', f'GRANT SELECT ON FUTURE VIEWS IN DATABASE {db} TO ROLE {r};',
                f'GRANT ROLE {r} TO ROLE {rw};',
                f'GRANT INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN DATABASE {db} TO ROLE {rw};', f'GRANT INSERT, UPDATE, DELETE, TRUNCATE ON FUTURE TABLES IN DATABASE {db} TO ROLE {rw};',
                f'GRANT CREATE TABLE, CREATE VIEW ON ALL SCHEMAS IN DATABASE {db} TO ROLE {rw};', f'GRANT CREATE TABLE, CREATE VIEW ON FUTURE SCHEMAS IN DATABASE {db} TO ROLE {rw};',
                f'GRANT ROLE {rw} TO ROLE {own};', f'GRANT CREATE SCHEMA, MONITOR ON DATABASE {db} TO ROLE {own};',
                f'GRANT ROLE {own} TO ROLE SYSADMIN;']
        grants_graph[rw].add(r)
        grants_graph[own].add(rw)
        grants_graph['SYSADMIN'].add(own)
    for (env, p, layer), lvl in sorted(access.items()):
        role, ar = frole(env, p), arole(U(env, layer), lvl)
        sql.append(f'GRANT ROLE {ar} TO ROLE {role};')
        grants_graph[role].add(ar)
    for env, p in principals:
        team = p[:-4] if p.endswith('_svc') else p
        sql += [f'GRANT USAGE, OPERATE ON WAREHOUSE {warehouses[(env, team)]} TO ROLE {frole(env, p)};', f'GRANT ROLE {frole(env, p)} TO ROLE SYSADMIN;']
        grants_graph['SYSADMIN'].add(frole(env, p))
    svc_users = [(env, p) for env, p in principals if p.endswith('_svc')]
    if svc_users:
        sql += ['', '-- 4) Servis kullanıcıları (USERADMIN): parola yok, anahtar çiftiyle bağlanır', 'USE ROLE USERADMIN;']
        for env, p in svc_users:
            user = U('SVC', p[:-4], env)
            sql += [f"CREATE USER IF NOT EXISTS {user} TYPE = SERVICE DEFAULT_ROLE = {frole(env, p)} DEFAULT_WAREHOUSE = {warehouses[(env, p[:-4])]} "
                    f"RSA_PUBLIC_KEY = '<ACIK_ANAHTAR>' COMMENT = '{p[:-4]} yazma işleri ({env})';", f'GRANT ROLE {frole(env, p)} TO USER {user};']
    sql += ['', '-- 5) Bütçe izleme (ACCOUNTADMIN)', 'USE ROLE ACCOUNTADMIN;']
    monitors = 0
    for (env, team), wh in warehouses.items():
        quota = credit.get(team)
        if not quota:
            continue
        q = max(1, round(quota * (1 if env == 'prod' else args.dev_share)))
        rm = U(env, team, 'RM')
        sql += [f'CREATE RESOURCE MONITOR IF NOT EXISTS {rm} WITH CREDIT_QUOTA = {q} FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY '
                'TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND ON 110 PERCENT DO SUSPEND_IMMEDIATE;', f'ALTER WAREHOUSE {wh} SET RESOURCE_MONITOR = {rm};']
        monitors += 1
    # Effective access check: walk the role graph from every principal role.
    def effective(role):
        seen, stack, best = set(), [role], {}
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            for db, lvl in privs.get(x, ()):
                best[db] = max(best.get(db, 0), lvl)
            stack += list(grants_graph.get(x, ()))
        return best
    matrix, diffs = [], 0
    for env, p in principals:
        eff = effective(frole(env, p))
        for layer in layers:
            want = access.get((env, p, layer), 0)
            got = eff.get(U(env, layer), 0)
            diffs += want != got
            matrix.append({'ortam': env, 'kimlik': frole(env, p), 'veritabani': U(env, layer), 'istenen': ['—', 'okuma', 'yazma', 'sahip'][want], 'etkin': ['—', 'okuma', 'yazma', 'sahip'][got]})
    if 'ACCOUNTADMIN' in ' '.join(s for s in sql if s.startswith('GRANT ROLE ACCOUNTADMIN')):
        diffs += 1
    notes = ['Bölümleri sırasıyla ve başlıktaki rolle çalıştırın; servis kullanıcılarının <ACIK_ANAHTAR> alanına RSA açık anahtarını koyun (parola tanımlanmaz).',
             'Erişim rolleri veritabanındaki mevcut ve gelecek şema/tablo/görünümlere hak verir; yeni nesneler için ayrıca GRANT gerekmez. '
             f'Yetki matrisi üretilen GRANT’lerin rol grafiğinde yürütülmesiyle hesaplandı: {diffs} farklı hücre.',
             'Dev depoları istenenden bir boy küçük, dev bütçesi prod’un %' + f'{100 * args.dev_share:.0f}’i. Hesapta çalıştırılarak test edilmedi.']
    if svc_users:
        notes.insert(1, f'Prod’da insanlar yalnızca okur; yazma ve sahiplik {len(svc_users)} servis kimliğine verildi (--prod-human-write ile değiştirilebilir).')
    return Result(f'{len(dbs)} veritabanı, {len(principals) - len(svc_users)} ekip rolü + {len(svc_users)} servis rolü, {len(warehouses)} depo; yetki farkı {diffs}', list(matrix[0]), matrix,
                  {'veritabani': len(dbs), 'erisim_rolu': 3 * len(dbs), 'islevsel_rol': len(principals) - len(svc_users), 'servis_kullanicisi': len(svc_users),
                   'depo': len(warehouses), 'monitor': monitors, 'fark': diffs},
                  files={'snowflake_kurulum.sql': '\n'.join(sql) + '\n', 'yetki_matrisi.csv': (list(matrix[0]), matrix)}, notes=notes)


@op('unity-catalog-kurulumu', title='Ekip ve katman listesinden sıfırdan Databricks Unity Catalog kurulumu: kataloglar, şemalar, grup yetkileri',
    domain='lakehouse', product='Unity Catalog', tag='UC kurulumu', samples=EKIPLER,
    args=ENV_ARGS + [arg('--storage-root', default='abfss://lake@hesap.dfs.core.windows.net', help='Yönetilen depolama kökü (ortam/katman eklenir)')],
    description='Aynı ekip listesinden Unity Catalog kurulum SQL’i yazar: ortam başına katalog (yönetilen depolama konumuyla), katman şemaları, ekip gruplarına USE CATALOG / '
                'USE SCHEMA / SELECT, yazma için MODIFY ve CREATE TABLE/VOLUME, sahiplik için ALL PRIVILEGES ve OWNER; prod’da yazma servis sorumlusuna (service principal) '
                'verilir. Etkin haklar hesaplanıp istenen erişimle karşılaştırılır.',
    input='CSV/XLSX: ekip, katman, erisim', output='uc_kurulum.sql + yetki_matrisi.csv + rapor', command='python main.py --input ekipler.csv --storage-root abfss://lake@depo.dfs.core.windows.net',
    expect={'katalog': 2, 'sema': 8, 'grup': 4, 'servis_sorumlusu': 3, 'grant': 32, 'fark': 0})
def uc_setup(columns, rows, args):
    req, teams, layers, _, _ = read_teams(columns, rows)
    envs = [snake(e) for e in args.envs.split(',') if e.strip()]
    access = plan_access(req, envs, args.prod_human_write)
    name = lambda env, p: f'sp_{p[:-4]}_{env}' if p.endswith('_svc') else p
    sql = ['-- Unity Catalog kurulumu: metastore yöneticisi veya CREATE CATALOG hakkı olan bir kullanıcıyla çalıştırın.',
           '-- Gruplar ve servis sorumluları hesap konsolunda (veya SCIM ile) önceden oluşturulmalıdır.']
    grants, eff = 0, defaultdict(int)
    privs = {1: 'USE SCHEMA, SELECT', 2: 'USE SCHEMA, SELECT, MODIFY, CREATE TABLE, CREATE VOLUME, READ VOLUME, WRITE VOLUME', 3: 'ALL PRIVILEGES'}
    root = args.storage_root.rstrip('/')
    for env in envs:
        sql += ['', f'-- {env}', f"CREATE CATALOG IF NOT EXISTS {env} MANAGED LOCATION '{root}/{env}' COMMENT '{env} ortamı';"]
        users = sorted({p for e, p, _ in access if e == env})
        for p in users:
            sql.append(f'GRANT USE CATALOG ON CATALOG {env} TO `{name(env, p)}`;')
            grants += 1
        for layer in layers:
            sql.append(f"CREATE SCHEMA IF NOT EXISTS {env}.{layer} MANAGED LOCATION '{root}/{env}/{layer}' COMMENT '{layer} katmanı';")
            owners = [p for p in users if access.get((env, p, layer)) == 3]
            for p in users:
                lvl = access.get((env, p, layer))
                if not lvl:
                    continue
                sql.append(f'GRANT {privs[lvl]} ON SCHEMA {env}.{layer} TO `{name(env, p)}`;')
                grants += 1
                eff[(env, p, layer)] = lvl
            if owners:
                sql.append(f'ALTER SCHEMA {env}.{layer} OWNER TO `{name(env, owners[0])}`;')
    diffs = sum(eff.get(k, 0) != v for k, v in access.items())
    matrix = [{'ortam': e, 'kimlik': name(e, p), 'sema': f'{e}.{l}', 'istenen': ['—', 'okuma', 'yazma', 'sahip'][v], 'etkin': ['—', 'okuma', 'yazma', 'sahip'][eff.get((e, p, l), 0)]}
              for (e, p, l), v in sorted(access.items())]
    svc = sorted({name(e, p) for e, p, _ in access if p.endswith('_svc')})
    return Result(f'{len(envs)} katalog, {len(envs) * len(layers)} şema, {grants} GRANT; yetki farkı {diffs}', list(matrix[0]), matrix,
                  {'katalog': len(envs), 'sema': len(envs) * len(layers), 'grup': len(teams), 'servis_sorumlusu': len(svc), 'grant': grants, 'fark': diffs},
                  files={'uc_kurulum.sql': '\n'.join(sql) + '\n', 'yetki_matrisi.csv': (list(matrix[0]), matrix)},
                  notes=['Gruplar (' + ', '.join(teams) + ') ve servis sorumluları (' + (', '.join(svc) or 'yok') + ') hesap düzeyinde olmalı ve çalışma alanına atanmalıdır.',
                         'Yazma hakkı MODIFY + CREATE TABLE/VOLUME, sahiplik ALL PRIVILEGES + OWNER olarak verilir; haklar şema düzeyindedir, yeni tablolara kendiliğinden geçer. '
                         'Çalışma alanında çalıştırılarak test edilmedi.'])


@op('bigquery-terraform-kurulumu', title='Ekip ve katman listesinden sıfırdan BigQuery kurulumu (Terraform): ortam projeleri, veri kümeleri, IAM ve servis hesapları',
    domain='warehouse', product='BigQuery', tag='Terraform', samples=EKIPLER,
    args=ENV_ARGS + [arg('--project-prefix', default='sirket-veri', help='Ortam projelerinin ön eki (<önek>-<ortam>)'), arg('--domain', default='sirket.com', help='Google grupları alan adı'),
                     arg('--location', default='EU'), arg('--sandbox-days', type=int, default=30, help='sandbox katmanında tablo ömrü (gün)')],
    description='Aynı ekip listesinden Terraform yazar: ortam × katman google_bigquery_dataset’leri (etiketler, sandbox için varsayılan tablo ömrü), ekip gruplarına veri kümesi '
                'düzeyinde dataViewer / dataEditor / dataOwner, sorgu çalıştırmak için proje düzeyinde jobUser, prod yazma işleri için servis hesapları. Kaynak başvuruları ve '
                'etkin roller kodla denetlenir.',
    input='CSV/XLSX: ekip, katman, erisim', output='terraform/ (main.tf, variables.tf, outputs.tf) + yetki_matrisi.csv',
    command='python main.py --input ekipler.csv --project-prefix acme-data --domain acme.com.tr',
    expect={'veri_kumesi': 8, 'iam_uyeligi': 21, 'servis_hesabi': 3, 'fark': 0})
def bq_setup(columns, rows, args):
    req, teams, layers, _, _ = read_teams(columns, rows)
    envs = [snake(e) for e in args.envs.split(',') if e.strip()]
    access = plan_access(req, envs, args.prod_human_write)
    role = {1: 'roles/bigquery.dataViewer', 2: 'roles/bigquery.dataEditor', 3: 'roles/bigquery.dataOwner'}
    tf = ['terraform {', '  required_providers {', '    google = {', '      source  = "hashicorp/google"', '      version = ">= 5.0"', '    }', '  }', '}', '',
          'provider "google" {', '  region = var.region', '}', '']
    defined, refs, members, eff = set(), [], 0, {}
    for env in envs:
        for layer in layers:
            r = f'{env}_{layer}'
            defined.add(f'google_bigquery_dataset.{r}')
            tf += [f'resource "google_bigquery_dataset" "{r}" {{', f'  project    = "${{var.project_prefix}}-{env}"', f'  dataset_id = "{layer}"', '  location   = var.location',
                   f'  labels = {{', f'    ortam  = "{env}"', f'    katman = "{layer}"', '  }']
            if layer == 'sandbox':
                tf.append(f'  default_table_expiration_ms = {args.sandbox_days * 86400000}')
            tf += ['  delete_contents_on_destroy = false', '}', '']
    svc = sorted({(e, p) for e, p, _ in access if p.endswith('_svc')})
    for env, p in svc:
        r = f'{p[:-4]}_{env}'
        defined.add(f'google_service_account.{r}')
        tf += [f'resource "google_service_account" "{r}" {{', f'  project      = "${{var.project_prefix}}-{env}"', f'  account_id   = "{p[:-4].replace("_", "-")}-yazma"[:30]',
               f'  display_name = "{p[:-4]} yazma işleri ({env})"', '}', '']
        tf[-4] = f'  account_id   = "{(p[:-4].replace("_", "-") + "-yazma")[:30]}"'
    for (env, p, layer), lvl in sorted(access.items()):
        ds = f'google_bigquery_dataset.{env}_{layer}'
        member = f'"serviceAccount:${{google_service_account.{p[:-4]}_{env}.email}}"' if p.endswith('_svc') else f'"group:{p.replace("_", "-")}@${{var.domain}}"'
        if p.endswith('_svc'):
            refs.append(f'google_service_account.{p[:-4]}_{env}')
        refs.append(ds)
        tf += [f'resource "google_bigquery_dataset_iam_member" "{env}_{layer}_{p}" {{', f'  project    = {ds}.project', f'  dataset_id = {ds}.dataset_id',
               f'  role       = "{role[lvl]}"', f'  member     = {member}', '}', '']
        members += 1
        eff[(env, p, layer)] = lvl
    for env in envs:
        for p in sorted({p for e, p, _ in access if e == env}):
            member = f'"serviceAccount:${{google_service_account.{p[:-4]}_{env}.email}}"' if p.endswith('_svc') else f'"group:{p.replace("_", "-")}@${{var.domain}}"'
            tf += [f'resource "google_project_iam_member" "{env}_{p}_jobuser" {{', f'  project = "${{var.project_prefix}}-{env}"', '  role    = "roles/bigquery.jobUser"',
                   f'  member  = {member}', '}', '']
    text = '\n'.join(tf)
    if text.count('{') != text.count('}'):
        raise ValueError('İç hata: Terraform süslü parantezleri dengesiz.')
    missing = sorted(set(refs) - defined)
    if missing:
        raise ValueError('İç hata: tanımsız kaynak başvurusu: ' + ', '.join(missing))
    variables = ('variable "project_prefix" {\n  type    = string\n  default = "' + args.project_prefix + '"\n}\n\nvariable "domain" {\n  type    = string\n  default = "' + args.domain +
                 '"\n}\n\nvariable "location" {\n  type    = string\n  default = "' + args.location + '"\n}\n\nvariable "region" {\n  type    = string\n  default = "europe-west1"\n}\n')
    outputs = '\n'.join(f'output "{p[:-4]}_{env}_servis_hesabi" {{\n  value = google_service_account.{p[:-4]}_{env}.email\n}}\n' for env, p in svc)
    diffs = sum(eff.get(k) != v for k, v in access.items())
    matrix = [{'ortam': e, 'kimlik': p, 'veri_kumesi': f'{args.project_prefix}-{e}.{l}', 'rol': role[v]} for (e, p, l), v in sorted(access.items())]
    return Result(f'{len(envs) * len(layers)} veri kümesi, {members} IAM üyeliği, {len(svc)} servis hesabı', list(matrix[0]), matrix,
                  {'veri_kumesi': len(envs) * len(layers), 'iam_uyeligi': members, 'servis_hesabi': len(svc), 'fark': diffs},
                  files={'terraform/main.tf': text, 'terraform/variables.tf': variables, 'terraform/outputs.tf': outputs, 'yetki_matrisi.csv': (list(matrix[0]), matrix)},
                  notes=['terraform klasöründe: terraform init, terraform plan, inceleyip terraform apply. Ortam projeleri (<önek>-<ortam>) önceden açılmış olmalı.',
                         'Haklar veri kümesi düzeyindedir (proje düzeyinde dataViewer verilmez); sorgu çalıştırmak için ekiplere yalnızca jobUser proje düzeyinde verilir. '
                         'terraform validate ile denetlenmedi; süslü parantez dengesi ve kaynak başvuruları kodla denetlendi.'])


# ---------- Azure Data Factory ----------
@op('adf-metadata-pipeline', title='Tablo listesinden sıfırdan Azure Data Factory yükleme hattı: kontrol tablosu, tam/artımlı kopya ve watermark',
    domain='integration', product='Azure Data Factory', tag='metadata güdümlü', samples={'adf_tablolar.csv': 'adf_tablolar.csv'},
    args=[arg('--lake-root', default='bronze', help='Data Lake’te hedef kök klasör'), arg('--batch', type=int, default=4, help='ForEach paralellik (batchCount)')],
    description='“kaynak şema, tablo, yükleme tipi (tam/artımlı), watermark sütunu, anahtar, grup” listesinden metadata güdümlü ADF çözümü üretir: kontrol tablosu ve watermark '
                'saklı yordamı (SQL), parametreli bağlı hizmetler ve veri kümeleri, Lookup → ForEach → Switch(tam/artımlı) → Copy (Parquet) → watermark güncelleme hattı. '
                'Watermark’sız artımlı tablo tam yüklemeye alınır ve raporlanır; aktivite bağımlılıkları kodla denetlenir.',
    input='CSV/XLSX: kaynak_sema, tablo, yukleme, watermark_sutun, anahtar, grup', output='adf/ (pipeline, dataset, linkedService JSON) + kontrol_tablosu.sql',
    command='python main.py --input tablolar.csv --lake-root raw --batch 8',
    expect={'tablo': 8, 'artimli': 4, 'tam': 4, 'uyari': 2, 'aktivite': 7, 'grup': 3})
def adf_pipeline(columns, rows, args):
    for c in ('kaynak_sema', 'tablo', 'yukleme'):
        need(c, columns, 'girdi')
    items, warns = [], []
    for i, r in enumerate(rows, start=2):
        kind = r['yukleme'].strip().lower()
        if kind not in ('tam', 'artimli', 'artımlı'):
            raise ValueError(f'{i}. satır: yukleme “tam” veya “artimli” olmalı.')
        kind = 'artimli' if kind != 'tam' else 'tam'
        wm, key = r.get('watermark_sutun', '').strip(), r.get('anahtar', '').strip()
        if kind == 'artimli' and not wm:
            warns.append(f'{r["kaynak_sema"]}.{r["tablo"]}: artımlı istendi ama watermark sütunu yok; tam yüklemeye alındı.')
            kind = 'tam'
        if not key:
            warns.append(f'{r["kaynak_sema"]}.{r["tablo"]}: anahtar yok; hedefte tekrar yükleme mükerrer satır üretebilir.')
        items.append({'KaynakSema': r['kaynak_sema'].strip(), 'Tablo': r['tablo'].strip(), 'YuklemeTipi': kind, 'WatermarkSutun': wm, 'Anahtar': key,
                      'Grup': int(r.get('grup') or 1), 'HedefKlasor': f'{args.lake_root}/{snake(r["kaynak_sema"])}/{snake(r["tablo"])}'})
    lit = lambda s: "'" + s.replace("'", "''") + "'"
    ddl = ['-- Kontrol tablosu ve watermark yordamı (ADF’nin okuduğu kaynak veya ayrı yönetim veritabanı)',
           'CREATE TABLE etl.YuklemeKontrol (\n    Id INT IDENTITY(1,1) PRIMARY KEY,\n    KaynakSema NVARCHAR(128) NOT NULL,\n    Tablo NVARCHAR(128) NOT NULL,\n'
           '    YuklemeTipi NVARCHAR(10) NOT NULL CHECK (YuklemeTipi IN (\'tam\', \'artimli\')),\n    WatermarkSutun NVARCHAR(128) NULL,\n    SonWatermark NVARCHAR(50) NULL,\n'
           '    Anahtar NVARCHAR(256) NULL,\n    Grup INT NOT NULL DEFAULT 1,\n    HedefKlasor NVARCHAR(400) NOT NULL,\n    Aktif BIT NOT NULL DEFAULT 1,\n'
           '    SonCalisma DATETIME2 NULL,\n    CONSTRAINT uq_YuklemeKontrol UNIQUE (KaynakSema, Tablo)\n);', '']
    for it in items:
        ddl.append(f"INSERT INTO etl.YuklemeKontrol (KaynakSema, Tablo, YuklemeTipi, WatermarkSutun, SonWatermark, Anahtar, Grup, HedefKlasor) VALUES "
                   f"({lit(it['KaynakSema'])}, {lit(it['Tablo'])}, {lit(it['YuklemeTipi'])}, {lit(it['WatermarkSutun']) if it['WatermarkSutun'] else 'NULL'}, "
                   f"{lit('1900-01-01T00:00:00') if it['YuklemeTipi'] == 'artimli' else 'NULL'}, {lit(it['Anahtar']) if it['Anahtar'] else 'NULL'}, {it['Grup']}, {lit(it['HedefKlasor'])});")
    ddl += ['', 'CREATE OR ALTER PROCEDURE etl.usp_WatermarkGuncelle @KaynakSema NVARCHAR(128), @Tablo NVARCHAR(128), @YeniWatermark NVARCHAR(50)\nAS\nBEGIN\n    SET NOCOUNT ON;\n'
            '    UPDATE etl.YuklemeKontrol SET SonWatermark = @YeniWatermark, SonCalisma = SYSUTCDATETIME()\n    WHERE KaynakSema = @KaynakSema AND Tablo = @Tablo;\nEND;']
    ls = {'LS_SqlKaynak': {'name': 'LS_SqlKaynak', 'properties': {'type': 'SqlServer', 'typeProperties': {'connectionString': {
              'type': 'AzureKeyVaultSecret', 'store': {'referenceName': 'LS_KeyVault', 'type': 'LinkedServiceReference'}, 'secretName': 'kaynak-sql-baglanti'}},
              'connectVia': {'referenceName': 'SelfHostedIR', 'type': 'IntegrationRuntimeReference'}}},
          'LS_DataLake': {'name': 'LS_DataLake', 'properties': {'type': 'AzureBlobFS', 'typeProperties': {'url': 'https://<depo>.dfs.core.windows.net/'}}},
          'LS_KeyVault': {'name': 'LS_KeyVault', 'properties': {'type': 'AzureKeyVault', 'typeProperties': {'baseUrl': 'https://<kasa>.vault.azure.net/'}}}}
    datasets = {'DS_SqlKaynak': {'name': 'DS_SqlKaynak', 'properties': {'linkedServiceName': {'referenceName': 'LS_SqlKaynak', 'type': 'LinkedServiceReference'},
                                                                       'parameters': {'Sema': {'type': 'string'}, 'Tablo': {'type': 'string'}}, 'type': 'SqlServerTable',
                                                                       'typeProperties': {'schema': {'value': '@dataset().Sema', 'type': 'Expression'},
                                                                                          'table': {'value': '@dataset().Tablo', 'type': 'Expression'}}}},
                'DS_Lake_Parquet': {'name': 'DS_Lake_Parquet', 'properties': {'linkedServiceName': {'referenceName': 'LS_DataLake', 'type': 'LinkedServiceReference'},
                                                                             'parameters': {'Klasor': {'type': 'string'}, 'Dosya': {'type': 'string'}}, 'type': 'Parquet',
                                                                             'typeProperties': {'location': {'type': 'AzureBlobFSLocation', 'fileSystem': 'lake',
                                                                                                             'folderPath': {'value': '@dataset().Klasor', 'type': 'Expression'},
                                                                                                             'fileName': {'value': '@dataset().Dosya', 'type': 'Expression'}},
                                                                                                'compressionCodec': 'snappy'}}}}
    expr = lambda v: {'value': v, 'type': 'Expression'}
    sink = lambda: {'referenceName': 'DS_Lake_Parquet', 'type': 'DatasetReference', 'parameters': {
        'Klasor': expr("@concat(item().HedefKlasor, '/', formatDateTime(pipeline().TriggerTime, 'yyyy/MM/dd'))"),
        'Dosya': expr("@concat(item().Tablo, '_', formatDateTime(pipeline().TriggerTime, 'yyyyMMddHHmmss'), '.parquet')")}}
    src = {'referenceName': 'DS_SqlKaynak', 'type': 'DatasetReference', 'parameters': {'Sema': expr('@item().KaynakSema'), 'Tablo': expr('@item().Tablo')}}
    policy = {'timeout': '0.02:00:00', 'retry': 2, 'retryIntervalInSeconds': 120}
    copy = lambda name, query, deps=(): {'name': name, 'type': 'Copy', 'dependsOn': [{'activity': d, 'dependencyConditions': ['Succeeded']} for d in deps], 'policy': policy,
                                         'typeProperties': {'source': {'type': 'SqlServerSource', 'sqlReaderQuery': expr(query), 'queryTimeout': '02:00:00'},
                                                            'sink': {'type': 'ParquetSink', 'storeSettings': {'type': 'AzureBlobFSWriteSettings'}}, 'enableStaging': False},
                                         'inputs': [src], 'outputs': [sink()]}
    new_wm = {'name': 'Yeni_Watermark', 'type': 'Lookup', 'dependsOn': [], 'policy': policy,
              'typeProperties': {'source': {'type': 'SqlServerSource', 'sqlReaderQuery': expr(
                  "@concat('SELECT CONVERT(NVARCHAR(50), MAX([', item().WatermarkSutun, ']), 126) AS YeniWatermark FROM [', item().KaynakSema, '].[', item().Tablo, ']')")},
                  'dataset': src, 'firstRowOnly': True}}
    incr_copy = copy('Artimli_Kopya', "@concat('SELECT * FROM [', item().KaynakSema, '].[', item().Tablo, '] WHERE [', item().WatermarkSutun, '] > ''', "
                                      "item().SonWatermark, ''' AND [', item().WatermarkSutun, '] <= ''', activity('Yeni_Watermark').output.firstRow.YeniWatermark, '''')", ['Yeni_Watermark'])
    update = {'name': 'Watermark_Guncelle', 'type': 'SqlServerStoredProcedure', 'dependsOn': [{'activity': 'Artimli_Kopya', 'dependencyConditions': ['Succeeded']}], 'policy': policy,
              'linkedServiceName': {'referenceName': 'LS_SqlKaynak', 'type': 'LinkedServiceReference'},
              'typeProperties': {'storedProcedureName': 'etl.usp_WatermarkGuncelle', 'storedProcedureParameters': {
                  'KaynakSema': {'value': expr('@item().KaynakSema'), 'type': 'String'}, 'Tablo': {'value': expr('@item().Tablo'), 'type': 'String'},
                  'YeniWatermark': {'value': expr("@activity('Yeni_Watermark').output.firstRow.YeniWatermark"), 'type': 'String'}}}}
    for a in (update['typeProperties']['storedProcedureParameters'].values()):
        a['value'] = a['value']['value']
    switch = {'name': 'Yukleme_Tipi', 'type': 'Switch', 'dependsOn': [], 'typeProperties': {'on': expr('@item().YuklemeTipi'), 'cases': [
        {'value': 'tam', 'activities': [copy('Tam_Kopya', "@concat('SELECT * FROM [', item().KaynakSema, '].[', item().Tablo, ']')")]},
        {'value': 'artimli', 'activities': [new_wm, incr_copy, update]}], 'defaultActivities': []}}
    pipeline = {'name': 'PL_Kontrol_Yukleme', 'properties': {
        'parameters': {'Grup': {'type': 'int', 'defaultValue': 1}},
        'activities': [
            {'name': 'Tablo_Listesi', 'type': 'Lookup', 'dependsOn': [], 'policy': policy, 'typeProperties': {
                'source': {'type': 'SqlServerSource', 'sqlReaderQuery': expr("@concat('SELECT * FROM etl.YuklemeKontrol WHERE Aktif = 1 AND Grup = ', string(pipeline().parameters.Grup))")},
                'dataset': {'referenceName': 'DS_SqlKaynak', 'type': 'DatasetReference', 'parameters': {'Sema': 'etl', 'Tablo': 'YuklemeKontrol'}}, 'firstRowOnly': False}},
            {'name': 'Her_Tablo', 'type': 'ForEach', 'dependsOn': [{'activity': 'Tablo_Listesi', 'dependencyConditions': ['Succeeded']}],
             'typeProperties': {'items': expr('@activity(\'Tablo_Listesi\').output.value'), 'isSequential': False, 'batchCount': args.batch, 'activities': [switch]}}],
        'annotations': ['kurulum-blogu']}}
    groups = sorted({it['Grup'] for it in items})
    master = {'name': 'PL_Ana', 'properties': {'activities': [
        {'name': f'Grup_{g}', 'type': 'ExecutePipeline', 'dependsOn': ([{'activity': f'Grup_{groups[i - 1]}', 'dependencyConditions': ['Succeeded']}] if i else []),
         'typeProperties': {'pipeline': {'referenceName': 'PL_Kontrol_Yukleme', 'type': 'PipelineReference'}, 'waitOnCompletion': True, 'parameters': {'Grup': g}}}
        for i, g in enumerate(groups)]}}
    trigger = {'name': 'TR_Gece', 'properties': {'type': 'ScheduleTrigger', 'typeProperties': {'recurrence': {'frequency': 'Day', 'interval': 1, 'startTime': '2026-01-01T01:00:00',
                                                                                                         'timeZone': 'Turkey Standard Time', 'schedule': {'hours': [1], 'minutes': [0]}}},
                                             'pipelines': [{'pipelineReference': {'referenceName': 'PL_Ana', 'type': 'PipelineReference'}}]}}
    # Dependency check across every container (same rules as the adf-denetim block).
    count = 0

    def check(acts):
        nonlocal count
        names = {a['name'] for a in acts}
        for a in acts:
            count += 1
            for d in a.get('dependsOn', []):
                if d['activity'] not in names:
                    raise ValueError(f'İç hata: {a["name"]} olmayan aktiviteye bağlı: {d["activity"]}')
            tp = a.get('typeProperties', {})
            for key in ('activities', 'ifTrueActivities', 'ifFalseActivities', 'defaultActivities'):
                if tp.get(key):
                    check(tp[key])
            for case in tp.get('cases', []):
                check(case['activities'])
    check(pipeline['properties']['activities'])
    assert sum(1 for _ in walk_activities(pipeline['properties']['activities'])) == count
    for text in [json.dumps(x) for x in (pipeline, master)]:
        for e in re.findall(r'"(@[^"]*)"', text):
            if e.count('(') != e.count(')'):
                raise ValueError(f'İç hata: parantez dengesiz ifade: {e}')
    files = {'adf/pipeline/PL_Kontrol_Yukleme.json': json.dumps(pipeline, ensure_ascii=False, indent=2), 'adf/pipeline/PL_Ana.json': json.dumps(master, ensure_ascii=False, indent=2),
             'adf/trigger/TR_Gece.json': json.dumps(trigger, ensure_ascii=False, indent=2), 'kontrol_tablosu.sql': '\n'.join(ddl) + '\n'}
    files.update({f'adf/linkedService/{k}.json': json.dumps(v, ensure_ascii=False, indent=2) for k, v in ls.items()})
    files.update({f'adf/dataset/{k}.json': json.dumps(v, ensure_ascii=False, indent=2) for k, v in datasets.items()})
    out = [{k: it[k] for k in ('KaynakSema', 'Tablo', 'YuklemeTipi', 'WatermarkSutun', 'Grup', 'HedefKlasor')} for it in items]
    return Result(f'{len(items)} tablo, {len(groups)} grup; {sum(it["YuklemeTipi"] == "artimli" for it in items)} artımlı', list(out[0]), out,
                  {'tablo': len(items), 'artimli': sum(it['YuklemeTipi'] == 'artimli' for it in items), 'tam': sum(it['YuklemeTipi'] == 'tam' for it in items), 'uyari': len(warns),
                   'aktivite': count, 'grup': len(groups)}, files=files,
                  notes=['adf klasörünü Data Factory Git deposunun köküne kopyalayın (pipeline/, dataset/, linkedService/, trigger/ klasörleri ADF’nin biçimidir) veya Studio’da '
                         'JSON’u yapıştırın. Önce kontrol_tablosu.sql’i çalıştırın; <depo>, <kasa> ve Self-hosted IR adını kendi ortamınıza göre değiştirin.',
                         'Gruplar PL_Ana’da sırayla, grup içindeki tablolar paralel (batchCount) yüklenir. Artımlı yüklemede yeni watermark kopyadan önce alınır; kopya başarısız '
                         'olursa watermark ilerlemez. Aktivite bağımlılıkları ve ifade parantezleri kodla denetlendi; Data Factory’de çalıştırılarak test edilmedi.'] + warns)


# ---------- Airflow ----------
def cron_ok(expr):
    parts = expr.split()
    if len(parts) != 5:
        return False
    limits = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    for p, (lo, hi) in zip(parts, limits):
        for piece in p.split(','):
            m = re.fullmatch(r'(\*|\d+(?:-\d+)?)(?:/(\d+))?', piece)
            if not m:
                return False
            if m.group(1) != '*':
                nums = [int(x) for x in m.group(1).split('-')]
                if any(n < lo or n > hi for n in nums):
                    return False
    return True


@op('airflow-dag-kurulumu', title='Görev listesinden sıfırdan Airflow DAG’i: operatörler, bağımlılıklar, havuzlar, yeniden deneme ve süre kontrolü',
    domain='orchestration', product='Airflow', tag='DAG kurulumu', samples={'dag_gorevler.csv': 'dag_gorevler.csv'},
    args=[arg('--dag-id', default='gece_veri_hatti'), arg('--schedule', default='0 2 * * *'), arg('--timezone', default='Europe/Istanbul'), arg('--start', default='2026-01-01'),
          arg('--deadline', default='05:00', help='Hattın bitmesi gereken saat (SS:DD)'), arg('--sql-conn', default='ambar'), arg('--dbt-dir', default='/opt/airflow/dbt'),
          arg('--owner', default='veri-ekibi')],
    description='“görev, tür (sensor/bash/python/sql/dbt/dbt_test), komut, bağımlı olduğu görevler, havuz, deneme, süre” listesinden Airflow 2.x/3.x ile uyumlu DAG dosyası yazar: '
                'dosya sensörü reschedule modunda, dbt görevleri, SQL görevleri ortak bağlantıyla, havuzlar ve yeniden deneme ayarları, görev başına zaman aşımı. Döngü ve bilinmeyen '
                'bağımlılık reddedilir; kritik yol hesaplanıp bitiş saati son saatle karşılaştırılır; havuz boyutları için pools.json üretilir. DAG Python olarak ayrıştırılır.',
    input='CSV/XLSX: gorev, tur, komut, bagimli, havuz, deneme, sure_dk', output='dags/<dag_id>.py + pools.json + rapor',
    command='python main.py --input gorevler.csv --dag-id erp_gece --schedule "30 1 * * *" --deadline 06:00',
    expect={'gorev': 7, 'bagimlilik': 6, 'kritik_yol_dk': 130, 'bitis': '04:10', 'sure_payi_dk': 50, 'havuz': 4})
def airflow_dag(columns, rows, args):
    for c in ('gorev', 'tur', 'komut'):
        need(c, columns, 'girdi')
    if not cron_ok(args.schedule):
        raise ValueError(f'--schedule geçerli bir cron ifadesi değil: {args.schedule}')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.dag_id):
        raise ValueError('--dag-id harf, rakam, nokta, tire ve alt çizgiden oluşmalı.')
    tasks = {}
    for i, r in enumerate(rows, start=2):
        tid = snake(r['gorev'])
        if tid in tasks:
            raise ValueError(f'{i}. satır: {tid} görevi iki kez tanımlı.')
        kind = r['tur'].strip().lower()
        if kind not in ('sensor', 'bash', 'python', 'sql', 'dbt', 'dbt_test'):
            raise ValueError(f'{i}. satır: tur sensor, bash, python, sql, dbt veya dbt_test olmalı (“{kind}”).')
        if kind == 'python' and not re.fullmatch(r'[\w.]+:\w+', r['komut'].strip()):
            raise ValueError(f'{i}. satır: python görevinde komut “modul.yol:fonksiyon” biçiminde olmalı.')
        tasks[tid] = {'id': tid, 'kind': kind, 'cmd': r['komut'].strip(), 'deps': [snake(d) for d in r.get('bagimli', '').split(',') if d.strip()],
                      'pool': snake(r.get('havuz', '') or 'default_pool'), 'retries': int(numbers([r], 'deneme')[0] or 0) if r.get('deneme', '').strip() else 1,
                      'minutes': int(numbers([r], 'sure_dk')[0] or 10) if r.get('sure_dk', '').strip() else 10}
    unknown = [(t, d) for t in tasks.values() for d in t['deps'] if d not in tasks]
    if unknown:
        raise ValueError('Bilinmeyen bağımlılık: ' + ', '.join(f'{t["id"]} → {d}' for t, d in unknown))
    indeg = {t: len(v['deps']) for t, v in tasks.items()}
    children = defaultdict(list)
    for t, v in tasks.items():
        for d in v['deps']:
            children[d].append(t)
    order, queue = [], deque(t for t in tasks if not indeg[t])
    finish = {}
    while queue:
        t = queue.popleft()
        order.append(t)
        finish[t] = max([finish[d] for d in tasks[t]['deps']] or [0]) + tasks[t]['minutes']
        for c in children[t]:
            indeg[c] -= 1
            if not indeg[c]:
                queue.append(c)
    if len(order) != len(tasks):
        raise ValueError('Görevler arasında döngü var: ' + ', '.join(t for t in tasks if t not in order))
    critical = max(finish.values())
    end_task = max(finish, key=finish.get)
    path = [end_task]
    while tasks[path[-1]]['deps']:
        path.append(max(tasks[path[-1]]['deps'], key=finish.get))
    path.reverse()
    sched = args.schedule.split()
    start_min = int(sched[1]) * 60 + int(sched[0]) if sched[0].isdigit() and sched[1].isdigit() else None
    dl = re.fullmatch(r'(\d{1,2}):(\d{2})', args.deadline)
    if not dl:
        raise ValueError('--deadline SS:DD biçiminde olmalı.')
    end = start_min + critical if start_min is not None else None
    slack = (int(dl.group(1)) * 60 + int(dl.group(2)) - end) if end is not None else None
    # Pool sizes: the most tasks of one pool that can run at the same time (same earliest start window).
    windows = {t: (finish[t] - tasks[t]['minutes'], finish[t]) for t in tasks}
    pools = {}
    for p in sorted({t['pool'] for t in tasks.values()}):
        mine = [windows[t] for t in tasks if tasks[t]['pool'] == p]
        pools[p] = max(sum(1 for s, e in mine if s <= x < e) for x, _ in mine)
    imports = {'sensor': 'FileSensor', 'bash': 'BashOperator', 'dbt': 'BashOperator', 'dbt_test': 'BashOperator', 'python': 'PythonOperator', 'sql': 'SQLExecuteQueryOperator'}
    used = {imports[t['kind']] for t in tasks.values()}
    py_imports = sorted({t['cmd'] for t in tasks.values() if t['kind'] == 'python'})
    lines = [f'"""{args.dag_id}: kurulum bloğunun görev listesinden ürettiği DAG. Kritik yol {critical} dk ({" → ".join(path)})."""', 'from datetime import timedelta', '']
    lines.append('try:  # Airflow 3')
    lines.append('    from airflow.sdk import DAG')
    if 'BashOperator' in used:
        lines.append('    from airflow.providers.standard.operators.bash import BashOperator')
    if 'PythonOperator' in used:
        lines.append('    from airflow.providers.standard.operators.python import PythonOperator')
    if 'FileSensor' in used:
        lines.append('    from airflow.providers.standard.sensors.filesystem import FileSensor')
    lines.append('except ImportError:  # Airflow 2.x')
    lines.append('    from airflow import DAG')
    if 'BashOperator' in used:
        lines.append('    from airflow.operators.bash import BashOperator')
    if 'PythonOperator' in used:
        lines.append('    from airflow.operators.python import PythonOperator')
    if 'FileSensor' in used:
        lines.append('    from airflow.sensors.filesystem import FileSensor')
    if 'SQLExecuteQueryOperator' in used:
        lines.append('from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator')
    for spec in py_imports:
        mod, fn = spec.split(':')
        lines.append(f'from {mod} import {fn} as {snake(mod)}_{fn}')
    y, mo, d = (int(x) for x in args.start.split('-'))
    lines += ['', 'import pendulum', '', 'default_args = {', f'    "owner": "{args.owner}",', '    "retries": 1,', '    "retry_delay": timedelta(minutes=5),',
              '    "retry_exponential_backoff": True,', '}', '', 'with DAG(', f'    dag_id="{args.dag_id}",', f'    schedule="{args.schedule}",',
              f'    start_date=pendulum.datetime({y}, {mo}, {d}, tz="{args.timezone}"),', '    catchup=False,', '    max_active_runs=1,',
              f'    dagrun_timeout=timedelta(minutes={max(critical * 2, 60)}),', '    default_args=default_args,', '    tags=["kurulum-blogu"],',
              f'    doc_md="Son bitiş {args.deadline}; kritik yol {critical} dk.",', ') as dag:']
    for t in order:
        v = tasks[t]
        common = f'task_id="{t}", pool="{v["pool"]}", retries={v["retries"]}, execution_timeout=timedelta(minutes={v["minutes"] * 3})'
        cmd = json.dumps(v['cmd'], ensure_ascii=False)
        if v['kind'] == 'sensor':  # a rescheduling sensor is bounded by its own timeout, not execution_timeout
            common = f'task_id="{t}", pool="{v["pool"]}", retries={v["retries"]}'
            body = f'FileSensor({common}, filepath={cmd}, fs_conn_id="fs_default", mode="reschedule", poke_interval=300, timeout={v["minutes"] * 60 * 4})'
        elif v['kind'] == 'bash':
            body = f'BashOperator({common}, bash_command={cmd})'
        elif v['kind'] in ('dbt', 'dbt_test'):
            verb = 'run' if v['kind'] == 'dbt' else 'test'
            dbt_cmd = json.dumps(f'cd {args.dbt_dir} && dbt {verb} --select {v["cmd"]}', ensure_ascii=False)
            body = f'BashOperator({common}, bash_command={dbt_cmd})'
        elif v['kind'] == 'python':
            mod, fn = v['cmd'].split(':')
            body = f'PythonOperator({common}, python_callable={snake(mod)}_{fn})'
        else:
            body = f'SQLExecuteQueryOperator({common}, conn_id="{args.sql_conn}", sql={cmd})'
        lines.append(f'    {t} = {body}')
    lines.append('')
    edges = 0
    for t in order:
        for d in tasks[t]['deps']:
            lines.append(f'    {d} >> {t}')
            edges += 1
    text = '\n'.join(lines) + '\n'
    ast.parse(text)
    pool_json = {p: {'slots': max(1, n), 'description': 'kurulum bloğu önerisi', 'include_deferred': False} for p, n in pools.items() if p != 'default_pool'}
    out = [{'gorev': t, 'tur': tasks[t]['kind'], 'baslangic_dk': finish[t] - tasks[t]['minutes'], 'bitis_dk': finish[t], 'havuz': tasks[t]['pool'],
            'kritik': 'evet' if t in path else ''} for t in order]
    bitis = f'{(end // 60) % 24:02d}:{end % 60:02d}' if end is not None else ''
    notes = [f'dags/{args.dag_id}.py dosyasını Airflow’un dags klasörüne koyun; havuzlar için: airflow pools import pools.json. SQL görevleri “{args.sql_conn}” bağlantısını kullanır.',
             f'Kritik yol: {" → ".join(path)} ({critical} dk). Sensör reschedule modundadır, beklerken işçi tutmaz. DAG Python olarak ayrıştırıldı; Airflow’da çalıştırılarak test edilmedi.']
    if slack is not None and slack < 0:
        notes.insert(0, f'Hat son saati {args.deadline} kaçırıyor: tahmini bitiş {bitis}, {-slack} dk geç.')
    return Result(f'{args.dag_id}: {len(tasks)} görev, {edges} bağımlılık; tahmini bitiş {bitis}', list(out[0]), out,
                  {'gorev': len(tasks), 'bagimlilik': edges, 'kritik_yol_dk': critical, 'bitis': bitis, 'sure_payi_dk': slack, 'havuz': len(pools)},
                  files={f'dags/{args.dag_id}.py': text, 'pools.json': json.dumps(pool_json, ensure_ascii=False, indent=2)}, notes=notes)


# ---------- scikit-learn ----------
TRAIN = r'''"""Eğitim: config.json’daki özelliklerle sınıflandırma/regresyon modeli kurar, çapraz doğrulama yapar, en iyiyi kaydeder.
Kullanım: python train.py --data egitim.csv   (pip install -r requirements.txt)"""
import argparse, json
from pathlib import Path
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold, KFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))


def candidates():
    if CFG['gorev'] == 'siniflandirma':
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        w = 'balanced' if CFG['dengesiz'] else None
        return {'lojistik': LogisticRegression(max_iter=2000, class_weight=w), 'gbm': HistGradientBoostingClassifier(class_weight=w, random_state=42)}
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    return {'ridge': Ridge(), 'gbm': HistGradientBoostingRegressor(random_state=42)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--sep', default=CFG['ayirici'])
    p.add_argument('--decimal', default=CFG['ondalik'])
    a = p.parse_args()
    df = pd.read_csv(a.data, sep=a.sep, decimal=a.decimal)
    X, y = df[CFG['sayisal'] + CFG['kategorik']], df[CFG['hedef']]
    pre = ColumnTransformer([
        ('sayi', Pipeline([('eksik', SimpleImputer(strategy='median')), ('olcek', StandardScaler())]), CFG['sayisal']),
        ('kat', Pipeline([('eksik', SimpleImputer(strategy='most_frequent')), ('kod', OneHotEncoder(handle_unknown='ignore', min_frequency=0.01))]), CFG['kategorik'])])
    clf = CFG['gorev'] == 'siniflandirma'
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y if clf else None)
    cv = StratifiedKFold(5, shuffle=True, random_state=42) if clf else KFold(5, shuffle=True, random_state=42)
    scoring = CFG['metrikler']
    results = {}
    for name, model in candidates().items():
        pipe = Pipeline([('on', pre), ('model', model)])
        s = cross_validate(pipe, X_tr, y_tr, cv=cv, scoring=scoring)
        results[name] = {m: round(float(s[f'test_{m}'].mean()), 4) for m in scoring}
        print(name, results[name])
    best = max(results, key=lambda n: results[n][scoring[0]])
    pipe = Pipeline([('on', pre), ('model', candidates()[best])]).fit(X_tr, y_tr)
    from sklearn.metrics import get_scorer
    test = {m: round(float(get_scorer(m)(pipe, X_te, y_te)), 4) for m in scoring}
    joblib.dump(pipe, HERE / 'model.joblib')
    (HERE / 'metrikler.json').write_text(json.dumps({'cv': results, 'secilen': best, 'test': test, 'taban': CFG['taban']}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Seçilen:', best, 'test:', test, '| taban:', CFG['taban'])


if __name__ == '__main__':
    main()
'''

PREDICT = r'''"""Tahmin: kaydedilen modelle yeni dosyayı puanlar. Kullanım: python predict.py --data yeni.csv --out tahmin.csv"""
import argparse, json
from pathlib import Path
import joblib
import pandas as pd

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--out', default='tahmin.csv')
    a = p.parse_args()
    df = pd.read_csv(a.data, sep=CFG['ayirici'], decimal=CFG['ondalik'])
    missing = [c for c in CFG['sayisal'] + CFG['kategorik'] if c not in df.columns]
    if missing:
        raise SystemExit('Eksik sütun: ' + ', '.join(missing))
    model = joblib.load(HERE / 'model.joblib')
    X = df[CFG['sayisal'] + CFG['kategorik']]
    out = df[[c for c in CFG['kimlik'] if c in df.columns]].copy()
    if CFG['gorev'] == 'siniflandirma':
        out['olasilik'] = model.predict_proba(X)[:, 1]
    out['tahmin'] = model.predict(X)
    out.to_csv(a.out, index=False, sep=CFG['ayirici'])
    print(f'{len(out)} satır puanlandı: {a.out}')


if __name__ == '__main__':
    main()
'''


@op('sklearn-proje-kurulumu', title='Eğitim dosyasından sıfırdan scikit-learn projesi: özellik seçimi, sızıntı ayıklama, eğitim ve tahmin betikleri',
    domain='ml', product='scikit-learn', tag='ML proje', samples={'egitim_verisi.csv': 'egitim_verisi.csv'},
    args=[arg('--target', default='churn', help='Hedef sütun')],
    description='Hedef sütunu verilen bir eğitim dosyasından çalışır bir proje kurar: görev tipini (sınıflandırma/regresyon) ve sınıf dengesini belirler, kimlik sütunlarını ve hedefi '
                'sızdıran sütunları (ör. yalnızca ayrılan müşteride dolu “iptal_tarihi”) çıkarır, sayısal ve kategorik özellikleri ayırır; ColumnTransformer + iki aday model, '
                'katmanlı çapraz doğrulama, test seti ve taban çizgisiyle karşılaştırma yapan train.py ile predict.py yazar.',
    input='CSV/XLSX (satır = örnek, hedef sütun dahil)', output='ml-proje/ (config.json, train.py, predict.py, requirements.txt, README.md) + rapor',
    command='python main.py --input musteriler.csv --target churn',
    expect={'ozellik': 4, 'sayisal': 3, 'kategorik': 1, 'cikarilan': 2, 'sizinti': 1, 'gorev': 'siniflandirma'})
def sklearn_project(columns, rows, args):
    target = need(args.target, columns, '--target')
    vals = {c: [r[c].strip() for r in rows] for c in columns}
    kinds = {c: infer_column(vals[c])[0] for c in columns}
    tv = vals[target]
    classes = Counter(v for v in tv if v)
    task = 'siniflandirma' if len(classes) <= 10 and kinds[target] in ('int', 'bool', 'text') else 'regresyon'
    if task == 'regresyon' and kinds[target] not in ('int', 'bigint', 'decimal'):
        raise ValueError(f'{target} sayısal değil ve 10’dan fazla sınıfı var; hedef sütunu kontrol edin.')
    drop, leak, feats_num, feats_cat = [], [], [], []
    n = len(rows)
    for c in columns:
        if c == target:
            continue
        v = vals[c]
        filled = [x for x in v if x]
        if IDLIKE.search(c) or (len(set(filled)) == n and kinds[c] == 'text'):
            drop.append((c, 'kimlik: her satırda farklı, genellemez'))
            continue
        if task == 'siniflandirma':
            pattern = defaultdict(set)
            for x, y in zip(v, tv):
                pattern[bool(x)].add(y)
            if len(filled) < n and all(len(s) == 1 for s in pattern.values()) and len(pattern) == 2:
                leak.append(c)
                drop.append((c, f'sızıntı: doluluğu hedefi birebir belirliyor ({target}={next(iter(pattern[True]))} iken dolu)'))
                continue
            by_value = defaultdict(set)
            for x, y in zip(v, tv):
                if x:
                    by_value[x].add(y)
            if len(by_value) > 2 and all(len(s) == 1 for s in by_value.values()) and len(by_value) < n:
                leak.append(c)
                drop.append((c, 'sızıntı: her değeri tek bir sınıfa düşüyor'))
                continue
        if kinds[c] in ('date', 'datetime'):
            drop.append((c, 'tarih: doğrudan kullanılmaz (gün sayısı gibi türev özellik çıkarın)'))
        elif PII.search(c):
            drop.append((c, 'kişisel veri'))
        elif kinds[c] in ('int', 'bigint', 'decimal'):
            feats_num.append(c)
        elif len(set(filled)) <= 50:
            feats_cat.append(c)
        else:
            drop.append((c, f'serbest metin ({len(set(filled))} farklı değer)'))
    if not feats_num and not feats_cat:
        raise ValueError('Kullanılabilir özellik kalmadı.')
    raw = Path(args.input).read_text(encoding='utf-8-sig', errors='replace')[:20000] if Path(args.input).suffix.lower() not in ('.xlsx', '.xlsm') else ','
    sep = max([';', ',', '\t', '|'], key=raw.count)
    styles = {tabular.number_style([x for x in vals[c] if x]) for c in feats_num}
    if task == 'siniflandirma':
        top, top_n = classes.most_common(1)[0]
        minority = min(classes.values()) / n
        base = {'cogunluk_sinifi': top, 'dogruluk': round(top_n / n, 4), 'azinlik_orani': round(minority, 4)}
        metrics = ['roc_auc', 'average_precision', 'f1'] if len(classes) == 2 else ['f1_macro', 'accuracy']
    else:
        ys = [x for x in numbers(rows, target) if x is not None]
        mean = sum(ys) / len(ys)
        base = {'ortalama_tahmin_mae': round(sum(abs(y - mean) for y in ys) / len(ys), 4)}
        metrics = ['neg_mean_absolute_error', 'r2']
        minority = None
    cfg = {'hedef': target, 'gorev': task, 'sayisal': feats_num, 'kategorik': feats_cat, 'kimlik': [c for c, why in drop if why.startswith('kimlik')],
           'cikarilan': {c: why for c, why in drop}, 'dengesiz': bool(minority is not None and minority < 0.2), 'metrikler': metrics, 'taban': base,
           'ayirici': sep, 'ondalik': ',' if 'tr' in styles else '.'}
    ast.parse(TRAIN)
    ast.parse(PREDICT)
    readme = (f'# {target} için {task} projesi\n\n```\npip install -r requirements.txt\npython train.py --data {Path(args.input).name}\npython predict.py --data yeni.csv\n```\n\n'
              f'Özellikler: {", ".join(feats_num + feats_cat)}. Çıkarılanlar: ' + '; '.join(f'{c} ({w})' for c, w in drop) + '.\n\n'
              f'Taban çizgisi: {json.dumps(base, ensure_ascii=False)}. Modelin bunu açıkça geçmesi gerekir; geçmiyorsa özellikleri gözden geçirin.\n')
    out = [{'sutun': c, 'rol': 'hedef' if c == target else 'sayısal' if c in feats_num else 'kategorik' if c in feats_cat else 'çıkarıldı',
            'neden': dict(drop).get(c, '')} for c in columns]
    notes = [f'ml-proje klasöründe: pip install -r requirements.txt, sonra python train.py --data {Path(args.input).name}. Betikler Python olarak ayrıştırıldı; '
             'scikit-learn ile çalıştırılarak test edilmedi.']
    if leak:
        notes.insert(0, 'Sızıntı: ' + ', '.join(leak) + ' hedefi doğrudan ele veriyor; tahmin anında bilinmeyeceği için modelden çıkarıldı.')
    if cfg['dengesiz']:
        notes.append(f'Sınıflar dengesiz (azınlık %{100 * minority:.1f}); class_weight="balanced" ve average_precision kullanılır, doğruluk yanıltıcıdır.')
    return Result(f'{task}: {len(feats_num) + len(feats_cat)} özellik, {len(drop)} sütun çıkarıldı', list(out[0]), out,
                  {'ozellik': len(feats_num) + len(feats_cat), 'sayisal': len(feats_num), 'kategorik': len(feats_cat), 'cikarilan': len(drop), 'sizinti': len(leak), 'gorev': task},
                  files={'ml-proje/config.json': json.dumps(cfg, ensure_ascii=False, indent=2), 'ml-proje/train.py': TRAIN, 'ml-proje/predict.py': PREDICT,
                         'ml-proje/requirements.txt': 'scikit-learn>=1.4\npandas>=2.1\njoblib>=1.3\n', 'ml-proje/README.md': readme}, notes=notes)
