"""Alarm eşiği önerici (AI agent): tablo yükleme metriklerinin (günlük satır sayısı, yükleme süresi, boş değer oranı) geçmişinden her metrik
için alarm kuralı önerir. Modele yalnızca metrik adları ve değerleri gider. Önerilen her kural yerelde geçmiş veri üzerinde geriye dönük
denenir: işaretli olaylardan (olay=1) birini kaçıran ya da olay olmayan günlerde izin verilenden fazla yanlış alarm veren kural seti,
kaçırılan olayların ve yanlış alarmların tarih/değerleriyle modele geri gönderilir. Kural tipleri: sabit alt/üst sınır ve aynı haftanın
gününün son 4 haftalık medyanına oran (hafta içi / hafta sonu farkını kendiliğinden yakalar).
Çıktı: alarm_kurallari.json, geriye_donuk_test.csv, alarm_kontrol.py (yeni veride aynı kuralları çalıştıran bağımsız betik), alarm-kurallari.md."""
import argparse, json, statistics
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

TYPES = {'sabit_alt': 'değer < eşik', 'sabit_ust': 'değer > eşik', 'oran_alt': 'değer < eşik × aynı gün medyanı', 'oran_ust': 'değer > eşik × aynı gün medyanı'}

# The evaluator is written once and shipped as alarm_kontrol.py too, so the backtest and the delivered script are the same code.
EVALUATOR = r'''
import statistics
from collections import defaultdict


def baseline(history, day, weeks=4):
    """Median of the same weekday in the previous `weeks` weeks (None when fewer than 2 points)."""
    from datetime import timedelta
    vals = [history[day - timedelta(days=7 * k)] for k in range(1, weeks + 1) if day - timedelta(days=7 * k) in history]
    return statistics.median(vals) if len(vals) >= 2 else None


def fires(rule, value, base):
    t, x = rule['tip'], float(rule['esik'])
    if t == 'sabit_alt':
        return value < x
    if t == 'sabit_ust':
        return value > x
    if base is None:
        return False
    return value < x * base if t == 'oran_alt' else value > x * base


def evaluate(series, rules):
    """series: {metric: {date: value}} -> [(metric, date, value, base, [fired rule ids])]"""
    out = []
    for metric, history in series.items():
        for day in sorted(history):
            base = baseline(history, day)
            hit = [r['id'] for r in rules if r['metrik'] == metric and fires(r, history[day], base)]
            out.append((metric, day, history[day], base, hit))
    return out
'''
exec(EVALUATOR)

SCRIPT = ('"""Alarm kontrolü: alarm_kurallari.json’daki kuralları metrik CSV’sinde (tarih, metrik, deger) çalıştırır.\n'
          'Kullanım: python alarm_kontrol.py metrikler.csv [alarm_kurallari.json]  — alarm varsa çıkış kodu 2."""\n'
          'import csv, json, sys\nfrom datetime import date\n' + EVALUATOR + r'''

def main():
    path, rules_path = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else 'alarm_kurallari.json')
    rules = json.load(open(rules_path, encoding='utf-8'))['kurallar']
    text = open(path, encoding='utf-8-sig').read()
    rows = list(csv.DictReader(text.splitlines(), delimiter=';' if text.split('\n', 1)[0].count(';') > text.split('\n', 1)[0].count(',') else ','))
    series = defaultdict(dict)
    for r in rows:
        series[r['metrik'].strip()][date.fromisoformat(r['tarih'].strip()[:10])] = float(r['deger'].replace(',', '.'))
    alarms = [(m, d, v, hit) for m, d, v, b, hit in evaluate(series, rules) if hit]
    last = max(d for s in series.values() for d in s)
    for m, d, v, hit in alarms:
        print(f"{'SON GÜN ' if d == last else ''}ALARM {d} {m}={v:g} kural={','.join(hit)}")
    today = [a for a in alarms if a[1] == last]
    print(f'{len(alarms)} alarm ({len(today)} tanesi son gün {last}).')
    sys.exit(2 if today else 0)


if __name__ == '__main__':
    main()
''')


def load(path):
    columns, rows = tabular.read(path)
    for c in ('tarih', 'metrik', 'deger'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    style = tabular.number_style([r['deger'] for r in rows])
    series, incidents = defaultdict(dict), set()
    for r in rows:
        d = tabular.parse_date(r['tarih'])
        if not d:
            raise ValueError(f'tarih okunamadı: {r["tarih"]}')
        m, day = r['metrik'].strip(), d[0].date()
        series[m][day] = tabular.parse_number(r['deger'], style)
        if (r.get('olay') or '').strip() in ('1', 'evet', 'e', 'true'):
            incidents.add((m, day))
    return series, incidents


def validator(series, incidents, max_false):
    def validate(obj):
        rules, problems = [], []
        for i, r in enumerate(obj.get('kurallar') or [], 1):
            if not isinstance(r, dict):
                continue
            m, t = str(r.get('metrik', '')).strip(), str(r.get('tip', '')).strip()
            try:
                x = float(r.get('esik'))
            except (TypeError, ValueError):
                problems.append(f'{i}. kural: eşik sayı değil')
                continue
            if m not in series:
                problems.append(f'{i}. kural: “{m}” metriği yok')
            elif t not in TYPES:
                problems.append(f'{i}. kural: tip {", ".join(TYPES)} olmalı')
            elif t.startswith('oran') and not 0 < x < 20:
                problems.append(f'{i}. kural: oran eşiği 0–20 arasında olmalı')
            elif not str(r.get('gerekce', '')).strip():
                problems.append(f'{i}. kural: gerekçe yok')
            else:
                rules.append({'id': f'{m}:{t}', 'metrik': m, 'tip': t, 'esik': x, 'gerekce': str(r['gerekce']).strip(),
                              'onem': 'kritik' if str(r.get('onem', '')).strip().lower() == 'kritik' else 'uyarı'})
        ids = [r['id'] for r in rules]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            problems.append(f'aynı metrik için aynı tipte birden fazla kural: {", ".join(dup)}')
        no_rule = [m for m in series if m not in {r['metrik'] for r in rules}]
        if no_rule:
            problems.append(f'kuralı olmayan metrik: {", ".join(no_rule)}')
        if problems:
            raise ValueError('; '.join(problems))
        result = evaluate(series, rules)
        missed = [(m, d, v) for m, d, v, b, hit in result if (m, d) in incidents and not hit]
        false = defaultdict(list)
        for m, d, v, b, hit in result:
            if hit and (m, d) not in incidents:
                false[m].append((d, v, hit))
        noisy = {m: f for m, f in false.items() if len(f) > max_false}
        if missed or noisy:
            msg = []
            if missed:
                msg.append('kaçırılan olaylar: ' + ', '.join(f'{m} {d} değer={v:g}' for m, d, v in missed))
            for m, f in noisy.items():
                msg.append(f'{m}: {len(f)} yanlış alarm (en fazla {max_false}): ' + ', '.join(f'{d} değer={v:g} ({"/".join(h.split(":")[1] for h in hit)})' for d, v, hit in f[:8]))
            raise ValueError('; '.join(msg) + '. Eşikleri geçmişe göre ayarla.')
        return rules, result, false
    return validate


SYSTEM = (llm.SAFETY + ' Sen veri gözlemlenebilirliği (data observability) uzmanısın. Her metrik için olayları yakalayan ama gereksiz alarm üretmeyen '
          'alarm kuralları öner. Kural tipleri: sabit_alt (değer < eşik), sabit_ust (değer > eşik), oran_alt / oran_ust (değer, haftanın aynı gününün '
          'son 4 haftalık medyanının eşik katından küçük/büyük; hafta içi/sonu farkı olan metriklerde bunu kullan). Her metrik için 1–3 kural yaz; '
          'aynı metrikte aynı tip bir kez kullanılır. Olay olarak işaretli günlerin hepsi en az bir kuralı tetiklemeli. onem: kritik veya uyarı. '
          'Yanıt şeması: {"kurallar": [{"metrik": "...", "tip": "oran_alt", "esik": 0.5, "onem": "kritik", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Metrik geçmişinden alarm eşiği öneren ve geriye dönük deneyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'metrik_gecmisi.csv')), help='tarih, metrik, deger[, olay] sütunlu CSV/XLSX')
    p.add_argument('--max-false', type=int, default=2, help='Metrik başına izin verilen yanlış alarm sayısı (geçmiş boyunca)')
    llm.add_arguments(p)
    args = p.parse_args()
    series, incidents = load(args.input)
    out = output_dir(args.output)
    parts = []
    for m, hist in series.items():
        vals = list(hist.values())
        days = sorted(hist)
        wk = [hist[d] for d in days if d.weekday() < 5]
        we = [hist[d] for d in days if d.weekday() >= 5]
        parts.append(f'## {m}\n{len(vals)} gün ({days[0]} – {days[-1]}); medyan {statistics.median(vals):g}, min {min(vals):g}, max {max(vals):g}'
                     + (f'; hafta içi medyan {statistics.median(wk):g}, hafta sonu medyan {statistics.median(we):g}' if wk and we else '')
                     + f'\nOlay günleri: ' + (', '.join(f'{d} ({hist[d]:g})' for d in days if (m, d) in incidents) or 'yok')
                     + '\nDeğerler (tarih=değer, haftanın günü): ' + ', '.join(f'{d.isoformat()}={hist[d]:g}({"PtSaÇaPeCuCtPz"[d.weekday() * 2:d.weekday() * 2 + 2]})' for d in days))
    user = f'Yanlış alarm bütçesi: metrik başına geçmiş boyunca en fazla {args.max_false}.\n\n' + '\n\n'.join(parts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(series, incidents, args.max_false))
    if result is None:
        return
    rules, backtest, false = result
    write_json(out / 'alarm_kurallari.json', {'kurallar': rules})
    write_text(out / 'alarm_kontrol.py', SCRIPT)
    rows = [{'metrik': m, 'tarih': d.isoformat(), 'deger': v, 'medyan_ayni_gun': '' if b is None else round(b, 4), 'olay': int((m, d) in incidents),
             'alarm': ','.join(h.split(':')[1] for h in hit)} for m, d, v, b, hit in backtest]
    tabular.write_csv(out / 'geriye_donuk_test.csv', list(rows[0]), rows)
    caught = sum(1 for r in rows if r['olay'] and r['alarm'])
    md = ['# Alarm kuralları', '', f'{len(series)} metrik için {len(rules)} kural. Geriye dönük test: {len(incidents)} işaretli olayın {caught}’i yakalandı; '
          f'olay olmayan günlerde toplam {sum(len(f) for f in false.values())} yanlış alarm (metrik başına en fazla {args.max_false}).', '',
          md_table(['Metrik', 'Kural', 'Eşik', 'Önem', 'Olay yakaladı', 'Yanlış alarm', 'Gerekçe'],
                   [[r['metrik'], TYPES[r['tip']], f'{r["esik"]:g}', r['onem'], sum(1 for m, d, v, b, h in backtest if r['id'] in h and (m, d) in incidents),
                     sum(1 for m, d, v, b, h in backtest if r['id'] in h and (m, d) not in incidents), r['gerekce']] for r in rules]), '']
    if any(false.values()):
        md += ['## Yanlış alarmlar', '', md_table(['Metrik', 'Tarih', 'Değer'], [[m, d, f'{v:g}'] for m, f in false.items() for d, v, _ in f]), '']
    md += ['Yeni veride çalıştırmak için: `python alarm_kontrol.py metrikler.csv alarm_kurallari.json` (son günde alarm varsa çıkış kodu 2; zamanlayıcıda kullanılabilir).', '',
           f'_Model: {meta["model"]} · Kurallar geçmiş veride kodla denendi; alarm_kontrol.py aynı değerlendirme kodunu kullanır. Geçmişe uyan eşik geleceği garanti etmez; '
           'birkaç hafta sonra yeniden çalıştırın._']
    write_text(out / 'alarm-kurallari.md', '\n'.join(md))
    print(f'[OK] {len(rules)} kural; {caught}/{len(incidents)} olay yakalandı. Çıktı: {out / "alarm-kurallari.md"}')


if __name__ == '__main__':
    llm.run(main)
