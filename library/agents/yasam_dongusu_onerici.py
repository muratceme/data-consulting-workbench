"""Yaşam döngüsü önerici (AI agent): nesne depolamadaki (S3/ADLS/GCS) her ön ek için yaşa göre depolama sınıfı geçişlerini önerir:
Standard → Infrequent Access (IA) → Glacier Instant Retrieval (GIR). Yaş dilimlerine göre boyut ve aylık okuma hacmi kodla okunur;
aylık maliyet = depolama + okuma (geri getirme) ücreti olarak kodla hesaplanır ve tüm eşik kombinasyonları taranır. Modele yalnızca bu
özet gider. Öneri kodla denetlenir: eşikler yaş dilimi sınırlarından (30/90/365 gün) seçilmeli, GIR eşiği IA’dan sonra gelmeli, yoğun
okunan veri ucuz ama geri getirmesi pahalı sınıfa taşınıp maliyeti artırmamalı ve toplam maliyet en iyi kombinasyonun belirli payı
içinde olmalı. Çıktı: lifecycle.json (S3 lifecycle kuralları), yasam_dongusu_plani.csv, yasam-dongusu.md."""
import argparse
from itertools import product
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

BUCKETS = [(0, 'yas_0_30_tb', 'okuma_0_30_tb'), (30, 'yas_30_90_tb', 'okuma_30_90_tb'), (90, 'yas_90_365_tb', 'okuma_90_365_tb'), (365, 'yas_365_ustu_tb', 'okuma_365_ustu_tb')]
EDGES = (30, 90, 365)
PRICE = {'STANDARD': 0.023, 'STANDARD_IA': 0.0125, 'GLACIER_IR': 0.004}
RETRIEVAL = {'STANDARD': 0.0, 'STANDARD_IA': 0.01, 'GLACIER_IR': 0.03}


def tier(age, ia, gir):
    if gir is not None and age >= gir:
        return 'GLACIER_IR'
    if ia is not None and age >= ia:
        return 'STANDARD_IA'
    return 'STANDARD'


def cost(pref, ia, gir):
    total = 0.0
    for age, size, reads in pref['b']:
        t = tier(age, ia, gir)
        total += size * 1024 * PRICE[t] + reads * 1024 * RETRIEVAL[t]
    return total


def options():
    for ia, gir in product((None,) + EDGES, (None,) + EDGES):
        if ia is not None and gir is not None and gir <= ia:
            continue
        yield ia, gir


def validator(prefs, tol):
    best = {k: min((cost(v, ia, gir), ia, gir) for ia, gir in options()) for k, v in prefs.items()}

    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('on_ekler') or []:
            k = str(e.get('on_ek', '')).strip()
            if k not in prefs or k in seen:
                problems.append(f'ön ek “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            try:
                ia = None if e.get('ia_gun') in (None, '') else int(e.get('ia_gun'))
                gir = None if e.get('glacier_ir_gun') in (None, '') else int(e.get('glacier_ir_gun'))
            except (TypeError, ValueError):
                problems.append(f'{k}: gün değerleri tam sayı ya da null olmalı')
                continue
            if any(x is not None and x not in EDGES for x in (ia, gir)):
                problems.append(f'{k}: eşikler yaş dilimi sınırlarından biri olmalı ({", ".join(map(str, EDGES))})')
                continue
            if ia is not None and gir is not None and gir <= ia:
                problems.append(f'{k}: Glacier IR eşiği ({gir}) IA eşiğinden ({ia}) sonra olmalı')
                continue
            c, now, b = cost(prefs[k], ia, gir), cost(prefs[k], None, None), best[k]
            if c > now:
                problems.append(f'{k}: önerilen kurallar aylık maliyeti {now:,.0f} → {c:,.0f} artırıyor (okuma ücretleri)')
            elif c > b[0] * (1 + tol):
                problems.append(f'{k}: aylık {c:,.0f}; en iyi kombinasyon IA {b[1] or "yok"} / GIR {b[2] or "yok"} gün ile {b[0]:,.0f}')
            out[k] = (ia, gir, c, now, str(e.get('gerekce', '')).strip())
        missing = [k for k in prefs if k not in seen]
        if missing:
            problems.append('kuralı yazılmayan ön ek: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir bulut maliyet (FinOps) mühendisisin. Her ön ek için nesnelerin kaç gün sonra Standard-IA’ya ve Glacier Instant Retrieval’a '
          'geçeceğini seç (30, 90, 365 ya da null): depolama ucuzlar ama okuma (geri getirme) ücreti artar; sık okunan veriyi taşıma. Yanıt şeması: '
          '{"on_ekler": [{"on_ek": "ham/loglar/", "ia_gun": 30, "glacier_ir_gun": 90, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Yaş ve okuma hacminden nesne depolama yaşam döngüsü kuralları öneren, maliyeti kodla hesaplayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'depolama_on_ekleri.csv')),
                   help='on_ek, yas_*_tb (0-30, 30-90, 90-365, 365+), okuma_*_tb (aylık), saklama_gun')
    p.add_argument('--bucket', default='veri-golu')
    p.add_argument('--tolerance', type=float, default=0.05)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    num = lambda v: float(v.replace(',', '.')) if v.strip() else 0.0
    prefs = {r['on_ek'].strip(): {'b': [(age, num(r[s]), num(r[g])) for age, s, g in BUCKETS], 'saklama': r.get('saklama_gun', '').strip()} for r in rows}
    out = output_dir(args.output)
    lines = []
    for k, v in prefs.items():
        lines.append(f'- {k}: ' + '; '.join(f'{age}+ gün {s:g} TB, okuma {g:g} TB/ay' for age, s, g in v['b']) + f' · bugün {cost(v, None, None):,.0f}/ay'
                     + (f' · saklama {v["saklama"]} gün' if v['saklama'] else ''))
    user = ('Fiyatlar (GB-ay depolama / GB okuma): ' + ', '.join(f'{t} {PRICE[t]}/{RETRIEVAL[t]}' for t in PRICE)
            + '\nÖn ekler (yaş dilimi boyutu ve aylık okuma):\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(prefs, args.tolerance))
    if result is None:
        return
    rules = []
    for k, (ia, gir, c, now, why) in result.items():
        trans = [{'Days': d, 'StorageClass': s} for d, s in ((ia, 'STANDARD_IA'), (gir, 'GLACIER_IR')) if d is not None]
        rule = {'ID': k.strip('/').replace('/', '-') or 'kok', 'Filter': {'Prefix': k}, 'Status': 'Enabled'}
        if trans:
            rule['Transitions'] = trans
        if prefs[k]['saklama']:
            rule['Expiration'] = {'Days': int(prefs[k]['saklama'])}
        rules.append(rule)
    write_json(out / 'lifecycle.json', {'Rules': rules})
    plan = [{'on_ek': k, 'ia_gun': ia or '', 'glacier_ir_gun': gir or '', 'aylik_once': round(now), 'aylik_sonra': round(c)} for k, (ia, gir, c, now, _) in result.items()]
    tabular.write_csv(out / 'yasam_dongusu_plani.csv', list(plan[0]), plan)
    before, after = sum(p_['aylik_once'] for p_ in plan), sum(p_['aylik_sonra'] for p_ in plan)
    md = ['# Depolama yaşam döngüsü', '', f'Aylık depolama + okuma maliyeti {before:,} → {after:,} (yıllık ~{12 * (before - after):,} tasarruf). '
          f'`aws s3api put-bucket-lifecycle-configuration --bucket {args.bucket} --lifecycle-configuration file://lifecycle.json`', '',
          md_table(['Ön ek', 'IA (gün)', 'Glacier IR (gün)', 'Aylık önce', 'Aylık sonra', 'Gerekçe'],
                   [[p_['on_ek'], p_['ia_gun'] or '—', p_['glacier_ir_gun'] or '—', f'{p_["aylik_once"]:,}', f'{p_["aylik_sonra"]:,}', result[p_['on_ek']][4]] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Maliyet fiyat tablosuyla kodla hesaplandı ve tüm eşik kombinasyonlarıyla karşılaştırıldı. Saklama süresi olan ön eklerde süre sonunda silme '
          'kuralı eklendi; IA ve GIR’in en az saklama süreleri (30/90 gün) ve geçiş istek ücretleri ayrıca değerlendirilmelidir._']
    write_text(out / 'yasam-dongusu.md', '\n'.join(md))
    print(f'[OK] {len(plan)} ön ek, aylık {before:,} → {after:,}. Çıktı: {out / "yasam-dongusu.md"}')


if __name__ == '__main__':
    llm.run(main)
