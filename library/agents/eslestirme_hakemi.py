"""Ana veri eşleştirme hakemi (AI agent): firma/müşteri listesinde aday mükerrer çiftleri yerelde bulur (ünvan benzerliği, ortak VKN/
telefon) ve modelden her çift için karar ister: aynı, farklı veya emin değil. Modele ünvan, il ve adres metni ile hangi
tanımlayıcıların ortak olduğu gider; VKN ve telefonun kendisi gönderilmez. Her çiftin tam bir kez karara bağlandığı yerelde
denetlenir; “aynı” kararlarından birleşme grupları kodla (birleşim-bul) kurulur ve aynı VKN’ye sahip olup “farklı” denen çiftler
ayrıca işaretlenir. Hiçbir sistemde birleştirme yapılmaz. Çıktı: eslesme-kararlari.csv, gruplar.csv, eslesme.md."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from anaveri import clusters

DECISIONS = ('aynı', 'farklı', 'emin değil')


def candidates(columns, rows, name, id_col, keys, threshold):
    for c in (name, id_col, *keys):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    _, pairs = clusters(rows, name, id_col, keys, threshold)
    by_id = {r[id_col]: r for r in rows}
    out = []
    for i, pr in enumerate(pairs, start=1):
        a, b = by_id[pr['kayit_1']], by_id[pr['kayit_2']]
        diff = [k for k in keys if re.sub(r'\D', '', a[k]) and re.sub(r'\D', '', b[k]) and re.sub(r'\D', '', a[k]) != re.sub(r'\D', '', b[k])]
        out.append({'id': f'C{i}', 'kayit_1': pr['kayit_1'], 'kayit_2': pr['kayit_2'], 'benzerlik': pr['benzerlik'], 'ortak': pr['ortak_alan'], 'farkli': ', '.join(diff), 'a': a, 'b': b})
    return out


def validator(pairs):
    ids = [p['id'] for p in pairs]

    def validate(obj):
        items, problems, by_id = obj.get('kararlar'), [], {}
        if not isinstance(items, list) or not items:
            raise ValueError('"kararlar" listesi boş.')
        for it in items:
            if not isinstance(it, dict):
                continue
            cid = str(it.get('id', '')).strip().upper()
            if cid not in ids:
                problems.append(f'olmayan çift: {cid or "?"}')
            elif cid in by_id:
                problems.append(f'{cid} iki kez karara bağlandı')
            else:
                by_id[cid] = it
        missing = [i for i in ids if i not in by_id]
        if missing:
            problems.append('karara bağlanmayan çift: ' + ', '.join(missing))
        out = []
        for p in pairs:
            it = by_id.get(p['id'])
            if not it:
                continue
            d = str(it.get('karar', '')).strip().lower().replace('ayni', 'aynı').replace('farkli', 'farklı').replace('emin degil', 'emin değil')
            if d not in DECISIONS:
                problems.append(f'{p["id"]} karar şunlardan biri olmalı: {", ".join(DECISIONS)}')
                continue
            if not str(it.get('gerekce', '')).strip():
                problems.append(f'{p["id"]} için gerekçe boş')
            note = 'ortak tanımlayıcı var ama "farklı" dendi; insan incelemesi gerekli' if d == 'farklı' and p['ortak'] else \
                   'tanımlayıcılar farklı ama "aynı" dendi; insan incelemesi gerekli' if d == 'aynı' and p['farkli'] else ''
            out.append({'id': p['id'], 'kayit_1': p['kayit_1'], 'kayit_2': p['kayit_2'], 'benzerlik': p['benzerlik'], 'ortak_tanimlayici': p['ortak'],
                        'farkli_tanimlayici': p['farkli'], 'karar': d, 'gerekce': str(it.get('gerekce', '')).strip(), 'kod_notu': note})
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


def groups(decisions, all_ids):
    parent = {i: i for i in all_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for d in decisions:
        if d['karar'] == 'aynı':
            parent[find(d['kayit_1'])] = find(d['kayit_2'])
    out = {}
    for i in all_ids:
        out.setdefault(find(i), []).append(i)
    return [sorted(m) for m in out.values() if len(m) > 1]


SYSTEM = (llm.SAFETY + ' Sen ana veri yönetimi (MDM) uzmanısın. Aday mükerrer firma kayıt çiftlerinin aynı tüzel kişiyi gösterip göstermediğine karar ver. '
          'Şirket türü kısaltmaları (A.Ş., Ltd. Şti.), “San. ve Tic.” gibi dolgu sözcükler, Türkçe karakter ve büyük/küçük harf farkları tek başına farklılık değildir; '
          'farklı il, farklı faaliyet adı veya farklı VKN güçlü farklılık işaretidir. Şube/merkez ayrımı gibi belirsiz durumlarda "emin değil" de. '
          f'karar: {", ".join(DECISIONS)}. Yanıt şeması: {{"kararlar": [{{"id": "C1", "karar": "aynı", "gerekce": "..."}}], "notlar": ["..."]}}')


def main():
    p = argparse.ArgumentParser(description='Aday mükerrer firma kayıtlarına karar veren, grupları kodla kuran agent.')
    p.add_argument('--input', default=str(here('ornek', 'cari_adaylar.csv')))
    p.add_argument('--name', default='unvan')
    p.add_argument('--id', default='cari_kodu')
    p.add_argument('--keys', default='vkn,telefon', help='Ortaklığı kontrol edilecek tanımlayıcılar (değerleri gönderilmez)')
    p.add_argument('--show', default='il,adres', help='Modele gösterilecek ek metin sütunları')
    p.add_argument('--threshold', type=float, default=0.7, help='Aday çift için ünvan benzerlik eşiği')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    keys = [k.strip() for k in args.keys.split(',') if k.strip()]
    show = [c.strip() for c in args.show.split(',') if c.strip() in columns]
    pairs = candidates(columns, rows, args.name, args.id, keys, args.threshold)
    if not pairs:
        print('[OK] Aday mükerrer çift bulunamadı; model çağrılmadı.')
        return
    out = output_dir(args.output)
    lines = []
    for pr in pairs:
        side = lambda r: f"{r[args.name]}" + ''.join(f' | {c}: {r[c]}' for c in show)
        lines.append(f"{pr['id']} (benzerlik {pr['benzerlik']}; ortak: {pr['ortak'] or '—'}; farklı: {pr['farkli'] or '—'})\n  A: {side(pr['a'])}\n  B: {side(pr['b'])}")
    user = 'Aday çiftler:\n' + '\n'.join(lines)
    decisions, meta = llm.ask(args, out, SYSTEM, user, validator(pairs))
    if decisions is None:
        return
    grp = groups(decisions, [r[args.id] for r in rows])
    tabular.write_csv(out / 'eslesme-kararlari.csv', list(decisions[0]), decisions)
    tabular.write_csv(out / 'gruplar.csv', ['grup', 'kayit'], [{'grup': f'G{i}', 'kayit': k} for i, g in enumerate(grp, start=1) for k in g])
    write_json(out / 'eslesme.json', {'kararlar': decisions, 'gruplar': grp})
    md = ['# Ana veri eşleştirme kararları', '', f"{len(decisions)} aday çift; {sum(d['karar'] == 'aynı' for d in decisions)} aynı, "
          f"{sum(d['karar'] == 'farklı' for d in decisions)} farklı, {sum(d['karar'] == 'emin değil' for d in decisions)} emin değil. {len(grp)} birleşme grubu.", '',
          md_table(['Çift', 'Kayıtlar', 'Benzerlik', 'Ortak', 'Karar', 'Gerekçe', 'Not'],
                   [[d['id'], f"{d['kayit_1']} ↔ {d['kayit_2']}", d['benzerlik'], d['ortak_tanimlayici'] or '—', d['karar'], d['gerekce'], d['kod_notu'] or ''] for d in decisions]),
          '', '## Birleşme grupları', ''] + [f"- G{i}: {', '.join(g)}" for i, g in enumerate(grp, start=1)]
    md += ['', f"_Model: {meta['model']} · Birleştirme yapılmadı; “emin değil” ve kod notu olan çiftleri veri sorumlusu inceler. Hayatta kalan kayıt için golden-record bloğunu kullanın._"]
    write_text(out / 'eslesme.md', '\n'.join(md))
    flagged = sum(bool(d['kod_notu']) for d in decisions)
    print(f"[{'!' if flagged else 'OK'}] {len(decisions)} çift karara bağlandı, {len(grp)} grup; {flagged} karar insan incelemesine işaretlendi. Çıktı: {out / 'eslesme.md'}")


if __name__ == '__main__':
    llm.run(main)
