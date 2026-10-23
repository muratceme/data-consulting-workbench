"""Veri entegrasyonu envanter blokları: Azure Data Factory pipeline JSON’u, SSIS .dtsx paketleri, PowerCenter repository XML’i ve Fivetran MAR kullanımı. Hiçbir platforma bağlanmaz."""
import json, re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fmt

SECRETISH = re.compile(r'(password|pwd|secret|token|apikey|api_key|authorization)["\']?\s*[=:]\s*["\']?(?:Bearer\s+|Basic\s+)?(?!@|\{|\$)[^;"\'\s]{4,}', re.I)


def files_in(path, patterns):
    p = Path(path)
    files = sorted({f.resolve() for pat in patterns for f in p.rglob(pat)}) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'Dosya bulunamadı: {p}')
    return files


def walk_activities(acts, parent=''):
    for a in acts or []:
        yield a, parent
        tp = a.get('typeProperties', {})
        for key in ('activities', 'ifTrueActivities', 'ifFalseActivities', 'defaultActivities'):
            yield from walk_activities(tp.get(key), a.get('name', ''))
        for case in tp.get('cases', []) or []:
            yield from walk_activities(case.get('activities'), a.get('name', ''))


@op('adf-pipeline-envanter', title='Azure Data Factory / Synapse pipeline envanteri', domain='integration', product='Azure Data Factory', tag='ADF', kind='file',
    samples={'adf/PL_Satis_Gunluk.json': 'adf/PL_Satis_Gunluk.json'}, sample_input='adf',
    description='ADF/Synapse/Fabric pipeline JSON’larındaki (Git deposu veya ARM dışa aktarımı) aktiviteleri iç içe ForEach/If/Switch dahil listeler; bağımlılık zincirini Mermaid olarak çizer, kullanılan dataset ve linked service’leri çıkarır. Yeniden denemesi olmayan aktiviteleri, SELECT * sorgularını ve koda gömülü kimlik bilgisini işaretler.',
    input='pipeline .json dosyası veya klasörü', output='sonuc.csv (aktiviteler) + bulgular + Mermaid', command='python main.py --input adf-repo/pipeline',
    expect={'pipeline': 1, 'aktivite': 5, 'bulgu': 5})
def adf(args):
    rows, findings, edges, refs = [], [], [], Counter()
    count = 0
    for f in files_in(args.input, ['*.json']):
        try:
            data = json.loads(f.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as exc:
            raise ValueError(f'{f.name}: JSON okunamadı ({exc.msg}).') from None
        props = data.get('properties', data)
        if 'activities' not in props:
            continue
        count += 1
        pl = data.get('name', f.stem)
        for a, parent in walk_activities(props['activities']):
            name, kind, tp = a.get('name', ''), a.get('type', ''), a.get('typeProperties', {})
            for d in a.get('dependsOn', []) or []:
                edges.append((d.get('activity'), name, '/'.join(d.get('dependencyConditions', []))))
            for io in (a.get('inputs') or []) + (a.get('outputs') or []):
                refs['dataset: ' + io.get('referenceName', '')] += 1
            if 'dataset' in tp:
                refs['dataset: ' + tp['dataset'].get('referenceName', '')] += 1
            if a.get('linkedServiceName'):
                refs['linked service: ' + a['linkedServiceName'].get('referenceName', '')] += 1
            # Nested activities are checked on their own; leave them out so a container does not repeat their findings.
            own = {k: v for k, v in tp.items() if k not in ('activities', 'ifTrueActivities', 'ifFalseActivities', 'defaultActivities', 'cases')}
            query = json.dumps(own, ensure_ascii=False)
            policy = a.get('policy', {})
            rows.append({'pipeline': pl, 'aktivite': name, 'tur': kind, 'ust': parent, 'yeniden_deneme': policy.get('retry', ''), 'zaman_asimi': policy.get('timeout', '')})
            if kind in ('Copy', 'Lookup', 'SqlServerStoredProcedure', 'Script', 'WebActivity', 'ExecuteDataFlow') and not policy.get('retry'):
                findings.append([pl, name, 'orta', 'Yeniden deneme (retry) yok — geçici bağlantı hatasında pipeline düşer'])
            if re.search(r'select\s+\*', query, re.I):
                findings.append([pl, name, 'düşük', 'SELECT * — kaynak şema değişince eşleme bozulur'])
            if SECRETISH.search(query):
                findings.append([pl, name, 'yüksek', 'Koda gömülü kimlik bilgisi — Key Vault referansı veya managed identity kullanın'])
    if not count:
        raise ValueError('Pipeline tanımı (properties.activities) bulunamadı.')
    mermaid = ['flowchart LR'] + [f'  {re.sub(r"\W", "_", a)} -->|{c}| {re.sub(r"\W", "_", b)}' for a, b, c in edges]
    return Result(f'{count} pipeline, {len(rows)} aktivite, {len(findings)} bulgu', list(rows[0]), rows,
                  {'pipeline': count, 'aktivite': len(rows), 'bulgu': len(findings), 'turler': dict(Counter(r['tur'] for r in rows))},
                  tables=[('Bulgular', ['Pipeline', 'Aktivite', 'Önem', 'Bulgu'], findings), ('Başvurulan kaynaklar', ['Kaynak', 'Kullanım'], refs.most_common())],
                  files={'akis.md': '```mermaid\n' + '\n'.join(mermaid) + '\n```'})


DTS = '{www.microsoft.com/SqlServer/Dts}'


def attr(el, name):
    return el.get(DTS + name) or el.get(name) or ''


@op('ssis-paket-envanter', title='SSIS (.dtsx) paket envanteri', domain='integration', product='SSIS', tag='SSIS', kind='file',
    samples={'ssis/SatisYukle.dtsx': 'ssis/SatisYukle.dtsx'}, sample_input='ssis',
    description='SSIS .dtsx paketlerinden bağlantı yöneticilerini (sunucu, veritabanı, dosya yolu), görevleri, devre dışı görevleri, Execute SQL komutlarını, veri akışı bileşenlerini ve kaynak sorgularını, değişkenleri ve öncelik kısıtlarını çıkarır. Paket içine yazılmış parolaları ve yerel dosya yollarını işaretler. Azure/Fabric’e geçiş envanteri için.',
    input='.dtsx dosyası veya klasörü', output='sonuc.csv (görevler) + baglantilar.csv + sql.csv', command='python main.py --input C:\\SSIS\\Projeler',
    expect={'paket': 1, 'gorev': 3, 'baglanti': 3, 'bulgu': 3})
def ssis(args):
    tasks, conns, sqls, findings = [], [], [], []
    packages = 0
    for f in files_in(args.input, ['*.dtsx']):
        raw = f.read_bytes()
        if b'<!DOCTYPE' in raw[:5000] or b'<!ENTITY' in raw[:20000]:
            raise ValueError(f'{f.name}: DTD/ENTITY içeren XML işlenmez.')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f'{f.name}: XML okunamadı ({exc}).') from None
        packages += 1
        pkg = attr(root, 'ObjectName') or f.stem
        for cm in root.iter(DTS + 'ConnectionManager'):
            if not attr(cm, 'ObjectName'):
                continue
            inner = next((c for c in cm.iter(DTS + 'ConnectionManager') if c is not cm and attr(c, 'ConnectionString')), None)
            cs = attr(inner, 'ConnectionString') if inner is not None else attr(cm, 'ConnectionString')
            server = re.search(r'Data Source=([^;]+)', cs, re.I)
            db = re.search(r'Initial Catalog=([^;]+)', cs, re.I)
            conns.append({'paket': pkg, 'baglanti': attr(cm, 'ObjectName'), 'tur': attr(cm, 'CreationName'), 'sunucu': server.group(1) if server else '',
                          'veritabani': db.group(1) if db else '', 'dosya': cs if not server and cs else ''})
            if re.search(r'Password=[^;]+', cs, re.I):
                findings.append([pkg, attr(cm, 'ObjectName'), 'yüksek', 'Bağlantı dizesinde parola — paket koruma düzeyi/parametre veya Windows kimlik doğrulaması kullanın'])
            if re.match(r'^[A-Za-z]:[\\/]', cs):
                findings.append([pkg, attr(cm, 'ObjectName'), 'orta', 'Yerel dosya yolu — sunucuda/bulutta çalışmaz; parametreleyin'])
        for ex in root.iter(DTS + 'Executable'):
            if ex is root:
                continue
            kind = attr(ex, 'ExecutableType').replace('Microsoft.', '')
            name = attr(ex, 'ObjectName')
            disabled = attr(ex, 'Disabled').lower() == 'true'
            tasks.append({'paket': pkg, 'gorev': name, 'tur': kind, 'devre_disi': 'evet' if disabled else ''})
            if disabled:
                findings.append([pkg, name, 'düşük', 'Devre dışı görev — kullanılmıyorsa kaldırın, kullanılıyorsa nedenini belgeleyin'])
            for el in ex.iter():
                src = next((v for k, v in el.attrib.items() if k.endswith('SqlStatementSource')), None)
                if src:
                    sqls.append({'paket': pkg, 'gorev': name, 'bilesen': '', 'sql': src})
            for comp in ex.iter('component'):
                for prop in comp.iter('property'):
                    if prop.get('name') in ('SqlCommand', 'OpenRowset') and (prop.text or '').strip():
                        sqls.append({'paket': pkg, 'gorev': name, 'bilesen': comp.get('name', ''), 'sql': prop.text.strip()})
    cols = ['paket', 'gorev', 'tur', 'devre_disi']
    return Result(f'{packages} paket, {len(tasks)} görev, {len(conns)} bağlantı, {len(sqls)} SQL; {len(findings)} bulgu', cols, tasks,
                  {'paket': packages, 'gorev': len(tasks), 'baglanti': len(conns), 'sql': len(sqls), 'bulgu': len(findings)},
                  tables=[('Bulgular', ['Paket', 'Nesne', 'Önem', 'Bulgu'], findings)],
                  files={'baglantilar.csv': (['paket', 'baglanti', 'tur', 'sunucu', 'veritabani', 'dosya'], conns), 'sql.csv': (['paket', 'gorev', 'bilesen', 'sql'], sqls)})


@op('fivetran-mar', title='Fivetran MAR analizi: pahalı bağlantı ve tablolar, kimsenin okumadığı senkronizasyonlar', domain='integration', product='Fivetran', tag='MAR',
    samples={'fivetran_mar.csv': 'fivetran_mar.csv', 'ambar_tablo_okumalari.csv': 'ambar_tablo_okumalari.csv'},
    args=[arg('--reads', default='@ornek/ambar_tablo_okumalari.csv', help='İsteğe bağlı: hedef ambarda tablo okuma sayısı (sema, tablo, okuma_90g); boş bırakılabilir'),
          arg('--price', type=float, default=500.0, help='Milyon MAR başına ortalama USD (sözleşmenize göre)'), arg('--growth', type=float, default=50.0, help='Aydan aya bu yüzdeden fazla artış işaretlenir')],
    description='Fivetran kullanım dışa aktarımından (Platform Connector INCREMENTAL_MAR: baglanti, sema, tablo, ay, mar) son ayın MAR’ını bağlantı ve tablo bazında sıralar, MAR’ın çoğunu üreten az sayıdaki tabloyu, önceki aya göre sıçrayan tabloları (yeniden senkronizasyon, birincil anahtarsız tablo veya kaynakta toplu güncelleme belirtisi) ve ambar okuma geçmişi verilirse 90 gündür kimsenin okumadığı hâlde senkronize edilen tabloları bulur; senkronizasyonu kapatınca kazanılacak MAR ve tutarı hesaplar.',
    input='Fivetran MAR CSV’si (+ isteğe bağlı ambar tablo okuma sayıları)', output='sonuc.csv (tablo bazında MAR ve öneri)', command='python main.py --input incremental_mar.csv --reads table_reads.csv --price 450',
    expect={'tablo': 10, 'son_ay_mar': 21210000, 'kullanilmayan_mar': 6480000, 'sicrayan': 2})
def fivetran_mar(columns, rows, args):
    for c in ('baglanti', 'sema', 'tablo', 'ay', 'mar'):
        need(c, columns, 'MAR dışa aktarımı')
    mar = numbers(rows, 'mar')
    months = sorted({r['ay'].strip()[:7] for r in rows})
    if not months:
        raise ValueError('MAR satırı yok.')
    last, prev = months[-1], months[-2] if len(months) > 1 else None
    by = defaultdict(lambda: defaultdict(float))
    conn = {}
    for r, m in zip(rows, mar):
        key = (r['sema'].strip().lower(), r['tablo'].strip().lower())
        by[key][r['ay'].strip()[:7]] += m or 0
        conn[key] = r['baglanti'].strip()
    reads = None
    if args.reads and Path(args.reads).is_file():
        rcols, rrows = tabular.read(args.reads)
        for c in ('sema', 'tablo', 'okuma_90g'):
            need(c, rcols, '--reads')
        reads = {(r['sema'].strip().lower(), r['tablo'].strip().lower()): n or 0 for r, n in zip(rrows, numbers(rrows, 'okuma_90g'))}
    total = sum(v[last] for v in by.values()) or 1
    out = []
    for key, v in by.items():
        now, before = v[last], v[prev] if prev else 0
        issues = []
        growth = 100 * (now - before) / before if before else None
        if growth is not None and growth > args.growth:
            issues.append(f'önceki aya göre %{growth:.0f} arttı: yeniden senkronizasyon, PK’sız tablo veya toplu UPDATE kontrol edin')
        unused = reads is not None and reads.get(key, 0) == 0 and now > 0
        if unused:
            issues.append('90 gündür ambarda okunmuyor: tabloyu bağlantı şemasından çıkarın')
        out.append({'baglanti': conn[key], 'sema': key[0], 'tablo': key[1], f'mar_{last}': int(now), f'mar_{prev}': int(before) if prev else '',
                    'pay_%': round(100 * now / total, 1), 'aylik_usd': round(now / 1e6 * args.price, 2), 'okuma_90g': '' if reads is None else int(reads.get(key, 0)),
                    'bulgular': '; '.join(issues)})
    out.sort(key=lambda o: -o[f'mar_{last}'])
    cum, pareto = 0, 0
    for o in out:
        if cum < 0.8 * total:
            pareto += 1
        cum += o[f'mar_{last}']
    unused_mar = sum(o[f'mar_{last}'] for o in out if 'okunmuyor' in o['bulgular'])
    jumps = sum('arttı' in o['bulgular'] for o in out)
    per_conn = Counter()
    for o in out:
        per_conn[o['baglanti']] += o[f'mar_{last}']
    return Result(f'{len(out)} tablo, {last} MAR {total:,.0f}; MAR’ın %80’i {pareto} tablodan; okunmayan tabloların MAR’ı {unused_mar:,.0f} (≈ ${unused_mar / 1e6 * args.price:,.0f}/ay)',
                  list(out[0]), out, {'tablo': len(out), 'son_ay_mar': int(total), 'kullanilmayan_mar': int(unused_mar), 'sicrayan': jumps},
                  tables=[('Bağlantılar', ['Bağlantı', 'MAR', 'Pay'], [[c, f'{n:,.0f}', f'%{100 * n / total:.1f}'] for c, n in per_conn.most_common()])],
                  notes=['MAR ay başında sıfırlanır; ayın ilk günü yüksek görünür. Fivetran 2025’ten beri fiyat kademesini bağlantı bazında uygular; tutar, girilen ortalama birim fiyatla kabaca hesaplanır.'])


PC_WEIGHTS = {'Source Qualifier': 1, 'Expression': 1, 'Filter': 1, 'Sequence': 1, 'Sorter': 1, 'Union Transformation': 1, 'Lookup Procedure': 2, 'Joiner': 2,
              'Router': 2, 'Aggregator': 2, 'Update Strategy': 2, 'Rank': 2, 'Normalizer': 3, 'Transaction Control': 3, 'XML Parser': 4, 'XML Generator': 4,
              'Stored Procedure': 4, 'Java': 5, 'Custom Transformation': 5, 'External Procedure': 5}
PC_NOTES = {'Java': 'Java dönüşümü: IDMC’de yeniden derlenmeli veya ifadeye çevrilmeli', 'Custom Transformation': 'Custom dönüşüm IDMC’de desteklenmez',
            'External Procedure': 'External Procedure IDMC’de desteklenmez', 'Stored Procedure': 'Saklı yordam: SQL dönüşümüne çevrilmeli',
            'Normalizer': 'Normalizer: hiyerarşi/COBOL kaynağı kontrol edilmeli', 'XML Parser': 'XML dönüşümü: Hierarchy Parser’a çevrilmeli', 'XML Generator': 'XML dönüşümü: Hierarchy Builder’a çevrilmeli'}


@op('powercenter-envanter', title='Informatica PowerCenter repository envanteri ve IDMC geçiş karmaşıklığı', domain='integration', product='PowerCenter', tag='pmrep', kind='file',
    samples={'powercenter/SATIS_repo.xml': 'powercenter/SATIS_repo.xml'}, sample_input='powercenter',
    args=[arg('--simple', type=int, default=8, help='Bu puana kadar basit'), arg('--medium', type=int, default=20, help='Bu puana kadar orta'),
          arg('--days', default='0.5,2,5', help='Basit, orta, karmaşık mapping başına efor (gün)')],
    description='pmrep objectexport ile alınan PowerCenter repository XML’inden klasör, kaynak, hedef, mapping, mapplet, oturum ve iş akışlarını çıkarır; her mapping’i dönüşüm türü ağırlıklarıyla (Lookup, Joiner, Router 2; Normalizer 3; Stored Procedure 4; Java/Custom 5) puanlar, SQL override, lookup override ve $$ parametrelerini sayar, IDMC’de desteklenmeyen veya yeniden yazılması gereken dönüşümleri listeler. Oturumu olmayan (çalışmayan) ve geçersiz (ISVALID=NO) mapping’leri ayırır; basit/orta/karmaşık sınıfı ve gün olarak geçiş eforu verir.',
    input='Repository XML dosyası veya klasörü', output='sonuc.csv (mapping bazında karmaşıklık) + iş akışı tablosu', command='python main.py --input C:\\PowerCenter\\export',
    expect={'mapping': 4, 'karmasik': 1, 'kullanilmayan': 1, 'desteklenmeyen': 2, 'efor_gun': 7.5})
def powercenter(args):
    days = [float(x) for x in args.days.split(',')]
    out, flows = [], []
    for f in files_in(args.input, ['*.xml', '*.XML']):
        raw = f.read_bytes()
        if b'<!DOCTYPE' in raw[:5000] and b'powrmart.dtd' not in raw[:5000].lower() or b'<!ENTITY' in raw[:20000]:
            raise ValueError(f'{f.name}: ENTITY içeren XML işlenmez.')
        raw = re.sub(rb'<!DOCTYPE[^>]*>', b'', raw, count=1)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f'{f.name}: XML okunamadı: {exc}') from None
        for folder in root.iter('FOLDER'):
            sessions = {s.get('MAPPINGNAME') for s in folder.iter('SESSION')}
            for wf in folder.iter('WORKFLOW'):
                tasks = [t for t in wf.iter('TASKINSTANCE')]
                flows.append([folder.get('NAME'), wf.get('NAME'), sum(t.get('TASKTYPE') == 'Session' for t in tasks), sum(t.get('TASKTYPE') == 'Worklet' for t in tasks),
                              ', '.join(sorted({t.get('TASKTYPE') for t in tasks} - {'Session', 'Start'}))])
            mapplets = {m.get('NAME'): m for m in folder.iter('MAPPLET')}
            for m in folder.iter('MAPPING'):
                kinds = Counter(t.get('TYPE') for t in m.iter('TRANSFORMATION'))
                for inst in m.iter('INSTANCE'):
                    if inst.get('TRANSFORMATION_TYPE') == 'Mapplet':
                        kinds['Mapplet'] += 1
                        mp = mapplets.get(inst.get('TRANSFORMATION_NAME'))
                        if mp is not None:
                            kinds.update(t.get('TYPE') for t in mp.iter('TRANSFORMATION'))
                sql_override = sum(1 for t in m.iter('TRANSFORMATION') for a in t.iter('TABLEATTRIBUTE')
                                   if a.get('NAME') in ('Sql Query', 'Lookup Sql Override', 'User Defined Join', 'Source Filter') and a.get('VALUE', '').strip())
                params = len(set(re.findall(r'\$\$\w+', ET.tostring(m, encoding='unicode'))))
                score = sum(PC_WEIGHTS.get(k, 1) * n for k, n in kinds.items() if k != 'Mapplet') + 3 * kinds['Mapplet'] + 2 * sql_override
                sources = sum(1 for i in m.iter('INSTANCE') if i.get('TYPE') == 'SOURCE')
                targets = sum(1 for i in m.iter('INSTANCE') if i.get('TYPE') == 'TARGET')
                score += sources + targets
                cls = 'basit' if score <= args.simple else 'orta' if score <= args.medium else 'karmaşık'
                unsupported = [PC_NOTES[k] for k in kinds if k in PC_NOTES]
                idle = m.get('NAME') not in sessions
                invalid = m.get('ISVALID', 'YES').upper() == 'NO'
                out.append({'klasor': folder.get('NAME'), 'mapping': m.get('NAME'), 'puan': score, 'sinif': cls, 'kaynak': sources, 'hedef': targets,
                            'donusum': sum(n for k, n in kinds.items() if k != 'Mapplet'), 'mapplet': kinds['Mapplet'], 'sql_override': sql_override, 'parametre': params,
                            'oturum': 'yok' if idle else 'var', 'gecerli': 'hayır' if invalid else 'evet',
                            'oneri': 'taşıma: oturumu yok, kullanılmıyor olabilir' if idle else ('önce düzeltin: geçersiz' if invalid else 'taşı'),
                            'efor_gun': 0 if idle else days[('basit', 'orta', 'karmaşık').index(cls)],
                            'dikkat': '; '.join(unsupported), 'donusum_turleri': ', '.join(f'{k} {n}' for k, n in kinds.most_common())})
    if not out:
        raise ValueError('XML’de MAPPING bulunamadı; pmrep objectexport çıktısı verin.')
    out.sort(key=lambda o: -o['puan'])
    cls = Counter(o['sinif'] for o in out)
    effort = sum(o['efor_gun'] for o in out)
    return Result(f'{len(out)} mapping (basit {cls["basit"]}, orta {cls["orta"]}, karmaşık {cls["karmaşık"]}), {len(flows)} iş akışı; tahmini efor {fmt(effort)} gün',
                  list(out[0]), out,
                  {'mapping': len(out), 'karmasik': cls['karmaşık'], 'kullanilmayan': sum(o['oturum'] == 'yok' for o in out),
                   'desteklenmeyen': sum(bool(o['dikkat']) for o in out), 'efor_gun': effort},
                  tables=[('İş akışları', ['Klasör', 'İş akışı', 'Oturum', 'Worklet', 'Diğer görevler'], flows)],
                  notes=['Puan: dönüşüm ağırlıkları + mapplet 3 + SQL/lookup override 2 + kaynak ve hedef sayısı. Eforu ilk dalganın gerçek süresiyle kalibre edin; '
                         'Informatica’nın PowerCenter→IDMC dönüşüm aracı mapping’lerin çoğunu otomatik çevirir, kalan iş override ve desteklenmeyen dönüşümlerdedir.'])
