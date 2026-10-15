"""Power BI model dokümanı: .pbit şablonundan (veya model.bim / DataModelSchema dosyasından) tabloları, sütunları,
ölçüleri (DAX), ilişkileri, Power Query kaynaklarını ve rapor sayfalarını çıkarır; model risklerini işaretler.
.pbix içindeki model sıkıştırılmış olduğundan okunmaz: Power BI Desktop'ta Dosya > Dışarı aktar > Power BI şablonu ile .pbit alın.
İnternete bağlanmaz. Çıktı: veri-modeli.md, model.json, olculer.csv, sutunlar.csv, iliskiler.csv."""
import argparse, json, re, zipfile
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

MAX_MEMBER = 256 * 1024 * 1024


def _text(value):
    return '\n'.join(value) if isinstance(value, list) else (value or '')


def _decode(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    if len(raw) > 1 and raw[1:2] == b'\x00':
        return raw.decode('utf-16-le')
    return raw.decode('utf-8-sig')


def read_schema(path):
    """Return (tmsl_json, report_layout_or_None)."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'Dosya bulunamadı: {path}')
    if path.suffix.lower() == '.pbix':
        raise ValueError('.pbix içindeki veri modeli sıkıştırılmış biçimdedir ve okunamaz. Power BI Desktop’ta '
                         'Dosya > Dışarı aktar > Power BI şablonu (.pbit) ile kaydedip o dosyayı verin.')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = {n.lower(): n for n in z.namelist()}
            if 'datamodelschema' not in names:
                raise ValueError('Dosyada DataModelSchema yok; geçerli bir .pbit şablonu verin.')
            for key in ('datamodelschema', 'report/layout'):
                if key in names and z.getinfo(names[key]).file_size > MAX_MEMBER:
                    raise ValueError('Şablon içindeki model 256 MB sınırını aşıyor.')
            schema = json.loads(_decode(z.read(names['datamodelschema'])))
            layout = None
            if 'report/layout' in names:
                try:
                    layout = json.loads(_decode(z.read(names['report/layout'])))
                except (ValueError, UnicodeDecodeError):
                    layout = None
            return schema, layout
    return json.loads(_decode(path.read_bytes())), None


def normalize(schema, layout=None):
    model = schema.get('model', schema)
    if not isinstance(model, dict) or 'tables' not in model:
        raise ValueError('Model şeması tanınmadı: "model.tables" bulunamadı.')
    tables = []
    for t in model.get('tables', []):
        name = t.get('name', '')
        columns = [{'name': c.get('name', ''), 'data_type': c.get('dataType', ''), 'kind': 'hesaplanmış' if c.get('type') == 'calculated' else ('satır no' if c.get('type') == 'rowNumber' else 'veri'),
                    'expression': _text(c.get('expression')), 'hidden': bool(c.get('isHidden')), 'description': _text(c.get('description')),
                    'format': c.get('formatString', ''), 'sort_by': c.get('sortByColumn', '')}
                   for c in t.get('columns', []) if c.get('type') != 'rowNumber']
        measures = [{'table': name, 'name': m.get('name', ''), 'expression': _text(m.get('expression')).strip(),
                     'format': m.get('formatString', ''), 'description': _text(m.get('description')),
                     'folder': m.get('displayFolder', ''), 'hidden': bool(m.get('isHidden'))} for m in t.get('measures', [])]
        sources = []
        for part in t.get('partitions', []):
            src = part.get('source', {})
            sources.append({'type': src.get('type', ''), 'expression': _text(src.get('expression')).strip()})
        tables.append({'name': name, 'hidden': bool(t.get('isHidden')), 'description': _text(t.get('description')),
                       'auto_date': name.startswith(('LocalDateTable_', 'DateTableTemplate_')),
                       'columns': columns, 'measures': measures, 'sources': sources})
    relationships = [{'from_table': r.get('fromTable', ''), 'from_column': r.get('fromColumn', ''), 'to_table': r.get('toTable', ''),
                      'to_column': r.get('toColumn', ''), 'active': r.get('isActive', True) is not False,
                      'both_directions': r.get('crossFilteringBehavior') == 'bothDirections',
                      'cardinality': f"{r.get('fromCardinality', 'many')}:{r.get('toCardinality', 'one')}"}
                     for r in model.get('relationships', [])]
    pages = []
    for section in (layout or {}).get('sections', []):
        visuals = []
        for vc in section.get('visualContainers', []):
            try:
                cfg = json.loads(vc.get('config', '{}'))
                visuals.append(cfg.get('singleVisual', {}).get('visualType', 'diğer'))
            except ValueError:
                visuals.append('diğer')
        pages.append({'name': section.get('displayName', ''), 'visuals': visuals})
    return {'name': schema.get('name', ''), 'compatibility_level': schema.get('compatibilityLevel'),
            'culture': model.get('culture', ''), 'tables': tables, 'relationships': relationships, 'pages': pages}


def findings(m):
    out = []
    user_tables = [t for t in m['tables'] if not t['auto_date']]
    auto = [t for t in m['tables'] if t['auto_date']]
    if auto:
        out.append(('orta', f'Otomatik tarih/saat açık: {len(auto)} gizli tarih tablosu. Ortak bir tarih tablosu kullanıp Seçenekler > Veri Yükleme > Otomatik tarih/saat’i kapatın.'))
    for r in m['relationships']:
        label = f"{r['from_table']}[{r['from_column']}] → {r['to_table']}[{r['to_column']}]"
        if r['to_table'].startswith('LocalDateTable_'):
            continue
        if r['both_directions']:
            out.append(('yüksek', f'Çift yönlü filtre: {label}. Belirsiz filtre yolları ve performans sorunu doğurabilir.'))
        if not r['active']:
            out.append(('bilgi', f'Pasif ilişki: {label}. Yalnızca USERELATIONSHIP ile kullanılır.'))
        if r['cardinality'] == 'many:many':
            out.append(('yüksek', f'Çoka-çok ilişki: {label}.'))
    related = {r['from_table'] for r in m['relationships']} | {r['to_table'] for r in m['relationships']}
    for t in user_tables:
        if not t['measures'] and t['name'] not in related and len(user_tables) > 1:
            out.append(('orta', f"İlişkisiz tablo: {t['name']}. Filtreler bu tabloya ulaşmaz."))
    measures = [x for t in m['tables'] for x in t['measures']]
    undocumented = [x['name'] for x in measures if not x['description']]
    if undocumented:
        out.append(('düşük', f'Açıklamasız ölçü: {len(undocumented)}/{len(measures)} ({", ".join(undocumented[:8])}{"…" if len(undocumented) > 8 else ""}).'))
    calculated = [f"{t['name']}[{c['name']}]" for t in user_tables for c in t['columns'] if c['kind'] == 'hesaplanmış']
    if calculated:
        out.append(('bilgi', f'Hesaplanmış sütun: {len(calculated)}. Mümkünse Power Query’de veya kaynakta üretin: {", ".join(calculated[:6])}'))
    unformatted = [x['name'] for x in measures if not x['format']]
    if unformatted:
        out.append(('düşük', f'Biçimi tanımsız ölçü: {", ".join(unformatted[:8])}'))
    known = {x['name'] for x in measures} | {c['name'] for t in m['tables'] for c in t['columns']}
    for x in measures:
        for ref in set(re.findall(r'(?<![\w\'\]])\[([^\]]+)\]', x['expression'])):
            if ref not in known:
                out.append(('yüksek', f"'{x['name']}' ölçüsü modelde olmayan [{ref}] başvurusu içeriyor."))
    whole = re.compile(r"FILTER\s*\(\s*'?[\w ]+'?\s*,")
    names = [x['name'] for x in measures if whole.search(x['expression'])]
    if names:
        out.append(('orta', f'Tüm tablo üzerinde FILTER kullanan ölçü: {", ".join(names[:6])}. Sütun filtresi (KEEPFILTERS / boolean) daha hızlıdır.'))
    return out


def source_summary(expression):
    calls = re.findall(r'\b(Sql\.Databases?|Excel\.Workbook|Csv\.Document|SharePoint\.\w+|Web\.Contents|OData\.Feed|Odbc\.\w+|Oracle\.Database|Snowflake\.Databases|GoogleBigQuery\.Database|Databricks\.\w+|Folder\.Files|File\.Contents|Lakehouse\.Contents|PowerPlatform\.Dataflows|AnalysisServices\.Database)\b', expression)
    return ', '.join(dict.fromkeys(calls)) or ('DAX hesaplanmış tablo' if expression and not expression.lstrip().startswith('let') else 'Diğer / elle girilen')


def markdown(m, source, found):
    user_tables = [t for t in m['tables'] if not t['auto_date']]
    measures = [x for t in user_tables for x in t['measures']]
    lines = [f'# Power BI veri modeli — {source}', '',
             f"- Tablo: **{len(user_tables)}** (+{len(m['tables']) - len(user_tables)} otomatik tarih tablosu)",
             f"- Sütun: **{sum(len(t['columns']) for t in user_tables)}** · Ölçü: **{len(measures)}** · İlişki: **{len(m['relationships'])}**",
             f"- Uyumluluk düzeyi: {m['compatibility_level'] or '—'} · Kültür: {m['culture'] or '—'}", '']
    lines += ['## Bulgular', '', md_table(['Önem', 'Bulgu'], found) if found else '_Belirgin bir risk bulunmadı._\n']
    lines += ['## Tablolar', '', md_table(['Tablo', 'Sütun', 'Ölçü', 'Kaynak', 'Gizli'],
                                          [[t['name'], len(t['columns']), len(t['measures']), ', '.join(source_summary(s['expression']) for s in t['sources']) or '—', 'evet' if t['hidden'] else ''] for t in user_tables])]
    lines += ['## İlişkiler', '', md_table(['Kimden', 'Kime', 'Kardinalite', 'Yön', 'Aktif'],
                                           [[f"{r['from_table']}[{r['from_column']}]", f"{r['to_table']}[{r['to_column']}]", r['cardinality'],
                                             'çift' if r['both_directions'] else 'tek', 'evet' if r['active'] else 'hayır']
                                            for r in m['relationships'] if not r['to_table'].startswith('LocalDateTable_')])]
    lines += ['## Ölçüler', '']
    for x in measures:
        lines += [f"### {x['table']}[{x['name']}]", '', (x['description'] or '_Açıklama yok._'), '',
                  f"Biçim: `{x['format'] or '—'}`" + (f" · Klasör: {x['folder']}" if x['folder'] else ''), '', '```dax', x['expression'], '```', '']
    if m['pages']:
        lines += ['## Rapor sayfaları', '', md_table(['Sayfa', 'Görsel', 'Türler'],
                                                     [[p['name'], len(p['visuals']), ', '.join(sorted(set(p['visuals'])))] for p in m['pages']])]
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='Power BI .pbit / model.bim dokümantasyonu (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'satis-modeli.pbit')), help='.pbit, model.bim veya DataModelSchema')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        schema, layout = read_schema(args.input)
        m = normalize(schema, layout)
        found = findings(m)
        out = output_dir(args.output)
        write_json(out / 'model.json', m)
        user = [t for t in m['tables'] if not t['auto_date']]
        tabular.write_csv(out / 'olculer.csv', ['table', 'name', 'expression', 'format', 'folder', 'description'], [x for t in user for x in t['measures']])
        tabular.write_csv(out / 'sutunlar.csv', ['table', 'name', 'data_type', 'kind', 'hidden', 'expression', 'description'],
                          [{**c, 'table': t['name']} for t in user for c in t['columns']])
        tabular.write_csv(out / 'iliskiler.csv', ['from_table', 'from_column', 'to_table', 'to_column', 'cardinality', 'both_directions', 'active'], m['relationships'])
        write_text(out / 'veri-modeli.md', markdown(m, Path(args.input).name, found))
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        p.exit(1, f'[X] {exc}\n')
    print(f"[OK] {len(user)} tablo, {sum(len(t['measures']) for t in user)} ölçü, {len(m['relationships'])} ilişki, {len(found)} bulgu. Çıktı: {out / 'veri-modeli.md'}")


if __name__ == '__main__':
    main()
