"""Kolon adı standardı (AI agent): eski sistemlerden gelen tablo kolonlarını (“MusteriAdi”, “SIP_TRH”, “Tutar (TL)”, “aktif?”, “order”)
bir isimlendirme standardına çevirir: küçük harf snake_case, yalnızca ASCII, ayrılmış SQL kelimesi yok, uzunluk sınırı, tarih kolonlarında
tarih eki, mantıksal kolonlarda soru eki/öneki ve tablo içinde tekil ad. Modele yalnızca tablo/kolon adları ve tipleri gider; veri
gönderilmez. Kurala uymayan, eksik ya da çakışan ad kolon kolon gerekçesiyle geri gönderilir. Üretilen yeniden adlandırma ve görünüm
(view) SQL’i agent içinde SQLite’ta orijinal adlarla kurulan boş tablolarda çalıştırılıp sonuç kolon adları doğrulanır.
Çıktı: kolon_esleme.csv, yeniden_adlandir.sql, gorunumler.sql, kolon-standardi.md."""
import argparse, re, sqlite3
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

RESERVED = set('''all alter and any as asc between by case cast check column comment constraint create cross current current_date current_time
current_timestamp current_user date day default delete desc distinct drop else end except exists false fetch for foreign from full grant group
having hour in index inner insert intersect interval into is join key left level like limit minute month natural not null number of offset on only
or order outer over partition percent primary range references revoke right row rows select session_user set size some table then time timestamp
to top trigger true union unique update user using value values view when where window with year'''.split())
DATE_TYPES = re.compile(r'date|time', re.I)
BOOL_TYPES = re.compile(r'^(bool|boolean|bit)$', re.I)
STYLE = {'tr': {'date': ('_tarihi', '_tarih', '_zamani'), 'bool_suffix': ('_mi', '_mu'), 'bool_prefix': ()},
         'en': {'date': ('_date', '_at', '_time'), 'bool_suffix': (), 'bool_prefix': ('is_', 'has_', 'can_')}}


def q(name, dialect):
    return '[' + name.replace(']', ']]') + ']' if dialect == 'sqlserver' else '"' + name.replace('"', '""') + '"'


def validator(schema, lang, max_len):
    style = STYLE[lang]

    def validate(obj):
        got, problems = {}, []
        for e in obj.get('eslemeler') or []:
            if not isinstance(e, dict):
                continue
            t, c, new = str(e.get('tablo', '')).strip(), str(e.get('kolon', '')).strip(), str(e.get('yeni_ad', '')).strip()
            if (t, c) not in schema:
                problems.append(f'şemada olmayan kolon: {t}.{c}')
                continue
            if (t, c) in got:
                problems.append(f'{t}.{c} iki kez eşlendi')
                continue
            typ = schema[(t, c)]
            why = []
            if not re.fullmatch(r'[a-z][a-z0-9]*(_[a-z0-9]+)*', new):
                why.append('küçük harf ASCII snake_case değil (harfle başlar, çift/sondaki alt çizgi yok)')
            if len(new) > max_len:
                why.append(f'{len(new)} karakter (en fazla {max_len})')
            if new in RESERVED:
                why.append('ayrılmış SQL kelimesi')
            if DATE_TYPES.search(typ) and not new.endswith(style['date']):
                why.append(f'tarih/zaman kolonu {" / ".join(style["date"])} ile bitmeli')
            if BOOL_TYPES.match(typ) and not (new.endswith(style['bool_suffix']) if style['bool_suffix'] else new.startswith(style['bool_prefix'])):
                why.append('mantıksal kolon ' + (f'{" / ".join(style["bool_suffix"])} ile bitmeli' if style['bool_suffix'] else f'{" / ".join(style["bool_prefix"])} ile başlamalı'))
            if why:
                problems.append(f'{t}.{c} → “{new}”: ' + ', '.join(why))
            got[(t, c)] = (new, str(e.get('gerekce', '')).strip())
        missing = [f'{t}.{c}' for (t, c) in schema if (t, c) not in got]
        if missing:
            problems.append('eşlenmemiş kolon: ' + ', '.join(missing[:20]))
        names = defaultdict(list)
        for (t, c), (new, _) in got.items():
            names[(t, new)].append(c)
        clash = [f'{t}: {", ".join(cs)} → {new}' for (t, new), cs in names.items() if len(cs) > 1]
        if clash:
            problems.append('tablo içinde aynı ada düşen kolonlar: ' + '; '.join(clash))
        if problems:
            raise ValueError('; '.join(problems[:25]))
        return got
    return validate


SYSTEM = (llm.SAFETY + ' Sen veri ambarı isimlendirme standardı yazan bir veri mimarısın. Her kolon için standarda uygun yeni ad öner: küçük harf, yalnızca '
          'ASCII (ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u), kelimeler alt çizgiyle ayrılır, ayrılmış SQL kelimesi kullanılmaz, anlamı koru, belirsiz kısaltmaları aç '
          '(TRH→tarihi, MUST→musteri), birim varsa ada ekle (tutar_tl). Tip kuralları kullanıcı mesajında. Uygun olan kolonu aynen bırakabilirsin. '
          'Yanıt şeması: {"eslemeler": [{"tablo": "...", "kolon": "...", "yeni_ad": "...", "gerekce": "..."}]}')


def sql_scripts(tables, mapping, dialect):
    rename, views = [], []
    def one(t, old, new):
        if dialect == 'sqlserver':
            return f"EXEC sp_rename N'{t}.{q(old, dialect).replace(chr(39), chr(39) * 2)}', N'{new}', N'COLUMN';"
        return f'ALTER TABLE {q(t, dialect)} RENAME COLUMN {q(old, dialect)} TO {new};'
    for t, cols in tables.items():
        moves = [(c, mapping[(t, c)][0]) for c, _ in cols if mapping[(t, c)][0] != c]
        originals = {c for c, _ in cols}
        if any(new in originals for _, new in moves):  # A→B while B→C: go through temporary names
            rename += [one(t, c, f'tmp_ad_{i}') for i, (c, _) in enumerate(moves)]
            rename += [one(t, f'tmp_ad_{i}', new) for i, (_, new) in enumerate(moves)]
        else:
            rename += [one(t, c, new) for c, new in moves]
        select = ',\n    '.join(f'{q(c, dialect)} AS {mapping[(t, c)][0]}' for c, _ in cols)
        views.append(f'CREATE VIEW {q("stg_" + t.lower(), dialect)} AS\nSELECT\n    {select}\nFROM {q(t, dialect)};')
    return rename, views


def main():
    p = argparse.ArgumentParser(description='Kolon adlarını isimlendirme standardına çeviren, SQL’ini SQLite’ta deneyen agent (yalnızca adlar ve tipler gönderilir).')
    p.add_argument('--input', default=str(here('ornek', 'eski_sema.csv')), help='tablo, kolon, tip sütunlu şema dökümü (information_schema çıktısı)')
    p.add_argument('--lang', choices=['tr', 'en'], default='tr', help='Ad dili ve ek kuralları')
    p.add_argument('--max-len', type=int, default=30, help='Kolon adı uzunluk sınırı (Oracle 30, PostgreSQL 63)')
    p.add_argument('--dialect', choices=['postgres', 'sqlserver', 'snowflake', 'oracle', 'sqlite'], default='postgres')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tablo', 'kolon', 'tip'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    tables, schema = defaultdict(list), {}
    for r in rows:
        t, c, typ = r['tablo'].strip(), r['kolon'].strip(), r['tip'].strip()
        if (t, c) in schema:
            raise ValueError(f'{t}.{c} şemada iki kez var.')
        schema[(t, c)] = typ
        tables[t].append((c, typ))
    rules = [f'Dil: {args.lang}; uzunluk en fazla {args.max_len} karakter.',
             f'Tarih/zaman tipli kolonlar şu eklerden biriyle bitmeli: {", ".join(STYLE[args.lang]["date"])}.',
             'Mantıksal (bool/bit) kolonlar ' + (f'şu eklerden biriyle bitmeli: {", ".join(STYLE[args.lang]["bool_suffix"])}.' if STYLE[args.lang]['bool_suffix']
                                                 else f'şu öneklerden biriyle başlamalı: {", ".join(STYLE[args.lang]["bool_prefix"])}.')]
    user = '\n'.join(rules) + '\n\nŞema (tablo.kolon: tip):\n' + '\n'.join(f'{t}.{c}: {typ}' for (t, c), typ in schema.items())
    out = output_dir(args.output)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(schema, args.lang, args.max_len))
    if result is None:
        return
    rename, views = sql_scripts(tables, result, args.dialect)
    # verify the scripts on empty SQLite tables built with the original names
    con = sqlite3.connect(':memory:')
    for t, cols in tables.items():
        con.execute(f'CREATE TABLE {q(t, "sqlite")} ({", ".join(q(c, "sqlite") + " TEXT" for c, _ in cols)})')
    s_rename, s_views = sql_scripts(tables, result, 'sqlite')
    for stmt in s_views:
        con.execute(stmt)
    for t, cols in tables.items():
        got = [d[0] for d in con.execute(f'SELECT * FROM {q("stg_" + t.lower(), "sqlite")}').description]
        if got != [result[(t, c)][0] for c, _ in cols]:
            raise ValueError(f'stg_{t} görünümünün kolonları beklenenden farklı (iç hata).')
        con.execute(f'DROP VIEW {q("stg_" + t.lower(), "sqlite")}')
    for stmt in s_rename:
        con.execute(stmt)
    for t, cols in tables.items():
        got = [r[1] for r in con.execute(f'PRAGMA table_info({q(t, "sqlite")})')]
        if got != [result[(t, c)][0] for c, _ in cols]:
            raise ValueError(f'{t}: yeniden adlandırma sonrası kolonlar beklenenden farklı (iç hata).')
    con.close()
    changed = sum(1 for k, v in result.items() if v[0] != k[1])
    tabular.write_csv(out / 'kolon_esleme.csv', ['tablo', 'kolon', 'tip', 'yeni_ad', 'gerekce'],
                      [{'tablo': t, 'kolon': c, 'tip': schema[(t, c)], 'yeni_ad': result[(t, c)][0], 'gerekce': result[(t, c)][1]} for (t, c) in schema])
    head = f'-- {args.dialect} · {changed} kolon yeniden adlandırılır. Önce yedek alın; bu kolonları kullanan sorgu, rapor ve ETL işlerini güncelleyin.\n'
    write_text(out / 'yeniden_adlandir.sql', head + '\n'.join(rename) + '\n')
    write_text(out / 'gorunumler.sql', f'-- {args.dialect} · tablolara dokunmadan standart adlarla okumak için görünümler\n\n' + '\n\n'.join(views) + '\n')
    md = ['# Kolon adı standardı', '', f'{len(tables)} tablo, {len(schema)} kolon: {changed} kolonun adı değişiyor, {len(schema) - changed} kolon zaten uygun.', '',
          md_table(['Tablo', 'Kolon', 'Tip', 'Yeni ad', 'Gerekçe'], [[t, c, schema[(t, c)], result[(t, c)][0] if result[(t, c)][0] != c else '(aynı)', result[(t, c)][1]] for (t, c) in schema]), '',
          'İki yol var: tabloyu değiştirmeden **gorunumler.sql** ile `stg_` görünümleri açmak (önerilen geçiş yolu) ya da **yeniden_adlandir.sql** ile kolonları kalıcı yeniden adlandırmak.', '',
          f'_Model: {meta["model"]} · Adlar kurallarla denetlendi; her iki betiğin SQLite karşılığı orijinal adlarla kurulan boş tablolarda çalıştırılıp sonuç kolonları doğrulandı. '
          f'{args.dialect} için üretilen betik o veritabanında çalıştırılarak test edilmedi._']
    write_text(out / 'kolon-standardi.md', '\n'.join(md))
    print(f'[OK] {len(schema)} kolon, {changed} yeni ad; SQL SQLite’ta doğrulandı. Çıktı: {out / "kolon-standardi.md"}')


if __name__ == '__main__':
    llm.run(main)
