"""Qlik → Power BI geçiş agent'ı: Qlik load script'inin soy ağacını yerelde çıkarır (qlik-soyagaci kod bloğu), script'i
ve soy ağacını modele verip uygulamada kalan her tablo için Power Query (M) karşılığını, ApplyMap/JOIN/CONCATENATE
dönüşümlerinin nasıl taşınacağını ve riskleri yazdırır. Yerel denetim: soy ağacındaki her son tablo karşılanmalı,
her M sorgusu let … in yapısında olmalı. Çıktı: gecis-plani.md, power-query/<tablo>.m, lineage.json."""
import argparse, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from qlik_soyagaci import parse, strip_comments

MAX_SCRIPT = 60000


def final_tables(lineage):
    names = []
    for t in lineage['tables']:
        if t['dropped'] or 'MAPPING' in t['prefix'] or 'JOIN' in t['prefix'] or 'KEEP' in t['prefix'] or t['prefix'] == 'CONCATENATE' or t['kind'] == 'binary':
            continue
        if t['name'] not in names:
            names.append(t['name'])
    return names


def safe_file(name):
    return re.sub(r'[^\w\-]+', '_', name, flags=re.U).strip('_') or 'tablo'


def validator(required):
    def validate(obj):
        tables = obj.get('tablolar')
        if not isinstance(tables, list):
            raise ValueError('"tablolar" listesi yok.')
        by = {}
        problems = []
        for t in tables:
            if not isinstance(t, dict):
                continue
            name = str(t.get('qlik_tablo', '')).strip()
            m = str(t.get('power_query_m', '')).strip()
            m = re.sub(r'^```\w*\s*|```$', '', m).strip()
            if not re.match(r'^let\b', m, re.I) or not re.search(r'\bin\b\s*\S+\s*$', m):
                problems.append(f'"{name}" için M sorgusu "let ... in <adım>" yapısında değil')
            if m.count('(') != m.count(')'):
                problems.append(f'"{name}" M sorgusunda parantezler dengesiz')
            body = re.split(r'\bin\b\s*\S+\s*$', m)[0]
            steps = re.findall(r'^\s*(#"[^"]+"|[A-Za-z_][\w.]*)\s*=(?!=|>)', body, re.M)
            repeated = sorted({s for s in steps if steps.count(s) > 1})
            if repeated:
                problems.append(f'"{name}" M sorgusunda aynı adım adı birden çok kez tanımlanmış ({", ".join(repeated)}); her adıma yeni ad ver')
            if re.search(r'\bQvd\.\w+|JoinType\.Concatenate|Csv\.File\b', m):
                problems.append(f'"{name}" M sorgusunda Power Query’de olmayan fonksiyon var (QVD okunamaz; birleştirme için Table.Combine, CSV için Csv.Document)')
            by[name] = {'name': name, 'm': m, 'notes': str(t.get('notlar', '') or '').strip()}
        missing = [n for n in required if n not in by]
        if missing:
            problems.append('Şu son tablolar karşılanmadı: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        measures = [{'name': str(x.get('ad', '')), 'dax': str(x.get('dax', '')), 'qlik': str(x.get('qlik_ifadesi', ''))}
                    for x in obj.get('dax_olculeri', []) if isinstance(x, dict) and x.get('ad')]
        return {'tables': [by[n] for n in required] + [v for k, v in by.items() if k not in required],
                'measures': measures, 'risks': [str(r) for r in obj.get('riskler', []) if r], 'model': str(obj.get('model_onerisi', '')).strip()}
    return validate


SYSTEM = (llm.SAFETY + ' Sen Qlik ve Power BI geçişlerinde deneyimli bir BI mimarısın. Verilen Qlik load script\'ini Power BI\'a taşı. '
          'Uygulamada kalan her tablo için tek bir Power Query (M) sorgusu yaz: kaynak bağlantısını (lib:// klasör bağlantısı için '
          'Folder/File.Contents, LIB CONNECT SQL için Sql.Database vb.) parametre gibi açıkça belirt, ApplyMap için Table.NestedJoin + '
          'Table.ExpandTableColumn veya Merge kullan, JOIN/CONCATENATE adımlarını aynı tablonun sorgusuna dahil et. '
          'Her M sorgusu kısa olsun (en fazla ~25 satır) ve aynı adımı tekrar etme. '
          'QVD kaynaklarının Power BI\'da doğrudan okunamayacağını belirt ve QVD\'yi üreten kaynağı kullan. Script\'te olmayan sütun uydurma. '
          'Yanıt şeması: {"tablolar": [{"qlik_tablo": "...", "power_query_m": "let ... in ...", "notlar": "..."}], '
          '"dax_olculeri": [{"ad": "...", "dax": "...", "qlik_ifadesi": "..."}], "riskler": ["..."], "model_onerisi": "yıldız şema / ilişki önerisi"}')


def main():
    p = argparse.ArgumentParser(description='Qlik load script için Power BI (Power Query M) geçiş planı yazan agent.')
    p.add_argument('--script', default=str(here('ornek', 'satis.qvs')))
    p.add_argument('--expressions', help='İsteğe bağlı: Qlik grafik ifadeleri (her satıra bir ifade) DAX karşılığı için')
    llm.add_arguments(p)
    args = p.parse_args()
    raw = Path(args.script).read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1254')
    lineage = parse(text)
    required = final_tables(lineage)
    if not required:
        raise ValueError('Script’te uygulamada kalan tablo bulunamadı.')
    script = strip_comments(text).strip()
    if len(script) > MAX_SCRIPT:
        raise ValueError(f'Script {len(script)} karakter; agent en fazla {MAX_SCRIPT} karakter gönderir. Script’i sekmelere bölün.')
    expressions = Path(args.expressions).read_text(encoding='utf-8-sig').strip() if args.expressions else ''
    out = output_dir(args.output)
    write_json(out / 'lineage.json', lineage)
    summary = '\n'.join(f"- {t['name']}: {t['prefix'] or 'LOAD'} ← {t['kind']} {t['source']}" for t in lineage['tables'])
    user = (f"Uygulamada kalan tablolar (her biri için M yaz): {', '.join(required)}\n\nYerel soy ağacı:\n{summary}\n\n"
            f"Bağlantılar: {', '.join(lineage['connections']) or 'yok'}\n\nQlik script:\n\"\"\"\n{script}\n\"\"\""
            + (f"\n\nGrafik ifadeleri:\n\"\"\"\n{expressions}\n\"\"\"" if expressions else ''))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(required))
    if result is None:
        return
    pq = output_dir(out / 'power-query')
    for t in result['tables']:
        write_text(pq / f"{safe_file(t['name'])}.m", f"// Qlik tablosu: {t['name']} · datawork geçiş taslağı\n{t['m']}")
    md = ['# Qlik → Power BI geçiş planı', '', f"Kaynak script: {Path(args.script).name} · Model: {meta['model']}", '',
          '## Tablolar', '', md_table(['Qlik tablosu', 'Power Query dosyası', 'Not'], [[t['name'], f"power-query/{safe_file(t['name'])}.m", t['notes']] for t in result['tables']])]
    if result['model']:
        md += ['## Model önerisi', '', result['model'], '']
    if result['measures']:
        md += ['## DAX ölçüleri', '', md_table(['Ölçü', 'DAX', 'Qlik ifadesi'], [[m['name'], f"`{m['dax']}`", f"`{m['qlik']}`"] for m in result['measures']])]
    risks = result['risks'] + lineage['warnings']
    if risks:
        md += ['## Riskler ve dikkat edilecekler', ''] + [f'- {r}' for r in risks]
    md += ['', '_M sorguları taslaktır: Power BI Desktop > Veri al > Boş sorgu > Gelişmiş düzenleyici ile ekleyip kaynak yollarını/sunucuları düzeltin, '
           'satır sayılarını Qlik uygulamasıyla karşılaştırın (tablo-mutabakati kod bloğu)._']
    write_text(out / 'gecis-plani.md', '\n'.join(md))
    print(f"[OK] {len(result['tables'])} tablo için M sorgusu, {len(result['measures'])} DAX ölçüsü. Çıktı: {out / 'gecis-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
