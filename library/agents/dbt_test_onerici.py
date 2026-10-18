"""dbt test önerici (AI agent): bir dbt modelinin çıktısından (CSV/XLSX dışa aktarımı) kolon başına dbt veri testleri önerir: unique,
not_null, accepted_values, relationships (başka bir modelin dışa aktarımına) ve dbt_utils.accepted_range. Modele kolon profili gider
(tip, boş/tekil sayısı, min/max, az değerli kolonlarda değer listesi; kişisel veri değerleri gönderilmez). Önerilen her test bugünkü
veride kodla çalıştırılır ve geçmelidir; geçmeyen test başarısız satır sayısı ve örnekleriyle geri gönderilir. Tekil ve dolu olan aday
anahtar kolon için unique + not_null zorunludur; accepted_values en fazla 25 değer içerebilir.
Çıktı: schema.yml (dbt 1.8+ data_tests), test-onerileri.md."""
import argparse
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table, pii_kind

KINDS = ('unique', 'not_null', 'accepted_values', 'relationships', 'accepted_range')


def run(test, col, rows, refs):
    vals = [r[col] for r in rows]
    t = test['test']
    if t == 'not_null':
        bad = [v for v in vals if not v.strip()]
    elif t == 'unique':
        seen, bad = set(), []
        for v in vals:
            if v.strip() and v in seen:
                bad.append(v)
            seen.add(v)
    elif t == 'accepted_values':
        allowed = set(test['values'])
        bad = [v for v in vals if v.strip() and v not in allowed]
    elif t == 'relationships':
        parent = refs[test['to']]
        keys = {r[test['field']] for r in parent['rows']}
        bad = [v for v in vals if v.strip() and v not in keys]
    else:
        style = tabular.number_style([v for v in vals if v.strip()])
        nums = [(v, tabular.parse_number(v, style)) for v in vals if v.strip()]
        lo, hi = test.get('min_value'), test.get('max_value')
        bad = [v for v, n in nums if n is None or (lo is not None and n < lo) or (hi is not None and n > hi)]
    return bad


def validator(columns, rows, refs, keys):
    def validate(obj):
        tests, problems = [], []
        for col, items in (obj.get('kolonlar') or {}).items():
            if col not in columns:
                problems.append(f'“{col}” kolonu yok')
                continue
            for it in items or []:
                it = it if isinstance(it, dict) else {'test': str(it)}
                t = str(it.get('test', '')).strip()
                if t not in KINDS:
                    problems.append(f'{col}: test {", ".join(KINDS)} olmalı')
                    continue
                spec = {'test': t}
                if t == 'accepted_values':
                    vals = [str(v) for v in it.get('values') or []]
                    if not vals or len(vals) > 25:
                        problems.append(f'{col}: accepted_values 1–25 değer içermeli')
                        continue
                    spec['values'] = vals
                elif t == 'relationships':
                    to, field = str(it.get('to', '')).strip(), str(it.get('field', '')).strip()
                    if to not in refs or field not in refs[to]['columns']:
                        problems.append(f'{col}: relationships hedefi {to}.{field} yok (verilen modeller: {", ".join(refs) or "yok"})')
                        continue
                    spec.update(to=to, field=field)
                elif t == 'accepted_range':
                    try:
                        spec['min_value'] = None if it.get('min_value') is None else float(it['min_value'])
                        spec['max_value'] = None if it.get('max_value') is None else float(it['max_value'])
                    except (TypeError, ValueError):
                        problems.append(f'{col}: accepted_range sınırları sayı olmalı')
                        continue
                    if spec['min_value'] is None and spec['max_value'] is None:
                        problems.append(f'{col}: accepted_range için en az bir sınır gerekli')
                        continue
                bad = run(spec, col, rows, refs)
                if bad:
                    problems.append(f'{col} {t}: bugünkü veride {len(bad)} satır geçmiyor (örnek: {", ".join(repr(b) for b in list(dict.fromkeys(bad))[:3])})')
                    continue
                tests.append((col, spec))
        have = {(c, s['test']) for c, s in tests}
        for k in keys:
            for t in ('unique', 'not_null'):
                if (k, t) not in have:
                    problems.append(f'aday anahtar {k} için {t} testi eksik')
        if problems:
            raise ValueError('; '.join(problems[:20]))
        return tests
    return validate


SYSTEM = (llm.SAFETY + ' Sen dbt projelerinde veri kalitesinden sorumlu analitik mühendisisin. Kolon profiline bakarak anlamlı dbt veri testleri öner: '
          'unique, not_null, accepted_values (values listesi), relationships (to: verilen model adı, field), accepted_range (min_value/max_value; dbt_utils). '
          'Testler bugünkü veride geçmeli; iş kuralını yansıtan sınırlar seç (ör. tutar ≥ 0), ölçülen min/max’ı birebir kopyalama. '
          'Yanıt şeması: {"kolonlar": {"siparis_no": [{"test": "unique"}, {"test": "not_null"}], "durum": [{"test": "accepted_values", "values": ["a", "b"]}], '
          '"musteri_kodu": [{"test": "relationships", "to": "dim_musteri", "field": "musteri_kodu"}], "tutar": [{"test": "accepted_range", "min_value": 0}]}}')


def profile(columns, rows):
    out = []
    for c in columns:
        vals = [r[c] for r in rows]
        filled = [v for v in vals if v.strip()]
        style = tabular.number_style(filled)
        nums = [tabular.parse_number(v, style) for v in filled]
        pii = any(pii_kind(v) for v in filled[:200])
        line = f'- {c}: {len(filled)}/{len(vals)} dolu, {len(set(filled))} tekil'
        if filled and all(n is not None for n in nums):
            line += f', sayı min {min(nums):g} max {max(nums):g}'
        elif not pii and len(set(filled)) <= 25:
            line += ', değerler: ' + ', '.join(sorted(set(filled)))
        elif pii:
            line += ', kişisel veri (değer gönderilmedi)'
        out.append(line)
    return out


def main():
    p = argparse.ArgumentParser(description='dbt modeli çıktısından, bugünkü veride geçen dbt testleri öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'fct_siparis.csv')), help='Modelin dışa aktarımı')
    p.add_argument('--dbt-model', default='', help='dbt model adı (varsayılan: dosya adı)')
    p.add_argument('--ref', action='append', default=[], help='İlişki için başka model: ad=dosya (tekrarlanabilir)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    model = args.dbt_model or Path(args.input).stem
    refs = {}
    for spec in args.ref or [f'dim_musteri={here("ornek", "dim_musteri_ozet.csv")}']:
        name, _, path = spec.partition('=')
        rc, rr = tabular.read(path)
        refs[name.strip()] = {'columns': rc, 'rows': rr}
    keys = [c for c in columns if all(r[c].strip() for r in rows) and len({r[c] for r in rows}) == len(rows)
            and any(c.lower().endswith(s) for s in ('_id', '_no', 'id', '_kodu', '_key'))]
    out = output_dir(args.output)
    user = (f'Model: {model} ({len(rows)} satır)\nKolonlar:\n' + '\n'.join(profile(columns, rows))
            + '\n\nİlişki kurulabilecek modeller: ' + ('; '.join(f'{n} ({", ".join(r["columns"])})' for n, r in refs.items()) or 'yok')
            + (f'\nAday anahtar (tekil ve dolu): {", ".join(keys)}' if keys else ''))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, refs, keys))
    if result is None:
        return
    yml = ['version: 2', '', 'models:', f'  - name: {model}', '    columns:']
    for c in columns:
        ts = [s for col, s in result if col == c]
        if not ts:
            continue
        yml += [f'      - name: {c}', '        data_tests:']
        for s in ts:
            if s['test'] in ('unique', 'not_null'):
                yml.append(f'          - {s["test"]}')
            elif s['test'] == 'accepted_values':
                yml += ['          - accepted_values:', '              arguments:', '                values: [' + ', '.join("'" + v.replace("'", "''") + "'" for v in s['values']) + ']']
            elif s['test'] == 'relationships':
                yml += ['          - relationships:', '              arguments:', f"                to: ref('{s['to']}')", f'                field: {s["field"]}']
            else:
                yml += ['          - dbt_utils.accepted_range:', '              arguments:'] + [f'                {k}: {s[k]:g}' for k in ('min_value', 'max_value') if s.get(k) is not None]
    write_text(out / 'schema.yml', '\n'.join(yml) + '\n')
    md = ['# dbt test önerileri', '', f'{model}: {len(result)} test, {len({c for c, _ in result})} kolon; hepsi bugünkü {len(rows)} satırda geçiyor.', '',
          md_table(['Kolon', 'Test', 'Ayrıntı'], [[c, s['test'], ', '.join(f'{k}={v}' for k, v in s.items() if k != 'test' and v is not None)] for c, s in result]), '',
          f'_Model: {meta["model"]} · Her test yerelde verinin tamamında çalıştırıldı. accepted_range dbt_utils paketini ister; schema.yml dbt 1.10+ data_tests + arguments biçimindedir._']
    write_text(out / 'test-onerileri.md', '\n'.join(md))
    print(f'[OK] {len(result)} test önerildi, hepsi geçti. Çıktı: {out / "test-onerileri.md"}')


if __name__ == '__main__':
    llm.run(main)
