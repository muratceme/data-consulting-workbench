"""Veri ambarı tasarım ve depolama blokları: Redshift tablo tasarımı (dağıtım, sıralama, sıkıştırma) ve Snowflake depolama (Time Travel, Fail-safe, klon).
Ambara bağlanmaz; sistem görünümlerinin dışa aktarımlarını okur. Fiyatlar parametredir."""
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
