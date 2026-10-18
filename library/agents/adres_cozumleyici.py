"""Adres çözümleyici (AI agent): serbest yazılmış Türkçe adresleri mahalle, cadde/sokak, kapı no, kat, daire, ilçe, il ve posta koduna
ayırır. Önce kural tabanlı ayrıştırıcı (adres-ayristir bloğu) çalışır; yalnızca kuralın tamamlayamadığı adresler modele gider. Modelin
verdiği her parça adres metninde gerçekten geçmelidir (uydurma mahalle/sokak reddedilir); il 81 il listesinden olmalı, posta kodu
metindeki 5 haneli kod olmalı ve ilk iki hanesi ilin plaka koduyla tutmalıdır. Metinde yazmayan il yalnızca ilçe ya da posta kodundan
çıkarılabilir ve “çıkarım” olarak işaretlenir.
Adresler kişisel veri olabilir: modele yalnızca adres sütunu gider, diğer sütunlar (ad, telefon, VKN) gönderilmez.
Çıktı: adresler_ayrisik.csv, adres-cozumleme.md."""
import argparse, re
import llm
import tabular
import turkiye as tr
from anaveri import parse_address
from common import here, output_dir, write_text, md_table

PARTS = ['mahalle', 'cadde_sokak', 'no', 'kat', 'daire', 'ilce', 'il', 'posta_kodu']
TYPE_WORDS = set('mahallesi mahalle mah mh caddesi cadde cad cd sokak sokağı sokagi sok sk bulvarı bulvari blv bulvar meydanı meydani '
                 'no numara kat daire d k'.split())


def tokens(text):
    return [w for w in re.split(r'[^0-9a-z]+', tr.fold(text)) if w and w not in TYPE_WORDS]


def complete(p):
    """All core parts present, and the neighbourhood does not swallow the district or province (a common rule-parser miss)."""
    if not all(p.get(k) for k in ('mahalle', 'no', 'ilce', 'il')):
        return False
    hood = set(tokens(p['mahalle']))
    return not (hood & set(tokens(p['il'])) or hood & set(tokens(p['ilce'])))


def validator(texts):
    def validate(obj):
        problems, out = [], {}
        for a in obj.get('adresler') or []:
            if not isinstance(a, dict):
                continue
            rid = str(a.get('id', '')).strip()
            if rid not in texts:
                problems.append(f'listede olmayan id: {rid or "?"}')
                continue
            text = texts[rid]
            have = set(tokens(text))
            got, why = {}, []
            for k in PARTS:
                v = re.sub(r'\s+', ' ', str(a.get(k) or '')).strip()
                got[k] = v
            for k in ('mahalle', 'cadde_sokak', 'no', 'kat', 'daire', 'ilce'):
                missing = [w for w in tokens(got[k]) if w not in have]
                if got[k] and missing:
                    why.append(f'{k} “{got[k]}” metinde geçmiyor ({", ".join(missing)})')
            source = ''
            if got['il']:
                name, how = tr.il_bul(got['il'])
                if not name or how not in ('tam', 'yazım', 'eş ad'):
                    why.append(f'il “{got["il"]}” 81 il listesinde yok')
                else:
                    got['il'] = name
                    if tr.fold(name) not in have and not any(tr.il_bul(w)[0] == name for w in re.split(r'[\s,/]+', text)):
                        source = 'çıkarım'
                        if not (got['ilce'] or got['posta_kodu']):
                            why.append(f'il {name} metinde yok; ilçe ya da posta kodu olmadan çıkarılamaz')
            if got['posta_kodu']:
                if not re.fullmatch(r'\d{5}', got['posta_kodu']) or got['posta_kodu'] not in re.findall(r'\b\d{5}\b', text):
                    why.append(f'posta kodu “{got["posta_kodu"]}” metindeki 5 haneli bir kod değil')
                elif got['il'] and tr.PLAKA.get(int(got['posta_kodu'][:2])) != got['il']:
                    why.append(f'posta kodu {got["posta_kodu"]} {tr.PLAKA.get(int(got["posta_kodu"][:2]), "?")} iline ait, il {got["il"]} verilmiş')
            if why:
                problems.append(f'{rid}: ' + '; '.join(why))
            out[rid] = (got, source)
        missing = [i for i in texts if i not in out]
        if missing:
            problems.append('cevapsız adres: ' + ', '.join(missing[:20]))
        if problems:
            raise ValueError(' | '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen Türkiye adres yapısını (mahalle, cadde/sokak/bulvar, kapı no, kat, daire, ilçe, il, posta kodu) bilen bir ana veri uzmanısın. '
          'Her adresi parçalarına ayır. Parçaları adres metninden al; metinde olmayan mahalle, sokak ya da numara uydurma, bulamadığını boş bırak. '
          'Kısaltmaları açabilirsin (Mah. → Mahallesi, Cad. → Caddesi, G.M.K. gibi metinde olmayan açılımları yazma). İl metinde yoksa yalnızca '
          'ilçeden ya da posta kodundan kesin biliniyorsa yaz. Yanıt şeması: {"adresler": [{"id": "3", "mahalle": "...", "cadde_sokak": "...", "no": "", '
          '"kat": "", "daire": "", "ilce": "", "il": "", "posta_kodu": ""}]}')


def main():
    p = argparse.ArgumentParser(description='Kuralın çözemediği Türkçe adresleri modelle parçalarına ayıran agent (yalnızca adres sütunu gönderilir).')
    p.add_argument('--input', default=str(here('ornek', 'musteri_adresleri.csv')))
    p.add_argument('--column', default='adres')
    p.add_argument('--id-column', default='', help='Satır kimliği sütunu (yoksa satır numarası)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.column not in columns:
        raise ValueError(f'{args.column} sütunu yok. Sütunlar: {", ".join(columns)}')
    ids = [r[args.id_column].strip() if args.id_column else str(i) for i, r in enumerate(rows, 1)]
    if len(set(ids)) != len(ids):
        raise ValueError('Satır kimlikleri tekil olmalı.')
    parsed, todo = {}, {}
    for rid, r in zip(ids, rows):
        text = re.sub(r'\s+', ' ', r[args.column]).strip()
        rule = parse_address(text)
        codes = re.findall(r'\b\d{5}\b', text)
        rule['posta_kodu'] = codes[-1] if codes else ''
        parsed[rid] = ({k: rule.get(k, '') for k in PARTS}, 'kural')
        if text and not complete(rule):
            todo[rid] = text
    out = output_dir(args.output)
    model = 'kullanılmadı'
    if todo:
        user = ('Ayrıştırılacak adresler (id: metin). Kural tabanlı ayrıştırıcı bunları tamamlayamadı:\n'
                + '\n'.join(f'{i}: {t}' for i, t in todo.items()))
        result, meta = llm.ask(args, out, SYSTEM, user, validator(todo))
        if result is None:
            return
        model = meta['model']
        for rid, (got, source) in result.items():
            parsed[rid] = (got, 'model' + (' (il çıkarım)' if source else ''))
    cols = columns + [f'adr_{k}' for k in PARTS] + ['adr_kaynak', 'adr_tam']
    table = []
    for rid, r in zip(ids, rows):
        got, source = parsed[rid]
        table.append({**r, **{f'adr_{k}': got[k] for k in PARTS}, 'adr_kaynak': source, 'adr_tam': 'evet' if complete(got) else 'hayır'})
    tabular.write_csv(out / 'adresler_ayrisik.csv', cols, table)
    before = sum(1 for rid in ids if rid not in todo)
    after = sum(1 for t in table if t['adr_tam'] == 'evet')
    fill = [[k, sum(1 for t in table if t[f'adr_{k}'])] for k in PARTS]
    md = ['# Adres çözümleme', '', f'{len(rows)} adres: kural {before} adresi tamamladı, {len(todo)} adres modele gitti. Tam adres (mahalle, no, ilçe, il) '
          f'{before} → {after}.', '', md_table(['Parça', 'Dolu'], fill), '',
          md_table(['Kimlik', 'Adres', 'Mahalle', 'Cadde/sokak', 'No', 'İlçe', 'İl', 'Posta kodu', 'Kaynak'],
                   [[rid, r[args.column], t['adr_mahalle'], t['adr_cadde_sokak'], t['adr_no'], t['adr_ilce'], t['adr_il'], t['adr_posta_kodu'], t['adr_kaynak']]
                    for rid, r, t in zip(ids, rows, table) if rid in todo]) if todo else '', '',
          f'_Model: {model} · Modelin parçaları adres metniyle, il 81 il listesiyle, posta kodu il plaka koduyla karşılaştırıldı. UAVT/adres kodu doğrulaması yapılmadı; '
          'ilçenin ile ait olduğu denetlenmedi._']
    write_text(out / 'adres-cozumleme.md', '\n'.join(md))
    print(f'[OK] {len(rows)} adres; tam adres {before} → {after}. Çıktı: {out / "adres-cozumleme.md"}')


if __name__ == '__main__':
    llm.run(main)
