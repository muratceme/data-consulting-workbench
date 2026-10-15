"""KPI tanımlayıcı (AI agent): toplantı notlarını veya rapor isteğini (ve isteğe bağlı olarak veri dosyasının sütun
listesini) okur; her KPI için tanım, formül, birim, periyot, kırılımlar, kaynak, sahip ve açık soruları içeren bir KPI
kataloğu yazar. Notlardaki kişisel veriler (e-posta, telefon, TCKN, IBAN) gönderilmeden önce yerelde maskelenir.
Veri dosyası verilirse formüllerde dosyada olmayan alan adı geçip geçmediği yerelde denetlenir.
Çıktı: kpi-katalogu.md, kpi-katalogu.csv, kpi-katalogu.json."""
import argparse, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

FIELDS = ('ad', 'tanim', 'formul', 'birim', 'periyot', 'kirilimlar', 'kaynak', 'sahip', 'hedef_yonu', 'acik_sorular')


def validator(columns):
    known = {c.lower() for c in columns}

    def validate(obj):
        kpis = obj.get('kpiler')
        if not isinstance(kpis, list) or not kpis:
            raise ValueError('"kpiler" listesi boş.')
        names, problems, rows = set(), [], []
        for i, k in enumerate(kpis, start=1):
            if not isinstance(k, dict):
                continue
            name = str(k.get('ad', '')).strip()
            if not name or not str(k.get('tanim', '')).strip() or not str(k.get('formul', '')).strip():
                problems.append(f'{i}. KPI için ad, tanim ve formul zorunlu')
                continue
            if name.lower() in names:
                problems.append(f'"{name}" iki kez tanımlandı')
            names.add(name.lower())
            direction = str(k.get('hedef_yonu', '')).strip().lower()
            row = {f: k.get(f, '') for f in FIELDS}
            row['hedef_yonu'] = direction if direction in ('yukarı', 'aşağı', 'aralık') else 'belirsiz'
            for f in ('kirilimlar', 'acik_sorular'):
                row[f] = [str(x) for x in row[f]] if isinstance(row[f], list) else [s.strip() for s in str(row[f]).split(',') if s.strip()]
            if known:
                refs = {r.lower() for r in re.findall(r'\[([^\]]+)\]', str(row['formul']))}
                missing = sorted(refs - known)
                row['eksik_alanlar'] = missing
            rows.append(row)
        if problems:
            raise ValueError('; '.join(problems))
        return {'kpis': rows, 'questions': [str(q) for q in obj.get('genel_acik_sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir BI danışmanısın. Notlardaki iş ihtiyacından ölçülebilir KPI tanımları çıkar. '
          'Formülde veri alanlarını [alan_adı] biçiminde yaz; veri sütunları verildiyse yalnızca onları kullan, yoksa eksik alanı açıkça belirt. '
          'Notlarda geçmeyen hedef değer veya sahip uydurma; bilinmiyorsa "belirlenecek" yaz ve açık sorulara ekle. '
          'hedef_yonu: yukarı, aşağı veya aralık. '
          'Yanıt şeması: {"kpiler": [{"ad": "...", "tanim": "...", "formul": "...", "birim": "TL, %, adet…", "periyot": "günlük/haftalık/aylık", '
          '"kirilimlar": ["..."], "kaynak": "...", "sahip": "...", "hedef_yonu": "...", "acik_sorular": ["..."]}], "genel_acik_sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Toplantı notlarından KPI kataloğu çıkaran agent.')
    p.add_argument('--notes', default=str(here('ornek', 'toplanti-notlari.md')))
    p.add_argument('--data', help='İsteğe bağlı CSV/XLSX: yalnızca sütun adları gönderilir, formüller bu adlara göre denetlenir')
    llm.add_arguments(p)
    args = p.parse_args()
    notes = Path(args.notes).read_text(encoding='utf-8-sig').strip()
    if not notes:
        raise ValueError('Not dosyası boş.')
    columns = tabular.read(args.data)[0] if args.data else []
    out = output_dir(args.output)
    user = f"Toplantı notları:\n\"\"\"\n{notes}\n\"\"\"" + (f"\n\nKullanılabilir veri sütunları: {', '.join(columns)}" if columns else '\n\nVeri sütunları verilmedi.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns))
    if result is None:
        return
    kpis = result['kpis']
    write_json(out / 'kpi-katalogu.json', result)
    tabular.write_csv(out / 'kpi-katalogu.csv', list(FIELDS) + (['eksik_alanlar'] if columns else []),
                      [{**k, 'kirilimlar': '; '.join(k['kirilimlar']), 'acik_sorular': '; '.join(k['acik_sorular']),
                        'eksik_alanlar': '; '.join(k.get('eksik_alanlar', []))} for k in kpis])
    md = ['# KPI kataloğu', '', md_table(['KPI', 'Formül', 'Birim', 'Periyot', 'Yön', 'Sahip'],
                                        [[k['ad'], f"`{k['formul']}`", k['birim'], k['periyot'], k['hedef_yonu'], k['sahip']] for k in kpis])]
    for k in kpis:
        md += [f"## {k['ad']}", '', str(k['tanim']), '', f"- Kırılımlar: {', '.join(k['kirilimlar']) or '—'}", f"- Kaynak: {k['kaynak'] or '—'}"]
        if k.get('eksik_alanlar'):
            md.append(f"- **Veride olmayan alan:** {', '.join(k['eksik_alanlar'])}")
        if k['acik_sorular']:
            md.append('- Açık sorular: ' + ' · '.join(k['acik_sorular']))
        md.append('')
    if result['questions']:
        md += ['## Genel açık sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Tanımlar taslaktır; KPI sahipleriyle onaylayın._"]
    write_text(out / 'kpi-katalogu.md', '\n'.join(md))
    gaps = sum(1 for k in kpis if k.get('eksik_alanlar'))
    print(f"[{'!' if gaps else 'OK'}] {len(kpis)} KPI tanımlandı{f', {gaps} KPI veride olmayan alan kullanıyor' if gaps else ''}. Çıktı: {out / 'kpi-katalogu.md'}")


if __name__ == '__main__':
    llm.run(main)
