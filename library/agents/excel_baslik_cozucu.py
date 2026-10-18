"""Excel başlık çözücü (AI agent): insanlar için hazırlanmış dağınık Excel/CSV raporlarını (üstte başlık ve tarih satırları, iki satırlı birleşik
başlık, araya girmiş ara toplamlar, altta notlar) düz bir tabloya çevirir. Modele dosyanın ilk satırları ızgara olarak gider (sayılar
yer tutucuya çevrilir, metin maskelenir). Model başlık satırlarını, veri aralığını, atlanacak satırları ve kolon adlarını söyler; tablo kodla
kurulur ve denetlenir: kolon sayısı ızgara genişliğine uymalı, adlar tekil snake_case olmalı, veri aralığında boş satır ya da “Toplam”
satırı kalmamalı, her kolon tek tipte (sayı ya da metin) olmalı. Çıktı: temiz.csv, baslik-cozumu.md."""
import argparse, csv, io, re
from pathlib import Path
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table, mask_text

TOTAL = re.compile(r'^(genel\s+)?(ara\s+)?toplam\b|^total\b|^grand total', re.I)


def grid(path):
    p = Path(path)
    if p.suffix.lower() in ('.xlsx', '.xlsm'):
        orig = tabular._rows_to_records
        tabular._rows_to_records = lambda table, name: table  # read the raw cell grid
        try:
            g = tabular.read_xlsx(path)
        finally:
            tabular._rows_to_records = orig
    else:
        text = p.read_text(encoding='utf-8-sig', errors='replace')
        head = '\n'.join(text.splitlines()[:30])
        delim = max(';,\t', key=head.count)
        g = list(csv.reader(io.StringIO(text), delimiter=delim))
    width = max((len(r) for r in g), default=0)
    return [[c.strip() for c in r] + [''] * (width - len(r)) for r in g]


def is_number(v):
    return tabular.parse_number(v, None) is not None or tabular.parse_number(v, 'tr') is not None


def shown(cell):
    if not cell:
        return ''
    return '<sayı>' if is_number(cell) else mask_text(cell)[0][:40]


def build(g, plan):
    first, last = plan['veri_ilk'] - 1, (plan['veri_son'] or len(g)) - 1
    skip = {s - 1 for s in plan['atla']}
    return [g[i] for i in range(first, last + 1) if i not in skip]


def validator(g):
    width = len(g[0]) if g else 0

    def validate(obj):
        try:
            plan = {'baslik': [int(x) for x in obj.get('baslik_satirlari') or []], 'veri_ilk': int(obj['veri_ilk']),
                    'veri_son': int(obj['veri_son']) if obj.get('veri_son') else None, 'atla': [int(x) for x in obj.get('atlanacak_satirlar') or []],
                    'kolonlar': [str(c).strip() for c in obj.get('kolon_adlari') or []]}
        except (KeyError, TypeError, ValueError):
            raise ValueError('veri_ilk (satır numarası), kolon_adlari ve isteğe bağlı veri_son, baslik_satirlari, atlanacak_satirlar gerekli') from None
        problems = []
        if not 1 <= plan['veri_ilk'] <= len(g) or (plan['veri_son'] and not plan['veri_ilk'] <= plan['veri_son'] <= len(g)):
            raise ValueError(f'veri aralığı 1–{len(g)} içinde olmalı')
        if any(h >= plan['veri_ilk'] for h in plan['baslik']):
            problems.append('başlık satırları veri aralığından önce olmalı')
        cols = plan['kolonlar']
        if len(cols) != width:
            problems.append(f'{len(cols)} kolon adı var, ızgara {width} kolon')
        bad = [c for c in cols if not re.fullmatch(r'[a-z][a-z0-9]*(_[a-z0-9]+)*', c)]
        if bad:
            problems.append('snake_case ASCII olmayan ad: ' + ', '.join(bad[:6]))
        if len(set(cols)) != len(cols):
            problems.append('tekrarlanan kolon adı: ' + ', '.join(sorted({c for c in cols if cols.count(c) > 1})))
        if problems:
            raise ValueError('; '.join(problems))
        data = build(g, plan)
        rows_at = [i + 1 for i in range(plan['veri_ilk'] - 1, (plan['veri_son'] or len(g))) if i + 1 not in plan['atla']]
        blank = [n for n, r in zip(rows_at, data) if not any(r)]
        totals = [n for n, r in zip(rows_at, data) if any(TOTAL.search(tr.fold(c)) for c in r if c)]
        if blank:
            problems.append(f'veri aralığında boş satır: {", ".join(map(str, blank[:8]))}')
        if totals:
            problems.append(f'veri aralığında toplam satırı: {", ".join(map(str, totals[:8]))} (atlanacak_satirlar’a ekle)')
        for j, c in enumerate(cols):
            vals = [r[j] for r in data if r[j]]
            nums = sum(1 for v in vals if is_number(v))
            if vals and 0 < nums < len(vals):
                odd = [f'satır {n}: “{shown(r[j])}”' for n, r in zip(rows_at, data) if r[j] and is_number(r[j]) != (nums * 2 > len(vals))]
                problems.append(f'{c} kolonu karışık tipte ({", ".join(odd[:3])})')
        if not data:
            problems.append('veri satırı kalmadı')
        if problems:
            raise ValueError('; '.join(problems))
        return plan, data
    return validate


SYSTEM = (llm.SAFETY + ' Sen dağınık Excel raporlarını analiz tablosuna çeviren bir veri analistisin. Izgarada (satır numarası: hücreler) başlık satırlarını, '
          'verinin ilk ve son satırını, atlanacak ara toplam/not satırlarını bul ve her kolon için snake_case ASCII bir ad ver. İki satırlı başlıkta üst '
          'başlığı (ay, bölge) alt başlıkla birleştir (ocak_adet, ocak_tutar). Satır numaraları 1’den başlar. Yanıt şeması: {"baslik_satirlari": [4, 5], '
          '"veri_ilk": 6, "veri_son": 14, "atlanacak_satirlar": [10], "kolon_adlari": ["bolge", "ocak_adet", "ocak_tutar"]}')


def main():
    p = argparse.ArgumentParser(description='Dağınık Excel/CSV raporunu başlıkları çözülmüş düz tabloya çeviren agent.')
    p.add_argument('--input', default=str(here('ornek', 'bolge_raporu_daginik.csv')))
    p.add_argument('--preview', type=int, default=60, help='Modele gösterilecek en fazla satır')
    llm.add_arguments(p)
    args = p.parse_args()
    g = grid(args.input)
    if not g:
        raise ValueError('Dosya boş.')
    out = output_dir(args.output)
    user = (f'Izgara: {len(g)} satır × {len(g[0])} kolon. Sayılar <sayı> olarak gösterildi.\n'
            + '\n'.join(f'{i}: ' + ' | '.join(shown(c) or '·' for c in r) for i, r in enumerate(g[:args.preview], 1)))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(g))
    if result is None:
        return
    plan, data = result
    tabular.write_csv(out / 'temiz.csv', plan['kolonlar'], [dict(zip(plan['kolonlar'], r)) for r in data])
    md = ['# Başlık çözümü', '', f'{len(g)} satırlık ızgaradan {len(data)} veri satırı ve {len(plan["kolonlar"])} kolon çıkarıldı. Başlık satırları: '
          f'{", ".join(map(str, plan["baslik"])) or "—"}; veri {plan["veri_ilk"]}–{plan["veri_son"] or len(g)}; atlanan: {", ".join(map(str, plan["atla"])) or "—"}.', '',
          md_table(['Kolon', 'Örnek'], [[c, next((r[j] for r in data if r[j]), '')] for j, c in enumerate(plan['kolonlar'])]), '',
          f'_Model: {meta["model"]} · Tablo kodla kuruldu; boş satır, toplam satırı ve karışık tipli kolon kalmadığı denetlendi. Sayıların biçimi (1.234,56) temiz.csv’de korunur._']
    write_text(out / 'baslik-cozumu.md', '\n'.join(md))
    print(f'[OK] {len(data)} satır, {len(plan["kolonlar"])} kolon. Çıktı: {out / "baslik-cozumu.md"}')


if __name__ == '__main__':
    llm.run(main)
