"""SQL optimizasyon danışmanı (AI agent): yavaş bir PostgreSQL sorgusunu ve EXPLAIN (ANALYZE, FORMAT JSON) çıktısını okur; plandaki
sıcak noktaları (en pahalı düğümler, filtreyle çok satır atan sıralı taramalar, diske taşan sıralamalar, satır tahmin hataları) kodla
çıkarır. Model yeniden yazılmış sorgu ve indeks önerisi verir. Yanıt yerelde denetlenir: yeniden yazım tek bir SELECT olmalı, yalnızca
özgün sorgudaki tabloları okumalı, yazma komutu içermemeli; her indeks önerisi CREATE INDEX biçiminde, özgün sorgudaki bir tablo ve o
sorguda geçen sütunlar üzerinde olmalı. Hiçbir veritabanında çalıştırılmaz. Çıktı: optimizasyon.md, onerilen_sorgu.sql, indeksler.sql."""
import argparse, json, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from sql_soyagaci import analyse, clean_sql

WRITE = re.compile(r'\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|COPY|CALL)\b', re.I)
INDEX = re.compile(r'^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+([\w.]+)\s*(?:USING\s+\w+\s*)?\(([^)]+)\)\s*(INCLUDE\s*\(([^)]+)\))?\s*(WHERE\s+.+)?;?\s*$', re.I | re.S)


def hotspots(plan_path):
    data = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
    item = data[0] if isinstance(data, list) else data
    if 'Plan' not in item:
        raise ValueError('JSON’da "Plan" yok; EXPLAIN (ANALYZE, FORMAT JSON) çıktısı verin.')
    nodes = []

    def walk(n, depth=0):
        nodes.append((n, depth))
        for c in n.get('Plans', []) or []:
            walk(c, depth + 1)
    walk(item['Plan'])
    facts = [f"Toplam çalışma süresi: {item.get('Execution Time', '?')} ms; planlama: {item.get('Planning Time', '?')} ms"]
    for n, depth in nodes:
        kind, rel = n.get('Node Type', ''), n.get('Relation Name', '')
        bits = [f"{'  ' * depth}{kind}{' ' + rel if rel else ''}", f"süre {n.get('Actual Total Time', '?')} ms", f"tahmin {n.get('Plan Rows', '?')} / gerçek {n.get('Actual Rows', '?')} satır"]
        for key, label in (('Filter', 'filtre'), ('Rows Removed by Filter', 'filtreyle atılan'), ('Index Name', 'indeks'), ('Index Cond', 'indeks koşulu'),
                           ('Hash Cond', 'hash koşulu'), ('Sort Key', 'sıralama'), ('Sort Space Type', 'sıralama alanı'), ('Actual Loops', 'döngü')):
            if n.get(key) not in (None, '', []):
                bits.append(f'{label}: {n[key]}')
        facts.append('; '.join(map(str, bits)))
    return facts


def validator(tables, query_cols):
    short = {t.split('.')[-1] for t in tables}

    def validate(obj):
        problems = []
        sql = re.sub(r'^```\w*\s*|```$', '', str(obj.get('sorgu', '')).strip()).strip().rstrip(';').strip()
        cleaned = clean_sql(sql)
        if not re.match(r'^\s*(WITH|SELECT)\b', cleaned, re.I):
            problems.append('sorgu SELECT veya WITH ile başlamalı')
        if ';' in cleaned:
            problems.append('tek bir ifade döndür')
        if WRITE.search(cleaned):
            problems.append('yeniden yazılmış sorgu yazma/DDL komutu içeremez')
        parsed = analyse(sql, 'oneri.sql') if not problems else []
        used = set(parsed[0]['sources']) if parsed else set()
        extra = sorted(t for t in used if t not in tables and t.split('.')[-1] not in short)
        if extra:
            problems.append('özgün sorguda olmayan tablo: ' + ', '.join(extra))
        idx = []
        for i, stmt in enumerate(obj.get('indeksler') or [], start=1):
            stmt = str(stmt).strip()
            m = INDEX.match(stmt)
            if not m:
                problems.append(f'{i}. indeks önerisi CREATE INDEX ad ON tablo (sütunlar) biçiminde değil')
                continue
            table = m.group(5).lower()
            if table not in tables and table.split('.')[-1] not in short:
                problems.append(f'{i}. indeks özgün sorguda olmayan tabloda: {table}')
            cols = [re.sub(r'\s+(ASC|DESC|NULLS\s+(FIRST|LAST))\b.*$', '', c.strip(), flags=re.I).strip('"').lower() for c in m.group(6).split(',')]
            cols += [c.strip().strip('"').lower() for c in (m.group(8) or '').split(',') if c.strip()]
            missing = [c for c in cols if re.fullmatch(r'\w+', c) and c not in query_cols]
            if missing:
                problems.append(f'{i}. indeks sorguda geçmeyen sütun içeriyor: {", ".join(missing)}')
            idx.append(stmt.rstrip(';') + ';')
        if problems:
            raise ValueError('; '.join(problems))
        return {'sql': sql, 'indexes': idx, 'summary': str(obj.get('ozet', '')).strip(),
                'changes': [str(x) for x in obj.get('degisiklikler', []) if x], 'risks': [str(x) for x in obj.get('riskler', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen kıdemli bir PostgreSQL performans uzmanısın. Sorguyu ve EXPLAIN ANALYZE düğüm özetini incele; sonucu DEĞİŞTİRMEDEN daha hızlı '
          'çalışacak tek bir SELECT yaz (gerekmiyorsa özgün sorguyu aynen döndür) ve gerekiyorsa CREATE INDEX önerileri ver. Yalnızca sorgudaki tabloları ve '
          'sütunları kullan; bilmediğin tablo boyutu veya veri dağılımı varsayımlarını riskler listesinde belirt. CONCURRENTLY kullanmayı tercih et. '
          'Yanıt şeması: {"ozet": "...", "sorgu": "SELECT ...", "indeksler": ["CREATE INDEX CONCURRENTLY ix_... ON tablo (sutun1, sutun2);"], '
          '"degisiklikler": ["..."], "riskler": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Yavaş PostgreSQL sorgusu için doğrulanmış yeniden yazım ve indeks önerisi üreten agent.')
    p.add_argument('--sql', default=str(here('ornek', 'yavas_sorgu.sql')))
    p.add_argument('--plan', default=str(here('ornek', 'explain.json')), help='EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) çıktısı')
    llm.add_arguments(p)
    args = p.parse_args()
    original = Path(args.sql).read_text(encoding='utf-8-sig').strip().rstrip(';')
    parsed = analyse(original, Path(args.sql).name)
    if not parsed or parsed[0]['kind'] != 'SELECT':
        raise ValueError('Dosyada tek bir SELECT sorgusu bulunamadı.')
    tables = set(parsed[0]['sources'])
    query_cols = {w.lower() for w in re.findall(r'[A-Za-z_]\w*', clean_sql(original))}
    facts = hotspots(args.plan)
    out = output_dir(args.output)
    user = f"Sorgu:\n```sql\n{original}\n```\n\nOkunan tablolar: {', '.join(sorted(tables))}\n\nEXPLAIN ANALYZE düğümleri:\n" + '\n'.join(facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, query_cols))
    if result is None:
        return
    write_text(out / 'onerilen_sorgu.sql', f'-- Model önerisi ({meta["model"]}); sonucu özgün sorguyla karşılaştırmadan kullanmayın.\n{result["sql"]};\n')
    if result['indexes']:
        write_text(out / 'indeksler.sql', '-- Önce test ortamında; yazma yükü ve disk etkisini ölçün.\n' + '\n'.join(result['indexes']) + '\n')
    write_json(out / 'optimizasyon.json', result)
    md = ['# Sorgu optimizasyonu', '', result['summary'], '', '## Önerilen sorgu', '', '```sql', result['sql'], '```']
    if result['indexes']:
        md += ['', '## İndeks önerileri', '', '```sql'] + result['indexes'] + ['```']
    if result['changes']:
        md += ['', '## Değişiklikler', ''] + [f'- {c}' for c in result['changes']]
    if result['risks']:
        md += ['', '## Riskler ve varsayımlar', ''] + [f'- {r}' for r in result['risks']]
    md += ['', '## Plan özeti (kodla çıkarıldı)', '', '```'] + facts + ['```',
           '', f"_Model: {meta['model']} · Doğrulama: önerilen sorguyu EXPLAIN ANALYZE ile çalıştırın ve sonuç kümesini özgün sorguyla (EXCEPT) karşılaştırın._"]
    write_text(out / 'optimizasyon.md', '\n'.join(md))
    print(f"[OK] Yeniden yazım ve {len(result['indexes'])} indeks önerisi doğrulandı. Çıktı: {out / 'optimizasyon.md'}")


if __name__ == '__main__':
    llm.run(main)
