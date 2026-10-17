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
