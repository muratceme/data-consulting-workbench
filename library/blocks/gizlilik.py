"""Gizlilik blokları: k-anonimlik ve l-çeşitlilik ölçümü, genelleştirerek anonimleştirme, İYS izin kontrolü ve yurt dışı aktarım envanteri.
Hukuki görüş yerine geçmez; kayıtları tanımlı kurallarla karşılaştırır ve hiçbir sisteme bağlanmaz."""
import re
from collections import Counter, defaultdict
from datetime import timedelta
import tabular
from islem import op, arg, Result, need, numbers, col_list, fold

D, P = 'privacy', 'Hassas veri envanteri'


def classes(rows, qi):
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[tuple(r[c].strip() for c in qi)].append(i)
    return groups


@op('k-anonimlik', title='k-anonimlik ve l-çeşitlilik ölçümü (yeniden tanınma riski)', domain=D, product=P, tag='k-anonimlik', samples={'hasta_kayitlari.csv': 'hasta_kayitlari.csv'},
    args=[arg('--qi', default='cinsiyet,il', help='Yarı tanımlayıcı sütunlar (virgülle)'), arg('--sensitive', default='tani', help='Hassas sütun (l-çeşitlilik için, boş bırakılabilir)'),
          arg('--k', type=int, default=5), arg('--l', type=int, default=2)],
    description='Paylaşılacak veya test ortamına taşınacak bir veri setinde yarı tanımlayıcı sütunların (yaş, cinsiyet, posta kodu, il…) oluşturduğu eşdeğerlik sınıflarını sayar; k’dan küçük sınıfları, risk altındaki kayıt sayısını, en yüksek ve ortalama yeniden tanınma riskini ve hassas sütunda tek değere düşen (l-çeşitliliği sağlamayan) sınıfları raporlar.',
    input='Kayıt düzeyinde CSV/XLSX', output='sonuc.csv (eşdeğerlik sınıfları)', command='python main.py --input paylasim.csv --qi yas_grubu,cinsiyet,ilce --sensitive tani --k 10',
    expect={'kayit': 20, 'sinif': 5, 'min_k': 1, 'k_alti_sinif': 2, 'k_alti_kayit': 4, 'l_alti_sinif': 2})
def kanon(columns, rows, args):
    qi = col_list(args.qi, columns, '--qi')
    sens = need(args.sensitive, columns, '--sensitive') if args.sensitive else None
    groups = classes(rows, qi)
    out = []
    for key, idx in groups.items():
        distinct = len({rows[i][sens].strip() for i in idx}) if sens else ''
        issues = []
        if len(idx) < args.k:
            issues.append(f'k<{args.k}')
        if sens and distinct < args.l:
            issues.append(f'l<{args.l} (hassas değer tek tip)')
        out.append({**dict(zip(qi, key)), 'kayit': len(idx), 'hassas_farkli': distinct, 'risk_%': round(100 / len(idx), 1), 'durum': ', '.join(issues) or 'uygun'})
    out.sort(key=lambda o: (o['kayit'], [o[c] for c in qi]))
    small = [o for o in out if o['kayit'] < args.k]
    return Result(f'{len(rows)} kayıt, {len(groups)} sınıf; en küçük sınıf {out[0]["kayit"]} kayıt; {sum(o["kayit"] for o in small)} kayıt k={args.k} altında',
                  [*qi, 'kayit', 'hassas_farkli', 'risk_%', 'durum'], out,
                  {'kayit': len(rows), 'sinif': len(groups), 'min_k': out[0]['kayit'], 'k_alti_sinif': len(small), 'k_alti_kayit': sum(o['kayit'] for o in small),
                   'l_alti_sinif': sum('l<' in o['durum'] for o in out), 'en_yuksek_risk_%': out[0]['risk_%'], 'ortalama_risk_%': round(100 * len(groups) / len(rows), 1)},
                  notes=['Ortalama risk, sınıf sayısı / kayıt sayısıdır (savcı modeli). Doğrudan tanımlayıcılar (ad, TCKN, telefon) bu ölçümden önce kaldırılmış olmalıdır.',
                         'k’yı yükseltmek için genellestir bloğuyla yaşı aralığa, posta kodunu ilk hanelerine, tarihi yıla indirin.'])


def generalize(value, rule):
    v = value.strip()
    if not v:
        return v
    m = re.fullmatch(r'aralik(\d+)', rule)
    if m:
        n = tabular.parse_number(v)
        if n is None:
            return v
        w = int(m.group(1))
        lo = int(n // w * w)
        return f'{lo}-{lo + w - 1}'
    m = re.fullmatch(r'ilk(\d+)', rule)
    if m:
        k = int(m.group(1))
        return v[:k] + '*' * max(0, len(v) - k)
    if rule in ('yil', 'ay'):
        d = tabular.parse_date(v)
        return v if not d else str(d[0].year) if rule == 'yil' else f'{d[0].year}-{d[0].month:02d}'
    if rule == 'maske':
        return '***'
    raise ValueError(f'Bilinmeyen kural: {rule} (aralikN, ilkN, yil, ay, maske, sil)')


@op('genellestir', title='Yarı tanımlayıcıları genelleştirerek veri setini anonimleştir', domain=D, product=P, tag='anonimleştirme', samples={'hasta_kayitlari.csv': 'hasta_kayitlari.csv'},
    args=[arg('--rules', default='yas:aralik10,posta_kodu:ilk3,ad_soyad:sil,hasta_no:sil', help='sutun:kural listesi (aralikN, ilkN, yil, ay, maske, sil)'),
          arg('--qi', default='yas,cinsiyet,posta_kodu', help='k ölçümünde kullanılacak yarı tanımlayıcılar'), arg('--k', type=int, default=3),
          arg('--suppress', action='store_true', help='Genelleştirme sonrası k’dan küçük sınıflardaki kayıtları çıkar')],
    description='Yaşı aralıklara, posta kodunu ilk hanelerine, tarihleri yıl veya aya indirir; doğrudan tanımlayıcıları siler veya maskeler. Genelleştirme öncesi ve sonrası k değerini ölçer, istenirse k’yı sağlamayan küçük sınıfları bastırır (satırı çıkarır). Test ortamı, analiz paylaşımı ve açık veri hazırlığı için.',
    input='Kayıt düzeyinde CSV/XLSX', output='sonuc.csv (anonimleştirilmiş veri)', command='python main.py --rules "yas:aralik5,dogum_tarihi:yil,tckn:sil" --qi yas,cinsiyet,ilce --k 5 --suppress',
    test_args=['--suppress'], expect={'k_once': 1, 'k_sonra': 3, 'bastirilan': 1, 'satir': 19})
def anonim(columns, rows, args):
    rules = {}
    for part in (p.strip() for p in args.rules.split(',') if p.strip()):
        col, _, rule = part.partition(':')
        rules[need(col.strip(), columns, '--rules')] = rule.strip().lower()
    for rule in set(rules.values()) - {'sil'}:
        generalize('1', rule)
    qi = [c for c in col_list(args.qi, columns, '--qi') if rules.get(c) != 'sil']
    before = min(len(v) for v in classes(rows, qi).values())
    keep = [c for c in columns if rules.get(c) != 'sil']
    new = [{c: generalize(r[c], rules[c]) if c in rules else r[c] for c in keep} for r in rows]
    groups = classes(new, qi)
    dropped = set()
    if args.suppress:
        dropped = {i for idx in groups.values() if len(idx) < args.k for i in idx}
        new = [r for i, r in enumerate(new) if i not in dropped]
        groups = classes(new, qi) if new else {}
    after = min((len(v) for v in groups.values()), default=0)
    return Result(f'k: {before} → {after}; {len(dropped)} kayıt bastırıldı, {len(columns) - len(keep)} sütun silindi', keep, new,
                  {'k_once': before, 'k_sonra': after, 'bastirilan': len(dropped), 'silinen_sutun': len(columns) - len(keep), 'sinif': len(groups)},
                  tables=[('Uygulanan kurallar', ['Sütun', 'Kural'], sorted(rules.items()))],
                  notes=[f'k={after} < {args.k}: kuralları kabalaştırın veya --suppress kullanın.' if after < args.k else f'Yarı tanımlayıcılarda her kayıt en az {after} kayıtla aynı görünüyor.',
                         'Serbest metin sütunları ve hassas değerlerin dağılımı (l-çeşitlilik) ayrıca kontrol edilmelidir.'])


def norm_addr(v, channel):
    v = v.strip()
    if channel == 'EPOSTA':
        return v.lower()
    digits = re.sub(r'\D', '', v)
    return digits[-10:] if len(digits) >= 10 else digits


@op('iys-izin-kontrol', title='Kampanya listesini İYS izin kayıtlarıyla karşılaştır', domain=D, product=P, tag='İYS izin',
    samples={'kampanya_listesi.csv': 'kampanya_listesi.csv', 'iys_izinleri.csv': 'iys_izinleri.csv'},
    args=[arg('--consents', default='@ornek/iys_izinleri.csv', help='İzin kayıtları (alici, tip, durum, tarih)'), arg('--channel', default='EPOSTA', choices=['EPOSTA', 'MESAJ', 'ARAMA']),
          arg('--address', help='Listedeki adres sütunu (varsayılan: EPOSTA için eposta, diğerleri için telefon)')],
    description='Ticari ileti gönderim listesindeki her alıcının ilgili kanaldaki (e-posta, SMS, arama) en güncel izin durumunu İYS’den dışa aktarılan kayıtlardan bulur; ret vermiş, izin kaydı olmayan, adresi boş ve listede tekrar eden alıcıları ayıklar, yalnızca izinli alıcılarla gonderilebilir.csv üretir. Telefonlar son 10 haneye, e-postalar küçük harfe göre eşleştirilir.',
    input='Gönderim listesi + İYS izin dışa aktarımı', output='sonuc.csv (alıcı bazında durum) + gonderilebilir.csv', command='python main.py --input sms_listesi.xlsx --channel MESAJ --consents iys.csv',
    expect={'liste': 8, 'gonderilebilir': 2, 'ret': 1, 'kayitsiz': 3, 'adres_yok': 1, 'tekrar': 1})
def iys(columns, rows, args):
    addr_col = need(args.address or ('eposta' if args.channel == 'EPOSTA' else 'telefon'), columns, '--address')
    ccols, crows = tabular.read(args.consents)
    for c in ('alici', 'tip', 'durum', 'tarih'):
        need(c, ccols, 'izin dosyası')
    latest = {}
    for r in crows:
        if r['tip'].strip().upper() != args.channel:
            continue
        d = tabular.parse_date(r['tarih'])
        key = norm_addr(r['alici'], args.channel)
        if d and (key not in latest or d[0] >= latest[key][0]):
            latest[key] = (d[0], r['durum'].strip().upper())
    seen, out = set(), []
    for i, r in enumerate(rows, 1):
        key = norm_addr(r[addr_col], args.channel)
        if not key:
            state = 'adres yok'
        elif key in seen:
            state = 'listede tekrar'
        elif key not in latest:
            state = 'izin kaydı yok'
        else:
            state = 'izinli' if latest[key][1] == 'ONAY' else 'ret'
        seen.add(key)
        out.append({'satir': i, **r, 'izin_tarihi': latest[key][0].date().isoformat() if key in latest else '', 'durum': state, 'gonder': 'evet' if state == 'izinli' else 'hayır'})
    count = Counter(o['durum'] for o in out)
    ok = [o for o in out if o['gonder'] == 'evet']
    return Result(f'{len(rows)} alıcı; {len(ok)} gönderilebilir, {count["ret"]} ret, {count["izin kaydı yok"]} kayıtsız', ['satir', *columns, 'izin_tarihi', 'durum', 'gonder'], out,
                  {'liste': len(rows), 'gonderilebilir': len(ok), 'ret': count['ret'], 'kayitsiz': count['izin kaydı yok'], 'adres_yok': count['adres yok'], 'tekrar': count['listede tekrar']},
                  files={'gonderilebilir.csv': (columns, [{c: o[c] for c in columns} for o in ok])},
                  notes=['İzin durumu dışa aktarım tarihindeki kayıtlara göredir; gönderimden hemen önce güncel İYS verisiyle tekrar çalıştırın.',
                         'Tacir/esnaf alıcılar için farklı kurallar olabilir; hukuk biriminizin yorumunu esas alın.'])


SPECIAL = re.compile(r'(sağlık|saglik|biyometrik|genetik|ceza|din|sendika|cinsel|ırk|irk|etnik|siyasi)', re.I)
DOMESTIC = {'turkiye', 'türkiye', 'tr', 'kktc'}


def add_business_days(d, n):
    while n:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d


@op('yurt-disi-aktarim', title='Yurt dışı veri aktarımı envanterini denetle', domain=D, product=P, tag='yurt dışı aktarım', samples={'veri_aktarimlari.csv': 'veri_aktarimlari.csv'},
    args=[arg('--as-of', default='2026-10-16'), arg('--notify-days', type=int, default=5, help='Standart sözleşmenin bildirimi için iş günü')],
    description='Tedarikçi ve grup şirketlerine yapılan kişisel veri aktarımlarını ülke, aktarım mekanizması ve sözleşme tarihleriyle kontrol eder: yurt dışına mekanizmasız aktarımı, standart sözleşmenin imzadan sonra süresinde bildirilmemesini, süresi dolmuş sözleşmeleri, düzenli aktarımda arızi istisnaya dayanılmasını ve özel nitelikli verinin yurt dışına çıkışını işaretler.',
    input='Aktarım envanteri (tedarikçi, ülke, veri kategorisi, mekanizma, imza/bildirim/bitiş tarihleri)', output='sonuc.csv (aktarım bazında bulgular)', command='python main.py --input aktarim_envanteri.xlsx --as-of 2026-12-31',
    expect={'aktarim': 8, 'yurt_disi': 7, 'sorunlu': 5, 'ozel_nitelikli_yurt_disi': 1})
def transfer(columns, rows, args):
    for c in ('tedarikci', 'ulke', 'veri_kategorisi', 'mekanizma'):
        need(c, columns, 'envanter')
    as_of = tabular.parse_date(args.as_of)[0].date()
    day = lambda r, c: (lambda d: d[0].date() if d else None)(tabular.parse_date(r.get(c, '') or ''))
    out = []
    for r in rows:
        abroad = fold(r['ulke']) not in {fold(x) for x in DOMESTIC}
        mech = fold(r['mekanizma'])
        special = (r.get('ozel_nitelikli', '').strip().lower() in ('evet', 'e', '1', 'true')) or bool(SPECIAL.search(r['veri_kategorisi']))
        issues = []
        if abroad:
            if not mech:
                issues.append('aktarım mekanizması yok')
            elif 'standart' in mech:
                signed, notified = day(r, 'imza_tarihi'), day(r, 'bildirim_tarihi')
                if not signed:
                    issues.append('imza tarihi yok')
                elif not notified:
                    issues.append('Kurul’a bildirim kaydı yok')
                elif notified > add_business_days(signed, args.notify_days):
                    issues.append(f'bildirim {args.notify_days} iş gününden geç ({signed} → {notified})')
            elif 'arizi' in mech:
                issues.append('arızi istisna düzenli aktarım için kullanılamaz')
            if special:
                issues.append('özel nitelikli veri yurt dışında: ek güvenlik önlemi ve açık dayanak gerekli')
        end = day(r, 'sozlesme_bitis')
        if end and end < as_of:
            issues.append(f'sözleşme süresi dolmuş ({end})')
        out.append({'tedarikci': r['tedarikci'], 'ulke': r['ulke'], 'veri_kategorisi': r['veri_kategorisi'], 'mekanizma': r['mekanizma'], 'yurt_disi': 'evet' if abroad else 'hayır',
                    'ozel_nitelikli': 'evet' if special else 'hayır', 'bulgu': '; '.join(issues), 'durum': 'SORUNLU' if issues else 'uygun'})
    out.sort(key=lambda o: (o['durum'] != 'SORUNLU', o['tedarikci']))
    bad = sum(o['durum'] == 'SORUNLU' for o in out)
    return Result(f'{len(out)} aktarım, {sum(o["yurt_disi"] == "evet" for o in out)} yurt dışı; {bad} sorunlu', list(out[0]), out,
                  {'aktarim': len(out), 'yurt_disi': sum(o['yurt_disi'] == 'evet' for o in out), 'sorunlu': bad,
                   'ozel_nitelikli_yurt_disi': sum(o['yurt_disi'] == 'evet' and o['ozel_nitelikli'] == 'evet' for o in out)},
                  notes=['İş günü hesabında resmî tatiller dikkate alınmaz; sınırdaki bildirimleri takvimle kontrol edin.',
                         'Mekanizma adları serbest metindir: "standart sözleşme", "bağlayıcı şirket kuralları", "yeterlilik kararı", "taahhütname", "arızi" gibi yazın.'])
