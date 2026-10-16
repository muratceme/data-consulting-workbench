"""Veritabanı performans blokları: SQL Server sorgu planı, bekleme istatistikleri ve indeks kullanımı; PostgreSQL EXPLAIN ve pg_stat_statements.
Veritabanına bağlanmaz; DBA’nın dışa aktardığı plan/DMV/istatistik dosyalarını okur."""
import json, re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
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
