"""GenAI / RAG veri hazırlığı blokları: doküman parçalama, tekrar eden içerik, token ve maliyet tahmini, prompt enjeksiyon taraması, geri getirme değerlendirmesi.
Hiçbir model veya embedding servisine bağlanmaz."""
import hashlib, json, math, re, unicodedata
from collections import Counter
from pathlib import Path
from islem import op, arg, Result, need, fmt

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
