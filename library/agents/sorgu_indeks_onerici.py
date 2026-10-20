"""Sorgu indeks önerici (AI agent): bir şema (CREATE TABLE) ve uygulamanın sık çalıştırdığı sorgulardan indeks önerir ve önerileri gerçek bir
sorgu planlayıcısında dener. Şema SQLite’ta boş olarak kurulur; her sorgunun planı (EXPLAIN QUERY PLAN) indekslerden önce ve sonra alınır.
Modele şema ve sorgular gider (veri yoktur). Öneri kodla denetlenir: CREATE INDEX derlenmeli ve şemadaki tablo/kolonları kullanmalı;
her indeks en az bir sorgunun planında kullanılmalı (kullanılmayan indeks yalnızca yazmayı yavaşlatır); başka bir indeksin baş kolonlarını
tekrarlayan indeks olmamalı; tablo başına en fazla --max-per-table indeks. Çıktı: indeksler.sql, plan_karsilastirma.csv, indeks-onerileri.md.
Not: plan SQLite planlayıcısıyla alınır; PostgreSQL/SQL Server planlayıcıları istatistiklere göre farklı seçim yapabilir."""
import argparse, re, sqlite3
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table


def statements(text):
    text = re.sub(r'--[^\n]*', '', text)
    return [s.strip() for s in text.split(';') if s.strip()]


def plan(con, sql):
    return ' | '.join(r[3] for r in con.execute('EXPLAIN QUERY PLAN ' + sql))


def build(schema):
    con = sqlite3.connect(':memory:')
    for s in statements(schema):
        con.execute(s)
    return con


def index_cols(con, name):
    return [r[2] for r in con.execute(f'PRAGMA index_info("{name}")')]


def validator(schema, queries, max_per_table):
    base = build(schema)
    before = [plan(base, q) for q in queries]
    existing = {r[0]: (r[1], index_cols(base, r[0])) for r in base.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")}

    def validate(obj):
        con = build(schema)
        problems, made = [], []
        for i, e in enumerate(obj.get('indeksler') or [], 1):
            sql = str(e.get('sql', '')).strip().rstrip(';')
            m = re.match(r'(?is)^create\s+(unique\s+)?index\s+(?:if\s+not\s+exists\s+)?("?)(\w+)\2\s+on\s+', sql)
            if not m or ';' in sql:
                problems.append(f'{i}. öneri tek bir CREATE INDEX olmalı')
                continue
            try:
                con.execute(sql)
            except sqlite3.Error as exc:
                problems.append(f'{m.group(3)}: {exc}')
                continue
            made.append((m.group(3), str(e.get('gerekce', '')).strip()))
        if problems:
            raise ValueError(' | '.join(problems))
        info = {n: (con.execute('SELECT tbl_name FROM sqlite_master WHERE name = ?', (n,)).fetchone()[0], index_cols(con, n)) for n, _ in made}
        after = [plan(con, q) for q in queries]
        for n, (tbl, cols) in info.items():
            if not any(re.search(rf'INDEX {re.escape(n)}\b', p) for p in after):
                problems.append(f'{n} ({tbl}: {", ".join(cols)}) hiçbir sorgunun planında kullanılmıyor')
            for other, (t2, c2) in list(info.items()) + list(existing.items()):
                if other != n and t2 == tbl and len(c2) >= len(cols) and c2[:len(cols)] == cols:
                    problems.append(f'{n} ({", ".join(cols)}) {other} ({", ".join(c2)}) indeksinin baş kolonlarını tekrarlıyor')
        per = {}
        for n, (tbl, _) in info.items():
            per[tbl] = per.get(tbl, 0) + 1
        over = [f'{t} ({k})' for t, k in per.items() if k > max_per_table]
        if over:
            problems.append(f'tablo başına en fazla {max_per_table} indeks: ' + ', '.join(over))
        if problems:
            raise ValueError(' | '.join(problems))
        return made, info, before, after
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veritabanı performans uzmanısın. Şema ve sık çalışan sorgulara bakarak indeks öner: eşitlik filtrelerini öne, aralık ve '
          'sıralama kolonlarını sona koy; birden fazla sorguya hizmet eden bileşik indeksleri tercih et; başka bir indeksin baş kolonlarını tekrarlama; her '
          'indeks en az bir sorguda kullanılmalı. Yanıt şeması: {"indeksler": [{"sql": "CREATE INDEX ix_siparis_musteri_tarih ON siparis (musteri_id, '
          'siparis_tarihi)", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Şema ve sorgulardan indeks öneren, önerileri SQLite sorgu planlayıcısında deneyen agent.')
    p.add_argument('--schema', default=str(here('ornek', 'magaza_semasi.sql')))
    p.add_argument('--queries', default=str(here('ornek', 'magaza_sorgulari.sql')))
    p.add_argument('--max-per-table', type=int, default=3)
    llm.add_arguments(p)
    args = p.parse_args()
    schema = Path(args.schema).read_text(encoding='utf-8')
    queries = [q for q in statements(Path(args.queries).read_text(encoding='utf-8')) if re.match(r'(?is)^(select|with)\b', q)]
    if not queries:
        raise ValueError('Sorgu dosyasında SELECT yok.')
    try:
        con = build(schema)
    except sqlite3.Error as exc:
        raise ValueError(f'Şema SQLite’ta kurulamadı ({exc}); şemayı sade CREATE TABLE ifadelerine indirin.') from None
    plans = [plan(con, q) for q in queries]
    out = output_dir(args.output)
    user = ('Şema:\n' + schema.strip() + '\n\nSorgular ve bugünkü planları:\n'
            + '\n\n'.join(f'-- Sorgu {i}\n{q}\n-- Plan: {pl}' for i, (q, pl) in enumerate(zip(queries, plans), 1)))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(schema, queries, args.max_per_table))
    if result is None:
        return
    made, info, before, after = result
    sqls = [f'-- {g}\nCREATE INDEX {n} ON {info[n][0]} ({", ".join(info[n][1])});' if g else f'CREATE INDEX {n} ON {info[n][0]} ({", ".join(info[n][1])});' for n, g in made]
    write_text(out / 'indeksler.sql', '\n'.join(sqls) + '\n')
    rows = [{'sorgu': i, 'once': b, 'sonra': a, 'iyilesti': 'evet' if ('SCAN' in b and 'SCAN' not in a) or (b != a and 'INDEX' in a) else 'hayır'}
            for i, (b, a) in enumerate(zip(before, after), 1)]
    tabular.write_csv(out / 'plan_karsilastirma.csv', ['sorgu', 'once', 'sonra', 'iyilesti'], rows)
    md = ['# İndeks önerileri', '', f'{len(made)} indeks, {len(queries)} sorgu; {sum(r["iyilesti"] == "evet" for r in rows)} sorgunun planı tam taramadan indeks aramaya döndü.', '',
          md_table(['İndeks', 'Tablo', 'Kolonlar', 'Gerekçe'], [[n, info[n][0], ', '.join(info[n][1]), g] for n, g in made]), '',
          md_table(['Sorgu', 'Önce', 'Sonra'], [[r['sorgu'], r['once'], r['sonra']] for r in rows]), '',
          f'_Model: {meta["model"]} · Planlar SQLite sorgu planlayıcısıyla (EXPLAIN QUERY PLAN) alındı; her indeksin en az bir planda kullanıldığı ve tekrar etmediği denetlendi. '
          'Üretim veritabanında istatistiklere göre farklı plan seçilebilir; uygulamadan önce kendi veritabanınızda EXPLAIN ile doğrulayın._']
    write_text(out / 'indeks-onerileri.md', '\n'.join(md))
    print(f'[OK] {len(made)} indeks. Çıktı: {out / "indeks-onerileri.md"}')


if __name__ == '__main__':
    llm.run(main)
