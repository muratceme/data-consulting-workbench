"""Veri kalitesi kök neden analisti (AI agent): kalite kurallarını (rules.json) dosyada yerelde çalıştırır; hata veren her kural için hatalı
satırların hangi kategorik değerlerde (kaynak sistem, kanal, bölge, tarih ayı…) yoğunlaştığını kodla ölçer ve numaralı olgulara çevirir.
Model yalnızca bu olgulara dayanarak kural başına olası kök neden, sorumlu taraf ve aksiyon yazar. Hata veren her kuralın tam bir kez ele
alındığı, kanıt olgularının geçerli olduğu ve metindeki her sayının olgularda bulunduğu yerelde denetlenir. Satırlar gönderilmez.
Çıktı: kok-neden.md, kok-neden.csv, olgular.csv."""
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from kalite_kontrol import run
from donem_yorumu import allowed_numbers, unknown_numbers, tr

OWNERS = ('kaynak sistem', 'entegrasyon/ETL', 'iş birimi', 'kural tanımı', 'bilinmiyor')


def build_facts(columns, rows, spec, max_card, top):
    results, violations = run(columns, rows, spec)
    failed_rows = defaultdict(set)
    for v in violations:
        if v['satir'] != '':
            failed_rows[v['kural']].add(v['satir'])
    numeric = {c for c in columns if sum(tabular.parse_number(r[c]) is not None for r in rows if r[c].strip()) >= 0.8 * max(1, sum(bool(r[c].strip()) for r in rows))}
    cats = [c for c in columns if c not in numeric and 1 < len({r[c].strip() for r in rows}) <= max_card]
    month_col = next((c for c in columns if sum(bool(tabular.parse_date(r[c])) for r in rows[:200]) >= 0.8 * min(len(rows), 200)), None)
    facts, rules, labels = [], [], set()

    def add(text, rule):
        facts.append({'id': f'F{len(facts) + 1}', 'kural': rule, 'olgu': text})
    for res in results:
        if res['passed']:
            continue
        rid = res['id']
        rules.append({'kural': rid, 'tanim': res['rule'], 'hata': res['failed'], 'seviye': res['severity']})
        labels.add(res['rule'])
        add(f"Kural {rid} ({res['rule']}): {res['failed']} satır hatalı, tüm satırların %{tr(100 * res['failed'] / max(len(rows), 1), 1)}", rid)
        bad = failed_rows.get(rid, set())
        if not bad or res['check'] in ('unique', 'unique_together', 'row_count_min'):  # duplicates share their values by definition
            continue
        dims = list(cats) + ([month_col] if month_col and month_col not in cats else [])
        found = []
        for c in dims:
            key = (lambda r, c=c: (tabular.parse_date(r[c])[0].strftime('%Y-%m') if tabular.parse_date(r[c]) else '(boş)')) if c == month_col else (lambda r, c=c: r[c].strip() or '(boş)')
            allc, badc = Counter(key(r) for r in rows), Counter(key(rows[n - 2]) for n in bad)
            for val, n in badc.items():
                share_bad, share_all = n / len(bad), allc[val] / len(rows)
                if n >= 3 and share_bad >= 0.5 and share_bad / share_all >= 1.5:
                    found.append((share_bad / share_all, c, val, n, share_bad, share_all))
        for lift, c, val, n, sb, sa in sorted(found, reverse=True)[:top]:
            labels.update({c, val})
            add(f"Kural {rid} hatalarının %{tr(sb * 100, 1)}’i ({n} satır) {c} = {val}; bu değer tüm satırların %{tr(sa * 100, 1)}’i", rid)
    return facts, rules, sorted(labels, key=len, reverse=True)


def validator(facts, rules, labels):
    ids = {f['id'] for f in facts}
    expected = {r['kural'] for r in rules}
    values = allowed_numbers(facts, labels)

    def validate(obj):
        items, problems, out, seen = obj.get('kurallar'), [], [], set()
        if not isinstance(items, list) or not items:
            raise ValueError('"kurallar" listesi boş.')
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                rid = int(str(it.get('kural', '')).strip().lstrip('Kk').strip())
            except ValueError:
                rid = None
            if rid not in expected:
                problems.append(f'hata vermeyen veya olmayan kural: {it.get("kural")}')
                continue
            if rid in seen:
                problems.append(f'kural {rid} iki kez ele alındı')
            seen.add(rid)
            refs = [str(x).strip().upper() for x in it.get('kanit') or []]
            if not refs or any(r not in ids for r in refs):
                problems.append(f'kural {rid}: kanit geçerli olgu numaraları içermeli')
            owner = str(it.get('sahip', '')).strip()
            if owner not in OWNERS:
                problems.append(f'kural {rid}: sahip şunlardan biri olmalı: {", ".join(OWNERS)}')
            out.append({'kural': rid, 'olasi_neden': str(it.get('olasi_neden', '')).strip(), 'sahip': owner, 'aksiyon': str(it.get('aksiyon', '')).strip(),
                        'kanit': ', '.join(refs), 'guven': str(it.get('guven', '')).strip()})
        missing = sorted(expected - seen)
        if missing:
            problems.append('ele alınmayan kural: ' + ', '.join(map(str, missing)))
        summary = str(obj.get('ozet', '')).strip()
        bad = unknown_numbers(' '.join([summary] + [o['olasi_neden'] + ' ' + o['aksiyon'] for o in out]), values | expected, labels)
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)))
        if problems:
            raise ValueError('; '.join(problems[:12]))
        return {'summary': summary, 'rows': sorted(out, key=lambda o: o['kural'])}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri kalitesi analistisin. Kodla hesaplanmış, numaralı olgulara (hangi kuralın kaç satırda bozulduğu ve hataların hangi değerlerde '
          'yoğunlaştığı) dayanarak her hatalı kural için olası kök nedeni, sorumlu tarafı ve aksiyonu yaz. Yoğunlaşma yoksa nedeni "belirsiz" de ve verinin sahibine '
          f'sorulacak soruyu aksiyon olarak yaz. Metinde olgularda olmayan sayı kullanma. sahip: {", ".join(OWNERS)}. guven: düşük, orta, yüksek. '
          'Yanıt şeması: {"ozet": "...", "kurallar": [{"kural": 3, "olasi_neden": "...", "kanit": ["F4"], "sahip": "kaynak sistem", "aksiyon": "...", "guven": "orta"}]}')


def main():
    p = argparse.ArgumentParser(description='Kalite kuralı hatalarının yoğunlaştığı yerleri kodla bulup kök neden yazan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'siparis_kalite.csv')))
    p.add_argument('--rules', default=str(here('ornek', 'kalite_kurallari.json')))
    p.add_argument('--max-cardinality', type=int, default=20, help='Kırılım olarak denenecek sütunların en fazla farklı değer sayısı')
    p.add_argument('--top', type=int, default=3, help='Kural başına en fazla kaç yoğunlaşma olgusu')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    spec = json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
    facts, rules, labels = build_facts(columns, rows, spec, args.max_cardinality, args.top)
    out = output_dir(args.output)
    if not rules:
        print('[OK] Tüm kurallar geçti; model çağrılmadı.')
        return
    tabular.write_csv(out / 'olgular.csv', ['id', 'kural', 'olgu'], facts)
    user = 'Olgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, rules, labels))
    if result is None:
        return
    by_rule = {r['kural']: r for r in rules}
    table = [{**r, 'tanim': by_rule[r['kural']]['tanim'], 'hata': by_rule[r['kural']]['hata']} for r in result['rows']]
    tabular.write_csv(out / 'kok-neden.csv', ['kural', 'tanim', 'hata', 'olasi_neden', 'sahip', 'aksiyon', 'kanit', 'guven'], table)
    write_json(out / 'kok-neden.json', {**result, 'olgular': facts})
    md = ['# Veri kalitesi kök neden analizi', '', result['summary'], '',
          md_table(['Kural', 'Hata', 'Olası neden', 'Sahip', 'Aksiyon', 'Kanıt'], [[f"{r['kural']}: {r['tanim']}", r['hata'], r['olasi_neden'], r['sahip'], r['aksiyon'], r['kanit']] for r in table]),
          '', '## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Nedenler hipotezdir; yoğunlaşma olgusu nedensellik kanıtı değildir, kaynak sistem sahibiyle doğrulayın._"]
    write_text(out / 'kok-neden.md', '\n'.join(md))
    print(f"[OK] {len(table)} hatalı kural için kök neden yazıldı. Çıktı: {out / 'kok-neden.md'}")


if __name__ == '__main__':
    llm.run(main)
