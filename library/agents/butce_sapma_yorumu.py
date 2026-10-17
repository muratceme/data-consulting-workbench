"""Bütçe sapma yorumcusu (AI agent): yıllık bütçeyi aylara dağıtıp gerçekleşenle karşılaştırma işini butce-gerceklesen kod bloğuyla
yerelde yapar; kalem bazında YTD ve son ay sapmalarını numaralı olgulara çevirir. Model, yönetim raporu için kalem başına sapma yorumu,
olası neden ve aksiyon yazar. Olumsuz sapması olan her kalemin ele alındığı, kanıtların geçerli olgu numarası olduğu ve metindeki her
sayının olgularda bulunduğu yerelde denetlenir; olumsuz bir sapmayı “olumlu” diye sunan yorum reddedilir. Satırlar gönderilmez.
Çıktı: butce-yorumu.md, olgular.csv, butce-yorumu.json."""
import argparse
from argparse import Namespace
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from planlama import butce_gercek
from donem_yorumu import allowed_numbers, unknown_numbers, tr


def build_facts(args):
    columns, rows = tabular.read(args.input)
    names = {tuple(r[k].strip() for k in args.keys.split(',')): r.get('hesap_adi', '').strip() for r in rows}
    res = butce_gercek(columns, rows, Namespace(actuals=args.actuals, keys=args.keys, budget_col=args.budget_col, actual_col=args.actual_col, month_col=args.month_col,
                                                weights=args.weights, threshold=args.threshold, expense_prefixes=args.expense_prefixes))
    keys = args.keys.split(',')
    monthly = defaultdict(list)
    for m in res.files['aylik.csv'][1]:
        monthly[tuple(m[k] for k in keys)].append(m)
    facts, items, labels = [], [], set()

    def add(text):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text})
    for o in res.rows:
        key = tuple(o[k] for k in keys)
        label = ' / '.join(key) + (f" ({names.get(key)})" if names.get(key) else '')
        labels.update(key)
        labels.add(names.get(key, ''))
        add(f"{label}: {o['tur']}; YTD bütçe {tr(o['ytd_butce'])}, gerçekleşen {tr(o['ytd_gerceklesen'])}, sapma {tr(o['sapma'])} (%{tr(o['sapma_%'], 1)}); durum: {o['durum']}")
        items.append({'kalem': ' / '.join(key), 'olgu': facts[-1]['id'], 'durum': o['durum'], 'tur': o['tur'], 'sapma_%': o['sapma_%']})
        last = monthly[key][-1]
        if last['sapma_%'] != '':
            add(f"{label}: son ay ({last['ay']}. ay) bütçe {tr(last['butce'])}, gerçekleşen {tr(last['gerceklesen'])}, sapma %{tr(last['sapma_%'], 1)}")
    return facts, items, res.summary['son_ay'], sorted((l for l in labels if l), key=len, reverse=True)


def validator(facts, items, labels):
    ids = {f['id'] for f in facts}
    by_item = {i['kalem']: i for i in items}
    must = {i['kalem'] for i in items if i['durum'] == 'olumsuz'}
    values = allowed_numbers(facts, labels)

    def validate(obj):
        problems, out, seen = [], [], set()
        for c in obj.get('kalemler') or []:
            if not isinstance(c, dict):
                continue
            k = str(c.get('kalem', '')).strip()
            if k not in by_item:
                problems.append(f'olmayan kalem: {k or "?"} (kalem adını olgudaki gibi yaz: {", ".join(by_item)})')
                continue
            seen.add(k)
            refs = [str(x).strip().upper() for x in c.get('kanit') or []]
            if not refs or any(r not in ids for r in refs):
                problems.append(f'{k}: kanit geçerli olgu numaraları içermeli')
            tone = str(c.get('degerlendirme', '')).strip().lower()
            if tone not in ('olumlu', 'olumsuz', 'nötr'):
                problems.append(f'{k}: degerlendirme olumlu, olumsuz veya nötr olmalı')
            elif by_item[k]['durum'] == 'olumsuz' and tone == 'olumlu':
                problems.append(f'{k}: kod bu sapmayı olumsuz buldu; olumlu sunulamaz')
            out.append({'kalem': k, 'degerlendirme': tone, 'yorum': str(c.get('yorum', '')).strip(), 'olasi_neden': str(c.get('olasi_neden', '')).strip(),
                        'aksiyon': str(c.get('aksiyon', '')).strip(), 'kanit': ', '.join(refs)})
        missing = sorted(must - seen)
        if missing:
            problems.append('olumsuz sapması olan ama yorumlanmayan kalem: ' + ', '.join(missing))
        summary = str(obj.get('ozet', '')).strip()
        bad = unknown_numbers(' '.join([summary] + [f"{o['yorum']} {o['olasi_neden']} {o['aksiyon']}" for o in out]), values, labels)
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)))
        if not summary:
            problems.append('"ozet" boş')
        if problems:
            raise ValueError('; '.join(problems[:12]))
        return {'summary': summary, 'rows': out}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir FP&A (finansal planlama ve analiz) uzmanısın. Kodla hesaplanmış, numaralı bütçe-gerçekleşen olgularından yönetim için kalem bazında '
          'sapma yorumu yaz. Kodun olumsuz bulduğu her kalemi mutlaka yorumla; gider aşımı ve gelir eksiği olumsuzdur. Nedeni olgularda yoksa “olası” de ve aksiyon olarak '
          'bütçe sahibine sorulacak soruyu yaz. Metinde olgularda olmayan sayı kullanma. degerlendirme: olumlu, olumsuz veya nötr. '
          'Yanıt şeması: {"ozet": "...", "kalemler": [{"kalem": "770 / Merkez", "degerlendirme": "olumsuz", "yorum": "...", "olasi_neden": "...", "aksiyon": "...", "kanit": ["F1"]}]}')


def main():
    p = argparse.ArgumentParser(description='Bütçe-gerçekleşen sapmalarını olgularla yorumlayan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'yillik_butce.csv')), help='Yıllık bütçe')
    p.add_argument('--actuals', default=str(here('ornek', 'gerceklesen.csv')), help='Aylık gerçekleşen')
    p.add_argument('--weights', default=str(here('ornek', 'mevsim_agirliklari.csv')), help='Aylık ağırlıklar ("" eşit dağıtır)')
    p.add_argument('--keys', default='hesap,departman')
    p.add_argument('--budget-col', default='yillik_butce')
    p.add_argument('--actual-col', default='gerceklesen')
    p.add_argument('--month-col', default='ay')
    p.add_argument('--threshold', type=float, default=10.0)
    p.add_argument('--expense-prefixes', default='6,7')
    llm.add_arguments(p)
    args = p.parse_args()
    facts, items, last, labels = build_facts(args)
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu'], facts)
    user = f'{last}. ay sonu itibarıyla olgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, items, labels))
    if result is None:
        return
    write_json(out / 'butce-yorumu.json', {**result, 'olgular': facts})
    md = [f'# Bütçe sapma yorumu — {last}. ay sonu', '', result['summary'], '',
          md_table(['Kalem', 'Değerlendirme', 'Yorum', 'Olası neden', 'Aksiyon', 'Kanıt'], [[r['kalem'], r['degerlendirme'], r['yorum'], r['olasi_neden'], r['aksiyon'], r['kanit']] for r in result['rows']]),
          '', '## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Rakamlar butce-gerceklesen bloğuyla hesaplandı ve metinle eşleştirildi; nedenler bütçe sahipleriyle doğrulanmalı._"]
    write_text(out / 'butce-yorumu.md', '\n'.join(md))
    print(f"[OK] {len(result['rows'])} kalem yorumlandı ({sum(i['durum'] == 'olumsuz' for i in items)} olumsuz sapma). Çıktı: {out / 'butce-yorumu.md'}")


if __name__ == '__main__':
    llm.run(main)
