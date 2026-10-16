"""Veri ambarı kullanım ve bulut maliyeti blokları: Snowflake sorgu geçmişi ve kredi tüketimi, BigQuery iş maliyeti, bulut fatura özeti ve ay sonu tahmini.
Fiyatlar parametredir; kurumunuzun sözleşme fiyatıyla çalıştırın. Hiçbir hesaba bağlanmaz."""
import calendar, math, re
from collections import Counter, defaultdict
from datetime import timedelta
import tabular
from islem import op, arg, Result, need, numbers, fmt, tr_sort_key

GB, TB = 1024 ** 3, 1024 ** 4
GENEL = 'Maliyet dışa aktarımları'


def when(r, col):
    d = tabular.parse_date(r[col])
    return d[0] if d else None


def money(x):
    return f'{x:,.2f}'


@op('snowflake-sorgu-gecmisi', title='Snowflake sorgu geçmişi: yavaş, taşan ve kuyrukta bekleyen sorgular', domain='warehouse', product='Snowflake', tag='query history',
    samples={'snowflake_sorgu_gecmisi.csv': 'snowflake_sorgu_gecmisi.csv'}, args=[arg('--slow-sec', type=float, default=600.0)],
    description='ACCOUNT_USAGE.QUERY_HISTORY dışa aktarımından uzun süren, yerel/uzak depolamaya taşan (spill) ve aşırı yük nedeniyle kuyrukta bekleyen sorguları; warehouse ve kullanıcı bazında toplam süre ve taranan veriyi çıkarır. Warehouse boyutu ve sorgu iyileştirme önceliği için.',
    input='QUERY_HISTORY CSV dışa aktarımı', output='sonuc.csv (sorunlu sorgular) + kullanıcı/warehouse özeti', command='python main.py --slow-sec 300',
    expect={'sorgu': 60, 'uzak_tasma': 2, 'yavas': 1})
def sf_queries(columns, rows, args):
    for c in ('query_id', 'warehouse_name', 'total_elapsed_time'):
        need(c, columns, 'QUERY_HISTORY')
    el = numbers(rows, 'total_elapsed_time')
    loc = numbers(rows, 'bytes_spilled_to_local_storage') if 'bytes_spilled_to_local_storage' in columns else [0] * len(rows)
    rem = numbers(rows, 'bytes_spilled_to_remote_storage') if 'bytes_spilled_to_remote_storage' in columns else [0] * len(rows)
    que = numbers(rows, 'queued_overload_time') if 'queued_overload_time' in columns else [0] * len(rows)
    scan = numbers(rows, 'bytes_scanned') if 'bytes_scanned' in columns else [0] * len(rows)
    out, by_wh, by_user = [], defaultdict(lambda: [0, 0.0, 0.0]), defaultdict(lambda: [0, 0.0])
    for r, e, l, rm, q, s in zip(rows, el, loc, rem, que, scan):
        e, l, rm, q, s = e or 0, l or 0, rm or 0, q or 0, s or 0
        w = by_wh[r['warehouse_name']]
        w[0] += 1
        w[1] += e / 1000
        w[2] += s / GB
        u = by_user[r.get('user_name', '')]
        u[0] += 1
        u[1] += e / 1000
        issues = []
        if e / 1000 >= args.slow_sec:
            issues.append(f'yavaş ({e / 60000:.1f} dk)')
        if rm:
            issues.append(f'uzak depolamaya taşma {rm / GB:.1f} GB — warehouse küçük veya sorgu çok geniş')
        elif l:
            issues.append(f'yerel taşma {l / GB:.1f} GB')
        if q >= 60_000:
            issues.append(f'kuyrukta {q / 1000:.0f} sn — çoklu küme/ölçekleme')
        if issues:
            out.append({'query_id': r['query_id'], 'kullanici': r.get('user_name', ''), 'warehouse': r['warehouse_name'], 'sure_sn': round(e / 1000, 1),
                        'taranan_gb': round(s / GB, 2), 'sorunlar': '; '.join(issues), 'sorgu': r.get('query_text', '')[:120]})
    out.sort(key=lambda o: -o['sure_sn'])
    return Result(f'{len(rows)} sorgu; {len(out)} sorunlu sorgu', ['query_id', 'kullanici', 'warehouse', 'sure_sn', 'taranan_gb', 'sorunlar', 'sorgu'], out,
                  {'sorgu': len(rows), 'sorunlu': len(out), 'uzak_tasma': sum(1 for x in rem if x), 'yavas': sum(1 for x in el if (x or 0) / 1000 >= args.slow_sec)},
                  tables=[('Warehouse özeti', ['Warehouse', 'Sorgu', 'Toplam süre (dk)', 'Taranan (GB)'], [[k, v[0], round(v[1] / 60, 1), round(v[2], 1)] for k, v in sorted(by_wh.items())]),
                          ('Kullanıcı özeti', ['Kullanıcı', 'Sorgu', 'Toplam süre (dk)'], [[k, v[0], round(v[1] / 60, 1)] for k, v in sorted(by_user.items(), key=lambda kv: -kv[1][1])])])


@op('bigquery-is-maliyeti', title='BigQuery sorgu (iş) maliyeti ve pahalı sorgular', domain='warehouse', product='BigQuery', tag='maliyet',
    samples={'bigquery_isler.csv': 'bigquery_isler.csv'}, args=[arg('--price-per-tib', type=float, default=6.25, help='İsteğe bağlı (on-demand) fiyat, USD/TiB')],
    description='INFORMATION_SCHEMA.JOBS dışa aktarımında faturalanan baytları TiB fiyatıyla maliyete çevirir; kullanıcı ve sorgu kalıbı bazında maliyeti, önbellek isabetini, SELECT * taramalarını ve 1 TB üstü tek sorguları listeler. Slot rezervasyonu kullanıyorsanız slot-saat kolonu ayrıca verilir.',
    input='JOBS CSV dışa aktarımı (total_bytes_billed, user_email, query)', output='sonuc.csv (pahalı işler) + kullanıcı özeti', command='python main.py --price-per-tib 6.25',
    expect={'is': 40, 'buyuk_tarama': 1})
def bq_cost(columns, rows, args):
    for c in ('job_id', 'total_bytes_billed'):
        need(c, columns, 'JOBS')
    billed = numbers(rows, 'total_bytes_billed')
    slots = numbers(rows, 'total_slot_ms') if 'total_slot_ms' in columns else [0] * len(rows)
    users, patterns = defaultdict(float), defaultdict(lambda: [0, 0.0])
    out = []
    for r, b, s in zip(rows, billed, slots):
        cost = (b or 0) / TB * args.price_per_tib
        users[r.get('user_email', '')] += cost
        q = re.sub(r"'[^']*'|\"[^\"]*\"|\b\d+\b", '?', r.get('query', '')).strip()[:100]
        patterns[q][0] += 1
        patterns[q][1] += cost
        flags = []
        if (b or 0) >= 10 ** 12:
            flags.append('1 TB üstü tarama')
        if re.search(r'select\s+\*', r.get('query', ''), re.I) and (b or 0) > 10 * GB:
            flags.append('SELECT * ile büyük tarama (sütun seçin, bölümleme filtresi ekleyin)')
        out.append({'job_id': r['job_id'], 'kullanici': r.get('user_email', ''), 'faturalanan_gb': round((b or 0) / GB, 2), 'maliyet_usd': round(cost, 2),
                    'slot_saat': round((s or 0) / 3_600_000, 3), 'uyari': '; '.join(flags)})
    out.sort(key=lambda o: -o['maliyet_usd'])
    total = sum(o['maliyet_usd'] for o in out)
    cache = sum(1 for r in rows if r.get('cache_hit', '').lower() == 'true')
    return Result(f'{len(out)} iş; toplam ${total:,.2f}; en pahalı iş ${out[0]["maliyet_usd"]:,.2f}; önbellek isabeti %{100 * cache / len(rows):.0f}', list(out[0]), out[:50],
                  {'is': len(out), 'toplam_usd': round(total, 2), 'buyuk_tarama': sum('1 TB' in o['uyari'] for o in out), 'onbellek_%': round(100 * cache / len(rows), 1)},
                  tables=[('Kullanıcı bazında', ['Kullanıcı', 'USD'], [[k, round(v, 2)] for k, v in sorted(users.items(), key=lambda kv: -kv[1])]),
                          ('Sorgu kalıbı bazında', ['Kalıp', 'İş', 'USD'], [[k, v[0], round(v[1], 2)] for k, v in sorted(patterns.items(), key=lambda kv: -kv[1][1])[:15]])],
                  notes=['Fiyat bölgeye ve sözleşmeye göre değişir; --price-per-tib ile güncelleyin. Kapasite (slot) fiyatlandırmasında bayt maliyeti geçerli değildir.'])


@op('bulut-maliyet-ozeti', title='Bulut faturası özeti: servis, etiket ve anormal günler', domain='finops', product=GENEL, tag='bulut maliyeti',
    samples={'bulut_maliyet.csv': 'bulut_maliyet.csv'},
    args=[arg('--date', default='tarih'), arg('--service', default='servis'), arg('--tag', default='etiket_proje'), arg('--cost', default='maliyet_usd'), arg('--z', type=float, default=3.0)],
    description='AWS CUR, Azure Cost Management veya GCP Billing dışa aktarımını servis ve etiket (proje/maliyet merkezi) bazında özetler; etiketsiz harcama oranını, günlük trendi ve servis bazında olağan dışı sıçrama yapan günleri (robust z-skoru) gösterir.',
    input='Maliyet CSV’si (tarih, servis, etiket, maliyet)', output='sonuc.csv (servis × etiket) + anormal günler', command='python main.py --service product_code --tag tag_project --cost unblended_cost',
    expect={'servis': 6, 'anormal_gun': 1})
def cloud(columns, rows, args):
    costs = numbers(rows, need(args.cost, columns, '--cost'))
    by = defaultdict(float)
    daily = defaultdict(lambda: defaultdict(float))
    untagged = 0.0
    for r, c in zip(rows, costs):
        c = c or 0.0
        s, t = r[need(args.service, columns, '--service')].strip(), r.get(args.tag, '').strip() if args.tag in columns else ''
        by[(s, t or '(etiketsiz)')] += c
        untagged += c if not t else 0
        d = tabular.parse_date(r[need(args.date, columns, '--date')])
        if d:
            daily[s][d[0].date()] += c
    total = sum(by.values())
    anomalies = []
    for s, series in daily.items():
        vals = sorted(series.values())
        med = vals[len(vals) // 2]
        mad = sorted(abs(v - med) for v in vals)[len(vals) // 2] or 1e-9
        for d, v in sorted(series.items()):
            z = 0.6745 * (v - med) / mad
            if z > args.z:
                anomalies.append([d.isoformat(), s, round(v, 2), round(med, 2), round(z, 1)])
    out = [{'servis': s, 'etiket': t, 'maliyet': round(v, 2), 'pay_%': round(100 * v / total, 2)} for (s, t), v in sorted(by.items(), key=lambda kv: -kv[1])]
    services = len({s for s, _ in by})
    return Result(f'Toplam {money(total)}; {services} servis; etiketsiz pay %{100 * untagged / total:.1f}; {len(anomalies)} anormal gün', list(out[0]), out,
                  {'toplam': round(total, 2), 'servis': services, 'etiketsiz_%': round(100 * untagged / total, 1), 'anormal_gun': len(anomalies)},
                  tables=[('Anormal günler', ['Tarih', 'Servis', 'Maliyet', 'Medyan', 'z'], anomalies)])


@op('snowflake-kredi-analizi', title='Snowflake kredi tüketimi ve boşta çalışan warehouse’lar', domain='finops', product='Account Usage', tag='kredi',
    samples={'warehouse_metering.csv': 'warehouse_metering.csv'}, args=[arg('--price', type=float, default=3.0, help='Kredi başı USD')],
    description='WAREHOUSE_METERING_HISTORY dışa aktarımından warehouse bazında kredi ve maliyeti, saat/gün dağılımını ve hafta sonu tüketimini çıkarır; iş saatleri dışında veya hafta sonunda kredi harcayan warehouse’ları otomatik askıya alma (auto-suspend) ve zamanlama için işaretler.',
    input='WAREHOUSE_METERING_HISTORY CSV’si', output='sonuc.csv (warehouse özeti) + saatlik dağılım', command='python main.py --price 3.0',
    expect={'warehouse': 3, 'hafta_sonu_tuketen': 2})
def credits(columns, rows, args):
    for c in ('start_time', 'warehouse_name', 'credits_used'):
        need(c, columns, 'METERING')
    cr = numbers(rows, 'credits_used')
    wh = defaultdict(lambda: {'kredi': 0.0, 'hafta_sonu': 0.0, 'mesai_disi': 0.0, 'saat': Counter()})
    for r, c in zip(rows, cr):
        t = when(r, 'start_time')
        if not t:
            continue
        w = wh[r['warehouse_name'].strip()]
        w['kredi'] += c or 0
        w['saat'][t.hour] += c or 0
        if t.weekday() >= 5:
            w['hafta_sonu'] += c or 0
        elif not 8 <= t.hour < 20:
            w['mesai_disi'] += c or 0
    out = []
    for name, w in sorted(wh.items()):
        flag = []
        if w['hafta_sonu'] > 0.1 * w['kredi']:
            flag.append('hafta sonu tüketimi yüksek — zamanlanmış işler mi, açık kalan oturum mu?')
        out.append({'warehouse': name, 'kredi': round(w['kredi'], 2), 'maliyet_usd': round(w['kredi'] * args.price, 2), 'hafta_sonu_%': round(100 * w['hafta_sonu'] / w['kredi'], 1) if w['kredi'] else 0,
                    'mesai_disi_%': round(100 * w['mesai_disi'] / w['kredi'], 1) if w['kredi'] else 0, 'en_yogun_saat': max(w['saat'], key=w['saat'].get) if w['saat'] else '', 'uyari': '; '.join(flag)})
    total = sum(o['kredi'] for o in out)
    return Result(f'{len(out)} warehouse, {total:.1f} kredi (${total * args.price:,.2f})', list(out[0]), out,
                  {'warehouse': len(out), 'kredi': round(total, 2), 'hafta_sonu_tuketen': sum(bool(o['uyari']) for o in out)},
                  notes=['ETL warehouse’larının gece ve hafta sonu çalışması beklenen olabilir; uyarıyı iş takvimiyle karşılaştırın.'])


@op('ay-sonu-maliyet-tahmini', title='Ay sonu bulut maliyet tahmini ve bütçe uyarısı', domain='finops', product=GENEL, tag='forecast',
    samples={'bulut_maliyet.csv': 'bulut_maliyet.csv'},
    args=[arg('--date', default='tarih'), arg('--cost', default='maliyet_usd'), arg('--group', default='servis'), arg('--budget', type=float, default=25000.0), arg('--as-of', help='Bu güne kadarki veriyi kullan')],
    description='Ayın gerçekleşen günlük maliyetinden son 7 günün ortalamasıyla ay sonu tahmini yapar; toplam ve grup (servis/proje) bazında tahmini, bütçeyle farkı ve bütçenin aşılacağı günü hesaplar. Bütçe alarmlarından önce erken uyarı için.',
    input='Günlük maliyet CSV’si + aylık bütçe', output='sonuc.csv (grup bazında tahmin)', command='python main.py --budget 30000 --as-of 2026-09-20',
    test_args=['--as-of', '2026-09-20', '--budget', '22000'], expect={'gun': 20, 'butce_asimi': True, 'asim_tarihi': '2026-09-28'})
def eom(columns, rows, args):
    costs = numbers(rows, need(args.cost, columns, '--cost'))
    cutoff = tabular.parse_date(args.as_of)[0].date() if args.as_of else None
    daily, groups = defaultdict(float), defaultdict(lambda: defaultdict(float))
    for r, c in zip(rows, costs):
        d = tabular.parse_date(r[need(args.date, columns, '--date')])
        if not d or (cutoff and d[0].date() > cutoff):
            continue
        daily[d[0].date()] += c or 0
        groups[r.get(args.group, '').strip() if args.group in columns else 'toplam'][d[0].date()] += c or 0
    if not daily:
        raise ValueError('Tarihi okunabilen maliyet satırı yok.')
    last = max(daily)
    month_days = calendar.monthrange(last.year, last.month)[1]
    mtd_days = [d for d in daily if d.year == last.year and d.month == last.month]
    remaining = month_days - last.day
    rate = lambda s: sum(s.get(last - timedelta(days=k), 0.0) for k in range(7)) / 7
    mtd = sum(daily[d] for d in mtd_days)
    fc = mtd + rate(daily) * remaining
    out = []
    for g, s in sorted(groups.items(), key=lambda kv: tr_sort_key(kv[0])):
        m = sum(v for d, v in s.items() if d.year == last.year and d.month == last.month)
        out.append({'grup': g, 'ay_basindan': round(m, 2), 'gunluk_hiz': round(rate(s), 2), 'ay_sonu_tahmini': round(m + rate(s) * remaining, 2)})
    over_day = None
    if fc > args.budget and rate(daily) > 0:
        over_day = last + timedelta(days=max(0, math.ceil((args.budget - mtd) / rate(daily))))
    return Result(f'{last.isoformat()} itibarıyla {money(mtd)}; ay sonu tahmini {money(fc)} (bütçe {money(args.budget)})' +
                  (f'; bütçe ≈ {over_day.isoformat()} günü aşılır' if over_day else ''), list(out[0]), out,
                  {'gun': len(mtd_days), 'ay_basindan': round(mtd, 2), 'ay_sonu_tahmini': round(fc, 2), 'butce_asimi': fc > args.budget,
                   'asim_tarihi': over_day.isoformat() if over_day else ''})
