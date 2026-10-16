"""Veri gözlemlenebilirliği blokları: tazelik SLA, hacim anomalisi, şema değişikliği, NULL oranı, iş çalışma geçmişi ve log hata özeti.
Metadata dışa aktarımları ve log dosyaları üzerinde çalışır."""
import re
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime
import tabular
from islem import op, arg, Result, need, numbers, fmt

D, P = 'observability', 'Tablo yükleme metrikleri'


def robust(vals):
    s = sorted(vals)
    med = s[len(s) // 2]
    mad = sorted(abs(v - med) for v in s)[len(s) // 2]
    return med, mad or (abs(med) * 0.01 or 1.0)


@op('tazelik-sla', title='Tablo tazeliği (freshness) SLA kontrolü', domain=D, product=P, tag='freshness', samples={'tablo_yuklemeleri.csv': 'tablo_yuklemeleri.csv'},
    args=[arg('--table', default='tablo'), arg('--loaded', default='son_yukleme'), arg('--sla', default='sla_saat'), arg('--as-of', default='2026-10-16 12:00'), arg('--strict', action='store_true')],
    description='Her tablonun son yükleme zamanını tanımlı SLA süresiyle (saat) karşılaştırır; gecikmiş, hiç yüklenmemiş ve SLA’sı dolmak üzere olan tabloları gecikme süresiyle listeler. information_schema/metadata sorgusu çıktısı veya orkestratör kaydıyla çalışır; CI/zamanlanmış görevde --strict ile hata kodu döner.',
    input='Tablo listesi (tablo, son yükleme, SLA saat)', output='sonuc.csv', command='python main.py --as-of "2026-10-16 09:00" --strict',
    expect={'tablo': 7, 'gecikmis': 2, 'hic_yuklenmemis': 1})
def fresh(columns, rows, args):
    as_of = tabular.parse_date(args.as_of)[0]
    slas = numbers(rows, need(args.sla, columns, '--sla'))
    out = []
    for r, sla in zip(rows, slas):
        d = tabular.parse_date(r[need(args.loaded, columns, '--loaded')])
        if not d:
            out.append({'tablo': r[need(args.table, columns, '--table')], 'son_yukleme': '', 'yas_saat': '', 'sla_saat': sla, 'durum': 'hiç yüklenmemiş / bilinmiyor'})
            continue
        age = (as_of - d[0]).total_seconds() / 3600
        state = 'GECİKMİŞ' if sla and age > sla else 'riskte' if sla and age > 0.8 * sla else 'güncel'
        out.append({'tablo': r[args.table], 'son_yukleme': d[0].isoformat(sep=' '), 'yas_saat': round(age, 1), 'sla_saat': sla, 'durum': state})
    order = {'GECİKMİŞ': 0, 'hiç yüklenmemiş / bilinmiyor': 1, 'riskte': 2, 'güncel': 3}
    out.sort(key=lambda o: order[o['durum']])
    late = sum(o['durum'] == 'GECİKMİŞ' for o in out)
    never = sum(o['durum'].startswith('hiç') for o in out)
    return Result(f'{len(out)} tablo; {late} gecikmiş, {never} bilinmiyor, {sum(o["durum"] == "riskte" for o in out)} riskte', list(out[0]), out,
                  {'tablo': len(out), 'gecikmis': late, 'hic_yuklenmemis': never}, code=2 if args.strict and (late or never) else 0)


@op('hacim-anomalisi', title='Günlük satır sayısında anomali (eksik/fazla yükleme)', domain=D, product=P, tag='hacim', samples={'gunluk_satir_sayilari.csv': 'gunluk_satir_sayilari.csv'},
    args=[arg('--date', default='tarih'), arg('--table', default='tablo'), arg('--count', default='satir'), arg('--z', type=float, default=4.0), arg('--min-pct', type=float, default=20.0, help='En az bu yüzde sapma'), arg('--last', type=int, default=7, help='Son kaç gün değerlendirilsin')],
    description='Tablo başına günlük satır sayılarını haftanın aynı günlerinin medyanına ve medyan mutlak sapmaya göre değerlendirir; sıfır yüklemeleri, beklenenden belirgin düşük/yüksek günleri ve yüzde sapmayı listeler. Kaynak sistem kesintisi ve tekrar yükleme (çift kayıt) tespiti için.',
    input='Günlük satır sayıları (tarih, tablo, satır)', output='sonuc.csv (anomaliler)', command='python main.py --z 3.5 --last 14',
    expect={'tablo': 3, 'anomali': 2, 'sifir_yukleme': 1})
def volume(columns, rows, args):
    vals = numbers(rows, need(args.count, columns, '--count'))
    series = defaultdict(list)
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[need(args.date, columns, '--date')])
        if d and v is not None:
            series[r[need(args.table, columns, '--table')]].append((d[0].date(), v))
    out = []
    for t, pts in sorted(series.items()):
        pts.sort()
        for d, v in pts[-args.last:]:
            same = [x for dd, x in pts if dd < d and dd.weekday() == d.weekday()] or [x for dd, x in pts if dd < d]
            if len(same) < 2:
                continue
            med, mad = robust(same)
            z = 0.6745 * (v - med) / mad
            pct = abs(v - med) / med * 100 if med else 100.0
            # With only a few same-weekday points the MAD is tiny, so a material % change is required too.
            if v == 0 or (abs(z) > args.z and pct >= args.min_pct):
                out.append({'tablo': t, 'tarih': d.isoformat(), 'satir': int(v), 'beklenen': int(med), 'sapma_%': round(100 * (v - med) / med, 1) if med else '',
                            'durum': 'SIFIR YÜKLEME' if v == 0 else ('düşük' if z < 0 else 'yüksek')})
    return Result(f'{len(series)} tablo; son {args.last} günde {len(out)} anomali', ['tablo', 'tarih', 'satir', 'beklenen', 'sapma_%', 'durum'], out,
                  {'tablo': len(series), 'anomali': len(out), 'sifir_yukleme': sum(o['durum'] == 'SIFIR YÜKLEME' for o in out)},
                  notes=['Beklenen değer haftanın aynı günlerinin medyanıdır; tatil ve kampanya günlerinde sapma olağan olabilir.'])


@op('sema-degisikligi-izle', title='Günlük şema anlık görüntülerinden değişiklikleri bul', domain=D, product=P, tag='schema drift', samples={'sema_anliklari.csv': 'sema_anliklari.csv'},
    args=[arg('--date', default='tarih'), arg('--table', default='tablo'), arg('--column', default='sutun'), arg('--type', default='tip')],
    description='information_schema.columns sorgusunun tarihli anlık görüntülerini karşılaştırarak ardışık günler arasında eklenen, silinen ve tipi değişen sütunları tablo bazında listeler. Kaynak sistemdeki habersiz değişikliklerin pipeline’ı kırmadan önce fark edilmesi için.',
    input='Tarihli sütun listesi (tarih, tablo, sütun, tip)', output='sonuc.csv (değişiklikler)', command='python main.py --input columns_snapshots.csv',
    expect={'degisiklik': 3, 'silinen': 1})
def schema_drift(columns, rows, args):
    snaps = defaultdict(dict)
    for r in rows:
        snaps[r[need(args.date, columns, '--date')].strip()][(r[need(args.table, columns, '--table')].strip(), r[need(args.column, columns, '--column')].strip().lower())] = r[need(args.type, columns, '--type')].strip().lower()
    days = sorted(snaps)
    if len(days) < 2:
        raise ValueError('Karşılaştırma için en az iki tarihli anlık görüntü gerekir.')
    out = []
    for a, b in zip(days, days[1:]):
        sa, sb = snaps[a], snaps[b]
        for k in sorted(set(sa) | set(sb)):
            if k not in sb:
                out.append({'tarih': b, 'tablo': k[0], 'sutun': k[1], 'degisiklik': 'silindi', 'eski': sa[k], 'yeni': ''})
            elif k not in sa:
                out.append({'tarih': b, 'tablo': k[0], 'sutun': k[1], 'degisiklik': 'eklendi', 'eski': '', 'yeni': sb[k]})
            elif sa[k] != sb[k]:
                out.append({'tarih': b, 'tablo': k[0], 'sutun': k[1], 'degisiklik': 'tip değişti', 'eski': sa[k], 'yeni': sb[k]})
    return Result(f'{len(days)} anlık görüntü; {len(out)} değişiklik', ['tarih', 'tablo', 'sutun', 'degisiklik', 'eski', 'yeni'], out,
                  {'anlik': len(days), 'degisiklik': len(out), 'silinen': sum(o['degisiklik'] == 'silindi' for o in out)})


@op('null-orani-izle', title='Sütun NULL oranında ani artışları izle', domain=D, product=P, tag='null oranı', samples={'null_oranlari.csv': 'null_oranlari.csv'},
    args=[arg('--date', default='tarih'), arg('--key', default='tablo,sutun'), arg('--value', default='null_yuzde'), arg('--jump', type=float, default=5.0, help='Medyana göre en az bu kadar puan artış')],
    description='Tarihli NULL yüzdesi (veya herhangi bir kalite metriği) serisinde her sütunun kendi geçmiş medyanına göre ani sıçramalarını bulur; kalıcı yüksek NULL oranını sıçramadan ayırır. dbt/Great Expectations/özel sorgu çıktılarıyla çalışır.',
    input='Tarihli metrik (tarih, tablo, sütun, null yüzdesi)', output='sonuc.csv (sıçramalar)', command='python main.py --jump 3',
    expect={'seri': 3, 'sicrama': 1})
def nulls(columns, rows, args):
    keys = [k.strip() for k in args.key.split(',') if k.strip()]
    for k in keys:
        need(k, columns, '--key')
    vals = numbers(rows, need(args.value, columns, '--value'))
    series = defaultdict(list)
    for r, v in zip(rows, vals):
        d = tabular.parse_date(r[need(args.date, columns, '--date')])
        if d and v is not None:
            series[' · '.join(r[k] for k in keys)].append((d[0].date(), v))
    out = []
    for s, pts in sorted(series.items()):
        pts.sort()
        for i, (d, v) in enumerate(pts):
            hist = [x for _, x in pts[:i]]
            if len(hist) < 5:
                continue
            med = sorted(hist)[len(hist) // 2]
            if v - med >= args.jump:
                out.append({'seri': s, 'tarih': d.isoformat(), 'deger': v, 'gecmis_medyan': round(med, 2), 'artis_puan': round(v - med, 2)})
    return Result(f'{len(series)} seri; {len(out)} ani artış', ['seri', 'tarih', 'deger', 'gecmis_medyan', 'artis_puan'], out, {'seri': len(series), 'sicrama': len(out)})


OK_WORDS = {'basarili', 'success', 'succeeded', 'ok', 'tamam', 'completed'}


def pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, max(0, -(-len(sorted_vals) * q // 100) - 1))] if sorted_vals else None


@op('pipeline-calisma-analizi', title='İş (pipeline) çalışma geçmişi: başarı oranı, süre ve yavaşlama', domain=D, product=P, tag='iş geçmişi', samples={'is_calismalari.csv': 'is_calismalari.csv'},
    args=[arg('--job', default='is'), arg('--start', default='baslangic'), arg('--end', default='bitis'), arg('--status', default='durum'),
          arg('--slow', type=float, default=2.0, help='Son süre, geçmiş medyanın bu katını aşarsa yavaşlama'), arg('--streak', type=int, default=2, help='Art arda bu kadar hata uyarı')],
    description='Orkestratör çalışma geçmişinden (Airflow, ADF, SQL Agent, cron logu) iş bazında başarı oranını, medyan ve p95 süreyi, son çalışmanın geçmiş medyana göre yavaşlamasını, art arda hata sayısını ve son başarılı çalışma zamanını çıkarır. Sabah raporu ve SLA takibi için.',
    input='Çalışma geçmişi (iş, başlangıç, bitiş, durum)', output='sonuc.csv (iş bazında özet)', command='python main.py --input dag_runs.csv --job dag_id --start start_date --end end_date --status state',
    expect={'is': 4, 'calisma': 29, 'basari_%': 82.8, 'yavaslayan': 1, 'ardisik_hatali': 1})
def runs(columns, rows, args):
    j, s, e, st = (need(c, columns, f) for c, f in ((args.job, '--job'), (args.start, '--start'), (args.end, '--end'), (args.status, '--status')))
    jobs = defaultdict(list)
    for r in rows:
        a, b = tabular.parse_date(r[s]), tabular.parse_date(r[e])
        if a:
            ok = r[st].strip().lower() in OK_WORDS
            jobs[r[j].strip()].append((a[0], (b[0] - a[0]).total_seconds() / 60 if b else None, ok))
    out = []
    for name, rs in sorted(jobs.items()):
        rs.sort()
        good = [d for _, d, ok in rs[:-1] if ok and d is not None]
        med = sorted(good)[len(good) // 2] if good else None
        last_t, last_d, last_ok = rs[-1]
        streak = 0
        for _, _, ok in reversed(rs):
            if ok:
                break
            streak += 1
        warn = []
        if last_ok and med and last_d is not None and last_d > args.slow * med:
            warn.append(f'yavaşlama: son süre medyanın {last_d / med:.1f} katı')
        if streak >= args.streak:
            warn.append(f'art arda {streak} hata')
        durs = sorted(d for _, d, ok in rs if ok and d is not None)
        last_good = next((t for t, _, ok in reversed(rs) if ok), None)
        out.append({'is': name, 'calisma': len(rs), 'basarili': sum(ok for *_, ok in rs), 'basari_%': round(100 * sum(ok for *_, ok in rs) / len(rs), 1),
                    'medyan_dk': fmt(round(durs[len(durs) // 2], 1)) if durs else '', 'p95_dk': fmt(round(pct(durs, 95), 1)) if durs else '',
                    'son_sure_dk': fmt(round(last_d, 1)) if last_d is not None else '', 'son_durum': 'başarılı' if last_ok else 'HATALI', 'ardisik_hata': streak,
                    'son_basarili': last_good.isoformat(sep=' ') if last_good else 'hiç', 'uyari': '; '.join(warn)})
    out.sort(key=lambda o: (not o['uyari'], o['is']))
    total = sum(o['calisma'] for o in out)
    return Result(f'{len(out)} iş, {total} çalışma; {sum(bool(o["uyari"]) for o in out)} işte uyarı', list(out[0]), out,
                  {'is': len(out), 'calisma': total, 'basari_%': round(100 * sum(o['basarili'] for o in out) / total, 1) if total else 0,
                   'yavaslayan': sum('yavaşlama' in o['uyari'] for o in out), 'ardisik_hatali': sum(o['ardisik_hata'] >= args.streak for o in out)})


LOG_HEAD = re.compile(r'^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\]?\s+(?:\[?(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|SEVERE|CRITICAL|FATAL)\]?)?\s*(?:\[[^\]]*\]\s*)?(.*)$', re.I)
ERROR_LEVELS = {'ERROR', 'SEVERE', 'CRITICAL', 'FATAL'}
NORMALIZE = [(re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+'), '<EPOSTA>'), (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I), '<ID>'),
             (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b'), '<IP>'), (re.compile(r'\b0x[0-9a-f]+\b', re.I), '<HEX>'), (re.compile(r'"[^"]*"|\'[^\']*\''), '<METİN>'),
             (re.compile(r'\d+(?:[.,]\d+)?'), '<N>')]


def signature(msg):
    for rx, rep in NORMALIZE:
        msg = rx.sub(rep, msg)
    return re.sub(r'\s+', ' ', msg).strip()[:200]


@op('log-hata-ozeti', title='Uygulama logundaki hataları imzalarına göre grupla', domain=D, product=P, tag='log analizi', kind='file', samples={'uygulama.log': 'uygulama.log'},
    args=[arg('--top', type=int, default=50, help='En çok kaç imza listelensin')],
    description='Zaman damgalı düz metin loglarında ERROR/FATAL kayıtlarını ve Python/Java yığın izlerini (traceback) kayıt bazında toplar; sayıları, kimlikleri, e-posta ve IP adreslerini yer tutucuya çevirerek aynı hatanın farklı örneklerini tek imzada birleştirir. İmza başına adet, ilk/son görülme ve istisna türlerini verir; kişisel veri rapora yazılmaz.',
    input='Uygulama logu (.log/.txt)', output='sonuc.csv (hata imzaları)', command='python main.py --input /var/log/app/app.log --top 20',
    expect={'kayit': 11, 'hata': 7, 'imza': 4, 'uyari': 1})
def logs(args):
    records, cur = [], None
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        m = LOG_HEAD.match(line)
        if m:
            cur = {'zaman': m.group(1), 'seviye': (m.group(2) or '').upper().replace('WARNING', 'WARN'), 'mesaj': m.group(3), 'ek': []}
            records.append(cur)
        elif cur is not None and line.strip():
            cur['ek'].append(line.strip())
    if not records:
        raise ValueError('Zaman damgalı log satırı bulunamadı (2026-10-15 08:00:01 ERROR … biçimi bekleniyor).')
    groups, exc = {}, Counter()
    for r in records:
        kinds = [m.group(1) for x in r['ek'] for m in [re.match(r'^([\w.]+(?:Error|Exception|Fault))\b', x)] if m]
        if r['seviye'] not in ERROR_LEVELS and not kinds:
            continue
        exc.update(k.split('.')[-1] for k in kinds)
        sig = signature(r['mesaj'])
        g = groups.setdefault(sig, {'imza': sig, 'seviye': r['seviye'] or 'istisna', 'adet': 0, 'ilk': r['zaman'], 'son': r['zaman'], 'istisna': set()})
        g['adet'] += 1
        g['son'] = r['zaman']
        g['istisna'].update(k.split('.')[-1] for k in kinds)
    out = sorted(groups.values(), key=lambda g: -g['adet'])[:args.top]
    for g in out:
        g['istisna'] = ', '.join(sorted(g['istisna']))
    errors = sum(g['adet'] for g in groups.values())
    return Result(f'{len(records)} kayıt; {errors} hata {len(groups)} imzada toplandı', ['imza', 'seviye', 'adet', 'ilk', 'son', 'istisna'], out,
                  {'kayit': len(records), 'hata': errors, 'imza': len(groups), 'uyari': sum(r['seviye'] == 'WARN' for r in records), 'istisna': dict(exc)},
                  tables=[('Seviyeye göre kayıt', ['Seviye', 'Kayıt'], Counter(r['seviye'] or '-' for r in records).most_common())])
