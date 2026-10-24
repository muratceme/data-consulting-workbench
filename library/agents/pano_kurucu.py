"""Pano kurucu (AI agent): bir klasördeki olgu ve boyut tablolarından veri modelini (anahtarlar, veride doğrulanmış ilişkiler, sütun tipleri ve
farklı değer sayıları) yerelde çıkarır, kullanıcının isteğiyle birlikte modele gönderir. Model panonun başlığını, ölçülerini (toplam, ortalama, farklı sayı),
kırılım alanlarını, detay tablosunu, trendi ve hedef araçları seçer. Seçim kodla denetlenir: ölçü olgu tablosunda ve sayısal olmalı (farklı sayı anahtar
sütunlarda), kırılım ilişkiyle olguya bağlı, kişisel veri içermeyen, az değerli bir alan olmalı. Onaylanan tasarımla kurulum blokları Qlik Sense, Power BI (PBIP),
Looker, Tableau, Superset ve Metabase projelerini kodla üretir. Satırlar ve örnek değerler gönderilmez. Çıktı: pano-tasarimi.md, pano-tasarimi.json ve araç klasörleri."""
import argparse
from argparse import Namespace
import llm
from common import here, output_dir, write_json, write_text, md_table
from sqlgen import snake
import rapor
import kurulum

TOOLS = {'qlik': ('Qlik Sense uygulaması', kurulum.qlik_build), 'powerbi': ('Power BI projesi (PBIP)', kurulum.pbi_build),
         'looker': ('Looker (LookML)', kurulum.lookml_build), 'tableau': ('Tableau çalışma kitabı', kurulum.tableau_build),
         'superset': ('Superset içe aktarma paketi', kurulum.superset_build), 'metabase': ('Metabase kurulum betiği', kurulum.metabase_build)}
KIND = {'int': 'tam sayı', 'bigint': 'tam sayı', 'decimal': 'ondalık sayı', 'date': 'tarih', 'datetime': 'tarih-saat', 'bool': 'evet/hayır', 'text': 'metin'}
AGG = {'toplam': 'sum', 'ortalama': 'avg', 'farkli_sayi': 'countd'}


def describe(m):
    fks = {(r.child, r.fk): r.parent for r in m.rels if r.active}
    lines = []
    for t in m.order + m.loose:
        tb = m.tables[t]
        role = 'OLGU' if t == m.fact.name else 'boyut' if t in m.path else 'bağlantısız'
        lines.append(f'Tablo {t} ({role}, {len(tb.rows)} satır):')
        for c in tb.columns:
            flags = (['anahtar'] if c == tb.pk else []) + ([f'yabancı anahtar → {fks[(t, c)]}'] if (t, c) in fks else []) + (['kişisel veri'] if rapor.PII.search(c) else [])
            lines.append(f'  - {c}: {KIND[tb.kinds[c]]}, {tb.distinct[c]} farklı değer' + (f' ({", ".join(flags)})' if flags else ''))
    lines.append('İlişkiler: ' + '; '.join(f'{r.child}.{r.fk} → {r.parent}.{r.pk}' + ('' if r.active else ' (pasif)') for r in m.rels))
    return '\n'.join(lines)


def validator(m, allowed):
    fact = m.fact
    attrs = {(a['table'], a['col']): a for a in m.attrs}
    keyish = {fact.pk} | {r.fk for r in m.rels if r.child == fact.name}

    def pick(items, what, limit):
        out, problems = [], []
        for x in items or []:
            key = (str(x.get('tablo', '')).strip(), str(x.get('sutun', '')).strip()) if isinstance(x, dict) else ('', '')
            if key not in attrs:
                tb = m.tables.get(key[0])
                why = ('tablo yok' if not tb else 'sütun yok' if key[1] not in tb.columns else
                       'olgu tablosuna bağlı değil' if key[0] not in m.path else 'kişisel veri, anahtar, sayı veya 2–50 farklı değerli metin değil')
                problems.append(f'{what} {key[0]}.{key[1]}: {why}')
            elif key in out:
                problems.append(f'{what} {key[0]}.{key[1]} iki kez seçilmiş')
            else:
                out.append(key)
        if not 1 <= len(out) <= limit and not problems:
            problems.append(f'1–{limit} {what} seçilmeli')
        return out, problems

    def validate(obj):
        problems = []
        title = str(obj.get('baslik', '')).strip()
        if not title or len(title) > 80:
            problems.append('baslik 1–80 karakter olmalı')
        measures, names = [], set()
        for x in obj.get('olculer') or []:
            c = str(x.get('sutun', '')).strip() if isinstance(x, dict) else ''
            how = str(x.get('toplama', '')).strip().lower() if isinstance(x, dict) else ''
            name = str(x.get('ad', '')).strip() if isinstance(x, dict) else ''
            if c not in fact.columns:
                problems.append(f'ölçü sütunu olgu tablosu {fact.name} içinde yok: {c or "?"}')
                continue
            if how not in AGG:
                problems.append(f'{c}: toplama toplam, ortalama veya farkli_sayi olmalı')
                continue
            if how == 'farkli_sayi':
                if c not in keyish or c == fact.pk:
                    problems.append(f'{c}: farkli_sayi yalnızca yabancı anahtar sütunlarında ({", ".join(sorted(keyish - {fact.pk}))}); satır sayısı zaten eklenir')
                    continue
            elif fact.kinds[c] not in ('int', 'bigint', 'decimal') or c in keyish or rapor.IDLIKE.search(c):
                problems.append(f'{c}: {how} için sayısal ve anahtar olmayan sütun gerekli')
                continue
            if name and (len(name) > 60 or any(ch in name for ch in '[]\'"')):
                problems.append(f'{c}: ad 60 karakteri geçmemeli, köşeli parantez ve tırnak içermemeli')
                continue
            agg = AGG[how]
            name = name or (f'{"Ortalama" if agg == "avg" else "Toplam"} {rapor.label(c)}' if agg != 'countd' else rapor.label(kurulum.entity(c)) + ' Sayısı')
            if name in names or name == m.count['name']:
                problems.append(f'ölçü adı iki kez kullanılmış: {name}')
                continue
            names.add(name)
            measures.append(dict(col=c, agg=agg, name=name, dec=2 if agg == 'avg' or fact.kinds[c] == 'decimal' and agg == 'sum' else 0))
        if not 1 <= len(measures) <= 5 and not problems:
            problems.append('1–5 ölçü seçilmeli')
        if measures and measures[0]['agg'] == 'avg':
            problems.append('ilk ölçü (ana ölçü) toplam veya farkli_sayi olmalı; kırılım ve trend onu gösterir')
        charts, p1 = pick(obj.get('kirilimlar'), 'kırılım', 4)
        detail, p2 = pick(obj.get('detay') or obj.get('kirilimlar'), 'detay alanı', 6)
        problems += p1 + p2
        trend = bool(obj.get('trend'))
        if trend and not m.date:
            problems.append(f'{fact.name} tablosunda tarih sütunu yok; trend false olmalı')
        tools = [str(t).strip().lower() for t in obj.get('araclar') or []]
        if not tools or any(t not in allowed for t in tools):
            problems.append(f'araclar şunlardan seçilmeli: {", ".join(allowed)}')
        if problems:
            raise ValueError('; '.join(problems))
        return {'baslik': title, 'olculer': measures, 'kirilimlar': [attrs[k] for k in charts], 'detay': [attrs[k] for k in detail], 'trend': trend,
                'araclar': list(dict.fromkeys(tools)), 'gerekce': str(obj.get('gerekce', '')).strip(), 'sorular': [str(q).strip() for q in obj.get('sorular') or [] if str(q).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir BI danışmanısın ve bir panoyu sıfırdan kuruyorsun. Verilen veri modeline (tablolar, sütun tipleri, anahtarlar, ilişkiler) ve kullanıcının '
          'isteğine göre pano tasarla: kısa başlık; 1–5 ölçü (yalnızca OLGU tablosundaki sütunlar; tutar/adet gibi sütunlar "toplam", fiyat/oran/puan "ortalama", müşteri '
          'veya ürün sayısı için yabancı anahtar sütununda "farkli_sayi"; ilk ölçü ana ölçüdür ve ortalama olamaz; istersen "ad" ver); 1–4 kırılım (boyut tablolarındaki az değerli '
          'metin sütunları, isteğe en uygun olan önce); en çok 6 alanlık detay tablosu; tarih varsa trend; kullanıcının istediği araçlar. Kişisel veri ve anahtar sütunlarını '
          'kırılım yapma. Belirsizliği gerekce’ye, netleştirilecekleri sorular’a yaz. Yanıt şeması: {"baslik": "...", "olculer": [{"sutun": "tutar", "toplama": "toplam", "ad": "Ciro"}], '
          '"kirilimlar": [{"tablo": "Urun", "sutun": "kategori"}], "detay": [{"tablo": "Magaza", "sutun": "magaza_adi"}], "trend": true, "araclar": ["qlik", "powerbi"], '
          '"gerekce": "...", "sorular": ["..."]}')


def build(m, design, out, args):
    m.title = design['baslik']
    m.measures = design['olculer']
    m.charts = design['kirilimlar']
    m.attrs = list({(a['table'], a['col']): a for a in design['kirilimlar'] + design['detay']}.values())
    if not design['trend']:
        m.date = None
    kurulum.compute(m)
    ns = Namespace(lib='lib://DataFiles', folder=f'C:\\Veri\\{m.folder.name}', connection='veri_ambari', model=snake(m.folder.name), schema=args.schema,
                   tableau_folder=f'C:/Veri/{m.folder.name}', dialect=args.dialect, database='veri_ambari', sqlalchemy_uri='postgresql://rapor:XXXXXXXXXX@ambar:5432/analytics')
    files, made, notes = {'pano-onizleme.html': kurulum.preview(m, ', '.join(TOOLS[t][0] for t in design['araclar']))}, [], []
    for tool in design['araclar']:
        f, extra, n = TOOLS[tool][1](m, ns)
        files.update(f)
        made.append((TOOLS[tool][0], ', '.join(f'{v} {k}' for k, v in extra.items())))
        notes.append(n[0])
    for name, content in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content) if isinstance(content, bytes) else write_text(path, content)
    return made, notes, len(files)


def main():
    p = argparse.ArgumentParser(description='Olgu ve boyut tablolarından isteğe göre pano tasarlayıp Qlik/Power BI/Looker/Tableau/Superset/Metabase projesini kuran agent.')
    p.add_argument('--input', default=str(here('ornek', 'magaza')), help='Tablo klasörü (her tablo bir CSV/XLSX)')
    p.add_argument('--request', default=str(here('ornek', 'pano_istek.md')), help='İstek metni veya .md dosyası')
    p.add_argument('--tools', default=','.join(TOOLS), help='İzin verilen araçlar: ' + ', '.join(TOOLS))
    p.add_argument('--relations', default='', help='Adı tutmayan ilişkiler: alt.sutun=ust.sutun;...')
    p.add_argument('--fact', default='')
    p.add_argument('--dialect', choices=kurulum.SQL_DIALECTS, default='postgres', help='Superset/Metabase SQL lehçesi')
    p.add_argument('--schema', default='analytics')
    llm.add_arguments(p)
    args = p.parse_args()
    from pathlib import Path
    request = Path(args.request).read_text(encoding='utf-8-sig').strip() if Path(args.request).is_file() else args.request
    m = kurulum.load_model(Namespace(input=args.input, title='', fact=args.fact, measures='', relations=args.relations))
    allowed = [t.strip() for t in args.tools.split(',') if t.strip() in TOOLS]
    if any(t.file.suffix.lower() in ('.xlsx', '.xlsm') for t in m.tables.values()) and 'tableau' in allowed:
        allowed.remove('tableau')
    out = output_dir(args.output)
    user = f'İstek:\n"""\n{request}\n"""\nKullanılabilir araçlar: {", ".join(allowed)}\n\nVeri modeli (kodla çıkarıldı):\n{describe(m)}'
    design, meta = llm.ask(args, out, SYSTEM, user, validator(m, allowed))
    if design is None:
        return
    made, notes, n = build(m, design, out, args)
    write_json(out / 'pano-tasarimi.json', {'baslik': design['baslik'], 'istek': request, 'olculer': design['olculer'],
                                            'kirilimlar': [f"{a['table']}.{a['col']}" for a in design['kirilimlar']], 'detay': [f"{a['table']}.{a['col']}" for a in design['detay']],
                                            'trend': design['trend'], 'araclar': design['araclar'], 'kpi': [{'ad': a, 'deger': v} for a, v, _ in m.kpis]})
    md = [f"# {design['baslik']}", '', f'**İstek:** {request}', '', design['gerekce'], '',
          md_table(['Öğe', 'Seçim'], [['Ölçüler', ', '.join(f"{x['name']} ({x['agg']} {m.fact.name}.{x['col']})" for x in m.measures) + f' + {m.count["name"]}'],
                                       ['Kırılımlar', ', '.join(f"{a['label']} ({a['table']})" for a in m.charts)], ['Detay', ', '.join(a['label'] for a in m.attrs)],
                                       ['Trend', f"aylık ({m.fact.name}.{m.date})" if m.date else 'yok']]), '',
          '## Üretilenler', '', md_table(['Araç', 'İçerik'], [list(x) for x in made]), '',
          '## Rakamlar (kodla hesaplandı)', '', md_table(['KPI', 'Değer'], [[a, rapor.tr_num(v, d)] for a, v, d in m.kpis])]
    for a in m.charts:
        md += ['', f"### {a['label']} bazında {m.primary['name']}", '', md_table([a['label'], m.primary['name']], [[k, rapor.tr_num(v, m.primary['dec'])] for k, v in m.breakdown[a['label']][:10]])]
    md += ['', '## Model', '', md_table(['Alt tablo', 'Sütun', 'Üst tablo', 'Eşleşmeyen', 'Durum'], [[r.child, r.fk, r.parent, r.orphans, 'aktif' if r.active else 'pasif'] for r in m.rels])]
    if design['sorular']:
        md += ['', '## Netleştirilecekler', ''] + [f'- {q}' for q in design['sorular']]
    md += ['', '## Kurulum', ''] + [f'- {x}' for x in notes] + ([''] + ['## Veri uyarıları', ''] + [f'- {w}' for w in m.warnings] if m.warnings else [])
    md += ['', f"_Model: {meta['model']} · Tasarım veri modeline göre denetlendi; dosyaları kurulum blokları kodla üretti, rakamlar dosyalardan hesaplandı._"]
    write_text(out / 'pano-tasarimi.md', '\n'.join(md))
    print(f"[OK] “{design['baslik']}”: {', '.join(x[0] for x in made)} ({n} dosya). Çıktı: {out / 'pano-tasarimi.md'}")


if __name__ == '__main__':
    llm.run(main)
