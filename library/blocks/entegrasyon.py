"""Veri entegrasyonu envanter blokları: Azure Data Factory pipeline JSON’u ve SSIS .dtsx paketleri. Hiçbir platforma bağlanmaz."""
import json, re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from islem import op, arg, Result

SECRETISH = re.compile(r'(password|pwd|secret|token|apikey|api_key|authorization)["\']?\s*[=:]\s*["\']?(?:Bearer\s+|Basic\s+)?(?!@|\{|\$)[^;"\'\s]{4,}', re.I)


def files_in(path, patterns):
    p = Path(path)
    files = sorted(f for pat in patterns for f in p.rglob(pat)) if p.is_dir() else [p]
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
