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


LAYERS = {'bronze': 0, 'raw': 0, 'ham': 0, 'silver': 1, 'clean': 1, 'temiz': 1, 'gold': 2, 'mart': 2, 'sunum': 2}


def layer_of(name):
    parts = re.split(r'[._/]', name.lower())
    for p in parts:
        if p in LAYERS:
            return p, LAYERS[p]
    return '', None


@op('medallion-katman-kurallari', title='Bronze / Silver / Gold katman kurallarını soy ağacında denetle', domain='lakehouse', product='Databricks Lakehouse', tag='medallion',
    samples={'lakehouse_soy.csv': 'lakehouse_soy.csv'},
    args=[arg('--source', default='kaynak'), arg('--target', default='hedef')],
    description='Tablo düzeyindeki soy ağacı kenarlarında (kaynak → hedef) her tablonun katmanını adından (bronze/raw/ham, silver/clean/temiz, gold/mart/sunum; şema veya önek olarak) çıkarır ve kuralları denetler: gold tablonun bronze’u doğrudan okuması (silver atlanmış), alt katmanın üst katmandan beslenmesi (ters akış), katmanı belirlenemeyen tablolar ve aynı katman içinde zincirleme bağımlılık. Unity Catalog / lineage dışa aktarımı için.',
    input='Soy ağacı kenarları (kaynak tablo, hedef tablo)', output='sonuc.csv (kural ihlalleri)', command='python main.py --input system_access_table_lineage.csv --source source_table_full_name --target target_table_full_name',
    expect={'kenar': 9, 'tablo': 10, 'katman_atlama': 1, 'ters_akis': 1, 'katmansiz': 1, 'uygun_kenar': 6})
def medallion(columns, rows, args):
    s, t = need(args.source, columns, '--source'), need(args.target, columns, '--target')
    edges = list(dict.fromkeys((r[s].strip(), r[t].strip()) for r in rows if r[s].strip() and r[t].strip()))
    tables = list(dict.fromkeys(x for e in edges for x in e))
    out, ok = [], 0
    for a, b in edges:
        (la, ra), (lb, rb) = layer_of(a), layer_of(b)
        if ra is None or rb is None:
            out.append({'kaynak': a, 'hedef': b, 'kaynak_katman': la, 'hedef_katman': lb, 'bulgu': 'katmanı belirlenemeyen tablo', 'detay': ', '.join(x for x, r in ((a, ra), (b, rb)) if r is None)})
        elif rb < ra:
            out.append({'kaynak': a, 'hedef': b, 'kaynak_katman': la, 'hedef_katman': lb, 'bulgu': 'ters akış', 'detay': f'{lb} katmanı {la} katmanından besleniyor'})
        elif rb - ra > 1:
            out.append({'kaynak': a, 'hedef': b, 'kaynak_katman': la, 'hedef_katman': lb, 'bulgu': 'katman atlama', 'detay': 'gold doğrudan bronze okuyor; temizlik kuralları atlanıyor'})
        else:
            ok += 1
    c = Counter(o['bulgu'] for o in out)
    return Result(f'{len(edges)} kenar, {len(tables)} tablo; {len(out)} kural ihlali', ['kaynak', 'hedef', 'kaynak_katman', 'hedef_katman', 'bulgu', 'detay'], out,
                  {'kenar': len(edges), 'tablo': len(tables), 'katman_atlama': c['katman atlama'], 'ters_akis': c['ters akış'],
                   'katmansiz': len({x for o in out if o['bulgu'] == 'katmanı belirlenemeyen tablo' for x in o['detay'].split(', ')}), 'uygun_kenar': ok},
                  notes=['Katman, tablo adının herhangi bir parçasından (katalog, şema, önek) okunur. Gold içinde gold’dan beslenme (ör. özet → daha üst özet) serbesttir.'])


@op('tablo-bakim-plani', title='Delta/Iceberg tabloları için OPTIMIZE, VACUUM ve istatistik bakım planı', domain='lakehouse', product='Delta Lake', tag='tablo bakımı',
    samples={'lakehouse_tablolar.csv': 'lakehouse_tablolar.csv'},
    args=[arg('--table', default='tablo'), arg('--files', default='dosya_sayisi'), arg('--size', default='boyut_gb'), arg('--last-optimize', default='son_optimize'),
          arg('--last-vacuum', default='son_vacuum'), arg('--deleted', default='silinmis_oran', help='Silme vektörü / silinmiş satır oranı (yüzde)'),
          arg('--as-of', default='2026-10-16'), arg('--target-mb', type=float, default=128, help='Hedef ortalama dosya boyutu (MB)'),
          arg('--vacuum-days', type=int, default=7), arg('--deleted-max', type=float, default=10)],
    description='Tablo bazında dosya sayısı, toplam boyut, son OPTIMIZE ve VACUUM tarihi ile silinmiş satır oranından bakım ihtiyacını çıkarır: ortalama dosya boyutu hedefin çok altındaysa OPTIMIZE (küçük dosya sorunu), VACUUM uzun süredir yapılmadıysa VACUUM, silinmiş satır oranı yüksekse REORG/yeniden yazma önerir; işleri tahmini etkiye göre sıralar ve SQL komutlarını üretir.',
    input='Tablo istatistikleri (DESCRIBE DETAIL / sistem tablolarından)', output='sonuc.csv (tablo bazında öneriler) + bakim.sql', command='python main.py --input table_stats.csv --target-mb 256',
    expect={'tablo': 5, 'optimize': 2, 'vacuum': 2, 'reorg': 1, 'bakim_gerekmez': 1})
def maintenance(columns, rows, args):
    tc = need(args.table, columns, '--table')
    files, size, deleted = (numbers(rows, need(c, columns, f)) for c, f in ((args.files, '--files'), (args.size, '--size'), (args.deleted, '--deleted')))
    lo, lv = need(args.last_optimize, columns, '--last-optimize'), need(args.last_vacuum, columns, '--last-vacuum')
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    if not as_of:
        raise ValueError(f'--as-of "{args.as_of}" tarih olarak okunamadı.')
    out, cmds = [], {}
    for r, f, g, d in zip(rows, files, size, deleted):
        name = r[tc].strip()
        sql = cmds.setdefault(name, [])
        avg_mb = round(g * 1024 / f, 1) if f and g is not None else None
        actions, prio = [], 0.0
        if avg_mb is not None and f >= 100 and avg_mb < args.target_mb / 4:
            actions.append(f'OPTIMIZE (ortalama dosya {fmt(avg_mb)} MB, {int(f)} dosya)')
            sql.append(f'OPTIMIZE {name};')
            prio += f / 1000
        vac = tabular.parse_date(r[lv]) if r[lv].strip() else None
        vdays = (as_of - vac[0]).days if vac else None
        if vdays is None or vdays > args.vacuum_days:
            actions.append('VACUUM (' + ('hiç yapılmamış' if vdays is None else f'{vdays} gündür yapılmamış') + ')')
            sql.append(f'VACUUM {name};')
            prio += (g or 0) / 100
        if d is not None and d > args.deleted_max:
            actions.append(f'REORG / yeniden yazma (silinmiş satır %{fmt(d)})')
            sql.append(f'REORG TABLE {name} APPLY (PURGE);')
            prio += d / 10
        opt = tabular.parse_date(r[lo]) if r[lo].strip() else None
        out.append({'tablo': name, 'dosya': int(f) if f is not None else '', 'boyut_gb': g, 'ortalama_dosya_mb': avg_mb if avg_mb is not None else '',
                    'son_optimize': opt[0].strftime('%Y-%m-%d') if opt else '', 'vacuum_gun': vdays if vdays is not None else '', 'oneri': '; '.join(actions) or 'bakım gerekmez',
                    'oncelik': round(prio, 2)})
    out.sort(key=lambda o: -o['oncelik'])
    joined = ' '.join(o['oneri'] for o in out)
    sql = [c for o in out for c in cmds[o['tablo']]]
    return Result(f'{len(out)} tablo; {sum(o["oneri"] != "bakım gerekmez" for o in out)} tabloda bakım önerisi', list(out[0]), out,
                  {'tablo': len(out), 'optimize': joined.count('OPTIMIZE'), 'vacuum': joined.count('VACUUM'), 'reorg': joined.count('REORG'),
                   'bakim_gerekmez': sum(o['oneri'] == 'bakım gerekmez' for o in out)},
                  files={'bakim.sql': '-- Öncelik sırasıyla; VACUUM saklama süresini (delta.deletedFileRetentionDuration) kontrol edin.\n' + '\n'.join(sql) + '\n'},
                  notes=['Komutlar Databricks/Delta Lake sözdizimindedir; Iceberg için rewrite_data_files ve expire_snapshots prosedürlerini kullanın. Öncelik puanı göreli bir sıralamadır.'])
