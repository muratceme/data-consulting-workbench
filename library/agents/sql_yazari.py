"""SQL yazarı (AI agent): tablo şemanı ve iş isteğini okur, istenen SQL lehçesinde tek bir SELECT sorgusu yazar.
Şema yerelde çözümlenir; model yalnızca sorguyu tasarlar. Sonuç yerelde denetlenir: tek ifade, yalnızca SELECT/WITH,
yazma komutu yok, şemada olmayan tablo yok. Sorgu hiçbir veritabanında çalıştırılmaz.
Girdi şeması: CREATE TABLE içeren .sql, veri-profili çıktısı profile.json veya {"tablo": ["sütun", ...]} biçiminde .json.
Çıktı: sorgu.sql, sorgu-aciklama.md, dogrulama.json."""
import argparse, json, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text
from sql_soyagaci import analyse, clean_sql, norm
from sema_farki import display, schema_from_ddl

DIALECTS = {'ansi': 'ANSI SQL', 'tsql': 'Microsoft SQL Server (T-SQL)', 'postgres': 'PostgreSQL', 'bigquery': 'Google BigQuery Standard SQL',
            'snowflake': 'Snowflake SQL', 'databricks': 'Databricks SQL', 'oracle': 'Oracle SQL', 'mysql': 'MySQL 8'}
WRITE = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|CALL|COPY|UNLOAD|REPLACE\s+INTO)\b', re.I)


def load_schema(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'Şema dosyası bulunamadı: {p}')
    text = p.read_text(encoding='utf-8-sig')
    if p.suffix.lower() == '.json':
        data = json.loads(text)
        if 'columns' in data and 'row_count' in data:  # veri-profili çıktısı
            name = Path(data.get('source', 'tablo')).stem.lower()
            return {name: [{'name': c['name'], 'type': c['type']} for c in data['columns']]}
        tables = data.get('tables', data)
        return {display(t): [c if isinstance(c, dict) else {'name': c, 'type': ''} for c in cols] for t, cols in tables.items()}
    tables = schema_from_ddl(text)
    if not tables:
        raise ValueError('Şema dosyasında CREATE TABLE bulunamadı.')
    return tables


def validator(schema, dialect):
    known = {norm(t) for t in schema}
    short = {t.split('.')[-1] for t in known}

    def validate(obj):
        sql = obj.get('sql')
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError('"sql" alanı boş.')
        sql = re.sub(r'^```\w*\s*|```$', '', sql.strip()).strip().rstrip(';').strip()
        cleaned = clean_sql(sql)
        if ';' in cleaned:
            raise ValueError('Yalnızca tek bir SQL ifadesi döndür.')
        if not re.match(r'^\s*(WITH|SELECT)\b', cleaned, re.I):
            raise ValueError('Sorgu SELECT veya WITH ile başlamalı.')
        bad = WRITE.findall(cleaned)
        if bad:
            raise ValueError(f'Yazma/DDL komutu kullanılamaz: {", ".join(sorted(set(b.upper() for b in bad)))}.')
        parsed = analyse(sql, 'sorgu.sql')
        used = parsed[0]['sources'] if parsed else []
        unknown = [t for t in used if t not in known and t.split('.')[-1] not in short]
        if unknown:
            raise ValueError(f'Şemada olmayan tablo kullanıldı: {", ".join(unknown)}. Yalnızca şunları kullan: {", ".join(sorted(schema))}.')
        columns = {c['name'].lower() for cols in schema.values() for c in cols}
        # Names the query defines itself (column aliases, CTE columns) may follow a dot; anything else must be in the schema.
        defined = {a.lower() for a in re.findall(r'\bAS\s+[\["]?([A-Za-z_]\w*)', cleaned, re.I)}
        refs = {r.lower() for r in re.findall(r'\b[A-Za-z_]\w*\.[\["]?([A-Za-z_]\w*)', cleaned)}
        invented = sorted(r for r in refs if r not in columns | defined | short)
        if invented:
            raise ValueError(f'Şemada olmayan sütun kullanıldı: {", ".join(invented)}. Hesaplanan değerleri ifadeyle yaz '
                             'veya önce bir CTE/alt sorguda AS ile tanımla.')
        return {'sql': sql, 'explanation': str(obj.get('aciklama', '')).strip(), 'assumptions': [str(a) for a in obj.get('varsayimlar', []) if a],
                'tables': used, 'column_warnings': []}
    return validate


SYSTEM = (llm.SAFETY + ' Sen kıdemli bir analitik mühendisisin. Verilen şemaya göre istenen soruyu cevaplayan TEK bir SELECT sorgusu yaz. '
          'Yalnızca şemadaki tablo ve sütunları kullan, tablo takma adları kullan, okunur biçimlendir. '
          'Bire-çok birleştirmeden sonra varlık sayarken COUNT(DISTINCT ...) kullan; oran ve ortalamaları doğru tanımda hesapla '
          '(ör. sipariş başına ortalama = toplam / farklı sipariş sayısı). Tarih filtresini indeks kullanabilecek aralık biçiminde yaz. '
          'Yanıt şeması: {"sql": "...", "aciklama": "sorgunun adım adım ne yaptığı", "varsayimlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Şemadan SQL yazan agent (sorgu çalıştırılmaz).')
    p.add_argument('--schema', default=str(here('ornek', 'sema.sql')))
    p.add_argument('--request', default=str(here('ornek', 'istek.md')), help='İş isteği (metin/Markdown dosyası)')
    p.add_argument('--dialect', default='tsql', choices=sorted(DIALECTS))
    llm.add_arguments(p)
    args = p.parse_args()
    schema = load_schema(args.schema)
    request = Path(args.request).read_text(encoding='utf-8-sig').strip()
    if not request:
        raise ValueError('İstek dosyası boş.')
    out = output_dir(args.output)
    schema_text = '\n'.join(f"{t}({', '.join((c['name'] + ' ' + c['type']).strip() for c in cols)})" for t, cols in schema.items())
    user = f'SQL lehçesi: {DIALECTS[args.dialect]}\n\nŞema:\n{schema_text}\n\nİş isteği:\n"""\n{request}\n"""'
    result, meta = llm.ask(args, out, SYSTEM, user, validator(schema, args.dialect))
    if result is None:
        return
    write_text(out / 'sorgu.sql', f"-- {DIALECTS[args.dialect]} · datawork SQL yazarı · {meta['model']}\n-- Çalıştırmadan önce inceleyin; sorgu hiçbir veritabanında test edilmedi.\n{result['sql']};")
    md = ['# Sorgu açıklaması', '', result['explanation'] or '_Açıklama verilmedi._', '']
    if result['assumptions']:
        md += ['## Varsayımlar', ''] + [f'- {a}' for a in result['assumptions']] + ['']
    md += ['## Yerel denetim', '', f"- Tek SELECT ifadesi: evet", f"- Kullanılan tablolar: {', '.join(result['tables']) or '—'}",
           f"- Şemada bulunamayan sütun başvuruları: {', '.join(result['column_warnings']) or 'yok'}", '', '```sql', result['sql'], '```']
    write_text(out / 'sorgu-aciklama.md', '\n'.join(md))
    write_json(out / 'dogrulama.json', {k: v for k, v in result.items() if k != 'sql'} | {'meta': meta})
    flag = '!' if result['column_warnings'] else 'OK'
    print(f"[{flag}] Sorgu yazıldı ve yerelde denetlendi ({meta['attempts']} deneme). Çıktı: {out / 'sorgu.sql'}")


if __name__ == '__main__':
    llm.run(main)
