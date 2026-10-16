"""Ana veri (MDM) blokları: benzer kayıt kümeleme, ünvan ve adres standardı, golden record, ad-soyad ayırma, hiyerarşi kontrolü."""
import difflib, re
from collections import Counter, defaultdict
import tabular
import turkiye as tr
from islem import op, arg, Result, need, col_list, fold, tr_title, tr_sort_key

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
