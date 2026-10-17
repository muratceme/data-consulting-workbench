"""Deney analisti (AI agent): iki grubun ölçümlerini (görev süresi, sepet tutarı, bekleme) yerelde özetler; her grup için normallik
(Jarque-Bera), Welch t-testi ve Mann-Whitney U testini kodla hesaplar ve numaralı olgulara çevirir. Model uygun testi seçer, sonucu
ve pratik anlamını yazar. Seçim kodla kısıtlanır: gruplardan biri normal değilse veya 8’den az ölçüm varsa t-testi seçilemez; “anlamlı
fark var/yok” kararı seçilen testin p-değeriyle, yön (hangi grup yüksek) ise medyanlarla eşleşmek zorundadır; metindeki her sayı
olgularla eşleştirilir. Satırlar gönderilmez. Çıktı: deney-raporu.md, olgular.csv, deney-raporu.json."""
import argparse, math
from argparse import Namespace
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_json, write_text
from analiz import describe, normality, mann_whitney, t_two_sided
from donem_yorumu import allowed_numbers, unknown_numbers, tr

TESTS = ('welch-t', 'mann-whitney')
RESULTS = ('anlamlı fark var', 'anlamlı fark yok')


def build_facts(columns, rows, args):
    if args.group not in columns or args.value not in columns:
        raise ValueError(f'--group ve --value sütunları dosyada olmalı. Sütunlar: {", ".join(columns)}')
    groups = defaultdict(list)
    style = tabular.number_style([x[args.value] for x in rows if x[args.value].strip()])
    for r in rows:
        v = tabular.parse_number(r[args.value], style) if r[args.value].strip() else None
        if v is not None and r[args.group].strip():
            groups[r[args.group].strip()].append(v)
    names = sorted(groups)
    a = args.a or (names[0] if names else '')
    b = args.b or next((n for n in names if n != a), '')
    if a not in groups or b not in groups:
        raise ValueError(f'İki grup bulunamadı; gruplar: {", ".join(names)}')
    facts, stats = [], {'gruplar': (a, b)}

    def add(text):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text})
    normal = {}
    for g in (a, b):
        d = describe(groups[g])
        stats[g] = d
        if d['n'] >= 8:
            res = normality(['x'], [{'x': repr(v)} for v in groups[g]], Namespace(columns='', alpha=args.alpha))
            row = res.rows[0]
            normal[g] = row['sonuc'] != 'normal değil'
            add(f"{g}: n={d['n']}, ortalama {tr(d['ortalama'])}, medyan {tr(d['medyan'])}, standart sapma {tr(d['std'])}; çarpıklık {tr(row['carpiklik'])}, "
                f"Jarque-Bera p-değeri {tr(row['p_degeri'], 4)} → {'normal dağılım reddedilemez' if normal[g] else 'normal değil'}")
        else:
            normal[g] = False
            add(f"{g}: n={d['n']}, ortalama {tr(d['ortalama'])}, medyan {tr(d['medyan'])}; normallik için ölçüm sayısı yetersiz (8’den az)")
    xa, xb = groups[a], groups[b]
    sa, sb = stats[a], stats[b]
    se = math.sqrt(sa['std'] ** 2 / sa['n'] + sb['std'] ** 2 / sb['n'])
    t = (sa['ortalama'] - sb['ortalama']) / se if se else 0.0
    df = se ** 4 / ((sa['std'] ** 2 / sa['n']) ** 2 / (sa['n'] - 1) + (sb['std'] ** 2 / sb['n']) ** 2 / (sb['n'] - 1)) if se else 0
    p_t = t_two_sided(abs(t), df) if se else 1.0
    add(f"Welch t-testi ({a} − {b}): ortalama farkı {tr(sa['ortalama'] - sb['ortalama'])}, t={tr(t, 3)}, serbestlik derecesi {tr(df, 1)}, p-değeri {tr(p_t, 4)}")
    mw = mann_whitney(['g', 'v'], [{'g': a, 'v': repr(x)} for x in xa] + [{'g': b, 'v': repr(x)} for x in xb], Namespace(group='g', value='v', a=a, b=b)).summary
    add(f"Mann-Whitney U ({a} vs {b}): U={tr(mw['u'], 1)}, p-değeri {tr(mw['p_degeri'], 4)}, üstünlük olasılığı %{tr(100 * mw['ustunluk'], 1)}, r etki büyüklüğü {tr(mw['etki_r'], 2)}")
    add(f"Anlamlılık düzeyi {tr(args.alpha, 2)}")
    stats.update(p={'welch-t': p_t, 'mann-whitney': mw['p_degeri']}, t_ok=all(normal.values()) and min(sa['n'], sb['n']) >= 8,
                 higher=a if sa['medyan'] > sb['medyan'] else b if sb['medyan'] > sa['medyan'] else '')
    return facts, stats


def validator(facts, stats, alpha):
    ids = {f['id'] for f in facts}
    a, b = stats['gruplar']
    values = allowed_numbers(facts, [a, b])

    def validate(obj):
        problems = []
        test = str(obj.get('test', '')).strip().lower()
        result = str(obj.get('sonuc', '')).strip().lower()
        higher = str(obj.get('yuksek_grup', '')).strip()
        if test not in TESTS:
            problems.append('test welch-t veya mann-whitney olmalı')
        elif test == 'welch-t' and not stats['t_ok']:
            problems.append('gruplardan en az biri normal değil ya da 8’den az ölçüm var; t-testi seçilemez, mann-whitney kullan')
        if result not in RESULTS:
            problems.append('sonuc "anlamlı fark var" veya "anlamlı fark yok" olmalı')
        elif test in TESTS:
            sig = stats['p'][test] < alpha
            if sig != (result == 'anlamlı fark var'):
                problems.append(f'{test} p-değeri {tr(stats["p"][test], 4)}, anlamlılık düzeyi {tr(alpha, 2)}: sonuc “{RESULTS[0] if sig else RESULTS[1]}” olmalı')
            if sig and higher != stats['higher']:
                problems.append(f'yuksek_grup medyanı yüksek olan grup olmalı: {stats["higher"]}')
        refs = [str(x).strip().upper() for x in obj.get('kanit') or []]
        if not refs or any(r not in ids for r in refs):
            problems.append('kanit geçerli olgu numaraları içermeli')
        text = ' '.join([str(obj.get('yorum', ''))] + [str(x) for x in obj.get('uyarilar') or []])
        bad = unknown_numbers(text, values, [a, b])
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)))
        if not str(obj.get('yorum', '')).strip():
            problems.append('"yorum" boş')
        if problems:
            raise ValueError('; '.join(problems))
        return {'test': test, 'sonuc': result, 'yuksek_grup': higher if result == RESULTS[0] else '', 'yorum': str(obj['yorum']).strip(),
                'uyarilar': [str(x).strip() for x in obj.get('uyarilar') or [] if str(x).strip()], 'kanit': refs}
    return validate


SYSTEM = (llm.SAFETY + ' Sen deney tasarımı ve istatistik bilen bir veri bilimcisisin. Kodla hesaplanmış numaralı olgulardan iki grubu karşılaştır: '
          'uygun testi seç (gruplardan biri normal değilse veya 8’den az ölçüm varsa mann-whitney, ikisi de normalse welch-t), sonucu seçtiğin testin p-değerine göre ver, '
          'fark anlamlıysa medyanı yüksek grubu yaz ve farkın pratik anlamını (etki büyüklüğü) açıkla. İstatistiksel anlamlılığı iş açısından önemle karıştırma; '
          'örneklem, aykırı değer ve çoklu karşılaştırma risklerini uyarilar’a yaz. Olgularda olmayan sayı kullanma. '
          'Yanıt şeması: {"test": "mann-whitney", "sonuc": "anlamlı fark var", "yuksek_grup": "A", "yorum": "...", "uyarilar": ["..."], "kanit": ["F1", "F4"]}')


def main():
    p = argparse.ArgumentParser(description='İki grubu uygun testle karşılaştırıp yorumlayan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'deney_sonuclari.csv')))
    p.add_argument('--group', default='varyant')
    p.add_argument('--value', default='gorev_suresi_sn')
    p.add_argument('--a', default='', help='Birinci grup (boşsa alfabetik ilk)')
    p.add_argument('--b', default='', help='İkinci grup')
    p.add_argument('--alpha', type=float, default=0.05)
    p.add_argument('--context', default='Yeni ödeme ekranının (B) görev süresine etkisi; düşük süre iyidir.', help='Deneyin amacı (modele gider)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    facts, stats = build_facts(columns, rows, args)
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu'], facts)
    user = f'Deneyin amacı: {args.context}\nÖlçüm: {args.value}\n\nOlgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, stats, args.alpha))
    if result is None:
        return
    write_json(out / 'deney-raporu.json', {**result, 'p_degerleri': stats['p'], 'olgular': facts})
    a, b = stats['gruplar']
    md = [f'# Deney raporu: {a} ve {b} ({args.value})', '', f"**Test:** {result['test']} · **Sonuç:** {result['sonuc']}"
          + (f" · **Yüksek grup:** {result['yuksek_grup']}" if result['yuksek_grup'] else ''), '', result['yorum'], '']
    if result['uyarilar']:
        md += ['## Uyarılar', ''] + [f'- {u}' for u in result['uyarilar']] + ['']
    md += ['## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Test seçimi ve karar kodla denetlendi (normallik → test türü, p-değeri → sonuç, medyan → yön); kanıt: {', '.join(result['kanit'])}._"]
    write_text(out / 'deney-raporu.md', '\n'.join(md))
    print(f"[OK] {result['test']}: {result['sonuc']}. Çıktı: {out / 'deney-raporu.md'}")


if __name__ == '__main__':
    llm.run(main)
