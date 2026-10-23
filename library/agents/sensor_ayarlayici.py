"""Sensör ayarlayıcı (AI agent): Airflow sensörleri (dosya, API, tablo bekleyen görevler) için mode (poke/reschedule), poke_interval ve
timeout önerir. Geçmiş bekleme süreleri (sensör başladıktan sonra verinin geldiği dakika; hiç gelmediği günler dahil) kodla özetlenir;
modele yalnızca yüzdelikler gider. Öneri kodla denetlenir: timeout verinin geldiği günlerin belirli payında erken bitmemeli ama hiç
gelmeyen veriyi de makul sürede bildirmeli (p99’un belirli katını aşmamalı), uzun bekleyen sensör worker slotunu tutmamak için reschedule
olmalı, poke_interval 30 sn’den kısa ve bekleme süresine göre çok seyrek olmamalı. Çıktı: sensor_ayarlari.py, sensor_plani.csv, sensor-ayarlari.md."""
import argparse
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table
from analiz import quantile


def validator(waits, missing, max_late, mult, long_wait):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('sensorler') or []:
            k = str(e.get('sensor', '')).strip()
            if k not in waits or k in seen:
                problems.append(f'sensör “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            mode = str(e.get('mode', '')).strip()
            try:
                interval, timeout = int(e.get('poke_interval_sn')), float(e.get('timeout_dk'))
            except (TypeError, ValueError):
                problems.append(f'{k}: poke_interval_sn tam sayı, timeout_dk sayı olmalı')
                continue
            w = waits[k]
            p50, p99 = quantile(w, .5), quantile(w, .99)
            late = sum(x > timeout for x in w) / len(w)
            if mode not in ('poke', 'reschedule'):
                problems.append(f'{k}: mode poke ya da reschedule olmalı')
            elif mode == 'poke' and p50 > long_wait:
                problems.append(f'{k}: medyan bekleme {p50:.1f} dk; poke modu bu süre boyunca bir worker slotunu tutar, reschedule kullan')
            if late > max_late:
                problems.append(f'{k}: timeout {timeout:g} dk, verinin geldiği günlerin %{100 * late:.1f} kadarında erken biter (en fazla %{100 * max_late:g})')
            if timeout > max(mult * p99, 10):
                problems.append(f'{k}: timeout {timeout:g} dk, p99 bekleme {p99:.1f} dk; hiç gelmeyen veri ({missing[k]} gün) geç fark edilir')
            if interval < 30 or interval > max(60, p50 * 60 / 2):
                problems.append(f'{k}: poke_interval {interval} sn; 30 sn ile medyan beklemenin yarısı ({max(60, p50 * 30):.0f} sn) arasında olmalı')
            out[k] = {'mode': mode, 'interval': interval, 'timeout': timeout, 'gec': late, 'gerekce': str(e.get('gerekce', '')).strip()}
        absent = [k for k in waits if k not in seen]
        if absent:
            problems.append('ayarı yazılmayan sensör: ' + ', '.join(absent))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir Airflow platform mühendisisin. Her sensör için mode (poke ya da reschedule), poke_interval (sn) ve timeout (dk) öner: veri '
          'normalde geldiğinde sensör erken düşmesin, hiç gelmeyen veri zamanında fark edilsin, uzun bekleyen sensör worker slotunu tutmasın. Yanıt şeması: '
          '{"sensorler": [{"sensor": "dag.gorev", "mode": "reschedule", "poke_interval_sn": 300, "timeout_dk": 120, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sensör bekleme geçmişinden mode, poke_interval ve timeout öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'sensor_bekleme_gecmisi.csv')), help='sensor, gun, bekleme_dk (boş = veri hiç gelmedi)')
    p.add_argument('--max-late', type=float, default=0.02, help='Verinin geldiği günlerde erken timeout payı en fazla')
    p.add_argument('--multiple', type=float, default=3, help='timeout en fazla p99 beklemenin kaç katı')
    p.add_argument('--long-wait', type=float, default=5, help='Bu dakikadan uzun medyan beklemede reschedule zorunlu')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    waits, missing = defaultdict(list), defaultdict(int)
    for r in rows:
        k = r['sensor'].strip()
        if r['bekleme_dk'].strip():
            waits[k].append(float(r['bekleme_dk'].replace(',', '.')))
        else:
            missing[k] += 1
    waits = {k: sorted(v) for k, v in waits.items()}
    out = output_dir(args.output)
    user = ('Sensörler (gelen gün · gelmeyen gün · bekleme dk p50/p90/p99/en uzun):\n'
            + '\n'.join(f'- {k}: {len(v)} · {missing[k]} · ' + '/'.join(f'{quantile(v, q):.1f}' for q in (.5, .9, .99)) + f'/{v[-1]:.1f}' for k, v in waits.items())
            + f'\n\nVerinin geldiği günlerde en fazla %{100 * args.max_late:g} erken timeout; timeout en fazla p99’un {args.multiple:g} katı; medyan bekleme {args.long_wait:g} dk’yı aşarsa reschedule.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(waits, missing, args.max_late, args.multiple, args.long_wait))
    if result is None:
        return
    py = ['# Airflow sensör ayarları (BaseSensorOperator parametreleri)', 'from datetime import timedelta', '', 'SENSOR_AYARLARI = {']
    py += [f'    "{k}": {{"mode": "{m["mode"]}", "poke_interval": {m["interval"]}, "timeout": int(timedelta(minutes={m["timeout"]:g}).total_seconds()), "soft_fail": False}},'
           for k, m in result.items()] + ['}']
    write_text(out / 'sensor_ayarlari.py', '\n'.join(py))
    plan = [{'sensor': k, 'mode': m['mode'], 'poke_interval_sn': m['interval'], 'timeout_dk': m['timeout'], 'p50_dk': round(quantile(waits[k], .5), 1),
             'p99_dk': round(quantile(waits[k], .99), 1), 'erken_timeout_orani': round(m['gec'], 3), 'veri_gelmeyen_gun': missing[k]} for k, m in result.items()]
    tabular.write_csv(out / 'sensor_plani.csv', list(plan[0]), plan)
    md = ['# Sensör ayarları', '', f'{len(plan)} sensör; timeout’lar geçmiş beklemelerle sınandı.', '',
          md_table(['Sensör', 'Mode', 'poke_interval (sn)', 'timeout (dk)', 'p50 / p99 (dk)', 'Gelmeyen gün', 'Gerekçe'],
                   [[p_['sensor'], p_['mode'], p_['poke_interval_sn'], f'{p_["timeout_dk"]:g}', f'{p_["p50_dk"]} / {p_["p99_dk"]}', p_['veri_gelmeyen_gun'],
                     result[p_['sensor']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca bekleme yüzdelikleri gitti; erken timeout payı, slot kullanımı ve aralık sınırları kodla denetlendi._']
    write_text(out / 'sensor-ayarlari.md', '\n'.join(md))
    print(f'[OK] {len(plan)} sensör. Çıktı: {out / "sensor-ayarlari.md"}')


if __name__ == '__main__':
    llm.run(main)
