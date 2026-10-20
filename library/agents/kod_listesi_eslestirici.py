"""Kod listesi eşleyici (AI agent): eski sistemdeki serbest ya da tutarsız kod değerlerini (ödeme tipi “KK”, “K.Kartı”, “Kredi Kartı”;
müşteri tipi, iade nedeni, kanal) hedef sistemin kod listesine eşler. Kodla birebir (büyük/küçük harf, Türkçe karakter ve noktalama farkı
yok sayılarak) kod ya da ad eşleşmesi olanlar kuralla eşlenir; yalnızca kalanlar modele gider (değer ve satır sayısı). Her eşleme kodla
denetlenir: hedef kod listede olmalı, her değer cevaplanmalı, eşlenemeyen değerin gerekçesi olmalı, aynı yazıma indirgenen değerler aynı
koda gitmeli. Çıktı: kod_esleme.csv, kod_esleme.sql (CASE ifadesi), kod-esleme.md."""
import argparse, re
from collections import Counter
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table


def key(v):
    return ''.join(re.split(r'[^0-9a-z]+', tr.fold(v)))


def validator(items, targets):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('eslemeler') or []:
            v, code, why = str(e.get('deger', '')).strip(), str(e.get('kod', '')).strip(), str(e.get('gerekce', '')).strip()
            seen.add(v)
            if v not in items:
                problems.append(f'listede olmayan değer: {v or "?"}')
            elif code.lower() in ('', 'eşlenemez', 'eslenemez'):
                if not why:
                    problems.append(f'“{v}” eşlenemez denmiş ama gerekçe yok')
                out[v] = ('', why)
            elif code not in targets:
                problems.append(f'“{v}”: {code} hedef kod listesinde yok')
            else:
                out[v] = (code, why)
        missing = [v for v in items if v not in seen]
        if missing:
            problems.append('cevapsız değer: ' + ', '.join(missing[:15]))
        groups = {}
        for v, (code, _) in out.items():
            groups.setdefault(key(v), set()).add(code)
        split = [k for k, codes in groups.items() if len(codes) > 1]
        if split:
            problems.append('aynı yazıma indirgenen değerler farklı koda gitmiş: ' + ', '.join(f'{[v for v in out if key(v) == k]}' for k in split))
        if problems:
            raise ValueError('; '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen ana veri yönetiminden sorumlu bir uzmansın. Eski sistemdeki değerleri hedef kod listesine eşle; kısaltmaları ve yazım farklarını '
          'çöz. Anlamı birden fazla koda uyabilen ya da hiçbirine uymayan değeri “eşlenemez” bırak ve nedenini yaz; “DIGER” gibi genel bir kod varsa onu '
          'yalnızca anlamı gerçekten oraya düşüyorsa kullan. Yanıt şeması: {"eslemeler": [{"deger": "K.Kartı", "kod": "KREDI_KARTI", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Eski sistem kod değerlerini hedef kod listesine eşleyen agent; birebir eşleşmeler kural, kalanlar model.')
    p.add_argument('--input', default=str(here('ornek', 'eski_odeme_tipleri.csv')), help='Eski değerlerin geçtiği tablo')
    p.add_argument('--column', default='odeme_tipi')
    p.add_argument('--codes', default=str(here('ornek', 'hedef_odeme_kodlari.csv')), help='kod, ad[, aciklama]')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.column not in columns:
        raise ValueError(f'{args.column} sütunu yok. Sütunlar: {", ".join(columns)}')
    cc, crows = tabular.read(args.codes)
    if 'kod' not in cc or 'ad' not in cc:
        raise ValueError('Kod listesinde kod ve ad sütunları gerekli.')
    targets = {r['kod'].strip(): r for r in crows}
    by_key = {}
    for code, r in targets.items():
        by_key.setdefault(key(code), code)
        by_key.setdefault(key(r['ad']), code)
    counts = Counter(r[args.column].strip() for r in rows if r[args.column].strip())
    rule = {v: by_key[key(v)] for v in counts if key(v) in by_key}
    items = [v for v in counts if v not in rule]
    out = output_dir(args.output)
    result, model = {}, 'kullanılmadı'
    if items:
        user = ('Hedef kod listesi:\n' + '\n'.join(f'- {c}: {r["ad"]}' + (f' — {r["aciklama"]}' if r.get('aciklama') else '') for c, r in targets.items())
                + '\n\nKuralla eşlenenler (değiştirme): ' + (', '.join(f'{v}={c}' for v, c in rule.items()) or 'yok')
                + '\n\nEşlenecek değerler (satır sayısı):\n' + '\n'.join(f'- {v} ({counts[v]})' for v in items))
        result, meta = llm.ask(args, out, SYSTEM, user, validator(items, targets))
        if result is None:
            return
        model = meta['model']
    table = [{'deger': v, 'kod': rule[v], 'kaynak': 'kural', 'satir': counts[v], 'gerekce': ''} for v in rule]
    table += [{'deger': v, 'kod': result[v][0], 'kaynak': 'model' if result[v][0] else 'eşlenemedi', 'satir': counts[v], 'gerekce': result[v][1]} for v in items]
    tabular.write_csv(out / 'kod_esleme.csv', ['deger', 'kod', 'kaynak', 'satir', 'gerekce'], table)
    q = lambda v: "'" + v.replace("'", "''") + "'"
    case = ['CASE TRIM(' + args.column + ')'] + [f"    WHEN {q(t['deger'])} THEN {q(t['kod'])}" for t in table if t['kod']] + ["    ELSE NULL  -- eşlenemeyenler: elle karar verin", 'END AS ' + args.column + '_kodu']
    write_text(out / 'kod_esleme.sql', '\n'.join(case) + '\n')
    left = [t for t in table if not t['kod']]
    rows_done = sum(t['satir'] for t in table if t['kod'])
    md = ['# Kod listesi eşleme', '', f'{len(counts)} farklı değer ({sum(counts.values())} satır): {len(rule)} kuralla, {len(items) - len(left)} modelle eşlendi, {len(left)} eşlenemedi; '
          f'satırların %{rows_done / sum(counts.values()) * 100:.1f}’i bir koda bağlandı.', '',
          md_table(['Değer', 'Kod', 'Kaynak', 'Satır', 'Gerekçe'], [[t['deger'], t['kod'] or '—', t['kaynak'], t['satir'], t['gerekce']] for t in table]), '',
          f'_Model: {model} · Her kod hedef listeyle, eşleme tutarlılığı (aynı yazım → aynı kod) kodla denetlendi._']
    write_text(out / 'kod-esleme.md', '\n'.join(md))
    print(f'[OK] {len(counts)} değer; {len(left)} eşlenemedi. Çıktı: {out / "kod-esleme.md"}')


if __name__ == '__main__':
    llm.run(main)
