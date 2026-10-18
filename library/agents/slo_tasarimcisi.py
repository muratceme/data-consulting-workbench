"""SLO tasarımcısı (AI agent): gece çalışan veri işleri için “tazelik SLO’su” yazar: “satis_dwh işi her gün 06:30’a kadar başarıyla
bitsin, 28 günlük pencerede %95”. Girdi işlerin çalışma geçmişi (tarih, bitiş saati, durum) ve her işin çıktısını kullananların ihtiyaç
saatidir. Model her iş için eşik saati, hedef oranı ve pencereyi önerir; kodla denetlenir: eşik tüketicinin ihtiyaç saatinden geç olamaz,
hedef %90–%99,9 arasında olmalı, geçmişte bu eşikle gerçekleşen oran kodla hesaplanır ve model “risk” işaretini buna göre doğru koymalı
(gerçekleşen < hedef ise risk: true). Hata bütçesi (pencerede izin verilen ihlal günü) ve son pencerede harcanan bütçe kodla hesaplanır.
Çıktı: slo.yaml (OpenSLO benzeri), slo_durumu.csv, slo-tasarimi.md."""
import argparse, math
from collections import defaultdict
from datetime import datetime
import llm
import tabular
from common import here, output_dir, write_text, md_table


def hm(t):
    return datetime.strptime(t.strip()[:5], '%H:%M').time()


def attainment(runs, threshold, window):
    last = sorted(runs)[-window:]
    ok = sum(1 for d in last if runs[d][1] and runs[d][0] is not None and runs[d][0] <= threshold)
    return ok, len(last)


def validator(jobs, needs):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('slolar') or []:
            if not isinstance(e, dict):
                continue
            j = str(e.get('is', '')).strip()
            seen.add(j)
            if j not in jobs or j in out:
                problems.append(f'iş “{j}” yok ya da iki kez yazıldı')
                continue
            try:
                th, target, window = hm(str(e.get('bitis_esigi'))), float(e.get('hedef')), int(e.get('pencere_gun'))
            except (TypeError, ValueError):
                problems.append(f'{j}: bitis_esigi SS:DD, hedef (0–1) ve pencere_gun gerekli')
                continue
            why = []
            if j in needs and th > needs[j][0]:
                why.append(f'eşik {th:%H:%M}, {needs[j][1]} ihtiyacı {needs[j][0]:%H:%M}’dan geç')
            if not 0.9 <= target <= 0.999:
                why.append('hedef 0,90–0,999 arasında olmalı')
            if not 7 <= window <= len(jobs[j]):
                why.append(f'pencere 7–{len(jobs[j])} gün olmalı (geçmiş {len(jobs[j])} gün)')
            if why:
                problems.append(f'{j}: ' + '; '.join(why))
                continue
            ok, n = attainment(jobs[j], th, window)
            risk = ok / n < target
            if bool(e.get('risk')) != risk:
                problems.append(f'{j}: son {n} günde {th:%H:%M} eşiğiyle gerçekleşen %{ok / n * 100:.1f}, hedef %{target * 100:g}; risk {"true" if risk else "false"} olmalı')
                continue
            out[j] = {'esik': th, 'hedef': target, 'pencere': window, 'ok': ok, 'n': n, 'risk': risk, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [j for j in jobs if j not in seen]
        if missing:
            problems.append('SLO yazılmayan iş: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformunda güvenilirlik (SRE) mühendisisin. Her iş için tazelik SLO’su öner: bitis_esigi (SS:DD; tüketicinin ihtiyaç '
          'saatinden geç olamaz, arada pay bırak), hedef (0,90–0,999), pencere_gun. Geçmiş bitiş saatlerine bak; bugün karşılanamayan bir hedef koyuyorsan '
          'risk: true yaz ve gerekçede ne yapılması gerektiğini söyle. Yanıt şeması: {"slolar": [{"is": "...", "bitis_esigi": "06:30", "hedef": 0.95, '
          '"pencere_gun": 28, "risk": false, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='İş çalışma geçmişinden tazelik SLO’su öneren ve gerçekleşen oranla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'gece_is_gecmisi.csv')), help='is, tarih, bitis (SS:DD), durum')
    p.add_argument('--needs', default=str(here('ornek', 'is_ihtiyac_saatleri.csv')), help='is, ihtiyac_saati, tuketici')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('is', 'tarih', 'bitis', 'durum'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    jobs = defaultdict(dict)
    for r in rows:
        ok = r['durum'].strip().lower() in ('basarili', 'başarılı', 'success', 'ok')
        jobs[r['is'].strip()][r['tarih'].strip()] = (hm(r['bitis']) if r['bitis'].strip() else None, ok)
    nc, nrows = tabular.read(args.needs)
    needs = {r['is'].strip(): (hm(r['ihtiyac_saati']), r.get('tuketici', '').strip() or 'tüketici') for r in nrows}
    out = output_dir(args.output)
    lines = []
    for j, runs in jobs.items():
        times = sorted(t for t, ok in runs.values() if ok and t)
        pick = lambda q: times[min(len(times) - 1, int(q * len(times)))].strftime('%H:%M') if times else '?'
        fails = sum(1 for _, ok in runs.values() if not ok)
        lines.append(f'- {j}: {len(runs)} gün; başarısız {fails}; bitiş medyan {pick(0.5)}, p90 {pick(0.9)}, p95 {pick(0.95)}, en geç {pick(0.999)}'
                     + (f'; ihtiyaç: {needs[j][1]} {needs[j][0]:%H:%M}' if j in needs else '; tanımlı tüketici ihtiyacı yok'))
    user = 'İşler (başarılı çalışmaların bitiş saati dağılımı):\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(jobs, needs))
    if result is None:
        return
    rows_out, yml = [], ['# Tazelik SLO’ları (OpenSLO benzeri); değerlendirme: iş eşik saatine kadar başarıyla bitti mi']
    for j, s in result.items():
        budget = math.floor((1 - s['hedef']) * s['pencere'] + 1e-9)
        spent = s['n'] - s['ok']
        rows_out.append({'is': j, 'esik': f'{s["esik"]:%H:%M}', 'hedef_%': round(s['hedef'] * 100, 1), 'pencere_gun': s['pencere'], 'gerceklesen_%': round(s['ok'] / s['n'] * 100, 1),
                         'hata_butcesi_gun': budget, 'harcanan_gun': spent, 'risk': 'evet' if s['risk'] else 'hayır'})
        yml += [f'- apiVersion: openslo/v1', '  kind: SLO', f'  metadata: {{name: {j}-tazelik}}', '  spec:', f'    description: "{j} her gün {s["esik"]:%H:%M}’a kadar başarıyla bitsin"',
                f'    budgetingMethod: Occurrences', f'    timeWindow: [{{duration: {s["pencere"]}d, isRolling: true}}]', f'    objectives: [{{target: {s["hedef"]}}}]']
    tabular.write_csv(out / 'slo_durumu.csv', list(rows_out[0]), rows_out)
    write_text(out / 'slo.yaml', '\n'.join(yml) + '\n')
    md = ['# Tazelik SLO’ları', '', f'{len(rows_out)} iş; {sum(r["risk"] == "evet" for r in rows_out)} tanesinde hedef bugünkü performansla karşılanmıyor.', '',
          md_table(['İş', 'Eşik', 'Hedef %', 'Pencere', 'Gerçekleşen %', 'Bütçe (gün)', 'Harcanan', 'Risk'], [list(r.values()) for r in rows_out]), '']
    md += [f'- **{j}**: {s["gerekce"]}' for j, s in result.items() if s['gerekce']]
    md += ['', f'_Model: {meta["model"]} · Gerçekleşen oran ve hata bütçesi geçmiş çalışmalardan kodla hesaplandı; eşikler tüketici ihtiyaç saatleriyle denetlendi._']
    write_text(out / 'slo-tasarimi.md', '\n'.join(md))
    print(f'[OK] {len(rows_out)} SLO. Çıktı: {out / "slo-tasarimi.md"}')


if __name__ == '__main__':
    llm.run(main)
