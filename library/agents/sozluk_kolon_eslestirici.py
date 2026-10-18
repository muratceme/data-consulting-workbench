"""İş sözlüğü → kolon eşleyici (AI agent): iş sözlüğündeki terimleri (Net Ciro, Aktif Müşteri, Sipariş Tarihi...) veri ambarındaki fiziksel
kolonlara bağlar. Modele terim tanımları ve tablo/kolon adları ile tipleri gider; veri gönderilmez. Her eşleme kodla denetlenir: tablo ve
kolon şemada olmalı, kolon tipi terimin tipine uymalı (tutar/sayı → sayısal, tarih → tarih/zaman, bayrak → bit/bool/char(1)), aynı kolon
iki farklı terime bağlanamaz, her terim cevaplanmalı ve eşlenmeyen terimin gerekçesi olmalı. Hiçbir terime bağlanmayan kolonlar kodla
listelenir. Çıktı: terim_kolon.csv, dbt_sozluk.yml (kolon açıklamaları ve meta.terim), sozluk-esleme.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

TYPES = {'metin': None, 'kod': None, 'sayi': r'int|dec|num|float|double|real|money|bigint|smallint',
         'tutar': r'dec|num|float|double|real|money', 'tarih': r'date|time', 'bayrak': r'^(bit|bool|boolean|char\(1\)|tinyint)$'}


def compatible(term_type, col_type):
    rx = TYPES[term_type]
    return rx is None or re.search(rx, col_type.lower()) is not None


def validator(terms, schema):
    def validate(obj):
        problems, out, owner = [], {}, {}
        for e in obj.get('terimler') or []:
            if not isinstance(e, dict):
                continue
            term = str(e.get('terim', '')).strip()
            if term not in terms or term in out:
                problems.append(f'terim “{term}” sözlükte yok ya da iki kez yazıldı')
                continue
            cols, why = [], str(e.get('gerekce', '')).strip()
            for m in e.get('kolonlar') or []:
                key = (str((m or {}).get('tablo', '')).strip(), str((m or {}).get('kolon', '')).strip())
                if key not in schema:
                    problems.append(f'{term}: {key[0]}.{key[1]} şemada yok')
                elif not compatible(terms[term]['tip'], schema[key]):
                    problems.append(f'{term} ({terms[term]["tip"]}): {key[0]}.{key[1]} tipi {schema[key]} uymuyor')
                elif key in owner and owner[key] != term:
                    problems.append(f'{key[0]}.{key[1]} hem “{owner[key]}” hem “{term}” terimine bağlanmış')
                else:
                    owner[key] = term
                    cols.append(key)
            if not cols and not why:
                problems.append(f'{term}: kolon bulunamadıysa gerekçe yaz')
            out[term] = (cols, why)
        missing = [t for t in terms if t not in out]
        if missing:
            problems.append('cevapsız terim: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri yönetişimi (data governance) uzmanısın. İş sözlüğündeki her terimi, anlamı birebir aynı olan fiziksel kolonlara bağla '
          '(bir terim birden fazla tabloda olabilir). Benzer ama farklı anlamdaki kolonu bağlama (ör. brüt tutar ≠ net ciro); karşılığı yoksa kolonlar boş ve '
          'gerekçe dolu olsun. Kolon tipi terim tipine uymalı; bir kolon yalnızca bir terime bağlanır. '
          'Yanıt şeması: {"terimler": [{"terim": "Net Ciro", "kolonlar": [{"tablo": "dwh.fct_satis", "kolon": "net_tutar"}], "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='İş sözlüğü terimlerini ambar kolonlarına tip denetimiyle eşleyen agent (yalnızca adlar ve tanımlar gönderilir).')
    p.add_argument('--glossary', default=str(here('ornek', 'terim_sozlugu.csv')), help='terim, tanim, tip (metin/kod/sayi/tutar/tarih/bayrak)')
    p.add_argument('--schema', default=str(here('ornek', 'dwh_kolonlari.csv')), help='tablo, kolon, tip')
    llm.add_arguments(p)
    args = p.parse_args()
    gc, grows = tabular.read(args.glossary)
    sc, srows = tabular.read(args.schema)
    for c, cols, name in (('terim', gc, 'sözlük'), ('tanim', gc, 'sözlük'), ('tip', gc, 'sözlük'), ('tablo', sc, 'şema'), ('kolon', sc, 'şema'), ('tip', sc, 'şema')):
        if c not in cols:
            raise ValueError(f'{name} dosyasında {c} sütunu yok.')
    terms = {}
    for r in grows:
        t = r['tip'].strip().lower()
        if t not in TYPES:
            raise ValueError(f'{r["terim"]}: tip {", ".join(TYPES)} olmalı.')
        terms[r['terim'].strip()] = {'tanim': r['tanim'].strip(), 'tip': t}
    schema = {(r['tablo'].strip(), r['kolon'].strip()): r['tip'].strip() for r in srows}
    out = output_dir(args.output)
    by_table = defaultdict(list)
    for (t, c), typ in schema.items():
        by_table[t].append(f'{c} {typ}')
    user = ('İş sözlüğü:\n' + '\n'.join(f'- {t} ({v["tip"]}): {v["tanim"]}' for t, v in terms.items())
            + '\n\nAmbar şeması:\n' + '\n'.join(f'- {t}: ' + ', '.join(cols) for t, cols in by_table.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(terms, schema))
    if result is None:
        return
    rows = [{'terim': t, 'tip': terms[t]['tip'], 'tablo': k[0], 'kolon': k[1], 'kolon_tipi': schema[k], 'tanim': terms[t]['tanim']}
            for t, (cols, _) in result.items() for k in cols]
    tabular.write_csv(out / 'terim_kolon.csv', ['terim', 'tip', 'tablo', 'kolon', 'kolon_tipi', 'tanim'], rows)
    owner = {(r['tablo'], r['kolon']): r for r in rows}
    yml = ['version: 2', '', 'models:']
    for t in by_table:
        mapped = [(k, owner[k]) for k in schema if k[0] == t and k in owner]
        if not mapped:
            continue
        yml += [f'  - name: {t.split(".")[-1]}', '    columns:']
        for (_, c), r in mapped:
            desc = r['tanim'].replace('"', "'")
            yml += [f'      - name: {c}', f'        description: "{desc}"', '        meta:', f'          terim: "{r["terim"]}"']
    write_text(out / 'dbt_sozluk.yml', '\n'.join(yml) + '\n')
    unmapped_terms = [(t, why) for t, (cols, why) in result.items() if not cols]
    orphan = [k for k in schema if k not in owner]
    md = ['# İş sözlüğü eşlemesi', '', f'{len(terms)} terim, {len(schema)} kolon: {len(terms) - len(unmapped_terms)} terim {len(rows)} kolona bağlandı; '
          f'{len(unmapped_terms)} terimin karşılığı yok; {len(orphan)} kolon hiçbir terime bağlı değil.', '',
          md_table(['Terim', 'Tip', 'Kolonlar'], [[t, terms[t]['tip'], ', '.join(f'{a}.{b}' for a, b in cols) or '—'] for t, (cols, _) in result.items()]), '']
    if unmapped_terms:
        md += ['## Karşılığı olmayan terimler', '', md_table(['Terim', 'Gerekçe'], unmapped_terms), '']
    if orphan:
        md += ['## Terimi olmayan kolonlar', '', ', '.join(f'{a}.{b}' for a, b in orphan), '']
    md += [f'_Model: {meta["model"]} · Her eşleme şemadaki varlık ve tip uyumuyla kodla denetlendi; aynı kolon iki terime bağlanamaz. Anlam eşliği veri sahibiyle doğrulanmalıdır. '
           'dbt_sozluk.yml kolon açıklamalarını ve terimleri dbt modellerine taşımak içindir._']
    write_text(out / 'sozluk-esleme.md', '\n'.join(md))
    print(f'[OK] {len(terms)} terim, {len(rows)} kolon eşlemesi. Çıktı: {out / "sozluk-esleme.md"}')


if __name__ == '__main__':
    llm.run(main)
