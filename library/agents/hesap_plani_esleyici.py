"""Hesap planı eşleyici (AI agent): şirketin/ERP’nin yerel hesap planını (kod, ad) okur; model her hesabı Tekdüzen Hesap Planı (TDHP)
ana hesabına eşler. Hedef kodlar koddaki TDHP ana hesap listesinden seçilmek zorunda; her yerel hesabın tam bir kez eşlendiği,
eşlenemeyenlerin gerekçeyle işaretlendiği ve hesap sınıfının (1 dönen varlık … 7 maliyet) yerel ad ile çelişmediği yerelde denetlenir:
ör. adında “gider” geçen hesap 1-5 sınıfına, “KDV” geçen hesap 191/391 dışına eşlenirse model düzeltmeye zorlanır. Konsolidasyon ve
mali tablo hazırlığı için taslak eşleme üretir. Çıktı: hesap-eslemesi.csv, hesap-eslemesi.md."""
import argparse, re
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

TDHP = {
    '100': 'Kasa', '101': 'Alınan Çekler', '102': 'Bankalar', '103': 'Verilen Çekler ve Ödeme Emirleri (-)', '108': 'Diğer Hazır Değerler', '110': 'Hisse Senetleri',
    '120': 'Alıcılar', '121': 'Alacak Senetleri', '126': 'Verilen Depozito ve Teminatlar', '127': 'Diğer Ticari Alacaklar', '128': 'Şüpheli Ticari Alacaklar',
    '129': 'Şüpheli Ticari Alacaklar Karşılığı (-)', '131': 'Ortaklardan Alacaklar', '135': 'Personelden Alacaklar', '136': 'Diğer Çeşitli Alacaklar',
    '150': 'İlk Madde ve Malzeme', '151': 'Yarı Mamuller - Üretim', '152': 'Mamuller', '153': 'Ticari Mallar', '157': 'Diğer Stoklar', '159': 'Verilen Sipariş Avansları',
    '180': 'Gelecek Aylara Ait Giderler', '181': 'Gelir Tahakkukları', '190': 'Devreden KDV', '191': 'İndirilecek KDV', '193': 'Peşin Ödenen Vergiler ve Fonlar',
    '195': 'İş Avansları', '196': 'Personel Avansları', '250': 'Arazi ve Arsalar', '252': 'Binalar', '253': 'Tesis, Makine ve Cihazlar', '254': 'Taşıtlar',
    '255': 'Demirbaşlar', '257': 'Birikmiş Amortismanlar (-)', '260': 'Haklar', '268': 'Birikmiş Amortismanlar (-) (maddi olmayan)', '300': 'Banka Kredileri',
    '303': 'Uzun Vadeli Kredilerin Anapara Taksitleri ve Faizleri', '320': 'Satıcılar', '321': 'Borç Senetleri', '326': 'Alınan Depozito ve Teminatlar',
    '329': 'Diğer Ticari Borçlar', '331': 'Ortaklara Borçlar', '335': 'Personele Borçlar', '336': 'Diğer Çeşitli Borçlar', '340': 'Alınan Sipariş Avansları',
    '360': 'Ödenecek Vergi ve Fonlar', '361': 'Ödenecek Sosyal Güvenlik Kesintileri', '370': 'Dönem Kârı Vergi ve Diğer Yasal Yükümlülük Karşılıkları',
    '380': 'Gelecek Aylara Ait Gelirler', '381': 'Gider Tahakkukları', '391': 'Hesaplanan KDV', '400': 'Banka Kredileri (uzun vadeli)',
    '472': 'Kıdem Tazminatı Karşılığı', '500': 'Sermaye', '540': 'Yasal Yedekler', '570': 'Geçmiş Yıllar Kârları', '580': 'Geçmiş Yıllar Zararları (-)',
    '590': 'Dönem Net Kârı', '591': 'Dönem Net Zararı (-)', '600': 'Yurt İçi Satışlar', '601': 'Yurt Dışı Satışlar', '602': 'Diğer Gelirler', '610': 'Satıştan İadeler (-)',
    '611': 'Satış İskontoları (-)', '620': 'Satılan Mamuller Maliyeti (-)', '621': 'Satılan Ticari Mallar Maliyeti (-)', '622': 'Satılan Hizmet Maliyeti (-)',
    '630': 'Araştırma ve Geliştirme Giderleri (-)', '631': 'Pazarlama Satış ve Dağıtım Giderleri (-)', '632': 'Genel Yönetim Giderleri (-)', '642': 'Faiz Gelirleri',
    '646': 'Kambiyo Kârları', '656': 'Kambiyo Zararları (-)', '660': 'Kısa Vadeli Borçlanma Giderleri (-)', '689': 'Diğer Olağan Dışı Gider ve Zararlar (-)',
    '710': 'Direkt İlk Madde ve Malzeme Giderleri', '720': 'Direkt İşçilik Giderleri', '730': 'Genel Üretim Giderleri', '740': 'Hizmet Üretim Maliyeti',
    '750': 'Araştırma ve Geliştirme Giderleri', '760': 'Pazarlama Satış ve Dağıtım Giderleri', '770': 'Genel Yönetim Giderleri', '780': 'Finansman Giderleri'}
CLASSES = {'1': 'dönen varlıklar', '2': 'duran varlıklar', '3': 'kısa vadeli yabancı kaynaklar', '4': 'uzun vadeli yabancı kaynaklar', '5': 'özkaynaklar',
           '6': 'gelir tablosu', '7': 'maliyet hesapları'}


def fold(s):
    return s.translate(str.maketrans('çğıöşüÇĞİÖŞÜI', 'cgiosuCGIOSUI')).lower()


def conflict(name, code):
    """Deterministic sanity rules between the local account name and the chosen TDHP code."""
    n = fold(name)
    if re.search(r'\bkdv\b', n):
        if 'indirilecek' in n or 'devreden' in n:
            return '' if code in ('190', '191') else 'indirilecek/devreden KDV 190 veya 191 olmalı'
        if 'hesaplanan' in n:
            return '' if code == '391' else 'hesaplanan KDV 391 olmalı'
    if re.search(r'gider', n) and 'tahakkuk' not in n and 'gelecek aylar' not in n and code[0] in '12345':
        return 'gider hesabı bilanço sınıfına (1-5) eşlenmiş'
    if re.search(r'satis|hasilat|gelir', n) and 'iade' not in n and 'tahakkuk' not in n and 'gelecek' not in n and code[0] in '12457':
        return 'satış/gelir hesabı gelir tablosu dışına eşlenmiş'
    if re.search(r'amortisman', n) and 'birikmis' in n and code not in ('257', '268'):
        return 'birikmiş amortisman 257 veya 268 olmalı'
    if re.search(r'kasa\b', n) and code != '100':
        return 'kasa 100 olmalı'
    return ''


def validator(accounts):
    codes = {a['kod'] for a in accounts}

    def validate(obj):
        items, problems, by_code = obj.get('eslemeler'), [], {}
        if not isinstance(items, list) or not items:
            raise ValueError('"eslemeler" listesi boş.')
        for it in items:
            if not isinstance(it, dict):
                continue
            k = str(it.get('yerel_kod', '')).strip()
            if k not in codes:
                problems.append(f'listede olmayan yerel hesap: {k or "?"}')
            elif k in by_code:
                problems.append(f'{k} iki kez eşlendi')
            else:
                by_code[k] = it
        missing = [a['kod'] for a in accounts if a['kod'] not in by_code]
        if missing:
            problems.append('eşlenmeyen yerel hesap: ' + ', '.join(missing[:15]))
        out = []
        for a in accounts:
            it = by_code.get(a['kod'])
            if not it:
                continue
            t = str(it.get('tdhp', '')).strip()
            if t in ('', '-', 'yok', 'eşlenemez'):
                if not str(it.get('gerekce', '')).strip():
                    problems.append(f"{a['kod']}: eşlenemiyorsa gerekçe yaz")
                out.append({**a, 'tdhp': '', 'tdhp_adi': '', 'sinif': '', 'guven': 'eşlenemedi', 'gerekce': str(it.get('gerekce', '')).strip()})
                continue
            if t not in TDHP:
                problems.append(f"{a['kod']}: {t} listedeki TDHP ana hesaplarından değil")
                continue
            c = conflict(a['ad'], t)
            if c:
                problems.append(f"{a['kod']} ({a['ad']}) → {t}: {c}")
            out.append({**a, 'tdhp': t, 'tdhp_adi': TDHP[t], 'sinif': CLASSES[t[0]], 'guven': str(it.get('guven', '')).strip() or 'orta',
                        'gerekce': str(it.get('gerekce', '')).strip()})
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return {'rows': out, 'questions': [str(q) for q in obj.get('sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen Türkiye Tekdüzen Hesap Planı’nı (TDHP) iyi bilen bir mali müşavirsin. Her yerel hesabı aşağıdaki listeden bir TDHP ana hesabına eşle; '
          'listede uygun hesap yoksa tdhp alanını "eşlenemez" yap ve gerekçe yaz. Ad belirsizse güven "düşük" de ve sorular listesine ekle. '
          'TDHP ana hesapları: ' + '; '.join(f'{k} {v}' for k, v in TDHP.items()) + '. '
          'Yanıt şeması: {"eslemeler": [{"yerel_kod": "...", "tdhp": "102", "guven": "yüksek|orta|düşük", "gerekce": "..."}], "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Yerel hesap planını TDHP ana hesaplarına eşleyen, eşlemeyi kurallarla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'yerel_hesap_plani.csv')))
    p.add_argument('--code', default='hesap_kodu')
    p.add_argument('--name', default='hesap_adi')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.code, args.name):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    accounts = [{'kod': r[args.code].strip(), 'ad': r[args.name].strip()} for r in rows if r[args.code].strip()]
    out = output_dir(args.output)
    user = 'Yerel hesaplar:\n' + '\n'.join(f"- {a['kod']}: {a['ad']}" for a in accounts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(accounts))
    if result is None:
        return
    rows_out = result['rows']
    tabular.write_csv(out / 'hesap-eslemesi.csv', ['kod', 'ad', 'tdhp', 'tdhp_adi', 'sinif', 'guven', 'gerekce'], rows_out)
    write_json(out / 'hesap-eslemesi.json', result)
    md = ['# Hesap planı eşlemesi (TDHP)', '', f"{len(rows_out)} yerel hesap; {sum(bool(r['tdhp']) for r in rows_out)} eşlendi, {sum(not r['tdhp'] for r in rows_out)} eşlenemedi, "
          f"{sum(r['guven'] == 'düşük' for r in rows_out)} düşük güvenli.", '',
          md_table(['Yerel hesap', 'TDHP', 'Sınıf', 'Güven', 'Gerekçe'], [[f"{r['kod']} {r['ad']}", f"{r['tdhp']} {r['tdhp_adi']}".strip() or '—', r['sinif'], r['guven'], r['gerekce']] for r in rows_out])]
    if result['questions']:
        md += ['', '## Muhasebeye sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Kod listesi TDHP ana hesaplarının sık kullanılan bir alt kümesidir. Eşleme taslaktır; mali müşavir onayı olmadan raporlamada kullanmayın._"]
    write_text(out / 'hesap-eslemesi.md', '\n'.join(md))
    print(f"[OK] {len(rows_out)} hesap eşlendi/işaretlendi. Çıktı: {out / 'hesap-eslemesi.csv'}")


if __name__ == '__main__':
    llm.run(main)
