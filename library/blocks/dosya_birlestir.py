"""Dosya birleştirme: klasördeki aylık/şube CSV ve XLSX dışa aktarımlarını tek tabloda toplar. Sütun adlarını
büyük/küçük harf, boşluk ve Türkçe karakter farkına bakmadan eşler, her satıra kaynak dosya adını ekler; dosyalar arası
şema kaymasını (eksik/fazla sütun), boş dosyaları ve dosyalar arası mükerrer satırları raporlar.
İnternete bağlanmaz; girdiler değiştirilmez. Çıktı: birlesik.csv, birlestirme-raporu.md, ozet.json."""
import argparse, re
from collections import Counter
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

FOLD = str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')
PATTERNS = ('*.csv', '*.txt', '*.tsv', '*.xlsx', '*.xlsm')


def key(name):
    """Column identity across files: 'Sipariş Tarihi', 'siparis_tarihi' and 'SIPARIS TARIHI' are the same column."""
    return re.sub(r'[^a-z0-9]+', '_', name.translate(FOLD).casefold()).strip('_')


def find_files(folder, pattern, output):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f'Klasör bulunamadı: {folder}')
    patterns = [pattern] if pattern else PATTERNS
    out = Path(output).resolve()
    files = sorted({f for pat in patterns for f in folder.rglob(pat) if f.is_file() and out not in f.resolve().parents and not f.name.startswith('~$')})
    if not files:
        raise ValueError(f'{folder} içinde CSV/XLSX dosyası yok.')
    return files


def merge(files, folder, sheet=None, strict=False, normalize=False):
    columns, keys, report, merged = [], {}, [], []
    for f in files:
        cols, rows = tabular.read(f, sheet)
        rel = f.relative_to(folder).as_posix()
        mapping = {}
        for c in cols:
            k = key(c)
            if k in mapping.values():
                raise ValueError(f'{rel}: "{c}" başka bir sütunla aynı ada indirgeniyor; sütun adlarını düzeltin.')
            if k not in keys:
                if strict and columns:
                    raise ValueError(f'{rel}: ilk dosyada olmayan sütun "{c}" (--strict).')
                keys[k] = c
                columns.append(c)
            mapping[c] = k
        report.append({'dosya': rel, 'satir': len(rows), 'sutun': len(cols), 'adlar': {mapping[c]: c for c in cols},
                       'values': {c: [r[c] for r in rows if r[c].strip()][:2000] for c in cols}})
        for r in rows:
            merged.append({'kaynak_dosya': rel, **{keys[mapping[c]]: v for c, v in r.items()}})
    all_keys = [key(c) for c in columns]
    for r in report:
        r['eksik'] = [keys[k] for k in all_keys if k not in r['adlar']]
        r['farkli_yazim'] = [f'{v} → {keys[k]}' for k, v in r['adlar'].items() if v != keys[k]]
        if strict and r['eksik']:
            raise ValueError(f"{r['dosya']}: eksik sütun: {', '.join(r['eksik'])} (--strict).")
    styles = {}
    for r in report:
        for k, original in r['adlar'].items():
            style = tabular.number_style(r['values'][original])
            if style:
                styles.setdefault(keys[k], {})[r['dosya']] = style
    mixed = {c: s for c, s in styles.items() if len(set(s.values())) > 1}
    if normalize:
        for c in styles:
            for row in merged:
                n = tabular.parse_number(row.get(c, ''), styles[c].get(row['kaynak_dosya']))
                if n is not None:
                    row[c] = f'{n:.10f}'.rstrip('0').rstrip('.')
    seen = Counter(tuple(row.get(c, '') for c in columns) for row in merged)
    dupes = sum(n - 1 for n in seen.values() if n > 1)
    for r in report:
        del r['values']
    return columns, merged, report, dupes, mixed


def markdown(columns, merged, report, dupes, folder, mixed, normalized):
    drift = [r for r in report if r['eksik'] or r['farkli_yazim']]
    lines = [f'# Dosya birleştirme — {folder}', '',
             f'- Dosya: **{len(report)}** · Toplam satır: **{len(merged)}** · Sütun: **{len(columns)}** (+ kaynak_dosya)',
             f'- Tamamen aynı (mükerrer) satır: **{dupes}**', f"- Şema kayması olan dosya: **{len(drift)}**", '',
             '## Dosyalar', '', md_table(['Dosya', 'Satır', 'Sütun', 'Eksik sütun', 'Farklı yazılan ad'],
                                         [[r['dosya'], r['satir'], r['sutun'], ', '.join(r['eksik']), ', '.join(r['farkli_yazim'])] for r in report])]
    empty = [r['dosya'] for r in report if not r['satir']]
    if empty:
        lines += [f"> Boş dosya: {', '.join(empty)}", '']
    if mixed:
        lines += ['## Ondalık ayırıcı farkı', '', md_table(['Sütun', 'Dosyalara göre biçim'], [[c, ', '.join(f'{f}: {"1.234,56" if v == "tr" else "1,234.56"}' for f, v in s.items())] for c, s in mixed.items()]),
                  'Sayılar düz biçime (1234.56) çevrildi.' if normalized else '> Birleşik dosyada iki biçim karışık duruyor; --normalize-numbers ile sayıları 1234.56 biçimine çevirin.', '']
    lines.append('Eksik sütunlar birleşik dosyada boş bırakıldı. Sütunların tam eşleşmesi gerekiyorsa --strict kullanın.')
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='Klasördeki CSV/XLSX dosyalarını tek tabloda birleştirir (internet bağlantısı yok).')
    p.add_argument('--folder', default=str(here('ornek', 'aylik')))
    p.add_argument('--pattern', help='Yalnızca bu desene uyan dosyalar, örn. "satis_*.xlsx"')
    p.add_argument('--sheet', help='XLSX sayfa adı (varsayılan ilk sayfa)')
    p.add_argument('--strict', action='store_true', help='Sütunlar her dosyada aynı değilse dur')
    p.add_argument('--normalize-numbers', action='store_true', help='Sayısal sütunları 1234.56 biçimine çevir (dosyalar farklı ondalık ayırıcı kullanıyorsa)')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        files = find_files(args.folder, args.pattern, args.output)
        columns, merged, report, dupes, mixed = merge(files, Path(args.folder), args.sheet, args.strict, args.normalize_numbers)
        out = output_dir(args.output)
        tabular.write_csv(out / 'birlesik.csv', ['kaynak_dosya'] + columns, merged)
        write_json(out / 'ozet.json', {'files': [{k: v for k, v in r.items() if k != 'adlar'} for r in report], 'rows': len(merged),
                                       'columns': columns, 'duplicate_rows': dupes,
                                       'mixed_decimal_columns': sorted(mixed), 'numbers_normalized': args.normalize_numbers})
        write_text(out / 'birlestirme-raporu.md', markdown(columns, merged, report, dupes, Path(args.folder).name, mixed, args.normalize_numbers))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    drift = sum(bool(r['eksik']) for r in report)
    print(f"[{'OK' if not drift else '!'}] {len(report)} dosya, {len(merged)} satır birleştirildi; {drift} dosyada eksik sütun, {dupes} mükerrer satır. "
          f"Çıktı: {out / 'birlesik.csv'}")


if __name__ == '__main__':
    main()
