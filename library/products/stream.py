"""Streaming: event export quality check on sampled messages or engine metrics (code) and an evidence-bound event schema &
stream architecture (agent).

Brokers are sampled without committing offsets: Kafka-compatible brokers (Kafka, Confluent Cloud, Event Hubs Kafka endpoint,
Redpanda) through explicit partition assignment, Kinesis through shard iterators, Pub/Sub through pull + immediate nack.
Processing engines (Flink, Spark Structured Streaming, Dataflow) are checked through their own runtime metrics."""
import base64
import datetime as dt
import json
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, aws_json, bearer, driver, ident, journal, name, names, need_list, need_text, number, opt_float, opt_int,
                           parse_time, plan_json, receipt, region_of, required, rows_md, secret, write_csv, NAME)

TOPIC = re.compile(r'[A-Za-z0-9._-]{1,249}')


# ---------- Message quality ----------
def flatten(value, prefix='', depth=0):
    out = {}
    if isinstance(value, dict) and depth < 4:
        for k, v in value.items():
            out.update(flatten(v, f'{prefix}{k}.', depth + 1) if isinstance(v, dict) else {f'{prefix}{k}': v})
    return out


def decode(raw):
    """Message value → (dict or None, format label). Confluent wire format (magic 0 + schema id) is reported, JSON is parsed."""
    if raw is None:
        return None, 'tombstone'
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if raw[:1] == b'\x00' and len(raw) > 5:
        schema_id = int.from_bytes(raw[1:5], 'big')
        try:
            return json.loads(raw[5:]), f'schema-registry json (id {schema_id})'
        except ValueError:
            return None, f'schema-registry ikili (id {schema_id})'
    try:
        value = json.loads(raw)
        return (value, 'json') if isinstance(value, dict) else (None, 'json (nesne değil)')
    except ValueError:
        return None, 'json değil'


def event_quality(events, cfg):
    """events: [{partition, offset, key, timestamp(datetime|None), value(bytes)}] → issues, field profile, summary."""
    required_fields = names(cfg, 'required_fields', limit=100, pattern=re.compile(r'[^\x00-\x1f]{1,128}'), optional=True)
    id_field, time_field = cfg.get('id_field') or '', cfg.get('event_time_field') or ''
    max_lag = opt_float(cfg, 'max_lag_seconds', 3600, 1, 10 ** 8)
    now = dt.datetime.now(dt.timezone.utc)
    issues, formats, fields, present = Counter(), Counter(), defaultdict(Counter), Counter()
    examples = defaultdict(list)
    ids, last_time = Counter(), {}
    lags = []
    def add(kind, ref):
        issues[kind] += 1
        if len(examples[kind]) < 5:
            examples[kind].append(ref)
    parsed = 0
    for e in events:
        ref = f"{e.get('partition')}:{e.get('offset')}"
        value, fmt = decode(e.get('value'))
        formats[fmt] += 1
        if value is None:
            if fmt != 'tombstone':
                add('çözümlenemeyen mesaj (' + fmt + ')', ref)
            continue
        parsed += 1
        flat = flatten(value)
        for k, v in flat.items():
            present[k] += 1
            fields[k][type(v).__name__ if v is not None else 'null'] += 1
        for f in required_fields:
            if flat.get(f) in (None, ''):
                add(f'zorunlu alan eksik: {f}', ref)
        key = flat.get(id_field) if id_field else e.get('key')
        if key not in (None, ''):
            ids[str(key)] += 1
            if ids[str(key)] == 2:
                add('tekrarlanan olay kimliği' if id_field else 'tekrarlanan mesaj anahtarı', ref)
        if time_field:
            t = parse_time(flat.get(time_field))
            if t is None:
                add(f'olay zamanı okunamadı: {time_field}', ref)
            else:
                if t > now + dt.timedelta(minutes=5):
                    add('gelecek olay zamanı', ref)
                if e.get('timestamp'):
                    lag = (e['timestamp'] - t).total_seconds()
                    lags.append(lag)
                    if lag > max_lag:
                        add(f'olay zamanı ile broker zamanı arası > {max_lag:g} sn', ref)
                part = e.get('partition')
                if part in last_time and t < last_time[part] - dt.timedelta(seconds=opt_float(cfg, 'out_of_order_tolerance_seconds', 0, 0, 86400)):
                    add('bölüm içinde sırasız olay zamanı', ref)
                last_time[part] = max(t, last_time.get(part, t))
    profile = [{'alan': k, 'doluluk': round(present[k] / parsed, 4) if parsed else 0, 'tipler': dict(fields[k])} for k in sorted(fields)]
    mixed = [p['alan'] for p in profile if len([t for t in p['tipler'] if t != 'null']) > 1]
    for f in mixed:
        add('alan tipi tutarsız', f)
    lags.sort()
    summary = {'ornek_mesaj': len(events), 'cozumlenen': parsed, 'bicimler': dict(formats), 'alan_sayisi': len(profile),
               'gecikme_sn': {'p50': round(lags[len(lags) // 2], 1), 'p95': round(lags[int(len(lags) * 0.95)], 1), 'max': round(lags[-1], 1)} if lags else {}}
    rows = [{'kontrol': k, 'adet': n, 'ornekler': '; '.join(map(str, examples[k]))} for k, n in sorted(issues.items())]
    return rows, profile, summary


# ---------- Kafka-compatible brokers ----------
def kafka_config(cfg, kind):
    conf = {'bootstrap.servers': required(cfg, 'bootstrap_servers'), 'group.id': 'datawork-quality-' + uuid.uuid4().hex[:12], 'enable.auto.commit': False,
            'enable.auto.offset.store': False, 'session.timeout.ms': 45000}
    if kind == 'event-hubs':
        conf.update({'security.protocol': 'SASL_SSL', 'sasl.mechanisms': 'PLAIN', 'sasl.username': '$ConnectionString', 'sasl.password': secret('EVENTHUBS_CONNECTION_STRING')})
    elif kind == 'confluent':
        conf.update({'security.protocol': 'SASL_SSL', 'sasl.mechanisms': 'PLAIN', 'sasl.username': secret('KAFKA_API_KEY'), 'sasl.password': secret('KAFKA_API_SECRET')})
    elif os.environ.get('KAFKA_USERNAME'):
        conf.update({'security.protocol': cfg.get('security_protocol', 'SASL_SSL'), 'sasl.mechanisms': cfg.get('sasl_mechanism', 'SCRAM-SHA-512'),
                     'sasl.username': secret('KAFKA_USERNAME'), 'sasl.password': secret('KAFKA_PASSWORD')})
    else:
        conf['security.protocol'] = cfg.get('security_protocol', 'SSL')
    return conf


def kafka_sample(cfg, kind):
    ck = driver('confluent_kafka')
    topic = name(cfg, 'topic', TOPIC)
    per_partition = opt_int(cfg, 'messages_per_partition', 500, 1, 20000)
    consumer = ck.Consumer(kafka_config(cfg, kind))
    events, meta = [], {}
    try:
        md = consumer.list_topics(topic, timeout=20).topics.get(topic)
        if md is None or md.error is not None:
            raise NativeError('Konu bulunamadı veya okunamıyor: ' + topic)
        assignments, ends = [], {}
        for p in sorted(md.partitions):
            low, high = consumer.get_watermark_offsets(ck.TopicPartition(topic, p), timeout=20)
            if high > low:
                assignments.append(ck.TopicPartition(topic, p, max(low, high - per_partition)))
                ends[p] = high
        meta = {'konu': topic, 'bolum': len(md.partitions), 'okunan_bolum': len(assignments)}
        consumer.assign(assignments)
        deadline = time.monotonic() + opt_int(cfg, 'max_seconds', 60, 5, 600)
        done = set()
        while len(done) < len(ends) and time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            ts_type, ts = msg.timestamp()
            events.append({'partition': msg.partition(), 'offset': msg.offset(), 'key': (msg.key() or b'').decode('utf-8', 'replace') or None,
                           'timestamp': dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc) if ts and ts > 0 else None, 'value': msg.value()})
            if msg.offset() >= ends[msg.partition()] - 1:
                done.add(msg.partition())
    finally:
        consumer.close()  # no commit: enable.auto.commit=False and commit() is never called
    return events, meta


def confluent_registry(cfg, factory):
    url = cfg.get('schema_registry_url')
    if not url:
        return {}
    client = factory(url, {'Authorization': native.basic_auth(secret('SR_API_KEY'), secret('SR_API_SECRET'))})
    subject = cfg.get('subject') or f"{cfg['topic']}-value"
    try:
        latest = client.json('GET', f'/subjects/{ident(subject)}/versions/latest')
        compat = client.json('GET', f'/config/{ident(subject)}?defaultToGlobal=true').get('compatibilityLevel')
    except NativeError:
        return {'konu_semasi': 'kayıt bulunamadı', 'subject': subject}
    return {'subject': subject, 'surum': latest.get('version'), 'tur': latest.get('schemaType', 'AVRO'), 'uyumluluk': compat, 'sema': str(latest.get('schema'))[:20000]}


# ---------- Kinesis ----------
def kinesis(factory, region, action, payload):
    return aws_json(factory, 'kinesis', region, 'Kinesis_20131202.' + action, payload)


def kinesis_sample(cfg, factory):
    region, stream = region_of(cfg), name(cfg, 'stream_name', TOPIC)
    minutes = opt_int(cfg, 'minutes', 60, 1, 10080)
    per_shard = opt_int(cfg, 'messages_per_partition', 500, 1, 10000)
    shards = kinesis(factory, region, 'ListShards', {'StreamName': stream}).get('Shards', [])
    events = []
    start = time.time() - minutes * 60
    for s in shards:
        it = kinesis(factory, region, 'GetShardIterator', {'StreamName': stream, 'ShardId': s['ShardId'], 'ShardIteratorType': 'AT_TIMESTAMP', 'Timestamp': start}).get('ShardIterator')
        got = 0
        for _ in range(50):
            if not it or got >= per_shard:
                break
            r = kinesis(factory, region, 'GetRecords', {'ShardIterator': it, 'Limit': min(1000, per_shard - got)})
            for rec in r.get('Records', []):
                ts = rec.get('ApproximateArrivalTimestamp')
                events.append({'partition': s['ShardId'], 'offset': rec.get('SequenceNumber'), 'key': rec.get('PartitionKey'),
                               'timestamp': dt.datetime.fromtimestamp(ts, dt.timezone.utc) if isinstance(ts, (int, float)) else parse_time(ts),
                               'value': base64.b64decode(rec.get('Data', ''))})
            got += len(r.get('Records', []))
            it = r.get('NextShardIterator')
            if not r.get('Records') and not r.get('MillisBehindLatest'):
                break
    return events, {'akis': stream, 'shard': len(shards), 'dakika': minutes}


# ---------- Pub/Sub ----------
def pubsub_sample(cfg, factory):
    client = bearer(factory, 'https://pubsub.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    sub = required(cfg, 'subscription')
    if not re.fullmatch(r'projects/[a-z][a-z0-9-]{4,29}/subscriptions/[A-Za-z][A-Za-z0-9._~+%-]{2,254}', sub):
        raise NativeError('subscription projects/P/subscriptions/S biçiminde olmalı.')
    limit = opt_int(cfg, 'max_messages', 500, 1, 5000)
    events = []
    for _ in range(50):
        r = client.json('POST', f'/v1/{sub}:pull', {'maxMessages': min(1000, limit - len(events))})
        msgs = r.get('receivedMessages', [])
        if not msgs:
            break
        # Never ack: the deadline is reset to 0 so the messages are redelivered to the real subscribers immediately.
        client.json('POST', f'/v1/{sub}:modifyAckDeadline', {'ackIds': [m['ackId'] for m in msgs], 'ackDeadlineSeconds': 0})
        for m in msgs:
            msg = m.get('message') or {}
            events.append({'partition': msg.get('orderingKey') or '-', 'offset': msg.get('messageId'), 'key': (msg.get('attributes') or {}).get(cfg.get('key_attribute', '')) or None,
                           'timestamp': parse_time(msg.get('publishTime')), 'value': base64.b64decode(msg.get('data', '')), 'teslim': m.get('deliveryAttempt')})
        if len(events) >= limit:
            break
    return events, {'abonelik': sub, 'not': 'Mesajlar onaylanmadı (nack); ölü mektup politikası olan aboneliklerde teslim denemesi sayısı artar — ayrı bir kalite aboneliği kullanın.'}


# ---------- Processing engines ----------
def flink_check(cfg, factory):
    client = factory(required(cfg, 'server'), {'Authorization': 'Bearer ' + os.environ['FLINK_TOKEN']} if os.environ.get('FLINK_TOKEN') else {})
    job = required(cfg, 'job_id')
    if not re.fullmatch(r'[0-9a-f]{32}', job):
        raise NativeError('job_id 32 haneli Flink iş kimliği olmalı.')
    info = client.json('GET', f'jobs/{job}')
    rows, metrics = [], []
    now = time.time() * 1000
    for v in info.get('vertices', []):
        vid = v['id']
        raw, _ = client.request('GET', f'jobs/{job}/vertices/{vid}/subtasks/metrics?get=numRecordsIn,numRecordsOut,numLateRecordsDropped&agg=sum')
        m = {x['id']: x.get('sum') for x in json.loads(raw)} if raw else {}
        wm = client.json('GET', f'jobs/{job}/vertices/{vid}/backpressure')
        raw, _ = client.request('GET', f'jobs/{job}/vertices/{vid}/watermarks')
        marks = [number(w.get('value')) for w in (json.loads(raw) if raw else []) if number(w.get('value'))]
        low = min(marks) if marks else None
        lag = (now - low) / 1000 if low and 0 < low < now else None
        metrics.append({'operator': v.get('name'), 'girdi': m.get('numRecordsIn'), 'cikti': m.get('numRecordsOut'), 'gec_atilan': m.get('numLateRecordsDropped'),
                        'backpressure': wm.get('backpressureLevel') or wm.get('backpressure-level'), 'filigran_gecikme_sn': round(lag, 1) if lag is not None else ''})
    for m in metrics:
        if number(m['gec_atilan']):
            rows.append({'kontrol': 'geç geldiği için atılan kayıt', 'adet': int(number(m['gec_atilan'])), 'ornekler': m['operator']})
        if str(m['backpressure']).lower() == 'high':
            rows.append({'kontrol': 'yüksek backpressure', 'adet': 1, 'ornekler': m['operator']})
        if m['filigran_gecikme_sn'] != '' and m['filigran_gecikme_sn'] > opt_float(cfg, 'max_lag_seconds', 3600, 1, 10 ** 8):
            rows.append({'kontrol': 'filigran gecikmesi eşik üstü', 'adet': 1, 'ornekler': f"{m['operator']} ({m['filigran_gecikme_sn']} sn)"})
    cp = client.json('GET', f'jobs/{job}/checkpoints').get('counts') or {}
    if cp.get('failed'):
        rows.append({'kontrol': 'başarısız checkpoint', 'adet': cp['failed'], 'ornekler': ''})
    return rows, metrics, {'is': info.get('name'), 'durum': info.get('state'), 'checkpoint': cp}


SPARK_PROGRESS = 'org.apache.spark.sql.streaming.StreamingQueryListener$QueryProgressEvent'


def spark_check(cfg, factory):
    path = Path(required(cfg, 'event_log_file'))
    progress = []
    with path.open(encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if SPARK_PROGRESS in line:
                try:
                    progress.append(json.loads(line).get('progress') or {})
                except ValueError:
                    continue
    if not progress:
        raise NativeError('Olay günlüğünde Structured Streaming ilerleme olayı yok (spark.eventLog.enabled açık mı?).')
    rows = Counter()
    examples = defaultdict(list)
    metrics = []
    trigger = opt_float(cfg, 'trigger_interval_seconds', 0, 0, 86400)
    for p in progress:
        batch = p.get('batchId')
        dropped = sum(int(number(s.get('numRowsDroppedByWatermark')) or 0) for s in p.get('stateOperators') or [])
        rate_in, rate_out = number(p.get('inputRowsPerSecond')) or 0, number(p.get('processedRowsPerSecond')) or 0
        duration = (number((p.get('durationMs') or {}).get('triggerExecution')) or 0) / 1000
        et = p.get('eventTime') or {}
        lag = (parse_time(et['max']) - parse_time(et['watermark'])).total_seconds() if et.get('max') and et.get('watermark') and parse_time(et['max']) and parse_time(et['watermark']) else None
        metrics.append({'sorgu': p.get('name') or p.get('id'), 'batch': batch, 'girdi': p.get('numInputRows'), 'girdi_hizi': rate_in, 'islem_hizi': rate_out, 'sure_sn': duration,
                        'filigran_atilan': dropped, 'filigran_farki_sn': lag if lag is not None else ''})
        for kind, cond in (('filigran nedeniyle atılan satır', dropped > 0), ('işlem hızı girdi hızının altında', rate_in and rate_out and rate_out < rate_in),
                           ('batch süresi tetikleme aralığını aşıyor', trigger and duration > trigger)):
            if cond:
                rows[kind] += dropped if kind.startswith('filigran') else 1
                if len(examples[kind]) < 5:
                    examples[kind].append(str(batch))
    return [{'kontrol': k, 'adet': n, 'ornekler': '; '.join(examples[k])} for k, n in rows.items()], metrics, {'ilerleme_olayi': len(progress)}


def dataflow_check(cfg, factory):
    client = bearer(factory, 'https://dataflow.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    project, location, job = required(cfg, 'project_id'), required(cfg, 'location'), required(cfg, 'job_id')
    info = client.json('GET', f'/v1b3/projects/{ident(project)}/locations/{ident(location)}/jobs/{ident(job)}')
    metrics = client.json('GET', f'/v1b3/projects/{ident(project)}/locations/{ident(location)}/jobs/{ident(job)}/metrics').get('metrics', [])
    rows, table = [], []
    for m in metrics:
        n = (m.get('name') or {})
        if n.get('origin') == 'dataflow/v1b3' and n.get('name') == 'ElementCount' and not (n.get('context') or {}).get('tentative'):
            table.append({'adim': (n.get('context') or {}).get('output_user_name') or (n.get('context') or {}).get('original_name'), 'oge': m.get('scalar')})
        if 'droppedduetolateness' in str(n.get('name', '')).lower().replace('_', '') and number(m.get('scalar')):
            rows.append({'kontrol': 'geç geldiği için atılan öğe', 'adet': int(number(m['scalar'])), 'ornekler': (n.get('context') or {}).get('step', '')})
    if info.get('currentState') in ('JOB_STATE_FAILED', 'JOB_STATE_CANCELLED'):
        rows.append({'kontrol': 'iş çalışmıyor', 'adet': 1, 'ornekler': info.get('currentState')})
    return rows, table, {'is': info.get('name'), 'durum': info.get('currentState'), 'tur': info.get('type')}


BROKERS = {'kafka': 'kafka', 'confluent-cloud': 'confluent', 'event-hubs': 'event-hubs', 'redpanda': 'kafka'}
ENGINES = {'flink': ('Apache Flink', flink_check), 'spark-streaming': ('Spark Structured Streaming', spark_check), 'dataflow': ('Dataflow', dataflow_check)}
LABELS = {'kafka': 'Apache Kafka', 'confluent-cloud': 'Confluent Cloud', 'event-hubs': 'Azure Event Hubs', 'redpanda': 'Redpanda', 'kinesis': 'Kinesis', 'pubsub': 'Pub/Sub',
          **{k: v[0] for k, v in ENGINES.items()}}


def sample(key, cfg, factory):
    if key in BROKERS:
        return kafka_sample(cfg, BROKERS[key])
    if key == 'kinesis':
        return kinesis_sample(cfg, factory)
    return pubsub_sample(cfg, factory)


def quality_check(cfg, out, factory):
    key = cfg['operation'].rsplit('-event-quality', 1)[0]
    if key in ENGINES:
        rows, metrics, meta = ENGINES[key][1](cfg, factory)
        write_csv(out, 'kontrol_sonuclari.csv', rows, ['kontrol', 'adet', 'ornekler'])
        write_csv(out, 'metrikler.csv', metrics)
        journal(out, state='verified')
        return receipt(out, 'ozet.json', {'urun': LABELS[key], **meta, 'bulgu': len(rows)})
    events, meta = sample(key, cfg, factory)
    if not events:
        raise NativeError('Örneklenecek mesaj bulunamadı (konu/akış boş veya zaman penceresi dar).')
    rows, profile, summary = event_quality(events, cfg)
    write_csv(out, 'kontrol_sonuclari.csv', rows, ['kontrol', 'adet', 'ornekler'])
    write_csv(out, 'alan_profili.csv', [{**p, 'tipler': json.dumps(p['tipler'])} for p in profile], ['alan', 'doluluk', 'tipler'])
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': LABELS[key], **meta, **summary, 'bulgu': sum(r['adet'] for r in rows),
                                      'not': 'Mesaj değerleri kaydedilmedi; yalnızca alan adları, tipleri ve konumlar raporlandı. Offset işlenmedi.'})


# ---------- Event schema & stream architecture agent ----------
def topology(key, cfg, factory):
    if key == 'flink':
        client = factory(required(cfg, 'server'), {})
        plan = client.json('GET', f"jobs/{required(cfg, 'job_id')}/plan").get('plan') or {}
        return {'operatorler': [{'ad': n.get('description', '')[:200], 'paralellik': n.get('parallelism'), 'girdiler': [i.get('id') for i in n.get('inputs', [])], 'id': n.get('id')}
                                for n in plan.get('nodes', [])]}
    if key == 'dataflow':
        client = bearer(factory, 'https://dataflow.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
        job = client.json('GET', f"/v1b3/projects/{ident(cfg['project_id'])}/locations/{ident(cfg['location'])}/jobs/{ident(cfg['job_id'])}?view=JOB_VIEW_DESCRIPTION")
        return {'adimlar': [{'ad': s.get('name'), 'tur': s.get('kind')} for s in (job.get('steps') or [])][:200]}
    return {}


SCHEMA = ('{"olaylar":[{"ad":"OrderCreated","konu":"kanıttaki konu/akış","anahtar":"alan","alanlar":[{"alan":"kanıttaki alan","tip":"string|long|double|boolean|timestamp","zorunlu":true}],'
          '"uyumluluk":"BACKWARD|BACKWARD_TRANSITIVE|FORWARD|FULL|FULL_TRANSITIVE|NONE"}],"konular":[{"konu":"...","yeni":false,"bolum":12,"saklama_saat":168,"temizleme":"delete|compact"}],'
          '"akislar":[{"kaynak":"konu","hedef":"konu/tablo","islem":"...","pencere":"5m tumbling","filigran_sn":60}],"riskler":["..."],"sorular":["..."]}')
AVRO = {'string': 'string', 'long': 'long', 'double': 'double', 'boolean': 'boolean', 'timestamp': {'type': 'long', 'logicalType': 'timestamp-millis'}}


def architecture(cfg, out, factory):
    key = cfg['operation'].rsplit('-event-architecture', 1)[0]
    label = LABELS[key]
    evidence = {}
    if key in ENGINES:
        rows, metrics, meta = ENGINES[key][1](cfg, factory)
        evidence = {'is': meta, 'metrikler': metrics, 'bulgular': rows, 'topoloji': topology(key, cfg, factory)}
        topics, fields = {str(m.get('operator') or m.get('adim') or m.get('sorgu')).lower() for m in metrics}, set()
    else:
        events, meta = sample(key, cfg, factory)
        rows, profile, summary = event_quality(events, cfg)
        evidence = {'kaynak': meta, 'ozet': summary, 'alan_profili': profile, 'bulgular': rows}
        if key == 'confluent-cloud':
            evidence['schema_registry'] = confluent_registry(cfg, factory)
        topics = {str(meta.get('konu') or meta.get('akis') or meta.get('abonelik')).lower()}
        fields = {p['alan'].lower() for p in profile}

    def validate(obj):
        known_topics = set(topics)
        for t in obj.get('konular') or []:
            if not isinstance(t, dict):
                raise ValueError('konular nesne listesi olmalı.')
            topic = need_text(t, 'konu', 'konu')
            if topic.lower() not in topics and not t.get('yeni'):
                raise ValueError(f'konu kanıtta yok; yeni ise "yeni": true yazın: {topic}')
            if not TOPIC.fullmatch(topic):
                raise ValueError('konu adı geçersiz: ' + topic)
            for k, lo, hi in (('bolum', 1, 1000), ('saklama_saat', 1, 24 * 3650)):
                if not isinstance(t.get(k), int) or not lo <= t[k] <= hi:
                    raise ValueError(f'{topic}: {k} {lo}–{hi} tamsayı olmalı.')
            need_text(t, 'temizleme', topic, choices={'delete', 'compact'})
            known_topics.add(topic.lower())
        for e in need_list(obj, 'olaylar', 'olaylar', maximum=50):
            ename = need_text(e, 'ad', 'olay')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,99}', ename):
                raise ValueError('olay adı Avro adı olmalı: ' + ename)
            need_text(e, 'uyumluluk', ename, choices={'BACKWARD', 'BACKWARD_TRANSITIVE', 'FORWARD', 'FORWARD_TRANSITIVE', 'FULL', 'FULL_TRANSITIVE', 'NONE'})
            if key not in ENGINES and str(e.get('konu', '')).lower() not in known_topics:
                raise ValueError(f'{ename}: konu kanıtta veya konular listesinde yok.')
            for f in need_list(e, 'alanlar', ename + ' alanları', maximum=300):
                fname = need_text(f, 'alan', ename)
                need_text(f, 'tip', fname, choices=set(AVRO))
                if fields and fname.lower() not in fields and not f.get('yeni'):
                    raise ValueError(f'{ename}: örneklemde olmayan alan (yeni ise "yeni": true): {fname}')
        return obj

    def render(result):
        files = {'akis_mimarisi.json': plan_json(result),
                 'akis_mimarisi.md': rows_md('Olay şeması ve akış mimarisi — ' + label,
                                             [('Olaylar', [{**e, 'alanlar': ', '.join(f['alan'] for f in e['alanlar'])} for e in result['olaylar']], ['ad', 'konu', 'anahtar', 'uyumluluk', 'alanlar']),
                                              ('Konular', result.get('konular') or [], ['konu', 'yeni', 'bolum', 'saklama_saat', 'temizleme']),
                                              ('Akışlar', result.get('akislar') or [], ['kaynak', 'hedef', 'islem', 'pencere', 'filigran_sn']),
                                              ('Riskler ve sorular', [{'madde': x} for x in result.get('riskler', []) + result.get('sorular', [])], ['madde'])])}
        for e in result['olaylar']:
            schema = {'type': 'record', 'name': e['ad'], 'namespace': cfg.get('avro_namespace', 'com.datawork.events'),
                      'fields': [{'name': re.sub(r'[^A-Za-z0-9_]', '_', f['alan']), 'type': AVRO[f['tip']] if f.get('zorunlu') else ['null', AVRO[f['tip']]],
                                  **({} if f.get('zorunlu') else {'default': None})} for f in e['alanlar']]}
            files[f"{e['ad']}.avsc"] = json.dumps(schema, ensure_ascii=False, indent=2)
        files['konu_ayarlari.json'] = json.dumps([{'topic_name': t['konu'], 'partitions_count': t['bolum'], 'configs': [
            {'name': 'retention.ms', 'value': str(t['saklama_saat'] * 3600000)}, {'name': 'cleanup.policy', 'value': t['temizleme']}]} for t in result.get('konular') or []], ensure_ascii=False, indent=2)
        return files
    return agent_task(cfg, out, product=label, title='Olay şeması ve akış mimarisi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Alanları yalnızca örneklem profilinden seç; tutarsız tipli alanları riskler bölümünde belirt. Bölüm sayısını anahtar dağılımı ve tüketici paralelliğine göre gerekçelendir.')


HANDLERS = {}
for _key in LABELS:
    HANDLERS[_key + '-event-quality'] = quality_check
    HANDLERS[_key + '-event-architecture'] = architecture
