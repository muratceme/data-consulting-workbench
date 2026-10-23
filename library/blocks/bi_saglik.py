"""BI sağlık kontrolü ve geçiş değerlendirmesi blokları: Power BI VertiPaq boyut analizi ve Best Practice Analyzer kuralları,
Tableau→Power BI geçiş karmaşıklığı, Qlik veri modeli (sentetik anahtar, döngü, ada tablo) ve Looker'da kullanılmayan alanlar.
Danışmanların sağlık kontrolü ve geçiş değerlendirmesinde okuduğu dışa aktarımları işler; platforma bağlanmaz."""
import json, re
from collections import Counter, defaultdict
from pathlib import Path
import tabular
from pbit_model import read_schema
from tableau_envanter import read_twb, inventory
from qlik_soyagaci import strip_comments, statements, clean, LABEL, PREFIX
from bi_ceviri import lookml_blocks
from islem import op, arg, Result, need, numbers, fmt

REQUIRES = ['pbit_model', 'tableau_envanter', 'qlik_soyagaci', 'bi_ceviri']
MB = 1024 ** 2
AUTO_DATE = ('LocalDateTable_', 'DateTableTemplate_')


def files_of(path, patterns):
    p = Path(path)
    files = sorted({f for pat in patterns for f in p.rglob(pat)}) if p.is_dir() else [p]
    if not files or not files[0].is_file():
        raise ValueError(f'Dosya bulunamadı: {p}')
    return files


# ---------- Power BI: VertiPaq ----------
@op('pbi-vertipaq', title='Power BI model boyutu: VertiPaq kolon analizi', domain='bi', product='Power BI Desktop', tag='VertiPaq',
    samples={'vertipaq_sutunlar.csv': 'vertipaq_sutunlar.csv'},
    args=[arg('--share', type=float, default=10.0, help='Modelde bu yüzdeden büyük yer kaplayan kolon işaretlenir'),
          arg('--text-card', type=int, default=100_000, help='Yüksek kardinaliteli metin eşiği'),
          arg('--datetime-card', type=int, default=5_000, help='Bu kardinalitenin üstündeki tarih/saat kolonunda saat kısmı var sayılır')],
    description='DAX Studio / VertiPaq Analyzer kolon dışa aktarımından (tablo, kolon, veri tipi, kardinalite, toplam boyut) model boyutunu tablo ve kolon bazında çıkarır; modelin büyük payını tek başına kaplayan kolonları, saat kısmı yüzünden kardinalitesi patlayan tarih/saat kolonlarını, yüksek kardinaliteli metin ve kayan noktalı (Double) kolonları, otomatik tarih tablolarını ve hiçbir ölçü, ilişki veya görselde kullanılmayan kolonları işaretler; kaldırılırsa kazanılacak boyutu hesaplar. Power BI sağlık kontrolünün ilk adımı.',
    input='VertiPaq Analyzer “Columns” dışa aktarımı (CSV)', output='sonuc.csv (kolon bazında boyut ve bulgular)', command='python main.py --input vpa_columns.csv --share 5',
    expect={'kolon': 18, 'model_mb': 912.5, 'sorunlu_kolon': 9, 'kazanilabilir_mb': 320.7})
def vertipaq(columns, rows, args):
    for c in ('tablo', 'kolon', 'veri_tipi', 'kardinalite', 'toplam_boyut'):
        need(c, columns, 'VertiPaq dışa aktarımı')
    card, size = numbers(rows, 'kardinalite'), numbers(rows, 'toplam_boyut')
    total = sum(s or 0 for s in size) or 1
    used = [r.get('kullaniliyor', '').strip().lower() for r in rows] if 'kullaniliyor' in columns else [''] * len(rows)
    out, removable = [], 0.0
    for r, k, s, u in zip(rows, card, size, used):
        k, s = k or 0, s or 0
        table, dtype = r['tablo'].strip(), r['veri_tipi'].strip().lower()
        share = 100 * s / total
        issues, remove = [], False
        if table.startswith(AUTO_DATE):
            issues.append('otomatik tarih tablosu: Seçenekler > Veri yükleme > Otomatik tarih/saat kapatılmalı')
            remove = True
        elif u in ('hayir', 'hayır', 'false', '0', 'no'):
            issues.append('hiçbir ölçü, ilişki veya görselde kullanılmıyor: kaldırın')
            remove = True
        if share > args.share:
            issues.append(f'model payı %{share:.1f}: gerçekten gerekli mi, kaynakta özetlenebilir mi değerlendirin')
        if dtype in ('datetime', 'date/time', 'tarih/saat') and k > args.datetime_card:
            issues.append(f'tarih/saat kardinalitesi {k:,.0f}: saat kısmını ayrı kolona alın veya atın')
        if dtype in ('string', 'text', 'metin') and k > args.text_card:
            issues.append(f'yüksek kardinaliteli metin ({k:,.0f}): raporda gerekli mi, kaynakta özetlenebilir mi')
        if dtype in ('double', 'decimal number', 'ondalık') and k > args.text_card:
            issues.append('kayan nokta: Fixed Decimal (Currency) ve gereksiz ondalıkları yuvarlama sıkıştırmayı artırır')
        if remove:
            removable += s
        out.append({'tablo': table, 'kolon': r['kolon'].strip(), 'veri_tipi': r['veri_tipi'].strip(), 'kardinalite': int(k), 'boyut_mb': round(s / MB, 1),
                    'model_payi_%': round(share, 1), 'bulgular': '; '.join(issues)})
    out.sort(key=lambda o: -o['boyut_mb'])
    by_table = defaultdict(float)
    for o in out:
        by_table[o['tablo']] += o['boyut_mb']
    bad = sum(bool(o['bulgular']) for o in out)
    return Result(f'{len(out)} kolon, model {total / MB:,.1f} MB; {bad} kolonda bulgu, kaldırılabilir {removable / MB:,.1f} MB', list(out[0]), out,
                  {'kolon': len(out), 'model_mb': round(total / MB, 1), 'sorunlu_kolon': bad, 'kazanilabilir_mb': round(removable / MB, 1)},
                  tables=[('Tablo boyutları', ['Tablo', 'MB', 'Pay'], [[t, round(v, 1), f'%{100 * v * MB / total:.1f}'] for t, v in sorted(by_table.items(), key=lambda kv: -kv[1])])],
                  notes=['Boyutlar sıkıştırılmış bellek boyutudur; yenileme sırasında model geçici olarak yaklaşık iki katı bellek ister.'])


# ---------- Power BI: Best Practice Analyzer ----------
NUMERIC = {'int64', 'double', 'decimal'}
KEYLIKE = re.compile(r'(^|_)(id|key|kod|kodu|no|yil|year)$|(ID|Key|Kodu|No)$')


def _txt(v):
    return '\n'.join(v) if isinstance(v, list) else (v or '')


def bpa_rules(model):
    out = []
    tables = [t for t in model.get('tables', [])]
    user = [t for t in tables if not t.get('name', '').startswith(AUTO_DATE)]
    cols = {(t['name'], c.get('name')): c for t in user for c in t.get('columns', []) if c.get('type') != 'rowNumber'}
    measures = [(t['name'], m) for t in user for m in t.get('measures', [])]
    mnames = {m.get('name') for _, m in measures}
    colnames = {c for _, c in cols}
    add = lambda rule, cat, sev, obj, text: out.append({'kural': rule, 'kategori': cat, 'onem': sev, 'nesne': obj, 'aciklama': text})
    if len(tables) > len(user):
        add('Otomatik tarih tablosunu kaldırın', 'Performans', 'yüksek', f'{len(tables) - len(user)} gizli tablo', 'Her tarih kolonu için gizli bir takvim tablosu üretiliyor; ortak tarih tablosu kullanın.')
    if not any(t.get('dataCategory') == 'Time' for t in user) and any(c.get('dataType') == 'dateTime' for c in cols.values()):
        add('Tarih tablosunu işaretleyin', 'DAX', 'orta', 'model', 'Zaman zekâsı fonksiyonları için bir tablo "tarih tablosu olarak işaretle" ile işaretlenmeli.')
    fks = {(r.get('fromTable'), r.get('fromColumn')) for r in model.get('relationships', []) if not str(r.get('toTable', '')).startswith(AUTO_DATE)}
    for r in model.get('relationships', []):
        label = f"{r.get('fromTable')}[{r.get('fromColumn')}] → {r.get('toTable')}[{r.get('toColumn')}]"
        if str(r.get('toTable', '')).startswith(AUTO_DATE):
            continue
        if r.get('crossFilteringBehavior') == 'bothDirections':
            add('Çift yönlü ilişkiden kaçının', 'Performans', 'orta', label, 'Belirsiz filtre yolları ve yavaş sorgular; gerekiyorsa ölçüde CROSSFILTER kullanın.')
        if r.get('fromCardinality') == 'many' and r.get('toCardinality') == 'many':
            add('Çoka-çok ilişkiden kaçının', 'Performans', 'orta', label, 'Köprü tablo ile iki bire-çok ilişkiye bölün.')
    for (t, c), col in cols.items():
        obj = f'{t}[{c}]'
        if col.get('dataType') == 'double':
            add('Kayan nokta veri tipi kullanmayın', 'Performans', 'orta', obj, 'Double yuvarlama hatası üretir ve kötü sıkışır; Decimal (Fixed Decimal) kullanın.')
        if (t, c) in fks and not col.get('isHidden'):
            add('Yabancı anahtarları gizleyin', 'Biçim', 'düşük', obj, 'İlişkinin çok tarafındaki anahtar görünür; kullanıcı yanlış tablodan filtreler.')
        if col.get('dataType') in NUMERIC and not col.get('isHidden') and col.get('summarizeBy', 'sum') != 'none' and KEYLIKE.search(c):
            add('Anahtar ve yıl kolonlarını özetlemeyin', 'Biçim', 'düşük', obj, 'Özetleme “Yok” olmalı; aksi hâlde görselde toplanır.')
        if c != c.strip():
            add('Ad başında/sonunda boşluk', 'Bakım', 'düşük', obj, 'Ad boşlukla başlıyor veya bitiyor.')
    qualified_measure = re.compile(r"(?:'[^']+'|\b\w+)\[([^\]]+)\]")
    for t, m in measures:
        name, expr = m.get('name', ''), _txt(m.get('expression'))
        obj = f'{t}[{name}]'
        code = re.sub(r'//[^\n]*|--[^\n]*|"[^"]*"', '', expr)
        if re.search(r'[\])\d]\s*/\s*[\[(\w\']', code):
            add('Bölme için DIVIDE kullanın', 'DAX', 'orta', obj, '"/" sıfıra bölmede hata verir; DIVIDE(pay, payda) boş döner.')
        if re.search(r'\b(IFERROR|ISERROR)\s*\(', code, re.I):
            add('IFERROR kullanmayın', 'DAX', 'orta', obj, 'IFERROR her satırda hata yakalar ve sorguyu yavaşlatır; hatayı kaynağında önleyin (DIVIDE, COALESCE).')
        for ref in qualified_measure.findall(code):
            if ref in mnames and ref not in colnames:
                add('Ölçü başvurusu tablo adıyla yazılmamalı', 'DAX', 'düşük', obj, f'[{ref}] bir ölçü; tablo önekiyle yazılınca kolonla karışır.')
        bare = re.sub(qualified_measure, '', code)
        for ref in set(re.findall(r'\[([^\]]+)\]', bare)):
            if ref in colnames and ref not in mnames:
                add('Kolon başvurusu tablo adıyla yazılmalı', 'DAX', 'düşük', obj, f'[{ref}] bir kolon; Tablo[{ref}] biçiminde yazın.')
        if not m.get('isHidden') and not m.get('formatString'):
            add('Ölçüye biçim verin', 'Biçim', 'düşük', obj, 'Biçim dizesi yok; sayılar raporda tutarsız görünür.')
        if not m.get('isHidden') and not _txt(m.get('description')).strip():
            add('Görünür ölçüye açıklama yazın', 'Bakım', 'düşük', obj, 'Açıklama yok; kullanıcı ölçünün tanımını göremez.')
        if name != name.strip():
            add('Ad başında/sonunda boşluk', 'Bakım', 'düşük', obj, 'Ad boşlukla başlıyor veya bitiyor.')
    return out


@op('pbi-bpa', title='Power BI modelini Best Practice Analyzer kurallarıyla denetle', domain='bi', product='Power BI Desktop', tag='BPA', kind='file',
    samples={'pbi_bpa_model.bim': 'pbi_bpa_model.bim'},
    description='.pbit, model.bim veya DataModelSchema dosyasındaki tabular modeli Tabular Editor Best Practice Analyzer’ın temel kurallarıyla denetler: otomatik tarih tabloları, işaretlenmemiş tarih tablosu, çift yönlü ve çoka-çok ilişkiler, kayan nokta (Double) kolonlar, görünür yabancı anahtarlar, özetlenen anahtar/yıl kolonları, "/" ile bölme (DIVIDE yerine), IFERROR, tablo önekli ölçü ve öneksiz kolon başvuruları, biçimsiz ve açıklamasız ölçüler. Kural bazında ihlal listesi üretir.',
    input='.pbit, model.bim veya DataModelSchema', output='sonuc.csv (kural ihlalleri)', command='python main.py --input satis.pbit',
    expect={'ihlal': 13, 'kural': 12, 'yuksek': 1, 'orta': 5})
def pbi_bpa(args):
    schema, _ = read_schema(args.input)
    model = schema.get('model', schema)
    if not isinstance(model, dict) or 'tables' not in model:
        raise ValueError('Model şeması tanınmadı: "model.tables" bulunamadı.')
    out = bpa_rules(model)
    order = {'yüksek': 0, 'orta': 1, 'düşük': 2}
    out.sort(key=lambda o: (order[o['onem']], o['kategori'], o['kural'], o['nesne']))
    sev = Counter(o['onem'] for o in out)
    rules = Counter(o['kural'] for o in out)
    return Result(f'{len(out)} ihlal, {len(rules)} kural (yüksek {sev["yüksek"]}, orta {sev["orta"]}, düşük {sev["düşük"]})',
                  ['kural', 'kategori', 'onem', 'nesne', 'aciklama'], out,
                  {'ihlal': len(out), 'kural': len(rules), 'yuksek': sev['yüksek'], 'orta': sev['orta']},
                  tables=[('Kural özeti', ['Kural', 'İhlal'], [[k, v] for k, v in rules.most_common()])],
                  notes=['Kurallar Tabular Editor BPA kural setinin en sık kullanılan bölümüdür; tam set için Tabular Editor’da BPA çalıştırın.'])


# ---------- Tableau → Power BI geçiş karmaşıklığı ----------
@op('tableau-gecis-karmasikligi', title='Tableau → Power BI geçişi: çalışma kitabı karmaşıklık puanı ve efor', domain='bi', product='Tableau Desktop', tag='geçiş', kind='file',
    samples={'tableau_gecis/satis_ozet.twb': 'tableau_gecis/satis_ozet.twb', 'tableau_gecis/stok_takip.twb': 'tableau_gecis/stok_takip.twb',
             'tableau_gecis/finans_kpi.twb': 'tableau_gecis/finans_kpi.twb', 'tableau_kullanim.csv': 'tableau_kullanim.csv'}, sample_input='tableau_gecis',
    args=[arg('--usage', default='@ornek/tableau_kullanim.csv', help='İsteğe bağlı kullanım dosyası (calisma_kitabi, goruntulenme_90g); boş bırakılabilir'),
          arg('--simple', type=int, default=20, help='Bu puana kadar basit'), arg('--medium', type=int, default=50, help='Bu puana kadar orta'),
          arg('--days', default='1,3,6', help='Basit, orta, karmaşık çalışma kitabı başına efor (gün)')],
    description='Klasördeki .twb/.twbx çalışma kitaplarını sayfa, pano, hesaplanan alan, LOD ifadesi, tablo hesaplaması, parametre, veri kaynağı, özel SQL, veri harmanlama (blending) ve eylem sayısına göre puanlar; basit/orta/karmaşık sınıfı ve gün olarak geçiş eforu verir. Power BI’da doğrudan karşılığı olmayan yapıları (LOD → CALCULATE, tablo hesaplaması → pencere fonksiyonları, harmanlama → model ilişkisi) listeler. Kullanım dosyası verilirse son 90 günde açılmayan çalışma kitaplarını taşımak yerine emekliye ayırmayı önerir ve eforu ondan düşer.',
    input='.twb/.twbx dosyası veya klasörü (+ isteğe bağlı kullanım CSV’si)', output='sonuc.csv (çalışma kitabı bazında puan ve efor)', command='python main.py --input C:\\Tableau\\Yayinlananlar --usage views_90d.csv',
    expect={'calisma_kitabi': 3, 'karmasik': 1, 'emekli': 1, 'efor_gun': 7})
def tableau_migration(args):
    usage = {}
    if args.usage and Path(args.usage).is_file():
        ucols, urows = tabular.read(args.usage)
        need('calisma_kitabi', ucols, '--usage')
        need('goruntulenme_90g', ucols, '--usage')
        usage = {r['calisma_kitabi'].strip().lower(): n or 0 for r, n in zip(urows, numbers(urows, 'goruntulenme_90g'))}
    days = [float(x) for x in args.days.split(',')]
    out = []
    for f in files_of(args.input, ['*.twb', '*.twbx']):
        root, _ = read_twb(f)
        inv = inventory(root)
        calcs = inv['calculations']
        lod, tcalc = sum(c['lod'] for c in calcs), sum(c['table_calc'] for c in calcs)
        custom_sql = sum(1 for r in root.iter('relation') if r.get('type') == 'text')
        blends = sum(1 for w in inv['worksheets'] if len([d for d in w['datasources'] if d and d != 'Parameters']) > 1)
        actions = sum(1 for _ in root.iter('action'))
        parts = {'sayfa': len(inv['worksheets']), 'pano': 2 * len(inv['dashboards']), 'hesap': len(calcs), 'lod': 3 * lod, 'tablo_hesabi': 3 * tcalc,
                 'parametre': len(inv['parameters']), 'kaynak': 2 * len(inv['datasources']), 'ozel_sql': 3 * custom_sql, 'harmanlama': 4 * blends, 'eylem': actions}
        score = sum(parts.values())
        cls = 'basit' if score <= args.simple else 'orta' if score <= args.medium else 'karmaşık'
        views = usage.get(f.stem.lower())
        retire = views is not None and views == 0
        gaps = [x for x, n in (('LOD → CALCULATE/ALLEXCEPT', lod), ('tablo hesaplaması → WINDOW/OFFSET', tcalc), ('harmanlama → model ilişkisi', blends),
                               ('özel SQL → Power Query/görünüm', custom_sql), ('parametre → alan parametresi / what-if', len(inv['parameters']))) if n]
        out.append({'calisma_kitabi': f.stem, 'puan': score, 'sinif': cls, 'sayfa': len(inv['worksheets']), 'pano': len(inv['dashboards']), 'hesaplanan_alan': len(calcs),
                    'lod': lod, 'tablo_hesabi': tcalc, 'parametre': len(inv['parameters']), 'veri_kaynagi': len(inv['datasources']), 'ozel_sql': custom_sql,
                    'harmanlanan_sayfa': blends, 'goruntulenme_90g': '' if views is None else int(views),
                    'oneri': 'emekliye ayır (90 günde açılmamış)' if retire else 'taşı', 'efor_gun': 0 if retire else days[('basit', 'orta', 'karmaşık').index(cls)],
                    'dogrudan_karsiligi_olmayan': '; '.join(gaps)})
    out.sort(key=lambda o: -o['puan'])
    effort = sum(o['efor_gun'] for o in out)
    count = Counter(o['sinif'] for o in out)
    return Result(f'{len(out)} çalışma kitabı (basit {count["basit"]}, orta {count["orta"]}, karmaşık {count["karmaşık"]}); tahmini efor {fmt(effort)} gün', list(out[0]), out,
                  {'calisma_kitabi': len(out), 'karmasik': count['karmaşık'], 'emekli': sum(o['oneri'].startswith('emekli') for o in out), 'efor_gun': effort},
                  notes=['Puan ağırlıkları: sayfa 1, pano 2, hesaplanan alan 1, LOD 3, tablo hesaplaması 3, parametre 1, veri kaynağı 2, özel SQL 3, harmanlanan sayfa 4, eylem 1. '
                         'Eforu ilk birkaç çalışma kitabının gerçek süresiyle kalibre edin.'])


# ---------- Qlik veri modeli ----------
def top_split(text, sep=','):
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"', '['):
            quote = ']' if ch == '[' else ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if ''.join(buf).strip():
        parts.append(''.join(buf).strip())
    return parts


def field_names(items):
    names = []
    for item in items:
        m = re.search(r'\s+AS\s+(\[[^\]]+\]|"[^"]+"|[\w.]+)\s*$', item, re.I)
        if m:
            names.append(clean(m.group(1)))
        elif re.fullmatch(r'\[[^\]]+\]|"[^"]+"|[\w.]+', item.strip()):
            names.append(clean(item).split('.')[-1] if not item.strip().startswith(('[', '"')) else clean(item))
    return names


def load_fields(body):
    """Field names of a LOAD or SELECT statement; None for LOAD * (unknown)."""
    m = re.match(r'^(?:SQL\s+)?(?:LOAD|SELECT)\s+(?:DISTINCT\s+)?(.*?)(?:\bFROM\b|\bRESIDENT\b|\bINLINE\b|\bAUTOGENERATE\b|$)', body, re.I | re.S)
    if not m:
        return None
    if re.search(r'\bINLINE\s*\[', body, re.I):
        head = re.search(r'\bINLINE\s*\[\s*([^\n\]]*)', body, re.I).group(1)
        return [clean(x) for x in head.split(',') if x.strip()]
    lst = m.group(1).strip()
    if lst.startswith('*'):
        return None
    return field_names(top_split(lst))


def qlik_model(text):
    tables, order, warnings = {}, [], []
    qualify, unqualified, mapping = False, set(), set()
    pending = None
    for line, stmt in statements(strip_comments(text)):
        body = re.sub(r'\$\((?:Must_)?Include\s*=[^)]+\)', '', stmt, flags=re.I).strip()
        m = re.match(r'^(UN)?QUALIFY\s+(.+)$', body, re.I | re.S)
        if m:
            names = {clean(x) for x in m.group(2).split(',')}
            if m.group(1):
                unqualified |= names
            elif '*' in names:
                qualify = True
            continue
        m = re.match(r'^DROP\s+TABLES?\s+(.+)$', body, re.I | re.S)
        if m:
            for name in m.group(1).split(','):
                tables.pop(clean(name), None)
            continue
        m = re.match(r'^DROP\s+FIELDS?\s+(.+?)(?:\s+FROM\s+(.+))?$', body, re.I | re.S)
        if m:
            names = {clean(x) for x in top_split(m.group(1))}
            targets = [clean(x) for x in m.group(2).split(',')] if m.group(2) else list(tables)
            for t in targets:
                if t in tables:
                    tables[t]['fields'] -= names
            continue
        m = re.match(r'^RENAME\s+FIELDS?\s+(\[[^\]]+\]|[\w.]+)\s+TO\s+(\[[^\]]+\]|[\w.]+)', body, re.I)
        if m:
            for t in tables.values():
                if clean(m.group(1)) in t['fields']:
                    t['fields'] = (t['fields'] - {clean(m.group(1))}) | {clean(m.group(2))}
            continue
        label = None
        lm = LABEL.match(body)
        if lm and not re.match(r'^(LOAD|SELECT|SQL)$', lm.group(1), re.I):
            label, body = clean(lm.group(1)), body[lm.end():].lstrip()
        prefix, target = '', ''
        while True:
            pm = PREFIX.match(body)
            if not pm:
                break
            word = (pm.group(1) or pm.group(2) or '').upper()
            prefix = (prefix + ' ' + re.sub(r'\s+', ' ', word.split('(')[0])).strip()
            if pm.group(3):
                target = clean(pm.group(3))
            body = body[pm.end():]
        if not re.match(r'^(LOAD|SELECT|SQL)\b', body, re.I):
            continue
        fields = load_fields(body)
        has_source = re.search(r'\b(FROM|RESIDENT|INLINE|AUTOGENERATE)\b', body, re.I) or re.match(r'^(SQL\s+)?SELECT\b', body, re.I)
        if not has_source and re.match(r'^LOAD\b', body, re.I):
            pending = {'label': label, 'prefix': prefix, 'target': target, 'fields': fields, 'line': line}
            continue
        if pending:
            label, prefix, target = pending['label'] or label, pending['prefix'] or prefix, pending['target'] or target
            fields, line = pending['fields'] if pending['fields'] is not None else fields, pending['line']
            pending = None
        if 'MAPPING' in prefix:
            mapping.add(label)
            continue
        if fields is None:
            src = re.search(r'\bRESIDENT\s+(\[[^\]]+\]|"[^"]+"|[\w.\-]+)', body, re.I)
            if src and clean(src.group(1)) in tables:
                fields = sorted(tables[clean(src.group(1))]['fields'])
            else:
                warnings.append(f'{line}. satır: LOAD * alanları bilinmiyor; bu tablonun alanları modele eklenmedi.')
                fields = []
        name = label or f'(etiketsiz {line})'
        fset = set(fields)
        joined = 'JOIN' in prefix or 'CONCATENATE' in prefix.split(' ')[-1:] or 'CONCATENATE' == prefix
        if joined:
            tgt = target or (order[-1] if order else name)
            if tgt in tables:
                tables[tgt]['fields'] |= fset
                continue
        if qualify:
            fset = {f if f in unqualified else f'{name}.{f}' for f in fset}
        if 'NOCONCATENATE' not in prefix:
            same = next((t for t, v in tables.items() if v['fields'] == fset and fset), None)
            if same:
                warnings.append(f'{line}. satır: {name} alanları {same} ile aynı; Qlik otomatik birleştirir (NoConcatenate yoksa).')
                continue
        if name in tables:
            name = f'{name}-{line}'
        tables[name] = {'fields': fset, 'line': line}
        order.append(name)
    return tables, warnings


@op('qlik-veri-modeli', title='Qlik veri modeli: sentetik anahtar, döngüsel ilişki ve ada tablolar', domain='bi', product='QlikView', tag='veri modeli', kind='file',
    samples={'qlik_veri_modeli.qvs': 'qlik_veri_modeli.qvs'},
    description='QlikView / Qlik Sense load script’inden her tablonun alanlarını çıkarır (LOAD alias’ları, önceki LOAD, INLINE, RESIDENT, JOIN/CONCATENATE, QUALIFY, DROP/RENAME FIELD, otomatik birleştirme) ve Qlik’in kuracağı ilişkileri hesaplar: iki veya daha çok ortak alanı olan tablo çiftlerini (sentetik anahtar), döngüsel ilişkileri (circular reference), hiçbir tabloya bağlanmayan ada tabloları ve ikiden çok tabloyu bağlayan ortak alanları listeler. Script çalıştırılmaz.',
    input='.qvs load script', output='sonuc.csv (tablo bazında alanlar ve bağlantılar)', command='python main.py --input satis.qvs',
    expect={'tablo': 6, 'sentetik_anahtar': 1, 'dongu': 1, 'ada': 1})
def qlik_data_model(args):
    tables, warnings = qlik_model(Path(args.input).read_text(encoding='utf-8-sig', errors='replace'))
    names = list(tables)
    by_field = defaultdict(list)
    for t in names:
        for f in tables[t]['fields']:
            by_field[f].append(t)
    pair_fields = defaultdict(set)
    for f, ts in by_field.items():
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                pair_fields[tuple(sorted((a, b)))].add(f)
    findings = []
    # Qlik moves the fields two tables share (two or more) into a $Syn table; repeat until no pair shares two fields.
    work = {t: set(tables[t]['fields']) for t in names}
    synthetic = []
    for _ in range(50):
        keys = list(work)
        pairs = sorted(((len(work[a] & work[b]), a, b) for i, a in enumerate(keys) for b in keys[i + 1:] if len(work[a] & work[b]) > 1), key=lambda x: (-x[0], x[1], x[2]))
        if not pairs:
            break
        _, a, b = pairs[0]
        shared, key = work[a] & work[b], f'$Syn {len(synthetic) + 1}'
        synthetic.append((a, b, shared))
        work[key] = shared | {key}
        work[a], work[b] = (work[a] - shared) | {key}, (work[b] - shared) | {key}
    for a, b, fs in synthetic:
        findings.append(['yüksek', 'sentetik anahtar', f'{a} ↔ {b}: ortak alanlar {", ".join(sorted(fs))}. Bileşik anahtar üretin (a & \'|\' & b) veya fazla alanları yeniden adlandırın.'])
    # A loop is a cycle in the table–field graph once synthetic keys are in place.
    parent = {}

    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    holders = defaultdict(list)
    for t, fs in work.items():
        for f in fs:
            holders[f].append(t)
    loops, forest = [], defaultdict(set)

    def path(a, b):
        prev, queue = {a: None}, [a]
        for x in queue:
            for y in forest[x]:
                if y not in prev:
                    prev[y] = x
                    queue.append(y)
        out = []
        while b is not None:
            out.append(b)
            b = prev[b]
        return out
    for f in sorted(holders):
        if len(holders[f]) < 2:
            continue
        for t in sorted(holders[f]):
            node = 'alan:' + f
            if root(t) == root(node):
                loops.append(list(reversed(path(t, node))) + [t])
            else:
                parent[root(t)] = root(node)
                forest[t].add(node)
                forest[node].add(t)
    syn_label = {f'$Syn {i + 1}': f'$Syn {i + 1} ({"+".join(sorted(fs))})' for i, (_, _, fs) in enumerate(synthetic)}
    for cycle in loops:
        members = ''.join(f' –{x[5:]}– ' if x.startswith('alan:') else syn_label.get(x, x) for x in cycle)
        findings.append(['yüksek', 'döngüsel ilişki', f'{members}. Qlik bağlantılardan birini gevşek (loosely coupled) yapar ve seçimler beklenmedik sonuç verir; '
                                                      'döngüdeki ortak alanlardan birini yeniden adlandırın (ör. Sehir → MusteriSehir) veya bağlantı tablosu kurun.'])
    islands = [t for t in names if not any(len(holders[f]) > 1 for f in work[t])] if len(names) > 1 else []
    for t in islands:
        findings.append(['orta', 'ada tablo', f'{t} hiçbir tabloyla ortak alan paylaşmıyor; seçimler bu tabloyu filtrelemez.'])
    for f, ts in sorted(by_field.items()):
        if len(ts) > 2:
            findings.append(['bilgi', 'çok tabloyu bağlayan alan', f'{f}: {", ".join(ts)}. Anahtar değilse yeniden adlandırın.'])
    findings += [['bilgi', 'uyarı', w] for w in warnings]
    out = []
    for t in names:
        links = sorted({b if a == t else a for (a, b) in pair_fields if t in (a, b)})
        shared = sorted(f for f in tables[t]['fields'] if len(by_field[f]) > 1)
        out.append({'tablo': t, 'alan_sayisi': len(tables[t]['fields']), 'ortak_alanlar': ', '.join(shared), 'bagli_tablolar': ', '.join(links),
                    'alanlar': ', '.join(sorted(tables[t]['fields']))})
    return Result(f'{len(names)} tablo; {len(synthetic)} sentetik anahtar, {len(loops)} döngü, {len(islands)} ada tablo', list(out[0]), out,
                  {'tablo': len(names), 'sentetik_anahtar': len(synthetic), 'dongu': len(loops), 'ada': len(islands)},
                  tables=[('Bulgular', ['Önem', 'Tür', 'Bulgu'], findings)],
                  notes=['Alan listesi script metninden çıkarılır; değişkenle üretilen alan adları ve LOAD * ile gelen bilinmeyen kaynak alanları görülmez. '
                         'Kesin model için uygulamanın Veri modeli görüntüleyicisi veya App Metadata Analyzer ile karşılaştırın.'])


# ---------- Looker: kullanılmayan alanlar ----------
@op('looker-kullanilmayan-alanlar', title='Looker: kullanılmayan alan, view ve explore temizliği', domain='bi', product='Looker', tag='System Activity', kind='file',
    samples={'looker_proje/satis.model.lkml': 'looker_proje/satis.model.lkml', 'looker_proje/siparis.view.lkml': 'looker_proje/siparis.view.lkml',
             'looker_proje/musteri.view.lkml': 'looker_proje/musteri.view.lkml', 'looker_proje/kampanya.view.lkml': 'looker_proje/kampanya.view.lkml',
             'looker_alan_kullanimi.csv': 'looker_alan_kullanimi.csv'}, sample_input='looker_proje',
    args=[arg('--usage', default='@ornek/looker_alan_kullanimi.csv', help='System Activity alan kullanımı (explore, view, alan, sorgu_sayisi)')],
    description='LookML projesindeki view, alan ve explore tanımlarını System Activity’nin alan kullanımı dışa aktarımıyla karşılaştırır: dönem içinde hiçbir sorguda kullanılmayan boyut ve ölçüleri (birincil anahtar ve kullanılan alanların ${} ile başvurduğu alanlar hariç), hiç sorgulanmayan explore’ları ve hiçbir explore’a bağlanmayan view’ları listeler; her biri için “hidden: yes” veya silme önerir. System Activity geçmişi 90 gündür; daha eski kullanım görünmez.',
    input='LookML proje klasörü + alan kullanımı CSV’si', output='sonuc.csv (alan bazında kullanım ve öneri)', command='python main.py --input looker-proje --usage field_usage_90d.csv',
    expect={'alan': 16, 'kullanilmayan_alan': 7, 'kullanilmayan_explore': 1, 'bagsiz_view': 1})
def looker_unused(args):
    ucols, urows = tabular.read(args.usage)
    for c in ('view', 'alan', 'sorgu_sayisi'):
        need(c, ucols, '--usage')
    counts = Counter()
    explore_hits = Counter()
    for r, n in zip(urows, numbers(urows, 'sorgu_sayisi')):
        counts[(r['view'].strip(), r['alan'].strip())] += n or 0
        if 'explore' in ucols:
            explore_hits[r['explore'].strip()] += n or 0
    fields, refs, explores, joined = [], defaultdict(set), [], set()
    structural = set()
    for f in files_of(args.input, ['*.lkml']):
        text = re.sub(r'#[^\n]*', '', f.read_text(encoding='utf-8-sig', errors='replace'))
        for m in re.finditer(r'\bexplore\s*:\s*(\w+)\s*\{', text):
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {'{': 1, '}': -1}.get(text[i], 0)
                i += 1
            body = text[m.end():i - 1]
            base = re.search(r'\bfrom\s*:\s*(\w+)', body)
            views = {base.group(1) if base else m.group(1)} | set(re.findall(r'\bjoin\s*:\s*(\w+)', body))
            explores.append((m.group(1), views))
            joined |= views
            structural |= set(re.findall(r'\$\{(\w+)\.(\w+)\}', body))
        for kind, view, body in lookml_blocks(text):
            if kind != 'view':
                continue
            for k, n, b in lookml_blocks(body):
                if k == 'view':
                    continue
                pk = bool(re.search(r'primary_key\s*:\s*yes', b))
                hidden = bool(re.search(r'hidden\s*:\s*yes', b))
                fields.append({'view': view, 'alan': n, 'tur': k, 'pk': pk, 'gizli': hidden})
                for ref in re.findall(r'\$\{(?:(\w+)\.)?(\w+)\}', b):
                    if ref[1] != 'TABLE':
                        refs[(ref[0] or view, ref[1])].add((view, n))
    suffixes = ('', '_date', '_week', '_month', '_quarter', '_year', '_raw', '_time')

    def used(view, name, seen=()):
        if any(counts[(view, name + s)] for s in suffixes):
            return True
        return any(used(*src, seen + ((view, name),)) for src in refs.get((view, name), ()) if src not in seen and src != (view, name))
    out = []
    for fl in fields:
        n = sum(counts[(fl['view'], fl['alan'] + s)] for s in suffixes)
        indirect = not n and used(fl['view'], fl['alan'])
        if n:
            advice = ''
        elif fl['pk']:
            advice = 'birincil anahtar: koru'
        elif (fl['view'], fl['alan']) in structural:
            advice = 'explore join koşulunda kullanılıyor: koru'
        elif indirect:
            advice = 'yalnızca başka bir kullanılan alan üzerinden kullanılıyor: koru'
        else:
            advice = 'kullanılmıyor: önce hidden: yes, bir dönem sonra sil' if not fl['gizli'] else 'gizli ve kullanılmıyor: silin'
        out.append({'view': fl['view'], 'alan': fl['alan'], 'tur': fl['tur'], 'sorgu_sayisi': int(n), 'oneri': advice})
    unused = [o for o in out if o['oneri'].startswith(('kullanılmıyor', 'gizli'))]
    idle_explores = [e for e, _ in explores if 'explore' in ucols and not explore_hits[e]]
    orphan_views = sorted({fl['view'] for fl in fields} - joined)
    out.sort(key=lambda o: (not o['oneri'].startswith(('kullanılmıyor', 'gizli')), o['view'], o['alan']))
    table = [['explore', e, 'dönem içinde hiç sorgulanmadı: kaldırmayı veya hidden: yes yapmayı değerlendirin'] for e in idle_explores]
    table += [['view', v, 'hiçbir explore’da yok (from/join): kullanılmıyorsa silin'] for v in orphan_views]
    return Result(f'{len(out)} alan; {len(unused)} alan, {len(idle_explores)} explore kullanılmıyor, {len(orphan_views)} view hiçbir explore’a bağlı değil', list(out[0]), out,
                  {'alan': len(out), 'kullanilmayan_alan': len(unused), 'kullanilmayan_explore': len(idle_explores), 'bagsiz_view': len(orphan_views)},
                  tables=[('Explore ve view', ['Tür', 'Ad', 'Öneri'], table)],
                  notes=['System Activity yalnız son 90 günü tutar; mevsimsel (yıl sonu) raporları silmeden önce sahibine sorun. Dashboard ve Look’larda filtre olarak kullanılan alanlar da sorgu sayısına dahildir.'])
