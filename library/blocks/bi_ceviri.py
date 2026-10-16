"""BI ve dbt analiz/çeviri blokları: dbt çalışma sonuçları ve DAG analizi, LookML envanteri, Power BI ölçü bağımlılığı,
Tableau ve Qlik ifadelerinden DAX taslağı. Çeviriler taslaktır; desteklenmeyen kalıplar açıkça işaretlenir."""
import json, re
from collections import Counter, defaultdict, deque
from pathlib import Path
from pbit_model import read_schema, normalize
from tableau_envanter import read_twb, inventory
from islem import op, arg, Result, fmt

REQUIRES = ['pbit_model', 'tableau_envanter']


def load_json(path, label):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'{label} okunamadı: {exc}') from None


@op('dbt-calisma-sonuclari', title='dbt run_results.json: hatalar ve en yavaş modeller', domain='transform', product='dbt Core', tag='run_results', kind='file',
    samples={'run_results.json': 'run_results.json'}, args=[arg('--top', type=int, default=10)],
    description='dbt run/build/test sonrası oluşan run_results.json’dan hata veren model ve testleri mesajlarıyla, atlanan düğümleri, en uzun süren modelleri ve toplam süreyi çıkarır. Gece çalışması sonrası özet ve CI raporu için; --strict gerekmez, hata varsa 2 koduyla çıkar.',
    input='target/run_results.json', output='sonuc.csv (düğüm sonuçları) + rapor', command='python main.py --input target/run_results.json',
    expect={'dugum': 8, 'hata': 2, 'atlanan': 1}, exit_code=2)
def run_results(args):
    data = load_json(args.input, 'run_results.json')
    rows = []
    for r in data.get('results', []):
        uid = r.get('unique_id', '')
        rows.append({'tur': uid.split('.')[0], 'dugum': uid.split('.')[-1], 'durum': r.get('status', ''), 'sure_sn': round(r.get('execution_time') or 0, 2),
                     'ihlal': r.get('failures') or '', 'mesaj': (r.get('message') or '')[:200] if r.get('status') in ('error', 'fail', 'warn') else ''})
    if not rows:
        raise ValueError('run_results.json içinde sonuç yok.')
    bad = [r for r in rows if r['durum'] in ('error', 'fail', 'runtime error')]
    skipped = [r for r in rows if r['durum'] == 'skipped']
    rows.sort(key=lambda r: (r['durum'] not in ('error', 'fail'), -r['sure_sn']))
    slow = sorted((r for r in rows if r['tur'] == 'model'), key=lambda r: -r['sure_sn'])[:args.top]
    total = data.get('elapsed_time') or sum(r['sure_sn'] for r in rows)
    return Result(f'{len(rows)} düğüm; {len(bad)} hata/başarısız test, {len(skipped)} atlandı; toplam {total:.0f} sn', list(rows[0]), rows,
                  {'dugum': len(rows), 'hata': len(bad), 'atlanan': len(skipped), 'toplam_sn': round(total, 1), 'dbt_surumu': data.get('metadata', {}).get('dbt_version', '')},
                  tables=[('En yavaş modeller', ['Model', 'Süre (sn)', 'Durum'], [[r['dugum'], r['sure_sn'], r['durum']] for r in slow])], code=2 if bad else 0)


@op('dbt-dag-analizi', title='dbt model bağımlılık grafiği (DAG) analizi', domain='transform', product='dbt Core', tag='DAG', kind='file', samples={'manifest.json': 'manifest.json'},
    description='manifest.json’dan modellerin derinliğini, alt/üst bağımlılık sayısını, hiçbir yerde kullanılmayan (son kullanıcısı/exposure’ı olmayan) modelleri, kaynağa doğrudan bağlı mart modellerini ve en uzun bağımlılık zincirini çıkarır; Mermaid şeması üretir.',
    input='target/manifest.json', output='sonuc.csv (model bazında) + dag.md (Mermaid)', command='python main.py --input target/manifest.json',
    expect={'model': 7, 'kaynak': 7, 'max_derinlik': 3, 'kullanilmayan': 1})
def dag(args):
    m = load_json(args.input, 'manifest.json')
    models = {k: v for k, v in m.get('nodes', {}).items() if k.startswith('model.')}
    sources = {k for k in m.get('sources', {})} | {d for v in models.values() for d in v.get('depends_on', {}).get('nodes', []) if d.startswith('source.')}
    children = defaultdict(set)
    for k, v in models.items():
        for d in v.get('depends_on', {}).get('nodes', []):
            children[d].add(k)
    exposed = {d for e in m.get('exposures', {}).values() for d in e.get('depends_on', {}).get('nodes', [])}
    depth = {}

    def dep(k, seen=()):
        if k in depth:
            return depth[k]
        if k not in models or k in seen:
            return 0
        ups = [d for d in models[k].get('depends_on', {}).get('nodes', []) if d in models]
        depth[k] = 1 + max((dep(u, seen + (k,)) for u in ups), default=0)
        return depth[k]
    out = []
    for k, v in sorted(models.items()):
        ups = v.get('depends_on', {}).get('nodes', [])
        name = k.split('.')[-1]
        down = sorted(children.get(k, ()))
        flags = []
        if not down and k not in exposed:
            flags.append('kullanılmıyor (alt model veya exposure yok)')
        if name.startswith(('fct_', 'dim_', 'mart_')) and any(u.startswith('source.') for u in ups):
            flags.append('mart katmanı kaynağı doğrudan okuyor')
        out.append({'model': name, 'derinlik': dep(k), 'ust': len(ups), 'alt': len(down), 'exposure': 'evet' if k in exposed else '',
                    'materialized': v.get('config', {}).get('materialized', ''), 'uyari': '; '.join(flags)})
    edges = [(d.split('.')[-1], k.split('.')[-1]) for k, v in models.items() for d in v.get('depends_on', {}).get('nodes', [])]
    mermaid = '```mermaid\nflowchart LR\n' + '\n'.join(f'  {a} --> {b}' for a, b in edges) + '\n```'
    deepest = max(out, key=lambda o: o['derinlik'])
    return Result(f"{len(out)} model, {len(sources)} kaynak; en derin {deepest['model']} ({deepest['derinlik']}); {sum('kullanılmıyor' in o['uyari'] for o in out)} kullanılmayan model",
                  list(out[0]), out, {'model': len(out), 'kaynak': len(sources), 'max_derinlik': deepest['derinlik'], 'kullanilmayan': sum('kullanılmıyor' in o['uyari'] for o in out)},
                  files={'dag.md': mermaid})


def lookml_blocks(text):
    """Yield (kind, name, body) for dimension/dimension_group/measure blocks with balanced braces."""
    for m in re.finditer(r'\b(dimension_group|dimension|measure|view)\s*:\s*([\w]+)\s*\{', text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
        yield m.group(1), m.group(2), text[m.end():i - 1]


@op('lookml-envanter', title='LookML view envanteri ve kalite kontrolü', domain='bi', product='Looker', tag='LookML', kind='file',
    samples={'lookml/siparisler.view.lkml': 'lookml/siparisler.view.lkml', 'lookml/musteriler.view.lkml': 'lookml/musteriler.view.lkml'}, sample_input='lookml',
    description='.view.lkml dosyalarından view, tablo (sql_table_name), boyut ve ölçüleri listeler; birincil anahtarı olmayan view’ları, açıklamasız alanları, değer biçimi tanımsız sayısal ölçüleri ve tanımsız ${alan} başvurularını işaretler. Looker’dan geçiş ve dokümantasyon için.',
    input='.lkml dosyası veya klasörü', output='sonuc.csv (alanlar) + bulgular', command='python main.py --input looker-proje/views',
    expect={'view': 2, 'alan': 11, 'bulgu': 12})
def lookml(args):
    p = Path(args.input)
    files = sorted(p.rglob('*.lkml')) if p.is_dir() else [p]
    rows, findings = [], []
    for f in files:
        text = re.sub(r'#[^\n]*', '', f.read_text(encoding='utf-8-sig', errors='replace'))
        for kind, view, body in lookml_blocks(text):
            if kind != 'view':
                continue
            table = re.search(r'sql_table_name\s*:\s*([^;]+);;', body)
            fields = [(k, n, b) for k, n, b in lookml_blocks(body) if k != 'view']
            names = {n for _, n, _ in fields}
            if not any(re.search(r'primary_key\s*:\s*yes', b) for _, _, b in fields):
                findings.append([view, '', 'orta', 'primary_key tanımlı değil — join’lerde sayım/toplam şişebilir (symmetric aggregates çalışmaz)'])
            for k, n, b in fields:
                typ = re.search(r'\btype\s*:\s*(\w+)', b)
                sql = re.search(r'\bsql\s*:\s*(.*?);;', b, re.S)
                desc = re.search(r'description\s*:\s*"([^"]*)"', b)
                rows.append({'view': view, 'tablo': table.group(1).strip() if table else '', 'tur': k, 'alan': n, 'tip': typ.group(1) if typ else '',
                             'sql': re.sub(r'\s+', ' ', sql.group(1)).strip() if sql else '', 'aciklama': desc.group(1) if desc else ''})
                if not desc:
                    findings.append([view, n, 'düşük', 'açıklama yok'])
                if k == 'measure' and typ and typ.group(1) in ('sum', 'average', 'number') and 'value_format' not in b:
                    findings.append([view, n, 'düşük', 'sayısal ölçüde value_format yok'])
                for ref in re.findall(r'\$\{(\w+)\}', sql.group(1) if sql else ''):
                    if ref != 'TABLE' and ref not in names:
                        findings.append([view, n, 'yüksek', f'tanımsız alan başvurusu: ${{{ref}}}'])
    views = len({r['view'] for r in rows})
    return Result(f'{views} view, {len(rows)} alan; {len(findings)} bulgu', ['view', 'tablo', 'tur', 'alan', 'tip', 'sql', 'aciklama'], rows,
                  {'view': views, 'alan': len(rows), 'bulgu': len(findings)}, tables=[('Bulgular', ['View', 'Alan', 'Önem', 'Bulgu'], findings)])


@op('pbi-olcu-bagimliligi', title='Power BI ölçü bağımlılık ağacı', domain='bi', product='Power BI Desktop', tag='ölçü bağımlılığı', kind='file',
    samples={'satis-modeli.pbit': 'satis-modeli.pbit'},
    description='.pbit modelindeki DAX ölçülerinin birbirine başvurularını çıkarır: her ölçünün kullandığı ölçüler ve sütunlar, ölçü derinliği, başka hiçbir ölçüde kullanılmayan ölçüler ve modelde olmayan ölçülere yapılan başvurular. Değişiklik etkisi analizi ve geçiş sırası için; Mermaid şeması üretir.',
    input='.pbit veya model.bim', output='sonuc.csv (ölçü bazında) + bagimlilik.md', command='python main.py --input rapor.pbit',
    expect={'olcu': 6, 'kayip_baglanti': 1, 'max_derinlik': 2})
def pbi_deps(args):
    schema, layout = read_schema(args.input)
    model = normalize(schema, layout)
    measures = {m['name']: m for t in model['tables'] for m in t['measures']}
    uses, used_by = {}, defaultdict(set)
    for name, m in measures.items():
        expr = re.sub(r'"(?:""|[^"])*"', '""', m['expression'])
        refs = set(re.findall(r'(?<![\w\'\]])\[([^\]]+)\]', expr))
        cols = set(re.findall(r"('[^']+'|\b\w+)\[([^\]]+)\]", expr))
        uses[name] = (refs, cols)
        for r in refs:
            used_by[r].add(name)
    depth = {}

    def d(n, seen=()):
        if n in depth:
            return depth[n]
        if n not in measures or n in seen:
            return 0
        depth[n] = 1 + max((d(r, seen + (n,)) for r in uses[n][0] if r in measures), default=0)
        return depth[n]
    out = []
    for name, m in sorted(measures.items()):
        refs, cols = uses[name]
        missing = sorted(r for r in refs if r not in measures)
        out.append({'olcu': name, 'tablo': m['table'], 'derinlik': d(name), 'kullandigi_olculer': ', '.join(sorted(r for r in refs if r in measures)),
                    'kullandigi_sutunlar': ', '.join(sorted({f'{t.strip(chr(39))}[{c}]' for t, c in cols})), 'kullanan_olculer': ', '.join(sorted(used_by.get(name, ()))),
                    'eksik_baslanti': ', '.join(missing)})
    edges = [f'  m{list(measures).index(r)}["{r}"] --> m{list(measures).index(n)}["{n}"]' for n in measures for r in uses[n][0] if r in measures]
    return Result(f"{len(out)} ölçü; derinlik en çok {max(o['derinlik'] for o in out)}; {sum(bool(o['eksik_baslanti']) for o in out)} ölçüde modelde olmayan başvuru", list(out[0]), out,
                  {'olcu': len(out), 'kayip_baglanti': sum(bool(o['eksik_baslanti']) for o in out), 'max_derinlik': max(o['derinlik'] for o in out),
                   'baska_olcude_kullanilmayan': sum(not o['kullanan_olculer'] for o in out)},
                  files={'bagimlilik.md': '```mermaid\nflowchart LR\n' + '\n'.join(edges) + '\n```'},
                  notes=['Görsellerde kullanım .pbit düzeninden çıkarılamadığı için “kullanılmayan” yalnızca diğer ölçüler açısından değerlendirilir.'])


TABLEAU_FUNCS = {'SUM': 'SUM', 'AVG': 'AVERAGE', 'MIN': 'MIN', 'MAX': 'MAX', 'COUNT': 'COUNT', 'COUNTD': 'DISTINCTCOUNT', 'ZN': 'ZN', 'ABS': 'ABS', 'ROUND': 'ROUND',
                 'UPPER': 'UPPER', 'LOWER': 'LOWER', 'LEN': 'LEN', 'LEFT': 'LEFT', 'RIGHT': 'RIGHT', 'TODAY': 'TODAY', 'YEAR': 'YEAR', 'MONTH': 'MONTH', 'DAY': 'DAY',
                 'IFNULL': 'COALESCE', 'ISNULL': 'ISBLANK', 'CONTAINS': 'CONTAINSSTRING'}


def tableau_to_dax(formula, table, known):
    """Return (dax, status, note). Handles aggregates, IF/IIF/ZN, FIXED LOD; marks table calcs as manual."""
    f = formula.strip()
    if re.search(r'\{\s*(INCLUDE|EXCLUDE)\b', f, re.I) or re.search(r'\b(WINDOW_\w+|RUNNING_\w+|LOOKUP|INDEX|RANK\w*|FIRST|LAST|SIZE|TOTAL)\s*\(', f, re.I):
        return '', 'elle', 'INCLUDE/EXCLUDE LOD veya tablo hesaplaması: görsel bağlamına göre DAX’ta yeniden tasarlanmalı'
    note = []
    m = re.fullmatch(r'\{\s*FIXED\s+(.+?)\s*:\s*(\w+)\(\s*\[([^\]]+)\]\s*\)\s*\}', f, re.I | re.S)
    if m:
        dims = [d.strip().strip('[]') for d in m.group(1).split(',')]
        agg = TABLEAU_FUNCS.get(m.group(2).upper(), m.group(2).upper())
        dax = f"CALCULATE ( {agg} ( '{table}'[{m.group(3)}] ), ALLEXCEPT ( '{table}', " + ', '.join(f"'{table}'[{d}]" for d in dims) + ' ) )'
        return dax, 'çevrildi', 'FIXED LOD → CALCULATE + ALLEXCEPT (filtre bağlamı davranışı farklı olabilir; boyut filtrelerini test edin)'
    if '[Parameters]' in f:
        f = f.replace('[Parameters].', '')
        note.append('parametre DAX’ta “What-if” parametresi veya ayrı tablo olarak oluşturulmalı')
    f = re.sub(r'\bIIF\s*\(', 'IF(', f, flags=re.I)
    f = re.sub(r'\bIF\s+(.+?)\s+THEN\s+(.+?)\s+ELSE\s+(.+?)\s+END\b', r'IF(\1, \2, \3)', f, flags=re.I | re.S)
    f = re.sub(r'\bZN\s*\(([^()]*)\)', r'COALESCE(\1, 0)', f, flags=re.I)
    if re.search(r'\bELSEIF\b|\bCASE\b', f, re.I):
        return '', 'elle', 'ELSEIF/CASE yapısı SWITCH ( TRUE (), … ) olarak elle çevrilmeli'

    def func(m_):
        name = m_.group(1).upper()
        return (TABLEAU_FUNCS.get(name, name)) + '('
    f = re.sub(r'\b([A-Z_]+)\s*\(', func, f, flags=re.I)

    def ref(m_):
        name = m_.group(1)
        return f'[{name}]' if name in known else f"'{table}'[{name}]"
    f = re.sub(r'\[([^\]]+)\]', ref, f)
    agg_div = re.fullmatch(r'(.+?)\s*/\s*(.+)', f)
    if agg_div and '(' in agg_div.group(2):
        f = f'DIVIDE ( {agg_div.group(1).strip()}, {agg_div.group(2).strip()} )'
        note.append('bölme DIVIDE ile sıfıra bölmeye karşı korundu')
    return f, 'çevrildi', '; '.join(note)


@op('tableau-dax-ceviri', title='Tableau hesaplanan alanlarını DAX taslağına çevir', domain='bi', product='Tableau Desktop', tag='Tableau→DAX', kind='file',
    samples={'satis-panosu.twbx': 'satis-panosu.twbx'}, args=[arg('--table', default='Satislar', help='Power BI’daki olgu tablosu adı')],
    description='Tableau çalışma kitabındaki hesaplanan alanları DAX ölçü taslağına çevirir: toplama fonksiyonları (COUNTD→DISTINCTCOUNT, AVG→AVERAGE), IF/IIF/ZN, alanlar arası başvurular, bölmede DIVIDE ve FIXED LOD → CALCULATE+ALLEXCEPT. INCLUDE/EXCLUDE, tablo hesaplamaları ve CASE yapılarını “elle” olarak işaretler.',
    input='.twb / .twbx', output='sonuc.csv (alan → DAX) + olculer.dax', command='python main.py --input pano.twbx --table Satislar',
    expect={'alan': 4, 'cevrilen': 4, 'elle': 0})
def tableau_dax(args):
    root, packaged = read_twb(args.input)
    inv = inventory(root, packaged)
    known = {c['name'] for c in inv['calculations']}
    out = []
    for c in inv['calculations']:
        dax, status, note = tableau_to_dax(c['formula'], args.table, known)
        out.append({'alan': c['name'], 'tableau': c['formula'], 'dax': dax, 'durum': status, 'not': note})
    text = 'DEFINE\n' + '\n'.join(f"    MEASURE '{args.table}'[{o['alan']}] =\n        {o['dax']}" for o in out if o['dax']) + '\nEVALUATE ROW ( "kontrol", 1 )'
    return Result(f"{len(out)} alan; {sum(o['durum'] == 'çevrildi' for o in out)} çevrildi, {sum(o['durum'] == 'elle' for o in out)} elle", list(out[0]), out,
                  {'alan': len(out), 'cevrilen': sum(o['durum'] == 'çevrildi' for o in out), 'elle': sum(o['durum'] == 'elle' for o in out)}, files={'olculer.dax': text},
                  notes=['Çeviri sözdizimsel bir taslaktır; DAX filtre bağlamı Tableau’nun görünüm düzeyinden farklıdır. Her ölçüyü bilinen bir rakamla doğrulayın.'])


QLIK_AGG = {'sum': 'SUM', 'avg': 'AVERAGE', 'min': 'MIN', 'max': 'MAX', 'count': 'COUNT'}


def set_filters(spec, table):
    filters = []
    for part in re.findall(r'(\w+)\s*(-?=)\s*\{([^}]*)\}', spec):
        field, op_, vals = part
        values = [v.strip().strip("'\"") for v in vals.split(',') if v.strip()]
        quote = lambda v: v if re.fullmatch(r'-?\d+(\.\d+)?', v) else f'"{v}"'
        if op_ == '=':
            filters.append(f"'{table}'[{field}] = {quote(values[0])}" if len(values) == 1 else f"'{table}'[{field}] IN {{ {', '.join(quote(v) for v in values)} }}")
        else:
            filters.append(f"NOT ( '{table}'[{field}] IN {{ {', '.join(quote(v) for v in values)} }} )")
    return filters


def qlik_to_dax(expr, table):
    e = expr.strip()
    if re.search(r'\b(Aggr|Above|Below|RangeSum|RangeAvg|Before|After|RowNo|Column)\s*\(', e, re.I):
        return '', 'elle', 'Aggr/Above/RangeSum gibi grafik iç fonksiyonları DAX’ta SUMMARIZE/WINDOW veya görsel hesaplamayla yeniden tasarlanmalı'

    def balanced(text, start, open_, close):
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == open_) - (text[i] == close)
            if depth == 0:
                return i
        raise ValueError('dengesiz parantez')

    def translate(call_name, inner):
        fn = call_name.lower()
        inner = inner.strip()
        distinct = re.match(r'DISTINCT\s+', inner, re.I)
        if distinct:
            inner = inner[distinct.end():]
        setexpr = ''
        if inner.startswith('{'):
            end = balanced(inner, 0, '{', '}')
            setexpr, inner = inner[1:end].strip(), inner[end + 1:].strip()
        if not re.fullmatch(r'\[?[\w ]+\]?', inner):
            raise ValueError('alan ifadesi')
        col = f"'{table}'[{inner.strip('[]').strip()}]"
        base = f"{'DISTINCTCOUNT' if fn == 'count' and distinct else QLIK_AGG[fn]} ( {col} )"
        if not setexpr:
            return base
        if setexpr == '1':
            return f"CALCULATE ( {base}, ALL ( '{table}' ) )"
        body = re.fullmatch(r'\$?\s*<(.*)>', setexpr, re.S)
        filters = set_filters(body.group(1), table) if body else []
        if not filters:
            raise ValueError('set analizi')
        return f'CALCULATE ( {base}, ' + ', '.join(filters) + ' )'
    out, i = '', 0
    try:
        for m in re.finditer(r'\b(Sum|Avg|Min|Max|Count)\s*\(', e, re.I):
            if m.start() < i:
                continue
            end = balanced(e, m.end() - 1, '(', ')')
            out += e[i:m.start()] + translate(m.group(1), e[m.end():end])
            i = end + 1
        out += e[i:]
    except ValueError:
        return '', 'elle', 'ifade otomatik çevrilemedi (iç içe ifade veya desteklenmeyen set analizi)'
    if not out.strip() or out == e:
        return '', 'elle', 'toplama fonksiyonu bulunamadı'
    m = re.fullmatch(r'(.+?)\s*/\s*(.+)', out)
    if m:
        out = f'DIVIDE ( {m.group(1).strip()}, {m.group(2).strip()} )'
    return out, 'çevrildi', ''


@op('qlik-dax-ceviri', title='Qlik grafik ifadelerini DAX taslağına çevir', domain='bi', product='Qlik Sense Enterprise', tag='Qlik→DAX', kind='file',
    samples={'qlik_ifadeleri.txt': 'qlik_ifadeleri.txt'}, args=[arg('--table', default='Satislar')],
    description='Qlik grafik ifadelerini (her satıra bir ifade) DAX ölçü taslağına çevirir: Sum/Avg/Count(DISTINCT) toplamaları, set analizi {<Alan={…}>} ve hariç tutma (-=) filtreleri → CALCULATE, {1} → ALL, bölme → DIVIDE. Aggr, Above, RangeSum gibi grafik iç fonksiyonlarını “elle” olarak işaretler.',
    input='İfade listesi (.txt, her satıra bir ifade)', output='sonuc.csv (ifade → DAX)', command='python main.py --input ifadeler.txt --table Satislar',
    expect={'ifade': 9, 'cevrilen': 7, 'elle': 2})
def qlik_dax(args):
    lines = [l.strip() for l in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines() if l.strip() and not l.strip().startswith('//')]
    if not lines:
        raise ValueError('Dosyada ifade yok.')
    out = []
    for i, l in enumerate(lines, start=1):
        dax, status, note = qlik_to_dax(l, args.table)
        out.append({'no': i, 'qlik': l, 'dax': dax, 'durum': status, 'not': note})
    return Result(f"{len(out)} ifade; {sum(o['durum'] == 'çevrildi' for o in out)} çevrildi, {sum(o['durum'] == 'elle' for o in out)} elle", list(out[0]), out,
                  {'ifade': len(out), 'cevrilen': sum(o['durum'] == 'çevrildi' for o in out), 'elle': sum(o['durum'] == 'elle' for o in out)},
                  notes=['Set analizi seçim durumuna göre çalışır; DAX’ta CALCULATE filtreleri mevcut filtre bağlamını değiştirir. Sonuçları rapor üzerinde karşılaştırın.'])
