"""Yeniden deneme politikası (AI agent): Airflow/Dagster görevlerinin deneme geçmişinden görev başına retries, retry_delay ve execution_timeout
önerir. Kanıt kodla hesaplanır: başarılı denemelerin süre dağılımı (p50, p95, en uzun), yeniden denenip kurtulan (geçici hata) ve tüm
denemelerde başarısız kalan (kalıcı hata) çalışma sayısı. Öneri kodla denetlenir: zaman aşımı en uzun başarılı denemenin en az 1,2 katı ve en
fazla 4 katı (ya da +30 dk) olmalı, böylece takılan görev erken düşer ama sağlıklı görev kesilmez; geçici hatayla kurtulan çalışması olan
görevde retries ≥ 1; hiç kurtulmamış ama kalıcı hatası olan görevde retries ≤ 1 (kalıcı hatayı tekrarlamak süre yakar); retries ≤ 5;
retry_delay 1–60 dk. Çıktı: retry_ayarlari.py (görev başına default_args), retry_politikasi.csv, yeniden-deneme.md."""
import argparse, math
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

OK = {'success', 'basarili', 'başarılı'}


def stats(rows):
    runs = defaultdict(list)
    for r in rows:
        runs[(r['dag_id'].strip(), r['task_id'].strip(), r['run_id'].strip())].append((int(r['try_number']), r['state'].strip().lower(), float(r['duration'].replace(',', '.') or 0)))
    tasks = defaultdict(lambda: {'calisma': 0, 'kurtulan': 0, 'kalici': 0, 'sureler': []})
    for (dag, task, _), tries in runs.items():
        tries.sort()
        t = tasks[(dag, task)]
        t['calisma'] += 1
        t['sureler'] += [d for _, s, d in tries if s in OK]
        if tries[-1][1] in OK and len(tries) > 1:
            t['kurtulan'] += 1
        if tries[-1][1] not in OK:
            t['kalici'] += 1
    for t in tasks.values():
        s = sorted(t['sureler']) or [0.0]
        t['p50'], t['p95'], t['max'] = s[len(s) // 2], s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)], s[-1]
    return tasks


def validator(tasks):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('gorevler') or []:
            key = (str(e.get('dag', '')).strip(), str(e.get('gorev', '')).strip())
            seen.add(key)
            if key not in tasks:
                problems.append(f'{key[0]}.{key[1]} yok')
                continue
            t = tasks[key]
            try:
                retries, delay, timeout = int(e.get('retries')), float(e.get('retry_delay_dk')), float(e.get('timeout_dk'))
            except (TypeError, ValueError):
                problems.append(f'{key[1]}: retries, retry_delay_dk, timeout_dk sayı olmalı')
                continue
            why, mx = [], t['max'] / 60
            if timeout < mx * 1.2:
                why.append(f'zaman aşımı {timeout:g} dk, en uzun başarılı deneme {mx:.1f} dk; en az {mx * 1.2:.1f} dk olmalı')
            elif timeout > max(mx * 4, mx + 30):
                why.append(f'zaman aşımı {timeout:g} dk çok uzun; en fazla {max(mx * 4, mx + 30):.1f} dk')
            if t['kurtulan'] and retries < 1:
                why.append(f'{t["kurtulan"]} çalışma yeniden denemeyle kurtulmuş; retries ≥ 1 olmalı')
            if not t['kurtulan'] and t['kalici'] and retries > 1:
                why.append(f'hiçbir çalışma yeniden denemeyle kurtulmamış, {t["kalici"]} kalıcı hata var; retries ≤ 1 olmalı')
            if not 0 <= retries <= 5:
                why.append('retries 0–5 olmalı')
            if not 1 <= delay <= 60:
                why.append('retry_delay_dk 1–60 olmalı')
            if why:
                problems.append(f'{key[0]}.{key[1]}: ' + '; '.join(why))
            out[key] = {'retries': retries, 'delay': delay, 'timeout': timeout, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [f'{d}.{t}' for d, t in tasks if (d, t) not in seen]
        if missing:
            problems.append('ayarı yazılmayan görev: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu mühendisisin. Her görev için retries, retry_delay_dk ve timeout_dk öner: zaman aşımı sağlıklı denemeleri kesmemeli ama '
          'takılan görevi erken düşürmeli; geçici hatalarla kurtulan görevlere yeniden deneme ver, kalıcı hatalarda deneme sayısını artırma (kök nedeni gizler). '
          'Yanıt şeması: {"gorevler": [{"dag": "...", "gorev": "...", "retries": 2, "retry_delay_dk": 5, "timeout_dk": 30, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Görev deneme geçmişinden retry/timeout ayarı öneren ve süre/hata kanıtıyla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'gorev_deneme_gecmisi.csv')), help='dag_id, task_id, run_id, try_number, state, duration (sn)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('dag_id', 'task_id', 'run_id', 'try_number', 'state', 'duration'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    tasks = stats(rows)
    out = output_dir(args.output)
    user = 'Görevler (çalışma · yeniden denemeyle kurtulan · kalıcı başarısız · başarılı deneme süresi p50 / p95 / en uzun, dk):\n' + '\n'.join(
        f'- {d}.{t}: {v["calisma"]} · {v["kurtulan"]} · {v["kalici"]} · {v["p50"] / 60:.1f} / {v["p95"] / 60:.1f} / {v["max"] / 60:.1f}' for (d, t), v in tasks.items())
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tasks))
    if result is None:
        return
    py = ['"""Görev başına yeniden deneme ayarları (yeniden-deneme-politikasi agent’ı). DAG’da: PythonOperator(..., **AYARLAR["dag.gorev"])"""',
          'from datetime import timedelta', '', 'AYARLAR = {']
    py += [f'    "{d}.{t}": {{"retries": {a["retries"]}, "retry_delay": timedelta(minutes={a["delay"]:g}), "retry_exponential_backoff": {a["retries"] > 1}, '
           f'"execution_timeout": timedelta(minutes={a["timeout"]:g})}},' for (d, t), a in result.items()] + ['}']
    write_text(out / 'retry_ayarlari.py', '\n'.join(py))
    rows_out = [{'dag': d, 'gorev': t, 'retries': a['retries'], 'retry_delay_dk': a['delay'], 'timeout_dk': a['timeout'], 'en_uzun_basarili_dk': round(tasks[(d, t)]['max'] / 60, 1),
                 'kurtulan': tasks[(d, t)]['kurtulan'], 'kalici': tasks[(d, t)]['kalici'], 'gerekce': a['gerekce']} for (d, t), a in result.items()]
    tabular.write_csv(out / 'retry_politikasi.csv', list(rows_out[0]), rows_out)
    md = ['# Yeniden deneme politikası', '', f'{len(rows_out)} görev için retry ve zaman aşımı ayarı; süreler ve hata türleri deneme geçmişinden kodla hesaplandı.', '',
          md_table(['Görev', 'Retries', 'Bekleme dk', 'Zaman aşımı dk', 'En uzun başarılı dk', 'Kurtulan', 'Kalıcı', 'Gerekçe'],
                   [[f'{r["dag"]}.{r["gorev"]}', r['retries'], r['retry_delay_dk'], r['timeout_dk'], r['en_uzun_basarili_dk'], r['kurtulan'], r['kalici'], r['gerekce']] for r in rows_out]), '',
          f'_Model: {meta["model"]} · Zaman aşımı sınırları ve deneme sayıları kodla denetlendi. Kalıcı hataların kök nedeni ayrıca çözülmelidir._']
    write_text(out / 'yeniden-deneme.md', '\n'.join(md))
    print(f'[OK] {len(rows_out)} görev ayarlandı. Çıktı: {out / "yeniden-deneme.md"}')


if __name__ == '__main__':
    llm.run(main)
