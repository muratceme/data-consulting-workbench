"""Sıfırdan kurulum: bir klasördeki tablolardan (olgu + boyut dosyaları) çok tablolu BI uygulaması ve panosu üretir.
Anahtarlar ve ilişkiler veriden bulunur ve doğrulanır (üst tabloda anahtar tekil mi, alt tabloda eşleşmeyen değer var mı); olgu tablosundan
yayılan bir ağaç kurulur, ağaca girmeyen ilişki Qlik'te alan adı ayrılarak, Power BI'da pasif ilişkiyle bırakılır. Böylece Qlik'te sentetik anahtar
ve döngü, Power BI'da belirsiz yol oluşmaz. Qlik Sense, Power BI (PBIP), Looker, Tableau, Superset ve Metabase için açılmaya hazır proje yazar;
panodaki rakamlar aynı dosyalardan Python ve SQLite ile hesaplanır. Hiçbir platforma bağlanmaz."""
import ast, io, json, re, sqlite3, uuid, zipfile
from collections import Counter, defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr
import tabular
from sqlgen import infer_column, snake, quote as sql_quote
import bigen
from rapor import label, tmdl_name, TMDL_TYPE, SCHEMA, TW_TYPE, QLIK_HEADER, PII, IDLIKE, AVG, PRIMARY, AY, yaml_dump, preview_html
from islem import op, arg, Result, numbers

REQUIRES = ['sema_farki', 'sqlgen', 'bigen', 'rapor']
D = 'bi'
MAGAZA = {f'magaza/{n}.csv': f'magaza/{n}.csv' for n in ('satis', 'musteri', 'urun', 'magaza')}
MODEL_ARGS = [arg('--title', default='', help='Uygulama/pano başlığı (boşsa klasör adından)'),
              arg('--fact', default='', help='Olgu tablosunun dosya adı (boşsa ilişkilerden bulunur)'),
              arg('--measures', default='', help='Olgu tablosundaki ölçü sütunları (boşsa sayısal sütunlar, en çok 4)'),
              arg('--relations', default='', help='Adı tutmayan ilişkiler: alt.sutun=ust.sutun;... (dosya adı.sütun)')]
CAL = ['Yıl', 'Çeyrek', 'Ay', 'Yıl Ay']


def entity(col):
    """musteri_id -> musteri"""
    return re.sub(r'_(no|id|kod|kodu|numara|numarasi|key)$', '', snake(col)) or snake(col)


def tname(stem):
    return ''.join(w.capitalize() for w in snake(stem).split('_')) or 'Tablo'


# ---------- model ----------
def load_model(args):
    folder = Path(args.input)
    if not folder.is_dir():
        raise ValueError('--input tabloların bulunduğu klasör olmalı (her tablo ayrı CSV/XLSX dosyası, ör. satis.csv, musteri.csv).')
    files = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in ('.csv', '.txt', '.xlsx', '.xlsm'))
    if len(files) < 2:
        raise ValueError(f'{folder} klasöründe en az iki tablo dosyası olmalı (bir olgu ve en az bir boyut).')
    tables = {}
    for f in files:
        columns, rows = tabular.read(str(f))
        if not columns or not rows:
            raise ValueError(f'{f.name}: başlık veya veri satırı yok.')
        name = tname(f.stem)
        if name in tables:
            raise ValueError(f'{f.name} ve {tables[name].file.name} aynı tablo adına çıkıyor; birini yeniden adlandırın.')
        vals = {c: [r[c].strip() for r in rows] for c in columns}
        t = SimpleNamespace(name=name, file=f, stem=snake(f.stem), columns=columns, rows=rows, vals=vals,
                            kinds={c: infer_column(vals[c])[0] for c in columns}, distinct={c: len({v for v in vals[c] if v}) for c in columns})
        unique = [c for c in columns if all(vals[c]) and t.distinct[c] == len(rows)]
        own = [c for c in unique if snake(c) in (f'{t.stem}_id', f'{t.stem}_no', f'{t.stem}_kodu', f'{t.stem}_kod', 'id')]
        t.pk = (own or [c for c in unique if IDLIKE.search(c) and not PII.search(c)] or [None])[0]
        tables[name] = t
    by_stem = {t.stem: t for t in tables.values()}
    warnings, rels = [], []

    def relation(child, fk, parent, pk, source):
        keys = set(parent.vals[pk])
        filled = [v for v in child.vals[fk] if v]
        orphans = [v for v in filled if v not in keys]
        return SimpleNamespace(child=child.name, fk=fk, parent=parent.name, pk=pk, source=source, orphans=len(orphans),
                               orphan_values=sorted(set(orphans))[:5], empty=len(child.rows) - len(filled), active=False,
                               coverage=(len(filled) - len(orphans)) / len(filled) if filled else 0)

    for spec in [s.strip() for s in args.relations.split(';') if s.strip()]:
        m = re.fullmatch(r'([\w.-]+)\.([^.=]+)=([\w.-]+)\.([^.=]+)', spec)
        if not m:
            raise ValueError(f'--relations biçimi “alt.sutun=ust.sutun” olmalı: {spec}')
        (ct, cc), (pt, pc) = (m.group(1), m.group(2)), (m.group(3), m.group(4))
        child, parent = by_stem.get(snake(ct)), by_stem.get(snake(pt))
        if not child or not parent or cc not in child.columns or pc not in parent.columns:
            raise ValueError(f'--relations: {spec} içindeki tablo veya sütun bulunamadı. Tablolar: {", ".join(sorted(by_stem))}')
        if parent.distinct[pc] != len(parent.rows) or not all(parent.vals[pc]):
            raise ValueError(f'--relations: {parent.file.name} içinde {pc} tekil ve dolu değil; ilişkinin “bir” tarafı olamaz.')
        parent.pk = parent.pk or pc
        rels.append(relation(child, cc, parent, pc, 'elle verildi'))
    manual = {(r.child, r.fk) for r in rels}
    for child in tables.values():
        for c in child.columns:
            if c == child.pk or (child.name, c) in manual:
                continue
            for parent in tables.values():
                if parent is not child and parent.pk and snake(parent.pk) == snake(c):
                    r = relation(child, c, parent, parent.pk, 'ad eşleşmesi')
                    if r.coverage < 0.5:
                        warnings.append(f'{child.file.name}.{c} ile {parent.file.name}.{parent.pk} adları aynı ama değerlerin yalnızca %{100 * r.coverage:.0f}’i eşleşiyor; ilişki kurulmadı.')
                    else:
                        rels.append(r)
    if not rels:
        raise ValueError('Tablolar arasında ilişki bulunamadı: alt tablodaki sütun adı üst tablonun anahtarıyla aynı olmalı (ör. satis.musteri_id → musteri.musteri_id) '
                         'veya --relations ile verin.')
    if args.fact:
        fact = by_stem.get(snake(Path(args.fact).stem))
        if not fact:
            raise ValueError(f'--fact: {args.fact} tablosu yok. Tablolar: {", ".join(sorted(by_stem))}')
    else:
        parents = {r.parent for r in rels}
        out = Counter(r.child for r in rels)
        cands = [t for t in tables.values() if out[t.name] and t.name not in parents] or [tables[n] for n in out]
        fact = max(cands, key=lambda t: (out[t.name], len(t.rows)))
    order, seen, queue, up = [fact.name], {fact.name}, deque([fact.name]), {}
    while queue:
        cur = queue.popleft()
        for r in rels:
            if r.child == cur and r.parent not in seen:
                r.active = True
                seen.add(r.parent)
                order.append(r.parent)
                up[r.parent] = r
                queue.append(r.parent)
    for r in rels:
        if not r.active and r.child in seen:
            warnings.append(f'{r.child}.{r.fk} → {r.parent} ilişkisi {r.parent} tablosuna ikinci bir yol açıyor; döngü/belirsizlik olmaması için pasif bırakıldı '
                            f'(Qlik’te alan adı ayrıldı, Power BI’da isActive: false, Looker’da join yazılmadı).')
        if r.active and r.orphans:
            warnings.append(f'{tables[r.child].file.name}: {r.orphans} satırın {r.fk} değeri {tables[r.parent].file.name} içinde yok ({", ".join(r.orphan_values)}); '
                            f'bu satırlar toplamlarda var, {r.parent} kırılımlarında görünmez.')
    loose = [t for t in tables if t not in seen]
    if loose:
        warnings.append('Olgu tablosuna bağlanmayan tablolar modele ayrı tablo olarak eklendi: ' + ', '.join(loose) + '.')
    path = {fact.name: []}
    for t in order[1:]:
        path[t] = path[up[t].child] + [up[t]]

    # Field labels: related keys share the parent key's label; any other clash is qualified with the table name.
    lab = {}
    for r in rels:
        if r.active:
            lab[(r.parent, r.pk)] = lab[(r.child, r.fk)] = label(r.pk)
    key_labels = set(lab.values())
    raw = Counter(label(c) for t in tables.values() for c in t.columns if (t.name, c) not in lab)
    for t in tables.values():
        for c in t.columns:
            if (t.name, c) not in lab:
                base = label(c)
                lab[(t.name, c)] = f'{t.name} {base}' if raw[base] > 1 or base in key_labels or base in CAL else base

    # Measures, count, attributes, date
    fks = {(r.child, r.fk) for r in rels}
    numeric = [c for c in fact.columns if fact.kinds[c] in ('int', 'bigint', 'decimal') and c != fact.pk and (fact.name, c) not in fks and not IDLIKE.search(c)]
    if args.measures:
        mcols = [c.strip() for c in args.measures.split(',') if c.strip()]
        bad = [c for c in mcols if c not in numeric]
        if bad:
            raise ValueError(f'--measures: {fact.file.name} içinde sayısal ölçü olmayan sütun: {", ".join(bad)}. Seçenekler: {", ".join(numeric)}')
    else:
        mcols = numeric[:4]
    if not mcols:
        raise ValueError(f'{fact.file.name} içinde ölçü olarak kullanılacak sayısal sütun yok; --measures ile verin.')
    measures = [dict(col=c, agg='avg' if AVG.search(c) else 'sum', dec=2 if fact.kinds[c] == 'decimal' or AVG.search(c) else 0) for c in mcols]
    for m in measures:
        m['name'] = f'{"Ortalama" if m["agg"] == "avg" else "Toplam"} {label(m["col"])}'
    if not args.measures:
        measures.sort(key=lambda m: (m['agg'] != 'sum', not PRIMARY.search(m['col'])))
    if fact.pk:
        count = dict(col=fact.pk, agg='countd', dec=0, name=re.sub(r'\s+(No|Id|Kod|Kodu|Numara|Numarasi|Key)$', '', label(fact.pk)) + ' Sayısı')
    else:
        count = dict(col=fact.columns[0], agg='count', dec=0, name='Kayıt Sayısı')
    attrs = []
    for t in order + loose:
        tb = tables[t]
        for c in tb.columns:
            if (tb.kinds[c] in ('text', 'bool') and c != tb.pk and (t, c) not in fks and not PII.search(c) and not IDLIKE.search(c)
                    and 2 <= tb.distinct[c] <= 50 and t in path):
                attrs.append(dict(table=t, col=c, label=lab[(t, c)], distinct=tb.distinct[c]))
    if not attrs:
        raise ValueError('Kırılım için 2–50 farklı değerli metin sütunu bulunamadı.')
    charts = []
    for t in order:
        own = [a for a in attrs if a['table'] == t]
        if own:
            charts.append(min(own, key=lambda a: a['distinct']))
    charts = charts[:4]
    date_col = next((c for c in fact.columns if fact.kinds[c] == 'date'), None)

    title = args.title or f'{label(folder.name)} Panosu'
    m = SimpleNamespace(folder=folder, tables=tables, rels=rels, fact=fact, order=order, loose=loose, path=path, lab=lab, measures=measures, count=count,
                        attrs=attrs, charts=charts, date=date_col, warnings=warnings, title=title)
    return compute(m)


def compute(m):
    """KPIs, breakdowns and the monthly trend straight from the files (orphan rows count in totals, not in breakdowns).
    Called again by the pano-kurucu agent after it changes measures, breakdowns or the date."""
    fact = m.fact
    index = {t: {m.tables[t].vals[m.tables[t].pk][i]: m.tables[t].rows[i] for i in range(len(m.tables[t].rows))} for t in m.order[1:]}
    fact_vals = {x['col']: numbers(fact.rows, x['col']) for x in m.measures if x['agg'] in ('sum', 'avg')}

    def resolve(i, t, c):
        row = fact.rows[i]
        for r in m.path[t]:
            row = index[r.parent].get(row[r.fk].strip())
            if row is None:
                return None
        return row[c].strip()

    def agg(x, idx):
        if x['agg'] == 'countd':
            return len({fact.vals[x['col']][i] for i in idx if fact.vals[x['col']][i]})
        if x['agg'] == 'count':
            return len(idx)
        xs = [fact_vals[x['col']][i] for i in idx if fact_vals[x['col']][i] is not None]
        return (sum(xs) / len(xs) if xs else None) if x['agg'] == 'avg' else sum(xs)

    every = range(len(fact.rows))
    m.primary = m.measures[0]
    m.kpis = [(x['name'], agg(x, every), x['dec']) for x in m.measures + [m.count]]
    m.breakdown = {}
    for a in m.attrs:
        groups = defaultdict(list)
        for i in every:
            v = resolve(i, a['table'], a['col'])
            if v is not None:
                groups[v or '(boş)'].append(i)
        m.breakdown[a['label']] = sorted(((k, agg(m.primary, ix)) for k, ix in groups.items()), key=lambda kv: (-(kv[1] or 0), kv[0]))
    m.trend, m.years = [], []
    if m.date:
        months = defaultdict(list)
        for i in every:
            p = tabular.parse_date(fact.vals[m.date][i])
            if p:
                months[p[0].strftime('%Y-%m')].append(i)
        m.trend = [(k, agg(m.primary, ix)) for k, ix in sorted(months.items())]
        m.years = sorted({int(k[:4]) for k in months})
    m.resolve = resolve
    return m


def preview(m, product):
    ds = SimpleNamespace(title=m.title, source=f'{m.folder.name}/ ({len(m.tables)} tablo)', rows=len(m.fact.rows), kpis=m.kpis,
                         dims=[a['label'] for a in m.charts], breakdown=m.breakdown, primary=m.primary, trend=m.trend)
    return preview_html(ds, product)


def model_tables(m):
    role = {t: 'olgu' if t == m.fact.name else 'boyut' if t in m.path else 'bağlantısız' for t in m.tables}
    return [('Tablolar', ['Tablo', 'Dosya', 'Satır', 'Anahtar', 'Rol'],
             [[t.name, t.file.name, len(t.rows), t.pk or '—', role[t.name]] for t in m.tables.values()]),
            ('İlişkiler', ['Alt tablo', 'Sütun', 'Üst tablo', 'Anahtar', 'Eşleşmeyen', 'Kaynak', 'Durum'],
             [[r.child, r.fk, r.parent, r.pk, r.orphans, r.source, 'aktif' if r.active else 'pasif'] for r in m.rels])]


def plan(m):
    out = [{'oge': 'KPI', 'ad': n, 'tanim': '', 'deger': round(v, 4) if v is not None else ''} for n, v, _ in m.kpis]
    for x in m.measures + [m.count]:
        out[[o['ad'] for o in out].index(x['name'])]['tanim'] = {'sum': 'toplam', 'avg': 'ortalama', 'countd': 'farklı sayısı', 'count': 'satır sayısı'}[x['agg']] + f' ({m.fact.name}.{x["col"]})'
    for a in m.charts:
        top = m.breakdown[a['label']][0]
        out.append({'oge': 'Kırılım', 'ad': f'{a["label"]} bazında {m.primary["name"]}', 'tanim': f'{a["table"]}.{a["col"]}; {len(m.breakdown[a["label"]])} değer, en yüksek {top[0]}',
                    'deger': round(top[1] or 0, 4)})
    if m.trend:
        out.append({'oge': 'Trend', 'ad': f'Aylık {m.primary["name"]}', 'tanim': f'{m.trend[0][0]} – {m.trend[-1][0]}', 'deger': len(m.trend)})
    return out


def model_summary(m, **extra):
    return {'tablo': len(m.tables), 'iliski': sum(r.active for r in m.rels), 'pasif_iliski': sum(not r.active for r in m.rels), 'olcu': len(m.measures),
            'boyut_alani': len(m.attrs), 'eslesmeyen_satir': sum(r.orphans for r in m.rels if r.active),
            'ana_olcu_toplam': round(m.kpis[0][1], 2) if m.kpis[0][1] is not None else None, **extra}


def run_model(args, product, build):
    m = load_model(args)
    files, extra, notes = build(m, args)
    files['pano-onizleme.html'] = preview(m, product)
    return Result(f'{m.title}: {len(m.tables)} tablo, {sum(r.active for r in m.rels)} ilişki, ' + ', '.join(f'{v} {k}' for k, v in extra.items() if isinstance(v, int)),
                  ['oge', 'ad', 'tanim', 'deger'], plan(m), model_summary(m, **extra), tables=model_tables(m), files=files, notes=notes + m.warnings)


# ---------- Qlik Sense ----------
def association_check(fields):
    """fields: {table: set(field)} -> (synthetic key count, loop count) the way Qlik would associate them."""
    names = list(fields)
    synthetic, edges = 0, []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = fields[a] & fields[b]
            if len(shared) > 1:
                synthetic += 1
            if shared:
                edges.append((a, b))
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    loops = 0
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra == rb:
            loops += 1
        else:
            parent[ra] = rb
    return synthetic, loops


def qlik_build(m, args):
    lib = args.lib.rstrip('/')
    script = ['///$tab Ayarlar\n' + '\n'.join(QLIK_HEADER) + '\n']
    fields = {}
    for t in m.order + m.loose:
        tb = m.tables[t]
        lines = [f'    {bigen.qlik_expr(c, tb.rows)} as [{m.lab[(t, c)]}]' for c in tb.columns]
        if tb.file.suffix.lower() in ('.xlsx', '.xlsm'):
            with zipfile.ZipFile(tb.file) as z:
                sheet = re.search(r'<sheet [^>]*name="([^"]+)"', z.read('xl/workbook.xml').decode('utf-8')).group(1)
            clause = f'(ooxml, embedded labels, table is [{sheet}])'
        else:
            raw = tb.file.read_text(encoding='utf-8-sig', errors='replace')[:20000]
            sep = max([';', ',', '\t', '|'], key=raw.count)
            clause = f"(txt, utf8, embedded labels, delimiter is '{sep}', msq)"
        role = 'olgu' if t == m.fact.name else 'boyut'
        script.append(f'///$tab {t}\n// {tb.file.name} ({role}, {len(tb.rows)} satır)\n[{t}]:\nLOAD\n' + ',\n'.join(lines) + f'\nFROM [{lib}/{tb.file.name}]\n{clause};\n')
        fields[t] = {m.lab[(t, c)] for c in tb.columns}
    dims = [dict(id=f'boyut-{snake(a["label"])}', field=a['label']) for a in m.attrs]
    if m.date:
        f = f'[{m.lab[(m.fact.name, m.date)]}]'
        script.append(f'///$tab Takvim\n[Takvim]:\nLOAD\n    {f},\n    Year({f}) as [Yıl],\n    \'Ç\' & Ceil(Month({f}) / 3) as [Çeyrek],\n'
                      f'    Month({f}) as [Ay],\n    Date(MonthStart({f}), \'YYYY-MM\') as [Yıl Ay];\n'
                      f'LOAD Date([MinTarih] + IterNo() - 1) as {f}\nWHILE [MinTarih] + IterNo() - 1 <= [MaxTarih];\n'
                      f'LOAD Floor(Min({f})) as [MinTarih], Floor(Max({f})) as [MaxTarih]\nRESIDENT [{m.fact.name}];\n')
        fields['Takvim'] = {f[1:-1], *CAL}
        dims += [dict(id='boyut-yil', field='Yıl'), dict(id='boyut-yil-ay', field='Yıl Ay')]
    synthetic, loops = association_check(fields)
    if synthetic or loops:
        raise ValueError(f'İç hata: üretilen modelde {synthetic} sentetik anahtar ve {loops} döngü var.')
    q = lambda t, c: f'[{m.lab[(t, c)]}]'
    expr = {'sum': 'Sum({})', 'avg': 'Avg({})', 'countd': 'Count(DISTINCT {})', 'count': 'Count({})'}
    meas = [dict(id=f'olcu-{snake(x["name"])}', name=x['name'], expr=expr[x['agg']].format(q(m.fact.name, x['col'])), dec=x['dec']) for x in m.measures + [m.count]]
    master_dims = [{'qInfo': {'qId': d['id'], 'qType': 'dimension'}, 'qDim': {'qGrouping': 'N', 'qFieldDefs': [d['field']], 'qFieldLabels': [d['field']], 'title': d['field']},
                    'qMetaDef': {'title': d['field'], 'description': 'Kurulum bloğu', 'tags': ['kurulum']}} for d in dims]
    master_meas = [{'qInfo': {'qId': x['id'], 'qType': 'measure'},
                    'qMeasure': {'qLabel': x['name'], 'qDef': x['expr'], 'qGrouping': 'N', 'qExpressions': [], 'qActiveExpression': 0,
                                 'qNumFormat': {'qType': 'F', 'qnDec': x['dec'], 'qUseThou': 1, 'qFmt': '#.##0' + (',' + '0' * x['dec'] if x['dec'] else ''), 'qDec': ',', 'qThou': '.'},
                                 'isCustomFormatted': True},
                    'qMetaDef': {'title': x['name'], 'description': x['expr'], 'tags': ['kurulum']}} for x in meas]
    dim_id = {d['field']: d['id'] for d in dims}
    fetch = lambda w, h: [{'qLeft': 0, 'qTop': 0, 'qWidth': w, 'qHeight': h}]

    def chart(qid, viz, title, dim_ids, meas_ids, **extra):
        n = len(dim_ids) + len(meas_ids)
        cube = {'qDimensions': [{'qLibraryId': i, 'qDef': {'autoSort': True}, 'qNullSuppression': True} for i in dim_ids],
                'qMeasures': [{'qLibraryId': i, 'qDef': {'autoSort': True}, 'qSortBy': {'qSortByNumeric': -1}} for i in meas_ids],
                'qInterColumnSortOrder': list(range(n)), 'qMode': 'S', 'qSuppressZero': False, 'qSuppressMissing': True, 'qInitialDataFetch': fetch(n, max(1, 10000 // max(1, n)))}
        return {'qProperty': {'qInfo': {'qId': qid, 'qType': viz}, 'visualization': viz, 'showTitles': bool(title), 'title': title, 'subtitle': '', 'footnote': '',
                              'qHyperCubeDef': cube, **extra}, 'qChildren': [], 'qEmbeddedSnapshotRef': None}

    def filterpane(qid, ds):
        boxes = [{'qProperty': {'qInfo': {'qId': f'{qid}-{snake(d["field"])}', 'qType': 'listbox'}, 'visualization': 'listbox', 'title': d['field'], 'showTitles': True,
                                'qListObjectDef': {'qLibraryId': d['id'], 'qDef': {'autoSort': True, 'qSortCriterias': [{'qSortByState': 1, 'qSortByNumeric': 1, 'qSortByAscii': 1}]},
                                                   'qInitialDataFetch': fetch(1, 1000)}}, 'qChildren': [], 'qEmbeddedSnapshotRef': None} for d in ds]
        return {'qProperty': {'qInfo': {'qId': qid, 'qType': 'filterpane'}, 'visualization': 'filterpane', 'showTitles': False, 'title': '',
                              'qChildListDef': {'qData': {'info': '/qInfo'}}}, 'qChildren': boxes, 'qEmbeddedSnapshotRef': None}

    def sheet(sid, title, placed):
        cells = [{'name': o['qProperty']['qInfo']['qId'], 'type': o['qProperty']['visualization'], 'col': c, 'row': r, 'colspan': cs, 'rowspan': rs,
                  'bounds': {'y': round(100 * r / 12, 4), 'x': round(100 * c / 24, 4), 'width': round(100 * cs / 24, 4), 'height': round(100 * rs / 12, 4)}}
                 for o, c, r, cs, rs in placed]
        return {'qProperty': {'qInfo': {'qId': sid, 'qType': 'sheet'}, 'qMetaDef': {'title': title, 'description': 'Kurulum bloğunun ürettiği sayfa'}, 'rank': 0,
                              'thumbnail': {'qStaticContentUrlDef': {'qUrl': ''}}, 'columns': 24, 'rows': 12, 'cells': cells, 'qChildListDef': {'qData': {'title': '/title'}}},
                'qChildren': [o for o, *_ in placed], 'qEmbeddedSnapshotRef': None}

    chart_dims = [d for d in dims if d['field'] in {a['label'] for a in m.charts}] + [d for d in dims if d['id'] == 'boyut-yil']
    placed = [(filterpane('filtreler', chart_dims), 0, 0, 4, 12)]
    w = 20 // len(meas)
    for i, x in enumerate(meas):
        placed.append((chart(f'kpi-{i + 1}', 'kpi', '', [], [x['id']], showMeasureTitle=True, textAlign='center', fontSize='M'), 4 + i * w, 0, w if i < len(meas) - 1 else 20 - i * w, 2))
    main = meas[0]['id']
    slots = [(4, 2), (14, 2), (4, 7), (14, 7)]
    bars = [(f'cubuk-{i + 1}', a) for i, a in enumerate(m.charts)]
    items = []
    if m.date:
        items.append(chart('cizgi-trend', 'linechart', f'Aylık {m.primary["name"]}', ['boyut-yil-ay'], [main]))
    for qid, a in bars:
        items.append(chart(qid, 'barchart', f'{a["label"]} bazında {m.primary["name"]}', [dim_id[a['label']]], [main], orientation='horizontal', dataPoint={'showLabels': True}))
    for obj, (c, r) in zip(items, slots):
        placed.append((obj, c, r, 10, 5))
    detail_dims = [dim_id[a['label']] for a in m.attrs[:6]] + (['boyut-yil-ay'] if m.date else [])
    detail = [(filterpane('detay-filtreler', [d for d in dims if d['id'] in detail_dims]), 0, 0, 4, 12),
              (chart('detay-tablo', 'table', 'Detay', detail_dims, [x['id'] for x in meas], totals={'show': True, 'position': 'top'}), 4, 0, 20, 12)]
    sheets = {'genel-bakis': sheet('genel-bakis', m.title, placed), 'detay': sheet('detay', 'Detay', detail)}
    root = 'qlik-uygulama/'
    uploads = '\n'.join(f'qlik data-file create --file "%KLASOR%\\{m.tables[t].file.name}" --name "{m.tables[t].file.name}"' for t in m.order + m.loose)
    build_cmd = ('@echo off\nrem Qlik Cloud: qlik-cli ile tablo dosyalarını yükler, uygulamayı oluşturur, script, ana öğeler ve sayfaları kurar.\n'
                 f'rem Kullanım: build.cmd C:\\Veri\\{m.folder.name}   (qlik context ile kiracıya bağlı olmalısınız)\n'
                 f'if "%~1"=="" (echo Tablo klasörünü verin: build.cmd C:\\Veri\\{m.folder.name} & exit /b 1)\nset KLASOR=%~f1\ncd /d "%~dp0"\n{uploads}\n'
                 f'for /f %%i in (\'qlik app create --attributes-name "{m.title}" -q\') do set APP=%%i\n'
                 'qlik app build --app %APP% --script script.qvs --dimensions dimensions.json --measures measures.json --variables variables.json '
                 '--objects objects/genel-bakis.json --objects objects/detay.json --app-properties app-properties.json\nqlik app reload --app %APP%\necho Uygulama kimliği: %APP%\npause\n')
    corectl = ('# Qlik Sense Enterprise (client-managed) veya Qlik Associative Engine: corectl build\nengine: localhost:9076\n'
               f'app: {m.title.replace(":", "")}.qvf\nscript: ./script.qvs\napp-properties: ./app-properties.json\n'
               'dimensions:\n  - ./dimensions.json\nmeasures:\n  - ./measures.json\nvariables:\n  - ./variables.json\nobjects:\n  - ./objects/*.json\n')
    files = {root + 'script.qvs': '\n'.join(script), root + 'dimensions.json': json.dumps(master_dims, ensure_ascii=False, indent=2),
             root + 'measures.json': json.dumps(master_meas, ensure_ascii=False, indent=2),
             root + 'variables.json': json.dumps([{'qInfo': {'qId': 'degisken-baslik', 'qType': 'variable'}, 'qName': 'vBaslik', 'qDefinition': m.title}], ensure_ascii=False, indent=2),
             root + 'app-properties.json': json.dumps({'qTitle': m.title, 'description': f'{m.folder.name} klasöründeki {len(m.tables)} tablodan kuruldu'}, ensure_ascii=False, indent=2),
             root + 'build.cmd': build_cmd, root + 'corectl.yml': corectl}
    files.update({f'{root}objects/{k}.json': json.dumps(v, ensure_ascii=False, indent=2) for k, v in sheets.items()})
    n_obj = sum(len(s['qProperty']['cells']) for s in sheets.values())
    return files, {'sentetik_anahtar': synthetic, 'dongu': loops, 'sayfa': len(sheets), 'nesne': n_obj}, [
        f'Qlik Cloud: qlik-uygulama\\build.cmd {m.folder.name} klasörünü verin (qlik-cli gerekir). Enterprise: tablo dosyalarını {args.lib} bağlantısına koyup corectl build. '
        'Elle kurmak için script.qvs’yi veri yükleme düzenleyicisine yapıştırın.',
        'Script her tabloyu ayrı yükler; yalnızca ilişki anahtarları aynı adla bırakıldı, diğer ortak adlar tablo adıyla ayrıldı. '
        'Üretilen alan kümesi Qlik’in ilişkilendirme kuralıyla denetlendi: sentetik anahtar ve döngü yok. Uygulama canlı bir Qlik kiracısında açılarak test edilmedi.']


@op('qlik-cok-tablolu-uygulama', title='Tablo klasöründen sıfırdan Qlik Sense uygulaması: ilişkili veri modeli, ana öğeler ve sayfalar', domain=D,
    product='Qlik Sense Enterprise', tag='Qlik kurulum', kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--lib', default='lib://DataFiles', help='Dosyaların Qlik’teki klasör bağlantısı')],
    description='Olgu ve boyut dosyalarından (satis, musteri, urun, magaza…) eksiksiz bir Qlik Sense uygulaması kurar: her tablo için tipli LOAD, anahtarları veriden bulunmuş '
                'ilişkiler (yalnızca anahtar alanlar ortak adla; diğer ortak adlar ayrılır, böylece sentetik anahtar ve döngü oluşmaz), ana takvim, boyut tablolarından ana '
                'boyutlar, olgu ölçüleri, “Genel bakış” (filtre, KPI, trend, kırılım çubukları) ve “Detay” sayfaları. qlik-cli veya corectl ile tek komutla yüklenir.',
    input='Klasör: her tablo bir CSV/XLSX (ilişki sütunu üst tablonun anahtarıyla aynı adda)', output='qlik-uygulama/ (script.qvs, ana öğeler, objects/, build.cmd) + pano-onizleme.html',
    command='python main.py --input C:\\Veri\\magaza --title "Mağaza Satış Uygulaması"',
    expect={'tablo': 4, 'iliski': 3, 'olcu': 4, 'eslesmeyen_satir': 2, 'sentetik_anahtar': 0, 'dongu': 0, 'sayfa': 2, 'nesne': 12, 'ana_olcu_toplam': 2687430.0})
def qlik_cok(args):
    return run_model(args, 'Qlik Sense', qlik_build)


# ---------- Power BI (PBIP) ----------
def pbi_build(m, args):
    fact = m.fact.name
    hidden = {(r.child, r.fk) for r in m.rels}
    entity = {t: t for t in m.tables}
    sm = {'definition/database.tmdl': 'database\n\tcompatibilityLevel: 1600\n',
          'definition/expressions.tmdl': f'expression KlasorYolu = "{args.folder.rstrip(chr(92)) + chr(92)}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'}
    model = ['model Model', '\tculture: tr-TR', '\tdefaultPowerBIDataSourceVersion: powerBI_V3', '\tsourceQueryCulture: tr-TR', '']
    props = defaultdict(set)
    agg = {'sum': 'SUM({})', 'avg': 'AVERAGE({})', 'countd': 'DISTINCTCOUNT({})', 'count': 'COUNTROWS({})'}
    fmt = lambda dec: '#,0.00' if dec else '#,0'
    for t in m.order + m.loose:
        tb = m.tables[t]
        styles = {tabular.number_style([v for v in tb.vals[c] if v]) for c in tb.columns if tb.kinds[c] == 'decimal'}
        culture = 'en-US' if 'en' in styles else 'tr-TR'
        q = bigen.power_query(tb.columns, tb.rows, SimpleNamespace(input=str(tb.file), path='@@YOL@@', culture=culture, sheet=None)).files['sorgu.m']
        q = q.replace('File.Contents("@@YOL@@")', f'File.Contents(KlasorYolu & "{tb.file.name}")')
        lines = [f'table {tmdl_name(t)}', '']
        if t == fact:
            for x in m.measures + [m.count]:
                target = tmdl_name(t) if x['agg'] == 'count' else f'{tmdl_name(t)}[{m.lab[(t, x["col"])]}]'
                lines += [f'\tmeasure {tmdl_name(x["name"])} = {agg[x["agg"]].format(target)}', f'\t\tformatString: {fmt(x["dec"])}', '']
                props[t].add(x['name'])
            if m.date:
                p = m.primary['name']
                for n, e, f in [(f'{p} GY', f'CALCULATE([{p}], SAMEPERIODLASTYEAR(Takvim[Date]))', fmt(m.primary['dec'])),
                                (f'{p} YoY %', f'DIVIDE([{p}] - [{p} GY], [{p} GY])', '0.0%;-0.0%;0.0%')]:
                    lines += [f'\tmeasure {tmdl_name(n)} = {e}', f'\t\tformatString: {f}', '']
                    props[t].add(n)
        for c in tb.columns:
            k, name = tb.kinds[c], m.lab[(t, c)]
            lines += [f'\tcolumn {tmdl_name(name)}', f'\t\tdataType: {TMDL_TYPE[k]}']
            if k in ('date', 'datetime'):
                lines.append('\t\tformatString: dd.mm.yyyy' if k == 'date' else '\t\tformatString: dd.mm.yyyy hh:nn')
            elif k == 'decimal':
                lines.append('\t\tformatString: #,0.00')
            elif k in ('int', 'bigint'):
                lines.append('\t\tformatString: 0')
            if (t, c) in hidden:
                lines.append('\t\tisHidden')
            lines += ['\t\tsummarizeBy: none', f'\t\tsourceColumn: {c}', '']
            props[t].add(name)
        lines += [f'\tpartition {tmdl_name(t)} = m', '\t\tmode: import', '\t\tsource ='] + ['\t\t\t\t' + ln for ln in q.split('\n')]
        sm[f'definition/tables/{t}.tmdl'] = '\n'.join(lines) + '\n'
        model.append(f'ref table {tmdl_name(t)}')
    rel_lines = []
    for r in m.rels:
        rel_lines += [f'relationship {snake(r.child)}_{snake(r.fk)}_{snake(r.parent)}', f'\tfromColumn: {tmdl_name(r.child)}.{tmdl_name(m.lab[(r.child, r.fk)])}',
                      f'\ttoColumn: {tmdl_name(r.parent)}.{tmdl_name(m.lab[(r.parent, r.pk)])}'] + ([] if r.active else ['\tisActive: false']) + ['']
    if m.date:
        y0, y1 = m.years[0], m.years[-1]
        cal = [('Date', 'dateTime', 'dd.mm.yyyy'), ('Yıl', 'int64', '0'), ('Ay No', 'int64', '0'), ('Ay', 'string', None), ('Yıl Ay', 'string', None), ('Çeyrek', 'string', None)]
        tl = ['table Takvim', '\tdataCategory: Time', '']
        for name, typ, f in cal:
            tl += [f'\tcolumn {tmdl_name(name)}', f'\t\tdataType: {typ}'] + (['\t\tisKey'] if name == 'Date' else []) + ([f'\t\tformatString: {f}'] if f else [])
            tl += ['\t\tsummarizeBy: none', '\t\tisNameInferred', f'\t\tsourceColumn: [{name}]'] + (["\t\tsortByColumn: 'Ay No'"] if name == 'Ay' else []) + ['']
            props['Takvim'].add(name)
        months = ', '.join(f'{i + 1}, "{a}"' for i, a in enumerate(AY))
        tl += ['\tpartition Takvim = calculated', '\t\tmode: import', '\t\tsource =', f'\t\t\t\tADDCOLUMNS(CALENDAR(DATE({y0}, 1, 1), DATE({y1}, 12, 31)),',
               '\t\t\t\t    "Yıl", YEAR([Date]), "Ay No", MONTH([Date]),', f'\t\t\t\t    "Ay", SWITCH(MONTH([Date]), {months}),',
               '\t\t\t\t    "Yıl Ay", FORMAT([Date], "YYYY-MM"), "Çeyrek", "Ç" & QUARTER([Date]))']
        sm['definition/tables/Takvim.tmdl'] = '\n'.join(tl) + '\n'
        model.append('ref table Takvim')
        rel_lines += [f'relationship {snake(fact)}_takvim', f'\tfromColumn: {tmdl_name(fact)}.{tmdl_name(m.lab[(fact, m.date)])}', '\ttoColumn: Takvim.Date', '']
        entity['Takvim'] = 'Takvim'
    sm['definition/relationships.tmdl'] = '\n'.join(rel_lines)
    sm['definition/model.tmdl'] = '\n'.join(model) + '\n'

    # Report (PBIR): two pages
    used = []

    def field(ent, prop, measure=False):
        used.append((ent, prop))
        return {('Measure' if measure else 'Column'): {'Expression': {'SourceRef': {'Entity': ent}}, 'Property': prop}}

    def proj(ent, prop, measure=False):
        return {'field': field(ent, prop, measure), 'queryRef': f'{ent}.{prop}', 'nativeQueryRef': prop}

    pages = {}

    def visual(page, name, vtype, x, y, w, h, state, sort=None, objects=None, title=None):
        vs = pages.setdefault(page, {})
        v = {'visualType': vtype, 'query': {'queryState': state}, 'drillFilterOtherVisuals': True}
        if sort:
            v['query']['sortDefinition'] = {'sort': [{'field': sort[0], 'direction': sort[1]}], 'isDefaultSort': True}
        if objects:
            v['objects'] = objects
        if title:
            v['visualContainerObjects'] = {'title': [{'properties': {'show': {'expr': {'Literal': {'Value': 'true'}}},
                                                                      'text': {'expr': {'Literal': {'Value': "'" + title.replace("'", "''") + "'"}}}}}]}
        vs[name] = {'$schema': SCHEMA + 'item/report/definition/visualContainer/2.1.0/schema.json', 'name': name,
                    'position': {'x': x, 'y': y, 'z': len(vs) * 1000, 'height': h, 'width': w, 'tabOrder': len(vs) * 1000}, 'visual': v}

    dropdown = {'data': [{'properties': {'mode': {'expr': {'Literal': {'Value': "'Dropdown'"}}}}}]}
    slicers = [(a['table'], a['label']) for a in m.charts] + ([('Takvim', 'Yıl')] if m.date else [])
    sh = 680 / len(slicers)
    for i, (ent, col) in enumerate(slicers):
        visual('genel', f'filtre{i + 1}', 'slicer', 10, 20 + i * sh, 200, sh - 10, {'Values': {'projections': [proj(ent, col)]}}, objects=dropdown)
    allm = m.measures + [m.count]
    cw = 1040 / len(allm)
    for i, x in enumerate(allm):
        visual('genel', f'kpi{i + 1}', 'card', 225 + i * cw, 20, cw - 15, 110, {'Values': {'projections': [proj(fact, x['name'], True)]}})
    p = m.primary['name']
    slots = [(225, 145), (750, 145), (225, 440), (750, 440)]
    blocks = []
    if m.date:
        blocks.append(('trend', 'lineChart', {'Category': {'projections': [proj('Takvim', 'Yıl Ay')]}, 'Y': {'projections': [proj(fact, p, True)]}},
                       (field('Takvim', 'Yıl Ay'), 'Ascending'), f'Aylık {p}'))
    for i, a in enumerate(m.charts):
        blocks.append((f'cubuk{i + 1}', 'clusteredBarChart', {'Category': {'projections': [proj(a['table'], a['label'])]}, 'Y': {'projections': [proj(fact, p, True)]}},
                       (field(fact, p, True), 'Descending'), f'{a["label"]} bazında {p}'))
    for (name, vt, state, sort, title), (x, y) in zip(blocks, slots):
        visual('genel', name, vt, x, y, 515, 280, state, sort=sort, title=title)
    detail_attrs = m.attrs[:6]
    visual('detay', 'detay', 'tableEx', 225, 20, 1040, 690, {'Values': {'projections': [proj(a['table'], a['label']) for a in detail_attrs] + [proj(fact, x['name'], True) for x in allm]}},
           sort=(field(fact, p, True), 'Descending'), title='Detay')
    for i, a in enumerate(detail_attrs[:4]):
        visual('detay', f'dfiltre{i + 1}', 'slicer', 10, 20 + i * 170, 200, 160, {'Values': {'projections': [proj(a['table'], a['label'])]}}, objects=dropdown)
    missing = sorted({f'{e}.{pp}' for e, pp in used if pp not in props.get(e, set())})
    if missing:
        raise ValueError('İç hata: rapor modelde olmayan alana bakıyor: ' + ', '.join(missing))
    name = re.sub(r'[^\w\- ]', '', m.title).strip() or 'Pano'
    rep = {'definition.pbir': {'$schema': SCHEMA + 'item/report/definitionProperties/2.0.0/schema.json', 'version': '4.0', 'datasetReference': {'byPath': {'path': f'../{name}.SemanticModel'}}},
           'definition/version.json': {'$schema': SCHEMA + 'item/report/definition/versionMetadata/1.0.0/schema.json', 'version': '2.0.0'},
           'definition/report.json': {'$schema': SCHEMA + 'item/report/definition/report/3.0.0/schema.json', 'themeCollection': {}},
           'definition/pages/pages.json': {'$schema': SCHEMA + 'item/report/definition/pagesMetadata/1.0.0/schema.json', 'pageOrder': ['genel', 'detay'], 'activePageName': 'genel'}}
    for pid, disp in [('genel', m.title[:100]), ('detay', 'Detay')]:
        rep[f'definition/pages/{pid}/page.json'] = {'$schema': SCHEMA + 'item/report/definition/page/2.0.0/schema.json', 'name': pid, 'displayName': disp,
                                                    'displayOption': 'FitToPage', 'height': 720, 'width': 1280}
        for vn, v in pages[pid].items():
            rep[f'definition/pages/{pid}/visuals/{vn}/visual.json'] = v
    files = {f'{name}.pbip': json.dumps({'$schema': SCHEMA + 'pbip/pbipProperties/1.0.0/schema.json', 'version': '1.0', 'artifacts': [{'report': {'path': f'{name}.Report'}}],
                                         'settings': {'enableAutoRecovery': True}}, ensure_ascii=False, indent=2),
             f'{name}.SemanticModel/definition.pbism': json.dumps({'$schema': SCHEMA + 'item/semanticModel/definitionProperties/1.0.0/schema.json', 'version': '4.2', 'settings': {}}, indent=2),
             '.gitignore': '**/.pbi/localSettings.json\n**/.pbi/cache.abf\n'}
    files.update({f'{name}.SemanticModel/{k}': v for k, v in sm.items()})
    files.update({f'{name}.Report/{k}': json.dumps(v, ensure_ascii=False, indent=2) for k, v in rep.items()})
    n_vis = sum(len(v) for v in pages.values())
    return {'pbip/' + k: v for k, v in files.items()}, {'sayfa': len(pages), 'gorsel': n_vis}, [
        f'Power BI Desktop’ta pbip/{name}.pbip dosyasını açın (Önizleme özellikleri’nde PBIP ve PBIR açık olmalı); Parametreler’den KlasorYolu’nu tablo klasörünüze çevirip Yenile’ye basın.',
        'İlişkiler çoktan bire ve tek yönlüdür; olgu tablosundaki yabancı anahtarlar gizlidir. Her görselin baktığı alanın modelde olduğu kodla denetlendi; '
        'proje Power BI Desktop’ta açılarak test edilmedi.']


@op('pbi-yildiz-model-projesi', title='Tablo klasöründen sıfırdan Power BI projesi (PBIP): yıldız model, ilişkiler, ölçüler ve iki sayfalık rapor', domain=D,
    product='Power BI Desktop', tag='PBIP kurulum', kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--folder', default='C:\\Veri\\magaza', help='Power BI’ın dosyaları okuyacağı klasör (KlasorYolu parametresi)')],
    description='Olgu ve boyut dosyalarından Power BI Desktop’ın proje biçiminde yıldız şema kurar: her tablo için Power Query (dosyanın kendi ondalık biçimine göre kültür), veriden '
                'doğrulanmış çoktan bire ilişkiler (ikinci yol açan ilişki pasif), gizli yabancı anahtarlar, takvim tablosu, DAX ölçüleri (toplam, ortalama, farklı sayı, geçen yıl, '
                'yıllık değişim) ve PBIR rapor: dilimleyiciler, kartlar, trend ve boyut tablolarından kırılım grafikleriyle “Genel bakış”, sıralı tabloyla “Detay” sayfası.',
    input='Klasör: her tablo bir CSV/XLSX', output='pbip/<Ad>.pbip + <Ad>.SemanticModel/ (TMDL) + <Ad>.Report/ (PBIR) + pano-onizleme.html',
    command='python main.py --input C:\\Veri\\magaza --folder "\\\\sunucu\\veri\\magaza" --title "Mağaza Satış Raporu"',
    expect={'tablo': 4, 'iliski': 3, 'pasif_iliski': 0, 'olcu': 4, 'sayfa': 2, 'gorsel': 18})
def pbi_cok(args):
    return run_model(args, 'Power BI', pbi_build)


# ---------- Looker (LookML) ----------
def lookml_build(m, args):
    lk = {'int': 'number', 'bigint': 'number', 'decimal': 'number', 'bool': 'yesno', 'text': 'string'}
    views, fields = {}, defaultdict(set)
    hidden = {(r.child, r.fk) for r in m.rels}
    look = {'sum': 'sum', 'avg': 'average', 'countd': 'count_distinct', 'count': 'count'}
    fname = lambda t, c: snake(m.lab[(t, c)])
    for t in m.order + m.loose:
        tb, v = m.tables[t], snake(t)
        schema = f'{args.schema}.' if args.schema else ''
        lines = [f'# {tb.file.name} dosyasından üretildi.', f'view: {v} {{', f'  sql_table_name: {schema}{tb.stem} ;;', '']
        for c in tb.columns:
            f = fname(t, c)
            if tb.kinds[c] in ('date', 'datetime'):
                lines += [f'  dimension_group: {f} {{', '    type: time', '    timeframes: [raw, date, week, month, quarter, year]', '    convert_tz: no',
                          f'    datatype: {"date" if tb.kinds[c] == "date" else "datetime"}', f'    label: "{m.lab[(t, c)]}"', f'    sql: ${{TABLE}}.{c} ;;', '  }', '']
                fields[v] |= {f'{f}_{tf}' for tf in ('raw', 'date', 'week', 'month', 'quarter', 'year')}
                continue
            lines += [f'  dimension: {f} {{'] + (['    primary_key: yes'] if c == tb.pk else []) + [f'    type: {lk[tb.kinds[c]]}', f'    label: "{m.lab[(t, c)]}"']
            lines += (['    hidden: yes'] if (t, c) in hidden or (t == m.fact.name and c in {x['col'] for x in m.measures}) else []) + [f'    sql: ${{TABLE}}.{c} ;;', '  }', '']
            fields[v].add(f)
        if t == m.fact.name:
            for x in m.measures + [m.count]:
                lines += [f'  measure: {snake(x["name"])} {{', f'    type: {look[x["agg"]]}', f'    label: "{x["name"]}"']
                lines += ([f'    sql: ${{{fname(t, x["col"])}}} ;;'] if x['agg'] != 'count' else []) + [f'    value_format_name: {"decimal_2" if x["dec"] else "decimal_0"}', '  }', '']
                fields[v].add(snake(x['name']))
        lines.append('}')
        views[v] = '\n'.join(lines) + '\n'
    fv = snake(m.fact.name)
    joins, refs = [], []
    for r in m.rels:
        if not r.active:
            continue
        a, b = f'${{{snake(r.child)}.{fname(r.child, r.fk)}}}', f'${{{snake(r.parent)}.{fname(r.parent, r.pk)}}}'
        refs += [(snake(r.child), fname(r.child, r.fk)), (snake(r.parent), fname(r.parent, r.pk))]
        joins.append(f'  join: {snake(r.parent)} {{\n    type: left_outer\n    relationship: many_to_one\n    sql_on: {a} = {b} ;;\n  }}')
    model_text = (f'connection: "{args.connection}"\n\ninclude: "/views/*.view.lkml"\ninclude: "/dashboards/*.dashboard.lookml"\n\n'
                  f'explore: {fv} {{\n  label: "{m.title}"\n' + '\n'.join(joins) + '\n}\n')
    fq = lambda v, f: (refs.append((v, f)), f'{v}.{f}')[1]
    pm = fq(fv, snake(m.primary['name']))
    listen = {a['label']: fq(snake(a['table']), snake(a['label'])) for a in m.charts}
    base = {'model': args.model, 'explore': fv, 'listen': listen}
    els, allm = [], m.measures + [m.count]
    w = 24 // len(allm)
    for i, x in enumerate(allm):
        els.append({'name': f'kpi_{i + 1}', 'title': x['name'], 'type': 'single_value', 'fields': [fq(fv, snake(x['name']))], **base,
                    'row': 0, 'col': i * w, 'width': w if i < len(allm) - 1 else 24 - i * w, 'height': 3})
    slots = [(3, 0), (3, 12), (11, 0), (11, 12)]
    blocks = []
    if m.date:
        tm = fq(fv, fname(m.fact.name, m.date) + '_month')
        blocks.append({'name': 'trend', 'title': f'Aylık {m.primary["name"]}', 'type': 'looker_line', 'fields': [tm, pm], 'fill_fields': [tm], 'sorts': [tm], 'limit': 500})
    for i, a in enumerate(m.charts):
        blocks.append({'name': f'kirilim_{i + 1}', 'title': f'{a["label"]} bazında {m.primary["name"]}', 'type': 'looker_bar',
                       'fields': [fq(snake(a['table']), snake(a['label'])), pm], 'sorts': [f'{pm} desc'], 'limit': 50})
    for b, (row, col) in zip(blocks, slots):
        els.append({**b, **base, 'row': row, 'col': col, 'width': 12, 'height': 8})
    els.append({'name': 'detay', 'title': 'Detay', 'type': 'looker_grid', 'fields': [fq(snake(a['table']), snake(a['label'])) for a in m.attrs[:6]] + [fq(fv, snake(x['name'])) for x in allm],
                'sorts': [f'{pm} desc'], 'limit': 500, **base, 'row': 19, 'col': 0, 'width': 24, 'height': 8})
    filters = [{'name': a['label'], 'title': a['label'], 'type': 'field_filter', 'default_value': '', 'allow_multiple_values': True, 'required': False,
                'ui_config': {'type': 'tag_list', 'display': 'popover'}, 'model': args.model, 'explore': fv, 'field': listen[a['label']]} for a in m.charts]
    bad = sorted({f'{v}.{f}' for v, f in refs if f not in fields[v]})
    if bad:
        raise ValueError('İç hata: LookML’de tanımsız alan: ' + ', '.join(bad))
    dash = [{'dashboard': snake(m.title), 'title': m.title, 'layout': 'newspaper', 'preferred_viewer': 'dashboards-next', 'elements': els, 'filters': filters}]
    files = {f'lookml/views/{v}.view.lkml': text for v, text in views.items()}
    files[f'lookml/{args.model}.model.lkml'] = model_text
    files[f'lookml/dashboards/{snake(m.title)}.dashboard.lookml'] = '---\n' + yaml_dump(dash)
    return files, {'view': len(views), 'join': len(joins), 'eleman': len(els)}, [
        'Dosyaları Looker projesine (Geliştirme modu) kopyalayın; bağlantı adını ve sql_table_name şemasını kontrol edip “Validate LookML” çalıştırın.',
        'Join’ler ağaç biçimindedir (olgu → boyut, boyut → alt boyut), hepsi many_to_one; her panodaki alanın bir view’da tanımlı olduğu kodla denetlendi. Looker’da açılarak test edilmedi.']


@op('lookml-cok-tablolu-proje', title='Tablo klasöründen sıfırdan Looker projesi: view’lar, join’li explore ve pano', domain=D, product='Looker', tag='LookML kurulum',
    kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--connection', default='veri_ambari', help='Looker bağlantı adı'), arg('--model', default='magaza', help='Model dosyası adı'),
                       arg('--schema', default='analytics', help='Tabloların ambar şeması')],
    description='Her tablo için tipli LookML view’u (birincil anahtar, gizli yabancı anahtarlar, tarih için zaman grubu), olgu tablosundan boyutlara many_to_one join’lerle '
                'explore ve KPI, aylık trend, boyut kırılımları ve detay tablodan oluşan, filtreleri bağlı LookML panosu üretir.',
    input='Klasör: her tablo bir CSV/XLSX (ambarda aynı adla tablo olduğu varsayılır)', output='lookml/ (views/, <model>.model.lkml, dashboards/) + pano-onizleme.html',
    command='python main.py --input C:\\Veri\\magaza --connection snowflake_prod --schema ANALYTICS',
    expect={'tablo': 4, 'iliski': 3, 'view': 4, 'join': 3, 'eleman': 10})
def lookml_cok(args):
    return run_model(args, 'Looker', lookml_build)


# ---------- Tableau (.twb) ----------
def tableau_build(m, args):
    if any(t.file.suffix.lower() in ('.xlsx', '.xlsm') for t in m.tables.values()):
        raise ValueError('Tableau şablonu CSV metin bağlantısı kurar; Excel dosyalarını önce CSV olarak kaydedin.')
    a = quoteattr
    ds_name, conn = 'federated.kurulum', 'textscan.kurulum'
    folder = args.tableau_folder.replace('\\', '/')
    tw = lambda t: t.file.name.replace('.', '#')
    ordered = [t for t in m.order]

    def rel_xml(tb, pad):
        raw = tb.file.read_text(encoding='utf-8-sig', errors='replace')[:20000]
        sep = max([';', ',', '\t', '|'], key=raw.count)
        cols = ''.join(f'\n{pad}    <column datatype={a(TW_TYPE[tb.kinds[c]])} name={a(c)} ordinal={a(str(i))} />' for i, c in enumerate(tb.columns))
        return (f'\n{pad}<relation connection={a(conn)} name={a(tb.file.name)} table={a("[" + tw(tb) + "]")} type="table">\n{pad}  <columns character-set="UTF-8" header="yes" '
                f'locale="tr_TR" separator={a("t" if sep == chr(9) else sep)}>{cols}\n{pad}  </columns>\n{pad}</relation>')

    # Left-join tree: ((fact ⟕ d1) ⟕ d2) ...
    tree = rel_xml(m.tables[ordered[0]], '          ')
    for t in ordered[1:]:
        r = m.path[t][-1]
        child, parent = m.tables[r.child], m.tables[r.parent]
        clause = (f'\n          <clause type="join">\n            <expression op="=">\n              <expression op={a(f"[{child.file.name}].[{r.fk}]")} />\n'
                  f'              <expression op={a(f"[{parent.file.name}].[{r.pk}]")} />\n            </expression>\n          </clause>')
        tree = f'\n        <relation join="left" type="join">{clause}{tree}{rel_xml(parent, "          ")}\n        </relation>'
    # Column names Tableau gives after the join: the first table keeps the plain name, later duplicates get "name (file)".
    seen, cols_map, local = set(), [], {}
    for t in ordered:
        tb = m.tables[t]
        for c in tb.columns:
            nm = c if c not in seen else f'{c} ({tb.file.name})'
            seen.add(c)
            local[(t, c)] = nm
            cols_map.append(f'\n        <map key={a(f"[{nm}]")} value={a(f"[{tb.file.name}].[{c}]")} />')
    measure_cols = {(m.fact.name, x['col']) for x in m.measures}
    meta = ''
    for (t, c), nm in local.items():
        tb = m.tables[t]
        role = 'measure' if (t, c) in measure_cols else 'dimension'
        typ = 'quantitative' if role == 'measure' else 'ordinal' if tb.kinds[c] in ('date', 'datetime') else 'nominal'
        hid = ' hidden="true"' if any((r.child, r.fk) == (t, c) or (r.parent, r.pk) == (t, c) and t != m.fact.name for r in m.rels if r.active) else ''
        meta += f'\n      <column caption={a(m.lab[(t, c)])} datatype={a(TW_TYPE[tb.kinds[c]])} name={a(f"[{nm}]")} role={a(role)} type={a(typ)}{hid} />'
    deriv = {'sum': ('Sum', 'sum'), 'avg': ('Avg', 'avg'), 'countd': ('CountD', 'ctd'), 'count': ('Count', 'cnt')}

    def inst(nm, how='none'):
        if how == 'none':
            return f'[none:{nm}:nk]', 'None', 'nominal'
        if how == 'month':
            return f'[tmn:{nm}:qk]', 'Month-Trunc', 'quantitative'
        d, p = deriv[how]
        return f'[{p}:{nm}:qk]', d, 'quantitative'

    def deps(pairs):
        cols, insts = {}, {}
        for (t, c), how in pairs:
            nm, tb = local[(t, c)], m.tables[t]
            role = 'measure' if (t, c) in measure_cols else 'dimension'
            cols[nm] = (f'\n            <column caption={a(m.lab[(t, c)])} datatype={a(TW_TYPE[tb.kinds[c]])} name={a(f"[{nm}]")} role={a(role)} '
                        f'type={a("quantitative" if role == "measure" else "ordinal" if tb.kinds[c] in ("date", "datetime") else "nominal")} />')
            n, d, typ = inst(nm, how)
            insts[n] = f'\n            <column-instance column={a(f"[{nm}]")} derivation={a(d)} name={a(n)} pivot="key" type={a(typ)} />'
        return ''.join(cols.values()) + ''.join(insts.values())

    def uid(name):
        return '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'kurulum/{m.folder.name}/{name}')).upper() + '}'

    ref = lambda key, how='none': f'[{ds_name}].{inst(local[key], how)[0]}'

    def sheet(name, pairs, mark, rows_=None, cols_=None, text=None):
        enc = f'\n              <encodings>\n                <text column={a(text)} />\n              </encodings>' if text else ''
        return (f'\n    <worksheet name={a(name)}>\n      <table>\n        <view>\n          <datasources>\n            <datasource caption={a(m.title)} name={a(ds_name)} />\n'
                f'          </datasources>\n          <datasource-dependencies datasource={a(ds_name)}>{deps(pairs)}\n          </datasource-dependencies>\n'
                f'          <aggregation value="true" />\n        </view>\n        <style />\n        <panes>\n          <pane selection-relaxation-option="selection-relaxation-allow">\n'
                f'            <view>\n              <breakdown value="auto" />\n            </view>\n            <mark class={a(mark)} />{enc}\n          </pane>\n        </panes>\n'
                f'        <rows>{escape(rows_ or "")}</rows>\n        <cols>{escape(cols_ or "")}</cols>\n      </table>\n      <simple-id uuid={a(uid(name))} />\n    </worksheet>')

    f = m.fact.name
    pk = (f, m.primary['col'])
    sheets, names = [], []
    for x in m.measures + [m.count]:
        nm = f'KPI {x["name"]}'
        names.append(nm)
        sheets.append(sheet(nm, [((f, x['col']), x['agg'])], 'Text', text=ref((f, x['col']), x['agg'])))
    k = len(names)
    if m.date:
        names.append('Aylık trend')
        sheets.append(sheet('Aylık trend', [((f, m.date), 'month'), (pk, m.primary['agg'])], 'Line', rows_=ref(pk, m.primary['agg']), cols_=ref((f, m.date), 'month')))
    for at in m.charts:
        key = (at['table'], at['col'])
        nm = f'{at["label"]} bazında'
        names.append(nm)
        sheets.append(sheet(nm, [(key, 'none'), (pk, m.primary['agg'])], 'Bar', rows_=ref(key), cols_=ref(pk, m.primary['agg'])))
    detail = [(at['table'], at['col']) for at in m.attrs[:4]]
    names.append('Detay')
    sheets.append(sheet('Detay', [(d, 'none') for d in detail] + [(pk, m.primary['agg'])], 'Text', rows_=' / '.join(ref(d) for d in detail), text=ref(pk, m.primary['agg'])))
    zones, zid = '', 3
    body = names[k:]
    rows_n = (len(body) + 1) // 2
    for i in range(k):
        zones += f'\n          <zone h="14000" id="{zid}" name={a(names[i])} w="{100000 // k}" x="{i * 100000 // k}" y="0" />'
        zid += 1
    for j, n in enumerate(body):
        zones += f'\n          <zone h="{86000 // rows_n}" id="{zid}" name={a(n)} w="50000" x="{(j % 2) * 50000}" y="{14000 + (j // 2) * (86000 // rows_n)}" />'
        zid += 1
    cards = ('<cards><edge name="left"><strip size="160"><card type="pages" /><card type="filters" /><card type="marks" /></strip></edge>'
             '<edge name="top"><strip size="2147483647"><card type="columns" /></strip><strip size="2147483647"><card type="rows" /></strip>'
             '<strip size="31"><card type="title" /></strip></edge></cards>')
    windows = ''.join(f'\n    <window class="worksheet" hidden="true" name={a(n)}>{cards}<simple-id uuid={a(uid("w:" + n))} /></window>' for n in names)
    first = m.tables[ordered[0]]
    text = f'''<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='26.1' source-build='0.0.0 (0000.0.0.0)' source-platform='win' version='26.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
    <ManifestByVersion />
  </document-format-change-manifest>
  <datasources>
    <datasource caption={a(m.title)} inline="true" name={a(ds_name)} version="18.1">
      <connection class="federated">
        <named-connections>
          <named-connection caption={a(m.folder.name)} name={a(conn)}>
            <connection class="textscan" directory={a(folder)} filename={a(first.file.name)} password="" server="" />
          </named-connection>
        </named-connections>{tree}
        <cols>{''.join(cols_map)}
        </cols>
      </connection>
      <aliases enabled="yes" />{meta}
    </datasource>
  </datasources>
  <worksheets>{''.join(sheets)}
  </worksheets>
  <dashboards>
    <dashboard name={a(m.title)}>
      <style />
      <size maxheight="900" maxwidth="1400" minheight="900" minwidth="1400" />
      <zones>
        <zone h="100000" id="2" type-v2="layout-basic" w="100000" x="0" y="0">{zones}
        </zone>
      </zones>
      <simple-id uuid={a(uid("pano"))} />
    </dashboard>
  </dashboards>
  <windows>{windows}
    <window class="dashboard" maximized="true" name={a(m.title)}><viewpoints>{''.join(f'<viewpoint name={a(n)} />' for n in names)}</viewpoints><active id="-1" /><simple-id uuid={a(uid("w:pano"))} /></window>
  </windows>
</workbook>
'''
    import xml.dom.minidom
    xml.dom.minidom.parseString(text.encode('utf-8'))  # well-formed or raise
    return {'pano.twb': text}, {'sayfa': len(names), 'join': len(ordered) - 1}, [
        f'CSV dosyalarını {args.tableau_folder} klasörüne koyun ve pano.twb’yi Tableau Desktop 2026.1 veya üzeriyle açın.',
        'Tablolar olgu tablosundan sol birleştirmeyle (left join) tek mantıksal tabloya bağlandı; ortak sütunlar Tableau’nun adlandırmasıyla “ad (dosya)” olur ve gizlenir. '
        'Dosya XML olarak doğrulandı; Tableau’da açılarak test edilmedi.']


@op('tableau-cok-tablolu-calisma-kitabi', title='Tablo klasöründen sıfırdan Tableau çalışma kitabı: birleştirilmiş veri kaynağı, sayfalar ve pano', domain=D,
    product='Tableau Desktop', tag='Tableau kurulum', kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--tableau-folder', default='C:/Veri/magaza', help='CSV’lerin Tableau’nun açacağı bilgisayardaki klasörü')],
    description='Olgu ve boyut CSV’lerini tek veri kaynağında, veriden bulunan anahtarlarla sol birleştirmeye (left join) bağlayan bir .twb yazar: her ölçü için KPI sayfası, '
                'aylık trend, her boyut tablosundan kırılım çubuğu, detay tablo ve bunları yerleştiren pano. Alan başlıkları ve rolleri (boyut/ölçü) ayarlanır, anahtarlar gizlenir.',
    input='Klasör: her tablo bir CSV', output='pano.twb + pano-onizleme.html', command='python main.py --input C:\\Veri\\magaza --tableau-folder "D:/Paylasim/magaza"',
    expect={'tablo': 4, 'iliski': 3, 'sayfa': 10, 'join': 3})
def tableau_cok(args):
    return run_model(args, 'Tableau', tableau_build)


# ---------- SQL panolar (Superset, Metabase) ----------
SQL_DIALECTS = ('postgres', 'mysql', 'sqlserver', 'snowflake', 'bigquery')


def month_sql(col, dialect):
    return {'postgres': f"CAST(date_trunc('month', {col}) AS DATE)", 'mysql': f"DATE_FORMAT({col}, '%Y-%m-01')", 'sqlserver': f'DATEFROMPARTS(YEAR({col}), MONTH({col}), 1)',
            'snowflake': f"DATE_TRUNC('MONTH', {col})", 'bigquery': f'DATE_TRUNC({col}, MONTH)', 'sqlite': f"date({col}, 'start of month')"}[dialect]


def star_sql(m, dialect, schema):
    """SELECT that joins the active tree with LEFT JOINs; every output column gets the snake-case field label."""
    qd = 'tsql' if dialect == 'sqlserver' else 'postgres' if dialect == 'sqlite' else dialect
    alias = {t: 'f' if t == m.fact.name else f't{i}' for i, t in enumerate(m.order)}
    tbl = lambda t: (f'{schema}.' if schema and dialect != 'sqlite' else '') + m.tables[t].stem
    col = lambda t, c: f'{alias[t]}.{sql_quote(c, qd)}'
    out = []
    for c in m.fact.columns:
        if c == m.fact.pk or c == m.date or c in {x['col'] for x in m.measures}:
            out.append(f'    {col(m.fact.name, c)} AS {snake(m.lab[(m.fact.name, c)])}')
    out += [f'    {col(a["table"], a["col"])} AS {snake(a["label"])}' for a in m.attrs]
    joins = [f'LEFT JOIN {tbl(t)} {alias[t]} ON {col(t, m.path[t][-1].pk)} = {col(m.path[t][-1].child, m.path[t][-1].fk)}' for t in m.order[1:]]
    return 'SELECT\n' + ',\n'.join(out) + f'\nFROM {tbl(m.fact.name)} f\n' + '\n'.join(joins)


def sqlite_db(m):
    con = sqlite3.connect(':memory:')
    typ = {'int': 'INTEGER', 'bigint': 'INTEGER', 'decimal': 'REAL', 'date': 'TEXT', 'datetime': 'TEXT', 'bool': 'TEXT', 'text': 'TEXT'}
    for t in m.tables.values():
        con.execute(f'CREATE TABLE {t.stem} (' + ', '.join(f'"{c}" {typ[t.kinds[c]]}' for c in t.columns) + ')')
        conv = {}
        for c in t.columns:
            k = t.kinds[c]
            if k in ('int', 'bigint', 'decimal'):
                conv[c] = numbers(t.rows, c)
            elif k in ('date', 'datetime'):
                conv[c] = [(tabular.parse_date(v)[0].strftime('%Y-%m-%d' if k == 'date' else '%Y-%m-%d %H:%M:%S') if v and tabular.parse_date(v) else None) for v in t.vals[c]]
            else:
                conv[c] = [v or None for v in t.vals[c]]
        con.executemany(f'INSERT INTO {t.stem} VALUES (' + ', '.join('?' * len(t.columns)) + ')', list(zip(*[conv[c] for c in t.columns])))
    return con


def metric_sql(m, x):
    col = snake(m.lab[(m.fact.name, x['col'])])
    return {'sum': f'SUM({col})', 'avg': f'AVG({col})', 'countd': f'COUNT(DISTINCT {col})', 'count': 'COUNT(*)'}[x['agg']]


def verify_sql(m):
    """Run the star query and every card aggregate in SQLite and compare with the Python figures."""
    con = sqlite_db(m)
    base = star_sql(m, 'sqlite', '')
    n = con.execute(f'SELECT COUNT(*) FROM ({base}) s').fetchone()[0]
    if n != len(m.fact.rows):
        raise ValueError(f'İç hata: birleştirme satır sayısını değiştirdi ({len(m.fact.rows)} → {n}); bir boyut anahtarı tekil değil.')
    checks = 0
    for (name, value, _), x in zip(m.kpis, m.measures + [m.count]):
        got = con.execute(f'SELECT {metric_sql(m, x)} FROM ({base}) s').fetchone()[0]
        if value is not None and abs((got or 0) - value) > 0.01:
            raise ValueError(f'İç hata: {name} SQL’de {got}, Python’da {value}.')
        checks += 1
    for a in m.charts:
        got = dict(con.execute(f'SELECT {snake(a["label"])}, {metric_sql(m, m.primary)} FROM ({base}) s WHERE {snake(a["label"])} IS NOT NULL GROUP BY 1').fetchall())
        want = dict(m.breakdown[a['label']])
        if set(got) != set(want) or any(abs((got[k] or 0) - (want[k] or 0)) > 0.01 for k in want):
            raise ValueError(f'İç hata: {a["label"]} kırılımı SQL ile Python arasında farklı.')
        checks += 1
    con.close()
    return checks


def superset_build(m, args):
    checks = verify_sql(m)
    u = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, f'kurulum/{m.folder.name}/{name}'))
    db_name = args.database
    root = f'{snake(m.title)}_export'
    db = {'database_name': db_name, 'sqlalchemy_uri': args.sqlalchemy_uri, 'cache_timeout': None, 'expose_in_sqllab': True, 'allow_run_async': False,
          'allow_ctas': False, 'allow_cvas': False, 'allow_dml': False, 'allow_file_upload': False, 'extra': {'allows_virtual_table_explore': True}, 'uuid': u('db'), 'version': '1.0.0'}
    ds_name = f'{snake(m.fact.name)}_yildiz'
    cols = []
    for c in m.fact.columns:
        if c == m.fact.pk or c == m.date or c in {x['col'] for x in m.measures}:
            k = m.fact.kinds[c]
            cols.append({'column_name': snake(m.lab[(m.fact.name, c)]), 'verbose_name': m.lab[(m.fact.name, c)], 'is_dttm': c == m.date, 'is_active': True,
                         'type': {'date': 'DATE', 'decimal': 'NUMERIC', 'int': 'INTEGER', 'bigint': 'BIGINT'}.get(k, 'VARCHAR'), 'groupby': c == m.date,
                         'filterable': True, 'expression': None, 'description': None, 'python_date_format': None, 'extra': {}})
    cols += [{'column_name': snake(a['label']), 'verbose_name': a['label'], 'is_dttm': False, 'is_active': True, 'type': 'VARCHAR', 'groupby': True, 'filterable': True,
              'expression': None, 'description': f'{a["table"]}.{a["col"]}', 'python_date_format': None, 'extra': {}} for a in m.attrs]
    metrics = [{'metric_name': snake(x['name']), 'verbose_name': x['name'], 'metric_type': x['agg'], 'expression': metric_sql(m, x), 'description': None,
                'd3format': ',.2f' if x['dec'] else ',d', 'extra': {}, 'warning_text': None} for x in m.measures + [m.count]]
    dataset = {'table_name': ds_name, 'main_dttm_col': snake(m.lab[(m.fact.name, m.date)]) if m.date else None, 'description': f'{m.title}: olgu + boyutlar (left join)',
               'default_endpoint': None, 'offset': 0, 'cache_timeout': None, 'schema': args.schema or None, 'sql': star_sql(m, args.dialect, args.schema), 'params': None,
               'template_params': None, 'filter_select_enabled': True, 'fetch_values_predicate': None, 'extra': None, 'normalize_columns': False, 'always_filter_main_dttm': False,
               'uuid': u('dataset'), 'metrics': metrics, 'columns': cols, 'version': '1.0.0', 'database_uuid': u('db')}
    charts, positions = [], {}
    pm = snake(m.primary['name'])

    def add(key, name, viz, params, width, row):
        cid = len(charts) + 1
        charts.append((f'charts/{key}_{cid}.yaml', {'slice_name': name, 'description': None, 'certified_by': None, 'certification_details': None, 'viz_type': viz,
                                                    'params': {'datasource': f'{cid}__table', 'viz_type': viz, **params}, 'query_context': None, 'cache_timeout': None,
                                                    'uuid': u(f'chart/{key}'), 'version': '1.0.0', 'dataset_uuid': u('dataset')}))
        positions.setdefault(row, []).append((f'CHART-{key}', cid, u(f'chart/{key}'), name, width))

    allm = m.measures + [m.count]
    for i, x in enumerate(allm):
        add(f'kpi_{i + 1}', x['name'], 'big_number_total', {'metric': snake(x['name']), 'y_axis_format': 'SMART_NUMBER', 'header_font_size': 0.4}, max(2, 12 // len(allm)), 1)
    if m.date:
        add('trend', f'Aylık {m.primary["name"]}', 'echarts_timeseries_line', {'x_axis': snake(m.lab[(m.fact.name, m.date)]), 'time_grain_sqla': 'P1M', 'metrics': [pm],
                                                                             'groupby': [], 'row_limit': 10000, 'adhoc_filters': []}, 6, 2)
    for i, a in enumerate(m.charts):
        add(f'kirilim_{i + 1}', f'{a["label"]} bazında {m.primary["name"]}', 'echarts_timeseries_bar',
            {'x_axis': snake(a['label']), 'metrics': [pm], 'groupby': [], 'orientation': 'horizontal', 'x_axis_sort': pm, 'x_axis_sort_asc': False, 'row_limit': 50,
             'adhoc_filters': []}, 6, 2 + (i + (1 if m.date else 0)) // 2)
    add('detay', 'Detay', 'table', {'query_mode': 'aggregate', 'groupby': [snake(a['label']) for a in m.attrs[:6]], 'metrics': [snake(x['name']) for x in allm],
                                   'row_limit': 1000, 'order_desc': True, 'adhoc_filters': []}, 12, 99)
    layout = {'DASHBOARD_VERSION_KEY': 'v2', 'ROOT_ID': {'type': 'ROOT', 'id': 'ROOT_ID', 'children': ['GRID_ID']},
              'HEADER_ID': {'id': 'HEADER_ID', 'type': 'HEADER', 'meta': {'text': m.title}}, 'GRID_ID': {'type': 'GRID', 'id': 'GRID_ID', 'children': [], 'parents': ['ROOT_ID']}}
    for n, row in enumerate(sorted(positions)):
        rid = f'ROW-{n + 1}'
        layout['GRID_ID']['children'].append(rid)
        layout[rid] = {'type': 'ROW', 'id': rid, 'children': [], 'parents': ['ROOT_ID', 'GRID_ID'], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}}
        for key, cid, cu, name, width in positions[row]:
            layout[rid]['children'].append(key)
            layout[key] = {'type': 'CHART', 'id': key, 'children': [], 'parents': ['ROOT_ID', 'GRID_ID', rid],
                           'meta': {'width': width, 'height': 18 if row == 1 else 50, 'chartId': cid, 'uuid': cu, 'sliceName': name}}
    native = [{'id': f'NATIVE_FILTER-{snake(a["label"])}', 'name': a['label'], 'filterType': 'filter_select', 'type': 'NATIVE_FILTER',
               'targets': [{'datasetUuid': u('dataset'), 'column': {'name': snake(a['label'])}}], 'controlValues': {'multiSelect': True, 'enableEmptyFilter': False},
               'defaultDataMask': {'filterState': {}}, 'cascadeParentIds': [], 'scope': {'rootPath': ['ROOT_ID'], 'excluded': []}} for a in m.charts]
    dash = {'dashboard_title': m.title, 'description': None, 'css': '', 'slug': snake(m.title).replace('_', '-'), 'certified_by': None, 'certification_details': None,
            'published': False, 'uuid': u('dashboard'), 'position': layout, 'metadata': {'native_filter_configuration': native, 'color_scheme': 'supersetColors',
                                                                                           'refresh_frequency': 0, 'expanded_slices': {}, 'label_colors': {}, 'timed_refresh_immune_slices': [],
                                                                                           'cross_filters_enabled': True}, 'version': '1.0.0'}
    parts = {f'{root}/metadata.yaml': yaml_dump({'version': '1.0.0', 'type': 'Dashboard', 'timestamp': '2026-01-01T00:00:00+00:00'}),
             f'{root}/databases/{snake(db_name)}.yaml': yaml_dump(db), f'{root}/datasets/{snake(db_name)}/{ds_name}.yaml': yaml_dump(dataset),
             f'{root}/dashboards/{snake(m.title)}.yaml': yaml_dump(dash)}
    parts.update({f'{root}/{k}': yaml_dump(v) for k, v in charts})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in parts.items():
            z.writestr(zipfile.ZipInfo(k, (2026, 1, 1, 0, 0, 0)), v.encode('utf-8'), zipfile.ZIP_DEFLATED)
    return {'superset-pano.zip': buf.getvalue(), 'yildiz_sorgu.sql': dataset['sql'] + '\n'}, {'grafik': len(charts), 'filtre': len(native), 'sql_kontrol': checks}, [
        'Superset’te Panolar > İçe aktar ile superset-pano.zip’i yükleyin; bağlantı parolası istenir (dosyada XXXXXXXXXX olarak maskelidir).',
        'Veri kümesi, tabloları olgu tablosundan sol birleştirmeyle okuyan sanal (SQL) veri kümesidir. Aynı sorgu SQLite’ta örnek dosyalarla çalıştırıldı: satır sayısı korunuyor, '
        'KPI ve kırılım değerleri Python hesabıyla aynı. Paket Superset’e yüklenerek test edilmedi.']


@op('superset-pano-paketi', title='Tablo klasöründen sıfırdan Apache Superset panosu: veri kümesi, grafikler ve filtreler (içe aktarma paketi)', domain=D,
    product='Superset', tag='Superset kurulum', kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--dialect', choices=SQL_DIALECTS, default='postgres'), arg('--schema', default='analytics', help='Tabloların şeması'),
                       arg('--database', default='veri_ambari', help='Superset’teki veritabanı bağlantısının adı'),
                       arg('--sqlalchemy-uri', default='postgresql://rapor:XXXXXXXXXX@ambar:5432/analytics', help='Bağlantı adresi (parola maskeli kalır)')],
    description='Superset’in “Pano içe aktar” biçiminde ZIP paketi üretir: veritabanı bağlantısı, olgu ve boyut tablolarını birleştiren sanal veri kümesi (sütun ve kayıtlı '
                'ölçülerle), KPI (big number), aylık trend, boyut kırılımı çubukları, detay tablo ve boyut filtreleriyle pano. Birleştirme SQL’i ve rakamlar SQLite’ta doğrulanır.',
    input='Klasör: her tablo bir CSV/XLSX (ambarda aynı adla tablo olduğu varsayılır)', output='superset-pano.zip + yildiz_sorgu.sql + pano-onizleme.html',
    command='python main.py --input C:\\Veri\\magaza --dialect snowflake --schema ANALYTICS --database snowflake_prod',
    expect={'tablo': 4, 'iliski': 3, 'grafik': 10, 'filtre': 3, 'sql_kontrol': 8})
def superset_cok(args):
    return run_model(args, 'Superset', superset_build)


METABASE_SCRIPT = r'''"""Metabase panosunu API ile kurar: koleksiyon, SQL soruları (kartlar) ve pano. Yalnızca standart kütüphane kullanır.
Kullanım:  set MB_URL=https://metabase.sirket.com  &  set MB_API_KEY=mb_...  &  python metabase_kur.py --database-id 2
API anahtarı: Yönetici > Ayarlar > Kimlik doğrulama > API anahtarları. Önce --dry-run ile ne yapılacağını görün."""
import argparse, json, os, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def call(method, path, body=None):
    req = urllib.request.Request(os.environ['MB_URL'].rstrip('/') + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'x-api-key': os.environ['MB_API_KEY']})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--database-id', type=int, required=True, help='Metabase’teki veritabanı kimliği (Yönetici > Veritabanları adresindeki sayı)')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    spec = json.loads((HERE / 'kartlar.json').read_text(encoding='utf-8'))
    if a.dry_run:
        for c in spec['cards']:
            print(f"[kart] {c['name']} ({c['display']})")
        print(f"[pano] {spec['dashboard']}: {len(spec['cards'])} kart")
        return
    if not os.environ.get('MB_URL') or not os.environ.get('MB_API_KEY'):
        sys.exit('MB_URL ve MB_API_KEY ortam değişkenlerini verin.')
    col = call('POST', '/api/collection', {'name': spec['dashboard'], 'color': '#509EE3'})
    dash = call('POST', '/api/dashboard', {'name': spec['dashboard'], 'collection_id': col['id']})
    dashcards = []
    for i, c in enumerate(spec['cards']):
        card = call('POST', '/api/card', {'name': c['name'], 'collection_id': col['id'], 'display': c['display'], 'visualization_settings': c['visualization_settings'],
                                          'dataset_query': {'type': 'native', 'native': {'query': c['sql']}, 'database': a.database_id}})
        dashcards.append({'id': -(i + 1), 'card_id': card['id'], 'row': c['row'], 'col': c['col'], 'size_x': c['size_x'], 'size_y': c['size_y']})
        print(f"[OK] kart {card['id']}: {c['name']}")
    call('PUT', f"/api/dashboard/{dash['id']}", {'dashcards': dashcards})
    print(f"[OK] pano: {os.environ['MB_URL'].rstrip('/')}/dashboard/{dash['id']}")


if __name__ == '__main__':
    main()
'''


def metabase_build(m, args):
    checks = verify_sql(m)
    base_sql = lambda d: star_sql(m, d, args.schema)
    con = sqlite_db(m)
    cards, allm = [], m.measures + [m.count]
    pm = metric_sql(m, m.primary)

    def card(name, display, select, group, order, row, col, sx, sy, settings=None, where=None):
        sql = lambda d: (f'SELECT {select(d)}\nFROM (\n' + '\n'.join('    ' + ln for ln in base_sql(d).split('\n')) + '\n) s' +
                         (f'\nWHERE {where} IS NOT NULL' if where else '') + (f'\nGROUP BY {group}' if group else '') + (f'\nORDER BY {order}' if order else ''))
        result = con.execute(sql('sqlite')).fetchall()
        cards.append({'name': name, 'display': display, 'sql': sql(args.dialect), 'row': row, 'col': col, 'size_x': sx, 'size_y': sy,
                      'visualization_settings': settings or {}, 'satir': len(result), 'ilk_satir': [str(v) for v in result[0]] if result else []})

    w = 24 // len(allm)
    for i, x in enumerate(allm):
        card(x['name'], 'scalar', lambda d, x=x: f'{metric_sql(m, x)} AS deger', None, None, 0, i * w, w if i < len(allm) - 1 else 24 - i * w, 3)
    slots = [(3, 0), (3, 12), (11, 0), (11, 12)]
    items = []
    if m.date:
        dc = snake(m.lab[(m.fact.name, m.date)])
        items.append((f'Aylık {m.primary["name"]}', 'line', lambda d: f'{month_sql(dc, d)} AS ay, {pm} AS deger', '1', '1', {'graph.dimensions': ['ay'], 'graph.metrics': ['deger']}, dc))
    for a in m.charts:
        c = snake(a['label'])
        items.append((f'{a["label"]} bazında {m.primary["name"]}', 'row', lambda d, c=c: f'{c}, {pm} AS deger', '1', '2 DESC', {'graph.dimensions': [c], 'graph.metrics': ['deger']}, c))
    for (name, disp, sel, grp, order, st, wh), (r, c) in zip(items, slots):
        card(name, disp, sel, grp, order, r, c, 12, 8, st, wh)
    dcols = [snake(a['label']) for a in m.attrs[:6]]
    card('Detay', 'table', lambda d: ', '.join(dcols) + ', ' + ', '.join(f'{metric_sql(m, x)} AS {snake(x["name"])}' for x in allm), ', '.join(str(i + 1) for i in range(len(dcols))),
         f'{len(dcols) + 1} DESC', 19, 0, 24, 9)
    con.close()
    spec = {'dashboard': m.title, 'dialect': args.dialect, 'cards': [{k: v for k, v in c.items() if k not in ('satir', 'ilk_satir')} for c in cards]}
    ast.parse(METABASE_SCRIPT)
    files = {'metabase/kartlar.json': json.dumps(spec, ensure_ascii=False, indent=2), 'metabase/metabase_kur.py': METABASE_SCRIPT}
    files.update({f'metabase/sql/{i + 1:02d}_{snake(c["name"])}.sql': c['sql'] + ';\n' for i, c in enumerate(cards)})
    m.extra_table = [('Kartlar (SQLite’ta çalıştırıldı)', ['Kart', 'Görünüm', 'Satır', 'İlk satır'], [[c['name'], c['display'], c['satir'], ' | '.join(c['ilk_satir'])] for c in cards])]
    return files, {'kart': len(cards), 'sql_kontrol': checks}, [
        'metabase klasöründe: MB_URL ve MB_API_KEY verip “python metabase_kur.py --database-id <kimlik>” (önce --dry-run). Betik koleksiyon, kartlar ve panoyu oluşturur.',
        f'Kart SQL’leri {args.dialect} lehçesindedir; aynı sorgular SQLite sürümüyle örnek dosyalarda çalıştırıldı ve KPI/kırılım değerleri Python hesabıyla karşılaştırıldı. '
        'Betik bir Metabase sunucusunda çalıştırılarak test edilmedi.']


@op('metabase-pano-kurulumu', title='Tablo klasöründen sıfırdan Metabase panosu: SQL kartları ve API kurulum betiği', domain=D, product='Metabase', tag='Metabase kurulum',
    kind='file', samples=MAGAZA, sample_input='magaza',
    args=MODEL_ARGS + [arg('--dialect', choices=SQL_DIALECTS, default='postgres'), arg('--schema', default='analytics', help='Tabloların şeması')],
    description='Olgu ve boyut tablolarını birleştiren SQL ile Metabase kartlarını (KPI, aylık trend, boyut kırılımları, detay) yazar ve bunları Metabase API’siyle koleksiyon + pano '
                'olarak kuran bir Python betiği üretir. Her kart SQL’i SQLite’ta örnek verilerle çalıştırılır; rakamlar Python hesabıyla karşılaştırılır.',
    input='Klasör: her tablo bir CSV/XLSX (veritabanında aynı adla tablo olduğu varsayılır)', output='metabase/ (kartlar.json, metabase_kur.py, sql/) + pano-onizleme.html',
    command='python main.py --input C:\\Veri\\magaza --dialect sqlserver --schema dbo',
    expect={'tablo': 4, 'iliski': 3, 'kart': 10, 'sql_kontrol': 8})
def metabase_cok(args):
    m_holder = {}

    def build(m, a):
        out = metabase_build(m, a)
        m_holder['m'] = m
        return out
    res = run_model(args, 'Metabase', build)
    res.tables += getattr(m_holder['m'], 'extra_table', [])
    return res
