"""Lakehouse blokları: Delta Lake işlem günlüğü, Apache Iceberg metadata ve nesne depolama dosya boyutu analizi. Depolamaya bağlanmaz;
yerel kopya veya dışa aktarılmış liste üzerinde çalışır."""
import json, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
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


@op('delta-vacuum-tahmini', title='Delta tablosunda VACUUM ile silinebilecek dosyaları tahmin et', domain='lakehouse', product='Delta Lake', tag='VACUUM', kind='file',
    samples={f'delta_iade/_delta_log/{v:020d}.json': f'delta_iade/_delta_log/{v:020d}.json' for v in range(4)}, sample_input='delta_iade',
    args=[arg('--as-of', default='2026-10-16 12:00', help='Hesap zamanı (UTC)'), arg('--retention-hours', type=float, default=168.0)],
    description='Delta _delta_log kayıtlarındaki remove işlemlerinden tablodan çıkarılmış ama depolamada duran dosyaları bulur; saklama süresini (varsayılan 7 gün) aşmış olanların VACUUM ile silinebilecek sayısını ve boyutunu, henüz süresi dolmamış olanları ve geriye dönük okumanın (time travel) en eski güvenli zamanını verir. Depolama maliyetini ve VACUUM etkisini önceden görmek için.',
    input='Delta tablo klasörü (_delta_log içeren)', output='sonuc.csv (kaldırılmış dosyalar)', command='python main.py --input C:\\yerel\\iade_tablosu --retention-hours 720',
    expect={'kaldirilan_dosya': 5, 'silinebilir_dosya': 4, 'silinebilir_mb': 40.0, 'beklemede': 1})
def vacuum(args):
    p = Path(args.input)
    log = p if p.name == '_delta_log' else p / '_delta_log'
    files = sorted(log.glob('*.json'))
    if not files:
        raise ValueError(f'{log} içinde Delta günlük dosyası yok.')
    as_of = tabular.parse_date(args.as_of)[0].replace(tzinfo=timezone.utc)
    cutoff = as_of.timestamp() * 1000 - args.retention_hours * 3600 * 1000
    sizes, removed = {}, []
    for f in files:
        for line in f.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            a = json.loads(line)
            if 'add' in a:
                sizes[a['add']['path']] = a['add'].get('size', 0)
            elif 'remove' in a:
                r = a['remove']
                removed.append({'dosya': r['path'], 'surum': int(f.stem), 'silinme': ts(r.get('deletionTimestamp', 0)),
                                'boyut_mb': round((r.get('size') or sizes.get(r['path'], 0)) / MB, 2), '_t': r.get('deletionTimestamp', 0)})
    for r in removed:
        r['durum'] = 'VACUUM ile silinebilir' if r.pop('_t') < cutoff else 'saklama süresinde'
    ok = [r for r in removed if r['durum'].startswith('VACUUM')]
    return Result(f'{len(removed)} kaldırılmış dosya; {len(ok)} dosya ({sum(r["boyut_mb"] for r in ok):.1f} MB) VACUUM ile silinebilir', ['dosya', 'surum', 'silinme', 'boyut_mb', 'durum'], removed,
                  {'kaldirilan_dosya': len(removed), 'silinebilir_dosya': len(ok), 'silinebilir_mb': round(sum(r['boyut_mb'] for r in ok), 2), 'beklemede': len(removed) - len(ok),
                   'guvenli_time_travel': datetime.fromtimestamp(cutoff / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')},
                  notes=['VACUUM sonrası saklama süresinden eski sürümlere time travel ile dönülemez; uzun çalışan okuma işleri varsa süreyi kısaltmayın.'])


@op('bolum-yapisi-denetimi', title='Veri gölünde bölüm (partition) yolu tutarlılığı ve eksik tarihler', domain='lakehouse', product='Delta Lake', tag='partition', samples={'bolum_listesi.csv': 'bolum_listesi.csv'},
    args=[arg('--path', default='yol'), arg('--size', default='boyut_bayt')],
    description='Nesne listesindeki anahtar=değer biçimli bölüm yollarını çözer: bölüm anahtarlarının adı ve sırası tutarsız olan yolları (DT= / dt=, ay/yil sırası), tarih bölümlerinde eksik günleri, boş (0 bayt) veri dosyalarını ve bölüm yapısı dışında kalan dosyaları bulur. _SUCCESS gibi işaret dosyalarını yok sayar. Hive tarzı bölümlenmiş tabloları sorgu motoruna tanıtmadan önce kontrol için.',
    input='Nesne listesi (yol, boyut)', output='sonuc.csv (bölüm bazında) + bulgular', command='python main.py --input s3_ls.csv --path key --size size',
    expect={'veri_dosyasi': 8, 'bolum': 6, 'eksik_gun': 1, 'bos_dosya': 1, 'tutarsiz_anahtar': 1, 'bolum_disi': 1})
def partitions(columns, rows, args):
    sizes = numbers(rows, need(args.size, columns, '--size'))
    pcol = need(args.path, columns, '--path')
    layouts, parts, findings, dates = Counter(), defaultdict(lambda: [0, 0]), [], set()
    data = 0
    for r, s in zip(rows, sizes):
        path = r[pcol].strip().replace('\\', '/')
        name = path.rsplit('/', 1)[-1]
        if name.startswith(('_', '.')):
            continue
        data += 1
        segs = [x for x in path.split('/')[:-1] if '=' in x]
        if s == 0:
            findings.append(['boş dosya', path])
        if not segs:
            findings.append(['bölüm dışında', path])
            continue
        keys = tuple(x.split('=', 1)[0] for x in segs)
        layouts[keys] += 1
        key = '/'.join(segs)
        parts[key][0] += 1
        parts[key][1] += s or 0
        for x in segs:
            v = x.split('=', 1)[1]
            d = tabular.parse_date(v)
            if d and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
                dates.add(d[0].date())
    main_layout = layouts.most_common(1)[0][0] if layouts else ()
    odd = [k for k in layouts if k != main_layout]
    for k in odd:
        findings.append(['tutarsız anahtar', f"{'/'.join(k)} (çoğunluk: {'/'.join(main_layout)})"])
    missing = []
    if dates:
        d, last = min(dates), max(dates)
        while d <= last:
            if d not in dates:
                missing.append(d)
            d += timedelta(days=1)
    findings += [['eksik gün', m.isoformat()] for m in missing]
    out = [{'bolum': k, 'dosya': v[0], 'boyut_mb': round(v[1] / MB, 2)} for k, v in sorted(parts.items())]
    kinds = Counter(f[0] for f in findings)
    return Result(f'{data} veri dosyası, {len(parts)} bölüm; {len(missing)} eksik gün, {kinds["tutarsız anahtar"]} tutarsız anahtar düzeni', ['bolum', 'dosya', 'boyut_mb'], out,
                  {'veri_dosyasi': data, 'bolum': len(parts), 'eksik_gun': len(missing), 'bos_dosya': kinds['boş dosya'], 'tutarsiz_anahtar': kinds['tutarsız anahtar'],
                   'bolum_disi': kinds['bölüm dışında']},
                  tables=[('Bulgular', ['Tür', 'Ayrıntı'], findings)])
