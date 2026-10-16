"""Şema farkı: iki DDL dosyasını (CREATE TABLE) karşılaştırır. Eklenen/silinen tabloları ve sütunları, tip ve NULL
değişikliklerini, olası yeniden adlandırmaları bulur; veri kaybı riski olanları işaretler ve gözden geçirilecek bir ALTER taslağı yazar.
Ortam karşılaştırma (dev ↔ prod) ve sürüm geçişi incelemesi içindir. İnternete bağlanmaz; hiçbir veritabanında komut çalıştırmaz.
Çıktı: sema-farki.md, farklar.csv, alter-taslak.sql, fark.json."""
import argparse, re
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

CREATE = re.compile(r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\w*\s+|TRANSIENT\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\[\]"`]+)\s*\(', re.I)
CONSTRAINT = re.compile(r'^(PRIMARY|FOREIGN|CONSTRAINT|UNIQUE|CHECK|INDEX|KEY)\b', re.I)


def display(name):
    return '.'.join(part.strip('[]"`') for part in re.split(r'\s*\.\s*', name.strip()))


def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'--[^\n]*', '', text)


def schema_from_ddl(text):
    """{table: [{'name', 'type', 'nullable', 'pk'}]} from CREATE TABLE statements (comments ignored, nested parentheses safe)."""
    text = _strip_comments(text)
    tables = {}
    for m in CREATE.finditer(text):
        depth, buf, parts, i = 1, '', [], m.end()
        while i < len(text) and depth:
            ch = text[i]
            depth += (ch == '(') - (ch == ')')
            if depth and ch == ',' and depth == 1:
                parts.append(buf)
                buf = ''
            elif depth:
                buf += ch
            i += 1
        parts.append(buf)
        cols, pk = [], set()
        for part in (p.strip() for p in parts):
            if not part:
                continue
            if CONSTRAINT.match(part):
                key = re.search(r'PRIMARY\s+KEY\s*\(([^)]*)\)', part, re.I)
                if key:
                    pk.update(c.strip().strip('[]"`').lower() for c in key.group(1).split(','))
                continue
            bits = part.split(None, 1)
            rest = bits[1] if len(bits) > 1 else ''
            col_type = re.split(r'\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY|IDENTITY|CONSTRAINT|REFERENCES|UNIQUE|CHECK|COLLATE|GENERATED|AUTO_INCREMENT)\b', rest, maxsplit=1, flags=re.I)[0]
            inline_pk = bool(re.search(r'\bPRIMARY\s+KEY\b', rest, re.I))
            cols.append({'name': bits[0].strip('[]"`'), 'type': re.sub(r'\s+', ' ', col_type.strip()),
                         'nullable': not re.search(r'\bNOT\s+NULL\b', rest, re.I) and not inline_pk, 'pk': inline_pk})
        for c in cols:
            if c['name'].lower() in pk:
                c['pk'], c['nullable'] = True, False
        tables[display(m.group(1))] = cols
    return tables


def load(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'DDL dosyası bulunamadı: {p}')
    files = sorted(p.rglob('*.sql')) if p.is_dir() else [p]
    tables = {}
    for f in files:
        tables.update(schema_from_ddl(f.read_text(encoding='utf-8-sig', errors='replace')))
    if not tables:
        raise ValueError(f'{p.name}: CREATE TABLE bulunamadı.')
    return tables


def base_type(t):
    m = re.match(r'\s*([A-Za-z ]+?)\s*(?:\(([^)]*)\))?\s*$', t)
    if not m:
        return t.lower(), []
    args = [a.strip().lower() for a in (m.group(2) or '').split(',') if a.strip()]
    return re.sub(r'\s+', ' ', m.group(1).lower()), args


def narrowing(old, new):
    """True when the new type may lose data (shorter text, smaller precision/scale, different family)."""
    (ob, oa), (nb, na) = base_type(old), base_type(new)
    text = ('char', 'varchar', 'nchar', 'nvarchar', 'text', 'string', 'varchar2', 'nvarchar2', 'character varying')
    ints = ('tinyint', 'smallint', 'int', 'integer', 'bigint')
    if ob in text and nb in text:
        if 'max' in oa and 'max' not in na and na:
            return True
        if oa and na and oa[0].isdigit() and na[0].isdigit():
            return int(na[0]) < int(oa[0])
        return bool(na) and not oa
    if ob in ints and nb in ints:
        return ints.index(nb) < ints.index(ob)
    if ob in ('decimal', 'numeric', 'number') and nb == ob and oa and na:
        op, os_ = int(oa[0]), int(oa[1]) if len(oa) > 1 else 0
        np_, ns = int(na[0]), int(na[1]) if len(na) > 1 else 0
        return np_ - ns < op - os_ or ns < os_
    if (ob, nb) in WIDENING or ob in ints and nb in ('decimal', 'numeric', 'number', 'float', 'double', 'real'):
        return False
    return ob != nb


WIDENING = {('date', 'datetime'), ('date', 'datetime2'), ('date', 'timestamp'), ('datetime', 'datetime2'), ('smalldatetime', 'datetime2'),
            ('smalldatetime', 'datetime'), ('char', 'varchar'), ('nchar', 'nvarchar'), ('varchar', 'nvarchar'), ('char', 'nvarchar'),
            ('real', 'float'), ('float', 'double'), ('timestamp', 'timestamptz')}


def compare(old, new):
    lower_old = {t.lower(): t for t in old}
    lower_new = {t.lower(): t for t in new}
    diffs = []
    for key in sorted(set(lower_old) - set(lower_new)):
        diffs.append({'tablo': lower_old[key], 'sutun': '', 'degisiklik': 'tablo silindi', 'eski': f'{len(old[lower_old[key]])} sütun', 'yeni': '', 'risk': 'veri kaybı'})
    for key in sorted(set(lower_new) - set(lower_old)):
        diffs.append({'tablo': lower_new[key], 'sutun': '', 'degisiklik': 'tablo eklendi', 'eski': '', 'yeni': f'{len(new[lower_new[key]])} sütun', 'risk': ''})
    for key in sorted(set(lower_old) & set(lower_new)):
        table = lower_new[key]
        oc = {c['name'].lower(): c for c in old[lower_old[key]]}
        nc = {c['name'].lower(): c for c in new[table]}
        removed = [oc[k] for k in oc if k not in nc]
        added = [nc[k] for k in nc if k not in oc]
        renames = []
        for r in list(removed):
            same = [a for a in added if base_type(a['type']) == base_type(r['type'])]
            if len(same) == 1 and len([x for x in removed if base_type(x['type']) == base_type(r['type'])]) == 1:
                renames.append((r, same[0]))
                removed.remove(r)
                added.remove(same[0])
        for r, a in renames:
            diffs.append({'tablo': table, 'sutun': a['name'], 'degisiklik': 'olası yeniden adlandırma', 'eski': r['name'], 'yeni': a['name'],
                          'risk': 'kontrol et: DROP+ADD olarak uygulanırsa veri kaybolur'})
        for c in removed:
            diffs.append({'tablo': table, 'sutun': c['name'], 'degisiklik': 'sütun silindi', 'eski': c['type'], 'yeni': '', 'risk': 'veri kaybı'})
        for c in added:
            risk = 'mevcut satırlar için DEFAULT gerekir' if not c['nullable'] else ''
            diffs.append({'tablo': table, 'sutun': c['name'], 'degisiklik': 'sütun eklendi', 'eski': '', 'yeni': c['type'] + ('' if c['nullable'] else ' NOT NULL'), 'risk': risk})
        for k in oc.keys() & nc.keys():
            o, n = oc[k], nc[k]
            if base_type(o['type']) != base_type(n['type']):
                diffs.append({'tablo': table, 'sutun': n['name'], 'degisiklik': 'tip değişti', 'eski': o['type'], 'yeni': n['type'],
                              'risk': 'daralma: veri kesilebilir' if narrowing(o['type'], n['type']) else ''})
            if o['nullable'] and not n['nullable']:
                diffs.append({'tablo': table, 'sutun': n['name'], 'degisiklik': 'NOT NULL oldu', 'eski': 'NULL', 'yeni': 'NOT NULL', 'risk': 'boş değerli satırlar hata verir'})
            elif not o['nullable'] and n['nullable']:
                diffs.append({'tablo': table, 'sutun': n['name'], 'degisiklik': 'NULL kabul ediyor', 'eski': 'NOT NULL', 'yeni': 'NULL', 'risk': ''})
    return diffs


def alter_draft(diffs, new):
    lines = ['-- sema-farki tarafından üretilen GÖZDEN GEÇİRME TASLAĞI. Doğrudan çalıştırmayın: lehçe, sıra ve veri taşıma adımlarını kontrol edin.', '']
    by_table = {t.lower(): {c['name'].lower(): c for c in cols} for t, cols in new.items()}
    for d in diffs:
        t, c, kind = d['tablo'], d['sutun'], d['degisiklik']
        if kind == 'tablo eklendi':
            cols = ',\n'.join(f"    {x['name']} {x['type']}{'' if x['nullable'] else ' NOT NULL'}" for x in new[t])
            lines.append(f'CREATE TABLE {t} (\n{cols}\n);')
        elif kind == 'tablo silindi':
            lines.append(f'-- DİKKAT veri kaybı: DROP TABLE {t};')
        elif kind == 'sütun eklendi':
            col = by_table[t.lower()][c.lower()]
            default = '' if col['nullable'] else '  -- NOT NULL için DEFAULT veya önce doldurma adımı ekleyin'
            lines.append(f"ALTER TABLE {t} ADD {c} {col['type']}{'' if col['nullable'] else ' NOT NULL'};{default}")
        elif kind == 'sütun silindi':
            lines.append(f'-- DİKKAT veri kaybı: ALTER TABLE {t} DROP COLUMN {c};')
        elif kind == 'olası yeniden adlandırma':
            lines.append(f"-- Yeniden adlandırma ise (SQL Server: EXEC sp_rename '{t}.{d['eski']}', '{c}', 'COLUMN';)")
            lines.append(f"ALTER TABLE {t} RENAME COLUMN {d['eski']} TO {c};")
        elif kind == 'tip değişti':
            warn = '  -- DİKKAT: ' + d['risk'] if d['risk'] else ''
            lines.append(f"ALTER TABLE {t} ALTER COLUMN {c} {d['yeni']};{warn}")
        elif kind == 'NOT NULL oldu':
            lines.append(f'-- Önce boşları doldurun: UPDATE {t} SET {c} = <değer> WHERE {c} IS NULL;')
            lines.append(f'ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL;')
        elif kind == 'NULL kabul ediyor':
            lines.append(f'ALTER TABLE {t} ALTER COLUMN {c} DROP NOT NULL;')
    return '\n'.join(lines)


def markdown(diffs, old_name, new_name, old, new):
    risky = [d for d in diffs if d['risk']]
    lines = [f'# Şema farkı — {old_name} → {new_name}', '',
             f'- Tablo: {len(old)} → {len(new)} · Fark: **{len(diffs)}** · Riskli: **{len(risky)}**', '']
    if not diffs:
        return '\n'.join(lines + ['**İki şema aynı.**'])
    if risky:
        lines += ['## Önce bunlara bakın', '', md_table(['Tablo', 'Sütun', 'Değişiklik', 'Eski', 'Yeni', 'Risk'],
                                                         [[d['tablo'], d['sutun'], d['degisiklik'], d['eski'], d['yeni'], d['risk']] for d in risky])]
    lines += ['## Tüm farklar', '', md_table(['Tablo', 'Sütun', 'Değişiklik', 'Eski', 'Yeni'],
                                             [[d['tablo'], d['sutun'], d['degisiklik'], d['eski'], d['yeni']] for d in diffs]),
              'ALTER taslağı: alter-taslak.sql (gözden geçirmeden çalıştırmayın).']
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='İki DDL (CREATE TABLE) dosyasını/klasörünü karşılaştırır (internet bağlantısı yok).')
    p.add_argument('--old', default=str(here('ornek', 'sema_v1.sql')), help='Eski / kaynak ortam DDL dosyası veya klasörü')
    p.add_argument('--new', default=str(here('ornek', 'sema_v2.sql')), help='Yeni / hedef ortam DDL dosyası veya klasörü')
    p.add_argument('--strict', action='store_true', help='Riskli fark varsa 2 koduyla çık (CI için)')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        old, new = load(args.old), load(args.new)
        diffs = compare(old, new)
        out = output_dir(args.output)
        tabular.write_csv(out / 'farklar.csv', ['tablo', 'sutun', 'degisiklik', 'eski', 'yeni', 'risk'], diffs)
        write_text(out / 'alter-taslak.sql', alter_draft(diffs, new))
        write_json(out / 'fark.json', {'old': Path(args.old).name, 'new': Path(args.new).name, 'differences': diffs,
                                       'risky': sum(bool(d['risk']) for d in diffs)})
        write_text(out / 'sema-farki.md', markdown(diffs, Path(args.old).name, Path(args.new).name, old, new))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    risky = sum(bool(d['risk']) for d in diffs)
    print(f"[{'OK' if not risky else '!'}] {len(diffs)} fark, {risky} riskli. Çıktı: {out / 'sema-farki.md'}")
    if args.strict and risky:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
