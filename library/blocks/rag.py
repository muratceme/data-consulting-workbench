"""GenAI / RAG veri hazırlığı blokları: doküman parçalama, tekrar eden içerik, token ve maliyet tahmini, prompt enjeksiyon taraması, geri getirme değerlendirmesi.
Hiçbir model veya embedding servisine bağlanmaz."""
import hashlib, json, math, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from common import mask_text
from islem import op, arg, Result, need, numbers, fmt, fold

D, P = 'ai', 'Doküman ve RAG verisi'
DOCS = {f'dokumanlar/{n}': f'dokumanlar/{n}' for n in ('iade_politikasi.md', 'kargo.md', 'kargo_kopya.md', 'tedarikci_notu.txt')}
TEXT_EXT = ('.md', '.txt', '.markdown', '.csv', '.json', '.html', '.htm', '.rst')


def documents(path):
    p = Path(path)
    files = sorted(f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in TEXT_EXT) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'Metin dokümanı bulunamadı: {p} (desteklenen: {", ".join(TEXT_EXT)})')
    out = []
    for f in files:
        text = f.read_text(encoding='utf-8-sig', errors='replace')
        if f.suffix.lower() in ('.html', '.htm'):
            text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.S | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
        out.append((f.relative_to(p).as_posix() if p.is_dir() else f.name, text))
    return out


def tokens(text):
    """Rough token estimate for multilingual BPE tokenizers: Turkish words split into ~1.6 tokens on average."""
    words = re.findall(r'\w+|[^\w\s]', text)
    return round(sum(1.6 if re.search(r'[çğıöşüÇĞİÖŞÜ]', w) or len(w) > 6 else 1.0 for w in words))


@op('dokuman-parcala', title='Dokümanları RAG için parçalara (chunk) böl', domain=D, product=P, tag='chunking', kind='file', samples=DOCS, sample_input='dokumanlar',
    args=[arg('--max-tokens', type=int, default=200), arg('--overlap', type=int, default=30, help='Ardışık parçalar arasında yaklaşık örtüşme (token)')],
    description='Markdown/metin dokümanlarını başlık yapısını koruyarak paragraf sınırlarından parçalara böler; her parçaya kaynak dosya, başlık yolu (İade Politikası > İade süreci), sıra no, karakter ve tahmini token sayısı ekler. Çıktı JSON Lines; vektör veritabanına yüklemeye hazırdır.',
    input='Doküman klasörü (.md, .txt, .html)', output='parcalar.jsonl + sonuc.csv (parça özeti)', command='python main.py --input belgeler/ --max-tokens 300 --overlap 40',
    expect={'dokuman': 4, 'parca': 8})
def chunk(args):
    chunks = []
    for name, text in documents(args.input):
        path, buf, n = [], [], 0

        def flush():
            nonlocal buf, n
            body = '\n\n'.join(buf).strip()
            if body:
                chunks.append({'id': f'{name}#{n + 1}', 'kaynak': name, 'baslik_yolu': ' > '.join(path), 'sira': n + 1, 'metin': body, 'karakter': len(body), 'token_tahmini': tokens(body)})
                n += 1
            buf = []
        for para in re.split(r'\n\s*\n', text):
            para = para.strip()
            if not para:
                continue
            h = re.match(r'^(#{1,6})\s+(.*)', para)
            if h:
                flush()
                level = len(h.group(1))
                path = path[:level - 1] + [h.group(2).strip()]
                continue
            if buf and tokens('\n\n'.join(buf + [para])) > args.max_tokens:
                tail = ' '.join(' '.join(buf).split()[-max(0, args.overlap):]) if args.overlap else ''
                flush()
                if tail:
                    buf = [tail]
            buf.append(para)
        flush()
    jsonl = '\n'.join(json.dumps(c, ensure_ascii=False) for c in chunks)
    rows = [{k: v for k, v in c.items() if k != 'metin'} for c in chunks]
    return Result(f'{len({c["kaynak"] for c in chunks})} dokümandan {len(chunks)} parça; ortalama {sum(c["token_tahmini"] for c in chunks) / max(1, len(chunks)):.0f} token',
                  list(rows[0]), rows, {'dokuman': len({c['kaynak'] for c in chunks}), 'parca': len(chunks)}, files={'parcalar.jsonl': jsonl},
                  notes=['Token sayısı yaklaşık tahmindir; kullandığınız modelin tokenizer’ı ile farklılık gösterebilir.'])


def shingles(text, k=5):
    words = re.findall(r'\w+', text.lower())
    return {' '.join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


@op('dokuman-tekrar', title='Tekrar eden ve çelişkili dokümanları bul', domain=D, product=P, tag='tekrar', kind='file', samples=DOCS, sample_input='dokumanlar',
    args=[arg('--threshold', type=float, default=0.6, help='Jaccard benzerlik eşiği (0-1)')],
    description='Doküman çiftlerini 5 kelimelik parçacıkların (shingle) Jaccard benzerliğiyle karşılaştırır; birebir kopyaları (özetle), yakın kopyaları ve yakın kopyalarda farklılaşan cümleleri gösterir. Aynı bilginin iki farklı sürümü RAG cevaplarında çelişkiye yol açar.',
    input='Doküman klasörü', output='sonuc.csv (benzer çiftler) + farklar', command='python main.py --input belgeler/ --threshold 0.5',
    expect={'cift': 1, 'birebir': 0})
def dup(args):
    docs = documents(args.input)
    sh = [(n, t, shingles(t), hashlib.sha256(re.sub(r'\s+', ' ', t).strip().encode()).hexdigest()) for n, t in docs]
    out, diffs = [], []
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            a, b = sh[i], sh[j]
            jac = len(a[2] & b[2]) / len(a[2] | b[2]) if a[2] | b[2] else 0
            if a[3] == b[3] or jac >= args.threshold:
                sa = {s.strip() for s in re.split(r'(?<=[.!?])\s+|\n', a[1]) if s.strip()}
                sb = {s.strip() for s in re.split(r'(?<=[.!?])\s+|\n', b[1]) if s.strip()}
                only = sorted(sa ^ sb)
                out.append({'dokuman_1': a[0], 'dokuman_2': b[0], 'benzerlik': round(jac, 3), 'tur': 'birebir kopya' if a[3] == b[3] else 'yakın kopya', 'farkli_cumle': len(only)})
                diffs += [[a[0] if s in sa else b[0], s[:160]] for s in only]
    return Result(f'{len(docs)} doküman; {len(out)} benzer çift', ['dokuman_1', 'dokuman_2', 'benzerlik', 'tur', 'farkli_cumle'], out,
                  {'dokuman': len(docs), 'cift': len(out), 'birebir': sum(o['tur'] == 'birebir kopya' for o in out)},
                  tables=[('Yakın kopyalarda farklılaşan cümleler', ['Doküman', 'Cümle'], diffs)],
                  notes=['Yakın kopyalardan yalnızca güncel ve onaylı olanı indeksleyin; farklılaşan cümleler çelişkili cevapların kaynağıdır.'])


@op('token-maliyet-tahmini', title='Doküman token sayısı ve embedding maliyeti tahmini', domain=D, product=P, tag='token', kind='file', samples=DOCS, sample_input='dokumanlar',
    args=[arg('--price', type=float, default=0.02, help='1 milyon token başına USD (embedding)'), arg('--refresh-per-month', type=float, default=1.0, help='Ayda kaç kez yeniden indekslenecek')],
    description='Klasördeki dokümanların karakter, kelime ve yaklaşık token sayısını dosya bazında çıkarır; verilen birim fiyatla tek seferlik ve aylık yeniden indeksleme maliyetini, en büyük dosyaları ve boş/çok kısa dosyaları gösterir.',
    input='Doküman klasörü', output='sonuc.csv (dosya bazında)', command='python main.py --input belgeler/ --price 0.13 --refresh-per-month 4',
    expect={'dokuman': 4})
def cost(args):
    out = []
    for name, text in documents(args.input):
        t = tokens(text)
        out.append({'dosya': name, 'karakter': len(text), 'kelime': len(re.findall(r'\w+', text)), 'token_tahmini': t, 'maliyet_usd': round(t / 1e6 * args.price, 6),
                    'uyari': 'çok kısa' if t < 20 else ''})
    out.sort(key=lambda o: -o['token_tahmini'])
    total = sum(o['token_tahmini'] for o in out)
    once = total / 1e6 * args.price
    return Result(f"{len(out)} doküman, ≈{f'{total:,}'.replace(',', '.')} token; tek seferlik ≈${once:.6f}, aylık ≈${once * args.refresh_per_month:.6f}",
                  list(out[0]), out, {'dokuman': len(out), 'token': total, 'maliyet_usd': round(total / 1e6 * args.price, 6)},
                  notes=['Token sayısı tahminidir (Türkçe kelimeler ≈1,6 token). Fiyatı sağlayıcınızın güncel listesinden alın.'])


INJECTION = [(r'ignore (all |any )?(previous|prior|above) (instructions|prompts)', 'İngilizce “önceki talimatları yok say”'),
             (r'(önceki|yukarıdaki|tüm) (tüm )?talimatları (yok say|unut|görmezden gel)', 'Türkçe “önceki talimatları yok say”'),
             (r'(reveal|print|show) (the )?(system prompt|hidden instructions)', 'sistem istemini ifşa etme isteği'),
             (r'(sistem (istemini|mesajını)|gizli talimatları) (göster|açıkla|yaz)', 'sistem istemini ifşa etme isteği (TR)'),
             (r'you are now|act as (an? )?(admin|developer|dan)\b|şimdi .{0,20} (rolündesin|gibi davran)', 'rol değiştirme'),
             (r'(%\d{2,3}|yüzde \d{2,3}) indirim (kodu )?ver', 'yetkisiz eylem talimatı'),
             (r'<\s*(script|iframe)|javascript:', 'gömülü betik'),
             (r'[A-Za-z0-9+/]{120,}={0,2}', 'uzun base64 bloğu (gizlenmiş içerik olabilir)')]
INVISIBLE = re.compile('[​‌‍⁠﻿‪-‮\U000e0000-\U000e007f]')


@op('prompt-enjeksiyon-tarama', title='Dokümanlarda prompt enjeksiyonu ve gizli içerik taraması', domain=D, product=P, tag='güvenlik', kind='file', samples=DOCS, sample_input='dokumanlar',
    args=[arg('--strict', action='store_true')],
    description='RAG’e eklenecek dokümanlarda modele yönelik talimat enjeksiyonlarını (“önceki talimatları yok say”, sistem istemini ifşa, rol değiştirme, yetkisiz eylem), görünmez Unicode karakterleri, yön değiştirme karakterlerini, gömülü betikleri ve uzun base64 bloklarını satır numarasıyla bulur. İndekslemeden önce karantina listesi üretir.',
    input='Doküman klasörü', output='sonuc.csv (bulgular)', command='python main.py --input belgeler/ --strict',
    expect={'bulgu': 5, 'riskli_dokuman': 1})
def injection(args):
    out = []
    for name, text in documents(args.input):
        for n, line in enumerate(text.splitlines(), start=1):
            low = line.lower()
            for rx, label in INJECTION:
                if re.search(rx, low if 'A-Za-z0-9' not in rx else line):
                    out.append({'dokuman': name, 'satir': n, 'tur': label, 'metin': line.strip()[:120]})
            inv = INVISIBLE.findall(line)
            if inv:
                out.append({'dokuman': name, 'satir': n, 'tur': f'{len(inv)} görünmez/yön karakteri ({", ".join(sorted({unicodedata.name(c, hex(ord(c))) for c in inv}))})', 'metin': INVISIBLE.sub('⟨?⟩', line.strip())[:120]})
    docs = len({o['dokuman'] for o in out})
    return Result(f'{len(out)} bulgu, {docs} riskli doküman', ['dokuman', 'satir', 'tur', 'metin'], out, {'bulgu': len(out), 'riskli_dokuman': docs},
                  code=2 if args.strict and out else 0, notes=['Kalıp tabanlı tarama tüm saldırıları yakalayamaz; modelin araç yetkilerini ayrıca sınırlayın.'])


@op('rag-geri-getirme-degerlendirme', title='RAG geri getirme kalitesini ölç (hit@k, MRR)', domain=D, product=P, tag='değerlendirme', samples={'rag_degerlendirme.csv': 'rag_degerlendirme.csv'},
    args=[arg('--expected', default='beklenen'), arg('--retrieved', default='getirilen'), arg('--sep', default='|'), arg('--k', default='1,3,5')],
    description='Soru başına beklenen doküman/parça ile geri getirilen sıralı listeyi karşılaştırır; hit@k, MRR ve hiç bulunamayan soruları raporlar. Birden çok beklenen parça “|” ile verilebilir. Chunk boyutu, embedding ve hibrit arama ayarlarını karşılaştırmak için.',
    input='Değerlendirme CSV’si (beklenen, getirilen sıralı liste)', output='sonuc.csv (soru bazında sıra) + metrikler', command='python main.py --k 1,3,10',
    expect={'soru': 5, 'hit@1': 0.6, 'hit@3': 0.8, 'mrr': 0.7})
def retrieval(columns, rows, args):
    ks = [int(k) for k in args.k.split(',') if k.strip()]
    out = []
    for r in rows:
        exp = {x.strip() for x in r[need(args.expected, columns, '--expected')].split(args.sep) if x.strip()}
        got = [x.strip() for x in r[need(args.retrieved, columns, '--retrieved')].split(args.sep) if x.strip()]
        rank = next((i for i, g in enumerate(got, start=1) if g in exp), None)
        out.append({**{c: r[c] for c in columns if c not in (args.retrieved,)}, 'ilk_dogru_sira': rank or '', 'bulundu': 'evet' if rank else 'hayır'})
    n = len(out)
    hits = {f'hit@{k}': round(sum(1 for o in out if o['ilk_dogru_sira'] and o['ilk_dogru_sira'] <= k) / n, 4) for k in ks}
    mrr = round(sum(1 / o['ilk_dogru_sira'] for o in out if o['ilk_dogru_sira']) / n, 4)
    return Result(f"{n} soru; " + ', '.join(f'{k} {v:.2f}' for k, v in hits.items()) + f', MRR {mrr:.3f}', list(out[0]), out, {'soru': n, **hits, 'mrr': mrr},
                  tables=[('Bulunamayan sorular', ['Soru', 'Beklenen'], [[o.get('soru', ''), o.get(args.expected, '')] for o in out if not o['ilk_dogru_sira']])])


def answer_tokens(s):
    return re.findall(r'\w+', fold(s))


@op('llm-cevap-degerlendir', title='LLM cevaplarını beklenen cevapla karşılaştır (EM, token F1)', domain=D, product=P, tag='değerlendirme', samples={'llm_degerlendirme.csv': 'llm_degerlendirme.csv'},
    args=[arg('--expected', default='beklenen'), arg('--answer', default='cevap'), arg('--question', default='soru')],
    description='Soru–beklenen cevap–model cevabı üçlülerinden oluşan değerlendirme setinde her cevap için tam eşleşme (EM), token düzeyinde F1 ve beklenen cevabın model cevabında geçip geçmediğini Türkçe karakter ve noktalama farkını yok sayarak hesaplar; en düşük skorlu soruları listeler. Prompt veya model değişikliğinden önce/sonra regresyon testi için.',
    input='Değerlendirme seti (soru, beklenen, cevap)', output='sonuc.csv (soru bazında skorlar)', command='python main.py --input eval_sonuclari.xlsx --expected ground_truth --answer model_output',
    expect={'soru': 6, 'tam_eslesme_%': 33.3, 'ortalama_f1': 0.552, 'iceren_%': 50.0, 'bos_cevap': 1})
def qa_eval(columns, rows, args):
    e, a = need(args.expected, columns, '--expected'), need(args.answer, columns, '--answer')
    q = args.question if args.question in columns else None
    out, f1s = [], []
    for i, r in enumerate(rows, 2):
        exp, ans = answer_tokens(r[e]), answer_tokens(r[a])
        common = sum((Counter(exp) & Counter(ans)).values())
        f1 = 2 * common / (len(exp) + len(ans)) if common else 0.0
        f1s.append(f1)
        contains = bool(exp) and f' {" ".join(exp)} ' in f' {" ".join(ans)} '
        out.append({'satir': i, 'soru': r[q] if q else '', 'tam_eslesme': int(exp == ans and bool(exp)), 'f1': round(f1, 3), 'iceriyor': int(contains),
                    'not': 'boş cevap' if not ans else ''})
    n = len(out) or 1
    order = sorted(out, key=lambda o: (o['f1'], o['satir']))
    return Result(f'{len(out)} soru; EM %{100 * sum(o["tam_eslesme"] for o in out) / n:.1f}, ortalama F1 {sum(f1s) / n:.3f}', list(out[0]), order,
                  {'soru': len(out), 'tam_eslesme_%': round(100 * sum(o['tam_eslesme'] for o in out) / n, 1), 'ortalama_f1': round(sum(f1s) / n, 3),
                   'iceren_%': round(100 * sum(o['iceriyor'] for o in out) / n, 1), 'bos_cevap': sum(o['not'] == 'boş cevap' for o in out)},
                  notes=['Token F1 kelime örtüşmesidir; anlamca doğru ama farklı ifade edilmiş cevaplar düşük puan alabilir. Düşük skorlu soruları elle veya hakem modelle inceleyin.'])


def mask_obj(obj, keys, counts, active=False):
    """Mask every string (keys=None) or only strings under the given keys, keeping the structure."""
    if isinstance(obj, dict):
        return {k: mask_obj(v, keys, counts, active or (keys is not None and k in keys)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v, keys, counts, active) for v in obj]
    if isinstance(obj, str) and (keys is None or active):
        return mask_value(obj, counts)
    return obj


def mask_value(v, counts):
    text, found = mask_text(v)
    counts.update(found)
    return text


@op('llm-log-maskele', title='LLM sohbet/çağrı loglarında kişisel veriyi maskele', domain=D, product=P, tag='log maskeleme', kind='file', samples={'sohbet_kayitlari.jsonl': 'sohbet_kayitlari.jsonl'},
    args=[arg('--keys', help='Yalnızca bu anahtarların değerlerini maskele (virgülle; varsayılan tüm metinler)')],
    description='JSON Lines biçimindeki sohbet, prompt ve cevap loglarında iç içe tüm metin değerlerindeki e-posta, telefon, TCKN (kontrol haneli), IBAN ve kart numaralarını tür etiketleriyle değiştirir; yapıyı ve anahtarları korur. Logları değerlendirme setine, sağlayıcıya veya analitiğe aktarmadan önce kullanılır; bozuk satırları kişisel veri sızmasın diye çıktıya yazmaz.',
    input='JSON Lines log dosyası', output='<ad>_maskeli.jsonl + sonuc.csv (tür bazında sayım)', command='python main.py --input prompts_2026-10.jsonl --keys content,prompt,completion',
    expect={'kayit': 3, 'bozuk_satir': 1, 'maskelenen': 5, 'maskeli_kayit': 3})
def log_mask(args):
    keys = {k.strip() for k in args.keys.split(',') if k.strip()} if args.keys else None
    lines, bad, counts, touched = [], 0, Counter(), 0
    for line in Path(args.input).read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        before = sum(counts.values())
        lines.append(json.dumps(mask_obj(obj, keys, counts), ensure_ascii=False))
        touched += sum(counts.values()) > before
    if not lines:
        raise ValueError('Okunabilir JSON satırı yok.')
    rows = [{'tur': k, 'adet': v} for k, v in counts.most_common()]
    return Result(f'{len(lines)} kayıt; {sum(counts.values())} kişisel veri maskelendi ({touched} kayıtta); {bad} bozuk satır atlandı', ['tur', 'adet'], rows,
                  {'kayit': len(lines), 'bozuk_satir': bad, 'maskelenen': sum(counts.values()), 'maskeli_kayit': touched},
                  files={f'{Path(args.input).stem}_maskeli.jsonl': '\n'.join(lines) + '\n'},
                  notes=['Ad-soyad ve adres gibi serbest metin kişisel verileri desenle yakalanmaz; bu alanları ayrıca gözden geçirin.'])


def parse_prices(text):
    out = {}
    for part in (p.strip() for p in re.split(r'[;,]', text or '') if p.strip()):
        name, _, val = part.partition('=')
        inp, _, outp = val.partition('/')
        try:
            out[name.strip()] = (float(inp), float(outp or inp))
        except ValueError:
            raise ValueError(f'--prices biçimi: model=girdi/çıktı (1M token başına), ör. "model-a=0.5/1.5". Hatalı: {part}') from None
    return out


@op('llm-kullanim-analizi', title='LLM çağrı loglarından maliyet, gecikme ve hata analizi', domain=D, product=P, tag='LLM kullanım', samples={'llm_cagrilari.csv': 'llm_cagrilari.csv'},
    args=[arg('--prices', default='model-a=0.5/1.5;model-b=3/15', help='model=girdi/çıktı USD (1M token başına); sözleşme fiyatlarınızı yazın'),
          arg('--group', default='uygulama,model')],
    description='Uygulama/model bazında çağrı sayısını, hata oranını, girdi ve çıktı token toplamını, verilen fiyatlarla maliyeti, p50/p95 gecikmeyi ve olumsuz geri bildirim oranını çıkarır; fiyatı tanımlanmamış modelleri ayrıca listeler. Model seçimi, bütçe ve SLA takibi için.',
    input='Çağrı logu (uygulama, model, girdi/çıktı token, süre ms, durum, geri bildirim)', output='sonuc.csv (grup bazında özet)', command='python main.py --input gateway_logs.csv --prices "kucuk-model=0.15/0.6;buyuk-model=3/15"',
    expect={'cagri': 12, 'hata_%': 16.7, 'toplam_maliyet': 0.1074, 'fiyatsiz_cagri': 1})
def llm_usage(columns, rows, args):
    for c in ('model', 'girdi_token', 'cikti_token'):
        need(c, columns, 'çağrı logu')
    prices = parse_prices(args.prices)
    keys = [k.strip() for k in args.group.split(',') if k.strip() in columns]
    tin, tout = numbers(rows, 'girdi_token'), numbers(rows, 'cikti_token')
    lat = numbers(rows, 'sure_ms') if 'sure_ms' in columns else [None] * len(rows)
    groups = defaultdict(list)
    for r, i, o, l in zip(rows, tin, tout, lat):
        groups[tuple(r[k].strip() for k in keys)].append((r, i or 0, o or 0, l))
    out, unpriced = [], 0
    for key, items in sorted(groups.items()):
        model = items[0][0]['model'].strip()
        price = prices.get(model)
        sum_in, sum_out = sum(x[1] for x in items), sum(x[2] for x in items)
        cost = round(sum_in * price[0] / 1e6 + sum_out * price[1] / 1e6, 6) if price else ''
        unpriced += 0 if price else len(items)
        errs = sum(x[0].get('durum', '').strip().lower() not in ('ok', 'basarili', 'success', '200', '') for x in items)
        lats = sorted(x[3] for x in items if x[3] is not None)
        fb = [x[0].get('geri_bildirim', '').strip().lower() for x in items if x[0].get('geri_bildirim', '').strip()]
        nearest = lambda q: lats[min(len(lats) - 1, max(0, math.ceil(len(lats) * q) - 1))] if lats else ''
        out.append({**dict(zip(keys, key)), 'cagri': len(items), 'hata_%': round(100 * errs / len(items), 1), 'girdi_token': int(sum_in), 'cikti_token': int(sum_out), 'maliyet_usd': cost,
                    'p50_ms': fmt(nearest(0.5)), 'p95_ms': fmt(nearest(0.95)), 'olumsuz_%': round(100 * fb.count('olumsuz') / len(fb), 1) if fb else '', '_err': errs})
    calls = sum(o['cagri'] for o in out)
    errors = sum(o.pop('_err') for o in out)
    total = sum(o['maliyet_usd'] for o in out if o['maliyet_usd'] != '')
    out.sort(key=lambda o: -(o['maliyet_usd'] or 0))
    return Result(f'{calls} çağrı; hata %{100 * errors / calls:.1f}; toplam ${total:.4f}', list(out[0]), out,
                  {'cagri': calls, 'hata_%': round(100 * errors / calls, 1), 'toplam_maliyet': round(total, 4), 'fiyatsiz_cagri': unpriced,
                   'fiyatsiz_model': sorted({o.get('model', '') for o in out if o['maliyet_usd'] == ''})},
                  notes=['Fiyatlar örnektir; sağlayıcınızın güncel liste veya sözleşme fiyatlarını --prices ile verin. Önbelleğe alınmış girdi tokenları ayrıca indirimli olabilir.'])


PLACEHOLDER = re.compile(r'(?<!\{)\{([A-Za-z_çğıöşüÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ]*)\}(?!\})')


@op('prompt-sablon-kontrol', title='Prompt şablonlarında eksik ve kullanılmayan değişkenler', domain=D, product=P, tag='prompt şablonu', kind='file',
    samples={'promptlar/ozet.txt': 'promptlar/ozet.txt', 'promptlar/siniflandir.md': 'promptlar/siniflandir.md', 'degiskenler.json': 'degiskenler.json'}, sample_input='promptlar',
    args=[arg('--variables', default='@ornek/degiskenler.json', help='Uygulamanın sağladığı değişkenler: {"degiskenler": [...]} veya {"dosya": [...]}')],
    description='Bir klasördeki prompt şablonlarında {degisken} yer tutucularını bulur ({{ }} kaçışlarını yok sayar) ve uygulamanın sağladığı değişken listesiyle karşılaştırır: şablonda olup verilmeyen (çalışırken hata veya boş kalan) ve verilip hiçbir şablonda kullanılmayan değişkenleri, şablon başına yaklaşık token sayısını raporlar. Prompt değişikliklerini yayına almadan önce CI kontrolü için.',
    input='Şablon klasörü (.txt/.md/.prompt) + değişken listesi (JSON)', output='sonuc.csv (şablon × değişken)', command='python main.py --input prompts/ --variables app_variables.json',
    expect={'sablon': 2, 'degisken': 6, 'eksik': 1, 'kullanilmayan': 1})
def prompt_vars(args):
    p = Path(args.input)
    files = sorted(f for f in (p.rglob('*') if p.is_dir() else [p]) if f.suffix.lower() in ('.txt', '.md', '.prompt', '.j2', '.tmpl'))
    if not files:
        raise ValueError('Şablon dosyası bulunamadı (.txt, .md, .prompt).')
    try:
        given = json.loads(Path(args.variables).read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Değişken JSON okunamadı: {exc}') from None
    common = set(given.get('degiskenler', [])) if isinstance(given, dict) else set(given)
    out, used = [], set()
    for f in files:
        text = f.read_text(encoding='utf-8-sig', errors='replace')
        names = sorted(set(PLACEHOLDER.findall(text)))
        avail = common | set(given.get(f.name, []) if isinstance(given, dict) else [])
        used.update(names)
        for n in names:
            out.append({'sablon': f.name, 'degisken': n, 'durum': 'verilmiyor (EKSİK)' if n not in avail else 'tamam', 'yaklasik_token': round(len(text) / 4)})
    unused = sorted(common - used)
    rows = sorted(out, key=lambda o: (o['durum'] == 'tamam', o['sablon']))
    return Result(f'{len(files)} şablon, {len(used)} değişken; {sum(o["durum"] != "tamam" for o in out)} eksik, {len(unused)} kullanılmayan', ['sablon', 'degisken', 'durum', 'yaklasik_token'], rows,
                  {'sablon': len(files), 'degisken': len(used), 'eksik': sum(o['durum'] != 'tamam' for o in out), 'kullanilmayan': len(unused)},
                  tables=[('Verilen ama kullanılmayan değişkenler', ['Değişken'], [[u] for u in unused])],
                  notes=['Token sayısı karakter/4 yaklaşımıdır; Türkçe metinlerde gerçek sayı biraz daha yüksek olabilir.'])


CITE = re.compile(r'\[([^\[\]\s]+)\]')


@op('atif-kontrol', title='RAG cevaplarındaki kaynak atıflarını getirilen dokümanlarla doğrula', domain=D, product=P, tag='atıf', samples={'rag_cevaplari.csv': 'rag_cevaplari.csv'},
    args=[arg('--answer', default='cevap'), arg('--retrieved', default='getirilen', help='Getirilen doküman kimlikleri (; ile)')],
    description='Model cevabındaki [doküman_kimliği] biçimli atıfları o soru için gerçekten getirilen dokümanlarla karşılaştırır: getirilmeyen bir dokümana yapılan (uydurma) atıfları, hiç atıf içermeyen cevapları ve doğru atıf oranını hesaplar. Kaynak gösterme zorunluluğu olan asistanların kalite kontrolü için.',
    input='Cevaplar (soru, cevap, getirilen doküman kimlikleri)', output='sonuc.csv (cevap bazında atıf durumu)', command='python main.py --input eval_run.csv --retrieved context_ids',
    expect={'cevap': 5, 'atifli': 4, 'uydurma_atif': 2, 'atifsiz': 1, 'dogru_atif_%': 60.0})
def citations(columns, rows, args):
    acol, rcol = need(args.answer, columns, '--answer'), need(args.retrieved, columns, '--retrieved')
    out, total, good = [], 0, 0
    for i, r in enumerate(rows, 2):
        cites = CITE.findall(r[acol])
        got = {x.strip() for x in re.split(r'[;,]', r[rcol]) if x.strip()}
        fake = [c for c in cites if c not in got]
        total += len(cites)
        good += len(cites) - len(fake)
        out.append({'satir': i, 'atif': ', '.join(cites), 'getirilen': ', '.join(sorted(got)), 'uydurma': ', '.join(fake),
                    'durum': 'atıfsız' if not cites else 'UYDURMA ATIF' if fake else 'doğru'})
    return Result(f'{len(out)} cevap; {sum(o["durum"] == "UYDURMA ATIF" for o in out)} cevapta uydurma atıf, {sum(o["durum"] == "atıfsız" for o in out)} atıfsız', list(out[0]), out,
                  {'cevap': len(out), 'atifli': sum(bool(o['atif']) for o in out), 'uydurma_atif': total - good, 'atifsiz': sum(o['durum'] == 'atıfsız' for o in out),
                   'dogru_atif_%': round(100 * good / total, 1) if total else 0})


POSITIVE = {'1', 'up', 'yes', 'evet', 'olumlu', 'iyi', 'like', '👍', 'true', '+1', '+'}
NEGATIVE = {'0', 'down', 'no', 'hayir', 'olumsuz', 'kotu', 'dislike', '👎', 'false', '-1', '-'}


def wilson_low(pos, n, z=1.96):
    if not n:
        return None
    p = pos / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


@op('geri-bildirim-analizi', title='Asistan/chatbot kullanıcı geri bildirimini konu bazında güven aralığıyla değerlendir', domain=D, product=P, tag='kullanıcı geri bildirimi',
    samples={'llm_geri_bildirim.csv': 'llm_geri_bildirim.csv'},
    args=[arg('--by', default='niyet', help='Gruplama sütunu (niyet, model, prompt sürümü…)'), arg('--rating', default='puan', help='Olumlu/olumsuz (1/0, up/down, 👍/👎)'),
          arg('--second', default='model', help='İkinci kırılım (isteğe bağlı, boş bırakılabilir)'), arg('--min-n', type=int, default=5), arg('--min-score', type=float, default=0.5, help='Wilson alt sınırı bu değerin altındaysa sorunlu')],
    description='LLM asistanına verilen beğendim/beğenmedim geri bildirimlerini konu (niyet), model veya prompt sürümüne göre gruplar; olumlu oranı ve az örnekli grupların yanıltmaması için Wilson %95 alt sınırını hesaplar. Alt sınırı eşiğin altında kalan konuları “sorunlu”, örneği az olanları “az veri” olarak ayırır; ikinci kırılımla (ör. model) karşılaştırma tablosu verir.',
    input='Geri bildirim kayıtları (konu, puan; isteğe bağlı model)', output='sonuc.csv (konu bazında olumlu oran ve alt sınır)', command='python main.py --input feedback.csv --by intent --rating thumbs --second prompt_version',
    expect={'geri_bildirim': 33, 'olumlu_%': 72.73, 'sorunlu_grup': 1, 'az_veri': 1})
def feedback(columns, rows, args):
    b, rc = need(args.by, columns, '--by'), need(args.rating, columns, '--rating')
    sc = need(args.second, columns, '--second') if args.second else None
    groups, second, skipped = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), 0
    for r in rows:
        v = fold(r[rc]).strip().lower()
        if v not in POSITIVE and v not in NEGATIVE:
            skipped += 1
            continue
        good = v in POSITIVE
        for bucket, key in ((groups, r[b].strip() or '(boş)'), *(((second, (r[b].strip() or '(boş)', r[sc].strip() or '(boş)')),) if sc else ())):
            bucket[key][0] += good
            bucket[key][1] += 1
    if not groups:
        raise ValueError('Olumlu/olumsuz olarak okunabilen puan yok.')
    out = []
    for name, (pos, n) in groups.items():
        low = wilson_low(pos, n)
        status = 'az veri' if n < args.min_n else 'sorunlu' if low < args.min_score else 'iyi'
        out.append({args.by: name, 'geri_bildirim': n, 'olumlu': pos, 'olumlu_%': round(100 * pos / n, 2), 'wilson_alt_%': round(100 * low, 2), 'durum': status})
    order = {'sorunlu': 0, 'az veri': 1, 'iyi': 2}
    out.sort(key=lambda o: (order[o['durum']], o['wilson_alt_%']))
    total, pos = sum(o['geri_bildirim'] for o in out), sum(o['olumlu'] for o in out)
    tables = [(f'{args.by} × {args.second}', [args.by, args.second, 'Geri bildirim', 'Olumlu %'],
               [[k[0], k[1], n, round(100 * p / n, 1)] for k, (p, n) in sorted(second.items())])] if sc else []
    return Result(f'{total} geri bildirim, olumlu %{fmt(round(100 * pos / total, 2))}; {sum(o["durum"] == "sorunlu" for o in out)} konu sorunlu', list(out[0]), out,
                  {'geri_bildirim': total, 'olumlu_%': round(100 * pos / total, 2), 'sorunlu_grup': sum(o['durum'] == 'sorunlu' for o in out),
                   'az_veri': sum(o['durum'] == 'az veri' for o in out), 'okunamayan_puan': skipped}, tables=tables,
                  notes=['Wilson alt sınırı, olumlu oranın %95 güvenle en az bu kadar olduğunu söyler; 3/3 olumlu bir konu 90/100’den daha iyi sayılmaz.',
                         'Geri bildirim veren kullanıcılar tüm kullanıcıları temsil etmez; olumsuz deneyimler genelde daha çok bildirilir.'])
