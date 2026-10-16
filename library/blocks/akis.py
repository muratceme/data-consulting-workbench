"""Akış (streaming) blokları: olaylardan şema çıkarma, Avro/JSON şema uyumluluğu, olay gecikmesi ve Kafka tüketici gecikmesi.
Broker’a veya şema kayıt servisine bağlanmaz; dışa aktarılmış örnekleri ve komut çıktılarını okur."""
import json, re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from islem import op, arg, Result, fmt

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
