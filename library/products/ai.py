"""GenAI & vector data: RAG corpus duplicate check (code) and an evidence-bound RAG architecture & evaluation design (agent).

Readers pull chunks/documents (text, metadata, vector when the store returns it) from each product's own API or
persisted format. Duplicates are found locally: exact (normalised text hash), near-duplicate text (MinHash LSH +
exact Jaccard) and near-duplicate vectors (random-hyperplane LSH + exact cosine). Nothing is deleted."""
import hashlib
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode

import native
from products.core import (NativeError, agent_task, bearer, fold, ident, journal, name, names, need_list, need_text, opt_float, opt_int,
                           plan_json, receipt, required, rows_md, secret, sql_source, write_csv, write_text)

MAX_CHUNKS = 50000


# ---------- Duplicate detection ----------
def shingles(text, k=5):
    words = fold(text).split()
    return {' '.join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))} if words else set()


def minhash(sh, n=64):
    if not sh:
        return None
    hashes = [int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), 'big') for s in sh]
    rnd = random.Random(7)
    params = [(rnd.randrange(1, 2 ** 61 - 1), rnd.randrange(0, 2 ** 61 - 1)) for _ in range(n)]
    return [min((a * h + b) % (2 ** 61 - 1) for h in hashes) for a, b in params]


def text_pairs(chunks, threshold, bands=16):
    sigs = {c['id']: (minhash(shingles(c['text'])), shingles(c['text'])) for c in chunks if c.get('text')}
    buckets = defaultdict(list)
    for cid, (sig, _) in sigs.items():
        if sig:
            rows = len(sig) // bands
            for b in range(bands):
                buckets[(b, tuple(sig[b * rows:(b + 1) * rows]))].append(cid)
    seen, pairs = set(), []
    for ids in buckets.values():
        if len(ids) > 200:
            ids = ids[:200]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = tuple(sorted((ids[i], ids[j])))
                if key in seen:
                    continue
                seen.add(key)
                a, b = sigs[key[0]][1], sigs[key[1]][1]
                score = len(a & b) / len(a | b) if a | b else 0
                if score >= threshold:
                    pairs.append({'parca_1': key[0], 'parca_2': key[1], 'tur': 'metin (Jaccard)', 'benzerlik': round(score, 4)})
    return pairs


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def vector_pairs(chunks, threshold, tables=6, bits=14):
    vecs = {c['id']: c['vector'] for c in chunks if c.get('vector')}
    if len(vecs) < 2:
        return []
    dim = len(next(iter(vecs.values())))
    if any(len(v) != dim for v in vecs.values()):
        raise NativeError('Vektör boyutları tutarsız; koleksiyonda karışık embedding modelleri olabilir.')
    rnd = random.Random(11)
    planes = [[[rnd.gauss(0, 1) for _ in range(dim)] for _ in range(bits)] for _ in range(tables)]
    buckets = defaultdict(list)
    for cid, v in vecs.items():
        for t, ps in enumerate(planes):
            buckets[(t, tuple(sum(p * x for p, x in zip(plane, v)) >= 0 for plane in ps))].append(cid)
    seen, pairs = set(), []
    for ids in buckets.values():
        for i in range(min(len(ids), 200)):
            for j in range(i + 1, min(len(ids), 200)):
                key = tuple(sorted((ids[i], ids[j])))
                if key in seen:
                    continue
                seen.add(key)
                score = cosine(vecs[key[0]], vecs[key[1]])
                if score >= threshold:
                    pairs.append({'parca_1': key[0], 'parca_2': key[1], 'tur': 'vektör (kosinüs)', 'benzerlik': round(score, 4)})
    return pairs


def snippet(text):
    try:
        from common import mask_text
        text = mask_text(text)[0]
    except ImportError:
        pass
    return re.sub(r'\s+', ' ', text or '')[:80]


def analyse(chunks, cfg):
    text_t = opt_float(cfg, 'text_similarity', 0.85, 0.5, 1.0)
    vec_t = opt_float(cfg, 'vector_similarity', 0.98, 0.8, 1.0)
    exact = defaultdict(list)
    for c in chunks:
        if c.get('text'):
            exact[hashlib.sha256(fold(c['text']).encode()).hexdigest()].append(c)
    groups = [g for g in exact.values() if len(g) > 1]
    exact_rows = [{'grup': i, 'parca': c['id'], 'kaynak': c.get('kaynak', ''), 'ozet': snippet(c['text'])} for i, g in enumerate(groups, 1) for c in g]
    exact_ids = {tuple(sorted((g[0]['id'], c['id']))) for g in groups for c in g[1:]}
    pairs = [p for p in text_pairs(chunks, text_t) + vector_pairs(chunks, vec_t) if (p['parca_1'], p['parca_2']) not in exact_ids]
    lengths = [len(c['text']) for c in chunks if c.get('text')]
    sources = Counter(c.get('kaynak') for c in chunks if c.get('kaynak'))
    meta_keys = Counter(k for c in chunks for k in (c.get('metadata') or {}))
    stats = {'parca': len(chunks), 'metinli': len(lengths), 'vektorlu': sum(1 for c in chunks if c.get('vector')),
             'birebir_grup': len(groups), 'birebir_fazla': sum(len(g) - 1 for g in groups), 'benzer_cift': len(pairs),
             'bos_metin': sum(1 for c in chunks if not (c.get('text') or '').strip() and not c.get('vector')),
             'uzunluk_karakter': {'min': min(lengths), 'medyan': int(statistics.median(lengths)), 'p95': sorted(lengths)[int(0.95 * (len(lengths) - 1))], 'max': max(lengths)} if lengths else {},
             'yaklasik_token_medyan': int(statistics.median(lengths) / 4) if lengths else 0,
             'kaynak_sayisi': len(sources), 'en_cok_parcali_kaynaklar': [{'kaynak': s, 'parca': n} for s, n in sources.most_common(10)],
             'metadata_alanlari': [{'alan': k, 'dolu_orani': round(n / len(chunks), 3)} for k, n in meta_keys.most_common(40)],
             'esikler': {'metin': text_t, 'vektor': vec_t}}
    dim = next((len(c['vector']) for c in chunks if c.get('vector')), None)
    if dim:
        stats['vektor_boyutu'] = dim
    return exact_rows, pairs, stats


# ---------- Readers: return (chunks, store_info) ----------
def chunk(cid, text='', metadata=None, vector=None, source_field=None, text_field=None):
    metadata = {k: v for k, v in (metadata or {}).items() if k != text_field and isinstance(v, (str, int, float, bool))}
    src = (metadata.get(source_field) if source_field else None) or metadata.get('source') or metadata.get('file_name') or ''
    vec = [float(x) for x in vector] if isinstance(vector, list) and vector and all(isinstance(x, (int, float)) for x in vector) else None
    return {'id': str(cid), 'text': text if isinstance(text, str) else '', 'metadata': metadata, 'vector': vec, 'kaynak': str(src)}


def limit_of(cfg):
    return opt_int(cfg, 'max_chunks', 10000, 1, MAX_CHUNKS)


def openai_read(cfg, factory, out):
    vs = required(cfg, 'vector_store_id')
    client = bearer(factory, 'https://api.openai.com', 'OPENAI_API_KEY')
    store = client.json('GET', '/v1/vector_stores/' + ident(vs))
    files, after = [], None
    for _ in range(200):
        r = client.json('GET', f'/v1/vector_stores/{ident(vs)}/files?' + urlencode({'limit': 100, **({'after': after} if after else {})}))
        files += [f for f in r.get('data', []) if isinstance(f, dict)]
        if not r.get('has_more') or len(files) >= limit_of(cfg):
            break
        after = r.get('last_id')
    chunks = []
    for f in files[:limit_of(cfg)]:
        if f.get('status') != 'completed':
            continue
        content = client.json('GET', f'/v1/vector_stores/{ident(vs)}/files/{ident(f["id"])}/content')
        text = '\n'.join(p.get('text', '') for p in content.get('data', []) if p.get('type') == 'text')
        meta = {**(f.get('attributes') or {}), 'file_id': f['id']}
        chunks.append(chunk(f['id'], text, meta, source_field='filename'))
    info = {'vector_store': store.get('name'), 'dosya_sayisi': (store.get('file_counts') or {}).get('total'),
            'parcalama': [f.get('chunking_strategy') for f in files[:5]], 'boyut_bayt': store.get('usage_bytes')}
    return chunks, info


def claude_read(cfg, factory, out):
    client = factory('https://api.anthropic.com', {'x-api-key': secret('ANTHROPIC_API_KEY'), 'anthropic-version': '2023-06-01', 'anthropic-beta': 'files-api-2025-04-14'})
    files, after = [], None
    for _ in range(100):
        r = client.json('GET', '/v1/files?' + urlencode({'limit': 1000, **({'after_id': after} if after else {})}))
        files += r.get('data', [])
        if not r.get('has_more'):
            break
        after = r.get('last_id')
    chunks = []
    by_meta = defaultdict(list)
    for f in files:
        by_meta[(f.get('filename'), f.get('size_bytes'))].append(f.get('id'))
        if f.get('downloadable') and str(f.get('mime_type', '')).startswith('text/'):
            raw, _ = client.request('GET', f'/v1/files/{ident(f["id"])}/content', accept='application/octet-stream')
            chunks.append(chunk(f['id'], raw.decode('utf-8', 'replace'), {'filename': f.get('filename')}, source_field='filename'))
    corpus = cfg.get('corpus_dir')
    if corpus:
        for path in sorted(Path(corpus).rglob('*')):
            if path.is_file() and path.suffix.lower() in ('.txt', '.md', '.json', '.csv', '.html'):
                chunks.append(chunk(str(path.relative_to(corpus)), path.read_text(encoding='utf-8', errors='replace'), {'source': path.name}))
    meta_dups = [{'dosya_adi': k[0], 'boyut': k[1], 'file_ids': ids} for k, ids in by_meta.items() if len(ids) > 1]
    return chunks, {'files_api_dosya': len(files), 'ayni_ad_ve_boyut': meta_dups, 'yerel_klasor': corpus or ''}


def langchain_read(cfg, factory, out):
    chunks = []
    path = Path(required(cfg, 'documents_file'))
    for i, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if not line.strip():
            continue
        doc = json.loads(line)
        if 'kwargs' in doc:  # langchain_core.load.dumpd() form
            doc = doc['kwargs']
        chunks.append(chunk(doc.get('id') or f'doc-{i + 1}', doc.get('page_content', ''), doc.get('metadata'), doc.get('embedding')))
        if len(chunks) >= limit_of(cfg):
            break
    return chunks, {'dosya': path.name, 'bicim': 'Document JSONL (page_content, metadata)'}


def langgraph_read(cfg, factory, out):
    client = factory(required(cfg, 'deployment_url'), {'x-api-key': secret('LANGSMITH_API_KEY')})
    namespace = names(cfg, 'namespace_prefix', pattern=re.compile(r'[^\x00-\x1f.]{1,128}'))
    text_field = cfg.get('text_field', 'text')
    chunks, offset = [], 0
    for _ in range(500):
        r = client.json('POST', '/store/items/search', {'namespace_prefix': namespace, 'limit': 100, 'offset': offset})
        items = r.get('items', [])
        for it in items:
            value = it.get('value') or {}
            text = value.get(text_field) if isinstance(value.get(text_field), str) else json.dumps(value, ensure_ascii=False)
            chunks.append(chunk('/'.join(it.get('namespace', [])) + '/' + str(it.get('key')), text, {k: v for k, v in value.items() if k != text_field},
                                text_field=text_field))
        offset += len(items)
        if len(items) < 100 or len(chunks) >= limit_of(cfg):
            break
    return chunks, {'namespace': namespace, 'metin_alani': text_field}


def llamaindex_read(cfg, factory, out):
    path = Path(required(cfg, 'docstore_file'))
    store = json.loads(path.read_text(encoding='utf-8'))
    data = store.get('docstore/data') or {}
    chunks = []
    for node_id, entry in list(data.items())[:limit_of(cfg)]:
        body = entry.get('__data__', entry) if isinstance(entry, dict) else {}
        if isinstance(body, str):
            body = json.loads(body)
        meta = body.get('metadata') or body.get('extra_info') or {}
        chunks.append(chunk(node_id, body.get('text', ''), meta, body.get('embedding')))
    return chunks, {'dosya': path.name, 'ref_doc': len(store.get('docstore/ref_doc_info') or {}), 'dugum': len(data)}


def pinecone_read(cfg, factory, out):
    host = required(cfg, 'index_host')
    client = factory(host if host.startswith('https://') else 'https://' + host, {'Api-Key': secret('PINECONE_API_KEY'), 'X-Pinecone-API-Version': '2025-04'})
    namespace, text_field = cfg.get('namespace', ''), cfg.get('text_field', 'text')
    stats = client.json('POST', '/describe_index_stats', {})
    ids, token = [], None
    for _ in range(1000):
        q = {'limit': 100, **({'namespace': namespace} if namespace else {}), **({'paginationToken': token} if token else {})}
        r = client.json('GET', '/vectors/list?' + urlencode(q))
        ids += [v.get('id') for v in r.get('vectors', [])]
        token = (r.get('pagination') or {}).get('next')
        if not token or len(ids) >= limit_of(cfg):
            break
    chunks = []
    for i in range(0, min(len(ids), limit_of(cfg)), 100):
        q = [('ids', x) for x in ids[i:i + 100]] + ([('namespace', namespace)] if namespace else [])
        vectors = client.json('GET', '/vectors/fetch?' + urlencode(q)).get('vectors') or {}
        for vid, v in vectors.items():
            meta = v.get('metadata') or {}
            chunks.append(chunk(vid, meta.get(text_field, ''), meta, v.get('values'), cfg.get('source_field'), text_field))
    return chunks, {'boyut': stats.get('dimension'), 'toplam_vektor': stats.get('totalVectorCount'), 'namespace': namespace, 'metric': stats.get('metric')}


def qdrant_read(cfg, factory, out):
    client = factory(required(cfg, 'server'), {'api-key': secret('QDRANT_API_KEY')})
    coll = ident(name(cfg, 'collection'))
    info = client.json('GET', '/collections/' + coll).get('result') or {}
    text_field = cfg.get('text_field', 'text')
    chunks, offset = [], None
    for _ in range(1000):
        body = {'limit': 256, 'with_payload': True, 'with_vector': True, **({'offset': offset} if offset is not None else {})}
        r = client.json('POST', f'/collections/{coll}/points/scroll', body).get('result') or {}
        for p in r.get('points', []):
            vec = p.get('vector')
            if isinstance(vec, dict):  # named vectors: use the configured one
                vec = vec.get(cfg.get('vector_name') or next(iter(vec), ''), None)
            payload = p.get('payload') or {}
            chunks.append(chunk(p.get('id'), payload.get(text_field, ''), payload, vec, cfg.get('source_field'), text_field))
        offset = r.get('next_page_offset')
        if offset is None or len(chunks) >= limit_of(cfg):
            break
    params = (info.get('config') or {}).get('params') or {}
    return chunks, {'nokta': info.get('points_count'), 'vektor_ayari': params.get('vectors'), 'hnsw': (info.get('config') or {}).get('hnsw_config'),
                    'payload_indeksleri': list((info.get('payload_schema') or {}).keys())}


def weaviate_read(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'WEAVIATE_API_KEY')
    cls = name(cfg, 'collection')
    schema = client.json('GET', '/v1/schema/' + ident(cls))
    text_field = cfg.get('text_field', 'text')
    chunks, after = [], None
    for _ in range(1000):
        q = {'class': cls, 'limit': 100, 'include': 'vector', **({'after': after} if after else {})}
        objs = client.json('GET', '/v1/objects?' + urlencode(q)).get('objects', [])
        for o in objs:
            props = o.get('properties') or {}
            chunks.append(chunk(o.get('id'), props.get(text_field, ''), props, o.get('vector'), cfg.get('source_field'), text_field))
        if len(objs) < 100 or len(chunks) >= limit_of(cfg):
            break
        after = objs[-1].get('id')
    return chunks, {'sinif': cls, 'vectorizer': schema.get('vectorizer'), 'vektor_indeksi': schema.get('vectorIndexType'),
                    'ozellikler': [{'alan': p.get('name'), 'tip': p.get('dataType')} for p in schema.get('properties', [])]}


def milvus_read(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'MILVUS_TOKEN')
    coll = name(cfg, 'collection')
    text_field, vector_field, pk = cfg.get('text_field', 'text'), cfg.get('vector_field', 'vector'), cfg.get('primary_field', 'id')
    desc = client.json('POST', '/v2/vectordb/collections/describe', {'collectionName': coll})
    if desc.get('code') not in (0, 200):
        raise NativeError('Milvus koleksiyonu okunamadı.')
    fields = [x for x in [pk, text_field, vector_field, *(cfg.get('metadata_fields') or [])] if x]
    chunks, offset = [], 0
    while offset < min(limit_of(cfg), 16384):
        r = client.json('POST', '/v2/vectordb/entities/query', {'collectionName': coll, 'filter': '', 'outputFields': fields,
                                                                'limit': min(1000, 16384 - offset), 'offset': offset})
        if r.get('code') not in (0, 200):
            raise NativeError('Milvus sorgusu hata döndürdü.')
        rows = r.get('data', [])
        for row in rows:
            chunks.append(chunk(row.get(pk), row.get(text_field, ''), row, row.get(vector_field), cfg.get('source_field'), text_field))
        offset += len(rows)
        if len(rows) < 1000:
            break
    data = desc.get('data') or {}
    return chunks, {'koleksiyon': coll, 'alanlar': [{'alan': f.get('name'), 'tip': f.get('type'), 'parametre': f.get('params')} for f in data.get('fields', [])],
                    'indeksler': data.get('indexes'), 'not': 'Milvus REST sorgusu offset+limit ≤ 16384 ile sınırlıdır.'}


def pgvector_read(cfg, factory, out):
    table, pk, text_col, vec_col = name(cfg, 'table'), name(cfg, 'id_column'), name(cfg, 'text_column'), name(cfg, 'embedding_column')
    meta = [name({'c': c}, 'c') for c in (cfg.get('metadata_columns') or [])]
    src = sql_source('postgres', cfg, factory, out)
    cols = ', '.join([f'{pk}::text AS id', f'{text_col} AS text', f'{vec_col}::text AS embedding'] + meta)
    _, rows = src.query(f'SELECT {cols} FROM {table} LIMIT {limit_of(cfg)}', 'chunks')
    chunks = [chunk(r['id'], r.get('text') or '', {m: r.get(m) for m in meta}, json.loads(r['embedding']) if r.get('embedding') else None, cfg.get('source_field'))
              for r in rows]
    _, idx = src.query("SELECT indexname, indexdef FROM pg_indexes WHERE indexdef ILIKE '%USING hnsw%' OR indexdef ILIKE '%USING ivfflat%'", 'vector_indexes')
    return chunks, {'tablo': table, 'vektor_indeksleri': idx}


PRODUCTS = {
    'openai': dict(label='OpenAI API (vector stores)', read=openai_read), 'claude': dict(label='Claude API', read=claude_read),
    'langchain': dict(label='LangChain', read=langchain_read), 'langgraph': dict(label='LangGraph', read=langgraph_read),
    'llamaindex': dict(label='LlamaIndex', read=llamaindex_read), 'pinecone': dict(label='Pinecone', read=pinecone_read),
    'qdrant': dict(label='Qdrant', read=qdrant_read), 'weaviate': dict(label='Weaviate', read=weaviate_read),
    'milvus': dict(label='Milvus', read=milvus_read), 'pgvector': dict(label='pgvector', read=pgvector_read),
}


def read(key, cfg, factory, out):
    chunks, info = PRODUCTS[key]['read'](cfg, factory, out)
    if not chunks:
        raise NativeError('Kaynaktan parça/doküman okunamadı; koleksiyon adı, alanlar ve yetkiyi kontrol edin.')
    return chunks, info


def duplicate_check(cfg, out, factory):
    key = cfg['operation'].rsplit('-rag-duplicates', 1)[0]
    chunks, info = read(key, cfg, factory, out)
    exact_rows, pairs, stats = analyse(chunks, cfg)
    write_csv(out, 'birebir_tekrar.csv', exact_rows, ['grup', 'parca', 'kaynak', 'ozet'])
    write_csv(out, 'benzer_parcalar.csv', sorted(pairs, key=lambda p: -p['benzerlik']), ['parca_1', 'parca_2', 'tur', 'benzerlik'])
    receipt(out, 'kaynak_bilgisi.json', info)
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': PRODUCTS[key]['label'], **stats, 'not': 'Hiçbir parça silinmedi; tekrarları kaynakta giderip indeksi yeniden oluşturun.'})


# ---------- RAG architecture & evaluation agent ----------
CHUNKING = {'sabit', 'cumle', 'baslik', 'semantik', 'mevcut_korunur'}
RETRIEVAL = {'vektor', 'hibrit', 'anahtar_kelime', 'hibrit_yeniden_siralama'}
METRICS = {'recall@k', 'precision@k', 'mrr', 'ndcg', 'hit_rate', 'faithfulness', 'answer_relevance', 'context_precision', 'context_recall', 'citation_accuracy'}
SCHEMA = ('{"parcalama":{"strateji":"sabit|cumle|baslik|semantik|mevcut_korunur","boyut_token":400,"ortusme_token":50,"gerekce":"..."},'
          '"getirme":{"yontem":"vektor|hibrit|anahtar_kelime|hibrit_yeniden_siralama","top_k":8,"metadata_filtreleri":["kanıttaki metadata alanı"],"gerekce":"..."},'
          '"temizlik":["tekrar giderme vb. adımlar"],"degerlendirme":{"metrikler":["recall@k","faithfulness"],"soru_sayisi":100,'
          '"soru_turleri":["olgusal","karşılaştırma","cevapsız"],"kabul_esikleri":{"recall@k":0.8}},"riskler":["prompt injection, yetki, güncellik"],"sorular":["..."]}')


def rag_design(cfg, out, factory):
    key = cfg['operation'].rsplit('-rag-design', 1)[0]
    chunks, info = read(key, cfg, factory, out)
    _, pairs, stats = analyse(chunks, cfg)
    fields = {m['alan'].lower(): m['alan'] for m in stats['metadata_alanlari']}
    evidence = {'depo': info, 'istatistik': stats}

    def validate(obj):
        p = obj.get('parcalama')
        if not isinstance(p, dict):
            raise ValueError('"parcalama" nesne olmalı.')
        need_text(p, 'strateji', 'parcalama', choices=CHUNKING)
        size, overlap = p.get('boyut_token'), p.get('ortusme_token', 0)
        if p['strateji'] != 'mevcut_korunur' and (not isinstance(size, int) or not 50 <= size <= 8000 or not isinstance(overlap, int) or not 0 <= overlap < size):
            raise ValueError('parcalama: boyut_token 50–8000, ortusme_token 0–boyut arası tamsayı olmalı.')
        g = obj.get('getirme')
        if not isinstance(g, dict):
            raise ValueError('"getirme" nesne olmalı.')
        need_text(g, 'yontem', 'getirme', choices=RETRIEVAL)
        if not isinstance(g.get('top_k'), int) or not 1 <= g['top_k'] <= 100:
            raise ValueError('getirme.top_k 1–100 tamsayı olmalı.')
        bad = [f for f in g.get('metadata_filtreleri') or [] if str(f).lower() not in fields]
        if bad:
            raise ValueError('getirme: kanıtta olmayan metadata alanı: ' + ', '.join(map(str, bad)))
        e = obj.get('degerlendirme')
        if not isinstance(e, dict):
            raise ValueError('"degerlendirme" nesne olmalı.')
        metrics = e.get('metrikler')
        if not isinstance(metrics, list) or not metrics or not set(metrics) <= METRICS:
            raise ValueError('degerlendirme.metrikler şunlardan seçilmeli: ' + ', '.join(sorted(METRICS)))
        n = e.get('soru_sayisi')
        if not isinstance(n, int) or not 10 <= n <= 5000:
            raise ValueError('degerlendirme.soru_sayisi 10–5000 tamsayı olmalı.')
        for m, v in (e.get('kabul_esikleri') or {}).items():
            if m not in metrics or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise ValueError('kabul_esikleri yalnızca seçilen metrikler için 0–1 olmalı.')
        return obj

    def render(result):
        rnd = random.Random(3)
        sample = rnd.sample(chunks, min(len(chunks), result['degerlendirme']['soru_sayisi']))
        template = '\n'.join(json.dumps({'soru': '', 'soru_turu': '', 'beklenen_kaynaklar': [c['id']], 'kaynak': c.get('kaynak', ''), 'beklenen_cevap': ''},
                                        ensure_ascii=False) for c in sample)
        p, g, e = result['parcalama'], result['getirme'], result['degerlendirme']
        md = rows_md('RAG mimarisi ve değerlendirme tasarımı — ' + PRODUCTS[key]['label'],
                     [('Parçalama', [p], ['strateji', 'boyut_token', 'ortusme_token', 'gerekce']),
                      ('Getirme', [g], ['yontem', 'top_k', 'metadata_filtreleri', 'gerekce']),
                      ('Değerlendirme', [{**e, 'kabul_esikleri': json.dumps(e.get('kabul_esikleri') or {}, ensure_ascii=False)}],
                       ['metrikler', 'soru_sayisi', 'soru_turleri', 'kabul_esikleri']),
                      ('Temizlik, riskler ve sorular', [{'madde': x} for x in result.get('temizlik', []) + result.get('riskler', []) + result.get('sorular', [])], ['madde'])])
        return {'rag_tasarimi.json': plan_json(result), 'rag_tasarimi.md': md, 'degerlendirme_sablonu.jsonl': template + '\n'}
    return agent_task(cfg, out, product=PRODUCTS[key]['label'], title='RAG mimarisi ve değerlendirme tasarımı', evidence=evidence, schema=SCHEMA,
                      validate=validate, render=render,
                      rules='Parça boyutunu kanıttaki uzunluk dağılımına göre gerekçelendir; tekrar oranı yüksekse temizlik adımı ekle. '
                            'Metadata filtresini yalnızca dolu oranı yüksek alanlardan seç.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-rag-duplicates'] = duplicate_check
    HANDLERS[_key + '-rag-design'] = rag_design
