"""Veritabanı performans blokları: SQL Server sorgu planı, bekleme istatistikleri, indeks kullanımı ve deadlock grafiği; PostgreSQL EXPLAIN,
pg_stat_statements, tablo şişmesi ve yapılandırma; SQL Server yapılandırması; Oracle AWR raporu; MongoDB profiler ve $indexStats; MySQL slow query log.
Veritabanına bağlanmaz; DBA’nın dışa aktardığı plan/DMV/istatistik dosyalarını okur."""
import json, re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
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


BACKUP_KIND = {'d': 'tam', 'full': 'tam', 'tam': 'tam', 'database': 'tam', 'i': 'fark', 'diff': 'fark', 'differential': 'fark', 'fark': 'fark', 'l': 'log', 'log': 'log'}


@op('yedek-kontrolu', title='Veritabanı yedek geçmişinde eski, eksik ve şüpheli küçülen yedekleri bul', domain='database', product='SQL Server', tag='yedek denetimi',
    samples={'yedek_gecmisi.csv': 'yedek_gecmisi.csv'},
    args=[arg('--database', default='veritabani'), arg('--type', default='tur', help='Yedek türü: FULL/DIFF/LOG veya msdb kodu D/I/L'), arg('--finish', default='bitis'),
          arg('--size', default='boyut_mb'), arg('--as-of', default='2026-10-16 12:00', help='Kontrol zamanı'), arg('--full-hours', type=float, default=168),
          arg('--diff-hours', type=float, default=26), arg('--log-hours', type=float, default=1), arg('--size-change', type=float, default=50, help='Ardışık tam yedekler arasında yüzde bu kadar değişim uyarı verir')],
    description='msdb.dbo.backupset, pgBackRest/Barman veya yedekleme aracı dışa aktarımından her veritabanının son tam, fark ve log yedeğini bulur; eşikten eski yedekleri, hiç tam yedeği olmayan veritabanlarını ve ardışık tam yedeklerde boyutu yüzde X’ten fazla değişen (veri kaybı ya da beklenmedik büyüme) yedekleri işaretler, yaklaşık RPO’yu (son yedekten geçen süre) verir.',
    input='Yedek geçmişi (veritabanı, tür, bitiş zamanı, boyut)', output='sonuc.csv (veritabanı bazında son yedekler ve bulgular)',
    command='python main.py --input backupset.csv --as-of "2026-10-16 08:00" --log-hours 0.25',
    expect={'veritabani': 4, 'sorunlu': 3, 'eski_tam': 1, 'eski_log': 1, 'tam_yok': 1, 'boyut_uyarisi': 1})
def backups(columns, rows, args):
    dbc, tc, fc = need(args.database, columns, '--database'), need(args.type, columns, '--type'), need(args.finish, columns, '--finish')
    sizes = numbers(rows, need(args.size, columns, '--size')) if args.size in columns else [None] * len(rows)
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    if not as_of:
        raise ValueError(f'--as-of "{args.as_of}" tarih olarak okunamadı.')
    hist = defaultdict(lambda: defaultdict(list))
    for r, s in zip(rows, sizes):
        kind, d = BACKUP_KIND.get(r[tc].strip().lower()), tabular.parse_date(r[fc])
        if kind and d and r[dbc].strip():
            hist[r[dbc].strip()][kind].append((d[0], s))
    out = []
    for db, kinds in sorted(hist.items(), key=lambda kv: kv[0].lower()):
        last = {k: max(v) for k, v in kinds.items()}
        age = {k: round((as_of - t).total_seconds() / 3600, 1) for k, (t, _) in last.items()}
        issues = []
        if 'tam' not in last:
            issues.append('tam yedek yok')
        elif age['tam'] > args.full_hours:
            issues.append(f'tam yedek {fmt(age["tam"])} saat önce')
        if 'fark' in last and 'tam' in last and age['tam'] > args.diff_hours and age['fark'] > args.diff_hours:
            issues.append(f'fark yedeği {fmt(age["fark"])} saat önce')
        if 'log' in last and age['log'] > args.log_hours:
            issues.append(f'log yedeği {fmt(age["log"])} saat önce')
        fulls = sorted(kinds.get('tam', []))
        if len(fulls) >= 2 and fulls[-1][1] and fulls[-2][1]:
            change = 100 * (fulls[-1][1] - fulls[-2][1]) / fulls[-2][1]
            if abs(change) > args.size_change:
                issues.append(f'tam yedek boyutu %{fmt(round(change, 1))} değişti')
        out.append({'veritabani': db, 'son_tam': last['tam'][0].strftime('%Y-%m-%d %H:%M') if 'tam' in last else '', 'son_fark': last['fark'][0].strftime('%Y-%m-%d %H:%M') if 'fark' in last else '',
                    'son_log': last['log'][0].strftime('%Y-%m-%d %H:%M') if 'log' in last else '', 'rpo_saat': min(age.values()) if 'tam' in last else '',
                    'bulgu': '; '.join(issues) or 'uygun'})
    out.sort(key=lambda o: (o['bulgu'] == 'uygun', o['veritabani'].lower()))
    flags = [o['bulgu'] for o in out]
    return Result(f'{len(out)} veritabanı; {sum(f != "uygun" for f in flags)} veritabanında yedek bulgusu', list(out[0]), out,
                  {'veritabani': len(out), 'sorunlu': sum(f != 'uygun' for f in flags), 'eski_tam': sum('tam yedek' in f and 'saat önce' in f for f in flags),
                   'eski_log': sum('log yedeği' in f for f in flags), 'tam_yok': sum('tam yedek yok' in f for f in flags), 'boyut_uyarisi': sum('boyutu' in f for f in flags)},
                  notes=['Log yedeği kontrolü yalnızca log yedeği alınan (FULL recovery) veritabanlarına uygulanır. RPO, en yeni yedekten geçen süredir; log zincirinin kopuk olmadığını ve yedeğin geri yüklenebildiğini ayrıca test edin.'])


def shape(v):
    if isinstance(v, dict):
        if any(k.startswith('$') for k in v) and not any(isinstance(x, (dict, list)) for x in v.values()):
            return '{' + ','.join(sorted(v)) + '}'
        return '{' + ','.join(f'{k}:{shape(x)}' for k, x in sorted(v.items())) + '}'
    if isinstance(v, list):
        return '[' + ','.join(sorted({shape(x) for x in v})) + ']' if v and all(isinstance(x, dict) for x in v) else '?'
    return '?'


def mongo_query(cmd):
    cmd = cmd or {}
    if 'pipeline' in cmd:
        stages = []
        for st in cmd['pipeline'] if isinstance(cmd['pipeline'], list) else []:
            name = next(iter(st), '?') if isinstance(st, dict) else '?'
            stages.append(name + (shape(st[name]) if name == '$match' else ''))
        return ' | '.join(stages)
    for k in ('filter', 'q', 'query'):
        if isinstance(cmd.get(k), dict):
            return shape(cmd[k])
    return '{}'


@op('mongodb-profil-analizi', title='MongoDB profiler kayıtlarından yavaş sorgu şekilleri ve COLLSCAN', domain='database', product='Atlas', tag='profiler', kind='file',
    samples={'mongo_profil.jsonl': 'mongo_profil.jsonl'},
    args=[arg('--ratio', type=float, default=100, help='İncelenen / dönen belge oranı bu değerin üstündeyse verimsiz'), arg('--min-docs', type=int, default=1000)],
    description='system.profile koleksiyonunun (mongoexport ile JSON Lines) kayıtlarını koleksiyon, işlem ve sorgu şekline (değerler ? ile, aggregate için aşama listesi) göre gruplar; şekil başına çalışma sayısı, toplam/ortalama/en yüksek süre, plan özeti (COLLSCAN, IXSCAN, IDHACK) ve incelenen / dönen belge oranını verir. İndeks eksikliği olan, en çok süre harcayan sorgu şekillerini öne çıkarır.',
    input='system.profile dökümü (JSON Lines)', output='sonuc.csv (sorgu şekli bazında)', command='mongoexport -d shop -c system.profile -o profil.jsonl; python main.py --input profil.jsonl',
    expect={'kayit': 8, 'sekil': 5, 'collscan_sekil': 2, 'toplam_ms': 3325, 'verimsiz': 2})
def mongo_profile(args):
    groups, n, bad = {}, 0, 0
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(d, dict) or 'millis' not in d:
            continue
        n += 1
        key = (d.get('ns', ''), d.get('op', ''), mongo_query(d.get('command') or {'filter': d.get('query')}))
        g = groups.setdefault(key, {'ns': key[0], 'islem': key[1], 'sekil': key[2], 'calisma': 0, 'toplam_ms': 0, 'en_yuksek_ms': 0, 'plan': set(), 'incelenen': 0, 'donen': 0, 'donen_var': False})
        g['calisma'] += 1
        g['toplam_ms'] += int(d.get('millis') or 0)
        g['en_yuksek_ms'] = max(g['en_yuksek_ms'], int(d.get('millis') or 0))
        g['plan'].add(str(d.get('planSummary', '')).split(' ')[0] or '-')
        g['incelenen'] += int(d.get('docsExamined') or 0)
        if 'nreturned' in d:
            g['donen'] += int(d.get('nreturned') or 0)
            g['donen_var'] = True
    if not groups:
        raise ValueError('millis alanı olan profiler kaydı yok (db.setProfilingLevel ile profiler açık mı?).')
    out = []
    for g in groups.values():
        ratio = round(g['incelenen'] / max(g['donen'], 1), 1) if g['donen_var'] else None
        issues = []
        if 'COLLSCAN' in g['plan']:
            issues.append('koleksiyon taraması (indeks yok)')
        if ratio is not None and ratio > args.ratio and g['incelenen'] >= args.min_docs:
            issues.append(f'{fmt(ratio)} belge incelenip 1 döndü')
        out.append({'ns': g['ns'], 'islem': g['islem'], 'sekil': g['sekil'], 'calisma': g['calisma'], 'toplam_ms': g['toplam_ms'], 'ortalama_ms': round(g['toplam_ms'] / g['calisma'], 1),
                    'en_yuksek_ms': g['en_yuksek_ms'], 'plan': ', '.join(sorted(g['plan'])), 'incelenen_donen_orani': fmt(ratio), 'bulgu': '; '.join(issues)})
    out.sort(key=lambda o: -o['toplam_ms'])
    total = sum(o['toplam_ms'] for o in out)
    return Result(f'{n} profiler kaydı {len(out)} sorgu şeklinde; {sum("COLLSCAN" in o["plan"] for o in out)} şekil koleksiyon taraması yapıyor', list(out[0]), out,
                  {'kayit': n, 'sekil': len(out), 'collscan_sekil': sum('COLLSCAN' in o['plan'] for o in out), 'toplam_ms': total,
                   'verimsiz': sum('incelenip' in o['bulgu'] for o in out), 'bozuk_satir': bad},
                  notes=['Profiler yalnızca slowms eşiğini aşan işlemleri kaydeder (varsayılan 100 ms); toplam süre tüm yükü değil, yavaş kısmı gösterir.',
                         'COLLSCAN yapan şekil için sorgudaki eşitlik alanları önce, aralık/sıralama alanları sonra gelecek şekilde (ESR kuralı) bileşik indeks düşünün.'])


@op('pg-uzun-islemler', title='pg_stat_activity: uzun süren sorgular ve “idle in transaction” oturumlar', domain='database', product=PG, tag='uzun işlem',
    samples={'pg_oturumlar.csv': 'pg_oturumlar.csv'},
    args=[arg('--as-of', default='2026-10-16 10:00:00', help='Dökümün alındığı an (now())'), arg('--query-min', type=float, default=5, help='Bu kadar dakikadan uzun aktif sorgu'),
          arg('--idle-min', type=float, default=1, help='Bu kadar dakikadan uzun idle in transaction'), arg('--xact-min', type=float, default=30, help='Bu kadar dakikadan uzun açık işlem')],
    description='pg_stat_activity dökümünden (pid, kullanıcı, uygulama, durum, xact_start, query_start, state_change, sorgu) uzun süren aktif sorguları, işlem açık bırakılıp beklenen “idle in transaction” oturumları (VACUUM’u ve kilitleri tutar) ve çok uzun açık işlemleri dakika cinsinden listeler; kullanıcı/uygulama bazında özetler ve gözden geçirilecek pg_terminate_backend komutlarını üretir (çalıştırmaz).',
    input='pg_stat_activity dışa aktarımı', output='sonuc.csv (bulgular) + sonlandir.sql (inceleme için)', command="python main.py --input activity.csv --as-of \"2026-10-16 10:00\" --idle-min 5",
    expect={'oturum': 7, 'uzun_sorgu': 1, 'idle_islem': 3, 'uzun_islem': 2, 'sorunlu_oturum': 4})
def long_tx(columns, rows, args):
    for c in ('pid', 'state', 'xact_start', 'query_start'):
        need(c, columns, c)
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    if not as_of:
        raise ValueError(f'--as-of "{args.as_of}" tarih olarak okunamadı.')

    def mins(v):
        d = tabular.parse_date(v) if v.strip() else None
        return round((as_of - d[0]).total_seconds() / 60, 1) if d else None
    out, kill = [], []
    for r in rows:
        state = r['state'].strip().lower()
        q, x = mins(r['query_start']), mins(r['xact_start'])
        issues = []
        if state == 'active' and q is not None and q > args.query_min:
            issues.append(f'sorgu {fmt(q)} dk sürüyor')
        if state.startswith('idle in transaction'):
            idle = mins(r.get('state_change', '')) if r.get('state_change', '').strip() else q
            if idle is not None and idle > args.idle_min:
                issues.append(f'{fmt(idle)} dk idle in transaction')
        if x is not None and x > args.xact_min:
            issues.append(f'işlem {fmt(x)} dk açık')
        if issues:
            out.append({'pid': r['pid'].strip(), 'kullanici': r.get('usename', '').strip(), 'uygulama': r.get('application_name', '').strip(), 'durum': state,
                        'islem_dk': x if x is not None else '', 'sorgu_dk': q if q is not None else '', 'bulgu': '; '.join(issues), 'sorgu': re.sub(r'\s+', ' ', r.get('query', ''))[:120]})
            kill.append(f"-- {r.get('usename', '')} / {state} / {'; '.join(issues)}\n-- SELECT pg_terminate_backend({r['pid'].strip()});")
    out.sort(key=lambda o: -(o['islem_dk'] or 0))
    flags = ' '.join(o['bulgu'] for o in out)
    return Result(f'{len(rows)} oturum; {len(out)} oturumda uzun sorgu/işlem', ['pid', 'kullanici', 'uygulama', 'durum', 'islem_dk', 'sorgu_dk', 'bulgu', 'sorgu'], out,
                  {'oturum': len(rows), 'uzun_sorgu': flags.count('sorgu '), 'idle_islem': flags.count('idle in transaction'), 'uzun_islem': flags.count('işlem '),
                   'sorunlu_oturum': len(out)},
                  files={'sonlandir.sql': '-- İNCELEME İÇİN: komutlar yorum satırıdır; uygulama sahibine sormadan çalıştırmayın.\n' + '\n\n'.join(kill) + '\n'},
                  notes=['idle in transaction oturumlar satır kilitlerini ve VACUUM’un temizleyebileceği en eski işlemi tutar; uygulama tarafında idle_in_transaction_session_timeout ayarlayın.'])


IDXDEF = re.compile(r'CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?("?[\w.]+"?)\s+ON\s+(?:ONLY\s+)?([\w."]+)\s*(?:USING\s+(\w+)\s*)?\((.*?)\)\s*(?:INCLUDE\s*\((.*?)\))?\s*(?:WHERE\s+(.*))?$', re.I | re.S)


@op('indeks-tekrari', title='Tekrarlanan ve başka bir indeksin kapsadığı (gereksiz) indeksler', domain='database', product=PG, tag='gereksiz indeks',
    samples={'pg_indexes.csv': 'pg_indexes.csv'}, args=[arg('--def', dest='def_col', default='indexdef', help='CREATE INDEX tanımı sütunu')],
    description='pg_indexes (veya başka bir veritabanının indeks tanımı) dökümündeki CREATE INDEX ifadelerini çözümler; aynı tablo, aynı sütun sırası, aynı yöntem ve aynı koşulla tanımlanmış tekrar indeksleri ve sütunları başka bir indeksin baş kısmında aynen bulunan (sol önek) kapsanan indeksleri bulur. Benzersiz ve kısmi (WHERE) indeksler kapsanan sayılmaz. Gereksiz indeksler yazma maliyetini ve disk kullanımını artırır.',
    input='İndeks tanımları (pg_indexes: tablename, indexname, indexdef)', output='sonuc.csv (gereksiz indeks adayları) + kaldir.sql (inceleme için)', command='python main.py --input pg_indexes.csv',
    expect={'indeks': 6, 'tekrar': 1, 'kapsanan': 1})
def duplicate_indexes(columns, rows, args):
    dc = need(args.def_col, columns, '--def')
    idx = []
    for r in rows:
        m = IDXDEF.match(r[dc].strip().rstrip(';'))
        if not m:
            continue
        cols = tuple(re.sub(r'\s+', ' ', c.strip().strip('"')).lower() for c in m.group(5).split(','))
        idx.append({'ad': m.group(2).strip('"'), 'tablo': m.group(3).strip('"').lower(), 'unique': bool(m.group(1)), 'yontem': (m.group(4) or 'btree').lower(),
                    'cols': cols, 'include': (m.group(6) or '').lower().replace(' ', ''), 'where': re.sub(r'\s+', ' ', (m.group(7) or '').strip().lower())})
    if not idx:
        raise ValueError('CREATE INDEX tanımı okunamadı.')
    out, flagged = [], set()
    for i, a in enumerate(idx):
        for b in idx[:i]:
            if (a['tablo'], a['yontem'], a['cols'], a['where']) == (b['tablo'], b['yontem'], b['cols'], b['where']) and not a['unique']:
                out.append({'indeks': a['ad'], 'tablo': a['tablo'], 'sutunlar': ', '.join(a['cols']), 'bulgu': 'tekrar', 'karsilik': b['ad']})
                flagged.add(a['ad'])
                break
    for a in idx:
        if a['ad'] in flagged or a['unique'] or a['where']:
            continue
        for b in idx:
            if b is not a and b['tablo'] == a['tablo'] and b['yontem'] == a['yontem'] == 'btree' and not b['where'] and len(b['cols']) > len(a['cols']) and b['cols'][:len(a['cols'])] == a['cols']:
                out.append({'indeks': a['ad'], 'tablo': a['tablo'], 'sutunlar': ', '.join(a['cols']), 'bulgu': 'kapsanan (sol önek)', 'karsilik': b['ad']})
                break
    sql = '\n'.join(f"-- {o['bulgu']}: {o['karsilik']} aynı işi görüyor\n-- DROP INDEX CONCURRENTLY {o['indeks']};" for o in out)
    return Result(f'{len(idx)} indeks; {sum(o["bulgu"] == "tekrar" for o in out)} tekrar, {sum(o["bulgu"] != "tekrar" for o in out)} kapsanan indeks', ['indeks', 'tablo', 'sutunlar', 'bulgu', 'karsilik'], out,
                  {'indeks': len(idx), 'tekrar': sum(o['bulgu'] == 'tekrar' for o in out), 'kapsanan': sum(o['bulgu'] != 'tekrar' for o in out)},
                  files={'kaldir.sql': '-- İNCELEME İÇİN: komutlar yorum satırıdır. Kaldırmadan önce indeks-kullanim-analizi ile kullanılmadığını ve kısıt (constraint) desteklemediğini doğrulayın.\n' + sql + '\n'},
                  notes=['Kapsanan indeks, daha küçük olduğu için bazı sorgularda tercih ediliyor olabilir; kullanım istatistiğiyle birlikte karar verin. Yabancı anahtar sütunlarındaki indeksleri kaldırmak silme işlemlerini yavaşlatabilir.'])


class _Tables(HTMLParser):
    """Collect every <table> of an HTML report as (summary, rows of cell texts)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.stack.append([dict(attrs).get('summary', ''), []])
        elif tag == 'tr' and self.stack:
            self.stack[-1][1].append([])
        elif tag in ('td', 'th') and self.stack and self.stack[-1][1]:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.stack and self.stack[-1][1]:
            self.stack[-1][1][-1].append(re.sub(r'\s+', ' ', ''.join(self.cell)).strip())
            self.cell = None
        elif tag == 'table' and self.stack:
            self.tables.append(tuple(self.stack.pop()))

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def awr_table(tables, *words):
    for summary, rows in tables:
        if all(w in summary.lower() for w in words):
            return [r for r in rows if r]
    return []


AWR_WAITS = [
    (r'^db file sequential read', 'tek blok okuma (indeks erişimi): en çok okuyan SQL’leri ayarlayın, buffer cache yeterli mi bakın'),
    (r'^db file scattered read|^direct path read', 'tam tablo taraması: eksik indeks veya bölümleme; büyük taramaları yapan SQL’lere bakın'),
    (r'^log file sync', 'commit bekleme: çok sık commit veya yavaş redo diski; toplu commit ve redo log diskini inceleyin'),
    (r'^log file switch', 'redo log dosyaları küçük: log dosyası boyutunu büyütün'),
    (r'^enq: TX - row lock', 'satır kilidi çakışması: uygulama aynı satırları eşzamanlı güncelliyor'),
    (r'^(latch|mutex|cursor: pin|library cache)', 'ayrıştırma çakışması: bağ değişkeni (bind variable) kullanmayan SQL'),
    (r'^gc ', 'RAC ara bağlantı beklemesi: aynı blokları farklı node’lar kullanıyor'),
    (r'^DB CPU', 'CPU üzerinde çalışma: en çok CPU harcayan SQL’leri ayarlayın'),
    (r'^resmgr:cpu quantum', 'Resource Manager CPU kısıtlaması: plan sınırlarını kontrol edin'),
]


def awr_num(text):
    text = (text or '').replace(',', '').strip()
    m = re.match(r'-?[\d.]+', text)
    if not m:
        return None
    return float(m.group(0)) * {'K': 1e3, 'M': 1e6, 'G': 1e9}.get(text[m.end():m.end() + 1].upper(), 1)


def fixed_width(lines, dash):
    """Cut lines into columns at the spans of a ---- ---- dash line (AWR text tables)."""
    spans = [(m.start(), m.end()) for m in re.finditer(r'-+', dash)]
    out = []
    for line in lines:
        cells = [line[a:(spans[i + 1][0] if i + 1 < len(spans) else len(line))].strip() for i, (a, _) in enumerate(spans)]
        out.append(cells)
    return out


def awr_text(text):
    """Turn a text AWR report (awrrpt_*.txt) into the same (summary, rows) tables the HTML report gives."""
    lines = text.splitlines()
    tables = []

    def block(start):
        """Rows after the first dash line following `start`, up to the next blank line."""
        for i in range(start, min(start + 8, len(lines))):
            if re.match(r'^\s*-{3,}(\s+-{2,})+\s*$', lines[i]):
                rows = []
                for ln in lines[i + 1:]:
                    if not ln.strip():
                        break
                    rows.append(ln)
                return lines[i], rows
        return None, []
    for i, ln in enumerate(lines):
        if re.match(r'^Host Name\s', ln):
            dash, rows = block(i)
            if dash:
                tables.append(('host information', [fixed_width([ln], dash)[0]] + fixed_width(rows, dash)))
    snap = [[m.group(1) + ':', m.group(2)] for m in re.finditer(r'^\s*(Elapsed|DB Time):\s+([\d,.]+)\s*\(mins\)', text, re.M)]
    tables.append(('snapshot information', snap))
    for i, ln in enumerate(lines):
        if ln.startswith('Load Profile'):
            rows = []
            for x in lines[i + 2:]:
                m = re.match(r'^\s*(.+?):\s+([\d,.]+)\s*([\d,.]*)', x)
                if not m:
                    break
                rows.append([m.group(1) + ':', m.group(2), m.group(3)])
            tables.append(('load profile', rows))
        if ln.startswith('Instance Efficiency Percentages'):
            rows = []
            for x in lines[i + 2:]:
                pairs = re.findall(r'([A-Za-z][A-Za-z -]*%):\s+([\d.]+)', x)
                if not pairs:
                    break
                rows.append([c for k, v in pairs for c in (k.strip() + ':', v)])
            tables.append(('instance efficiency percentages', rows))
        if re.match(r'^Top \d+ (Foreground Events|Timed)', ln):
            dash, rows = block(i)
            if dash:
                cells = fixed_width(rows, dash)
                tables.append(('top 10 wait events', [['Event', 'Waits', 'Total Wait Time (sec)', 'Wait Avg(ms)', '% DB time', 'Wait Class']] + [c[:6] + [''] * (6 - len(c)) for c in cells]))
        if ln.startswith('SQL ordered by Elapsed Time'):
            dash, _ = block(i)
            start = lines.index(dash, i) + 1 if dash else len(lines)
            rows, cur = [], None
            for x in lines[start:]:
                m = re.match(r'^\s*([\d,.]+)\s+([\d,.]+)\s+([\d,.N/A]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\w{13})\s*$', x)
                if m:
                    cur = list(m.groups()) + ['', '']
                    rows.append(cur)
                elif cur is not None and x.startswith('Module:'):
                    cur[7] = x[7:].strip()
                elif cur is not None and x.strip() and not cur[8]:
                    cur[8] = x.strip()
                elif not x.strip() and cur is not None and cur[8]:
                    cur = None
                if re.match(r'^\s*-{5,}\s*$', x) or x.startswith('SQL ordered by CPU'):
                    break
            tables.append(('top sql by elapsed time', [['Elapsed Time (s)', 'Executions', 'Elapsed Time per Exec (s)', '%Total', '%CPU', '%IO', 'SQL Id', 'SQL Module', 'SQL Text']] + rows))
    return tables


@op('oracle-awr', title='Oracle AWR raporu: yük profili, en çok bekleme olayları ve en pahalı SQL', domain='database', product='Oracle Database', tag='AWR', kind='file',
    samples={'oracle_awr.txt': 'oracle_awr.txt'},
    description='AWR raporundan (HTML veya metin: awrrpt_*.html / .txt) anlık görüntü aralığını, DB Time ve ortalama aktif oturumu (AAS) CPU sayısıyla, yük profilini (redo, mantıksal/fiziksel okuma, ayrıştırma), örnek verimliliği yüzdelerini, en çok bekleme olaylarını ve geçen süreye göre en pahalı SQL’leri çıkarır. AAS’in CPU sayısını aşmasını, bekleme olayının türüne göre olası nedenini (indeks okuma, tam tarama, commit, kilit, ayrıştırma, RAC), yüksek hard parse oranını ve DB Time’ın büyük kısmını tüketen tek SQL’leri işaretler.',
    input='AWR raporu (HTML veya metin)', output='sonuc.csv (en pahalı SQL’ler) + bulgular ve bekleme olayları', command='python main.py --input awrrpt_1_4512_4513.html',
    expect={'aas': 9.8, 'cpu': 8, 'bulgu': 5, 'sql': 6})
def oracle_awr(args):
    text = Path(args.input).read_text(encoding='utf-8', errors='replace')
    if '<table' in text.lower():
        p = _Tables()
        p.feed(text)
        tables = p.tables
    else:
        tables = awr_text(text)
    snaps = awr_table(tables, 'snapshot')
    elapsed = dbtime = None
    for r in snaps:
        label = r[0].lower()
        value = next((awr_num(c) for c in r[1:] if awr_num(c) is not None), None)
        if label.startswith('elapsed'):
            elapsed = value
        if label.startswith('db time'):
            dbtime = value
    host = awr_table(tables, 'host')
    cpus = None
    if len(host) > 1 and 'CPUs' in host[0]:
        cpus = int(awr_num(host[1][host[0].index('CPUs')]) or 0) or None
    if not elapsed or not dbtime:
        raise ValueError('AWR raporunda Elapsed / DB Time bulunamadı; awrrpt.sql ile alınmış HTML veya metin AWR raporu verin.')
    aas = dbtime / elapsed
    findings = []
    if cpus and aas > cpus:
        findings.append(['yüksek', f'Ortalama aktif oturum {aas:.1f}, CPU sayısı {cpus}: veritabanı bu aralıkta doymuş; bekleyen iş birikmiş.'])
    load = {r[0].rstrip(':').strip(): r[1:] for r in awr_table(tables, 'load profile') if len(r) > 1 and r[0].strip()}
    lp = lambda k, i=0: awr_num(load.get(k, [None])[i]) if k in load else None
    parses, hard = lp('Parses (SQL)'), lp('Hard parses (SQL)')
    if parses and hard is not None and hard / parses > 0.1:
        findings.append(['orta', f'Hard parse oranı %{100 * hard / parses:.0f} (saniyede {hard:g}): bağ değişkeni kullanmayan SQL’ler var; cursor_sharing veya uygulama düzeltmesi.'])
    eff = {}
    for r in awr_table(tables, 'instance efficiency'):
        for i in range(0, len(r) - 1, 2):
            if r[i].endswith(':'):
                eff[r[i].rstrip(':').strip()] = awr_num(r[i + 1])
    if eff.get('Soft Parse %') is not None and eff['Soft Parse %'] < 90:
        findings.append(['orta', f'Soft Parse %{eff["Soft Parse %"]:g}: %95 üstü beklenir.'])
    waits = awr_table(tables, 'wait events')
    wait_rows = []
    if waits:
        head = waits[0]
        ix = {h: i for i, h in enumerate(head)}
        for r in waits[1:]:
            if len(r) < len(head):
                continue
            name = r[0]
            pct = awr_num(r[ix.get('% DB time', ix.get('%DB time', len(r) - 2))])
            why = next((w for rx, w in AWR_WAITS if re.match(rx, name, re.I)), '')
            wait_rows.append([name, r[ix.get('Wait Class', len(r) - 1)], r[ix.get('Total Wait Time (sec)', 2)], fmt(pct), why])
        top = [w for w in wait_rows if w[0] != 'DB CPU']
        if top and awr_num(top[0][3]) and awr_num(top[0][3]) >= 20:
            findings.append(['yüksek', f'En büyük bekleme “{top[0][0]}” DB Time’ın %{top[0][3]} kadarı: {top[0][4] or "olay türünü inceleyin"}.'])
    sqls = awr_table(tables, 'sql', 'elapsed')
    out = []
    if sqls:
        head = sqls[0]
        ix = {h: i for i, h in enumerate(head)}
        for r in sqls[1:]:
            if len(r) < len(head):
                continue
            total_pct = awr_num(r[ix['%Total']]) if '%Total' in ix else None
            execs = awr_num(r[ix['Executions']]) if 'Executions' in ix else None
            note = []
            if total_pct and total_pct >= 25:
                note.append('DB Time’ın büyük kısmı tek SQL’de: ilk ayarlanacak SQL')
            if execs and execs > 100_000:
                note.append('çok sık çalışıyor: çağrı sayısını (döngü içinde SQL) azaltmak gerekebilir')
            out.append({'sql_id': r[ix.get('SQL Id', len(r) - 3)], 'gecen_sure_sn': awr_num(r[0]), 'calisma': int(execs) if execs is not None else '', 'calisma_basi_sn': awr_num(r[ix['Elapsed Time per Exec (s)']]) if 'Elapsed Time per Exec (s)' in ix else '',
                        'db_time_%': total_pct, 'cpu_%': awr_num(r[ix['%CPU']]) if '%CPU' in ix else '', 'io_%': awr_num(r[ix['%IO']]) if '%IO' in ix else '',
                        'modul': r[ix['SQL Module']] if 'SQL Module' in ix else '', 'sql': r[ix['SQL Text']][:120] if 'SQL Text' in ix else '', 'not': '; '.join(note)})
        heavy = [o for o in out if o['db_time_%'] and o['db_time_%'] >= 25]
        if heavy:
            findings.append(['yüksek', f'{heavy[0]["sql_id"]} tek başına DB Time’ın %{heavy[0]["db_time_%"]:g} kadarını kullanıyor: SQL Tuning Advisor / plan incelemesi.'])
    lp_rows = [[k, *(v[:2])] for k, v in load.items()]
    return Result(f'{elapsed:g} dk aralık, DB Time {dbtime:g} dk; ortalama aktif oturum {aas:.1f}' + (f' ({cpus} CPU)' if cpus else '') + f'; {len(findings)} bulgu',
                  ['sql_id', 'gecen_sure_sn', 'calisma', 'calisma_basi_sn', 'db_time_%', 'cpu_%', 'io_%', 'modul', 'sql', 'not'], out,
                  {'aas': round(aas, 1), 'cpu': cpus, 'bulgu': len(findings), 'sql': len(out)},
                  tables=[('Bulgular', ['Önem', 'Bulgu'], findings), ('Bekleme olayları', ['Olay', 'Sınıf', 'Toplam bekleme (sn)', '% DB time', 'Olası neden'], wait_rows),
                          ('Yük profili', ['Ölçü', 'Saniyede', 'İşlem başına'], lp_rows)],
                  notes=['Tek bir AWR aralığı tek bir anı gösterir; normal bir günün raporuyla (baseline) karşılaştırın. AWR, Diagnostics Pack lisansı gerektirir; lisans yoksa Statspack kullanın.'])


def pg_bytes(value, unit):
    v = float(value)
    mult = {'8kB': 8192, 'kB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'B': 1, '': 1}.get(unit, 1)
    return v * mult


@op('pg-yapilandirma', title='PostgreSQL yapılandırma denetimi ve transaction ID wraparound riski', domain='database', product=PG, tag='pg_settings',
    samples={'pg_settings.csv': 'pg_settings.csv', 'pg_veritabani_yasi.csv': 'pg_veritabani_yasi.csv'},
    args=[arg('--ram-gb', type=float, default=64, help='Sunucu belleği (GB)'), arg('--ssd', default='evet', help='Veri diski SSD mi (evet/hayır)'),
          arg('--age', default='@ornek/pg_veritabani_yasi.csv', help='İsteğe bağlı: SELECT datname, age(datfrozenxid) FROM pg_database çıktısı')],
    description='pg_settings dışa aktarımını (name, setting, unit) sunucu belleği ve disk türüyle karşılaştırır: shared_buffers, effective_cache_size, work_mem × max_connections bellek riski, maintenance_work_mem, max_connections (bağlantı havuzu ihtiyacı), random_page_cost (SSD), kapalı statement/lock/idle_in_transaction zaman aşımları, kapalı autovacuum, sık checkpoint (max_wal_size), kapalı yavaş sorgu logu, pg_stat_statements yüklenmemesi, track_io_timing ve veri kaybı riski taşıyan fsync/full_page_writes. Veritabanı yaşı verilirse transaction ID wraparound’a yaklaşan veritabanlarını işaretler.',
    input='pg_settings CSV’si (+ isteğe bağlı veritabanı yaşları)', output='sonuc.csv (parametre bazında bulgular)', command='python main.py --input pg_settings.csv --ram-gb 128 --age db_age.csv',
    expect={'parametre': 24, 'bulgu': 15, 'yuksek': 2})
def pg_config(columns, rows, args):
    for c in ('name', 'setting'):
        need(c, columns, 'pg_settings')
    st = {r['name'].strip(): (r['setting'].strip(), r.get('unit', '').strip()) for r in rows}
    ram = args.ram_gb * 1024 ** 3
    out = []

    def add(name, sev, advice):
        v, u = st.get(name, ('', ''))
        out.append({'parametre': name, 'deger': f'{v} {u}'.strip(), 'onem': sev, 'oneri': advice})

    def b(name):
        v, u = st.get(name, (None, ''))
        return pg_bytes(v, u) if v not in (None, '', '-1') else None
    def val(name):
        return st.get(name, ('', ''))[0]
    sb = b('shared_buffers')
    if sb is not None and not 0.15 * ram <= sb <= 0.4 * ram:
        add('shared_buffers', 'orta', f'Belleğin %{100 * sb / ram:.1f} kadarı; %25 civarı ({0.25 * args.ram_gb:g} GB) önerilir.')
    ec = b('effective_cache_size')
    if ec is not None and ec < 0.5 * ram:
        add('effective_cache_size', 'düşük', f'Belleğin %{100 * ec / ram:.0f} kadarı; planlayıcı indeksleri az kullanır. %50–75 ({0.75 * args.ram_gb:g} GB) verin.')
    wm, mc = b('work_mem'), int(val('max_connections') or 0)
    if wm is not None and mc and wm * mc > 2 * ram:
        add('work_mem', 'yüksek', f'work_mem × max_connections = {wm * mc / 1024 ** 3:,.0f} GB, bellek {args.ram_gb:g} GB: eşzamanlı sıralamalarda bellek taşar (OOM).')
    mw = b('maintenance_work_mem')
    if mw is not None and mw <= 64 * 1024 ** 2 and args.ram_gb >= 16:
        add('maintenance_work_mem', 'düşük', 'Varsayılan 64 MB; VACUUM ve indeks oluşturma yavaş. 1–2 GB verin.')
    if mc > 300:
        add('max_connections', 'orta', f'{mc} bağlantı: her bağlantı ayrı süreçtir; PgBouncer gibi bir havuzla 100–200’e indirin.')
    if val('random_page_cost') in ('4', '4.0') and args.ssd.lower().startswith('e'):
        add('random_page_cost', 'orta', 'SSD’de 1.1 olmalı; 4 değeri planlayıcıyı tam taramaya iter.')
    for name in ('statement_timeout', 'idle_in_transaction_session_timeout', 'lock_timeout'):
        if val(name) == '0':
            add(name, 'orta' if name != 'lock_timeout' else 'düşük', 'Kapalı (0): kaçak sorgu veya açık bırakılan işlem kilit ve şişme üretir; rol veya veritabanı düzeyinde bir sınır verin.')
    if val('autovacuum') == 'off':
        add('autovacuum', 'yüksek', 'Kapalı: ölü satırlar temizlenmez ve wraparound koruması yalnızca acil vacuum’a kalır.')
    if val('fsync') == 'off' or val('full_page_writes') == 'off':
        add('fsync' if val('fsync') == 'off' else 'full_page_writes', 'yüksek', 'Kapalı: çökme anında veri bozulur. Yalnız atılabilir test ortamında kabul edilebilir.')
    mx = b('max_wal_size')
    if mx is not None and mx <= 1024 ** 3:
        add('max_wal_size', 'orta', 'Varsayılan 1 GB: yazma yükünde checkpoint’ler sık ve ani I/O olur; 4–16 GB verin, pg_stat_bgwriter’da checkpoints_req’e bakın.')
    if val('log_min_duration_statement') == '-1':
        add('log_min_duration_statement', 'düşük', 'Yavaş sorgu logu kapalı; 1000 ms gibi bir eşik verin.')
    if 'pg_stat_statements' not in val('shared_preload_libraries'):
        add('shared_preload_libraries', 'orta', 'pg_stat_statements yüklenmemiş: hangi sorgunun pahalı olduğu ölçülemez.')
    if val('track_io_timing') == 'off':
        add('track_io_timing', 'düşük', 'Kapalı: sorgu planlarında I/O süresi görünmez.')
    age_rows = []
    if args.age and Path(args.age).is_file():
        acols, arows = tabular.read(args.age)
        need('datname', acols, '--age')
        col = next((c for c in acols if c.lower().startswith('age')), None)
        if col is None:
            raise ValueError('--age dosyasında age sütunu yok.')
        for r, a in zip(arows, numbers(arows, col)):
            if a is None:
                continue
            sev = 'yüksek' if a > 1.5e9 else 'orta' if a > 1e9 else ''
            age_rows.append([r['datname'], f'{a:,.0f}', f'%{100 * a / 2 ** 31:.0f}', sev or 'normal'])
            if sev:
                out.append({'parametre': f'veritabanı {r["datname"]}', 'deger': f'age {a:,.0f}', 'onem': sev,
                            'oneri': f'Wraparound sınırının (2,1 milyar) %{100 * a / 2 ** 31:.0f} kadarında: en eski tablolarda VACUUM FREEZE planlayın, uzun açık işlem ve yetim replikasyon slotlarını kontrol edin.'})
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2}
    out.sort(key=lambda o: (order[o['onem']], o['parametre']))
    high = sum(o['onem'] == 'yüksek' for o in out)
    return Result(f'{len(st)} parametre; {len(out)} bulgu ({high} yüksek)', ['parametre', 'deger', 'onem', 'oneri'], out,
                  {'parametre': len(st), 'bulgu': len(out), 'yuksek': high},
                  tables=[('Veritabanı yaşı (wraparound)', ['Veritabanı', 'age(datfrozenxid)', 'Sınıra oran', 'Durum'], age_rows)] if age_rows else [],
                  notes=['Öneriler genel başlangıç değerleridir; iş yükü ölçülerek (pg_stat_statements, pg_stat_bgwriter) ayarlanmalıdır. Değişiklikleri önce test ortamında deneyin.'])


SS_CONFIG = [
    ('max server memory (MB)', lambda v, a: v >= 2147483647, 'yüksek', 'Sınırsız (varsayılan): SQL Server işletim sistemini belleksiz bırakır; toplam belleğin %80–90’ı kadar verin.'),
    ('max degree of parallelism', lambda v, a: v == 0 and a.cpus > 8, 'orta', 'MAXDOP 0 ve 8’den çok çekirdek: tek sorgu tüm çekirdekleri alır; 8 veya NUMA düğümü başına çekirdek sayısı verin.'),
    ('cost threshold for parallelism', lambda v, a: v <= 5, 'orta', 'Varsayılan 5: küçük sorgular da paralelleşir (CXPACKET); 50 civarından başlayın.'),
    ('optimize for ad hoc workloads', lambda v, a: v == 0, 'düşük', 'Kapalı: tek kullanımlık planlar plan önbelleğini doldurur.'),
    ('backup compression default', lambda v, a: v == 0, 'düşük', 'Kapalı: yedekler daha büyük ve yavaş.'),
    ('remote admin connections', lambda v, a: v == 0, 'düşük', 'Uzak DAC kapalı: sunucu kilitlendiğinde uzaktan teşhis bağlantısı açılamaz.'),
    ('xp_cmdshell', lambda v, a: v == 1, 'yüksek', 'Açık: SQL üzerinden işletim sistemi komutu çalıştırılabilir; kapatın.'),
    ('priority boost', lambda v, a: v == 1, 'yüksek', 'Açık: işletim sistemini aç bırakabilir; Microsoft kapalı tutulmasını önerir.'),
    ('lightweight pooling', lambda v, a: v == 1, 'orta', 'Fiber modu açık: çoğu iş yükünde yarardan çok sorun üretir.'),
]


@op('sqlserver-yapilandirma', title='SQL Server sunucu ve veritabanı yapılandırma denetimi', domain='database', product=SS, tag='sp_configure',
    samples={'sqlserver_ayarlar.csv': 'sqlserver_ayarlar.csv', 'sqlserver_veritabanlari.csv': 'sqlserver_veritabanlari.csv'},
    args=[arg('--databases', default='@ornek/sqlserver_veritabanlari.csv', help='sys.databases dışa aktarımı'), arg('--cpus', type=int, default=16, help='Mantıksal çekirdek sayısı'),
          arg('--compat', type=int, default=160, help='Sunucunun desteklediği en yüksek uyumluluk düzeyi (2022: 160)')],
    description='sys.configurations (name, value_in_use) ve sys.databases dışa aktarımlarını sağlık kontrolü kurallarıyla denetler: sınırsız max server memory, MAXDOP ve cost threshold for parallelism varsayılanları, optimize for ad hoc workloads, yedek sıkıştırma, uzak DAC, xp_cmdshell, priority boost; veritabanlarında AUTO_SHRINK, AUTO_CLOSE, CHECKSUM olmayan sayfa doğrulama, kapalı otomatik istatistik, FULL kurtarma modelinde log yedeği alınmadığı için büyüyen log (log_reuse_wait LOG_BACKUP), eski uyumluluk düzeyi ve çevrim dışı veritabanları.',
    input='sys.configurations CSV’si + sys.databases CSV’si', output='sonuc.csv (bulgular)', command='python main.py --input configurations.csv --databases databases.csv --cpus 32 --compat 150',
    expect={'bulgu': 14, 'yuksek': 5, 'veritabani': 5})
def ss_config(columns, rows, args):
    for c in ('name', 'value_in_use'):
        need(c, columns, 'sys.configurations')
    cfg = {r['name'].strip().lower(): r['value_in_use'] for r in rows}
    out = []
    for name, bad, sev, advice in SS_CONFIG:
        if name.lower() in cfg:
            v = tabular.parse_number(cfg[name.lower()])
            if v is not None and bad(v, args):
                out.append({'kapsam': 'sunucu', 'nesne': name, 'deger': cfg[name.lower()], 'onem': sev, 'oneri': advice})
    dcols, drows = tabular.read(args.databases)
    need('name', dcols, '--databases')
    for r in drows:
        db = r['name'].strip()
        if db.lower() in ('master', 'model', 'msdb', 'tempdb'):
            continue
        g = lambda c: r.get(c, '').strip()
        add = lambda field, value, sev, advice: out.append({'kapsam': 'veritabanı', 'nesne': f'{db}.{field}', 'deger': value, 'onem': sev, 'oneri': advice})
        if g('state_desc') and g('state_desc').upper() != 'ONLINE':
            add('state_desc', g('state_desc'), 'bilgi', 'Çevrim içi değil; bilinçli mi kontrol edin.')
            continue
        if g('is_auto_shrink_on') in ('1', 'True', 'true'):
            add('auto_shrink', 'açık', 'yüksek', 'Sürekli küçültme parçalanma ve I/O üretir; kapatın.')
        if g('is_auto_close_on') in ('1', 'True', 'true'):
            add('auto_close', 'açık', 'orta', 'Her bağlantıda veritabanı yeniden açılır; kapatın.')
        if g('page_verify_option_desc') and g('page_verify_option_desc').upper() != 'CHECKSUM':
            add('page_verify', g('page_verify_option_desc'), 'yüksek', 'Sayfa bozulması fark edilmez; CHECKSUM yapın.')
        if g('is_auto_create_stats_on') in ('0', 'False', 'false') or g('is_auto_update_stats_on') in ('0', 'False', 'false'):
            add('auto_stats', 'kapalı', 'orta', 'Otomatik istatistik kapalı: planlar eskir; açın veya bakım işiyle güncelleyin.')
        if g('recovery_model_desc').upper() == 'FULL' and g('log_reuse_wait_desc').upper() == 'LOG_BACKUP':
            add('recovery_model', 'FULL', 'yüksek', 'Log yedeği alınmadığı için işlem günlüğü büyüyor (log_reuse_wait = LOG_BACKUP); log yedeği planlayın veya SIMPLE’a geçin.')
        lvl = tabular.parse_number(g('compatibility_level')) if g('compatibility_level') else None
        if lvl and lvl < args.compat:
            add('compatibility_level', str(int(lvl)), 'düşük', f'Sunucu {args.compat} destekliyor; yeni optimizer özellikleri kapalı. Query Store ile test ederek yükseltin.')
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2, 'bilgi': 3}
    out.sort(key=lambda o: (order[o['onem']], o['kapsam'], o['nesne']))
    high = sum(o['onem'] == 'yüksek' for o in out)
    dbs = sum(1 for r in drows if r['name'].strip().lower() not in ('master', 'model', 'msdb', 'tempdb'))
    return Result(f'{len(out)} bulgu ({high} yüksek), {dbs} kullanıcı veritabanı', ['kapsam', 'nesne', 'deger', 'onem', 'oneri'], out,
                  {'bulgu': len(out), 'yuksek': high, 'veritabani': dbs},
                  notes=['Kurallar sp_Blitz ve Microsoft en iyi uygulamalarının en sık bulunan maddeleridir; yedek geçmişi için yedek-kontrolu, bekleme ve indeks için ilgili blokları da çalıştırın.'])


def mongo_key(spec):
    return list(spec.items()) if isinstance(spec, dict) else []


@op('mongodb-indeks-kullanimi', title='MongoDB $indexStats: kullanılmayan ve önek olarak tekrar eden indeksler', domain='database', product='Atlas', tag='$indexStats', kind='file',
    samples={'mongodb_indexstats.jsonl': 'mongodb_indexstats.jsonl'},
    args=[arg('--as-of', default='2026-10-20', help='Kontrol tarihi'), arg('--days', type=int, default=30, help='Sayaç en az bu kadar gündür toplanıyor olmalı'),
          arg('--max', type=int, default=10, help='Koleksiyon başına en fazla indeks')],
    description='Her koleksiyonda db.koleksiyon.aggregate([{$indexStats: {}}]) çıktısının JSON Lines dışa aktarımını (ns alanı eklenmiş) okur: sayaç yeterince uzun süredir toplanıyorken hiç kullanılmayan indeksleri, anahtarı başka bir indeksin önekinden ibaret olan (gereksiz) indeksleri ve çok fazla indeksi olan (yazmayı yavaşlatan) koleksiyonları bulur. _id, unique, TTL ve kısmi (partial) indeksler silinme önerisinden ayrı tutulur.',
    input='$indexStats JSON Lines (ns, name, key, accesses, spec)', output='sonuc.csv (indeks bazında kullanım ve öneri)', command='python main.py --input indexstats.jsonl --as-of 2026-10-20',
    expect={'indeks': 16, 'kullanilmayan': 3, 'gereksiz_onek': 2, 'koleksiyon': 3})
def mongo_indexes(args):
    as_of = tabular.parse_date(args.as_of)[0]
    items = []
    for n, line in enumerate(Path(args.input).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            raise ValueError(f'{n}. satır JSON değil.') from None
        acc = d.get('accesses', {})
        since = acc.get('since', '')
        since = since.get('$date', '') if isinstance(since, dict) else since
        ops = acc.get('ops', 0)
        ops = int(ops.get('$numberLong', 0)) if isinstance(ops, dict) else int(ops or 0)
        spec = d.get('spec', {})
        items.append({'ns': d.get('ns', '?'), 'name': d.get('name', ''), 'key': mongo_key(d.get('key', {})), 'ops': ops, 'since': str(since)[:10],
                      'special': bool(spec.get('unique') or spec.get('expireAfterSeconds') is not None or spec.get('partialFilterExpression') or d.get('name') == '_id_')})
    by_ns = defaultdict(list)
    for it in items:
        by_ns[it['ns']].append(it)
    out = []
    for ns, idx in sorted(by_ns.items()):
        for it in idx:
            status, advice = 'kullanılıyor', ''
            covering = [o for o in idx if o is not it and len(o['key']) > len(it['key']) and o['key'][:len(it['key'])] == it['key']]
            d = tabular.parse_date(it['since'])
            age = (as_of - d[0]).days if d else None
            if covering and not it['special']:
                status, advice = 'gereksiz önek', f'{covering[0]["name"]} aynı sorguları karşılar; silmeden önce hint kullanan sorgu var mı bakın'
            elif it['ops'] == 0 and not it['special'] and age is not None and age >= args.days:
                status, advice = 'kullanılmıyor', f'{age} gündür hiç kullanılmadı: önce db.koleksiyon.hideIndex() ile gizleyip izleyin, sonra silin'
            elif it['ops'] == 0 and it['special']:
                status, advice = 'kullanılmıyor (özel)', 'unique/TTL/partial/_id indeksi: kısıt veya temizlik için gerekli olabilir, silmeyin'
            elif it['ops'] == 0:
                status, advice = 'veri yetersiz', f'sayaç {age} gündür toplanıyor; en az {args.days} gün bekleyin'
            out.append({'koleksiyon': ns, 'indeks': it['name'], 'anahtar': ', '.join(f'{k}:{v}' for k, v in it['key']), 'kullanim': it['ops'], 'sayac_baslangic': it['since'],
                        'durum': status, 'oneri': advice})
    many = [[ns, len(idx)] for ns, idx in sorted(by_ns.items()) if len(idx) > args.max]
    unused = sum(o['durum'] == 'kullanılmıyor' for o in out)
    redundant = sum(o['durum'] == 'gereksiz önek' for o in out)
    return Result(f'{len(out)} indeks, {len(by_ns)} koleksiyon; {unused} kullanılmıyor, {redundant} gereksiz önek', list(out[0]), out,
                  {'indeks': len(out), 'kullanilmayan': unused, 'gereksiz_onek': redundant, 'koleksiyon': len(by_ns)},
                  tables=[('Çok indeksli koleksiyonlar', ['Koleksiyon', 'İndeks'], many)] if many else [],
                  notes=['$indexStats sayaçları sunucu yeniden başlayınca ve replika setin her üyesinde ayrı tutulur; tüm üyelerin çıktısını birleştirin. Atlas’ta Performance Advisor da kullanılmayan indeksleri listeler.'])
