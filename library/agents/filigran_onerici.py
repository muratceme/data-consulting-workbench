"""Filigran (watermark) önerici (AI agent): akış verisinde olay zamanı ile varış zamanı arasındaki gecikmeyi kaynak bazında ölçer ve her kaynak
için watermark gecikmesi ile izin verilen geç gelme süresini (allowed lateness) önerir. Gecikme yüzdelikleri kodla hesaplanır; modele
yalnızca bu dağılım gider, olaylar gönderilmez. Öneri kodla denetlenir: watermark’tan sonra gelen (sonucu güncelleyen) olay payı ve
watermark + izinli gecikmeden sonra gelen (atılan) olay payı sınırı aşmamalı, watermark gereğinden uzun olup sonuçları geciktirmemeli.
Çıktı: watermark.sql (Flink SQL), spark_watermark.py, filigran_plani.csv, filigran.md."""
import argparse
from collections import defaultdict
from datetime import datetime
import llm
import tabular
from common import here, output_dir, write_text, md_table
from analiz import quantile

QS = (.5, .9, .95, .99, .995, .999)


def validator(lat, max_late, max_drop):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('kaynaklar') or []:
            k = str(e.get('kaynak', '')).strip()
            if k not in lat or k in seen:
                problems.append(f'kaynak “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            try:
                wm, allowed = int(e.get('watermark_sn')), int(e.get('izinli_gecikme_sn', 0))
            except (TypeError, ValueError):
                problems.append(f'{k}: watermark_sn ve izinli_gecikme_sn tam sayı olmalı')
                continue
            v = lat[k]
            late, drop = sum(x > wm for x in v) / len(v), sum(x > wm + allowed for x in v) / len(v)
            p95 = quantile(v, .95)
            if wm < 0 or allowed < 0:
                problems.append(f'{k}: süreler negatif olamaz')
            if late > max_late:
                problems.append(f'{k}: watermark’tan sonra gelen olay payı %{100 * late:.1f} (en fazla %{100 * max_late:g}); watermark kısa')
            if drop > max_drop:
                problems.append(f'{k}: watermark + izinli gecikmeden sonra gelip atılan olay payı %{100 * drop:.2f} (en fazla %{100 * max_drop:g})')
            if wm > max(2 * p95, 1):
                problems.append(f'{k}: watermark {wm} sn, p95 gecikme {p95:.1f} sn; 2×p95’ten uzun watermark sonuçları gereksiz geciktirir, geç olaylar için izinli gecikme kullan')
            out[k] = (wm, allowed, str(e.get('gerekce', '')).strip(), late, drop)
        missing = [k for k in lat if k not in seen]
        if missing:
            problems.append('ayarı yazılmayan kaynak: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir akış işleme mühendisisin. Her kaynak için watermark gecikmesini (saniye) ve pencere kapandıktan sonra geç olayları kabul etme '
          'süresini (izinli gecikme, saniye) öner. Watermark çoğu olayı beklemeli ama sonuçları gereksiz geciktirmemeli; nadir çok geç olaylar için izinli '
          'gecikme kullan. Yanıt şeması: {"kaynaklar": [{"kaynak": "...", "watermark_sn": 10, "izinli_gecikme_sn": 60, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Olay gecikmelerinden kaynak bazında watermark ve allowed lateness öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'olay_gecikmeleri.csv')), help='kaynak, olay_zamani, varis_zamani')
    p.add_argument('--source', default='kaynak')
    p.add_argument('--event-time', default='olay_zamani')
    p.add_argument('--arrival-time', default='varis_zamani')
    p.add_argument('--max-late', type=float, default=0.05, help='Watermark’tan sonra gelebilecek en yüksek olay payı')
    p.add_argument('--max-drop', type=float, default=0.005, help='Atılabilecek (izinli gecikmeden de sonra gelen) en yüksek olay payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.source, args.event_time, args.arrival_time):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    lat, bad = defaultdict(list), 0
    for r in rows:
        try:
            d = (datetime.fromisoformat(r[args.arrival_time].strip()) - datetime.fromisoformat(r[args.event_time].strip())).total_seconds()
        except ValueError:
            bad += 1
            continue
        lat[r[args.source].strip()].append(max(d, 0.0))
    lat = {k: sorted(v) for k, v in sorted(lat.items())}
    out = output_dir(args.output)
    user = (f'{sum(map(len, lat.values()))} olay, kaynak bazında gecikme (varış − olay zamanı, saniye) yüzdelikleri (n · p50/p90/p95/p99/p99,5/p99,9 · max):\n'
            + '\n'.join(f'- {k}: {len(v)} · ' + '/'.join(f'{quantile(v, q):.1f}' for q in QS) + f' · {v[-1]:.1f}' for k, v in lat.items())
            + f'\n\nHedef: watermark’tan sonra gelen olay ≤ %{100 * args.max_late:g}, atılan olay ≤ %{100 * args.max_drop:g}.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(lat, args.max_late, args.max_drop))
    if result is None:
        return
    rows_out = [{'kaynak': k, 'watermark_sn': wm, 'izinli_gecikme_sn': al, 'p95_sn': round(quantile(lat[k], .95), 1), 'gec_olay_orani': round(late, 4), 'atilan_olay_orani': round(drop, 4)}
                for k, (wm, al, _, late, drop) in result.items()]
    tabular.write_csv(out / 'filigran_plani.csv', list(rows_out[0]), rows_out)
    sql = ['-- Flink SQL: kaynak başına watermark (olay zamanı sütunu TIMESTAMP(3) olmalı). İzinli gecikme pencere sorgusunda ayarlanır.']
    py = ['# Spark Structured Streaming: kaynak başına watermark (watermark + izinli gecikme; Spark ikisini tek eşik olarak uygular)']
    for k, (wm, al, why, _, _) in result.items():
        name = ''.join(ch if ch.isalnum() else '_' for ch in k)
        sql += ['', f'-- {k}: {why}', f'CREATE TABLE olaylar_{name} (', '  olay_id STRING,', f'  {args.event_time} TIMESTAMP(3),',
                f"  WATERMARK FOR {args.event_time} AS {args.event_time} - INTERVAL '{wm}' SECOND", f") WITH ('connector' = 'kafka', 'topic' = 'olaylar.{name}');",
                f"-- table.exec.emit.allow-lateness = '{al} s'"]
        py += [f'{name}_df = {name}_df.withWatermark("{args.event_time}", "{wm + al} seconds")  # watermark {wm} sn + izinli gecikme {al} sn']
    write_text(out / 'watermark.sql', '\n'.join(sql))
    write_text(out / 'spark_watermark.py', '\n'.join(py))
    md = ['# Watermark planı', '', f'{sum(map(len, lat.values()))} olay, {len(lat)} kaynak' + (f'; {bad} satırın zamanı okunamadı' if bad else '') + '.', '',
          md_table(['Kaynak', 'Watermark (sn)', 'İzinli gecikme (sn)', 'p95 (sn)', 'Geç gelen', 'Atılan', 'Gerekçe'],
                   [[k, wm, al, f'{quantile(lat[k], .95):.1f}', f'%{100 * late:.1f}', f'%{100 * drop:.2f}', why] for k, (wm, al, why, late, drop) in result.items()]), '',
          f'_Model: {meta["model"]} · Modele yalnızca gecikme yüzdelikleri gitti; geç gelen ve atılan olay payları gerçek gecikmelerle kodla hesaplandı._']
    write_text(out / 'filigran.md', '\n'.join(md))
    print(f'[OK] {len(result)} kaynak. Çıktı: {out / "filigran.md"}')


if __name__ == '__main__':
    llm.run(main)
