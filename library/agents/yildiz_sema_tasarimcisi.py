"""Yıldız şema tasarımcısı (AI agent): düz bir dosyanın sütun profilini ve sütunlar arası işlevsel bağımlılıkları (her A değerinin tek bir B
değeri olup olmadığı) yerelde ölçer. Model olgu tanecikliğini, boyutları ve hangi sütunun boyuta, olguya (dejenere boyut) ya da dışarıda
kalacağını seçer. Seçim kodla denetlenir: sütunlar dosyada olmalı, bir sütun iki yerde olamaz, ölçüler sayısal olmalı, kişisel veri boyuta
konamaz, her boyutun ilk sütunu diğerlerini veride gerçekten belirlemeli. Onaylanan tasarımı yildiz-sema bloğu DDL ve yükleme SQL’ine çevirir
ve SQLite’ta çalıştırıp satır ve toplamları kaynakla karşılaştırır. Satırlar ve örnek değerler gönderilmez.
Çıktı: tasarim.md, ddl.sql, yukleme.sql, dogrulama.csv."""
import argparse, re
from argparse import Namespace
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from sqlgen import infer_column, snake
import yildiz

PII = re.compile(r'e_?posta|email|mail|telefon|phone|gsm|tckn|iban|kart_no|adres|ad_soyad', re.I)


def profile(columns, rows):
    prof = {}
    for c in columns:
        vals = [r[c].strip() for r in rows]
        filled = [v for v in vals if v]
        prof[c] = {'kind': infer_column(vals)[0], 'farkli': len(set(filled)), 'bos': len(vals) - len(filled), 'kisisel': bool(PII.search(c))}
    deps = []
    cands = [c for c in columns if prof[c]['kind'] == 'text' and 1 < prof[c]['farkli'] < len(rows) and not prof[c]['kisisel']]
    for a in cands:
        for b in cands:
            if a == b or prof[a]['farkli'] < prof[b]['farkli']:
                continue
            seen = {}
            if all(seen.setdefault(r[a].strip(), r[b].strip()) == r[b].strip() for r in rows if r[a].strip()):
                deps.append((a, b))
    return prof, deps


def determines(rows, key, other):
    seen = {}
    bad = set()
    for r in rows:
        k = r[key].strip()
        if seen.setdefault(k, r[other].strip()) != r[other].strip():
            bad.add(k)
    return sorted(bad)


def validator(columns, rows, prof):
    def validate(obj):
        problems, dims, used = [], {}, []
        for d in obj.get('boyutlar') or []:
            if not isinstance(d, dict):
                continue
            name = snake(str(d.get('ad', '')).strip())
            cols = [str(c).strip() for c in d.get('sutunlar') or []]
            if not name or not cols:
                problems.append('her boyutun ad ve sutunlar alanı dolu olmalı')
                continue
            for c in cols:
                if c not in prof:
                    problems.append(f'dim_{name}: dosyada olmayan sütun {c}')
                elif prof[c]['kisisel']:
                    problems.append(f'dim_{name}: {c} kişisel veri; boyuta konamaz, cikarilacaklar’a ekle')
            for c in cols[1:]:
                if cols[0] in prof and c in prof:
                    bad = determines(rows, cols[0], c)
                    if bad:
                        problems.append(f'dim_{name}: {cols[0]} → {c} bağımlılığı veride tutmuyor ({len(bad)} {cols[0]} değeri birden çok {c} ile geçiyor); '
                                        f'{c} için ayrı boyut kurun veya anahtarı değiştirin')
            used += cols
            dims[name] = cols
        if not 1 <= len(dims) <= 8:
            problems.append('1–8 boyut seç')
        date = str(obj.get('tarih') or '').strip()
        if date and (date not in prof or prof[date]['kind'] != 'date'):
            problems.append(f'{date} saatsiz tarih sütunu değil')
        keep = [str(c).strip() for c in obj.get('olguda_kalan') or []]
        drop = [str(c).strip() for c in obj.get('cikarilacaklar') or []]
        for c in keep + drop:
            if c not in prof:
                problems.append(f'dosyada olmayan sütun {c}')
        everything = used + keep + drop + ([date] if date else [])
        dup = sorted({c for c in everything if everything.count(c) > 1})
        if dup:
            problems.append('birden çok yerde kullanılan sütun: ' + ', '.join(dup))
        measures = [c for c in columns if c not in everything and prof[c]['kind'] in ('int', 'bigint', 'decimal')]
        left = [c for c in columns if c not in everything and c not in measures]
        if left:
            problems.append('yeri belirtilmemiş metin sütunu: ' + ', '.join(left) + ' (boyuta, olguda_kalan’a veya cikarilacaklar’a koy)')
        pii_kept = [c for c in keep if prof.get(c, {}).get('kisisel')]
        if pii_kept:
            problems.append('kişisel veri olguda kalamaz: ' + ', '.join(pii_kept))
        if not measures:
            problems.append('olguda en az bir sayısal ölçü kalmalı')
        if not str(obj.get('taneciklik', '')).strip():
            problems.append('"taneciklik" boş (örn. sipariş satırı)')
        if problems:
            raise ValueError('; '.join(dict.fromkeys(problems)))
        return {'dims': dims, 'date': date, 'keep': keep, 'drop': drop, 'measures': measures, 'taneciklik': str(obj['taneciklik']).strip(),
                'olgu_adi': snake(str(obj.get('olgu_adi') or 'olgu')), 'gerekce': str(obj.get('gerekce', '')).strip()}
    return validate


SYSTEM = (llm.SAFETY + ' Sen Kimball yöntemini bilen bir veri ambarı mimarısın. Verilen sütun profili ve ölçülmüş işlevsel bağımlılıklarla düz dosyayı yıldız şemaya böl: '
          'olgu tanecikliğini yaz, her boyutun ilk sütunu doğal anahtar olsun ve diğer sütunlar ona bağımlı olsun (yalnızca listelenen bağımlılıkları kullan), sipariş/fatura no gibi '
          'yüksek değerli kimlikleri olguda_kalan’a (dejenere boyut), kişisel verileri cikarilacaklar’a koy, varsa tarih sütununu ayrı yaz. Sayısal ölçüler otomatik olarak olguda kalır. '
          'Yanıt şeması: {"olgu_adi": "satis", "taneciklik": "...", "boyutlar": [{"ad": "urun", "sutunlar": ["urun", "kategori"]}], "tarih": "siparis_tarihi", '
          '"olguda_kalan": ["siparis_no"], "cikarilacaklar": ["musteri_eposta"], "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='Düz dosyayı ölçülmüş bağımlılıklarla yıldız şemaya bölen ve sonucu SQLite’ta doğrulayan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satis_detay.csv')))
    p.add_argument('--sheet', default=None)
    p.add_argument('--dialect', default='postgres', choices=yildiz.DIALECTS)
    p.add_argument('--context', default='Satış analizi için veri ambarı; raporlar bölge, kanal ve ürün kategorisi kırılımında.', help='Modelin amacı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    if not rows:
        raise ValueError('Dosyada veri satırı yok.')
    prof, deps = profile(columns, rows)
    kind = {'int': 'tam sayı', 'bigint': 'tam sayı', 'decimal': 'ondalık', 'date': 'tarih', 'datetime': 'tarih-saat', 'bool': 'evet/hayır', 'text': 'metin'}
    lines = [f"- {c}: {kind[x['kind']]}, {x['farkli']} farklı değer, {x['bos']} boş" + (', kişisel veri' if x['kisisel'] else '') for c, x in prof.items()]
    user = (f'Amaç: {args.context}\nSatır sayısı: {len(rows)}\n\nSütunlar:\n' + '\n'.join(lines) + '\n\nVeride ölçülen bağımlılıklar (A → B: her A değerinin tek B değeri var):\n'
            + ('\n'.join(f'- {a} → {b}' for a, b in deps) or '- yok'))
    out = output_dir(args.output)
    design, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, prof))
    if design is None:
        return
    spec = ';'.join(f'{n}={",".join(cs)}' for n, cs in design['dims'].items())
    res = yildiz.star(columns, rows, Namespace(dims=spec, drop=','.join(design['drop']), keep=','.join(design['keep']), date=design['date'],
                                               name=design['olgu_adi'], dialect=args.dialect))
    write_text(out / 'ddl.sql', res.files['ddl.sql'])
    write_text(out / 'yukleme.sql', res.files['yukleme.sql'])
    tabular.write_csv(out / 'dogrulama.csv', res.columns, res.rows)
    write_json(out / 'tasarim.json', {**{k: v for k, v in design.items()}, 'dogrulama': res.rows})
    md = [f"# Yıldız şema tasarımı: fct_{design['olgu_adi']}", '', f"**Taneciklik:** {design['taneciklik']}", '', design['gerekce'], '',
          md_table(['Tablo', 'Sütunlar'], [[f'dim_{n}', ', '.join(cs)] for n, cs in design['dims'].items()]
                   + ([['dim_tarih', design['date']]] if design['date'] else []) + [[f"fct_{design['olgu_adi']}", ', '.join(design['keep'] + design['measures'])]]),
          '', f"Modele alınmayan: {', '.join(design['drop']) or 'yok'}", '', '## Doğrulama (üretilen SQL SQLite’ta çalıştırıldı)', '',
          md_table(res.columns, [[r[c] for c in res.columns] for r in res.rows])] + [''] + [f'- {n}' for n in res.notes]
    md += ['', f"_Model: {meta['model']} · Bağımlılıklar veride ölçüldü; şema yildiz-sema bloğuyla üretildi ve doğrulandı._"]
    write_text(out / 'tasarim.md', '\n'.join(md))
    print(f"[OK] fct_{design['olgu_adi']} + {len(design['dims'])} boyut; {res.headline}. Çıktı: {out / 'tasarim.md'}")


if __name__ == '__main__':
    llm.run(main)
