"""Kaynak tazelik önerici (AI agent): dbt kaynakları (sources) için freshness warn_after / error_after eşiklerini yükleme geçmişinden
önerir. Kaynak başına yüklemeler arası süreler (medyan, p95, en uzun) ve geçmişte kaç kez eşiği aşacağı kodla hesaplanır; modele
yalnızca bu özet gider. Öneri kodla denetlenir: warn eşiği geçmişte sınırdan fazla yanlış uyarı üretmemeli (normal yükleme aralığında
sürekli uyarı olmaz), error eşiği warn’dan uzun olmalı ama gerçek kesintiyi geç fark edecek kadar uzun olmamalı (p95 aralığın belirli
katını aşmamalı), süreler minute/hour/day birimiyle yazılmalı. Çıktı: sources.yml (freshness), tazelik_plani.csv, kaynak-tazelik.md."""
import argparse
from collections import defaultdict
from datetime import datetime
import llm
import tabular
from common import here, output_dir, write_text, md_table
from analiz import quantile

UNIT = {'minute': 1 / 60, 'hour': 1, 'day': 24}


def hours(spec):
    try:
        n, period = int(spec.get('count')), str(spec.get('period', '')).strip()
    except (TypeError, ValueError, AttributeError):
        return None, None
    return (n * UNIT[period], (n, period)) if period in UNIT and n > 0 else (None, None)


def validator(gaps, max_false, max_mult):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('kaynaklar') or []:
            k = str(e.get('kaynak', '')).strip()
            if k not in gaps or k in seen:
                problems.append(f'kaynak “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            (w, ws), (er, es) = hours(e.get('warn_after') or {}), hours(e.get('error_after') or {})
            if w is None or er is None:
                problems.append(f'{k}: warn_after ve error_after {{"count": n, "period": "minute|hour|day"}} biçiminde olmalı')
                continue
            g = gaps[k]
            false = sum(x > w for x in g)
            p95 = quantile(g, .95)
            if false > max_false:
                problems.append(f'{k}: warn {w:g} sa; geçmiş {len(g)} yükleme aralığının {false} tanesinde uyarı verirdi (en fazla {max_false}); p95 aralık {p95:.2f} sa')
            if er <= w:
                problems.append(f'{k}: error_after ({er:g} sa) warn_after’dan ({w:g} sa) uzun olmalı')
            if er > max_mult * p95:
                problems.append(f'{k}: error {er:g} sa, p95 aralık {p95:.2f} sa; {max_mult:g} katından uzun eşik kesintiyi geç fark eder')
            out[k] = {'warn': ws, 'error': es, 'w': w, 'er': er, 'yanlis_uyari': false, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [k for k in gaps if k not in seen]
        if missing:
            problems.append('eşiği yazılmayan kaynak: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir analitik mühendisisin. Her dbt kaynağı için freshness warn_after ve error_after öner: normal yükleme aralıklarında uyarı vermesin, '
          'gerçek kesintiyi zamanında yakalasın. Yanıt şeması: {"kaynaklar": [{"kaynak": "schema.tablo", "warn_after": {"count": 2, "period": "hour"}, '
          '"error_after": {"count": 6, "period": "hour"}, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Yükleme geçmişinden dbt source freshness eşikleri öneren ve geçmişte üreteceği uyarıları kodla sayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'kaynak_yukleme_zamanlari.csv')), help='kaynak (schema.tablo), yuklenme_zamani')
    p.add_argument('--loaded-at-field', default='_yuklenme_zamani', help='Kaynak tablolardaki yükleme zamanı sütunu')
    p.add_argument('--max-false', type=int, default=2, help='Geçmişte izin verilen en fazla yanlış uyarı')
    p.add_argument('--max-multiple', type=float, default=6, help='error_after en fazla p95 aralığın kaç katı olabilir')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('kaynak', 'yuklenme_zamani'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    times = defaultdict(list)
    for r in rows:
        times[r['kaynak'].strip()].append(datetime.fromisoformat(r['yuklenme_zamani'].strip()))
    gaps = {k: sorted((b - a).total_seconds() / 3600 for a, b in zip(sorted(v), sorted(v)[1:])) for k, v in sorted(times.items()) if len(v) > 2}
    if not gaps:
        raise ValueError('Her kaynak için en az 3 yükleme zamanı gerekir.')
    out = output_dir(args.output)
    user = (f'Kaynaklar (yükleme sayısı · aralık saat medyan/p95/p99/en uzun):\n'
            + '\n'.join(f'- {k}: {len(g) + 1} · ' + '/'.join(f'{quantile(g, q):.2f}' for q in (.5, .95, .99)) + f'/{g[-1]:.2f}' for k, g in gaps.items())
            + f'\n\nwarn eşiği geçmişte en fazla {args.max_false} kez aşılabilir; error_after p95 aralığın en fazla {args.max_multiple:g} katı olabilir.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(gaps, args.max_false, args.max_multiple))
    if result is None:
        return
    plan = [{'kaynak': k, 'warn_after': f'{m["warn"][0]} {m["warn"][1]}', 'error_after': f'{m["error"][0]} {m["error"][1]}', 'p95_aralik_sa': round(quantile(gaps[k], .95), 2),
             'en_uzun_aralik_sa': round(gaps[k][-1], 2), 'gecmiste_uyari': m['yanlis_uyari'], 'gecmiste_hata': sum(x > m['er'] for x in gaps[k])} for k, m in result.items()]
    tabular.write_csv(out / 'tazelik_plani.csv', list(plan[0]), plan)
    by_schema = defaultdict(list)
    for k in result:
        schema, _, table = k.rpartition('.')
        by_schema[schema or 'kaynak'].append((table, result[k]))
    yml = ['version: 2', 'sources:']
    for schema, tables in by_schema.items():
        yml += [f'  - name: {schema}', f'    loaded_at_field: {args.loaded_at_field}', '    tables:']
        for t, m in tables:
            yml += [f'      - name: {t}', '        freshness:', f'          warn_after: {{count: {m["warn"][0]}, period: {m["warn"][1]}}}',
                    f'          error_after: {{count: {m["error"][0]}, period: {m["error"][1]}}}']
    write_text(out / 'sources.yml', '\n'.join(yml))
    md = ['# Kaynak tazelik eşikleri', '', f'{len(plan)} kaynak; eşikler geçmiş yükleme aralıklarıyla sınandı (`dbt source freshness`).', '',
          md_table(['Kaynak', 'warn_after', 'error_after', 'p95 aralık (sa)', 'En uzun (sa)', 'Geçmişte uyarı', 'Geçmişte hata', 'Gerekçe'],
                   [[p_['kaynak'], p_['warn_after'], p_['error_after'], p_['p95_aralik_sa'], p_['en_uzun_aralik_sa'], p_['gecmiste_uyari'], p_['gecmiste_hata'],
                     result[p_['kaynak']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca aralık yüzdelikleri gitti; geçmişte üreteceği uyarılar ve eşik sınırları kodla denetlendi._']
    write_text(out / 'kaynak-tazelik.md', '\n'.join(md))
    print(f'[OK] {len(plan)} kaynak. Çıktı: {out / "kaynak-tazelik.md"}')


if __name__ == '__main__':
    llm.run(main)
