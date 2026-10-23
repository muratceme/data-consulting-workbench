"""Log örnekleme önerici (AI agent): servis ve log seviyesi başına günlük satır sayısı ve ortalama boyuttan, log platformuna gönderilecek
örnekleme oranlarını önerir (DEBUG kapat, INFO’yu örnekle, WARN/ERROR hepsini tut). Günlük hacim ve maliyet kodla hesaplanır; modele
yalnızca bu özet gider, log içeriği gönderilmez. Öneri kodla denetlenir: WARN, ERROR ve FATAL örneklenemez (oran 1), her serviste hata
ayıklama için INFO’dan günde en az belirli sayıda satır kalmalı, oranlar 0–1 arasında olmalı ve toplam günlük hacim bütçeyi aşmamalı.
Çıktı: otel_ornekleme.yaml (OpenTelemetry Collector), ornekleme_plani.csv, log-ornekleme.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table

KEEP = ('WARN', 'ERROR', 'FATAL')
GB = 1024 ** 3


def validator(vol, budget_gb, min_info):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('oranlar') or []:
            k = (str(e.get('servis', '')).strip(), str(e.get('seviye', '')).strip().upper())
            if k not in vol or k in seen:
                problems.append(f'{k[0]}/{k[1]} yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            try:
                r = float(e.get('oran'))
            except (TypeError, ValueError):
                problems.append(f'{k[0]}/{k[1]}: oran 0–1 arası sayı olmalı')
                continue
            if not 0 <= r <= 1:
                problems.append(f'{k[0]}/{k[1]}: oran 0–1 arası olmalı')
                continue
            if k[1] in KEEP and r < 1:
                problems.append(f'{k[0]}/{k[1]}: uyarı ve hata logları örneklenmez (oran 1 olmalı)')
            if k[1] == 'INFO' and vol[k]['satir'] * r < min_info:
                problems.append(f'{k[0]}/INFO: günde {vol[k]["satir"] * r:,.0f} satır kalıyor; hata ayıklama için en az {min_info:,}')
            out[k] = r
        missing = [f'{s}/{l}' for s, l in vol if (s, l) not in seen]
        if missing:
            problems.append('oranı yazılmayan: ' + ', '.join(missing))
        total = sum(vol[k]['gb'] * r for k, r in out.items())
        if not problems and total > budget_gb:
            problems.append(f'günlük hacim {total:.1f} GB, bütçe {budget_gb:g} GB')
        if problems:
            raise ValueError('; '.join(problems))
        return out, total
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir gözlemlenebilirlik (observability) mühendisisin. Servis ve seviye başına log örnekleme oranı öner (0–1): WARN/ERROR/FATAL hepsi '
          'tutulur, DEBUG genelde kapatılır, gürültülü INFO örneklenir; toplam hacim bütçeye sığsın ama her serviste hata ayıklamaya yetecek INFO kalsın. '
          'Yanıt şeması: {"oranlar": [{"servis": "...", "seviye": "INFO", "oran": 0.2, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Servis/seviye başına log örnekleme oranı öneren, hacim bütçesini ve zorunlu seviyeleri kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'log_hacmi.csv')), help='servis, seviye, gunluk_satir, ort_bayt')
    p.add_argument('--budget-gb', type=float, default=15, help='Günlük log platformu hacim bütçesi (GB)')
    p.add_argument('--price-gb', type=float, default=0.5, help='GB başına alım (ingest) ücreti')
    p.add_argument('--min-info', type=int, default=500000, help='Servis başına günde tutulacak en az INFO satırı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    vol = {}
    for r in rows:
        n, b = float(r['gunluk_satir'].replace(',', '.')), float(r['ort_bayt'].replace(',', '.'))
        vol[(r['servis'].strip(), r['seviye'].strip().upper())] = {'satir': n, 'gb': n * b / GB}
    now = sum(v['gb'] for v in vol.values())
    out = output_dir(args.output)
    user = (f'Bugünkü hacim {now:.1f} GB/gün ({now * args.price_gb * 30:,.0f} aylık), bütçe {args.budget_gb:g} GB/gün. Servis başına en az {args.min_info:,} INFO satırı kalmalı.\n'
            'Servis/seviye (günlük satır · GB):\n' + '\n'.join(f'- {s}/{l}: {v["satir"]:,.0f} · {v["gb"]:.2f}' for (s, l), v in vol.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(vol, args.budget_gb, args.min_info))
    if result is None:
        return
    rates, total = result
    plan = [{'servis': s, 'seviye': l, 'oran': r, 'gunluk_satir_once': int(vol[(s, l)]['satir']), 'gunluk_satir_sonra': round(vol[(s, l)]['satir'] * r),
             'gb_sonra': round(vol[(s, l)]['gb'] * r, 3)} for (s, l), r in rates.items()]
    tabular.write_csv(out / 'ornekleme_plani.csv', list(plan[0]), plan)
    yml = ['# OpenTelemetry Collector: servis/seviye başına log örnekleme', 'processors:']
    for (s, l), r in rates.items():
        name = f'{s}_{l.lower()}'.replace('-', '_')
        if r >= 1:
            continue
        cond = f'resource.attributes["service.name"] == "{s}" and severity_text == "{l}"'
        if r == 0:
            yml += [f'  filter/{name}:', '    logs:', '      log_record:', f"        - '{cond}'"]
        else:
            yml += [f'  probabilistic_sampler/{name}:', f'    sampling_percentage: {100 * r:g}', f'    # yalnızca: {cond} (routing connector ile ayrı hatta uygulanır)']
    write_text(out / 'otel_ornekleme.yaml', '\n'.join(yml))
    md = ['# Log örnekleme', '', f'Günlük hacim {now:.1f} GB → {total:.1f} GB (bütçe {args.budget_gb:g} GB); aylık ingest maliyeti {now * args.price_gb * 30:,.0f} → {total * args.price_gb * 30:,.0f}.', '',
          md_table(['Servis', 'Seviye', 'Oran', 'Satır/gün önce', 'Satır/gün sonra', 'GB sonra'],
                   [[p_['servis'], p_['seviye'], f'{p_["oran"]:g}', f'{p_["gunluk_satir_once"]:,}', f'{p_["gunluk_satir_sonra"]:,}', p_['gb_sonra']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca hacim özeti gitti; zorunlu seviyeler, INFO alt sınırı ve bütçe kodla denetlendi._']
    write_text(out / 'log-ornekleme.md', '\n'.join(md))
    print(f'[OK] {len(plan)} servis/seviye, {total:.1f} GB/gün. Çıktı: {out / "log-ornekleme.md"}')


if __name__ == '__main__':
    llm.run(main)
