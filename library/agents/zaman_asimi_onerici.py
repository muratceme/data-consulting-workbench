"""Zaman aşımı önerici (AI agent): PostgreSQL’de veritabanı rolü başına statement_timeout, lock_timeout ve
idle_in_transaction_session_timeout önerir. Rol başına başarılı sorgu süresi ve kilit bekleme yüzdelikleri ile elle iptal edilmiş
(kaçak) sorgular kodla çıkarılır; modele yalnızca bu özet gider, sorgu metinleri gönderilmez. Öneri kodla denetlenir: statement_timeout
başarılı sorguların belirli payından fazlasını kesmemeli ama kaçak sorguları da durdurmalı (gereğinden uzun olmamalı); lock_timeout
statement_timeout’tan kısa olmalı ve normal kilit beklemelerini kesmemeli; boşta işlem süresi makul aralıkta olmalı.
Çıktı: zaman_asimlari.sql (ALTER ROLE … SET), zaman_asimi_plani.csv, zaman-asimi.md."""
import argparse
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table
from analiz import quantile


def validator(roles, max_cut):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('roller') or []:
            k = str(e.get('rol', '')).strip()
            if k not in roles or k in seen:
                problems.append(f'rol “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            try:
                st, lt, it = int(e.get('statement_timeout_ms')), int(e.get('lock_timeout_ms')), int(e.get('idle_in_transaction_ms'))
            except (TypeError, ValueError):
                problems.append(f'{k}: süreler milisaniye cinsinden tam sayı olmalı')
                continue
            v = roles[k]
            cut = sum(x > st for x in v['ok']) / len(v['ok'])
            p999 = quantile(v['ok'], .999)
            if cut > max_cut:
                problems.append(f'{k}: statement_timeout {st} ms ile kesilecek başarılı sorgu payı %{100 * cut:.2f} (en fazla %{100 * max_cut:g}); p99,9 {p999:.0f} ms')
            if st > max(10 * p999, 5000):
                problems.append(f'{k}: statement_timeout {st} ms, p99,9 süre {p999:.0f} ms; 10 katından uzun süre kaçak sorguları durdurmaz')
            for run in v['iptal']:
                if st >= run:
                    problems.append(f'{k}: elle iptal edilen {run:.0f} ms’lik sorgu bu sürede de durmazdı')
                    break
            lw = quantile(v['lock'], .99)
            if not lw <= lt < st:
                problems.append(f'{k}: lock_timeout {lt} ms; p99 kilit beklemesi ({lw:.0f} ms) ile statement_timeout arasında olmalı')
            if not 60000 <= it <= 3600000:
                problems.append(f'{k}: idle_in_transaction 1 dk ile 1 saat arasında olmalı')
            out[k] = {'st': st, 'lt': lt, 'it': it, 'kesilen': cut, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [k for k in roles if k not in seen]
        if missing:
            problems.append('ayarı yazılmayan rol: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir PostgreSQL veritabanı yöneticisisin. Her rol için statement_timeout, lock_timeout ve idle_in_transaction_session_timeout öner '
          '(milisaniye): normal sorguları kesmesin ama kaçak sorguları ve uzun kilit beklemelerini durdursun. Yanıt şeması: {"roller": [{"rol": "...", '
          '"statement_timeout_ms": 5000, "lock_timeout_ms": 1000, "idle_in_transaction_ms": 300000, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sorgu süre geçmişinden rol başına PostgreSQL zaman aşımları öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'sorgu_sure_gecmisi.csv')), help='rol, sure_ms, durum (basarili/iptal), kilit_bekleme_ms (pg_stat_statements / log özeti)')
    p.add_argument('--max-cut', type=float, default=0.001, help='Kesilebilecek en yüksek başarılı sorgu payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('rol', 'sure_ms', 'durum', 'kilit_bekleme_ms'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    roles = defaultdict(lambda: {'ok': [], 'iptal': [], 'lock': []})
    for r in rows:
        v = roles[r['rol'].strip()]
        d, w = float(r['sure_ms'].replace(',', '.')), float(r['kilit_bekleme_ms'].replace(',', '.') or 0)
        (v['ok'] if r['durum'].strip().lower().startswith('basar') else v['iptal']).append(d)
        v['lock'].append(w)
    roles = {k: {**v, 'ok': sorted(v['ok']), 'lock': sorted(v['lock']), 'iptal': sorted(v['iptal'])} for k, v in sorted(roles.items())}
    out = output_dir(args.output)
    user = ('Roller (başarılı sorgu · süre ms p50/p95/p99/p99,9/max · kilit bekleme ms p99/max · elle iptal edilen sorgu süreleri ms):\n'
            + '\n'.join(f'- {k}: {len(v["ok"])} · ' + '/'.join(f'{quantile(v["ok"], q):.0f}' for q in (.5, .95, .99, .999)) + f'/{v["ok"][-1]:.0f} · '
                        f'{quantile(v["lock"], .99):.0f}/{v["lock"][-1]:.0f} · {", ".join(f"{x:.0f}" for x in v["iptal"]) or "yok"}' for k, v in roles.items())
            + f'\n\nKesilebilecek başarılı sorgu payı en fazla %{100 * args.max_cut:g}.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(roles, args.max_cut))
    if result is None:
        return
    plan = [{'rol': k, 'statement_timeout_ms': m['st'], 'lock_timeout_ms': m['lt'], 'idle_in_transaction_ms': m['it'], 'p99_9_ms': round(quantile(roles[k]['ok'], .999)),
             'kesilecek_basarili': sum(x > m['st'] for x in roles[k]['ok'])} for k, m in result.items()]
    tabular.write_csv(out / 'zaman_asimi_plani.csv', list(plan[0]), plan)
    sql = ['-- Rol başına zaman aşımları; yeni oturumlarda geçerli olur. Uygulamadan önce bağlantı havuzlarını yeniden başlatın.']
    for k, m in result.items():
        sql += [f'-- {k}: {m["gerekce"]}' if m['gerekce'] else f'-- {k}', f"ALTER ROLE {k} SET statement_timeout = '{m['st']}ms';",
                f"ALTER ROLE {k} SET lock_timeout = '{m['lt']}ms';", f"ALTER ROLE {k} SET idle_in_transaction_session_timeout = '{m['it']}ms';"]
    write_text(out / 'zaman_asimlari.sql', '\n'.join(sql))
    md = ['# Zaman aşımları', '', f'{len(plan)} rol; önerilen ayarlarla kesilen başarılı sorgu payı en fazla %{100 * args.max_cut:g} ve elle iptal edilen kaçak sorgular duruyor.', '',
          md_table(['Rol', 'statement_timeout', 'lock_timeout', 'idle_in_transaction', 'p99,9 süre', 'Kesilecek başarılı sorgu', 'Gerekçe'],
                   [[p_['rol'], f'{p_["statement_timeout_ms"]} ms', f'{p_["lock_timeout_ms"]} ms', f'{p_["idle_in_transaction_ms"]} ms', f'{p_["p99_9_ms"]} ms',
                     p_['kesilecek_basarili'], result[p_['rol']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca süre yüzdelikleri gitti; kesilecek sorgu payı, kaçak sorgular ve kilit beklemeleri gerçek sürelerle kodla denetlendi._']
    write_text(out / 'zaman-asimi.md', '\n'.join(md))
    print(f'[OK] {len(plan)} rol. Çıktı: {out / "zaman-asimi.md"}')


if __name__ == '__main__':
    llm.run(main)
