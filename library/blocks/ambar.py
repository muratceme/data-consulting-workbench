"""Veri ambarı tasarım ve depolama blokları: Redshift tablo tasarımı (dağıtım, sıralama, sıkıştırma) ve Snowflake depolama (Time Travel, Fail-safe, klon).
Ambara bağlanmaz; sistem görünümlerinin dışa aktarımlarını okur. Fiyatlar parametredir."""
import json, re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fmt

TB = 1024 ** 4


@op('redshift-tablo-tasarim', title='Redshift tablo tasarımı: dağıtım çarpıklığı, sıralama, sıkıştırma', domain='warehouse', product='Redshift', tag='SVV_TABLE_INFO',
    samples={'svv_table_info.csv': 'svv_table_info.csv'},
    args=[arg('--skew', type=float, default=4.0), arg('--unsorted', type=float, default=20.0), arg('--stats-off', type=float, default=10.0),
          arg('--all-rows', type=int, default=5_000_000, help='DISTSTYLE ALL için en fazla satır'), arg('--sort-rows', type=int, default=1_000_000, help='Bu satırın üstünde sıralama anahtarı beklenir')],
    description='SVV_TABLE_INFO dışa aktarımından dilimler arası satır çarpıklığı yüksek (yanlış DISTKEY), sıralanmamış bölümü büyük (VACUUM SORT), istatistiği eskimiş (ANALYZE), sıkıştırma kodlaması olmayan, DISTSTYLE ALL için fazla büyük ve büyük olup sıralama anahtarı olmayan tabloları bulur; her bulgu için önerilen işlemi yazar.',
    input='SVV_TABLE_INFO CSV’si', output='sonuc.csv (tablo bazında bulgular)', command='python main.py --input svv_table_info.csv --skew 3',
    expect={'tablo': 5, 'sorunlu': 4, 'bulgu': 7})
def redshift(columns, rows, args):
    for c in ('table', 'diststyle', 'tbl_rows'):
        need(c, columns, 'SVV_TABLE_INFO')
    get = lambda c: numbers(rows, c) if c in columns else [None] * len(rows)
    skew, unsorted, stats, nrows, size = get('skew_rows'), get('unsorted'), get('stats_off'), get('tbl_rows'), get('size')
    out, total = [], 0
    for r, sk, un, st, n, mb in zip(rows, skew, unsorted, stats, nrows, size):
        n = n or 0
        dist = r['diststyle'].strip().upper()
        issues = []
        if sk is not None and sk > args.skew:
            issues.append(f'dağıtım çarpık ({fmt(sk)}×): DISTKEY’i yüksek kardinaliteli birleşim sütunu yapın veya AUTO/EVEN deneyin')
        if un is not None and un > args.unsorted:
            issues.append(f'%{fmt(un)} sıralanmamış: VACUUM SORT ONLY')
        if st is not None and st > args.stats_off:
            issues.append(f'istatistik %{fmt(st)} eski: ANALYZE')
        if r.get('encoded', '').strip().upper().startswith('N'):
            issues.append('sıkıştırma kodlaması yok: ENCODE AUTO')
        if dist.startswith('ALL') and n > args.all_rows:
            issues.append(f'DISTSTYLE ALL için büyük ({n:,.0f} satır): KEY veya AUTO')
        if not r.get('sortkey1', '').strip() and n > args.sort_rows:
            issues.append('sıralama anahtarı yok: filtrelenen tarih/zaman sütununu SORTKEY yapın')
        total += len(issues)
        out.append({'tablo': f"{r.get('schema', '').strip()}.{r['table'].strip()}".strip('.'), 'diststyle': r['diststyle'], 'sortkey1': r.get('sortkey1', ''), 'satir': int(n),
                    'boyut_mb': fmt(mb), 'bulgu_sayisi': len(issues), 'bulgular': '; '.join(issues)})
    out.sort(key=lambda o: (-o['bulgu_sayisi'], o['tablo']))
    return Result(f'{len(out)} tablo; {sum(o["bulgu_sayisi"] > 0 for o in out)} tabloda {total} bulgu', list(out[0]), out,
                  {'tablo': len(out), 'sorunlu': sum(o['bulgu_sayisi'] > 0 for o in out), 'bulgu': total})


@op('snowflake-depolama', title='Snowflake depolama: Time Travel, Fail-safe ve klon maliyeti', domain='warehouse', product='Snowflake', tag='storage',
    samples={'table_storage_metrics.csv': 'table_storage_metrics.csv'},
    args=[arg('--price', type=float, default=23.0, help='TB-ay depolama fiyatı (USD)')],
    description='TABLE_STORAGE_METRICS dışa aktarımında aktif, Time Travel, Fail-safe ve klon için tutulan baytları tablo bazında ayırır; geçmiş depolaması aktif veriden büyük (sık yeniden yazılan, transient adayı), silinmiş ama Fail-safe’te ücretlenen ve klon yüzünden tutulan tabloları işaretler; aylık depolama maliyetini tahmin eder.',
    input='TABLE_STORAGE_METRICS CSV’si', output='sonuc.csv (tablo bazında depolama)', command='python main.py --input storage_metrics.csv --price 40',
    expect={'tablo': 5, 'toplam_tb': 1.4, 'gecmis_%': 52.5, 'sorunlu': 3})
def sf_storage(columns, rows, args):
    for c in ('table_name', 'active_bytes', 'time_travel_bytes', 'failsafe_bytes'):
        need(c, columns, 'TABLE_STORAGE_METRICS')
    act, tt, fs = (numbers(rows, c) for c in ('active_bytes', 'time_travel_bytes', 'failsafe_bytes'))
    clone = numbers(rows, 'retained_for_clone_bytes') if 'retained_for_clone_bytes' in columns else [0] * len(rows)
    out = []
    for r, a, t, f, c in zip(rows, act, tt, fs, clone):
        a, t, f, c = a or 0, t or 0, f or 0, c or 0
        transient = r.get('is_transient', '').strip().upper() in ('YES', 'TRUE', 'Y')
        deleted = r.get('deleted', '').strip().upper() in ('TRUE', 'YES', 'Y')
        issues = []
        if deleted and t + f:
            issues.append('silinmiş tablo hâlâ Time Travel/Fail-safe’te ücretleniyor')
        elif t + f > a and not transient:
            issues.append('geçmiş depolama aktiften büyük: sık yeniden yazılıyor; transient tablo veya kısa DATA_RETENTION_TIME_IN_DAYS düşünün')
        if c > a:
            issues.append('klon nedeniyle tutulan veri aktiften büyük: eski klonları kaldırın')
        tot = a + t + f + c
        out.append({'tablo': '.'.join(x for x in (r.get('table_catalog', '').strip(), r.get('table_schema', '').strip(), r['table_name'].strip()) if x),
                    'aktif_gb': round(a / 1024 ** 3, 2), 'time_travel_gb': round(t / 1024 ** 3, 2), 'failsafe_gb': round(f / 1024 ** 3, 2), 'klon_gb': round(c / 1024 ** 3, 2),
                    'aylik_usd': round(tot / TB * args.price, 2), 'transient': 'evet' if transient else 'hayır', 'bulgu': '; '.join(issues)})
    out.sort(key=lambda o: -o['aylik_usd'])
    total = sum(sum(x or 0 for x in v) for v in (act, tt, fs, clone))
    hist = sum(x or 0 for x in tt) + sum(x or 0 for x in fs)
    return Result(f'{len(out)} tablo, {total / TB:.2f} TB (aylık ≈ ${total / TB * args.price:,.2f}); geçmiş depolama payı %{100 * hist / (total or 1):.1f}', list(out[0]), out,
                  {'tablo': len(out), 'toplam_tb': round(total / TB, 2), 'aylik_usd': round(total / TB * args.price, 2), 'gecmis_%': round(100 * hist / (total or 1), 1),
                   'sorunlu': sum(bool(o['bulgu']) for o in out)},
                  notes=['Fail-safe süresi kalıcı tablolarda 7 gündür ve kısaltılamaz; transient/temporary tablolarda Fail-safe yoktur.'])


GB = 1024 ** 3


@op('bigquery-tablo-tasarim', title='BigQuery tablo tasarımı: bölümleme, kümeleme ve bölüm filtresi', domain='warehouse', product='BigQuery', tag='partition/cluster',
    samples={'bq_tablolar.csv': 'bq_tablolar.csv'}, args=[arg('--large-gb', type=float, default=10.0, help='Bu boyuttan büyük tablolar için bölümleme/kümeleme beklenir')],
    description='INFORMATION_SCHEMA.TABLES ve TABLE_STORAGE dışa aktarımından büyük olup bölümlenmemiş, bölümlenmiş ama sorgularda bölüm filtresi zorunlu olmayan (tam tarama riski) ve kümeleme sütunu olmayan tabloları bulur; uzun süreli (long-term) depolama payını gösterir. Tarama maliyetini düşürecek tasarım değişikliklerini önceliklendirmek için.',
    input='Tablo listesi (tablo, boyut, bölüm sütunu, kümeleme, require_partition_filter)', output='sonuc.csv (tablo bazında bulgular)', command='python main.py --input bq_tables.csv --large-gb 50',
    expect={'tablo': 5, 'sorunlu': 3, 'bulgu': 5})
def bq_design(columns, rows, args):
    for c in ('table_name', 'total_logical_bytes', 'partitioning_column', 'clustering_columns'):
        need(c, columns, 'tablo listesi')
    total = numbers(rows, 'total_logical_bytes')
    lt = numbers(rows, 'long_term_logical_bytes') if 'long_term_logical_bytes' in columns else [None] * len(rows)
    out, count = [], 0
    for r, t, l in zip(rows, total, lt):
        gb = (t or 0) / GB
        part, clus = r['partitioning_column'].strip(), r['clustering_columns'].strip()
        req = r.get('require_partition_filter', '').strip().upper() in ('YES', 'TRUE')
        issues = []
        if gb > args.large_gb and not part:
            issues.append('bölümleme yok: tarih/zaman sütunuyla PARTITION BY')
        if part and not req:
            issues.append('bölüm filtresi zorunlu değil: require_partition_filter = TRUE')
        if gb > args.large_gb and not clus:
            issues.append('kümeleme yok: sık filtrelenen sütunlarla CLUSTER BY')
        count += len(issues)
        out.append({'tablo': '.'.join(x for x in (r.get('table_schema', '').strip(), r['table_name'].strip()) if x), 'boyut_gb': round(gb, 1), 'bolum': part or '-', 'kume': clus or '-',
                    'uzun_sureli_%': round(100 * (l or 0) / t, 1) if t else '', 'bulgular': '; '.join(issues)})
    out.sort(key=lambda o: (-len(o['bulgular']), -o['boyut_gb']))
    return Result(f'{len(out)} tablo; {sum(bool(o["bulgular"]) for o in out)} tabloda {count} tasarım bulgusu', list(out[0]), out,
                  {'tablo': len(out), 'sorunlu': sum(bool(o['bulgular']) for o in out), 'bulgu': count})


@op('kullanilmayan-tablolar', title='Sorgulanmayan ambar tablolarını erişim geçmişinden bul', domain='warehouse', product='Snowflake', tag='erişim geçmişi',
    samples={'ambar_tablolari.csv': 'ambar_tablolari.csv', 'tablo_erisimleri.csv': 'tablo_erisimleri.csv'},
    args=[arg('--access', default='@ornek/tablo_erisimleri.csv', help='Erişim geçmişi (tablo, kullanici, zaman)'), arg('--as-of', default='2026-10-16'), arg('--days', type=int, default=90)],
    description='Tablo listesini (boyutuyla) ACCESS_HISTORY, INFORMATION_SCHEMA.JOBS veya denetim logundan çıkarılmış erişim kayıtlarıyla eşleştirir; belirtilen gün sayısında hiç okunmamış tabloları, son erişim tarihini, erişen kullanıcı sayısını ve kaldırılırsa kazanılacak depolamayı raporlar. Ambar temizliği ve depolama maliyeti için.',
    input='Tablo listesi (tablo, boyut_gb) + erişim geçmişi', output='sonuc.csv (tablo bazında kullanım)', command='python main.py --input tables.csv --access access_history.csv --days 180',
    expect={'tablo': 6, 'kullanilmayan': 3, 'kullanilmayan_gb': 460.0})
def unused_tables(columns, rows, args):
    need('tablo', columns, 'tablo listesi')
    acols, arows = tabular.read(args.access)
    for c in ('tablo', 'zaman'):
        need(c, acols, '--access')
    as_of = tabular.parse_date(args.as_of)[0]
    last, users, hits = {}, defaultdict(set), Counter()
    for a in arows:
        d = tabular.parse_date(a['zaman'])
        if not d:
            continue
        k = a['tablo'].strip().lower()
        last[k] = max(last.get(k, d[0]), d[0])
        if (as_of - d[0]).days <= args.days:
            hits[k] += 1
            users[k].add(a.get('kullanici', '').strip().lower())
    sizes = numbers(rows, 'boyut_gb') if 'boyut_gb' in columns else [None] * len(rows)
    out = []
    for r, s in zip(rows, sizes):
        k = r['tablo'].strip().lower()
        idle = (as_of - last[k]).days if k in last else None
        unused = idle is None or idle > args.days
        out.append({'tablo': r['tablo'], 'boyut_gb': fmt(s), 'son_erisim': last[k].strftime('%Y-%m-%d') if k in last else 'hiç', 'gun_once': idle if idle is not None else '',
                    f'erisim_{args.days}g': hits[k], 'kullanici': len(users[k]), 'durum': 'KULLANILMIYOR' if unused else 'kullanılıyor'})
    out.sort(key=lambda o: (o['durum'] != 'KULLANILMIYOR', -(float(o['boyut_gb'] or 0))))
    unused_gb = sum(float(o['boyut_gb'] or 0) for o in out if o['durum'] == 'KULLANILMIYOR')
    return Result(f'{len(out)} tablo; {sum(o["durum"] == "KULLANILMIYOR" for o in out)} tablo {args.days} gündür okunmuyor ({unused_gb:,.1f} GB)', list(out[0]), out,
                  {'tablo': len(out), 'kullanilmayan': sum(o['durum'] == 'KULLANILMIYOR' for o in out), 'kullanilmayan_gb': round(unused_gb, 2)},
                  notes=['Erişim geçmişi yalnız sorgu okumalarını gösterir; yükleme işlerinin yazdığı ama kimsenin okumadığı tablolar da burada kullanılmıyor görünür. Silmeden önce sahibine sorun ve yedekleyin.'])


OPEN_END = {'', '9999-12-31', '9999-12-31 00:00:00', '2999-12-31', 'null', 'none'}
TRUE = {'1', 'true', 'evet', 'e', 'y', 'yes', 't'}


@op('scd2-butunluk', title='SCD tip 2 boyut tablosunda çakışan, boşluklu ve çok güncelli kayıtları bul', domain='warehouse', product='Snowflake', tag='SCD2',
    samples={'dim_musteri_scd2.csv': 'dim_musteri_scd2.csv'},
    args=[arg('--key', default='musteri_no', help='Doğal anahtar sütun(lar)ı, virgülle'), arg('--start', default='gecerlilik_baslangic'), arg('--end', default='gecerlilik_bitis'),
          arg('--current', default='guncel_mi', help='Güncel bayrağı (boş bırakılabilir)'), arg('--ignore', default='', help='Değişiklik karşılaştırmasında yok sayılacak ek sütunlar (sk, yükleme zamanı)'),
          arg('--inclusive', action='store_true', help='Bitiş tarihi kapsayıcı (sonraki başlangıç = bitiş + 1 gün); varsayılan: sonraki başlangıç = bitiş')],
    description='Tip 2 yavaş değişen boyut dışa aktarımında her doğal anahtarın geçerlilik aralıklarını sıraya koyar; çakışan aralıkları, araya giren boşlukları, birden çok veya hiç güncel kaydı olmayan anahtarları, güncel bayrağı ile açık bitiş tarihi çelişen satırları, başlangıcı bitişinden sonra olan satırları ve hiçbir nitelik değişmeden açılmış gereksiz versiyonları bulur. Olgu tablosunun yanlış boyut versiyonuna bağlanmasını önlemek için.',
    input='SCD2 boyut tablosu dışa aktarımı (anahtar, nitelikler, geçerlilik başlangıç/bitiş, güncel bayrağı)', output='sonuc.csv (bulgular)',
    command='python main.py --input dim_customer.csv --key customer_id --start valid_from --end valid_to --current is_current --ignore customer_sk,load_ts',
    expect={'anahtar': 7, 'satir': 14, 'sorunlu_anahtar': 6, 'cakisma': 1, 'bosluk': 1, 'coklu_guncel': 1, 'guncel_yok': 1, 'bayrak_tutarsiz': 1, 'gereksiz_versiyon': 1, 'ters_aralik': 1})
def scd2(columns, rows, args):
    keys = [need(k.strip(), columns, '--key') for k in args.key.split(',') if k.strip()]
    sc, ec = need(args.start, columns, '--start'), need(args.end, columns, '--end')
    cc = need(args.current, columns, '--current') if args.current else None
    skip = set(keys) | {sc, ec} | ({cc} if cc else set()) | {c.strip() for c in args.ignore.split(',') if c.strip()}
    # Columns unique on every row (surrogate keys, load timestamps) change with each version by design; they are not attributes.
    auto = [c for c in columns if c not in skip and len(rows) > 1 and len({r[c].strip() for r in rows}) == len(rows)]
    attrs = [c for c in columns if c not in skip and c not in auto]
    groups = defaultdict(list)
    for i, r in enumerate(rows, start=2):
        groups[tuple(r[k].strip() for k in keys)].append((i, r))
    out, step = [], timedelta(days=1 if args.inclusive else 0)

    def add(key, kind, line, detail):
        out.append({'anahtar': ' | '.join(key), 'bulgu': kind, 'satir': line, 'detay': detail})
    for key, items in groups.items():
        versions = []
        for line, r in items:
            s = tabular.parse_date(r[sc])
            open_end = r[ec].strip().lower() in OPEN_END
            e = None if open_end else tabular.parse_date(r[ec])
            if not s or (not open_end and not e):
                add(key, 'tarih okunamadı', line, f'{r[sc]} → {r[ec]}')
                continue
            s, e = s[0], (e[0] if e else None)
            flag = r[cc].strip().lower() in TRUE if cc else open_end
            if e is not None and e < s:
                add(key, 'ters aralık', line, f'başlangıç {s:%Y-%m-%d} bitişten ({e:%Y-%m-%d}) sonra')
                continue
            if cc and flag != open_end:
                add(key, 'bayrak tutarsız', line, 'güncel işaretli ama bitiş tarihi kapalı' if flag else 'bitiş açık ama güncel işaretli değil')
            versions.append((s, e, flag, line, tuple(r[a].strip() for a in attrs)))
        versions.sort(key=lambda v: v[0])
        current = [v for v in versions if v[2]]
        if len(current) > 1:
            add(key, 'birden çok güncel', ', '.join(str(v[3]) for v in current), f'{len(current)} satır güncel')
        elif not current and versions:
            add(key, 'güncel kayıt yok', versions[-1][3], 'son versiyon kapalı (silinmiş kayıt olabilir)')
        for a, b in zip(versions, versions[1:]):
            if a[1] is None or b[0] < a[1] + step:
                add(key, 'çakışma', f'{a[3]}, {b[3]}', f'{a[0]:%Y-%m-%d}–{a[1]:%Y-%m-%d} ile {b[0]:%Y-%m-%d} başlangıcı çakışıyor' if a[1] else 'açık uçlu versiyondan sonra yeni versiyon')
            elif b[0] > a[1] + step:
                add(key, 'boşluk', f'{a[3]}, {b[3]}', f'{(a[1] + step):%Y-%m-%d} ile {b[0]:%Y-%m-%d} arasında geçerli versiyon yok')
            elif a[4] == b[4]:
                add(key, 'gereksiz versiyon', f'{a[3]}, {b[3]}', 'nitelikler değişmeden yeni versiyon açılmış')
    out.sort(key=lambda o: (o['anahtar'], str(o['satir'])))
    c = Counter(o['bulgu'] for o in out)
    return Result(f'{len(groups)} anahtar, {len(rows)} satır; {len({o["anahtar"] for o in out})} anahtarda SCD2 bulgusu', ['anahtar', 'bulgu', 'satir', 'detay'], out,
                  {'anahtar': len(groups), 'satir': len(rows), 'sorunlu_anahtar': len({o['anahtar'] for o in out}), 'cakisma': c['çakışma'], 'bosluk': c['boşluk'],
                   'coklu_guncel': c['birden çok güncel'], 'guncel_yok': c['güncel kayıt yok'], 'bayrak_tutarsiz': c['bayrak tutarsız'],
                   'gereksiz_versiyon': c['gereksiz versiyon'], 'ters_aralik': c['ters aralık']},
                  notes=['Açık bitiş: boş, 9999-12-31 veya 2999-12-31. Varsayılan kural: sonraki versiyon önceki versiyonun bitiş tarihinde başlar (bitiş hariç); bitişiniz kapsayıcıysa --inclusive verin.',
                         'Gereksiz versiyon karşılaştırmasında her satırda farklı olan sütunlar (' + (', '.join(auto) or 'yok') + ') ve --ignore ile verilenler nitelik sayılmaz.'])


def clustering_info(obj):
    info = obj.get('clustering_information', obj)
    if isinstance(info, str):
        info = json.loads(info)
    return info


@op('clustering-derinligi', title='Snowflake clustering bilgisinden tablo kümeleme sağlığı', domain='warehouse', product='Snowflake', tag='clustering', kind='file',
    samples={'snowflake_clustering.json': 'snowflake_clustering.json'},
    args=[arg('--good', type=float, default=4, help='Ortalama derinlik bu değere kadar iyi'), arg('--bad', type=float, default=16, help='Ortalama derinlik bu değerin üstünde kötü')],
    description='SYSTEM$CLUSTERING_INFORMATION çıktılarını (tablo başına JSON; dizi veya JSON Lines) okur; mikro-bölüm sayısını, sabit bölüm oranını, ortalama örtüşme ve derinliği, derinlik histogramından p90 derinliği hesaplar ve tabloları iyi/orta/kötü olarak sınıflar. Zaman damgası gibi yüksek kardinaliteli kümeleme anahtarlarına TO_DATE önerir. Otomatik kümeleme maliyetine değip değmeyeceğini görmek için.',
    input='Tablo başına clustering_information JSON’u (tablo + bilgiler)', output='sonuc.csv (tablo bazında kümeleme sağlığı)',
    command="python main.py --input clustering.json   # SELECT 'SATIS' tablo, SYSTEM$CLUSTERING_INFORMATION('SATIS') ...",
    expect={'tablo': 3, 'iyi': 1, 'orta': 1, 'kotu': 1, 'anahtar_onerisi': 1})
def clustering(args):
    text = Path(args.input).read_text(encoding='utf-8-sig').strip()
    try:
        items = json.loads(text)
        items = items if isinstance(items, list) else [items]
    except json.JSONDecodeError:
        items = [json.loads(l) for l in text.splitlines() if l.strip()]
    out = []
    for i, obj in enumerate(items, start=1):
        info = clustering_info(obj)
        total = info.get('total_partition_count') or 0
        hist = {int(k): v for k, v in (info.get('partition_depth_histogram') or {}).items()}
        n, cum, p90 = sum(hist.values()), 0, None
        for depth in sorted(hist):
            cum += hist[depth]
            if p90 is None and n and cum >= 0.9 * n:
                p90 = depth
        depth = float(info.get('average_depth') or 0)
        status = 'iyi' if depth <= args.good else 'kötü' if depth > args.bad else 'orta'
        keys = str(info.get('cluster_by_keys', ''))
        hint = ''
        inner = re.search(r'LINEAR\((.*)\)', keys, re.I)
        if inner and any(re.search(r'zaman|timestamp|_ts$|^ts$|_at$|time', k.strip().strip('"'), re.I) and '(' not in k for k in inner.group(1).split(',')):
            hint = 'zaman damgası kümeleme anahtarı: TO_DATE(...) ile gün düzeyine indirin'
        out.append({'tablo': obj.get('tablo') or obj.get('table') or f'#{i}', 'anahtar': keys, 'bolum': total,
                    'sabit_bolum_%': round(100 * (info.get('total_constant_partition_count') or 0) / total, 1) if total else 0,
                    'ort_ortusme': info.get('average_overlaps'), 'ort_derinlik': depth, 'p90_derinlik': p90, 'durum': status,
                    'oneri': hint or ('kümeleme anahtarını sorgu filtrelerine göre gözden geçirin veya yeniden kümelemeye izin verin' if status == 'kötü' else '')})
    if not out:
        raise ValueError('clustering_information kaydı bulunamadı.')
    out.sort(key=lambda o: -o['ort_derinlik'])
    c = Counter(o['durum'] for o in out)
    return Result(f'{len(out)} tablo; {c["kötü"]} tablo kötü kümelenmiş, {c["orta"]} orta', list(out[0]), out,
                  {'tablo': len(out), 'iyi': c['iyi'], 'orta': c['orta'], 'kotu': c['kötü'], 'anahtar_onerisi': sum(bool(o['oneri']) and 'TO_DATE' in o['oneri'] for o in out)},
                  notes=['Derinlik, aynı değer aralığını içeren üst üste binen mikro-bölüm sayısıdır; 1’e yakın olması budamanın (pruning) iyi çalıştığını gösterir. Küçük tablolarda (birkaç yüz bölüm) kümeleme genelde maliyetine değmez.'])


SYSTEM_ROLES = {'ACCOUNTADMIN', 'SECURITYADMIN', 'SYSADMIN', 'USERADMIN', 'ORGADMIN', 'PUBLIC'}


def lower_rows(rows):
    return [{k.strip().lower(): (v or '').strip() for k, v in r.items()} for r in rows]


@op('snowflake-rbac-denetimi', title='Snowflake RBAC denetimi: ACCOUNTADMIN, rol hiyerarşisi ve PUBLIC yetkileri', domain='warehouse', product='Snowflake', tag='RBAC',
    samples={'snowflake_rol_yetkileri.csv': 'snowflake_rol_yetkileri.csv', 'snowflake_kullanici_rolleri.csv': 'snowflake_kullanici_rolleri.csv'},
    args=[arg('--users', default='@ornek/snowflake_kullanici_rolleri.csv', help='ACCOUNT_USAGE.GRANTS_TO_USERS dışa aktarımı'),
          arg('--max-admins', type=int, default=3, help='ACCOUNTADMIN rolüne sahip en fazla kullanıcı'), arg('--max-roles', type=int, default=8, help='Bir kullanıcıya doğrudan verilen en fazla rol')],
    description='ACCOUNT_USAGE.GRANTS_TO_ROLES ve GRANTS_TO_USERS dışa aktarımlarından rol hiyerarşisini kurar ve Snowflake erişim modelini denetler: ACCOUNTADMIN rolünün çok fazla (veya tek) kişide olması, SYSADMIN altına bağlanmamış özel roller, PUBLIC rolüne verilmiş tablo/şema/ambar yetkileri, ACCOUNTADMIN’in sahibi olduğu nesneler, SECURITYADMIN dışında MANAGE GRANTS yetkisi, hiçbir kullanıcıya ve role verilmemiş roller ve çok fazla doğrudan rol biriktiren kullanıcılar. Silinmiş (deleted_on dolu) yetkiler yok sayılır.',
    input='GRANTS_TO_ROLES CSV’si + GRANTS_TO_USERS CSV’si', output='sonuc.csv (bulgular) + rol özeti', command='python main.py --input grants_to_roles.csv --users grants_to_users.csv',
    expect={'rol': 11, 'kullanici': 10, 'bulgu': 8, 'yuksek': 3})
def sf_rbac(columns, rows, args):
    rows = lower_rows(rows)
    for c in ('privilege', 'granted_on', 'name', 'grantee_name'):
        need(c, [k.strip().lower() for k in columns], 'GRANTS_TO_ROLES')
    ucols, urows = tabular.read(args.users)
    urows = lower_rows(urows)
    for c in ('role', 'grantee_name'):
        need(c, [k.strip().lower() for k in ucols], '--users')
    rows = [r for r in rows if not r.get('deleted_on')]
    urows = [r for r in urows if not r.get('deleted_on')]
    parents, privs, owned = defaultdict(set), defaultdict(list), []
    for r in rows:
        role, on, priv = r['grantee_name'].upper(), r['granted_on'].upper(), r['privilege'].upper()
        if on == 'ROLE' and priv == 'USAGE':
            parents[r['name'].upper()].add(role)
        else:
            privs[role].append((priv, on, r['name']))
            if priv == 'OWNERSHIP' and role == 'ACCOUNTADMIN' and on in ('DATABASE', 'SCHEMA', 'TABLE', 'VIEW', 'WAREHOUSE'):
                owned.append(f'{on} {r["name"]}')
    users_of, roles_of = defaultdict(set), defaultdict(set)
    for r in urows:
        users_of[r['role'].upper()].add(r['grantee_name'])
        roles_of[r['grantee_name']].add(r['role'].upper())
    granted = {p for ps in parents.values() for p in ps}
    roles = set(parents) | granted | set(privs) | set(users_of)

    def ancestors(role, seen=None):
        seen = set() if seen is None else seen
        for p in parents.get(role, ()):
            if p not in seen:
                seen.add(p)
                ancestors(p, seen)
        return seen
    out = []
    add = lambda sev, check, obj, detail: out.append({'onem': sev, 'kontrol': check, 'nesne': obj, 'ayrinti': detail})
    admins = sorted(users_of.get('ACCOUNTADMIN', ()))
    if len(admins) > args.max_admins:
        add('yüksek', 'ACCOUNTADMIN sayısı', 'ACCOUNTADMIN', f'{len(admins)} kullanıcıda ({", ".join(admins)}); en fazla {args.max_admins} olmalı, günlük iş için SYSADMIN/işlev rolü kullanılmalı.')
    elif len(admins) < 2:
        add('orta', 'ACCOUNTADMIN sayısı', 'ACCOUNTADMIN', 'Yalnızca bir kullanıcıda; kilitlenme riskine karşı MFA’lı ikinci bir yönetici tanımlayın.')
    for role in sorted(roles - SYSTEM_ROLES):
        if 'SYSADMIN' not in ancestors(role):
            add('orta', 'Hiyerarşi dışı rol', role, f'SYSADMIN’e bağlanmamış; SYSADMIN bu rolün nesnelerini yönetemez. GRANT ROLE {role} TO ROLE SYSADMIN.')
        if not users_of.get(role) and not parents.get(role) and role not in granted:
            add('düşük', 'Kullanılmayan rol', role, 'Hiçbir kullanıcıya veya role verilmemiş; gerekmiyorsa kaldırın.')
    for priv, on, name in privs.get('PUBLIC', ()):
        if on in ('TABLE', 'VIEW', 'SCHEMA', 'DATABASE', 'WAREHOUSE'):
            add('yüksek' if on in ('TABLE', 'VIEW') else 'orta', 'PUBLIC yetkisi', f'{on} {name}', f'{priv} yetkisi PUBLIC rolünde: hesaptaki herkes erişir.')
    if owned:
        add('orta', 'ACCOUNTADMIN nesne sahibi', 'ACCOUNTADMIN', f'{len(owned)} nesnenin sahibi ({", ".join(owned[:5])}); sahiplik SYSADMIN veya işlev rolüne devredilmeli.')
    for role, items in sorted(privs.items()):
        if role not in ('SECURITYADMIN', 'ACCOUNTADMIN') and any(p == 'MANAGE GRANTS' for p, _, _ in items):
            add('yüksek', 'MANAGE GRANTS', role, 'Her nesnede yetki verip alabilir; yalnızca SECURITYADMIN’de olmalı.')
    for user, rs in sorted(roles_of.items()):
        if len(rs) > args.max_roles:
            add('düşük', 'Çok sayıda doğrudan rol', user, f'{len(rs)} rol doğrudan verilmiş; işlev rolüyle toplayın.')
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2}
    out.sort(key=lambda o: (order[o['onem']], o['kontrol'], o['nesne']))
    summary_rows = [[r, ', '.join(sorted(parents.get(r, ()))) or '—', len(users_of.get(r, ())), len(privs.get(r, ()))] for r in sorted(roles)]
    high = sum(o['onem'] == 'yüksek' for o in out)
    return Result(f'{len(roles)} rol, {len(roles_of)} kullanıcı; {len(out)} bulgu ({high} yüksek)', ['onem', 'kontrol', 'nesne', 'ayrinti'], out,
                  {'rol': len(roles), 'kullanici': len(roles_of), 'bulgu': len(out), 'yuksek': high},
                  tables=[('Roller', ['Rol', 'Üst rol', 'Kullanıcı', 'Nesne yetkisi'], summary_rows)],
                  notes=['ACCOUNT_USAGE görünümleri 2 saate kadar gecikmeli olabilir. Veritabanı rolleri (database role) ayrı görünümdedir ve burada denetlenmez.'])


TD_PATTERNS = [
    ('SEL/INS/UPD/DEL kısaltması', r'^\s*(SEL|INS|UPD|DEL)\b', 'otomatik', 'SELECT/INSERT/UPDATE/DELETE olarak yazılır'),
    ('LOCKING ... FOR ACCESS', r'\bLOCK(ING)?\s+(ROW|TABLE\s+\S+|DATABASE\s+\S+)\s+FOR\s+ACCESS\b', 'otomatik', 'okuma kilidi ipucu; kaldırılır'),
    ('COLLECT STATISTICS', r'\bCOLLECT\s+STAT', 'otomatik', 'hedefte gerekmez; kaldırılır'),
    ('VOLATILE tablo', r'\bVOLATILE\b', 'otomatik', 'TEMPORARY TABLE'),
    ('MULTISET tablo', r'\bCREATE\s+MULTISET\b', 'otomatik', 'MULTISET ifadesi kaldırılır'),
    ('SET tablo', r'\bCREATE\s+SET\s+((GLOBAL\s+TEMPORARY|VOLATILE)\s+)?TABLE\b', 'basit', 'SET tablo mükerrer satırı sessizce atar; hedefte DISTINCT veya benzersizlik kontrolü gerekir'),
    ('PRIMARY INDEX', r'\bPRIMARY\s+INDEX\b', 'basit', 'clustering / dağıtım anahtarı olarak yeniden tasarlanır'),
    ('CASESPECIFIC', r'\b(NOT\s+)?CASESPECIFIC\b|\(\s*(NOT\s+)?CS\s*\)', 'basit', 'büyük/küçük harf duyarlılığı: COLLATE veya UPPER()'),
    ("FORMAT '...'", r"\bFORMAT\s+'[^']*'", 'basit', 'TO_CHAR / TO_DATE'),
    ('ZEROIFNULL / NULLIFZERO', r'\b(ZEROIFNULL|NULLIFZERO)\s*\(', 'basit', 'COALESCE(x, 0) / NULLIF(x, 0)'),
    ('TOP n WITH TIES', r'\bTOP\s+\d+\s+WITH\s+TIES\b', 'basit', 'QUALIFY RANK() <= n'),
    ('SAMPLE', r'\bSAMPLE\s+\d', 'basit', 'TABLESAMPLE / SAMPLE'),
    ('CHARACTERS / INDEX', r'\b(CHARACTERS|CHARS)\s*\(|(?<!PRIMARY )(?<!UNIQUE )\bINDEX\s*\(', 'basit', 'LENGTH / POSITION'),
    ('HASHROW / HASHAMP', r'\bHASH(ROW|AMP|BUCKET|BAKAMP)\s*\(', 'manuel', 'AMP dağıtımına özgü; karşılığı yok, mantık yeniden yazılmalı'),
    ('PERIOD / EXPAND ON / NORMALIZE', r'\bPERIOD\s*\(|\bEXPAND\s+ON\b|\bNORMALIZE\b', 'manuel', 'zaman aralığı türü; başlangıç/bitiş kolonlarına ve takvim tablosuna çevrilmeli'),
    ('Makro (EXEC / CREATE MACRO)', r'^\s*EXEC(UTE)?\s+\w|\bCREATE\s+MACRO\b', 'manuel', 'saklı yordam veya görünüme çevrilmeli'),
    ('BTEQ komutu', r'(?m)^\s*\.(LOGON|EXPORT|IMPORT|IF|GOTO|QUIT|SET|RUN|LABEL|OS)\b', 'manuel', 'betik akışı orkestratöre / Python’a taşınmalı'),
]
RANK = {'otomatik': 0, 'basit': 1, 'manuel': 2}


def td_fingerprint(sql):
    s = re.sub(r'/\*.*?\*/|--[^\n]*', ' ', sql, flags=re.S)
    s = re.sub(r"'(?:[^']|'')*'", '?', s)
    s = re.sub(r'\b\d+(\.\d+)?\b', '?', s)
    s = re.sub(r'\(\s*\?(\s*,\s*\?)+\s*\)', '(?)', s)
    return re.sub(r'\s+', ' ', s).strip().upper()


@op('teradata-is-yuku', title='Teradata DBQL iş yükü analizi ve dönüştürülecek söz dizimi envanteri', domain='warehouse', product='Vantage', tag='DBQL',
    samples={'teradata_dbql.csv': 'teradata_dbql.csv'},
    args=[arg('--top', type=int, default=10, help='CPU payı raporlanacak en pahalı kalıp sayısı')], test_args=['--top', '3'],
    description='DBQL (DBC.QryLogV) dışa aktarımındaki sorguları sabitler ? yapılarak kalıplara ayırır; kalıp başına çalışma sayısı, kullanıcı sayısı ve AMP CPU payını, en çok kullanılan tabloları ve Snowflake/BigQuery/Databricks’e geçişte değişmesi gereken Teradata’ya özgü söz dizimini (SEL kısaltması, LOCKING, COLLECT STATISTICS, VOLATILE/SET tablo, PRIMARY INDEX, CASESPECIFIC, FORMAT, ZEROIFNULL, TOP WITH TIES, HASHROW, PERIOD, makro, BTEQ komutları) çıkarır; her kalıbı otomatik/basit/manuel dönüşüm sınıfına koyar. Geçiş değerlendirmesinde kıyas testi yapılacak kalıpları seçmek ve efor tahmini için.',
    input='DBQL dışa aktarımı (UserName, StartTime, AMPCPUTime, QueryText)', output='sonuc.csv (kalıp bazında iş yükü ve dönüşüm sınıfı)', command='python main.py --input dbql_90gun.csv --top 100',
    expect={'sorgu': 24, 'kalip': 9, 'manuel_kalip': 2, 'ilk_kalip_cpu_%': 92.4})
def teradata(columns, rows, args):
    rows = lower_rows(rows)
    cols = [c.strip().lower() for c in columns]
    for c in ('username', 'querytext', 'ampcputime'):
        need(c, cols, 'DBQL')
    cpu = [tabular.parse_number(r['ampcputime']) or 0.0 for r in rows]
    groups = defaultdict(lambda: {'n': 0, 'cpu': 0.0, 'users': set(), 'text': '', 'tables': set()})
    table_hits, table_last = Counter(), {}
    for r, c in zip(rows, cpu):
        text = r['querytext']
        g = groups[td_fingerprint(text)]
        g['n'] += 1
        g['cpu'] += c
        g['users'].add(r['username'])
        g['text'] = g['text'] or text
        for t in re.findall(r'\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w$]*\.[A-Za-z_][\w$]*)', text, re.I):
            t = t.lower()
            g['tables'].add(t)
            table_hits[t] += 1
            if r.get('starttime'):
                table_last[t] = max(table_last.get(t, ''), r['starttime'][:10])
    total = sum(cpu) or 1
    out = []
    for g in groups.values():
        found = [(name, cls, note) for name, rx, cls, note in TD_PATTERNS if re.search(rx, g['text'], re.I)]
        cls = max((c for _, c, _ in found), key=RANK.get, default='otomatik')
        out.append({'kalip': re.sub(r'\s+', ' ', g['text']).strip()[:140], 'calisma': g['n'], 'kullanici': len(g['users']), 'cpu_sn': round(g['cpu'], 1),
                    'cpu_payi_%': round(100 * g['cpu'] / total, 1), 'tablolar': ', '.join(sorted(g['tables'])), 'sinif': cls,
                    'teradataya_ozgu': '; '.join(f'{n} → {note}' for n, _, note in found)})
    out.sort(key=lambda o: -o['cpu_sn'])
    top = sum(o['cpu_sn'] for o in out[:args.top])
    cls = Counter(o['sinif'] for o in out)
    tables = [[t, n, table_last.get(t, '')] for t, n in table_hits.most_common()]
    return Result(f'{len(rows)} sorgu, {len(out)} kalıp; ilk {min(args.top, len(out))} kalıp CPU payı %{100 * top / total:.1f}; '
                  f'dönüşüm: otomatik {cls["otomatik"]}, basit {cls["basit"]}, manuel {cls["manuel"]}', list(out[0]), out,
                  {'sorgu': len(rows), 'kalip': len(out), 'manuel_kalip': cls['manuel'], 'ilk_kalip_cpu_%': round(100 * top / total, 1)},
                  tables=[('Tablo kullanımı', ['Tablo', 'Sorgu', 'Son kullanım'], tables)],
                  notes=['Desenler metin üzerinde aranır; kesin dönüşüm için SnowConvert gibi bir ayrıştırıcıyla doğrulayın. DBQL’de uzun sorgular QueryText’te kesilebilir; tam metin DBQLSqlTbl’dedir.'])


GB = 1024 ** 3


@op('clickhouse-parca-analizi', title='ClickHouse system.parts: çok fazla parça, küçük insert, ince bölümleme, düşük sıkıştırma', domain='warehouse', product='ClickHouse', tag='system.parts',
    samples={'clickhouse_parts.csv': 'clickhouse_parts.csv'},
    args=[arg('--delay', type=int, default=1000, help='parts_to_delay_insert (bölüm başına aktif parça)'), arg('--small-rows', type=int, default=100_000, help='Ortalama parça bu satırdan küçükse insert’ler küçük sayılır'),
          arg('--min-partition-gb', type=float, default=1.0, help='Bölüm başına beklenen en küçük ortalama boyut (GB)'), arg('--min-ratio', type=float, default=3.0, help='Beklenen en düşük sıkıştırma oranı')],
    description='system.parts dışa aktarımından (database, table, partition, active, rows, bytes_on_disk, data_compressed_bytes, data_uncompressed_bytes) aktif parçaları tablo ve bölüm bazında toplar: bölüm başına parça sayısının parts_to_delay_insert sınırına yaklaşması (“Too many parts” riski), çok küçük parçalar (sık ve küçük insert: toplu yazma veya async_insert), çok sayıda küçük bölüm (aşırı ince PARTITION BY: aylığa geçin) ve düşük sıkıştırma oranı (kolon codec’leri ve ORDER BY). Her tablo için önerilen ayarı yazar.',
    input='system.parts CSV’si', output='sonuc.csv (tablo bazında parça ve bölüm analizi)', command='python main.py --input system_parts.csv --delay 300',
    expect={'tablo': 4, 'aktif_parca': 1450, 'sorunlu_tablo': 3, 'bulgu': 4})
def clickhouse_parts(columns, rows, args):
    for c in ('database', 'table', 'partition', 'active', 'rows', 'bytes_on_disk'):
        need(c, columns, 'system.parts')
    nrows, disk = numbers(rows, 'rows'), numbers(rows, 'bytes_on_disk')
    comp = numbers(rows, 'data_compressed_bytes') if 'data_compressed_bytes' in columns else [None] * len(rows)
    raw = numbers(rows, 'data_uncompressed_bytes') if 'data_uncompressed_bytes' in columns else [None] * len(rows)
    t = defaultdict(lambda: {'parts': 0, 'rows': 0, 'bytes': 0, 'comp': 0, 'raw': 0, 'partitions': Counter()})
    for r, n, b, c, u in zip(rows, nrows, disk, comp, raw):
        if r['active'].strip().lower() not in ('1', 'true'):
            continue
        e = t[f"{r['database'].strip()}.{r['table'].strip()}"]
        e['parts'] += 1
        e['rows'] += n or 0
        e['bytes'] += b or 0
        e['comp'] += c or 0
        e['raw'] += u or 0
        e['partitions'][r['partition'].strip()] += 1
    out, total = [], 0
    for name, e in sorted(t.items()):
        issues = []
        worst = max(e['partitions'].values())
        if worst > 0.5 * args.delay:
            issues.append(f'bir bölümde {worst} aktif parça (sınır {args.delay}): insert’leri toplayın, birleştirme (merge) yetişmiyor')
        avg_rows = e['rows'] / e['parts']
        if avg_rows < args.small_rows and e['parts'] > 50:
            issues.append(f'ortalama parça {avg_rows:,.0f} satır: küçük ve sık insert; 10 bin–100 bin satırlık toplu yazma veya async_insert=1')
        nparts = len(e['partitions'])
        avg_part_gb = e['bytes'] / nparts / GB if nparts else 0
        if nparts > 100 and avg_part_gb < args.min_partition_gb:
            issues.append(f'{nparts} bölüm, ortalama {avg_part_gb:.2f} GB: bölümleme çok ince; PARTITION BY toYYYYMM(...) gibi daha kaba bölümleme')
        ratio = e['raw'] / e['comp'] if e['comp'] else None
        if ratio is not None and ratio < args.min_ratio:
            issues.append(f'sıkıştırma {ratio:.1f}×: ORDER BY ile benzer değerleri yan yana getirin, LowCardinality / Delta / ZSTD codec’lerini deneyin')
        total += len(issues)
        out.append({'tablo': name, 'aktif_parca': e['parts'], 'bolum': nparts, 'en_kalabalik_bolum_parca': worst, 'satir': int(e['rows']), 'disk_gb': round(e['bytes'] / GB, 2),
                    'sikistirma': round(ratio, 1) if ratio else '', 'bulgular': '; '.join(issues)})
    out.sort(key=lambda o: (-len(o['bulgular']), o['tablo']))
    return Result(f'{len(out)} tablo, {sum(o["aktif_parca"] for o in out)} aktif parça; {sum(bool(o["bulgular"]) for o in out)} tabloda {total} bulgu', list(out[0]), out,
                  {'tablo': len(out), 'aktif_parca': sum(o['aktif_parca'] for o in out), 'sorunlu_tablo': sum(bool(o['bulgular']) for o in out), 'bulgu': total},
                  notes=['Sınır değerleri sürüme göre değişir: parts_to_delay_insert ve parts_to_throw_insert ayarlarını SELECT * FROM system.merge_tree_settings ile kontrol edin. Pasif (active=0) parçalar birleştirme sonrası silinmeyi bekler ve sayılmaz.'])
