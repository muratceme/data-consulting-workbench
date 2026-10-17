"""Hazır rapor şablonları: tek bir CSV/Excel dosyasından açılmaya hazır rapor projeleri üretir. Qlik Sense uygulaması (qlik-cli/corectl
build klasörü), Power BI projesi (PBIP: TMDL anlamsal model + PBIR rapor), Tableau çalışma kitabı (.twb), formüllü ve grafikli Excel raporu
(.xlsx) ve tek dosyalık HTML panosu. Hepsi aynı rapor tasarımını kullanır: KPI kartları, boyut kırılımları, aylık trend, filtreler ve detay
tablo. Ölçü, boyut ve tarih sütunları veriden seçilir (--measures, --dimensions, --date ile değiştirilebilir). Hiçbir platforma bağlanmaz."""
import html, io, json, re, uuid, zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape, quoteattr
import tabular
from sqlgen import infer_column, snake
import bigen
from islem import op, arg, Result, col_list, numbers, tr_lower

REQUIRES = ['sema_farki', 'sqlgen', 'bigen']
D = 'bi'
SATIS = {'satis_detay.csv': 'satis_detay.csv'}
PII = re.compile(r'e_?posta|email|mail|telefon|phone|gsm|tckn|iban|kart_no|adres|ad_soyad|isim', re.I)
IDLIKE = re.compile(r'(^|_)(no|id|kod|kodu|numara|numarasi|key)$', re.I)
AVG = re.compile(r'fiyat|oran|yuzde|puan|skor|ortalama|price|rate|score', re.I)
PRIMARY = re.compile(r'tutar|ciro|gelir|satis|hasilat|amount|revenue|sales', re.I)
AY = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
UZUN_AY = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
DESIGN_ARGS = [arg('--title', default='', help='Rapor başlığı (boşsa dosya adından)'), arg('--table', default='', help='Model/tablo adı (boşsa dosya adından)'),
               arg('--measures', default='', help='Ölçü sütunları (boşsa sayısal sütunlar, en çok 4)'),
               arg('--dimensions', default='', help='Kırılım sütunları (boşsa 2–30 farklı değerli metin sütunları, en çok 3)'),
               arg('--date', default='', help='Tarih sütunu (boşsa ilk tarih sütunu)')]


def label(c):
    """Field label used by every template; identical to the qlik-load-script block's renaming."""
    return ' '.join(w.capitalize() for w in snake(c).split('_'))


def tr_num(v, dec=0):
    if v is None:
        return '–'
    return f'{v:,.{dec}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def design(columns, rows, args):
    """Choose measures, dimensions, date and key columns and compute the figures every template shows."""
    if not rows:
        raise ValueError('Dosyada veri satırı yok.')
    kinds = {c: infer_column([r[c] for r in rows])[0] for c in columns}
    filled = {c: sum(bool(r[c].strip()) for r in rows) for c in columns}
    distinct = {c: len({r[c].strip() for r in rows if r[c].strip()}) for c in columns}
    stem = Path(args.input).stem
    table = args.table or (snake(stem).title().replace('_', '') or 'Veri')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
        raise ValueError('--table yalnızca harf, rakam ve alt çizgi içermeli (örn. Satislar).')
    if args.date:
        dcol = col_list(args.date, columns, '--date')[0]
        if kinds[dcol] != 'date':
            raise ValueError(f'{dcol} sütunu saatsiz tarih olarak okunamadı (YYYY-AA-GG veya GG.AA.YYYY).')
    else:
        dcol = next((c for c in columns if kinds[c] == 'date'), None)
    key = next((c for c in columns if IDLIKE.search(c) and not PII.search(c) and filled[c] == len(rows) and distinct[c] == len(rows)), None)
    if args.measures:
        mcols = col_list(args.measures, columns, '--measures')
        bad = [c for c in mcols if kinds[c] not in ('int', 'bigint', 'decimal')]
        if bad:
            raise ValueError(f'Sayısal olmayan ölçü sütunu: {", ".join(bad)}')
    else:
        mcols = [c for c in columns if kinds[c] in ('int', 'bigint', 'decimal') and not IDLIKE.search(c)][:4]
    if not mcols:
        raise ValueError('Ölçü olarak kullanılabilecek sayısal sütun yok; --measures ile verin.')
    if args.dimensions:
        dims = col_list(args.dimensions, columns, '--dimensions')
    else:
        dims = [c for c in columns if kinds[c] in ('text', 'bool') and c != key and not PII.search(c) and not IDLIKE.search(c) and 2 <= distinct[c] <= 30][:3]
    if not dims:
        raise ValueError('Kırılım için 2–30 farklı değerli metin sütunu yok; --dimensions ile verin.')
    vals = {c: numbers(rows, c) for c in mcols}
    measures = []
    for c in mcols:
        agg = 'avg' if AVG.search(c) else 'sum'
        measures.append(dict(col=c, agg=agg, name=f'{"Ortalama" if agg == "avg" else "Toplam"} {label(c)}', dec=2 if kinds[c] == 'decimal' else 0))
    measures.sort(key=lambda m: (m['agg'] != 'sum', not PRIMARY.search(m['col'])))
    if key:
        base = re.sub(r'\s+(No|Id|Kod|Kodu|Numara|Numarasi|Key)$', '', label(key))
        count = dict(col=key, agg='countd', name=f'{base} Sayısı', dec=0)
    else:
        count = dict(col=columns[0], agg='count', name='Kayıt Sayısı', dec=0)
    primary = measures[0]

    def agg(m, idx):
        if m['agg'] == 'countd':
            return len({rows[i][m['col']].strip() for i in idx if rows[i][m['col']].strip()})
        if m['agg'] == 'count':
            return len(idx)
        xs = [vals[m['col']][i] for i in idx if vals[m['col']][i] is not None]
        if m['agg'] == 'avg':
            return sum(xs) / len(xs) if xs else None
        return sum(xs)

    every = range(len(rows))
    kpis = [(m['name'], agg(m, every), m['dec']) for m in measures + [count]]
    breakdown, warnings = {}, []
    for dcol_ in dims:
        # Excel SUMIFS and Power BI compare text case-insensitively; group the same way and show the most common spelling.
        groups, spell = defaultdict(list), defaultdict(Counter)
        for i, r in enumerate(rows):
            v = r[dcol_].strip()
            groups[tr_lower(v)].append(i)
            spell[tr_lower(v)][v] += 1
        breakdown[dcol_] = sorted(((spell[k].most_common(1)[0][0] or '(boş)', agg(primary, ix)) for k, ix in groups.items()), key=lambda kv: (-(kv[1] or 0), kv[0]))
        mixed = [' / '.join(f'“{s}”' for s in sp) for sp in spell.values() if len(sp) > 1]
        if mixed:
            warnings.append(f'{dcol_}: aynı değer farklı büyük/küçük harfle yazılmış ({"; ".join(mixed)}). Excel ve Power BI bunları tek değer, Qlik ve Tableau ayrı değer '
                            'sayar; rakamların her araçta aynı çıkması için önce metin temizleme bloğuyla düzeltin.')
        if '' in groups:
            warnings.append(f'{dcol_}: {len(groups[""])} satır boş; raporlarda “(boş)” ya da boş etiketle görünür.')
    trend, years = [], []
    if dcol:
        months = defaultdict(list)
        for i, r in enumerate(rows):
            p = tabular.parse_date(r[dcol])
            if p:
                months[p[0].strftime('%Y-%m')].append(i)
        trend = [(k, agg(primary, ix)) for k, ix in sorted(months.items())]
        years = sorted({int(k[:4]) for k in months})
    return SimpleNamespace(table=table, title=args.title or f'{label(stem)} Raporu', kinds=kinds, date=dcol, key=key, measures=measures, count=count,
                           primary=primary, dims=dims, kpis=kpis, breakdown=breakdown, trend=trend, years=years, warnings=warnings, rows=len(rows), source=Path(args.input).name)


def plan_rows(ds):
    out = [{'oge': 'KPI', 'ad': name, 'tanim': '', 'deger': round(v, 4) if v is not None else ''} for name, v, _ in ds.kpis]
    for m in ds.measures + [ds.count]:
        out[[o['ad'] for o in out].index(m['name'])]['tanim'] = {'sum': 'toplam', 'avg': 'ortalama', 'countd': 'farklı sayısı', 'count': 'satır sayısı'}[m['agg']] + f' ({m["col"]})'
    for d in ds.dims:
        top = ds.breakdown[d][0]
        out.append({'oge': 'Kırılım', 'ad': f'{label(d)} bazında {ds.primary["name"]}', 'tanim': f'{len(ds.breakdown[d])} değer; en yüksek {top[0]}', 'deger': round(top[1] or 0, 4)})
    if ds.trend:
        out.append({'oge': 'Trend', 'ad': f'Aylık {ds.primary["name"]}', 'tanim': f'{ds.trend[0][0]} – {ds.trend[-1][0]}, {len(ds.trend)} ay ({ds.date})', 'deger': len(ds.trend)})
    return out


def summary(ds, **extra):
    return {'olcu': len(ds.measures), 'kpi': len(ds.kpis), 'boyut': len(ds.dims), 'ay': len(ds.trend),
            'ana_olcu_toplam': round(ds.kpis[0][1], 2) if ds.kpis[0][1] is not None else None, **extra}


# ---------- HTML preview (shared by every template) ----------
def preview_html(ds, product):
    e = html.escape
    cards = ''.join(f'<div class="card"><span>{e(n)}</span><b>{tr_num(v, d)}</b></div>' for n, v, d in ds.kpis)
    bars = ''
    for d in ds.dims:
        top = max((v or 0) for _, v in ds.breakdown[d]) or 1
        items = ''.join(f'<li><span class="k">{e(k)}</span><span class="bar"><i style="width:{max(0.5, 100 * (v or 0) / top):.1f}%"></i></span>'
                        f'<span class="v">{tr_num(v, ds.primary["dec"])}</span></li>' for k, v in ds.breakdown[d][:12])
        bars += f'<section class="panel"><h2>{e(label(d))} bazında {e(ds.primary["name"])}</h2><ul class="bars">{items}</ul></section>'
    line = ''
    if len(ds.trend) > 1:
        w, h, pad = 640, 220, 34
        ys = [v or 0 for _, v in ds.trend]
        lo, hi = 0, max(ys) or 1
        pts = [(pad + i * (w - 2 * pad) / (len(ys) - 1), h - pad - (y - lo) / (hi - lo) * (h - 2 * pad)) for i, y in enumerate(ys)]
        poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
        dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3"><title>{e(k)}: {tr_num(v, ds.primary["dec"])}</title></circle>' for (x, y), (k, v) in zip(pts, ds.trend))
        ticks = ''.join(f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle">{AY[int(k[5:]) - 1]}{" " + k[2:4] if k[5:] == "01" or i == 0 else ""}</text>'
                        for i, ((x, _), (k, _)) in enumerate(zip(pts, ds.trend)))
        line = (f'<section class="panel wide"><h2>Aylık {e(ds.primary["name"])}</h2><svg viewBox="0 0 {w} {h}" role="img" aria-label="Aylık trend">'
                f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" class="axis"/><polyline points="{poly}"/>{dots}{ticks}</svg></section>')
    return f'''<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(ds.title)}</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--ink:#1d2433;--muted:#5d6779;--accent:#2f6fde;--line:#e3e6ec}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--panel:#1b2029;--ink:#e8ecf3;--muted:#9aa4b5;--accent:#6ea0ff;--line:#2b3240}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 40px}}h1{{font-size:24px;margin:0 0 4px}}p.sub{{color:var(--muted);margin:0 0 20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}.card span{{color:var(--muted);font-size:13px;display:block}}
.card b{{font-size:22px;font-variant-numeric:tabular-nums}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}}
.wide{{grid-column:1/-1}}h2{{font-size:15px;margin:0 0 10px}}ul.bars{{list-style:none;margin:0;padding:0}}
ul.bars li{{display:grid;grid-template-columns:minmax(80px,30%) 1fr auto;gap:8px;align-items:center;margin:5px 0;font-size:13px}}
.k{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar{{background:var(--line);height:10px;border-radius:5px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--accent)}}.v{{font-variant-numeric:tabular-nums}}
svg{{width:100%;height:auto}}polyline{{fill:none;stroke:var(--accent);stroke-width:2.5}}circle{{fill:var(--accent)}}.axis{{stroke:var(--line)}}
svg text{{fill:var(--muted);font-size:11px}}footer{{color:var(--muted);font-size:12px;margin-top:18px}}
</style></head><body><main>
<h1>{e(ds.title)}</h1><p class="sub">{e(ds.source)} · {ds.rows} satır · {e(product)} şablonunun önizlemesi</p>
<div class="cards">{cards}</div><div class="grid">{line}{bars}</div>
<footer>Bu sayfa şablonun hangi rakamları göstereceğini kontrol etmek içindir; değerler aynı dosyadan Python ile hesaplandı.</footer>
</main></body></html>
'''


# ---------- Qlik Sense ----------
def qlik_files(columns, rows, args, ds):
    lib = args.lib or f'lib://DataFiles/{ds.source}'
    load = bigen.qlik(columns, rows, SimpleNamespace(input=args.input, table=ds.table, lib=lib)).files['yukleme.qvs']
    if Path(args.input).suffix.lower() in ('.xlsx', '.xlsm'):
        sheet = getattr(args, 'sheet', None)
        if not sheet:
            with zipfile.ZipFile(args.input) as z:
                sheet = re.search(r'<sheet [^>]*name="([^"]+)"', z.read('xl/workbook.xml').decode('utf-8')).group(1)
        load = load.replace('(ooxml, embedded labels)', f'(ooxml, embedded labels, table is [{html.unescape(sheet)}])')
    q = lambda c: f'[{label(c)}]'
    header = ["SET ThousandSep='.';", "SET DecimalSep=',';", "SET MoneyThousandSep='.';", "SET MoneyDecimalSep=',';",
              "SET MoneyFormat='#.##0,00 ₺;-#.##0,00 ₺';", "SET TimeFormat='hh:mm:ss';", "SET DateFormat='DD.MM.YYYY';",
              "SET TimestampFormat='DD.MM.YYYY hh:mm:ss[.fff]';", 'SET FirstWeekDay=0;', 'SET BrokenWeeks=0;', 'SET ReferenceDay=4;', 'SET FirstMonthOfYear=1;',
              "SET CollationLocale='tr-TR';", f"SET MonthNames='{';'.join(AY)}';", f"SET LongMonthNames='{';'.join(UZUN_AY)}';",
              "SET DayNames='Pzt;Sal;Çar;Per;Cum;Cmt;Paz';", "SET LongDayNames='Pazartesi;Salı;Çarşamba;Perşembe;Cuma;Cumartesi;Pazar';"]
    script = '///$tab Ayarlar\n' + '\n'.join(header) + '\n\n///$tab Veri\n' + load
    dims = [dict(id=f'boyut-{snake(d)}', field=label(d)) for d in ds.dims]
    if ds.date:
        f = q(ds.date)
        script += (f'\n///$tab Takvim\n[Takvim]:\nLOAD\n    {f},\n    Year({f}) as [Yıl],\n    \'Ç\' & Ceil(Month({f}) / 3) as [Çeyrek],\n'
                   f'    Month({f}) as [Ay],\n    Date(MonthStart({f}), \'YYYY-MM\') as [Yıl Ay];\n'
                   f'LOAD Date([MinTarih] + IterNo() - 1) as {f}\nWHILE [MinTarih] + IterNo() - 1 <= [MaxTarih];\n'
                   f'LOAD Floor(Min({f})) as [MinTarih], Floor(Max({f})) as [MaxTarih]\nRESIDENT [{ds.table}];\n')
        dims += [dict(id='boyut-yil', field='Yıl'), dict(id='boyut-yil-ay', field='Yıl Ay')]
    expr = {'sum': 'Sum({})', 'avg': 'Avg({})', 'countd': 'Count(DISTINCT {})', 'count': 'Count({})'}
    meas = [dict(id=f'olcu-{snake(m["name"])}', name=m['name'], expr=expr[m['agg']].format(q(m['col'])), dec=m['dec']) for m in ds.measures + [ds.count]]
    dimensions = [{'qInfo': {'qId': d['id'], 'qType': 'dimension'}, 'qDim': {'qGrouping': 'N', 'qFieldDefs': [d['field']], 'qFieldLabels': [d['field']], 'title': d['field']},
                   'qMetaDef': {'title': d['field'], 'description': f'{ds.source} dosyasından', 'tags': ['rapor-sablonu']}} for d in dims]
    measures = [{'qInfo': {'qId': m['id'], 'qType': 'measure'},
                 'qMeasure': {'qLabel': m['name'], 'qDef': m['expr'], 'qGrouping': 'N', 'qExpressions': [], 'qActiveExpression': 0,
                              'qNumFormat': {'qType': 'F', 'qnDec': m['dec'], 'qUseThou': 1, 'qFmt': '#.##0' + (',' + '0' * m['dec'] if m['dec'] else ''), 'qDec': ',', 'qThou': '.'},
                              'isCustomFormatted': True},
                 'qMetaDef': {'title': m['name'], 'description': m['expr'], 'tags': ['rapor-sablonu']}} for m in meas]
    variables = [{'qInfo': {'qId': 'degisken-rapor-basligi', 'qType': 'variable'}, 'qName': 'vRaporBasligi', 'qDefinition': ds.title, 'qComment': 'Sayfa başlığında kullanılır'}]
    dim_id = {d['field']: d['id'] for d in dims}
    fetch = lambda w, h: [{'qLeft': 0, 'qTop': 0, 'qWidth': w, 'qHeight': h}]

    def chart(qid, viz, title, dim_ids, meas_ids, **extra):
        cube = {'qDimensions': [{'qLibraryId': i, 'qDef': {'autoSort': True}, 'qNullSuppression': True} for i in dim_ids],
                'qMeasures': [{'qLibraryId': i, 'qDef': {'autoSort': True}, 'qSortBy': {'qSortByNumeric': -1}} for i in meas_ids],
                'qInterColumnSortOrder': list(range(len(dim_ids) + len(meas_ids))), 'qMode': 'S', 'qSuppressZero': False, 'qSuppressMissing': True,
                'qInitialDataFetch': fetch(len(dim_ids) + len(meas_ids), max(1, 10000 // max(1, len(dim_ids) + len(meas_ids))))}
        return {'qProperty': {'qInfo': {'qId': qid, 'qType': viz}, 'visualization': viz, 'showTitles': bool(title), 'title': title, 'subtitle': '', 'footnote': '',
                              'qHyperCubeDef': cube, **extra}, 'qChildren': [], 'qEmbeddedSnapshotRef': None}

    children, cells = [], []

    def place(obj, col, row, cs, rs):
        children.append(obj)
        p = obj['qProperty']
        cells.append({'name': p['qInfo']['qId'], 'type': p['visualization'], 'col': col, 'row': row, 'colspan': cs, 'rowspan': rs,
                      'bounds': {'y': round(100 * row / 12, 4), 'x': round(100 * col / 24, 4), 'width': round(100 * cs / 24, 4), 'height': round(100 * rs / 12, 4)}})

    listboxes = [{'qProperty': {'qInfo': {'qId': f'liste-{snake(d["field"])}', 'qType': 'listbox'}, 'visualization': 'listbox', 'title': d['field'], 'showTitles': True,
                                'qListObjectDef': {'qLibraryId': d['id'], 'qDef': {'autoSort': True, 'qSortCriterias': [{'qSortByState': 1, 'qSortByNumeric': 1, 'qSortByAscii': 1}]},
                                                   'qInitialDataFetch': fetch(1, 1000)}}, 'qChildren': [], 'qEmbeddedSnapshotRef': None} for d in dims]
    place({'qProperty': {'qInfo': {'qId': 'filtreler', 'qType': 'filterpane'}, 'visualization': 'filterpane', 'showTitles': False, 'title': '',
                         'qChildListDef': {'qData': {'info': '/qInfo'}}}, 'qChildren': listboxes, 'qEmbeddedSnapshotRef': None}, 0, 0, 4, 12)
    width = 20 // len(meas)
    for i, m in enumerate(meas):
        place(chart(f'kpi-{i + 1}', 'kpi', '', [], [m['id']], showMeasureTitle=True, textAlign='center', fontSize='M'), 4 + i * width, 0, width if i < len(meas) - 1 else 20 - i * width, 2)
    main = meas[0]['id']
    first = dim_id[label(ds.dims[0])]
    place(chart('cubuk-1', 'barchart', f'{label(ds.dims[0])} bazında {ds.primary["name"]}', [first], [main], orientation='horizontal', dataPoint={'showLabels': True}), 4, 2, 10, 5)
    if ds.date:
        place(chart('cizgi-trend', 'linechart', f'Aylık {ds.primary["name"]}', ['boyut-yil-ay'], [main], dataPoint={'bubbleSizes': 5, 'showLabels': False}), 14, 2, 10, 5)
    elif len(ds.dims) > 2:
        place(chart('cubuk-3', 'barchart', f'{label(ds.dims[2])} bazında {ds.primary["name"]}', [dim_id[label(ds.dims[2])]], [main], orientation='horizontal'), 14, 2, 10, 5)
    if len(ds.dims) > 1:
        place(chart('cubuk-2', 'barchart', f'{label(ds.dims[1])} bazında {ds.primary["name"]}', [dim_id[label(ds.dims[1])]], [main], orientation='vertical'), 4, 7, 8, 5)
    table_dims = [dim_id[label(d)] for d in ds.dims] + (['boyut-yil-ay'] if ds.date else [])
    place(chart('detay-tablo', 'table', 'Detay', table_dims, [m['id'] for m in meas], totals={'show': True, 'position': 'top'}),
          12 if len(ds.dims) > 1 else 4, 7, 12 if len(ds.dims) > 1 else 20, 5)
    sheet = {'qProperty': {'qInfo': {'qId': 'rapor-sayfasi', 'qType': 'sheet'}, 'qMetaDef': {'title': ds.title, 'description': f'{ds.source} için üretilen rapor sayfası'},
                           'rank': 0, 'thumbnail': {'qStaticContentUrlDef': {'qUrl': ''}}, 'columns': 24, 'rows': 12, 'cells': cells,
                           'qChildListDef': {'qData': {'title': '/title'}}}, 'qChildren': children, 'qEmbeddedSnapshotRef': None}
    root = 'qlik-uygulama/'
    build_cmd = (f'@echo off\nrem Qlik Cloud: qlik-cli ile veri dosyasını yükler, yeni uygulama oluşturur ve şablonu kurar.\n'
                 f'rem Kullanım: build.cmd C:\\Veri\\{ds.source}   (qlik context ile kiracıya bağlı olmalısınız)\n'
                 f'if "%~1"=="" (echo Veri dosyasının yolunu verin: build.cmd C:\\Veri\\{ds.source} & exit /b 1)\nset VERI=%~f1\ncd /d "%~dp0"\n'
                 f'qlik data-file create --file "%VERI%" --name "{ds.source}"\n'
                 f'for /f %%i in (\'qlik app create --attributes-name "{ds.title}" -q\') do set APP=%%i\n'
                 'qlik app build --app %APP% --script script.qvs --dimensions dimensions.json --measures measures.json --variables variables.json '
                 '--objects objects/rapor-sayfasi.json --app-properties app-properties.json\necho Uygulama kimliği: %APP%\npause\n')
    corectl = ('# Qlik Sense Enterprise (client-managed) veya Qlik Associative Engine için: corectl build\n'
               'engine: localhost:9076\napp: ' + ds.title.replace(':', '') + '.qvf\nscript: ./script.qvs\napp-properties: ./app-properties.json\n'
               'dimensions:\n  - ./dimensions.json\nmeasures:\n  - ./measures.json\nvariables:\n  - ./variables.json\nobjects:\n  - ./objects/*.json\n')
    files = {root + 'script.qvs': script, root + 'dimensions.json': json.dumps(dimensions, ensure_ascii=False, indent=2),
             root + 'measures.json': json.dumps(measures, ensure_ascii=False, indent=2), root + 'variables.json': json.dumps(variables, ensure_ascii=False, indent=2),
             root + 'app-properties.json': json.dumps({'qTitle': ds.title, 'description': f'{ds.source} dosyasından üretilen rapor şablonu'}, ensure_ascii=False, indent=2),
             root + 'objects/rapor-sayfasi.json': json.dumps(sheet, ensure_ascii=False, indent=2), root + 'build.cmd': build_cmd, root + 'corectl.yml': corectl}
    return files, len(children), sum(len(c['qChildren']) for c in children)


@op('qlik-rapor-sablonu', title='CSV/Excel’den hazır Qlik Sense uygulaması: script, ana öğeler ve rapor sayfası', domain=D, product='Qlik Sense Enterprise', tag='Qlik rapor',
    samples=SATIS, args=DESIGN_ARGS + [arg('--lib', default='', help='Script’teki veri yolu (boşsa lib://DataFiles/<dosya>)')],
    description='Dosyadan eksiksiz bir Qlik Sense uygulama şablonu kurar: Türkçe ayarlı ve tipli load script, ana takvim, ana boyutlar ve ana ölçüler (toplam, ortalama, farklı sayı), '
                'filtre paneli, KPI’lar, çubuk ve çizgi grafikler ile detay tablodan oluşan hazır bir rapor sayfası. Qlik Cloud’da qlik-cli (“qlik app build”), '
                'Enterprise’ta corectl ile tek komutla uygulamaya yüklenir; aynı rakamlarla HTML önizleme de üretir.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='qlik-uygulama/ (script.qvs, dimensions.json, measures.json, objects/, build.cmd) + rapor-onizleme.html',
    command='python main.py --input satislar.csv --title "Satış Raporu" --dimensions bolge,urun_adi',
    expect={'olcu': 3, 'boyut': 3, 'ay': 21, 'nesne': 9, 'liste': 5, 'ana_olcu_toplam': 5912910.0})
def qlik_report(columns, rows, args):
    ds = design(columns, rows, args)
    files, objects, nested = qlik_files(columns, rows, args, ds)
    files['rapor-onizleme.html'] = preview_html(ds, 'Qlik Sense')
    return Result(f'{ds.title}: {len(ds.measures) + 1} ana ölçü, {len(ds.dims) + (2 if ds.date else 0)} ana boyut ve {objects} nesneli rapor sayfası', ['oge', 'ad', 'tanim', 'deger'],
                  plan_rows(ds), summary(ds, nesne=objects, liste=nested), files=files,
                  notes=['Qlik Cloud: qlik-uygulama/build.cmd (qlik-cli gerekir; qlik context ile kiracıya bağlanın). Enterprise: corectl.yml’deki engine adresini düzeltip “corectl build”. '
                         'Elle kurmak için script.qvs’yi veri yükleme düzenleyicisine yapıştırın.',
                         'Dosya biçimi corectl’in açık kaynak build/unbuild biçimidir; şablon canlı bir Qlik kiracısında açılarak test edilmedi.'] + ds.warnings)


# ---------- Power BI (PBIP) ----------
def tmdl_name(n):
    return n if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) else "'" + n.replace("'", "''") + "'"


def dax_col(table, col):
    return f'{tmdl_name(table)}[{col}]'


TMDL_TYPE = {'int': 'int64', 'bigint': 'int64', 'decimal': 'double', 'date': 'dateTime', 'datetime': 'dateTime', 'bool': 'boolean', 'text': 'string'}
SCHEMA = 'https://developer.microsoft.com/json-schemas/fabric/'


def pbip_files(columns, rows, args, ds):
    t = ds.table
    m_query = bigen.power_query(columns, rows, SimpleNamespace(input=args.input, path='@@YOL@@', culture='tr-TR', sheet=getattr(args, 'sheet', None))).files['sorgu.m']
    m_query = m_query.replace('File.Contents("@@YOL@@")', 'File.Contents(DosyaYolu)')
    lines = [f'table {t}', '']
    agg = {'sum': 'SUM({})', 'avg': 'AVERAGE({})', 'countd': 'DISTINCTCOUNT({})', 'count': 'COUNTROWS({})'}
    fmt_of = lambda dec: '#,0.00' if dec else '#,0'
    for m in ds.measures + [ds.count]:
        target = tmdl_name(t) if m['agg'] == 'count' else dax_col(t, label(m['col']))
        lines += [f'\tmeasure {tmdl_name(m["name"])} = {agg[m["agg"]].format(target)}', f'\t\tformatString: {fmt_of(m["dec"])}', '']
    p = ds.primary['name']
    if ds.date:
        lines += [f'\tmeasure {tmdl_name(p + " GY")} = CALCULATE([{p}], SAMEPERIODLASTYEAR(Takvim[Date]))', f'\t\tformatString: {fmt_of(ds.primary["dec"])}', '',
                  f'\tmeasure {tmdl_name(p + " YoY %")} = DIVIDE([{p}] - [{p} GY], [{p} GY])', '\t\tformatString: 0.0%;-0.0%;0.0%', '']
    measure_cols = {m['col'] for m in ds.measures}
    for c in columns:
        k = ds.kinds[c]
        lines += [f'\tcolumn {tmdl_name(label(c))}', f'\t\tdataType: {TMDL_TYPE[k]}']
        if k in ('date', 'datetime'):
            lines.append('\t\tformatString: dd.mm.yyyy' if k == 'date' else '\t\tformatString: dd.mm.yyyy hh:nn')
        elif k == 'decimal':
            lines.append('\t\tformatString: #,0.00')
        elif k in ('int', 'bigint'):
            lines.append('\t\tformatString: 0')
        lines += [f'\t\tsummarizeBy: {"sum" if c in measure_cols else "none"}', f'\t\tsourceColumn: {c}', '']
    lines += [f'\tpartition {t} = m', '\t\tmode: import', '\t\tsource =']
    lines += ['\t\t\t\t' + ln for ln in m_query.split('\n')]
    model = ['model Model', '\tculture: tr-TR', '\tdefaultPowerBIDataSourceVersion: powerBI_V3', '\tsourceQueryCulture: tr-TR', '', f'ref table {t}']
    sm = {'definition/database.tmdl': 'database\n\tcompatibilityLevel: 1600\n',
          f'definition/tables/{t}.tmdl': '\n'.join(lines) + '\n',
          'definition/expressions.tmdl': f'expression DosyaYolu = "C:\\Veri\\{ds.source}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'}
    if ds.date:
        y0, y1 = ds.years[0], ds.years[-1]
        cal = [('Date', 'dateTime', 'dd.mm.yyyy'), ('Yıl', 'int64', '0'), ('Ay No', 'int64', '0'), ('Ay', 'string', None), ('Yıl Ay', 'string', None), ('Çeyrek', 'string', None)]
        tl = ['table Takvim', '\tdataCategory: Time', '']
        for name, typ, f in cal:
            tl += [f'\tcolumn {tmdl_name(name)}', f'\t\tdataType: {typ}'] + (['\t\tisKey'] if name == 'Date' else []) + ([f'\t\tformatString: {f}'] if f else [])
            tl += ['\t\tsummarizeBy: none', '\t\tisNameInferred', f'\t\tsourceColumn: [{name}]'] + (['\t\tsortByColumn: \'Ay No\''] if name == 'Ay' else []) + ['']
        months = ', '.join(f'{i + 1}, "{a}"' for i, a in enumerate(AY))
        tl += ['\tpartition Takvim = calculated', '\t\tmode: import', '\t\tsource =',
               f'\t\t\t\tADDCOLUMNS(CALENDAR(DATE({y0}, 1, 1), DATE({y1}, 12, 31)),',
               '\t\t\t\t    "Yıl", YEAR([Date]), "Ay No", MONTH([Date]),',
               f'\t\t\t\t    "Ay", SWITCH(MONTH([Date]), {months}),',
               '\t\t\t\t    "Yıl Ay", FORMAT([Date], "YYYY-MM"), "Çeyrek", "Ç" & QUARTER([Date]))']
        sm['definition/tables/Takvim.tmdl'] = '\n'.join(tl) + '\n'
        sm['definition/relationships.tmdl'] = f'relationship rapor_tarih\n\tfromColumn: {tmdl_name(t)}.{tmdl_name(label(ds.date))}\n\ttoColumn: Takvim.Date\n'
        model.append('ref table Takvim')
    sm['definition/model.tmdl'] = '\n'.join(model) + '\n'

    # Report (PBIR)
    def field(entity, prop, measure=False):
        return {('Measure' if measure else 'Column'): {'Expression': {'SourceRef': {'Entity': entity}}, 'Property': prop}}

    def proj(entity, prop, measure=False):
        return {'field': field(entity, prop, measure), 'queryRef': f'{entity}.{prop}', 'nativeQueryRef': prop}

    visuals = {}

    def visual(name, vtype, x, y, w, h, state, sort=None, objects=None, title=None):
        v = {'visualType': vtype, 'query': {'queryState': state}, 'drillFilterOtherVisuals': True}
        if sort:
            v['query']['sortDefinition'] = {'sort': [{'field': sort[0], 'direction': sort[1]}], 'isDefaultSort': True}
        if objects:
            v['objects'] = objects
        if title:
            v['visualContainerObjects'] = {'title': [{'properties': {'show': {'expr': {'Literal': {'Value': 'true'}}},
                                                                      'text': {'expr': {'Literal': {'Value': "'" + title.replace("'", "''") + "'"}}}}}]}
        visuals[name] = {'$schema': SCHEMA + 'item/report/definition/visualContainer/2.1.0/schema.json', 'name': name,
                         'position': {'x': x, 'y': y, 'z': len(visuals) * 1000, 'height': h, 'width': w, 'tabOrder': len(visuals) * 1000}, 'visual': v}

    slicers = [(t, label(d)) for d in ds.dims] + ([('Takvim', 'Yıl')] if ds.date else [])
    sh = 680 / len(slicers)
    for i, (ent, col) in enumerate(slicers):
        visual(f'filtre{i + 1}', 'slicer', 10, 20 + i * sh, 200, sh - 10, {'Values': {'projections': [proj(ent, col)]}},
               objects={'data': [{'properties': {'mode': {'expr': {'Literal': {'Value': "'Dropdown'"}}}}}]})
    allm = ds.measures + [ds.count]
    cw = 1040 / len(allm)
    for i, m in enumerate(allm):
        visual(f'kpi{i + 1}', 'card', 225 + i * cw, 20, cw - 15, 110, {'Values': {'projections': [proj(t, m['name'], True)]}})
    main = proj(t, p, True)
    d0 = label(ds.dims[0])
    visual('cubuk1', 'clusteredBarChart', 225, 145, 515, 280, {'Category': {'projections': [proj(t, d0)]}, 'Y': {'projections': [main]}},
           sort=(field(t, p, True), 'Descending'), title=f'{d0} bazında {p}')
    if ds.date:
        visual('trend', 'lineChart', 750, 145, 515, 280, {'Category': {'projections': [proj('Takvim', 'Yıl Ay')]}, 'Y': {'projections': [main]}},
               sort=(field('Takvim', 'Yıl Ay'), 'Ascending'), title=f'Aylık {p}')
    if len(ds.dims) > 1:
        d1 = label(ds.dims[1])
        visual('cubuk2', 'clusteredColumnChart', 225, 440, 400, 270, {'Category': {'projections': [proj(t, d1)]}, 'Y': {'projections': [main]}},
               sort=(field(t, p, True), 'Descending'), title=f'{d1} bazında {p}')
    tx = 640 if len(ds.dims) > 1 else 225
    visual('detay', 'tableEx', tx, 440, 1265 - tx, 270, {'Values': {'projections': [proj(t, label(d)) for d in ds.dims] + [proj(t, m['name'], True) for m in allm]}},
           sort=(field(t, p, True), 'Descending'), title='Detay')
    name = re.sub(r'[^\w\- ]', '', ds.title).strip() or 'Rapor'
    rep = {'definition.pbir': {'$schema': SCHEMA + 'item/report/definitionProperties/2.0.0/schema.json', 'version': '4.0',
                               'datasetReference': {'byPath': {'path': f'../{name}.SemanticModel'}}},
           'definition/version.json': {'$schema': SCHEMA + 'item/report/definition/versionMetadata/1.0.0/schema.json', 'version': '2.0.0'},
           'definition/report.json': {'$schema': SCHEMA + 'item/report/definition/report/3.0.0/schema.json', 'themeCollection': {}},
           'definition/pages/pages.json': {'$schema': SCHEMA + 'item/report/definition/pagesMetadata/1.0.0/schema.json', 'pageOrder': ['rapor'], 'activePageName': 'rapor'},
           'definition/pages/rapor/page.json': {'$schema': SCHEMA + 'item/report/definition/page/2.0.0/schema.json', 'name': 'rapor', 'displayName': ds.title[:100],
                                                'displayOption': 'FitToPage', 'height': 720, 'width': 1280}}
    for vn, v in visuals.items():
        rep[f'definition/pages/rapor/visuals/{vn}/visual.json'] = v
    files = {f'{name}.pbip': json.dumps({'$schema': SCHEMA + 'pbip/pbipProperties/1.0.0/schema.json', 'version': '1.0',
                                         'artifacts': [{'report': {'path': f'{name}.Report'}}], 'settings': {'enableAutoRecovery': True}}, ensure_ascii=False, indent=2),
             f'{name}.SemanticModel/definition.pbism': json.dumps({'$schema': SCHEMA + 'item/semanticModel/definitionProperties/1.0.0/schema.json', 'version': '4.2', 'settings': {}}, indent=2),
             '.gitignore': '**/.pbi/localSettings.json\n**/.pbi/cache.abf\n'}
    files.update({f'{name}.SemanticModel/{k}': v for k, v in sm.items()})
    files.update({f'{name}.Report/{k}': json.dumps(v, ensure_ascii=False, indent=2) for k, v in rep.items()})
    return {'pbip/' + k: v for k, v in files.items()}, len(visuals), name


@op('pbi-rapor-sablonu', title='CSV/Excel’den hazır Power BI projesi (PBIP): TMDL model, DAX ölçüleri ve rapor sayfası', domain=D, product='Power BI Desktop', tag='PBIP rapor',
    samples=SATIS, args=DESIGN_ARGS,
    description='Power BI Desktop’ın proje biçiminde (PBIP) açılmaya hazır bir rapor üretir: Power Query ile Türkçe kültürde tipleyen tablo, dosya yolu parametresi, takvim tablosu ve '
                'ilişkisi, DAX ölçüleri (toplam, ortalama, farklı sayı, geçen yıl, yıllık değişim) ve dilimleyiciler, kartlar, çubuk/çizgi grafikler ve detay tablodan oluşan PBIR '
                'rapor sayfası. Model TMDL, rapor JSON biçimindedir; Git’te satır satır izlenebilir.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='pbip/<Ad>.pbip + <Ad>.SemanticModel/ (TMDL) + <Ad>.Report/ (PBIR) + rapor-onizleme.html',
    command='python main.py --input satislar.csv --title "Satış Raporu" --measures tutar,adet',
    expect={'olcu': 3, 'boyut': 3, 'ay': 21, 'gorsel': 12, 'ana_olcu_toplam': 5912910.0})
def pbi_report(columns, rows, args):
    ds = design(columns, rows, args)
    files, n, name = pbip_files(columns, rows, args, ds)
    files['rapor-onizleme.html'] = preview_html(ds, 'Power BI')
    return Result(f'{name}.pbip: {len(ds.measures) + 1 + (2 if ds.date else 0)} DAX ölçüsü ve {n} görselli rapor sayfası', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds),
                  summary(ds, gorsel=n), files=files,
                  notes=['Power BI Desktop’ta Dosya > Aç ile pbip klasöründeki .pbip dosyasını açın (Seçenekler > Önizleme özellikleri’nde “Power BI Project (.pbip)” ve “PBIR” açık olmalı). '
                         'Parametreler’den DosyaYolu’nu dosyanızın yoluna çevirip Yenile’ye basın.',
                         'TMDL dosyaları Microsoft’un TMDL ayrıştırıcısından, rapor JSON’ları resmî PBIR şemalarından geçirildi; proje Power BI Desktop’ta açılarak test edilmedi.'] + ds.warnings)


# ---------- Tableau (.twb) ----------
TW_TYPE = {'int': 'integer', 'bigint': 'integer', 'decimal': 'real', 'date': 'date', 'datetime': 'datetime', 'bool': 'boolean', 'text': 'string'}


def twb_text(columns, rows, args, ds):
    a = quoteattr
    ds_name, conn = 'federated.rapor', 'textscan.rapor'
    raw = Path(args.input).read_text(encoding='utf-8-sig', errors='replace')[:20000]
    sep = max([';', ',', '\t', '|'], key=raw.count)
    sep_attr = 't' if sep == '\t' else sep
    folder = (args.folder or 'C:/Veri').replace('\\', '/')
    rel_cols = ''.join(f'\n            <column datatype={a(TW_TYPE[ds.kinds[c]])} name={a(c)} ordinal={a(str(i))} />' for i, c in enumerate(columns))
    measure_cols = {m['col'] for m in ds.measures}
    ds_cols = ''.join(f'\n      <column caption={a(label(c))} datatype={a(TW_TYPE[ds.kinds[c]])} name={a(f"[{c}]")} role={a("measure" if c in measure_cols else "dimension")} '
                      f'type={a("quantitative" if c in measure_cols else "ordinal" if ds.kinds[c] in ("date", "datetime") else "nominal")} />' for c in columns)
    deriv = {'sum': ('Sum', 'sum'), 'avg': ('Avg', 'avg'), 'countd': ('CountD', 'ctd'), 'count': ('Count', 'cnt')}

    def inst(c, how='none'):
        if how == 'none':
            return f'[none:{c}:nk]', 'None', 'nominal'
        if how == 'month':
            return f'[tmn:{c}:qk]', 'Month-Trunc', 'quantitative'
        d, p = deriv[how]
        return f'[{p}:{c}:qk]', d, 'quantitative'

    def deps(pairs):
        cols, insts = {}, {}
        for c, how in pairs:
            measure = c in measure_cols
            cols[c] = (f'\n            <column caption={a(label(c))} datatype={a(TW_TYPE[ds.kinds[c]])} name={a(f"[{c}]")} role={a("measure" if measure else "dimension")} '
                       f'type={a("quantitative" if measure else "ordinal" if ds.kinds[c] in ("date", "datetime") else "nominal")} />')
            n, d, typ = inst(c, how)
            insts[n] = f'\n            <column-instance column={a(f"[{c}]")} derivation={a(d)} name={a(n)} pivot="key" type={a(typ)} />'
        return ''.join(cols.values()) + ''.join(insts.values())

    def uid(name):
        return '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'rapor-sablonu/{ds.source}/{name}')).upper() + '}'

    def ref(c, how='none'):
        return f'[{ds_name}].{inst(c, how)[0]}'

    def sheet(name, pairs, mark, rows_=None, cols_=None, text=None):
        enc = f'\n              <encodings>\n                <text column={a(text)} />\n              </encodings>' if text else ''
        return (f'\n    <worksheet name={a(name)}>\n      <table>\n        <view>\n          <datasources>\n            <datasource caption={a(ds.table)} name={a(ds_name)} />\n'
                f'          </datasources>\n          <datasource-dependencies datasource={a(ds_name)}>{deps(pairs)}\n          </datasource-dependencies>\n'
                f'          <aggregation value="true" />\n        </view>\n        <style />\n        <panes>\n          <pane selection-relaxation-option="selection-relaxation-allow">\n'
                f'            <view>\n              <breakdown value="auto" />\n            </view>\n            <mark class={a(mark)} />{enc}\n          </pane>\n        </panes>\n'
                f'        <rows>{escape(rows_ or "")}</rows>\n        <cols>{escape(cols_ or "")}</cols>\n      </table>\n      <simple-id uuid={a(uid(name))} />\n    </worksheet>')

    sheets, names = [], []
    pm = ds.primary
    for m in ds.measures + [ds.count]:
        nm = f'KPI {m["name"]}'
        names.append(nm)
        sheets.append(sheet(nm, [(m['col'], m['agg'])], 'Text', text=ref(m['col'], m['agg'])))
    for d in ds.dims[:2]:
        nm = f'{label(d)} bazında'
        names.append(nm)
        sheets.append(sheet(nm, [(d, 'none'), (pm['col'], pm['agg'])], 'Bar', rows_=ref(d), cols_=ref(pm['col'], pm['agg'])))
    if ds.date:
        names.append('Aylık trend')
        sheets.append(sheet('Aylık trend', [(ds.date, 'month'), (pm['col'], pm['agg'])], 'Line', rows_=ref(pm['col'], pm['agg']), cols_=ref(ds.date, 'month')))
    names.append('Detay')
    sheets.append(sheet('Detay', [(d, 'none') for d in ds.dims] + [(pm['col'], pm['agg'])], 'Text', rows_=' / '.join(ref(d) for d in ds.dims), text=ref(pm['col'], pm['agg'])))
    k = len(ds.measures) + 1
    zones, zid = [], 3
    for i in range(k):
        zones.append((names[i], i * 100000 // k, 0, 100000 // k, 16000))
    body = names[k:]
    half = [(n, (j % 2) * 50000, 16000 + (j // 2) * 42000, 50000, 42000) for j, n in enumerate(body)]
    zones += half
    zone_xml = ''
    for n, x, y, w, h in zones:
        zone_xml += f'\n          <zone h={a(str(h))} id={a(str(zid))} name={a(n)} w={a(str(w))} x={a(str(x))} y={a(str(y))} />'
        zid += 1
    cards = ('<cards><edge name="left"><strip size="160"><card type="pages" /><card type="filters" /><card type="marks" /></strip></edge>'
             '<edge name="top"><strip size="2147483647"><card type="columns" /></strip><strip size="2147483647"><card type="rows" /></strip>'
             '<strip size="31"><card type="title" /></strip></edge></cards>')
    windows = ''.join(f'\n    <window class="worksheet" hidden="true" name={a(n)}>{cards}<simple-id uuid={a(uid("w:" + n))} /></window>' for n in names)
    return f'''<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='26.1' source-build='0.0.0 (0000.0.0.0)' source-platform='win' version='26.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
    <ManifestByVersion />
  </document-format-change-manifest>
  <datasources>
    <datasource caption={a(ds.table)} inline="true" name={a(ds_name)} version="18.1">
      <connection class="federated">
        <named-connections>
          <named-connection caption={a(ds.source)} name={a(conn)}>
            <connection class="textscan" directory={a(folder)} filename={a(ds.source)} password="" server="" />
          </named-connection>
        </named-connections>
        <relation connection={a(conn)} name={a(ds.source)} table={a("[" + ds.source.replace(".", "#") + "]")} type="table">
          <columns character-set="UTF-8" header="yes" locale="tr_TR" separator={a(sep_attr)}>{rel_cols}
          </columns>
        </relation>
      </connection>
      <aliases enabled="yes" />{ds_cols}
    </datasource>
  </datasources>
  <worksheets>{''.join(sheets)}
  </worksheets>
  <dashboards>
    <dashboard name={a(ds.title)}>
      <style />
      <size maxheight="900" maxwidth="1400" minheight="900" minwidth="1400" />
      <zones>
        <zone h="100000" id="2" type-v2="layout-basic" w="100000" x="0" y="0">{zone_xml}
        </zone>
      </zones>
      <simple-id uuid={a(uid("pano"))} />
    </dashboard>
  </dashboards>
  <windows>{windows}
    <window class="dashboard" maximized="true" name={a(ds.title)}><viewpoints>{''.join(f'<viewpoint name={a(n)} />' for n in names)}</viewpoints><active id="-1" /><simple-id uuid={a(uid("w:pano"))} /></window>
  </windows>
  <explain-data enabled-for-viewer="true" extreme-values-enabled-for-all="true">
    <explanation-types />
  </explain-data>
</workbook>
''', len(names)


@op('tableau-rapor-sablonu', title='CSV’den hazır Tableau çalışma kitabı (.twb): KPI, kırılım, trend sayfaları ve pano', domain=D, product='Tableau Desktop', tag='Tableau rapor',
    samples=SATIS, args=DESIGN_ARGS + [arg('--folder', default='C:/Veri', help='CSV’nin Tableau’nun açacağı bilgisayardaki klasörü')],
    description='CSV dosyasına metin bağlantısıyla bağlanan bir Tableau çalışma kitabı yazar: sütun başlıkları ve rolleri (boyut/ölçü), her KPI için metin sayfası, boyut bazında çubuk '
                'grafikler, aylık trend çizgisi, detay tablo ve bunları yerleştiren bir pano. Çalışma kitabı Tableau’nun resmî TWB şemasına (XSD 2026.1) uygundur.',
    input='CSV (ayırıcı ; , sekme veya |)', output='rapor.twb + rapor-onizleme.html', command='python main.py --input satislar.csv --folder "D:/Paylasim/Veri" --title "Satış Raporu"',
    expect={'olcu': 3, 'boyut': 3, 'ay': 21, 'sayfa': 8, 'ana_olcu_toplam': 5912910.0})
def tableau_report(columns, rows, args):
    if Path(args.input).suffix.lower() in ('.xlsx', '.xlsm'):
        raise ValueError('Tableau şablonu CSV metin bağlantısı kurar; Excel dosyasını önce CSV olarak kaydedin.')
    ds = design(columns, rows, args)
    text, n = twb_text(columns, rows, args, ds)
    return Result(f'rapor.twb: {n} sayfa ve “{ds.title}” panosu', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds), summary(ds, sayfa=n),
                  files={'rapor.twb': text, 'rapor-onizleme.html': preview_html(ds, 'Tableau')},
                  notes=[f'CSV’yi {args.folder} klasörüne koyun (ya da --folder ile yolu verin) ve rapor.twb’yi Tableau Desktop 2026.1 veya üzeriyle açın.',
                         'Dosya Tableau’nun resmî XSD şemasından geçirildi (sözdizimi); Tableau’da açılarak test edilmedi. Türkçe ondalıklar tr_TR yerel ayarıyla okunur.'] + ds.warnings)


# ---------- Excel (.xlsx) ----------
def col_letter(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx_bytes(columns, rows, ds):
    n = len(rows)
    letters = {c: col_letter(i) for i, c in enumerate(columns)}
    rng = lambda c: f"Veri!${letters[c]}$2:${letters[c]}${n + 1}"
    nums = {c: numbers(rows, c) for c in columns if ds.kinds[c] in ('int', 'bigint', 'decimal')}
    epoch = date(1899, 12, 30)
    # styles: 0 normal, 1 title, 2 header, 3 int, 4 decimal, 5 date, 6 percent, 7 muted
    fmt_style = lambda dec: 4 if dec else 3

    def cell(ref, value=None, style=0, formula=None):
        attrs = f' r="{ref}"' + (f' s="{style}"' if style else '')
        if formula is not None:
            v = '' if value is None else f'<v>{value}</v>'
            return f'<c{attrs}><f>{escape(formula)}</f>{v}</c>'
        if value is None or value == '':
            return f'<c{attrs}/>' if style else ''
        if isinstance(value, (int, float)):
            return f'<c{attrs}><v>{value}</v></c>'
        return f'<c{attrs} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'

    def sheet_xml(rows_xml, cols_xml='', extra=''):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' + (f'<cols>{cols_xml}</cols>' if cols_xml else '')
                + '<sheetData>' + ''.join(f'<row r="{r}">{"".join(cs)}</row>' for r, cs in sorted(rows_xml.items()) if cs) + '</sheetData>' + extra + '</worksheet>')

    # Data sheet
    data = {1: [cell(f'{letters[c]}1', c, 2) for c in columns]}
    for i, r in enumerate(rows):
        cs = []
        for c in columns:
            ref, k = f'{letters[c]}{i + 2}', ds.kinds[c]
            if c in nums and nums[c][i] is not None:
                cs.append(cell(ref, nums[c][i], 4 if k == 'decimal' else 0))
            elif k == 'date' and tabular.parse_date(r[c]):
                cs.append(cell(ref, (tabular.parse_date(r[c])[0].date() - epoch).days, 5))
            else:
                cs.append(cell(ref, r[c].strip()))
        data[i + 2] = cs
    widths = ''.join(f'<col min="{i + 1}" max="{i + 1}" width="{min(40, max(10, len(c) + 2, *(len(r[c]) + 2 for r in rows[:200])))}" customWidth="1"/>' for i, c in enumerate(columns))
    last = f'{col_letter(len(columns) - 1)}{n + 1}'
    data_xml = sheet_xml(data, widths, '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>')
    table_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="1" name="VeriTablosu" '
                 f'displayName="VeriTablosu" ref="A1:{last}" totalsRowShown="0"><autoFilter ref="A1:{last}"/><tableColumns count="{len(columns)}">'
                 + ''.join(f'<tableColumn id="{i + 1}" name={quoteattr(c)}/>' for i, c in enumerate(columns))
                 + '</tableColumns><tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>')

    # Summary sheet
    s = defaultdict(list)
    s[1].append(cell('A1', ds.title, 1))
    s[2].append(cell('A2', f'Kaynak: {ds.source} · {n} satır · formüller Veri sayfasından hesaplanır', 7))
    allm = ds.measures + [ds.count]
    for j, (m, (name, v, dec)) in enumerate(zip(allm, ds.kpis)):
        L = col_letter(j)
        c = m['col']
        f = {'sum': f'SUM({rng(c)})', 'avg': f'AVERAGE({rng(c)})', 'count': f'ROWS({rng(c)})',
             'countd': f'SUMPRODUCT(({rng(c)}<>"")/COUNTIF({rng(c)},{rng(c)}&""))'}[m['agg']]
        s[4].append(cell(f'{L}4', name, 2))
        s[5].append(cell(f'{L}5', round(v, 6) if v is not None else None, fmt_style(dec), f))
    pm = ds.primary
    sumf = 'SUMIFS' if pm['agg'] == 'sum' else 'AVERAGEIFS'
    row, charts = 7, []
    for d in ds.dims:
        items = ds.breakdown[d]
        s[row].append(cell(f'A{row}', label(d), 2))
        s[row].append(cell(f'B{row}', pm['name'], 2))
        s[row].append(cell(f'C{row}', 'Pay', 2))
        top, bottom = row + 1, row + len(items)
        for i, (k, v) in enumerate(items):
            r_ = row + 1 + i
            s[r_].append(cell(f'A{r_}', k))
            crit = '""' if k == '(boş)' else f'A{r_}' if not re.search(r'[*?~]|^[=<>]', k) else '"=' + re.sub(r'([*?~])', r'~\1', k).replace('"', '""') + '"'
            s[r_].append(cell(f'B{r_}', round(v, 6) if v is not None else None, fmt_style(pm['dec']), f'{sumf}({rng(pm["col"])},{rng(d)},{crit})'))
            total = sum(x or 0 for _, x in items)
            s[r_].append(cell(f'C{r_}', round((v or 0) / total, 6) if total and pm['agg'] == 'sum' else None, 6,
                              f'IFERROR(B{r_}/SUM($B${top}:$B${bottom}),"")' if pm['agg'] == 'sum' else '""'))
        charts.append(('bar', f'{label(d)} bazında {pm["name"]}', f"'Özet'!$A${top}:$A${bottom}", f"'Özet'!$B${top}:$B${bottom}", [k for k, _ in items], [v for _, v in items]))
        row = bottom + 2
    if ds.trend:
        s[row].append(cell(f'A{row}', 'Ay', 2))
        s[row].append(cell(f'B{row}', pm['name'], 2))
        top = row + 1
        for i, (k, v) in enumerate(ds.trend):
            r_ = row + 1 + i
            y, mo = int(k[:4]), int(k[5:])
            ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
            s[r_].append(cell(f'A{r_}', k))
            dr = rng(ds.date)
            s[r_].append(cell(f'B{r_}', round(v, 6) if v is not None else None, fmt_style(pm['dec']),
                              f'{sumf}({rng(pm["col"])},{dr},">="&DATE({y},{mo},1),{dr},"<"&DATE({ny},{nm},1))'))
        bottom = row + len(ds.trend)
        charts.append(('line', f'Aylık {pm["name"]}', f"'Özet'!$A${top}:$A${bottom}", f"'Özet'!$B${top}:$B${bottom}", [k for k, _ in ds.trend], [v for _, v in ds.trend]))
    sum_cols = '<col min="1" max="1" width="24" customWidth="1"/><col min="2" max="6" width="18" customWidth="1"/>'
    summary_xml = sheet_xml(s, sum_cols, '<drawing r:id="rId1"/>')

    def chart_xml(kind, title, cat, val, cats, vals):
        pts = ''.join(f'<c:pt idx="{i}"><c:v>{escape(str(x))}</c:v></c:pt>' for i, x in enumerate(cats))
        npts = ''.join(f'<c:pt idx="{i}"><c:v>{x}</c:v></c:pt>' for i, x in enumerate(vals) if x is not None)
        ser = (f'<c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>{escape(pm["name"])}</c:v></c:tx>'
               + ('<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="2F6FDE"/></a:solidFill></a:ln></c:spPr><c:marker><c:symbol val="circle"/><c:size val="5"/></c:marker>'
                  if kind == 'line' else '<c:spPr><a:solidFill><a:srgbClr val="2F6FDE"/></a:solidFill></c:spPr><c:invertIfNegative val="0"/>')
               + f'<c:cat><c:strRef><c:f>{escape(cat)}</c:f><c:strCache><c:ptCount val="{len(cats)}"/>{pts}</c:strCache></c:strRef></c:cat>'
               f'<c:val><c:numRef><c:f>{escape(val)}</c:f><c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="{len(vals)}"/>{npts}</c:numCache></c:numRef></c:val>'
               + ('<c:smooth val="0"/>' if kind == 'line' else '') + '</c:ser>')
        if kind == 'bar':
            plot = f'<c:barChart><c:barDir val="bar"/><c:grouping val="clustered"/><c:varyColors val="0"/>{ser}<c:gapWidth val="60"/><c:axId val="1001"/><c:axId val="1002"/></c:barChart>'
        else:
            plot = f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{ser}<c:marker val="1"/><c:axId val="1001"/><c:axId val="1002"/></c:lineChart>'
        axes = ('<c:catAx><c:axId val="1001"/><c:scaling><c:orientation val="' + ('maxMin' if kind == 'bar' else 'minMax') + '"/></c:scaling><c:delete val="0"/>'
                '<c:axPos val="' + ('l' if kind == 'bar' else 'b') + '"/><c:numFmt formatCode="General" sourceLinked="0"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/>'
                '<c:tickLblPos val="nextTo"/><c:crossAx val="1002"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
                '<c:valAx><c:axId val="1002"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="' + ('t' if kind == 'bar' else 'l') + '"/>'
                '<c:majorGridlines/><c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
                '<c:crossAx val="1001"/><c:crosses val="' + ('max' if kind == 'bar' else 'autoZero') + '"/><c:crossBetween val="between"/></c:valAx>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<c:roundedCorners val="0"/><c:chart><c:title><c:tx><c:rich><a:bodyPr/><a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr><a:r><a:rPr lang="tr-TR" sz="1200" b="1"/>'
                f'<a:t>{escape(title)}</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>{plot}{axes}</c:plotArea>'
                '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>')

    anchors, chart_files = [], {}
    for i, ch in enumerate(charts):
        chart_files[f'xl/charts/chart{i + 1}.xml'] = chart_xml(*ch)
        r0 = 3 + i * 18
        anchors.append(f'<xdr:twoCellAnchor><xdr:from><xdr:col>7</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{r0}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                       f'<xdr:to><xdr:col>15</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{r0 + 16}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                       f'<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="{i + 2}" name="Grafik {i + 1}"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
                       '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
                       f'<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId{i + 1}"/></a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor>')
    drawing = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
               'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' + ''.join(anchors) + '</xdr:wsDr>')
    rel = lambda items: ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + ''.join(f'<Relationship Id="{i}" Type="http://schemas.openxmlformats.org/{t}" Target="{g}"/>' for i, t, g in items) + '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="4"><numFmt numFmtId="164" formatCode="#,##0"/><numFmt numFmtId="165" formatCode="#,##0.00"/><numFmt numFmtId="166" formatCode="dd.mm.yyyy"/>'
              '<numFmt numFmtId="167" formatCode="0.0%"/></numFmts>'
              '<fonts count="4"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="16"/><name val="Calibri"/><family val="2"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/><family val="2"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F7"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="8"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="167" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="0"/></bookViews>'
                '<sheets><sheet name="Özet" sheetId="1" r:id="rId1"/><sheet name="Veri" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          '<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>'
          '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
          + ''.join(f'<Override PartName="/{p}" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>' for p in chart_files)
          + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(ds.title)}</dc:title><dc:creator>agency-codeblocks</dc:creator></cp:coreProperties>')
    parts = {'[Content_Types].xml': ct,
             '_rels/.rels': rel([('rId1', 'officeDocument/2006/relationships/officeDocument', 'xl/workbook.xml'),
                                 ('rId2', 'package/2006/relationships/metadata/core-properties', 'docProps/core.xml')]),
             'docProps/core.xml': core, 'xl/workbook.xml': workbook,
             'xl/_rels/workbook.xml.rels': rel([('rId1', 'officeDocument/2006/relationships/worksheet', 'worksheets/sheet1.xml'),
                                                ('rId2', 'officeDocument/2006/relationships/worksheet', 'worksheets/sheet2.xml'),
                                                ('rId3', 'officeDocument/2006/relationships/styles', 'styles.xml')]),
             'xl/styles.xml': styles, 'xl/worksheets/sheet1.xml': summary_xml, 'xl/worksheets/sheet2.xml': data_xml,
             'xl/worksheets/_rels/sheet1.xml.rels': rel([('rId1', 'officeDocument/2006/relationships/drawing', '../drawings/drawing1.xml')]),
             'xl/worksheets/_rels/sheet2.xml.rels': rel([('rId1', 'officeDocument/2006/relationships/table', '../tables/table1.xml')]),
             'xl/tables/table1.xml': table_xml, 'xl/drawings/drawing1.xml': drawing,
             'xl/drawings/_rels/drawing1.xml.rels': rel([(f'rId{i + 1}', 'officeDocument/2006/relationships/chart', f'../charts/chart{i + 1}.xml') for i in range(len(charts))]),
             **chart_files}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode('utf-8'), zipfile.ZIP_DEFLATED)
    return buf.getvalue(), len(charts), sum(c.count('<f>') for v in s.values() for c in v)


@op('excel-rapor-sablonu', title='CSV/Excel’den formüllü ve grafikli Excel raporu (.xlsx)', domain=D, product='Rapor gereksinimleri', tag='Excel rapor', samples=SATIS, args=DESIGN_ARGS,
    description='Veriyi tipli bir Excel tablosuna (filtreli “Veri” sayfası) koyar ve “Özet” sayfasında canlı formüllerle KPI’lar (SUM, AVERAGE, farklı sayı), boyut bazında '
                'SUMIFS kırılımları ve payları, aylık trend tablosu ile çubuk ve çizgi grafikler kurar. Veri sayfasına satır eklendiğinde formüller yeniden hesaplanır; ek eklenti '
                'veya makro yoktur. Dosya Open XML SDK doğrulayıcısından geçer.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='rapor.xlsx + rapor-onizleme.html', command='python main.py --input satislar.csv --title "Satış Raporu" --dimensions bolge,durum',
    expect={'olcu': 3, 'boyut': 3, 'ay': 21, 'grafik': 4, 'formul': 49, 'ana_olcu_toplam': 5912910.0})
def excel_report(columns, rows, args):
    ds = design(columns, rows, args)
    data, charts, formulas = xlsx_bytes(columns, rows, ds)
    return Result(f'rapor.xlsx: {formulas} formül, {charts} grafik ({len(rows)} veri satırı)', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds),
                  summary(ds, grafik=charts, formul=formulas), files={'rapor.xlsx': data, 'rapor-onizleme.html': preview_html(ds, 'Excel')},
                  notes=['Formüller dosya açılınca yeniden hesaplanır; Özet’teki önbellek değerleri Python’la hesaplanıp yazıldığı için önizlemede de görünür. '
                         'Yeni satırları Veri tablosunun sonuna ekleyin; aralıklar sabit olduğu için çok satır eklerseniz formül aralıklarını genişletin.'] + ds.warnings)


@op('html-rapor', title='CSV/Excel’den tek dosyalık HTML rapor panosu', domain=D, product='Rapor gereksinimleri', tag='HTML pano', samples=SATIS, args=DESIGN_ARGS,
    description='Hiçbir BI aracı gerektirmeyen, e-postayla gönderilebilen tek dosyalık bir pano üretir: KPI kartları, boyut bazında çubuklar ve aylık trend çizgisi. JavaScript ve dış '
                'bağlantı içermez, açık/koyu temaya ve telefona uyar. Diğer rapor şablonlarının önizlemesi de bu sayfadır.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='rapor.html + sonuc.csv (rapor planı)', command='python main.py --input satislar.csv --title "Eylül Satışları"',
    expect={'olcu': 3, 'kpi': 4, 'boyut': 3, 'ay': 21, 'ana_olcu_toplam': 5912910.0})
def html_report(columns, rows, args):
    ds = design(columns, rows, args)
    return Result(f'rapor.html: {len(ds.kpis)} KPI, {len(ds.dims)} kırılım{", aylık trend" if ds.trend else ""}', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds), summary(ds),
                  files={'rapor.html': preview_html(ds, 'HTML')}, notes=['Sayfa dış kaynak yüklemez; tarayıcıda açın veya e-posta ekine koyun.'] + ds.warnings)


# ---------- dbt project ----------
def ident(c):
    return snake(c) or 'sutun'


def yaml_dump(obj, indent=0):
    """Small YAML writer for plain dicts/lists/strings/numbers; strings are quoted whenever YAML could read them differently."""
    pad = '  ' * indent

    def scalar(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v)
        if (s == '' or re.search(r'[:#{}\[\],&*?|<>=!%@`"\'\\]|^[-\s]|\s$', s) or s.lower() in ('yes', 'no', 'true', 'false', 'null', 'on', 'off', 'y', 'n', '~')
                or re.fullmatch(r'[\d.eE+-]+', s)):
            return json.dumps(s, ensure_ascii=False)
        return s

    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f'{pad}{scalar(k)}:\n' + yaml_dump(v, indent + 1).rstrip('\n'))
            elif isinstance(v, (dict, list)):
                out.append(f'{pad}{scalar(k)}: {"{}" if isinstance(v, dict) else "[]"}')
            else:
                out.append(f'{pad}{scalar(k)}: {scalar(v)}')
    else:
        for v in obj:
            if isinstance(v, dict) and v:
                first, *rest = yaml_dump(v, indent + 1).rstrip('\n').split('\n')
                out.append(f'{pad}- {first.strip()}' + ''.join('\n' + line for line in rest))
            elif isinstance(v, list) and v:
                out.append(f'{pad}-\n' + yaml_dump(v, indent + 1).rstrip('\n'))
            else:
                out.append(f'{pad}- {scalar(v) if not isinstance(v, (dict, list)) else "{}" if isinstance(v, dict) else "[]"}')
    return '\n'.join(out) + '\n'


def dbt_files(columns, rows, args, ds):
    proj = ident(Path(args.input).stem)
    name, seed = f'rapor_{proj}', proj
    stg, fct, monthly = f'stg_{proj}', f'fct_{proj}', f'{proj}_aylik'
    src = Path(args.input)
    if src.suffix.lower() in ('.xlsx', '.xlsm'):
        seed_text = ';'.join(columns) + '\n' + ''.join(';'.join(r[c].replace(';', ',') for c in columns) + '\n' for r in rows)
        sep = ';'
    else:
        seed_text = src.read_text(encoding='utf-8-sig', errors='replace')
        sep = max([';', ',', '\t', '|'], key=seed_text[:20000].count)
    ref = lambda m: "{{ ref('" + m + "') }}"
    quote = lambda c: c if re.fullmatch(r'[a-z_][a-z0-9_]*', c) else "{{ adapter.quote('" + c.replace("'", "\\'") + "') }}"
    sel = []
    for c in columns:
        k, col = ds.kinds[c], quote(c)
        if k == 'decimal':
            style = tabular.number_style([r[c] for r in rows if r[c].strip()])
            expr = (f"cast(replace(replace(trim({col}), '.', ''), ',', '.') as numeric(18, 4))" if style == 'tr'
                    else f"cast(replace(trim({col}), ',', '') as numeric(18, 4))")
        elif k in ('int', 'bigint'):
            expr = f"cast(replace(replace(trim({col}), '.', ''), ',', '') as {'bigint' if k == 'bigint' else 'integer'})"
        elif k == 'date':
            expr = '{{ metin_tarih("' + col + '") }}' if col == c else f'{{{{ metin_tarih(adapter.quote("{c}")) }}}}'
        else:
            expr = f'trim({col})'
        sel.append(f'    {expr} as {ident(c)}')
    files = {}
    files[f'models/staging/{stg}.sql'] = (f'-- {ds.source} dosyasından üretildi: metinler kırpılır, sayılar ve tarihler tiplenir.\n'
                                          f'with kaynak as (\n    select * from {ref(seed)}\n)\n\nselect\n' + ',\n'.join(sel) + '\nfrom kaynak\n')
    d = ident(ds.date) if ds.date else None
    month = '    cast({{ dbt.date_trunc("month", "' + d + '") }} as date) as ay,\n' if d else ''
    files[f'models/marts/{fct}.sql'] = f'select\n{month}    *\nfrom {ref(stg)}\n'
    agg_sql = {'sum': 'sum({})', 'avg': 'avg({})', 'countd': 'count(distinct {})', 'count': 'count(*)'}
    metric_cols = [f'    {agg_sql[m["agg"]].format(ident(m["col"]))} as {ident(m["name"])}' for m in ds.measures + [ds.count]]
    models = [stg, fct]
    if d:
        files[f'models/marts/{monthly}.sql'] = 'select\n    ay,\n' + ',\n'.join(metric_cols) + f'\nfrom {ref(fct)}\ngroup by ay\n'
        models.append(monthly)
    p, pc = ds.primary, ident(ds.primary['col'])
    for dim in ds.dims:
        dn = f'{proj}_{ident(dim)}_ozet'
        share = f',\n    sum({pc}) / nullif(sum(sum({pc})) over (), 0) as pay' if p['agg'] == 'sum' else ''
        files[f'models/marts/{dn}.sql'] = f'select\n    {ident(dim)},\n' + ',\n'.join(metric_cols) + share + f'\nfrom {ref(fct)}\ngroup by {ident(dim)}\n'
        models.append(dn)
    warn = {'config': {'severity': 'warn'}}
    cols_yml, n_tests = [], 0
    for c in columns:
        tests = []
        if c == ds.key:
            tests = ['not_null', 'unique']
        elif c == ds.date:
            tests = ['not_null']
        elif c in ds.dims:
            values = sorted({v for v, _ in ds.breakdown[c] if v != '(boş)'})
            tests = [{'not_null': warn}] + ([{'accepted_values': {'arguments': {'values': values}, **warn}}] if len(values) <= 12 else [])
        elif c in {m['col'] for m in ds.measures}:
            tests = [{'not_null': warn}]
        entry = {'name': ident(c), 'description': f'Kaynak sütun: {c}'}
        if tests:
            entry['data_tests'] = tests
            n_tests += len(tests)
        cols_yml.append(entry)
    files['models/schema.yml'] = yaml_dump({'version': 2, 'models': [
        {'name': stg, 'description': f'{ds.source} için tiplenmiş ve temizlenmiş staging modeli.', 'columns': cols_yml},
        {'name': fct, 'description': 'Raporların okuduğu olgu tablosu.'}] + [{'name': m, 'description': 'Rapor özeti.'} for m in models[2:]]})
    files['models/exposures.yml'] = yaml_dump({'version': 2, 'exposures': [{
        'name': ident(ds.title), 'label': ds.title, 'type': 'dashboard', 'maturity': 'medium', 'owner': {'name': 'Veri Ekibi'},
        'description': 'Bu projenin beslediği rapor.', 'depends_on': [f"ref('{m}')" for m in models[2:]] or [f"ref('{fct}')"]}]})
    if d and p['agg'] == 'sum':
        files['tests/aylik_toplam_tutarli.sql'] = ('-- Aylık özetin toplamı olgu tablosunun toplamına eşit olmalı; fark varsa satır döner ve test kalır.\n'
                                                   f'select a.toplam as aylik, b.toplam as olgu\nfrom (select sum({ident(p["name"])}) as toplam from {ref(monthly)}) a\n'
                                                   f'cross join (select sum({pc}) as toplam from {ref(fct)}) b\n'
                                                   'where abs(coalesce(a.toplam, 0) - coalesce(b.toplam, 0)) > 0.01\n')
        n_tests += 1
    files['macros/metin_tarih.sql'] = (
        '{# YYYY-AA-GG veya GG.AA.YYYY metnini DATE’e çevirir; veritabanına göre doğru fonksiyon seçilir. #}\n'
        "{% macro metin_tarih(sutun) %}{{ return(adapter.dispatch('metin_tarih')(sutun)) }}{% endmacro %}\n\n"
        "{% macro default__metin_tarih(sutun) %}\n    case when {{ sutun }} like '__.__.____' then to_date({{ sutun }}, 'DD.MM.YYYY') else cast({{ sutun }} as date) end\n{% endmacro %}\n\n"
        "{% macro duckdb__metin_tarih(sutun) %}\n    coalesce(try_cast({{ sutun }} as date), cast(try_strptime({{ sutun }}, '%d.%m.%Y') as date))\n{% endmacro %}\n\n"
        "{% macro bigquery__metin_tarih(sutun) %}\n    coalesce(safe_cast({{ sutun }} as date), safe.parse_date('%d.%m.%Y', {{ sutun }}))\n{% endmacro %}\n\n"
        "{% macro snowflake__metin_tarih(sutun) %}\n    coalesce(try_to_date({{ sutun }}, 'YYYY-MM-DD'), try_to_date({{ sutun }}, 'DD.MM.YYYY'))\n{% endmacro %}\n")
    files['dbt_project.yml'] = yaml_dump({'name': name, 'version': '1.0.0', 'profile': name, 'model-paths': ['models'], 'seed-paths': ['seeds'], 'test-paths': ['tests'],
                                          'macro-paths': ['macros'], 'clean-targets': ['target', 'dbt_packages'],
                                          'seeds': {name: {'+delimiter': sep, seed: {'+column_types': {c: 'varchar' for c in columns}}}},
                                          'models': {name: {'staging': {'+materialized': 'view'}, 'marts': {'+materialized': 'table'}}}})
    files['profiles.yml'] = ('# Yerel deneme: pip install dbt-duckdb, sonra bu klasörde: dbt build --profiles-dir .\n'
                             '# Kendi ambarınız için bu bağlantıyı ~/.dbt/profiles.yml’deki hedefinizle değiştirin.\n'
                             + yaml_dump({name: {'target': 'yerel', 'outputs': {'yerel': {'type': 'duckdb', 'path': 'rapor.duckdb', 'threads': 4}}}}))
    files[f'seeds/{seed}.csv'] = seed_text
    files['README.md'] = (f'# {ds.title}: dbt projesi\n\n{ds.source} dosyasından üretildi. Akış: seed `{seed}` → `{stg}` (view; tipleme ve temizlik) → `{fct}` (table) → '
                          + ', '.join(f'`{m}`' for m in models[2:]) + ' (rapor özetleri).\n\n```\npip install dbt-duckdb\ndbt build --profiles-dir .\n```\n\n'
                          f"Kendi ambarınızda seed yerine kaynak tabloyu kullanın: staging modelindeki `ref('{seed}')` ifadesini `source(...)` ile değiştirip sources tanımı ekleyin. "
                          'Boyut ve ölçü testleri uyarı düzeyindedir: kirli değerler build’i durdurmaz ama raporlanır. accepted_values testi dbt 1.10+ “arguments” biçimini kullanır.\n')
    return {'dbt-proje/' + k: v for k, v in files.items()}, len(models), n_tests


@op('dbt-proje-sablonu', title='CSV/Excel’den çalışır dbt projesi: staging, olgu ve rapor modelleri, testler', domain='transform', product='dbt Core', tag='dbt proje',
    samples=SATIS, args=DESIGN_ARGS,
    description='Dosyadan uçtan uca bir dbt projesi kurar: seed, metinleri kırpan ve Türkçe ondalık/tarihleri tipleyen staging modeli, olgu tablosu, aylık ve boyut bazında rapor '
                'modelleri, anahtar/tarih/boyut testleri (unique, not_null, accepted_values), aylık toplamın olgu tablosuyla tutarlılığını denetleyen tekil test, exposure ve '
                'DuckDB/BigQuery/Snowflake/Postgres için tarih makrosu. “dbt build” ile doğrudan çalışır.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='dbt-proje/ (dbt_project.yml, models/, tests/, macros/, seeds/, profiles.yml)',
    command='python main.py --input satislar.csv --dimensions bolge,kanal', expect={'olcu': 3, 'boyut': 3, 'ay': 21, 'model': 6, 'test': 13, 'ana_olcu_toplam': 5912910.0})
def dbt_report(columns, rows, args):
    ds = design(columns, rows, args)
    files, n_models, n_tests = dbt_files(columns, rows, args, ds)
    return Result(f'dbt-proje: {n_models} model ve {n_tests} test', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds), summary(ds, model=n_models, test=n_tests), files=files,
                  notes=['dbt-proje klasöründe: pip install dbt-duckdb, ardından dbt build --profiles-dir . (örnek veriyle tüm modeller ve testler yerelde çalışır). '
                         'Proje dbt-core 1.12 ve DuckDB ile çalıştırılarak test edildi.'] + ds.warnings)


# ---------- Looker (LookML) ----------
def lookml_files(columns, rows, args, ds):
    view = ident(Path(args.input).stem)
    model = args.model
    measure_cols = {m['col'] for m in ds.measures}
    lk = {'int': 'number', 'bigint': 'number', 'decimal': 'number', 'bool': 'yesno', 'text': 'string'}
    lines = [f'# {ds.source} dosyasından üretildi.', f'view: {view} {{', f'  sql_table_name: {args.sql_table or "analytics.fct_" + view} ;;', '']
    for c in columns:
        f = ident(c)
        if ds.kinds[c] in ('date', 'datetime'):
            lines += [f'  dimension_group: {f} {{', '    type: time', '    timeframes: [raw, date, week, month, quarter, year]', '    convert_tz: no',
                      f'    datatype: {"date" if ds.kinds[c] == "date" else "datetime"}', f'    label: "{label(c)}"', f'    sql: ${{TABLE}}.{f} ;;', '  }', '']
            continue
        lines += [f'  dimension: {f} {{'] + (['    primary_key: yes'] if c == ds.key else []) + [f'    type: {lk[ds.kinds[c]]}', f'    label: "{label(c)}"']
        lines += (['    hidden: yes'] if c in measure_cols else []) + [f'    sql: ${{TABLE}}.{f} ;;', '  }', '']
    look = {'sum': 'sum', 'avg': 'average', 'countd': 'count_distinct', 'count': 'count'}
    for m in ds.measures + [ds.count]:
        lines += [f'  measure: {ident(m["name"])} {{', f'    type: {look[m["agg"]]}', f'    label: "{m["name"]}"']
        lines += ([f'    sql: ${{{ident(m["col"])}}} ;;'] if m['agg'] != 'count' else []) + [f'    value_format_name: {"decimal_2" if m["dec"] else "decimal_0"}', '  }', '']
    lines.append('}')
    model_text = (f'connection: "{args.connection}"\n\ninclude: "/views/*.view.lkml"\ninclude: "/dashboards/*.dashboard.lookml"\n\n'
                  f'explore: {view} {{\n  label: "{ds.title}"\n}}\n')
    fq = lambda f: f'{view}.{f}'
    pm = fq(ident(ds.primary['name']))
    base = {'model': model, 'explore': view, 'listen': {label(d): fq(ident(d)) for d in ds.dims}}
    elements, allm = [], ds.measures + [ds.count]
    w = 24 // len(allm)
    for i, m in enumerate(allm):
        elements.append({'name': f'kpi_{i + 1}', 'title': m['name'], 'type': 'single_value', 'fields': [fq(ident(m['name']))], **base,
                         'row': 0, 'col': i * w, 'width': w if i < len(allm) - 1 else 24 - i * w, 'height': 3})
    elements.append({'name': 'kirilim_1', 'title': f'{label(ds.dims[0])} bazında {ds.primary["name"]}', 'type': 'looker_bar', 'fields': [fq(ident(ds.dims[0])), pm],
                     'sorts': [f'{pm} desc'], 'limit': 50, **base, 'row': 3, 'col': 0, 'width': 12 if ds.date else 24, 'height': 8})
    if ds.date:
        tm = fq(ident(ds.date) + '_month')
        elements.append({'name': 'trend', 'title': f'Aylık {ds.primary["name"]}', 'type': 'looker_line', 'fields': [tm, pm], 'fill_fields': [tm], 'sorts': [tm],
                         'limit': 500, **base, 'row': 3, 'col': 12, 'width': 12, 'height': 8})
    elements.append({'name': 'detay', 'title': 'Detay', 'type': 'looker_grid', 'fields': [fq(ident(d)) for d in ds.dims] + [fq(ident(m['name'])) for m in allm],
                     'sorts': [f'{pm} desc'], 'limit': 500, **base, 'row': 11, 'col': 0, 'width': 24, 'height': 8})
    filters = [{'name': label(d), 'title': label(d), 'type': 'field_filter', 'default_value': '', 'allow_multiple_values': True, 'required': False,
                'ui_config': {'type': 'tag_list', 'display': 'popover'}, 'model': model, 'explore': view, 'field': fq(ident(d))} for d in ds.dims]
    dash = [{'dashboard': ident(ds.title), 'title': ds.title, 'layout': 'newspaper', 'preferred_viewer': 'dashboards-next', 'elements': elements, 'filters': filters}]
    return {f'lookml/views/{view}.view.lkml': '\n'.join(lines) + '\n', f'lookml/{model}.model.lkml': model_text,
            f'lookml/dashboards/{ident(ds.title)}.dashboard.lookml': '---\n' + yaml_dump(dash)}, len(elements)


@op('lookml-rapor-sablonu', title='CSV/Excel’den hazır Looker projesi: LookML view, explore ve pano', domain=D, product='Looker', tag='LookML rapor', samples=SATIS,
    args=DESIGN_ARGS + [arg('--connection', default='veri_ambari', help='Looker bağlantı adı'), arg('--model', default='rapor', help='Model dosyası adı'),
                        arg('--sql-table', default='', help='Tablo (boşsa analytics.fct_<dosya>; dbt-proje-sablonu çıktısıyla aynı)')],
    description='Dosyanın sütunlarından LookML view’u (tipli boyutlar, tarih için zaman grubu, toplam/ortalama/farklı sayı ölçüleri, birincil anahtar), explore içeren modeli ve KPI, '
                'çubuk, aylık trend ve detay tablodan oluşan, boyut filtreleri bağlı bir LookML panosunu (newspaper düzeni) üretir. Tablo adı dbt şablonunun olgu tablosuyla eşleşir.',
    input='CSV/XLSX (satır bazında işlem verisi)', output='lookml/ (views/*.view.lkml, rapor.model.lkml, dashboards/*.dashboard.lookml) + rapor-onizleme.html',
    command='python main.py --input satislar.csv --connection snowflake_prod --sql-table "ANALYTICS.FCT_SATIS"', expect={'olcu': 3, 'boyut': 3, 'eleman': 7, 'ana_olcu_toplam': 5912910.0})
def lookml_report(columns, rows, args):
    ds = design(columns, rows, args)
    files, n = lookml_files(columns, rows, args, ds)
    files['rapor-onizleme.html'] = preview_html(ds, 'Looker')
    return Result(f'LookML: 1 view, 1 explore ve {n} elemanlı pano', ['oge', 'ad', 'tanim', 'deger'], plan_rows(ds), summary(ds, eleman=n), files=files,
                  notes=['Dosyaları Looker projesine (Geliştirme modunda) kopyalayın, bağlantı adını ve sql_table_name’i kontrol edip “Validate LookML” çalıştırın.',
                         'View ve model dosyaları lkml ayrıştırıcısından, pano YAML’ı alan referans kontrolünden geçirildi; Looker örneğinde açılarak test edilmedi.'] + ds.warnings)
