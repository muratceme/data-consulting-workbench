"""JSON eşleme yazarı (AI agent): iç içe bir API/JSON yanıtının alan yollarını, tiplerini ve bulunma oranlarını yerelde çıkarır; model,
kayıt dizisinin yolunu, isteğe bağlı olarak satıra açılacak alt diziyi ve her sütun için kaynak yolu, hedef tipi ve zorunluluğu içeren
bir düzleştirme eşlemesi önerir. Eşleme yerelde örnek JSON’a uygulanır: kayıt yolu bir nesne listesine çıkmalı, her yol en az bir kayıtta
bulunmalı, değerlerin en az yüzde 95’i hedef tipe dönüşmeli, zorunlu sütun her satırda dolu olmalı ve sütun adları tekil snake_case
olmalı. Sonuç tablosunu kod üretir. Çıktı: esleme.json, duz_tablo.csv, esleme.md."""
import argparse, json, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

TYPES = ('metin', 'tam sayı', 'ondalık', 'tarih', 'mantıksal')
COL = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def get(obj, path):
    for part in [p for p in path.split('.') if p]:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def paths(obj, prefix='', out=None):
    out = {} if out is None else out
    for k, v in obj.items():
        p = f'{prefix}.{k}' if prefix else k
        kind = 'nesne' if isinstance(v, dict) else 'dizi' if isinstance(v, list) else type(v).__name__ if v is not None else 'null'
        out.setdefault(p, {'tipler': set(), 'adet': 0, 'ornek': ''})
        out[p]['tipler'].add(kind)
        out[p]['adet'] += 1
        if not out[p]['ornek'] and isinstance(v, (str, int, float, bool)):
            out[p]['ornek'] = str(v)[:30]
        if isinstance(v, dict):
            paths(v, p, out)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for x in v:
                paths(x, p + '[]', out)
    return out


def convert(v, kind):
    if v is None or v == '':
        return ''
    if isinstance(v, (dict, list)):
        raise ValueError('nesne/dizi')
    if kind == 'metin':
        return str(v)
    if kind == 'tam sayı':
        if isinstance(v, bool) or not re.fullmatch(r'[+-]?\d+', str(v).strip()):
            raise ValueError
        return int(str(v))
    if kind == 'ondalık':
        if isinstance(v, bool):
            raise ValueError
        return float(v) if isinstance(v, (int, float)) else float(str(v).replace(',', '.'))
    if kind == 'tarih':
        if not tabular.parse_date(str(v)[:19].replace('T', ' ')):
            raise ValueError
        return str(v)
    if kind == 'mantıksal':
        if isinstance(v, bool):
            return v
        if str(v).lower() in ('true', 'false', '1', '0'):
            return str(v).lower() in ('true', '1')
        raise ValueError
    raise ValueError


def apply(doc, mapping):
    records = get(doc, mapping['kayit_yolu']) if mapping['kayit_yolu'] else doc
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        raise ValueError(f'kayit_yolu "{mapping["kayit_yolu"]}" nesne listesine çıkmıyor')
    explode = mapping.get('acilim') or ''
    out = []
    for rec in records:
        items = get(rec, explode) if explode else None
        for item in (items if isinstance(items, list) and items else [None]):
            row = {}
            for c in mapping['sutunlar']:
                p = c['yol']
                row[c['sutun']] = get(item, p[len(explode) + 3:]) if explode and p.startswith(explode + '[].') else get(rec, p)
            out.append(row)
    return records, out


def validator(doc):
    def validate(obj):
        problems = []
        root = str(obj.get('kayit_yolu', '')).strip()
        explode = str(obj.get('acilim', '') or '').strip()
        explode = explode[len(root) + 3:] if root and explode.startswith(root + '[].') else explode
        mapping = {'kayit_yolu': root, 'acilim': explode, 'sutunlar': []}
        names = set()
        for c in obj.get('sutunlar') or []:
            if not isinstance(c, dict):
                continue
            name, path, kind = str(c.get('sutun', '')).strip(), str(c.get('yol', '')).strip(), str(c.get('tip', '')).strip().lower()
            if mapping['kayit_yolu'] and path.startswith(mapping['kayit_yolu'] + '[].'):
                path = path[len(mapping['kayit_yolu']) + 3:]  # the profile shows absolute paths; mapping paths are record-relative
            if not COL.match(name) or name in names:
                problems.append(f'sütun adı "{name}" tekil snake_case olmalı')
            if kind not in TYPES:
                problems.append(f'{name}: tip şunlardan biri olmalı: {", ".join(TYPES)}')
            names.add(name)
            mapping['sutunlar'].append({'sutun': name, 'yol': path, 'tip': kind, 'zorunlu': bool(c.get('zorunlu')), 'aciklama': str(c.get('aciklama', '')).strip()})
        if not mapping['sutunlar']:
            problems.append('en az bir sütun gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        try:
            records, rows = apply(doc, mapping)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        if mapping['acilim'] and not any(isinstance(get(r, mapping['acilim']), list) for r in records):
            problems.append(f'acilim "{mapping["acilim"]}" hiçbir kayıtta dizi değil')
        for c in mapping['sutunlar']:
            vals = [r[c['sutun']] for r in rows]
            present = [v for v in vals if v not in (None, '')]
            if not present:
                problems.append(f"{c['sutun']}: yol \"{c['yol']}\" hiçbir kayıtta bulunamadı" + (f" (alt dizi alanları \"{mapping['acilim']}[].alan\" biçiminde yazılır)" if mapping['acilim'] else ''))
                continue
            bad = 0
            for v in present:
                try:
                    convert(v, c['tip'])
                except (ValueError, TypeError):
                    bad += 1
            if bad > 0.05 * len(present):
                problems.append(f"{c['sutun']}: {bad}/{len(present)} değer {c['tip']} tipine dönüşmüyor")
            if c['zorunlu'] and len(present) < len(vals):
                problems.append(f"{c['sutun']}: zorunlu ama {len(vals) - len(present)} satırda boş")
        if problems:
            raise ValueError('; '.join(problems[:12]))
        return {'mapping': mapping, 'rows': rows, 'records': len(records), 'notes': [str(n) for n in obj.get('notlar', []) if n]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri entegrasyonu mühendisisin. Verilen JSON alan yollarından ambar tablosuna yüklenecek düz bir tablo eşlemesi tasarla. '
          'kayit_yolu: kayıt dizisinin yolu (kökteyse boş). İç içe bir diziyi satırlara açmak gerekiyorsa acilim alanına o dizinin kayıt içindeki yolunu yaz ve '
          'onun alanlarını "dizi[].alan" biçiminde kullan; diğer alanlar kayda göre "a.b" biçiminde (kayit_yolu öneki olmadan). Sütun adları snake_case ve tekil. '
          f'tip: {", ".join(TYPES)}. Her satırda dolu olmayan alanı zorunlu yapma. Yanıt şeması: {{"kayit_yolu": "veri", "acilim": "kalemler", '
          '"sutunlar": [{"sutun": "siparis_id", "yol": "id", "tip": "metin", "zorunlu": true, "aciklama": "..."}], "notlar": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='İç içe JSON için örnek veride doğrulanmış düzleştirme eşlemesi yazan agent.')
    p.add_argument('--input', default=str(here('ornek', 'siparisler.json')))
    p.add_argument('--request', default='Sipariş kalemi düzeyinde tablo; müşteri bilgileri her satırda.', help='İstenen tablonun kısa tarifi')
    llm.add_arguments(p)
    args = p.parse_args()
    doc = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    info = paths(doc) if isinstance(doc, dict) else paths({'': doc})
    out = output_dir(args.output)
    lines = [f"- {p}: {', '.join(sorted(i['tipler']))}; {i['adet']} kez" + (f"; örnek: {i['ornek']}" if i['ornek'] else '') for p, i in info.items()]
    user = f"İstek: {args.request}\n\nJSON alan yolları (yerelde çıkarıldı; [] dizi elemanı demektir):\n" + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(doc))
    if result is None:
        return
    m = result['mapping']
    cols = [c['sutun'] for c in m['sutunlar']]
    table = []
    for r in result['rows']:
        conv = {}
        for c in m['sutunlar']:
            try:
                v = convert(r[c['sutun']], c['tip'])
                conv[c['sutun']] = ('true' if v else 'false') if isinstance(v, bool) else v
            except (ValueError, TypeError):
                conv[c['sutun']] = ''
        table.append(conv)
    tabular.write_csv(out / 'duz_tablo.csv', cols, table)
    write_json(out / 'esleme.json', m)
    md = ['# JSON düzleştirme eşlemesi', '', f"Kayıt yolu: `{m['kayit_yolu'] or '(kök)'}` · Açılan dizi: `{m['acilim'] or '—'}` · {result['records']} kayıt → {len(table)} satır", '',
          md_table(['Sütun', 'Yol', 'Tip', 'Zorunlu', 'Açıklama'], [[c['sutun'], f"`{c['yol']}`", c['tip'], 'evet' if c['zorunlu'] else '', c['aciklama']] for c in m['sutunlar']])]
    if result['notes']:
        md += ['', '## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · Eşleme örnek JSON’a uygulanarak doğrulandı; esleme.json entegrasyon aracınızda (ADF, Airbyte, dbt) sütun eşlemesi olarak kullanılabilir._"]
    write_text(out / 'esleme.md', '\n'.join(md))
    print(f"[OK] {len(cols)} sütun, {len(table)} satır; eşleme örnek veride doğrulandı. Çıktı: {out / 'duz_tablo.csv'}")


if __name__ == '__main__':
    llm.run(main)
