"""Grafik seçici (AI agent): bir veri tablosu ve rapor soruları (“Aylık ciro nasıl gidiyor?”, “Hangi bölge en çok satıyor?”) için her soruya
uygun grafiği seçer ve Vega-Lite tanımını üretir. Modele kolon profili gider, satırlar gönderilmez. Seçim kodla denetlenir: alanlar tabloda
olmalı; çizgi grafiğin x’i tarih olmalı; çubuk grafiğin x’i en fazla 25 kategorili bir boyut olmalı; pasta en fazla 6 kategori, toplam
(sum) ve negatif olmayan ölçüyle kurulabilir; dağılım iki sayısal alan ister; KPI kartı x almaz; ölçü (y) sayısal olmalı (count hariç).
Veri kodla toplulaştırılıp tanımın içine gömülür. Çıktı: grafikler/<n>.vl.json, grafikler.html (vega-embed), grafik-secimi.md."""
import argparse, json
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table, pii_kind

KINDS = {'cizgi': 'line', 'cubuk': 'bar', 'yatay_cubuk': 'bar', 'pasta': 'arc', 'dagilim': 'point', 'kpi': 'text'}
AGG = ('sum', 'mean', 'count', 'max', 'min')


def profile(columns, rows):
    info = {}
    for c in columns:
        vals = [r[c] for r in rows if r[c].strip()]
        style = tabular.number_style(vals)
        if vals and all(tabular.parse_number(v, style) is not None for v in vals):
            nums = [tabular.parse_number(v, style) for v in vals]
            info[c] = {'tip': 'sayi', 'tekil': len(set(vals)), 'min': min(nums), 'style': style}
        elif vals and all(tabular.parse_date(v) for v in vals):
            info[c] = {'tip': 'tarih', 'tekil': len(set(vals))}
        else:
            info[c] = {'tip': 'metin', 'tekil': len(set(vals)), 'kisisel': any(pii_kind(v) for v in vals[:200])}
    return info


def check(g, info):
    kind, x, y, color, agg = g['tur'], g.get('x'), g.get('y'), g.get('renk'), g.get('toplama', 'sum')
    for f in (x, y, color):
        if f and f not in info:
            return f'{f} alanı yok'
    if kind not in KINDS:
        return f'tür {", ".join(KINDS)} olmalı'
    if agg not in AGG:
        return f'toplama {", ".join(AGG)} olmalı'
    if agg != 'count' and (not y or info[y]['tip'] != 'sayi'):
        return 'ölçü (y) sayısal olmalı'
    if kind == 'kpi':
        return 'KPI kartı x almaz' if x else None
    if not x:
        return 'x gerekli'
    if kind == 'cizgi' and info[x]['tip'] != 'tarih':
        return f'çizgi grafiğin x’i tarih olmalı ({x} {info[x]["tip"]})'
    if kind in ('cubuk', 'yatay_cubuk') and (info[x]['tip'] == 'sayi' or info[x]['tekil'] > 25):
        return f'çubuk grafiğin x’i en fazla 25 kategorili boyut olmalı ({x}: {info[x]["tekil"]} değer)'
    if kind == 'pasta' and (info[x]['tekil'] > 6 or agg != 'sum' or info[y]['min'] < 0):
        return f'pasta en fazla 6 kategori, sum ve negatif olmayan ölçüyle kurulur ({x}: {info[x]["tekil"]} kategori)'
    if kind == 'dagilim' and (info[x]['tip'] != 'sayi' or info[y]['tip'] != 'sayi'):
        return 'dağılım grafiği iki sayısal alan ister'
    if color and info[color]['tekil'] > 8:
        return f'renk alanı en fazla 8 değer almalı ({color}: {info[color]["tekil"]})'
    return None


def aggregate(rows, g, info):
    x, y, color, agg = g.get('x'), g.get('y'), g.get('renk'), g.get('toplama', 'sum')
    if g['tur'] == 'dagilim':
        return [{x: tabular.parse_number(r[x], info[x]['style']), y: tabular.parse_number(r[y], info[y]['style'])} for r in rows]
    groups = defaultdict(list)
    for r in rows:
        k = (r[x][:7] if x and info[x]['tip'] == 'tarih' and g.get('zaman') == 'ay' else r[x] if x else 'toplam', r[color] if color else None)
        groups[k].append(tabular.parse_number(r[y], info[y]['style']) if y and agg != 'count' else 1)
    fn = {'sum': sum, 'mean': lambda v: sum(v) / len(v), 'count': len, 'max': max, 'min': min}[agg]
    out = []
    for (kx, kc), vals in sorted(groups.items()):
        rec = {'deger': round(fn([v for v in vals if v is not None]), 2)}
        if x:
            rec[x] = kx
        if color:
            rec[color] = kc
        out.append(rec)
    return out


def spec(g, data, title):
    kind, x, y, color = g['tur'], g.get('x'), g.get('y'), g.get('renk')
    val = 'deger' if kind != 'dagilim' else y
    vl = {'$schema': 'https://vega.github.io/schema/vega-lite/v5.json', 'title': title, 'data': {'values': data}, 'mark': {'type': KINDS[kind], 'tooltip': True}, 'encoding': {}}
    if kind == 'kpi':
        vl['mark'] = {'type': 'text', 'fontSize': 40}
        vl['encoding']['text'] = {'field': 'deger', 'type': 'quantitative', 'format': ',.0f'}
    elif kind == 'pasta':
        vl['encoding'] = {'theta': {'field': 'deger', 'type': 'quantitative'}, 'color': {'field': x, 'type': 'nominal'}}
    else:
        xt = 'temporal' if kind == 'cizgi' else 'quantitative' if kind == 'dagilim' else 'nominal'
        enc = {'x': {'field': x, 'type': xt}, 'y': {'field': val, 'type': 'quantitative', 'title': y or 'adet'}}
        if kind == 'yatay_cubuk':
            enc = {'y': {'field': x, 'type': 'nominal', 'sort': '-x'}, 'x': {'field': 'deger', 'type': 'quantitative', 'title': y or 'adet'}}
        if color:
            enc['color'] = {'field': color, 'type': 'nominal'}
        vl['encoding'] = enc
    return vl


def validator(questions, info, rows):
    def validate(obj):
        out, problems = [], []
        graphs = obj.get('grafikler') or []
        if len(graphs) != len(questions):
            problems.append(f'{len(questions)} soru var, {len(graphs)} grafik yazılmış')
        for i, g in enumerate(graphs, 1):
            if not isinstance(g, dict):
                continue
            g = {k: (v.strip() if isinstance(v, str) else v) for k, v in g.items()}
            g['toplama'] = g.get('toplama') or ('count' if not g.get('y') else 'sum')
            err = check(g, info)
            if err:
                problems.append(f'{i}. soru ({g.get("tur")}): {err}')
                continue
            out.append(g)
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri görselleştirme uzmanısın. Her soru için tek bir grafik seç: cizgi (zaman içinde değişim; x tarih, zaman: "ay" ya da "gun"), '
          'cubuk / yatay_cubuk (kategorileri karşılaştırma; çok kategoride yatay), pasta (yalnızca 6’dan az parçanın toplama oranı), dagilim (iki sayısal '
          'değişken ilişkisi), kpi (tek sayı). y ölçüsü ve toplama (sum, mean, count, max, min); isteğe bağlı renk (en fazla 8 değerli boyut). '
          'Yanıt şeması: {"grafikler": [{"soru": "...", "tur": "cizgi", "x": "tarih", "zaman": "ay", "y": "tutar", "toplama": "sum", "renk": null, "baslik": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Rapor sorularına uygun grafiği seçip kurallarla denetlenen Vega-Lite tanımları üreten agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satis_detay.csv')))
    p.add_argument('--questions', default='Aylık ciro nasıl gidiyor?;Hangi bölge en çok satıyor?;Kanalların ciro payı nedir?;Toplam ciro ne kadar?',
                   help='Noktalı virgülle ayrılmış rapor soruları')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    info = profile(columns, rows)
    questions = [q.strip() for q in args.questions.split(';') if q.strip()]
    out = output_dir(args.output)
    lines = [f'- {c}: {v["tip"]}, {v["tekil"]} farklı değer' + (f', min {v["min"]:g}' if v['tip'] == 'sayi' else '') + (' (kişisel veri)' if v.get('kisisel') else '') for c, v in info.items()]
    user = f'Tablo: {len(rows)} satır.\nAlanlar:\n' + '\n'.join(lines) + '\n\nSorular:\n' + '\n'.join(f'{i}. {q}' for i, q in enumerate(questions, 1))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(questions, info, rows))
    if result is None:
        return
    specs = []
    (out / 'grafikler').mkdir(exist_ok=True)
    for i, (q, g) in enumerate(zip(questions, result), 1):
        vl = spec(g, aggregate(rows, g, info), g.get('baslik') or q)
        write_text(out / 'grafikler' / f'{i}.vl.json', json.dumps(vl, ensure_ascii=False, indent=1))
        specs.append(vl)
    html = ['<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Grafikler</title>',
            '<script src="https://cdn.jsdelivr.net/npm/vega@5.30.0"></script><script src="https://cdn.jsdelivr.net/npm/vega-lite@5.21.0"></script>',
            '<script src="https://cdn.jsdelivr.net/npm/vega-embed@6.26.0"></script></head><body style="font-family:sans-serif">']
    html += [f'<h3>{q}</h3><div id="g{i}"></div>' for i, q in enumerate(questions, 1)]
    html += ['<script>'] + [f'vegaEmbed("#g{i}", {json.dumps(s, ensure_ascii=False)});' for i, s in enumerate(specs, 1)] + ['</script></body></html>']
    write_text(out / 'grafikler.html', '\n'.join(html))
    md = ['# Grafik seçimi', '', f'{len(questions)} soru için grafik seçildi; veriler kodla toplulaştırılıp Vega-Lite tanımlarına gömüldü.', '',
          md_table(['Soru', 'Grafik', 'x', 'y', 'Toplama', 'Renk'], [[q, g['tur'], g.get('x') or '—', g.get('y') or 'adet', g['toplama'], g.get('renk') or '—'] for q, g in zip(questions, result)]), '',
          f'_Model: {meta["model"]} · Grafik türü ile alan tipleri, kategori sayısı ve toplama kuralları kodla denetlendi. grafikler.html vega-embed ile tarayıcıda açılır._']
    write_text(out / 'grafik-secimi.md', '\n'.join(md))
    print(f'[OK] {len(questions)} grafik. Çıktı: {out / "grafik-secimi.md"}')


if __name__ == '__main__':
    llm.run(main)
