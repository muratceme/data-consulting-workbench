"""Kalite kuralı kontrolü: rules.json içindeki kuralları CSV/XLSX dışa aktarımına uygular.
İnternete bağlanmaz. Girdi değiştirilmez. Çıktı: sonuc.json, ihlaller.csv, kalite-raporu.md.
--strict ile 'hata' seviyesindeki bir kural bozulursa çıkış kodu 2 olur (CI/zamanlanmış görev için)."""
import argparse, json, re
from collections import Counter
from datetime import datetime
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table, pii_kind
import tabular
from tabular import parse_number, parse_date, number_style

CHECKS = {
    'not_null': 'boş olamaz', 'unique': 'benzersiz olmalı', 'unique_together': 'birlikte benzersiz olmalı',
    'min': 'en az', 'max': 'en çok', 'between': 'aralıkta olmalı', 'allowed': 'izinli değerlerden biri olmalı',
    'regex': 'kalıba uymalı', 'max_length': 'en fazla uzunluk', 'type': 'tipte olmalı', 'not_future': 'gelecek tarih olamaz',
    'no_whitespace': 'başında/sonunda boşluk olmamalı', 'row_count_min': 'en az satır sayısı',
}
TYPES = {'tam sayı', 'ondalık', 'tarih', 'metin'}
MAX_VIOLATIONS = 50000


def validate_rules(spec, columns):
    """Fail early with a readable message; a typo must never silently pass."""
    if not isinstance(spec, dict) or not isinstance(spec.get('rules'), list) or not spec['rules']:
        raise ValueError('rules.json bir "rules" listesi içermeli.')
    problems = []
    for i, rule in enumerate(spec['rules'], start=1):
        check = rule.get('check') if isinstance(rule, dict) else None
        if check not in CHECKS:
            problems.append(f'{i}. kural: bilinmeyen kontrol "{check}". Geçerli: {", ".join(CHECKS)}')
            continue
        cols = rule.get('columns') if check == 'unique_together' else ([rule.get('column')] if check != 'row_count_min' else [])
        if check == 'unique_together' and (not isinstance(cols, list) or len(cols) < 2):
            problems.append(f'{i}. kural: unique_together en az iki sütunlu "columns" listesi ister.')
            continue
        for c in cols:
            if c not in columns:
                problems.append(f'{i}. kural: "{c}" sütunu dosyada yok.')
        value = rule.get('value')
        if check in ('min', 'max', 'max_length', 'row_count_min') and not isinstance(value, (int, float)):
            problems.append(f'{i}. kural: {check} sayısal "value" ister.')
        if check == 'between' and not (isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value) and value[0] <= value[1]):
            problems.append(f'{i}. kural: between [alt, üst] ister.')
        if check == 'allowed' and not (isinstance(value, list) and value):
            problems.append(f'{i}. kural: allowed değer listesi ister.')
        if check == 'type' and value not in TYPES:
            problems.append(f'{i}. kural: type şunlardan biri olmalı: {", ".join(sorted(TYPES))}')
        if check == 'regex':
            try:
                re.compile(value)
            except (re.error, TypeError):
                problems.append(f'{i}. kural: geçersiz regex.')
        if rule.get('severity', 'hata') not in ('hata', 'uyarı'):
            problems.append(f'{i}. kural: severity "hata" veya "uyarı" olmalı.')
    if problems:
        raise ValueError('rules.json geçersiz:\n  - ' + '\n  - '.join(problems))


def describe(rule):
    check, value = rule['check'], rule.get('value')
    target = ' + '.join(rule['columns']) if check == 'unique_together' else rule.get('column', 'tablo')
    text = CHECKS[check]
    if check in ('min', 'max', 'max_length', 'row_count_min', 'type'):
        text += f' {value}'
    elif check == 'between':
        text += f' [{value[0]}, {value[1]}]'
    elif check == 'allowed':
        text += ': ' + ', '.join(map(str, value))
    elif check == 'regex':
        text += f' {value}'
    return f'{target}: {text}'


def is_type(value, kind):
    if kind == 'metin':
        return True
    if kind == 'tarih':
        return parse_date(value) is not None
    n = parse_number(value)
    return n is not None and (kind == 'ondalık' or float(n).is_integer())


def run(columns, rows, spec, today=None):
    validate_rules(spec, columns)
    today = today or datetime.now()
    results, violations = [], []
    for index, rule in enumerate(spec['rules'], start=1):
        check, value, col = rule['check'], rule.get('value'), rule.get('column')
        failed = []  # (row_number, column, value)
        if check == 'row_count_min':
            if len(rows) < value:
                failed.append(('', '', str(len(rows))))
        elif check == 'unique':
            counts = Counter(r[col] for r in rows if r[col].strip())
            failed = [(n, col, r[col]) for n, r in enumerate(rows, start=2) if r[col].strip() and counts[r[col]] > 1]
        elif check == 'unique_together':
            cols = rule['columns']
            counts = Counter(tuple(r[c] for c in cols) for r in rows)
            failed = [(n, '+'.join(cols), ' | '.join(r[c] for c in cols)) for n, r in enumerate(rows, start=2) if counts[tuple(r[c] for c in cols)] > 1]
        else:
            style = number_style([r[col] for r in rows])
            for n, r in enumerate(rows, start=2):
                v = r[col]
                blank = not v.strip()
                if check == 'not_null':
                    bad = blank
                elif blank:
                    continue  # Empty values are only judged by not_null.
                elif check in ('min', 'max', 'between'):
                    x = parse_number(v, style)
                    low, high = (value, None) if check == 'min' else (None, value) if check == 'max' else value
                    bad = x is None or (low is not None and x < low) or (high is not None and x > high)
                elif check == 'allowed':
                    bad = v not in [str(a) for a in value]
                elif check == 'regex':
                    bad = re.fullmatch(value, v) is None
                elif check == 'max_length':
                    bad = len(v) > value
                elif check == 'type':
                    bad = not is_type(v, value)
                elif check == 'not_future':
                    d = parse_date(v)
                    bad = d is None or d[0] > today
                elif check == 'no_whitespace':
                    bad = v != v.strip()
                else:
                    bad = False
                if bad:
                    failed.append((n, col, v))
        severity = rule.get('severity', 'hata')
        results.append({'id': index, 'rule': describe(rule), 'check': check, 'severity': severity,
                        'failed': len(failed), 'passed': not failed, 'note': rule.get('note', '')})
        for n, c, v in failed[:MAX_VIOLATIONS]:
            shown = f'[{pii_kind(v).upper()}]' if pii_kind(v) else v
            violations.append({'kural': index, 'seviye': severity, 'satir': n, 'sutun': c, 'deger': shown, 'aciklama': describe(rule)})
    return results, violations


def markdown(source, rows, results):
    errors = [r for r in results if not r['passed'] and r['severity'] == 'hata']
    warnings = [r for r in results if not r['passed'] and r['severity'] == 'uyarı']
    status = 'GEÇMEDİ' if errors else ('UYARILARLA GEÇTİ' if warnings else 'GEÇTİ')
    lines = [f'# Kalite raporu — {source}', '', f'**Sonuç: {status}** · {len(rows)} satır · {len(results)} kural · '
             f'{len(errors)} hata · {len(warnings)} uyarı', '',
             md_table(['#', 'Kural', 'Seviye', 'İhlal', 'Durum'],
                      [[r['id'], r['rule'], r['severity'], r['failed'], 'geçti' if r['passed'] else 'bozuldu'] for r in results]),
             'Satır numaraları başlık satırı 1 kabul edilerek verilir (Excel’deki satır numarası). Ayrıntı: ihlaller.csv']
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='CSV/XLSX kalite kuralı kontrolü (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')))
    p.add_argument('--rules', default=str(here('ornek', 'rules.json')))
    p.add_argument('--sheet')
    p.add_argument('--output', default='output')
    p.add_argument('--strict', action='store_true', help='Hata seviyesinde ihlal varsa çıkış kodu 2')
    args = p.parse_args()
    try:
        columns, rows = tabular.read(args.input, args.sheet)
        spec = json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
        results, violations = run(columns, rows, spec)
        out = output_dir(args.output)
        write_json(out / 'sonuc.json', {'source': Path(args.input).name, 'rows': len(rows), 'results': results})
        tabular.write_csv(out / 'ihlaller.csv', ['kural', 'seviye', 'satir', 'sutun', 'deger', 'aciklama'], violations)
        write_text(out / 'kalite-raporu.md', markdown(Path(args.input).name, rows, results))
    except json.JSONDecodeError as exc:
        p.exit(1, f'[X] rules.json okunamadı: {exc}\n')
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    errors = sum(1 for r in results if not r['passed'] and r['severity'] == 'hata')
    broken = sum(1 for r in results if not r['passed'])
    print(f"[{'!' if broken else 'OK'}] {len(results)} kural, {broken} bozuldu ({errors} hata), {len(violations)} ihlal satırı. Çıktı: {out / 'kalite-raporu.md'}")
    if args.strict and errors:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
