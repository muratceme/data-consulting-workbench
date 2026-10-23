"""Altın kayıt kuralı (AI agent): aynı müşteriye ait kayıt kümelerinden (CRM, ERP, web) tek altın kayıt üretirken her alanın hangi kurala
göre seçileceğini (en güncel, kaynak önceliği, en sık, en uzun) belirler. Veri sorumlusunun onayladığı örnek kümeler üzerinde her kuralın
ve her kaynağın alan bazında doğruluğu kodla hesaplanır; modele yalnızca bu doğruluk tablosu gider, ad, telefon, e-posta ve adres
değerleri gönderilmez. Seçim kodla denetlenir: kural ve kaynaklar tanımlı olmalı, her alanda seçilen kuralın doğruluğu taranan en iyi
kuralın belirli payı içinde ve alt sınırın üstünde olmalı. golden-record bloğunun sabit kuralı yerine alan bazında kural uygular.
Çıktı: altin_kayitlar.csv, kurallar.yaml, alan_dogrulugu.csv, altin-kayit-kurallari.md."""
import argparse
from collections import Counter, defaultdict
from itertools import permutations
import llm
import tabular
from common import here, output_dir, write_text, md_table

RULES = ('en_guncel', 'kaynak_onceligi', 'en_sik', 'en_uzun')


def pick(recs, field, rule, order=()):
    vals = [r for r in recs if r[field].strip()]
    if not vals:
        return ''
    if rule == 'en_guncel':
        return max(vals, key=lambda r: r['guncelleme'])[field].strip()
    if rule == 'kaynak_onceligi':
        rank = {s: i for i, s in enumerate(order)}
        return min(vals, key=lambda r: (rank.get(r['kaynak'], len(rank)), r['guncelleme']))[field].strip()
    if rule == 'en_sik':
        c = Counter(r[field].strip() for r in vals)
        top = max(c.values())
        return max((r for r in vals if c[r[field].strip()] == top), key=lambda r: r['guncelleme'])[field].strip()
    return max(vals, key=lambda r: (len(r[field].strip()), r['guncelleme']))[field].strip()


def accuracy(clusters, gold, field, rule, order=()):
    keys = [k for k in gold if field in gold[k]]
    return sum(pick(clusters[k], field, rule, order) == gold[k][field] for k in keys) / len(keys) if keys else 0.0


def best_of(clusters, gold, field, sources):
    opts = [(accuracy(clusters, gold, field, r), r, ()) for r in RULES if r != 'kaynak_onceligi']
    opts += [(accuracy(clusters, gold, field, 'kaynak_onceligi', o), 'kaynak_onceligi', o) for o in permutations(sources)]
    return max(opts, key=lambda x: x[0])


def validator(clusters, gold, fields, sources, tol, min_acc):
    best = {f: best_of(clusters, gold, f, sources) for f in fields}

    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('alanlar') or []:
            f = str(e.get('alan', '')).strip()
            if f not in fields or f in seen:
                problems.append(f'alan “{f}” yok ya da iki kez yazıldı')
                continue
            seen.add(f)
            rule, order = str(e.get('kural', '')).strip(), tuple(str(s).strip() for s in e.get('kaynak_sirasi') or [])
            if rule not in RULES:
                problems.append(f'{f}: kural {", ".join(RULES)} olmalı')
                continue
            if rule == 'kaynak_onceligi' and (not order or set(order) - set(sources)):
                problems.append(f'{f}: kaynak_sirasi şu kaynaklardan oluşmalı: {", ".join(sources)}')
                continue
            acc = accuracy(clusters, gold, f, rule, order)
            b = best[f]
            if acc < b[0] - tol or acc < min_acc:
                problems.append(f'{f}: {rule}{" " + ">".join(order) if order else ""} onaylı kümelerde %{100 * acc:.0f} doğru; en iyi kural '
                                f'{b[1]}{" " + ">".join(b[2]) if b[2] else ""} %{100 * b[0]:.0f} (en az %{100 * min_acc:g})')
            out[f] = (rule, order, acc, str(e.get('gerekce', '')).strip())
        missing = [f for f in fields if f not in seen]
        if missing:
            problems.append('kuralı yazılmayan alan: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir ana veri yöneticisisin. Her alan için altın kayıtta hangi değerin kalacağını belirleyen kuralı seç: en_guncel, kaynak_onceligi '
          '(kaynak_sirasi ile), en_sik ya da en_uzun. Doğruluk tablosu veri sorumlusunun onayladığı kümelerden hesaplandı. Yanıt şeması: '
          '{"alanlar": [{"alan": "telefon", "kural": "kaynak_onceligi", "kaynak_sirasi": ["crm", "erp", "web"], "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Altın kayıtta alan bazında hayatta kalma kuralı seçen ve onaylı örneklerle kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'musteri_kume_kayitlari.csv')), help='kume, kaynak, guncelleme + alanlar')
    p.add_argument('--approved', default=str(here('ornek', 'altin_kayit_onayli.csv')), help='kume, alan, onayli_deger (veri sorumlusu onayı)')
    p.add_argument('--fields', default='ad,telefon,eposta,adres')
    p.add_argument('--tolerance', type=float, default=0.02)
    p.add_argument('--min-accuracy', type=float, default=0.7)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    fields = [f.strip() for f in args.fields.split(',') if f.strip() in columns]
    for c in ('kume', 'kaynak', 'guncelleme'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    clusters = defaultdict(list)
    for r in rows:
        clusters[r['kume'].strip()].append({**r, 'kaynak': r['kaynak'].strip(), 'guncelleme': r['guncelleme'].strip()})
    _, grows = tabular.read(args.approved)
    gold = defaultdict(dict)
    for r in grows:
        if r['kume'].strip() in clusters:
            gold[r['kume'].strip()][r['alan'].strip()] = r['onayli_deger'].strip()
    sources = sorted({r['kaynak'] for v in clusters.values() for r in v})
    lines = []
    for f in fields:
        keys = [k for k in gold if f in gold[k]]
        src = []
        for s in sources:
            have = [k for k in keys if any(r['kaynak'] == s and r[f].strip() for r in clusters[k])]
            right = sum(any(r['kaynak'] == s and r[f].strip() == gold[k][f] for r in clusters[k]) for k in have)
            src.append(f'{s} dolu %{100 * len(have) / len(keys):.0f}, doğru %{100 * right / len(have):.0f}' if have else f'{s} hiç dolu değil')
        lines.append(f'- {f}: ' + ', '.join(f'{r} %{100 * accuracy(clusters, gold, f, r):.0f}' for r in RULES if r != 'kaynak_onceligi') + ' · kaynaklar: ' + '; '.join(src))
    out = output_dir(args.output)
    user = (f'{len(clusters)} küme, {len(rows)} kayıt, kaynaklar {", ".join(sources)}; {len(gold)} küme veri sorumlusunca onaylandı.\n'
            'Alan bazında kuralların onaylı kümelerdeki doğruluğu ve kaynakların dolu/doğru oranı:\n' + '\n'.join(lines)
            + f'\n\nSeçilen kuralın doğruluğu en az %{100 * args.min_accuracy:g} olmalı.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(clusters, gold, fields, sources, args.tolerance, args.min_accuracy))
    if result is None:
        return
    golden = [{'kume': k, **{f: pick(v, f, result[f][0], result[f][1]) for f in fields}} for k, v in sorted(clusters.items())]
    tabular.write_csv(out / 'altin_kayitlar.csv', ['kume'] + fields, golden)
    tabular.write_csv(out / 'alan_dogrulugu.csv', ['alan', 'kural', 'kaynak_sirasi', 'dogruluk'],
                      [{'alan': f, 'kural': r, 'kaynak_sirasi': '>'.join(o), 'dogruluk': round(a, 3)} for f, (r, o, a, _) in result.items()])
    yml = ['# Altın kayıt hayatta kalma kuralları', 'alanlar:'] + [f'  {f}: {{kural: {r}' + (f', kaynak_sirasi: [{", ".join(o)}]' if o else '') + '}' for f, (r, o, _, _) in result.items()]
    write_text(out / 'kurallar.yaml', '\n'.join(yml))
    md = ['# Altın kayıt kuralları', '', f'{len(clusters)} kümeden {len(golden)} altın kayıt üretildi; kurallar {len(gold)} onaylı kümede sınandı.', '',
          md_table(['Alan', 'Kural', 'Kaynak sırası', 'Onaylı kümelerde doğruluk', 'Gerekçe'],
                   [[f, r, ' > '.join(o) or '—', f'%{100 * a:.0f}', why] for f, (r, o, a, why) in result.items()]), '',
          f'_Model: {meta["model"]} · Modele yalnızca doğruluk tablosu gitti; kişisel veri değerleri gönderilmedi. Kuralların doğruluğu onaylı örneklerle kodla hesaplandı._']
    write_text(out / 'altin-kayit-kurallari.md', '\n'.join(md))
    print(f'[OK] {len(golden)} altın kayıt. Çıktı: {out / "altin-kayit-kurallari.md"}')


if __name__ == '__main__':
    llm.run(main)
