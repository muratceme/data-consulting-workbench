"""dbt test şiddeti (AI agent): dbt testlerinin geçmiş başarısız satır sayılarından test başına severity, warn_if ve error_if eşiklerini
önerir. Her eşiğin geçmişte kaç kez uyarı/hata üreteceği ve işaretli gerçek olayların (incident) kaçının hata olarak yakalanacağı kodla
yeniden oynatılır; modele yalnızca bu özet gider. Öneri kodla denetlenir: gerçek olayların hepsi error_if ile yakalanmalı, olay olmayan
günlerde hata (pipeline durdurma) sınırı aşılmamalı, warn eşiği error eşiğinden büyük olmamalı, eşikler “> n” biçiminde olmalı.
Çıktı: test_ayarlari.yml (dbt config), test_siddeti.csv, dbt-test-siddeti.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table


def threshold(v):
    m = re.fullmatch(r'\s*(>=|>|!=)\s*(\d+)\s*', str(v or ''))
    if not m:
        return None
    op, n = m.group(1), int(m.group(2))
    return (lambda x: x >= n) if op == '>=' else (lambda x: x != n) if op == '!=' else (lambda x: x > n), f'{op}{n}'


def validator(hist, max_false):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('testler') or []:
            t = str(e.get('test', '')).strip()
            if t not in hist or t in seen:
                problems.append(f'test “{t}” yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            sev = str(e.get('severity', 'error')).strip()
            w, er = threshold(e.get('warn_if')), threshold(e.get('error_if'))
            if sev not in ('warn', 'error') or not w or not er:
                problems.append(f'{t}: severity warn/error, warn_if ve error_if "> n" biçiminde olmalı')
                continue
            h = hist[t]
            nw, ne = int(w[1].lstrip('>=!')), int(er[1].lstrip('>=!'))
            if nw > ne:
                problems.append(f'{t}: warn_if ({w[1]}) error_if’ten ({er[1]}) büyük olamaz')
            errs = [d for d, n, inc in h if sev == 'error' and er[0](n)]
            caught = [d for d, n, inc in h if inc and sev == 'error' and er[0](n)]
            incidents = [d for d, _, inc in h if inc]
            false = [d for d in errs if d not in incidents]
            if len(caught) < len(incidents):
                problems.append(f'{t}: {len(incidents)} gerçek olaydan {len(caught)} tanesi hata üretir; kaçan: '
                                + ', '.join(f'{d} ({n} satır)' for d, n, inc in h if inc and d not in caught))
            if len(false) > max_false:
                problems.append(f'{t}: olay olmayan {len(false)} günde pipeline durur (en fazla {max_false}); en yüksek normal değer '
                                f'{max(n for _, n, inc in h if not inc)} satır')
            out[t] = {'severity': sev, 'warn_if': w[1], 'error_if': er[1], 'uyari_gun': sum(w[0](n) for _, n, _ in h), 'hata_gun': len(errs), 'yakalanan': len(caught),
                      'olay': len(incidents), 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [t for t in hist if t not in seen]
        if missing:
            problems.append('ayarı yazılmayan test: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir analitik mühendisisin. Her dbt testi için severity (warn/error), warn_if ve error_if eşiğini (ör. ">10") öner: gerçek olaylar '
          'pipeline’ı durdursun, normal gürültü durdurmasın, küçük sapmalar uyarı versin. Yanıt şeması: {"testler": [{"test": "...", "severity": "error", '
          '"warn_if": ">0", "error_if": ">50", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='dbt test geçmişinden severity ve eşik öneren, eşikleri geçmiş olaylarda yeniden oynatan agent.')
    p.add_argument('--input', default=str(here('ornek', 'dbt_test_gecmisi.csv')), help='test, gun, basarisiz_satir, olay (evet/hayir)')
    p.add_argument('--max-false', type=int, default=1, help='Olay olmayan günlerde izin verilen en fazla hata (durdurma)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    hist = defaultdict(list)
    for r in rows:
        hist[r['test'].strip()].append((r['gun'].strip(), int(float(r['basarisiz_satir'] or 0)), r['olay'].strip().lower() in ('evet', 'e', '1', 'true')))
    out = output_dir(args.output)
    lines = []
    for t, h in hist.items():
        normal = sorted(n for _, n, inc in h if not inc)
        lines.append(f'- {t}: {len(h)} gün · normal günlerde başarısız satır en çok {normal[-1]}, sıfır olmayan gün {sum(n > 0 for n in normal)} · gerçek olaylar: '
                     + (', '.join(f'{d} {n} satır' for d, n, inc in h if inc) or 'yok'))
    user = (f'Test geçmişi (olay = veri ekibinin işaretlediği gerçek sorun). Olay olmayan günlerde en fazla {args.max_false} durdurma kabul edilir.\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(hist, args.max_false))
    if result is None:
        return
    yml = ['# dbt_project.yml ya da test tanımlarına eklenecek ayarlar', 'tests:']
    for t, m in result.items():
        yml += [f'  # {t}: {m["gerekce"]}' if m['gerekce'] else f'  # {t}', f'  - name: {t}', '    config:', f'      severity: {m["severity"]}',
                f'      warn_if: "{m["warn_if"]}"', f'      error_if: "{m["error_if"]}"']
    write_text(out / 'test_ayarlari.yml', '\n'.join(yml))
    plan = [{'test': t, **{k: v for k, v in m.items() if k != 'gerekce'}} for t, m in result.items()]
    tabular.write_csv(out / 'test_siddeti.csv', list(plan[0]), plan)
    md = ['# dbt test şiddeti', '', f'{len(plan)} test; geçmişte {sum(p_["olay"] for p_ in plan)} gerçek olayın {sum(p_["yakalanan"] for p_ in plan)} tanesi hata üretirdi.', '',
          md_table(['Test', 'Severity', 'warn_if', 'error_if', 'Uyarı günü', 'Hata günü', 'Yakalanan olay', 'Gerekçe'],
                   [[p_['test'], p_['severity'], p_['warn_if'], p_['error_if'], p_['uyari_gun'], p_['hata_gun'], f'{p_["yakalanan"]}/{p_["olay"]}', result[p_['test']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Eşikler geçmiş test sonuçlarında kodla yeniden oynatıldı; olay yakalama ve gereksiz durdurma sınırları kodla denetlendi._']
    write_text(out / 'dbt-test-siddeti.md', '\n'.join(md))
    print(f'[OK] {len(plan)} test. Çıktı: {out / "dbt-test-siddeti.md"}')


if __name__ == '__main__':
    llm.run(main)
