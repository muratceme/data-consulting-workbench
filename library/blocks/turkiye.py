"""Türkiye'ye özgü referans veriler ve doğrulama algoritmaları (TCKN, VKN, IBAN, plaka, il kodları). Ağ erişimi yoktur."""
import re

ILLER = ['Adana', 'Adıyaman', 'Afyonkarahisar', 'Ağrı', 'Amasya', 'Ankara', 'Antalya', 'Artvin', 'Aydın', 'Balıkesir', 'Bilecik', 'Bingöl', 'Bitlis',
         'Bolu', 'Burdur', 'Bursa', 'Çanakkale', 'Çankırı', 'Çorum', 'Denizli', 'Diyarbakır', 'Edirne', 'Elazığ', 'Erzincan', 'Erzurum', 'Eskişehir',
         'Gaziantep', 'Giresun', 'Gümüşhane', 'Hakkari', 'Hatay', 'Isparta', 'Mersin', 'İstanbul', 'İzmir', 'Kars', 'Kastamonu', 'Kayseri', 'Kırklareli',
         'Kırşehir', 'Kocaeli', 'Konya', 'Kütahya', 'Malatya', 'Manisa', 'Kahramanmaraş', 'Mardin', 'Muğla', 'Muş', 'Nevşehir', 'Niğde', 'Ordu', 'Rize',
         'Sakarya', 'Samsun', 'Siirt', 'Sinop', 'Sivas', 'Tekirdağ', 'Tokat', 'Trabzon', 'Tunceli', 'Şanlıurfa', 'Uşak', 'Van', 'Yozgat', 'Zonguldak',
         'Aksaray', 'Bayburt', 'Karaman', 'Kırıkkale', 'Batman', 'Şırnak', 'Bartın', 'Ardahan', 'Iğdır', 'Yalova', 'Karabük', 'Kilis', 'Osmaniye', 'Düzce']
PLAKA = {i + 1: name for i, name in enumerate(ILLER)}
# Common alternative spellings seen in exports.
IL_ALIAS = {'icel': 'Mersin', 'afyon': 'Afyonkarahisar', 'maras': 'Kahramanmaraş', 'k.maras': 'Kahramanmaraş', 'urfa': 'Şanlıurfa', 's.urfa': 'Şanlıurfa',
            'antep': 'Gaziantep', 'g.antep': 'Gaziantep', 'ist': 'İstanbul', 'ist.': 'İstanbul', 'izmit': 'Kocaeli', 'adapazari': 'Sakarya', 'hakkâri': 'Hakkari'}
# Bank codes in TR IBANs (digits 5-9). Limited to large banks; unknown codes are reported as unknown, not invalid.
BANKALAR = {'00010': 'T.C. Ziraat Bankası', '00012': 'Türkiye Halk Bankası', '00015': 'Türkiye Vakıflar Bankası', '00032': 'Türk Ekonomi Bankası',
            '00046': 'Akbank', '00059': 'Şekerbank', '00062': 'Türkiye Garanti Bankası', '00064': 'Türkiye İş Bankası', '00067': 'Yapı ve Kredi Bankası',
            '00099': 'ING Bank', '00111': 'QNB Bank', '00123': 'HSBC Bank', '00134': 'Denizbank', '00203': 'Albaraka Türk', '00205': 'Kuveyt Türk',
            '00206': 'Türkiye Finans', '00209': 'Ziraat Katılım', '00210': 'Vakıf Katılım'}


def digits(v):
    return re.sub(r'\D', '', str(v))


def tckn(v):
    """'' if valid, otherwise the reason."""
    d = digits(v)
    if len(d) != 11:
        return '11 hane değil'
    if d[0] == '0':
        return 'ilk hane 0'
    n = [int(c) for c in d]
    if (sum(n[0:9:2]) * 7 - sum(n[1:8:2])) % 10 != n[9]:
        return '10. hane tutmuyor'
    if sum(n[:10]) % 10 != n[10]:
        return '11. hane tutmuyor'
    return ''


def vkn(v):
    """Gelir İdaresi vergi kimlik numarası kontrol hanesi."""
    d = digits(v)
    if len(d) != 10:
        return '10 hane değil'
    total = 0
    for i in range(9):
        tmp = (int(d[i]) + 9 - i) % 10
        value = (tmp * 2 ** (9 - i)) % 9
        total += 9 if tmp and not value else value
    return '' if (10 - total % 10) % 10 == int(d[9]) else 'kontrol hanesi tutmuyor'


def iban(v):
    s = re.sub(r'\s+', '', str(v)).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', s):
        return 'biçim hatalı'
    if s.startswith('TR') and len(s) != 26:
        return 'TR IBAN 26 karakter olmalı'
    num = ''.join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return '' if int(num) % 97 == 1 else 'kontrol haneleri (mod 97) tutmuyor'


def iban_bank(v):
    s = re.sub(r'\s+', '', str(v)).upper()
    return BANKALAR.get(s[4:9], 'bilinmiyor') if s.startswith('TR') and len(s) == 26 else ''


def luhn(v):
    d = digits(v)
    if not 12 <= len(d) <= 19:
        return 'uzunluk hatalı'
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return '' if total % 10 == 0 else 'Luhn kontrolü tutmuyor'


PLATE = re.compile(r'^(\d{2})\s*([A-ZÇĞİÖŞÜ]{1,3})\s*(\d{2,5})$')


def plaka(v):
    s = re.sub(r'[\s-]+', ' ', str(v).strip().upper())
    m = PLATE.match(s.replace(' ', '')) or PLATE.match(s)
    if not m:
        return '', 'biçim hatalı'
    il, harf, no = int(m.group(1)), m.group(2), m.group(3)
    if not 1 <= il <= 81:
        return '', 'il kodu 01-81 dışında'
    if re.search('[ÇĞİÖŞÜQWX]', harf):
        return '', 'plakada kullanılmayan harf'
    if len(harf) == 1 and len(no) not in (4, 5) or len(harf) == 2 and len(no) not in (3, 4) or len(harf) == 3 and len(no) not in (2, 3):
        return '', 'harf/rakam sayısı kurala uymuyor'
    return f'{il:02d} {harf} {no}', ''


def fold(s):
    s = str(s).replace('İ', 'i').replace('I', 'ı').lower()
    return re.sub(r'\s+', ' ', s.translate(str.maketrans('çğıöşüâîû', 'cgiosuaiu'))).strip()


IL_INDEX = {fold(n): n for n in ILLER}


def il_bul(v):
    """Return (canonical il, method) for messy input such as 'ISTANBUL', 'istanbul ', '34', 'K.Maraş'."""
    s = str(v).strip()
    if not s:
        return '', ''
    if re.fullmatch(r'\d{1,2}', s) and 1 <= int(s) <= 81:
        return PLAKA[int(s)], 'plaka kodu'
    key = fold(s)
    if key in IL_INDEX:
        return IL_INDEX[key], 'tam' if s == IL_INDEX[key] else 'yazım'
    key2 = key.replace('.', '').replace(' ', '')
    for alias, name in IL_ALIAS.items():
        if key in (alias, alias.replace('.', '')) or key2 == alias.replace('.', ''):
            return name, 'eş ad'
    import difflib
    close = difflib.get_close_matches(key, IL_INDEX, n=1, cutoff=0.8)
    if close:
        return IL_INDEX[close[0]], 'yakın eşleşme'
    return '', 'bulunamadı'
