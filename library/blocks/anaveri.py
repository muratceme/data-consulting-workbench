"""Ana veri (MDM) blokları: benzer kayıt kümeleme, ünvan ve adres standardı, golden record, ad-soyad ayırma, hiyerarşi kontrolü."""
import difflib, re
from collections import Counter, defaultdict
import tabular
import turkiye as tr
from islem import op, arg, Result, need, col_list, fold, fmt, tr_title, tr_sort_key

REQUIRES = ['turkiye']
D, P = 'mdm', 'Ana veri temizliği'
FIRMA = {'firmalar.csv': 'firmalar.csv'}
LEGAL = [(r'\b(anonim\s+sirketi|a\.?\s?s\.?|as)\b', 'A.Ş.'), (r'\b(limited\s+sirketi|ltd\.?\s*sti\.?|ltd\.?|sti\.?)\b', 'Ltd. Şti.'),
         (r'\b(kollektif\s+sirketi|koll\.?\s*sti\.?)\b', 'Koll. Şti.'), (r'\b(komandit\s+sirketi|kom\.?\s*sti\.?)\b', 'Kom. Şti.'),
         (r'\b(kooperatifi|koop\.?)\b', 'Kooperatif')]
NOISE = r'\b(sanayi|san\.?|ticaret|tic\.?|ve|ithalat|ihracat|ith\.?|ihr\.?|pazarlama|paz\.?|ins\.?|insaat|limited|sirketi|anonim|ltd|sti|as|a\.s\.?)\b'


def legal_form(name):
    f = fold(name).replace('ş', 's')
    for pattern, label in LEGAL:
        if re.search(pattern, f):
            return label
    return 'Şahıs / belirsiz'


def core_name(name):
    """Comparable company core: folded, legal forms and filler words removed."""
    f = fold(name)
    f = re.sub(NOISE, ' ', f)
    f = re.sub(r'[.,/&()\-]', ' ', f)
    f = re.sub(NOISE + r'|\ba s\b|\bltd sti\b', ' ', f)
    return re.sub(r'\s+', ' ', f).strip()


def clusters(rows, name_col, id_col, extra_keys, threshold):
    keys = [core_name(r[name_col]) for r in rows]
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    pairs = []
    blocks = defaultdict(list)
    for i, k in enumerate(keys):
        blocks[k[:3]].append(i)
        for col in extra_keys:
            v = re.sub(r'\D', '', rows[i][col]) if col else ''
            if v:
                blocks[f'{col}:{v}'].append(i)
    seen = set()
    for members in blocks.values():
        for a_i, a in enumerate(members):
            for b in members[a_i + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                score = difflib.SequenceMatcher(None, keys[a], keys[b]).ratio()
                shared = [c for c in extra_keys if re.sub(r'\D', '', rows[a][c]) and re.sub(r'\D', '', rows[a][c]) == re.sub(r'\D', '', rows[b][c])]
                if score >= threshold or shared and score >= threshold - 0.25:
                    pairs.append({'kayit_1': rows[a][id_col], 'kayit_2': rows[b][id_col], 'benzerlik': round(score, 3), 'ortak_alan': ', '.join(shared)})
                    parent[find(a)] = find(b)
    groups = defaultdict(list)
    for i in range(len(rows)):
        groups[find(i)].append(i)
    cid = {}
    for n, (root, members) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1])), start=1):
        for i in members:
            cid[i] = (f'K{n:04d}', len(members))
    return cid, pairs


MATCH_ARGS = [arg('--name', default='unvan'), arg('--id', default='cari_kodu'), arg('--keys', default='vkn,telefon', help='Eşleşmeyi güçlendiren sütunlar (VKN, telefon…)'),
              arg('--threshold', type=float, default=0.85)]


@op('benzer-kayit', title='Benzer (mükerrer) firma/müşteri kayıtlarını kümele', domain=D, product=P, tag='eşleştirme', samples=FIRMA, args=MATCH_ARGS,
    description='Ünvanları Türkçe karakter, büyük/küçük harf, şirket türü (A.Ş., Ltd. Şti.) ve “San. ve Tic.” gibi dolgu kelimelerinden arındırıp benzerlik skoruyla karşılaştırır; VKN veya telefon ortaklığı eşleşmeyi güçlendirir. Aynı varlığa ait kayıtlara küme numarası verir.',
    input='Cari/müşteri listesi (ad, kod; isteğe bağlı VKN, telefon)', output='sonuc.csv (küme no) + ciftler.csv', command='python main.py --name unvan --keys vkn,telefon --threshold 0.85',
    expect={'kume': 7, 'mukerrer_kume': 3, 'mukerrer_kayit': 6})
def benzer(columns, rows, args):
    need(args.name, columns, '--name')
    id_col = args.id if args.id in columns else columns[0]
    keys = [k for k in col_list(args.keys, columns, '--keys', required=False)]
    cid, pairs = clusters(rows, args.name, id_col, keys, args.threshold)
    out = [{**r, 'kume_no': cid[i][0], 'kume_boyutu': cid[i][1]} for i, r in enumerate(rows)]
    multi = {c for c, n in cid.values() if n > 1}
    return Result(f'{len(rows)} kayıt {len({c for c, _ in cid.values()})} kümede; {len(multi)} kümede mükerrer şüphesi', columns + ['kume_no', 'kume_boyutu'], out,
                  {'kume': len({c for c, _ in cid.values()}), 'mukerrer_kume': len(multi), 'mukerrer_kayit': sum(n > 1 for _, n in cid.values())},
                  files={'ciftler.csv': (['kayit_1', 'kayit_2', 'benzerlik', 'ortak_alan'], pairs)},
                  notes=['Eşik düşürüldükçe daha çok eşleşme ve daha çok yanlış birleşme olur. Kümeleri birleştirmeden önce gözden geçirin.'])


@op('unvan-normalize', title='Şirket ünvanlarını standartlaştır', domain=D, product=P, tag='ünvan', samples=FIRMA,
    description='“ABC GIDA SAN. VE TİC. AŞ”, “Beta Yapi Limited Sirketi” gibi ünvanlarda şirket türünü (A.Ş., Ltd. Şti., Koll. Şti., Kooperatif) tespit edip standart kısaltmayla yazar, Türkçe baş harf büyük yapar ve eşleştirme için çekirdek ad üretir.',
    input='CSV/XLSX + ünvan sütunu', output='sonuc.csv', command='python main.py --column unvan',
    args=[arg('--column', default='unvan')], expect={'as': 4, 'ltd': 3, 'diger': 3})
def unvan(columns, rows, args):
    need(args.column, columns, '--column')
    out, kinds = [], Counter()
    for r in rows:
        form = legal_form(r[args.column])
        kinds[form] += 1
        base = re.sub(r'\s+', ' ', r[args.column]).strip()
        f = fold(base)
        for pattern, _ in LEGAL:
            m = re.search(pattern, f.replace('ş', 's'))
            if m:
                base = base[:m.start()].rstrip(' ,')
                break
        std = tr_title(base) if base.isupper() else base
        std = re.sub(r'\bSan\.?\s*ve\s*Tic\.?', 'San. ve Tic.', std, flags=re.I)
        std = re.sub(r'\bSanayi ve Ticaret\b', 'Sanayi ve Ticaret', std, flags=re.I)
        out.append({**r, 'unvan_standart': f"{std} {form}" if form != 'Şahıs / belirsiz' else std, 'sirket_turu': form, 'cekirdek_ad': core_name(r[args.column])})
    return Result(f"{kinds['A.Ş.']} A.Ş., {kinds['Ltd. Şti.']} Ltd. Şti., {len(rows) - kinds['A.Ş.'] - kinds['Ltd. Şti.']} diğer", columns + ['unvan_standart', 'sirket_turu', 'cekirdek_ad'], out,
                  {'as': kinds['A.Ş.'], 'ltd': kinds['Ltd. Şti.'], 'diger': len(rows) - kinds['A.Ş.'] - kinds['Ltd. Şti.']},
                  tables=[('Şirket türleri', ['Tür', 'Kayıt'], kinds.most_common())])


ADDR = {'mahalle': r'([\wçğıöşüÇĞİÖŞÜ.\- ]+?)\s+(?:mahallesi|mah\.?|mh\.?)(?=\s|$|,)',
        'cadde_sokak': r'([\wçğıöşüÇĞİÖŞÜ.\- ]+?\s+(?:caddesi|cad\.?|cd\.?|sokak|sokağı|sok\.?|sk\.?|bulvarı|blv\.?|bulvar))(?=\s|$|,)',
        'no': r'\bno\s*[:.]?\s*(\d+[a-zA-Z]?)', 'kat': r'\bkat\s*[:.]?\s*(\d+)', 'daire': r'\b(?:daire|d)\s*[:.]\s*(\d+)'}


def parse_address(text):
    t = re.sub(r'\s+', ' ', text).strip()
    out = {}
    for key, rx in ADDR.items():
        m = re.search(rx, t, re.I)
        out[key] = m.group(1).strip(' ,') if m else ''
    if not out['no']:  # "Bulvarı 45" style: the number right after the street is the door number
        m = re.search(r'(?:caddesi|cad\.?|cd\.?|sokak|sokağı|sok\.?|sk\.?|bulvarı|blv\.?)\s+(\d+[a-zA-Z]?)\b', t, re.I)
        out['no'] = m.group(1) if m else ''
    if out['mahalle']:
        out['mahalle'] = out['mahalle'].split(',')[-1].strip()
        out['mahalle'] = re.sub(r'^.*\b(?:no|kat|d)\s*[:.]?\s*\d+\s*', '', out['mahalle'], flags=re.I).strip()
    tail = re.split(r'[\s,]+', re.sub(r'\s*/\s*', ' / ', t))
    il, ilce = '', ''
    for i in range(len(tail) - 1, -1, -1):
        name, how = tr.il_bul(tail[i])
        if name and how in ('tam', 'yazım', 'eş ad'):
            il = name
            j = i - 1
            while j >= 0 and tail[j] == '/':
                j -= 1
            if j >= 0 and not re.search(r'\d', tail[j]):
                ilce = tr_title(tail[j].lower()) if tail[j].isupper() else tail[j]
            break
    out['ilce'], out['il'] = ilce, il
    return out


@op('adres-ayristir', title='Türkçe adresleri parçalarına ayır', domain=D, product=P, tag='adres', samples=FIRMA,
    description='Serbest yazılmış adreslerden mahalle, cadde/sokak/bulvar, kapı no, kat, daire, ilçe ve ili (81 il listesine göre) ayrı sütunlara çıkarır. Kural tabanlıdır; bulunamayan parçaları boş bırakır ve tamlık oranını raporlar.',
    input='CSV/XLSX + adres sütunu', output='sonuc.csv', command='python main.py --column adres',
    args=[arg('--column', default='adres')], expect={'il_bulunan': 10, 'mahalle_bulunan': 9, 'no_bulunan': 10})
def adres(columns, rows, args):
    need(args.column, columns, '--column')
    parts = ['mahalle', 'cadde_sokak', 'no', 'kat', 'daire', 'ilce', 'il']
    out = []
    for r in rows:
        p = parse_address(r[args.column])
        out.append({**r, **{f'adr_{k}': p[k] for k in parts}})
    found = {k: sum(bool(o[f'adr_{k}']) for o in out) for k in parts}
    return Result(f"{len(rows)} adres ayrıştırıldı; il {found['il']}, mahalle {found['mahalle']}, kapı no {found['no']} kayıtta bulundu", columns + [f'adr_{k}' for k in parts], out,
                  {'il_bulunan': found['il'], 'mahalle_bulunan': found['mahalle'], 'no_bulunan': found['no']},
                  tables=[('Parça tamlığı', ['Parça', 'Bulunan', '%'], [[k, v, round(100 * v / len(rows))] for k, v in found.items()])],
                  notes=['Kural tabanlı ayrıştırmadır; UAVT/adres kodu doğrulaması yapmaz.'])


@op('golden-record', title='Mükerrer kayıtlardan tek “altın kayıt” üret', domain=D, product=P, tag='golden record', samples=FIRMA,
    args=MATCH_ARGS + [arg('--cluster', help='Hazır küme sütunu (benzer-kayit çıktısı); yoksa kümeler burada hesaplanır'),
                       arg('--recent', default='guncelleme', help='En güncel kaydı seçmek için tarih sütunu')],
    description='Aynı varlığa ait kayıt kümelerinden alan bazında kurala göre (en güncel dolu değer; yoksa en sık değer) tek kayıt üretir. Her alanın hangi kaynak kayıttan geldiğini ve hangi kayıtların birleştiğini ayrı dosyada izlenebilir tutar.',
    input='Cari/müşteri listesi (+ isteğe bağlı küme sütunu)', output='sonuc.csv (altın kayıtlar) + kaynak-izi.csv', command='python main.py --cluster kume_no --recent guncelleme',
    expect={'altin_kayit': 7, 'birlesen_kume': 3})
def golden(columns, rows, args):
    id_col = args.id if args.id in columns else columns[0]
    if args.cluster:
        need(args.cluster, columns, '--cluster')
        groups = defaultdict(list)
        for i, r in enumerate(rows):
            groups[r[args.cluster]].append(i)
    else:
        need(args.name, columns, '--name')
        cid, _ = clusters(rows, args.name, id_col, col_list(args.keys, columns, '--keys', required=False), args.threshold)
        groups = defaultdict(list)
        for i in range(len(rows)):
            groups[cid[i][0]].append(i)
    recency = {}
    if args.recent in columns:
        for i, r in enumerate(rows):
            d = tabular.parse_date(r[args.recent])
            recency[i] = d[0] if d else None
    out, trail = [], []
    for g, members in sorted(groups.items()):
        ordered = sorted(members, key=lambda i: (recency.get(i) is not None, recency.get(i) or 0), reverse=True)
        rec = {'altin_id': g, 'kaynak_kayitlar': ', '.join(rows[i][id_col] for i in members)}
        for c in columns:
            filled = [i for i in ordered if rows[i][c].strip()]
            if not filled:
                rec[c] = ''
                continue
            pick = filled[0] if recency else Counter(rows[i][c] for i in filled).most_common(1)[0][0]
            source = pick if isinstance(pick, int) else next(i for i in filled if rows[i][c] == pick)
            rec[c] = rows[source][c]
            if len(members) > 1:
                trail.append({'altin_id': g, 'alan': c, 'deger': rows[source][c], 'kaynak': rows[source][id_col], 'kural': 'en güncel dolu değer' if recency else 'en sık değer'})
        out.append(rec)
    merged = sum(len(m) > 1 for m in groups.values())
    return Result(f'{len(rows)} kayıttan {len(out)} altın kayıt; {merged} küme birleşti', ['altin_id', 'kaynak_kayitlar'] + columns, out,
                  {'altin_kayit': len(out), 'birlesen_kume': merged}, files={'kaynak-izi.csv': (['altin_id', 'alan', 'deger', 'kaynak', 'kural'], trail)},
                  notes=['Kaynak sistemlerde birleştirme yapılmaz; çıktı gözden geçirme ve ana veri yüklemesi içindir.'])


TITLES = {'dr', 'dr.', 'prof', 'prof.', 'doç', 'doç.', 'av', 'av.', 'müh', 'müh.', 'uzm', 'uzm.', 'sn', 'sn.', 'bay', 'bayan'}


@op('ad-soyad-ayir', title='Kişi adlarını ad / ikinci ad / soyad olarak ayır', domain=D, product=P, tag='ad soyad', samples={'musteriler.csv': 'musteriler.csv'},
    description='“dr. ayşe NUR yılmaz” gibi tam adları unvan, ad, ikinci ad ve soyad olarak ayırır; Türkçe kurallarla baş harf büyük yazar (i/İ, ı/I). Tek kelimelik ve şüpheli değerleri ayrıca işaretler.',
    input='CSV/XLSX + tam ad sütunu', output='sonuc.csv', command='python main.py --column ad_soyad',
    args=[arg('--column', default='ad_soyad')], expect={'ayrilan': 12, 'tek_kelime': 0})
def ad_soyad(columns, rows, args):
    need(args.column, columns, '--column')
    out, single = [], 0
    for r in rows:
        words = [w for w in re.split(r'\s+', r[args.column].strip()) if w]
        title = []
        while words and words[0].lower() in TITLES:
            title.append(tr_title(words.pop(0).lower()))
        words = [tr_title(w.lower()) if w.isupper() or w.islower() else w for w in words]
        if len(words) == 1:
            single += 1
        first, middle, last = (words[0] if words else ''), (' '.join(words[1:-1]) if len(words) > 2 else ''), (words[-1] if len(words) > 1 else '')
        out.append({**r, 'unvan': ' '.join(title), 'ad': first, 'ikinci_ad': middle, 'soyad': last})
    return Result(f'{len(rows) - single} ad ayrıldı, {single} tek kelimelik değer', columns + ['unvan', 'ad', 'ikinci_ad', 'soyad'], out,
                  {'ayrilan': len(rows) - single, 'tek_kelime': single},
                  notes=['Birden çok kelimeli soyadlarda (ör. “Karaosmanoğlu Yılmaz”) son kelime soyad sayılır; kontrol edin.'])


@op('hiyerarsi-kontrol', title='Hiyerarşi (ağaç) yapısını doğrula ve düzleştir', domain=D, product=P, tag='hiyerarşi', samples={'kategori_agaci.csv': 'kategori_agaci.csv'},
    description='Ürün kategorisi, organizasyon şeması veya hesap planı gibi üst-alt ilişkilerinde yetim kayıtları, döngüleri, birden çok kökü ve derinliği bulur; her düğüm için kökten yolu (Elektronik > Bilgisayar > Dizüstü) ve seviyeyi yazar.',
    input='CSV/XLSX (id, üst id, ad)', output='sonuc.csv (yol ve seviye) + rapor', command='python main.py --id kategori_id --parent ust_id --name ad',
    args=[arg('--id', default='kategori_id'), arg('--parent', default='ust_id'), arg('--name', default='ad')],
    expect={'dugum': 12, 'kok': 1, 'yetim': 1, 'donguye_giren': 2, 'max_derinlik': 4})
def hiyerarsi(columns, rows, args):
    ids, par, name = need(args.id, columns, '--id'), need(args.parent, columns, '--parent'), need(args.name, columns, '--name')
    parent = {r[ids].strip(): r[par].strip() for r in rows}
    label = {r[ids].strip(): r[name].strip() for r in rows}
    dup = [k for k, n in Counter(r[ids].strip() for r in rows).items() if n > 1]
    out, cyc, orphan, roots = [], set(), set(), [k for k, p in parent.items() if not p]
    for node in parent:
        path, seen, cur, status = [], set(), node, ''
        while cur:
            if cur in seen:
                status = 'döngü'
                cyc.update(seen)
                break
            seen.add(cur)
            if cur not in parent:
                status = 'yetim (üst kayıt yok)'
                orphan.add(node)
                break
            path.append(label[cur])
            cur = parent[cur]
        out.append({'id': node, 'ad': label[node], 'ust_id': parent[node], 'seviye': len(path) if not status else '',
                    'yol': ' > '.join(reversed(path)) if not status else '', 'durum': status or 'tamam'})
    depth = max((o['seviye'] for o in out if o['seviye'] != ''), default=0)
    out.sort(key=lambda o: (o['durum'] != 'tamam', tr_sort_key(o['yol'] or o['ad'])))
    return Result(f'{len(out)} düğüm, {len(roots)} kök, derinlik {depth}; {len(orphan)} yetim, {len(cyc)} düğüm döngüde', ['id', 'ad', 'ust_id', 'seviye', 'yol', 'durum'], out,
                  {'dugum': len(out), 'kok': len(roots), 'yetim': len([o for o in out if o['durum'].startswith('yetim')]), 'donguye_giren': len(cyc),
                   'max_derinlik': depth, 'tekrar_eden_id': len(dup)})


@op('referans-esle', title='Serbest metin değerleri referans kod listesine eşle', domain=D, product=P, tag='kod listesi', samples={'urun_birimleri.csv': 'urun_birimleri.csv', 'birimler.csv': 'birimler.csv'},
    args=[arg('--column', default='birim'), arg('--reference', default='@ornek/birimler.csv', help='Referans liste (kod, ad, esanlamlilar)'), arg('--min', type=float, default=0.85, help='Benzerlik eşiği')],
    description='Bir sütundaki serbest metin değerleri (birim, ülke, şehir, kategori, ödeme tipi…) referans kod listesine eşler: kod, ad ve eş anlamlılarla büyük/küçük harf ve Türkçe karakter farkı gözetmeden tam eşleşme, olmazsa yazım benzerliğiyle öneri üretir. Farklı değer başına kullanım sayısı ve eşleme tablosu (eslesme_tablosu.csv) çıkarır; temizlik kuralı olarak geri yüklenebilir.',
    input='Değerlerin olduğu dosya + referans kod listesi', output='sonuc.csv (farklı değer → kod) + eslesme_tablosu.csv', command='python main.py --input malzeme.xlsx --column olcu_birimi --reference birim_kodlari.csv --min 0.8',
    expect={'farkli_deger': 12, 'tam': 10, 'benzer': 1, 'eslesmeyen': 1})
def ref_map(columns, rows, args):
    col = need(args.column, columns, '--column')
    rcols, refs = tabular.read(args.reference)
    for c in ('kod', 'ad'):
        need(c, rcols, 'referans liste')
    index = {}
    for r in refs:
        for name in [r['kod'], r['ad'], *(r.get('esanlamlilar', '') or '').split(',')]:
            if name.strip():
                index.setdefault(fold(name), r)
    counts, sample = Counter(), {}
    for r in rows:
        v = r[col].strip()
        if v:
            counts[fold(v)] += 1
            sample.setdefault(fold(v), v)
    out = []
    for key, n in counts.most_common():
        hit = index.get(key)
        if hit:
            out.append({'deger': sample[key], 'adet': n, 'kod': hit['kod'], 'ad': hit['ad'], 'yontem': 'tam', 'skor': 1.0})
            continue
        best = max(index, key=lambda k: (difflib.SequenceMatcher(None, key, k).ratio(), k), default=None)
        score = difflib.SequenceMatcher(None, key, best).ratio() if best else 0.0
        ok = score >= args.min
        out.append({'deger': sample[key], 'adet': n, 'kod': index[best]['kod'] if ok else '', 'ad': index[best]['ad'] if ok else '', 'yontem': 'benzer (onaylayın)' if ok else 'eşleşmedi',
                    'skor': round(score, 3)})
    c = Counter(o['yontem'] for o in out)
    mapping = [{'kaynak_deger': o['deger'], 'kod': o['kod']} for o in out if o['kod']]
    return Result(f'{len(out)} farklı değer; {c["tam"]} tam, {c["benzer (onaylayın)"]} benzer, {c["eşleşmedi"]} eşleşmedi', ['deger', 'adet', 'kod', 'ad', 'yontem', 'skor'], out,
                  {'farkli_deger': len(out), 'tam': c['tam'], 'benzer': c['benzer (onaylayın)'], 'eslesmeyen': c['eşleşmedi'],
                   'eslesen_kayit_%': round(100 * sum(o['adet'] for o in out if o['kod']) / (sum(counts.values()) or 1), 1)},
                  files={'eslesme_tablosu.csv': (['kaynak_deger', 'kod'], mapping)},
                  notes=['Benzer eşleşmeler yazım yakınlığına dayanır; "kutu" ile "koli" gibi anlamca farklı değerleri onaylamadan kullanmayın.'])


def valid_for(col, value):
    """'' when the value is valid for a column recognised by name, otherwise the reason; None when the column has no rule."""
    name = fold(col).replace(' ', '_')
    v = value.strip()
    if re.search(r'e_?posta|email|e_?mail', name):
        return '' if re.fullmatch(r'[\w.+-]+@[\w-]+(\.[\w-]+)+', v) else 'e-posta biçimi'
    if re.search(r'telefon|gsm|cep', name):
        d = re.sub(r'\D', '', v)
        d = d[2:] if d.startswith('90') and len(d) == 12 else d[1:] if d.startswith('0') and len(d) == 11 else d
        return '' if len(d) == 10 and d[0] in '2345' else 'telefon 10 hane değil'
    if re.search(r'tckn|tc_?kimlik', name):
        return tr.tckn(v)
    if re.search(r'vkn|vergi_?no', name):
        return tr.vkn(v)
    if 'iban' in name:
        return tr.iban(v)
    if re.search(r'posta_?kodu', name):
        return '' if re.fullmatch(r'(0[1-9]|[1-7]\d|8[01])\d{3}', v) else 'posta kodu'
    if re.fullmatch(r'il|sehir', name):
        return '' if tr.il_bul(v)[0] else 'il adı tanınmadı'
    return None


@op('ana-veri-tamlik', title='Ana veri kayıtları için tamlık ve geçerlilik skoru', domain=D, product=P, tag='veri kalitesi skoru', samples={'cari_kartlar.csv': 'cari_kartlar.csv'},
    args=[arg('--required', default='unvan,vkn,telefon,eposta,il,iban', help='Skora giren alanlar (alan:ağırlık da yazılabilir)'), arg('--key', default='cari_kod'),
          arg('--min-score', type=float, default=80.0)],
    description='Müşteri, tedarikçi veya cari kartlarında her kayıt için ağırlıklı tamlık ve geçerlilik skorunu hesaplar: alan dolu mu, adına göre tanınan alanlarda değer geçerli mi (VKN/TCKN kontrol hanesi, IBAN mod 97, e-posta, telefon, il, posta kodu). Alan bazında doluluk/geçerlilik oranlarını ve eşik altındaki kayıtları eksik alan listesiyle verir.',
    input='Ana veri listesi (CSV/XLSX)', output='sonuc.csv (kayıt bazında skor) + alan özeti', command='python main.py --input tedarikciler.xlsx --required "unvan:2,vkn:3,iban:3,eposta,telefon" --min-score 90',
    expect={'kayit': 8, 'tam_kayit': 2, 'esik_alti': 3})
def completeness(columns, rows, args):
    fields = []
    for part in (p.strip() for p in args.required.split(',') if p.strip()):
        name, _, w = part.partition(':')
        fields.append((need(name.strip(), columns, '--required'), float(w) if w else 1.0))
    total_w = sum(w for _, w in fields)
    key = args.key if args.key in columns else None
    stats = {f: [0, 0] for f, _ in fields}
    out = []
    for i, r in enumerate(rows, 2):
        got, problems = 0.0, []
        for f, w in fields:
            v = r[f].strip()
            if not v:
                problems.append(f'{f}: boş')
                continue
            stats[f][0] += 1
            reason = valid_for(f, v)
            if reason:
                problems.append(f'{f}: {reason}')
            else:
                stats[f][1] += 1
                got += w
        score = round(100 * got / total_w, 1)
        out.append({'kayit': r[key] if key else i, 'skor': score, 'sorunlar': '; '.join(problems)})
    out.sort(key=lambda o: (o['skor'], str(o['kayit'])))
    n = len(rows) or 1
    field_rows = [[f, w, round(100 * stats[f][0] / n, 1), round(100 * stats[f][1] / n, 1), 'evet' if valid_for(f, 'x') is not None else 'yalnız doluluk'] for f, w in fields]
    return Result(f'{len(rows)} kayıt; ortalama skor {sum(o["skor"] for o in out) / n:.1f}; {sum(o["skor"] < args.min_score for o in out)} kayıt {fmt(args.min_score)} altında',
                  ['kayit', 'skor', 'sorunlar'], out,
                  {'kayit': len(rows), 'ortalama_skor': round(sum(o['skor'] for o in out) / n, 1), 'tam_kayit': sum(o['skor'] == 100 for o in out),
                   'esik_alti': sum(o['skor'] < args.min_score for o in out)},
                  tables=[('Alan özeti', ['Alan', 'Ağırlık', 'Dolu %', 'Geçerli %', 'Geçerlilik kuralı'], field_rows)])


IDENT = [('vkn', 'aynı vergi no', lambda v: re.sub(r'\D', '', v)), ('iban', 'aynı IBAN', lambda v: re.sub(r'\s', '', v).upper()),
         ('telefon', 'aynı telefon', lambda v: re.sub(r'\D', '', v)[-10:]), ('eposta', 'aynı e-posta', lambda v: v.strip().lower())]


@op('paylasilan-kimlik', title='Tedarikçi/cari kartlarında paylaşılan vergi no, IBAN, telefon ve e-posta', domain=D, product=P, tag='mükerrer cari', samples={'tedarikciler.csv': 'tedarikciler.csv'},
    args=[arg('--key', default='kod'), arg('--name', default='unvan')],
    description='Cari veya tedarikçi ana verisinde aynı vergi numarasını (mükerrer kart), aynı IBAN’ı, telefonu veya e-postayı paylaşan kayıtları gruplar. Farklı vergi numaralı firmaların aynı IBAN’ı kullanması gibi ödeme dolandırıcılığı sinyallerini yüksek risk olarak işaretler. Telefonlar son 10 haneye, IBAN boşluksuz büyük harfe normalize edilir.',
    input='Cari/tedarikçi listesi (kod, ünvan, vkn, iban, telefon, eposta)', output='sonuc.csv (paylaşım grupları)', command='python main.py --input satici_ana_verisi.xlsx --key lifnr --name name1',
    expect={'kayit': 6, 'grup': 3, 'ilgili_kayit': 5, 'yuksek_risk': 1})
def shared_ids(columns, rows, args):
    kcol, ncol = need(args.key, columns, '--key'), need(args.name, columns, '--name')
    out = []
    for col, label, norm in IDENT:
        if col not in columns:
            continue
        groups = defaultdict(list)
        for r in rows:
            v = norm(r[col]) if r[col].strip() else ''
            if len(v) >= 5:
                groups[v].append(r)
        for v, rs in groups.items():
            if len(rs) < 2:
                continue
            vkns = {re.sub(r'\D', '', r.get('vkn', '')) for r in rs} - {''}
            risk = 'YÜKSEK: farklı vergi numaralı firmalar aynı hesabı kullanıyor' if col == 'iban' and len(vkns) > 1 else \
                'mükerrer kart olabilir' if col == 'vkn' else 'orta: aynı kişi/aracı olabilir' if len(vkns) > 1 else 'mükerrer kart olabilir'
            out.append({'tur': label, 'deger': v[:4] + '…' + v[-4:] if col == 'iban' else v, 'kayit_sayisi': len(rs), 'kodlar': ', '.join(r[kcol] for r in rs),
                        'unvanlar': ' | '.join(r[ncol] for r in rs), 'risk': risk})
    out.sort(key=lambda o: (not o['risk'].startswith('YÜKSEK'), o['tur']))
    involved = {k.strip() for o in out for k in o['kodlar'].split(',')}
    return Result(f'{len(rows)} kayıt; {len(out)} paylaşım grubunda {len(involved)} kayıt; {sum(o["risk"].startswith("YÜKSEK") for o in out)} yüksek risk', list(out[0]) if out else ['tur'], out,
                  {'kayit': len(rows), 'grup': len(out), 'ilgili_kayit': len(involved), 'yuksek_risk': sum(o['risk'].startswith('YÜKSEK') for o in out)},
                  notes=['Grup şirketleri ve şubeler meşru olarak aynı telefonu/IBAN’ı paylaşabilir; yüksek riskli grupları ödeme öncesi satın alma ve finansla doğrulayın.'])


@op('hiyerarsi-duzlestir', title='Ebeveyn–çocuk hiyerarşisini seviye sütunlarına düzleştir', domain=D, product=P, tag='hiyerarşi', samples={'kategori_agaci.csv': 'kategori_agaci.csv'},
    args=[arg('--id', default='kategori_id'), arg('--parent', default='ust_id'), arg('--name', default='ad'), arg('--sep', default=' > ')],
    description='Kimlik–üst kimlik biçimindeki kategori, organizasyon, hesap planı veya ürün ağacını her kayıt için seviye numarası, kökten yola (Tüm ürünler > Elektronik > Bilgisayar) ve seviye_1…seviye_n sütunlarına düzleştirir; yaprak düğümleri işaretler, üst kaydı olmayan (yetim) ve döngüye giren kayıtları ayırır. BI araçlarında hiyerarşi ve drill-down kurmak için.',
    input='Hiyerarşi listesi (id, üst id, ad)', output='sonuc.csv (düzleştirilmiş hiyerarşi)', command='python main.py --input org.csv --id personel_no --parent yonetici_no --name ad_soyad',
    expect={'kayit': 12, 'maks_seviye': 4, 'yaprak': 4, 'yetim': 1, 'dongu': 2})
def flatten(columns, rows, args):
    icol, pcol, ncol = need(args.id, columns, '--id'), need(args.parent, columns, '--parent'), need(args.name, columns, '--name')
    nodes = {r[icol].strip(): r for r in rows if r[icol].strip()}
    has_child = {r[pcol].strip() for r in rows if r[pcol].strip()}
    paths, status = {}, {}
    for k in nodes:
        chain, cur, seen = [], k, set()
        while cur:
            if cur in seen:
                status[k] = 'döngü'
                break
            if cur not in nodes:
                status[k] = 'yetim'
                break
            seen.add(cur)
            chain.append(cur)
            cur = nodes[cur][pcol].strip()
        else:
            status[k] = 'tamam'
        paths[k] = list(reversed(chain))
    depth = max((len(p) for k, p in paths.items() if status[k] == 'tamam'), default=0)
    out = []
    for k, r in nodes.items():
        ok = status[k] == 'tamam'
        names = [nodes[x][ncol] for x in paths[k]] if ok else []
        out.append({'id': k, 'ad': r[ncol], 'seviye': len(names) if ok else '', 'yol': args.sep.join(names), 'yaprak': 'evet' if ok and k not in has_child else '',
                    **{f'seviye_{i}': names[i - 1] if i <= len(names) else '' for i in range(1, depth + 1)}, 'durum': status[k]})
    c = Counter(status.values())
    return Result(f'{len(out)} kayıt, en derin {depth} seviye; {c["yetim"]} yetim, {c["döngü"]} döngüde', list(out[0]), out,
                  {'kayit': len(out), 'maks_seviye': depth, 'yaprak': sum(o['yaprak'] == 'evet' for o in out), 'yetim': c['yetim'], 'dongu': c['döngü']},
                  notes=['Yetim ve döngüdeki kayıtların yolu boş bırakılır; hiyerarsi-kontrol bloğu bu kayıtların ayrıntısını verir.'])
