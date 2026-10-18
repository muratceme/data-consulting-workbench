"""Etiket kuralı yazarı (AI agent): bulut maliyet dışa aktarımında etiketi (owner, cost_center, env) eksik kaynaklar için, kaynak adı/servis
desenlerinden etiket türeten kurallar yazar (“db-* → owner=mehmet, cost_center=CC100”). Modele kaynak adları, servisler, maliyet ve mevcut
etiketler gider. Kurallar kodla denetlenir ve uygulanır: regex derlenmeli, değer izinli listede (env) ya da mevcut doğru etiketlerde görülmüş
olmalı (owner, cost_center uydurulamaz), mevcut dolu etiketin üzerine yazılmaz, aynı kaynağa iki kural farklı değer veremez ve kurallar
etiketi eksik maliyetin en az --kapsama oranını kapatmalıdır. Çıktı: etiket_kurallari.json, etiketlenmis.csv, etiket-kurallari.md."""
import argparse, re
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, write_json, md_table

NESTED = re.compile(r'\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]')


def validator(rows, rid, cost, tags, allowed, seen, coverage):
    missing_cost = sum(cost(r) for r in rows if any(not r[t].strip() for t in tags))

    def validate(obj):
        rules, problems = [], []
        for i, k in enumerate(obj.get('kurallar') or [], 1):
            if not isinstance(k, dict):
                continue
            rx, values = str(k.get('desen', '')).strip(), k.get('etiketler') or {}
            if NESTED.search(rx):
                problems.append(f'{i}. kural: iç içe niceleyici')
                continue
            try:
                c = re.compile(rx)
            except re.error as exc:
                problems.append(f'{i}. kural: desen derlenmiyor ({exc})')
                continue
            bad = []
            for t, v in values.items():
                if t not in tags:
                    bad.append(f'{t} etiketi yok')
                elif t in allowed and v not in allowed[t]:
                    bad.append(f'{t}={v} izinli değil ({"/".join(allowed[t])})')
                elif t not in allowed and v not in seen[t]:
                    bad.append(f'{t}={v} mevcut etiketlerde hiç görülmemiş (görülenler: {", ".join(sorted(seen[t]))})')
            if bad or not values:
                problems.append(f'{i}. kural ({rx}): ' + ('; '.join(bad) or 'etiket yok'))
                continue
            rules.append({'desen': rx, 'rx': c, 'alan': str(k.get('alan', 'kaynak_id')), 'etiketler': {t: str(v) for t, v in values.items()}, 'gerekce': str(k.get('gerekce', '')).strip()})
        if problems:
            raise ValueError(' | '.join(problems))
        result, conflicts = [], []
        for r in rows:
            new, src = dict(r), {}
            for k in rules:
                if not k['rx'].search(r.get(k['alan'], r[rid])):
                    continue
                for t, v in k['etiketler'].items():
                    if r[t].strip():
                        continue  # never overwrite an existing tag
                    if t in src and new[t] != v:
                        conflicts.append(f'{r[rid]}.{t}: “{src[t]}” → {new[t]}, “{k["desen"]}” → {v}')
                    new[t], src[t] = v, k['desen']
            result.append(new)
        if conflicts:
            raise ValueError('çakışan kurallar: ' + '; '.join(conflicts[:6]))
        fixed = sum(cost(n) for o, n in zip(rows, result) if any(not o[t].strip() for t in tags) and all(n[t].strip() for t in tags))
        rate = fixed / missing_cost if missing_cost else 1
        if rate < coverage:
            left = [f'{n[rid]} ({", ".join(t for t in tags if not n[t].strip())})' for n in result if any(not n[t].strip() for t in tags)]
            raise ValueError(f'etiketi eksik maliyetin %{rate * 100:.0f}’i tamamlandı (gereken %{coverage * 100:.0f}); hâlâ eksik: ' + ', '.join(left[:8]))
        return rules, result, rate
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir FinOps uzmanısın. Etiketi eksik bulut kaynakları için kaynak adı (ya da servis) desenlerine dayalı etiket kuralları yaz. '
          'Değerleri yalnızca izinli listelerden ve mevcut doğru etiketlerden seç; bir ekibi ya da maliyet merkezini uydurma. Kurallar yalnızca boş etiketleri '
          'doldurur; aynı kaynağa iki kural farklı değer vermemeli. Belirsiz kaynakları kuralsız bırakabilirsin. Yanıt şeması: {"kurallar": [{"desen": "^db-", '
          '"alan": "kaynak_id", "etiketler": {"owner": "mehmet", "cost_center": "CC100"}, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Eksik bulut etiketleri için desen kuralları yazan ve maliyet kapsamasıyla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'bulut_kaynaklari.csv')))
    p.add_argument('--resource', default='kaynak_id')
    p.add_argument('--cost', default='maliyet_usd')
    p.add_argument('--prefix', default='tag_')
    p.add_argument('--allowed', default='env=prod|test|dev', help='etiket=değer1|değer2;…')
    p.add_argument('--coverage', type=float, default=0.8)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    tags = [c for c in columns if c.startswith(args.prefix)]
    if args.resource not in columns or args.cost not in columns or not tags:
        raise ValueError(f'{args.resource}, {args.cost} ve {args.prefix}* etiket sütunları gerekli.')
    allowed = {}
    for part in args.allowed.split(';'):
        k, _, v = part.partition('=')
        if k.strip():
            allowed[args.prefix + k.strip()] = [x.strip() for x in v.split('|') if x.strip()]
    seen = defaultdict(set)
    for r in rows:
        for t in tags:
            v = r[t].strip()
            if v and (t not in allowed or v in allowed[t]):
                seen[t].add(v)
    style = tabular.number_style([r[args.cost] for r in rows])
    cost = lambda r: tabular.parse_number(r[args.cost], style) or 0.0
    out = output_dir(args.output)
    user = ('İzinli değerler: ' + '; '.join(f'{t}: {"/".join(v)}' for t, v in allowed.items()) + '\nMevcut doğru değerler: '
            + '; '.join(f'{t}: {", ".join(sorted(seen[t]))}' for t in tags) + '\n\nKaynaklar (id · servis · maliyet · etiketler):\n'
            + '\n'.join(f'- {r[args.resource]} · {r.get("servis", "")} · {cost(r):g} · ' + ', '.join(f'{t}={r[t] or "—"}' for t in tags) for r in rows))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, args.resource, cost, tags, allowed, seen, args.coverage))
    if result is None:
        return
    rules, tagged, rate = result
    tabular.write_csv(out / 'etiketlenmis.csv', columns, tagged)
    write_json(out / 'etiket_kurallari.json', [{k: v for k, v in r.items() if k != 'rx'} for r in rules])
    changed = [(o, n) for o, n in zip(rows, tagged) if o != n]
    md = ['# Etiket kuralları', '', f'{len(rules)} kural {len(changed)} kaynağın boş etiketlerini doldurdu; etiketi eksik maliyetin %{rate * 100:.0f}’i tamamlandı. Mevcut etiketlere dokunulmadı.', '',
          md_table(['Desen', 'Etiketler', 'Gerekçe'], [[r['desen'], ', '.join(f'{k}={v}' for k, v in r['etiketler'].items()), r['gerekce']] for r in rules]), '',
          md_table(['Kaynak', 'Maliyet'] + tags, [[n[args.resource], f'{cost(n):g}'] + [n[t] + (' *' if not o[t].strip() and n[t] else '') for t in tags] for o, n in changed]), '',
          '\\* kuralla dolduruldu.', '', f'_Model: {meta["model"]} · Kurallar kodla uygulandı; izinli değer, çakışma ve maliyet kapsaması denetlendi. Bulut sağlayıcısında etiket yazılmadı._']
    write_text(out / 'etiket-kurallari.md', '\n'.join(md))
    print(f'[OK] {len(rules)} kural, %{rate * 100:.0f} kapsama. Çıktı: {out / "etiket-kurallari.md"}')


if __name__ == '__main__':
    llm.run(main)
