"""Tableau hesaplanmış alan yazarı (AI agent): .twb/.twbx çalışma kitabının veri kaynağındaki alanları (iç ad, görünen ad, tip, rol) yerelde
okur; model istenen ölçüleri Tableau hesaplanmış alanı olarak (IF/CASE, tarih fonksiyonları, FIXED/INCLUDE/EXCLUDE LOD, tablo hesapları)
yazar. Her formül kodla denetlenir: parantez, köşeli parantez, süslü parantez ve tırnak dengesi, IF/CASE…END eşleşmesi, alan adlarının
veri kaynağında olması, fonksiyonların Tableau fonksiyonu olması, toplanmış ve toplanmamış ifadelerin karıştırılmaması. Geçen alanlar
görünen adlardan iç adlara çevrilip çalışma kitabına eklenir (yeni .twb). Veri ve formül dışı içerik gönderilmez; yalnızca alan adları gider.
Çıktı: hesapli.twb, hesaplanan-alanlar.md, alanlar.csv."""
import argparse, re, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr
import llm
import tabular
from common import here, output_dir, write_text, md_table

AGG = {'sum', 'avg', 'count', 'countd', 'min', 'max', 'median', 'attr', 'stdev', 'stdevp', 'var', 'varp', 'percentile', 'collect'}
FUNCS = AGG | {f.lower() for f in '''IF THEN ELSEIF ELSE END IIF CASE WHEN ZN ISNULL IFNULL ISDATE DATETRUNC DATEPART DATENAME DATEADD DATEDIFF DATEPARSE MAKEDATE MAKEDATETIME
MAKETIME TODAY NOW YEAR QUARTER MONTH WEEK DAY ISOYEAR ISOWEEK ISOQUARTER DATE DATETIME FLOAT INT STR LEFT RIGHT MID FIND FINDNTH CONTAINS STARTSWITH ENDSWITH UPPER LOWER
TRIM LTRIM RTRIM LEN REPLACE SPLIT SPACE ASCII CHAR REGEXP_MATCH REGEXP_EXTRACT REGEXP_REPLACE ROUND ABS DIV CEILING FLOOR POWER SQRT EXP LN LOG SIGN SQUARE PI ZN
MIN MAX FIXED INCLUDE EXCLUDE WINDOW_SUM WINDOW_AVG WINDOW_MIN WINDOW_MAX WINDOW_COUNT WINDOW_MEDIAN RUNNING_SUM RUNNING_AVG RUNNING_COUNT RUNNING_MIN RUNNING_MAX
TOTAL RANK RANK_DENSE RANK_UNIQUE RANK_PERCENTILE INDEX FIRST LAST LOOKUP PREVIOUS_VALUE SIZE AND OR NOT IN USERNAME FULLNAME ISMEMBEROF'''.split()}
KEYWORDS = {'if', 'then', 'elseif', 'else', 'end', 'case', 'when', 'and', 'or', 'not', 'in', 'true', 'false', 'null', 'fixed', 'include', 'exclude'}
TYPES = {'real', 'integer', 'string', 'date', 'datetime', 'boolean'}


def read_twb(path):
    raw = Path(path).read_bytes()
    if raw[:2] == b'PK':
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith('.twb')), None)
            if not name:
                raise ValueError('.twbx içinde .twb yok.')
            raw = z.read(name)
    if b'<!DOCTYPE' in raw[:2000] or b'<!ENTITY' in raw[:5000]:
        raise ValueError('DTD/ENTITY içeren XML işlenmez.')
    return raw


def fields_of(root):
    ds = next((d for d in root.iter('datasource') if d.get('name') != 'Parameters' and d.find('connection') is not None), None)
    if ds is None:
        raise ValueError('Çalışma kitabında bağlantılı veri kaynağı yok.')
    fields = {}
    for c in ds.findall('column'):
        name = c.get('name', '')
        fields[name.strip('[]')] = {'ad': name.strip('[]'), 'gorunen': c.get('caption') or name.strip('[]'), 'tip': c.get('datatype', ''), 'rol': c.get('role', ''),
                                    'hesap': c.find('calculation') is not None}
    for c in ds.iter('column'):  # relation columns not declared at datasource level
        if c.get('ordinal') is not None and c.get('name') and c.get('name') not in fields:
            fields[c.get('name')] = {'ad': c.get('name'), 'gorunen': c.get('name'), 'tip': c.get('datatype', ''), 'rol': '', 'hesap': False}
    return ds, fields


def strip_calls(text, pattern):
    """Remove every call matched by pattern together with its (arbitrarily nested) argument list."""
    out, i = [], 0
    while True:
        m = pattern.search(text, i)
        if not m:
            return ''.join(out) + text[i:]
        out.append(text[i:m.start()] + ' ')
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {'(': 1, ')': -1}.get(text[j], 0)
            j += 1
        i = j


def check(formula, names, new_names):
    """Problems of one Tableau formula; names maps every accepted reference (internal or caption) to the internal name."""
    problems, stack, quote = [], [], None
    pairs = {')': '(', ']': '[', '}': '{'}
    i = 0
    while i < len(formula):
        ch = formula[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch == '[':
            j = formula.find(']', i)
            if j < 0:
                return ['köşeli parantez kapanmamış']
            ref = formula[i + 1:j]
            if ref not in names and ref not in new_names:
                problems.append(f'veri kaynağında olmayan alan [{ref}]')
            i = j
        elif ch in '({':
            stack.append(ch)
        elif ch in ')}':
            if not stack or stack.pop() != pairs[ch]:
                return ['parantezler dengesiz']
        elif ch == '/' and formula[i:i + 2] == '//':
            j = formula.find('\n', i)
            i = len(formula) if j < 0 else j
        i += 1
    if stack or quote:
        return ['parantez veya tırnak kapanmamış']
    bare = re.sub(r'"[^"]*"|\'[^\']*\'|\[[^\]]*\]|//[^\n]*', ' ', formula)
    words = [w.lower() for w in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', bare)]
    for w, call in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*(\()?', bare):
        if call and w.lower() not in FUNCS:
            problems.append(f'Tableau fonksiyonu değil: {w}()')
        elif not call and w.lower() not in KEYWORDS and w.lower() not in FUNCS and not re.fullmatch(r'\d+', w):
            problems.append(f'tanınmayan sözcük: {w} (alanları köşeli parantezle yazın)')
    opened = words.count('if') + words.count('case')
    if opened != words.count('end'):
        problems.append('IF/CASE ile END sayısı eşleşmiyor')
    outside_lod = re.sub(r'\{[^{}]*\}', ' ', re.sub(r'"[^"]*"|\'[^\']*\'', ' ', formula))
    has_agg = re.search(r'\b(' + '|'.join(AGG) + r')\s*\(', outside_lod, re.I)
    if has_agg:
        stripped = strip_calls(outside_lod, re.compile(r'\b(' + '|'.join(AGG) + r'|window_\w+|running_\w+|total|lookup|rank\w*)\s*\(', re.I))
        loose = [r for r in re.findall(r'\[([^\]]+)\]', stripped) if r not in new_names]
        if loose:
            problems.append('toplanmış ve toplanmamış ifade karışık: ' + ', '.join(f'[{x}]' for x in dict.fromkeys(loose)) + ' bir toplama fonksiyonuna alınmalı veya ATTR() kullanılmalı')
    return list(dict.fromkeys(problems))


def validator(fields):
    names = {}
    for f in fields.values():
        names[f['ad']] = f['ad']
        names[f['gorunen']] = f['ad']

    def validate(obj):
        problems, out = [], []
        items = obj.get('alanlar') or []
        if not 1 <= len(items) <= 15:
            problems.append('1–15 hesaplanmış alan yaz')
        new_names = {str(i.get('ad', '')).strip() for i in items if isinstance(i, dict)}
        for it in items:
            if not isinstance(it, dict):
                continue
            name, formula = str(it.get('ad', '')).strip(), str(it.get('formul', '')).strip()
            tip, rol = str(it.get('tip', 'real')).strip().lower(), str(it.get('rol', 'measure')).strip().lower()
            if not name or not formula:
                problems.append('her alanın ad ve formul değeri dolu olmalı')
                continue
            if name in names:
                problems.append(f'{name}: veri kaynağında bu adda alan var; başka ad seç')
            if tip not in TYPES:
                problems.append(f'{name}: tip {", ".join(sorted(TYPES))} değerlerinden biri olmalı')
            if rol not in ('measure', 'dimension'):
                problems.append(f'{name}: rol measure veya dimension olmalı')
            p = check(formula, names, new_names - {name})
            if p:
                problems.append(f'{name}: ' + '; '.join(p))
            out.append({'ad': name, 'formul': formula, 'tip': tip, 'rol': rol, 'aciklama': str(it.get('aciklama', '')).strip()})
        if len({o['ad'] for o in out}) != len(out):
            problems.append('alan adları tekrarlanıyor')
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return {'alanlar': out, 'varsayimlar': [str(x).strip() for x in obj.get('varsayimlar') or [] if str(x).strip()]}
    return validate, names


SYSTEM = (llm.SAFETY + ' Sen deneyimli bir Tableau geliştiricisisin. Kullanıcının istediği her ölçü için Tableau hesaplanmış alanı yaz. Alanları köşeli parantezle ve verilen '
          'görünen adlarıyla yaz ([Tutar]); tarih aralıkları için DATETRUNC/DATEDIFF ve {FIXED : MAX([Tarih])} gibi LOD kullan; toplanmış ve toplanmamış ifadeleri karıştırma. '
          'Önceki bir hesaplanmış alanı adıyla köşeli parantez içinde kullanabilirsin. Bir alan yoksa uydurma; varsayimlar’a yaz. tip: real, integer, string, date, datetime veya '
          'boolean; rol: measure veya dimension. Yanıt şeması: {"alanlar": [{"ad": "Ciro YTD", "formul": "SUM(IF ... THEN [Tutar] END)", "tip": "real", "rol": "measure", '
          '"aciklama": "..."}], "varsayimlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Veri kaynağındaki alanlarla doğrulanmış Tableau hesaplanmış alanları yazan ve çalışma kitabına ekleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'satis_raporu.twb')), help='.twb veya .twbx')
    p.add_argument('--request', default='1) Yıl başından bugüne ciro (verideki son tarihe göre), 2) geçen yılın aynı dönemi, 3) sipariş başına ortalama ciro, '
                                        '4) bölgenin toplam ciro içindeki payı (LOD), 5) her siparişin ait olduğu ayın toplam cirosu.', help='İstenen hesaplar')
    llm.add_arguments(p)
    args = p.parse_args()
    raw = read_twb(args.input)
    root = ET.fromstring(raw)
    ds, fields = fields_of(root)
    out = output_dir(args.output)
    tabular.write_csv(out / 'alanlar.csv', ['ad', 'gorunen', 'tip', 'rol', 'hesap'], list(fields.values()))
    validate, names = validator(fields)
    lines = [f"- [{f['gorunen']}] ({f['tip'] or '?'}, {f['rol'] or '?'}{', hesaplanmış' if f['hesap'] else ''})" for f in fields.values()]
    user = f'İstek: {args.request}\n\nVeri kaynağı: {ds.get("caption") or ds.get("name")}\nAlanlar:\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validate)
    if result is None:
        return
    calc_names = {o['ad']: f'[Calculation_{i + 1:03d}_agent]' for i, o in enumerate(result['alanlar'])}

    def internal(formula):
        def sub(m):
            ref = m.group(1)
            return calc_names.get(ref) or f'[{names.get(ref, ref)}]'
        return re.sub(r'\[([^\]]+)\]', sub, formula)
    text = raw.decode('utf-8')
    xml_cols = ''.join(f'\n      <column caption={quoteattr(o["ad"])} datatype={quoteattr(o["tip"])} name={quoteattr(calc_names[o["ad"]])} role={quoteattr(o["rol"])} '
                       f'type={quoteattr("quantitative" if o["rol"] == "measure" else "nominal")}>\n        <calculation class="tableau" formula={quoteattr(internal(o["formul"]))} />\n      </column>'
                       for o in result['alanlar'])
    ds_name = ds.get('name')
    m = re.search(r'(<datasource\b[^>]*name=(["\'])' + re.escape(ds_name) + r'\2[^>]*>.*?)(\n\s*</datasource>)', text, re.S)
    if not m:
        raise ValueError('Veri kaynağı XML’de bulunamadı.')
    new_text = text[:m.end(1)] + xml_cols + text[m.end(1):]
    ET.fromstring(new_text.encode('utf-8'))  # still well-formed
    (out / 'hesapli.twb').write_bytes(new_text.encode('utf-8'))
    md = ['# Tableau hesaplanmış alanları', '', f'**İstek:** {args.request}', '',
          md_table(['Alan', 'Formül', 'Tip', 'Açıklama'], [[o['ad'], f"`{o['formul']}`", o['tip'], o['aciklama']] for o in result['alanlar']])]
    if result['varsayimlar']:
        md += ['', '## Varsayımlar', ''] + [f'- {x}' for x in result['varsayimlar']]
    md += ['', f"_Model: {meta['model']} · Formüller veri kaynağındaki {len(fields)} alana göre denetlendi ve hesapli.twb’ye eklendi. Tableau’da açıp bilinen bir değerle karşılaştırın._"]
    write_text(out / 'hesaplanan-alanlar.md', '\n'.join(md))
    print(f"[OK] {len(result['alanlar'])} hesaplanmış alan denetlendi ve hesapli.twb’ye eklendi. Çıktı: {out / 'hesaplanan-alanlar.md'}")


if __name__ == '__main__':
    llm.run(main)
