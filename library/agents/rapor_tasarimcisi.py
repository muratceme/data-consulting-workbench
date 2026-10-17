"""Rapor tasarımcısı (AI agent): bir CSV/Excel dosyasının sütun profilini (tip, dolu/farklı değer sayısı, kimlik ve kişisel veri işareti)
yerelde çıkarır ve kullanıcının isteğiyle ("bölge müdürleri için aylık satış raporu") modele gönderir. Model başlığı, ölçüleri ve
toplama biçimini, kırılım boyutlarını, tarih sütununu ve hedef araçları seçer. Seçim kodla denetlenir: sütunlar dosyada olmalı, ölçüler
sayısal ve kimlik olmayan sütunlar, boyutlar 2–100 farklı değerli ve kişisel veri içermeyen sütunlar, tarih gerçekten tarih olmalı.
Onaylanan tasarımla rapor şablonu blokları Qlik Sense, Power BI (PBIP), Tableau, Looker, Excel, PowerPoint, dbt ve HTML raporlarını kodla üretir.
Satırlar ve örnek değerler gönderilmez. Çıktı: tasarim.md, tasarim.json, rapor-onizleme.html ve seçilen araç klasörleri."""
import argparse
from argparse import Namespace
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from sqlgen import infer_column
import rapor

TOOLS = {'qlik': 'Qlik Sense uygulaması', 'powerbi': 'Power BI projesi (PBIP)', 'tableau': 'Tableau çalışma kitabı', 'looker': 'Looker (LookML)',
         'excel': 'formüllü Excel raporu', 'powerpoint': 'PowerPoint sunumu', 'dbt': 'dbt projesi', 'html': 'HTML pano'}
KIND = {'int': 'tam sayı', 'bigint': 'tam sayı', 'decimal': 'ondalık sayı', 'date': 'tarih', 'datetime': 'tarih-saat', 'bool': 'evet/hayır', 'text': 'metin'}


def profile(columns, rows):
    out = []
    for c in columns:
        vals = [r[c].strip() for r in rows]
        filled = [v for v in vals if v]
        kind = infer_column(vals)[0]
        out.append({'sutun': c, 'tip': KIND[kind], 'kind': kind, 'dolu': len(filled), 'farkli': len(set(filled)),
                    'kimlik': bool(rapor.IDLIKE.search(c)) or (len(set(filled)) == len(rows) and kind == 'text'), 'kisisel': bool(rapor.PII.search(c))})
    return out


def validator(prof, allowed_tools):
    by = {p['sutun']: p for p in prof}

    def validate(obj):
        problems = []
        title = str(obj.get('baslik', '')).strip()
        if not title or len(title) > 80:
            problems.append('baslik 1–80 karakter olmalı')
        measures, averages = [], []
        for m in obj.get('olculer') or []:
            c = str(m.get('sutun', '')).strip() if isinstance(m, dict) else ''
            agg = str(m.get('toplama', '')).strip().lower() if isinstance(m, dict) else ''
            if c not in by:
                problems.append(f'olmayan ölçü sütunu: {c or "?"}')
            elif by[c]['kind'] not in ('int', 'bigint', 'decimal'):
                problems.append(f'{c} sayısal değil ({by[c]["tip"]}); ölçü olamaz')
            elif by[c]['kimlik']:
                problems.append(f'{c} kimlik/kod sütunu; toplanamaz')
            elif agg not in ('toplam', 'ortalama'):
                problems.append(f'{c}: toplama "toplam" veya "ortalama" olmalı')
            elif c in measures:
                problems.append(f'{c} iki kez ölçü seçilmiş')
            else:
                measures.append(c)
                if agg == 'ortalama':
                    averages.append(c)
        if not 1 <= len(measures) <= 4 and not problems:
            problems.append('1–4 ölçü seçilmeli')
        if measures and measures[0] in averages:
            problems.append('ilk ölçü (ana ölçü) toplam olmalı; kırılım ve trendler onu gösterir')
        dims = []
        for d in obj.get('boyutlar') or []:
            d = str(d).strip()
            if d not in by:
                problems.append(f'olmayan boyut sütunu: {d or "?"}')
            elif by[d]['kisisel']:
                problems.append(f'{d} kişisel veri; raporda kırılım olamaz')
            elif not 2 <= by[d]['farkli'] <= 100:
                problems.append(f'{d}: {by[d]["farkli"]} farklı değer var; kırılım için 2–100 olmalı')
            elif d in measures or d in dims:
                problems.append(f'{d} iki kez seçilmiş')
            else:
                dims.append(d)
        if not 1 <= len(dims) <= 3 and not any('boyut' in x for x in problems):
            problems.append('1–3 boyut seçilmeli')
        date = obj.get('tarih')
        date = str(date).strip() if date not in (None, '', 'null', 'yok') else ''
        if date and (date not in by or by[date]['kind'] != 'date'):
            problems.append(f'{date} saatsiz tarih sütunu değil' + (f'; tarih sütunları: {", ".join(p["sutun"] for p in prof if p["kind"] == "date")}' if any(p['kind'] == 'date' for p in prof) else ''))
        tools = [str(t).strip().lower() for t in obj.get('araclar') or []]
        unknown = [t for t in tools if t not in allowed_tools]
        if unknown or not tools:
            problems.append(f'araclar şunlardan seçilmeli: {", ".join(allowed_tools)}')
        if problems:
            raise ValueError('; '.join(problems))
        return {'baslik': title, 'olculer': measures, 'ortalamalar': averages, 'boyutlar': dims, 'tarih': date, 'araclar': list(dict.fromkeys(tools)),
                'gerekce': str(obj.get('gerekce', '')).strip(), 'sorular': [str(x).strip() for x in obj.get('sorular') or [] if str(x).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir BI rapor tasarımcısısın. Kullanıcının isteğine ve verilen sütun profiline göre rapor tasarla: kısa bir başlık, 1–4 ölçü '
          '(ilk ölçü ana ölçüdür ve "toplam" olmalı; birim fiyat, oran, puan gibi sütunlar "ortalama"), 1–3 kırılım boyutu (az değerli, kişisel veri olmayan metin sütunları; '
          'isteğe en uygun olan önce), varsa trend için tarih sütunu ve kullanıcının istediği araçlar. Kimlik/kod sütunlarını ölçü yapma. İstek belirsizse varsayımını gerekce’ye, '
          'netleştirilecekleri sorular’a yaz. Yanıt şeması: {"baslik": "...", "olculer": [{"sutun": "tutar", "toplama": "toplam"}], "boyutlar": ["bolge"], "tarih": "siparis_tarihi", '
          '"araclar": ["qlik", "powerbi"], "gerekce": "...", "sorular": ["..."]}')


def build(args, columns, rows, design, out):
    ns = Namespace(input=args.input, title=design['baslik'], table='', measures=','.join(design['olculer']), dimensions=','.join(design['boyutlar']),
                   date=design['tarih'], averages=design['ortalamalar'], lib='', folder='C:/Veri', connection='veri_ambari', model='rapor', sql_table='', sheet=None)
    ds = rapor.design(columns, rows, ns)
    if not design['tarih']:
        ds.date, ds.trend, ds.years = None, [], []
    files = {'rapor-onizleme.html': rapor.preview_html(ds, ', '.join(TOOLS[t] for t in design['araclar']))}
    made = []
    for tool in design['araclar']:
        if tool == 'qlik':
            files.update(rapor.qlik_files(columns, rows, ns, ds)[0])
        elif tool == 'powerbi':
            files.update(rapor.pbip_files(columns, rows, ns, ds)[0])
        elif tool == 'tableau':
            files['tableau/rapor.twb'] = rapor.twb_text(columns, rows, ns, ds)[0]
        elif tool == 'looker':
            files.update(rapor.lookml_files(columns, rows, ns, ds)[0])
        elif tool == 'excel':
            files['excel/rapor.xlsx'] = rapor.xlsx_bytes(columns, rows, ds)[0]
        elif tool == 'powerpoint':
            files['powerpoint/rapor.pptx'] = rapor.pptx_bytes(ds, f'{ds.trend[0][0]} – {ds.trend[-1][0]}' if ds.trend else '')[0]
        elif tool == 'dbt':
            files.update(rapor.dbt_files(columns, rows, ns, ds)[0])
        elif tool == 'html':
            files['rapor.html'] = files['rapor-onizleme.html']
        made.append(TOOLS[tool])
    for name, content in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content) if isinstance(content, bytes) else write_text(path, content)
    return ds, made, len(files)


def main():
    p = argparse.ArgumentParser(description='İsteğe göre rapor tasarlayıp Qlik/Power BI/Tableau/Looker/Excel/PowerPoint/dbt şablonlarını üreten agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satis_detay.csv')))
    p.add_argument('--sheet', default=None)
    p.add_argument('--request', default='Bölge müdürleri için aylık satış raporu: ciro ve sipariş adedi, bölge ve kanal kırılımı. Qlik ve Power BI’da olsun.',
                   help='Raporun kimin için ve ne için olduğu')
    p.add_argument('--tools', default=','.join(TOOLS), help='İzin verilen araçlar: ' + ', '.join(TOOLS))
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input, args.sheet)
    if not rows:
        raise ValueError('Dosyada veri satırı yok.')
    allowed = [t.strip() for t in args.tools.split(',') if t.strip() in TOOLS]
    if Path(args.input).suffix.lower() in ('.xlsx', '.xlsm') and 'tableau' in allowed:
        allowed.remove('tableau')  # the Tableau template connects to a CSV text file
    prof = profile(columns, rows)
    out = output_dir(args.output)
    lines = [f"- {x['sutun']}: {x['tip']}, {x['dolu']} dolu / {x['farkli']} farklı değer" + (', kimlik/kod' if x['kimlik'] else '') + (', kişisel veri' if x['kisisel'] else '') for x in prof]
    user = f'İstek: {args.request}\nKullanılabilir araçlar: {", ".join(allowed)}\nSatır sayısı: {len(rows)}\n\nSütun profili:\n' + '\n'.join(lines)
    design, meta = llm.ask(args, out, SYSTEM, user, validator(prof, allowed))
    if design is None:
        return
    ds, made, n = build(args, columns, rows, design, out)
    write_json(out / 'tasarim.json', {**design, 'istek': args.request, 'kpi': [{'ad': a, 'deger': v} for a, v, _ in ds.kpis]})
    md = [f"# {design['baslik']}", '', f'**İstek:** {args.request}', '', design['gerekce'], '',
          md_table(['Öğe', 'Seçim'], [['Ölçüler', ', '.join(m['name'] for m in ds.measures) + f' + {ds.count["name"]}'], ['Kırılımlar', ', '.join(design['boyutlar'])],
                                       ['Tarih', design['tarih'] or 'yok'], ['Üretilenler', ', '.join(made)]]), '',
          '## Rakamlar (kodla hesaplandı)', '', md_table(['KPI', 'Değer'], [[a, rapor.tr_num(v, d)] for a, v, d in ds.kpis])]
    if design['sorular']:
        md += ['', '## Netleştirilecekler', ''] + [f'- {q}' for q in design['sorular']]
    if ds.warnings:
        md += ['', '## Veri uyarıları', ''] + [f'- {w}' for w in ds.warnings]
    md += ['', f"_Model: {meta['model']} · Tasarım sütun profiline göre denetlendi; dosyaları rapor şablonu blokları üretti ve her şablon kendi biçim doğrulamasından geçmiş koddur._"]
    write_text(out / 'tasarim.md', '\n'.join(md))
    print(f"[OK] “{design['baslik']}”: {', '.join(made)} ({n} dosya). Çıktı: {out / 'tasarim.md'}")


if __name__ == '__main__':
    llm.run(main)
