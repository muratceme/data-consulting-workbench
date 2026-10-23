"""Veri yönetişimi blokları: iş sözlüğü kalitesi, SQL betiklerinden tablo soy ağacı, görevler ayrılığı (SoD), sınıflandırma tutarlılığı ve veri sözleşmesi doğrulaması.
Katalog aracına bağlanmaz; dışa aktarılmış listeler ve betikler üzerinde çalışır."""
import json, re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
import tabular
from islem import op, arg, Result, need, numbers, fold, fmt

D, P = 'governance', 'Erişim ve sahiplik listeleri'


def words(s):
    return re.findall(r'\w+', fold(s))


@op('is-sozlugu-kontrol', title='İş sözlüğü (business glossary) kalite kontrolü', domain=D, product=P, tag='iş sözlüğü', samples={'is_sozlugu.csv': 'is_sozlugu.csv'},
    args=[arg('--term', default='terim'), arg('--definition', default='tanim'), arg('--owner', default='sahip'), arg('--synonyms', default='esanlamlilar', help='Virgülle ayrılmış eş anlamlılar sütunu'),
          arg('--min-words', type=int, default=5)],
    description='İş sözlüğündeki terimleri kontrol eder: büyük/küçük harf ve Türkçe karakter farkıyla tekrar eden terimleri, tanımı boş, çok kısa veya terimin kendisini tekrarlayan (döngüsel) tanımları, sahibi olmayan terimleri ve eş anlamlısı başka bir terim olarak ayrıca tanımlanmış (çakışan) kayıtları listeler.',
    input='Sözlük dışa aktarımı (terim, tanım, sahip, eş anlamlılar)', output='sonuc.csv (terim × sorun)', command='python main.py --input glossary.xlsx --term name --definition description --owner steward',
    expect={'terim': 10, 'sorun': 8, 'sorunlu_terim': 6, 'tekrar': 1})
def glossary(columns, rows, args):
    tcol, dcol = need(args.term, columns, '--term'), need(args.definition, columns, '--definition')
    ocol = args.owner if args.owner in columns else None
    scol = args.synonyms if args.synonyms in columns else None
    first = {}
    terms = {fold(r[tcol]) for r in rows if r[tcol].strip()}
    out = []
    for i, r in enumerate(rows, 2):
        term, key, definition = r[tcol].strip(), fold(r[tcol]), r[dcol].strip()
        add = lambda kind, detail='': out.append({'satir': i, 'terim': term, 'sorun': kind, 'ayrinti': detail})
        if not term:
            add('terim adı boş')
            continue
        if key in first:
            add('tekrar eden terim', f'{first[key][1]} (satır {first[key][0]})')
        else:
            first[key] = (i, term)
        w = words(definition)
        if not definition:
            add('tanım yok')
        elif set(w) <= set(words(term)):
            add('döngüsel tanım', 'tanım terimi tekrar ediyor')
        elif len(w) < args.min_words:
            add('kısa tanım', f'{len(w)} kelime')
        if ocol and not r[ocol].strip():
            add('sahip yok')
        for syn in (s.strip() for s in (r[scol] if scol else '').split(',') if s.strip()):
            if fold(syn) in terms and fold(syn) != key:
                add('eş anlamlı çakışması', f'"{syn}" ayrı bir terim olarak da tanımlı')
    kinds = Counter(o['sorun'] for o in out)
    return Result(f'{len(rows)} terim; {len({o["satir"] for o in out})} terimde {len(out)} sorun', ['satir', 'terim', 'sorun', 'ayrinti'], out,
                  {'terim': len(rows), 'sorun': len(out), 'sorunlu_terim': len({o['satir'] for o in out}), 'tekrar': kinds['tekrar eden terim'], 'tanimsiz': kinds['tanım yok'], 'sahipsiz': kinds['sahip yok']},
                  tables=[('Sorun türleri', ['Sorun', 'Adet'], kinds.most_common())])


NAME = r'((?:[\w$#@]+|\[[^\]]+\]|"[^"]+"|`[^`]+`)(?:\s*\.\s*(?:[\w$#@]+|\[[^\]]+\]|"[^"]+"|`[^`]+`)){0,3})'
TARGETS = [('INSERT', re.compile(r'\bINSERT\s+(?:OVERWRITE\s+)?(?:INTO\s+|TABLE\s+)?' + NAME, re.I)),
           ('MERGE', re.compile(r'\bMERGE\s+(?:INTO\s+)?' + NAME, re.I)),
           ('CREATE AS', re.compile(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:GLOBAL\s+|LOCAL\s+)?TEMP(?:ORARY)?\s+|TRANSIENT\s+)?(?:TABLE|VIEW|MATERIALIZED\s+VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?' + NAME, re.I)),
           ('SELECT INTO', re.compile(r'\bSELECT\b[^;]*?\bINTO\s+' + NAME + r'\s+FROM\b', re.I)),
           ('UPDATE', re.compile(r'^\s*UPDATE\s+' + NAME, re.I))]
SOURCE = re.compile(r'\b(?:FROM|JOIN|USING)\s+' + NAME, re.I)
CTE = re.compile(r'(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([\w]+)\s+AS\s*\(', re.I)
NOT_TABLE = {'select', 'lateral', 'unnest', 'table', 'values', 'dual', 'only'}


def clean_name(n):
    return '.'.join(p.strip().strip('[]"`') for p in n.split('.')).lower()


def strip_sql(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'--[^\n]*', ' ', text)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    return re.sub(r'\b(EXTRACT|TRIM|SUBSTRING|POSITION|OVERLAY)\s*\([^()]*\)', 'x', text, flags=re.I)


def lineage(text):
    """[(operation, target, [sources])] for statements that write a table from other tables."""
    found = []
    for stmt in strip_sql(text).split(';'):
        for kind, rx in TARGETS:
            m = rx.search(stmt)
            if not m or (kind == 'CREATE AS' and not re.search(r'\bAS\b[\s(]*(?:WITH|SELECT)\b', stmt[m.end():], re.I)):
                continue
            target = clean_name(m.group(1))
            ctes = {c.lower() for c in CTE.findall(stmt)}
            body = stmt[m.end():] if kind != 'SELECT INTO' else stmt
            sources = []
            for s in SOURCE.findall(body):
                name = clean_name(s)
                if name not in ctes and name != target and name not in NOT_TABLE and not name.startswith('@') and name not in sources:
                    sources.append(name)
            if sources:
                found.append((kind, target, sources))
            break
    return found


@op('sql-soy-agaci', title='SQL betiklerinden tablo soy ağacı (lineage) ve etki analizi', domain=D, product=P, tag='lineage', kind='file',
    samples={f'sql/{n}': f'sql/{n}' for n in ('01_staging.sql', '02_mart.sql', '03_bakim.sql')}, sample_input='sql',
    args=[arg('--focus', help='Bu tablonun yukarı (kaynak) ve aşağı (etkilenen) akışını listele')],
    description='Bir klasördeki .sql dosyalarında INSERT…SELECT, CREATE TABLE/VIEW AS, MERGE…USING, SELECT…INTO ve UPDATE…FROM ifadelerinden tablo düzeyinde soy ağacını çıkarır; CTE adlarını kaynak saymaz. Kaynak→hedef kenar listesini ve Mermaid diyagramını üretir, --focus ile bir tablo değişirse etkilenecek tüm aşağı akış tablolarını gösterir.',
    input='.sql dosyaları veya klasörü', output='sonuc.csv (kaynak → hedef) + soy_agaci.mmd', command='python main.py --input etl\\sql --focus stg.musteri',
    test_args=['--focus', 'stg.musteri'], expect={'kenar': 6, 'tablo': 7, 'kok_kaynak': 3, 'son_tablo': 2, 'asagi_akis': 2, 'yukari_akis': 1})
def soy(args):
    p = Path(args.input)
    files = sorted(p.rglob('*.sql')) if p.is_dir() else [p]
    edges = {}
    for f in files:
        for kind, target, sources in lineage(f.read_text(encoding='utf-8-sig', errors='replace')):
            for s in sources:
                edges.setdefault((s, target), (f.relative_to(p).as_posix() if p.is_dir() else f.name, kind))
    if not edges:
        raise ValueError('Tablo yazan (INSERT/CREATE AS/MERGE/SELECT INTO) ifade bulunamadı.')
    tables = sorted({t for e in edges for t in e})
    srcs, tgts = {s for s, _ in edges}, {t for _, t in edges}
    down, up = defaultdict(set), defaultdict(set)
    for s, t in edges:
        down[s].add(t)
        up[t].add(s)

    def reach(start, graph):
        seen, stack = set(), [start]
        while stack:
            for n in graph[stack.pop()]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        return seen
    ids = {t: f'n{i}' for i, t in enumerate(tables)}
    mmd = ['flowchart LR'] + [f'    {ids[t]}["{t}"]' for t in tables] + [f'    {ids[s]} --> {ids[t]}' for s, t in sorted(edges)]
    rows = [{'kaynak': s, 'hedef': t, 'islem': k, 'dosya': f} for (s, t), (f, k) in sorted(edges.items())]
    summary = {'dosya': len(files), 'kenar': len(edges), 'tablo': len(tables), 'kok_kaynak': len(srcs - tgts), 'son_tablo': len(tgts - srcs)}
    tables_out = [('Kök kaynaklar', ['Tablo'], [[t] for t in sorted(srcs - tgts)]), ('Son tablolar', ['Tablo'], [[t] for t in sorted(tgts - srcs)])]
    if args.focus:
        f = clean_name(args.focus)
        if f not in ids:
            raise ValueError(f'--focus: "{args.focus}" soy ağacında yok.')
        d, u = reach(f, down), reach(f, up)
        summary.update(asagi_akis=len(d), yukari_akis=len(u))
        tables_out.insert(0, (f'{f} etki analizi', ['Yön', 'Tablo'], [['aşağı akış (etkilenir)', t] for t in sorted(d)] + [['yukarı akış (kaynak)', t] for t in sorted(u)]))
    return Result(f'{len(files)} dosya; {len(tables)} tablo arasında {len(edges)} bağlantı', ['kaynak', 'hedef', 'islem', 'dosya'], rows, summary,
                  tables=tables_out, files={'soy_agaci.mmd': '\n'.join(mmd) + '\n'},
                  notes=['Dinamik SQL, saklı yordam çağrıları ve tablo adını değişkenden alan ifadeler görünmez; bu ifadeler için betiği elle kontrol edin.'])


@op('gorev-ayriligi', title='Görevler ayrılığı (SoD) çatışmalarını bul', domain=D, product=P, tag='SoD', samples={'kullanici_rolleri.csv': 'kullanici_rolleri.csv', 'sod_kurallari.csv': 'sod_kurallari.csv'},
    args=[arg('--rules', default='@ornek/sod_kurallari.csv', help='Çatışan rol çiftleri (rol_a, rol_b, risk, aciklama)'), arg('--user', default='kullanici'), arg('--role', default='rol')],
    description='Kullanıcı–rol atama listesini, birlikte bulunmaması gereken rol çiftleri kuralıyla karşılaştırır (ör. finansal raporu hazırlayan kişinin kaynak veriyi değiştirebilmesi). Her çatışmayı risk düzeyi ve gerekçesiyle, kullanıcı bazında özetle birlikte listeler; denetim ve yetki gözden geçirmesi için.',
    input='Kullanıcı–rol listesi + SoD kural listesi', output='sonuc.csv (çatışmalar)', command='python main.py --input ad_grup_uyelikleri.csv --user sAMAccountName --role group --rules sod.csv',
    expect={'kullanici': 7, 'catisma': 3, 'catismali_kullanici': 2, 'yuksek_risk': 2})
def sod(columns, rows, args):
    ucol, rcol = need(args.user, columns, '--user'), need(args.role, columns, '--role')
    rcols, rules = tabular.read(args.rules)
    for c in ('rol_a', 'rol_b'):
        need(c, rcols, 'kural dosyası')
    roles = defaultdict(set)
    for r in rows:
        if r[ucol].strip() and r[rcol].strip():
            roles[r[ucol].strip()].add(r[rcol].strip().upper())
    out = []
    for u, rs in sorted(roles.items()):
        for rule in rules:
            a, b = rule['rol_a'].strip().upper(), rule['rol_b'].strip().upper()
            if a in rs and b in rs:
                out.append({'kullanici': u, 'rol_a': a, 'rol_b': b, 'risk': rule.get('risk', '').strip(), 'aciklama': rule.get('aciklama', '').strip()})
    out.sort(key=lambda o: (o['risk'].lower() not in ('yüksek', 'yuksek', 'high', 'kritik'), o['kullanici']))
    high = sum(o['risk'].lower() in ('yüksek', 'yuksek', 'high', 'kritik') for o in out)
    return Result(f'{len(roles)} kullanıcı, {len(rules)} kural; {len({o["kullanici"] for o in out})} kullanıcıda {len(out)} çatışma ({high} yüksek risk)',
                  ['kullanici', 'rol_a', 'rol_b', 'risk', 'aciklama'], out,
                  {'kullanici': len(roles), 'kural': len(rules), 'catisma': len(out), 'catismali_kullanici': len({o['kullanici'] for o in out}), 'yuksek_risk': high},
                  notes=['Onaylı istisnalar için çatışmayı telafi edici kontrolle (ör. dört göz onayı) birlikte kayda alın.'])


PII_NAME = re.compile(r'(tckn|tc_?kimlik|kimlik_?no|e_?posta|email|e_?mail|telefon|gsm|cep|iban|dogum|doğum|adres|ad_?soyad|pasaport)', re.I)


@op('siniflandirma-tutarliligi', title='Aynı sütunun tablolar arası sınıflandırma tutarlılığı', domain=D, product=P, tag='sınıflandırma', samples={'kolon_katalogu.csv': 'kolon_katalogu.csv'},
    args=[arg('--levels', default='genel,iç,kişisel,gizli,özel', help='Düşükten yükseğe sınıflandırma düzeyleri'), arg('--table', default='tablo'), arg('--column', default='sutun'), arg('--class', dest='cls', default='siniflandirma')],
    description='Katalogdaki sütun sınıflandırmalarını aynı (normalize edilmiş) sütun adına göre gruplar: aynı veri farklı tablolarda farklı düzeyde etiketlenmişse en yüksek düzeyi önerir; adı kişisel veri çağrıştıran (tckn, e-posta, telefon, iban, doğum tarihi…) ama sınıflandırılmamış veya "genel" etiketli sütunları işaretler.',
    input='Sütun kataloğu (tablo, sütun, sınıflandırma)', output='sonuc.csv (düzeltme önerileri)', command='python main.py --input purview_columns.csv --table asset --column column_name --class classification',
    expect={'sutun': 14, 'tutarsiz_grup': 4, 'oneri': 5, 'siniflandirilmamis': 2})
def classify(columns, rows, args):
    t, c, k = need(args.table, columns, '--table'), need(args.column, columns, '--column'), need(args.cls, columns, '--class')
    levels_raw = [x.strip() for x in args.levels.split(',') if x.strip()]
    levels = [fold(x) for x in levels_raw]
    rank = lambda v: levels.index(fold(v)) if fold(v) in levels else -1
    floor = levels.index('kisisel') if 'kisisel' in levels else None
    groups = defaultdict(list)
    for r in rows:
        groups[re.sub(r'[^a-z0-9]', '', fold(r[c]))].append(r)
    out, inconsistent = [], 0
    unknown = sorted({r[k].strip() for r in rows if r[k].strip() and rank(r[k]) < 0})
    for key, rs in sorted(groups.items()):
        vals = {fold(r[k]) for r in rs}
        best = max((r[k].strip() for r in rs if r[k].strip()), key=rank, default='')
        if len(vals) > 1:
            inconsistent += 1
        if PII_NAME.search(rs[0][c]) and floor is not None and rank(best) < floor:
            best = levels_raw[floor]
        for r in rs:
            if fold(r[k]) != fold(best):
                why = 'gruptaki en yüksek düzey' if len(vals) > 1 else 'kişisel veri adlı sütun'
                out.append({'tablo': r[t], 'sutun': r[c], 'mevcut': r[k].strip() or '(boş)', 'onerilen': best, 'neden': why})
    return Result(f'{len(rows)} sütun; {inconsistent} tutarsız grup, {len(out)} düzeltme önerisi', ['tablo', 'sutun', 'mevcut', 'onerilen', 'neden'], out,
                  {'sutun': len(rows), 'tutarsiz_grup': inconsistent, 'oneri': len(out), 'siniflandirilmamis': sum(not r[k].strip() for r in rows), 'bilinmeyen_duzey': unknown},
                  notes=['Öneri, gruptaki en yüksek düzeydir; kişisel veri adlı sütunlar en az "kişisel" önerilir. Sütun adı eşleşmesi aynı veriyi garanti etmez, öneriyi veri sahibiyle doğrulayın.'])


def check_type(v, kind):
    if kind in ('integer', 'int'):
        return re.fullmatch(r'[+-]?\d+', v.strip()) is not None
    if kind in ('number', 'decimal', 'float'):
        return tabular.parse_number(v) is not None
    if kind == 'date':
        return tabular.parse_date(v) is not None
    if kind in ('boolean', 'bool'):
        return v.strip().lower() in ('true', 'false', '1', '0', 'evet', 'hayır', 'hayir', 'e', 'h')
    return True


@op('veri-sozlesmesi', title='Veri setini veri sözleşmesine (data contract) göre doğrula', domain=D, product=P, tag='data contract',
    samples={'sozlesme_verisi.csv': 'sozlesme_verisi.csv', 'veri_sozlesmesi.json': 'veri_sozlesmesi.json'},
    args=[arg('--contract', default='@ornek/veri_sozlesmesi.json', help='JSON sözleşme: columns[name,type,required,unique,allowed,pattern,min,max], min_rows'),
          arg('--strict', action='store_true', help='İhlal varsa hata koduyla çık (CI için)')],
    description='Üretici ekiple anlaşılan veri sözleşmesini (sütunlar, tipler, zorunluluk, tekillik, izin verilen değerler, desen, alt/üst sınır, en az satır) bir teslimat dosyasına uygular; eksik/fazla sütunları ve kural bazında ihlal sayısını, örnek satır numaralarıyla raporlar. Değerler rapora yazılmaz; --strict ile pipeline’da kapı (gate) olarak kullanılır.',
    input='CSV/XLSX + JSON sözleşme', output='sonuc.csv (kural bazında ihlaller)', command='python main.py --input teslimat.csv --contract siparis_contract.json --strict',
    expect={'ihlal_kural': 8, 'ihlal_satir': 7, 'eksik_sutun': 0, 'fazla_sutun': 1})
def contract(columns, rows, args):
    try:
        spec = json.loads(Path(args.contract).read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'Sözleşme JSON okunamadı: {exc}') from None
    cols = spec.get('columns') or []
    if not cols:
        raise ValueError('Sözleşmede "columns" listesi yok.')
    out, bad_rows = [], set()

    def add(col, rule, hits, detail=''):
        if hits:
            bad_rows.update(hits)
            out.append({'sutun': col, 'kural': rule, 'ihlal': len(hits), 'ornek_satirlar': ', '.join(map(str, hits[:5])), 'ayrinti': detail})
    names = [c['name'] for c in cols]
    missing = [n for n in names if n not in columns]
    for n in missing:
        out.append({'sutun': n, 'kural': 'sütun var', 'ihlal': 1, 'ornek_satirlar': '', 'ayrinti': 'sözleşmedeki sütun dosyada yok'})
    extra = [c for c in columns if c not in names]
    for n in extra:
        out.append({'sutun': n, 'kural': 'sözleşme dışı sütun', 'ihlal': 1, 'ornek_satirlar': '', 'ayrinti': 'sözleşmeye eklenmeli veya kaldırılmalı'})
    for c in cols:
        n = c['name']
        if n not in columns:
            continue
        vals = [(i, r[n]) for i, r in enumerate(rows, 2)]
        filled = [(i, v) for i, v in vals if v.strip()]
        if c.get('required'):
            add(n, 'zorunlu', [i for i, v in vals if not v.strip()])
        kind = c.get('type', 'string').lower()
        typed = [(i, v) for i, v in filled if check_type(v, kind)]
        add(n, f'tip: {kind}', [i for i, v in filled if not check_type(v, kind)])
        if c.get('unique'):
            seen = set()
            dup = []
            for i, v in filled:
                dup += [i] if v.strip() in seen else []
                seen.add(v.strip())
            add(n, 'tekil', dup)
        if 'allowed' in c:
            ok = set(map(str, c['allowed']))
            add(n, 'izin verilen değerler', [i for i, v in filled if v.strip() not in ok], ', '.join(sorted(ok)))
        if c.get('pattern'):
            rx = re.compile(c['pattern'])
            add(n, f'desen {c["pattern"]}', [i for i, v in filled if not rx.search(v.strip())])
        if kind in ('integer', 'int', 'number', 'decimal', 'float') and ('min' in c or 'max' in c):
            nums = [(i, tabular.parse_number(v)) for i, v in typed]
            add(n, f'aralık [{c.get("min", "")}, {c.get("max", "")}]', [i for i, x in nums if x is not None and (('min' in c and x < c['min']) or ('max' in c and x > c['max']))])
    if len(rows) < spec.get('min_rows', 0):
        out.append({'sutun': '*', 'kural': f'en az {spec["min_rows"]} satır', 'ihlal': 1, 'ornek_satirlar': '', 'ayrinti': f'{len(rows)} satır geldi'})
    violations = [o for o in out if o['kural'] != 'sözleşme dışı sütun']
    return Result(f'{spec.get("name", "sözleşme")}: {len(violations)} kural ihlali, {len(bad_rows)} satır etkilendi', ['sutun', 'kural', 'ihlal', 'ornek_satirlar', 'ayrinti'], out,
                  {'satir_sayisi': len(rows), 'ihlal_kural': len(violations), 'ihlal_satir': len(bad_rows), 'eksik_sutun': len(missing), 'fazla_sutun': len(extra)},
                  code=2 if args.strict and violations else 0)


@op('erisim-talep-denetimi', title='Veri erişim taleplerinde kendi onayı, gecikme ve süresi dolan erişim', domain=D, product=P, tag='erişim talebi', samples={'erisim_talepleri.csv': 'erisim_talepleri.csv'},
    args=[arg('--as-of', default='2026-10-16'), arg('--sla', type=int, default=3, help='Onay için gün')],
    description='Erişim talep kayıtlarını denetler: talep edenin kendi talebini onayladığı, onay kaydı olmadan aktif olan, onayı SLA’dan geç verilen ve bitiş tarihi geçtiği hâlde hâlâ aktif olan (geçici) erişimleri listeler. İç denetim ve ISO 27001 / SOX erişim kontrolü kanıtı için.',
    input='Talep kayıtları (talep eden, onaylayan, kaynak, talep/onay/bitiş tarihi, durum)', output='sonuc.csv (bulgulu talepler)', command='python main.py --input servicenow_access.csv --sla 2',
    expect={'talep': 6, 'sorunlu': 4, 'kendi_onayi': 1, 'onaysiz': 1, 'gec_onay': 1, 'suresi_dolmus_aktif': 1})
def access_requests(columns, rows, args):
    for c in ('talep_no', 'talep_eden', 'onaylayan', 'talep_tarihi', 'onay_tarihi', 'durum'):
        need(c, columns, 'talep kayıtları')
    as_of = tabular.parse_date(args.as_of)[0].date()
    day = lambda v: (lambda d: d[0].date() if d else None)(tabular.parse_date(v or ''))
    out, kinds = [], Counter()
    for r in rows:
        active = fold(r['durum']) in ('aktif', 'active', 'acik')
        req, ok, end = day(r['talep_tarihi']), day(r['onay_tarihi']), day(r.get('bitis_tarihi', ''))
        issues = []
        if r['onaylayan'].strip() and fold(r['onaylayan']) == fold(r['talep_eden']):
            issues.append(('kendi onayı', 'talep eden kendi talebini onaylamış'))
        if active and not (r['onaylayan'].strip() and ok):
            issues.append(('onaysız', 'onay kaydı olmadan aktif'))
        if req and ok and (ok - req).days > args.sla:
            issues.append(('geç onay', f'onay {(ok - req).days} gün sürdü'))
        if active and end and end < as_of:
            issues.append(('süresi dolmuş aktif', f'bitiş {end} geçti, erişim hâlâ aktif'))
        kinds.update(k for k, _ in issues)
        if issues:
            out.append({'talep_no': r['talep_no'], 'talep_eden': r['talep_eden'], 'onaylayan': r['onaylayan'], 'kaynak': r.get('kaynak', ''), 'bulgu': '; '.join(m for _, m in issues)})
    return Result(f'{len(rows)} talep; {len(out)} talepte bulgu', ['talep_no', 'talep_eden', 'onaylayan', 'kaynak', 'bulgu'], out,
                  {'talep': len(rows), 'sorunlu': len(out), 'kendi_onayi': kinds['kendi onayı'], 'onaysiz': kinds['onaysız'], 'gec_onay': kinds['geç onay'],
                   'suresi_dolmus_aktif': kinds['süresi dolmuş aktif']})


@op('rapor-kullanim-envanteri', title='BI rapor envanterinde kullanılmayan, sahipsiz ve boşa yenilenen raporlar', domain=D, product=P, tag='rapor envanteri',
    samples={'bi_raporlari.csv': 'bi_raporlari.csv', 'ik_aktif_personel.csv': 'ik_aktif_personel.csv'},
    args=[arg('--hr', default='@ornek/ik_aktif_personel.csv', help='Aktif kullanıcı listesi (kullanici, durum)'), arg('--as-of', default='2026-10-16'),
          arg('--days', type=int, default=90, help='Bu kadar gündür açılmayan rapor emekliye ayrılabilir'), arg('--min-views', type=int, default=5)],
    description='Power BI, Qlik veya Tableau rapor kullanım dışa aktarımında uzun süredir açılmayan (emekliye ayrılabilir), az kullanılan, sahibi boş veya ayrılmış çalışan olan ve kimse bakmadığı hâlde düzenli yenilenen (boşa kapasite harcayan) raporları bulur. Rapor yığılmasını azaltma ve lisans/kapasite tasarrufu için.',
    input='Rapor kullanım listesi + aktif personel listesi', output='sonuc.csv (rapor bazında öneri)', command='python main.py --input pbi_activity.csv --days 120 --hr aktif_kullanicilar.csv',
    expect={'rapor': 6, 'emekliye_ayir': 3, 'sahipsiz': 2, 'bosa_yenilenen': 2, 'az_kullanilan': 1, 'sorunlu': 5})
def report_usage(columns, rows, args):
    for c in ('rapor', 'sahip', 'son_goruntulenme'):
        need(c, columns, 'rapor listesi')
    hcols, hrows = tabular.read(args.hr)
    need('kullanici', hcols, '--hr')
    active = {fold(r['kullanici']) for r in hrows if fold(r.get('durum', 'aktif')) == 'aktif'}
    as_of = tabular.parse_date(args.as_of)[0]
    views = numbers(rows, 'goruntulenme_90g') if 'goruntulenme_90g' in columns else [None] * len(rows)
    out, kinds = [], Counter()
    for r, v in zip(rows, views):
        seen, refreshed = tabular.parse_date(r['son_goruntulenme']), tabular.parse_date(r.get('son_yenileme', '') or '')
        idle = (as_of - seen[0]).days if seen else None
        tags = []
        if idle is None or idle > args.days:
            tags.append('emekliye ayır')
        elif v is not None and v <= args.min_views:
            tags.append('az kullanılan')
        if not r['sahip'].strip() or fold(r['sahip']) not in active:
            tags.append('sahipsiz')
        if 'emekliye ayır' in tags and refreshed and (as_of - refreshed[0]).days <= 7:
            tags.append('boşa yenileniyor')
        kinds.update(tags)
        out.append({'rapor': r['rapor'], 'calisma_alani': r.get('calisma_alani', ''), 'sahip': r['sahip'] or '-', 'acilmayan_gun': idle if idle is not None else 'hiç açılmadı',
                    'goruntulenme_90g': fmt(v), 'oneri': ', '.join(tags)})
    out.sort(key=lambda o: (not o['oneri'], o['rapor']))
    return Result(f'{len(out)} rapor; {kinds["emekliye ayır"]} emekliye ayrılabilir, {kinds["sahipsiz"]} sahipsiz, {kinds["boşa yenileniyor"]} boşa yenileniyor', list(out[0]), out,
                  {'rapor': len(out), 'emekliye_ayir': kinds['emekliye ayır'], 'sahipsiz': kinds['sahipsiz'], 'bosa_yenilenen': kinds['boşa yenileniyor'],
                   'az_kullanilan': kinds['az kullanılan'], 'sorunlu': sum(bool(o['oneri']) for o in out)},
                  notes=['Emekliye ayırmadan önce rapor sahibine haber verin ve raporu arşivleyin; dönemsel (yıl sonu) raporlar uzun süre açılmayabilir.'])


@op('siniflandirma-yayilimi', title='Sınıflandırmayı soy ağacı boyunca yay; aşağı akışta düşük sınıflı tabloları bul', domain=D, product=P, tag='sınıf yayılımı',
    samples={'soy_kenarlari.csv': 'soy_kenarlari.csv', 'tablo_siniflari.csv': 'tablo_siniflari.csv'},
    args=[arg('--source', default='kaynak'), arg('--target', default='hedef'), arg('--classes', default='@ornek/tablo_siniflari.csv', help='Tablo sınıfları (tablo, sinif)'),
          arg('--levels', default='genel,iç,gizli,kişisel', help='Düşükten yükseğe sınıf düzeyleri'),
          arg('--exempt', default='', help='Bilinçli olarak sınıfı düşürülen tablolar (toplulaştırma, anonimleştirme); virgülle')],
    description='Tablo düzeyindeki soy ağacı kenarlarını (kaynak → hedef) ve beyan edilen sınıfları okur; her tablonun yukarı akıştan devraldığı en yüksek sınıfı hesaplar. Beyan edilen sınıfı devraldığından düşük olan tabloları (ör. kişisel veriden beslenen ama “iç” etiketli mart), sınıfsız tabloları ve sınıfın hangi yukarı akış tablosundan geldiğini listeler. Anonimleştirme noktaları --exempt ile bariyer yapılır.',
    input='Soy ağacı kenarları (kaynak, hedef) + tablo sınıfları', output='sonuc.csv (tablo bazında beyan / devralınan sınıf)',
    command='python main.py --input lineage_edges.csv --classes siniflar.csv --exempt mart.musteri_ozet',
    expect={'tablo': 10, 'dusuk_sinif': 4, 'siniflandirilmamis': 1, 'uygun': 5})
def class_propagation(columns, rows, args):
    s, t = need(args.source, columns, '--source'), need(args.target, columns, '--target')
    ccols, crows = tabular.read(args.classes)
    if len(ccols) < 2:
        raise ValueError('--classes dosyası en az iki sütun (tablo, sinif) içermeli.')
    levels_raw = [x.strip() for x in args.levels.split(',') if x.strip()]
    levels = [fold(x) for x in levels_raw]
    declared = {}
    for r in crows:
        name, cls = r[ccols[0]].strip().lower(), fold(r[ccols[1]])
        if name and cls:
            if cls not in levels:
                raise ValueError(f'"{r[ccols[1]]}" sınıfı --levels içinde yok ({args.levels}).')
            declared[name] = levels.index(cls)
    exempt = {x.strip().lower() for x in args.exempt.split(',') if x.strip()}
    edges = [(r[s].strip().lower(), r[t].strip().lower()) for r in rows if r[s].strip() and r[t].strip()]
    nodes = list(dict.fromkeys([n for e in edges for n in e] + list(declared)))
    eff = {n: declared.get(n, -1) for n in nodes}
    origin = {n: n if n in declared else '' for n in nodes}
    for _ in range(len(nodes)):  # relaxation also terminates on cycles
        changed = False
        for a, b in edges:
            if b not in exempt and eff[a] > eff[b]:
                eff[b], origin[b], changed = eff[a], origin[a], True
        if not changed:
            break
    parents = defaultdict(set)
    for a, b in edges:
        parents[b].add(a)
    out = []
    for n in nodes:
        d, e = declared.get(n, -1), eff[n]
        if d < 0:
            status = 'sınıfsız' + (f' (öneri: {levels_raw[e]})' if e >= 0 else '')
        elif d < e:
            status = 'düşük sınıf'
        else:
            status = 'uygun' + (' (bariyer)' if n in exempt else '')
        out.append({'tablo': n, 'beyan': levels_raw[d] if d >= 0 else '', 'devralinan': levels_raw[e] if e >= 0 else '',
                    'sinif_kaynagi': origin[n] if origin[n] != n else '', 'ust_tablo': len(parents[n]), 'durum': status})
    order = {'düşük sınıf': 0, 'sınıfsız': 1}
    out.sort(key=lambda o: (order.get(o['durum'].split(' (')[0], 2), o['tablo']))
    low = sum(o['durum'] == 'düşük sınıf' for o in out)
    unc = sum(o['durum'].startswith('sınıfsız') for o in out)
    return Result(f'{len(nodes)} tablo; {low} tablo devraldığı sınıftan düşük etiketli, {unc} tablo sınıfsız', list(out[0]), out,
                  {'tablo': len(nodes), 'dusuk_sinif': low, 'siniflandirilmamis': unc, 'uygun': len(nodes) - low - unc},
                  notes=['Devralınan sınıf, yukarı akıştaki tabloların en yükseğidir. Toplulaştırma veya anonimleştirme ile sınıfı bilinçli düşürülen tabloları --exempt ile belirtin; bu tablolar kendi beyanını aşağı aktarır.',
                         'sinif_kaynagi, en yüksek sınıfın geldiği en yakın beyanlı tablodur.'])


PRIVILEGED = 'ALL,OWNER,OWNERSHIP,ADMIN,DELETE,DROP,ALTER,GRANT,TRUNCATE,CONTROL,UPDATE,SYSADMIN,DB_OWNER'


@op('yetki-degisim-denetimi', title='İki yetki listesi arasındaki değişiklikleri ve yeni ayrıcalıklı yetkileri bul', domain=D, product=P, tag='yetki farkı',
    samples={'yetki_simdi.csv': 'yetki_simdi.csv', 'yetki_onceki.csv': 'yetki_onceki.csv'},
    args=[arg('--old', default='@ornek/yetki_onceki.csv', help='Önceki dönemin yetki listesi'), arg('--user', default='kullanici'), arg('--object', default='nesne'),
          arg('--privilege', default='yetki'), arg('--privileged', default=PRIVILEGED, help='Ayrıcalıklı sayılan yetki sözcükleri (virgülle)')],
    description='Veritabanı, ambar veya uygulama yetki dışa aktarımının iki anlık görüntüsünü (ör. geçen ay ↔ bu ay) kullanıcı + nesne + yetki üçlüsüyle karşılaştırır; eklenen ve kaldırılan yetkileri, yeni gelen ve tamamen çıkan kullanıcıları, yeni verilen ayrıcalıklı yetkileri (ALL, OWNER, DELETE, DROP, GRANT…) listeler. Dönemsel erişim gözden geçirmesi ve denetim kanıtı için.',
    input='Güncel yetki listesi + önceki yetki listesi (kullanici, nesne, yetki)', output='sonuc.csv (değişiklikler)', command='python main.py --input grants_2026_10.csv --old grants_2026_09.csv',
    expect={'onceki': 8, 'simdi': 11, 'eklenen': 4, 'kaldirilan': 1, 'ayricalikli_eklenen': 2, 'yeni_kullanici': 1, 'cikan_kullanici': 1})
def grant_diff(columns, rows, args):
    u, o, p = need(args.user, columns, '--user'), need(args.object, columns, '--object'), need(args.privilege, columns, '--privilege')
    ocols, orows = tabular.read(args.old)
    for c, flag in ((u, '--user'), (o, '--object'), (p, '--privilege')):
        need(c, ocols, f'--old {flag}')
    priv = {x.strip().upper() for x in args.privileged.split(',') if x.strip()}

    def key(r):
        return (r[u].strip().lower(), r[o].strip().lower(), re.sub(r'\s+', ' ', r[p].strip().upper()))

    def grants(rs):
        return {key(r): (r[u].strip(), r[o].strip(), r[p].strip()) for r in rs if r[u].strip() and r[o].strip()}

    new, old = grants(rows), grants(orows)
    new_users, old_users = {k[0] for k in new}, {k[0] for k in old}
    out = []
    for kind, keys, src, users in (('eklendi', new.keys() - old.keys(), new, old_users), ('kaldırıldı', old.keys() - new.keys(), old, new_users)):
        for k in keys:
            user, obj, pr = src[k]
            privileged = bool(set(re.findall(r'[A-Z_]+', k[2])) & priv)
            note = ('yeni kullanıcı' if kind == 'eklendi' else 'kullanıcının tüm yetkileri kalktı') if k[0] not in users else ''
            out.append({'degisim': kind, 'kullanici': user, 'nesne': obj, 'yetki': pr, 'ayricalikli': 'evet' if privileged else '', 'not': note})
    out.sort(key=lambda r: (r['degisim'] != 'eklendi', r['ayricalikli'] != 'evet', r['kullanici'].lower(), r['nesne'].lower(), r['yetki']))
    added = [r for r in out if r['degisim'] == 'eklendi']
    return Result(f'{len(added)} yetki eklendi ({sum(r["ayricalikli"] == "evet" for r in added)} ayrıcalıklı), {len(out) - len(added)} yetki kaldırıldı',
                  ['degisim', 'kullanici', 'nesne', 'yetki', 'ayricalikli', 'not'], out,
                  {'onceki': len(old), 'simdi': len(new), 'eklenen': len(added), 'kaldirilan': len(out) - len(added),
                   'ayricalikli_eklenen': sum(r['ayricalikli'] == 'evet' for r in added), 'yeni_kullanici': len(new_users - old_users), 'cikan_kullanici': len(old_users - new_users)},
                  notes=['Yetki adları büyük/küçük harf ve boşluk farkı gözetilmeden karşılaştırılır. Rol üyelikleriyle gelen dolaylı yetkiler, dışa aktarım rolleri açmıyorsa görünmez.',
                         'Her ayrıcalıklı ekleme için onaylı talep kaydı (erisim-talep-denetimi bloğu) bulunmalıdır.'])


@op('istisna-kaydi-kontrol', title='Veri politikası istisna kaydında süresi dolan, onaysız ve süresiz istisnalar', domain=D, product=P, tag='istisna kaydı',
    samples={'politika_istisnalari.csv': 'politika_istisnalari.csv'},
    args=[arg('--id', default='istisna_no'), arg('--requester', default='talep_eden'), arg('--approver', default='onaylayan'), arg('--start', default='baslangic'),
          arg('--end', default='bitis'), arg('--status', default='durum'), arg('--active', default='aktif,açık,acik,onaylandı,onaylandi', help='Aktif sayılan durumlar'),
          arg('--as-of', default='2026-10-16'), arg('--warn-days', type=int, default=30), arg('--max-days', type=int, default=365)],
    description='Maskeleme, saklama süresi, erişim veya kalite politikalarına verilen istisnaların kaydını denetler: süresi dolduğu hâlde aktif görünen, önümüzdeki N günde dolacak, onaylayanı boş, talep edenin kendisinin onayladığı, bitiş tarihi olmayan ve izin verilen en uzun süreyi aşan istisnaları listeler. Denetim öncesi istisna temizliği için.',
    input='İstisna kaydı (no, politika, talep eden, onaylayan, başlangıç, bitiş, durum)', output='sonuc.csv (bulgular)', command='python main.py --input exceptions.xlsx --as-of 2026-12-31 --warn-days 45',
    expect={'istisna': 8, 'aktif': 7, 'sorunlu': 6, 'suresi_gecmis': 1, 'yaklasan': 1, 'onaysiz': 1, 'kendi_onayi': 1, 'suresiz': 1, 'uzun': 1})
def exceptions(columns, rows, args):
    ic, rq, ap = need(args.id, columns, '--id'), need(args.requester, columns, '--requester'), need(args.approver, columns, '--approver')
    sc, ec, st = need(args.start, columns, '--start'), need(args.end, columns, '--end'), need(args.status, columns, '--status')
    active = {fold(x).strip() for x in args.active.split(',') if x.strip()}
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    if not as_of:
        raise ValueError(f'--as-of "{args.as_of}" tarih olarak okunamadı.')
    out, n_active = [], 0
    kinds = Counter()
    for r in rows:
        if fold(r[st]).strip() not in active:
            continue
        n_active += 1
        s, e = tabular.parse_date(r[sc]), tabular.parse_date(r[ec]) if r[ec].strip() else None
        issues = []
        if e is None:
            issues.append(('süresiz', 'bitiş tarihi yok'))
        elif e[0] < as_of:
            issues.append(('süresi geçti', f'{(as_of - e[0]).days} gün önce doldu, hâlâ aktif'))
        elif (e[0] - as_of).days <= args.warn_days:
            issues.append(('yaklaşıyor', f'{(e[0] - as_of).days} gün içinde doluyor'))
        if s and e and (e[0] - s[0]).days > args.max_days:
            issues.append(('çok uzun', f'{(e[0] - s[0]).days} gün (en fazla {args.max_days})'))
        if not r[ap].strip():
            issues.append(('onaysız', 'onaylayan boş'))
        elif fold(r[ap]).strip() == fold(r[rq]).strip():
            issues.append(('kendi onayı', 'talep eden kendi istisnasını onaylamış'))
        for kind, detail in issues:
            kinds[kind] += 1
            out.append({'istisna': r[ic].strip(), 'bulgu': kind, 'detay': detail, 'talep_eden': r[rq].strip(), 'onaylayan': r[ap].strip(), 'bitis': r[ec].strip()})
    order = {'süresi geçti': 0, 'onaysız': 1, 'kendi onayı': 2, 'süresiz': 3, 'çok uzun': 4, 'yaklaşıyor': 5}
    out.sort(key=lambda o: (order[o['bulgu']], o['istisna']))
    return Result(f'{len(rows)} istisna ({n_active} aktif); {len({o["istisna"] for o in out})} istisnada bulgu', ['istisna', 'bulgu', 'detay', 'talep_eden', 'onaylayan', 'bitis'], out,
                  {'istisna': len(rows), 'aktif': n_active, 'sorunlu': len({o['istisna'] for o in out}), 'suresi_gecmis': kinds['süresi geçti'], 'yaklasan': kinds['yaklaşıyor'],
                   'onaysiz': kinds['onaysız'], 'kendi_onayi': kinds['kendi onayı'], 'suresiz': kinds['süresiz'], 'uzun': kinds['çok uzun']},
                  notes=['Yalnızca aktif durumdaki istisnalar denetlenir. Süresi geçmiş istisnaya dayanan erişim veya maskeleme muafiyetini kapatın ya da yeniden onaya gönderin.'])


@op('kullanilmayan-yetkiler', title='Verilmiş ama kullanılmayan yetkileri erişim günlüğüyle bul (en az yetki)', domain=D, product=P, tag='en az yetki',
    samples={'yetki_simdi.csv': 'yetki_simdi.csv', 'nesne_erisim.csv': 'nesne_erisim.csv'},
    args=[arg('--access', default='@ornek/nesne_erisim.csv', help='Erişim günlüğü (kullanici, nesne, zaman)'), arg('--user', default='kullanici'), arg('--object', default='nesne'),
          arg('--privilege', default='yetki'), arg('--as-of', default='2026-10-16'), arg('--days', type=int, default=90)],
    description='Yetki listesindeki her kullanıcı + nesne çiftini erişim/sorgu günlüğüyle (Snowflake ACCESS_HISTORY, BigQuery INFORMATION_SCHEMA.JOBS, audit log) karşılaştırır; son N günde hiç kullanılmayan yetkileri, hiç erişimi olmayan kullanıcıları ve yalnızca okuma yapıldığı hâlde yazma/sahip yetkisi taşıyan çiftleri listeler. Dönemsel erişim gözden geçirmesinde geri alınacak yetkileri seçmek için.',
    input='Yetki listesi (kullanıcı, nesne, yetki) + erişim günlüğü (kullanıcı, nesne, zaman, isteğe bağlı işlem)', output='sonuc.csv (geri alma adayları)',
    command='python main.py --input grants.csv --access access_history.csv --days 60',
    expect={'yetki': 11, 'kullanilmayan': 2, 'hic_erisimi_olmayan_kullanici': 1, 'fazla_yetki': 1})
def unused_grants(columns, rows, args):
    u, o, p = need(args.user, columns, '--user'), need(args.object, columns, '--object'), need(args.privilege, columns, '--privilege')
    acols, arows = tabular.read(args.access)
    au, ao = need(args.user, acols, '--access --user'), need(args.object, acols, '--access --object')
    tcol = next((c for c in ('zaman', 'tarih', 'query_start_time', 'timestamp') if c in acols), None)
    op_col = next((c for c in ('islem', 'operation', 'query_type') if c in acols), None)
    if not tcol:
        raise ValueError('Erişim günlüğünde zaman sütunu (zaman, tarih, timestamp) yok.')
    as_of = (tabular.parse_date(args.as_of) or (None,))[0]
    since = as_of - timedelta(days=args.days)
    used, writes, users_seen = defaultdict(lambda: None), set(), set()
    for r in arows:
        d = tabular.parse_date(r[tcol])
        if not d or d[0] < since:
            continue
        key = (r[au].strip().lower(), r[ao].strip().lower())
        users_seen.add(key[0])
        used[key] = max(filter(None, [used[key], d[0]]))
        if op_col and re.search(r'insert|update|delete|merge|write|yaz', r[op_col], re.I):
            writes.add(key)
    priv = set(PRIVILEGED.split(',')) | {'INSERT'}
    out, users = [], {r[u].strip().lower() for r in rows if r[u].strip()}
    for r in rows:
        key = (r[u].strip().lower(), r[o].strip().lower())
        if not key[0]:
            continue
        words = set(re.findall(r'[A-Z_]+', r[p].upper()))
        if used[key] is None:
            out.append({'kullanici': r[u].strip(), 'nesne': r[o].strip(), 'yetki': r[p].strip(), 'son_erisim': '', 'bulgu': f'{args.days} gündür kullanılmıyor' + (' (kullanıcının hiç erişimi yok)' if key[0] not in users_seen else '')})
        elif words & priv and key not in writes and op_col:
            out.append({'kullanici': r[u].strip(), 'nesne': r[o].strip(), 'yetki': r[p].strip(), 'son_erisim': used[key].strftime('%Y-%m-%d'), 'bulgu': 'yalnızca okuma yapılmış; yazma/sahip yetkisi fazla'})
    out.sort(key=lambda o: (o['bulgu'].startswith('yalnızca'), o['kullanici'].lower(), o['nesne']))
    return Result(f'{len(rows)} yetkiden {sum(not o["bulgu"].startswith("yalnızca") for o in out)} tanesi {args.days} gündür kullanılmıyor', ['kullanici', 'nesne', 'yetki', 'son_erisim', 'bulgu'], out,
                  {'yetki': len(rows), 'kullanilmayan': sum(not o['bulgu'].startswith('yalnızca') for o in out), 'hic_erisimi_olmayan_kullanici': len(users - users_seen),
                   'fazla_yetki': sum(o['bulgu'].startswith('yalnızca') for o in out)},
                  notes=['Erişim günlüğünün saklama süresi --days’ten kısaysa kullanılmıyor görünen yetkiler aslında kullanılmış olabilir. Yıllık/çeyreklik raporları besleyen hizmet hesaplarını geri almadan önce sorun.'])


PURVIEW_STATUS = {'taslak': 'Draft', 'draft': 'Draft', 'onaylı': 'Approved', 'onayli': 'Approved', 'approved': 'Approved', 'uyarı': 'Alert', 'uyari': 'Alert', 'alert': 'Alert',
                  'süresi dolmuş': 'Expired', 'suresi dolmus': 'Expired', 'expired': 'Expired'}
PURVIEW_COLUMNS = ['Name', 'Nick Name', 'Status', 'Definition', 'Acronym', 'Resources', 'Related Terms', 'Synonyms', 'Stewards', 'Experts', 'Parent Term Name',
                   'IsDefinitionRichText', 'Term Template Names']
MAIL = re.compile(r'^[^@\s;:]+@[^@\s;:]+\.[A-Za-z]{2,}$')


@op('sozluk-ice-aktarim', title='İş sözlüğünü Microsoft Purview içe aktarma dosyasına çevir ve hiyerarşiyi doğrula', domain=D, product='Purview', tag='glossary import',
    samples={'purview_sozluk_taslak.csv': 'purview_sozluk_taslak.csv'},
    description='Excel’de hazırlanmış iş sözlüğünü (terim, tanım, durum, üst terim, eş anlamlılar, ilgili terimler, sorumlu ve uzman e-postası, kısaltma, kaynak bağlantısı) Purview veri haritası sözlük içe aktarma CSV’sine çevirir. Önce doğrular: aynı üst terim altında tekrar eden adlar, olmayan üst terim, döngüsel hiyerarşi, olmayan eş anlamlı/ilgili terim başvurusu, geçersiz sorumlu e-postası, tanınmayan durum ve boş tanım. Hatalı terimleri (ve onlara bağlı alt terimleri) dosyaya koymaz; üst terimler alt terimlerden önce gelecek şekilde sıralar.',
    input='Sözlük CSV/XLSX', output='sonuc.csv (bulgular) + purview_sozluk.csv', command='python main.py --input is_sozlugu_taslak.xlsx',
    expect={'terim': 14, 'aktarilan': 9, 'hata': 5, 'uyari': 4, 'derinlik': 3})
def purview_glossary(columns, rows, args):
    for c in ('terim', 'tanim', 'ust_terim'):
        need(c, columns, 'sözlük')
    g = lambda r, c: r.get(c, '').strip()
    split = lambda v: [x.strip() for x in re.split(r'[;,]', v) if x.strip()]
    names = Counter(fold(g(r, 'terim')) for r in rows if g(r, 'terim'))
    by_key = {}
    findings, bad = [], set()
    for i, r in enumerate(rows, 2):
        t = g(r, 'terim')
        if not t:
            findings.append({'satir': i, 'terim': '', 'tur': 'hata', 'bulgu': 'terim adı boş'})
            continue
        key = (fold(g(r, 'ust_terim')), fold(t))
        if key in by_key:
            findings.append({'satir': i, 'terim': t, 'tur': 'hata', 'bulgu': f'aynı üst terim altında tekrar ediyor (satır {by_key[key][0]})'})
            bad.add(i)
            continue
        by_key[key] = (i, r)
    rows_by = {i: r for (_, _), (i, r) in by_key.items()}
    parent_of = {i: g(r, 'ust_terim') for i, r in rows_by.items()}
    first_row = {}
    for i, r in rows_by.items():
        first_row.setdefault(fold(g(r, 'terim')), i)
    for i, r in sorted(rows_by.items()):
        t = g(r, 'terim')
        add = lambda kind, text: findings.append({'satir': i, 'terim': t, 'tur': kind, 'bulgu': text})
        p = parent_of[i]
        if p and fold(p) not in first_row:
            add('hata', f'üst terim “{p}” sözlükte yok')
            bad.add(i)
        seen, cur = {i}, i
        while parent_of.get(cur) and fold(parent_of[cur]) in first_row:
            cur = first_row[fold(parent_of[cur])]
            if cur in seen:
                add('hata', 'döngüsel hiyerarşi: terim kendi üst terimlerinden birinin altında')
                bad.add(i)
                break
            seen.add(cur)
        for ref in split(g(r, 'esanlamlilar')) + split(g(r, 'ilgili_terimler')):
            if fold(ref) not in names:
                add('uyarı', f'“{ref}” sözlükte yok; Purview’da bağlantı kurulamaz')
        for col in ('sahip', 'uzman'):
            for mail in split(g(r, col)):
                if not MAIL.match(mail):
                    add('hata', f'{col} e-postası geçersiz: {mail}')
                    bad.add(i)
        st = g(r, 'durum').lower()
        if st and st not in PURVIEW_STATUS:
            add('uyarı', f'durum “{g(r, "durum")}” tanınmadı; Draft olarak aktarılacak')
        if not g(r, 'tanim'):
            add('uyarı', 'tanım boş')
    changed = True
    while changed:  # children of rejected terms cannot be imported either
        changed = False
        for i, p in parent_of.items():
            if i not in bad and p and fold(p) in first_row and first_row[fold(p)] in bad:
                bad.add(i)
                findings.append({'satir': i, 'terim': g(rows_by[i], 'terim'), 'tur': 'hata', 'bulgu': f'üst terim “{p}” aktarılamadığı için aktarılmadı'})
                changed = True
    depth = {}

    def level(i):
        if i not in depth:
            p = parent_of[i]
            depth[i] = 1 if not p or fold(p) not in first_row or first_row[fold(p)] == i else 1 + level(first_row[fold(p)])
        return depth[i]
    ok = sorted((i for i in rows_by if i not in bad), key=lambda i: (level(i), i))
    exported = {fold(g(rows_by[i], 'terim')) for i in ok}
    for i in ok:
        for ref in split(g(rows_by[i], 'esanlamlilar')) + split(g(rows_by[i], 'ilgili_terimler')):
            if fold(ref) in names and fold(ref) not in exported:
                findings.append({'satir': i, 'terim': g(rows_by[i], 'terim'), 'tur': 'uyarı', 'bulgu': f'“{ref}” hata nedeniyle aktarılmadığı için bağlantı atlandı'})
    export = []
    for i in ok:
        r = rows_by[i]
        export.append({'Name': g(r, 'terim'), 'Nick Name': '', 'Status': PURVIEW_STATUS.get(g(r, 'durum').lower(), 'Draft'), 'Definition': g(r, 'tanim'),
                       'Acronym': g(r, 'kisaltma'), 'Resources': ';'.join(f'Kaynak:{u}' for u in split(g(r, 'kaynak'))),
                       'Related Terms': ';'.join(x for x in split(g(r, 'ilgili_terimler')) if fold(x) in exported),
                       'Synonyms': ';'.join(x for x in split(g(r, 'esanlamlilar')) if fold(x) in exported),
                       'Stewards': ';'.join(f'{m}:' for m in split(g(r, 'sahip'))), 'Experts': ';'.join(f'{m}:' for m in split(g(r, 'uzman'))),
                       'Parent Term Name': g(r, 'ust_terim'), 'IsDefinitionRichText': 'false', 'Term Template Names': 'System default'})
    errors = len({f['satir'] for f in findings if f['tur'] == 'hata'})
    warnings = sum(f['tur'] == 'uyarı' for f in findings)
    findings.sort(key=lambda f: (f['tur'] != 'hata', f['satir']))
    return Result(f'{len(rows)} terim; {len(export)} terim içe aktarma dosyasında, {errors} terim hata nedeniyle dışarıda, {warnings} uyarı',
                  ['satir', 'terim', 'tur', 'bulgu'], findings,
                  {'terim': len(rows), 'aktarilan': len(export), 'hata': errors, 'uyari': warnings, 'derinlik': max((level(i) for i in ok), default=0)},
                  files={'purview_sozluk.csv': (PURVIEW_COLUMNS, export)},
                  notes=['Sütunlar Purview veri haritası (klasik) sözlük içe aktarma şablonundadır; sorumlu ve uzmanlar Purview’da kayıtlı Entra ID kullanıcısı olmalıdır. '
                         'Önce birkaç terimle deneyin; Unified Catalog iş terimleri ayrı bir içe aktarma kullanır.'])
