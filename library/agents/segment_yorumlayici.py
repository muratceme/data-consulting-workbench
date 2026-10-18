"""Segment yorumlayıcı (AI agent): müşteri özellik tablosunu (harcama, sipariş sayısı, son alışverişten bu yana gün, iade oranı...) yerelde
k-means ile kümeler; model yalnızca kümelerin özet istatistiklerini görür ve her kümeye ad, tarif ve aksiyon yazar. Müşteri satırları
gönderilmez. Modelin her “özellik” iddiası (ör. harcama: yüksek) kümenin standartlaştırılmış merkezine göre denetlenir: yüksek/düşük
denen metrik gerçekten ortalamanın en az 0,5 standart sapma üstünde/altında olmalı; kümenin 1 standart sapmadan belirgin her metriği
anılmalı; adlar tekil olmalı. Kümeleme deterministiktir (sabit tohum, çoklu başlangıç).
Çıktı: musteri_segmentleri.csv, segment_ozeti.csv, segmentler.md."""
import argparse, math, random, statistics
import llm
import tabular
from common import here, output_dir, write_text, md_table

HIGH, LOW, STRONG = 0.5, -0.5, 1.0


def num(v):
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:.3g}'


def kmeans(points, k, seed=42, restarts=8, iters=100):
    """Plain k-means++ with several restarts; returns (labels, centres) of the lowest inertia run."""
    rnd = random.Random(seed)
    best = None
    dist = lambda a, b: sum((x - y) ** 2 for x, y in zip(a, b))
    for _ in range(restarts):
        centres = [points[rnd.randrange(len(points))]]
        while len(centres) < k:
            d = [min(dist(p, c) for c in centres) for p in points]
            total = sum(d)
            r, acc = rnd.random() * total, 0
            for p, w in zip(points, d):
                acc += w
                if acc >= r:
                    centres.append(p)
                    break
        for _ in range(iters):
            labels = [min(range(k), key=lambda j: dist(p, centres[j])) for p in points]
            new = []
            for j in range(k):
                members = [p for p, l in zip(points, labels) if l == j]
                new.append([sum(c) / len(members) for c in zip(*members)] if members else centres[j])
            if new == centres:
                break
            centres = new
        inertia = sum(dist(p, centres[l]) for p, l in zip(points, labels))
        if best is None or inertia < best[0]:
            best = (inertia, labels, centres)
    _, labels, centres = best
    # stable numbering: biggest segment first
    order = sorted(range(k), key=lambda j: -labels.count(j))
    remap = {old: new for new, old in enumerate(order)}
    return [remap[l] for l in labels], [centres[j] for j in order]


def validator(segments, metrics):
    def validate(obj):
        problems, out, names = [], {}, set()
        for s in obj.get('segmentler') or []:
            if not isinstance(s, dict):
                continue
            try:
                no = int(s.get('no'))
            except (TypeError, ValueError):
                problems.append('“no” sayı olmalı')
                continue
            if no not in segments or no in out:
                problems.append(f'segment {no} yok ya da iki kez yazıldı')
                continue
            name = str(s.get('ad', '')).strip()
            if not name or name.lower() in names:
                problems.append(f'segment {no}: ad boş ya da başka segmentle aynı')
            names.add(name.lower())
            z = segments[no]['z']
            claims, why = {}, []
            for c in s.get('ozellikler') or []:
                m, d = str((c or {}).get('metrik', '')).strip(), str((c or {}).get('yon', '')).strip().lower().replace('yuksek', 'yüksek').replace('dusuk', 'düşük')
                if m not in metrics:
                    why.append(f'{m or "?"} metriği yok')
                elif d not in ('yüksek', 'düşük', 'ortalama'):
                    why.append(f'{m}: yön yüksek/düşük/ortalama olmalı')
                elif d == 'yüksek' and z[m] < HIGH or d == 'düşük' and z[m] > LOW or d == 'ortalama' and abs(z[m]) >= HIGH:
                    why.append(f'{m} “{d}” denmiş ama küme ortalamadan {z[m]:+.2f} standart sapma uzakta')
                else:
                    claims[m] = d
            missed = [m for m in metrics if abs(z[m]) >= STRONG and m not in claims]
            if missed:
                why.append('belirgin metrik anılmamış: ' + ', '.join(f'{m} ({z[m]:+.1f} ss)' for m in missed))
            if not str(s.get('tarif', '')).strip() or not [a for a in s.get('aksiyonlar') or [] if str(a).strip()]:
                why.append('tarif ve en az bir aksiyon gerekli')
            if why:
                problems.append(f'segment {no}: ' + '; '.join(why))
            out[no] = {'ad': name, 'tarif': str(s.get('tarif', '')).strip(), 'ozellikler': claims, 'aksiyonlar': [str(a).strip() for a in s.get('aksiyonlar') or [] if str(a).strip()]}
        missing = [str(n) for n in segments if n not in out]
        if missing:
            problems.append('yazılmayan segment: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir CRM analistisin. Kodla hesaplanmış müşteri kümelerinin özet istatistiklerine bakarak her kümeye kısa, ayırt edici bir ad, '
          'bir cümlelik tarif ve pazarlama/satış aksiyonları yaz. "ozellikler" listesinde her metrik için yön ver (yüksek, düşük, ortalama); yön, verilen '
          'z-değerine uymalı (yüksek ≥ +0,5, düşük ≤ −0,5) ve |z| ≥ 1 olan her metrik anılmalı. Yanıt şeması: {"segmentler": [{"no": 0, "ad": "...", '
          '"tarif": "...", "ozellikler": [{"metrik": "harcama", "yon": "yüksek"}], "aksiyonlar": ["..."]}]}')


def main():
    p = argparse.ArgumentParser(description='Müşterileri yerelde kümeleyip kümeleri modele adlandırtan agent (müşteri satırları gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'musteri_ozellikleri.csv')))
    p.add_argument('--id-column', default='musteri_no')
    p.add_argument('--metrics', default='', help='Kümelemeye girecek sayısal sütunlar (virgülle); boşsa id dışındaki tüm sayısal sütunlar')
    p.add_argument('--k', type=int, default=4)
    p.add_argument('--log', default='', help='Log ölçeğe alınacak çarpık sütunlar (ör. harcama)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.id_column not in columns:
        raise ValueError(f'{args.id_column} sütunu yok. Sütunlar: {", ".join(columns)}')
    metrics = [m.strip() for m in args.metrics.split(',') if m.strip()] or \
              [c for c in columns if c != args.id_column and all(tabular.parse_number(r[c], None) is not None for r in rows if r[c].strip())]
    styles = {m: tabular.number_style([r[m] for r in rows]) for m in metrics}
    values = []
    for r in rows:
        v = [tabular.parse_number(r[m], styles[m]) if r[m].strip() else None for m in metrics]
        if None in v:
            raise ValueError(f'{r[args.id_column]}: {metrics[v.index(None)]} boş ya da sayı değil; kümelemeden önce doldurun.')
        values.append(v)
    if len(rows) < args.k * 5:
        raise ValueError(f'{len(rows)} müşteri {args.k} küme için az (en az {args.k * 5}).')
    logged = {m.strip() for m in args.log.split(',') if m.strip()}
    scaled_in = [[math.log1p(max(x, 0)) if m in logged else x for m, x in zip(metrics, v)] for v in values]
    mean = [statistics.fmean(c) for c in zip(*scaled_in)]
    sd = [statistics.pstdev(c) or 1 for c in zip(*scaled_in)]
    points = [[(x - mu) / s for x, mu, s in zip(v, mean, sd)] for v in scaled_in]
    labels, centres = kmeans(points, args.k)
    raw_mean = [statistics.fmean(c) for c in zip(*values)]
    segments = {}
    for j in range(args.k):
        members = [v for v, l in zip(values, labels) if l == j]
        segments[j] = {'n': len(members), 'z': {m: round(centres[j][i], 2) for i, m in enumerate(metrics)},
                       'medyan': {m: statistics.median(c) for m, c in zip(metrics, zip(*members))}}
    out = output_dir(args.output)
    user = [f'{len(rows)} müşteri, {args.k} küme. Metrikler ve genel ortalama: ' + ', '.join(f'{m} {num(mu)}' for m, mu in zip(metrics, raw_mean))
            + (f'. Log ölçekli: {", ".join(sorted(logged))}' if logged else ''), '']
    for j, s in segments.items():
        user.append(f'Küme {j}: {s["n"]} müşteri (%{s["n"] / len(rows) * 100:.0f}) · ' + ', '.join(f'{m}: medyan {num(s["medyan"][m])}, z {s["z"][m]:+.2f}' for m in metrics))
    result, meta = llm.ask(args, out, SYSTEM, '\n'.join(user), validator(segments, metrics))
    if result is None:
        return
    table = [{**r, 'segment_no': l, 'segment': result[l]['ad']} for r, l in zip(rows, labels)]
    tabular.write_csv(out / 'musteri_segmentleri.csv', columns + ['segment_no', 'segment'], table)
    summary = [{'segment_no': j, 'segment': result[j]['ad'], 'musteri': s['n'], 'oran': round(s['n'] / len(rows) * 100, 1),
                **{f'{m}_medyan': round(s['medyan'][m], 2) for m in metrics}, **{f'{m}_z': s['z'][m] for m in metrics}} for j, s in segments.items()]
    tabular.write_csv(out / 'segment_ozeti.csv', list(summary[0]), summary)
    md = ['# Müşteri segmentleri', '', f'{len(rows)} müşteri {args.k} kümeye ayrıldı (k-means, standartlaştırılmış {", ".join(metrics)}).', '',
          md_table(['#', 'Segment', 'Müşteri', '%'] + [f'{m} (medyan)' for m in metrics],
                   [[j, result[j]['ad'], s['n'], round(s['n'] / len(rows) * 100, 1)] + [num(s['medyan'][m]) for m in metrics] for j, s in segments.items()]), '']
    for j in segments:
        r = result[j]
        md += [f'## {j}. {r["ad"]}', '', r['tarif'], '', 'Özellikler: ' + ', '.join(f'{m} {d}' for m, d in r['ozellikler'].items()), '']
        md += [f'- {a}' for a in r['aksiyonlar']] + ['']
    md += [f'_Model: {meta["model"]} · Kümeler kodla hesaplandı; modelin her yüksek/düşük iddiası küme merkezinin z-değeriyle denetlendi. Küme sayısı (--k) '
           'veriye göre değiştirilerek denenmelidir; k-means küresel kümeler varsayar._']
    write_text(out / 'segmentler.md', '\n'.join(md))
    print(f'[OK] {len(rows)} müşteri, {args.k} segment. Çıktı: {out / "segmentler.md"}')


if __name__ == '__main__':
    llm.run(main)
