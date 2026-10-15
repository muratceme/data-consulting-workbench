"""Tableau çalışma kitabı envanteri: .twb/.twbx dosyasından veri kaynaklarını, bağlantıları, hesaplanan alanları
(formüller okunur adlarla), parametreleri, sayfaları ve panoları çıkarır; kullanılmayan alanları ve yerel dosya
bağımlılıklarını işaretler. Migrasyon ve devir teslim içindir. İnternete bağlanmaz.
Çıktı: tableau-envanter.md, hesaplanan-alanlar.csv, envanter.json."""
import argparse, re, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table
import tabular

MAX_TWB = 256 * 1024 * 1024
TABLE_CALC = re.compile(r'\b(WINDOW_\w+|RUNNING_\w+|LOOKUP|INDEX|RANK\w*|FIRST|LAST|SIZE|TOTAL|PREVIOUS_VALUE)\s*\(', re.I)
LOD = re.compile(r'\{\s*(FIXED|INCLUDE|EXCLUDE)\b', re.I)


def read_twb(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'Dosya bulunamadı: {path}')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            twbs = [i for i in z.infolist() if i.filename.lower().endswith('.twb') and '/' not in i.filename.strip('/')]
            if not twbs:
                raise ValueError('.twbx içinde .twb bulunamadı.')
            if twbs[0].file_size > MAX_TWB:
                raise ValueError('.twb 256 MB sınırını aşıyor.')
            raw = z.read(twbs[0])
            packaged = [i.filename for i in z.infolist() if not i.filename.lower().endswith('.twb') and not i.is_dir()]
    else:
        raw, packaged = path.read_bytes(), []
    if b'<!DOCTYPE' in raw[:2000] or b'<!ENTITY' in raw[:20000]:
        raise ValueError('DTD/ENTITY içeren XML güvenlik nedeniyle işlenmez.')
    try:
        return ET.fromstring(raw), packaged
    except ET.ParseError as exc:
        raise ValueError(f'Tableau XML okunamadı: {exc}') from None


def strip(name):
    return (name or '').strip('[]')


def inventory(root, packaged=()):
    captions = {}
    sources, parameters, calcs = [], [], []
    for ds in root.iter('datasource'):
        if ds.get('name') is None or ds.find('column') is None and ds.find('connection') is None:
            continue
        ds_name, ds_caption = ds.get('name'), ds.get('caption') or ds.get('name')
        for col in ds.findall('column'):
            captions[(ds_name, strip(col.get('name')))] = col.get('caption') or strip(col.get('name'))
            captions.setdefault(('*', strip(col.get('name'))), col.get('caption') or strip(col.get('name')))
        if ds_name == 'Parameters':
            for col in ds.findall('column'):
                parameters.append({'name': col.get('caption') or strip(col.get('name')), 'datatype': col.get('datatype', ''),
                                   'value': col.get('value', ''), 'domain': col.get('param-domain-type', '')})
            continue
        connections = []
        for c in ds.iter('connection'):
            cls = c.get('class')
            if cls and cls != 'federated':
                connections.append({'class': cls, 'server': c.get('server', ''), 'database': c.get('dbname', ''),
                                    'file': c.get('filename', ''), 'username': bool(c.get('username'))})
        tables = sorted({r.get('table') or r.get('name') for r in ds.iter('relation') if r.get('type') == 'table'})
        fields = ds.findall('column')
        sources.append({'name': ds_name, 'caption': ds_caption, 'connections': connections, 'tables': tables,
                        'fields': len(fields), 'extract': ds.find('extract') is not None and ds.find('extract').get('enabled') == 'true'})
        for col in fields:
            calc = col.find('calculation')
            if calc is None or calc.get('formula') is None:
                continue
            calcs.append({'datasource': ds_caption, 'ds': ds_name, 'id': strip(col.get('name')), 'name': col.get('caption') or strip(col.get('name')),
                          'role': col.get('role', ''), 'datatype': col.get('datatype', ''), 'raw': calc.get('formula')})

    def readable(formula, ds):
        def repl(m):
            inner = m.group(1)
            if inner == 'Parameters':
                return '[Parameters]'
            return '[' + captions.get((ds, inner), captions.get(('*', inner), inner)) + ']'
        return re.sub(r'\[([^\]]+)\]', repl, formula)

    for c in calcs:
        c['formula'] = readable(c['raw'], c['ds'])
        c['lod'] = bool(LOD.search(c['raw']))
        c['table_calc'] = bool(TABLE_CALC.search(c['raw']))
    worksheets = []
    used = set()
    for ws in root.iter('worksheet'):
        fields = sorted({strip(ci.get('column')) for ci in ws.iter('column-instance')})
        dss = sorted({d.get('datasource') for d in ws.iter('datasource-dependencies')})
        used.update(fields)
        worksheets.append({'name': ws.get('name'), 'datasources': dss, 'fields': [captions.get(('*', f), f) for f in fields]})
    # A calculation is used if a sheet shows it or another used calculation refers to it.
    changed = True
    while changed:
        changed = False
        for c in calcs:
            if c['id'] in used:
                for ref in re.findall(r'\[([^\]]+)\]', c['raw']):
                    if ref not in used:
                        used.add(ref)
                        changed = True
    for c in calcs:
        c['used'] = c['id'] in used
    dashboards = []
    for db in root.iter('dashboard'):
        sheets = sorted({z.get('name') for z in db.iter('zone') if z.get('name')})
        dashboards.append({'name': db.get('name'), 'sheets': sheets})
    on_dash = {s for d in dashboards for s in d['sheets']}
    for ws in worksheets:
        ws['on_dashboard'] = ws['name'] in on_dash
    return {'version': root.get('version', ''), 'build': root.get('source-build', ''), 'datasources': sources, 'parameters': parameters,
            'calculations': [{k: v for k, v in c.items() if k not in ('raw', 'ds')} for c in calcs],
            'worksheets': worksheets, 'dashboards': dashboards, 'packaged_files': list(packaged)}


def findings(inv):
    out = []
    for ds in inv['datasources']:
        for c in ds['connections']:
            if c['file'] and re.match(r'^[A-Za-z]:[\\/]|^\\\\', c['file']):
                out.append(('yüksek', f"'{ds['caption']}' yerel dosyaya bağlı ({c['class']}: {c['file']}). Sunucuda/Cloud’da yenilenmez."))
            if c['username']:
                out.append(('orta', f"'{ds['caption']}' bağlantısında kullanıcı adı kayıtlı. Yayın öncesi kimlik doğrulama yöntemini kontrol edin."))
    unused = [c['name'] for c in inv['calculations'] if not c['used']]
    if unused:
        out.append(('düşük', f'Hiçbir sayfada kullanılmayan hesaplanan alan: {", ".join(unused)}'))
    loose = [w['name'] for w in inv['worksheets'] if not w['on_dashboard']]
    if loose and inv['dashboards']:
        out.append(('bilgi', f'Panoda olmayan sayfa: {", ".join(loose)}'))
    lods = [c['name'] for c in inv['calculations'] if c['lod']]
    if lods:
        out.append(('bilgi', f'LOD ifadesi ({len(lods)}): {", ".join(lods)}. Hedef araçta (ör. DAX) karşılığı ayrıca tasarlanmalı.'))
    tcs = [c['name'] for c in inv['calculations'] if c['table_calc']]
    if tcs:
        out.append(('bilgi', f'Tablo hesaplaması ({len(tcs)}): {", ".join(tcs)}. Sonuç görünüm düzenine bağlıdır.'))
    return out


def markdown(inv, source, found):
    lines = [f'# Tableau envanteri — {source}', '',
             f"- Veri kaynağı: **{len(inv['datasources'])}** · Hesaplanan alan: **{len(inv['calculations'])}** · Parametre: **{len(inv['parameters'])}**",
             f"- Sayfa: **{len(inv['worksheets'])}** · Pano: **{len(inv['dashboards'])}** · Sürüm: {inv['version'] or '—'} ({inv['build'] or '—'})", '',
             '## Bulgular', '', md_table(['Önem', 'Bulgu'], found) if found else '_Belirgin bir risk bulunmadı._\n',
             '## Veri kaynakları', '',
             md_table(['Kaynak', 'Bağlantı', 'Sunucu / dosya', 'Veritabanı', 'Tablolar', 'Alan', 'Extract'],
                      [[d['caption'], ', '.join(c['class'] for c in d['connections']) or '—', ', '.join(filter(None, (c['server'] or c['file'] for c in d['connections']))) or '—',
                        ', '.join(filter(None, (c['database'] for c in d['connections']))) or '—', ', '.join(d['tables']) or '—', d['fields'], 'evet' if d['extract'] else 'hayır']
                       for d in inv['datasources']]),
             '## Hesaplanan alanlar', '',
             md_table(['Alan', 'Kaynak', 'Formül', 'Tür', 'Kullanılıyor'],
                      [[c['name'], c['datasource'], f"`{c['formula']}`", 'LOD' if c['lod'] else ('tablo hes.' if c['table_calc'] else 'satır/toplam'), 'evet' if c['used'] else 'hayır']
                       for c in inv['calculations']])]
    if inv['parameters']:
        lines += ['## Parametreler', '', md_table(['Parametre', 'Tip', 'Değer', 'Alan türü'], [[p['name'], p['datatype'], p['value'], p['domain']] for p in inv['parameters']])]
    lines += ['## Sayfalar ve panolar', '', md_table(['Sayfa', 'Kullanılan alanlar', 'Panoda'], [[w['name'], ', '.join(w['fields']), 'evet' if w['on_dashboard'] else 'hayır'] for w in inv['worksheets']])]
    for d in inv['dashboards']:
        lines.append(f"- **{d['name']}**: {', '.join(d['sheets'])}")
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='Tableau .twb/.twbx envanteri (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'satis-panosu.twbx')))
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        root, packaged = read_twb(args.input)
        if root.tag != 'workbook':
            raise ValueError('Dosya bir Tableau çalışma kitabı değil (kök öğe <workbook> değil).')
        inv = inventory(root, packaged)
        found = findings(inv)
        out = output_dir(args.output)
        write_json(out / 'envanter.json', inv)
        tabular.write_csv(out / 'hesaplanan-alanlar.csv', ['datasource', 'name', 'formula', 'role', 'datatype', 'lod', 'table_calc', 'used'], inv['calculations'])
        write_text(out / 'tableau-envanter.md', markdown(inv, Path(args.input).name, found))
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        p.exit(1, f'[X] {exc}\n')
    print(f"[OK] {len(inv['datasources'])} kaynak, {len(inv['calculations'])} hesaplanan alan, {len(inv['worksheets'])} sayfa, {len(found)} bulgu. Çıktı: {out / 'tableau-envanter.md'}")


if __name__ == '__main__':
    main()
