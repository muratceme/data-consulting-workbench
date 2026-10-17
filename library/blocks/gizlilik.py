"""Gizlilik blokları: k-anonimlik ve l-çeşitlilik ölçümü, genelleştirerek anonimleştirme, İYS izin kontrolü ve yurt dışı aktarım envanteri.
Hukuki görüş yerine geçmez; kayıtları tanımlı kurallarla karşılaştırır ve hiçbir sisteme bağlanmaz."""
import re
from collections import Counter, defaultdict
from datetime import timedelta
import tabular
import turkiye as tr
from pathlib import Path
from islem import op, arg, Result, need, numbers, col_list, fold, fmt

D, P = 'privacy', 'Hassas veri envanteri'
REQUIRES = ['turkiye']


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


@op('ihlal-bildirim-suresi', title='Veri ihlallerinde 72 saatlik Kurul bildirim süresi takibi', domain=D, product=P, tag='ihlal bildirimi', samples={'veri_ihlalleri.csv': 'veri_ihlalleri.csv'},
    args=[arg('--as-of', default='2026-10-16 12:00', help='Açık olaylar için hesap zamanı'), arg('--hours', type=float, default=72.0)],
    description='Veri ihlali kayıtlarında tespit ile Kurul’a bildirim arasındaki süreyi hesaplar: süresinde, geç bildirilmiş ve hâlâ bildirilmemiş (süresi dolmuş veya kalan saati işleyen) olayları ayırır; ilgili kişilere bildirim kaydı olmayan olayları ve etkilenen kişi sayısını raporlar. İhlal müdahale ekibinin günlük takibi için.',
    input='İhlal kayıtları (olay, tespit zamanı, Kurul bildirim zamanı, ilgili kişi bildirimi, etkilenen kişi)', output='sonuc.csv (olay bazında süre durumu)', command='python main.py --input ihlal_kayitlari.xlsx --as-of "2026-12-01 09:00"',
    expect={'olay': 5, 'gec_bildirim': 1, 'bildirilmemis_gecikmis': 1, 'suresi_isleyen': 1, 'ilgili_kisi_kaydi_yok': 3})
def breach(columns, rows, args):
    for c in ('olay_no', 'tespit_zamani', 'kurul_bildirim_zamani'):
        need(c, columns, 'ihlal kayıtları')
    now = tabular.parse_date(args.as_of)[0]
    people = numbers(rows, 'etkilenen_kisi') if 'etkilenen_kisi' in columns else [None] * len(rows)
    out = []
    for r, n in zip(rows, people):
        found = tabular.parse_date(r['tespit_zamani'])
        if not found:
            out.append({'olay_no': r['olay_no'], 'gecen_saat': '', 'kalan_saat': '', 'durum': 'tespit zamanı yok', 'etkilenen_kisi': fmt(n), 'ilgili_kisi_bildirimi': r.get('ilgili_kisi_bildirim', '')})
            continue
        sent = tabular.parse_date(r['kurul_bildirim_zamani'])
        hours = ((sent[0] if sent else now) - found[0]).total_seconds() / 3600
        if sent:
            state = 'süresinde bildirildi' if hours <= args.hours else 'GEÇ BİLDİRİLDİ'
        else:
            state = 'BİLDİRİLMEDİ — süre doldu' if hours > args.hours else 'süre işliyor'
        out.append({'olay_no': r['olay_no'], 'gecen_saat': round(hours, 1), 'kalan_saat': round(args.hours - hours, 1) if not sent and hours <= args.hours else '',
                    'durum': state, 'etkilenen_kisi': fmt(n), 'ilgili_kisi_bildirimi': r.get('ilgili_kisi_bildirim', '').strip() or 'kayıt yok'})
    order = {'BİLDİRİLMEDİ — süre doldu': 0, 'süre işliyor': 1, 'GEÇ BİLDİRİLDİ': 2}
    out.sort(key=lambda o: (order.get(o['durum'], 3), o['olay_no']))
    c = Counter(o['durum'] for o in out)
    return Result(f'{len(out)} olay; {c["GEÇ BİLDİRİLDİ"]} geç, {c["BİLDİRİLMEDİ — süre doldu"]} bildirilmemiş ve süresi dolmuş, {c["süre işliyor"]} olayda süre işliyor', list(out[0]), out,
                  {'olay': len(out), 'gec_bildirim': c['GEÇ BİLDİRİLDİ'], 'bildirilmemis_gecikmis': c['BİLDİRİLMEDİ — süre doldu'], 'suresi_isleyen': c['süre işliyor'],
                   'ilgili_kisi_kaydi_yok': sum(o['ilgili_kisi_bildirimi'] == 'kayıt yok' for o in out), 'etkilenen_toplam': sum(n or 0 for n in people)},
                  notes=['Süre tespit anından başlatılır; kurumunuzun prosedüründeki başlangıç anını (öğrenme/teyit) esas alın. Hukuki değerlendirme yerine geçmez.'])


@op('cerez-envanteri', title='Çerez envanterini onay ve süre kurallarıyla denetle', domain=D, product=P, tag='çerez', samples={'cerezler.csv': 'cerezler.csv'},
    args=[arg('--site', default='ornek.com.tr', help='Birinci taraf alan adı'), arg('--max-days', type=int, default=365, help='Bu süreden uzun çerezleri işaretle')],
    description='Web sitesi çerez taraması dışa aktarımında (çerez, alan adı, kategori, süre, onaydan önce yerleşip yerleşmediği) zorunlu olmayan ama onay alınmadan yerleşen, kategorisi belirlenmemiş ve azami süreyi aşan çerezleri bulur; üçüncü taraf alan adlarını listeler. Çerez politikası ve onay yönetim aracı ayarlarını doğrulamak için.',
    input='Çerez tarama listesi (CSV/XLSX)', output='sonuc.csv (çerez bazında bulgular)', command='python main.py --input cookie_scan.csv --site magaza.com.tr --max-days 180',
    expect={'cerez': 7, 'sorunlu': 3, 'onaysiz_yerlesen': 2, 'ucuncu_taraf': 2})
def cookies(columns, rows, args):
    for c in ('cerez', 'alan_adi', 'kategori'):
        need(c, columns, 'çerez listesi')
    days = numbers(rows, 'sure_gun') if 'sure_gun' in columns else [None] * len(rows)
    site = args.site.lower().lstrip('.')
    out = []
    for r, d in zip(rows, days):
        cat = fold(r['kategori'])
        dom = r['alan_adi'].strip().lower().lstrip('.')
        third = not (dom == site or dom.endswith('.' + site))
        before = r.get('onay_oncesi', '').strip().lower() in ('evet', 'e', 'true', '1', 'yes')
        issues = []
        if not cat:
            issues.append('kategori belirlenmemiş')
        if before and cat != 'zorunlu':
            issues.append('zorunlu değil ama onaydan önce yerleşiyor')
        if d is not None and d > args.max_days:
            issues.append(f'süre {fmt(d)} gün > {args.max_days}')
        out.append({'cerez': r['cerez'], 'alan_adi': r['alan_adi'], 'kategori': r['kategori'] or '-', 'sure_gun': fmt(d), 'taraf': 'üçüncü taraf' if third else 'birinci taraf',
                    'onay_oncesi': 'evet' if before else 'hayır', 'bulgu': '; '.join(issues)})
    out.sort(key=lambda o: (not o['bulgu'], o['cerez']))
    return Result(f'{len(out)} çerez; {sum(bool(o["bulgu"]) for o in out)} sorunlu, {sum(o["taraf"] == "üçüncü taraf" for o in out)} üçüncü taraf', list(out[0]), out,
                  {'cerez': len(out), 'sorunlu': sum(bool(o['bulgu']) for o in out), 'onaysiz_yerlesen': sum('onaydan önce' in o['bulgu'] for o in out),
                   'ucuncu_taraf': sum(o['taraf'] == 'üçüncü taraf' for o in out)},
                  notes=['Kategori adları zorunlu, analitik, tercih, pazarlama gibi yazılmalıdır; süre eşiğini kurumunuzun çerez politikasına göre verin.'])


def search_norm(v):
    """(kind, normalized) for exact comparison: e-mail, IBAN, digits (phone/TCKN/no) or folded text."""
    v = v.strip()
    if '@' in v:
        return 'e', v.casefold()
    compact = re.sub(r'\s', '', v).upper()
    if re.fullmatch(r'TR\d{24}', compact):
        return 'i', compact
    d = re.sub(r'\D', '', v)
    if len(d) >= 10 and not re.search(r'[^\d\s()+\-.]', v):
        return 'd', d[-10:] if len(d) in (10, 11, 12) and d[-10] == '5' else d  # mobile numbers: last 10 digits
    return 't', fold(v).casefold()


def contains(kind, target, cell):
    if kind == 'e':
        return target in cell.casefold()
    if kind == 'i':
        return target in re.sub(r'\s', '', cell).upper()
    if kind == 'd':
        return target in re.sub(r'\D', '', cell)
    return bool(re.search(r'(?<!\w)' + re.escape(target) + r'(?!\w)', fold(cell).casefold()))


@op('ilgili-kisi-arama', title='İlgili kişi başvurusu için bir kişinin verisini klasördeki tüm dosyalarda ara', domain=D, product=P, tag='başvuru araması', kind='file',
    samples={f'kvkk_arama/{n}': f'kvkk_arama/{n}' for n in ('crm_musteri.csv', 'siparisler.csv', 'destek_talepleri.csv', 'urunler.csv')}, sample_input='kvkk_arama',
    args=[arg('--value', default='zeynep.demir@ornek.test', help='Aranan tanımlayıcı: e-posta, telefon, TCKN, IBAN veya müşteri no'),
          arg('--pattern', default='*', help='Dosya adı deseni (ör. *.csv)')],
    description='KVKK md. 11 ilgili kişi başvurusunda “hangi verilerim, nerede?” sorusunu cevaplamak için bir e-posta, telefon, TCKN, IBAN veya müşteri numarasını klasördeki tüm CSV/XLSX dışa aktarımlarında arar. Telefonları biçimden bağımsız (son 10 hane), e-postaları büyük/küçük harften bağımsız eşler; hücrenin tamamı olan eşleşmeyi ve serbest metin içindeki geçişi ayırır. Rapora aranan değer ve bulunan değerler yazılmaz; yalnızca dosya, sütun ve satır numaraları.',
    input='CSV/XLSX dışa aktarımlarının bulunduğu klasör + aranan değer', output='sonuc.csv (dosya, sütun, satır numaraları)', command='python main.py --input C:\\disaaktarim --value 05324182790',
    expect={'dosya': 4, 'eslesen_dosya': 3, 'eslesme': 4, 'tam': 3, 'metin_icinde': 1})
def subject_search(args):
    root = Path(args.input)
    files = sorted(f for f in (root.rglob(args.pattern) if root.is_dir() else [root]) if f.is_file() and f.suffix.lower() in ('.csv', '.txt', '.xlsx', '.tsv'))
    if not files:
        raise ValueError(f'{root}: CSV/XLSX dosyası bulunamadı.')
    kind, target = search_norm(args.value)
    if len(target) < 4:
        raise ValueError('--value en az 4 anlamlı karakter içermeli.')
    out, errors = [], []
    for f in files:
        try:
            columns, rows = tabular.read(str(f))
        except (ValueError, OSError) as exc:
            errors.append(f'{f.name}: {exc}')
            continue
        for c in columns:
            exact, inside = [], []
            for i, r in enumerate(rows, start=2):
                cell = r[c]
                if not cell.strip():
                    continue
                k, v = search_norm(cell)
                if k == kind and v == target:
                    exact.append(i)
                elif contains(kind, target, cell):
                    inside.append(i)
            for label, hits in (('tam', exact), ('metin içinde', inside)):
                if hits:
                    out.append({'dosya': str(f.relative_to(root)) if root.is_dir() else f.name, 'sutun': c, 'eslesme_turu': label, 'satir_sayisi': len(hits),
                                'satirlar': ', '.join(map(str, hits[:20])) + (' …' if len(hits) > 20 else '')})
    shown = args.value.strip()
    masked = shown[:2] + '***' + shown[-2:] if len(shown) > 6 else '***'
    return Result(f'"{masked}" {len(files)} dosyada arandı; {len({o["dosya"] for o in out})} dosyada {sum(o["satir_sayisi"] for o in out)} satırda bulundu',
                  ['dosya', 'sutun', 'eslesme_turu', 'satir_sayisi', 'satirlar'], out,
                  {'dosya': len(files), 'eslesen_dosya': len({o['dosya'] for o in out}), 'eslesme': sum(o['satir_sayisi'] for o in out),
                   'tam': sum(o['satir_sayisi'] for o in out if o['eslesme_turu'] == 'tam'), 'metin_icinde': sum(o['satir_sayisi'] for o in out if o['eslesme_turu'] != 'tam'),
                   'okunamayan': len(errors)},
                  notes=['Satır numaraları Excel’deki satırdır (başlık 1. satır). Aranan değer rapora yazılmaz; cevap yazısını bu listeyle hazırlayın.',
                         'Yalnızca verilen dosyalar taranır; veritabanları, e-posta kutuları ve yedekler ayrıca aranmalıdır.'] + errors)


TEST_DOMAINS = ('example.com', 'example.org', 'example.net', 'test.com', 'mailinator.com', 'localhost')
TEST_CARDS = {'4111111111111111', '4242424242424242', '5555555555554444', '5105105105105100', '378282246310005', '4000056655665556', '4012888888881881'}
TEST_TCKN = {'11111111110', '12345678950'}


def value_kind(v):
    s = v.strip()
    d = re.sub(r'\D', '', s)
    if re.fullmatch(r'[\w.+-]+@[\w-]+(\.[\w-]+)+', s):
        return 'e-posta'
    if re.fullmatch(r'TR\d{24}', s.upper().replace(' ', '')):
        return 'IBAN'
    if re.fullmatch(r'[1-9]\d{10}', s):
        return 'TCKN'
    if re.fullmatch(r'(\+?90[ -]?)?0?5\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}', s):
        return 'telefon'
    if 13 <= len(d) <= 19 and re.fullmatch(r'[\d -]+', s):
        return 'kart no'
    return None


def real_looking(kind, v):
    s, d = v.strip(), re.sub(r'\D', '', v)
    if kind == 'e-posta':
        domain = s.rsplit('@', 1)[1].lower()
        return not (domain in TEST_DOMAINS or domain.endswith(('.test', '.example', '.invalid', '.localhost')) or domain.startswith('test.'))
    if kind == 'IBAN':
        return tr.iban(s) == ''
    if kind == 'TCKN':
        return tr.tckn(d) == '' and d not in TEST_TCKN
    if kind == 'telefon':
        return not re.search(r'(\d)\1{5}', d[-10:]) and '1234567' not in d
    return tr.luhn(d) == '' and d not in TEST_CARDS


@op('test-ortami-gercek-veri', title='Test/geliştirme verisinde gerçek görünümlü kişisel veri ara', domain=D, product=P, tag='test verisi',
    samples={'test_ortami_musteri.csv': 'test_ortami_musteri.csv'}, args=[arg('--min-share', type=float, default=50.0, help='Sütun tipini belirlemek için gereken asgari kalıp oranı (yüzde)')],
    description='Test, geliştirme veya eğitim ortamına kopyalanan bir dosyada kişisel veri kalıbı taşıyan sütunları bulur ve her değerin gerçek mi sentetik mi göründüğünü ayırır: kontrol hanesi tutan TCKN, mod 97’si tutan IBAN, Luhn’u tutan ve bilinen test kartı olmayan kart numarası, test alanı (example.com, .test) dışındaki e-posta ve tekrar eden rakamlardan oluşmayan telefon “gerçek görünümlü” sayılır. Maskelenmeden taşınmış üretim verisini yakalamak için.',
    input='Test ortamı dışa aktarımı (CSV/XLSX)', output='sonuc.csv (sütun bazında gerçek görünümlü değer sayısı ve satırlar)', command='python main.py --input test_db_musteri.csv',
    expect={'kisisel_sutun': 5, 'gercek_gorunumlu': 7, 'riskli_sutun': 4})
def real_in_test(columns, rows, args):
    out = []
    for c in columns:
        values = [(i, r[c]) for i, r in enumerate(rows, start=2) if r[c].strip()]
        kinds = Counter(k for k in (value_kind(v) for _, v in values) if k)
        if not kinds:
            continue
        kind, n = kinds.most_common(1)[0]
        if 100 * n / len(values) < args.min_share:
            continue
        real = [i for i, v in values if value_kind(v) == kind and real_looking(kind, v)]
        out.append({'sutun': c, 'tur': kind, 'dolu': len(values), 'gercek_gorunumlu': len(real), 'sentetik': n - len(real),
                    'gercek_%': round(100 * len(real) / n, 1), 'satirlar': ', '.join(map(str, real[:15])) + (' …' if len(real) > 15 else ''),
                    'durum': 'maskelenmemiş olabilir' if real else 'sentetik'})
    out.sort(key=lambda o: (-o['gercek_gorunumlu'], o['sutun']))
    risky = sum(o['gercek_gorunumlu'] > 0 for o in out)
    return Result(f'{len(out)} kişisel veri sütunu; {risky} sütunda toplam {sum(o["gercek_gorunumlu"] for o in out)} gerçek görünümlü değer',
                  ['sutun', 'tur', 'dolu', 'gercek_gorunumlu', 'sentetik', 'gercek_%', 'satirlar', 'durum'], out,
                  {'kisisel_sutun': len(out), 'gercek_gorunumlu': sum(o['gercek_gorunumlu'] for o in out), 'riskli_sutun': risky},
                  notes=['“Gerçek görünümlü” bir değerin gerçek kişiye ait olduğunu kanıtlamaz; kontrol hanesi tutan değerleri sentetik veri üreteçleri de üretebilir. Kaynağını doğrulayın.',
                         'Değerler rapora yazılmaz; satır numaraları Excel’deki satırdır. Üretimden kopyalanan veriyi kvkk-maskeleme bloğuyla maskeleyin.'])
