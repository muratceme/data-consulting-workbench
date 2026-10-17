"""Boyutsal model tasarımcısı (AI agent): düz bir dışa aktarımın (CSV/XLSX) profilini yerelde çıkarır; model sütunları bir olgu
tablosuna (ölçüler, dejenere boyutlar, tarih anahtarları) ve boyut tablolarına (doğal anahtar, nitelikler, SCD tipi) dağıtır.
Her sütunun tam bir kez kullanıldığı, ölçülerin sayısal, doğal anahtarın boyutun içinde olduğu yerelde denetlenir. Ardından kod,
her boyut için doğal anahtar → nitelik bağımlılığını gerçek veride ölçer (aynı anahtarda farklı nitelik değeri = SCD2 ya da
yanlış boyut) ve CREATE TABLE taslağıyla Mermaid ER şemasını yazar. Satırlar gönderilmez.
Çıktı: boyutsal-model.md, model.sql, model.json."""
import argparse, re
from collections import defaultdict
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from veri_profili import profile
from veri_sozlugu import profile_text

NUMERIC = ('tam sayı', 'ondalık')
NAME = re.compile(r'^[a-z][a-z0-9_]{1,62}$')


def sql_type(col):
    t = col['type']
    if t == 'tam sayı':
        return 'BIGINT'
    if t == 'ondalık':
        return 'DECIMAL(18,2)'
    if t == 'tarih':
        return 'DATE'
    return f"VARCHAR({max(10, (col['max_length'] * 3 // 2 + 9) // 10 * 10)})"


def validator(data):
    cols = {c['name']: c for c in data['columns']}

    def as_list(v):
        return [str(x).strip() for x in v] if isinstance(v, list) else ([str(v).strip()] if v else [])

    def validate(obj):
        fact, dims = obj.get('olgu') or {}, obj.get('boyutlar') or []
        if not isinstance(fact, dict) or not isinstance(dims, list):
            raise ValueError('"olgu" nesnesi ve "boyutlar" listesi gerekli.')
        problems, used = [], defaultdict(list)
        fname = str(fact.get('ad', '')).strip().lower()
        if not fname.startswith('fct_') or not NAME.match(fname):
            problems.append('olgu adı fct_ ile başlayan küçük harfli bir ad olmalı')
        measures, degenerate, dates = as_list(fact.get('olculer')), as_list(fact.get('dejenere')), as_list(fact.get('tarih_sutunlari'))
        if not measures:
            problems.append('olguda en az bir ölçü olmalı')
        for role, names in (('ölçü', measures), ('dejenere', degenerate), ('tarih', dates)):
            for n in names:
                used[n].append(f'olgu/{role}')
        for n in measures:
            if n in cols and cols[n]['type'] not in NUMERIC:
                problems.append(f'"{n}" sayısal değil ({cols[n]["type"]}); ölçü olamaz')
        for n in dates:
            if n in cols and cols[n]['type'] != 'tarih':
                problems.append(f'"{n}" tarih değil; tarih_sutunlari listesine konamaz')
        clean_dims, dnames = [], set()
        for d in dims:
            if not isinstance(d, dict):
                continue
            name = str(d.get('ad', '')).strip().lower()
            key, attrs = as_list(d.get('dogal_anahtar')), as_list(d.get('nitelikler'))
            scd = str(d.get('scd', '1')).strip()
            if not name.startswith('dim_') or not NAME.match(name) or name in dnames:
                problems.append(f'boyut adı "{name}" dim_ ile başlamalı ve tekil olmalı')
            if not key:
                problems.append(f'{name}: dogal_anahtar boş')
            if scd not in ('1', '2'):
                problems.append(f'{name}: scd 1 veya 2 olmalı')
            dnames.add(name)
            for n in key + attrs:
                used[n].append(name)
            clean_dims.append({'ad': name, 'dogal_anahtar': key, 'nitelikler': attrs, 'scd': scd, 'aciklama': str(d.get('aciklama', '')).strip()})
        unknown = sorted(n for n in used if n not in cols)
        twice = sorted(f'{n} ({" + ".join(w)})' for n, w in used.items() if len(w) > 1)
        missing = [n for n in cols if n not in used]
        if unknown:
            problems.append('dosyada olmayan sütun: ' + ', '.join(unknown))
        if twice:
            problems.append('birden çok yerde kullanılan sütun: ' + ', '.join(twice))
        if missing:
            problems.append('hiçbir tabloya konmayan sütun: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return {'fact': {'ad': fname, 'tane': str(fact.get('tane', '')).strip(), 'olculer': measures, 'dejenere': degenerate, 'tarih_sutunlari': dates},
                'dims': clean_dims, 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


def dependency(rows, dims):
    """For each dimension: how many natural keys map to more than one attribute combination in the data."""
    found = []
    for d in dims:
        seen = defaultdict(set)
        for r in rows:
            key = tuple(r[k].strip() for k in d['dogal_anahtar'])
            if all(key):
                seen[key].add(tuple(r[a].strip() for a in d['nitelikler']))
        bad = sum(len(v) > 1 for v in seen.values())
        culprits = [a for i, a in enumerate(d['nitelikler']) if any(len({t[i] for t in v}) > 1 for v in seen.values())]
        found.append({'boyut': d['ad'], 'anahtar': len(seen), 'celisen_anahtar': bad, 'degisen_nitelikler': ', '.join(culprits), 'scd': d['scd'],
                      'yorum': '' if not bad else ('SCD2 olarak tasarlandı; geçmiş tutulacak' if d['scd'] == '2' else
                                                    'aynı anahtarda farklı değer: nitelik olguya ait olabilir veya SCD2 gerekir')})
    return found


def ddl(result, cols, schema):
    fact, out, er = result['fact'], [], ['erDiagram']
    for d in result['dims']:
        lines = [f"    {d['ad']}_sk BIGINT NOT NULL PRIMARY KEY"]
        lines += [f"    {n} {sql_type(cols[n])} NOT NULL" for n in d['dogal_anahtar']]
        lines += [f"    {n} {sql_type(cols[n])}" for n in d['nitelikler']]
        if d['scd'] == '2':
            lines += ['    gecerlilik_baslangic DATE NOT NULL', '    gecerlilik_bitis DATE', '    guncel_mi BOOLEAN NOT NULL']
        out.append(f"CREATE TABLE {schema}.{d['ad']} (\n" + ',\n'.join(lines) + '\n);')
        er.append(f"    {d['ad']} ||--o{{ {fact['ad']} : \"{', '.join(d['dogal_anahtar'])}\"")
    lines = [f"    {d['ad']}_sk BIGINT NOT NULL REFERENCES {schema}.{d['ad']}" for d in result['dims']]
    lines += [f"    {n}_key INT NOT NULL REFERENCES {schema}.dim_tarih" for n in fact['tarih_sutunlari']]
    lines += [f"    {n} {sql_type(cols[n])}" for n in fact['dejenere']]
    lines += [f"    {n} {sql_type(cols[n])}" for n in fact['olculer']]
    note = '\n-- Tarih anahtarları yyyymmdd (INT); dim_tarih için tarih-tablosu kod bloğunu kullanın.' if fact['tarih_sutunlari'] else ''
    out.append(f"-- Tane: {fact['tane'] or 'belirtilmedi'}{note}\nCREATE TABLE {schema}.{fact['ad']} (\n" + ',\n'.join(lines) + '\n);')
    if fact['tarih_sutunlari']:
        er.append(f"    dim_tarih ||--o{{ {fact['ad']} : \"{', '.join(fact['tarih_sutunlari'])}\"")
    return '\n\n'.join(out) + '\n', '\n'.join(er)


SYSTEM = (llm.SAFETY + ' Sen Kimball yöntemini uygulayan bir veri ambarı mimarısın. Düz bir dışa aktarımın sütun profilinden yıldız şema öner: '
          'bir olgu tablosu (tane, ölçüler, dejenere boyutlar, tarih sütunları) ve boyut tabloları (doğal anahtar, nitelikler, SCD tipi). '
          'Dosyadaki HER sütunu tam bir kez kullan: olgu/olculer, olgu/dejenere, olgu/tarih_sutunlari, bir boyutun dogal_anahtar veya nitelikler listesi. '
          'Ölçüler sayısal olmalı. Tablo adları küçük harf; olgu fct_, boyut dim_ ile başlar. Tarih boyutu oluşturma, tarih sütunlarını tarih_sutunlari’na yaz. '
          'Yanıt şeması: {"olgu": {"ad": "fct_...", "tane": "bir satır = ...", "olculer": [], "dejenere": [], "tarih_sutunlari": []}, '
          '"boyutlar": [{"ad": "dim_...", "dogal_anahtar": [], "nitelikler": [], "scd": "1", "aciklama": "..."}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Düz dışa aktarımdan yıldız şema (olgu + boyut) tasarlayan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')))
    p.add_argument('--sheet')
    p.add_argument('--context', help='İş bağlamı (isteğe bağlı .md)')
    p.add_argument('--schema', default='dwh', help='DDL taslağındaki şema adı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    data = {'source': Path(args.input).name, **profile(columns, rows)}
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    user = f"Sütun profili:\n{profile_text(data)}\n\nİş bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(data))
    if result is None:
        return
    cols = {c['name']: c for c in data['columns']}
    checks = dependency(rows, result['dims'])
    sql, er = ddl(result, cols, args.schema)
    write_text(out / 'model.sql', f'-- {data["source"]} için yıldız şema taslağı (model: {meta["model"]}). Gözden geçirmeden çalıştırmayın.\n\n' + sql)
    write_json(out / 'model.json', {**result, 'bagimlilik_kontrolu': checks})
    f = result['fact']
    md = [f"# Boyutsal model — {data['source']}", '', f"**{f['ad']}** · tane: {f['tane'] or '—'}", '',
          f"- Ölçüler: {', '.join(f['olculer'])}", f"- Dejenere boyutlar: {', '.join(f['dejenere']) or '—'}", f"- Tarih anahtarları: {', '.join(f['tarih_sutunlari']) or '—'}", '',
          md_table(['Boyut', 'Doğal anahtar', 'Nitelikler', 'SCD', 'Açıklama'], [[d['ad'], ', '.join(d['dogal_anahtar']), ', '.join(d['nitelikler']) or '—', d['scd'], d['aciklama']] for d in result['dims']]),
          '', '## Veride anahtar → nitelik bağımlılığı (kodla ölçüldü)', '',
          md_table(['Boyut', 'Anahtar', 'Çelişen anahtar', 'Değişen nitelikler', 'Yorum'], [[c['boyut'], c['anahtar'], c['celisen_anahtar'], c['degisen_nitelikler'] or '—', c['yorum'] or 'tutarlı'] for c in checks]),
          '', '```mermaid', er, '```']
    if result['notes']:
        md += ['', '## Tasarım notları', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · DDL taslaktır (model.sql); tane ve SCD kararlarını iş sahibiyle onaylayın._"]
    write_text(out / 'boyutsal-model.md', '\n'.join(md))
    bad = [c['boyut'] for c in checks if c['celisen_anahtar'] and c['scd'] != '2']
    warn = f"; anahtarı tutarsız boyut: {', '.join(bad)}" if bad else ''
    print(f"[{'!' if bad else 'OK'}] {f['ad']} + {len(result['dims'])} boyut tasarlandı{warn}. Çıktı: {out / 'boyutsal-model.md'}")


if __name__ == '__main__':
    llm.run(main)
