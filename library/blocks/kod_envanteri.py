"""Analitik kod envanteri blokları: SAS programları ve Alteryx iş akışları için geçiş (Python / SQL / dbt) değerlendirmesi.
Dosyalar çalıştırılmaz; metin ve XML olarak okunur. Kimlik bilgileri çıktıya yazılmadan maskelenir."""
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from islem import op, arg, Result, fmt

PROC_MAP = {
    'sql': ('SQL / pandas', 2), 'sort': ('sort_values', 1), 'means': ('groupby().agg', 1), 'summary': ('groupby().agg', 1), 'freq': ('value_counts / crosstab', 1),
    'print': ('rapor çıktısı', 1), 'contents': ('df.info()', 1), 'import': ('read_csv / read_excel', 1), 'export': ('to_csv / to_excel', 1), 'append': ('concat', 1),
    'transpose': ('pivot / melt', 2), 'format': ('eşleme sözlüğü (map)', 2), 'datasets': ('dosya işlemleri', 1), 'univariate': ('describe / scipy.stats', 2),
    'corr': ('df.corr()', 1), 'rank': ('rank()', 1), 'tabulate': ('pivot_table', 2), 'report': ('pivot_table + rapor', 3),
    'reg': ('statsmodels OLS', 3), 'logistic': ('statsmodels Logit / scikit-learn', 3), 'glm': ('statsmodels GLM', 3), 'mixed': ('statsmodels MixedLM', 4),
    'genmod': ('statsmodels GLM', 3), 'arima': ('statsmodels ARIMA', 4), 'forecast': ('statsmodels / prophet', 4), 'ttest': ('scipy.stats.ttest_ind', 2),
    'npar1way': ('scipy.stats (Mann-Whitney / Kruskal)', 2), 'surveyselect': ('sample()', 2), 'optmodel': ('Pyomo / OR-Tools (manuel)', 5), 'iml': ('NumPy (manuel)', 5),
    'hpforest': ('scikit-learn RandomForest', 4), 'phreg': ('lifelines CoxPH', 4), 'lifetest': ('lifelines KaplanMeier', 3)}


def sas_strip(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'(?m)^\s*\*[^;]*;', ' ', text)


@op('sas-kod-envanteri', title='SAS kod envanteri ve Python’a geçiş karmaşıklığı', domain='science', product='Viya', tag='SAS', kind='file',
    samples={'sas_proje/musteri_skor.sas': 'sas_proje/musteri_skor.sas', 'sas_proje/aylik_rapor.sas': 'sas_proje/aylik_rapor.sas',
             'sas_proje/makrolar.sas': 'sas_proje/makrolar.sas'}, sample_input='sas_proje',
    args=[arg('--simple', type=int, default=10, help='Bu puana kadar basit'), arg('--medium', type=int, default=20, help='Bu puana kadar orta'),
          arg('--days', default='1,3,8', help='Basit, orta, karmaşık program başına efor (gün)')],
    description='Klasördeki .sas programlarını çalıştırmadan okur: DATA adımları, PROC kullanımı (her PROC için Python karşılığı), tanımlanan ve çağrılan makrolar, %include, LIBNAME’ler (veritabanı motoru ve sabit dosya yolları), okunan ve yazılan veri setleri, CALL EXECUTE / SYMPUT ile dinamik kod ve X / CALL SYSTEM ile işletim sistemi komutları. Her programı puanlar, basit/orta/karmaşık sınıfı ve gün olarak geçiş eforu verir; doğrudan karşılığı zayıf PROC’ları (OPTMODEL, IML) ayrıca işaretler.',
    input='.sas dosyası veya klasörü', output='sonuc.csv (program bazında envanter) + PROC tablosu', command='python main.py --input C:\\SAS\\projeler',
    expect={'program': 3, 'proc': 8, 'makro': 2, 'karmasik': 1, 'efor_gun': 10})
def sas_inventory(args):
    p = Path(args.input)
    files = sorted({f.resolve() for f in p.rglob('*.sas')}) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'.sas dosyası bulunamadı: {p}')
    days = [float(x) for x in args.days.split(',')]
    out, procs_all, defined, called = [], Counter(), set(), Counter()
    for f in files:
        text = sas_strip(f.read_text(encoding='utf-8', errors='replace'))
        procs = Counter(m.lower() for m in re.findall(r'(?im)\bproc\s+(\w+)', text))
        data_steps = len(re.findall(r'(?im)^\s*data\s+(?!_null_\b)[\w.&]+', text)) + len(re.findall(r'(?im)^\s*data\s+_null_', text))
        macros = re.findall(r'(?im)%macro\s+(\w+)', text)
        defined.update(m.lower() for m in macros)
        calls = [m.lower() for m in re.findall(r'%(\w+)\s*(?:\(|;)', text)
                 if m.lower() not in ('macro', 'mend', 'let', 'put', 'if', 'then', 'else', 'do', 'end', 'include', 'global', 'local', 'sysfunc', 'eval', 'str', 'nrstr', 'upcase', 'scan', 'substr', 'sysevalf', 'symexist', 'return', 'abort')]
        called.update(calls)
        libs = re.findall(r'(?im)\blibname\s+(\w+)\s+(?:(\w+)\s+)?([\'"][^\'"]*[\'"])?', text)
        engines = sorted({e.lower() for _, e, _ in libs if e and e.lower() not in ('base', 'v9')})
        paths = sorted({q.strip('\'"') for q in re.findall(r'[\'"]([A-Za-z]:\\[^\'"]+|/(?:data|home|sasdata|opt|mnt)/[^\'"]+)[\'"]', text)})
        dynamic = len(re.findall(r'(?i)\bcall\s+(execute|symputx?)\b', text))
        oscmd = len(re.findall(r'(?im)^\s*x\s+[\'"]|\bcall\s+system\b|%sysexec', text))
        includes = re.findall(r'(?i)%include\s+[\'"]([^\'"]+)', text)
        reads = sorted({x.lower() for x in re.findall(r'(?i)\b(?:set|merge)\s+([A-Za-z_]\w*\.\w+)', text)} | {x.lower() for x in re.findall(r'(?i)\bfrom\s+([A-Za-z_]\w*\.\w+)', text)})
        writes = sorted({x.lower() for x in re.findall(r'(?im)^\s*data\s+([A-Za-z_]\w*\.\w+)', text)} | {x.lower() for x in re.findall(r'(?i)\bcreate\s+table\s+([A-Za-z_]\w*\.\w+)', text)})
        weak = sorted(pr for pr in procs if PROC_MAP.get(pr, ('', 5))[1] >= 5)
        score = 2 * data_steps + sum(PROC_MAP.get(pr, ('', 5))[1] * n for pr, n in procs.items()) + 3 * len(macros) + len(calls) + 3 * dynamic + 3 * oscmd + 2 * len(engines) + len(includes)
        cls = 'basit' if score <= args.simple else 'orta' if score <= args.medium else 'karmaşık'
        procs_all.update(procs)
        notes = []
        if dynamic:
            notes.append(f'{dynamic} CALL EXECUTE/SYMPUT: çalışma anında üretilen kod; davranışı veriyle test edin')
        if oscmd:
            notes.append(f'{oscmd} işletim sistemi komutu (X / CALL SYSTEM)')
        if paths:
            notes.append('sabit dosya yolu: ' + ', '.join(paths[:3]))
        if weak:
            notes.append('Python’da doğrudan karşılığı zayıf: PROC ' + ', '.join(w.upper() for w in weak))
        out.append({'program': f.name, 'puan': score, 'sinif': cls, 'data_adimi': data_steps, 'proc': sum(procs.values()), 'proc_turleri': ', '.join(f'{k.upper()} {v}' for k, v in procs.most_common()),
                    'makro_tanimi': len(macros), 'makro_cagrisi': len(calls), 'libname_motoru': ', '.join(engines), 'okunan': ', '.join(reads), 'yazilan': ', '.join(writes),
                    'efor_gun': days[('basit', 'orta', 'karmaşık').index(cls)], 'dikkat': '; '.join(notes)})
    out.sort(key=lambda o: -o['puan'])
    undefined = sorted(set(called) - defined)
    cls = Counter(o['sinif'] for o in out)
    effort = sum(o['efor_gun'] for o in out)
    table = [[k.upper(), n, PROC_MAP.get(k, ('karşılığı tanımlı değil (manuel)', 5))[0]] for k, n in procs_all.most_common()]
    notes = ['Puan: DATA adımı 2, PROC ağırlığı (1–5), makro tanımı 3, makro çağrısı 1, dinamik kod 3, OS komutu 3, veritabanı LIBNAME’i 2. Sonuçları SAS çıktısıyla sayısal toleransla karşılaştırarak doğrulayın.']
    if undefined:
        notes.append('Tanımı klasörde bulunmayan makro çağrısı (otomatik çağrı makrosu veya başka kütüphane): ' + ', '.join(undefined))
    return Result(f'{len(out)} program, {sum(procs_all.values())} PROC, {len(defined)} makro; basit {cls["basit"]}, orta {cls["orta"]}, karmaşık {cls["karmaşık"]}; tahmini efor {fmt(effort)} gün',
                  list(out[0]), out,
                  {'program': len(out), 'proc': sum(procs_all.values()), 'makro': len(defined), 'karmasik': cls['karmaşık'], 'efor_gun': effort},
                  tables=[('PROC kullanımı ve Python karşılığı', ['PROC', 'Adet', 'Python karşılığı'], table)], notes=notes)


AX_TOOLS = {
    'DbFileInput': ('girdi', 1, 'source()'), 'DbFileOutput': ('çıktı', 1, 'model materialization'), 'TextInput': ('girdi', 1, 'seed'),
    'Filter': ('dönüşüm', 1, 'WHERE'), 'Formula': ('dönüşüm', 1, 'SELECT ifadesi'), 'Join': ('dönüşüm', 2, 'JOIN'), 'JoinMultiple': ('dönüşüm', 2, 'JOIN'),
    'Summarize': ('dönüşüm', 1, 'GROUP BY'), 'Sort': ('dönüşüm', 1, 'ORDER BY'), 'AlteryxSelect': ('dönüşüm', 1, 'SELECT / yeniden adlandırma'),
    'Union': ('dönüşüm', 1, 'UNION ALL'), 'Unique': ('dönüşüm', 1, 'ROW_NUMBER ile tekilleştirme'), 'CrossTab': ('dönüşüm', 2, 'PIVOT'),
    'Transpose': ('dönüşüm', 2, 'UNPIVOT'), 'MultiRowFormula': ('dönüşüm', 2, 'LAG/LEAD pencere fonksiyonu'), 'RegEx': ('dönüşüm', 2, 'REGEXP fonksiyonları'),
    'TextToColumns': ('dönüşüm', 1, 'SPLIT_PART'), 'FindReplace': ('dönüşüm', 2, 'JOIN + REPLACE'), 'Sample': ('dönüşüm', 1, 'LIMIT / QUALIFY'),
    'DateTime': ('dönüşüm', 1, 'tarih fonksiyonları'), 'RecordID': ('dönüşüm', 1, 'ROW_NUMBER'), 'AppendFields': ('dönüşüm', 2, 'CROSS JOIN'),
    'BrowseV2': ('görüntüleme', 0, '— (kaldırılır)'), 'Browse': ('görüntüleme', 0, '— (kaldırılır)'), 'ToolContainer': ('görüntüleme', 0, '—'), 'Comment': ('görüntüleme', 0, '—'),
    'JupyterCode': ('kod', 5, 'Python modeli (manuel)'), 'R': ('kod', 5, 'Python/R (manuel)'), 'RunCommand': ('kod', 4, 'orkestratör adımı (manuel)'),
    'DownloadTool': ('kod', 4, 'Python / ingestion aracı (manuel)'),
}


def ax_plugin(node):
    gui = node.find('GuiSettings')
    plugin = gui.get('Plugin', '') if gui is not None else ''
    eng = node.find('EngineSettings')
    macro = eng.get('Macro', '') if eng is not None else ''
    return plugin.split('.')[-1] if plugin else '', macro


@op('alteryx-envanter', title='Alteryx iş akışı envanteri ve dbt/SQL’e geçiş karmaşıklığı', domain='science', product='Designer', tag='Alteryx', kind='file',
    samples={'alteryx_akislar/aylik_satis.yxmd': 'alteryx_akislar/aylik_satis.yxmd', 'alteryx_akislar/churn_skor.yxmd': 'alteryx_akislar/churn_skor.yxmd'},
    sample_input='alteryx_akislar',
    args=[arg('--simple', type=int, default=12, help='Bu puana kadar basit'), arg('--medium', type=int, default=20, help='Bu puana kadar orta'),
          arg('--days', default='1,3,6', help='Basit, orta, karmaşık iş akışı başına efor (gün)')],
    description='Alteryx .yxmd/.yxmc dosyalarını (XML) çalıştırmadan okur: araç sayısı ve türleri, girdi/çıktı dosya ve veritabanı bağlantıları, kullanılan makrolar (öngörücü / uzamsal makrolar dahil), Python/R aracı ve komut çalıştırma, bağlantı (connection) sayısı. Her aracı dbt/SQL karşılığıyla eşler, iş akışını puanlar ve basit/orta/karmaşık sınıfı ile gün olarak geçiş eforu verir. Bağlantı dizesindeki parolaları maskeler ve işaretler; yerel disk yollarını ayrıca listeler.',
    input='.yxmd/.yxmc dosyası veya klasörü', output='sonuc.csv (iş akışı bazında envanter) + araç tablosu', command='python main.py --input C:\\Alteryx\\Workflows',
    expect={'is_akisi': 2, 'arac': 18, 'karmasik': 1, 'parola': 1, 'efor_gun': 7})
def alteryx(args):
    p = Path(args.input)
    files = sorted({f.resolve() for pat in ('*.yxmd', '*.yxmc', '*.yxwz') for f in p.rglob(pat)}) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'Alteryx iş akışı bulunamadı: {p}')
    days = [float(x) for x in args.days.split(',')]
    out, tools_all = [], Counter()
    secrets = 0
    for f in files:
        raw = f.read_bytes()
        if b'<!DOCTYPE' in raw[:5000] or b'<!ENTITY' in raw[:20000]:
            raise ValueError(f'{f.name}: DTD/ENTITY içeren XML işlenmez.')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f'{f.name}: XML okunamadı: {exc}') from None
        kinds, inputs, outputs, macros, local_paths, notes = Counter(), [], [], [], set(), []
        for node in root.iter('Node'):
            plugin, macro = ax_plugin(node)
            if macro:
                kind = 'makro: ' + Path(macro.replace('\\', '/')).stem
                macros.append(macro)
            else:
                kind = plugin or 'bilinmeyen'
            kinds[kind] += 1
            cfg = node.find('Properties/Configuration')
            if cfg is None:
                continue
            for tag in ('File', 'Connection'):
                for el in cfg.iter(tag):
                    text = (el.text or '').strip()
                    if not text:
                        continue
                    masked = re.sub(r'(?i)((?:pwd|password)\s*=\s*)[^;|"]+', r'\1***', text)
                    if masked != text:
                        secrets += 1
                        notes.append('bağlantı dizesinde parola var (maskelendi): Alteryx Server’da kayıtlı bağlantı (DCM) kullanın')
                    if re.match(r'^[A-Za-z]:\\', masked):
                        local_paths.add(masked)
                    (inputs if plugin in ('DbFileInput', 'TextInput') else outputs if plugin == 'DbFileOutput' else inputs).append(masked)
        score = 0
        for k, n in kinds.items():
            if k.startswith('makro: '):
                name = k[7:].lower()
                score += (5 if re.search(r'regression|forest|boosted|logistic|score|cluster|predict|spatial|trade_area|distance', name) else 3) * n
            else:
                score += AX_TOOLS.get(k, ('?', 3, ''))[1] * n
        cls = 'basit' if score <= args.simple else 'orta' if score <= args.medium else 'karmaşık'
        manual = [k for k in kinds if AX_TOOLS.get(k, ('', 0))[0] == 'kod' or k.startswith('makro: ')]
        if manual:
            notes.append('elle çevrilecek: ' + ', '.join(manual))
        if local_paths:
            notes.append('yerel dosya yolu: ' + ', '.join(sorted(local_paths)[:3]))
        tools_all.update(k for k in kinds.elements())
        real = sum(n for k, n in kinds.items() if AX_TOOLS.get(k, ('', 1))[0] != 'görüntüleme')
        out.append({'is_akisi': f.name, 'puan': score, 'sinif': cls, 'arac': real, 'baglanti': len(root.findall('Connections/Connection')),
                    'girdi': '; '.join(dict.fromkeys(inputs)), 'cikti': '; '.join(dict.fromkeys(outputs)), 'makro': ', '.join(Path(m.replace('\\', '/')).name for m in macros),
                    'efor_gun': days[('basit', 'orta', 'karmaşık').index(cls)], 'dikkat': '; '.join(dict.fromkeys(notes))})
    out.sort(key=lambda o: -o['puan'])
    cls = Counter(o['sinif'] for o in out)
    effort = sum(o['efor_gun'] for o in out)
    table = [[k, n, AX_TOOLS.get(k, ('', 0, 'makro içeriğini ayrıca inceleyin' if k.startswith('makro: ') else 'karşılık tanımlı değil'))[2]] for k, n in tools_all.most_common()]
    return Result(f'{len(out)} iş akışı, {sum(o["arac"] for o in out)} araç; basit {cls["basit"]}, orta {cls["orta"]}, karmaşık {cls["karmaşık"]}; tahmini efor {fmt(effort)} gün',
                  list(out[0]), out,
                  {'is_akisi': len(out), 'arac': sum(o['arac'] for o in out), 'karmasik': cls['karmaşık'], 'parola': secrets, 'efor_gun': effort},
                  tables=[('Araçlar ve dbt/SQL karşılığı', ['Araç', 'Adet', 'Karşılık'], table)],
                  notes=['Dosya içeriği iş akışının ne yaptığını gösterir, hâlâ çalışıp çalışmadığını göstermez: Alteryx Server zamanlama ve sahip listesini ayrıca alın. '
                         'Geçişte her iş akışının çıktısını dbt audit_helper ile eski çıktıyla karşılaştırın.'])
