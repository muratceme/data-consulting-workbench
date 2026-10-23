"""Ambar boyutu seçici (AI agent): Snowflake tipi sanal ambarlarda iş yükü başına ambar boyutu, en fazla küme sayısı (multi-cluster) ve
otomatik askıya alma süresi önerir. Kod basit bir ölçekleme modeliyle hesaplar: bir boyut büyütmek sorgu süresini 0,6 katına indirir ve
saatlik krediyi ikiye katlar; günlük kredi = sorgu × ortalama süre × kredi/saat + askıya alma beklemesi. Modele yalnızca iş yükü özeti
gider. Öneri kodla denetlenir: p95 süre SLA’yı karşılamalı, küme × 8 eşzamanlı sorgu tepe eşzamanlılığı karşılamalı, otomatik askıya
alma 60–600 sn olmalı ve günlük kredi SLA’yı karşılayan en ucuz boyutun belirli payı içinde kalmalı. Çıktı: ambar_ayarlari.sql, ambar_plani.csv, ambar-boyutu.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table

SIZES = ['XS', 'S', 'M', 'L', 'XL', '2XL']
CREDITS = {s: 2 ** i for i, s in enumerate(SIZES)}
SNOWFLAKE = {'XS': 'XSMALL', 'S': 'SMALL', 'M': 'MEDIUM', 'L': 'LARGE', 'XL': 'XLARGE', '2XL': 'XXLARGE'}
SLOTS = 8


def estimate(w, size, suspend):
    step = SIZES.index(size) - SIZES.index(w['boyut'])
    f = 0.6 ** step
    busy_h = w['sorgu'] * w['ort'] * f / 3600
    idle_h = min(w['sorgu'], 24 * 3600 / max(suspend, 1)) * suspend / 3600 * 0.3  # rough: a third of query gaps end in a suspend wait
    return {'p95': w['p95'] * f, 'kredi': (busy_h + idle_h) * CREDITS[size]}


def validator(work, tol):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('is_yukleri') or []:
            k = str(e.get('is_yuku', '')).strip()
            if k not in work or k in seen:
                problems.append(f'iş yükü “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            size = str(e.get('boyut', '')).strip().upper()
            try:
                clusters, suspend = int(e.get('max_kume')), int(e.get('otomatik_askiya_sn'))
            except (TypeError, ValueError):
                problems.append(f'{k}: max_kume ve otomatik_askiya_sn tam sayı olmalı')
                continue
            if size not in SIZES:
                problems.append(f'{k}: boyut {", ".join(SIZES)} olmalı')
                continue
            w = work[k]
            est = estimate(w, size, suspend)
            if est['p95'] > w['sla']:
                problems.append(f'{k}: {size} ile tahmini p95 {est["p95"]:.0f} sn, SLA {w["sla"]:g} sn')
            if clusters * SLOTS < w['tepe']:
                problems.append(f'{k}: {clusters} küme × {SLOTS} = {clusters * SLOTS} eşzamanlı sorgu, tepe {w["tepe"]:g}; sorgular kuyrukta bekler')
            if not 60 <= suspend <= 600:
                problems.append(f'{k}: otomatik askıya alma 60–600 sn olmalı')
            ok = [s for s in SIZES if estimate(w, s, 60)['p95'] <= w['sla']]
            if ok and not problems:
                cheapest = min((estimate(w, s, 60)['kredi'], s) for s in ok)
                if est['kredi'] > cheapest[0] * (1 + tol):
                    problems.append(f'{k}: {size} günlük {est["kredi"]:.1f} kredi; SLA’yı karşılayan en ucuz seçenek {cheapest[1]} {cheapest[0]:.1f} kredi')
            out[k] = {'boyut': size, 'kume': clusters, 'askiya': suspend, **est, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [k for k in work if k not in seen]
        if missing:
            problems.append('ayarı yazılmayan iş yükü: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri ambarı FinOps mühendisisin. Her iş yükü için ambar boyutu (XS–2XL), en fazla küme sayısı (multi-cluster) ve otomatik askıya '
          'alma süresi (sn) öner: p95 süre SLA’yı karşılasın, eşzamanlı sorgular kuyrukta beklemesin, kredi gereksiz harcanmasın. Yanıt şeması: '
          '{"is_yukleri": [{"is_yuku": "...", "boyut": "M", "max_kume": 2, "otomatik_askiya_sn": 60, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='İş yükü başına ambar boyutu, küme sayısı ve askıya alma süresi öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'ambar_is_yukleri.csv')),
                   help='is_yuku, mevcut_boyut, gunluk_sorgu, ort_sure_sn, p95_sure_sn, tepe_eszamanli, sla_p95_sn')
    p.add_argument('--credit-price', type=float, default=3.0, help='Kredi birim fiyatı')
    p.add_argument('--tolerance', type=float, default=0.25, help='En ucuz uygun seçeneğe göre kabul edilen fazla kredi')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    num = lambda v: float(v.replace(',', '.'))
    work = {r['is_yuku'].strip(): {'boyut': r['mevcut_boyut'].strip().upper(), 'sorgu': num(r['gunluk_sorgu']), 'ort': num(r['ort_sure_sn']), 'p95': num(r['p95_sure_sn']),
                                   'tepe': num(r['tepe_eszamanli']), 'sla': num(r['sla_p95_sn'])} for r in rows}
    out = output_dir(args.output)
    lines = []
    for k, w in work.items():
        cur = estimate(w, w['boyut'], 600)
        lines.append(f'- {k}: {w["boyut"]} · {w["sorgu"]:g} sorgu/gün · ort {w["ort"]:g} sn · p95 {w["p95"]:g} sn · tepe {w["tepe"]:g} eşzamanlı · SLA p95 {w["sla"]:g} sn · '
                     f'boyuta göre tahmini p95/kredi: ' + ', '.join(f'{s} {estimate(w, s, 60)["p95"]:.0f}/{estimate(w, s, 60)["kredi"]:.1f}' for s in SIZES)
                     + f' · bugünkü (600 sn askıya alma) {cur["kredi"]:.1f} kredi')
    user = (f'Model: bir boyut büyütmek süreyi 0,6 katına indirir, krediyi 2 katına çıkarır; küme başına {SLOTS} eşzamanlı sorgu.\nİş yükleri:\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(work, args.tolerance))
    if result is None:
        return
    sql, plan = [], []
    for k, m in result.items():
        sql.append(f"ALTER WAREHOUSE wh_{k} SET WAREHOUSE_SIZE = '{SNOWFLAKE[m['boyut']]}' "
                   f"MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = {m['kume']} AUTO_SUSPEND = {m['askiya']} AUTO_RESUME = TRUE;")
        cur = estimate(work[k], work[k]['boyut'], 600)['kredi']
        plan.append({'is_yuku': k, 'mevcut': work[k]['boyut'], 'onerilen': m['boyut'], 'max_kume': m['kume'], 'otomatik_askiya_sn': m['askiya'], 'p95_tahmini_sn': round(m['p95']),
                     'kredi_once': round(cur, 1), 'kredi_sonra': round(m['kredi'], 1)})
    write_text(out / 'ambar_ayarlari.sql', '\n'.join(sql))
    tabular.write_csv(out / 'ambar_plani.csv', list(plan[0]), plan)
    before, after = sum(p_['kredi_once'] for p_ in plan), sum(p_['kredi_sonra'] for p_ in plan)
    md = ['# Ambar boyutları', '', f'Günlük tahmini kredi {before:.1f} → {after:.1f} (aylık ~{30 * (before - after) * args.credit_price:,.0f} tasarruf, kredi {args.credit_price:g}).', '',
          md_table(['İş yükü', 'Mevcut', 'Önerilen', 'Max küme', 'Askıya alma (sn)', 'p95 tahmini (sn)', 'Kredi/gün önce', 'Kredi/gün sonra', 'Gerekçe'],
                   [[p_['is_yuku'], p_['mevcut'], p_['onerilen'], p_['max_kume'], p_['otomatik_askiya_sn'], p_['p95_tahmini_sn'], p_['kredi_once'], p_['kredi_sonra'],
                     result[p_['is_yuku']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Süre ve kredi basit bir ölçekleme modeliyle kodla tahmin edildi; SLA, eşzamanlılık ve maliyet sınırları kodla denetlendi. '
          'Değişikliği bir hafta izleyip QUERY_HISTORY ile doğrulayın._']
    write_text(out / 'ambar-boyutu.md', '\n'.join(md))
    print(f'[OK] {len(plan)} iş yükü, kredi {before:.1f} → {after:.1f}/gün. Çıktı: {out / "ambar-boyutu.md"}')


if __name__ == '__main__':
    llm.run(main)
