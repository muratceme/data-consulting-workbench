"""Belge alan çıkarıcı (AI agent): sözleşme, teklif ya da yazışma metinlerinden tanımladığınız alanları (taraf, tarih, tutar, süre,
seçenek) tabloya çıkarır. Model her değer için belgeden birebir bir KANIT alıntısı vermek zorundadır ve her değer kodla denetlenir:
alıntı belgede geçmeli; tarih alıntıdaki bir tarihle (15.01.2026, 15/01/2026, 15 Ocak 2026), tutar ve sayı alıntıdaki bir sayıyla
(1.250.000,00 / 1250000) eşleşmeli; seçim alanı izin verilen seçeneklerden biri olmalı; metin değerinin kelimeleri alıntıda geçmeli.
Bulunamayan alan boş bırakılır, uydurulmaz. Kişisel veriler gönderilmeden maskelenir (alıntılar maskeli metne göre denetlenir).
Çıktı: alanlar.csv, kanitlar.csv, belge-alanlari.md."""
import argparse, re
from datetime import date
from pathlib import Path
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table, mask_text

MONTHS = ['ocak', 'subat', 'mart', 'nisan', 'mayis', 'haziran', 'temmuz', 'agustos', 'eylul', 'ekim', 'kasim', 'aralik']
TYPES = ('metin', 'tarih', 'tutar', 'sayi', 'secim')


def norm(text):
    return re.sub(r'\s+', ' ', text).strip()


def dates_in(text):
    t, out = tr.fold(text), set()
    for d, m, y in re.findall(r'\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b', t):
        out.add((int(y), int(m), int(d)))
    for y, m, d in re.findall(r'\b(\d{4})-(\d{2})-(\d{2})\b', t):
        out.add((int(y), int(m), int(d)))
    for d, mon, y in re.findall(r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\b', t):
        out.add((int(y), MONTHS.index(mon) + 1, int(d)))
    return out


def numbers_in(text):
    out = set()
    for raw in re.findall(r'\d[\d.,]*', text):
        raw = raw.rstrip('.,')
        for style in ('tr', 'en'):
            v = tabular.parse_number(raw, style)
            if v is not None:
                out.add(round(v, 2))
    return out


def load_fields(path):
    columns, rows = tabular.read(path)
    for c in ('alan', 'tip'):
        if c not in columns:
            raise ValueError(f'Alan tanımında {c} sütunu yok. Sütunlar: {", ".join(columns)}')
    fields = {}
    for r in rows:
        t = r['tip'].strip().lower()
        if t not in TYPES:
            raise ValueError(f'{r["alan"]}: tip {", ".join(TYPES)} olmalı.')
        opts = [o.strip() for o in (r.get('secenekler') or '').split('|') if o.strip()]
        if t == 'secim' and not opts:
            raise ValueError(f'{r["alan"]}: secim alanı için secenekler (a|b|c) gerekli.')
        fields[r['alan'].strip()] = {'tip': t, 'aciklama': (r.get('aciklama') or '').strip(), 'secenekler': opts}
    return fields


def check(field, value, quote, text):
    """Return an error message or None."""
    if not value:
        return None if not quote or norm(quote) in text else 'alıntı belgede geçmiyor'
    if not quote:
        return 'kanıt alıntısı yok'
    if norm(quote) not in text:
        return 'alıntı belgede birebir geçmiyor'
    t = field['tip']
    if t == 'tarih':
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return 'tarih YYYY-AA-GG biçiminde olmalı'
        if (d.year, d.month, d.day) not in dates_in(quote):
            return f'{value} alıntıdaki tarihlerle uyuşmuyor'
    elif t in ('tutar', 'sayi'):
        v = tabular.parse_number(value.replace(' ', ''), 'en' if re.fullmatch(r'-?\d+(\.\d+)?', value.strip()) else None)
        if v is None:
            return 'sayı okunamadı'
        if round(v, 2) not in numbers_in(quote):
            return f'{value} alıntıdaki sayılarla uyuşmuyor'
    elif t == 'secim':
        if value not in field['secenekler']:
            return f'seçenek {"/".join(field["secenekler"])} olmalı'
    else:
        have = set(re.split(r'[^0-9a-z]+', tr.fold(quote)))
        missing = [w for w in re.split(r'[^0-9a-z]+', tr.fold(value)) if w and w not in have]
        if missing:
            return f'değerin kelimeleri alıntıda yok ({", ".join(missing[:4])})'
    return None


def validator(docs, fields):
    def validate(obj):
        problems, out = [], {}
        for b in obj.get('belgeler') or []:
            if not isinstance(b, dict):
                continue
            name = str(b.get('belge', '')).strip()
            if name not in docs or name in out:
                problems.append(f'belge “{name}” yok ya da iki kez yazıldı')
                continue
            got = b.get('alanlar') or {}
            res = {}
            for f, spec in fields.items():
                e = got.get(f)
                if not isinstance(e, dict):
                    problems.append(f'{name}.{f}: alan yok (bulunamadıysa deger boş bırak)')
                    continue
                value, quote = str(e.get('deger') or '').strip(), str(e.get('kanit') or '').strip()
                err = check(spec, value, quote, docs[name])
                if err:
                    problems.append(f'{name}.{f} = “{value}”: {err}')
                res[f] = (value, quote)
            out[name] = res
        missing = [d for d in docs if d not in out]
        if missing:
            problems.append('yazılmayan belge: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen sözleşme ve iş belgelerinden bilgi çıkaran bir analistsin. Her belge için istenen her alanı doldur: "deger" ve belgeden birebir '
          'kopyalanmış kısa bir "kanit" alıntısı (değeri içeren cümle parçası). Tarihleri YYYY-AA-GG, tutar ve sayıları nokta ondalıklı düz sayı (1250000.50) '
          'olarak yaz; seçim alanlarında yalnızca verilen seçenekleri kullan. Belgede yoksa deger ve kanit boş kalsın, tahmin etme. '
          'Yanıt şeması: {"belgeler": [{"belge": "a.txt", "alanlar": {"alan_adi": {"deger": "...", "kanit": "..."}}}]}')


def main():
    p = argparse.ArgumentParser(description='Belgelerden kanıt alıntılı alan çıkaran agent; her değer alıntıyla kodla denetlenir.')
    p.add_argument('--input', default=str(here('ornek', 'sozlesme_metinleri')), help='.txt/.md belgeler klasörü ya da tek dosya')
    p.add_argument('--fields', default=str(here('ornek', 'sozlesme_alanlari.csv')), help='alan, tip (metin/tarih/tutar/sayi/secim), aciklama, secenekler (a|b)')
    p.add_argument('--max-chars', type=int, default=60000)
    llm.add_arguments(p)
    args = p.parse_args()
    fields = load_fields(args.fields)
    src = Path(args.input)
    files = sorted(f for f in (src.rglob('*') if src.is_dir() else [src]) if f.suffix.lower() in ('.txt', '.md'))
    if not files:
        raise ValueError(f'{src}: .txt/.md belge yok.')
    docs = {f.name: norm(mask_text(f.read_text(encoding='utf-8', errors='replace'))[0]) for f in files}
    total = sum(len(t) for t in docs.values())
    if total > args.max_chars:
        raise ValueError(f'Belgeler {total} karakter; tek istekte en fazla {args.max_chars}. Klasörü bölün ya da --max-chars verin.')
    out = output_dir(args.output)
    user = ('İstenen alanlar:\n' + '\n'.join(f'- {f} ({s["tip"]}{": " + "/".join(s["secenekler"]) if s["secenekler"] else ""}): {s["aciklama"]}' for f, s in fields.items())
            + '\n\n' + '\n\n'.join(f'=== {name} ===\n{text}' for name, text in docs.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(docs, fields))
    if result is None:
        return
    wide = [{'belge': d, **{f: result[d][f][0] for f in fields}} for d in docs]
    tabular.write_csv(out / 'alanlar.csv', ['belge'] + list(fields), wide)
    tabular.write_csv(out / 'kanitlar.csv', ['belge', 'alan', 'deger', 'kanit'],
                      [{'belge': d, 'alan': f, 'deger': result[d][f][0], 'kanit': result[d][f][1]} for d in docs for f in fields])
    empty = [(d, f) for d in docs for f in fields if not result[d][f][0]]
    md = ['# Belge alanları', '', f'{len(docs)} belge × {len(fields)} alan; {len(docs) * len(fields) - len(empty)} değer bulundu, {len(empty)} alan belgede yok.', '',
          md_table(['Belge'] + list(fields), [[w['belge']] + [w[f] or '—' for f in fields] for w in wide]), '']
    if empty:
        md += ['Bulunamayanlar: ' + ', '.join(f'{d} · {f}' for d, f in empty) + '.', '']
    md += [f'_Model: {meta["model"]} · Her değer belgedeki birebir alıntıyla kodla denetlendi (tarih ve sayılar alıntıdan yeniden okundu). Alıntının doğru '
           'maddeden alındığı, yani anlamın doğruluğu, kanitlar.csv üzerinden elle gözden geçirilmelidir._']
    write_text(out / 'belge-alanlari.md', '\n'.join(md))
    print(f'[OK] {len(docs)} belge, {len(fields)} alan; {len(empty)} alan boş. Çıktı: {out / "belge-alanlari.md"}')


if __name__ == '__main__':
    llm.run(main)
