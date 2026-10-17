"""dbt model yazarı (AI agent): devralınan bir SELECT/VIEW sorgusunu dbt modeline çevirir. Sorgunun okuduğu tablolar yerelde
çıkarılır; model her tabloyu source() veya ref() ile değiştirir, materializasyon seçer ve sütun açıklaması/testleri önerir.
Sorguda kalan ham tablo adı, eşlenmeyen tablo, yazma komutu, izinsiz test türü ve SQL’de geçmeyen sütun yerelde reddedilir;
incremental seçilirse is_incremental() filtresi zorunludur. Model SQL’i, schema.yml ve sources.yml dosyalarını kod yazar.
Hiçbir veritabanında çalıştırılmaz. Çıktı: models/<ad>.sql, models/schema.yml, models/sources.yml, dbt-model.md."""
import argparse, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from sql_soyagaci import analyse

MATERIALIZED = ('view', 'table', 'incremental', 'ephemeral')
TESTS = ('not_null', 'unique', 'accepted_values', 'relationships')
WRITE = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|GRANT|CREATE)\b', re.I)
NAME = re.compile(r'^[a-z][a-z0-9_]*$')


def body(sql_text):
    """The SELECT of a legacy statement (CREATE VIEW … AS is stripped)."""
    text = re.sub(r'/\*.*?\*/', ' ', sql_text, flags=re.S)
    text = re.sub(r"--[^\n']*$", '', text, flags=re.M).strip().rstrip(';').strip()  # string literals stay intact
    m = re.search(r'\bAS\s+(\(?\s*(?:WITH|SELECT)\b.*)$', text, re.I | re.S) if re.match(r'^\s*CREATE\b', text, re.I) else None
    return (m.group(1) if m else text).strip()


def validator(tables):
    def validate(obj):
        problems = []
        sql = re.sub(r'^```\w*\s*|```$', '', str(obj.get('sql', '')).strip()).strip().rstrip(';')
        sql = re.sub(r'^\s*\{\{\s*config\(.*?\)\s*\}\}\s*', '', sql, flags=re.S)
        name = str(obj.get('model_adi', '')).strip().lower()
        mat = str(obj.get('materializasyon', '')).strip().lower()
        if not NAME.match(name):
            problems.append('model_adi küçük harf, rakam ve alt çizgiden oluşmalı')
        if mat not in MATERIALIZED:
            problems.append(f'materializasyon şunlardan biri olmalı: {", ".join(MATERIALIZED)}')
        plain = re.sub(r'\{\{.*?\}\}|\{%.*?%\}', ' ', sql, flags=re.S)
        if not re.match(r'^\s*(WITH|SELECT)\b', plain, re.I):
            problems.append('sql WITH veya SELECT ile başlamalı (config bloğunu kod ekler)')
        if WRITE.search(plain):
            problems.append('sql yazma/DDL komutu içeremez')
        if mat == 'incremental' and 'is_incremental()' not in sql.replace(' ', ''):
            problems.append('incremental modelde {% if is_incremental() %} filtresi gerekli')
        maps, seen = [], set()
        for k in obj.get('kaynaklar') or []:
            if not isinstance(k, dict):
                continue
            t = str(k.get('tablo', '')).strip().lower()
            kind = str(k.get('tur', '')).strip().lower()
            if t not in tables:
                problems.append(f'sorguda olmayan tablo eşlendi: {t or "?"}')
                continue
            seen.add(t)
            if kind == 'source':
                src, tbl = str(k.get('source', '')).strip(), str(k.get('ad', '')).strip()
                rx = r'source\(\s*[\'"]' + re.escape(src) + r'[\'"]\s*,\s*[\'"]' + re.escape(tbl) + r'[\'"]\s*\)'
            elif kind == 'ref':
                src, tbl = '', str(k.get('ad', '')).strip()
                rx = r'ref\(\s*[\'"]' + re.escape(tbl) + r'[\'"]\s*\)'
            else:
                problems.append(f'{t}: tur "source" veya "ref" olmalı')
                continue
            if not tbl or not re.search(rx, sql):
                problems.append(f'{t} için sql içinde {kind}() çağrısı yok veya adı tutmuyor')
            maps.append({'tablo': t, 'tur': kind, 'source': src, 'ad': tbl})
        missing = [t for t in tables if t not in seen]
        if missing:
            problems.append('eşlenmeyen tablo: ' + ', '.join(missing))
        raw = [t for t in tables if re.search(r'(?<![\w.\'"])' + re.escape(t) + r'(?![\w])', plain, re.I)]
        if raw:
            problems.append('sql içinde ham tablo adı kaldı: ' + ', '.join(raw))
        cols = []
        for c in obj.get('sutunlar') or []:
            if not isinstance(c, dict) or not str(c.get('ad', '')).strip():
                continue
            cname = str(c['ad']).strip()
            tests = [str(x).strip() for x in (c.get('testler') or [])]
            bad = [x for x in tests if x not in TESTS]
            if bad:
                problems.append(f'{cname}: izin verilmeyen test {", ".join(bad)} (izinli: {", ".join(TESTS)})')
            if not re.search(r'\b' + re.escape(cname) + r'\b', plain, re.I):
                problems.append(f'{cname}: sql içinde geçmiyor')
            cols.append({'ad': cname, 'aciklama': str(c.get('aciklama', '')).strip(), 'testler': [x for x in tests if x in TESTS]})
        if not cols:
            problems.append('en az bir sütun tanımı gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        return {'name': name, 'materialized': mat, 'sql': sql.strip(), 'maps': maps, 'columns': cols,
                'description': str(obj.get('aciklama', '')).strip(), 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


def q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def schema_yml(r):
    lines = ['version: 2', '', 'models:', f"  - name: {r['name']}", f"    description: {q(r['description'])}", '    columns:']
    for c in r['columns']:
        lines += [f"      - name: {c['ad']}", f"        description: {q(c['aciklama'])}"]
        ready = [t for t in c['testler'] if t in ('not_null', 'unique')]
        todo = {'accepted_values': '# - accepted_values: {values: [...]}  # değerleri doldurup açın',
                'relationships': "# - relationships: {to: ref('...'), field: ...}  # hedefi doldurup açın"}
        pending = [todo[t] for t in c['testler'] if t in todo]
        if ready:
            lines.append('        data_tests:')
            lines += [f'          - {t}' for t in ready] + [f'          {x}' for x in pending]
        elif pending:  # a key with only comments would be null and fail dbt parse
            lines += ['        # data_tests:'] + [f'        #   {x[2:]}' for x in pending]
    return '\n'.join(lines) + '\n'


def sources_yml(maps):
    by = {}
    for m in maps:
        if m['tur'] == 'source':
            by.setdefault(m['source'], []).append(m)
    if not by:
        return ''
    lines = ['version: 2', '', 'sources:']
    for src, ms in sorted(by.items()):
        schema = ms[0]['tablo'].rsplit('.', 1)[0] if '.' in ms[0]['tablo'] else src
        lines += [f'  - name: {src}', f'    schema: {schema}', '    tables:']
        lines += [f"      - name: {m['ad']}" + (f"\n        identifier: {m['tablo'].rsplit('.', 1)[-1]}" if m['ad'] != m['tablo'].rsplit('.', 1)[-1] else '') for m in ms]
    return '\n'.join(lines) + '\n'


SYSTEM = (llm.SAFETY + ' Sen bir analytics engineer’sın. Verilen eski SQL sorgusunu tek bir dbt modeline çevir. Sorgunun okuduğu HER tabloyu '
          "{{ source('kaynak', 'tablo') }} (ham sistem tablosu) veya {{ ref('model') }} (projede model olan tablo) ile değiştir; ham tablo adı bırakma. "
          'İş mantığını değiştirme; yalnızca CTE’lerle okunur hâle getirebilirsin. config bloğu yazma, materializasyonu ayrı alanda ver. '
          f'materializasyon: {", ".join(MATERIALIZED)}; incremental seçersen is_incremental() filtresi ekle. Sütun testleri: {", ".join(TESTS)}. '
          'Yanıt şeması: {"model_adi": "fct_...", "aciklama": "...", "materializasyon": "view", "sql": "WITH ... SELECT ...", '
          '"kaynaklar": [{"tablo": "şema.tablo (sorgudaki gibi)", "tur": "source", "source": "erp", "ad": "siparis"}], '
          '"sutunlar": [{"ad": "...", "aciklama": "...", "testler": ["not_null"]}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Eski SELECT/VIEW sorgusunu dbt modeline (source/ref, testler, yml) çeviren agent.')
    p.add_argument('--sql', default=str(here('ornek', 'eski_rapor.sql')))
    p.add_argument('--models', default='', help='Projede zaten model olan tablolar (ref() ile çağrılacak), virgülle')
    p.add_argument('--context', help='İş bağlamı (isteğe bağlı .md)')
    llm.add_arguments(p)
    args = p.parse_args()
    select = body(Path(args.sql).read_text(encoding='utf-8-sig'))
    parsed = analyse(select, Path(args.sql).name)
    if not parsed:
        raise ValueError('Dosyada SELECT veya CREATE VIEW … AS SELECT bulunamadı.')
    tables = list(dict.fromkeys(t for s in parsed for t in s['sources']))
    if not tables:
        raise ValueError('Sorguda okunan tablo bulunamadı.')
    known = [m.strip().lower() for m in args.models.split(',') if m.strip()]
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    user = (f"Okunan tablolar (yerelde çıkarıldı): {', '.join(tables)}\nProjede model olanlar (ref): {', '.join(known) or 'yok'}\n\n"
            f"Eski sorgu:\n```sql\n{select}\n```\n\nBağlam:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\"")
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables))
    if result is None:
        return
    models = out / 'models'
    models.mkdir(parents=True, exist_ok=True)
    write_text(models / f"{result['name']}.sql", f"{{{{ config(materialized='{result['materialized']}') }}}}\n\n{result['sql']}\n")
    write_text(models / 'schema.yml', schema_yml(result))
    src = sources_yml(result['maps'])
    if src:
        write_text(models / 'sources.yml', src)
    write_json(out / 'dbt-model.json', result)
    md = [f"# dbt modeli — {result['name']}", '', result['description'], '', f"Materializasyon: **{result['materialized']}**", '',
          md_table(['Eski tablo', 'Yeni çağrı'], [[m['tablo'], f"source('{m['source']}', '{m['ad']}')" if m['tur'] == 'source' else f"ref('{m['ad']}')"] for m in result['maps']]), '',
          md_table(['Sütun', 'Açıklama', 'Testler'], [[c['ad'], c['aciklama'], ', '.join(c['testler']) or '—'] for c in result['columns']])]
    if result['notes']:
        md += ['', '## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · `dbt compile` ve `dbt build --select {result['name']}` ile deneyin; eski sorguyla satır/tutar mutabakatı yapın._"]
    write_text(out / 'dbt-model.md', '\n'.join(md))
    print(f"[OK] {result['name']} ({result['materialized']}): {len(result['maps'])} tablo eşlendi, {sum(len(c['testler']) for c in result['columns'])} test. Çıktı: {models}")


if __name__ == '__main__':
    llm.run(main)
