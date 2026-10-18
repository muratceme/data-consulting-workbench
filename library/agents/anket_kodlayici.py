"""Açık uçlu anket kodlayıcı (AI agent): müşteri/çalışan anketindeki serbest metin yanıtlarından bir tema listesi (kod çerçevesi)
çıkarır ve her yanıtı bir veya birkaç temaya, bir de duygu etiketine atar. Kişisel veriler (e-posta, telefon, TCKN, IBAN) gönderilmeden
maskelenir. Model yalnızca temaları ve atamaları verir; sayımlar, yüzdeler, segment kırılımları ve örnek alıntılar yerelde kodla
hesaplanır. Atanmamış ya da bilinmeyen yanıt, listede olmayan tema, iki kez atanan yanıt, tanımsız tema, 2’den az yanıtı olan
(birleştirilmesi gereken) tema ve izin verilen sayıdan fazla tema reddedilir. Tema adı yanıtlarda geçmeyen bir “alıntı” uydurulamaz:
alıntılar yanıtların kendisinden seçilir.
Çıktı: kodlanmis_yanitlar.csv, tema_ozeti.csv, anket-temalari.md."""
import argparse
from collections import Counter, defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table, mask_text

SENTIMENT = ('olumlu', 'olumsuz', 'nötr', 'karışık')


def validator(ids, max_themes, min_size):
    def validate(obj):
        themes = obj.get('temalar')
        if not isinstance(themes, list) or not themes:
            raise ValueError('"temalar" boş.')
        codes, problems = {}, []
        for t in themes:
            if not isinstance(t, dict):
                continue
            code, name, desc = str(t.get('kod', '')).strip().upper(), str(t.get('ad', '')).strip(), str(t.get('tanim', '')).strip()
            if not code or code in codes:
                problems.append(f'tema kodu “{code}” boş ya da tekrar ediyor')
            elif not name or not desc:
                problems.append(f'{code}: ad ve tanım dolu olmalı')
            else:
                codes[code] = (name, desc)
        if len(codes) > max_themes:
            problems.append(f'{len(codes)} tema var; en fazla {max_themes} olmalı (benzerleri birleştir)')
        if problems:
            raise ValueError('; '.join(problems[:10]))
        out, seen_twice, unknown, bad_codes, bad_sent = {}, [], [], [], []
        for a in obj.get('atamalar') or []:
            if not isinstance(a, dict):
                continue
            rid = str(a.get('id', '')).strip()
            if rid not in ids:
                unknown.append(rid or '?')
                continue
            if rid in out:
                seen_twice.append(rid)
                continue
            got = a.get('temalar') or []
            got = [str(c).strip().upper() for c in (got if isinstance(got, list) else [got])]
            wrong = [c for c in got if c not in codes]
            if not got or wrong:
                bad_codes.append(f'{rid}: {", ".join(wrong) or "tema yok"}')
                continue
            sent = str(a.get('duygu', '')).strip().lower().replace('notr', 'nötr').replace('karisik', 'karışık')
            if sent not in SENTIMENT:
                bad_sent.append(rid)
                continue
            out[rid] = (list(dict.fromkeys(got)), sent)
        missing = [i for i in ids if i not in out and i not in [b.split(':')[0] for b in bad_codes] and i not in bad_sent]
        for label, items in (('listede olmayan yanıt id', unknown), ('iki kez atanan yanıt', seen_twice), ('geçersiz tema kodu', bad_codes),
                             (f'duygu {"/".join(SENTIMENT)} olmalı', bad_sent), ('atanmamış yanıt', missing)):
            if items:
                problems.append(f'{label}: {", ".join(items[:15])}' + (f' (+{len(items) - 15})' if len(items) > 15 else ''))
        if problems:
            raise ValueError('; '.join(problems))
        size = Counter(c for got, _ in out.values() for c in got)
        small = [c for c in codes if size[c] < min_size]
        if small:
            raise ValueError(f'{min_size} yanıttan az atanan tema: {", ".join(f"{c} ({size[c]})" for c in small)}; başka bir temayla birleştir ya da kaldır')
        return codes, out
    return validate


SYSTEM = (llm.SAFETY + ' Sen anket analizi yapan bir araştırmacısın. Açık uçlu yanıtlardan birbirini dışlayan, anlamlı ve yanıtlarla desteklenen bir tema '
          'listesi (kod çerçevesi) çıkar; her yanıtı bir veya birkaç temaya ata ve duygusunu (olumlu, olumsuz, nötr, karışık) belirle. Konu dışı ya da '
          'anlamsız yanıtlar için "Diğer / konu dışı" teması aç. Tema sayısını küçük tut, benzerleri birleştir. Her yanıt id’si tam bir kez atanmalı. '
          'Yanıt şeması: {"temalar": [{"kod": "T1", "ad": "Teslimat gecikmesi", "tanim": "..."}], '
          '"atamalar": [{"id": "12", "temalar": ["T1"], "duygu": "olumsuz"}]}')


def main():
    p = argparse.ArgumentParser(description='Açık uçlu anket yanıtlarını temalara ve duyguya kodlayan agent (kişisel veriler maskelenir).')
    p.add_argument('--input', default=str(here('ornek', 'anket_yanitlari.csv')))
    p.add_argument('--id-column', default='yanit_id')
    p.add_argument('--text-column', default='yanit')
    p.add_argument('--segment', default=None, help='Kırılım sütunu (ör. kanal, bölge; varsayılan: varsa kanal); yalnızca yerelde kullanılır')
    p.add_argument('--question', default='Hizmetimizle ilgili en çok neyi geliştirmemizi istersiniz?')
    p.add_argument('--max-themes', type=int, default=8)
    p.add_argument('--min-size', type=int, default=2)
    p.add_argument('--max-rows', type=int, default=400)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.segment is None:
        args.segment = 'kanal' if 'kanal' in columns else ''
    for c in [args.id_column, args.text_column] + ([args.segment] if args.segment else []):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    rows = [r for r in rows if r[args.text_column].strip()]
    if len(rows) > args.max_rows:
        raise ValueError(f'{len(rows)} yanıt var; tek istekte en fazla {args.max_rows}. Dosyayı bölün ya da --max-rows verin.')
    ids = [r[args.id_column].strip() for r in rows]
    if len(set(ids)) != len(ids) or '' in ids:
        raise ValueError(f'{args.id_column} sütunu boş olmayan ve tekil olmalı.')
    by_id = {r[args.id_column].strip(): r for r in rows}
    out = output_dir(args.output)
    user = (f'Soru: {args.question}\n\n{len(rows)} yanıt (id: metin):\n'
            + '\n'.join(f'{i}: {" ".join(by_id[i][args.text_column].split())[:600]}' for i in ids))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(ids, args.max_themes, args.min_size), max_tokens=16000)
    if result is None:
        return
    codes, assigned = result
    coded = [{args.id_column: i, 'temalar': ';'.join(assigned[i][0]), 'tema_adlari': '; '.join(codes[c][0] for c in assigned[i][0]),
              'duygu': assigned[i][1], **({'segment': by_id[i][args.segment]} if args.segment else {}), 'yanit': by_id[i][args.text_column]} for i in ids]
    tabular.write_csv(out / 'kodlanmis_yanitlar.csv', list(coded[0]), coded)
    n = len(ids)
    members = defaultdict(list)
    for i in ids:
        for c in assigned[i][0]:
            members[c].append(i)
    summary = []
    for c, (name, desc) in sorted(codes.items(), key=lambda kv: -len(members[kv[0]])):
        sent = Counter(assigned[i][1] for i in members[c])
        # quote: the shortest answers of at least four words, taken from the data itself and masked
        quotes = sorted(members[c], key=lambda i: (len(by_id[i][args.text_column].split()) < 4, len(by_id[i][args.text_column])))[:2]
        summary.append({'kod': c, 'tema': name, 'tanim': desc, 'yanit': len(members[c]), 'oran': round(len(members[c]) / n * 100, 1),
                        'olumsuz_orani': round(sent['olumsuz'] / len(members[c]) * 100, 1),
                        'ornek_1': mask_text(by_id[quotes[0]][args.text_column])[0], 'ornek_2': mask_text(by_id[quotes[1]][args.text_column])[0] if len(quotes) > 1 else ''})
    tabular.write_csv(out / 'tema_ozeti.csv', list(summary[0]), summary)
    overall = Counter(a[1] for a in assigned.values())
    multi = sum(1 for a in assigned.values() if len(a[0]) > 1)
    md = ['# Açık uçlu yanıt temaları', '', f'Soru: _{args.question}_', '',
          f'{n} yanıt {len(codes)} temaya kodlandı; {multi} yanıt birden fazla temaya girdi (yüzdelerin toplamı bu yüzden 100’ü geçebilir). '
          f'Duygu: ' + ', '.join(f'{k} {overall[k]} (%{overall[k] / n * 100:.0f})' for k in SENTIMENT if overall[k]) + '.', '',
          md_table(['Kod', 'Tema', 'Yanıt', '%', 'Olumsuz %', 'Örnek'], [[s['kod'], s['tema'], s['yanit'], s['oran'], s['olumsuz_orani'], s['ornek_1']] for s in summary]), '']
    if args.segment:
        segs = sorted({by_id[i][args.segment] for i in ids})
        seg_n = Counter(by_id[i][args.segment] for i in ids)
        md += [f'## {args.segment} kırılımı (yanıtların %’si)', '',
               md_table(['Tema'] + [f'{s} (n={seg_n[s]})' for s in segs],
                        [[codes[c][0]] + [f'{sum(1 for i in members[c] if by_id[i][args.segment] == s) / seg_n[s] * 100:.0f}' for s in segs] for c in [x['kod'] for x in summary]]), '']
    md += ['## Tema tanımları', ''] + [f'- **{s["kod"]} {s["tema"]}**: {s["tanim"]}' for s in summary]
    md += ['', f'_Model: {meta["model"]} · Sayımlar, yüzdeler ve örnek alıntılar kodla hesaplandı; alıntılar yanıtların kendisinden seçildi. '
           'Kodlamanın bir kısmını elle kontrol edin (ör. her temadan 5 yanıt); model ataması tutarlılık için bir insan kodlayıcıyla karşılaştırılmadı._']
    write_text(out / 'anket-temalari.md', '\n'.join(md))
    print(f'[OK] {n} yanıt, {len(codes)} tema. Çıktı: {out / "anket-temalari.md"}')


if __name__ == '__main__':
    llm.run(main)
