"""Lakehouse blokları: Delta Lake işlem günlüğü, Apache Iceberg metadata ve nesne depolama dosya boyutu analizi. Depolamaya bağlanmaz;
yerel kopya veya dışa aktarılmış liste üzerinde çalışır."""
import json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fmt

MB = 1024 * 1024


def ts(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


@op('delta-log-ozet', title='Delta Lake işlem günlüğünü (_delta_log) özetle', domain='lakehouse', product='Delta Lake', tag='Delta', kind='file',
    samples={f'delta_satis/_delta_log/{v:020d}.json': f'delta_satis/_delta_log/{v:020d}.json' for v in range(8)}, sample_input='delta_satis',
    args=[arg('--small-mb', type=float, default=32.0, help='Bu boyutun altındaki dosyalar “küçük” sayılır')],
    description='Delta tablosunun _delta_log JSON kayıtlarını okuyarak sürüm geçmişini (işlem türü, zaman), etkin dosya sayısını ve boyutunu, küçük dosya oranını, bölüm (partition) başına dosya dağılımını ve şema değişikliklerini çıkarır. OPTIMIZE/VACUUM ihtiyacını sayıyla gösterir.',
    input='Delta tablo klasörü (_delta_log içeren) veya _delta_log klasörü', output='sonuc.csv (sürümler) + bolumler.csv', command='python main.py --input C:\\yerel\\satis_tablosu --small-mb 64',
    expect={'surum': 8, 'etkin_dosya': 33, 'kucuk_dosya': 30, 'sema_degisikligi': 1})
def delta(args):
    p = Path(args.input)
    log = p if p.name == '_delta_log' else p / '_delta_log'
    files = sorted(log.glob('*.json'))
    if not files:
        raise ValueError(f'{log} içinde Delta günlük dosyası yok. (Checkpoint .parquet dosyaları okunmaz; son JSON’ları kopyalayın.)')
    active, versions, schemas, partcols = {}, [], [], []
    for f in files:
        v = int(f.stem)
        info = {'surum': v, 'islem': '', 'zaman': '', 'eklenen': 0, 'silinen': 0}
        for line in f.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            a = json.loads(line)
            if 'commitInfo' in a:
                info['islem'] = a['commitInfo'].get('operation', '')
                info['zaman'] = ts(a['commitInfo'].get('timestamp', 0))
            elif 'add' in a:
                active[a['add']['path']] = (a['add'].get('size', 0), a['add'].get('partitionValues', {}))
                info['eklenen'] += 1
            elif 'remove' in a:
                active.pop(a['remove']['path'], None)
                info['silinen'] += 1
            elif 'metaData' in a:
                fields = [x['name'] for x in json.loads(a['metaData']['schemaString'])['fields']]
                if not schemas or schemas[-1][1] != fields:
                    schemas.append((v, fields))
                partcols = a['metaData'].get('partitionColumns', [])
        versions.append(info)
    sizes = [s for s, _ in active.values()]
    small = sum(s < args.small_mb * MB for s in sizes)
    parts = defaultdict(list)
    for s, pv in active.values():
        parts['/'.join(f'{k}={v}' for k, v in sorted(pv.items())) or '(bölümsüz)'].append(s)
    prow = [{'bolum': k, 'dosya': len(v), 'toplam_mb': round(sum(v) / MB, 2), 'ortalama_mb': round(sum(v) / len(v) / MB, 2)} for k, v in sorted(parts.items())]
    changes = [f"v{v}: {', '.join(set(f) - set(schemas[i][1]))} eklendi" for i, (v, f) in enumerate(schemas[1:])]
    notes = []
    if sizes and small / len(sizes) > 0.3:
        notes.append(f'Etkin dosyaların %{100 * small / len(sizes):.0f}’i {args.small_mb:g} MB altında: OPTIMIZE (ve gerekiyorsa Z-ORDER) önerilir.')
    return Result(f'{len(versions)} sürüm; {len(active)} etkin dosya ({sum(sizes) / MB:.1f} MB), {small} küçük dosya; bölüm sütunları: {", ".join(partcols) or "yok"}',
                  ['surum', 'islem', 'zaman', 'eklenen', 'silinen'], versions,
                  {'surum': len(versions), 'etkin_dosya': len(active), 'toplam_mb': round(sum(sizes) / MB, 2), 'kucuk_dosya': small, 'sema_degisikligi': len(schemas) - 1},
                  tables=[('Şema değişiklikleri', ['Değişiklik'], [[c] for c in changes])], files={'bolumler.csv': (['bolum', 'dosya', 'toplam_mb', 'ortalama_mb'], prow)}, notes=notes)


@op('iceberg-metadata-ozet', title='Apache Iceberg tablo metadata’sını özetle', domain='lakehouse', product='Iceberg', tag='Iceberg', kind='file',
    samples={'iceberg/v5.metadata.json': 'iceberg/v5.metadata.json'},
    description='Iceberg metadata.json dosyasından snapshot geçmişini (işlem, eklenen/silinen dosya ve kayıt), şema sürümleri arasındaki alan değişikliklerini, bölümleme (partition spec) dönüşümlerini, tablo özelliklerini ve snapshot saklama ayarını çıkarır; snapshot başına ortalama dosya boyutu ve silme oranıyla bakım ihtiyacını gösterir.',
    input='vN.metadata.json dosyası', output='sonuc.csv (snapshot’lar) + rapor', command='python main.py --input metadata/v42.metadata.json',
    expect={'snapshot': 5, 'sema_surumu': 2, 'toplam_kayit': 101900})
def iceberg(args):
    try:
        meta = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Metadata okunamadı: {exc}') from None
    if 'format-version' not in meta:
        raise ValueError('Dosya bir Iceberg metadata.json değil (format-version yok).')
    snaps = []
    for s in meta.get('snapshots', []):
        sm = s.get('summary', {})
        snaps.append({'snapshot': s.get('snapshot-id'), 'zaman': ts(s.get('timestamp-ms', 0)), 'islem': sm.get('operation', ''), 'eklenen_dosya': sm.get('added-data-files', ''),
                      'silinen_dosya': sm.get('deleted-data-files', ''), 'eklenen_kayit': sm.get('added-records', ''), 'toplam_kayit': sm.get('total-records', ''),
                      'toplam_dosya': sm.get('total-data-files', ''), 'guncel': 'evet' if s.get('snapshot-id') == meta.get('current-snapshot-id') else ''})
    schemas = meta.get('schemas', [])
    diffs = []
    for a, b in zip(schemas, schemas[1:]):
        fa, fb = {f['id']: f for f in a['fields']}, {f['id']: f for f in b['fields']}
        diffs += [[b['schema-id'], f"eklendi: {fb[i]['name']} ({fb[i]['type']})"] for i in fb if i not in fa]
        diffs += [[b['schema-id'], f"silindi: {fa[i]['name']}"] for i in fa if i not in fb]
        diffs += [[b['schema-id'], f"değişti: {fb[i]['name']} {fa[i]['type']} → {fb[i]['type']}"] for i in fb if i in fa and fa[i]['type'] != fb[i]['type']]
    specs = [[s['spec-id'], ', '.join(f"{f['name']} ({f['transform']})" for f in s.get('fields', []))] for s in meta.get('partition-specs', [])]
    cur = next((s for s in snaps if s['guncel']), snaps[-1] if snaps else {})
    deleted = sum(int(s['silinen_dosya'] or 0) for s in snaps)
    return Result(f"Iceberg v{meta['format-version']} · {len(snaps)} snapshot · {cur.get('toplam_kayit', '?')} kayıt, {cur.get('toplam_dosya', '?')} dosya · {len(schemas)} şema sürümü",
                  list(snaps[0]) if snaps else [], snaps,
                  {'snapshot': len(snaps), 'sema_surumu': len(schemas), 'toplam_kayit': int(cur.get('toplam_kayit') or 0), 'silinen_dosya_toplam': deleted, 'konum': meta.get('location', '')},
                  tables=[('Şema değişiklikleri', ['Şema', 'Değişiklik'], diffs), ('Bölümleme', ['Spec', 'Alanlar'], specs),
                          ('Tablo özellikleri', ['Özellik', 'Değer'], sorted(meta.get('properties', {}).items()))],
                  notes=['Manifest ve veri dosyaları okunmaz; dosya boyutları için nesne-dosya-boyutu bloğunu depolama listesiyle kullanın.'])


@op('nesne-dosya-boyutu', title='Veri gölünde küçük dosya ve bölüm analizi', domain='lakehouse', product='Delta Lake', tag='küçük dosya', samples={'nesne_listesi.csv': 'nesne_listesi.csv'},
    args=[arg('--path', default='yol'), arg('--size', default='boyut_bayt'), arg('--small-mb', type=float, default=32.0), arg('--target-mb', type=float, default=256.0)],
    description='S3/ADLS/GCS dosya listesinden (aws s3 ls, az storage blob list veya envanter raporu) “anahtar=değer” bölümlerine göre dosya sayısı, toplam ve ortalama boyutu hesaplar; küçük dosya oranı yüksek bölümleri ve hedef dosya boyutuyla sıkıştırma sonrası beklenen dosya sayısını gösterir. Delta, Iceberg, Hudi ve düz Parquet için geçerlidir.',
    input='Dosya listesi CSV’si (yol, boyut)', output='sonuc.csv (bölüm bazında)', command='python main.py --path Key --size Size --small-mb 64 --target-mb 512',
    expect={'bolum': 8, 'dosya': 435, 'sikistirma_adayi': 3})
def nesne(columns, rows, args):
    sizes = numbers(rows, need(args.size, columns, '--size'))
    parts = defaultdict(list)
    for r, s in zip(rows, sizes):
        path = r[need(args.path, columns, '--path')].strip()
        name = path.rsplit('/', 1)[-1]
        if not s or name.startswith(('_', '.')):
            continue  # markers like _SUCCESS and hidden files
        part = '/'.join(seg for seg in path.split('/')[:-1] if '=' in seg) or path.rsplit('/', 1)[0]
        parts[part].append(s)
    out = []
    for k, v in sorted(parts.items()):
        small = sum(x < args.small_mb * MB for x in v)
        target = max(1, round(sum(v) / (args.target_mb * MB)))
        out.append({'bolum': k, 'dosya': len(v), 'toplam_mb': round(sum(v) / MB, 2), 'ortalama_mb': round(sum(v) / len(v) / MB, 2), 'kucuk_dosya_%': round(100 * small / len(v), 1),
                    'hedef_dosya': target, 'durum': 'sıkıştırma adayı' if small / len(v) > 0.5 and len(v) > 2 * target else ''})
    total = sum(o['dosya'] for o in out)
    return Result(f"{len(out)} bölüm, {total} dosya; {sum(bool(o['durum']) for o in out)} bölüm sıkıştırma adayı", list(out[0]), out,
                  {'bolum': len(out), 'dosya': total, 'sikistirma_adayi': sum(bool(o['durum']) for o in out), 'toplam_gb': round(sum(o['toplam_mb'] for o in out) / 1024, 2)})
