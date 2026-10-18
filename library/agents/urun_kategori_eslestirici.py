"""Ürün kategori eşleyici (AI agent): ürün adlarını şirketin kendi kategori ağacına (kod, ad, üst kod) atar. Modele yalnızca tekil ürün
adları ve kategori ağacı gider; fiyat, stok ve tedarikçi gönderilmez. Her atama ağaçta var olan bir YAPRAK kategori olmalıdır (ara
düğüme atama reddedilir); her ürün cevaplanmalı, emin olunamayan ürün gerekçesiyle “eşlenemez” bırakılabilir. Aynı ada (büyük/küçük
harf, boşluk ve noktalama farkı yok sayılarak) sahip ürünler aynı kategoriye düşer. Kategorisi dolu ürünler (--keep sütunu) değiştirilmez.
Çıktı: urun_kategorileri.csv, kategori_dagilimi.csv, kategori-esleme.md."""
import argparse, re
from collections import Counter
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table


def key(name):
    return ''.join(re.split(r'[^0-9a-z]+', tr.fold(name)))  # 'Türk Kahvesi 100 g' == 'türk kahvesi 100g'


def load_tree(path):
    columns, rows = tabular.read(path)
    for c in ('kod', 'ad', 'ust_kod'):
        if c not in columns:
            raise ValueError(f'Kategori ağacında {c} sütunu yok. Sütunlar: {", ".join(columns)}')
    nodes = {r['kod'].strip(): (r['ad'].strip(), r['ust_kod'].strip()) for r in rows if r['kod'].strip()}
    for k, (_, parent) in nodes.items():
        if parent and parent not in nodes:
            raise ValueError(f'{k}: üst kod {parent} ağaçta yok.')
    parents = {p for _, p in nodes.values() if p}
    leaves = [k for k in nodes if k not in parents]

    def path(k):
        out, seen = [], set()
        while k and k not in seen:
            seen.add(k)
            out.append(nodes[k][0])
            k = nodes[k][1]
        return ' > '.join(reversed(out))
    return nodes, leaves, path


def validator(items, nodes, leaves):
    leaf = set(leaves)

    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('eslemeler') or []:
            if not isinstance(e, dict):
                continue
            name, code, why = str(e.get('urun', '')).strip(), str(e.get('kategori', '')).strip(), str(e.get('gerekce', '')).strip()
            seen.add(name)
            if name not in items:
                problems.append(f'listede olmayan ürün: {name or "?"}')
            elif code.lower() in ('eşlenemez', 'eslenemez', ''):
                if not why:
                    problems.append(f'“{name}” eşlenemez denmiş ama gerekçe yok')
                out[name] = ('', why)
            elif code not in nodes:
                problems.append(f'“{name}”: {code} kategori ağacında yok')
            elif code not in leaf:
                problems.append(f'“{name}”: {code} ({nodes[code][0]}) ara düğüm; alt kategorilerinden birini seç')
            else:
                out[name] = (code, why)
        missing = [n for n in items if n not in seen]
        if missing:
            problems.append('cevapsız ürün: ' + ', '.join(missing[:15]))
        if problems:
            raise ValueError('; '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen perakende ürün ana verisini yöneten bir kategori uzmanısın. Her ürünü verilen kategori ağacındaki en uygun YAPRAK kategoriye '
          'ata (yalnızca listedeki kodları kullan, ara düğüm seçme). Ürün adı karar vermeye yetmiyorsa kategori "eşlenemez" yaz ve nedenini gerekçede söyle. '
          'Yanıt şeması: {"eslemeler": [{"urun": "...", "kategori": "K-121", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Ürün adlarını kategori ağacının yaprak kategorilerine eşleyen agent (yalnızca ürün adları gönderilir).')
    p.add_argument('--input', default=str(here('ornek', 'urun_listesi.csv')))
    p.add_argument('--tree', default=str(here('ornek', 'urun_kategori_agaci.csv')), help='kod, ad, ust_kod sütunlu kategori ağacı')
    p.add_argument('--column', default='urun_adi')
    p.add_argument('--keep', default='mevcut_kategori', help='Dolu olduğunda değiştirilmeyecek kategori sütunu')
    llm.add_arguments(p)
    args = p.parse_args()
    nodes, leaves, path = load_tree(args.tree)
    columns, rows = tabular.read(args.input)
    if args.column not in columns:
        raise ValueError(f'{args.column} sütunu yok. Sütunlar: {", ".join(columns)}')
    keep = args.keep if args.keep in columns else ''
    for r in rows:
        if keep and r[keep].strip() and r[keep].strip() not in nodes:
            raise ValueError(f'{r[args.column]}: mevcut kategori {r[keep]} ağaçta yok.')
    by_key, items = {}, []
    for r in rows:
        if keep and r[keep].strip():
            continue
        k = key(r[args.column])
        if k and k not in by_key:
            by_key[k] = r[args.column].strip()
            items.append(by_key[k])
    out = output_dir(args.output)
    result, model = {}, 'kullanılmadı'
    if items:
        user = ('Kategori ağacı (yaprak kategoriler *):\n' + '\n'.join(f'- {k}{" *" if k in leaves else ""}: {path(k)}' for k in nodes)
                + '\n\nEşlenecek ürünler:\n' + '\n'.join(f'- {n}' for n in items))
        result, meta = llm.ask(args, out, SYSTEM, user, validator(items, nodes, leaves))
        if result is None:
            return
        model = meta['model']
    table = []
    for r in rows:
        if keep and r[keep].strip():
            code, source, why = r[keep].strip(), 'mevcut', ''
        else:
            code, why = result.get(by_key.get(key(r[args.column]), ''), ('', ''))
            source = 'model' if code else 'eşlenemedi'
        table.append({**r, 'kategori_kodu': code, 'kategori_yolu': path(code) if code else '', 'kaynak': source, 'gerekce': why})
    cols = columns + ['kategori_kodu', 'kategori_yolu', 'kaynak', 'gerekce']
    tabular.write_csv(out / 'urun_kategorileri.csv', cols, table)
    dist = Counter(t['kategori_kodu'] for t in table if t['kategori_kodu'])
    tabular.write_csv(out / 'kategori_dagilimi.csv', ['kategori_kodu', 'kategori_yolu', 'urun'], [{'kategori_kodu': k, 'kategori_yolu': path(k), 'urun': v} for k, v in dist.most_common()])
    empty = [k for k in leaves if k not in dist]
    left = [t for t in table if t['kaynak'] == 'eşlenemedi']
    md = ['# Ürün kategori eşleme', '', f'{len(rows)} ürün: {sum(t["kaynak"] == "mevcut" for t in table)} mevcut kategorisiyle bırakıldı, '
          f'{sum(t["kaynak"] == "model" for t in table)} modelle eşlendi, {len(left)} eşlenemedi. {len(items)} tekil ad gönderildi (aynı adlı ürünler birlikte eşlendi).', '',
          md_table(['Kategori', 'Ürün'], [[path(k), v] for k, v in dist.most_common()]), '']
    if left:
        md += ['## Eşlenemeyenler', '', md_table(['Ürün', 'Gerekçe'], [[t[args.column], t['gerekce']] for t in left]), '']
    if empty:
        md += [f'Ürünü olmayan yaprak kategoriler: {", ".join(path(k) for k in empty)}.', '']
    md += [f'_Model: {model} · Her atama kategori ağacında var olan bir yaprak kategoriyle denetlendi; anlam doğruluğu örneklem alınarak elle kontrol edilmelidir._']
    write_text(out / 'kategori-esleme.md', '\n'.join(md))
    print(f'[OK] {len(rows)} ürün, {len(dist)} kategori; {len(left)} eşlenemedi. Çıktı: {out / "kategori-esleme.md"}')


if __name__ == '__main__':
    llm.run(main)
