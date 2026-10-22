"""Yükleme sırası planlayıcı (AI agent): geçişte hedef veritabanına tabloların hangi sırayla yükleneceğini ve döngüsel yabancı anahtarlarda
(departman.mudur_id ↔ calisan.departman_id, calisan.yonetici_id → calisan) hangi anahtarın ertelenip yükleme sonrası UPDATE ile
doldurulacağını önerir. Yabancı anahtarlar DDL’den sqlgen bloğunun ilişki okuyucusuyla (ALTER TABLE … FOREIGN KEY dahil) kodla çıkarılır;
modele yalnızca tablo ve ilişki listesi gider. Öneri kodla denetlenir: her tablo bir kez sırada olmalı, ertelenmeyen her anahtarda üst
tablo alt tablodan önce gelmeli, kendine referans veren anahtar ertelenmeli, yalnızca döngüdeki ve NULL kabul eden anahtarlar ertelenebilir.
Çıktı: yukleme_sirasi.csv (sıra ve paralel dalga), yukleme_plani.sql, yukleme-sirasi.md."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table
from sqlgen import relationships, read_sql_files, load_ddl

ALTER = re.compile(r'ALTER\s+TABLE\s+([\w.]+)\s+ADD\s+(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([\w.]+)\s*\(([^)]+)\)', re.I)


def reaches(edges, start, goal):
    seen, stack = set(), [start]
    while stack:
        x = stack.pop()
        if x == goal:
            return True
        if x not in seen:
            seen.add(x)
            stack += [p for c, _, p in edges if c == x]
    return False


def validator(tables, fks, nullable):
    def validate(obj):
        order = [str(t).strip() for t in obj.get('sira') or []]
        problems = []
        if sorted(order) != sorted(tables):
            extra, miss = sorted(set(order) - set(tables)), sorted(set(tables) - set(order))
            problems.append('sıra tüm tabloları bir kez içermeli' + (f'; fazla: {", ".join(extra)}' if extra else '') + (f'; eksik: {", ".join(miss)}' if miss else '')
                            + ('; tekrar eden tablo var' if len(order) != len(set(order)) else ''))
            raise ValueError('; '.join(problems))
        deferred = set()
        for e in obj.get('ertelenen') or []:
            key = (str(e.get('tablo', '')).strip(), str(e.get('kolon', '')).strip())
            fk = next((f for f in fks if (f[0], f[1]) == key), None)
            if not fk:
                problems.append(f'ertelenen {key[0]}.{key[1]} bir yabancı anahtar değil')
                continue
            if not nullable.get(key, True):
                problems.append(f'{key[0]}.{key[1]} NOT NULL; ertelenemez (önce NULL yüklenemez)')
            elif fk[0] != fk[2] and not reaches(fks, fk[2], fk[0]):
                problems.append(f'{key[0]}.{key[1]} → {fk[2]} bir döngüde değil; ertelemek gereksiz, {fk[2]} önce yüklenmeli')
            deferred.add(key)
        pos = {t: i for i, t in enumerate(order)}
        for c, col, p in fks:
            if (c, col) in deferred:
                continue
            if c == p:
                problems.append(f'{c}.{col} tablonun kendisine referans veriyor; ertelenmeli (önce NULL, sonra UPDATE)')
            elif pos[p] > pos[c]:
                problems.append(f'{p}, {c} tablosundan önce yüklenmeli ({c}.{col} → {p})')
        if problems:
            raise ValueError('; '.join(problems))
        return order, deferred
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri geçişi mühendisisin. Tabloların hedefe yükleme sırasını öner: üst (referans verilen) tablo önce yüklensin. Döngüsel ya da '
          'kendine referans veren yabancı anahtarları kırmak için ertelenecek anahtarları seç: bu sütunlar önce NULL yüklenir, tüm tablolar yüklendikten sonra '
          'UPDATE ile doldurulur. NOT NULL sütunlar ertelenemez. Yanıt şeması: {"sira": ["..."], "ertelenen": [{"tablo": "...", "kolon": "...", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='DDL’deki yabancı anahtarlardan tablo yükleme sırası ve ertelenecek anahtarları öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'gecis_fk_semasi.sql')), help='.sql dosyası veya klasörü (CREATE TABLE, ALTER TABLE … FOREIGN KEY)')
    llm.add_arguments(p)
    args = p.parse_args()
    ddl = load_ddl(args.input)
    text = '\n'.join(t for _, t in read_sql_files(args.input))
    norm = lambda n: n.split('.')[-1].strip('[]"`').lower()
    tables = list(dict.fromkeys(norm(t) for t in ddl))
    nullable = {(norm(t), c['name'].lower()): c['nullable'] for t, cols in ddl.items() for c in cols}
    fks = [(norm(c), col.lower(), norm(p_)) for c, col, p_, _, src in relationships(text, ddl) if src == 'tanımlı']
    fks += [(norm(m.group(1)), m.group(2).strip().lower(), norm(m.group(3))) for m in ALTER.finditer(re.sub(r'--[^\n]*', '', text))]
    fks = list(dict.fromkeys(f for f in fks if f[0] in tables and f[2] in tables))
    if not fks:
        raise ValueError('DDL’de yabancı anahtar bulunamadı.')
    out = output_dir(args.output)
    user = (f'{len(tables)} tablo: {", ".join(tables)}\n{len(fks)} yabancı anahtar (alt tablo.sütun → üst tablo · NULL kabul):\n'
            + '\n'.join(f'- {c}.{col} → {p_} · {"evet" if nullable.get((c, col), True) else "hayır (NOT NULL)"}' for c, col, p_ in fks))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, fks, nullable))
    if result is None:
        return
    order, deferred = result
    wave = {}
    for t in order:
        wave[t] = 1 + max([wave[p_] for c, col, p_ in fks if c == t and (c, col) not in deferred and p_ != t] or [0])
    tabular.write_csv(out / 'yukleme_sirasi.csv', ['sira', 'dalga', 'tablo', 'ertelenen_sutunlar'],
                      [{'sira': i + 1, 'dalga': wave[t], 'tablo': t, 'ertelenen_sutunlar': ', '.join(col for c, col in sorted(deferred) if c == t)} for i, t in enumerate(order)])
    sql = ['-- Yükleme planı: aynı dalgadaki tablolar paralel yüklenebilir.', '-- 1) Ertelenen yabancı anahtarlar devre dışı (sütunlar NULL yüklenecek)']
    sql += [f'ALTER TABLE {c} DROP CONSTRAINT IF EXISTS fk_{c}_{col};  -- {c}.{col} NULL yüklenir' for c, col in sorted(deferred)]
    sql += ['', '-- 2) Yükleme sırası'] + [f'-- dalga {wave[t]}: {t}' for t in order]
    sql += ['', '-- 3) Ertelenen sütunları kaynaktan doldur ve kısıtı geri ekle']
    for c, col in sorted(deferred):
        p_ = next(f[2] for f in fks if (f[0], f[1]) == (c, col))
        sql += [f'UPDATE {c} t SET {col} = k.{col} FROM kaynak_{c} k WHERE k.{c}_id = t.{c}_id;  -- anahtar sütununu kaynağa göre uyarlayın',
                f'ALTER TABLE {c} ADD CONSTRAINT fk_{c}_{col} FOREIGN KEY ({col}) REFERENCES {p_};']
    write_text(out / 'yukleme_plani.sql', '\n'.join(sql))
    md = ['# Yükleme sırası', '', f'{len(order)} tablo, {max(wave.values())} paralel dalga; {len(deferred)} yabancı anahtar ertelendi.', '',
          md_table(['Sıra', 'Dalga', 'Tablo', 'Ertelenen sütun'], [[i + 1, wave[t], t, ', '.join(col for c, col in sorted(deferred) if c == t) or '—'] for i, t in enumerate(order)]), '',
          f'_Model: {meta["model"]} · Yabancı anahtarlar DDL’den kodla okundu; sıra, döngü ve NOT NULL kuralları kodla denetlendi._']
    write_text(out / 'yukleme-sirasi.md', '\n'.join(md))
    print(f'[OK] {len(order)} tablo, {len(deferred)} ertelenen anahtar. Çıktı: {out / "yukleme-sirasi.md"}')


if __name__ == '__main__':
    llm.run(main)
