"""A/B test karar notu (AI agent): ziyaretçi bazlı deney verisinden dönüşüm farkını, %95 güven aralığını, p-değerini, ziyaretçi başı geliri
ve örneklem oranı uyumsuzluğunu (SRM) kodla hesaplar; numaralı olgulara çevirir. Model bu olgulardan ürün ekibine karar notu yazar.
Karar kodla kısıtlanır: istatistiksel olarak anlamlı olmayan bir farkta “yayına al” denemez, SRM varsa karar “test geçersiz” olmak
zorundadır; metindeki her sayı olgularla eşleştirilir. Satırlar gönderilmez. Çıktı: ab-karar.md, olgular.csv, ab-karar.json."""
import argparse, math
from argparse import Namespace
from statistics import NormalDist
import llm
import tabular
from common import here, output_dir, write_json, write_text
from analiz import ab
from donem_yorumu import allowed_numbers, unknown_numbers, tr

DECISIONS = ('deneyi yayına al', 'kontrolde kal', 'fark yok: testi uzat veya sonlandır', 'test geçersiz')


def build_facts(columns, rows, args):
    res = ab(columns, rows, Namespace(group=args.group, converted=args.converted, revenue=args.revenue, control=args.control))
    s = res.summary
    n_a, n_b = s['kontrol_n'], s['deney_n']
    expected = (n_a + n_b) / 2
    chi = (n_a - expected) ** 2 / expected + (n_b - expected) ** 2 / expected
    srm_p = 2 * (1 - NormalDist().cdf(math.sqrt(chi)))
    facts = []

    def add(text):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text})
    add(f"Kontrol ({s['kontrol']}): {n_a} ziyaretçi, dönüşüm oranı %{tr(100 * s['kontrol_oran'], 2)}")
    add(f"Deney ({s['deney']}): {n_b} ziyaretçi, dönüşüm oranı %{tr(100 * s['deney_oran'], 2)}")
    add(f"Göreli artış (lift) %{tr(s['lift_%'], 2)}; fark için %95 güven aralığı {tr(100 * s['fark_ga_alt'], 2)} ile {tr(100 * s['fark_ga_ust'], 2)} puan arası")
    add(f"İki oran z-testi p-değeri {tr(s['p_degeri'], 4)}; anlamlılık düzeyi {tr(args.alpha, 2)}")
    if 'gelir_kontrol' in s:
        add(f"Ziyaretçi başı gelir: kontrol {tr(s['gelir_kontrol'])}, deney {tr(s['gelir_deney'])}; Welch t-testi p-değeri {tr(s['gelir_p'], 4)}")
    add(f"Örneklem oranı (SRM) kontrolü p-değeri {tr(srm_p, 4)} (beklenen 50/50)")
    significant = s['p_degeri'] < args.alpha
    winner = 'deney' if significant and s['deney_oran'] > s['kontrol_oran'] else 'kontrol' if significant else ''
    return facts, {'significant': significant, 'winner': winner, 'srm': srm_p < 0.001, 'summary': s}


def validator(facts, stat):
    ids = {f['id'] for f in facts}
    values = allowed_numbers(facts, [stat['summary']['kontrol'], stat['summary']['deney']])

    def validate(obj):
        problems = []
        decision = str(obj.get('karar', '')).strip().lower()
        if decision not in DECISIONS:
            problems.append(f'karar şunlardan biri olmalı: {", ".join(DECISIONS)}')
        elif stat['srm'] and decision != 'test geçersiz':
            problems.append('SRM p-değeri 0,001’in altında; karar "test geçersiz" olmalı')
        elif not stat['srm'] and decision == 'deneyi yayına al' and stat['winner'] != 'deney':
            problems.append('fark anlamlı değil veya deney kötü; "deneyi yayına al" denemez')
        elif not stat['srm'] and decision == 'kontrolde kal' and stat['winner'] == 'deney':
            problems.append('deney anlamlı biçimde daha iyi; "kontrolde kal" kararı olgularla çelişiyor')
        reasons = [r for r in obj.get('gerekceler') or [] if isinstance(r, dict) and str(r.get('metin', '')).strip()]
        if not reasons:
            problems.append('"gerekceler" boş')
        for i, r in enumerate(reasons, start=1):
            refs = [str(x).strip().upper() for x in r.get('kanit') or []]
            if not refs or any(x not in ids for x in refs):
                problems.append(f'{i}. gerekçenin kanit listesi geçerli olgu içermeli')
        text = ' '.join([str(obj.get('ozet', ''))] + [str(r['metin']) for r in reasons] + [str(x) for x in obj.get('sonraki_adimlar', [])])
        bad = unknown_numbers(text, values, [stat['summary']['kontrol'], stat['summary']['deney']])
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)))
        if problems:
            raise ValueError('; '.join(problems))
        return {'decision': decision, 'summary': str(obj.get('ozet', '')).strip(), 'reasons': [{'metin': str(r['metin']).strip(), 'kanit': [str(x).upper() for x in r['kanit']]} for r in reasons],
                'next': [str(x) for x in obj.get('sonraki_adimlar', []) if x], 'risks': [str(x) for x in obj.get('riskler', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir deney (A/B test) analistisin. Kodla hesaplanmış, numaralı olgulardan ürün ekibine kısa bir karar notu yaz. '
          f'karar şunlardan biri: {", ".join(DECISIONS)}. p-değeri anlamlılık düzeyinin altında değilse "deneyi yayına al" deme; SRM p-değeri 0,001’in altındaysa '
          'karar "test geçersiz". Güven aralığını ve gelir etkisini yorumla; metinde olgularda olmayan sayı kullanma. '
          'Yanıt şeması: {"karar": "...", "ozet": "...", "gerekceler": [{"metin": "...", "kanit": ["F3"]}], "riskler": ["..."], "sonraki_adimlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='A/B test verisinden kodla kısıtlanmış karar notu yazan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'ab_testi.csv')))
    p.add_argument('--group', default='grup')
    p.add_argument('--converted', default='donusum')
    p.add_argument('--revenue', default='gelir')
    p.add_argument('--control', default='A')
    p.add_argument('--alpha', type=float, default=0.05)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    facts, stat = build_facts(columns, rows, args)
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu'], facts)
    user = 'Olgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, stat))
    if result is None:
        return
    write_json(out / 'ab-karar.json', {**result, 'olgular': facts, 'anlamli': stat['significant'], 'srm': stat['srm']})
    md = ['# A/B test karar notu', '', f"**Karar: {result['decision']}**", '', result['summary'], '', '## Gerekçeler', '']
    md += [f"- {r['metin']} ({', '.join(r['kanit'])})" for r in result['reasons']]
    if result['risks']:
        md += ['', '## Riskler', ''] + [f'- {x}' for x in result['risks']]
    if result['next']:
        md += ['', '## Sonraki adımlar', ''] + [f'- {x}' for x in result['next']]
    md += ['', '## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Karar, anlamlılık ve SRM kurallarıyla kodla sınırlandı. Testin önceden belirlenen süre ve örneklemle yürütüldüğünü doğrulayın._"]
    write_text(out / 'ab-karar.md', '\n'.join(md))
    print(f"[OK] Karar: {result['decision']} (anlamlı: {'evet' if stat['significant'] else 'hayır'}). Çıktı: {out / 'ab-karar.md'}")


if __name__ == '__main__':
    llm.run(main)
