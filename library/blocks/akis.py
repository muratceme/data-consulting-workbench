"""Akış (streaming) blokları: olaylardan şema çıkarma, Avro/JSON şema uyumluluğu, olay gecikmesi ve Kafka tüketici gecikmesi.
Broker’a veya şema kayıt servisine bağlanmaz; dışa aktarılmış örnekleri ve komut çıktılarını okur."""
import json, math, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import tabular
from islem import op, arg, Result, need, fmt

GENEL = 'Olay şemaları ve örnekleri'


def load_events(path):
    rows, bad = [], 0
    for line in Path(path).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            rows.append(obj if isinstance(obj, dict) else {'deger': obj})
        except json.JSONDecodeError:
            bad += 1
    if not rows:
        raise ValueError('JSON Lines olay bulunamadı.')
    return rows, bad


def jtype(v):
    return 'null' if v is None else 'boolean' if isinstance(v, bool) else 'integer' if isinstance(v, int) else 'number' if isinstance(v, float) else \
        'string' if isinstance(v, str) else 'array' if isinstance(v, list) else 'object'


def collect(obj, prefix, stats):
    for k, v in obj.items():
        path = f'{prefix}.{k}' if prefix else k
        s = stats[path]
        s['types'][jtype(v)] += 1
        s['count'] += 1
        if isinstance(v, (str, int, float, bool)) and len(s['values']) <= 30:
            s['values'][v] += 1
        if isinstance(v, dict):
            collect(v, path, stats)


@op('olay-semasi-cikar', title='Olay örneklerinden JSON Schema çıkar', domain='stream', product=GENEL, tag='şema çıkarma', kind='file', samples={'olaylar.jsonl': 'olaylar.jsonl'},
    args=[arg('--enum-max', type=int, default=5, help='Bu kadar veya daha az farklı değerli metin alanları enum yapılır')],
    description='JSON Lines olay örneklerinden iç içe alanların tiplerini, zorunluluğunu (her olayda var mı), düşük kardinaliteli alanlar için izinli değerleri ve tarih-saat biçimini çıkararak JSON Schema (draft 2020-12) üretir; aynı alanın farklı tiplerle geldiği üretici hatalarını raporlar.',
    input='.jsonl olay örneği', output='sema.json + sonuc.csv (alan profili)', command='python main.py --input ornek_olaylar.jsonl --enum-max 10',
    expect={'olay': 200, 'tip_catismasi': 1, 'opsiyonel_alan': 1})
def infer_schema(args):
    events, bad = load_events(args.input)
    stats = defaultdict(lambda: {'types': Counter(), 'count': 0, 'values': Counter()})
    for e in events:
        collect(e, '', stats)
    out, conflicts = [], 0

    def node(prefix):
        props, required = {}, []
        for path in sorted(p for p in stats if p.rsplit('.', 1)[0] == prefix and p != prefix or ('.' not in p and not prefix)):
            if (path.rsplit('.', 1)[0] if '.' in path else '') != prefix:
                continue
            s = stats[path]
            name = path.rsplit('.', 1)[-1]
            types = [t for t, _ in s['types'].most_common()]
            parent_count = len(events) if not prefix else stats[prefix]['types']['object']
            if s['count'] >= parent_count:
                required.append(name)
            if 'object' in types:
                props[name] = node(path)
            else:
                t = types[0] if len(types) == 1 else types
                sch = {'type': t}
                strs = [v for v in s['values'] if isinstance(v, str)]
                if types == ['string'] and strs and all(re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', v) for v in strs):
                    sch['format'] = 'date-time'
                elif types == ['string'] and 0 < len(s['values']) <= args.enum_max and s['count'] >= 10:
                    sch['enum'] = sorted(s['values'])
                props[name] = sch
        return {'type': 'object', 'properties': props, 'required': required}
    schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema', **node('')}
    for path, s in sorted(stats.items()):
        types = dict(s['types'])
        conflict = len([t for t in types if t != 'null']) > 1
        conflicts += conflict
        parent = path.rsplit('.', 1)[0] if '.' in path else ''
        parent_count = len(events) if not parent else stats[parent]['types']['object']
        out.append({'alan': path, 'tipler': ', '.join(f'{t} ({n})' for t, n in s['types'].most_common()), 'bulunma_%': round(100 * s['count'] / parent_count, 1),
                    'farkli_deger': len(s['values']) if len(s['values']) <= 30 else '30+', 'uyari': 'tip çatışması' if conflict else ''})
    optional = sum(1 for o in out if o['bulunma_%'] < 100)
    return Result(f'{len(events)} olay, {len(out)} alan; {conflicts} tip çatışması, {optional} opsiyonel alan' + (f'; {bad} bozuk satır' if bad else ''), list(out[0]), out,
                  {'olay': len(events), 'alan': len(out), 'tip_catismasi': conflicts, 'opsiyonel_alan': optional}, files={'sema.json': json.dumps(schema, ensure_ascii=False, indent=2)},
                  notes=['Şema örneklerden çıkarılır; örneklerde görülmeyen değer ve alanları kapsamaz. Tip çatışmasında üreticiyi düzeltin, şemayı gevşetmeyin.'])


def avro_fields(schema):
    return {f['name']: f for f in schema.get('fields', [])}


def avro_type(f):
    t = f['type']
    return tuple(sorted(x if isinstance(x, str) else x.get('type', 'complex') for x in t)) if isinstance(t, list) else (t if isinstance(t, str) else t.get('type', 'complex'),)


PROMOTE = {('int', 'long'), ('int', 'float'), ('int', 'double'), ('long', 'float'), ('long', 'double'), ('float', 'double'), ('string', 'bytes'), ('bytes', 'string')}


@op('sema-uyumlulugu', title='Avro şema sürümleri arasında uyumluluk kontrolü', domain='stream', product='Kafka', tag='schema registry', kind='file',
    samples={'semalar/siparis_v1.avsc': 'semalar/siparis_v1.avsc', 'semalar/siparis_v2.avsc': 'semalar/siparis_v2.avsc'}, sample_input='semalar/siparis_v1.avsc',
    args=[arg('--new', default='@ornek/semalar/siparis_v2.avsc')],
    description='Eski ve yeni Avro şemasını Schema Registry kurallarına göre karşılaştırır: geriye (yeni tüketici eski veriyi okur) ve ileriye (eski tüketici yeni veriyi okur) uyumluluğu bozan değişiklikleri — varsayılansız eklenen/silinen alan, yükseltilemeyen tip değişikliği — listeler ve hangi uyumluluk modunun geçeceğini söyler.',
    input='Eski .avsc + yeni .avsc', output='sonuc.csv (alan değişiklikleri) + rapor', command='python main.py --input v1.avsc --new v2.avsc',
    expect={'degisiklik': 4, 'geriye_uyumlu': False, 'ileriye_uyumlu': False})
def compat(args):
    try:
        old = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        new = json.loads(Path(args.new).read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Şema okunamadı: {exc}') from None
    fo, fn = avro_fields(old), avro_fields(new)
    out = []
    for name in sorted(set(fo) | set(fn)):
        a, b = fo.get(name), fn.get(name)
        if a and not b:
            out.append({'alan': name, 'degisiklik': 'silindi', 'geriye': 'evet', 'ileriye': 'evet' if 'default' in a else 'HAYIR', 'aciklama': 'eski tüketiciler alanı varsayılanla doldurur' if 'default' in a else 'eski tüketici zorunlu alanı bulamaz'})
        elif b and not a:
            out.append({'alan': name, 'degisiklik': 'eklendi', 'geriye': 'evet' if 'default' in b else 'HAYIR', 'ileriye': 'evet', 'aciklama': 'varsayılan var' if 'default' in b else 'yeni tüketici eski veride alanı bulamaz (varsayılan ekleyin)'})
        else:
            ta, tb = avro_type(a), avro_type(b)
            if ta != tb:
                back = ta == tb or (len(ta) == 1 and len(tb) == 1 and (ta[0], tb[0]) in PROMOTE) or set(ta) <= set(tb)
                fwd = (len(ta) == 1 and len(tb) == 1 and (tb[0], ta[0]) in PROMOTE) or set(tb) <= set(ta)
                out.append({'alan': name, 'degisiklik': f"tip {'/'.join(ta)} → {'/'.join(tb)}", 'geriye': 'evet' if back else 'HAYIR', 'ileriye': 'evet' if fwd else 'HAYIR',
                            'aciklama': 'Avro tip yükseltme kuralı' if back or fwd else 'yükseltilemez tip değişikliği'})
    back = all(o['geriye'] == 'evet' for o in out)
    fwd = all(o['ileriye'] == 'evet' for o in out)
    mode = 'FULL' if back and fwd else 'BACKWARD' if back else 'FORWARD' if fwd else 'NONE (uyumsuz)'
    return Result(f'{len(out)} değişiklik; geçen uyumluluk modu: {mode}', ['alan', 'degisiklik', 'geriye', 'ileriye', 'aciklama'], out,
                  {'degisiklik': len(out), 'geriye_uyumlu': back, 'ileriye_uyumlu': fwd, 'mod': mode},
                  notes=['Yalnızca kayıt (record) düzeyindeki alanlar karşılaştırılır; iç içe kayıt, enum sembolleri ve alias kuralları ayrıca kontrol edilmelidir.'])


def parse_ts(v):
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None


@op('olay-gecikmesi', title='Olay gecikmesi, sıra dışı gelme ve watermark önerisi', domain='stream', product=GENEL, tag='gecikme', kind='file', samples={'olaylar.jsonl': 'olaylar.jsonl'},
    args=[arg('--event-time', default='olay_zamani'), arg('--ingest-time', default='alinma_zamani')],
    description='Olayların gerçekleşme ve sisteme alınma zamanlarından gecikme dağılımını (p50, p95, p99, en büyük), sıra dışı gelen olay oranını ve geç gelen olayları kaçırmamak için önerilen watermark/izin verilen gecikme süresini hesaplar. Flink, Spark Streaming ve Kafka Streams pencere ayarı için.',
    input='.jsonl olay örneği (iki zaman alanı)', output='sonuc.csv (gecikme dilimleri) + en geç olaylar', command='python main.py --event-time event_ts --ingest-time kafka_ts',
    expect={'olay': 200, 'p50_sn': 1.5})
def lateness(args):
    events, _ = load_events(args.input)
    rows = []
    for e in events:
        a, b = parse_ts(e.get(args.event_time)), parse_ts(e.get(args.ingest_time))
        if a and b:
            rows.append((b, a, (b - a).total_seconds(), e))
    if not rows:
        raise ValueError(f'"{args.event_time}" ve "{args.ingest_time}" alanları ISO zaman olarak okunamadı.')
    rows.sort(key=lambda x: x[0])
    delays = sorted(d for _, _, d, _ in rows)
    q = lambda p: delays[min(len(delays) - 1, int(round(p * (len(delays) - 1))))]
    max_seen, ooo = None, 0
    for _, a, _, _ in rows:
        if max_seen and a < max_seen:
            ooo += 1
        max_seen = max(max_seen, a) if max_seen else a
    buckets = [(0, 1), (1, 5), (5, 60), (60, 600), (600, float('inf'))]
    out = [{'gecikme': f'{lo:g}–{hi:g} sn' if hi != float('inf') else f'>{lo:g} sn', 'olay': sum(lo <= d < hi for d in delays), 'yuzde': round(100 * sum(lo <= d < hi for d in delays) / len(delays), 2)}
           for lo, hi in buckets]
    worst = sorted(rows, key=lambda x: -x[2])[:10]
    return Result(f'{len(rows)} olay; gecikme p50 {q(.5):g} sn, p99 {q(.99):g} sn, en büyük {delays[-1]:.0f} sn; sıra dışı %{100 * ooo / len(rows):.1f}; önerilen watermark ≈ {q(.99):.0f} sn',
                  ['gecikme', 'olay', 'yuzde'], out,
                  {'olay': len(rows), 'p50_sn': round(q(.5), 2), 'p95_sn': round(q(.95), 2), 'p99_sn': round(q(.99), 2), 'en_buyuk_sn': round(delays[-1], 2), 'sira_disi_%': round(100 * ooo / len(rows), 2)},
                  tables=[('En geç gelen olaylar', ['Olay', 'Olay zamanı', 'Gecikme (sn)'], [[w[3].get('olay_id', ''), w[1].isoformat(), round(w[2], 1)] for w in worst])],
                  notes=['Watermark’ı p99’a göre seçmek olayların ~%1’ini geç sayar; geç olaylar için yan çıktı (side output) veya allowed lateness tanımlayın.'])


@op('kafka-tuketici-gecikmesi', title='Kafka tüketici grubu gecikmesi (lag) analizi', domain='stream', product='Kafka', tag='consumer lag', kind='file', samples={'consumer_groups.txt': 'consumer_groups.txt'},
    args=[arg('--max-lag', type=int, default=10000)],
    description='kafka-consumer-groups.sh --describe çıktısını okuyarak grup, topic ve partition bazında gecikmeyi (lag) toplar; tüketicisi olmayan (atanmamış) partition’ları, tek partition’da yığılan gecikmeyi (dengesiz/sıkışmış tüketici) ve eşiği aşan grupları işaretler.',
    input='kafka-consumer-groups --describe çıktısı (.txt)', output='sonuc.csv (partition bazında) + grup özeti', command='python main.py --input lag.txt --max-lag 50000',
    expect={'grup': 2, 'partition': 5, 'tuketicisiz_partition': 2, 'esik_ustu_grup': 2})
def lag(args):
    rows = []
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[2].isdigit() and parts[0] != 'GROUP':
            lag_v = int(parts[5]) if parts[5].lstrip('-').isdigit() else None
            rows.append({'grup': parts[0], 'topic': parts[1], 'partition': int(parts[2]), 'mevcut_offset': parts[3], 'son_offset': parts[4], 'lag': lag_v if lag_v is not None else '',
                         'tuketici': '' if len(parts) < 7 or parts[6] == '-' else parts[6]})
    if not rows:
        raise ValueError('Komut çıktısında partition satırı bulunamadı (GROUP TOPIC PARTITION … başlıklı tablo bekleniyor).')
    groups = defaultdict(list)
    for r in rows:
        groups[(r['grup'], r['topic'])].append(r)
    table, over = [], 0
    for (g, t), rs in sorted(groups.items()):
        total = sum(r['lag'] or 0 for r in rs)
        top = max(rs, key=lambda r: r['lag'] or 0)
        issues = []
        if any(not r['tuketici'] for r in rs):
            issues.append('tüketicisi olmayan partition var (grup çalışmıyor?)')
        if total and (top['lag'] or 0) / total > 0.8 and len(rs) > 1:
            issues.append(f"gecikmenin %{100 * top['lag'] / total:.0f}’i partition {top['partition']}’de (sıcak anahtar veya sıkışmış tüketici)")
        if total > args.max_lag:
            over += 1
        table.append([g, t, len(rs), total, '; '.join(issues)])
    return Result(f'{len({r["grup"] for r in rows})} grup, {len(rows)} partition; {over} grup eşik üstünde', list(rows[0]), rows,
                  {'grup': len({r['grup'] for r in rows}), 'partition': len(rows), 'tuketicisiz_partition': sum(1 for r in rows if not r['tuketici']), 'esik_ustu_grup': over},
                  tables=[('Grup özeti', ['Grup', 'Topic', 'Partition', 'Toplam lag', 'Uyarı'], table)])


def fields(line):
    out = {}
    for tok in re.split(r'\t+|\s{2,}', line.strip()):
        k, sep, v = tok.partition(':')
        if sep:
            out[k.strip()] = v.strip()
    return out


def brokers(v):
    return [b for b in re.split(r'[,\s]+', v or '') if b]


@op('kafka-konu-denetimi', title='Kafka topic yapılandırma ve replikasyon denetimi', domain='stream', product='Kafka', tag='topic denetimi', kind='file', samples={'kafka_topics.txt': 'kafka_topics.txt'},
    args=[arg('--min-rf', type=int, default=3, help='Beklenen en düşük replikasyon faktörü')],
    description='kafka-topics.sh --describe çıktısını okur: replikasyon faktörü düşük topic’leri, min.insync.replicas eksik veya replikasyon faktörüne eşit (tek broker kaybında yazmayı durduran) ayarları, ISR’ı eksik (under-replicated) ve lideri olmayan (offline) partition’ları, tercih edilen lideri dışında çalışan partition’ları ve broker başına lider dağılımını raporlar.',
    input='kafka-topics --describe çıktısı (.txt)', output='sonuc.csv (topic bazında bulgular) + partition ve lider tabloları', command='python main.py --input topics.txt --min-rf 3',
    expect={'topic': 4, 'partition': 8, 'eksik_isr': 1, 'lidersiz': 1, 'sorunlu_topic': 3})
def topics(args):
    tops, parts = {}, []
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        f = fields(line)
        if 'PartitionCount' in f:
            cfg = dict(kv.split('=', 1) for kv in f.get('Configs', '').split(',') if '=' in kv)
            tops[f['Topic']] = {'rf': int(f.get('ReplicationFactor') or 0), 'pc': int(f.get('PartitionCount') or 0), 'cfg': cfg}
        elif 'Partition' in f and 'Topic' in f:
            leader = f.get('Leader', '').strip()
            parts.append({'topic': f['Topic'], 'partition': int(f['Partition']), 'lider': '' if leader in ('none', '-1', '') else leader,
                          'replikalar': brokers(f.get('Replicas')), 'isr': brokers(f.get('Isr'))})
    if not tops and not parts:
        raise ValueError('kafka-topics --describe çıktısı bulunamadı (Topic: … PartitionCount: … satırları bekleniyor).')
    for p_ in parts:
        tops.setdefault(p_['topic'], {'rf': len(p_['replikalar']), 'pc': 0, 'cfg': {}})
    out = []
    for t, info in sorted(tops.items()):
        issues = []
        rf, cfg = info['rf'], info['cfg']
        mine = [p_ for p_ in parts if p_['topic'] == t]
        if rf < args.min_rf:
            issues.append(f'replikasyon faktörü {rf} < {args.min_rf}')
        isr_min = cfg.get('min.insync.replicas')
        if isr_min is None and rf > 1:
            issues.append('min.insync.replicas topic’te tanımsız (broker varsayılanı, genelde 1)')
        elif isr_min is not None and rf > 1 and int(isr_min) >= rf:
            issues.append(f'min.insync.replicas={isr_min} ≥ RF: tek broker kaybında acks=all yazmalar durur')
        if info['pc'] == 1 or len(mine) == 1:
            issues.append('tek partition: tüketici paralelliği yok')
        under = [p_ for p_ in mine if p_['lider'] and len(p_['isr']) < len(p_['replikalar'])]
        offline = [p_ for p_ in mine if not p_['lider']]
        moved = [p_ for p_ in mine if p_['lider'] and p_['replikalar'] and p_['lider'] != p_['replikalar'][0]]
        if under:
            issues.append(f"ISR eksik partition: {', '.join(str(p_['partition']) for p_ in under)}")
        if offline:
            issues.append(f"LİDERSİZ partition: {', '.join(str(p_['partition']) for p_ in offline)}")
        if moved:
            issues.append(f"tercih edilen lider dışında: {', '.join(str(p_['partition']) for p_ in moved)} (leader election önerilir)")
        out.append({'topic': t, 'partition': len(mine) or info['pc'], 'rf': rf, 'min_isr': isr_min or '', 'bulgu': '; '.join(issues), 'durum': 'SORUNLU' if issues else 'uygun'})
    leaders = Counter(p_['lider'] for p_ in parts if p_['lider'])
    out.sort(key=lambda o: (o['durum'] != 'SORUNLU', o['topic']))
    return Result(f'{len(out)} topic, {len(parts)} partition; {sum(o["durum"] == "SORUNLU" for o in out)} topic sorunlu', ['topic', 'partition', 'rf', 'min_isr', 'bulgu', 'durum'], out,
                  {'topic': len(out), 'partition': len(parts), 'eksik_isr': sum(1 for p_ in parts if p_['lider'] and len(p_['isr']) < len(p_['replikalar'])),
                   'lidersiz': sum(1 for p_ in parts if not p_['lider']), 'sorunlu_topic': sum(o['durum'] == 'SORUNLU' for o in out)},
                  tables=[('Broker başına lider', ['Broker', 'Lider partition'], sorted(leaders.items())),
                          ('Partition’lar', ['Topic', 'Partition', 'Lider', 'Replikalar', 'ISR'], [[p_['topic'], p_['partition'], p_['lider'] or 'yok', ','.join(p_['replikalar']), ','.join(p_['isr'])] for p_ in parts])],
                  notes=['Dahili topic’ler (__consumer_offsets, _schemas) de değerlendirilir; bulguları küme ayarlarıyla birlikte yorumlayın.'])


def get_path(obj, path):
    for part in path.split('.'):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


@op('olay-tekrar-sira', title='Olaylarda tekrar, çelişkili kopya ve sıra boşluğu kontrolü', domain='stream', product=GENEL, tag='exactly-once', kind='file', samples={'odeme_olaylari.jsonl': 'odeme_olaylari.jsonl'},
    args=[arg('--id', dest='id_field', default='olay_id', help='Olay kimliği alanı (iç içe: a.b)'), arg('--key', default='hesap', help='Sıranın tutulduğu anahtar alanı'),
          arg('--seq', default='sira', help='Anahtar içinde artan sıra/versiyon alanı (boş bırakılabilir)')],
    description='JSON Lines olay dökümünde aynı kimlikle birden çok gelen olayları (birebir kopya veya içeriği farklı çelişkili kopya), anahtar bazında sıra numarasındaki boşlukları (kayıp olay) ve sıra dışı gelen olayları bulur. At-least-once teslimatın, yeniden deneme ve CDC akışlarının doğrulanması için.',
    input='JSON Lines olay dosyası', output='sonuc.csv (bulgular)', command='python main.py --input dlq_export.jsonl --id event.id --key payload.account_id --seq payload.version',
    expect={'olay': 11, 'tekrar': 2, 'celiskili_tekrar': 1, 'eksik_sira': 3, 'sira_disi': 1, 'bozuk_satir': 1})
def dedupe(args):
    events, bad = load_events(args.input)
    seen, out = {}, []
    dup = conflict = 0
    for n, e in enumerate(events, 1):
        eid = get_path(e, args.id_field)
        if eid is None:
            continue
        body = json.dumps(e, sort_keys=True, ensure_ascii=False)
        if eid in seen:
            dup += 1
            same = seen[eid][1] == body
            conflict += not same
            out.append({'tur': 'tekrar' if same else 'ÇELİŞKİLİ TEKRAR', 'anahtar': get_path(e, args.key) or '', 'olay_id': eid, 'sira': get_path(e, args.seq) if args.seq else '',
                        'ayrinti': f'ilk görüldüğü olay #{seen[eid][0]}' + ('' if same else '; içerik farklı')})
        else:
            seen[eid] = (n, body)
    missing = late = 0
    if args.seq:
        per = defaultdict(list)
        firsts = {}
        for e in events:
            eid, k, s = get_path(e, args.id_field), get_path(e, args.key), get_path(e, args.seq)
            if isinstance(s, (int, float)) and not isinstance(s, bool) and k is not None and firsts.setdefault(eid if eid is not None else id(e), id(e)) == id(e):
                per[str(k)].append(int(s))
        for k, seqs in sorted(per.items()):
            top = None
            for s in seqs:
                if top is not None and s < top:
                    late += 1
                    out.append({'tur': 'sıra dışı', 'anahtar': k, 'olay_id': '', 'sira': s, 'ayrinti': f'{top} sonrasında geldi'})
                top = s if top is None else max(top, s)
            gaps = sorted(set(range(min(seqs), max(seqs) + 1)) - set(seqs))
            missing += len(gaps)
            if gaps:
                out.append({'tur': 'EKSİK SIRA', 'anahtar': k, 'olay_id': '', 'sira': ', '.join(map(str, gaps[:20])), 'ayrinti': f'{len(gaps)} sıra numarası hiç gelmedi'})
    return Result(f'{len(events)} olay; {dup} tekrar ({conflict} çelişkili), {missing} eksik sıra, {late} sıra dışı', ['tur', 'anahtar', 'olay_id', 'sira', 'ayrinti'], out,
                  {'olay': len(events), 'tekrar': dup, 'celiskili_tekrar': conflict, 'eksik_sira': missing, 'sira_disi': late, 'bozuk_satir': bad},
                  notes=['Çelişkili tekrar, aynı kimlikle farklı içerik demektir: üretici kimliği yeniden kullanıyor olabilir, tüketici tarafında son yazan kazanır.'])


@op('cdc-olay-ozeti', title='Debezium CDC olaylarının tablo ve işlem bazında özeti', domain='stream', product='Kafka', tag='CDC', kind='file', samples={'cdc_olaylari.jsonl': 'cdc_olaylari.jsonl'},
    description='Debezium biçimindeki değişiklik olaylarını (payload içinde veya düz; op c/u/d/r, before/after, source.table) tablo bazında ekleme, güncelleme, silme ve anlık görüntü (snapshot) sayılarına ayırır; hiçbir alanı değişmeyen boş güncellemeleri, en sık değişen alanları ve silme sonrası tombstone kayıtlarını raporlar. CDC kurulumunun ve tüketici mantığının doğrulanması için.',
    input='CDC olayları (JSON Lines)', output='sonuc.csv (tablo × işlem) + değişen alanlar', command='python main.py --input dbserver1.inventory.customers.jsonl',
    expect={'olay': 8, 'tombstone': 1, 'tablo': 2, 'bos_guncelleme': 1, 'silme': 1})
def cdc(args):
    stats = defaultdict(lambda: Counter())
    fields = defaultdict(Counter)
    tomb = bad = total = 0
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if obj is None or (isinstance(obj, dict) and 'payload' in obj and obj['payload'] is None):
            tomb += 1
            continue
        ev = obj.get('payload', obj) if isinstance(obj, dict) else None
        if not isinstance(ev, dict) or ev.get('op') not in ('c', 'u', 'd', 'r'):
            bad += 1
            continue
        total += 1
        src = ev.get('source') or {}
        table = '.'.join(x for x in (src.get('schema') or src.get('db'), src.get('table')) if x) or 'bilinmiyor'
        stats[table][ev['op']] += 1
        if ev['op'] == 'u':
            before, after = ev.get('before') or {}, ev.get('after') or {}
            changed = [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
            if not changed:
                stats[table]['bos'] += 1
            fields[table].update(changed)
    if not total:
        raise ValueError('Debezium olayı bulunamadı (op: c/u/d/r alanı bekleniyor).')
    names = {'c': 'ekleme', 'u': 'guncelleme', 'd': 'silme', 'r': 'snapshot'}
    out = [{'tablo': t, **{names[k]: s[k] for k in 'cudr'}, 'bos_guncelleme': s['bos'], 'en_cok_degisen': ', '.join(f'{f} ({n})' for f, n in fields[t].most_common(3))} for t, s in sorted(stats.items())]
    return Result(f'{total} CDC olayı, {len(out)} tablo; {sum(s["bos"] for s in stats.values())} boş güncelleme, {tomb} tombstone', list(out[0]), out,
                  {'olay': total, 'tombstone': tomb, 'bozuk_satir': bad, 'tablo': len(out), 'bos_guncelleme': sum(s['bos'] for s in stats.values()),
                   'silme': sum(s['d'] for s in stats.values())},
                  notes=['Boş güncellemeler genelde kaynakta değeri aynı yazan UPDATE’lerden gelir; tüketicide gereksiz işlem ve hedefte gereksiz sürüm yaratır.'])


def ts(v):
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None


@op('pencere-toplam', title='Olay zamanına göre sabit pencerelerde toplam ve geç gelen olaylar', domain='stream', product=GENEL, tag='tumbling window', kind='file', samples={'olaylar.jsonl': 'olaylar.jsonl'},
    args=[arg('--time', default='olay_zamani'), arg('--arrival', default='alinma_zamani'), arg('--value', default='tutar'), arg('--window', type=int, default=60, help='Pencere (sn)'),
          arg('--lateness', type=int, default=30, help='İzin verilen gecikme (sn)')],
    description='Olayları olay zamanına göre sabit (tumbling) pencerelere böler; her pencerede olay sayısını ve değer toplamını, pencere kapandıktan sonra (pencere sonu + izin verilen gecikme) gelen ve akış motorunda düşürülecek olayları gösterir. Flink/Spark/Kafka Streams pencere ve watermark ayarının sonuca etkisini önceden görmek için.',
    input='JSON Lines olaylar (olay zamanı, varış zamanı, değer)', output='sonuc.csv (pencere bazında)', command='python main.py --window 300 --lateness 60 --value amount --time event_time --arrival ingest_time',
    expect={'olay': 200, 'pencere': 10, 'gec_olay': 6})
def windows(args):
    events, bad = load_events(args.input)
    win = defaultdict(lambda: {'olay': 0, 'toplam': 0.0, 'gec': 0, 'gec_toplam': 0.0})
    skipped = 0
    for e in events:
        t, a = ts(get_path(e, args.time)), ts(get_path(e, args.arrival))
        v = get_path(e, args.value)
        if not t:
            skipped += 1
            continue
        start = int(t.timestamp()) // args.window * args.window
        w = win[start]
        w['olay'] += 1
        w['toplam'] += v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        if a and a.timestamp() > start + args.window + args.lateness:
            w['gec'] += 1
            w['gec_toplam'] += v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
    if not win:
        raise ValueError(f'"{args.time}" alanında okunabilir zaman yok.')
    out = [{'pencere_baslangic': datetime.fromtimestamp(s, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'olay': w['olay'], 'toplam': round(w['toplam'], 2),
            'gec_olay': w['gec'], 'dusurulecek_toplam': round(w['gec_toplam'], 2), 'kesin_toplam': round(w['toplam'] - w['gec_toplam'], 2)} for s, w in sorted(win.items())]
    late = sum(o['gec_olay'] for o in out)
    return Result(f'{sum(o["olay"] for o in out)} olay, {len(out)} pencere ({args.window} sn); {late} olay izin verilen gecikmeden sonra geldi', list(out[0]), out,
                  {'olay': sum(o['olay'] for o in out), 'pencere': len(out), 'gec_olay': late, 'zamansiz': skipped, 'bozuk_satir': bad},
                  notes=['Geç olaylar akış motorunda varsayılan olarak düşürülür; kayıp kabul edilemiyorsa izin verilen gecikmeyi artırın veya yan çıktıya (side output) yönlendirin.'])


def nearest_rank(vals, q):
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, math.ceil(len(s) * q) - 1))] if s else None


@op('mesaj-boyutu-analizi', title='Olay/mesaj boyutu dağılımı ve broker sınırı kontrolü', domain='stream', product='Kafka', tag='mesaj boyutu', kind='file', samples={'olaylar.jsonl': 'olaylar.jsonl'},
    args=[arg('--type', default='tur', help='Olay türü alanı (boyutları türe göre ayırmak için)'), arg('--max-bytes', type=int, default=1048576, help='Broker/topic mesaj sınırı (max.message.bytes)')],
    description='JSON Lines olay dökümünde her mesajın UTF-8 bayt boyutunu ölçer; p50/p95/en büyük boyutu, olay türüne göre ortalama boyutu, sınıra yaklaşan ve sınırı aşan mesajları ve toplam veri hacmini verir. Topic ayarı, sıkıştırma ve büyük alanları dışarı taşıma (claim-check) kararı için.',
    input='JSON Lines olay dosyası', output='sonuc.csv (tür bazında boyut)', command='python main.py --input orders.jsonl --type event_type --max-bytes 2097152',
    expect={'olay': 200, 'p50_bayt': 190, 'p95_bayt': 195, 'en_buyuk_bayt': 196, 'sinir_ustu': 0})
def msg_size(args):
    rows = []
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = {}
        rows.append((len(line.encode('utf-8')), get_path(obj, args.type) if isinstance(obj, dict) else None))
    if not rows:
        raise ValueError('Mesaj yok.')
    sizes = [s for s, _ in rows]
    by = defaultdict(list)
    for s, t in rows:
        by[str(t) if t is not None else '(türsüz)'].append(s)
    out = [{'tur': t, 'mesaj': len(v), 'ortalama_bayt': round(sum(v) / len(v), 1), 'en_buyuk_bayt': max(v), 'toplam_kb': round(sum(v) / 1024, 2)} for t, v in sorted(by.items(), key=lambda kv: -sum(kv[1]))]
    over = sum(s > args.max_bytes for s in sizes)
    near = sum(0.8 * args.max_bytes < s <= args.max_bytes for s in sizes)
    return Result(f'{len(sizes)} mesaj; p50 {nearest_rank(sizes, 0.5)} B, p95 {nearest_rank(sizes, 0.95)} B, en büyük {max(sizes)} B; {over} mesaj sınırı aşıyor', list(out[0]), out,
                  {'olay': len(sizes), 'p50_bayt': nearest_rank(sizes, 0.5), 'p95_bayt': nearest_rank(sizes, 0.95), 'en_buyuk_bayt': max(sizes), 'toplam_kb': round(sum(sizes) / 1024, 2),
                   'sinira_yakin': near, 'sinir_ustu': over},
                  notes=['Ölçülen boyut sıkıştırılmamış JSON gövdesidir; başlıklar ve anahtar dahil değildir. Producer sıkıştırması (lz4/zstd) ağ ve diskte boyutu düşürür.'])


@op('anahtar-carpikligi', title='Partition anahtarı çarpıklığı (sıcak anahtar) analizi', domain='stream', product='Kafka', tag='sıcak anahtar', kind='file', samples={'olaylar.jsonl': 'olaylar.jsonl'},
    args=[arg('--key', default='musteri.id', help='Mesaj anahtarı olarak kullanılan alan'), arg('--partitions', type=int, default=6)],
    description='Mesaj anahtarı olarak kullanılan alanın değer dağılımını çıkarır: farklı anahtar sayısını, en sık anahtarların payını, bir partition’ın adil payını (1/partition sayısı) aşan sıcak anahtarları ve dağılımın Gini katsayısını verir. Sıcak anahtar tek bir tüketiciyi tıkar; anahtar seçimi ve partition sayısı kararı için.',
    input='JSON Lines olay dosyası', output='sonuc.csv (anahtar bazında pay)', command='python main.py --input clicks.jsonl --key user.id --partitions 24',
    expect={'olay': 200, 'anahtar': 50, 'en_sik_pay_%': 4.0, 'sicak_anahtar': 0})
def key_skew(args):
    events, bad = load_events(args.input)
    keys = Counter(str(get_path(e, args.key)) for e in events if get_path(e, args.key) is not None)
    if not keys:
        raise ValueError(f'"{args.key}" alanı olaylarda yok.')
    n = sum(keys.values())
    fair = 1 / args.partitions
    out = [{'anahtar': k, 'mesaj': c, 'pay_%': round(100 * c / n, 2), 'durum': 'SICAK' if c / n > fair else ''} for k, c in keys.most_common()]
    vals = sorted(keys.values())
    gini = sum((2 * i - len(vals) - 1) * v for i, v in enumerate(vals, 1)) / (len(vals) * sum(vals))
    top = out[0]
    return Result(f'{len(keys)} farklı anahtar; en sık {top["anahtar"]} (%{top["pay_%"]}); Gini {gini:.3f}', list(out[0]), out,
                  {'olay': n, 'anahtar': len(keys), 'en_sik_anahtar': top['anahtar'], 'en_sik_pay_%': top['pay_%'], 'sicak_anahtar': sum(o['durum'] == 'SICAK' for o in out),
                   'gini': round(gini, 3), 'anahtarsiz': len(events) - n},
                  notes=[f'Bir anahtarın payı 1/{args.partitions} (%{100 * fair:.1f}) üstündeyse o anahtarın partition’ı diğerlerinden fazla yük alır; anahtar sayısı partition sayısından azsa bazı partition’lar boş kalır.'])


DLQ_CLASS = ('__connect.errors.exception.class.name', 'error.class', 'exception', 'error_type', 'hata_turu')
DLQ_MSG = ('__connect.errors.exception.message', 'error.message', 'message', 'hata_mesaji')
DLQ_TOPIC = ('__connect.errors.topic', 'original.topic', 'kaynak_topic', 'topic')
DLQ_RETRY = ('retry.count', 'deneme', '__connect.errors.retry.count')
TRANSIENT = re.compile(r'timeout|timedout|retriable|retryable|connection|throttl|unavailable|notleader|broker', re.I)


def first(obj, keys):
    for scope in (obj.get('headers') if isinstance(obj.get('headers'), dict) else {}, obj):
        for k in keys:
            if scope.get(k) not in (None, ''):
                return scope[k]
    return None


def dlq_signature(msg):
    msg = re.sub(r'"[^"]*"|\'[^\']*\'', '<METİN>', str(msg))
    return re.sub(r'\s+', ' ', re.sub(r'\d+', '<N>', msg)).strip()[:160]


@op('dlq-analizi', title='Dead letter queue (DLQ) mesajlarını hata türüne göre grupla', domain='stream', product='Kafka', tag='DLQ', kind='file', samples={'dlq.jsonl': 'dlq.jsonl'},
    args=[arg('--time', default='timestamp', help='Mesaj zamanı alanı')],
    description='Kafka Connect veya uygulama DLQ dökümünü (JSON Lines) okur; hata sınıfını, mesajını ve orijinal topic’i başlıklardan (__connect.errors.* dahil) çıkarır, sayıları ve tırnak içini yer tutucuya çevirerek aynı hatayı tek grupta toplar. Grup başına mesaj sayısı, ilk/son zaman, en yüksek deneme sayısı ve hatanın geçici (yeniden oynatılabilir) mi kalıcı (veri/şema düzeltmesi gerekir) mi olduğunu verir.',
    input='DLQ mesajları (JSON Lines; headers nesnesi veya düz alanlar)', output='sonuc.csv (hata grupları)', command='python main.py --input connect-dlq.jsonl --time ts',
    expect={'mesaj': 10, 'grup': 5, 'tekrar_denenebilir': 3, 'kalici': 7, 'konu': 3})
def dlq(args):
    events, bad = load_events(args.input)
    groups = {}
    for e in events:
        cls = str(first(e, DLQ_CLASS) or 'bilinmiyor').split('.')[-1]
        topic = str(first(e, DLQ_TOPIC) or '-')
        sig = dlq_signature(first(e, DLQ_MSG) or '')
        g = groups.setdefault((topic, cls, sig), {'topic': topic, 'hata_sinifi': cls, 'imza': sig, 'mesaj': 0, 'ilk': '', 'son': '', 'en_cok_deneme': 0})
        g['mesaj'] += 1
        t = str(get_path(e, args.time) or '')
        if t:
            g['ilk'] = min(g['ilk'] or t, t)
            g['son'] = max(g['son'], t)
        try:
            g['en_cok_deneme'] = max(g['en_cok_deneme'], int(first(e, DLQ_RETRY) or 0))
        except (TypeError, ValueError):
            pass
    out = sorted(groups.values(), key=lambda g: (-g['mesaj'], g['topic'], g['hata_sinifi']))
    for g in out:
        g['tur'] = 'geçici: yeniden oynatılabilir' if TRANSIENT.search(g['hata_sinifi'] + ' ' + g['imza']) else 'kalıcı: veri/şema düzeltilmeli'
    retry = sum(g['mesaj'] for g in out if g['tur'].startswith('geçici'))
    return Result(f'{len(events)} DLQ mesajı {len(out)} hata grubunda; {retry} mesaj yeniden oynatılabilir', ['topic', 'hata_sinifi', 'imza', 'mesaj', 'ilk', 'son', 'en_cok_deneme', 'tur'], out,
                  {'mesaj': len(events), 'grup': len(out), 'tekrar_denenebilir': retry, 'kalici': len(events) - retry, 'konu': len({g['topic'] for g in out}), 'bozuk_satir': bad},
                  notes=['Geçici/kalıcı ayrımı hata sınıfı ve mesajdaki sözcüklere göre yapılan bir tahmindir. Kalıcı hatalı mesajları düzeltmeden yeniden oynatmak DLQ’ya geri döner.'])


@op('outbox-mutabakati', title='Outbox tablosu ile yayınlanan olayları mutabık et (eksik, yetim, tekrar, gecikme)', domain='stream', product=GENEL, tag='outbox',
    samples={'outbox.csv': 'outbox.csv', 'yayinlanan_olaylar.jsonl': 'yayinlanan_olaylar.jsonl'},
    args=[arg('--events', default='@ornek/yayinlanan_olaylar.jsonl', help='Yayınlanan olaylar (JSON Lines)'), arg('--id', default='olay_id', help='Outbox kimlik sütunu'),
          arg('--created', default='olusturma', help='Outbox oluşturma zamanı sütunu'), arg('--event-id', default='id', help='Olaydaki kimlik alanı (iç içe: a.b)'),
          arg('--event-time', default='yayin_zamani', help='Olaydaki yayın zamanı alanı'), arg('--max-delay', type=int, default=60, help='Bu kadar saniyeden geç yayın geç sayılır'),
          arg('--grace', type=int, default=120, help='Son yayından bu kadar saniye önce oluşturulan kayıtlar henüz eksik sayılmaz')],
    description='Transactional outbox deseninde veritabanındaki outbox dışa aktarımını broker’a yayınlanan olaylarla kimlik üzerinden karşılaştırır: hiç yayınlanmayan (kayıp), outbox’ta karşılığı olmayan (yetim), birden çok yayınlanan olayları ve oluşturma → yayın gecikmesinin p50/p95 değerlerini verir. Debezium/outbox relay doğrulaması için.',
    input='Outbox dışa aktarımı (CSV/XLSX) + yayınlanan olaylar (JSON Lines)', output='sonuc.csv (bulgular)', command='python main.py --input outbox.csv --events topic_dump.jsonl --event-id payload.event_id --event-time ts',
    expect={'outbox': 8, 'yayinlanan': 6, 'eksik': 1, 'bekleyen': 1, 'yetim': 1, 'tekrar': 1, 'gec': 1, 'p50_sn': 3.0, 'p95_sn': 300.0})
def outbox(columns, rows, args):
    ic, cc = need(args.id, columns, '--id'), need(args.created, columns, '--created')
    created = {}
    for r in rows:
        d = tabular.parse_date(r[cc])
        if r[ic].strip():
            created[r[ic].strip()] = d[0].replace(tzinfo=None) if d else None
    events, bad = load_events(args.events)
    pubs = defaultdict(list)
    for e in events:
        eid, t = get_path(e, args.event_id), ts(get_path(e, args.event_time))
        if eid is not None and t:
            pubs[str(eid)].append(t.replace(tzinfo=None))
    if not pubs:
        raise ValueError(f'Olaylarda "{args.event_id}" ve "{args.event_time}" alanları okunamadı.')
    as_of = max(t for ts_ in pubs.values() for t in ts_)
    out, delays = [], []
    for k, c in created.items():
        if k not in pubs:
            pending = c is not None and (as_of - c).total_seconds() < args.grace
            out.append({'olay_id': k, 'bulgu': 'bekliyor (yeni)' if pending else 'yayınlanmamış', 'olusturma': c.strftime('%Y-%m-%d %H:%M:%S') if c else '', 'yayin': '', 'gecikme_sn': '', 'yayin_sayisi': 0})
            continue
        first_pub = min(pubs[k])
        delay = (first_pub - c).total_seconds() if c else None
        if delay is not None:
            delays.append(delay)
        if len(pubs[k]) > 1:
            out.append({'olay_id': k, 'bulgu': 'tekrar yayın', 'olusturma': c.strftime('%Y-%m-%d %H:%M:%S') if c else '', 'yayin': first_pub.strftime('%Y-%m-%d %H:%M:%S'), 'gecikme_sn': fmt(delay), 'yayin_sayisi': len(pubs[k])})
        if delay is not None and delay > args.max_delay:
            out.append({'olay_id': k, 'bulgu': 'geç yayın', 'olusturma': c.strftime('%Y-%m-%d %H:%M:%S'), 'yayin': first_pub.strftime('%Y-%m-%d %H:%M:%S'), 'gecikme_sn': fmt(delay), 'yayin_sayisi': len(pubs[k])})
    for k in pubs.keys() - created.keys():
        out.append({'olay_id': k, 'bulgu': 'yetim (outbox’ta yok)', 'olusturma': '', 'yayin': min(pubs[k]).strftime('%Y-%m-%d %H:%M:%S'), 'gecikme_sn': '', 'yayin_sayisi': len(pubs[k])})
    order = {'yayınlanmamış': 0, 'yetim (outbox’ta yok)': 1, 'tekrar yayın': 2, 'geç yayın': 3}
    out.sort(key=lambda o: (order.get(o['bulgu'], 4), o['olay_id']))
    count = Counter(o['bulgu'] for o in out)
    p50, p95 = nearest_rank(delays, 0.5), nearest_rank(delays, 0.95)
    return Result(f'{len(created)} outbox kaydı; {count["yayınlanmamış"]} yayınlanmamış, {count["yetim (outbox’ta yok)"]} yetim, {count["tekrar yayın"]} tekrar; gecikme p95 {fmt(p95)} sn',
                  ['olay_id', 'bulgu', 'olusturma', 'yayin', 'gecikme_sn', 'yayin_sayisi'], out,
                  {'outbox': len(created), 'yayinlanan': sum(1 for k in created if k in pubs), 'eksik': count['yayınlanmamış'], 'bekleyen': count['bekliyor (yeni)'],
                   'yetim': count['yetim (outbox’ta yok)'], 'tekrar': count['tekrar yayın'], 'gec': count['geç yayın'],
                   'p50_sn': float(p50) if p50 is not None else None, 'p95_sn': float(p95) if p95 is not None else None, 'bozuk_satir': bad},
                  notes=['Zaman dilimi bilgisi yok sayılır; outbox ve olay zamanlarının aynı saat diliminde (tercihen UTC) olduğundan emin olun.',
                         'Tekrar yayın at-least-once teslimatta beklenir; tüketiciler olay kimliğiyle idempotent olmalıdır.'])
