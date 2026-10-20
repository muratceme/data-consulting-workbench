"""Başlık birleştirici (AI agent): aynı raporun farklı aylarda farklı başlıklarla gelen dosyalarını (“Tutar TL”, “tutar”, “Amount”; sütun
eklenmiş/çıkarılmış; noktalı virgül/virgül) tek bir şemada birleştirir. Modele her dosyanın başlıkları ve kolon başına birkaç örnek değer
(kişisel veri maskelenir) gider. Model ortak (kanonik) kolonları ve her dosyanın başlık → kanonik kolon eşlemesini verir. Kodla denetlenir:
her dosyanın her başlığı eşlenmeli ya da bilerek atılmalı (null), bir dosyada iki başlık aynı kolona gidemez, kanonik adlar tekil
snake_case olmalı, her kanonik kolon en az bir dosyada bulunmalı ve kanonik kolonun değerleri tüm dosyalarda aynı tipte (sayı/tarih/metin)
olmalı. Çıktı: birlesik.csv (kaynak_dosya sütunuyla), baslik_esleme.csv, baslik-birlestirme.md."""
import argparse, re
from collections import defaultdict
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table, mask_text


def kind(values):
    vals = [v for v in values if v.strip()]
    if not vals:
        return 'bos'
    if all(tabular.parse_date(v) for v in vals):
        return 'tarih'
    style = tabular.number_style(vals)
    if all(tabular.parse_number(v, style) is not None for v in vals):
        return 'sayi'
    return 'metin'


def validator(files):
    def validate(obj):
        canon = [str(c).strip() for c in obj.get('kolonlar') or []]
        problems = []
        bad = [c for c in canon if not re.fullmatch(r'[a-z][a-z0-9]*(_[a-z0-9]+)*', c)]
        if bad or len(set(canon)) != len(canon) or not canon:
            problems.append('kanonik kolonlar tekil snake_case olmalı' + (f' ({", ".join(bad)})' if bad else ''))
        maps = obj.get('eslemeler') or {}
        plan = {}
        for f, (cols, rows) in files.items():
            m = maps.get(f)
            if not isinstance(m, dict):
                problems.append(f'{f}: eşleme yok')
                continue
            unknown = [h for h in m if h not in cols]
            missing = [h for h in cols if h not in m]
            wrong = [f'{h}→{t}' for h, t in m.items() if t is not None and t not in canon]
            used = [t for t in m.values() if t is not None]
            dup = sorted({t for t in used if used.count(t) > 1})
            for label, items in (('dosyada olmayan başlık', unknown), ('eşlenmeyen başlık (atılacaksa null yaz)', missing), ('kanonik listede olmayan hedef', wrong),
                                 ('iki başlık aynı kolona gidiyor', dup)):
                if items:
                    problems.append(f'{f}: {label}: {", ".join(items)}')
            plan[f] = {h: t for h, t in m.items() if h in cols}
        if problems:
            raise ValueError(' | '.join(problems))
        present = {t for m in plan.values() for t in m.values() if t}
        unused = [c for c in canon if c not in present]
        if unused:
            problems.append('hiçbir dosyada olmayan kanonik kolon: ' + ', '.join(unused))
        kinds = defaultdict(dict)
        for f, m in plan.items():
            cols, rows = files[f]
            for h, t in m.items():
                if t:
                    k = kind([r[h] for r in rows])
                    if k != 'bos':
                        kinds[t][f] = k
        mixed = [f'{t} ({", ".join(f"{f}: {k}" for f, k in v.items())})' for t, v in kinds.items() if len(set(v.values())) > 1]
        if mixed:
            problems.append('farklı tipte veriyi aynı kolona bağlıyor: ' + '; '.join(mixed))
        if problems:
            raise ValueError(' | '.join(problems))
        return canon, plan
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri analistisin. Aynı raporun farklı dönem dosyalarını tek tabloda birleştireceksin. Ortak (kanonik) kolonları snake_case adlarla '
          'belirle ve her dosyanın her başlığını bir kanonik kolona eşle; gereksiz başlıkları null ile at. Anlamı aynı olan başlıkları (Tutar TL, tutar, Amount) '
          'aynı kolona, farklı olanları (brüt/net tutar) ayrı kolonlara bağla. Yanıt şeması: {"kolonlar": ["tarih", "musteri", "tutar"], '
          '"eslemeler": {"ocak.csv": {"Tarih": "tarih", "Müşteri": "musteri", "Not": null}}}')


def main():
    p = argparse.ArgumentParser(description='Farklı başlıklarla gelen dönem dosyalarını tek şemada birleştiren, tip tutarlılığını kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'donem_dosyalari')), help='CSV/XLSX dosyaları klasörü')
    llm.add_arguments(p)
    args = p.parse_args()
    paths = sorted(f for f in Path(args.input).iterdir() if f.suffix.lower() in ('.csv', '.xlsx', '.txt'))
    if len(paths) < 2:
        raise ValueError('Birleştirmek için en az iki dosya gerekli.')
    files = {f.name: tabular.read(str(f)) for f in paths}
    out = output_dir(args.output)
    blocks = []
    for f, (cols, rows) in files.items():
        lines = [f'  - {h}: ' + ' | '.join(mask_text(v)[0] for v in list(dict.fromkeys(r[h] for r in rows if r[h].strip()))[:3]) for h in cols]
        blocks.append(f'{f} ({len(rows)} satır):\n' + '\n'.join(lines))
    user = 'Dosyalar, başlıklar ve örnek değerler:\n' + '\n'.join(blocks)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(files))
    if result is None:
        return
    canon, plan = result
    merged = []
    for f, m in plan.items():
        cols, rows = files[f]
        inv = {t: h for h, t in m.items() if t}
        merged += [{'kaynak_dosya': f, **{c: r[inv[c]] if c in inv else '' for c in canon}} for r in rows]
    tabular.write_csv(out / 'birlesik.csv', ['kaynak_dosya'] + canon, merged)
    tabular.write_csv(out / 'baslik_esleme.csv', ['dosya', 'baslik', 'kanonik'], [{'dosya': f, 'baslik': h, 'kanonik': t or '(atıldı)'} for f, m in plan.items() for h, t in m.items()])
    md = ['# Başlık birleştirme', '', f'{len(files)} dosya, {len(merged)} satır {len(canon)} ortak kolonda birleşti.', '',
          md_table(['Kolon'] + list(plan), [[c] + [next((h for h, t in m.items() if t == c), '—') for m in plan.values()] for c in canon]), '']
    dropped = [f'{f}: {h}' for f, m in plan.items() for h, t in m.items() if t is None]
    if dropped:
        md += ['Atılan başlıklar: ' + ', '.join(dropped) + '.', '']
    md += [f'_Model: {meta["model"]} · Eşlemelerin eksiksizliği, tekilliği ve kolon tipi tutarlılığı kodla denetlendi. Sayı biçimleri (1.234,56 / 1,234.56) dosyadaki gibi korunur._']
    write_text(out / 'baslik-birlestirme.md', '\n'.join(md))
    print(f'[OK] {len(files)} dosya, {len(merged)} satır. Çıktı: {out / "baslik-birlestirme.md"}')


if __name__ == '__main__':
    llm.run(main)
