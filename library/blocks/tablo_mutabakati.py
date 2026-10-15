"""Tablo mutabakatı: kaynak ve hedef dışa aktarımını anahtar sütun(lar)la satır satır karşılaştırır.
Migrasyon, ETL ve ERP→ambar aktarım doğrulaması içindir. İnternete bağlanmaz; girdiler değiştirilmez.
Çıktı: mutabakat.md, farklar.csv, sadece-kaynakta.csv, sadece-hedefte.csv, ozet.json."""
import argparse
from collections import Counter
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table, pii_kind
import tabular
from tabular import parse_number


def index(rows, keys, label):
    counts = Counter(tuple(r[k].strip() for k in keys) for r in rows)
    dupes = [k for k, c in counts.items() if c > 1]
    if dupes:
        sample = ', '.join(' | '.join(k) for k in dupes[:5])
        raise ValueError(f'{label} dosyasında anahtar benzersiz değil ({len(dupes)} tekrar eden anahtar, örn. {sample}). '
                         'Anahtara sütun ekleyin (--keys a,b) veya önce mükerrerleri giderin.')
    return {tuple(r[k].strip() for k in keys): r for r in rows}


def same(a, b, tolerance, ignore_case):
    a, b = a.strip(), b.strip()
    if a == b:
        return True, None
    x, y = parse_number(a), parse_number(b)
    if x is not None and y is not None:
        return abs(x - y) <= tolerance, y - x
    if ignore_case and a.casefold() == b.casefold():
        return True, None
    return False, None


def shown(value):
    kind = pii_kind(value)
    return f'[{kind.upper()}]' if kind else value


def reconcile(src_cols, src_rows, tgt_cols, tgt_rows, keys, columns=None, tolerance=0.0, ignore_case=False):
    for k in keys:
        if k not in src_cols or k not in tgt_cols:
            raise ValueError(f'Anahtar sütun "{k}" iki dosyada da olmalı.')
    common = [c for c in src_cols if c in tgt_cols and c not in keys]
    compare = columns or common
    for c in compare:
        if c not in common:
            raise ValueError(f'Karşılaştırılacak sütun "{c}" iki dosyada da olmalı.')
    source, target = index(src_rows, keys, 'Kaynak'), index(tgt_rows, keys, 'Hedef')
    only_source = [k for k in source if k not in target]
    only_target = [k for k in target if k not in source]
    diffs, changed = [], set()
    totals = {c: [0.0, 0.0, True] for c in compare}
    for key, s in source.items():
        t = target.get(key)
        for c in compare:
            for side, row in ((0, s), (1, t)):
                if row is not None:
                    n = parse_number(row[c])
                    if row[c].strip() and n is None:
                        totals[c][2] = False
                    elif n is not None:
                        totals[c][side] += n
        if t is None:
            continue
        for c in compare:
            ok, delta = same(s[c], t[c], tolerance, ignore_case)
            if not ok:
                changed.add(key)
                diffs.append({**dict(zip(keys, key)), 'sutun': c, 'kaynak': shown(s[c]), 'hedef': shown(t[c]),
                              'fark': '' if delta is None else round(delta, 6)})
    for key in only_target:
        for c in compare:
            n = parse_number(target[key][c])
            if n is not None:
                totals[c][1] += n
    numeric_totals = [{'sutun': c, 'kaynak': round(a, 6), 'hedef': round(b, 6), 'fark': round(b - a, 6)} for c, (a, b, ok) in totals.items() if ok and (a or b)]
    return {
        'keys': keys, 'compared_columns': compare,
        'only_in_source_columns': [c for c in src_cols if c not in tgt_cols],
        'only_in_target_columns': [c for c in tgt_cols if c not in src_cols],
        'source_rows': len(src_rows), 'target_rows': len(tgt_rows), 'matched_rows': len(source) - len(only_source),
        'identical_rows': len(source) - len(only_source) - len(changed), 'changed_rows': len(changed),
        'only_source': only_source, 'only_target': only_target, 'differences': diffs, 'totals': numeric_totals,
        'source': source, 'target': target,
    }


def markdown(r, src_name, tgt_name, tolerance):
    ok = not (r['only_source'] or r['only_target'] or r['differences'])
    by_col = Counter(d['sutun'] for d in r['differences'])
    lines = [f'# Mutabakat — {src_name} ↔ {tgt_name}', '', f"**Sonuç: {'MUTABIK' if ok else 'FARK VAR'}**", '',
             md_table(['', 'Satır'], [['Kaynak', r['source_rows']], ['Hedef', r['target_rows']], ['Eşleşen anahtar', r['matched_rows']],
                                      ['Birebir aynı', r['identical_rows']], ['Değeri farklı', r['changed_rows']],
                                      ['Yalnız kaynakta', len(r['only_source'])], ['Yalnız hedefte', len(r['only_target'])]]),
             f"Anahtar: **{' + '.join(r['keys'])}** · Karşılaştırılan sütun: {len(r['compared_columns'])} · Sayısal tolerans: {tolerance}", '']
    if r['only_in_source_columns'] or r['only_in_target_columns']:
        lines += ['## Şema farkı', '', f"- Yalnız kaynakta: {', '.join(r['only_in_source_columns']) or '—'}",
                  f"- Yalnız hedefte: {', '.join(r['only_in_target_columns']) or '—'}", '']
    if by_col:
        lines += ['## Farkların sütunlara dağılımı', '', md_table(['Sütun', 'Farklı satır'], by_col.most_common()), '']
    if r['totals']:
        lines += ['## Sayısal sütun toplamları', '', md_table(['Sütun', 'Kaynak', 'Hedef', 'Fark'],
                                                              [[t['sutun'], t['kaynak'], t['hedef'], t['fark']] for t in r['totals']]), '']
    lines.append('Ayrıntılar: farklar.csv, sadece-kaynakta.csv, sadece-hedefte.csv')
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='İki CSV/XLSX dışa aktarımını anahtarla karşılaştırır (internet bağlantısı yok).')
    p.add_argument('--source', default=str(here('ornek', 'kaynak_bakiye.csv')))
    p.add_argument('--target', default=str(here('ornek', 'hedef_bakiye.csv')))
    p.add_argument('--keys', default='hesap_kodu', help='Virgülle ayrılmış anahtar sütun(lar)')
    p.add_argument('--columns', help='Yalnızca bu sütunları karşılaştır (virgülle)')
    p.add_argument('--tolerance', type=float, default=0.0, help='Sayısal farklarda kabul edilen mutlak sapma, örn. 0.01')
    p.add_argument('--ignore-case', action='store_true')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        sc, sr = tabular.read(args.source)
        tc, tr = tabular.read(args.target)
        keys = [k.strip() for k in args.keys.split(',') if k.strip()]
        columns = [c.strip() for c in args.columns.split(',')] if args.columns else None
        r = reconcile(sc, sr, tc, tr, keys, columns, args.tolerance, args.ignore_case)
        out = output_dir(args.output)
        tabular.write_csv(out / 'farklar.csv', keys + ['sutun', 'kaynak', 'hedef', 'fark'], r['differences'])
        tabular.write_csv(out / 'sadece-kaynakta.csv', sc, [r['source'][k] for k in r['only_source']])
        tabular.write_csv(out / 'sadece-hedefte.csv', tc, [r['target'][k] for k in r['only_target']])
        summary = {k: v for k, v in r.items() if k not in ('source', 'target', 'differences', 'only_source', 'only_target')}
        summary.update(only_source=len(r['only_source']), only_target=len(r['only_target']), differences=len(r['differences']))
        write_json(out / 'ozet.json', summary)
        write_text(out / 'mutabakat.md', markdown(r, Path(args.source).name, Path(args.target).name, args.tolerance))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    ok = not (r['only_source'] or r['only_target'] or r['differences'])
    print(f"[{'OK' if ok else '!'}] {r['matched_rows']} eşleşen, {r['changed_rows']} farklı, {len(r['only_source'])} yalnız kaynakta, "
          f"{len(r['only_target'])} yalnız hedefte. Çıktı: {out / 'mutabakat.md'}")


if __name__ == '__main__':
    main()
