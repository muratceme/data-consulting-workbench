"""Katman atayıcı (AI agent): eski ambarın (crm/erp/stg/dwh/mart gibi şemalar) tablo soy ağacını lakehouse’a taşırken her tabloya medallion
katmanı (bronze/silver/gold) ve yeni ad atar; gold tablonun bronze’u doğrudan okuduğu yerlere ara silver tablo önerir. Soy ağacı kodla
okunur; modele yalnızca tablo adları ve kenarlar gider. Atama kodla denetlenir: kaynak sistemden gelen (beslenmeyen) tablolar bronze,
beslenen tablolar bronze olmamalı; yeni adlar katmanla başlamalı ve tekil olmalı; yeni adlarla kurulan soy ağacı
medallion-katman-kurallari bloğundan ihlalsiz geçmeli (ters akış, katman atlama yok). Çıktı: katman_haritasi.csv, gecis.sql, soy.mmd, katman-atama.md."""
import argparse
from collections import defaultdict
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from lakehouse import medallion

LAYERS = ('bronze', 'silver', 'gold')


def validator(tables, edges, upstream):
    def validate(obj):
        seen = set()
        problems, layer, name = [], {}, {}
        for e in obj.get('tablolar') or []:
            t, k, n = str(e.get('tablo', '')).strip(), str(e.get('katman', '')).strip().lower(), str(e.get('yeni_ad', '')).strip()
            if t not in tables or t in seen:
                problems.append(f'tablo “{t}” soy ağacında yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            if k not in LAYERS:
                problems.append(f'{t}: katman bronze, silver ya da gold olmalı')
                continue
            if not upstream[t] and k != 'bronze':
                problems.append(f'{t} kaynak sistemden geliyor (beslenmiyor); bronze olmalı')
            if upstream[t] and k == 'bronze':
                problems.append(f'{t} başka tablolardan besleniyor ({", ".join(sorted(upstream[t]))}); bronze ham veridir, silver ya da gold olmalı')
            if not n.startswith(k + '.') or len(n) <= len(k) + 1:
                problems.append(f'{t}: yeni ad “{n}” {k}. ile başlamalı')
            layer[t], name[t] = k, n
        missing = [t for t in tables if t not in seen]
        if missing:
            problems.append('katmanı yazılmayan tablo: ' + ', '.join(missing))
        extra = []
        for e in obj.get('ara_tablolar') or []:
            a, b, n = str(e.get('kaynak', '')).strip(), str(e.get('hedef', '')).strip(), str(e.get('ad', '')).strip()
            if (a, b) not in edges:
                problems.append(f'ara tablo {n or "?"}: {a} → {b} kenarı yok')
            elif not n.startswith('silver.'):
                problems.append(f'ara tablo {n or "?"} silver. ile başlamalı')
            else:
                extra.append((a, b, n))
        names = list(name.values()) + [n for _, _, n in extra]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            problems.append('aynı yeni ad birden fazla tabloya verilmiş: ' + ', '.join(dup))
        if problems:
            raise ValueError('; '.join(problems))
        split = {(a, b): n for a, b, n in extra}
        new_edges, back = [], {}
        for a, b in edges:
            pairs = [(name[a], split[(a, b)]), (split[(a, b)], name[b])] if (a, b) in split else [(name[a], name[b])]
            for x, y in pairs:
                new_edges.append({'kaynak': x, 'hedef': y})
                back[(x, y)] = f'{a} → {b}'
        res = medallion(['kaynak', 'hedef'], new_edges, SimpleNamespace(source='kaynak', target='hedef'))
        if res.rows:
            raise ValueError('; '.join(f'{back[(r["kaynak"], r["hedef"])]} ({r["kaynak"]} → {r["hedef"]}): {r["bulgu"]} — {r["detay"]}' for r in res.rows)
                             + '; gold doğrudan bronze okuyorsa hedefi silver yap ya da ara_tablolar ile araya silver tablo ekle')
        return layer, name, extra, new_edges
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir lakehouse mimarısın. Eski ambar tablolarını medallion katmanlarına ata: bronze kaynak sistemden gelen ham veri, silver temizlenmiş '
          've birleştirilmiş varlıklar, gold rapor ve analiz için iş düzeyinde tablolar. Yeni ad "katman.ad" biçiminde olsun. Gold bir tablo bronze tabloyu '
          'doğrudan okuyorsa araya silver tablo ekle. Yanıt şeması: {"tablolar": [{"tablo": "crm.musteri", "katman": "bronze", "yeni_ad": "bronze.crm_musteri", '
          '"gerekce": "..."}], "ara_tablolar": [{"kaynak": "...", "hedef": "...", "ad": "silver.x", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Eski ambar soy ağacını medallion katmanlarına atayan ve ihlalleri kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'lakehouse_gecis_soyu.csv')), help='Soy ağacı kenarları (kaynak, hedef)')
    p.add_argument('--source', default='kaynak')
    p.add_argument('--target', default='hedef')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.source, args.target):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    edges = list(dict.fromkeys((r[args.source].strip(), r[args.target].strip()) for r in rows if r[args.source].strip() and r[args.target].strip()))
    tables = list(dict.fromkeys(x for e in edges for x in e))
    upstream, downstream = defaultdict(set), defaultdict(set)
    for a, b in edges:
        upstream[b].add(a)
        downstream[a].add(b)
    out = output_dir(args.output)
    user = (f'{len(tables)} tablo, {len(edges)} kenar. Tablolar (besleyen ← tablo → beslediği):\n'
            + '\n'.join(f'- {t}: {", ".join(sorted(upstream[t])) or "kaynak sistem"} ← → {", ".join(sorted(downstream[t])) or "tüketici (rapor/uygulama)"}' for t in tables))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, set(edges), upstream))
    if result is None:
        return
    layer, name, extra, new_edges = result
    tabular.write_csv(out / 'katman_haritasi.csv', ['eski_ad', 'katman', 'yeni_ad'], [{'eski_ad': t, 'katman': layer[t], 'yeni_ad': name[t]} for t in tables])
    sql = ['-- Lakehouse geçişi: tabloları yeni katman adlarıyla kopyalayın; eski adlar geçiş süresince uyumluluk görünümü olarak kalır.']
    for t in sorted(tables, key=lambda t: LAYERS.index(layer[t])):
        sql += [f'CREATE TABLE IF NOT EXISTS {name[t]} DEEP CLONE {t};', f'-- uyumluluk: CREATE OR REPLACE VIEW {t}_eski AS SELECT * FROM {name[t]};']
    for a, b, n in extra:
        sql += [f'-- {a} → {b} arasına temizlik katmanı: tip dönüşümü, tekilleştirme ve iş kuralları burada uygulanmalı',
                f'CREATE TABLE IF NOT EXISTS {n} AS SELECT * FROM {name[a]};']
    write_text(out / 'gecis.sql', '\n'.join(sql))
    ids = {n: f'n{i}' for i, n in enumerate(dict.fromkeys(x for e in new_edges for x in (e['kaynak'], e['hedef'])))}
    mmd = ['flowchart LR'] + [f'  {i}["{n}"]:::{n.split(".")[0]}' for n, i in ids.items()] + [f'  {ids[e["kaynak"]]} --> {ids[e["hedef"]]}' for e in new_edges]
    mmd += ['  classDef bronze fill:#cd7f32,color:#fff', '  classDef silver fill:#c0c0c0', '  classDef gold fill:#ffd700']
    write_text(out / 'soy.mmd', '\n'.join(mmd))
    count = {k: sum(v == k for v in layer.values()) for k in LAYERS}
    md = ['# Medallion katman ataması', '', f'{len(tables)} tablo: {count["bronze"]} bronze, {count["silver"]} silver, {count["gold"]} gold; {len(extra)} ara silver tablo eklendi. '
          'Yeni soy ağacında ters akış ve katman atlama yok.', '',
          md_table(['Eski ad', 'Katman', 'Yeni ad'], [[t, layer[t], name[t]] for t in tables]), '']
    if extra:
        md += [md_table(['Kenar', 'Ara tablo'], [[f'{a} → {b}', n] for a, b, n in extra]), '']
    md += [f'_Model: {meta["model"]} · Modele yalnızca tablo adları ve soy ağacı gitti; kaynak tabloların bronze olması, ad kuralı ve medallion akış kuralları kodla denetlendi._']
    write_text(out / 'katman-atama.md', '\n'.join(md))
    print(f'[OK] {len(tables)} tablo, {len(extra)} ara tablo. Çıktı: {out / "katman-atama.md"}')


if __name__ == '__main__':
    llm.run(main)
