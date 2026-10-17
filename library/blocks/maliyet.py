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


@op('etiket-uyumu', title='Bulut kaynaklarında zorunlu etiket (tag) uyumu', domain='finops', product=GENEL, tag='etiket politikası', samples={'kaynak_etiketleri.csv': 'kaynak_etiketleri.csv'},
    args=[arg('--resource', default='kaynak_id'), arg('--cost', default='maliyet_usd'), arg('--prefix', default='tag_', help='Etiket sütunlarının öneki'),
          arg('--required', default='owner,cost_center,env', help='Zorunlu etiketler'), arg('--allowed', default='env=prod|test|dev', help='etiket=değer1|değer2;… izin verilen değerler')],
    description='Maliyet veya kaynak envanteri dışa aktarımında her kaynağın zorunlu etiketlerini (sahip, maliyet merkezi, ortam…) kontrol eder; eksik ve izin verilen listede olmayan (Prod/prod gibi) değerleri, uyumsuz kaynakların maliyetini ve toplam içindeki payını raporlar. Showback/chargeback öncesi etiket temizliği için.',
    input='Kaynak × etiket × maliyet CSV’si (AWS CUR, Azure, GCP dışa aktarımı)', output='sonuc.csv (uyumsuz kaynaklar)', command='python main.py --input cur.csv --resource line_item_resource_id --prefix resource_tags_user_ --cost line_item_unblended_cost',
    expect={'kaynak_sayisi': 10, 'uyumsuz': 5, 'uyumsuz_maliyet_%': 65.7, 'gecersiz_deger': 2})
def tags(columns, rows, args):
    rcol = need(args.resource, columns, '--resource')
    required = [t.strip() for t in args.required.split(',') if t.strip()]
    tcols = {t: need(args.prefix + t, columns, '--required') for t in required}
    allowed = {}
    for part in (p for p in args.allowed.split(';') if p.strip()):
        k, _, v = part.partition('=')
        allowed[k.strip()] = set(x.strip() for x in v.split('|'))
    costs = numbers(rows, need(args.cost, columns, '--cost'))
    res = {}
    for r, c in zip(rows, costs):
        k = r[rcol].strip()
        e = res.setdefault(k, {'cost': 0.0, 'tags': {t: '' for t in required}})
        e['cost'] += c or 0.0
        for t in required:
            e['tags'][t] = e['tags'][t] or r[tcols[t]].strip()
    total = sum(e['cost'] for e in res.values()) or 1.0
    out, missing, invalid = [], Counter(), 0
    for k, e in sorted(res.items(), key=lambda kv: -kv[1]['cost']):
        gone = [t for t in required if not e['tags'][t]]
        bad = [f'{t}={e["tags"][t]}' for t in required if e['tags'][t] and t in allowed and e['tags'][t] not in allowed[t]]
        missing.update(gone)
        invalid += len(bad)
        if gone or bad:
            out.append({'kaynak': k, 'maliyet': round(e['cost'], 2), 'eksik_etiket': ', '.join(gone), 'gecersiz_deger': ', '.join(bad), **{t: e['tags'][t] for t in required}})
    bad_cost = sum(o['maliyet'] for o in out)
    return Result(f'{len(res)} kaynak; {len(out)} uyumsuz, maliyetin %{100 * bad_cost / total:.1f}’i etiket politikası dışında', ['kaynak', 'maliyet', 'eksik_etiket', 'gecersiz_deger', *required], out,
                  {'kaynak_sayisi': len(res), 'uyumsuz': len(out), 'uyumsuz_maliyet': round(bad_cost, 2), 'uyumsuz_maliyet_%': round(100 * bad_cost / total, 1), 'gecersiz_deger': invalid,
                   'eksik': dict(missing)}, tables=[('Eksik etiketler', ['Etiket', 'Kaynak'], [[t, missing[t]] for t in required])])


@op('rezervasyon-onerisi', title='Saatlik kullanımdan taahhüt (reserved/savings plan) önerisi', domain='finops', product=GENEL, tag='commitment',
    samples={'saatlik_kullanim.csv': 'saatlik_kullanim.csv'},
    args=[arg('--usage', default='vcpu', help='Saatlik kullanım sütunu (vCPU, örnek sayısı, kredi…)'), arg('--rate', type=float, default=0.10, help='Birim saat isteğe bağlı (on-demand) fiyat'),
          arg('--discount', type=float, default=0.40, help='Taahhüt indirimi (0,40 = yüzde 40)')],
    description='Saatlik kullanım serisinde her olası taahhüt düzeyinin maliyetini hesaplar (taahhüt edilen kısım her saat indirimli ödenir, aşan kısım isteğe bağlı fiyattan): en düşük toplam maliyeti veren düzeyi, kapsama ve taahhüt kullanım oranını, isteğe bağlıya göre tasarrufu verir. Fiyat ve indirim parametredir; sözleşme koşullarınızla çalıştırın.',
    input='Saatlik kullanım CSV’si (saat, kullanım)', output='sonuc.csv (taahhüt düzeyi × maliyet)', command='python main.py --usage instances --rate 0.192 --discount 0.37',
    expect={'saat': 336, 'onerilen_taahhut': 10, 'tasarruf_%': 24.2})
def commit(columns, rows, args):
    use = [u or 0.0 for u in numbers(rows, need(args.usage, columns, '--usage'))]
    if not use:
        raise ValueError('Kullanım satırı yok.')
    price = args.rate * (1 - args.discount)
    on_demand = sum(use) * args.rate
    out = []
    for c in sorted({0.0, *use}):
        level = int(c) if float(c).is_integer() else c
        over = sum(max(0.0, u - c) for u in use)
        cost = c * price * len(use) + over * args.rate
        used = sum(min(u, c) for u in use)
        out.append({'taahhut': level, 'maliyet': round(cost, 2), 'tasarruf': round(on_demand - cost, 2), 'tasarruf_%': round(100 * (on_demand - cost) / on_demand, 1) if on_demand else 0,
                    'kapsama_%': round(100 * used / sum(use), 1) if sum(use) else 0, 'taahhut_kullanim_%': round(100 * used / (c * len(use)), 1) if c else ''})
    best = min(out, key=lambda o: o['maliyet'])
    for o in out:
        o['oneri'] = 'ÖNERİLEN' if o is best else ''
    return Result(f'{len(use)} saat; önerilen taahhüt {best["taahhut"]} birim, tasarruf %{best["tasarruf_%"]} ({best["tasarruf"]:,.2f})', list(out[0]), out,
                  {'saat': len(use), 'istege_bagli_maliyet': round(on_demand, 2), 'onerilen_taahhut': best['taahhut'],
                   'tasarruf_%': best['tasarruf_%'], 'tasarruf': best['tasarruf']},
                  notes=['Analiz geçmiş kullanımın gelecekte süreceğini varsayar; büyüme veya taşınma planı varsa taahhüdü düşük tutun.',
                         f'Bir birim taahhüt, kullanım o düzeyin üzerinde olduğu saatlerin oranı %{100 * (1 - args.discount):.0f}’den fazlaysa kârlıdır.'])


@op('kaynak-boyutlandirma', title='Boşta ve gereğinden büyük sanal makineleri bul (rightsizing)', domain='finops', product=GENEL, tag='rightsizing',
    samples={'kaynak_kullanim.csv': 'kaynak_kullanim.csv'},
    args=[arg('--idle-p95', type=float, default=5.0, help='p95 CPU bu değerin altındaysa boşta'), arg('--small-p95', type=float, default=40.0, help='p95 CPU bu değerin altındaysa küçültülebilir'),
          arg('--mem', type=float, default=50.0, help='Bellek kullanımı bu değerin altında olmalı')],
    description='Sanal makine/örnek kullanım özetinden (vCPU, ortalama ve p95 CPU, bellek, aylık maliyet) boşta duran kaynakları (kapat/durdur) ve CPU ile belleği birlikte düşük kullanılanları (bir boy küçült) bulur; tahmini aylık tasarrufu hesaplar. Bellek yoğun ve en küçük boydaki kaynakları küçültme önerisinden çıkarır.',
    input='Kaynak kullanım CSV’si (kaynak, vcpu, ort/p95 CPU %, bellek %, aylık maliyet)', output='sonuc.csv (öneriler)', command='python main.py --input azure_advisor_export.csv --small-p95 30',
    expect={'kaynak_sayisi': 8, 'bosta': 2, 'kucultulebilir': 2, 'tahmini_tasarruf': 1800})
def rightsize(columns, rows, args):
    for c in ('kaynak_id', 'vcpu', 'p95_cpu', 'ort_bellek', 'aylik_maliyet'):
        need(c, columns, 'kullanım dosyası')
    vcpu, p95, mem, cost = (numbers(rows, c) for c in ('vcpu', 'p95_cpu', 'ort_bellek', 'aylik_maliyet'))
    out = []
    for r, v, p, m, c in zip(rows, vcpu, p95, mem, cost):
        c = c or 0.0
        if p is not None and p < args.idle_p95:
            act, save = 'BOŞTA: kapat veya zamanla durdur', c
        elif p is not None and p < args.small_p95 and (m is None or m < args.mem) and (v or 0) > 1:
            act, save = f'KÜÇÜLT: {fmt(v)} → {fmt(v / 2)} vCPU', c / 2
        else:
            act, save = 'uygun' if not (p is not None and p < args.small_p95) else ('uygun (bellek yoğun)' if m is not None and m >= args.mem else 'uygun (en küçük boy)'), 0.0
        out.append({'kaynak_id': r['kaynak_id'], 'tip': r.get('tip', ''), 'vcpu': fmt(v), 'p95_cpu': fmt(p), 'ort_bellek': fmt(m), 'aylik_maliyet': round(c, 2), 'oneri': act, 'tahmini_tasarruf': round(save, 2)})
    out.sort(key=lambda o: -o['tahmini_tasarruf'])
    saving = sum(o['tahmini_tasarruf'] for o in out)
    return Result(f'{len(out)} kaynak; aylık tahmini tasarruf {money(saving)} (toplamın %{100 * saving / (sum(o["aylik_maliyet"] for o in out) or 1):.0f}’i)', list(out[0]), out,
                  {'kaynak_sayisi': len(out), 'bosta': sum(o['oneri'].startswith('BOŞTA') for o in out), 'kucultulebilir': sum(o['oneri'].startswith('KÜÇÜLT') for o in out),
                   'tahmini_tasarruf': round(saving, 2)},
                  notes=['Bir boy küçültmenin maliyeti yarıya indirdiği varsayılır; gerçek fiyatı hedef boyun liste fiyatıyla kontrol edin.',
                         'En az 14–30 günlük kullanım verisi kullanın; ay sonu kapanışı gibi dönemsel yükleri kaçırmayın.'])


@op('maliyet-paylastir', title='Ortak/etiketsiz maliyeti projelere dağıt (showback)', domain='finops', product=GENEL, tag='showback', samples={'bulut_maliyet.csv': 'bulut_maliyet.csv'},
    args=[arg('--tag', default='etiket_proje'), arg('--cost', default='maliyet_usd'), arg('--method', choices=['oransal', 'esit'], default='oransal'),
          arg('--shared', default='', help='Ortak sayılacak etiket değerleri (virgülle, ör. platform,ortak)')],
    description='Etiketsiz ve ortak (platform, ağ, güvenlik) maliyetleri, projelerin doğrudan maliyetleri oranında veya eşit olarak dağıtır; proje bazında doğrudan + dağıtılan = toplam maliyeti ve payını üretir. Aylık showback/chargeback raporu için.',
    input='Maliyet CSV’si (etiket, maliyet)', output='sonuc.csv (proje bazında dağıtılmış maliyet)', command='python main.py --tag tag_project --shared platform,network --method oransal',
    expect={'proje': 2, 'paylastirilan': 3517.21, 'toplam': 23548.47})
def showback(columns, rows, args):
    tcol = need(args.tag, columns, '--tag')
    shared_tags = {s.strip().lower() for s in args.shared.split(',') if s.strip()}
    direct, shared = defaultdict(float), 0.0
    for r, c in zip(rows, numbers(rows, need(args.cost, columns, '--cost'))):
        t = r[tcol].strip()
        if not t or t.lower() in shared_tags:
            shared += c or 0.0
        else:
            direct[t] += c or 0.0
    if not direct:
        raise ValueError('Etiketli (doğrudan) maliyet yok; dağıtım yapılamaz.')
    base = sum(direct.values())
    out = []
    for t, v in sorted(direct.items(), key=lambda kv: -kv[1]):
        add = shared * (v / base if args.method == 'oransal' else 1 / len(direct))
        out.append({'proje': t, 'dogrudan': round(v, 2), 'paylastirilan': round(add, 2), 'toplam': round(v + add, 2), 'pay_%': round(100 * (v + add) / (base + shared), 2)})
    return Result(f'{len(out)} proje; {money(shared)} ortak/etiketsiz maliyet {args.method} dağıtıldı', list(out[0]), out,
                  {'proje': len(out), 'dogrudan': round(base, 2), 'paylastirilan': round(shared, 2), 'toplam': round(base + shared, 2)},
                  notes=['Oransal dağıtım, ortak maliyeti en çok harcayan projeye en çok yükler; ağ/güvenlik gibi kalemler için kullanım ölçüsü varsa onu tercih edin.'])


@op('birim-maliyet', title='Birim maliyet: işlem/müşteri başına bulut maliyeti trendi', domain='finops', product=GENEL, tag='unit economics',
    samples={'aylik_maliyet.csv': 'aylik_maliyet.csv', 'is_metrikleri.csv': 'is_metrikleri.csv'},
    args=[arg('--metrics', default='@ornek/is_metrikleri.csv', help='Aylık iş metriği (ay, metrik)'), arg('--metric', default='siparis'), arg('--month', default='ay'),
          arg('--cost', default='maliyet'), arg('--alert', type=float, default=10.0, help='Önceki aya göre bu yüzdeden fazla artışta uyar')],
    description='Aylık bulut maliyetini sipariş, aktif müşteri veya API çağrısı gibi bir iş metriğine böler; birim maliyetin aylık değişimini, maliyet ve hacim büyümesini yan yana gösterir ve birim maliyetin arttığı ayları işaretler. Toplam fatura yükselirken verimliliğin korunup korunmadığını göstermek için.',
    input='Aylık maliyet (ay, maliyet) + aylık iş metriği (ay, metrik)', output='sonuc.csv (ay bazında birim maliyet)', command='python main.py --input cost_by_month.csv --metrics active_users.csv --metric aktif_kullanici',
    expect={'ay': 4, 'son_birim_maliyet': 0.25, 'son_degisim_%': 25.0, 'uyari_ay': 1})
def unit_cost(columns, rows, args):
    mcol = need(args.month, columns, '--month')
    cost = defaultdict(float)
    for r, c in zip(rows, numbers(rows, need(args.cost, columns, '--cost'))):
        cost[r[mcol].strip()[:7]] += c or 0.0
    kcols, krows = tabular.read(args.metrics)
    metric = {r[need(args.month, kcols, '--metrics')].strip()[:7]: v for r, v in zip(krows, numbers(krows, need(args.metric, kcols, '--metric'))) if v}
    out, prev = [], None
    for m in sorted(cost):
        if m not in metric:
            out.append({'ay': m, 'maliyet': round(cost[m], 2), 'metrik': '', 'birim_maliyet': '', 'degisim_%': '', 'maliyet_buyume_%': '', 'hacim_buyume_%': '', 'uyari': 'metrik yok'})
            continue
        unit = cost[m] / metric[m]
        row = {'ay': m, 'maliyet': round(cost[m], 2), 'metrik': fmt(metric[m]), 'birim_maliyet': round(unit, 6), 'degisim_%': '', 'maliyet_buyume_%': '', 'hacim_buyume_%': '', 'uyari': ''}
        if prev:
            pm, pu = prev
            row['degisim_%'] = round(100 * (unit / pu - 1), 1)
            row['maliyet_buyume_%'] = round(100 * (cost[m] / cost[pm] - 1), 1)
            row['hacim_buyume_%'] = round(100 * (metric[m] / metric[pm] - 1), 1)
            if row['degisim_%'] > args.alert:
                row['uyari'] = 'birim maliyet arttı: maliyet hacimden hızlı büyüyor'
        prev = (m, unit)
        out.append(row)
    last = next((o for o in reversed(out) if o['birim_maliyet'] != ''), None)
    return Result(f'{len(out)} ay; son birim maliyet {last["birim_maliyet"] if last else "-"} ({last["degisim_%"] if last else ""}%)', list(out[0]), out,
                  {'ay': len(out), 'son_birim_maliyet': last['birim_maliyet'] if last else '', 'son_degisim_%': last['degisim_%'] if last else '',
                   'uyari_ay': sum(bool(o['uyari']) and o['uyari'] != 'metrik yok' for o in out)})


@op('depolama-katman-onerisi', title='Nesne depolamada erişim sıklığına göre sınıf (tier) önerisi', domain='finops', product=GENEL, tag='storage tier',
    samples={'depolama_envanteri.csv': 'depolama_envanteri.csv'},
    args=[arg('--prices', default='standard=0.023,ia=0.0125,archive=0.004', help='Sınıf=GB-ay fiyatı'), arg('--ia-days', type=int, default=30), arg('--archive-days', type=int, default=180)],
    description='S3/Blob/GCS envanterinde önek (prefix) bazında boyut, son erişimden beri geçen gün ve mevcut depolama sınıfını okur; uzun süredir erişilmeyen verileri seyrek erişim veya arşiv sınıfına taşıma önerisini ve aylık tasarrufu fiyat parametreleriyle hesaplar. Yaşam döngüsü (lifecycle) kuralı yazmadan önce etkiyi görmek için.',
    input='Depolama envanteri (kova, önek, boyut GB, son erişim gün, sınıf)', output='sonuc.csv (önek bazında öneri)', command='python main.py --input s3_inventory_summary.csv --prices "standard=0.0245,ia=0.0135,archive=0.0045"',
    expect={'onek': 5, 'oneri': 3, 'aylik_tasarruf': 22.55, 'toplam_gb': 2100.0})
def tiering(columns, rows, args):
    for c in ('onek', 'boyut_gb', 'son_erisim_gun', 'sinif'):
        need(c, columns, 'envanter')
    prices = {}
    for part in (p for p in args.prices.split(',') if '=' in p):
        k, v = part.split('=', 1)
        prices[k.strip().lower()] = float(v)
    for k in ('standard', 'ia', 'archive'):
        if k not in prices:
            raise ValueError(f'--prices içinde "{k}" fiyatı yok.')
    rank = {'standard': 0, 'ia': 1, 'archive': 2}
    size, age = numbers(rows, 'boyut_gb'), numbers(rows, 'son_erisim_gun')
    out = []
    for r, s, a in zip(rows, size, age):
        cur = r['sinif'].strip().lower()
        want = 'archive' if (a or 0) > args.archive_days else 'ia' if (a or 0) > args.ia_days else 'standard'
        move = cur in rank and rank[want] > rank[cur]
        save = (s or 0) * (prices[cur] - prices[want]) if move else 0.0
        out.append({'kova': r.get('kova', ''), 'onek': r['onek'], 'boyut_gb': fmt(s), 'son_erisim_gun': fmt(a), 'mevcut': cur, 'onerilen': want if move else cur,
                    'aylik_tasarruf': round(save, 2), 'not': 'arşivden geri okuma ücretli ve gecikmeli' if move and want == 'archive' else ''})
    out.sort(key=lambda o: -o['aylik_tasarruf'])
    total = sum(o['aylik_tasarruf'] for o in out)
    return Result(f'{len(out)} önek; {sum(o["aylik_tasarruf"] > 0 for o in out)} taşıma önerisi, aylık tasarruf {money(total)}', list(out[0]), out,
                  {'onek': len(out), 'oneri': sum(o['aylik_tasarruf'] > 0 for o in out), 'aylik_tasarruf': round(total, 2), 'toplam_gb': round(sum(s or 0 for s in size), 2)},
                  notes=['Seyrek erişim ve arşiv sınıflarında asgari saklama süresi ve okuma ücreti vardır; sık okunan önekleri taşımayın.'])


@op('atil-kaynaklar', title='Son günlerde kullanılmadığı hâlde ücret yazan (atıl) bulut kaynaklarını bul', domain='finops', product=GENEL, tag='atıl kaynak',
    samples={'kaynak_gunluk.csv': 'kaynak_gunluk.csv'},
    args=[arg('--resource', default='kaynak'), arg('--date', default='tarih'), arg('--cost', default='maliyet'), arg('--usage', default='kullanim', help='CPU yüzdesi, istek sayısı, okuma vb.'),
          arg('--days', type=int, default=7, help='Dosyadaki son tarihten geriye bakılacak gün'), arg('--max-usage', type=float, default=2.0, help='Bu değere kadar kullanım atıl sayılır')],
    description='Kaynak bazında günlük maliyet ve kullanım dökümünde son N günde kullanımı hiç eşiği geçmeyen ama maliyet yazan kaynakları (kapatılmamış test makineleri, bağlı olmayan diskler, boşta yük dengeleyiciler) bulur; pencere içindeki maliyeti ve günlük ortalamadan aylık tasarruf tahminini verir.',
    input='Günlük kaynak maliyeti ve kullanımı (kaynak, tarih, maliyet, kullanım)', output='sonuc.csv (kaynak bazında durum)', command='python main.py --input cur_daily.csv --days 14 --max-usage 1',
    expect={'kaynak_sayisi': 5, 'atil_kaynak': 3, 'aylik_tasarruf': 1410.0})
def idle(columns, rows, args):
    rc, dc = need(args.resource, columns, '--resource'), need(args.date, columns, '--date')
    cost, use = numbers(rows, need(args.cost, columns, '--cost')), numbers(rows, need(args.usage, columns, '--usage'))
    pts = [(r[rc].strip(), when(r, dc), c or 0.0, u) for r, c, u in zip(rows, cost, use) if r[rc].strip() and when(r, dc)]
    if not pts:
        raise ValueError('Tarihi okunabilen satır yok.')
    last = max(p[1] for p in pts)
    start = last - timedelta(days=args.days - 1)
    win = defaultdict(list)
    for name, d, c, u in pts:
        if d >= start:
            win[name].append((d, c, u))
    out = []
    for name, vals in win.items():
        total = sum(c for _, c, _ in vals)
        mx = max((u for _, _, u in vals if u is not None), default=None)
        days = len({d for d, _, _ in vals})
        is_idle = total > 0 and (mx is None or mx <= args.max_usage)
        out.append({'kaynak': name, 'gun': days, 'pencere_maliyeti': round(total, 2), 'en_yuksek_kullanim': fmt(mx),
                    'aylik_tahmin': round(total / days * 30, 2) if is_idle else 0.0, 'durum': 'atıl' if is_idle else 'kullanılıyor'})
    out.sort(key=lambda o: (o['durum'] != 'atıl', -o['aylik_tahmin'], o['kaynak']))
    idle_n = sum(o['durum'] == 'atıl' for o in out)
    saving = round(sum(o['aylik_tahmin'] for o in out), 2)
    return Result(f'{len(out)} kaynak; {idle_n} kaynak son {args.days} günde atıl, aylık tasarruf tahmini {money(saving)}', list(out[0]), out,
                  {'kaynak_sayisi': len(out), 'atil_kaynak': idle_n, 'aylik_tasarruf': saving, 'pencere_baslangic': start.strftime('%Y-%m-%d'), 'pencere_bitis': last.strftime('%Y-%m-%d')},
                  notes=['Aylık tahmin = penceredeki günlük ortalama maliyet × 30. Yedek, felaket kurtarma ve aylık çalışan işler düşük kullanımlı görünebilir; kapatmadan önce sahibine sorun.'])


@op('lisans-kullanimi', title='Kullanılmayan BI/SaaS lisanslarını ve aylık israfı bul', domain='finops', product=GENEL, tag='lisans israfı', samples={'lisanslar.csv': 'lisanslar.csv'},
    args=[arg('--user', default='kullanici'), arg('--product', default='urun'), arg('--license', default='lisans_turu'), arg('--price', default='aylik_ucret'),
          arg('--last-login', default='son_giris'), arg('--as-of', default='2026-10-16'), arg('--days', type=int, default=90, help='Bu kadar gündür giriş yapmayan lisans atıl sayılır')],
    description='Power BI, Tableau, Qlik veya başka bir SaaS aracının kullanıcı-lisans listesinden son giriş tarihine bakarak N gündür kullanılmayan ve hiç giriş yapılmamış lisansları bulur; ürün ve lisans türü bazında aylık/yıllık israfı hesaplar. Lisans yenileme ve gerçek ihtiyaç kadar satın alma kararı için.',
    input='Lisans listesi (kullanıcı, ürün, lisans türü, aylık ücret, son giriş)', output='sonuc.csv (atıl lisanslar) + ürün bazında özet', command='python main.py --input pbi_users.csv --as-of 2026-10-31 --days 60',
    expect={'lisans': 10, 'atil_lisans': 5, 'hic_giris': 1, 'aylik_israf': 120.0, 'yillik_israf': 1440.0})
def licenses(columns, rows, args):
    uc, pc, lc, lg = (need(c, columns, f) for c, f in ((args.user, '--user'), (args.product, '--product'), (args.license, '--license'), (args.last_login, '--last-login')))
    price = numbers(rows, need(args.price, columns, '--price'))
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    if not as_of:
        raise ValueError(f'--as-of "{args.as_of}" tarih olarak okunamadı.')
    out, by_product = [], defaultdict(lambda: [0, 0, 0.0])
    for r, pr in zip(rows, price):
        d = tabular.parse_date(r[lg]) if r[lg].strip() else None
        days = (as_of - d[0]).days if d else None
        idle = days is None or days > args.days
        key = (r[pc].strip(), r[lc].strip())
        by_product[key][0] += 1
        if idle:
            by_product[key][1] += 1
            by_product[key][2] += pr or 0.0
            out.append({'kullanici': r[uc].strip(), 'urun': key[0], 'lisans_turu': key[1], 'aylik_ucret': pr, 'son_giris': d[0].strftime('%Y-%m-%d') if d else '',
                        'gun': days if days is not None else '', 'durum': 'hiç giriş yok' if d is None else f'{days} gündür giriş yok'})
    out.sort(key=lambda o: (-(o['aylik_ucret'] or 0), o['urun'], o['kullanici']))
    waste = round(sum(o['aylik_ucret'] or 0 for o in out), 2)
    return Result(f'{len(rows)} lisansın {len(out)} tanesi {args.days} gündür kullanılmıyor; aylık israf {money(waste)}',
                  ['kullanici', 'urun', 'lisans_turu', 'aylik_ucret', 'son_giris', 'gun', 'durum'], out,
                  {'lisans': len(rows), 'atil_lisans': len(out), 'hic_giris': sum(o['durum'] == 'hiç giriş yok' for o in out), 'aylik_israf': waste, 'yillik_israf': round(waste * 12, 2)},
                  tables=[('Ürün / lisans türü', ['Ürün', 'Lisans', 'Toplam', 'Atıl', 'Aylık israf'], [[k[0], k[1], v[0], v[1], money(v[2])] for k, v in sorted(by_product.items())])],
                  notes=['Ayrılan çalışanların lisansları da bu listede atıl görünür; İK listesiyle karşılaştırıp önce onları geri alın. Paylaşılan/hizmet hesaplarını ayrıca değerlendirin.'])


EGRESS_INTERNET = re.compile(r'^(internet|public|dış|dis|external|0\.0\.0\.0/0)$', re.I)


@op('egress-analizi', title='Bulut veri transferi (egress) maliyetini yol ve türe göre ayır', domain='finops', product=GENEL, tag='egress',
    samples={'veri_transferi.csv': 'veri_transferi.csv'},
    args=[arg('--source', default='kaynak_bolge'), arg('--target', default='hedef_bolge', help='Hedef bölge veya "internet"'), arg('--gb', default='gb'),
          arg('--price-internet', type=float, default=0.09, help='İnternete çıkış GB fiyatı'), arg('--price-inter', type=float, default=0.02, help='Bölgeler arası GB fiyatı'),
          arg('--price-intra', type=float, default=0.01, help='Aynı bölge içi (bölgeler/AZ arası) GB fiyatı')],
    description='Veri transferi kayıtlarını (kaynak bölge, hedef bölge veya internet, GB) internete çıkış, bölgeler arası ve bölge içi olarak sınıflar; verilen birim fiyatlarla yol bazında maliyeti, payını ve en pahalı yolları hesaplar. Çok bölgeli replikasyon, BI aracının bulut dışından veri çekmesi ve CDN kararları için.',
    input='Transfer dökümü (kaynak bölge, hedef bölge/internet, GB)', output='sonuc.csv (yol bazında GB ve maliyet)', command='python main.py --input vpc_flow_ozet.csv --price-internet 0.085',
    expect={'yol': 4, 'toplam_gb': 5600.0, 'toplam_maliyet': 124.0, 'internet_maliyet_%': 43.55})
def egress(columns, rows, args):
    s, t = need(args.source, columns, '--source'), need(args.target, columns, '--target')
    gbs = numbers(rows, need(args.gb, columns, '--gb'))
    paths = defaultdict(float)
    for r, g in zip(rows, gbs):
        if g is not None and r[s].strip():
            paths[(r[s].strip(), r[t].strip() or 'internet')] += g
    if not paths:
        raise ValueError('GB değeri okunabilen satır yok.')
    out = []
    for (a, b), g in paths.items():
        kind = 'internet' if EGRESS_INTERNET.match(b) else 'bölge içi' if a.lower() == b.lower() else 'bölgeler arası'
        price = {'internet': args.price_internet, 'bölge içi': args.price_intra, 'bölgeler arası': args.price_inter}[kind]
        out.append({'kaynak': a, 'hedef': b, 'tur': kind, 'gb': round(g, 2), 'birim_fiyat': price, 'maliyet': round(g * price, 2)})
    total = sum(o['maliyet'] for o in out)
    for o in out:
        o['pay_%'] = round(100 * o['maliyet'] / total, 2) if total else 0
    out.sort(key=lambda o: -o['maliyet'])
    by_kind = defaultdict(lambda: [0.0, 0.0])
    for o in out:
        by_kind[o['tur']][0] += o['gb']
        by_kind[o['tur']][1] += o['maliyet']
    return Result(f'{len(out)} transfer yolu, {fmt(round(sum(o["gb"] for o in out), 2))} GB, maliyet {money(total)}; en pahalı yol {out[0]["kaynak"]} → {out[0]["hedef"]}',
                  ['kaynak', 'hedef', 'tur', 'gb', 'birim_fiyat', 'maliyet', 'pay_%'], out,
                  {'yol': len(out), 'toplam_gb': round(sum(o['gb'] for o in out), 2), 'toplam_maliyet': round(total, 2),
                   'internet_maliyet_%': round(100 * by_kind['internet'][1] / total, 2) if total else 0},
                  tables=[('Türe göre', ['Tür', 'GB', 'Maliyet'], [[k, round(v[0], 2), money(v[1])] for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1][1])])],
                  notes=['Varsayılan fiyatlar örnektir; sağlayıcınızın güncel fiyat listesini ve kademeli indirimleri --price-* ile girin. Ücretsiz ilk GB’lar ve özel bağlantı (Direct Connect, ExpressRoute) fiyatları hesaba katılmaz.'])
