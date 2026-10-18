"""Tip dönüşüm eşleyici (AI agent): Oracle/SQL Server’dan PostgreSQL, Snowflake ya da SQL Server’a taşınan kolonlar için hedef veri tipini
seçer. Girdi, kaynak tipin yanında kaynakta ölçülmüş değerleri içerir (en uzun metin, en büyük tam sayı hanesi, ondalık hanesi, saat bilgisi
var mı, Türkçe/Unicode karakter var mı, yalnızca 0/1 mi). Modelin her tipi kodla denetlenir: hedef lehçede geçerli bir tip olmalı, aynı tip
ailesinde kalmalı, ölçülen değeri sığdırmalı (varchar uzunluğu, numeric hassasiyeti, tamsayı aralığı), saat içeren Oracle DATE tarihe
düşürülemez, Unicode metin SQL Server’da varchar olamaz, boolean yalnızca 0/1 kolonlarda. Veri kaybı riski olan öneri kolon kolon geri
gönderilir. Çıktı: tip_esleme.csv, hedef_ddl.sql, tip-donusum.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

INT_DIGITS = {'tinyint': 2, 'smallint': 4, 'integer': 9, 'int': 9, 'bigint': 18}
TARGETS = {
    'postgres': {'int': ['smallint', 'integer', 'bigint'], 'dec': ['numeric'], 'str': ['varchar', 'text', 'char'], 'date': ['date'],
                 'ts': ['timestamp', 'timestamptz'], 'bool': ['boolean'], 'bin': ['bytea'], 'float': ['double precision', 'real']},
    'snowflake': {'int': [], 'dec': ['number'], 'str': ['varchar'], 'date': ['date'], 'ts': ['timestamp_ntz', 'timestamp_tz'], 'bool': ['boolean'],
                  'bin': ['binary'], 'float': ['float']},
    'sqlserver': {'int': ['tinyint', 'smallint', 'int', 'bigint'], 'dec': ['decimal', 'numeric'], 'str': ['varchar', 'nvarchar'], 'date': ['date'],
                  'ts': ['datetime2', 'datetimeoffset'], 'bool': ['bit'], 'bin': ['varbinary'], 'float': ['float']},
}


def source_family(t):
    t = t.lower()
    if re.match(r'(n?varchar2?|n?char|clob|nclob|text|ntext|long)\b', t):
        return 'str'
    if re.match(r'(blob|raw|long raw|varbinary|binary|image)\b', t):
        return 'bin'
    if re.match(r'(binary_double|binary_float|float|real|double)\b', t):
        return 'float'
    if re.match(r'(number|numeric|decimal|int|integer|smallint|bigint|tinyint|money)\b', t):
        return 'num'
    if re.match(r'(timestamp|datetime|datetime2|smalldatetime)\b', t):
        return 'ts'
    if t.startswith('date'):
        return 'date'
    if t.startswith('bit') or t.startswith('bool'):
        return 'bool'
    return 'other'


def parse_target(t):
    m = re.fullmatch(r'\s*([a-z_ ]+?)\s*(?:\(\s*(max|\d+)\s*(?:,\s*(\d+))?\s*\))?\s*', t.lower())
    if not m:
        return None
    return m.group(1).strip(), m.group(2), m.group(3)


def check(col, target_type, dialect):
    parsed = parse_target(target_type)
    if not parsed:
        return 'tip okunamadı'
    name, a, b = parsed
    fams = {f for f, names in TARGETS[dialect].items() if name in names}
    if not fams:
        return f'{dialect} için geçerli tip değil'
    fam = fams.pop()
    src = source_family(col['kaynak_tip'])
    L, I, D = col['max_uzunluk'], col['max_tam_hane'], col['max_ondalik']
    allowed = {'str': {'str'}, 'bin': {'bin'}, 'float': {'float', 'dec'}, 'num': {'int', 'dec', 'bool', 'float'}, 'date': {'date', 'ts'}, 'ts': {'ts'}, 'bool': {'bool', 'int'}}.get(src, set())
    if fam not in allowed:
        return f'kaynak {col["kaynak_tip"]} ({src}) {fam} ailesine taşınamaz'
    if fam == 'str':
        if a and a != 'max' and int(a) < L:
            return f'uzunluk {a} < ölçülen en uzun değer {L}'
        if name == 'char' and a and int(a) != L:
            return 'char yalnızca sabit uzunluklu değerler için'
        if dialect == 'sqlserver' and name == 'varchar' and col['unicode']:
            return 'Türkçe/Unicode karakter var; SQL Server’da nvarchar gerekir'
        if dialect != 'postgres' and name != 'text' and not a:
            return 'uzunluk verilmeli'
    elif fam == 'int':
        if D:
            return f'{D} ondalık hane var; tamsayı tipi veri kaybettirir'
        if I > INT_DIGITS[name]:
            return f'{I} haneli değer {name} aralığına sığmaz'
    elif fam == 'dec':
        p, s = (int(a) if a and a != 'max' else 38), int(b or 0)
        if dialect == 'snowflake' and not a:
            p, s = 38, 0
        if s < D:
            return f'ölçek {s} < ölçülen ondalık hane {D}'
        if p - s < I:
            return f'tam kısım {p - s} hane < ölçülen {I}'
    elif fam == 'date' and col['saat_var']:
        return 'değerlerde saat var; tarih tipi saati keser'
    elif fam == 'bool' and not col['sadece_01']:
        return 'boolean yalnızca 0/1 değerli kolonlarda'
    elif fam == 'float' and src == 'num':
        return 'kesin sayıyı kayan noktaya çevirmek yuvarlama hatası getirir'
    return None


def validator(cols, dialect):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('eslemeler') or []:
            if not isinstance(e, dict):
                continue
            key = (str(e.get('tablo', '')).strip(), str(e.get('kolon', '')).strip())
            if key not in cols or key in out:
                problems.append(f'{key[0]}.{key[1]} listede yok ya da iki kez yazıldı')
                continue
            t = str(e.get('hedef_tip', '')).strip()
            err = check(cols[key], t, dialect)
            if err:
                problems.append(f'{key[0]}.{key[1]} ({cols[key]["kaynak_tip"]}) → {t}: {err}')
            out[key] = (t, str(e.get('gerekce', '')).strip())
        missing = [f'{a}.{b}' for a, b in cols if (a, b) not in out]
        if missing:
            problems.append('eşlenmeyen kolon: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems[:25]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen veritabanı geçişi yapan bir veri mimarısın. Her kaynak kolon için hedef veritabanında veri kaybı olmadan en uygun tipi seç; ölçülen '
          'değerlere (en uzun metin, tam/ondalık hane, saat bilgisi, Unicode, yalnızca 0/1) bak, gereğinden çok geniş tip seçme. Oracle DATE saat de tutar. '
          'Yanıt şeması: {"eslemeler": [{"tablo": "...", "kolon": "...", "hedef_tip": "numeric(12,2)", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Kaynak kolon tiplerini ölçülen değerlere göre hedef veritabanı tiplerine eşleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'kaynak_kolon_profili.csv')),
                   help='tablo, kolon, kaynak_tip, max_uzunluk, max_tam_hane, max_ondalik, saat_var, unicode_var, sadece_01, bos_var')
    p.add_argument('--target', choices=list(TARGETS), default='postgres')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    need = ['tablo', 'kolon', 'kaynak_tip', 'max_uzunluk', 'max_tam_hane', 'max_ondalik', 'saat_var', 'unicode_var', 'sadece_01', 'bos_var']
    missing = [c for c in need if c not in columns]
    if missing:
        raise ValueError(f'Eksik sütun: {", ".join(missing)}')
    yes = lambda v: v.strip().lower() in ('evet', 'e', '1', 'true', 'yes')
    num = lambda v: int(float(v.replace(',', '.'))) if v.strip() else 0
    cols = {(r['tablo'].strip(), r['kolon'].strip()): {'kaynak_tip': r['kaynak_tip'].strip(), 'max_uzunluk': num(r['max_uzunluk']), 'max_tam_hane': num(r['max_tam_hane']),
                                                       'max_ondalik': num(r['max_ondalik']), 'saat_var': yes(r['saat_var']), 'unicode': yes(r['unicode_var']),
                                                       'sadece_01': yes(r['sadece_01']), 'bos_var': yes(r['bos_var'])} for r in rows}
    out = output_dir(args.output)
    user = (f'Hedef: {args.target}. Geçerli tip adları: ' + '; '.join(f'{f}: {", ".join(n)}' for f, n in TARGETS[args.target].items() if n)
            + '\n\nKolonlar (kaynak tip · ölçümler):\n' + '\n'.join(
                f'- {t}.{c}: {v["kaynak_tip"]} · en uzun {v["max_uzunluk"]} · tam hane {v["max_tam_hane"]} · ondalık {v["max_ondalik"]} · saat {"var" if v["saat_var"] else "yok"} · '
                f'Unicode {"var" if v["unicode"] else "yok"} · {"yalnızca 0/1" if v["sadece_01"] else "0/1 dışı değer var"} · {"boş değer var" if v["bos_var"] else "boş yok"}'
                for (t, c), v in cols.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(cols, args.target))
    if result is None:
        return
    table = [{'tablo': t, 'kolon': c, 'kaynak_tip': v['kaynak_tip'], 'hedef_tip': result[(t, c)][0], 'null': 'NULL' if v['bos_var'] else 'NOT NULL', 'gerekce': result[(t, c)][1]}
             for (t, c), v in cols.items()]
    tabular.write_csv(out / 'tip_esleme.csv', list(table[0]), table)
    q = (lambda n: f'[{n}]') if args.target == 'sqlserver' else (lambda n: f'"{n}"')
    by = defaultdict(list)
    for r in table:
        by[r['tablo']].append(f'    {q(r["kolon"].lower())} {r["hedef_tip"].upper()}{" NOT NULL" if r["null"] == "NOT NULL" else ""}')
    ddl = [f'-- {args.target} · {len(table)} kolon; tipler kaynakta ölçülen değerlere göre denetlendi'] + [f'CREATE TABLE {q(t.lower())} (\n' + ',\n'.join(c) + '\n);' for t, c in by.items()]
    write_text(out / 'hedef_ddl.sql', '\n\n'.join(ddl) + '\n')
    md = ['# Tip dönüşümü', '', f'{len(table)} kolon {args.target} tiplerine eşlendi; her tip ölçülen değerleri sığdırıyor ve tip ailesini koruyor.', '',
          md_table(['Tablo', 'Kolon', 'Kaynak tip', 'Hedef tip', 'Null', 'Gerekçe'], [list(r.values()) for r in table]), '',
          f'_Model: {meta["model"]} · Uzunluk, hassasiyet, tamsayı aralığı, saat ve Unicode kuralları kodla denetlendi. hedef_ddl.sql hedef veritabanında çalıştırılarak test edilmedi; '
          'ölçümler kaynakta alınan örneğe dayanır, gelecekte daha uzun değer gelebilir._']
    write_text(out / 'tip-donusum.md', '\n'.join(md))
    print(f'[OK] {len(table)} kolon {args.target} tiplerine eşlendi. Çıktı: {out / "tip-donusum.md"}')


if __name__ == '__main__':
    llm.run(main)
