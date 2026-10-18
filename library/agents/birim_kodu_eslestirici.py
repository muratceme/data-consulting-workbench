"""Birim kodu eşleyici (AI agent): ERP/stok dışa aktarımındaki birim yazımlarını (“AD”, “Kg.”, “KOLİ”, “Sandık”, “Bidon”) e-Fatura ve
e-İrsaliye’nin istediği UN/ECE Recommendation 20 birim kodlarına çevirir. Bilinen yazımları kod kuralla eşler; yalnızca tanınmayanlar
modele gider. Modelin önerdiği her kod GİB UBL-TR kod listesinde olmalıdır; kuralın bildiği bir yazım için farklı kod önermek ve bir
birimi cevapsız bırakmak reddedilir. Çıktı, efatura-uret ve eirsaliye-uret bloklarının --unit-map girdisidir.
Çıktı: birim_esleme.csv, birim-esleme.md."""
import argparse
from collections import Counter
import llm
import tabular
from common import here, output_dir, write_text, md_table
from ebelge import GIB_UNITS, unit_code

HINTS = {'C62': 'adet', 'KGM': 'kilogram', 'GRM': 'gram', 'TNE': 'ton', 'LTR': 'litre', 'MLT': 'mililitre', 'MTR': 'metre', 'CMT': 'santimetre', 'MTK': 'metrekare',
         'MTQ': 'metreküp', 'HUR': 'saat', 'DAY': 'gün', 'MON': 'ay', 'ANN': 'yıl', 'PA': 'paket', 'BX': 'kutu', 'CT': 'koli/karton', 'CS': 'sandık (case)',
         'CR': 'kasa (crate)', 'DR': 'varil (drum)', 'CA': 'teneke kutu (can)', 'BJ': 'kova (bucket)', 'JR': 'kavanoz (jar)', 'BO': 'şişe (bottle)', 'BG': 'torba (bag)',
         'SA': 'çuval (sack)', 'PF': 'palet', 'BE': 'demet/bağ (bundle)', 'RO': 'rulo/top (roll)', 'TU': 'tüp (tube)', 'CY': 'silindir/tüp gaz (cylinder)',
         'PR': 'çift', 'SET': 'set/takım', 'DZN': 'düzine', 'KWH': 'kilovatsaat', 'NAR': 'parça sayısı', 'TN': 'teneke (tin)', 'BB': 'bobin (bobbin)', 'PL': 'pail/kova', 'ZZ': 'karşılıklı tanımlı birim'}


def validator(unknown, known):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('eslemeler') or []:
            if not isinstance(e, dict):
                continue
            unit, code = str(e.get('birim', '')).strip(), str(e.get('kod', '')).strip().upper()
            if unit in known:
                if code != known[unit]:
                    problems.append(f'“{unit}” kuralla {known[unit]} olarak eşlendi; farklı kod önerilemez')
                continue
            if unit not in unknown:
                problems.append(f'listede olmayan birim: {unit or "?"}')
                continue
            if code in ('', 'ESLENEMEZ', 'EŞLENEMEZ'):
                out[unit] = ('', str(e.get('gerekce', '')).strip())
                if not out[unit][1]:
                    problems.append(f'“{unit}” eşlenemez denmiş ama gerekçe yok')
                continue
            if code not in GIB_UNITS:
                problems.append(f'“{unit}”: {code} GİB birim kod listesinde yok')
                continue
            out[unit] = (code, str(e.get('gerekce', '')).strip())
        missing = [u for u in unknown if u not in out]
        if missing:
            problems.append('cevapsız birim: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen e-Fatura ve e-İrsaliye birim kodlarını bilen bir muhasebe/lojistik uzmanısın. Verilen birim yazımlarını UN/ECE Recommendation 20 koduna eşle; '
          'yalnızca GİB UBL-TR kod listesindeki kodları kullan (sık kullanılanlar listede açıklamalarıyla verildi). Ambalaj birimlerinde (sandık, kasa, varil, bidon) ambalajın '
          'kendisini sayan kodu seç. Emin olamadığın birimi uydurma: kod "eşlenemez" yaz ve gerekçe ver (ör. içerik miktarı belirsiz). '
          'Yanıt şeması: {"eslemeler": [{"birim": "Sandık", "kod": "CS", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='ERP birim yazımlarını GİB birim kodlarına eşleyen agent (yalnızca birim yazımları gönderilir).')
    p.add_argument('--input', default=str(here('ornek', 'erp_birimler.csv')))
    p.add_argument('--column', default='birim')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.column not in columns:
        raise ValueError(f'{args.column} sütunu yok. Sütunlar: {", ".join(columns)}')
    counts = Counter(r[args.column].strip() for r in rows if r[args.column].strip())
    known = {u: unit_code(u) for u in counts if unit_code(u)}
    unknown = [u for u in counts if u not in known]
    out = output_dir(args.output)
    result, model = {}, 'kullanılmadı'
    if unknown:
        user = ('Eşlenecek birim yazımları (satır sayısıyla):\n' + '\n'.join(f'- {u} ({counts[u]} satır)' for u in unknown)
                + '\n\nKuralla eşlenenler (değiştirme): ' + ', '.join(f'{u}={c}' for u, c in known.items())
                + '\n\nSık kullanılan GİB birim kodları:\n' + '\n'.join(f'- {c}: {d}' for c, d in HINTS.items()))
        result, meta = llm.ask(args, out, SYSTEM, user, validator(unknown, known))
        if result is None:
            return
        model = meta['model']
    table = [{'birim': u, 'kod': known[u], 'kaynak': 'kural', 'satir': counts[u], 'gerekce': ''} for u in known]
    table += [{'birim': u, 'kod': result[u][0], 'kaynak': 'model' if result[u][0] else 'eşlenemedi', 'satir': counts[u], 'gerekce': result[u][1]} for u in unknown]
    tabular.write_csv(out / 'birim_esleme.csv', ['birim', 'kod', 'kaynak', 'satir', 'gerekce'], [t for t in table if t['kod']])
    left = [t for t in table if not t['kod']]
    md = ['# Birim kodu eşleme', '', f'{len(counts)} farklı birim yazımı: {len(known)} kuralla, {len(unknown) - len(left)} modelle eşlendi, {len(left)} eşlenemedi.', '',
          md_table(['Birim', 'Kod', 'Açıklama', 'Kaynak', 'Satır', 'Gerekçe'], [[t['birim'], t['kod'] or '—', HINTS.get(t['kod'], ''), t['kaynak'], t['satir'], t['gerekce']] for t in table])]
    if left:
        md += ['', 'Eşlenemeyen birimler birim_esleme.csv’ye yazılmadı; bu satırlar e-belge üretiminde hata verir. Kodu elle ekleyin.']
    md += ['', f'_Model: {model} · Kodlar GİB UBL-TR kod listesiyle ({len(GIB_UNITS)} kod) denetlendi. Dosyayı efatura-uret / eirsaliye-uret bloklarına --unit-map ile verin._']
    write_text(out / 'birim-esleme.md', '\n'.join(md))
    print(f'[OK] {len(counts)} birim: {len(known)} kural, {len(unknown) - len(left)} model, {len(left)} eşlenemedi. Çıktı: {out / "birim-esleme.md"}')


if __name__ == '__main__':
    llm.run(main)
