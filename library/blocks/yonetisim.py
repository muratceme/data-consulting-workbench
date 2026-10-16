"""Gizlilik ve yönetişim blokları: klasör kişisel veri envanteri, saklama süresi, KVKK başvuru süreleri, erişim gözden geçirme, veri sahipliği kapsamı.
Hukuki görüş yerine geçmez; kayıtların kurallarla karşılaştırılmasını otomatikleştirir."""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import tabular
from common import pii_kind
from islem import op, arg, Result, need, numbers, fold

PRIV, GOV = 'Hassas veri envanteri', 'Erişim ve sahiplik listeleri'


def day(v):
    d = tabular.parse_date(v or '')
    return d[0].date() if d else None


@op('kisisel-veri-envanteri', title='Klasördeki dosyalarda kişisel veri envanteri çıkar', domain='privacy', product=PRIV, tag='VERBİS', kind='file',
    samples={f'veri_klasoru/{n}': f'veri_klasoru/{n}' for n in ('musteriler.csv', 'satislar.csv', 'urunler.csv', 'kimlik_listesi.csv')}, sample_input='veri_klasoru',
    args=[arg('--min-share', type=float, default=20.0, help='Sütunun en az bu yüzdesi kişisel veri desenine uyuyorsa işaretle')],
    description='Bir klasördeki tüm CSV/XLSX dosyalarını tarayarak hangi dosyanın hangi sütununda hangi tür kişisel veri (e-posta, TCKN, IBAN, telefon, kart no; ad/adres/doğum tarihi sütun adları) bulunduğunu, kayıt sayısını ve yüzdesini listeler. Değerler rapora yazılmaz. VERBİS ve kişisel veri işleme envanteri hazırlığı için.',
    input='CSV/XLSX klasörü', output='sonuc.csv (dosya × sütun envanteri)', command='python main.py --input \\\\dosya-sunucusu\\paylasim\\raporlar',
    expect={'dosya': 4, 'kisisel_sutun': 14, 'kisisel_veri_iceren_dosya': 3})
def envanter(args):
    p = Path(args.input)
    files = sorted(f for f in p.rglob('*') if f.suffix.lower() in ('.csv', '.txt', '.tsv', '.xlsx', '.xlsm')) if p.is_dir() else [p]
    name_hint = re.compile(r'(ad_?soyad|adsoyad|^ad$|isim|soyad|adres|dogum|doğum|full_?name)', re.I)
    out, errors = [], []
    for f in files:
        try:
            cols, rows = tabular.read(f)
        except (ValueError, OSError) as exc:
            errors.append([f.name, str(exc)])
            continue
        for c in cols:
            vals = [r[c] for r in rows if r[c].strip()][:5000]
            kinds = Counter(k for k in (pii_kind(v) for v in vals) if k)
            kind, share = None, 0.0
            if kinds:
                kind, n = kinds.most_common(1)[0]
                share = 100 * n / len(vals)
            if kind and share >= args.min_share:
                out.append({'dosya': f.relative_to(p).as_posix() if p.is_dir() else f.name, 'sutun': c, 'veri_turu': kind, 'dolu_kayit': len(vals), 'eslesen_%': round(share, 1), 'yontem': 'değer deseni'})
            elif name_hint.search(c):
                out.append({'dosya': f.relative_to(p).as_posix() if p.is_dir() else f.name, 'sutun': c, 'veri_turu': 'ad/adres/doğum (sütun adı)', 'dolu_kayit': len(vals), 'eslesen_%': '', 'yontem': 'sütun adı'})
    files_with = len({o['dosya'] for o in out})
    return Result(f'{len(files)} dosya tarandı; {files_with} dosyada {len(out)} kişisel veri sütunu', ['dosya', 'sutun', 'veri_turu', 'dolu_kayit', 'eslesen_%', 'yontem'], out,
                  {'dosya': len(files), 'kisisel_sutun': len(out), 'kisisel_veri_iceren_dosya': files_with, 'okunamayan': len(errors)},
                  tables=[('Veri türüne göre', ['Tür', 'Sütun'], Counter(o['veri_turu'] for o in out).most_common())] + ([('Okunamayan dosyalar', ['Dosya', 'Hata'], errors)] if errors else []),
                  notes=['Tarama desen tabanlıdır; sağlık, biyometrik gibi özel nitelikli veriler ve serbest metin içindeki bilgiler ayrıca değerlendirilmelidir.'])


@op('saklama-suresi-kontrol', title='Saklama süresi dolan kayıtları bul (imha/anonimleştirme)', domain='privacy', product=PRIV, tag='saklama süresi',
    samples={'kayit_envanteri.csv': 'kayit_envanteri.csv', 'saklama_politikasi.csv': 'saklama_politikasi.csv'},
    args=[arg('--policy', default='@ornek/saklama_politikasi.csv'), arg('--category', default='kategori'), arg('--as-of', default='2026-10-16')],
    description='Kayıt envanterini saklama ve imha politikasıyla karşılaştırır: kategori başına saklama süresi ve süre başlangıcı (oluşturma veya son işlem) kuralına göre süresi dolmuş kayıtları, uygulanacak işlemi (sil/anonimleştir) ve politikası tanımlı olmayan kategorileri listeler. Periyodik imha raporu için.',
    input='Kayıt envanteri + saklama politikası (kategori, saklama_yil, baslangic, islem)', output='sonuc.csv (süresi dolan kayıtlar)', command='python main.py --input kayitlar.csv --policy politika.csv --as-of 2026-12-31',
    expect={'suresi_dolan': 38, 'politikasiz_kayit': 24})
def saklama(columns, rows, args):
    pc, pr = tabular.read(args.policy)
    for c in ('kategori', 'saklama_yil', 'baslangic'):
        need(c, pc, '--policy')
    policy = {fold(r['kategori']): (float(r['saklama_yil'].replace(',', '.')), r['baslangic'].strip(), r.get('islem', 'imha').strip() or 'imha') for r in pr}
    as_of = day(args.as_of)
    out, missing = [], Counter()
    for r in rows:
        cat = r[need(args.category, columns, '--category')]
        pol = policy.get(fold(cat))
        if not pol:
            missing[cat] += 1
            continue
        years, start_col, action = pol
        start = day(r.get(start_col, ''))
        if not start:
            continue
        due = start + timedelta(days=round(years * 365.25))
        if due <= as_of:
            out.append({**r, 'son_saklama_gunu': due.isoformat(), 'gecen_gun': (as_of - due).days, 'islem': action})
    out.sort(key=lambda o: -o['gecen_gun'])
    return Result(f'{len(out)} kaydın saklama süresi dolmuş; {sum(missing.values())} kaydın kategorisi için politika yok', columns + ['son_saklama_gunu', 'gecen_gun', 'islem'], out,
                  {'suresi_dolan': len(out), 'politikasiz_kayit': sum(missing.values())},
                  tables=[('Politikası olmayan kategoriler', ['Kategori', 'Kayıt'], missing.most_common())],
                  notes=['Yasal saklama yükümlülükleri (VUK, TTK, iş mevzuatı) kategoriye göre değişir; politika dosyasını hukuk biriminizle doğrulayın.'])


@op('kvkk-basvuru-sure', title='KVKK ilgili kişi başvurularının 30 günlük yanıt süresi takibi', domain='privacy', product=PRIV, tag='KVKK başvuru',
    samples={'kvkk_basvurulari.csv': 'kvkk_basvurulari.csv'}, args=[arg('--received', default='basvuru_tarihi'), arg('--answered', default='cevap_tarihi'),
                                                                    arg('--days', type=int, default=30), arg('--as-of', default='2026-10-16')],
    description='İlgili kişi başvurularının (erişim, silme, düzeltme, itiraz) başvuru ve cevap tarihlerinden yasal 30 günlük süreye göre süresinde, geç ve açık-süresi dolmuş/dolmak üzere olanları çıkarır; talep türü bazında ortalama yanıt süresini verir.',
    input='Başvuru kayıtları (başvuru tarihi, cevap tarihi, talep türü)', output='sonuc.csv (başvuru bazında durum)', command='python main.py --as-of 2026-10-31',
    expect={'basvuru': 14, 'gec_cevap': 4, 'acik': 2})
def basvuru(columns, rows, args):
    as_of = day(args.as_of)
    out = []
    for r in rows:
        a, b = day(r[need(args.received, columns, '--received')]), day(r.get(args.answered, ''))
        if not a:
            out.append({**r, 'son_gun': '', 'sure_gun': '', 'durum': 'başvuru tarihi okunamadı'})
            continue
        due = a + timedelta(days=args.days)
        if b:
            state = 'süresinde' if b <= due else 'GEÇ CEVAP'
            dur = (b - a).days
        else:
            state = 'AÇIK – süre doldu' if as_of > due else f'açık – {(due - as_of).days} gün kaldı'
            dur = (as_of - a).days
        out.append({**r, 'son_gun': due.isoformat(), 'sure_gun': dur, 'durum': state})
    late = sum(o['durum'] == 'GEÇ CEVAP' for o in out)
    open_ = sum(o['durum'].startswith(('AÇIK', 'açık')) for o in out)  # str.lower() turns "AÇIK" into "açik", so compare both spellings
    return Result(f'{len(out)} başvuru; {late} geç cevap, {open_} açık ({sum(o["durum"] == "AÇIK – süre doldu" for o in out)} tanesinin süresi dolmuş)', columns + ['son_gun', 'sure_gun', 'durum'], out,
                  {'basvuru': len(out), 'gec_cevap': late, 'acik': open_}, notes=['Süre takvim günü olarak hesaplanır (KVKK md. 13: en geç otuz gün).'])


GENERIC = re.compile(r'^(ortak|shared|test|admin|servis|service|svc|etl|bot|generic|guest)[._-]|[._-](servis|service|svc|test|ortak)$', re.I)
PRIVILEGED = re.compile(r'(admin|yazma|write|owner|gizli|dba|sysadmin)', re.I)


@op('erisim-gozden-gecirme', title='Kullanıcı erişim gözden geçirmesi (ayrılan, yetkili, kullanılmayan hesap)', domain='governance', product=GOV, tag='erişim',
    samples={'kullanici_rolleri.csv': 'kullanici_rolleri.csv', 'ik_aktif_personel.csv': 'ik_aktif_personel.csv'},
    args=[arg('--hr', default='@ornek/ik_aktif_personel.csv'), arg('--user', default='kullanici'), arg('--role', default='rol'), arg('--last-login', default='son_giris'),
          arg('--as-of', default='2026-10-16'), arg('--inactive-days', type=int, default=90), arg('--max-roles', type=int, default=4)],
    description='Sistem kullanıcı–rol dışa aktarımını İK aktif personel listesiyle karşılaştırır: ayrılmış personelin açık hesaplarını, İK’da olmayan (servis/ortak) hesapları, yönetici/yazma yetkilerini, uzun süredir giriş yapmayanları ve çok fazla rol biriktiren kullanıcıları listeler. Periyodik erişim denetimi (SOX, ISO 27001) için.',
    input='Kullanıcı–rol listesi + İK personel listesi', output='sonuc.csv (bulgular)', command='python main.py --input roller.csv --hr ik.csv --inactive-days 60',
    expect={'kullanici': 7, 'ayrilan_aktif_hesap': 1, 'ik_disi_hesap': 2})
def erisim(columns, rows, args):
    hc, hr = tabular.read(args.hr)
    need('kullanici', hc, '--hr')
    hr_state = {r['kullanici'].strip().lower(): r.get('durum', 'aktif').strip().lower() for r in hr}
    as_of = day(args.as_of)
    users = defaultdict(lambda: {'roles': set(), 'login': None})
    for r in rows:
        u = users[r[need(args.user, columns, '--user')].strip().lower()]
        u['roles'].add(r[need(args.role, columns, '--role')].strip())
        d = day(r.get(args.last_login, ''))
        u['login'] = max(u['login'], d) if u['login'] and d else d or u['login']
    out = []
    for name, u in sorted(users.items()):
        state = hr_state.get(name)
        priv = sorted(r for r in u['roles'] if PRIVILEGED.search(r))
        if state and state != 'aktif':
            out.append({'kullanici': name, 'bulgu': 'ayrılmış personelin hesabı açık', 'onem': 'yüksek', 'ayrinti': ', '.join(sorted(u['roles']))})
        if not state:
            out.append({'kullanici': name, 'bulgu': 'İK listesinde yok (servis/ortak hesap?)', 'onem': 'yüksek' if priv else 'orta',
                        'ayrinti': ('ortak hesap adı; ' if GENERIC.search(name) else '') + (f'yetkili roller: {", ".join(priv)}' if priv else 'sahibi belirlenmeli')})
        if u['login'] and (as_of - u['login']).days > args.inactive_days:
            out.append({'kullanici': name, 'bulgu': f"{(as_of - u['login']).days} gündür giriş yok", 'onem': 'orta', 'ayrinti': f"son giriş {u['login'].isoformat()}"})
        if len(u['roles']) > args.max_roles:
            out.append({'kullanici': name, 'bulgu': f"{len(u['roles'])} rol — gereğinden fazla yetki birikmiş olabilir", 'onem': 'orta', 'ayrinti': ', '.join(sorted(u['roles']))})
        if priv and state == 'aktif':
            out.append({'kullanici': name, 'bulgu': 'yönetici/yazma/gizli veri yetkisi — iş gerekçesi teyit edilmeli', 'onem': 'bilgi', 'ayrinti': ', '.join(priv)})
    return Result(f'{len(users)} kullanıcı; {len(out)} bulgu', ['kullanici', 'bulgu', 'onem', 'ayrinti'], out,
                  {'kullanici': len(users), 'bulgu': len(out), 'ayrilan_aktif_hesap': sum(o['bulgu'].startswith('ayrılmış') for o in out),
                   'ik_disi_hesap': sum(o['bulgu'].startswith('İK listesinde') for o in out)})


@op('veri-sahipligi-kapsami', title='Veri varlıklarında sahip, açıklama ve sınıflandırma eksikleri', domain='governance', product=GOV, tag='sahiplik',
    samples={'veri_varliklari.csv': 'veri_varliklari.csv', 'ik_aktif_personel.csv': 'ik_aktif_personel.csv'},
    args=[arg('--hr', default='@ornek/ik_aktif_personel.csv', help='Steward’ların hâlâ çalıştığını kontrol etmek için (isteğe bağlı)'), arg('--max-assets', type=int, default=10)],
    description='Veri kataloğu/varlık listesinde sahibi, steward’ı, açıklaması veya hassasiyet sınıfı eksik varlıkları; ayrılmış çalışanlara atanmış steward’lıkları ve kişi başına çok fazla varlık düşen yoğunlaşmayı raporlar; kapsam yüzdelerini verir.',
    input='Varlık listesi (varlik, sahip, steward, aciklama, siniflandirma)', output='sonuc.csv (eksikler)', command='python main.py --input katalog.csv --hr ik.csv',
    expect={'varlik': 8, 'sahipsiz': 2, 'siniflandirmasiz': 3, 'ayrilan_steward': 1})
def sahiplik(columns, rows, args):
    for c in ('varlik', 'sahip', 'steward', 'aciklama', 'siniflandirma'):
        need(c, columns, 'varlık listesi')
    hr = {}
    if args.hr:
        hc, hrr = tabular.read(args.hr)
        hr = {r['kullanici'].strip().lower(): r.get('durum', '').strip().lower() for r in hrr}
    out, load = [], Counter(r['steward'].strip() for r in rows if r['steward'].strip())
    for r in rows:
        miss = [c for c in ('sahip', 'steward', 'aciklama', 'siniflandirma') if not r[c].strip()]
        steward = r['steward'].strip().lower()
        gone = hr and steward and hr.get(steward, 'yok') != 'aktif'
        if miss or gone:
            out.append({'varlik': r['varlik'], 'eksik': ', '.join(miss), 'steward_durumu': ('ayrılmış/bilinmiyor' if gone else ''), 'oneri': 'sahip ata' if 'sahip' in miss else 'tamamla'})
    total = len(rows)
    cov = {c: round(100 * sum(bool(r[c].strip()) for r in rows) / total, 1) for c in ('sahip', 'steward', 'aciklama', 'siniflandirma')}
    heavy = [[s, n] for s, n in load.items() if n > args.max_assets]
    return Result(f"{total} varlık; kapsam: sahip %{cov['sahip']}, steward %{cov['steward']}, açıklama %{cov['aciklama']}, sınıf %{cov['siniflandirma']}", ['varlik', 'eksik', 'steward_durumu', 'oneri'], out,
                  {'varlik': total, 'sahipsiz': sum('sahip' in o['eksik'].split(', ') for o in out), 'siniflandirmasiz': sum('siniflandirma' in o['eksik'] for o in out),
                   'ayrilan_steward': sum(bool(o['steward_durumu']) for o in out)},
                  tables=[('Kapsam %', ['Alan', '%'], list(cov.items())), ('Yoğunlaşma', ['Steward', 'Varlık'], heavy)])
