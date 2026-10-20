"""SCD tipi önerici (AI agent): bir boyut tablosunun (müşteri, ürün, personel) tarihli anlık görüntülerinden her kolon için yavaş değişen boyut
tipini seçer: 0 (hiç değişmez), 1 (üzerine yaz), 2 (tarihçe tut). Kolon başına değişim sıklığı kodla ölçülür; modele yalnızca bu ölçümler
ve kolon adları gider, değerler gönderilmez. Öneri kodla denetlenir: anahtar her görüntüde tekil ve dolu olmalı; tip 0 yalnızca hiç
değişmemiş kolonlara verilebilir; --history ile tarihçesi zorunlu denen kolonlar tip 2 olmalı; tip 2 kolonların yıllık satır artışı
(sürüm sayısı) --max-growth sınırını aşmamalı. Çıktı: dbt snapshot YAML’ı (check stratejisi, tip 2 kolonları), scd_plani.csv, scd-plani.md."""
import argparse
from collections import defaultdict
from datetime import date
import llm
import tabular
from common import here, output_dir, write_text, md_table


def measure(rows, key, cols):
    snaps = defaultdict(dict)
    for r in rows:
        snaps[r['tarih'].strip()][r[key].strip()] = r
    days = sorted(snaps)
    changes = {c: [] for c in cols}  # per step: set of keys whose value changed
    for a, b in zip(days, days[1:]):
        common = set(snaps[a]) & set(snaps[b])
        for c in cols:
            changes[c].append({k for k in common if snaps[a][k][c].strip() != snaps[b][k][c].strip()})
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days or 1
    return snaps, days, changes, span


def validator(rows, key, cols, snaps, days, changes, span, required, max_growth):
    keys = len(snaps[days[-1]])

    def validate(obj):
        types, problems = {}, []
        given = obj.get('kolonlar') or {}
        for c in cols:
            t = given.get(c)
            try:
                t = int(t)
            except (TypeError, ValueError):
                problems.append(f'{c}: tip 0, 1 ya da 2 olmalı')
                continue
            moved = sum(len(s) for s in changes[c])
            if t not in (0, 1, 2):
                problems.append(f'{c}: tip 0, 1 ya da 2 olmalı')
            elif t == 0 and moved:
                problems.append(f'{c}: {moved} değişiklik görülmüş; tip 0 (değişmez) olamaz')
            elif c in required and t != 2:
                problems.append(f'{c}: tarihçesi zorunlu (--history); tip 2 olmalı')
            types[c] = t
        if problems:
            raise ValueError('; '.join(problems))
        t2 = [c for c in cols if types[c] == 2]
        versions = sum(len(set().union(*[changes[c][i] for c in t2])) for i in range(len(days) - 1)) if t2 else 0
        growth = versions / keys * 365 / span
        if growth > max_growth:
            raise ValueError(f'tip 2 kolonları ({", ".join(t2)}) yılda satırların %{growth * 100:.0f}’i kadar yeni sürüm üretir (sınır %{max_growth * 100:.0f}); '
                             'sık değişen ve tarihçesi gerekmeyen kolonları tip 1 yap')
        reasons = {c: str(v).strip() for c, v in (obj.get('gerekceler') or {}).items()}
        return types, growth, versions, reasons
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir boyutsal modelleme uzmanısın. Her kolon için SCD tipi seç: 0 (hiç değişmez; yalnızca hiç değişmemiş kolonlar), 1 (üzerine yaz; '
          'düzeltmeler ve tarihçesi önemsiz alanlar), 2 (tarihçe; raporlarda geçmiş dönemin değeriyle analiz gereken alanlar: segment, bölge, fiyat '
          'listesi gibi). Çok sık değişen alanı tip 2 yapmak tabloyu büyütür. Yanıt şeması: {"kolonlar": {"segment": 2, "telefon": 1, "dogum_tarihi": 0}, '
          '"gerekceler": {"segment": "..."}}')


def main():
    p = argparse.ArgumentParser(description='Boyut tablosu anlık görüntülerinden kolon başına SCD tipi seçen ve satır artışını denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'musteri_anlik_goruntuleri.csv')), help='tarih + anahtar + kolonlar (her tarih bir anlık görüntü)')
    p.add_argument('--key', default='musteri_no')
    p.add_argument('--history', default='segment', help='Tarihçesi zorunlu kolonlar (virgülle)')
    p.add_argument('--max-growth', type=float, default=0.5, help='Tip 2 kolonların yıllık sürüm artışı sınırı (satır oranı)')
    p.add_argument('--snapshot-name', default='')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tarih', args.key):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    cols = [c for c in columns if c not in ('tarih', args.key)]
    snaps, days, changes, span = measure(rows, args.key, cols)
    if len(days) < 2:
        raise ValueError('En az iki tarihli anlık görüntü gerekir.')
    for d in days:
        ks = [r[args.key].strip() for r in rows if r['tarih'].strip() == d]
        if '' in ks or len(ks) != len(set(ks)):
            raise ValueError(f'{d}: {args.key} her görüntüde tekil ve dolu olmalı.')
    required = [c.strip() for c in args.history.split(',') if c.strip()]
    keys = len(snaps[days[-1]])
    out = output_dir(args.output)
    lines = [f'- {c}: {sum(len(s) for s in changes[c])} değişiklik; kayıtların %{len(set().union(*changes[c])) / keys * 100:.0f}’i en az bir kez değişti; '
             f'yıllık sürüm etkisi %{sum(len(s) for s in changes[c]) / keys * 365 / span * 100:.0f}' + (' (tarihçesi zorunlu)' if c in required else '') for c in cols]
    user = (f'{len(days)} anlık görüntü ({days[0]} – {days[-1]}), {keys} kayıt, anahtar {args.key}. Tip 2 yıllık sürüm sınırı %{args.max_growth * 100:.0f}.\n'
            'Kolonlar (değişim ölçümleri):\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, args.key, cols, snaps, days, changes, span, required, args.max_growth))
    if result is None:
        return
    types, growth, versions, reasons = result
    name = args.snapshot_name or f'{args.key.replace("_no", "")}_gecmisi'
    t2 = [c for c in cols if types[c] == 2]
    yml = ['snapshots:', f'  - name: {name}', "    relation: source('kaynak', 'musteri')  # kaynak tabloya göre düzenleyin", '    config:',
           f'      unique_key: {args.key}', '      strategy: check', '      check_cols: [' + ', '.join(t2) + ']', '      hard_deletes: invalidate']
    write_text(out / 'snapshot.yml', '\n'.join(yml) + '\n')
    plan = [{'kolon': c, 'tip': types[c], 'degisiklik': sum(len(s) for s in changes[c])} for c in cols]
    tabular.write_csv(out / 'scd_plani.csv', ['kolon', 'tip', 'degisiklik'], plan)
    md = ['# SCD planı', '', f'{keys} kayıt, {len(days)} görüntü. Tip 2: {", ".join(t2) or "yok"}; tahmini yıllık sürüm artışı %{growth * 100:.0f} '
          f'(gözlem döneminde {versions} yeni sürüm).', '',
          md_table(['Kolon', 'Tip', 'Değişiklik', 'Gerekçe'], [[x['kolon'], x['tip'], x['degisiklik'], reasons.get(x['kolon'], '')] for x in plan]), '',
          'Tip 1 kolonlar snapshot’ta izlenmez; boyut tablosunda son değerle güncellenir. snapshot.yml dbt 1.9+ YAML snapshot biçimindedir.', '',
          f'_Model: {meta["model"]} · Değişim sıklıkları ve sürüm artışı kodla ölçüldü; tip 0 ve zorunlu tarihçe kuralları denetlendi._']
    write_text(out / 'scd-plani.md', '\n'.join(md))
    print(f'[OK] {len(cols)} kolon; tip 2: {", ".join(t2) or "yok"}. Çıktı: {out / "scd-plani.md"}')


if __name__ == '__main__':
    llm.run(main)
