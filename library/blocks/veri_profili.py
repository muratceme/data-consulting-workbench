"""Veri profili: CSV/XLSX dışa aktarımının sütun tiplerini, boşlukları, aralıklarını ve kişisel veri izlerini çıkarır.
İnternete bağlanmaz. Girdi değiştirilmez. Çıktı: profile.json (makine için) ve profil.md (insan için)."""
import argparse, re
from collections import Counter
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table, pii_kind
import tabular
from tabular import parse_number, parse_date, number_style

BOOLS = {'true', 'false', 'evet', 'hayır', 'hayir', 'yes', 'no', 'doğru', 'yanlış', 'e', 'h'}


def infer(values):
    """Return (type, parsed list, formats) where type covers at least 95% of non-empty values."""
    if not values:
        return 'boş', [], set()
    lowered = [v.strip().lower() for v in values]
    if all(v in BOOLS for v in lowered) and len(set(lowered)) <= 4:
        return 'mantıksal', [], set()
    style = number_style(values)
    nums = [parse_number(v, style) for v in values]
    ok = [n for n in nums if n is not None]
    if len(ok) >= 0.95 * len(values):
        integer = all(float(n).is_integer() for n in ok) and not any(re.search(r'[.,]\d{1,2}$', v.strip()) for v in values)
        # Leading zeros (postal codes, account numbers) are identifiers, not numbers.
        if any(re.fullmatch(r'0\d+', v.strip()) for v in values):
            return 'metin', [], set()
        return ('tam sayı' if integer else 'ondalık'), ok, set()
    dates = [parse_date(v) for v in values]
    ok_dates = [d for d in dates if d]
    if len(ok_dates) >= 0.95 * len(values):
        return 'tarih', [d[0] for d in ok_dates], {d[1] for d in ok_dates}
    return 'metin', [], set()


def fmt_number(n):
    if n is None:
        return ''
    return str(int(n)) if float(n).is_integer() and abs(n) < 1e15 else f'{n:.4f}'.rstrip('0').rstrip('.')


def profile(columns, rows, top_n=5):
    total = len(rows)
    result = []
    for name in columns:
        raw = [r[name] for r in rows]
        values = [v for v in raw if v.strip()]
        kind, parsed, formats = infer(values)
        counts = Counter(values)
        col = {'name': name, 'type': kind, 'missing': total - len(values),
               'missing_pct': round(100 * (total - len(values)) / total, 2) if total else 0,
               'distinct': len(counts), 'unique': bool(values) and len(counts) == len(values) == total,
               'min_length': min((len(v) for v in values), default=0), 'max_length': max((len(v) for v in values), default=0),
               'warnings': []}
        pii = Counter(k for k in (pii_kind(v) for v in values[:5000]) if k)
        col['pii'] = pii.most_common(1)[0][0] if pii and sum(pii.values()) >= max(1, 0.2 * min(len(values), 5000)) else None
        if kind in ('tam sayı', 'ondalık'):
            col.update(min=min(parsed), max=max(parsed), sum=sum(parsed), mean=sum(parsed) / len(parsed))
            if len(parsed) < len(values):
                col['warnings'].append(f'{len(values) - len(parsed)} değer sayı değil')
            if col['min'] < 0:
                col['warnings'].append('negatif değer var')
        elif kind == 'tarih':
            col.update(min=min(parsed).strftime('%Y-%m-%d'), max=max(parsed).strftime('%Y-%m-%d'))
            if len(formats) > 1:
                col['warnings'].append('birden çok tarih biçimi: ' + ', '.join(sorted(formats)))
            if len(parsed) < len(values):
                col['warnings'].append(f'{len(values) - len(parsed)} değer tarih değil')
        if not col['unique'] and values and len(counts) >= 0.95 * len(values) and len(counts) < len(values):
            col['warnings'].append(f'neredeyse benzersiz: {len(values) - len(counts)} tekrar; anahtar ise mükerrerleri inceleyin')
        if any(v != v.strip() for v in values):
            col['warnings'].append('başında/sonunda boşluk olan değer var')
        if kind == 'metin':
            folded = Counter(v.strip().casefold() for v in values)
            if len(folded) < len(counts) and len(counts) <= 1000:
                col['warnings'].append('yalnızca büyük/küçük harf veya boşlukla ayrışan değerler var')
        # Personal data is reported as a type, never copied into the profile.
        col['top'] = [] if col['pii'] else [{'value': v, 'count': c} for v, c in counts.most_common(top_n)]
        if col['missing'] and total and col['missing'] == total:
            col['warnings'].append('sütun tamamen boş')
        result.append(col)
    seen = Counter(tuple(r[c] for c in columns) for r in rows)
    duplicates = sum(c - 1 for c in seen.values() if c > 1)
    keys = [c['name'] for c in result if c['unique'] and not c['missing']]
    return {'row_count': total, 'column_count': len(columns), 'duplicate_rows': duplicates, 'candidate_keys': keys, 'columns': result}


def markdown(data):
    lines = [f"# Veri profili — {data['source']}", '',
             f"- Satır: **{data['row_count']:,}**".replace(',', '.'),
             f"- Sütun: **{data['column_count']}**",
             f"- Tamamen aynı (mükerrer) satır: **{data['duplicate_rows']}**",
             f"- Aday anahtar sütun(lar): **{', '.join(data['candidate_keys']) or 'yok'}**", '']
    pii = [c for c in data['columns'] if c['pii']]
    if pii:
        lines += ['> **Kişisel veri izi:** ' + ', '.join(f"{c['name']} ({c['pii']})" for c in pii) + '. Bu sütunların değerleri raporda gösterilmez.', '']
    lines += ['## Sütunlar', '', md_table(['Sütun', 'Tip', 'Kişisel veri', 'Boş %', 'Farklı', 'En küçük', 'En büyük', 'Uyarı'], [
        [c['name'], c['type'], c['pii'] or '', c['missing_pct'], c['distinct'],
         fmt_number(c.get('min')) if isinstance(c.get('min'), float) else c.get('min', ''),
         fmt_number(c.get('max')) if isinstance(c.get('max'), float) else c.get('max', ''),
         '; '.join(c['warnings'])] for c in data['columns']])]
    lines += ['## En sık değerler', '']
    for c in data['columns']:
        if c['top']:
            lines.append(f"- **{c['name']}**: " + ', '.join(f"{t['value']} ({t['count']})" for t in c['top']))
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='CSV/XLSX veri profili (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')), help='CSV veya XLSX dosyası')
    p.add_argument('--sheet', help='XLSX sayfa adı (varsayılan ilk sayfa)')
    p.add_argument('--output', default='output', help='Çıktı klasörü')
    args = p.parse_args()
    try:
        columns, rows = tabular.read(args.input, args.sheet)
        if not columns:
            raise tabular.InputError('Dosyada başlık satırı yok.')
        data = {'source': Path(args.input).name, 'sheet': args.sheet, **profile(columns, rows)}
        out = output_dir(args.output)
        write_json(out / 'profile.json', data)
        write_text(out / 'profil.md', markdown(data))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    warn = sum(len(c['warnings']) for c in data['columns'])
    print(f"[OK] {data['row_count']} satır, {data['column_count']} sütun profillendi. Uyarı: {warn}. Çıktı: {out / 'profil.md'}")


if __name__ == '__main__':
    main()
