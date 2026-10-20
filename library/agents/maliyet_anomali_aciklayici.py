"""Maliyet anomalisi açıklayıcı (AI agent): günlük bulut maliyetinde anormal artış olan günleri kodla bulur (önceki 14 günün medyanı ve medyan
mutlak sapmasına göre) ve her anormal gün için artışa en çok katkı yapan servis, kaynak ve projeyi önceki günlerin ortalamasına göre
hesaplar. Model her anomali için açıklama ve aksiyon yazar. Kodla denetlenir: yazılan gün gerçekten anomali olmalı, “ana kaynak” ve “ana
servis” artışa en çok katkı yapan kaynak/servis olmalı, her anomali açıklanmalı, metindeki sayılar hesaplanan değerlerden gelmeli.
Çıktı: anomaliler.csv, maliyet-anomalileri.md."""
import argparse, statistics
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table
from donem_yorumu import unknown_numbers


def detect(rows, cost, window, k):
    daily, by = defaultdict(float), {'servis': defaultdict(lambda: defaultdict(float)), 'kaynak': defaultdict(lambda: defaultdict(float)), 'etiket_proje': defaultdict(lambda: defaultdict(float))}
    for r in rows:
        d, v = r['tarih'].strip(), cost(r)
        daily[d] += v
        for dim in by:
            by[dim][d][r.get(dim, '').strip() or '(boş)'] += v
    days = sorted(daily)
    found = []
    for i, d in enumerate(days):
        hist = [daily[x] for x in days[max(0, i - window):i]]
        if len(hist) < 7:
            continue
        med = statistics.median(hist)
        mad = statistics.median(abs(h - med) for h in hist) or 1.0
        score = (daily[d] - med) / (1.4826 * mad)
        if score > k:
            prev = days[max(0, i - window):i]
            contrib = {}
            for dim, tab in by.items():
                base = defaultdict(float)
                for x in prev:
                    for name, v in tab[x].items():
                        base[name] += v / len(prev)
                deltas = {n: tab[d].get(n, 0.0) - base.get(n, 0.0) for n in set(tab[d]) | set(base)}
                contrib[dim] = sorted(deltas.items(), key=lambda kv: -kv[1])[:3]
            found.append({'tarih': d, 'maliyet': round(daily[d], 2), 'beklenen': round(med, 2), 'fark': round(daily[d] - med, 2), 'skor': round(score, 1), 'katki': contrib})
    return days, daily, found


def validator(found):
    by_day = {a['tarih']: a for a in found}
    nums = set()
    for a in found:
        nums |= {abs(a['maliyet']), abs(a['beklenen']), abs(a['fark']), abs(a['skor'])}
        nums |= {round(abs(v), 2) for items in a['katki'].values() for _, v in items}
        nums |= {round(abs(a['fark']) / a['beklenen'] * 100, 1)}

    def validate(obj):
        problems, out = [], {}
        for e in obj.get('anomaliler') or []:
            d = str(e.get('tarih', '')).strip()
            if d not in by_day:
                problems.append(f'{d} anomali günü değil (anomaliler: {", ".join(by_day)})')
                continue
            a = by_day[d]
            top_k, top_s = a['katki']['kaynak'][0][0], a['katki']['servis'][0][0]
            if str(e.get('ana_kaynak', '')).strip() != top_k:
                problems.append(f'{d}: artışa en çok katkı yapan kaynak {e.get("ana_kaynak")} değil')
            if str(e.get('ana_servis', '')).strip() != top_s:
                problems.append(f'{d}: artışa en çok katkı yapan servis {e.get("ana_servis")} değil')
            if not str(e.get('aksiyon', '')).strip():
                problems.append(f'{d}: aksiyon yazılmalı')
            out[d] = e
        missing = [d for d in by_day if d not in out]
        if missing:
            problems.append('açıklanmayan anomali: ' + ', '.join(missing))
        text = ' '.join(str(e.get('aciklama', '')) + ' ' + str(e.get('aksiyon', '')) for e in out.values())
        bad = unknown_numbers(text, nums, list(by_day))
        if bad:
            problems.append('hesaplanmayan sayılar: ' + ', '.join(bad[:8]))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir FinOps analistisin. Kodla bulunan maliyet anomalilerini açıkla: artışa en çok katkı yapan kaynağı ve servisi (katkı tablosundaki ilk '
          'satır) yaz, olası nedeni ve yapılacak işi söyle. Yalnızca verilen sayıları kullan. Yanıt şeması: {"anomaliler": [{"tarih": "2026-09-18", '
          '"ana_servis": "...", "ana_kaynak": "...", "aciklama": "...", "aksiyon": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Günlük bulut maliyetindeki anomalileri bulup katkı analiziyle açıklatan agent; iddialar kodla denetlenir.')
    p.add_argument('--input', default=str(here('ornek', 'bulut_maliyet.csv')), help='tarih, servis, kaynak, etiket_proje, maliyet')
    p.add_argument('--cost', default='maliyet_usd')
    p.add_argument('--window', type=int, default=14)
    p.add_argument('--threshold', type=float, default=4.0, help='Sağlam z-skoru eşiği')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tarih', 'servis', 'kaynak', args.cost):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    style = tabular.number_style([r[args.cost] for r in rows])
    cost = lambda r: tabular.parse_number(r[args.cost], style) or 0.0
    days, daily, found = detect(rows, cost, args.window, args.threshold)
    out = output_dir(args.output)
    if not found:
        write_text(out / 'maliyet-anomalileri.md', f'# Maliyet anomalileri\n\n{days[0]} – {days[-1]}: anomali yok (eşik {args.threshold:g}).\n')
        print('[OK] Anomali yok; model çağrılmadı.')
        return
    lines = []
    for a in found:
        lines.append(f'{a["tarih"]}: maliyet {a["maliyet"]}, beklenen (medyan) {a["beklenen"]}, fark {a["fark"]} (%{a["fark"] / a["beklenen"] * 100:.1f}), skor {a["skor"]}')
        for dim, items in a['katki'].items():
            lines.append(f'  {dim} katkısı: ' + ', '.join(f'{n} {v:+.2f}' for n, v in items))
    user = f'{days[0]} – {days[-1]}, günlük ortalama {statistics.fmean(daily.values()):.2f}.\nAnomaliler ve artışa katkı (önceki {args.window} günün ortalamasına göre):\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(found))
    if result is None:
        return
    rows_out = [{'tarih': a['tarih'], 'maliyet': a['maliyet'], 'beklenen': a['beklenen'], 'fark': a['fark'], 'skor': a['skor'], 'ana_servis': a['katki']['servis'][0][0],
                 'ana_kaynak': a['katki']['kaynak'][0][0], 'kaynak_katkisi': round(a['katki']['kaynak'][0][1], 2), 'aksiyon': result[a['tarih']]['aksiyon']} for a in found]
    tabular.write_csv(out / 'anomaliler.csv', list(rows_out[0]), rows_out)
    md = ['# Maliyet anomalileri', '', f'{len(found)} anormal gün ({days[0]} – {days[-1]}).', '',
          md_table(['Tarih', 'Maliyet', 'Beklenen', 'Fark', 'Ana servis', 'Ana kaynak'], [[r['tarih'], r['maliyet'], r['beklenen'], r['fark'], r['ana_servis'], r['ana_kaynak']] for r in rows_out]), '']
    for a in found:
        e = result[a['tarih']]
        md += [f'## {a["tarih"]}', '', e.get('aciklama', ''), '', f'Aksiyon: {e["aksiyon"]}', '']
    md += [f'_Model: {meta["model"]} · Anomaliler ve katkılar kodla hesaplandı; ana kaynak/servis ve sayılar denetlendi. Neden açıklaması hipotezdir._']
    write_text(out / 'maliyet-anomalileri.md', '\n'.join(md))
    print(f'[OK] {len(found)} anomali açıklandı. Çıktı: {out / "maliyet-anomalileri.md"}')


if __name__ == '__main__':
    llm.run(main)
