"""Qlik ifade yazarı (AI agent): load script’ten alan adlarını ve değişkenleri yerelde çıkarır; model kullanıcının istediği ölçüleri Qlik
grafik ifadesi olarak (set analizi, TOTAL, Aggr dahil) yazar. Her ifade kodla denetlenir: parantez/tırnak dengesi, köşeli parantezli ve çıplak
alan adlarının script’te olması, fonksiyon adlarının Qlik fonksiyonu olması, $(değişken) genişletmelerinin script’te tanımlı olması. Geçen
ifadeler qlik-cli/corectl ile yüklenebilecek ana ölçü dosyasına yazılır. Script metni gönderilmez; yalnızca alan ve değişken adları gider.
Çıktı: measures.json, ifadeler.md, alanlar.csv."""
import argparse, json, re
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from qlik_soyagaci import strip_comments, statements, parse

FUNCTIONS = {f.lower() for f in '''Sum Count Avg Min Max Only Concat MinString MaxString Mode Median Fractile Stdev Skew Kurtosis Correl Aggr Rank HRank Above Below Before After
Top Bottom First Last RangeSum RangeAvg RangeMin RangeMax RangeCount RangeOnly RangeStdev If Alt Num Num# Date Date# Time Timestamp Timestamp# Year Month Day Week WeekDay
WeekYear Quarter Today Now YearStart YearEnd MonthStart MonthEnd QuarterStart QuarterEnd WeekStart WeekEnd DayStart DayEnd AddMonths AddYears InYear InYearToDate
InMonth InMonthToDate InQuarter InQuarterToDate MakeDate MakeTime Dual Text Round Floor Ceil Fabs Div Mod Pick Match MixMatch WildMatch Len Left Right Mid Index
Upper Lower Capitalize Trim LTrim RTrim Replace SubField KeepChar PurgeChar Repeat GetSelectedCount GetFieldSelections GetPossibleCount GetExcludedCount GetCurrentSelections
Column ColumnNo RowNo NoOfRows NoOfColumns Dimensionality SecondaryDimensionality FirstSortedValue Peek Exists IsNull IsNum IsText Null Class Interval Interval#
NetWorkDays Pow Sqrt Exp Log Log10 Sign Even Odd RGB ARGB Color ColorMix1 ColorMix2 Money Money# Chr Ord Evaluate ValueList ValueLoop Pi Rand Count Sum
Not And Or Xor Like Precedes Follows'''.split()}
KEYWORDS = {'distinct', 'total', 'all', 'nodistinct', 'and', 'or', 'not', 'xor', 'like', 'precedes', 'follows', 'null'}


def script_fields(text):
    """Field names created by LOAD lists (aliases and plain fields) and INLINE headers; LOAD * is reported separately."""
    fields, star = set(), False
    for st in statements(strip_comments(text)):
        body = st[1]
        for m in re.finditer(r'\bLOAD\b(.*?)(?=\bFROM\b|\bRESIDENT\b|\bINLINE\b|\bAUTOGENERATE\b|\bWHILE\b|\bWHERE\b|;|$)', body, re.I | re.S):
            items, depth, cur = [], 0, ''
            for ch in m.group(1):
                if ch in '([{':
                    depth += 1
                elif ch in ')]}':
                    depth -= 1
                if ch == ',' and depth == 0:
                    items.append(cur)
                    cur = ''
                else:
                    cur += ch
            items.append(cur)
            for it in items:
                it = re.sub(r'^\s*DISTINCT\s+', '', it.strip(), flags=re.I)
                if not it:
                    continue
                if it == '*':
                    star = True
                    continue
                alias = re.search(r'\bas\s+(\[[^\]]+\]|"[^"]+"|[\w%.#$]+)\s*$', it, re.I)
                name = alias.group(1) if alias else it if re.fullmatch(r'\[[^\]]+\]|"[^"]+"|[\w%.#$]+', it) else None
                if name:
                    fields.add(name.strip('[]"'))
        inline = re.search(r'\bINLINE\s*\[\s*([^\n\]]+)', body, re.I)
        if inline:
            sep = ';' if ';' in inline.group(1) else ','
            fields |= {f.strip().strip('[]"') for f in inline.group(1).split(sep) if f.strip()}
    return fields, star


def check(expr, fields, variables):
    """Problems of one chart expression."""
    problems, stack, quote, i = [], [], None, 0
    pairs = {')': '(', ']': '[', '}': '{'}
    for ch in expr:
        if quote:
            quote = None if ch == quote else quote
            continue
        if ch in '"\'':
            quote = ch
        elif ch in '([{':
            stack.append(ch)
        elif ch in ')]}':
            if not stack or stack.pop() != pairs[ch]:
                return ['parantezler dengesiz']
    if stack or quote:
        return ['parantez veya tırnak kapanmamış']
    for v in re.findall(r'\$\(\s*([^=)][^)]*)\)', expr):
        name = v.strip()
        if name.lower() not in variables:
            problems.append(f'tanımsız değişken $({name})')
    bare = re.sub(r"'[^']*'|\"[^\"]*\"|\$\([^)]*\)", ' ', expr)  # strings, search strings and dollar expansions
    for f in re.findall(r'\[([^\]]+)\]', bare):
        if f not in fields:
            problems.append(f'script’te olmayan alan [{f}]')
    bare = re.sub(r'\[[^\]]*\]', ' ', bare)
    for tok, call in re.findall(r'(?<![\w.#])([A-Za-z_][\w.#]*)\s*(\()?', bare):
        low = tok.lower()
        if call:
            if low not in FUNCTIONS:
                problems.append(f'Qlik fonksiyonu değil: {tok}()')
        elif low not in KEYWORDS and tok not in fields and low not in variables and not re.fullmatch(r'\d+', tok):
            problems.append(f'script’te olmayan alan {tok}')
    return list(dict.fromkeys(problems))


def validator(fields, variables):
    def validate(obj):
        problems, out = [], []
        items = obj.get('ifadeler') or []
        if not 1 <= len(items) <= 12:
            problems.append('1–12 ifade yaz')
        for it in items:
            if not isinstance(it, dict):
                continue
            name, expr = str(it.get('ad', '')).strip(), str(it.get('ifade', '')).strip()
            if not name or not expr:
                problems.append('her ifadenin ad ve ifade alanı dolu olmalı')
                continue
            if name in [o['ad'] for o in out]:
                problems.append(f'{name}: ad tekrarlanmış')
            p = check(expr, fields, variables)
            if p:
                problems.append(f'{name}: ' + '; '.join(p))
            out.append({'ad': name, 'ifade': expr, 'aciklama': str(it.get('aciklama', '')).strip()})
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return {'ifadeler': out, 'varsayimlar': [str(x).strip() for x in obj.get('varsayimlar') or [] if str(x).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen deneyimli bir Qlik Sense geliştiricisisin. Kullanıcının istediği her ölçü için Qlik grafik ifadesi yaz: yalnızca verilen alan ve değişken '
          'adlarını kullan, alan adlarını köşeli parantez içinde yaz ([Alan Adı]); tarih aralıkları için set analizi ve $(=...) genişletmesi, seçimden bağımsız toplam için '
          'TOTAL veya {1}, gruplu hesap için Aggr kullan. Bir alan yoksa uydurma; ölçüyü yazmadan varsayimlar’a yaz. Script yüklemesi değil, grafik ifadesi yaz. '
          'Yanıt şeması: {"ifadeler": [{"ad": "Ciro YTD", "ifade": "Sum({<[Tarih]={\\">=$(=Date(YearStart(Max([Tarih]))))\\"}>} [Tutar])", "aciklama": "..."}], "varsayimlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Script’teki alanlarla doğrulanmış Qlik grafik ifadeleri yazan agent (script metni gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satis.qvs')), help='Load script (.qvs/.txt)')
    p.add_argument('--request', default='1) Yıl başından bugüne ciro, 2) geçen yılın aynı dönemi, 3) sipariş başına ortalama tutar, 4) kategori seçiminden bağımsız toplam ciro '
                                        've seçili kategorinin payı, 5) en az 3 siparişi olan müşteri sayısı.', help='İstenen ölçüler')
    p.add_argument('--fields', default='', help='Script’te görünmeyen (LOAD * ile gelen) ek alan adları, virgülle')
    llm.add_arguments(p)
    args = p.parse_args()
    text = open(args.input, encoding='utf-8-sig', errors='replace').read()
    fields, star = script_fields(text)
    fields |= {f.strip() for f in args.fields.split(',') if f.strip()}
    lin = parse(text)
    variables = {v['name'].lower() for v in lin['variables']}
    if not fields:
        raise ValueError('Script’te alan adı bulunamadı (LOAD * kullanılıyorsa --fields ile alanları verin).')
    out = output_dir(args.output)
    tabular.write_csv(out / 'alanlar.csv', ['alan'], [{'alan': f} for f in sorted(fields)])
    tables = ', '.join(t['name'] for t in lin['tables'] if t.get('kind') != 'mapping' and t['prefix'].lower() != 'mapping') or '?'
    user = (f'İstek: {args.request}\n\nTablolar: {tables}\nAlanlar: ' + ', '.join(f'[{f}]' for f in sorted(fields))
            + '\nDeğişkenler: ' + (', '.join(v['name'] for v in lin['variables']) or 'yok') + ('\nNot: script’te LOAD * var; listelenmeyen alanlar olabilir.' if star else ''))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(fields, variables))
    if result is None:
        return
    measures = [{'qInfo': {'qId': 'ifade-' + re.sub(r'[^a-z0-9]+', '-', o['ad'].lower()).strip('-'), 'qType': 'measure'},
                 'qMeasure': {'qLabel': o['ad'], 'qDef': o['ifade'], 'qGrouping': 'N', 'qExpressions': [], 'qActiveExpression': 0},
                 'qMetaDef': {'title': o['ad'], 'description': o['aciklama'], 'tags': ['agent']}} for o in result['ifadeler']]
    write_text(out / 'measures.json', json.dumps(measures, ensure_ascii=False, indent=2))
    md = ['# Qlik ifadeleri', '', f'**İstek:** {args.request}', '', md_table(['Ölçü', 'İfade', 'Açıklama'], [[o['ad'], f"`{o['ifade']}`", o['aciklama']] for o in result['ifadeler']])]
    if result['varsayimlar']:
        md += ['', '## Varsayımlar', ''] + [f'- {x}' for x in result['varsayimlar']]
    md += ['', f"_Model: {meta['model']} · İfadeler script’teki {len(fields)} alan ve {len(variables)} değişkene göre denetlendi (sözdizimi dengesi, alan, fonksiyon ve değişken adları). "
           'Sonuçları Qlik’te bilinen bir değerle karşılaştırın; qlik-zaman-zekasi bloğu beklenen değer üretir._']
    write_text(out / 'ifadeler.md', '\n'.join(md))
    print(f"[OK] {len(result['ifadeler'])} ifade yazıldı ve denetlendi. Çıktı: {out / 'ifadeler.md'}")


if __name__ == '__main__':
    llm.run(main)
