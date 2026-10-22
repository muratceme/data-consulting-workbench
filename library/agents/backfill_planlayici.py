"""Backfill planlayıcı (AI agent): eksik kalan DAG bölümlerinin (tarih partition) yeniden çalıştırılması için DAG başına eşzamanlı çalışma
sayısı (max_active_runs), sıra (eskiden yeniye / yeniden eskiye) ve öncelik önerir. Eksik tarihler ve süreler kodla okunur; modele yalnızca
DAG başına özet gider. Plan kodla denetlenir: depends_on_past olan DAG’ler sırayla ve eskiden yeniye çalışmalı, DAG başına eşzamanlılık
paylaşılan slot sayısını aşmamalı, plan slot paylaşımıyla simüle edilir ve tüm backfill zaman penceresine sığmalı.
Çıktı: backfill.sh (Airflow 3 `airflow backfill create` komutları), backfill_takvimi.csv, backfill.md."""
import argparse
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

ORDERS = ('eskiden_yeniye', 'yeniden_eskiye')


def simulate(dags, plan, slots):
    queue = {d: sorted(v['runs'], reverse=plan[d]['sira'] == 'yeniden_eskiye') for d, v in dags.items()}
    active, t, sched = [], 0.0, []
    order = sorted(dags, key=lambda d: (plan[d]['oncelik'], d))
    while any(queue.values()) or active:
        started = True
        while started and len(active) < slots:
            started = False
            for d in order:
                running = sum(a[1] == d for a in active)
                if queue[d] and running < plan[d]['mar'] and len(active) < slots:
                    day, dur = queue[d].pop(0)
                    active.append((t + dur, d, day))
                    sched.append({'dag': d, 'tarih': day, 'baslangic_dk': round(t, 1), 'bitis_dk': round(t + dur, 1)})
                    started = True
        if active:
            active.sort()
            t = active[0][0]
            active = [a for a in active if a[0] > t]
    return t, sched


def validator(dags, slots, window):
    def validate(obj):
        seen = set()
        problems, plan = [], {}
        for e in obj.get('daglar') or []:
            d = str(e.get('dag', '')).strip()
            if d not in dags or d in seen:
                problems.append(f'DAG “{d}” yok ya da iki kez yazıldı')
                continue
            seen.add(d)
            try:
                mar, pr = int(e.get('max_active_runs')), int(e.get('oncelik', 1))
            except (TypeError, ValueError):
                problems.append(f'{d}: max_active_runs ve oncelik tam sayı olmalı')
                continue
            order = str(e.get('sira', '')).strip()
            if order not in ORDERS:
                problems.append(f'{d}: sira {" ya da ".join(ORDERS)} olmalı')
                continue
            if not 1 <= mar <= slots:
                problems.append(f'{d}: max_active_runs 1–{slots} olmalı (paylaşılan slot {slots})')
                continue
            if dags[d]['dep'] and (mar != 1 or order != 'eskiden_yeniye'):
                problems.append(f'{d}: depends_on_past=True; her bölüm bir öncekini bekler, max_active_runs 1 ve eskiden_yeniye olmalı')
            plan[d] = {'mar': mar, 'sira': order, 'oncelik': pr, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [d for d in dags if d not in seen]
        if missing:
            problems.append('planı yazılmayan DAG: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        _, sched = simulate(dags, plan, slots)
        end = max(s['bitis_dk'] for s in sched)  # same rounding as the schedule file
        if end > window:
            last = {}
            for s in sched:
                last[s['dag']] = max(last.get(s['dag'], 0), s['bitis_dk'])
            raise ValueError(f'simülasyonda backfill {end / 60:.2f} saat sürüyor, pencere {window / 60:g} saat; DAG bitişleri: '
                             + ', '.join(f'{d} {v / 60:.2f} sa' for d, v in sorted(last.items(), key=lambda kv: -kv[1])))
        return plan, end, sched
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu mühendisisin. Eksik bölümlerin backfill’i için her DAG’e max_active_runs, sıra (eskiden_yeniye ya da yeniden_eskiye) '
          've öncelik (1 en önce) öner; tüm backfill paylaşılan slotlarla zaman penceresinde bitsin. depends_on_past olan DAG’ler bölümleri sırayla çalıştırır. '
          'Yanıt şeması: {"daglar": [{"dag": "...", "max_active_runs": 2, "sira": "eskiden_yeniye", "oncelik": 1, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Eksik DAG bölümleri için backfill eşzamanlılığı ve sırası öneren, planı simüle eden agent.')
    p.add_argument('--input', default=str(here('ornek', 'eksik_bolumler.csv')), help='dag, tarih, ort_sure_dk, depends_on_past')
    p.add_argument('--slots', type=int, default=4, help='Backfill için ayrılan paralel çalışma (pool slot) sayısı')
    p.add_argument('--window-hours', type=float, default=4.5, help='Backfill’in bitmesi gereken süre (saat)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if [c for c in ('dag', 'tarih', 'ort_sure_dk', 'depends_on_past') if c not in columns]:
        raise ValueError('dag, tarih, ort_sure_dk, depends_on_past sütunları gerekli.')
    dags = defaultdict(lambda: {'runs': [], 'dep': False})
    for r in rows:
        d = dags[r['dag'].strip()]
        d['runs'].append((r['tarih'].strip(), float(r['ort_sure_dk'].replace(',', '.'))))
        d['dep'] = d['dep'] or r['depends_on_past'].strip().lower() in ('evet', 'true', '1', 'e')
    dags = dict(dags)
    window = args.window_hours * 60
    work = sum(x for v in dags.values() for _, x in v['runs'])
    out = output_dir(args.output)
    user = (f'{args.slots} paylaşılan slot, pencere {args.window_hours:g} saat; toplam iş {work:.0f} dk (slot başına en az {work / args.slots:.0f} dk).\n'
            'DAG’ler (eksik bölüm sayısı · ilk–son tarih · bölüm başı ort. süre dk · toplam dk · depends_on_past):\n'
            + '\n'.join(f'- {d}: {len(v["runs"])} · {min(v["runs"])[0]}–{max(v["runs"])[0]} · {sum(x for _, x in v["runs"]) / len(v["runs"]):.1f} · '
                        f'{sum(x for _, x in v["runs"]):.0f} · {"evet" if v["dep"] else "hayır"}' for d, v in dags.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(dags, args.slots, window))
    if result is None:
        return
    plan, end, sched = result
    tabular.write_csv(out / 'backfill_takvimi.csv', list(sched[0]), sched)
    sh = ['#!/usr/bin/env bash', f'# Backfill planı: {args.slots} slot, simülasyonda {end / 60:.2f} saat. Yalnızca eksik çalışmalar oluşturulur (--reprocess-behavior none).', 'set -euo pipefail']
    for d in sorted(plan, key=lambda d: (plan[d]['oncelik'], d)):
        days = [x for x, _ in dags[d]['runs']]
        sh.append(f'airflow backfill create --dag-id {d} --from-date {min(days)} --to-date {max(days)} --max-active-runs {plan[d]["mar"]} --reprocess-behavior none'
                  + (' --run-backwards' if plan[d]['sira'] == 'yeniden_eskiye' else ''))
    write_text(out / 'backfill.sh', '\n'.join(sh))
    md = ['# Backfill planı', '', f'{sum(len(v["runs"]) for v in dags.values())} eksik bölüm, {len(dags)} DAG; simülasyonda {end / 60:.2f} saatte biter (pencere {args.window_hours:g} saat, {args.slots} slot).', '',
          md_table(['DAG', 'Bölüm', 'depends_on_past', 'max_active_runs', 'Sıra', 'Öncelik', 'Bitiş (sa)', 'Gerekçe'],
                   [[d, len(dags[d]['runs']), 'evet' if dags[d]['dep'] else 'hayır', m['mar'], m['sira'], m['oncelik'],
                     f'{max(s["bitis_dk"] for s in sched if s["dag"] == d) / 60:.2f}', m['gerekce']] for d, m in sorted(plan.items(), key=lambda kv: kv[1]['oncelik'])]), '',
          f'_Model: {meta["model"]} · Modele yalnızca DAG özetleri gitti; depends_on_past kuralı ve süre slot paylaşımlı simülasyonla kodla denetlendi._']
    write_text(out / 'backfill.md', '\n'.join(md))
    print(f'[OK] {len(dags)} DAG, {end / 60:.2f} saat. Çıktı: {out / "backfill.md"}')


if __name__ == '__main__':
    llm.run(main)
