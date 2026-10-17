"""Gece işi zamanlayıcı (AI agent): iş listesini (iş, bağımlılıklar, süre) ve iş önceliklerini okur; bağımlılık grafını, kritik
yolu ve kuralları kodla çıkarır. Model, iş bağlamındaki öncelikleri (ör. "finans raporu 05:00’te hazır olmalı") gözeterek her işe
başlangıç saati önerir. Öneri yerelde simüle edilir: her iş tam bir kez, bağımlılıklar bitmeden başlayan iş yok, pencere dışına
taşan iş yok, aynı anda çalışan iş sayısı sınırı aşılmıyor. Kod ayrıca açgözlü (greedy) bir temel plan hesaplar ve iki planı
karşılaştırır. Hiçbir zamanlayıcıya yazılmaz. Çıktı: zamanlama-plani.md, plan.csv."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

HHMM = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


def minutes(v):
    m = HHMM.match(str(v).strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def clock(m):
    return f'{(m // 60) % 24:02d}:{m % 60:02d}'


def load_jobs(path, job_col, dep_col, dur_col):
    columns, rows = tabular.read(path)
    for c in (job_col, dep_col, dur_col):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    jobs = {}
    for i, r in enumerate(rows, start=2):
        name = r[job_col].strip()
        if not name:
            continue
        d = tabular.parse_number(r[dur_col])
        if d is None or d <= 0:
            raise ValueError(f'{i}. satır: süre pozitif sayı olmalı.')
        jobs[name] = {'deps': [x.strip() for x in re.split(r'[,;|]', r[dep_col]) if x.strip()], 'dur': int(round(d))}
    unknown = sorted({d for j in jobs.values() for d in j['deps'] if d not in jobs})
    if unknown:
        raise ValueError('Listede olmayan bağımlılık: ' + ', '.join(unknown))
    order, state = [], {}

    def visit(n, path):
        if state.get(n) == 1:
            raise ValueError('Döngüsel bağımlılık: ' + ' → '.join(path + [n]))
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in jobs[n]['deps']:
            visit(d, path + [n])
        state[n] = 2
        order.append(n)
    for n in jobs:
        visit(n, [])
    return jobs, order


def critical_path(jobs, order):
    finish, prev = {}, {}
    for n in order:
        start = max((finish[d] for d in jobs[n]['deps']), default=0)
        prev[n] = max(jobs[n]['deps'], key=lambda d: finish[d]) if jobs[n]['deps'] else None
        finish[n] = start + jobs[n]['dur']
    end = max(finish, key=finish.get)
    path = [end]
    while prev[path[-1]]:
        path.append(prev[path[-1]])
    return finish[end], list(reversed(path))


def simulate(jobs, starts, window_start, window_end, max_parallel):
    problems, finish = [], {}
    for n, s in starts.items():
        finish[n] = s + jobs[n]['dur']
        for d in jobs[n]['deps']:
            if d in starts and s < starts[d] + jobs[d]['dur']:
                problems.append(f'{n} {clock(s)} başlıyor ama bağımlı olduğu {d} {clock(starts[d] + jobs[d]["dur"])} bitiyor')
        if s < window_start:
            problems.append(f'{n} pencere başlangıcından ({clock(window_start)}) önce başlıyor')
        if finish[n] > window_end:
            problems.append(f'{n} {clock(finish[n])} bitiyor; pencere sonu {clock(window_end)}')
    peak, at = 0, None
    for t in range(window_start, max(finish.values(), default=window_start)):
        running = sum(1 for n, s in starts.items() if s <= t < finish[n])
        if running > peak:
            peak, at = running, t
    if peak > max_parallel:
        problems.append(f'{clock(at)} itibarıyla {peak} iş aynı anda çalışıyor; sınır {max_parallel}')
    return problems, max(finish.values()) if finish else window_start, peak


def greedy(jobs, order, window_start, max_parallel):
    """List scheduling by longest remaining path: a valid baseline the model has to compete with."""
    tail = {}
    for n in reversed(order):
        tail[n] = jobs[n]['dur'] + max((tail[c] for c in jobs if n in jobs[c]['deps']), default=0)
    starts, t = {}, window_start
    while len(starts) < len(jobs):
        running = [n for n, s in starts.items() if s <= t < s + jobs[n]['dur']]
        ready = sorted((n for n in jobs if n not in starts and all(d in starts and starts[d] + jobs[d]['dur'] <= t for d in jobs[n]['deps'])), key=lambda n: (-tail[n], n))
        for n in ready[:max(0, max_parallel - len(running))]:
            starts[n] = t
        t += 1
    return starts


def validator(jobs, window_start, window_end, max_parallel):
    def validate(obj):
        plan = obj.get('plan')
        if not isinstance(plan, list) or not plan:
            raise ValueError('"plan" listesi boş.')
        starts, problems = {}, []
        for it in plan:
            if not isinstance(it, dict):
                continue
            n, s = str(it.get('is', '')).strip(), minutes(it.get('baslangic', ''))
            if n not in jobs:
                problems.append(f'listede olmayan iş: {n or "?"}')
            elif n in starts:
                problems.append(f'{n} iki kez planlandı')
            elif s is None:
                problems.append(f'{n} başlangıcı SS:DD biçiminde olmalı')
            else:
                starts[n] = s + (1440 if s < window_start - 720 else 0)  # after midnight
        missing = [n for n in jobs if n not in starts]
        if missing:
            problems.append('planlanmayan iş: ' + ', '.join(missing))
        if not problems:
            problems += simulate(jobs, starts, window_start, window_end, max_parallel)[0]
        if problems:
            raise ValueError('; '.join(problems[:12]))
        return {'starts': starts, 'summary': str(obj.get('gerekce', '')).strip(), 'notes': [str(x) for x in obj.get('notlar', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu operasyon mühendisisin. Gece çalışan işler için başlangıç saatleri öner. Kurallar: her iş bir kez; '
          'bir iş, bağımlı olduğu tüm işler BİTTİKTEN sonra başlar (başlangıç ≥ bağımlının başlangıcı + süresi); hiçbir iş pencere dışına taşmaz; '
          'aynı anda çalışan iş sayısı sınırı aşmaz. Bağlamdaki öncelikli işleri mümkün olduğunca erken bitir. Saatleri SS:DD yaz. '
          'Yanıt şeması: {"plan": [{"is": "...", "baslangic": "01:30"}], "gerekce": "...", "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Bağımlılıklı gece işleri için kuralları yerelde doğrulanan zamanlama planı öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'gece_isleri.csv')))
    p.add_argument('--job', default='is')
    p.add_argument('--deps', default='bagli_oldugu')
    p.add_argument('--duration', default='sure_dk')
    p.add_argument('--window-start', default='01:00')
    p.add_argument('--window-end', default='06:00')
    p.add_argument('--max-parallel', type=int, default=2)
    p.add_argument('--context', default=str(here('ornek', 'gece_oncelikleri.md')), help='İş öncelikleri ve teslim saatleri')
    llm.add_arguments(p)
    args = p.parse_args()
    ws, we = minutes(args.window_start), minutes(args.window_end)
    if ws is None or we is None:
        raise ValueError('--window-start ve --window-end SS:DD biçiminde olmalı.')
    we += 1440 if we <= ws else 0
    jobs, order = load_jobs(args.input, args.job, args.deps, args.duration)
    length, path = critical_path(jobs, order)
    if length > we - ws:
        raise ValueError(f'Kritik yol {length} dk ({" → ".join(path)}); {we - ws} dakikalık pencereye sığmaz. Pencereyi büyütün veya işleri kısaltın.')
    base = greedy(jobs, order, ws, args.max_parallel)
    _, base_end, _ = simulate(jobs, base, ws, we, args.max_parallel)
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    lines = [f"- {n}: {jobs[n]['dur']} dk; bağımlı: {', '.join(jobs[n]['deps']) or '—'}" for n in order]
    user = (f"Pencere: {args.window_start}–{args.window_end}; aynı anda en çok {args.max_parallel} iş.\nKritik yol: {' → '.join(path)} ({length} dk).\n"
            f"İşler:\n" + '\n'.join(lines) + f"\n\nÖncelikler / bağlam:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\"")
    result, meta = llm.ask(args, out, SYSTEM, user, validator(jobs, ws, we, args.max_parallel))
    if result is None:
        return
    starts = result['starts']
    _, end, peak = simulate(jobs, starts, ws, we, args.max_parallel)
    rows = sorted(({'is': n, 'baslangic': clock(s), 'bitis': clock(s + jobs[n]['dur']), 'sure_dk': jobs[n]['dur'], 'bagli_oldugu': ', '.join(jobs[n]['deps']),
                    'temel_plan_baslangic': clock(base[n]), 'kritik_yolda': 'evet' if n in path else ''} for n, s in starts.items()), key=lambda r: (starts[r['is']], r['is']))
    tabular.write_csv(out / 'plan.csv', list(rows[0]), rows)
    write_json(out / 'plan.json', {'plan': rows, 'bitis': clock(end), 'temel_plan_bitis': clock(base_end), 'kritik_yol': path, 'kritik_yol_dk': length})
    md = ['# Gece işleri zamanlama planı', '', result['summary'], '',
          f"- Önerilen planın bitişi: **{clock(end)}** (en yoğun anda {peak} iş) · Kodun temel planı: {clock(base_end)} · Kritik yol alt sınırı: {clock(ws + length)}",
          f"- Kritik yol: {' → '.join(path)} ({length} dk)", '',
          md_table(['İş', 'Başlangıç', 'Bitiş', 'Süre', 'Bağımlı', 'Temel plan'], [[r['is'], r['baslangic'], r['bitis'], r['sure_dk'], r['bagli_oldugu'] or '—', r['temel_plan_baslangic']] for r in rows]),
          '', '```mermaid', 'gantt', '  dateFormat HH:mm', '  axisFormat %H:%M'] + [f"  {r['is']} :{'crit, ' if r['kritik_yolda'] else ''}{r['baslangic']}, {r['sure_dk']}m" for r in rows] + ['```']
    if result['notes']:
        md += ['', '## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · Plan bağımlılık, pencere ve eşzamanlılık kurallarıyla yerelde doğrulandı. Süreler geçmiş ortalamadır; p90 süreyle tekrar deneyin._"]
    write_text(out / 'zamanlama-plani.md', '\n'.join(md))
    worse = end > base_end
    print(f"[{'!' if worse else 'OK'}] {len(rows)} iş planlandı, bitiş {clock(end)} (temel plan {clock(base_end)}). Çıktı: {out / 'zamanlama-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
