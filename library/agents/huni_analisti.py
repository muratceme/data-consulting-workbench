"""Huni analisti (AI agent): web/mobil olay kayıtlarından dönüşüm hunisini kurar ve yorumlar. Adımlar --steps ile verilir ya da olayların
kullanıcılarda ilk görüldüğü sıranın medyanından kodla çıkarılır; huni donusum-hunisi bloğunun kuralıyla (bir adım ancak önceki
adımdan sonra gelirse sayılır) hesaplanır. Modele yalnızca huni tablosu gider (kullanıcı kimlikleri gönderilmez); model bulguları
yazar ve her iddia denetlenir: “en büyük kayıp” denen adım kodun
bulduğu adım olmalı; kırılımda “en kötü segment” gerçekten o adımda en düşük dönüşümlü segment olmalı; metindeki her sayı hesaplanmış
sonuçlardan biri olmalı. Çıktı: huni.csv, huni-analizi.md."""
import argparse
from collections import Counter, defaultdict
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from musteri import huni
from donem_yorumu import unknown_numbers


def funnel(columns, rows, steps, by):
    res = huni(columns, rows, SimpleNamespace(user='kullanici', event='olay', time='zaman', steps=','.join(steps), by=by))
    return res.rows


def facts(table, steps):
    main = [o for o in table if o['grup'] == 'tümü']
    worst = min(main[1:], key=lambda o: o['adim_donusumu_%'])
    seg = defaultdict(dict)
    for o in table:
        if o['grup'] != 'tümü':
            seg[o['olay']][o['grup']] = o['adim_donusumu_%']
    worst_seg = {s: min(v, key=v.get) for s, v in seg.items() if v}
    nums = {abs(float(o[k])) for o in table for k in ('kullanici', 'adim_donusumu_%', 'toplam_donusum_%')} | set(range(1, len(steps) + 1))
    return main, worst, worst_seg, nums


def finding_validator(steps, table):
    main, worst, worst_seg, nums = facts(table, steps)

    def validate(obj):
        problems = []
        if str(obj.get('en_buyuk_kayip', '')).strip() != worst['olay']:
            problems.append(f'en büyük kayıp “{obj.get("en_buyuk_kayip")}” değil; hesaplanan adım dönüşümlerine bak')
        for s, g in (obj.get('en_kotu_segment') or {}).items():
            if s in worst_seg and str(g).strip() != worst_seg[s]:
                problems.append(f'{s} adımında en düşük dönüşüm {g} segmentinde değil')
        text = ' '.join([str(obj.get('ozet', ''))] + [str(x) for x in obj.get('oneriler') or []])
        bad = unknown_numbers(text, nums, steps)
        if bad:
            problems.append('hesaplanan sonuçlarda olmayan sayılar: ' + ', '.join(bad[:8]))
        if not str(obj.get('ozet', '')).strip() or not obj.get('oneriler'):
            problems.append('ozet ve oneriler gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        return obj
    return validate


SYSTEM_FIND = (llm.SAFETY + ' Sen bir ürün analistisin. Kodla hesaplanmış huni tablosunu yorumla: en büyük kaybın olduğu adım, kırılımda her adım için en düşük '
               'dönüşümlü segment, kısa özet ve somut öneriler. Yalnızca tablodaki sayıları kullan. Yanıt şeması: {"en_buyuk_kayip": "olay_adi", '
               '"en_kotu_segment": {"olay_adi": "segment"}, "ozet": "...", "oneriler": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Olay kayıtlarından huni kuran ve bulguları kodla denetlenerek yorumlayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'web_olaylari.csv')), help='kullanici, zaman, olay [, kırılım] sütunları')
    p.add_argument('--by', default='cihaz', help='Kırılım sütunu (boş bırakılabilir)')
    p.add_argument('--steps', default='', help='Huni adımları (virgülle); boşsa olayların tipik sırası')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('kullanici', 'zaman', 'olay'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    by = args.by if args.by in columns else None
    users = defaultdict(set)
    first = defaultdict(list)
    per_user = defaultdict(list)
    for r in rows:
        users[r['olay'].strip()].add(r['kullanici'])
        d = tabular.parse_date(r['zaman'])
        per_user[r['kullanici']].append((d[0] if d else None, r['olay'].strip()))
    for evs in per_user.values():
        seen = []
        for _, e in sorted((x for x in evs if x[0]), key=lambda x: x[0]):
            if e not in seen:
                seen.append(e)
        for i, e in enumerate(seen):
            first[e].append(i + 1)
    events = list(users)
    out = output_dir(args.output)
    median = lambda e: sorted(first[e])[len(first[e]) // 2] if first[e] else 99
    if args.steps:
        steps = [x.strip() for x in args.steps.split(',') if x.strip()]
        unknown = [x for x in steps if x not in events]
        if unknown:
            raise ValueError(f'Kayıtta olmayan olay: {", ".join(unknown)}. Olaylar: {", ".join(events)}')
    else:  # the typical order: median position at which users first do each event
        steps = sorted(events, key=lambda e: (median(e), -len(users[e])))
    if len(steps) < 3:
        raise ValueError('Huni için en az 3 adım gerekli.')
    table = funnel(columns, rows, steps, by)
    main_rows = [o for o in table if o['grup'] == 'tümü']
    user = (f'{len(per_user)} kullanıcı, {len(rows)} olay. Huni adımları: {" → ".join(steps)}.\n'
            'Huni tablosu (grup · adım · olay · kullanıcı · adım dönüşümü % · toplam dönüşüm %):\n' + '\n'.join(
                f'{o["grup"]} · {o["adim"]} · {o["olay"]} · {o["kullanici"]} · {o["adim_donusumu_%"]} · {o["toplam_donusum_%"]}' for o in table))
    r2, meta2 = llm.ask(args, out, SYSTEM_FIND, user, finding_validator(steps, table))
    if r2 is None:
        return
    tabular.write_csv(out / 'huni.csv', list(table[0]), table)
    md = ['# Huni analizi', '', f'Adımlar: {" → ".join(steps)}. {main_rows[0]["kullanici"]} kullanıcıdan {main_rows[-1]["kullanici"]}’i son adıma ulaştı '
          f'(%{main_rows[-1]["toplam_donusum_%"]}).', '',
          md_table(['Adım', 'Olay', 'Kullanıcı', 'Adım dönüşümü %', 'Toplam dönüşüm %'], [[o['adim'], o['olay'], o['kullanici'], o['adim_donusumu_%'], o['toplam_donusum_%']] for o in main_rows]), '']
    if by:
        groups = sorted({o['grup'] for o in table if o['grup'] != 'tümü'})
        md += [f'## {by} kırılımı (adım dönüşümü %)', '', md_table(['Olay'] + groups, [[s] + [next(o['adim_donusumu_%'] for o in table if o['grup'] == g and o['olay'] == s) for g in groups] for s in steps]), '']
    md += ['## Bulgular', '', f'En büyük kayıp: **{r2["en_buyuk_kayip"]}** adımı.', '', r2['ozet'], ''] + [f'- {x}' for x in r2['oneriler']]
    md += ['', f'_Model: {meta2["model"]} · Huni kodla hesaplandı; en büyük kayıp, en kötü segment ve metindeki sayılar hesaplanan tabloyla denetlendi._']
    write_text(out / 'huni-analizi.md', '\n'.join(md))
    print(f'[OK] {len(steps)} adımlı huni; en büyük kayıp {r2["en_buyuk_kayip"]}. Çıktı: {out / "huni-analizi.md"}')


if __name__ == '__main__':
    llm.run(main)
