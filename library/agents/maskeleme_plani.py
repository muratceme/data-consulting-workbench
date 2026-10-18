"""Maskeleme planı (AI agent): bir tabloyu test ortamına ya da bir tedarikçiye vermeden önce kolon kolon maskeleme yöntemi seçer:
koru, sil, kısmi (e-posta/telefon/TCKN/IBAN/kart), takma ad (tuzlu HMAC; birleştirme anahtarı korunur) ya da genelleştir (tarih → yıl-ay,
sayı → aralık). Kişisel veri kolonları kodla bulunur (kvkk-maskeleme bloğuyla aynı tespit); modele kolon adları, bulunan tür ve maskeli
iki örnek gider. Plan kodla denetlenir ve uygulanır: kişisel veri kolonu “koru” olamaz, kısmi maskeleme yalnızca desteklenen türde,
genelleştirme yalnızca tarih/sayı kolonunda, “anahtar” denen kolon takma adla maskelenmelidir. Maskeleme sonrası tablo yeniden taranır;
herhangi bir kolonda kişisel veri kalırsa plan reddedilir. Çıktı: maskeli.csv, maskeleme_plani.json, maskeleme-plani.md."""
import argparse, secrets
from collections import Counter
import llm
import tabular
from common import here, output_dir, write_text, write_json, md_table, mask_text
from kvkk_maskeleme import detect, partial, pseudonym, residual, PREFIX

METHODS = ('koru', 'sil', 'kismi', 'takma_ad', 'genellestir')
PARTIAL_KINDS = {'e-posta', 'telefon', 'kart no', 'TCKN', 'IBAN', 'doğum tarihi'}


def kind_of(values):
    filled = [v for v in values if v.strip()]
    if filled and all(tabular.parse_date(v) for v in filled):
        return 'tarih'
    style = tabular.number_style(filled)
    if filled and all(tabular.parse_number(v, style) is not None for v in filled):
        return 'sayi'
    return 'metin'


def generalise(value, kind, width):
    if not value.strip():
        return value
    if kind == 'tarih':
        d = tabular.parse_date(value)
        return d[0].strftime('%Y-%m') if d else ''
    v = tabular.parse_number(value, None) or 0
    lo = int(v // width * width)
    return f'{lo}-{lo + width}'


def apply(columns, rows, plan, types, salt):
    out = []
    for r in rows:
        new = dict(r)
        for c in columns:
            m, v = plan[c]['yontem'], r[c]
            if not v.strip() or m == 'koru':
                if m == 'koru':  # free text in kept columns still gets embedded personal data masked
                    new[c] = mask_text(v)[0]
                continue
            if m == 'sil':
                new[c] = ''
            elif m == 'kismi':
                new[c] = partial(v, plan[c]['tur'])
            elif m == 'takma_ad':
                new[c] = pseudonym(v, salt, PREFIX.get(plan[c]['tur'], 'KV'))
            elif m == 'genellestir':
                new[c] = generalise(v, types[c], plan[c].get('aralik') or 1000)
        out.append(new)
    return out


def validator(columns, rows, found, types, salt):
    def validate(obj):
        plan, problems = {}, []
        given = obj.get('kolonlar') or {}
        for c in columns:
            e = given.get(c)
            if not isinstance(e, dict):
                problems.append(f'{c}: yöntem yok')
                continue
            m, kind = str(e.get('yontem', '')).strip(), found.get(c, '')
            if m not in METHODS:
                problems.append(f'{c}: yöntem {", ".join(METHODS)} olmalı')
            elif kind and m == 'koru':
                problems.append(f'{c}: kişisel veri ({kind}) korunamaz')
            elif m == 'kismi' and kind not in PARTIAL_KINDS:
                problems.append(f'{c}: kısmi maskeleme yalnızca {", ".join(sorted(PARTIAL_KINDS))} için ({kind or "kişisel veri değil"})')
            elif m == 'genellestir' and types[c] not in ('tarih', 'sayi'):
                problems.append(f'{c}: genelleştirme yalnızca tarih/sayı kolonunda')
            elif e.get('anahtar') and m != 'takma_ad':
                problems.append(f'{c}: birleştirme anahtarı takma adla maskelenmeli (tutarlılık için)')
            elif m == 'genellestir' and types[c] == 'sayi' and not (isinstance(e.get('aralik'), (int, float)) and e['aralik'] > 0):
                problems.append(f'{c}: sayı genelleştirmede "aralik" (ör. 1000) gerekli')
            else:
                plan[c] = {'yontem': m, 'tur': kind, 'anahtar': bool(e.get('anahtar')), 'aralik': e.get('aralik'), 'gerekce': str(e.get('gerekce', '')).strip()}
        if problems:
            raise ValueError('; '.join(problems))
        masked = apply(columns, rows, plan, types, salt)
        left = residual(columns, masked, found)
        if left:
            raise ValueError('maskeleme sonrası kişisel veri kaldı: ' + ', '.join(f'{c} ({n} hücre)' for c, n in left.items()))
        return plan, masked
    return validate


SYSTEM = (llm.SAFETY + ' Sen KVKK uyumundan sorumlu bir veri mühendisisin. Tablo test ortamına/tedarikçiye verilecek; her kolon için maskeleme yöntemi seç: '
          'koru, sil, kismi (e-posta/telefon/TCKN/IBAN/kart no/doğum tarihi), takma_ad (aynı değer hep aynı takma ada döner; tablolar arası birleştirme '
          'anahtarları için), genellestir (tarih → yıl-ay; sayı → "aralik" genişliğinde aralık). Kişisel veri kolonunu koruma. Analiz için gereken ama kişiyi '
          'belirleyen kolonları genelleştir; işe yaramayanları sil. Birleştirme anahtarlarını "anahtar": true ile işaretle. '
          'Yanıt şeması: {"kolonlar": {"eposta": {"yontem": "kismi", "gerekce": "..."}, "musteri_no": {"yontem": "takma_ad", "anahtar": true}, '
          '"dogum_tarihi": {"yontem": "genellestir"}, "maas": {"yontem": "genellestir", "aralik": 5000}}}')


def main():
    p = argparse.ArgumentParser(description='Kolon kolon maskeleme planı seçen ve uygulayan agent; sonuç kişisel veri taramasından geçmeli.')
    p.add_argument('--input', default=str(here('ornek', 'personel_listesi.csv')))
    p.add_argument('--salt', default='', help='Takma ad tuzu (yoksa MASK_SALT ya da rastgele)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    import os
    salt = args.salt or os.environ.get('MASK_SALT') or secrets.token_hex(16)
    found = detect(columns, rows)
    types = {c: kind_of([r[c] for r in rows]) for c in columns}
    out = output_dir(args.output)
    lines = []
    for c in columns:
        ex = [mask_text(v)[0] for v in dict.fromkeys(r[c] for r in rows if r[c].strip())][:2]
        shown = ', '.join(f'“{v}”' for v in ex) if not found.get(c) else '(kişisel veri; örnek gönderilmedi)'
        lines.append(f'- {c}: tip {types[c]}; kişisel veri: {found.get(c) or "bulunmadı"}; {len(set(r[c] for r in rows))} farklı değer; örnek: {shown}')
    user = f'Tablo: {len(rows)} satır. Kolonlar:\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, found, types, salt))
    if result is None:
        return
    plan, masked = result
    tabular.write_csv(out / 'maskeli.csv', columns, masked)
    write_json(out / 'maskeleme_plani.json', {'kolonlar': plan, 'not': 'Takma ad tuzu dosyaya yazılmaz; aynı tuzla yeniden çalıştırın.'})
    count = Counter(p['yontem'] for p in plan.values())
    md = ['# Maskeleme planı', '', f'{len(columns)} kolon, {len(rows)} satır; kişisel veri bulunan kolon: {len(found)}. Yöntemler: '
          + ', '.join(f'{m} {n}' for m, n in count.most_common()) + '.', '',
          md_table(['Kolon', 'Tip', 'Kişisel veri', 'Yöntem', 'Gerekçe'], [[c, types[c], found.get(c, '') or '—', plan[c]['yontem'] + (' (anahtar)' if plan[c]['anahtar'] else ''), plan[c]['gerekce']] for c in columns]), '',
          'Maskeleme sonrası tarama: temiz (hiçbir kolonda e-posta, telefon, TCKN, IBAN ya da kart numarası kalmadı).', '',
          f'_Model: {meta["model"]} · Plan kodla denetlendi ve uygulandı; maskeli tablo yeniden tarandı. Takma ad verisi KVKK’ya göre hâlâ kişisel veri sayılabilir; '
          'ad/adres kolonları ad ipucundan bulunur, listede olmayan kolon gözden kaçabilir._']
    write_text(out / 'maskeleme-plani.md', '\n'.join(md))
    print(f'[OK] {len(columns)} kolon maskelendi; tarama temiz. Çıktı: {out / "maskeleme-plani.md"}')


if __name__ == '__main__':
    llm.run(main)
