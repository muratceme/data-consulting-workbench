"""Veritabanı performans blokları: SQL Server sorgu planı, bekleme istatistikleri, indeks kullanımı ve deadlock grafiği; PostgreSQL EXPLAIN,
pg_stat_statements ve tablo şişmesi; MySQL slow query log.
Veritabanına bağlanmaz; DBA’nın dışa aktardığı plan/DMV/istatistik dosyalarını okur."""
import json, re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fmt

SS, PG = 'SQL Server', 'PostgreSQL'
NS = '{http://schemas.microsoft.com/sqlserver/2004/07/showplan}'


def read_xml(path):
    raw = Path(path).read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        raw = raw.decode('utf-16').encode('utf-8')
        raw = re.sub(rb'encoding="utf-16"', b'encoding="utf-8"', raw, count=1, flags=re.I)
    if b'<!DOCTYPE' in raw[:5000] or b'<!ENTITY' in raw[:20000]:
        raise ValueError('DTD/ENTITY içeren XML işlenmez.')
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f'Plan XML okunamadı: {exc}') from None


@op('sqlserver-plan-analizi', title='SQL Server sorgu planını (.sqlplan) analiz et', domain='database', product=SS, tag='sorgu planı', kind='file',
    samples={'sorgu_plani.sqlplan': 'sorgu_plani.sqlplan'},
    description='SSMS’ten kaydedilen gerçek veya tahmini yürütme planında eksik indeks önerilerini (etki puanı ve CREATE INDEX taslağıyla), örtük tip dönüşümlerini, tempdb’ye taşan sıralama/hash’leri, key lookup’ları, büyük tablo taramalarını ve tahmin ile gerçekleşen satır sayısı arasındaki büyük sapmaları listeler.',
    input='.sqlplan / .xml yürütme planı', output='sonuc.csv (bulgular) + indeks-onerileri.sql', command='python main.py --input yavas_sorgu.sqlplan',
    expect={'bulgu': 7, 'eksik_indeks': 1})
def sqlplan(args):
    root = read_xml(args.input)
    out, ddl = [], []
    for stmt in root.iter(NS + 'StmtSimple'):
        text = (stmt.get('StatementText') or '').strip()
        short = re.sub(r'\s+', ' ', text)[:80]
        for g in stmt.iter(NS + 'MissingIndexGroup'):
            impact = float(g.get('Impact', 0))
            for mi in g.iter(NS + 'MissingIndex'):
                table = f"{mi.get('Schema', '')}.{mi.get('Table', '')}".strip('.')
                cols = defaultdict(list)
                for cg in mi.iter(NS + 'ColumnGroup'):
                    cols[cg.get('Usage')] += [c.get('Name') for c in cg.iter(NS + 'Column')]
                key = cols['EQUALITY'] + cols['INEQUALITY']
                name = 'IX_' + table.split('.')[-1].strip('[]') + '_' + '_'.join(c.strip('[]') for c in key)
                ddl.append(f"-- Etki %{impact:g} · {short}\nCREATE NONCLUSTERED INDEX [{name}] ON {table} ({', '.join(key)})" +
                           (f"\nINCLUDE ({', '.join(cols['INCLUDE'])})" if cols['INCLUDE'] else '') + ';')
                out.append({'onem': 'yüksek' if impact >= 50 else 'orta', 'bulgu': f'Eksik indeks önerisi (etki %{impact:g}): {table} ({", ".join(key)})', 'dugum': ''})
        for w in stmt.iter(NS + 'PlanAffectingConvert'):
            out.append({'onem': 'yüksek', 'bulgu': f"Örtük dönüşüm indeks aramasını engelliyor: {w.get('Expression')}", 'dugum': ''})
        for rel in stmt.iter(NS + 'RelOp'):
            op_name, node = rel.get('PhysicalOp', ''), rel.get('NodeId', '')
            est = float(rel.get('EstimateRows', 0) or 0)
            counters = [c for c in rel.findall(f'{NS}RunTimeInformation/{NS}RunTimeCountersPerThread')]
            actual = sum(float(c.get('ActualRows', 0)) for c in counters) if counters else None
            execs = sum(float(c.get('ActualExecutions', 0)) for c in counters) if counters else None
            if rel.find(f'{NS}Warnings/{NS}SpillToTempDb') is not None:
                out.append({'onem': 'yüksek', 'bulgu': f'{op_name} tempdb’ye taşıyor (bellek yetersiz / yanlış tahmin)', 'dugum': node})
            if op_name == 'Key Lookup':
                out.append({'onem': 'orta', 'bulgu': f'Key Lookup ({execs and int(execs) or "?"} kez) — kapsayan indeks (INCLUDE) değerlendirin', 'dugum': node})
            if op_name in ('Table Scan', 'Clustered Index Scan', 'Index Scan') and float(rel.get('TableCardinality', 0) or 0) >= 1_000_000:
                out.append({'onem': 'orta', 'bulgu': f"{op_name}: {int(float(rel.get('TableCardinality')))} satırlık tablo taranıyor", 'dugum': node})
            if actual is not None and est and (actual / max(est * max(execs or 1, 1), 1) >= 10 or (est * (execs or 1)) / max(actual, 1) >= 10) and op_name != 'Key Lookup':
                out.append({'onem': 'orta', 'bulgu': f'{op_name}: tahmin {est:g} satır, gerçekleşen {actual:g} — istatistikleri güncelleyin / parametre koklama', 'dugum': node})
    sev = Counter(o['onem'] for o in out)
    return Result(f"{len(out)} bulgu ({sev['yüksek']} yüksek); {len(ddl)} eksik indeks önerisi", ['onem', 'bulgu', 'dugum'], out,
                  {'bulgu': len(out), 'yuksek': sev['yüksek'], 'eksik_indeks': len(ddl)}, files={'indeks-onerileri.sql': '\n\n'.join(ddl) or '-- Öneri yok'},
                  notes=['Eksik indeks önerileri optimizer tahminidir; mevcut indekslerle çakışmasını ve yazma maliyetini kontrol etmeden oluşturmayın.'])


def walk_pg(node, depth=0):
    yield node, depth
    for child in node.get('Plans', []) or []:
        yield from walk_pg(child, depth + 1)


@op('postgres-explain-analizi', title='PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) çıktısını analiz et', domain='database', product=PG, tag='EXPLAIN', kind='file',
    samples={'explain.json': 'explain.json'},
    description='EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) çıktısını ağaç olarak düzleştirir; en çok süre harcayan düğümleri, filtreyle çok satır atan sıralı taramaları (indeks adayı), diske taşan sıralamaları, satır tahmin hatalarını ve çok döngülü nested loop’ları işaretler.',
    input='EXPLAIN JSON çıktısı (.json)', output='sonuc.csv (düğümler) + bulgular', command='python main.py --input plan.json',
    expect={'dugum': 5, 'bulgu': 5})
def pg_explain(args):
    try:
        data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'EXPLAIN JSON okunamadı: {exc}') from None
    item = data[0] if isinstance(data, list) else data
    if 'Plan' not in item:
        raise ValueError('JSON’da "Plan" yok; EXPLAIN (FORMAT JSON) çıktısı verin.')
    rows, findings = [], []
    for n, depth in walk_pg(item['Plan']):
        kind = n.get('Node Type', '')
        rel = n.get('Relation Name', '')
        est, act, loops = n.get('Plan Rows', 0), n.get('Actual Rows'), n.get('Actual Loops', 1) or 1
        rows.append({'derinlik': depth, 'dugum': ('  ' * depth) + kind, 'tablo': rel, 'tahmin_satir': est, 'gercek_satir': act if act is not None else '',
                     'dongu': loops, 'sure_ms': n.get('Actual Total Time', '')})
        if kind == 'Seq Scan' and n.get('Rows Removed by Filter', 0) > 10 * max(act or 0, 1) and n.get('Rows Removed by Filter', 0) > 100_000:
            findings.append(['yüksek', f"{rel}: sıralı tarama {n['Rows Removed by Filter']} satırı filtreyle atıyor ({n.get('Filter', '')}) — indeks adayı"])
        if n.get('Sort Space Type') == 'Disk':
            findings.append(['yüksek', f"Sıralama diske taşıyor ({n.get('Sort Space Used', '?')} kB) — work_mem veya sorgu daraltma"])
        if act is not None and est and max(act / est, est / max(act, 1)) >= 10 and kind not in ('Hash',):
            findings.append(['orta', f'{kind}{" " + rel if rel else ""}: tahmin {est}, gerçek {act} satır — ANALYZE / istatistik hedefi'])
        if kind == 'Nested Loop' and loops > 10_000:
            findings.append(['orta', f'Nested Loop {loops} kez döngüde — hash/merge join uygun olabilir'])
    total = item.get('Execution Time')
    return Result(f"{len(rows)} düğüm, toplam {total} ms; {len(findings)} bulgu", list(rows[0]), rows,
                  {'dugum': len(rows), 'bulgu': len(findings), 'calisma_ms': total}, tables=[('Bulgular', ['Önem', 'Bulgu'], findings)])


@op('pg-stat-statements', title='pg_stat_statements: en pahalı sorgular', domain='database', product=PG, tag='yavaş sorgu',
    samples={'pg_stat_statements.csv': 'pg_stat_statements.csv'},
    args=[arg('--top', type=int, default=10)],
    description='pg_stat_statements dışa aktarımından toplam süreye göre en pahalı sorguları, toplam yükteki paylarını, çağrı başı ortalamayı, önbellek isabet oranını ve satır başına maliyeti hesaplar; düşük isabet oranlı ve SELECT * içeren sorguları işaretler.',
    input='pg_stat_statements CSV (\\copy ile)', output='sonuc.csv (sıralı sorgular)', command='python main.py --input pgss.csv --top 20',
    expect={'sorgu': 6, 'dusuk_isabet': 3})
def pgss(columns, rows, args):
    for c in ('query', 'calls', 'total_exec_time'):
        need(c, columns, 'pg_stat_statements')
    tot, calls = numbers(rows, 'total_exec_time'), numbers(rows, 'calls')
    hit = numbers(rows, 'shared_blks_hit') if 'shared_blks_hit' in columns else [None] * len(rows)
    read = numbers(rows, 'shared_blks_read') if 'shared_blks_read' in columns else [None] * len(rows)
    grand = sum(t or 0 for t in tot)
    out = []
    for r, t, c, h, rd in zip(rows, tot, calls, hit, read):
        ratio = h / (h + rd) * 100 if h is not None and rd is not None and (h + rd) else None
        flags = []
        if ratio is not None and ratio < 90:
            flags.append('düşük önbellek isabeti')
        if re.search(r'select\s+\*', r['query'], re.I):
            flags.append('SELECT *')
        if re.search(r'where\s+(lower|upper|date|coalesce)\(', r['query'], re.I):
            flags.append('WHERE’de fonksiyon (ifade indeksi?)')
        out.append({'sorgu': r['query'][:200], 'cagri': int(c or 0), 'toplam_ms': round(t or 0, 1), 'pay_%': round(100 * (t or 0) / grand, 1) if grand else 0,
                    'ortalama_ms': round((t or 0) / c, 2) if c else '', 'isabet_%': round(ratio, 1) if ratio is not None else '', 'uyari': ', '.join(flags)})
    out.sort(key=lambda o: -o['toplam_ms'])
    top = out[:args.top]
    return Result(f"{len(out)} sorgu; ilk 3 sorgu toplam sürenin %{sum(o['pay_%'] for o in out[:3]):.1f}’i", list(out[0]), top,
                  {'sorgu': len(out), 'dusuk_isabet': sum('düşük önbellek' in o['uyari'] for o in out), 'toplam_saat': round(grand / 3_600_000, 2)})


BENIGN = {'SLEEP_TASK', 'BROKER_TASK_STOP', 'LAZYWRITER_SLEEP', 'SQLTRACE_BUFFER_FLUSH', 'XE_TIMER_EVENT', 'XE_DISPATCHER_WAIT', 'REQUEST_FOR_DEADLOCK_SEARCH', 'CHECKPOINT_QUEUE',
          'LOGMGR_QUEUE', 'FT_IFTS_SCHEDULER_IDLE_WAIT', 'BROKER_TO_FLUSH', 'BROKER_EVENTHANDLER', 'DIRTY_PAGE_POLL', 'HADR_FILESTREAM_IOMGR_IOCOMPLETION', 'SP_SERVER_DIAGNOSTICS_SLEEP',
          'SQLTRACE_INCREMENTAL_FLUSH_SLEEP', 'WAITFOR', 'CLR_AUTO_EVENT', 'ONDEMAND_TASK_QUEUE', 'QDS_PERSIST_TASK_MAIN_LOOP_SLEEP', 'QDS_CLEANUP_STALE_QUERIES_TASK_MAIN_LOOP_SLEEP'}
CATEGORY = [(r'^PAGEIOLATCH', 'Disk okuma (veri sayfası bellekte yok)', 'Eksik indeks/tarama, bellek yetersizliği veya yavaş disk'),
            (r'^(CXPACKET|CXCONSUMER|CXSYNC)', 'Paralellik', 'MAXDOP / cost threshold for parallelism ayarı, büyük taramalar'),
            (r'^LCK_M_', 'Kilit bekleme (blocking)', 'Uzun işlemler, eksik indeks, izolasyon düzeyi'),
            (r'^SOS_SCHEDULER_YIELD', 'CPU baskısı', 'CPU yoğun sorgular, yüksek sinyal beklemesi'),
            (r'^WRITELOG', 'İşlem günlüğü yazma', 'Log diski gecikmesi, çok küçük işlemler'),
            (r'^ASYNC_NETWORK_IO', 'İstemci sonucu yavaş okuyor', 'Uygulama satır satır işliyor veya ağ'),
            (r'^RESOURCE_SEMAPHORE', 'Bellek izni (memory grant) bekleme', 'Büyük sıralama/hash, yanlış tahmin'),
            (r'^PAGELATCH', 'Bellekte sayfa çekişmesi', 'tempdb veya sıralı anahtar ekleme (son sayfa)')]


@op('sqlserver-bekleme-analizi', title='SQL Server bekleme istatistiklerini yorumla', domain='database', product=SS, tag='wait stats',
    samples={'bekleme_istatistikleri.csv': 'bekleme_istatistikleri.csv'},
    description='sys.dm_os_wait_stats dışa aktarımından zararsız arka plan beklemelerini ayıklar; kalan beklemeleri pay, görev başı ortalama ve sinyal (CPU) oranıyla sıralar ve her birini kategori (disk, paralellik, kilit, CPU, log, bellek) ve olası nedenle açıklar.',
    input='sys.dm_os_wait_stats CSV’si', output='sonuc.csv', command='python main.py --input wait_stats.csv',
    expect={'anlamli_bekleme': 7, 'elenen': 3, 'en_buyuk': 'PAGEIOLATCH_SH'})
def waits(columns, rows, args):
    for c in ('wait_type', 'wait_time_ms'):
        need(c, columns, 'wait stats')
    wt, cnt = numbers(rows, 'wait_time_ms'), numbers(rows, 'waiting_tasks_count') if 'waiting_tasks_count' in columns else [None] * len(rows)
    sig = numbers(rows, 'signal_wait_time_ms') if 'signal_wait_time_ms' in columns else [None] * len(rows)
    kept = [(r['wait_type'].strip(), w or 0, c, s) for r, w, c, s in zip(rows, wt, cnt, sig) if r['wait_type'].strip() not in BENIGN and (w or 0) > 0]
    total = sum(w for _, w, _, _ in kept)
    out = []
    for name, w, c, s in sorted(kept, key=lambda x: -x[1]):
        cat = next(((a, b) for rx, a, b in CATEGORY if re.match(rx, name)), ('Diğer', ''))
        out.append({'bekleme': name, 'sure_sn': round(w / 1000, 1), 'pay_%': round(100 * w / total, 1), 'ort_ms': round(w / c, 2) if c else '',
                    'sinyal_%': round(100 * s / w, 1) if s is not None and w else '', 'kategori': cat[0], 'olasi_neden': cat[1]})
    return Result(f"{len(out)} anlamlı bekleme türü; en büyüğü {out[0]['bekleme']} (%{out[0]['pay_%']})" if out else 'Anlamlı bekleme yok', list(out[0]) if out else [], out,
                  {'anlamli_bekleme': len(out), 'elenen': len(rows) - len(kept), 'en_buyuk': out[0]['bekleme'] if out else ''},
                  notes=['DMV değerleri sunucu yeniden başlatılalı beri birikir; iki ölçüm arasındaki farkı analiz etmek daha doğrudur.'])


@op('indeks-kullanim-analizi', title='Kullanılmayan ve tekrarlayan indeksleri bul', domain='database', product=SS, tag='indeks', samples={'index_kullanimi.csv': 'index_kullanimi.csv'},
    args=[arg('--min-reads', type=int, default=100, help='Bunun altında okunan indeks “kullanılmıyor” sayılır')],
    description='İndeks tanımı ve kullanım istatistiği dışa aktarımından (sys.dm_db_index_usage_stats + sütunlar) hiç/az okunan ama sürekli yazılan indeksleri, aynı anahtarla başlayan (kapsanan/örtüşen) indeksleri ve kaldırılırsa kazanılacak alanı listeler. Birincil anahtarları önermez.',
    input='CSV (tablo, index, sutunlar, include, okuma, yazma, boyut_mb)', output='sonuc.csv (öneriler) + drop taslağı', command='python main.py --min-reads 1000',
    expect={'kullanilmayan': 3, 'ortusen': 1})
def indexes(columns, rows, args):
    for c in ('tablo', 'index', 'sutunlar', 'okuma', 'yazma'):
        need(c, columns, 'indeks dosyası')
    reads, writes = numbers(rows, 'okuma'), numbers(rows, 'yazma')
    size = numbers(rows, 'boyut_mb') if 'boyut_mb' in columns else [0] * len(rows)
    idx = [{'tablo': r['tablo'].strip(), 'index': r['index'].strip(), 'key': [c.strip().lower() for c in r['sutunlar'].split(',') if c.strip()],
            'include': {c.strip().lower() for c in r.get('include', '').split(',') if c.strip()}, 'okuma': rd or 0, 'yazma': wr or 0, 'mb': mb or 0}
           for r, rd, wr, mb in zip(rows, reads, writes, size)]
    out = []
    for i in idx:
        if i['index'].upper().startswith('PK'):
            continue
        if i['okuma'] < args.min_reads and i['yazma'] > i['okuma']:
            out.append({'tablo': i['tablo'], 'index': i['index'], 'oneri': 'kaldırılabilir (kullanılmıyor)', 'neden': f"okuma {int(i['okuma'])}, yazma {int(i['yazma'])}", 'kazanc_mb': i['mb']})
            continue
        for j in idx:
            if j is i or j['tablo'] != i['tablo'] or j['index'].upper().startswith('PK'):
                continue
            # j covers i when i's key is a prefix of j's key and i's included columns are available in j.
            covers = len(j['key']) >= len(i['key']) and j['key'][:len(i['key'])] == i['key'] and i['include'] <= (j['include'] | set(j['key']))
            if covers and (len(j['key']), j['index']) > (len(i['key']), i['index']):  # for exact duplicates only one of the pair is reported
                out.append({'tablo': i['tablo'], 'index': i['index'], 'oneri': 'örtüşüyor', 'neden': f"{j['index']} ({', '.join(j['key'])}) bu indeksi kapsıyor", 'kazanc_mb': i['mb']})
                break
    drops = '\n'.join(f"-- {o['neden']}\n-- DROP INDEX [{o['index']}] ON {o['tablo']};" for o in out)
    return Result(f"{sum(o['oneri'].startswith('kaldır') for o in out)} kullanılmayan, {sum(o['oneri'] == 'örtüşüyor' for o in out)} örtüşen indeks; {fmt(sum(o['kazanc_mb'] for o in out))} MB",
                  ['tablo', 'index', 'oneri', 'neden', 'kazanc_mb'], out,
                  {'kullanilmayan': sum(o['oneri'].startswith('kaldır') for o in out), 'ortusen': sum(o['oneri'] == 'örtüşüyor' for o in out), 'kazanc_mb': sum(o['kazanc_mb'] for o in out)},
                  files={'drop-taslagi.sql': drops or '-- Öneri yok'},
                  notes=['Kullanım istatistiği sunucu yeniden başlayınca sıfırlanır; ay sonu/yıl sonu raporlarında kullanılan indeksleri gözden kaçırmamak için yeterince uzun bir dönemi inceleyin.'])


def fingerprint(sql):
    s = re.sub(r'/\*.*?\*/|--[^\n]*|#[^\n]*', ' ', sql, flags=re.S).lower()
    s = re.sub(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"", '?', s)
    s = re.sub(r'\b\d+(?:\.\d+)?\b', '?', s)
    s = re.sub(r'\bin\s*\(\s*\?(?:\s*,\s*\?)*\s*\)', 'in (?+)', s)
    return re.sub(r'\s+', ' ', s).strip().rstrip(';').strip()


@op('mysql-yavas-log', title='MySQL/MariaDB slow query log analizi', domain='database', product='MySQL', tag='slow log', kind='file', samples={'mysql_slow.log': 'mysql_slow.log'},
    args=[arg('--ratio', type=float, default=1000.0, help='Taranan/dönen satır oranı bu değeri aşarsa indeks şüphesi'), arg('--top', type=int, default=30)],
    description='slow_query_log dosyasındaki sorguları parmak izine (sabitler ve IN listeleri ? olarak) göre gruplar; toplam/ortalama/en uzun süreyi, kilit bekleme payını ve taranan/dönen satır oranını hesaplar. Oranı yüksek sorguları eksik indeks, kilit beklemesi baskın olanları çakışma şüphesiyle işaretler; pt-query-digest kurulamayan ortamlar için.',
    input='slow query log (.log)', output='sonuc.csv (parmak izi bazında)', command='python main.py --input /var/lib/mysql/db-slow.log --top 20',
    expect={'sorgu': 6, 'parmak_izi': 4, 'toplam_sure': 16.7, 'indeks_supheli': 3, 'kilit_agirlikli': 1})
def slowlog(args):
    entries, cur = [], None
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        m = re.match(r'#\s*Query_time:\s*([\d.]+)\s+Lock_time:\s*([\d.]+)\s+Rows_sent:\s*(\d+)\s+Rows_examined:\s*(\d+)', line)
        if m:
            cur = {'t': float(m.group(1)), 'lock': float(m.group(2)), 'sent': int(m.group(3)), 'exam': int(m.group(4)), 'sql': []}
            entries.append(cur)
        elif line.startswith('#'):
            cur = None if cur and cur['sql'] else cur
        elif cur is not None and not re.match(r'\s*(SET\s+timestamp=|use\s+\w+;)', line, re.I) and line.strip():
            cur['sql'].append(line.strip())
    entries = [e for e in entries if e['sql']]
    if not entries:
        raise ValueError('Slow log kaydı bulunamadı (# Query_time: … satırları bekleniyor).')
    groups = {}
    for e in entries:
        fp = fingerprint(' '.join(e['sql']))
        g = groups.setdefault(fp, {'parmak_izi': fp[:300], 'adet': 0, 'toplam_sn': 0.0, 'en_uzun_sn': 0.0, 'kilit_sn': 0.0, 'taranan': 0, 'donen': 0})
        g['adet'] += 1
        g['toplam_sn'] += e['t']
        g['en_uzun_sn'] = max(g['en_uzun_sn'], e['t'])
        g['kilit_sn'] += e['lock']
        g['taranan'] += e['exam']
        g['donen'] += e['sent']
    out = []
    for g in sorted(groups.values(), key=lambda g: -g['toplam_sn']):
        ratio = g['taranan'] / max(g['donen'], 1)
        warn = []
        if ratio > args.ratio:
            warn.append('taranan/dönen oranı yüksek: indeks eksik olabilir')
        if g['kilit_sn'] > 0.5 * g['toplam_sn']:
            warn.append('sürenin çoğu kilit beklemesi')
        out.append({**g, 'toplam_sn': round(g['toplam_sn'], 3), 'ort_sn': round(g['toplam_sn'] / g['adet'], 3), 'kilit_sn': round(g['kilit_sn'], 3), 'tarama_orani': round(ratio), 'uyari': '; '.join(warn)})
    total = sum(e['t'] for e in entries)
    return Result(f'{len(entries)} yavaş sorgu, {len(groups)} parmak izi; toplam {total:.1f} sn', ['parmak_izi', 'adet', 'toplam_sn', 'ort_sn', 'en_uzun_sn', 'kilit_sn', 'taranan', 'donen', 'tarama_orani', 'uyari'], out[:args.top],
                  {'sorgu': len(entries), 'parmak_izi': len(groups), 'toplam_sure': round(total, 2), 'indeks_supheli': sum('indeks' in o['uyari'] for o in out),
                   'kilit_agirlikli': sum('kilit' in o['uyari'] for o in out)},
                  notes=['Parmak izi sabitleri ? ile değiştirir; aynı sorgunun farklı parametreli çalışmaları tek satırda toplanır. EXPLAIN ile planı doğrulayın.'])


@op('pg-tablo-sisme', title='PostgreSQL tablo şişmesi, vacuum ve sıralı tarama kontrolü', domain='database', product=PG, tag='vacuum', samples={'pg_stat_user_tables.csv': 'pg_stat_user_tables.csv'},
    args=[arg('--as-of', default='2026-10-16'), arg('--dead-pct', type=float, default=20.0), arg('--min-dead', type=int, default=10000), arg('--vacuum-days', type=int, default=7)],
    description='pg_stat_user_tables dışa aktarımından ölü satır oranı yüksek (VACUUM gerektiren), autovacuum’un uzun süredir uğramadığı, hiç istatistiği toplanmamış ve büyük olup indeks yerine sıralı taramayla okunan tabloları bulur; her tablo için önerilen bakım işlemini yazar.',
    input='pg_stat_user_tables CSV’si', output='sonuc.csv (tablo bazında bulgular)', command='python main.py --input pg_stats.csv --as-of 2026-12-01 --dead-pct 10',
    expect={'tablo': 5, 'vacuum_gerekli': 1, 'indeks_supheli': 1, 'istatistiksiz': 1, 'sorunlu_tablo': 3})
def bloat(columns, rows, args):
    for c in ('relname', 'n_live_tup', 'n_dead_tup', 'seq_scan', 'idx_scan'):
        need(c, columns, 'pg_stat_user_tables')
    as_of = tabular.parse_date(args.as_of)[0]
    live, dead, seq, idx = (numbers(rows, c) for c in ('n_live_tup', 'n_dead_tup', 'seq_scan', 'idx_scan'))
    out = []
    for r, lv, dd, sq, ix in zip(rows, live, dead, seq, idx):
        lv, dd, sq, ix = lv or 0, dd or 0, sq or 0, ix or 0
        ratio = 100 * dd / (lv + dd) if lv + dd else 0.0
        issues = []
        if ratio >= args.dead_pct and dd >= args.min_dead:
            issues.append('VACUUM (ANALYZE) gerekli')
        last_vac = tabular.parse_date(r.get('last_autovacuum', '') or r.get('last_vacuum', '') or '')
        if ratio >= args.dead_pct / 2 and dd >= args.min_dead and (not last_vac or (as_of - last_vac[0]).days > args.vacuum_days):
            issues.append(f'autovacuum {args.vacuum_days} günden uzun süredir çalışmamış: eşikleri tablo bazında düşürün')
        if not (r.get('last_autoanalyze', '').strip() or r.get('last_analyze', '').strip()):
            issues.append('istatistik hiç toplanmamış: ANALYZE')
        if lv >= 100000 and sq >= 100 and sq > 10 * ix:
            issues.append('büyük tablo çoğunlukla sıralı taranıyor: indeks eksik olabilir')
        out.append({'tablo': f"{r.get('schemaname', '').strip()}.{r['relname'].strip()}".strip('.'), 'canli': int(lv), 'olu': int(dd), 'olu_%': round(ratio, 1),
                    'seq_scan': int(sq), 'idx_scan': int(ix), 'son_autovacuum': last_vac[0].isoformat(sep=' ') if last_vac else '', 'bulgu': '; '.join(issues)})
    out.sort(key=lambda o: (not o['bulgu'], -o['olu']))
    return Result(f'{len(out)} tablo; {sum(bool(o["bulgu"]) for o in out)} tabloda bakım önerisi', list(out[0]), out,
                  {'tablo': len(out), 'vacuum_gerekli': sum('VACUUM' in o['bulgu'] for o in out), 'indeks_supheli': sum('indeks' in o['bulgu'] for o in out),
                   'istatistiksiz': sum('istatistik' in o['bulgu'] for o in out), 'sorunlu_tablo': sum(bool(o['bulgu']) for o in out), 'toplam_olu_satir': sum(o['olu'] for o in out)},
                  notes=['İstatistikler son pg_stat_reset’ten beri birikir; seq_scan/idx_scan oranını sıfırlama tarihine göre yorumlayın.'])


@op('sqlserver-deadlock', title='SQL Server deadlock grafiğini (XML) çözümle', domain='database', product=SS, tag='deadlock', kind='file', samples={'deadlock.xml': 'deadlock.xml'},
    description='system_health veya Extended Events’ten alınan xml_deadlock_report (bir veya birden çok <deadlock>) içinden her süreç için kurban olup olmadığını, oturum, uygulama, kilit modu, tuttuğu ve beklediği nesne/indeksi ve çalıştırdığı sorgu ya da yordamı çıkarır; en sık deadlock’a karışan nesneleri özetler.',
    input='Deadlock XML (.xdl/.xml)', output='sonuc.csv (deadlock × süreç)', command='python main.py --input system_health_deadlocks.xml',
    expect={'deadlock': 2, 'surec': 4, 'kurban': 2, 'en_sik_nesne': 'Satis.dbo.Stok'})
def deadlock(args):
    root = read_xml(args.input)
    graphs = [root] if root.tag.split('}')[-1] == 'deadlock' else [e for e in root.iter() if e.tag.split('}')[-1] == 'deadlock']
    if not graphs:
        raise ValueError('<deadlock> öğesi bulunamadı.')
    out, objects = [], Counter()
    for n, g in enumerate(graphs, 1):
        victims = {v.get('id') for v in g.iter() if v.tag.endswith('victimProcess')} | ({g.get('victim')} if g.get('victim') else set())
        holds, waits = defaultdict(set), defaultdict(set)
        involved = set()
        for res in g.iter():
            obj = res.get('objectname')
            if not obj or res.tag.split('}')[-1] in ('process', 'frame'):
                continue
            label = obj + (f" ({res.get('indexname')})" if res.get('indexname') else '')
            involved.add(obj)
            for o in res.iter():
                tag = o.tag.split('}')[-1]
                if tag == 'owner':
                    holds[o.get('id')].add(f"{label} [{o.get('mode', '')}]")
                elif tag == 'waiter':
                    waits[o.get('id')].add(f"{label} [{o.get('mode', '')}]")
        objects.update(involved)
        for p_ in (e for e in g.iter() if e.tag.split('}')[-1] == 'process'):
            pid = p_.get('id')
            frames = [f for f in p_.iter() if f.tag.split('}')[-1] == 'frame' and (f.text or '').strip()]
            buf = next((e.text for e in p_.iter() if e.tag.split('}')[-1] == 'inputbuf' and e.text), '')
            sql = (frames[0].text if frames else buf) or ''
            out.append({'deadlock': n, 'surec': pid, 'spid': p_.get('spid', ''), 'kurban': 'EVET' if pid in victims else 'hayır', 'uygulama': p_.get('clientapp', ''),
                        'kullanici': p_.get('loginname', ''), 'kilit_modu': p_.get('lockMode', ''), 'izolasyon': p_.get('isolationlevel', ''),
                        'tuttugu': '; '.join(sorted(holds[pid])), 'bekledigi': '; '.join(sorted(waits[pid])),
                        'yordam': frames[0].get('procname', '') if frames else '', 'sorgu': re.sub(r'\s+', ' ', sql).strip()[:300]})
    top = objects.most_common(1)[0][0] if objects else ''
    return Result(f'{len(graphs)} deadlock, {len(out)} süreç; en sık nesne {top or "-"}', list(out[0]) if out else ['deadlock'], out,
                  {'deadlock': len(graphs), 'surec': len(out), 'kurban': sum(o['kurban'] == 'EVET' for o in out), 'en_sik_nesne': top},
                  tables=[('Deadlock’a karışan nesneler', ['Nesne', 'Deadlock'], objects.most_common())],
                  notes=['Kalıcı çözüm genelde nesnelere aynı sırada erişmek, işlemi kısaltmak ve bekleyen sorguya uygun indeks eklemektir; READ COMMITTED SNAPSHOT okuma–yazma çakışmalarını azaltır.'])


def bracket_cols(v):
    return [c.strip().strip('[]') for c in (v or '').split(',') if c.strip()]


@op('eksik-indeks-onerisi', title='SQL Server eksik indeks DMV’sinden CREATE INDEX önerileri', domain='database', product=SS, tag='eksik indeks', samples={'eksik_indeksler.csv': 'eksik_indeksler.csv'},
    args=[arg('--min-score', type=float, default=1000.0, help='Bu iyileştirme puanının altındaki önerileri “düşük etki” say')],
    description='sys.dm_db_missing_index_details/group_stats dışa aktarımından her öneri için iyileştirme puanını (ortalama maliyet × etki % × arama sayısı) hesaplar, eşitlik ve eşitsizlik sütunlarıyla INCLUDE’u doğru sırada içeren CREATE INDEX komutunu yazar; aynı tablodaki önerilerden anahtarı diğerinin önekiyle aynı olanları birleştirilebilir diye işaretler. DMV önerilerini körü körüne uygulamadan önce elemek için.',
    input='Eksik indeks DMV dışa aktarımı (tablo, equality/inequality/included, seeks, maliyet, etki)', output='sonuc.csv (puanlı öneriler) + eksik_indeksler.sql', command='python main.py --input missing_idx.csv --min-score 5000',
    expect={'oneri': 5, 'onerilen': 4, 'birlestirilebilir': 1})
def missing_index(columns, rows, args):
    for c in ('tablo', 'equality_columns', 'inequality_columns', 'included_columns', 'user_seeks', 'avg_total_user_cost', 'avg_user_impact'):
        need(c, columns, 'eksik indeks listesi')
    seeks, cost, impact = numbers(rows, 'user_seeks'), numbers(rows, 'avg_total_user_cost'), numbers(rows, 'avg_user_impact')
    items = []
    for r, s, c, i in zip(rows, seeks, cost, impact):
        eq, ineq, inc = bracket_cols(r['equality_columns']), bracket_cols(r['inequality_columns']), bracket_cols(r['included_columns'])
        table = r['tablo'].strip()
        short = table.split('.')[-1].strip('[]')
        name = 'IX_' + short + '_' + '_'.join(eq + ineq)
        sql = f'CREATE INDEX [{name}] ON {table} ({", ".join(f"[{x}]" for x in eq + ineq)})' + (f' INCLUDE ({", ".join(f"[{x}]" for x in inc)})' if inc else '') + ';'
        items.append({'tablo': table, 'anahtar': ', '.join(eq + ineq), 'include': ', '.join(inc), 'puan': round((s or 0) * (c or 0) * (i or 0) / 100, 1), 'sql': sql, '_key': eq + ineq})
    for a in items:
        a['not'] = ''
        if a['puan'] < args.min_score:
            a['not'] = 'düşük etki'
        for b in items:
            if a is not b and a['tablo'] == b['tablo'] and len(a['_key']) < len(b['_key']) and b['_key'][:len(a['_key'])] == a['_key']:
                a['not'] = (a['not'] + '; ' if a['not'] else '') + f'birleştirilebilir: {b["anahtar"]} önerisinin öneki'
    items.sort(key=lambda o: -o['puan'])
    for o in items:
        o.pop('_key')
    good = [o for o in items if 'düşük etki' not in o['not'] and 'birleştirilebilir' not in o['not']]
    return Result(f'{len(items)} öneri; {sum("düşük etki" not in o["not"] for o in items)} öneri eşik üstünde, {sum("birleştirilebilir" in o["not"] for o in items)} birleştirilebilir', list(items[0]), items,
                  {'oneri': len(items), 'onerilen': sum('düşük etki' not in o['not'] for o in items), 'birlestirilebilir': sum('birleştirilebilir' in o['not'] for o in items)},
                  files={'eksik_indeksler.sql': '-- Uygulamadan önce mevcut indekslerle örtüşmeyi ve yazma yükünü kontrol edin.\n' + '\n'.join(o['sql'] for o in good) + '\n'},
                  notes=['DMV önerileri sunucu yeniden başlayınca sıfırlanır ve mevcut indeksleri dikkate almaz; indeks-kullanim-analizi bloğuyla birlikte değerlendirin.'])


def pids(v):
    return [int(x) for x in re.findall(r'\d+', v or '')]


@op('pg-kilit-agaci', title='PostgreSQL kilit bekleme ağacı ve kök engelleyiciler', domain='database', product=PG, tag='kilit', samples={'pg_aktivite.csv': 'pg_aktivite.csv'},
    description='pg_stat_activity ve pg_blocking_pids() çıktısından kim kimi bekletiyor ağacını kurar; zincirin başındaki kök engelleyicileri, her birinin dolaylı olarak beklettiği oturum sayısını ve “idle in transaction” durumunda kilit tutan oturumları işaretler; gerekirse sonlandırma komutunu yazar. Kilit kaynaklı yavaşlamada ilk müdahale için.',
    input='Oturum listesi (pid, usename, state, blocked_by, query)', output='sonuc.csv (kök engelleyiciler) + ağaç', command='python main.py --input pg_locks_snapshot.csv',
    expect={'oturum': 7, 'bekleyen': 4, 'kok_engelleyici': 2, 'idle_in_tx_kok': 1})
def lock_tree(columns, rows, args):
    for c in ('pid', 'blocked_by'):
        need(c, columns, 'oturum listesi')
    info = {int(r['pid']): r for r in rows if r['pid'].strip().isdigit()}
    blockers = {p: pids(r['blocked_by']) for p, r in info.items()}
    children = defaultdict(list)
    for p, bs in blockers.items():
        for b in bs:
            children[b].append(p)
    waiting = [p for p, bs in blockers.items() if bs]
    roots = sorted(p for p in children if not blockers.get(p))

    def below(p, seen=None):
        seen = seen if seen is not None else set()
        for c in children[p]:
            if c not in seen:
                seen.add(c)
                below(c, seen)
        return seen
    tree = []

    def draw(p, depth):
        r = info.get(p, {})
        tree.append(['  ' * depth + str(p), r.get('usename', ''), r.get('state', ''), (r.get('query', '') or '')[:80]])
        for c in sorted(children[p]):
            draw(c, depth + 1)
    out = []
    for p in roots:
        r = info.get(p, {})
        idle = 'idle in transaction' in (r.get('state', '') or '').lower()
        out.append({'pid': p, 'kullanici': r.get('usename', ''), 'durum': r.get('state', ''), 'bekletilen': len(below(p)), 'sorgu': (r.get('query', '') or '')[:200],
                    'oneri': f'SELECT pg_terminate_backend({p});  -- işlem açık bırakılmış' if idle else 'sorgunun bitmesini bekleyin veya pg_cancel_backend ile iptal edin'})
        draw(p, 0)
    out.sort(key=lambda o: -o['bekletilen'])
    return Result(f'{len(info)} oturum; {len(waiting)} oturum bekliyor, {len(roots)} kök engelleyici', ['pid', 'kullanici', 'durum', 'bekletilen', 'sorgu', 'oneri'], out,
                  {'oturum': len(info), 'bekleyen': len(waiting), 'kok_engelleyici': len(roots), 'idle_in_tx_kok': sum('idle in transaction' in o['durum'].lower() for o in out)},
                  tables=[('Bekleme ağacı', ['PID', 'Kullanıcı', 'Durum', 'Sorgu'], tree)],
                  notes=['Anlık görüntüdür; birkaç saniye arayla iki kez alıp aynı kök engelleyicinin sürdüğünü doğrulayın. idle_in_transaction_session_timeout ayarını değerlendirin.'])
