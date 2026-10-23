"""End-to-end tests of local code blocks and agents. Every test runs the *packaged* copy (as a user would after unzipping)."""
import csv, io, json, os, re, subprocess, sys, tempfile, threading, unittest, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'catalog'), str(ROOT / 'scripts'), str(ROOT / 'library' / 'blocks'), str(ROOT / 'library' / 'agents')]
from local_catalog import LOCAL
import local_package, tabular, common
from common import mask_text
from sql_soyagaci import analyse
from qlik_soyagaci import parse as qlik_parse
from kalite_kontrol import validate_rules
from tablo_mutabakati import reconcile
from sema_farki import narrowing, schema_from_ddl
from tarih_tablosu import build as build_dates
from donem_yorumu import unknown_numbers, allowed_numbers
from datetime import date

ITEMS = {i['id']: i for i in LOCAL}
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.endswith(('_KEY', 'TOKEN')) and k not in ('PROVIDER', 'MODEL', 'OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'OLLAMA_URL')}
CLEAN_ENV['PYTHONIOENCODING'] = 'utf-8'


class Package:
    def __init__(self, item_id):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = local_package.extract(ITEMS[item_id], self.tmp.name)

    def run(self, *args, env=None, stdin=None):
        return subprocess.run([sys.executable, 'main.py', *args], cwd=self.dir, capture_output=True, text=True, encoding='utf-8',
                              env={**CLEAN_ENV, **(env or {})}, stdin=stdin if stdin is not None else subprocess.DEVNULL, timeout=120)

    def out(self, name):
        return self.dir / 'output' / name

    def json(self, name):
        return json.loads(self.out(name).read_text(encoding='utf-8'))

    def csv(self, name):
        with open(self.out(name), encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))

    def close(self):
        self.tmp.cleanup()


class LocalTestCase(unittest.TestCase):
    def package(self, item_id):
        p = Package(item_id)
        self.addCleanup(p.close)
        return p

    def ok(self, done):
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn('Traceback', done.stderr)
        return done


class PackageShape(LocalTestCase):
    def test_every_local_package_is_self_contained_and_has_no_network_code(self):
        self.assertEqual(len([i for i in LOCAL if not i.get('op')]), 127)
        self.assertEqual(sum(i['kind'] == 'agent' for i in LOCAL), 115)
        self.assertEqual(len({i['id'] for i in LOCAL}), len(LOCAL))
        for item in LOCAL:
            names = local_package.files(item)
            for required in ('main.py', 'README.md', 'LICENSE', 'task.json', 'run.bat', '.gitignore'):
                self.assertIn(required, names, item['id'])
            self.assertEqual(item['kind'] == 'agent', '.env.example' in names, item['id'])
            if item.get('spec', {}).get('kind') != 'none':  # generators (e.g. loan schedule) take parameters, not files
                self.assertTrue(any(n.startswith('ornek/') for n in names), item['id'])
            code = b''.join(v for k, v in names.items() if k.endswith('.py') and k != 'llm.py')
            for forbidden in (b'import socket', b'import urllib', b'import requests', b'import http', b'subprocess', b'os.system'):
                self.assertNotIn(forbidden, code, f"{item['id']} code block must stay offline")
            manifest = json.loads(names['task.json'])
            self.assertEqual(manifest['id'], item['id'])

    def test_every_local_package_reaches_the_site_catalog(self):
        from catalog import catalog
        listed = {task.get('local_id') for domain in catalog() for tool in domain['tools'] for product in tool['products'] for task in product['tasks']}
        self.assertEqual([i['id'] for i in LOCAL if i['id'] not in listed], [])  # an unknown domain/product name silently drops a package


class CodeBlocks(LocalTestCase):
    def test_veri_profili_sample(self):
        p = self.package('veri-profili')
        self.ok(p.run())
        data = p.json('profile.json')
        cols = {c['name']: c for c in data['columns']}
        self.assertEqual((data['row_count'], data['duplicate_rows']), (41, 1))
        self.assertEqual(cols['musteri_eposta']['pii'], 'e-posta')
        self.assertEqual(cols['tutar']['type'], 'ondalık')
        self.assertIn('negatif değer var', cols['tutar']['warnings'])
        self.assertTrue(any('birden çok tarih biçimi' in w for w in cols['siparis_tarihi']['warnings']))
        # Personal data is never copied into the outputs.
        for name in ('profile.json', 'profil.md'):
            self.assertNotIn('@ornek.test', p.out(name).read_text(encoding='utf-8'))

    def test_veri_profili_reads_xlsx_and_rejects_bad_files(self):
        p = self.package('veri-profili')
        xlsx = p.dir / 'test.xlsx'
        write_xlsx(xlsx, [['Ad', 'Tutar', 'Tarih'], ['Ayşe', 10.5, 46000], ['Ali', 3, 46001]])
        self.ok(p.run('--input', str(xlsx)))
        cols = {c['name']: c for c in p.json('profile.json')['columns']}
        self.assertEqual((cols['Ad']['distinct'], cols['Tutar']['type']), (2, 'ondalık'))
        (p.dir / 'bad.csv').write_text('a,a\n1,2\n', encoding='utf-8')
        done = p.run('--input', 'bad.csv')
        self.assertEqual(done.returncode, 1)
        self.assertIn('mükerrer sütun', done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        done = p.run('--input', 'yok.csv')
        self.assertIn('bulunamadı', done.stderr)

    def test_kalite_kontrol_sample_and_strict_exit_code(self):
        p = self.package('kalite-kontrol')
        done = p.run('--strict')
        self.assertEqual(done.returncode, 2, done.stderr)
        results = p.json('sonuc.json')['results']
        self.assertEqual(sum(not r['passed'] for r in results), 6)
        broken = {r['rule'].split(':')[0] + ':' + r['check'] for r in results if not r['passed']}
        self.assertIn('siparis_no:unique', broken)
        self.assertIn('tutar:min', broken)
        violations = p.csv('ihlaller.csv')
        self.assertEqual(len(violations), 8)
        self.assertIn({'kural': '8', 'satir': '6'}, [{'kural': v['kural'], 'satir': v['satir']} for v in violations])
        self.ok(p.run())  # without --strict the report is written and exit code is 0

    def test_kalite_kontrol_rejects_typos(self):
        with self.assertRaisesRegex(ValueError, 'bilinmeyen kontrol'):
            validate_rules({'rules': [{'column': 'a', 'check': 'notnull'}]}, ['a'])
        with self.assertRaisesRegex(ValueError, 'sütunu dosyada yok'):
            validate_rules({'rules': [{'column': 'b', 'check': 'not_null'}]}, ['a'])
        with self.assertRaisesRegex(ValueError, 'between'):
            validate_rules({'rules': [{'column': 'a', 'check': 'between', 'value': [5, 1]}]}, ['a'])

    def test_tablo_mutabakati_sample_and_tolerance(self):
        p = self.package('tablo-mutabakati')
        self.ok(p.run('--tolerance', '0.01'))
        summary = p.json('ozet.json')
        self.assertEqual((summary['matched_rows'], summary['changed_rows'], summary['only_source'], summary['only_target']), (11, 2, 1, 1))
        self.assertEqual(summary['only_in_target_columns'], ['yukleme_tarihi'])
        self.assertEqual(p.csv('sadece-kaynakta.csv')[0]['hesap_kodu'], '621')
        self.assertEqual(p.csv('sadece-hedefte.csv')[0]['hesap_kodu'], '780')
        diffs = {(d['hesap_kodu'], d['sutun']) for d in p.csv('farklar.csv')}
        self.assertEqual(diffs, {('120', 'borc'), ('320', 'hesap_adi')})
        p2 = self.package('tablo-mutabakati')
        self.ok(p2.run())  # no tolerance: the 0.004 rounding difference is reported too
        self.assertEqual(p2.json('ozet.json')['changed_rows'], 3)

    def test_tablo_mutabakati_rejects_duplicate_keys(self):
        with self.assertRaisesRegex(ValueError, 'benzersiz değil'):
            reconcile(['k', 'v'], [{'k': '1', 'v': 'a'}, {'k': '1', 'v': 'b'}], ['k', 'v'], [{'k': '1', 'v': 'a'}], ['k'])

    def test_sql_soyagaci_sample(self):
        p = self.package('sql-soyagaci')
        self.ok(p.run())
        data = p.json('lineage.json')
        self.assertEqual(data['final_targets'], ['mart.musteri_ozet'])
        self.assertEqual(data['raw_sources'], ['erp.dbo.musteriler', 'erp.dbo.siparisler', 'ref.segmentler'])
        risks = [r for s in data['statements'] for r in s['risks']]
        self.assertIn('WHERE olmadan UPDATE', risks)
        self.assertNotIn('WHERE olmadan DELETE', risks)

    def test_sql_parser_ignores_ctes_strings_and_comments(self):
        s = analyse("-- from fake.comment\nWITH x AS (SELECT * FROM a.b) INSERT INTO t.out SELECT 'from fake.string' FROM x JOIN c.d ON 1=1", 'f.sql')[0]
        self.assertEqual((s['target'], s['sources'], s['ctes']), ('t.out', ['a.b', 'c.d'], ['x']))

    def test_pbit_model_sample(self):
        p = self.package('pbit-model')
        self.ok(p.run())
        md = p.out('veri-modeli.md').read_text(encoding='utf-8')
        for expected in ('Çift yönlü filtre', '[Toplam İade]', 'Otomatik tarih/saat açık', 'İlişkisiz tablo: Hedefler', 'FILTER kullanan ölçü: Büyük Siparişler'):
            self.assertIn(expected, md)
        self.assertEqual(len(p.csv('olculer.csv')), 6)
        model = p.json('model.json')
        self.assertEqual(model['pages'][0]['name'], 'Özet')
        (p.dir / 'rapor.pbix').write_bytes(b'PK')
        done = p.run('--input', 'rapor.pbix')
        self.assertEqual(done.returncode, 1)
        self.assertIn('.pbit', done.stderr)

    def test_tableau_envanter_sample(self):
        p = self.package('tableau-envanter')
        self.ok(p.run())
        inv = p.json('envanter.json')
        calcs = {c['name']: c for c in inv['calculations']}
        self.assertEqual(len(calcs), 4)
        self.assertEqual(calcs['Kâr Marjı']['formula'], '[Kâr] / SUM([Tutar])')
        self.assertTrue(calcs['Müşteri İlk Sipariş']['lod'])
        self.assertEqual(sorted(c for c, v in calcs.items() if not v['used']), ['Hedefe Uzaklık', 'Müşteri İlk Sipariş'])
        self.assertIn('yerel dosyaya bağlı', p.out('tableau-envanter.md').read_text(encoding='utf-8'))
        (p.dir / 'x.twb').write_text('<!DOCTYPE x [<!ENTITY a "b">]><workbook/>', encoding='utf-8')
        self.assertIn('DTD', p.run('--input', 'x.twb').stderr)

    def test_qlik_soyagaci_sample(self):
        p = self.package('qlik-soyagaci')
        self.ok(p.run())
        data = p.json('lineage.json')
        steps = [(t['line'], t['name'], t['prefix']) for t in data['tables']]
        self.assertIn((19, 'Satislar', ''), steps)
        self.assertIn((38, 'Satislar', 'LEFT JOIN'), steps)
        self.assertEqual(data['stores'][0]['target'], 'lib://Veri/QVD/satislar.qvd')
        self.assertEqual(len(data['warnings']), 1)

    def test_qlik_parser_keeps_lib_urls_and_flags_order(self):
        r = qlik_parse("A: LOAD * FROM [lib://X/a.qvd] (qvd); // yorum\nB: LOAD x RESIDENT C;")
        self.assertEqual(r['tables'][0]['source'], 'lib://X/a.qvd')
        self.assertTrue(any('RESIDENT C' in w for w in r['warnings']))

    def test_dbt_denetim_sample(self):
        p = self.package('dbt-denetim')
        done = self.ok(p.run())
        self.assertIn('5 sorunlu', done.stdout)
        audit = p.json('audit.json')
        models = {m['model']: m for m in audit['models']}
        self.assertIn('sabit tablo adı: analytics.kur_tablosu', models['int_siparis_kalemleri']['sorunlar'])
        self.assertIn('mart doğrudan kaynak okuyor', models['fct_satislar']['sorunlar'])
        self.assertEqual(models['dim_musteri']['sorunlar'], '')
        self.assertEqual(audit['summary']['unused_sources'], 1)


    def test_kvkk_maskeleme_pseudonyms_are_stable_and_nothing_leaks(self):
        p = self.package('kvkk-maskeleme')
        done = self.ok(p.run('--salt', 'test-tuzu'))
        self.assertIn('Kalan kişisel veri: 0', done.stdout)
        summary = p.json('ozet.json')
        self.assertEqual(sorted(summary['columns']), ['ad_soyad', 'dogum_tarihi', 'eposta', 'iban', 'tckn', 'telefon'])
        self.assertEqual(summary['embedded'], {'notlar': 2})  # notes keep their text, only the embedded phone/e-mail is masked
        rows = p.csv('maskeli.csv')
        text = p.out('maskeli.csv').read_text(encoding='utf-8-sig')
        for leaked in ('@ornek.test', 'Ayşe Yılmaz', '10000791952'):
            self.assertNotIn(leaked, text)
        self.assertEqual(rows[8]['notlar'], 'VIP, teslimatta arayın')
        self.assertEqual(rows[2]['notlar'], 'Aradı, yeni numarası [TELEFON]')
        self.assertEqual(rows[0]['sehir'], 'İzmir')
        first = rows[0]['tckn']
        self.ok(p.run('--salt', 'test-tuzu'))
        self.assertEqual(p.csv('maskeli.csv')[0]['tckn'], first)  # same salt → same pseudonym, joins keep working
        self.ok(p.run('--salt', 'baska-tuz'))
        self.assertNotEqual(p.csv('maskeli.csv')[0]['tckn'], first)
        self.ok(p.run('--method', 'kismi', '--keep', 'dogum_tarihi'))
        row = p.csv('maskeli.csv')[0]
        self.assertEqual((row['tckn'], row['eposta'], row['ad_soyad']), ('10*******52', 'm***@ornek.test', 'A*** Y***'))
        self.assertEqual(row['dogum_tarihi'], '19.10.1990')
        done = p.run('--columns', 'yok')
        self.assertEqual(done.returncode, 1)
        self.assertIn('sütunu dosyada yok', done.stderr)

    def test_sema_farki_sample_and_strict(self):
        p = self.package('sema-farki')
        done = p.run('--strict')
        self.assertEqual(done.returncode, 2, done.stderr)
        diffs = {(d['tablo'], d['sutun'], d['degisiklik']): d for d in p.json('fark.json')['differences']}
        self.assertIn(('dbo.Musteriler', 'TelefonNo', 'olası yeniden adlandırma'), diffs)
        self.assertIn(('dbo.Iadeler', '', 'tablo eklendi'), diffs)
        self.assertIn(('dbo.EskiKampanyalar', '', 'tablo silindi'), diffs)
        self.assertEqual(diffs[('dbo.Siparisler', 'Tutar', 'tip değişti')]['risk'], 'daralma: veri kesilebilir')
        self.assertEqual(diffs[('dbo.Siparisler', 'SiparisTarihi', 'tip değişti')]['risk'], '')
        self.assertEqual(diffs[('dbo.Siparisler', 'Durum', 'tip değişti')]['risk'], '')
        self.assertIn(('dbo.Musteriler', 'KayitTarihi', 'NOT NULL oldu'), diffs)
        alter = p.out('alter-taslak.sql').read_text(encoding='utf-8')
        self.assertIn('-- DİKKAT veri kaybı: ALTER TABLE dbo.Siparisler DROP COLUMN Aciklama;', alter)
        self.ok(p.run('--old', 'ornek/sema_v1.sql', '--new', 'ornek/sema_v1.sql', '--strict'))
        self.assertIn('İki şema aynı', p.out('sema-farki.md').read_text(encoding='utf-8'))

    def test_tarih_tablosu_sample(self):
        p = self.package('tarih-tablosu')
        self.ok(p.run('--start', '2026-01-01', '--end', '2026-12-31', '--fiscal-start', '4'))
        rows = {r['tarih']: r for r in p.csv('tarih-tablosu.csv')}
        self.assertEqual(len(rows), 365)
        self.assertEqual((rows['2026-10-29']['tatil_adi'], rows['2026-10-29']['is_gunu']), ('Cumhuriyet Bayramı', '0'))
        self.assertEqual((rows['2026-10-28']['yarim_gun'], rows['2026-10-28']['is_gunu']), ('1', '1'))
        self.assertEqual(rows['2026-05-27']['tatil_adi'], 'Kurban Bayramı 1. gün')  # from ornek/tatiller.csv
        self.assertEqual(rows['2026-03-31']['mali_yil'], '2026')
        self.assertEqual((rows['2026-04-01']['mali_yil'], rows['2026-04-01']['mali_ay']), ('2027', '1'))
        self.assertEqual((rows['2026-12-31']['iso_hafta'], rows['2026-01-01']['gun_adi']), ('53', 'Perşembe'))
        self.assertEqual(rows['2026-02-02']['aydaki_is_gunu_sirasi'], '1')
        self.assertIn('INSERT INTO dim_tarih', p.out('tarih-tablosu.sql').read_text(encoding='utf-8'))
        done = p.run('--start', '2026-02-30')
        self.assertEqual(done.returncode, 1)
        self.assertIn('YYYY-AA-GG', done.stderr)

    def test_dosya_birlestir_sample(self):
        p = self.package('dosya-birlestir')
        self.ok(p.run())
        summary = p.json('ozet.json')
        self.assertEqual((summary['rows'], summary['duplicate_rows'], summary['columns']), (16, 1, ['Sipariş No', 'Sipariş Tarihi', 'Bölge', 'Tutar', 'kanal']))
        self.assertEqual(summary['mixed_decimal_columns'], ['Tutar'])
        missing = {f['dosya']: f['eksik'] for f in summary['files']}
        self.assertEqual(missing, {'2026-07.csv': ['kanal'], '2026-08.csv': [], '2026-09.csv': ['Bölge']})
        rows = p.csv('birlesik.csv')
        self.assertEqual(rows[-1]['kaynak_dosya'], '2026-09.csv')
        self.ok(p.run('--normalize-numbers'))
        amounts = [r['Tutar'] for r in p.csv('birlesik.csv')]
        self.assertTrue(all(',' not in a for a in amounts), amounts)
        done = p.run('--strict')
        self.assertEqual(done.returncode, 1)
        self.assertIn('--strict', done.stderr)


class SinglePurposeBlocks(LocalTestCase):
    """Every block registered with islem.op runs from its packaged copy on its own sample and must reproduce the
    summary values written next to it (expect=...). The values were checked by hand against the sample files."""

    def test_every_registered_block_on_its_sample(self):
        ops = [i for i in LOCAL if i.get('op')]
        self.assertGreaterEqual(len(ops), 40)
        for item in ops:
            spec = item['spec']
            with self.subTest(item['id']):
                p = self.package(item['id'])
                done = p.run(*spec['test_args'])
                self.assertEqual(done.returncode, spec['exit_code'], done.stderr + done.stdout)
                self.assertNotIn('Traceback', done.stderr)
                summary = p.json('ozet.json')
                for key, value in spec['expect'].items():
                    self.assertEqual(summary.get(key), value, f"{item['id']}: {key}")
                self.assertTrue(p.out('rapor.md').is_file())
                self.assertTrue(spec['expect'], f"{item['id']} has no expected values")

    def test_bad_input_is_a_readable_error(self):
        for item_id, args, message in [('filtrele', ['--where', '__import__("os")'], 'yalnızca şu fonksiyonlar'), ('filtrele', ['--where', 'yok > 1'], 'bilinmeyen ad'),
                                       ('grupla', ['--by', 'olmayan'], 'dosyada olmayan'), ('tckn-dogrula', ['--column', 'yok'], 'sütunu dosyada yok')]:
            with self.subTest(item_id):
                done = self.package(item_id).run(*args)
                self.assertEqual(done.returncode, 1)
                self.assertIn(message, done.stderr)
                self.assertNotIn('Traceback', done.stderr)

    def test_xlsx_writer_round_trips_and_keeps_numbers_numeric(self):
        p = self.package('csv-xlsx')
        self.ok(p.run())
        cols, rows = tabular.read(p.out('veri.xlsx'))
        src_cols, src_rows = tabular.read(p.dir / 'ornek' / 'satislar.csv')
        self.assertEqual((cols, len(rows)), (src_cols, len(src_rows)))
        self.assertEqual((rows[0]['tutar'], rows[0]['siparis_no']), ('1900.0', 'S1001'))  # stored as a number, read back as one

    def test_qlik_report_template_references_resolve(self):
        p = self.package('qlik-rapor-sablonu')
        self.ok(p.run())
        app = p.out('qlik-uygulama')
        script = (app / 'script.qvs').read_text(encoding='utf-8')
        fields = set(re.findall(r'\bas\s+\[([^\]]+)\]', script, re.I)) | set(re.findall(r'^\s+\[([^\]]+)\],?$', script, re.M))
        self.assertIn('SatisDetay', [t['name'] for t in qlik_parse(script)['tables']])
        dims, meas = json.loads((app / 'dimensions.json').read_text(encoding='utf-8')), json.loads((app / 'measures.json').read_text(encoding='utf-8'))
        library = {o['qInfo']['qId'] for o in dims + meas}
        for d in dims:
            self.assertLessEqual(set(d['qDim']['qFieldDefs']), fields)
        for m in meas:
            self.assertLessEqual(set(re.findall(r'\[([^\]]+)\]', m['qMeasure']['qDef'])), fields, m['qMeasure']['qDef'])
        sheet = json.loads((app / 'objects' / 'rapor-sayfasi.json').read_text(encoding='utf-8'))
        children = sheet['qChildren']
        self.assertEqual([c['qProperty']['qInfo']['qId'] for c in children], [c['name'] for c in sheet['qProperty']['cells']])
        cells = set()
        for c in sheet['qProperty']['cells']:
            area = {(x, y) for x in range(c['col'], c['col'] + c['colspan']) for y in range(c['row'], c['row'] + c['rowspan'])}
            self.assertFalse(area & cells, c['name'])
            cells |= area
        self.assertEqual(len(cells), 24 * 12)  # the whole grid is used, nothing overlaps or overflows

        def walk(o):
            prop = o['qProperty']
            self.assertTrue(prop['qInfo']['qId'] and prop['qInfo']['qType'])  # corectl's rule for GenericObjectEntry files
            cube = prop.get('qHyperCubeDef', {})
            for ref in [x['qLibraryId'] for x in cube.get('qDimensions', []) + cube.get('qMeasures', [])] + ([prop['qListObjectDef']['qLibraryId']] if 'qListObjectDef' in prop else []):
                self.assertIn(ref, library)
            for c in o['qChildren']:
                walk(c)
        walk(sheet)
        self.assertIn('qlik app build', (app / 'build.cmd').read_text(encoding='utf-8'))

    def test_pbip_report_template_matches_its_model(self):
        p = self.package('pbi-rapor-sablonu')
        self.ok(p.run())
        root = p.out('pbip')
        pbip = next(root.glob('*.pbip'))
        report = root / json.loads(pbip.read_text(encoding='utf-8'))['artifacts'][0]['report']['path']
        model = (report / json.loads((report / 'definition.pbir').read_text(encoding='utf-8'))['datasetReference']['byPath']['path']).resolve()
        self.assertTrue((model / 'definition' / 'model.tmdl').is_file())
        names = {}
        for tmdl in (model / 'definition' / 'tables').glob('*.tmdl'):
            text = tmdl.read_text(encoding='utf-8')
            table = re.match(r'table (\S+)', text).group(1)
            names[table] = {n.strip("'") for n in re.findall(r"^\t(?:measure|column) ('[^']+'|[^\s=]+)", text, re.M)}
        self.assertEqual(set(names), {'SatisDetay', 'Takvim'})
        rel = (model / 'definition' / 'relationships.tmdl').read_text(encoding='utf-8')
        self.assertIn("fromColumn: SatisDetay.'Siparis Tarihi'", rel)
        visuals = list(report.rglob('visual.json'))
        self.assertEqual(len(visuals), 12)
        for f in report.rglob('*.json'):
            doc = json.loads(f.read_text(encoding='utf-8'))
            self.assertTrue(doc['$schema'].startswith('https://developer.microsoft.com/json-schemas/fabric/item/report/'), f)
        for f in visuals:
            for field in re.findall(r'"Entity": "([^"]+)"\s*\}\s*\},\s*"Property": "([^"]+)"', f.read_text(encoding='utf-8')):
                self.assertIn(field[1], names[field[0]], f'{f.parent.name}: {field}')

    def test_tableau_report_template_is_consistent(self):
        import xml.etree.ElementTree as ET
        p = self.package('tableau-rapor-sablonu')
        self.ok(p.run())
        wb = ET.parse(p.out('rapor.twb')).getroot()
        self.assertEqual(wb.get('version'), '26.1')
        sheets = {w.get('name') for w in wb.iter('worksheet')}
        zones = {z.get('name') for z in wb.iter('zone') if z.get('name')}
        self.assertEqual(zones, sheets)
        source_cols = {c.get('name') for c in wb.find('datasources/datasource').findall('column')}
        for inst in wb.iter('column-instance'):
            self.assertIn(inst.get('column'), source_cols)
        for shelf in list(wb.iter('rows')) + list(wb.iter('cols')):
            for ref in re.findall(r'\]\.(\[[^\]]+\])', shelf.text or ''):
                self.assertTrue(any(i.get('name') == ref for i in wb.iter('column-instance')), ref)

    def test_excel_report_template_formulas_and_dirty_input(self):
        p = self.package('excel-rapor-sablonu')
        self.ok(p.run())
        with zipfile.ZipFile(p.out('rapor.xlsx')) as z:
            parts = set(z.namelist())
            types = z.read('[Content_Types].xml').decode('utf-8')
            for name in parts:
                if name.endswith(('.xml', '.rels')):
                    import xml.dom.minidom
                    xml.dom.minidom.parseString(z.read(name))
                    if not name.endswith('.rels') and name != '[Content_Types].xml':
                        self.assertIn(f'PartName="/{name}"', types, name)
            summary = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        self.assertIn('<c r="A5" s="4"><f>SUM(Veri!$J$2:$J$241)</f><v>5912910.0</v></c>', summary)
        self.assertIn('SUMPRODUCT((Veri!$A$2:$A$241&lt;&gt;"")/COUNTIF(Veri!$A$2:$A$241,Veri!$A$2:$A$241&amp;""))', summary)
        cols, rows = tabular.read(p.out('rapor.xlsx'), 'Veri')
        self.assertEqual((len(cols), len(rows)), (10, 240))
        # Dirty file: "Teslim edildi" and "teslim edildi " are one value for SUMIFS, so the cached value must be their sum and the user is warned.
        dirty = self.package('excel-rapor-sablonu')
        self.ok(dirty.run('--input', str(ROOT / 'library' / 'samples' / 'satislar.csv')))
        plan = {r['ad']: r for r in dirty.csv('sonuc.csv')}
        self.assertEqual(plan['Durum bazında Toplam Tutar']['tanim'], '3 değer; en yüksek Teslim edildi')
        self.assertEqual(float(plan['Durum bazında Toplam Tutar']['deger']), 113860.0)
        self.assertIn('büyük/küçük harfle', dirty.out('rapor.md').read_text(encoding='utf-8'))

    def test_pptx_report_template_parts_and_numbers(self):
        import xml.dom.minidom
        p = self.package('pptx-rapor-sablonu')
        self.ok(p.run())
        with zipfile.ZipFile(p.out('rapor.pptx')) as z:
            names = set(z.namelist())
            types = z.read('[Content_Types].xml').decode('utf-8')
            for name in names:
                if name.endswith(('.xml', '.rels')):
                    xml.dom.minidom.parseString(z.read(name))
                    if not name.endswith('.rels') and name != '[Content_Types].xml':
                        self.assertIn(f'PartName="/{name}"', types, name)
            for rels in [n for n in names if n.endswith('.rels') and n != '_rels/.rels']:
                base = rels.replace('_rels/', '').rsplit('/', 1)[0]
                for target in re.findall(r'Target="([^"]+)"', z.read(rels).decode('utf-8')):
                    resolved = '/'.join(x for x in (base + '/' + target).split('/') if x != '.')
                    while '/../' in resolved:
                        resolved = re.sub(r'[^/]+/\.\./', '', resolved, count=1)
                    self.assertIn(resolved, names, f'{rels} -> {target}')
            kpis = z.read('ppt/slides/slide2.xml').decode('utf-8')
            chart = z.read('ppt/charts/chart2.xml').decode('utf-8')
        self.assertEqual(len([n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]), 6)
        self.assertIn('<a:t>5.912.910,00</a:t>', kpis)
        self.assertIn('<c:v>Karadeniz</c:v>', chart)
        self.assertIn('<c:v>1776520.0</c:v>', chart)

    def test_dbt_and_lookml_templates_are_wired_together(self):
        p = self.package('dbt-proje-sablonu')
        self.ok(p.run())
        proj = p.out('dbt-proje')
        models = {f.stem for f in proj.rglob('models/**/*.sql')}
        seeds = {f.stem for f in (proj / 'seeds').glob('*.csv')}
        for f in list(proj.rglob('*.sql')):
            for ref in re.findall(r"ref\('([^']+)'\)", f.read_text(encoding='utf-8')):
                self.assertIn(ref, models | seeds, f'{f.name}: {ref}')
        staging = (proj / 'models' / 'staging' / 'stg_satis_detay.sql').read_text(encoding='utf-8')
        aliases = set(re.findall(r' as (\w+),?$', staging, re.M))
        schema = (proj / 'models' / 'schema.yml').read_text(encoding='utf-8')
        self.assertLessEqual(set(re.findall(r'^      - name: (\w+)$', schema, re.M)), aliases)
        self.assertIn("{{ metin_tarih(\"siparis_tarihi\") }}", staging)
        self.assertIn('macro duckdb__metin_tarih', (proj / 'macros' / 'metin_tarih.sql').read_text(encoding='utf-8'))
        self.assertIn('+delimiter: ;', (proj / 'dbt_project.yml').read_text(encoding='utf-8'))
        lk = self.package('lookml-rapor-sablonu')
        self.ok(lk.run())
        view = (lk.out('lookml') / 'views' / 'satis_detay.view.lkml').read_text(encoding='utf-8')
        self.assertIn('sql_table_name: analytics.fct_satis_detay ;;', view)  # the table the dbt template builds
        fields = set(re.findall(r'^  (?:dimension|measure): (\w+) \{', view, re.M))
        for group in re.findall(r'^  dimension_group: (\w+) \{', view, re.M):
            fields |= {f'{group}_{t}' for t in ('raw', 'date', 'week', 'month', 'quarter', 'year')}
        dashboard = next((lk.out('lookml') / 'dashboards').glob('*.dashboard.lookml')).read_text(encoding='utf-8')
        refs = set(re.findall(r'satis_detay\.(\w+)', dashboard))
        self.assertTrue(refs)
        self.assertLessEqual(refs, fields)

    def test_qlik_and_power_bi_model_blocks(self):
        p = self.package('qlik-zaman-zekasi')
        self.ok(p.run('--fiscal-start', '4'))
        rows = {r['olcu']: r for r in p.csv('sonuc.csv')}
        self.assertEqual((rows['Ciro YTD']['baslangic'], float(rows['Ciro YTD']['beklenen'])), ('2026-04-01', 1880360.0))  # fiscal year from April
        self.assertEqual(float(rows['Ciro GY YTD']['beklenen']), 1649610.0)
        self.assertIn('YearStart(Max([Siparis Tarihi]), 0, 4)', rows['Ciro YTD']['ifade'])
        self.assertIn("LET vciro_ytd = Replace('Sum({<", p.out('olculer.qvs').read_text(encoding='utf-8'))
        cg = self.package('pbi-zaman-hesaplama-grubu')
        self.ok(cg.run())
        tmdl = cg.out('Zaman Hesaplamaları.tmdl').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'ordinal: (\d+)', tmdl), [str(i) for i in range(9)])
        self.assertIn('DATESBETWEEN ( Takvim[Date], EDATE ( YilBasi, -12 ), EDATE ( SonGun, -12 ) )', tmdl)
        sa = self.package('qlik-section-access')
        self.ok(sa.run('--domain', 'ORNEK'))
        script = sa.out('section_access.qvs').read_text(encoding='utf-8')
        self.assertIn(r'USER;ORNEK\ALI.CELIK;IC ANADOLU', script)  # invariant upper case, full access written value by value
        self.assertIn('IC ANADOLU;İç Anadolu', script)
        view = {r['kullanici']: r for r in sa.csv('sonuc.csv')}
        self.assertEqual((view['mehmet.kaya@ornek.test']['gorunen_satir'], float(view['mehmet.kaya@ornek.test']['gorunen_toplam'])), ('98', 1987370.0))
        rls = self.package('pbi-rls-tmdl')
        self.ok(rls.run())
        role = rls.out('roles/Bölge Erişimi.tmdl').read_text(encoding='utf-8')
        self.assertIn('RETURN "*" IN Yetkiler || SatisDetay[Bolge] IN Yetkiler', role)
        self.assertIn('{"ayse.yilmaz@ornek.test", "Marmara"}', rls.out('tables/Guvenlik.tmdl').read_text(encoding='utf-8'))
        inc = self.package('qlik-artimli-yukleme')
        self.ok(inc.run('--key', 'fatura_no,satir_no', '--dialect', 'oracle'))
        text = inc.out('artimli_yukleme.qvs').read_text(encoding='utf-8')
        self.assertIn("WHERE Not Exists([%Anahtar], [fatura_no] & '|' & [satir_no]);", text)
        self.assertIn("TO_TIMESTAMP('$(vSonDegisiklik)', 'YYYY-MM-DD HH24:MI:SS')", text)
        self.assertEqual([t['name'] for t in qlik_parse(text)['tables']], ['Siparis', 'Siparis'])

    def test_star_schema_and_dbt_change_templates(self):
        p = self.package('yildiz-sema')
        self.ok(p.run('--dims', 'kategori=kategori,urun;bolge=bolge'))
        self.assertEqual(p.json('ozet.json')['bagimlilik_ihlali'], 4)  # a category does not determine the product
        dirty = self.package('yildiz-sema')
        self.ok(dirty.run('--input', str(ROOT / 'library' / 'samples' / 'satislar.csv'), '--dims', 'urun=urun_kodu,urun_adi;bolge=bolge', '--keep', 'siparis_no,durum,musteri_no',
                          '--dialect', 'sqlserver'))
        checks = {r['kontrol']: r for r in dirty.csv('sonuc.csv')}
        self.assertEqual(checks['dim_bolge: “bilinmiyor”a düşen satır']['yildiz_sema'], '1')  # the row with an empty region
        self.assertEqual({r['sonuc'] for r in checks.values()}, {'tamam'})
        self.assertIn('SET IDENTITY_INSERT dim_bolge ON;', dirty.out('yukleme.sql').read_text(encoding='utf-8'))
        inc = self.package('dbt-artimli-model')
        self.ok(inc.run())
        model = inc.out('dbt-artimli/models/musteri_kartlari_artimli.sql').read_text(encoding='utf-8')
        self.assertIn("{{ dbt.dateadd('hour', -3, 'max(guncelleme)') }} from {{ this }}", model)
        self.assertIn("unique_key='musteri_no'", model)
        bad = self.package('dbt-artimli-model')
        done = bad.run('--input', str(ROOT / 'library' / 'samples' / 'satis_detay.csv'), '--key', 'bolge', '--updated', 'siparis_tarihi')
        self.assertEqual(done.returncode, 1)
        self.assertIn('Anahtar tekil değil', done.stderr)
        snap = self.package('dbt-snapshot-sablonu')
        self.ok(snap.run())
        yml = snap.out('dbt-snapshot/snapshots/musteri_kartlari_gecmis.yml').read_text(encoding='utf-8')
        self.assertIn('hard_deletes: invalidate', yml)
        self.assertIn('updated_at: guncelleme', yml)

    def test_efatura_generator_round_trips_through_the_reader(self):
        import xml.etree.ElementTree as ET
        p = self.package('efatura-uret')
        self.ok(p.run('--prefix', 'FRM', '--start', '1001'))
        files = sorted(p.out('efatura').glob('*.xml'))
        self.assertEqual([f.stem for f in files], [f'FRM202600000100{i}' for i in range(1, 7)])  # the rejected invoice uses no number
        ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2', 'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
        first = ET.parse(files[0]).getroot()
        self.assertEqual(first.find('cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount', ns).text, '3250.00')  # 5% off 2 × 32.500
        self.assertEqual(first.find('cac:LegalMonetaryTotal/cbc:PayableAmount', ns).text, '77445.00')
        self.assertEqual([s.find('cbc:Percent', ns).text for s in first.findall('cac:TaxTotal/cac:TaxSubtotal', ns)], ['10', '20'])
        person = ET.parse(files[2]).getroot()
        self.assertEqual(person.find('cbc:ProfileID', ns).text, 'EARSIVFATURA')  # TCKN buyer
        self.assertEqual(person.find('cac:AccountingCustomerParty/cac:Party/cac:Person/cbc:FamilyName', ns).text, 'Yılmaz')
        reader = self.package('efatura-csv')
        self.ok(reader.run('--input', str(p.out('efatura'))))
        self.assertEqual({r['kontrol'] for r in reader.csv('sonuc.csv')}, {'tutarlı'})
        self.assertAlmostEqual(sum(float(r['odenecek']) for r in reader.csv('sonuc.csv')), 240124.04, places=2)
        held = ET.parse(files[3]).getroot()  # 40.000 cleaning, 20% VAT, 9/10 withheld
        self.assertEqual(held.find('cbc:InvoiceTypeCode', ns).text, 'TEVKIFAT')
        self.assertEqual(held.find('cac:WithholdingTaxTotal/cbc:TaxAmount', ns).text, '7200.00')
        self.assertEqual(held.find('cac:LegalMonetaryTotal/cbc:PayableAmount', ns).text, '41820.00')
        ret = ET.parse(files[4]).getroot()
        self.assertEqual((ret.find('cbc:ProfileID', ns).text, ret.find('cbc:InvoiceTypeCode', ns).text), ('TEMELFATURA', 'IADE'))
        self.assertEqual(ret.find('cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID', ns).text, 'DNZ2026000000457')
        exempt = ET.parse(files[5]).getroot()
        self.assertEqual(exempt.find('cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode', ns).text, '302')
        self.assertIn('603', p.csv('reddedilen.csv')[0]['gerekce'])  # 603 allows only 5/10 and 7/10
        bad = self.package('efatura-uret')
        rows = (bad.dir / 'ornek' / 'fatura_satirlari.csv').read_text(encoding='utf-8')
        rows = rows.replace(';ISTISNA;', ';SATIS;').replace(';Kılıf;2;adet;149,90;20;', ';Kılıf;2;adet;149,90;0;')
        (bad.dir / 'f.csv').write_text(rows, encoding='utf-8')
        self.ok(bad.run('--input', str(bad.dir / 'f.csv')))
        reasons = ' | '.join(r['gerekce'] for r in bad.csv('reddedilen.csv'))
        self.assertIn('yalnızca ISTISNA veya IADE faturasında', reasons)
        self.assertIn('KDV %0 satır için istisna_kodu gerekli', reasons)

    def test_despatch_advice_and_pivot_report(self):
        import xml.etree.ElementTree as ET
        p = self.package('eirsaliye-uret')
        self.ok(p.run())
        ns = {'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2', 'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'}
        doc = ET.parse(p.out('eirsaliye/IRS2026000000002.xml')).getroot()
        self.assertEqual(doc.find('cac:Shipment/cac:ShipmentStage/cac:TransportMeans/cac:RoadTransport/cbc:LicensePlateID', ns).text, '35DG4410')
        self.assertEqual([q.get('unitCode') for q in doc.iter('{%s}DeliveredQuantity' % ns['cbc'])], ['KGM', 'LTR', 'BX'])  # kg, LT, KT
        self.assertEqual(doc.find('cac:Shipment/cac:Delivery/cac:DeliveryAddress/cbc:PostalZone', ns).text, '35250')
        bad = self.package('eirsaliye-uret')
        rows = (bad.dir / 'ornek' / 'irsaliye_satirlari.csv').read_text(encoding='utf-8').replace(';35250;', ';3525;').replace(';10000000146;', ';10000000147;')
        (bad.dir / 'kotu.csv').write_text(rows, encoding='utf-8')
        done = bad.run('--input', str(bad.dir / 'kotu.csv'))
        self.assertEqual(done.returncode, 1)  # nothing valid to write
        report = bad.out('rapor.md').read_text(encoding='utf-8')
        self.assertIn('posta kodu geçersiz: 3525', report)
        self.assertIn('şoför ad, soyad ve geçerli TCKN birlikte verilmeli', report)
        pv = self.package('excel-pivot-rapor')
        self.ok(pv.run())
        grid = {r['bolge']: r for r in pv.csv('sonuc.csv')}
        self.assertEqual((float(grid['Karadeniz']['Web']), float(grid['Genel Toplam']['Genel Toplam'])), (807900.0, 5912910.0))
        with zipfile.ZipFile(pv.out('pivot.xlsx')) as z:
            pivot = z.read('xl/pivotTables/pivotTable1.xml').decode('utf-8')
            cache = z.read('xl/pivotCache/pivotCacheDefinition1.xml').decode('utf-8')
            self.assertIn('<pivotCache cacheId="1" r:id="rId4"/>', z.read('xl/workbook.xml').decode('utf-8'))
        self.assertIn('<location ref="A3:E10"', pivot)
        self.assertIn('<pageField fld="4" hier="-1"/>', pivot)
        self.assertIn('<worksheetSource ref="A1:J241" sheet="Veri"/>', cache)
        self.assertEqual(cache.count('<cacheField '), 10)

    def test_sql_formatter_never_turns_code_into_a_comment(self):
        p = self.package('sql-bicimlendir')
        (p.dir / 'x.sql').write_text("SELECT a -- not\nFROM t WHERE b = 'x -- y'; /* c */ DELETE FROM u WHERE id = 1", encoding='utf-8')
        self.ok(p.run('--input', 'x.sql'))
        out = p.out('bicimli/x.sql').read_text(encoding='utf-8')
        self.assertIn('-- not\n', out)
        self.assertIn("'x -- y'", out)
        for line in out.splitlines():
            if '--' in line and "'" not in line:
                self.assertTrue(line.strip().startswith(('--', 'SELECT')) and line.rstrip().endswith('not'), line)
        self.assertEqual(p.json('ozet.json')['atlanan'], 0)

    def test_card_numbers_never_leave_unmasked(self):
        p = self.package('kart-luhn')
        self.ok(p.run())
        text = p.out('sonuc.csv').read_text(encoding='utf-8-sig')
        self.assertNotIn('4111 1111 1111 1111', text)
        self.assertIn('411111******1111', text)

    def test_control_totals_detect_a_changed_file(self):
        p = self.package('kontrol-toplami')
        self.ok(p.run('--output', 'kaynak'))
        lines = (p.dir / 'ornek' / 'satislar.csv').read_text(encoding='utf-8').splitlines()
        (p.dir / 'hedef.csv').write_text('\n'.join(lines[:-1]) + '\n', encoding='utf-8')
        done = p.run('--input', 'hedef.csv', '--compare', 'kaynak/kontrol.json')
        self.assertEqual(done.returncode, 2)
        self.assertEqual(p.json('ozet.json')['fark'], 5)  # rows, content hash and three numeric totals


class Helpers(unittest.TestCase):
    def test_masking_is_checksum_aware(self):
        text, counts = mask_text('a@b.co 10000000146 12345678901 0532 123 45 67 TR330006100519786457841326')
        self.assertEqual(text, '[E-POSTA] [TCKN] 12345678901 [TELEFON] [IBAN]')
        self.assertEqual(counts, {'E-POSTA': 1, 'TCKN': 1, 'TELEFON': 1, 'IBAN': 1})

    def test_numbers_and_csv_safety(self):
        for raw, value in [('1.234,56', 1234.56), ('1,234.56', 1234.56), ('12,5', 12.5), ('₺ 2.500', 2500.0), ('-3', -3.0), ('abc', None)]:
            self.assertEqual(tabular.parse_number(raw), value, raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.csv'
            tabular.write_csv(path, ['a'], [{'a': '=HYPERLINK("x")'}, {'a': '-5'}])
            self.assertEqual(path.read_text(encoding='utf-8-sig').splitlines()[1:], ['"\'=HYPERLINK(""x"")"', '-5'])
            path.write_bytes('ad;şehir\nAli;İzmir\n'.encode('cp1254'))
            self.assertEqual(tabular.read(path)[1], [{'ad': 'Ali', 'şehir': 'İzmir'}])


    def test_schema_parser_and_narrowing(self):
        t = schema_from_ddl("/* x */ CREATE TABLE a.b ([Id] INT NOT NULL, Ad NVARCHAR(50) DEFAULT ('x,y'), Fiyat DECIMAL(18, 2), PRIMARY KEY (Id)); -- son")
        self.assertEqual([(c['name'], c['type'], c['nullable'], c['pk']) for c in t['a.b']],
                         [('Id', 'INT', False, True), ('Ad', 'NVARCHAR(50)', True, False), ('Fiyat', 'DECIMAL(18, 2)', True, False)])
        for old, new, risky in [('VARCHAR(50)', 'VARCHAR(20)', True), ('VARCHAR(20)', 'NVARCHAR(20)', False), ('NVARCHAR(MAX)', 'NVARCHAR(100)', True),
                                ('INT', 'BIGINT', False), ('BIGINT', 'INT', True), ('DECIMAL(18,2)', 'DECIMAL(20,4)', False), ('DECIMAL(18,2)', 'DECIMAL(18,4)', True), ('DECIMAL(18,4)', 'DECIMAL(18,2)', True),
                                ('DATE', 'DATETIME2', False), ('DATETIME', 'DATE', True), ('INT', 'VARCHAR(10)', True)]:
            self.assertEqual(narrowing(old, new), risky, f'{old} → {new}')

    def test_date_dimension_holidays(self):
        rows = {r['tarih']: r for r in build_dates(date(2026, 1, 1), date(2026, 12, 31), {date(2026, 3, 20): ('Ramazan Bayramı', False)})}
        self.assertEqual(sum(r['resmi_tatil'] for r in rows.values()), 8)
        self.assertEqual(rows['2026-03-20']['is_gunu'], 0)
        self.assertEqual(rows['2026-07-15']['tatil_adi'], 'Demokrasi ve Millî Birlik Günü')
        with self.assertRaisesRegex(ValueError, 'önce'):
            build_dates(date(2026, 2, 1), date(2026, 1, 1), {})

    def test_number_check_allows_rounding_but_not_invention(self):
        facts = [{'olgu': 'Toplam 2026-09: 1.234.567,89; önceki 2026-08: 1.100.000,00; değişim 134.567,89 (%12,2)'}]
        labels = {'2026-09', '2026-08'}
        values = allowed_numbers(facts, labels)
        for ok in ('2026-09 döneminde ciro %12,2 arttı', 'Ciro 1,2 milyon TL', 'yaklaşık %12', '134,6 bin TL', 'iki kanal, 2 bölge', '1.234.567,89 TL'):
            self.assertEqual(unknown_numbers(ok, values, labels), [], ok)
        self.assertEqual(unknown_numbers('Ciro %15 arttı ve 48.000 TL kayıp var', values, labels), ['15', '48.000'])


# ---------------------------------------------------------------- agents
class FakeModel:
    """Local HTTP server that speaks the OpenAI, Anthropic and Ollama chat formats and returns scripted answers."""

    def __init__(self):
        self.answers, self.requests, self.status = [], [], 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    self.wfile.write(b'{"error":"secret request echo"}')
                    return
                answer = outer.answers.pop(0) if outer.answers else '{}'
                text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
                if self.path.endswith('/api/chat'):
                    self.send_response(200)
                    self.end_headers()
                    for i in range(0, len(text), 50):
                        self.wfile.write(json.dumps({'message': {'content': text[i:i + 50]}, 'done': False}).encode() + b'\n')
                    self.wfile.write(json.dumps({'message': {'content': ''}, 'done': True}).encode() + b'\n')
                    return
                payload = {'content': [{'type': 'text', 'text': text}]} if self.path.endswith('/v1/messages') else {'choices': [{'message': {'content': text}}]}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def env(self, provider='openai'):
        return {'PROVIDER': provider, 'MODEL': 'test-model', 'OPENAI_API_KEY': 'sk-test', 'ANTHROPIC_API_KEY': 'ak-test',
                'OPENAI_BASE_URL': self.url + '/v1', 'ANTHROPIC_BASE_URL': self.url, 'OLLAMA_URL': self.url}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


sys.path.insert(0, str(ROOT / 'library' / 'agents'))
from sql_aciklayici import statements as sql_statements
N_SQL = len(sql_statements(ROOT / 'library' / 'samples' / 'sql'))
GOOD = {
    'sql-yazari': {'sql': 'SELECT m.Sehir, SUM(k.Adet * k.BirimFiyat) AS Ciro FROM dbo.Siparisler s JOIN dbo.Musteriler m ON m.MusteriNo = s.MusteriNo '
                          'JOIN dbo.SiparisKalemleri k ON k.SiparisNo = s.SiparisNo GROUP BY m.Sehir', 'aciklama': 'Şehir bazında ciro.', 'varsayimlar': ['KDV hariç']},
    'dax-yazari': {'olculer': [{'ad': 'Kâr Marjı', 'tablo': 'Satislar', 'ifade': 'DIVIDE ( [Brüt Kâr], [Toplam Satış] )', 'bicim': '0.0%', 'aciklama': 'Brüt kâr / satış'},
                               {'ad': 'Müşteri Sayısı', 'tablo': 'Satislar', 'ifade': 'DISTINCTCOUNT ( Satislar[MusteriNo] )', 'bicim': '#,0'}]},
    'veri-sozlugu': {'tablo_aciklamasi': 'Sipariş listesi', 'sutunlar': [{'ad': c, 'is_adi': c.title(), 'aciklama': 'Açıklama', 'hassasiyet': 'genel'} for c in
                     ['siparis_no', 'siparis_tarihi', 'musteri_no', 'musteri_eposta', 'bolge', 'urun_kodu', 'urun_adi', 'adet', 'birim_fiyat', 'tutar', 'durum']],
                     'acik_sorular': ['Negatif tutar nedir?']},
    'kalite-kurali': {'rules': [{'column': 'siparis_no', 'check': 'unique', 'severity': 'hata', 'gerekce': 'Anahtar'},
                                {'column': 'tutar', 'check': 'min', 'value': 0, 'gerekce': 'Negatif olmaz'},
                                {'column': 'tutar', 'check': 'min', 'value': 0}]},
    'qlik-powerbi': {'tablolar': [{'qlik_tablo': t, 'power_query_m': 'let\n    Kaynak = Sql.Database("sunucu", "SatisDB")\nin\n    Kaynak', 'notlar': 'not'} for t in ('Satislar', 'Musteriler', 'Hedefler')],
                     'dax_olculeri': [{'ad': 'Ciro', 'dax': 'SUM(Satislar[Tutar])', 'qlik_ifadesi': 'Sum(Tutar)'}], 'riskler': ['QVD okunamaz'], 'model_onerisi': 'Yıldız şema'},
    'kpi-tanimlayici': {'kpiler': [{'ad': 'Haftalık ciro', 'tanim': 'İptal hariç ciro', 'formul': "SUM([tutar]) WHERE [durum] <> 'İptal'", 'birim': 'TL', 'periyot': 'haftalık',
                                    'kirilimlar': ['bolge'], 'hedef_yonu': 'yukarı'},
                                   {'ad': 'Tekrar alım oranı', 'tanim': '…', 'formul': '[tekrar_eden_musteri] / [musteri]', 'hedef_yonu': 'yukarı'}],
                        'genel_acik_sorular': ['90 mı 180 gün mü?']},
}
GOOD['sql-aciklayici'] = {'genel_ozet': 'ERP siparişlerinden müşteri özeti üretir.',
                          'ifadeler': [{'no': f'S{i}', 'amac': f'{i}. adım', 'is_kurallari': ['İptaller hariç'], 'dikkat': []} for i in range(1, N_SQL + 1)],
                          'sorular': ['Segment tablosu ne sıklıkla güncelleniyor?']}
GOOD['donem-yorumu'] = {'baslik': 'Eylülde ciro %3,8 geriledi', 'ozet': 'Toplam ciro 119.487,60 ile önceki aya göre %3,8 düştü; düşüşü Ege sürükledi.',
                        'bulgular': [{'metin': 'Ege %23,7 geriledi (-7.305,10).', 'kanit': ['F2']}, {'metin': 'Marmara %3,9 büyüdü.', 'kanit': ['F3']}],
                        'aksiyonlar': ['Ege mağazalarını inceleyin.'], 'sorular': ['Ege’de mağaza kapanışı oldu mu?']}
OUTPUTS = {'sql-aciklayici': 'sql-aciklama.md', 'donem-yorumu': 'donem-yorumu.md', 'sql-yazari': 'sorgu.sql', 'dax-yazari': 'olculer.dax', 'veri-sozlugu': 'veri-sozlugu.md', 'kalite-kurali': 'rules.json',
           'qlik-powerbi': 'gecis-plani.md', 'kpi-tanimlayici': 'kpi-katalogu.md'}
GOOD['kvkk-envanteri'] = {'kayitlar': [{'sutun': 'ad_soyad', 'veri_kategorisi': 'kimlik', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/2-c'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'tckn', 'veri_kategorisi': 'kimlik', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/2-ç'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'telefon', 'veri_kategorisi': 'iletişim', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/2-c'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'eposta', 'veri_kategorisi': 'iletişim', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['md. 5/2-c'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'iban', 'veri_kategorisi': 'finans', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/2-c'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'dogum_tarihi', 'veri_kategorisi': 'kimlik', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/1'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}, {'sutun': 'notlar', 'veri_kategorisi': 'müşteri işlem', 'amac': 'Sipariş ve fatura işlemleri', 'hukuki_sebep': ['5/2-f'], 'saklama_suresi': 'İlişki bitiminden itibaren 10 yıl', 'erisim': ['çağrı merkezi', 'fatura'], 'aktarim': 'yurt dışı', 'onlem': 'rol bazlı erişim'}], 'sorular': ['Doğum tarihi hangi amaçla tutuluyor?']}
GOOD['olay-analisti'] = {'ozet': 'Zaman aşımları ve disk doluluğu.', 'imzalar': [{'id': 'S1', 'kategori': 'kaynak sistem', 'oncelik': 'P2', 'olasi_neden': 'Sipariş servisi yavaş', 'aksiyonlar': ['Servis gecikmesini inceleyin']}, {'id': 'S2', 'kategori': 'yetki', 'oncelik': 'P3', 'olasi_neden': 'Süresi dolmuş oturum', 'aksiyonlar': ['Token süresini kontrol edin']}, {'id': 'S3', 'kategori': 'kapasite', 'oncelik': 'P2', 'olasi_neden': 'Havuz boyutu yetersiz', 'aksiyonlar': ['Havuz boyutunu artırın']}, {'id': 'S4', 'kategori': 'altyapı', 'oncelik': 'P2', 'olasi_neden': 'Log rotasyonu yok', 'aksiyonlar': ['Disk alanı açın']}], 'sorular': ['Disk ne zamandır dolu?']}
GOOD['boyutsal-model'] = {'olgu': {'ad': 'fct_satis', 'tane': 'bir satır = bir sipariş satırı', 'olculer': ['adet', 'birim_fiyat', 'tutar'], 'dejenere': ['siparis_no', 'durum'], 'tarih_sutunlari': ['siparis_tarihi']}, 'boyutlar': [{'ad': 'dim_musteri', 'dogal_anahtar': ['musteri_no'], 'nitelikler': ['musteri_eposta', 'bolge'], 'scd': '1'}, {'ad': 'dim_urun', 'dogal_anahtar': ['urun_kodu'], 'nitelikler': ['urun_adi'], 'scd': '1'}], 'notlar': ['birim_fiyat ürün boyutunda da tutulabilir']}
GOOD['regex-yazari'] = {'desen': '^(0[1-9]|[1-7]\\d|8[01]) ([A-Z] \\d{4,5}|[A-Z]{2} \\d{3,4}|[A-Z]{3} \\d{2,3})$', 'aciklama': 'İl kodu + harf/rakam grupları', 'parcalar': [{'parca': '(0[1-9]|[1-7]\\d|8[01])', 'anlam': 'il kodu 01–81'}], 'sinirlar': ['Türkçe harf kontrolü yok']}
GOOD['migrasyon-esleme'] = {'eslemeler': [{'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'musteri_id', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': ['MusteriNo'], 'ifade': 'MusteriNo'}, {'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'ad_soyad', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': ['MusteriAdi'], 'ifade': 'MusteriAdi'}, {'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'sehir', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': ['Sehir'], 'ifade': 'Sehir'}, {'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'telefon', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': ['Telefon'], 'ifade': 'Telefon'}, {'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'kayit_tarihi', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': ['KayitTarihi'], 'ifade': 'KayitTarihi'}, {'hedef_tablo': 'crm.musteri', 'hedef_sutun': 'kaynak_sistem', 'kaynak_tablo': 'dbo.Musteriler', 'kaynak_sutunlar': [], 'ifade': "'ERP'"}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'siparis_id', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': ['SiparisNo'], 'ifade': 'SiparisNo'}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'musteri_id', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': ['MusteriNo'], 'ifade': 'MusteriNo'}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'siparis_tarihi', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': ['SiparisTarihi'], 'ifade': 'SiparisTarihi'}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'tutar', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': ['Tutar'], 'ifade': 'Tutar'}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'durum', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': ['Durum'], 'ifade': 'CAST(Durum AS VARCHAR(20))'}, {'hedef_tablo': 'satis.siparis', 'hedef_sutun': 'yukleme_zamani', 'kaynak_tablo': 'dbo.Siparisler', 'kaynak_sutunlar': [], 'ifade': 'CURRENT_TIMESTAMP'}], 'sorular': ['Kayıt tarihi boş müşteriler nasıl yüklenecek?']}
OUTPUTS.update({'kvkk-envanteri': 'kvkk-envanteri.md', 'olay-analisti': 'olay-raporu.md', 'boyutsal-model': 'boyutsal-model.md', 'regex-yazari': 'desen.md',
                'migrasyon-esleme': 'yukleme.sql'})
GOOD['dbt-model-yazari'] = {'model_adi': 'fct_segment_siparis', 'aciklama': 'İptal hariç siparişler ve müşteri segmenti.', 'materializasyon': 'view', 'sql': "WITH siparis AS (SELECT * FROM {{ source('erp', 'siparis') }}),\nmusteri AS (SELECT * FROM {{ source('crm', 'musteri') }}),\nkampanya AS (SELECT * FROM {{ source('ref_data', 'kampanya') }})\nSELECT s.siparis_id, s.musteri_id, m.segment, s.tarih, s.tutar\nFROM siparis s\nJOIN musteri m ON m.musteri_id = s.musteri_id\nLEFT JOIN kampanya k ON k.kampanya_id = s.kampanya_id\nWHERE s.durum <> 'IPTAL'", 'kaynaklar': [{'tablo': 'erp.siparis', 'tur': 'source', 'source': 'erp', 'ad': 'siparis'}, {'tablo': 'crm.musteri', 'tur': 'source', 'source': 'crm', 'ad': 'musteri'}, {'tablo': 'ref.kampanya', 'tur': 'source', 'source': 'ref_data', 'ad': 'kampanya'}], 'sutunlar': [{'ad': 'siparis_id', 'aciklama': 'Sipariş anahtarı', 'testler': ['unique', 'not_null']}, {'ad': 'segment', 'aciklama': 'Müşteri segmenti', 'testler': ['accepted_values']}, {'ad': 'tutar', 'aciklama': 'Sipariş tutarı', 'testler': []}], 'notlar': ['kampanya join sonucu kullanılmıyor; kaldırılabilir.']}
GOOD['zamanlama-planlayici'] = {'plan': [{'is': 'erp_cek', 'baslangic': '01:00'}, {'is': 'crm_cek', 'baslangic': '01:00'}, {'is': 'web_cek', 'baslangic': '01:20'}, {'is': 'stg_yukle', 'baslangic': '01:40'}, {'is': 'web_oturum', 'baslangic': '01:50'}, {'is': 'dim_musteri', 'baslangic': '02:05'}, {'is': 'fct_satis', 'baslangic': '02:20'}, {'is': 'pazarlama_rapor', 'baslangic': '02:25'}, {'is': 'kalite', 'baslangic': '03:05'}, {'is': 'finans_rapor', 'baslangic': '03:15'}], 'gerekce': 'Kritik yol önce; web zinciri boşluklara yerleşti.', 'notlar': ['finans_rapor 03:35’te hazır.']}
GOOD['tasarruf-plani'] = {'ozet': 'En büyük kalem ec2-satis; taahhüt ve duraklatma ile tahmini 2.100 aylık tasarruf.', 'aksiyonlar': [{'baslik': 'ec2-satis için 1 yıllık taahhüt', 'aciklama': 'ec2-satis son 30 günde 5.372,14 harcadı ve sürekli çalışıyor.', 'kanit': ['F7'], 'tahmini_aylik_tasarruf': 1500, 'efor': 'düşük', 'risk': 'Kullanım düşerse taahhüt boşa gider.'}, {'baslik': 'redshift-pazarlama gece duraklatma', 'aciklama': 'Mesai dışında kullanılmıyorsa duraklatın.', 'kanit': ['F11'], 'tahmini_aylik_tasarruf': 600, 'efor': 'orta'}, {'baslik': 'Etiketsiz maliyeti sahiplendirme', 'aciklama': 'Toplamın %14,9’u etiketsiz.', 'kanit': ['F12'], 'tahmini_aylik_tasarruf': 0, 'efor': 'düşük'}], 'sorular': ['Redshift pazarlama mesai dışında kullanılıyor mu?']}
GOOD['model-karti'] = {'model_adi': 'Churn modeli', 'amac': ['Ayrılma riski yüksek müşterileri elde tutma kampanyası için sıralamak.'], 'kullanim_alani': ['CRM ekibinin haftalık listesi; karar insanda.'], 'kullanilmamasi_gereken': ['Kredi ve fiyat kararları.'], 'bulgular': [{'metin': 'Genel ayrıştırma gücü yüksek: AUC 0,905.', 'kanit': ['F2']}, {'metin': 'Kurumsal segmentte duyarlılık %69,8, bireyselde %94,8; kurumsal müşterilerin ayrılanlarının önemli kısmı kaçırılıyor.', 'kanit': ['F4', 'F5']}], 'sinirlamalar': ['Eğitim verisinin dönemi belirtilmemiş.'], 'riskler': ['Kurumsal segmentte düşük kesinlik gereksiz kampanya maliyeti doğurur.'], 'izleme': ['Segment bazında AUC ve duyarlılık aylık izlenmeli.']}
GOOD['eslestirme-hakemi'] = {'kararlar': [{'id': 'C1', 'karar': 'aynı', 'gerekce': 'Aynı VKN, aynı adres; yalnızca yazım farkı.'}, {'id': 'C2', 'karar': 'emin değil', 'gerekce': 'Pazarlama şirketi ayrı tüzel kişi olabilir; il ve VKN farklı.'}, {'id': 'C3', 'karar': 'emin değil', 'gerekce': 'C2 ile aynı durum.'}, {'id': 'C4', 'karar': 'farklı', 'gerekce': 'Aynı ad, farklı il, VKN ve telefon.'}, {'id': 'C5', 'karar': 'aynı', 'gerekce': 'Aynı telefon ve adres.'}]}
OUTPUTS.update({'dbt-model-yazari': 'dbt-model.md', 'zamanlama-planlayici': 'zamanlama-plani.md', 'tasarruf-plani': 'tasarruf-plani.md', 'model-karti': 'model-karti.md',
                'eslestirme-hakemi': 'eslesme.md'})
GOOD['sql-optimizasyon'] = {'ozet': 'Sıralı tarama ve diske taşan sıralama asıl maliyet; durum + tutar indeksi ikisini de çözer.', 'sorgu': "SELECT s.siparis_id, s.tarih, s.tutar, m.ad, m.sehir\nFROM siparisler s\nJOIN musteriler m ON m.musteri_id = s.musteri_id\nWHERE s.durum = 'Teslim'\nORDER BY s.tutar DESC", 'indeksler': ['CREATE INDEX CONCURRENTLY ix_siparisler_durum_tutar ON siparisler (durum, tutar DESC) INCLUDE (siparis_id, tarih, musteri_id);'], 'degisiklikler': ['Sorgu metni değişmedi; iyileştirme indeksle.'], 'riskler': ['Yazma yükü artar.']}
GOOD['avro-sema-tasarimci'] = {'sema': {'type': 'record', 'name': 'SiparisOlayi', 'namespace': 'com.ornek.siparis', 'doc': 'Sipariş yaşam döngüsü olayı', 'fields': [{'name': 'olay_id', 'type': 'string', 'doc': 'Olay kimliği'}, {'name': 'tur', 'type': {'type': 'enum', 'name': 'OlayTuru', 'symbols': ['siparis_olustu', 'odeme_alindi', 'kargoya_verildi']}, 'doc': 'Olay türü'}, {'name': 'olay_zamani', 'type': 'string', 'doc': 'ISO 8601 olay zamanı'}, {'name': 'alinma_zamani', 'type': 'string', 'doc': 'ISO 8601 alınma zamanı'}, {'name': 'musteri', 'type': {'type': 'record', 'name': 'Musteri', 'fields': [{'name': 'id', 'type': 'string'}, {'name': 'segment', 'type': ['null', {'type': 'enum', 'name': 'Segment', 'symbols': ['A', 'B']}], 'default': None}]}}, {'name': 'tutar', 'type': ['double', 'string'], 'doc': 'Tutar; bazı üreticiler metin gönderiyor (düzeltilmeli)'}]}, 'notlar': ['tutar alanında 4 olay metin; üretici düzeltilince yalnızca double yapın.']}
GOOD['rag-test-seti'] = {'sorular': [{'parca': 'iade_politikasi.md#1', 'soru': 'Ürünler kaç gün içinde iade edilebilir?', 'cevap': '14 gün', 'dayanak': 'Ürünler teslimattan itibaren 14 gün içinde iade edilebilir.', 'zorluk': 'kolay'}, {'parca': 'iade_politikasi.md#2', 'soru': 'Onaylanan iadede ödeme ne zaman geri yapılır?', 'cevap': '7 iş günü içinde', 'dayanak': 'Onaylanan iadelerde ödeme, kullanılan ödeme yöntemine 7 iş günü içinde geri yapılır.', 'zorluk': 'kolay'}, {'parca': 'iade_politikasi.md#3', 'soru': 'Hasarlı gelen ürün için ne yapılmalı?', 'cevap': 'Kargo görevlisine tutanak tutturulmalı', 'dayanak': 'Teslimatta hasarlı gelen ürünler için kargo görevlisine tutanak tutturulmalıdır.', 'zorluk': 'kolay'}, {'parca': 'kargo.md#1', 'soru': 'İstanbul içi teslimat kaç iş günü sürer?', 'cevap': '1 iş günü', 'dayanak': 'İstanbul içi teslimat 1 iş günü, diğer iller 2-3 iş günüdür.', 'zorluk': 'kolay'}, {'parca': 'kargo.md#2', 'soru': 'Kargo hangi tutardan itibaren ücretsiz?', 'cevap': '750 TL', 'dayanak': '750 TL ve üzeri siparişlerde kargo ücretsizdir.', 'zorluk': 'kolay'}, {'parca': 'kargo_kopya.md#1', 'soru': 'Aynı gün kargo için sipariş saat kaça kadar verilmeli?', 'cevap': 'Hafta içi 15:00', 'dayanak': 'Siparişler hafta içi saat 15:00’e kadar verilirse aynı gün kargoya teslim edilir.', 'zorluk': 'kolay'}, {'parca': 'kargo_kopya.md#2', 'soru': 'Ücretsiz kargo limitinin altında kargo ücreti ne kadar?', 'cevap': '59,90 TL', 'dayanak': 'Altındaki siparişlerde sabit 59,90 TL kargo ücreti alınır.', 'zorluk': 'kolay'}, {'parca': 'tedarikci_notu.txt#1', 'soru': 'Yeni fiyat listesi ne zaman yürürlüğe girer?', 'cevap': 'Ekim ayında', 'dayanak': 'Yeni fiyat listesi Ekim ayında yürürlüğe girer.', 'zorluk': 'kolay'}]}
GOOD['kalite-kok-neden'] = {'ozet': 'Boş e-postalar çağrı merkezinden, negatif tutarlar iadelerden, hatalı durum mağaza kasasından geliyor.', 'kurallar': [{'kural': 2, 'olasi_neden': 'Çağrı merkezi ekranında e-posta zorunlu değil.', 'kanit': ['F2'], 'sahip': 'kaynak sistem', 'aksiyon': 'Çağrı merkezi formunda e-postayı zorunlu yapın.', 'guven': 'yüksek'}, {'kural': 3, 'olasi_neden': 'İadeler negatif tutarla aynı tabloya yazılıyor; kural iadeleri dışlamıyor.', 'kanit': ['F4'], 'sahip': 'kural tanımı', 'aksiyon': 'Kuralı durum İade dışındaki satırlara uygulayın.', 'guven': 'yüksek'}, {'kural': 4, 'olasi_neden': 'Mağaza kasası durum metnine sonda boşluk ekliyor.', 'kanit': ['F6'], 'sahip': 'entegrasyon/ETL', 'aksiyon': 'Yüklemede TRIM uygulayın.', 'guven': 'orta'}]}
GOOD['ab-test-karari'] = {'karar': 'fark yok: testi uzat veya sonlandır', 'ozet': 'B dönüşümü %5,30, A %5,00; fark istatistiksel olarak anlamlı değil (p 0,6677).', 'gerekceler': [{'metin': 'Güven aralığı -1,07 ile 1,67 puan arası; sıfırı içeriyor.', 'kanit': ['F3']}, {'metin': 'Gelir farkı da anlamlı değil (p 0,3441).', 'kanit': ['F5']}], 'riskler': ['Test erken sonlandırılırsa küçük etkiler kaçabilir.'], 'sonraki_adimlar': ['Gerekli örneklemi hesaplayıp testi uzatın.']}
OUTPUTS.update({'sql-optimizasyon': 'optimizasyon.md', 'avro-sema-tasarimci': 'sema.avsc', 'rag-test-seti': 'test_seti.csv', 'kalite-kok-neden': 'kok-neden.md',
                'ab-test-karari': 'ab-karar.md'})
GOOD['veri-sozlesmesi-yazari'] = {'sozlesme': {'name': 'satis.siparis', 'owner': 'Satış Veri Ekibi', 'version': '1.0', 'min_rows': 1, 'columns': [{'name': 'siparis_id', 'type': 'integer', 'required': True, 'unique': True}, {'name': 'musteri_no', 'type': 'string', 'required': True, 'pattern': '^M[0-9]{3}$'}, {'name': 'tarih', 'type': 'date', 'required': True}, {'name': 'tutar', 'type': 'number', 'required': True, 'min': 0}, {'name': 'para_birimi', 'type': 'string', 'allowed': ['TRY', 'USD', 'EUR']}, {'name': 'durum', 'type': 'string', 'required': True, 'allowed': ['yeni', 'onaylandi', 'iptal']}, {'name': 'kanal', 'type': 'string', 'required': True, 'allowed': ['web', 'magaza']}]}, 'bilinen_ihlaller': [{'sutun': 'siparis_id', 'kural': 'tekil', 'gerekce': 'Üretici aynı siparişi iki kez gönderiyor; düzeltmeli.'}, {'sutun': 'musteri_no', 'kural': 'zorunlu', 'gerekce': 'Mağaza satışında müşteri no boş geliyor.'}, {'sutun': 'musteri_no', 'kural': 'desen', 'gerekce': 'M05 hatalı biçimli.'}, {'sutun': 'tarih', 'kural': 'tip', 'gerekce': 'Geçersiz tarih (13. ay).'}, {'sutun': 'tutar', 'kural': 'tip', 'gerekce': 'Sayı olmayan tutar.'}, {'sutun': 'tutar', 'kural': 'aralık', 'gerekce': 'Negatif tutar; iadeler ayrı akışta gelmeli.'}, {'sutun': 'para_birimi', 'kural': 'izin verilen değerler', 'gerekce': 'GBP desteklenmiyor.'}, {'sutun': 'durum', 'kural': 'izin verilen değerler', 'gerekce': 'Büyük harfli Onaylandi.'}], 'notlar': ['Tarih biçimi ISO 8601 olmalı.']}
GOOD['butce-sapma-yorumu'] = {'ozet': 'Pazarlama giderleri YTD bütçeyi %15,6 aştı; diğer kalemler eşik içinde.', 'kalemler': [{'kalem': '760 / Merkez', 'degerlendirme': 'olumsuz', 'yorum': 'YTD sapma 99.748,00; son ayda %34,1 aşım.', 'olasi_neden': 'Olası kampanya harcaması.', 'aksiyon': 'Pazarlama bütçe sahibinden açıklama isteyin.', 'kanit': ['F5', 'F6']}, {'kalem': '600 / Merkez', 'degerlendirme': 'nötr', 'yorum': 'YTD %3,2 üzerinde ama son ay %-12,1 geride.', 'olasi_neden': 'Belirsiz.', 'aksiyon': 'Ekim satışlarını izleyin.', 'kanit': ['F1', 'F2']}]}
GOOD['hesap-plani-esleyici'] = {'eslemeler': [{'yerel_kod': '1000', 'tdhp': '100', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1010', 'tdhp': '102', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1011', 'tdhp': '102', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1200', 'tdhp': '120', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1210', 'tdhp': '121', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1530', 'tdhp': '153', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '1910', 'tdhp': '191', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '2550', 'tdhp': '255', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '2570', 'tdhp': '257', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '3200', 'tdhp': '320', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '3600', 'tdhp': '360', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '3610', 'tdhp': '361', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '3910', 'tdhp': '391', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '6000', 'tdhp': '600', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '6100', 'tdhp': '610', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '7600', 'tdhp': '760', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '7700', 'tdhp': '770', 'guven': 'yüksek', 'gerekce': ''}, {'yerel_kod': '9999', 'tdhp': 'eşlenemez', 'gerekce': 'Geçici aktarım hesabı; dönem sonunda sıfırlanmalı.'}], 'sorular': ['9999 hesabı dönem sonunda bakiye veriyor mu?']}
GOOD['json-esleme-yazari'] = {'kayit_yolu': 'veri', 'acilim': 'kalemler', 'sutunlar': [{'sutun': 'siparis_id', 'yol': 'id', 'tip': 'metin', 'zorunlu': True}, {'sutun': 'siparis_tarihi', 'yol': 'tarih', 'tip': 'tarih', 'zorunlu': True}, {'sutun': 'musteri_no', 'yol': 'musteri.no', 'tip': 'metin', 'zorunlu': True}, {'sutun': 'musteri_sehir', 'yol': 'musteri.sehir', 'tip': 'metin'}, {'sutun': 'urun_kodu', 'yol': 'kalemler[].urun', 'tip': 'metin', 'zorunlu': True}, {'sutun': 'adet', 'yol': 'kalemler[].adet', 'tip': 'tam sayı', 'zorunlu': True}, {'sutun': 'birim_fiyat', 'yol': 'kalemler[].fiyat', 'tip': 'ondalık', 'zorunlu': True}, {'sutun': 'iptal', 'yol': 'iptal', 'tip': 'mantıksal'}], 'notlar': ['etiketler dizisi ayrı tabloya alınmalı.']}
GOOD['bolumleme-danismani'] = {'bolum_sutunu': 'siparis_tarihi', 'ayrinti': 'ay', 'kume_sutunlari': ['bolge', 'urun_kodu'], 'gerekce': 'Sorguların çoğu tarih aralığı filtreliyor.', 'riskler': ['Ay içi dengesiz hacim.']}
GOOD['deney-analisti'] = {'test': 'mann-whitney', 'sonuc': 'anlamlı fark var', 'yuksek_grup': 'A', 'yorum': 'A grubunun medyanı 62,45, B grubunun 47,85 saniye; Mann-Whitney p-değeri 0,0166. Yeni ekran (B) görevi daha kısa sürede tamamlatıyor; etki küçük-orta (r 0,27).', 'uyarilar': ['A grubu normal değil; t-testi yerine Mann-Whitney kullanıldı.'], 'kanit': ['F1', 'F2', 'F4']}
GOOD['sube-planlayici'] = {'ozet': 'En büyük iki kapsama dışı küme için şube önerilir.', 'oneriler': [{'kume': 'K3', 'gerekce': 'En büyük küme (18 müşteri); en yakın şube 88,9 km uzakta.', 'kanit': ['F4']}, {'kume': 'K2', 'gerekce': '12 müşteri; en yakın şube 358,9 km.', 'kanit': ['F3']}], 'riskler': ['Kira, rakip ve trafik bilgisi olgularda yok; saha ziyaretiyle doğrulanmalı.']}
GOOD['rapor-tasarimcisi'] = {'baslik': 'Bölge Satış Raporu', 'olculer': [{'sutun': 'tutar', 'toplama': 'toplam'}, {'sutun': 'adet', 'toplama': 'toplam'}], 'boyutlar': ['bolge', 'kanal'], 'tarih': 'siparis_tarihi', 'araclar': ['qlik', 'powerbi'], 'gerekce': 'Ciro tutar sütunundan; sipariş adedi için farklı sipariş sayısı eklenir.', 'sorular': ['Ciro KDV dahil mi?']}
GOOD['qlik-ifade-yazari'] = {'ifadeler': [{'ad': 'Ciro YTD', 'ifade': 'Sum({<[Yil]=, [Tarih]={">=$(=Date(YearStart(Max([Tarih]))))<=$(=Date(Max([Tarih])))"}>} [Tutar])', 'aciklama': 'Yıl başından son tarihe ciro.'}, {'ad': 'Ciro GY YTD', 'ifade': 'Sum({<[Yil]=, [Tarih]={">=$(=Date(AddYears(YearStart(Max([Tarih])), -1)))<=$(=Date(AddYears(Max([Tarih]), -1)))"}>} [Tutar])', 'aciklama': 'Geçen yılın aynı dönemi.'}, {'ad': 'Sipariş Başına Ciro', 'ifade': 'Sum([Tutar]) / Count(DISTINCT [SiparisNo])', 'aciklama': ''}, {'ad': 'Kategori Payı', 'ifade': 'Sum([Tutar]) / Sum({<[Kategori]=>} TOTAL [Tutar])', 'aciklama': 'Seçili kategorinin tüm kategoriler içindeki payı.'}, {'ad': '3+ Siparişli Müşteri', 'ifade': 'Count({<[MusteriNo]={"=Count(DISTINCT [SiparisNo])>=3"}>} DISTINCT [MusteriNo])', 'aciklama': ''}], 'varsayimlar': ['Tarih alanı tarih (dual) olarak yükleniyor.']}
OUTPUTS['qlik-ifade-yazari'] = 'ifadeler.md'
GOOD['birim-kodu-eslestirici'] = {'eslemeler': [{'birim': 'Kasa', 'kod': 'CR', 'gerekce': 'Sebze kasası; ambalaj sayılır.'}, {'birim': 'Damacana', 'kod': 'BO', 'gerekce': 'Büyük şişe.'}, {'birim': 'Bidon', 'kod': 'CA', 'gerekce': 'Metal/plastik kap.'}, {'birim': 'Varil', 'kod': 'DR', 'gerekce': 'Drum.'}, {'birim': 'Sandık', 'kod': 'CS', 'gerekce': 'Case.'}, {'birim': 'Muhtelif', 'kod': 'eşlenemez', 'gerekce': 'Birim belirsiz; stok kartında gerçek birim yazılmalı.'}]}
OUTPUTS['birim-kodu-eslestirici'] = 'birim-esleme.md'
GOOD['tevkifat-kodu-onerici'] = {"oneriler": [{"urun": "Bina temizlik hizmeti - Eylül", "kod": "612", "oran": "9/10", "gerekce": "Temizlik hizmeti kısmi tevkifat kapsamında."}, {"urun": "Ofis kırtasiye malzemesi", "kod": "yok", "gerekce": "Kırtasiye malzemesi teslimi tevkifat kapsamında değil."}, {"urun": "Forklift periyodik bakım ve onarımı", "kod": "603", "oran": "7/10", "gerekce": "Makine ve teçhizat bakım onarımı."}, {"urun": "Personel servis taşımacılığı - Eylül", "kod": "614", "oran": "5/10", "gerekce": "Servis taşımacılığı hizmeti."}, {"urun": "Personel öğle yemeği hizmeti", "kod": "604", "gerekce": "Yemek servis hizmeti; oran tek."}]}
OUTPUTS['tevkifat-kodu-onerici'] = 'tevkifat-onerileri.md'
GOOD['log-ayristirici'] = {"desenler": [{"ad": "uygulama", "desen": "(?P<zaman>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{3}) (?P<seviye>[A-Z]+) \\[(?P<servis>[^\\]]+)\\] (?P<mesaj>.*)", "aciklama": "Uygulama logu (Python/Java logging biçimi)"}, {"ad": "nginx_erisim", "desen": "(?P<ip>[0-9.]+) - (?P<kullanici>[^ ]+) \\[(?P<zaman>[^\\]]+)\\] \"(?P<mesaj>(?P<metot>[A-Z]+) (?P<yol>[^ \"]+) [^\"]*)\" (?P<durum>\\d{3}) (?P<boyut>\\d+) \"(?P<referer>[^\"]*)\" \"(?P<istemci>[^\"]*)\" (?P<sure_sn>[0-9.]+)", "aciklama": "nginx combined + istek süresi"}]}
OUTPUTS['log-ayristirici'] = 'log-desenleri.md'
GOOD['anket-kodlayici'] = {"temalar": [{"kod": "T1", "ad": "Teslimat süresi", "tanim": "Geç teslimat, teslimat günü belirsizliği, hızlı teslimat isteği"}, {"kod": "T2", "ad": "Kargo ve ambalaj hasarı", "tanim": "Kırık/ezik ürün, özensiz paketleme"}, {"kod": "T3", "ad": "Müşteri hizmetlerine ulaşım ve çözüm", "tanim": "Çağrı merkezi, canlı destek, e-posta dönüşü ve sorun çözme"}, {"kod": "T4", "ad": "Fiyat ve kampanya", "tanim": "Fiyat seviyesi, kampanya tutarsızlığı, kargo ücreti"}, {"kod": "T5", "ad": "Mobil uygulama sorunları", "tanim": "Uygulamada hata, yavaşlık, oturum ve bildirim sorunları"}, {"kod": "T6", "ad": "Olumlu ya da öneri içermeyen", "tanim": "Memnuniyet, sorun yok ya da anlamsız yanıt"}], "atamalar": [{"id": "1", "temalar": ["T1"], "duygu": "olumsuz"}, {"id": "2", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "3", "temalar": ["T2"], "duygu": "olumsuz"}, {"id": "4", "temalar": ["T3"], "duygu": "olumsuz"}, {"id": "5", "temalar": ["T4"], "duygu": "olumsuz"}, {"id": "6", "temalar": ["T1"], "duygu": "olumsuz"}, {"id": "7", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "8", "temalar": ["T6"], "duygu": "olumlu"}, {"id": "9", "temalar": ["T1", "T2"], "duygu": "karışık"}, {"id": "10", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "11", "temalar": ["T3"], "duygu": "olumsuz"}, {"id": "12", "temalar": ["T4"], "duygu": "olumsuz"}, {"id": "13", "temalar": ["T1"], "duygu": "nötr"}, {"id": "14", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "15", "temalar": ["T2"], "duygu": "olumsuz"}, {"id": "16", "temalar": ["T3"], "duygu": "olumsuz"}, {"id": "17", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "18", "temalar": ["T4"], "duygu": "olumsuz"}, {"id": "19", "temalar": ["T6"], "duygu": "olumlu"}, {"id": "20", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "21", "temalar": ["T3"], "duygu": "olumsuz"}, {"id": "22", "temalar": ["T1"], "duygu": "nötr"}, {"id": "23", "temalar": ["T4", "T5"], "duygu": "olumsuz"}, {"id": "24", "temalar": ["T2"], "duygu": "olumsuz"}, {"id": "25", "temalar": ["T6"], "duygu": "nötr"}, {"id": "26", "temalar": ["T1"], "duygu": "olumsuz"}, {"id": "27", "temalar": ["T5"], "duygu": "olumsuz"}, {"id": "28", "temalar": ["T3"], "duygu": "karışık"}]}
OUTPUTS['anket-kodlayici'] = 'anket-temalari.md'
GOOD['alarm-esigi-onerici'] = {"kurallar": [{"metrik": "satis_satir", "tip": "oran_alt", "esik": 0.5, "onem": "kritik", "gerekce": "Satır sayısı aynı günün son 4 hafta medyanının yarısının altına düşerse yükleme eksik; hafta sonu düşük hacmi kendiliğinden hesaba katar."}, {"metrik": "yukleme_suresi_dk", "tip": "sabit_ust", "esik": 45, "onem": "uyarı", "gerekce": "Normal süre 13–31 dk; 45 dk üstü kilit ya da kaynak sorunu."}, {"metrik": "musteri_bos_orani", "tip": "sabit_ust", "esik": 2, "onem": "kritik", "gerekce": "Boş oranı geçmişte %0,8’i geçmedi; %2 üstü kaynak şema değişikliği."}]}
OUTPUTS['alarm-esigi-onerici'] = 'alarm-kurallari.md'
GOOD['kolon-adi-standardi'] = {"eslemeler": [{"tablo": "SIPARIS", "kolon": "SIP_NO", "yeni_ad": "siparis_no", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "MUST_KOD", "yeni_ad": "musteri_kodu", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "SIP_TRH", "yeni_ad": "siparis_tarihi", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "TeslimTarihi", "yeni_ad": "teslim_tarihi", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "Tutar (TL)", "yeni_ad": "tutar_tl", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "KDV", "yeni_ad": "kdv_tutari_tl", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "iptal", "yeni_ad": "iptal_mi", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "order", "yeni_ad": "sira_no", "gerekce": "standart ad"}, {"tablo": "SIPARIS", "kolon": "durum", "yeni_ad": "durum", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "MusteriKodu", "yeni_ad": "musteri_kodu", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "MusteriAdi", "yeni_ad": "musteri_adi", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "Şehir", "yeni_ad": "sehir", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "aktif?", "yeni_ad": "aktif_mi", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "KAYIT_ZAMANI", "yeni_ad": "kayit_zamani", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "user", "yeni_ad": "kullanici_adi", "gerekce": "standart ad"}, {"tablo": "MUSTERI", "kolon": "TEL", "yeni_ad": "telefon", "gerekce": "standart ad"}]}
OUTPUTS['kolon-adi-standardi'] = 'kolon-standardi.md'
GOOD['adres-cozumleyici'] = {"adresler": [{"id": "3", "mahalle": "Caferağa Mahallesi", "cadde_sokak": "Moda Caddesi", "no": "18", "kat": "", "daire": "4", "ilce": "Kadıköy", "il": "İstanbul", "posta_kodu": "34710"}, {"id": "5", "mahalle": "Suadiye Mahallesi", "cadde_sokak": "Bağdat Caddesi", "no": "312", "kat": "", "daire": "5", "ilce": "Kadıköy", "il": "İstanbul", "posta_kodu": ""}, {"id": "6", "mahalle": "Odunluk Mahallesi", "cadde_sokak": "Akpınar Caddesi", "no": "9", "kat": "", "daire": "", "ilce": "Nilüfer", "il": "Bursa", "posta_kodu": "16110"}, {"id": "9", "mahalle": "Esentepe Mahallesi", "cadde_sokak": "Büyükdere Caddesi", "no": "199", "kat": "", "daire": "", "ilce": "Şişli", "il": "İstanbul", "posta_kodu": "34394"}, {"id": "10", "mahalle": "Liman Mahallesi", "cadde_sokak": "25. Sokak", "no": "4", "kat": "", "daire": "", "ilce": "Konyaaltı", "il": "Antalya", "posta_kodu": ""}]}
OUTPUTS['adres-cozumleyici'] = 'adres-cozumleme.md'
GOOD['urun-kategori-eslestirici'] = {"eslemeler": [{"urun": "Buğday Unu 5 kg", "kategori": "K111", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Kırmızı Mercimek 1 kg", "kategori": "K112", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Baldo Pirinç 2 kg", "kategori": "K112", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Türk Kahvesi 100 g", "kategori": "K122", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Siyah Çay 1 kg", "kategori": "K122", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Sıvı Bulaşık Deterjanı 1,5 L", "kategori": "K21", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Tuvalet Kağıdı 32'li", "kategori": "K22", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Kağıt Havlu 12'li", "kategori": "K22", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "USB-C Şarj Kablosu 1 m", "kategori": "K31", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Telefon Kılıfı", "kategori": "K31", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Kettle 1,7 L", "kategori": "K32", "gerekce": "Ad açıkça bu kategoriyi belirtiyor."}, {"urun": "Hediye Seti", "kategori": "eşlenemez", "gerekce": "İçeriği belli değil; gıda, kozmetik ya da elektronik olabilir."}, {"urun": "Filtre Kahve Makinesi", "kategori": "K32", "gerekce": "Elektrikli küçük ev aleti; kahve kategorisi değil."}]}
OUTPUTS['urun-kategori-eslestirici'] = 'kategori-esleme.md'
GOOD['sema-degisiklik-etkisi'] = {"ozet": "Bir kırıcı değişiklik (eposta silindi) pazartesi CRM aktarımını bozar; tutar tipinin daraltılması ciro raporunda taşma riski yaratır.", "degisiklikler": [{"no": 1, "seviye": "kırıcı", "etki": "kampanya_listesi.sql eposta kolonunu seçiyor ve filtreliyor; sorgu bir sonraki çalışmada hata verir ve CRM aktarımı durur.", "aksiyonlar": ["Kaynak ekiple eposta kolonunun nereye taşındığını doğrula", "kampanya_listesi.sql sorgusunu yeni kaynağa göre güncelle", "CRM aktarımını değişiklik bitene kadar durdur"], "dosyalar": ["kampanya_listesi.sql"]}, {"no": 2, "seviye": "bilgi", "etki": "Yeni kanal kolonu henüz hiçbir sorguda kullanılmıyor.", "aksiyonlar": ["Ciro raporuna kanal kırılımı eklenip eklenmeyeceğini rapor sahibine sor"], "dosyalar": []}, {"no": 3, "seviye": "riskli", "etki": "tutar decimal(18,2)’den decimal(12,2)’ye daraltıldı; 10 milyarı aşan tutarlar sığmaz. Aylık ciro raporu bu kolonu topluyor.", "aksiyonlar": ["Kaynakta 10 haneyi aşan tutar olup olmadığını kontrol et", "Daraltmanın bilinçli olup olmadığını kaynak ekiple doğrula"], "dosyalar": ["rapor_aylik_ciro.sql"]}]}
OUTPUTS['sema-degisiklik-etkisi'] = 'degisiklik-etkisi.md'
GOOD['segment-yorumlayici'] = {"segmentler": [{"no": 0, "ad": "Yeni ve tek seferlik", "tarif": "Yakın zamanda alışveriş yapmış ama az sipariş ve düşük harcamalı müşteriler.", "ozellikler": [{"metrik": "harcama", "yon": "düşük"}, {"metrik": "siparis_sayisi", "yon": "düşük"}, {"metrik": "son_alisveris_gun", "yon": "düşük"}], "aksiyonlar": ["İkinci siparişe özel karşılama kuponu", "İlk 30 günde ürün önerisi e-postası"]}, {"no": 1, "ad": "Sadık yüksek değerli", "tarif": "Sık ve yüksek tutarlı alışveriş yapan, aktif müşteriler.", "ozellikler": [{"metrik": "harcama", "yon": "yüksek"}, {"metrik": "siparis_sayisi", "yon": "yüksek"}, {"metrik": "son_alisveris_gun", "yon": "düşük"}, {"metrik": "iade_orani", "yon": "düşük"}], "aksiyonlar": ["Sadakat programında üst seviye", "Yeni ürünlere erken erişim"]}, {"no": 2, "ad": "Kayıp riski", "tarif": "Ortalama harcamalı ama aylardır alışveriş yapmamış müşteriler.", "ozellikler": [{"metrik": "son_alisveris_gun", "yon": "yüksek"}, {"metrik": "harcama", "yon": "ortalama"}], "aksiyonlar": ["Geri kazanma kampanyası", "Ayrılma nedenini soran kısa anket"]}, {"no": 3, "ad": "Sık iade edenler", "tarif": "Ortalama sipariş sayısında ama iade oranı belirgin yüksek müşteriler.", "ozellikler": [{"metrik": "iade_orani", "yon": "yüksek"}, {"metrik": "siparis_sayisi", "yon": "ortalama"}], "aksiyonlar": ["İade nedenlerini ürün bazında incele", "Beden/ölçü rehberi gönder"]}]}
OUTPUTS['segment-yorumlayici'] = 'segmentler.md'
GOOD['sentetik-veri-tasarimcisi'] = {"anahtar": ["musteri_no"], "kolonlar": {"musteri_no": {"tur": "sira", "onek": "T", "baslangic": 1, "hane": 5}, "ad_soyad": {"tur": "sahte_ad"}, "eposta": {"tur": "sahte_eposta"}, "telefon": {"tur": "sahte_telefon", "bos_orani": 0.08}, "il": {"tur": "kategori", "degerler": {"İstanbul": 25, "İzmir": 13, "Ankara": 11, "Antalya": 6, "Bursa": 5}}, "segment": {"tur": "kategori", "degerler": {"Bireysel": 40, "KOBİ": 15, "Kurumsal": 5}}, "kayit_tarihi": {"tur": "tarih", "min": "2022-01-11", "max": "2026-08-20"}, "toplam_harcama": {"tur": "sayi", "min": 195.34, "max": 47984.79, "ondalik": True}, "aktif": {"tur": "kategori", "degerler": {"evet": 51, "hayır": 9}}}, "notlar": "Kişisel kolonlar sahte üreticilerle."}
OUTPUTS['sentetik-veri-tasarimcisi'] = 'sentetik-veri.md'
GOOD['belge-alan-cikarici'] = {"belgeler": [{"belge": "bulut_hizmet.txt", "alanlar": {"tedarikci": {"deger": "Bulutçu Bilişim Teknolojileri Ltd. Şti.", "kanit": "diğer yanda Bulutçu Bilişim Teknolojileri Ltd. Şti. (Hizmet Sağlayıcı)"}, "sozlesme_tarihi": {"deger": "2026-01-15", "kanit": "Bu sözleşme 15 Ocak 2026 tarihinde yürürlüğe girer"}, "bitis_tarihi": {"deger": "2028-01-14", "kanit": "14.01.2028 tarihine kadar geçerlidir"}, "aylik_bedel": {"deger": "185000", "kanit": "aylık 185.000,00 TL + KDV ödeyecektir"}, "para_birimi": {"deger": "TRY", "kanit": "aylık 185.000,00 TL + KDV"}, "fesih_bildirim_gun": {"deger": "30", "kanit": "otuz (30) gün önceden yazılı bildirimde bulunarak"}, "otomatik_yenileme": {"deger": "evet", "kanit": "birer yıllık dönemler halinde kendiliğinden uzar"}}}, {"belge": "guvenlik_teklifi.md", "alanlar": {"tedarikci": {"deger": "Kalkan Güvenlik Hizmetleri A.Ş.", "kanit": "Teklifi veren: Kalkan Güvenlik Hizmetleri A.Ş."}, "sozlesme_tarihi": {"deger": "", "kanit": ""}, "bitis_tarihi": {"deger": "", "kanit": ""}, "aylik_bedel": {"deger": "96400", "kanit": "Aylık hizmet bedeli 96.400 TL (KDV hariç)"}, "para_birimi": {"deger": "TRY", "kanit": "96.400 TL"}, "fesih_bildirim_gun": {"deger": "", "kanit": ""}, "otomatik_yenileme": {"deger": "", "kanit": ""}}}, {"belge": "yazilim_destek.txt", "alanlar": {"tedarikci": {"deger": "Kod Atölyesi Yazılım A.Ş.", "kanit": "Örnek Lojistik A.Ş. ile Kod Atölyesi Yazılım A.Ş. arasında"}, "sozlesme_tarihi": {"deger": "2026-03-01", "kanit": "İşbu sözleşme 01/03/2026 tarihinde"}, "bitis_tarihi": {"deger": "2027-02-28", "kanit": "28/02/2027 tarihinde sona erer"}, "aylik_bedel": {"deger": "4500", "kanit": "Aylık destek bedeli 4.500 USD"}, "para_birimi": {"deger": "USD", "kanit": "4.500 USD"}, "fesih_bildirim_gun": {"deger": "60", "kanit": "60 gün önceden bildirmek kaydıyla"}, "otomatik_yenileme": {"deger": "hayır", "kanit": "sözleşme kendiliğinden uzamaz"}}}]}
OUTPUTS['belge-alan-cikarici'] = 'belge-alanlari.md'
GOOD['birlestirme-anahtari'] = {'anahtarlar': [{'sol': 'cari_kodu', 'sag': 'ERP_REF', 'donusum_sol': ['trim', 'upper', 'onek_sil:C', 'sifir_sil'], 'donusum_sag': ['trim', 'upper', 'onek_sil:C', 'sifir_sil']}], 'iliski': '1-1', 'gerekce': 'CRM, ERP cari kodunu önek ve sıfırlar olmadan, bazen küçük harf ve sonda boşlukla tutuyor; vergi numarası bazı kayıtlarda boş ya da boşluklu.'}
OUTPUTS['birlestirme-anahtari'] = 'birlestirme.md'
GOOD['hata-siniflandirici'] = {'kategoriler': [{'kod': 'BAGLANTI', 'ad': 'Bağlantı / zaman aşımı', 'desen': 'timed out|timeout|could not connect|connection (reset|refused)', 'yeniden_dene': True, 'aksiyon': 'Üstel bekleme ile 3 kez yeniden dene; sürerse ağ/sunucu ekibine bildir.', 'ornekler': [1, 2]}, {'kod': 'KIMLIK', 'ad': 'Kimlik doğrulama', 'desen': 'login failed|password|authentication failed', 'yeniden_dene': False, 'aksiyon': 'Servis hesabı parolasının süresini ve kasadaki değerini kontrol et.', 'ornekler': [4]}, {'kod': 'DOSYA_YOK', 'ad': 'Kaynak dosya gelmedi', 'desen': 'no such file|filenotfound', 'yeniden_dene': True, 'aksiyon': 'Dosya geç gelmiş olabilir; 30 dk sonra yeniden dene, sonra kaynağa sor.', 'ornekler': [3]}, {'kod': 'SEMA', 'ad': 'Şema uyumsuzluğu', 'desen': 'invalid column name|column \\S+ does not exist|42S22', 'yeniden_dene': False, 'aksiyon': 'Kaynak şema değişikliğini incele; sema-degisiklik-etkisi agent’ını çalıştır.', 'ornekler': [5]}, {'kod': 'KILIT', 'ad': 'Kilit / deadlock', 'desen': 'deadlock', 'yeniden_dene': True, 'aksiyon': 'Kısa beklemeyle yeniden dene; tekrarlarsa yükleme saatlerini ayır.', 'ornekler': [6]}, {'kod': 'VERI_KALITESI', 'ad': 'Veri kalitesi testi', 'desen': 'validation failed|expect_column', 'yeniden_dene': False, 'aksiyon': 'Kaynak veriyi incele; hatalı satırları karantinaya al.', 'ornekler': [7]}, {'kod': 'KAYNAK', 'ad': 'Disk / bellek', 'desen': 'no space left|out of memory|memoryerror', 'yeniden_dene': False, 'aksiyon': 'Geçici alanı temizle ya da disk büyüt.', 'ornekler': [8]}]}
OUTPUTS['hata-siniflandirici'] = 'hata-siniflari.md'
GOOD['sozluk-kolon-eslestirici'] = {'terimler': [{'terim': 'Net Ciro', 'kolonlar': [{'tablo': 'dwh.fct_satis', 'kolon': 'net_tutar'}], 'gerekce': 'Anlamı birebir aynı.'}, {'terim': 'Brüt Satış Tutarı', 'kolonlar': [{'tablo': 'dwh.fct_satis', 'kolon': 'brut_tutar'}], 'gerekce': 'Anlamı birebir aynı.'}, {'terim': 'Sipariş Tarihi', 'kolonlar': [{'tablo': 'dwh.fct_satis', 'kolon': 'siparis_tarihi'}], 'gerekce': 'Anlamı birebir aynı.'}, {'terim': 'Müşteri Kodu', 'kolonlar': [{'tablo': 'dwh.fct_satis', 'kolon': 'musteri_kodu'}, {'tablo': 'dwh.dim_musteri', 'kolon': 'musteri_kodu'}], 'gerekce': 'Anlamı birebir aynı.'}, {'terim': 'Aktif Müşteri', 'kolonlar': [{'tablo': 'dwh.dim_musteri', 'kolon': 'aktif_mi'}], 'gerekce': 'Anlamı birebir aynı.'}, {'terim': 'Satış Kanalı', 'kolonlar': [{'tablo': 'dwh.fct_satis', 'kolon': 'kanal'}], 'gerekce': 'Siparişin kanalı; dim_musteri.kayit_kanali müşterinin ilk kayıt kanalıdır, farklı anlam.'}, {'terim': 'Müşteri Yaşam Boyu Değeri', 'kolonlar': [], 'gerekce': 'Ambarda tahmini kâr ya da CLV kolonu yok; hesaplanması gerekir.'}]}
OUTPUTS['sozluk-kolon-eslestirici'] = 'sozluk-esleme.md'
GOOD['tahsilat-eslestirici'] = {'eslesmeler': [{'hareket': 'H02', 'faturalar': ['FTR-2026-1003'], 'kismi': False, 'gerekce': 'Beta Yapı, tutar tek faturayla aynı.'}, {'hareket': 'H03', 'faturalar': ['FTR-2026-1002'], 'kismi': False, 'gerekce': 'Ada Gıda’nın kalan tek açık faturası.'}, {'hareket': 'H04', 'faturalar': ['FTR-2026-1004', 'FTR-2026-1005'], 'kismi': False, 'gerekce': 'Açıklamada 1004 ve 1005; toplam 12.501,00.'}, {'hareket': 'H05', 'faturalar': ['FTR-2026-1006'], 'kismi': True, 'gerekce': 'Ege Tekstil kısmi ödeme.'}, {'hareket': 'H06', 'faturalar': [], 'kismi': False, 'gerekce': 'Gönderen belli değil, tutar hiçbir faturaya uymuyor.'}]}
OUTPUTS['tahsilat-eslestirici'] = 'tahsilat-eslesme.md'
GOOD['maskeleme-plani'] = {'kolonlar': {'sicil_no': {'yontem': 'takma_ad', 'anahtar': True, 'gerekce': 'Bordro ile birleştirme anahtarı.'}, 'ad_soyad': {'yontem': 'sil'}, 'tckn': {'yontem': 'sil'}, 'eposta': {'yontem': 'kismi'}, 'telefon': {'yontem': 'sil'}, 'dogum_tarihi': {'yontem': 'genellestir', 'gerekce': 'Yaş analizi için yıl-ay yeterli.'}, 'departman': {'yontem': 'koru'}, 'maas': {'yontem': 'genellestir', 'aralik': 10000}, 'ise_giris': {'yontem': 'koru'}, 'notlar': {'yontem': 'koru'}}}
OUTPUTS['maskeleme-plani'] = 'maskeleme-plani.md'
GOOD['tip-donusum-eslestirici'] = {'eslemeler': [{'tablo': 'SIPARIS', 'kolon': 'SIPARIS_ID', 'hedef_tip': 'bigint', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'MUSTERI_KODU', 'hedef_tip': 'varchar(20)', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'SIPARIS_TARIHI', 'hedef_tip': 'timestamp', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'TESLIM_TARIHI', 'hedef_tip': 'date', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'TUTAR', 'hedef_tip': 'numeric(14,2)', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'KUR', 'hedef_tip': 'numeric(12,6)', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'IPTAL', 'hedef_tip': 'boolean', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'ADET', 'hedef_tip': 'integer', 'gerekce': 'ölçümlere göre'}, {'tablo': 'SIPARIS', 'kolon': 'ACIKLAMA', 'hedef_tip': 'varchar(4000)', 'gerekce': 'ölçümlere göre'}, {'tablo': 'MUSTERI', 'kolon': 'UNVAN', 'hedef_tip': 'varchar(200)', 'gerekce': 'ölçümlere göre'}, {'tablo': 'MUSTERI', 'kolon': 'NOTLAR', 'hedef_tip': 'text', 'gerekce': 'ölçümlere göre'}, {'tablo': 'MUSTERI', 'kolon': 'KAYIT_ZAMANI', 'hedef_tip': 'timestamp', 'gerekce': 'ölçümlere göre'}]}
OUTPUTS['tip-donusum-eslestirici'] = 'tip-donusum.md'
GOOD['dbt-test-onerici'] = {'kolonlar': {'siparis_no': [{'test': 'unique'}, {'test': 'not_null'}], 'siparis_tarihi': [{'test': 'not_null'}], 'musteri_kodu': [{'test': 'not_null'}, {'test': 'relationships', 'to': 'dim_musteri', 'field': 'musteri_kodu'}], 'durum': [{'test': 'accepted_values', 'values': ['yeni', 'hazirlaniyor', 'kargoda', 'teslim', 'iptal']}], 'kanal': [{'test': 'accepted_values', 'values': ['web', 'mobil', 'magaza']}], 'adet': [{'test': 'accepted_range', 'min_value': 1}], 'tutar': [{'test': 'accepted_range', 'min_value': 0}], 'indirim_orani': [{'test': 'accepted_range', 'min_value': 0, 'max_value': 100}]}}
OUTPUTS['dbt-test-onerici'] = 'test-onerileri.md'
GOOD['huni-analisti'] = {'en_buyuk_kayip': 'sepete_ekleme', 'en_kotu_segment': {'urun_goruntuleme': 'mobil', 'sepete_ekleme': 'mobil', 'odeme_baslatma': 'mobil', 'satin_alma': 'masaustu'}, 'ozet': '200 kullanıcının 22’si satın aldı (%11). En büyük kayıp ürün görüntülemeden sepete eklemeye geçişte: %40,7; mobilde bu oran %35,6.', 'oneriler': ['Mobil ürün sayfasında sepete ekle düğmesini sabitleyin ve test edin.', 'Ürün sayfasında kargo süresi ve iade koşullarını görünür yapın.']}
OUTPUTS['huni-analisti'] = 'huni-analizi.md'
GOOD['regresyon-yorumlayici'] = {'degiskenler': [{'ad': 'reklam_harcamasi', 'etki': 'pozitif', 'yorum': 'Reklam harcaması satışla pozitif ilişkili: diğer değişken sabitken katsayı 1,3004 (p 0,00023).'}, {'ad': 'indirim_orani', 'etki': 'pozitif', 'yorum': 'İndirim oranının her puanlık farkı satışta 8208,6979 birimlik farkla birlikte görülüyor (p 0,00001).'}], 'ozet': 'Model satıştaki değişimin %53,2’sini açıklıyor (R² 0,532); iki değişken de anlamlı ve satışla birlikte hareket ediyor.'}
OUTPUTS['regresyon-yorumlayici'] = 'regresyon-yorumu.md'
GOOD['vardiya-planlayici'] = {'vardiyalar': [{'baslangic': '08:00', 'sure_saat': 4.0, 'kisi': 7}, {'baslangic': '08:30', 'sure_saat': 4.0, 'kisi': 2}, {'baslangic': '09:00', 'sure_saat': 4.0, 'kisi': 2}, {'baslangic': '09:30', 'sure_saat': 4.0, 'kisi': 3}, {'baslangic': '10:00', 'sure_saat': 4.0, 'kisi': 3}, {'baslangic': '11:00', 'sure_saat': 4.0, 'kisi': 1}, {'baslangic': '12:00', 'sure_saat': 4.0, 'kisi': 5}, {'baslangic': '12:30', 'sure_saat': 4.0, 'kisi': 2}, {'baslangic': '13:00', 'sure_saat': 4.0, 'kisi': 2}, {'baslangic': '13:30', 'sure_saat': 4.0, 'kisi': 4}, {'baslangic': '14:00', 'sure_saat': 4.0, 'kisi': 3}, {'baslangic': '14:00', 'sure_saat': 6.0, 'kisi': 2}, {'baslangic': '14:30', 'sure_saat': 4.0, 'kisi': 1}, {'baslangic': '15:30', 'sure_saat': 4.0, 'kisi': 2}, {'baslangic': '16:00', 'sure_saat': 4.0, 'kisi': 4}], 'aciklama': 'Sabah ve akşam yoğunluğuna göre kademeli başlangıçlar.'}
OUTPUTS['vardiya-planlayici'] = 'vardiya-plani.md'
GOOD['excel-baslik-cozucu'] = {'baslik_satirlari': [5, 6], 'veri_ilk': 7, 'veri_son': 13, 'atlanacak_satirlar': [10], 'kolon_adlari': ['bolge', 'temsilci', 'temmuz_adet', 'temmuz_tutar', 'agustos_adet', 'agustos_tutar']}
OUTPUTS['excel-baslik-cozucu'] = 'baslik-cozumu.md'
GOOD['kafka-konu-tasarimcisi'] = {'konular': [{'konu': 'siparis-olaylari', 'bolum': 12, 'cogaltma': 3, 'min_insync': 2, 'saklama_saat': 168, 'cleanup': 'delete', 'gerekce': 'yük ve ihtiyaçlara göre'}, {'konu': 'tiklama-akisi', 'bolum': 24, 'cogaltma': 3, 'min_insync': 2, 'saklama_saat': 24, 'cleanup': 'delete', 'gerekce': 'yük ve ihtiyaçlara göre'}, {'konu': 'musteri-profil', 'bolum': 6, 'cogaltma': 3, 'min_insync': 2, 'saklama_saat': -1, 'cleanup': 'compact', 'gerekce': 'yük ve ihtiyaçlara göre'}, {'konu': 'odeme-sonuclari', 'bolum': 6, 'cogaltma': 3, 'min_insync': 2, 'saklama_saat': 336, 'cleanup': 'delete', 'gerekce': 'yük ve ihtiyaçlara göre'}, {'konu': 'stok-seviyesi', 'bolum': 3, 'cogaltma': 3, 'min_insync': 2, 'saklama_saat': -1, 'cleanup': 'compact', 'gerekce': 'yük ve ihtiyaçlara göre'}]}
OUTPUTS['kafka-konu-tasarimcisi'] = 'kafka-konulari.md'
GOOD['slo-tasarimcisi'] = {'slolar': [{'is': 'erp_stg_yukle', 'bitis_esigi': '04:45', 'hedef': 0.9, 'pencere_gun': 28, 'risk': False, 'gerekce': 'Sonraki işler 05:00’te başlıyor.'}, {'is': 'satis_dwh', 'bitis_esigi': '06:00', 'hedef': 0.95, 'pencere_gun': 28, 'risk': True, 'gerekce': 'Panoya 30 dk pay; son ayda üç gün 06:00’ı aştı, yükleme hızlandırılmalı.'}, {'is': 'gunluk_pano_yenile', 'bitis_esigi': '07:00', 'hedef': 0.9, 'pencere_gun': 28, 'risk': False, 'gerekce': 'Toplantıdan bir saat önce hazır.'}]}
OUTPUTS['slo-tasarimcisi'] = 'slo-tasarimi.md'
GOOD['etiket-kurali-yazari'] = {'kurallar': [{'desen': '^prod-', 'alan': 'kaynak_id', 'etiketler': {'tag_env': 'prod'}, 'gerekce': ''}, {'desen': '^dev-', 'alan': 'kaynak_id', 'etiketler': {'tag_env': 'dev'}, 'gerekce': ''}, {'desen': '^test-', 'alan': 'kaynak_id', 'etiketler': {'tag_env': 'test'}, 'gerekce': ''}, {'desen': '-satis-', 'alan': 'kaynak_id', 'etiketler': {'tag_owner': 'satis-ekibi', 'tag_cost_center': 'CC100'}, 'gerekce': ''}, {'desen': '-finans-', 'alan': 'kaynak_id', 'etiketler': {'tag_owner': 'finans-ekibi', 'tag_cost_center': 'CC200'}, 'gerekce': ''}, {'desen': '-veri-', 'alan': 'kaynak_id', 'etiketler': {'tag_owner': 'veri-ekibi', 'tag_cost_center': 'CC300'}, 'gerekce': ''}]}
OUTPUTS['etiket-kurali-yazari'] = 'etiket-kurallari.md'
GOOD['ozellik-muhendisi'] = {'ozellikler': [{'ad': 'sepet_gelir_orani', 'islem': 'oran', 'a': 'sepet', 'b': 'gelir', 'gerekce': 'iş anlamı'}, {'ad': 'yas_kova', 'islem': 'kova', 'a': 'yas', 'sinirlar': [30, 45, 60], 'gerekce': 'iş anlamı'}, {'ad': 'magaza_mi', 'islem': 'esit', 'a': 'kanal', 'deger': 'magaza', 'gerekce': 'iş anlamı'}, {'ad': 'log_gelir', 'islem': 'log', 'a': 'gelir', 'gerekce': 'iş anlamı'}]}
OUTPUTS['ozellik-muhendisi'] = 'ozellikler.md'
GOOD['kayma-yorumlayici'] = {'ozellikler': [{'ozellik': 'kanal', 'durum': 'belirgin kayma', 'olasi_neden': 'Mobil payı %20,3’ten %51,8’e çıktı; mağaza %35,9’dan %18,4’e düştü.', 'aksiyon': 'Mobil kanal kampanyasını doğrula, modeli yeni kanal dağılımıyla yeniden eğit.'}, {'ozellik': 'yas', 'durum': 'belirgin kayma', 'olasi_neden': '≤27 dilimi %10,7’den %26,7’ye çıktı; genç müşteri ağırlığı arttı.', 'aksiyon': 'Yaş dağılımı değişimini pazarlama ekibiyle teyit et.'}, {'ozellik': 'gelir', 'durum': 'kararlı', 'olasi_neden': '', 'aksiyon': ''}, {'ozellik': 'sepet', 'durum': 'kararlı', 'olasi_neden': '', 'aksiyon': ''}], 'yeniden_egit': True, 'ozet': 'İki özellikte belirgin kayma var (kanal PSI 0,464, yas PSI 0,372); model güncel müşteri profilini temsil etmiyor.'}
OUTPUTS['kayma-yorumlayici'] = 'kayma-raporu.md'
GOOD['sss-yazari'] = {'sss': [{'soru': 'Ürünü kaç gün içinde iade edebilirim?', 'cevap': 'Ürünler teslimattan itibaren 14 gün içinde, kullanılmamış ve orijinal ambalajındaysa iade edilebilir.', 'kaynak': 'iade_politikasi.md', 'kanit': 'Ürünler teslimattan itibaren 14 gün içinde iade edilebilir. İade edilecek ürün kullanılmamış ve orijinal ambalajında olmalıdır.'}, {'soru': 'Hangi ürünler iade edilemez?', 'cevap': 'Hijyen ürünleri, kişiye özel üretilen ürünler ve dijital içerikler iade kapsamı dışındadır.', 'kaynak': 'iade_politikasi.md', 'kanit': 'Hijyen ürünleri, kişiye özel üretilen ürünler ve dijital içerikler iade kapsamı dışındadır.'}, {'soru': 'İade ödemem ne zaman yapılır?', 'cevap': 'Ürün depoya ulaştıktan sonra 3 iş günü içinde kontrol edilir; onaylanan iadede ödeme 7 iş günü içinde aynı ödeme yöntemine yapılır.', 'kaynak': 'iade_politikasi.md', 'kanit': 'Ürün depoya ulaştıktan sonra 3 iş günü içinde kontrol edilir. Onaylanan iadelerde ödeme, kullanılan ödeme yöntemine 7 iş günü içinde geri yapılır.'}, {'soru': 'Ürün hasarlı gelirse ne yapmalıyım?', 'cevap': 'Teslimatta kargo görevlisine tutanak tutturun; tutanak yoksa fotoğrafla destek ekibine başvurabilirsiniz.', 'kaynak': 'iade_politikasi.md', 'kanit': 'Teslimatta hasarlı gelen ürünler için kargo görevlisine tutanak tutturulmalıdır. Tutanak olmadan hasar iddiası kabul edilmez; fotoğraf ile destek ekibine başvurulabilir.'}], 'celiskiler': [{'belgeler': ['kargo.md', 'kargo_kopya.md'], 'aciklama': 'Kargo ücreti bir belgede 49,90 TL, diğerinde 59,90 TL; güncel tutar netleşmeden kargo sorusu yazılmadı.'}]}
OUTPUTS['sss-yazari'] = 'sss.md'
GOOD['delta-bakim-plani'] = {'tablolar': [{'tablo': 'satis.fct_siparis', 'optimize': True, 'zorder': ['musteri_id', 'magaza_id'], 'vacuum_saat': 168, 'gerekce': 'dosya boyutu ve sorgu filtrelerine göre'}, {'tablo': 'satis.dim_musteri', 'optimize': False, 'zorder': [], 'vacuum_saat': None, 'gerekce': 'dosya boyutu ve sorgu filtrelerine göre'}, {'tablo': 'log.web_olaylari', 'optimize': True, 'zorder': ['kullanici_id', 'olay'], 'vacuum_saat': 168, 'gerekce': 'dosya boyutu ve sorgu filtrelerine göre'}, {'tablo': 'finans.gl_hareket', 'optimize': False, 'zorder': [], 'vacuum_saat': 168, 'gerekce': 'dosya boyutu ve sorgu filtrelerine göre'}]}
OUTPUTS['delta-bakim-plani'] = 'tablo-bakimi.md'
GOOD['kohort-yorumlayici'] = {'en_iyi_kohort': '2026-04', 'en_kotu_kohort': '2026-03', 'egilim': 'yatay', 'ozet': '1. ay tutundurma kohortlar arasında %18,2 ile %70 arasında dalgalanıyor; belirgin bir eğilim yok. En iyi 2026-04 kohortu (%70), en zayıf 2026-03 kohortu (%18,2).', 'oneriler': ['2026-04 kohortunun ilk ayında hangi kampanyanın çalıştığını inceleyin.', 'İlk siparişten sonraki ay için hatırlatma akışı kurun.']}
OUTPUTS['kohort-yorumlayici'] = 'kohort-yorumu.md'
GOOD['sabit-genislik-cozucu'] = {'alanlar': [{'alan': 'hesap_no', 'baslangic': 1, 'uzunluk': 10, 'tip': 'metin'}, {'alan': 'islem_tarihi', 'baslangic': 11, 'uzunluk': 8, 'tip': 'tarih:%Y%m%d'}, {'alan': 'islem_tipi', 'baslangic': 19, 'uzunluk': 3, 'tip': 'metin'}, {'alan': 'yon', 'baslangic': 22, 'uzunluk': 1, 'tip': 'metin'}, {'alan': 'tutar', 'baslangic': 23, 'uzunluk': 13, 'tip': 'sayi:2'}, {'alan': 'aciklama', 'baslangic': 36, 'uzunluk': 20, 'tip': 'metin'}, {'alan': 'sube_kodu', 'baslangic': 56, 'uzunluk': 4, 'tip': 'metin'}]}
OUTPUTS['sabit-genislik-cozucu'] = 'sabit-genislik.md'
GOOD['capraz-kural-yazari'] = {'kurallar': [{'kural': 'empty(teslim_tarihi) or teslim_tarihi >= siparis_tarihi', 'tur': 'kesin', 'aciklama': 'Teslimat siparişten önce olamaz.'}, {'kural': 'abs(tutar - (adet * birim_fiyat - indirim_tutari)) < 0.01', 'tur': 'kesin', 'aciklama': 'Tutar adet × fiyat − indirimdir.'}, {'kural': "durum != 'iptal' or not empty(iptal_tarihi)", 'tur': 'kesin', 'aciklama': 'İptal edilen siparişin iptal tarihi dolu olmalı.'}, {'kural': "durum != 'teslim' or not empty(teslim_tarihi)", 'tur': 'kesin', 'aciklama': 'Teslim edilen siparişin teslim tarihi olmalı.'}]}
OUTPUTS['capraz-kural-yazari'] = 'capraz-kurallar.md'
GOOD['scd-tipi-onerici'] = {'kolonlar': {'ad': 1, 'dogum_yili': 0, 'segment': 2, 'il': 2, 'telefon': 1, 'puan': 1}, 'gerekceler': {'segment': 'Raporlar geçmiş segmente göre analiz eder.', 'il': 'Bölge raporları taşınmadan önceki ile göre.', 'puan': 'Her ay değişir; tarihçe tabloyu büyütür.'}}
OUTPUTS['scd-tipi-onerici'] = 'scd-plani.md'
GOOD['kesinti-plani'] = {'plan': [{'gorev': 'Kullanıcı erişimini kapat', 'baslangic': '22:00'}, {'gorev': 'Son yedek', 'baslangic': '22:10'}, {'gorev': 'Delta veri aktarımı', 'baslangic': '23:10'}, {'gorev': 'Satır sayısı mutabakatı', 'baslangic': '00:40'}, {'gorev': 'Bakiye mutabakatı', 'baslangic': '00:40'}, {'gorev': 'Entegrasyonları yeni sisteme çevir', 'baslangic': '01:30'}, {'gorev': 'Eski sistemi salt okunur yap', 'baslangic': '02:10'}, {'gorev': 'Duman testleri', 'baslangic': '02:10'}, {'gorev': 'Kullanıcı erişimini aç', 'baslangic': '02:40'}], 'karar_noktasi': '01:30', 'notlar': 'Mutabakatlar bittikten sonra karar.'}
OUTPUTS['kesinti-plani'] = 'kesinti-plani.md'
GOOD['kod-listesi-eslestirici'] = {'eslemeler': [{'deger': 'K.Kartı', 'kod': 'KREDI_KARTI', 'gerekce': 'Kısaltma.'}, {'deger': 'KK', 'kod': 'KREDI_KARTI', 'gerekce': 'Kredi kartı kısaltması.'}, {'deger': 'Havale', 'kod': 'BANKA_TRANSFERI', 'gerekce': ''}, {'deger': 'EFT', 'kod': 'BANKA_TRANSFERI', 'gerekce': ''}, {'deger': 'Havale/EFT', 'kod': 'BANKA_TRANSFERI', 'gerekce': ''}, {'deger': 'FAST', 'kod': 'BANKA_TRANSFERI', 'gerekce': ''}, {'deger': 'Banka Kartı', 'kod': 'KREDI_KARTI', 'gerekce': 'Kod açıklamasında banka kartı da var.'}, {'deger': 'Çek', 'kod': 'eşlenemez', 'gerekce': 'Banka çeki; hediye çeki değil ve listede karşılığı yok.'}, {'deger': 'Karma', 'kod': 'eşlenemez', 'gerekce': 'Birden fazla ödeme tipi; satır bazında ayrılmalı.'}]}
OUTPUTS['kod-listesi-eslestirici'] = 'kod-esleme.md'
GOOD['sorgu-indeks-onerici'] = {'indeksler': [{'sql': 'CREATE INDEX ix_siparis_musteri_tarih ON siparis (musteri_id, siparis_tarihi)', 'gerekce': 'Sipariş geçmişi sayfası: eşitlik + sıralama.'}, {'sql': 'CREATE UNIQUE INDEX ux_musteri_eposta ON musteri (eposta)', 'gerekce': 'Giriş sorgusu; e-posta tekil olmalı.'}, {'sql': 'CREATE INDEX ix_kalem_urun ON siparis_kalem (urun_id)', 'gerekce': 'Ürünün geçtiği siparişler.'}]}
OUTPUTS['sorgu-indeks-onerici'] = 'indeks-onerileri.md'
GOOD['veri-sahibi-onerici'] = {'oneriler': [{'tablo': 'dwh.fct_satis', 'ekip': 'Veri Mühendisliği', 'sorumlu': 'can.o', 'gerekce': 'Tabloyu ETL servisi ve can.o yazıyor.'}, {'tablo': 'dwh.dim_urun', 'ekip': 'Veri Mühendisliği', 'sorumlu': 'zeynep.a', 'gerekce': ''}, {'tablo': 'mart.kampanya_performans', 'ekip': 'Pazarlama', 'sorumlu': 'elif.s', 'gerekce': 'Yazan yok; en çok Pazarlama kullanıyor.'}, {'tablo': 'stg.crm_musteri', 'ekip': 'Veri Mühendisliği', 'sorumlu': 'can.o', 'gerekce': ''}]}
OUTPUTS['veri-sahibi-onerici'] = 'veri-sahipligi.md'
GOOD['grafik-secici'] = {'grafikler': [{'soru': 'Aylık ciro', 'tur': 'cizgi', 'x': 'siparis_tarihi', 'y': 'tutar', 'toplama': 'sum', 'renk': None, 'zaman': 'ay'}, {'soru': 'Bölge', 'tur': 'yatay_cubuk', 'x': 'bolge', 'y': 'tutar', 'toplama': 'sum', 'renk': None, 'zaman': None}, {'soru': 'Kanal payı', 'tur': 'pasta', 'x': 'kanal', 'y': 'tutar', 'toplama': 'sum', 'renk': None, 'zaman': None}, {'soru': 'Toplam ciro', 'tur': 'kpi', 'x': None, 'y': 'tutar', 'toplama': 'sum', 'renk': None, 'zaman': None}]}
OUTPUTS['grafik-secici'] = 'grafik-secimi.md'
GOOD['rota-gun-atayici'] = {'atamalar': [{'musteri': 'B01', 'gun': 'Pzt'}, {'musteri': 'B02', 'gun': 'Pzt'}, {'musteri': 'B03', 'gun': 'Cum'}, {'musteri': 'B04', 'gun': 'Pzt'}, {'musteri': 'B05', 'gun': 'Pzt'}, {'musteri': 'B06', 'gun': 'Pzt'}, {'musteri': 'B07', 'gun': 'Pzt'}, {'musteri': 'B08', 'gun': 'Pzt'}, {'musteri': 'B09', 'gun': 'Sal'}, {'musteri': 'B10', 'gun': 'Sal'}, {'musteri': 'B11', 'gun': 'Sal'}, {'musteri': 'B12', 'gun': 'Sal'}, {'musteri': 'B13', 'gun': 'Sal'}, {'musteri': 'B14', 'gun': 'Sal'}, {'musteri': 'B15', 'gun': 'Sal'}, {'musteri': 'B16', 'gun': 'Sal'}, {'musteri': 'B17', 'gun': 'Çar'}, {'musteri': 'B18', 'gun': 'Çar'}, {'musteri': 'B19', 'gun': 'Çar'}, {'musteri': 'B20', 'gun': 'Çar'}, {'musteri': 'B21', 'gun': 'Çar'}, {'musteri': 'B22', 'gun': 'Çar'}, {'musteri': 'B23', 'gun': 'Çar'}, {'musteri': 'B24', 'gun': 'Çar'}, {'musteri': 'B25', 'gun': 'Per'}, {'musteri': 'B26', 'gun': 'Per'}, {'musteri': 'B27', 'gun': 'Per'}, {'musteri': 'B28', 'gun': 'Per'}, {'musteri': 'B29', 'gun': 'Per'}, {'musteri': 'B30', 'gun': 'Per'}, {'musteri': 'B31', 'gun': 'Per'}, {'musteri': 'B32', 'gun': 'Per'}, {'musteri': 'B33', 'gun': 'Cum'}, {'musteri': 'B34', 'gun': 'Cum'}, {'musteri': 'B35', 'gun': 'Cum'}, {'musteri': 'B36', 'gun': 'Cum'}, {'musteri': 'B37', 'gun': 'Cum'}, {'musteri': 'B38', 'gun': 'Cum'}, {'musteri': 'B39', 'gun': 'Cum'}, {'musteri': 'B40', 'gun': 'Cum'}], 'aciklama': 'Her gün bir ilçe kümesi.'}
OUTPUTS['rota-gun-atayici'] = 'teslimat-gunleri.md'
GOOD['yeniden-deneme-politikasi'] = {'gorevler': [{'dag': 'satis_gunluk', 'gorev': 'extract_erp', 'retries': 2, 'retry_delay_dk': 5, 'timeout_dk': 10, 'gerekce': 'ERP bağlantısı ara sıra kopuyor; 6 çalışma yeniden denemeyle kurtuldu.'}, {'dag': 'satis_gunluk', 'gorev': 'load_dwh', 'retries': 1, 'retry_delay_dk': 10, 'timeout_dk': 30, 'gerekce': 'Hatalar kalıcı; tekrar denemek süre yakar.'}, {'dag': 'satis_gunluk', 'gorev': 'refresh_pbi', 'retries': 1, 'retry_delay_dk': 5, 'timeout_dk': 10, 'gerekce': ''}, {'dag': 'crm_gunluk', 'gorev': 'api_cek', 'retries': 3, 'retry_delay_dk': 10, 'timeout_dk': 25, 'gerekce': 'API zaman aşımları geçici.'}]}
OUTPUTS['yeniden-deneme-politikasi'] = 'yeniden-deneme.md'
GOOD['autovacuum-ayarlayici'] = {'tablolar': [{'tablo': 'public.siparis', 'vacuum_scale_factor': 0.01, 'vacuum_threshold': 10000, 'analyze_scale_factor': 0.01, 'gerekce': 'günlük değişime göre'}, {'tablo': 'public.oturum', 'vacuum_scale_factor': 0.05, 'vacuum_threshold': 1000, 'analyze_scale_factor': 0.05, 'gerekce': 'günlük değişime göre'}, {'tablo': 'public.urun', 'vacuum_scale_factor': 0.005, 'vacuum_threshold': 50, 'analyze_scale_factor': 0.01, 'gerekce': 'günlük değişime göre'}, {'tablo': 'public.audit_log', 'vacuum_scale_factor': 0.2, 'vacuum_threshold': 50, 'analyze_scale_factor': 0.01, 'gerekce': 'günlük değişime göre'}, {'tablo': 'public.ayarlar', 'vacuum_scale_factor': 0.2, 'vacuum_threshold': 50, 'analyze_scale_factor': 0.1, 'gerekce': 'günlük değişime göre'}]}
OUTPUTS['autovacuum-ayarlayici'] = 'autovacuum.md'
GOOD['parca-boyutu-secici'] = {'parca_boyu': 400, 'ortusme': 40, 'gerekce': 'En küçük %100 kapsama veren boy; 200 ve 300’de bazı cevaplar bölünüyor.'}
OUTPUTS['parca-boyutu-secici'] = 'parca-boyutu.md'
GOOD['masraf-politika-denetcisi'] = {'atamalar': [{'aciklama': 'Ankara otel 1 gece', 'kategori': 'konaklama', 'gerekce': ''}, {'aciklama': 'Akşam yemeği', 'kategori': 'yemek', 'gerekce': ''}, {'aciklama': 'Taksi havalimanı', 'kategori': 'ulasim', 'gerekce': ''}, {'aciklama': 'Öğle yemeği', 'kategori': 'yemek', 'gerekce': ''}, {'aciklama': 'İstanbul-İzmir uçak bileti', 'kategori': 'ucak', 'gerekce': ''}, {'aciklama': 'İzmir otel 1 gece', 'kategori': 'konaklama', 'gerekce': ''}, {'aciklama': 'Müşteri yemeği (Deniz Gıda, 3 kişi)', 'kategori': 'musteri_agirlama', 'gerekce': ''}, {'aciklama': 'Otopark', 'kategori': 'ulasim', 'gerekce': ''}, {'aciklama': 'Hız cezası', 'kategori': 'kapsam_disi', 'gerekce': 'Trafik cezaları şirketçe karşılanmaz.'}, {'aciklama': 'Veri mühendisliği konferansı kaydı', 'kategori': 'egitim', 'gerekce': ''}, {'aciklama': 'akşam yemeği', 'kategori': 'yemek', 'gerekce': ''}, {'aciklama': 'Kişisel market alışverişi', 'kategori': 'kapsam_disi', 'gerekce': 'Kişisel harcama.'}]}
OUTPUTS['masraf-politika-denetcisi'] = 'masraf-denetimi.md'
GOOD['baslik-birlestirici'] = {'kolonlar': ['tarih', 'musteri', 'bolge', 'kanal', 'tutar', 'iskonto'], 'eslemeler': {'2026_07_satis.csv': {'Tarih': 'tarih', 'Müşteri': 'musteri', 'Bölge': 'bolge', 'Tutar TL': 'tutar', 'Not': None}, '2026_08_satis.csv': {'tarih': 'tarih', 'musteri_adi': 'musteri', 'bolge': 'bolge', 'kanal': 'kanal', 'tutar': 'tutar', 'iskonto': 'iskonto'}, '2026_09_sales.csv': {'Date': 'tarih', 'Customer': 'musteri', 'Region': 'bolge', 'Channel': 'kanal', 'Amount': 'tutar', 'Discount': 'iskonto', 'Sales Rep': None}}}
OUTPUTS['baslik-birlestirici'] = 'baslik-birlestirme.md'
GOOD['siparis-onerici'] = {'hizmet_seviyeleri': {'A': 0.98, 'B': 0.95, 'C': 0.9}, 'siparisler': [{'urun': 'Dizüstü Bilgisayar', 'miktar': 100, 'gerekce': 'ROP altında'}, {'urun': 'Kulaklık', 'miktar': 300, 'gerekce': 'ROP altında'}, {'urun': 'Monitör', 'miktar': 130, 'gerekce': 'ROP altında'}, {'urun': 'USB Bellek', 'miktar': 600, 'gerekce': 'ROP altında'}]}
OUTPUTS['siparis-onerici'] = 'siparis-onerileri.md'
GOOD['maliyet-anomali-aciklayici'] = {'anomaliler': [{'tarih': '2026-09-18', 'ana_servis': 'AWS Glue', 'ana_kaynak': 'glue-satis', 'aciklama': 'Günlük maliyet 1072.36, beklenen 787.28 (%36.2 fazla). Artışın neredeyse tamamı AWS Glue’dan (+294.86); en büyük katkı glue-satis işi (+172.26).', 'aksiyon': 'glue-satis işinin o günkü DPU sayısını ve tekrar çalışmalarını kontrol edin; iş için maliyet alarmı kurun.'}]}
OUTPUTS['maliyet-anomali-aciklayici'] = 'maliyet-anomalileri.md'
GOOD['gecerli-aralik-onerici'] = {'kolonlar': [{'kolon': 'fiyat', 'min': 0.01, 'max': None, 'uc_degerler_gecerli': True, 'gerekce': 'Fiyat pozitif olmalı; 2.000 TL üstü ürünler premium segmentte, geçerli.'}, {'kolon': 'agirlik_kg', 'min': 0.05, 'max': 50, 'uc_degerler_gecerli': False, 'gerekce': 'Kargo ürünleri 50 kg altında; 9999 gram/kg karışıklığı.'}, {'kolon': 'stok', 'min': 0, 'max': None, 'uc_degerler_gecerli': True, 'gerekce': 'Stok negatif olamaz; yüksek stoklu hızlı ürünler geçerli.'}, {'kolon': 'indirim_orani', 'min': 0, 'max': 100, 'uc_degerler_gecerli': False, 'gerekce': 'Oran yüzde olarak 0–100.'}, {'kolon': 'garanti_ay', 'min': 0, 'max': 60, 'uc_degerler_gecerli': False, 'gerekce': 'Garanti en fazla 5 yıl.'}]}
OUTPUTS['gecerli-aralik-onerici'] = 'gecerli-araliklar.md'
GOOD['hipotez-testi-secici'] = {'hedef': 'cozum_suresi_saat', 'grup': 'kanal', 'test': 'kruskal', 'gerekce': 'Üç kanal, çözüm süreleri sağa çarpık; parametrik olmayan test.'}
OUTPUTS['hipotez-testi-secici'] = 'hipotez-testi.md'
GOOD['filigran-onerici'] = {'kaynaklar': [{'kaynak': 'mobil', 'watermark_sn': 300, 'izinli_gecikme_sn': 600, 'gerekce': 'Mobil uygulama çevrimdışıyken olaylar 15 dakikaya kadar gecikiyor; pencereyi 5 dk bekleyip geç olayları 10 dk kabul et.'}, {'kaynak': 'pos', 'watermark_sn': 5, 'izinli_gecikme_sn': 5, 'gerekce': 'POS gecikmesi birkaç saniye.'}, {'kaynak': 'web', 'watermark_sn': 15, 'izinli_gecikme_sn': 10, 'gerekce': 'Web gecikmesi 25 sn altında.'}]}
OUTPUTS['filigran-onerici'] = 'filigran.md'
GOOD['incremental-strateji-onerici'] = {'modeller': [{'tablo': 'magazalar', 'materialized': 'table', 'unique_key': None, 'strateji': None, 'geriye_bakis_gun': 0, 'gerekce': '12 mağazalık küçük boyut tablosu; her yüklemede tam görüntü geliyor.'}, {'tablo': 'siparisler', 'materialized': 'incremental', 'unique_key': ['siparis_id'], 'strateji': 'merge', 'geriye_bakis_gun': 3, 'gerekce': 'Siparişler durum değiştikçe yeniden geliyor; 70 saate varan gecikme için 3 gün geriye bak.'}, {'tablo': 'web_olaylari', 'materialized': 'incremental', 'unique_key': ['olay_id'], 'strateji': 'merge', 'geriye_bakis_gun': 3, 'gerekce': 'Olaylar güncellenmiyor ama 60 saate kadar geç geliyor; geriye bakışla tekrar seçilenler merge ile tekilleşir.'}]}
OUTPUTS['incremental-strateji-onerici'] = 'incremental-strateji.md'
GOOD['katman-atayici'] = {'tablolar': [{'tablo': 'crm.musteri', 'katman': 'bronze', 'yeni_ad': 'bronze.crm_musteri', 'gerekce': ''}, {'tablo': 'stg.musteri', 'katman': 'silver', 'yeni_ad': 'silver.musteri', 'gerekce': ''}, {'tablo': 'dwh.dim_musteri', 'katman': 'gold', 'yeni_ad': 'gold.dim_musteri', 'gerekce': ''}, {'tablo': 'erp.siparis', 'katman': 'bronze', 'yeni_ad': 'bronze.erp_siparis', 'gerekce': ''}, {'tablo': 'stg.siparis', 'katman': 'silver', 'yeni_ad': 'silver.siparis', 'gerekce': ''}, {'tablo': 'erp.siparis_kalem', 'katman': 'bronze', 'yeni_ad': 'bronze.erp_siparis_kalem', 'gerekce': ''}, {'tablo': 'stg.siparis_kalem', 'katman': 'silver', 'yeni_ad': 'silver.siparis_kalem', 'gerekce': ''}, {'tablo': 'dwh.fct_siparis', 'katman': 'gold', 'yeni_ad': 'gold.fct_siparis', 'gerekce': ''}, {'tablo': 'erp.urun', 'katman': 'bronze', 'yeni_ad': 'bronze.erp_urun', 'gerekce': ''}, {'tablo': 'stg.urun', 'katman': 'silver', 'yeni_ad': 'silver.urun', 'gerekce': ''}, {'tablo': 'dwh.dim_urun', 'katman': 'gold', 'yeni_ad': 'gold.dim_urun', 'gerekce': ''}, {'tablo': 'mart.satis_ozet', 'katman': 'gold', 'yeni_ad': 'gold.satis_ozet', 'gerekce': ''}, {'tablo': 'mart.musteri_ozet', 'katman': 'gold', 'yeni_ad': 'gold.musteri_ozet', 'gerekce': ''}, {'tablo': 'pazarlama.kampanya', 'katman': 'bronze', 'yeni_ad': 'bronze.pazarlama_kampanya', 'gerekce': ''}, {'tablo': 'mart.kampanya_performans', 'katman': 'gold', 'yeni_ad': 'gold.kampanya_performans', 'gerekce': ''}, {'tablo': 'web.olay', 'katman': 'bronze', 'yeni_ad': 'bronze.web_olay', 'gerekce': ''}, {'tablo': 'stg.web_olay', 'katman': 'silver', 'yeni_ad': 'silver.web_olay', 'gerekce': ''}, {'tablo': 'dwh.fct_oturum', 'katman': 'gold', 'yeni_ad': 'gold.fct_oturum', 'gerekce': ''}, {'tablo': 'mart.donusum_hunisi', 'katman': 'gold', 'yeni_ad': 'gold.donusum_hunisi', 'gerekce': ''}], 'ara_tablolar': [{'kaynak': 'pazarlama.kampanya', 'hedef': 'mart.kampanya_performans', 'ad': 'silver.kampanya', 'gerekce': 'Kampanya verisi temizlenmeden rapora gidiyor.'}]}
OUTPUTS['katman-atayici'] = 'katman-atama.md'
GOOD['baglanti-havuzu-boyutlandirici'] = {'servisler': [{'servis': 'siparis-api', 'havuz_max': 5, 'havuz_min': 2, 'baglanti_bekleme_ms': 2000, 'gerekce': 'p95’te ~3 eşzamanlı sorgu; 4 instance × 5 = 20.'}, {'servis': 'odeme-api', 'havuz_max': 7, 'havuz_min': 2, 'baglanti_bekleme_ms': 3000, 'gerekce': 'p95’te ~5; ödeme kesintisine tolerans yok.'}, {'servis': 'katalog-api', 'havuz_max': 4, 'havuz_min': 1, 'baglanti_bekleme_ms': 1000, 'gerekce': ''}, {'servis': 'raporlama', 'havuz_max': 15, 'havuz_min': 2, 'baglanti_bekleme_ms': 10000, 'gerekce': 'Uzun rapor sorguları; ayrı ve sınırlı havuz.'}, {'servis': 'bildirim-worker', 'havuz_max': 3, 'havuz_min': 1, 'baglanti_bekleme_ms': 1000, 'gerekce': ''}]}
OUTPUTS['baglanti-havuzu-boyutlandirici'] = 'baglanti-havuzu.md'
GOOD['geofence-yaricap-onerici'] = {'magazalar': [{'magaza': 'MG01', 'yaricap_m': 170, 'gerekce': 'Çarşı içi; Moda’ya 697 m.'}, {'magaza': 'MG02', 'yaricap_m': 210, 'gerekce': ''}, {'magaza': 'MG03', 'yaricap_m': 300, 'gerekce': 'AVM otoparkı geniş.'}, {'magaza': 'MG04', 'yaricap_m': 290, 'gerekce': ''}, {'magaza': 'MG05', 'yaricap_m': 280, 'gerekce': ''}, {'magaza': 'MG06', 'yaricap_m': 270, 'gerekce': ''}]}
OUTPUTS['geofence-yaricap-onerici'] = 'geofence.md'
GOOD['esik-secici'] = {'esik': 0.45, 'aciklama': '0,45 eşiğinde 218 alarm üretiliyor; 111 gerçek vakanın 99’u yakalanıyor (duyarlılık 0,8919) ve toplam maliyet 4180. 0,5 eşiğinde maliyet 4370’e çıkıyor çünkü kaçan vaka 19’a yükseliyor.'}
OUTPUTS['esik-secici'] = 'esik-secimi.md'
GOOD['k8s-kaynak-ayarlayici'] = {'deploymentlar': [{'deployment': 'siparis-api', 'cpu_request_m': 450, 'cpu_limit_m': None, 'mem_request_mib': 640, 'mem_limit_mib': 800, 'gerekce': 'p95 420m; 1 çekirdek request yarı yarıya boş.'}, {'deployment': 'sepet-api', 'cpu_request_m': 230, 'cpu_limit_m': None, 'mem_request_mib': 400, 'mem_limit_mib': 512, 'gerekce': ''}, {'deployment': 'arama', 'cpu_request_m': 1800, 'cpu_limit_m': None, 'mem_request_mib': 2700, 'mem_limit_mib': 3500, 'gerekce': 'Bellek limiti 3072 MiB en yüksek kullanımın (3100) altında; OOMKilled riski.'}, {'deployment': 'resim-isleyici', 'cpu_request_m': 300, 'cpu_limit_m': None, 'mem_request_mib': 512, 'mem_limit_mib': 1024, 'gerekce': 'Ani CPU tepeleri için limit konmadı.'}, {'deployment': 'etl-worker', 'cpu_request_m': 800, 'cpu_limit_m': None, 'mem_request_mib': 1600, 'mem_limit_mib': 2048, 'gerekce': '4 çekirdek ve 8 GiB büyük ölçüde boşta.'}, {'deployment': 'bildirim', 'cpu_request_m': 50, 'cpu_limit_m': None, 'mem_request_mib': 128, 'mem_limit_mib': 256, 'gerekce': ''}]}
OUTPUTS['k8s-kaynak-ayarlayici'] = 'k8s-kaynaklari.md'
GOOD['toplu-cekim-planlayici'] = {'uc_noktalar': [{'uc_nokta': '/musteriler', 'mod': 'artimli', 'sayfa_boyutu': 500, 'dakikada_istek': 2, 'gerekce': ''}, {'uc_nokta': '/siparisler', 'mod': 'artimli', 'sayfa_boyutu': 1000, 'dakikada_istek': 5, 'gerekce': 'Günde 52.000 değişen sipariş; tam çekim 2.600 istek.'}, {'uc_nokta': '/urunler', 'mod': 'tam', 'sayfa_boyutu': 200, 'dakikada_istek': 10, 'gerekce': 'Artımlı destek yok; 210 istek.'}, {'uc_nokta': '/faturalar', 'mod': 'artimli', 'sayfa_boyutu': 500, 'dakikada_istek': 3, 'gerekce': ''}, {'uc_nokta': '/stok', 'mod': 'tam', 'sayfa_boyutu': 1000, 'dakikada_istek': 5, 'gerekce': ''}, {'uc_nokta': '/kampanyalar', 'mod': 'tam', 'sayfa_boyutu': 100, 'dakikada_istek': 2, 'gerekce': ''}]}
OUTPUTS['toplu-cekim-planlayici'] = 'toplu-cekim.md'
GOOD['backfill-planlayici'] = {'daglar': [{'dag': 'musteri_bakiye', 'max_active_runs': 1, 'sira': 'eskiden_yeniye', 'oncelik': 1, 'gerekce': 'depends_on_past; 181 dk seri iş, en uzun zincir önce başlamalı.'}, {'dag': 'kpi_ozet', 'max_active_runs': 1, 'sira': 'eskiden_yeniye', 'oncelik': 2, 'gerekce': 'depends_on_past.'}, {'dag': 'satis_gunluk', 'max_active_runs': 2, 'sira': 'eskiden_yeniye', 'oncelik': 3, 'gerekce': ''}, {'dag': 'stok_snapshot', 'max_active_runs': 2, 'sira': 'yeniden_eskiye', 'oncelik': 4, 'gerekce': 'Güncel stok raporları önce düzelsin.'}]}
OUTPUTS['backfill-planlayici'] = 'backfill.md'
GOOD['butce-fazlandirici'] = {'hesaplar': [{'hesap': '770', 'agirliklar': [0.196662, 0.070684, 0.072841, 0.071066, 0.070415, 0.072881, 0.076682, 0.075817, 0.075557, 0.071558, 0.073875, 0.071962], 'istisna_aylar': [1], 'gerekce': 'Ocak’ta 150.000 TL peşin lisans; kalan geçen yıl dağılımıyla.'}, {'hesap': '760', 'agirliklar': [0.045796, 0.053063, 0.121102, 0.082284, 0.073356, 0.065065, 0.04877, 0.053545, 0.085152, 0.095896, 0.153408, 0.122563], 'istisna_aylar': [3], 'gerekce': 'Mart’ta fuar için 40.000 TL; Kasım kampanyası geçen yılla aynı.'}, {'hesap': '600', 'agirliklar': [0.069163, 0.063877, 0.075753, 0.084718, 0.083338, 0.088665, 0.091375, 0.079868, 0.086908, 0.086332, 0.092054, 0.097949], 'istisna_aylar': [], 'gerekce': ''}, {'hesap': '621', 'agirliklar': [0.069224, 0.066986, 0.077678, 0.086024, 0.0832, 0.090657, 0.092348, 0.082557, 0.080315, 0.08151, 0.094302, 0.095199], 'istisna_aylar': [], 'gerekce': ''}]}
OUTPUTS['butce-fazlandirici'] = 'butce-fazlandirma.md'
GOOD['k-anonimlik-genellestirici'] = {'kolonlar': [{'kolon': 'yas', 'tip': 'aralik', 'deger': 20, 'gerekce': '20 yıllık yaş grupları'}, {'kolon': 'cinsiyet', 'tip': 'koru', 'deger': 0, 'gerekce': ''}, {'kolon': 'posta_kodu', 'tip': 'onek', 'deger': 2, 'gerekce': 'İl düzeyi'}, {'kolon': 'meslek', 'tip': 'gizle', 'deger': 0, 'gerekce': '11 meslek küçük grup oluşturuyor'}]}
OUTPUTS['k-anonimlik-genellestirici'] = 'k-anonimlik.md'
GOOD['yukleme-sirasi-planlayici'] = {'sira': ['ulke', 'sehir', 'urun', 'departman', 'calisan', 'musteri', 'siparis', 'siparis_kalem', 'fatura'], 'ertelenen': [{'tablo': 'departman', 'kolon': 'mudur_id', 'gerekce': 'departman ↔ calisan döngüsü'}, {'tablo': 'calisan', 'kolon': 'yonetici_id', 'gerekce': 'kendine referans'}]}
OUTPUTS['yukleme-sirasi-planlayici'] = 'yukleme-sirasi.md'
GOOD['ozet-tablo-onerici'] = {'ozet_tablolar': [{'ad': 'agg_ay_bolge_kategori_kanal', 'boyutlar': ['ay', 'bolge', 'kategori', 'kanal'], 'olcumler': ['sum(tutar)', 'sum(adet)', 'count(*)', 'max(tutar)'], 'gerekce': 'Q01, Q02, Q03, Q04, Q09, Q10 bu tablodan toplanır.'}, {'ad': 'agg_ay_magaza', 'boyutlar': ['ay', 'magaza'], 'olcumler': ['sum(tutar)', 'sum(adet)'], 'gerekce': 'Mağaza bazında aylık rapor (Q06).'}, {'ad': 'agg_ay_tekil_musteri', 'boyutlar': ['ay'], 'olcumler': ['count_distinct(musteri)'], 'gerekce': 'Tekil müşteri yeniden toplanamaz; ay düzeyinde ayrı tutulur.'}]}
OUTPUTS['ozet-tablo-onerici'] = 'ozet-tablolar.md'
GOOD['rol-madenciligi'] = {'roller': [{'ad': 'satis_temel', 'yetkiler': ['crm.okuma', 'crm.yazma', 'rapor.satis', 'siparis.okuma', 'siparis.yazma']}, {'ad': 'finans_temel', 'yetkiler': ['fatura.okuma', 'fatura.yazma', 'odeme.okuma', 'rapor.finans', 'siparis.okuma']}, {'ad': 'destek', 'yetkiler': ['crm.okuma', 'destek.yazma', 'siparis.okuma']}, {'ad': 'analist_okuma', 'yetkiler': ['crm.okuma', 'fatura.okuma', 'rapor.finans', 'rapor.satis', 'siparis.okuma']}, {'ad': 'dwh_okuma', 'yetkiler': ['dwh.okuma']}, {'ad': 'satis_yonetici_ek', 'yetkiler': ['siparis.onay', 'rapor.yonetim']}, {'ad': 'finans_yonetici_ek', 'yetkiler': ['odeme.onay', 'rapor.yonetim']}], 'atamalar': [{'kume': 'K1', 'roller': ['satis_temel']}, {'kume': 'K2', 'roller': ['finans_temel']}, {'kume': 'K3', 'roller': ['destek']}, {'kume': 'K4', 'roller': ['analist_okuma', 'dwh_okuma']}, {'kume': 'K5', 'roller': ['satis_temel', 'satis_yonetici_ek']}, {'kume': 'K6', 'roller': ['satis_temel', 'dwh_okuma']}, {'kume': 'K7', 'roller': ['finans_temel', 'finans_yonetici_ek']}]}
OUTPUTS['rol-madenciligi'] = 'rol-madenciligi.md'
GOOD['getirme-ayari-onerici'] = {'top_k': 5, 'min_score': 0.7, 'gerekce': 'Bulma oranı %90’a ilk top_k 5’te ulaşıyor; 0,7 altındaki parçalar bağlamı büyütüyor ama doğru cevap getirmiyor.'}
OUTPUTS['getirme-ayari-onerici'] = 'getirme-ayari.md'
GOOD['zaman-asimi-onerici'] = {'roller': [{'rol': 'analist', 'statement_timeout_ms': 600000, 'lock_timeout_ms': 2000, 'idle_in_transaction_ms': 600000, 'gerekce': 'Ad-hoc sorgular 6 dakikaya kadar sürüyor; saatlerce süren kaçak sorgular 10 dakikada kesilir.'}, {'rol': 'app_batch', 'statement_timeout_ms': 60000, 'lock_timeout_ms': 5000, 'idle_in_transaction_ms': 300000, 'gerekce': ''}, {'rol': 'app_web', 'statement_timeout_ms': 2000, 'lock_timeout_ms': 500, 'idle_in_transaction_ms': 60000, 'gerekce': 'Web isteklerinde 30 sn kilit beklemesi kullanıcıyı bekletir.'}, {'rol': 'rapor', 'statement_timeout_ms': 400000, 'lock_timeout_ms': 3000, 'idle_in_transaction_ms': 600000, 'gerekce': ''}]}
OUTPUTS['zaman-asimi-onerici'] = 'zaman-asimi.md'
GOOD['kaynak-tazelik-onerici'] = {'kaynaklar': [{'kaynak': 'crm.musteriler', 'warn_after': {'count': 3, 'period': 'day'}, 'error_after': {'count': 4, 'period': 'day'}, 'gerekce': 'Gece yüklemesi ara sıra atlanıyor; tek atlama uyarı üretmesin.'}, {'kaynak': 'erp.siparisler', 'warn_after': {'count': 2, 'period': 'hour'}, 'error_after': {'count': 6, 'period': 'hour'}, 'gerekce': ''}, {'kaynak': 'finans.kur', 'warn_after': {'count': 36, 'period': 'hour'}, 'error_after': {'count': 2, 'period': 'day'}, 'gerekce': ''}, {'kaynak': 'web.olaylar', 'warn_after': {'count': 30, 'period': 'minute'}, 'error_after': {'count': 1, 'period': 'hour'}, 'gerekce': '15 dakikalık akış.'}]}
OUTPUTS['kaynak-tazelik-onerici'] = 'kaynak-tazelik.md'
GOOD['zorder-secici'] = {'tablolar': [{'tablo': 'satis.siparis', 'kolonlar': ['musteri_id', 'magaza_id', 'urun_id'], 'gerekce': 'Tarih bölümleme; müşteri, mağaza ve ürün filtreleri sorguların %85’i.'}, {'tablo': 'web.olay', 'kolonlar': ['kullanici_id', 'oturum_id', 'sayfa'], 'gerekce': 'Olay tipi 12 değerli; veri atlama sağlamaz.'}]}
OUTPUTS['zorder-secici'] = 'zorder.md'
GOOD['anket-agirliklandirici'] = {'degiskenler': ['yas_grubu', 'bolge', 'cinsiyet', 'egitim'], 'kirpma_alt': 0.3, 'kirpma_ust': 3, 'gerekce': '55+ ve Diğer bölgeler az temsil ediliyor; eğitim de 9 puan sapıyor.'}
OUTPUTS['anket-agirliklandirici'] = 'anket-agirliklari.md'
GOOD['depo-yeri-secici'] = {'depolar': ['D-Gebze', 'D-Ankara', 'D-Manisa', 'D-Adana', 'D-Samsun', 'D-Antalya'], 'gerekce': 'Konya ve Kayseri talebi Ankara ve Adana’dan karşılanıyor; sabit maliyet tasarrufu taşımayı aşıyor.'}
OUTPUTS['depo-yeri-secici'] = 'depo-secimi.md'
GOOD['tekrar-penceresi-onerici'] = {'anahtar': ['olay_id'], 'pencere_sn': 300, 'gerekce': 'Yeniden teslimler 253 sn içinde geliyor; islem_no kısmi ödemelerde tekrar ediyor.'}
OUTPUTS['tekrar-penceresi-onerici'] = 'tekrar-penceresi.md'
GOOD['altin-kayit-kurali'] = {'alanlar': [{'alan': 'ad', 'kural': 'kaynak_onceligi', 'kaynak_sirasi': ['crm', 'web', 'erp'], 'gerekce': 'ERP büyük harfle, web küçük harfle yazıyor; CRM doğru yazımı tutuyor.'}, {'alan': 'telefon', 'kural': 'kaynak_onceligi', 'kaynak_sirasi': ['crm', 'erp', 'web'], 'gerekce': 'Web telefonu boşluksuz giriyor.'}, {'alan': 'eposta', 'kural': 'en_guncel', 'kaynak_sirasi': [], 'gerekce': ''}, {'alan': 'adres', 'kural': 'kaynak_onceligi', 'kaynak_sirasi': ['crm', 'erp', 'web'], 'gerekce': 'ERP adresleri eski.'}]}
OUTPUTS['altin-kayit-kurali'] = 'altin-kayit-kurallari.md'
GOOD['log-ornekleme-onerici'] = {'oranlar': [{'servis': 'siparis-api', 'seviye': 'DEBUG', 'oran': 0}, {'servis': 'siparis-api', 'seviye': 'INFO', 'oran': 0.5}, {'servis': 'siparis-api', 'seviye': 'WARN', 'oran': 1}, {'servis': 'siparis-api', 'seviye': 'ERROR', 'oran': 1}, {'servis': 'odeme-api', 'seviye': 'DEBUG', 'oran': 0}, {'servis': 'odeme-api', 'seviye': 'INFO', 'oran': 1}, {'servis': 'odeme-api', 'seviye': 'WARN', 'oran': 1}, {'servis': 'odeme-api', 'seviye': 'ERROR', 'oran': 1}, {'servis': 'katalog-api', 'seviye': 'DEBUG', 'oran': 0}, {'servis': 'katalog-api', 'seviye': 'INFO', 'oran': 0.3}, {'servis': 'katalog-api', 'seviye': 'WARN', 'oran': 1}, {'servis': 'katalog-api', 'seviye': 'ERROR', 'oran': 1}, {'servis': 'ingress', 'seviye': 'INFO', 'oran': 0.1}, {'servis': 'ingress', 'seviye': 'WARN', 'oran': 1}, {'servis': 'ingress', 'seviye': 'ERROR', 'oran': 1}]}
OUTPUTS['log-ornekleme-onerici'] = 'log-ornekleme.md'
GOOD['model-terfi-karari'] = {'karar': 'bekle', 'aciklama': 'Aday model genel AUC’yi 0.8368’den 0.8838’e çıkarıyor ve güven aralığı [0.0338, 0.0601] sıfırın üstünde. Ancak kurumsal segmentte AUC 0.7961’den 0.5771’e düşüyor; bu segment düzelene kadar terfi beklemeli.'}
OUTPUTS['model-terfi-karari'] = 'model-terfi.md'
GOOD['bos-deger-politikasi'] = {'kolonlar': [{'kolon': 'siparis_no', 'politika': 'zorunlu', 'varsayilan': None, 'gerekce': ''}, {'kolon': 'musteri_no', 'politika': 'zorunlu', 'varsayilan': None, 'gerekce': ''}, {'kolon': 'siparis_tarihi', 'politika': 'zorunlu', 'varsayilan': None, 'gerekce': ''}, {'kolon': 'kanal', 'politika': 'zorunlu', 'varsayilan': None, 'gerekce': ''}, {'kolon': 'kargo_ucreti', 'politika': 'varsayilan', 'varsayilan': '0', 'gerekce': 'Mağaza satışlarında kargo yok; boşlar 0 ile doldurulur.'}, {'kolon': 'para_birimi', 'politika': 'varsayilan', 'varsayilan': 'TRY', 'gerekce': 'Satışların büyük çoğunluğu TL; eksik değer TRY.'}, {'kolon': 'kupon_kodu', 'politika': 'izin', 'varsayilan': None, 'gerekce': 'Kupon isteğe bağlı.'}, {'kolon': 'tutar', 'politika': 'zorunlu', 'varsayilan': None, 'gerekce': ''}, {'kolon': 'durum', 'politika': 'varsayilan', 'varsayilan': 'olusturuldu', 'gerekce': 'Durumu boş siparişler ilk durumda kabul edilir.'}]}
OUTPUTS['bos-deger-politikasi'] = 'bos-deger-politikasi.md'
GOOD['silme-talebi-planlayici'] = {'tablolar': [{'tablo': 'crm.musteri', 'islem': 'anonimlestir', 'gerekce': 'Segment ve kayıt tarihi analizde kullanılıyor.'}, {'tablo': 'crm.iletisim_izni', 'islem': 'sil', 'gerekce': ''}, {'tablo': 'satis.fatura', 'islem': 'sakla', 'gerekce': 'VUK md. 253 ve TTK md. 82 gereği 10 yıl; yalnızca muhasebe erişebilir.'}, {'tablo': 'satis.siparis', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'destek.talep', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'web.oturum', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'dwh.gunluk_satis_ozet', 'islem': 'kapsam_disi', 'gerekce': 'Kişiye bağlanamayan özet.'}, {'tablo': 'muhasebe.yevmiye', 'islem': 'sakla', 'gerekce': 'VUK md. 253: 5 yıl.'}]}
OUTPUTS['silme-talebi-planlayici'] = 'silme-talebi.md'
GOOD['ambar-boyutu-secici'] = {'is_yukleri': [{'is_yuku': 'etl_gece', 'boyut': 'M', 'max_kume': 1, 'otomatik_askiya_sn': 60, 'gerekce': 'Gece penceresi 30 dk p95’e izin veriyor; L gereksiz.'}, {'is_yuku': 'bi_dashboard', 'boyut': 'XL', 'max_kume': 5, 'otomatik_askiya_sn': 60, 'gerekce': '40 eşzamanlı sorgu için 5 küme.'}, {'is_yuku': 'adhoc_analist', 'boyut': 'L', 'max_kume': 2, 'otomatik_askiya_sn': 60, 'gerekce': 'p95 420 sn SLA’yı aşıyor.'}, {'is_yuku': 'ml_ozellik', 'boyut': 'M', 'max_kume': 1, 'otomatik_askiya_sn': 60, 'gerekce': 'XS’te p95 40 dk.'}]}
OUTPUTS['ambar-boyutu-secici'] = 'ambar-boyutu.md'
GOOD['mutabakat-fark-aciklayici'] = {'gruplar': [{'grup': 'tarih', 'neden': 'saat_dilimi', 'aciklama': 'Yeni sistem UTC tutuyor (3 saat geri).'}, {'grup': 'kdv_orani', 'neden': 'birim', 'aciklama': 'Oran yerine yüzde yazılıyor.'}, {'grup': 'tutar', 'neden': 'yuvarlama', 'aciklama': 'Satır toplamları kuruşta yuvarlanıyor.'}, {'grup': 'musteri', 'neden': 'yazim', 'aciklama': 'Büyük harf.'}, {'grup': 'HEDEFTE EKSİK', 'neden': 'filtre', 'aciklama': 'İptal faturalar yeni raporda yok.', 'kosul': {'kolon': 'durum', 'deger': 'iptal'}}]}
OUTPUTS['mutabakat-fark-aciklayici'] = 'mutabakat-farklari.md'
GOOD['dbt-test-siddeti'] = {'testler': [{'test': 'not_null_siparis_musteri_id', 'severity': 'error', 'warn_if': '>0', 'error_if': '>50', 'gerekce': 'Normal günlerde en çok 2 satır; olaylar 95 ve 340 satır.'}, {'test': 'unique_musteri_id', 'severity': 'error', 'warn_if': '>0', 'error_if': '>0', 'gerekce': ''}, {'test': 'relationships_siparis_urun', 'severity': 'error', 'warn_if': '>10', 'error_if': '>100', 'gerekce': 'Geç gelen ürünler yüzünden günde 8 satıra kadar normal.'}, {'test': 'accepted_values_siparis_durum', 'severity': 'warn', 'warn_if': '>0', 'error_if': '>10', 'gerekce': 'Olay geçmişi yok; yalnızca uyarı.'}]}
OUTPUTS['dbt-test-siddeti'] = 'dbt-test-siddeti.md'
GOOD['sensor-ayarlayici'] = {'sensorler': [{'sensor': 'erp_gece.bekle_erp_dosyasi', 'mode': 'reschedule', 'poke_interval_sn': 300, 'timeout_dk': 150, 'gerekce': 'Dosya genelde 40 dk sonra geliyor.'}, {'sensor': 'banka.bekle_ekstre', 'mode': 'poke', 'poke_interval_sn': 60, 'timeout_dk': 20, 'gerekce': ''}, {'sensor': 'pazarlama.bekle_kampanya_api', 'mode': 'reschedule', 'poke_interval_sn': 600, 'timeout_dk': 360, 'gerekce': 'API 5,5 saate kadar gecikebiliyor.'}, {'sensor': 'kur.bekle_tcmb', 'mode': 'poke', 'poke_interval_sn': 30, 'timeout_dk': 10, 'gerekce': ''}]}
OUTPUTS['sensor-ayarlayici'] = 'sensor-ayarlari.md'
GOOD['yasam-dongusu-onerici'] = {'on_ekler': [{'on_ek': 'ham/loglar/', 'ia_gun': None, 'glacier_ir_gun': 30, 'gerekce': 'Loglar ilk aydan sonra neredeyse okunmuyor.'}, {'on_ek': 'ml/egitim-verisi/', 'ia_gun': None, 'glacier_ir_gun': 365, 'gerekce': 'Eğitim verisi bir yıl boyunca sık okunuyor.'}, {'on_ek': 'yedek/veritabani/', 'ia_gun': None, 'glacier_ir_gun': 30, 'gerekce': ''}, {'on_ek': 'rapor/arsiv/', 'ia_gun': 30, 'glacier_ir_gun': 90, 'gerekce': ''}]}
OUTPUTS['yasam-dongusu-onerici'] = 'yasam-dongusu.md'
GOOD['stok-transfer-onerici'] = {'transferler': [{'urun': 'Ç-Kahve-250g', 'kaynak': 'Beşiktaş', 'hedef': 'Kadıköy', 'adet': 80}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Ümraniye', 'hedef': 'Kadıköy', 'adet': 60}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Ümraniye', 'hedef': 'Bakırköy', 'adet': 30}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Ümraniye', 'hedef': 'Ataşehir', 'adet': 40}, {'urun': 'Ç-Kupa-Seti', 'kaynak': 'Ataşehir', 'hedef': 'Kadıköy', 'adet': 90}, {'urun': 'Ç-Kupa-Seti', 'kaynak': 'Ataşehir', 'hedef': 'Beşiktaş', 'adet': 25}, {'urun': 'Ç-Kupa-Seti', 'kaynak': 'Ümraniye', 'hedef': 'Bakırköy', 'adet': 25}]}
OUTPUTS['stok-transfer-onerici'] = 'stok-transferi.md'
GOOD['cdc-yontem-secici'] = {'tablolar': [{'tablo': 'erp.siparis', 'yontem': 'log', 'gerekce': ''}, {'tablo': 'erp.siparis_kalem', 'yontem': 'log', 'gerekce': 'updated_at yok ve satırlar siliniyor.'}, {'tablo': 'erp.musteri', 'yontem': 'log', 'gerekce': 'Silmeler yakalanmalı.'}, {'tablo': 'erp.urun', 'yontem': 'tam', 'gerekce': ''}, {'tablo': 'erp.doviz_kuru', 'yontem': 'tam', 'gerekce': 'PK yok, 9.000 satır.'}, {'tablo': 'erp.stok_hareket', 'yontem': 'sorgu', 'gerekce': 'PK yok; yalnızca ekleme ve updated_at var.'}, {'tablo': 'erp.kampanya', 'yontem': 'tam', 'gerekce': ''}]}
OUTPUTS['cdc-yontem-secici'] = 'cdc-yontemi.md'
BAD31 = {'silme': {'tablolar': [{'tablo': 'crm.musteri', 'islem': 'sil', 'gerekce': ''}, {'tablo': 'crm.iletisim_izni', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'satis.fatura', 'islem': 'sil', 'gerekce': ''}, {'tablo': 'satis.siparis', 'islem': 'sakla', 'gerekce': 'iş ihtiyacı'}, {'tablo': 'destek.talep', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'web.oturum', 'islem': 'anonimlestir', 'gerekce': ''}, {'tablo': 'dwh.gunluk_satis_ozet', 'islem': 'sil', 'gerekce': ''}, {'tablo': 'muhasebe.yevmiye', 'islem': 'sakla', 'gerekce': 'VUK'}]}, 'ambar': {'is_yukleri': [{'is_yuku': 'etl_gece', 'boyut': 'XL', 'max_kume': 1, 'otomatik_askiya_sn': 60, 'gerekce': ''}, {'is_yuku': 'bi_dashboard', 'boyut': 'L', 'max_kume': 5, 'otomatik_askiya_sn': 60, 'gerekce': ''}, {'is_yuku': 'adhoc_analist', 'boyut': 'M', 'max_kume': 1, 'otomatik_askiya_sn': 60, 'gerekce': ''}, {'is_yuku': 'ml_ozellik', 'boyut': 'XS', 'max_kume': 1, 'otomatik_askiya_sn': 3600, 'gerekce': ''}]}, 'mutabakat': {'gruplar': [{'grup': 'tarih', 'neden': 'saat_dilimi', 'aciklama': ''}, {'grup': 'kdv_orani', 'neden': 'birim', 'aciklama': ''}, {'grup': 'tutar', 'neden': 'gercek_hata', 'aciklama': ''}, {'grup': 'musteri', 'neden': 'yuvarlama', 'aciklama': ''}, {'grup': 'HEDEFTE EKSİK', 'neden': 'filtre', 'aciklama': '', 'kosul': {'kolon': 'musteri', 'deger': 'Deniz Lojistik'}}]}, 'dbt': {'testler': [{'test': 'not_null_siparis_musteri_id', 'severity': 'error', 'warn_if': '>0', 'error_if': '>100', 'gerekce': ''}, {'test': 'unique_musteri_id', 'severity': 'error', 'warn_if': '>0', 'error_if': '>0', 'gerekce': ''}, {'test': 'relationships_siparis_urun', 'severity': 'error', 'warn_if': '>0', 'error_if': '>5', 'gerekce': ''}, {'test': 'accepted_values_siparis_durum', 'severity': 'error', 'warn_if': '>5', 'error_if': '>0', 'gerekce': ''}]}, 'sensor': {'sensorler': [{'sensor': 'erp_gece.bekle_erp_dosyasi', 'mode': 'poke', 'poke_interval_sn': 60, 'timeout_dk': 60, 'gerekce': ''}, {'sensor': 'banka.bekle_ekstre', 'mode': 'poke', 'poke_interval_sn': 10, 'timeout_dk': 600, 'gerekce': ''}, {'sensor': 'pazarlama.bekle_kampanya_api', 'mode': 'reschedule', 'poke_interval_sn': 600, 'timeout_dk': 120, 'gerekce': ''}, {'sensor': 'kur.bekle_tcmb', 'mode': 'poke', 'poke_interval_sn': 30, 'timeout_dk': 10, 'gerekce': ''}]}, 'yasam': {'on_ekler': [{'on_ek': 'ham/loglar/', 'ia_gun': 45, 'glacier_ir_gun': None, 'gerekce': ''}, {'on_ek': 'ml/egitim-verisi/', 'ia_gun': 30, 'glacier_ir_gun': 90, 'gerekce': ''}, {'on_ek': 'yedek/veritabani/', 'ia_gun': 90, 'glacier_ir_gun': 30, 'gerekce': ''}, {'on_ek': 'rapor/arsiv/', 'ia_gun': 30, 'glacier_ir_gun': 90, 'gerekce': ''}]}, 'stok': {'transferler': [{'urun': 'Ç-Kahve-250g', 'kaynak': 'Ataşehir', 'hedef': 'Kadıköy', 'adet': 100}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Beşiktaş', 'hedef': 'Ümraniye', 'adet': 10}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Ümraniye', 'hedef': 'Bakırköy', 'adet': 30}, {'urun': 'Ç-Termos-500ml', 'kaynak': 'Ümraniye', 'hedef': 'Ataşehir', 'adet': 40}]}, 'cdc': {'tablolar': [{'tablo': 'erp.siparis', 'yontem': 'log', 'gerekce': ''}, {'tablo': 'erp.siparis_kalem', 'yontem': 'sorgu', 'gerekce': ''}, {'tablo': 'erp.musteri', 'yontem': 'tam', 'gerekce': ''}, {'tablo': 'erp.urun', 'yontem': 'tam', 'gerekce': ''}, {'tablo': 'erp.doviz_kuru', 'yontem': 'log', 'gerekce': ''}, {'tablo': 'erp.stok_hareket', 'yontem': 'log', 'gerekce': ''}, {'tablo': 'erp.kampanya', 'yontem': 'tam', 'gerekce': ''}]}}
GOOD['tableau-hesap-yazari'] = {'alanlar': [{'ad': 'Son Tarih', 'formul': '{FIXED : MAX([Siparis Tarihi])}', 'tip': 'date', 'rol': 'dimension', 'aciklama': 'Verideki son sipariş tarihi.'}, {'ad': 'Ciro YTD', 'formul': "SUM(IF [Siparis Tarihi] >= DATETRUNC('year', [Son Tarih]) AND [Siparis Tarihi] <= [Son Tarih] THEN [Tutar] END)", 'tip': 'real', 'rol': 'measure', 'aciklama': 'Yıl başından son tarihe.'}, {'ad': 'Ciro GY YTD', 'formul': "SUM(IF [Siparis Tarihi] >= DATEADD('year', -1, DATETRUNC('year', [Son Tarih])) AND [Siparis Tarihi] <= DATEADD('year', -1, [Son Tarih]) THEN [Tutar] END)", 'tip': 'real', 'rol': 'measure', 'aciklama': 'Geçen yılın aynı dönemi.'}, {'ad': 'Sipariş Başına Ciro', 'formul': 'SUM([Tutar]) / COUNTD([Siparis No])', 'tip': 'real', 'rol': 'measure', 'aciklama': ''}, {'ad': 'Bölge Payı', 'formul': 'SUM({FIXED [Bolge] : SUM([Tutar])}) / SUM({FIXED : SUM([Tutar])})', 'tip': 'real', 'rol': 'measure', 'aciklama': ''}], 'varsayimlar': ['Ciro, Tutar alanının toplamıdır.']}
OUTPUTS['tableau-hesap-yazari'] = 'hesaplanan-alanlar.md'
OUTPUTS.update({'deney-analisti': 'deney-raporu.md', 'sube-planlayici': 'sube-plani.md', 'rapor-tasarimcisi': 'tasarim.md'})
OUTPUTS.update({'veri-sozlesmesi-yazari': 'veri_sozlesmesi.json', 'butce-sapma-yorumu': 'butce-yorumu.md', 'hesap-plani-esleyici': 'hesap-eslemesi.csv',
                'json-esleme-yazari': 'duz_tablo.csv', 'bolumleme-danismani': 'bolumleme.md'})
KPI_ARGS = ('--data', 'ornek/satislar.csv')


class Agents(LocalTestCase):
    def setUp(self):
        self.model = FakeModel()
        self.addCleanup(self.model.close)

    def run_agent(self, item_id, *args, provider='openai', answers=(), stdin=None):
        p = self.package(item_id)
        self.model.answers = list(answers)
        extra = KPI_ARGS if item_id == 'kpi-tanimlayici' else ()
        return p, p.run(*extra, *args, env=self.model.env(provider), stdin=stdin)

    def test_every_agent_end_to_end_with_valid_answer(self):
        for item_id, answer in GOOD.items():
            with self.subTest(item_id):
                self.model.requests.clear()
                p, done = self.run_agent(item_id, '--yes', answers=[answer])
                self.ok(done)
                self.assertTrue(p.out(OUTPUTS[item_id]).is_file())
                self.assertEqual(len(self.model.requests), 1)
                sent = json.dumps(self.model.requests[0]['body'], ensure_ascii=False)
                self.assertNotIn('@ornek.test', sent)  # personal data never leaves unmasked
                self.assertEqual(json.loads(p.out('gonderilecek-istek.json').read_text(encoding='utf-8'))['model'], 'test-model')

    def test_agent_outputs_are_correct(self):
        p, done = self.run_agent('dax-yazari', '--yes', answers=[GOOD['dax-yazari']])
        self.ok(done)
        dax = p.out('olculer.dax').read_text(encoding='utf-8')
        self.assertIn("MEASURE 'Satislar'[Kâr Marjı] =", dax)
        self.assertIn('"Müşteri Sayısı", [Müşteri Sayısı]', dax)
        p, done = self.run_agent('kalite-kurali', '--yes', answers=[GOOD['kalite-kurali']])
        self.ok(done)
        self.assertEqual(len(p.json('rules.json')['rules']), 2)  # duplicate rule removed
        self.assertEqual([r['failed'] for r in p.json('sonuc.json')['results']], [3, 1])
        p, done = self.run_agent('veri-sozlugu', '--yes', answers=[GOOD['veri-sozlugu']])
        rows = {r['sutun']: r for r in p.csv('veri-sozlugu.csv')}
        self.assertEqual(rows['musteri_eposta']['hassasiyet'], 'kişisel')  # model said "genel"; code overrides
        p, done = self.run_agent('kpi-tanimlayici', '--yes', answers=[GOOD['kpi-tanimlayici']])
        self.assertIn('1 KPI veride olmayan alan', done.stdout)
        sent = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('[TELEFON]', sent)
        self.assertNotIn('0532', sent)
        p, done = self.run_agent('qlik-powerbi', '--yes', answers=[GOOD['qlik-powerbi']])
        self.assertTrue((p.dir / 'output' / 'power-query' / 'Hedefler.m').is_file())

    def test_new_agents_reject_invented_tables_and_numbers(self):
        invented = {**GOOD['sql-aciklayici'], 'genel_ozet': 'Sonuç `mart.olmayan_tablo` tablosuna yazılır.'}
        p, done = self.run_agent('sql-aciklayici', '--yes', answers=[invented, GOOD['sql-aciklayici']])
        self.ok(done)
        retry = self.model.requests[1]['body']['messages'][-1]['content']
        self.assertIn('mart.olmayan_tablo', retry)
        md = p.out('sql-aciklama.md').read_text(encoding='utf-8')
        self.assertIn('WHERE olmadan UPDATE', md)  # risks come from code, not from the model
        self.assertIn('flowchart LR', md)
        self.model.requests.clear()
        p, done = self.run_agent('donem-yorumu', '--yes', answers=[{**GOOD['donem-yorumu'], 'ozet': 'Ciro 250.000 TL azaldı.'}, GOOD['donem-yorumu']])
        self.ok(done)
        self.assertIn('250.000', self.model.requests[-1]['body']['messages'][-1]['content'])
        sent = self.model.requests[0]['body']['messages'][-1]['content']
        self.assertIn('F1: Toplam ciro 2026-09: 119.487,60', sent)
        self.assertNotIn('01.09.2026', sent)  # rows never leave, only computed facts
        self.assertEqual(p.csv('olgular.csv')[1]['grup'], 'bolge')

    def test_third_batch_agents_validate_locally(self):
        p, done = self.run_agent('kvkk-envanteri', '--yes', answers=[{'kayitlar': [{**k, 'hukuki_sebep': ['5/2-z']} if k['sutun'] == 'tckn' else k
                                                                                  for k in GOOD['kvkk-envanteri']['kayitlar']]}, GOOD['kvkk-envanteri']])
        self.ok(done)
        self.assertIn('"tckn" hukuki sebep kodu geçersiz', self.model.requests[1]['body']['messages'][-1]['content'])
        sent = self.model.requests[0]['body']['messages'][-1]['content']
        self.assertIn('notlar: serbest metin, 2 satırda', sent)
        self.assertNotIn('10000791952', sent)  # only column names and kinds leave, never values
        rows = {r['sutun']: r for r in p.csv('kvkk-envanteri.csv')}
        self.assertEqual(rows['tckn']['hukuki_sebep'], '5/2-ç hukuki yükümlülük')
        self.assertIn('md. 9', rows['iban']['kod_notu'])
        p, done = self.run_agent('olay-analisti', '--yes', answers=[GOOD['olay-analisti']])
        self.ok(done)
        rows = p.csv('olay-imzalari.csv')
        self.assertEqual((rows[0]['id'], rows[0]['oncelik'], rows[0]['seviye']), ('S4', 'P1', 'FATAL'))  # model said P2; FATAL forces P1
        self.assertNotIn('ayse@ornek.test', json.dumps(self.model.requests[-1]['body'], ensure_ascii=False))
        p, done = self.run_agent('boyutsal-model', '--yes', answers=[GOOD['boyutsal-model']])
        self.ok(done)
        checks = {c['boyut']: c for c in p.json('model.json')['bagimlilik_kontrolu']}
        self.assertEqual((checks['dim_musteri']['celisen_anahtar'], checks['dim_urun']['celisen_anahtar']), (10, 0))
        self.assertIn('siparis_tarihi_key INT NOT NULL REFERENCES dwh.dim_tarih,', p.out('model.sql').read_text(encoding='utf-8'))
        self.assertIn('- urun_kodu → urun_adi', self.model.requests[-1]['body']['messages'][-1]['content'])  # measured dependencies reach the model
        self.assertEqual({r['sonuc'] for r in p.csv('yukleme/dogrulama.csv')}, {'tamam'})  # the design's load SQL ran in SQLite
        self.assertIn('CREATE TABLE fct_satis', p.out('yukleme/ddl.sql').read_text(encoding='utf-8'))
        p, done = self.run_agent('regex-yazari', '--yes', answers=[{'desen': r'^(\d+ ?)+[A-Z]+$'}, GOOD['regex-yazari']])
        self.ok(done)
        self.assertIn('İç içe niceleyici', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertEqual(p.json('rules.json')['rules'][0]['check'], 'regex')
        p, done = self.run_agent('migrasyon-esleme', '--yes', answers=[GOOD['migrasyon-esleme']])
        self.ok(done)
        sql = p.out('yukleme.sql').read_text(encoding='utf-8')
        self.assertIn('Tutar,  -- UYARI: tip daralması DECIMAL(18,2) → DECIMAL(12,2)', sql)
        self.assertEqual(p.json('esleme.json')['tasinmayan_sutunlar'], ['dbo.Siparisler.Aciklama'])

    def test_fourth_batch_agents_validate_locally(self):
        dbt = GOOD['dbt-model-yazari']
        raw = {**dbt, 'sql': dbt['sql'].replace("{{ source('erp', 'siparis') }}", 'erp.siparis')}
        p, done = self.run_agent('dbt-model-yazari', '--yes', answers=[raw, dbt])
        self.ok(done)
        self.assertIn('ham tablo adı kaldı: erp.siparis', self.model.requests[1]['body']['messages'][-1]['content'])
        self.assertIn("WHERE s.durum <> 'IPTAL'", self.model.requests[0]['body']['messages'][-1]['content'])  # literals survive comment stripping
        model_sql = (p.dir / 'output' / 'models' / 'fct_segment_siparis.sql').read_text(encoding='utf-8')
        self.assertTrue(model_sql.startswith("{{ config(materialized='view') }}"))
        yml = (p.dir / 'output' / 'models' / 'schema.yml').read_text(encoding='utf-8')
        self.assertIn('        # data_tests:', yml)  # placeholder-only tests never leave a null key
        plan = GOOD['zamanlama-planlayici']['plan']
        clash = {'plan': [{**x, 'baslangic': '01:00'} if x['is'] == 'web_cek' else x for x in plan]}
        p, done = self.run_agent('zamanlama-planlayici', '--yes', answers=[clash, GOOD['zamanlama-planlayici']])
        self.ok(done)
        self.assertIn('3 iş aynı anda çalışıyor', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertEqual(p.json('plan.json')['bitis'], '03:35')
        p, done = self.run_agent('tasarruf-plani', '--yes', answers=[GOOD['tasarruf-plani']])
        self.ok(done)
        self.assertEqual(p.json('tasarruf-plani.json')['toplam_tasarruf'], 2100)
        self.assertNotIn('2026-09-15', self.model.requests[-1]['body']['messages'][-1]['content'])  # only totals leave, never rows
        p, done = self.run_agent('model-karti', '--yes', answers=[{**GOOD['model-karti'], 'bulgular': [{'metin': 'AUC 0,95.', 'kanit': ['F2']}]}, GOOD['model-karti']])
        self.ok(done)
        self.assertIn('0,95', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertIn('F2: ROC AUC 0,905', self.model.requests[-2]['body']['messages'][-1]['content'])
        wrong = {'kararlar': [{**k, 'karar': 'aynı'} if k['id'] == 'C4' else k for k in GOOD['eslestirme-hakemi']['kararlar']]}
        p, done = self.run_agent('eslestirme-hakemi', '--yes', answers=[wrong])
        self.ok(done)
        sent = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertNotIn('1234567802', sent)  # identifiers are compared locally, never sent
        rows = {r['id']: r for r in p.csv('eslesme-kararlari.csv')}
        self.assertIn('insan incelemesi', rows['C4']['kod_notu'])
        self.assertEqual([r['kayit'] for r in p.csv('gruplar.csv') if r['grup'] == 'G2'], ['K04', 'K05'])

    def test_fifth_batch_agents_validate_locally(self):
        bad_idx = {**GOOD['sql-optimizasyon'], 'indeksler': ['CREATE INDEX ix_x ON siparisler (kargo_firmasi);']}
        p, done = self.run_agent('sql-optimizasyon', '--yes', answers=[bad_idx, GOOD['sql-optimizasyon']])
        self.ok(done)
        self.assertIn('sorguda geçmeyen sütun içeriyor: kargo_firmasi', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertIn('filtreyle atılan: 3916000', self.model.requests[-2]['body']['messages'][-1]['content'])
        self.assertIn('INCLUDE (siparis_id, tarih, musteri_id);', p.out('indeksler.sql').read_text(encoding='utf-8'))
        narrow = json.loads(json.dumps(GOOD['avro-sema-tasarimci']))
        narrow['sema']['fields'][5]['type'] = 'double'
        p, done = self.run_agent('avro-sema-tasarimci', '--yes', answers=[narrow, GOOD['avro-sema-tasarimci']])
        self.ok(done)
        self.assertIn('4/200 örnek olay şemaya uymuyor', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertEqual(p.json('sema.avsc')['fields'][4]['type']['fields'][1]['default'], None)
        p, done = self.run_agent('rag-test-seti', '--yes', answers=[GOOD['rag-test-seti']])
        self.ok(done)
        rows = p.csv('test_seti.csv')
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['beklenen'], 'iade_politikasi.md#1')  # ready for rag-geri-getirme-degerlendirme
        p, done = self.run_agent('kalite-kok-neden', '--yes', answers=[GOOD['kalite-kok-neden']])
        self.ok(done)
        sent = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('F2: Kural 2 hatalarının %83,3’i (5 satır) kaynak = cagri_merkezi', sent)
        self.assertNotIn('S033', sent)  # rows never leave
        p, done = self.run_agent('ab-test-karari', '--yes', answers=[GOOD['ab-test-karari']])
        self.ok(done)
        self.assertFalse(p.json('ab-karar.json')['anlamli'])
        self.assertIn('F4: İki oran z-testi p-değeri 0,6677', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_sixth_batch_agents_validate_locally(self):
        good = GOOD['veri-sozlesmesi-yazari']
        p, done = self.run_agent('veri-sozlesmesi-yazari', '--yes', answers=[{**good, 'bilinen_ihlaller': good['bilinen_ihlaller'][:3]}, good])
        self.ok(done)
        self.assertIn('tutar / aralık [0, ]: 1 satır', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertEqual(len(p.json('veri_sozlesmesi.json')['columns']), 7)
        p, done = self.run_agent('butce-sapma-yorumu', '--yes', answers=[GOOD['butce-sapma-yorumu']])
        self.ok(done)
        self.assertIn('F5: 760 / Merkez (Pazarlama Giderleri): gider; YTD bütçe 637.500,00', self.model.requests[-1]['body']['messages'][-1]['content'])
        wrong = {'eslemeler': [{**i, 'tdhp': '320'} if i['yerel_kod'] == '1910' else i for i in GOOD['hesap-plani-esleyici']['eslemeler']]}
        p, done = self.run_agent('hesap-plani-esleyici', '--yes', answers=[wrong, GOOD['hesap-plani-esleyici']])
        self.ok(done)
        self.assertIn('indirilecek/devreden KDV 190 veya 191 olmalı', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = {r['kod']: r for r in p.csv('hesap-eslemesi.csv')}
        self.assertEqual((rows['1910']['tdhp'], rows['9999']['guven']), ('191', 'eşlenemedi'))
        p, done = self.run_agent('json-esleme-yazari', '--yes', answers=[GOOD['json-esleme-yazari']])
        self.ok(done)
        rows = p.csv('duz_tablo.csv')
        self.assertEqual(len(rows), 8)  # 5 orders exploded into 8 order lines
        self.assertEqual((rows[0]['urun_kodu'], rows[0]['iptal'], rows[-1]['musteri_sehir']), ('P-300', 'false', ''))
        p, done = self.run_agent('bolumleme-danismani', '--yes', '--size-gb', '20', answers=[{**GOOD['bolumleme-danismani'], 'ayrinti': 'gün'}, GOOD['bolumleme-danismani']])
        self.ok(done)
        self.assertIn('küçük dosya sorunu', self.model.requests[-1]['body']['messages'][-1]['content'])  # 20 GB / 37 days < 1 GB per partition
        self.assertEqual(p.json('bolumleme.json')['partitions'], 9)
        self.assertIn('PARTITIONED BY (siparis_tarihi_ay)', p.out('ddl.sql').read_text(encoding='utf-8'))

    def test_seventh_batch_agents_validate_locally(self):
        p, done = self.run_agent('deney-analisti', '--yes', answers=[{**GOOD['deney-analisti'], 'test': 'welch-t'}, GOOD['deney-analisti']])
        self.ok(done)
        self.assertIn('t-testi seçilemez', self.model.requests[-1]['body']['messages'][-1]['content'])  # group A fails Jarque-Bera (p 0,0364)
        self.assertIn('F4: Mann-Whitney U (A vs B): U=1.049,5, p-değeri 0,0166', self.model.requests[-2]['body']['messages'][-1]['content'])
        self.assertEqual(p.json('deney-raporu.json')['test'], 'mann-whitney')
        p, done = self.run_agent('sube-planlayici', '--yes', answers=[{**GOOD['sube-planlayici'], 'ozet': 'Kapsama %90 olur.'}, GOOD['sube-planlayici']])
        self.ok(done)
        self.assertIn('Olgularda olmayan sayı: 90', self.model.requests[-1]['body']['messages'][-1]['content'])
        self.assertNotIn('40.19', self.model.requests[-2]['body']['messages'][-1]['content'])  # coordinates never leave the machine
        self.assertEqual(p.json('sube-plani.json')['etki'], {'kapsanan_once': 63, 'kapsanan_sonra': 93, 'kapsama_once_%': 60.0, 'kapsama_sonra_%': 88.57})
        bad = {**GOOD['rapor-tasarimcisi'], 'olculer': [{'sutun': 'siparis_no', 'toplama': 'toplam'}], 'boyutlar': ['musteri_eposta']}
        p, done = self.run_agent('rapor-tasarimcisi', '--yes', answers=[bad, GOOD['rapor-tasarimcisi']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('musteri_eposta kişisel veri', retry)
        self.assertIn('siparis_no sayısal değil', retry)
        self.assertNotIn('S00001', self.model.requests[-2]['body']['messages'][-1]['content'])  # only the column profile is sent
        self.assertTrue((p.out('qlik-uygulama') / 'objects' / 'rapor-sayfasi.json').is_file())
        self.assertTrue(list(p.out('pbip').glob('*.pbip')))
        self.assertEqual(p.json('tasarim.json')['kpi'][0], {'ad': 'Toplam Tutar', 'deger': 5912910.0})

    def test_qlik_expression_agent_checks_fields_functions_and_variables(self):
        bad = {'ifadeler': [{'ad': 'Ciro', 'ifade': 'Summ([Ciro])'}, {'ad': 'Bugün', 'ifade': 'Sum({<[Tarih]={"$(vYok)"}>} [Tutar])'}, {'ad': 'Açık', 'ifade': 'Sum([Tutar]'}]}
        p, done = self.run_agent('qlik-ifade-yazari', '--yes', answers=[bad, GOOD['qlik-ifade-yazari']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for message in ('script’te olmayan alan [Ciro]', 'Qlik fonksiyonu değil: Summ()', 'tanımsız değişken $(vYok)', 'parantez veya tırnak kapanmamış'):
            self.assertIn(message, retry)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('[SiparisNo]', sent)
        self.assertNotIn('SatisDB.dbo', sent)  # only field and variable names leave the machine
        self.assertEqual([m['qMeasure']['qLabel'] for m in p.json('measures.json')][:2], ['Ciro YTD', 'Ciro GY YTD'])

    def test_tableau_calculation_agent_checks_fields_and_aggregation(self):
        bad = {'alanlar': [{'ad': 'Ciro', 'formul': 'SUMM([Ciro])', 'tip': 'real', 'rol': 'measure'}, {'ad': 'Oran', 'formul': 'SUM([Tutar]) / [Adet]', 'tip': 'real', 'rol': 'measure'},
                           {'ad': 'Bayrak', 'formul': 'IF [Tutar] > 5 THEN 1', 'tip': 'integer', 'rol': 'dimension'}]}
        p, done = self.run_agent('tableau-hesap-yazari', '--yes', answers=[bad, GOOD['tableau-hesap-yazari']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for message in ('veri kaynağında olmayan alan [Ciro]', 'Tableau fonksiyonu değil: SUMM()', 'toplanmış ve toplanmamış ifade karışık: [Adet]', 'IF/CASE ile END sayısı eşleşmiyor'):
            self.assertIn(message, retry)
        self.assertIn('- [Siparis Tarihi] (date, dimension)', self.model.requests[-2]['body']['messages'][-1]['content'])
        twb = p.out('hesapli.twb').read_text(encoding='utf-8')
        import xml.etree.ElementTree as ET
        calcs = {c.get('caption'): c.find('calculation').get('formula') for c in ET.fromstring(twb.encode('utf-8')).iter('column') if c.find('calculation') is not None}
        self.assertEqual(calcs['Sipariş Başına Ciro'], 'SUM([tutar]) / COUNTD([siparis_no])')  # captions turned into internal names
        self.assertIn('[Calculation_001_agent]', calcs['Ciro YTD'])  # a calculation can use an earlier one

    def test_unit_code_agent_feeds_the_e_documents(self):
        bad = {'eslemeler': [{'birim': 'Kasa', 'kod': 'KASA'}, {'birim': 'AD', 'kod': 'AD'}]}
        p, done = self.run_agent('birim-kodu-eslestirici', '--yes', answers=[bad, GOOD['birim-kodu-eslestirici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('“Kasa”: KASA GİB birim kod listesinde yok', retry)
        self.assertIn('“AD” kuralla C62 olarak eşlendi', retry)  # “AD” is also the UN/ECE code for byte
        self.assertIn('cevapsız birim: Kasa, Damacana, Bidon, Varil, Sandık, Muhtelif', retry)
        mapping = {r['birim']: r['kod'] for r in p.csv('birim_esleme.csv')}
        self.assertEqual((mapping['KG'], mapping['Sandık'], mapping['Varil']), ('KGM', 'CS', 'DR'))
        self.assertNotIn('Muhtelif', mapping)  # left unmapped with a reason
        irs = self.package('eirsaliye-uret')
        source = (irs.dir / 'ornek' / 'irsaliye_satirlari.csv').read_text(encoding='utf-8').replace(';5;KOLİ', ';4;Sandık')
        (irs.dir / 'sevk.csv').write_text(source, encoding='utf-8')
        self.ok(irs.run('--input', str(irs.dir / 'sevk.csv'), '--unit-map', str(p.out('birim_esleme.csv'))))
        self.assertIn('unitCode="CS">4<', irs.out('eirsaliye/IRS2026000000001.xml').read_text(encoding='utf-8'))

    def test_withholding_agent_feeds_the_invoice_generator(self):
        bad = {'oneriler': [{'urun': 'Bina temizlik hizmeti - Eylül', 'kod': '612', 'oran': '5/10', 'gerekce': 'x'},
                            {'urun': 'Forklift periyodik bakım ve onarımı', 'kod': '650', 'oran': '50', 'gerekce': 'x'}]}
        p, done = self.run_agent('tevkifat-kodu-onerici', '--yes', answers=[bad, GOOD['tevkifat-kodu-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('1234567890', sent)  # only descriptions leave the machine
        self.assertNotIn('Özel güvenlik hizmeti - Eylül', sent)  # the user's own code is kept, not re-asked
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('612 için oran 5/10 olamaz (geçerli: 7/10, 9/10)', retry)
        self.assertIn('650 GİB tevkifat kod listesinde yok', retry)
        rows = p.csv('fatura_satirlari_tevkifatli.csv')
        self.assertEqual([(r['tevkifat_kodu'], r['tevkifat_orani']) for r in rows],
                         [('612', '9/10'), ('', ''), ('603', '7/10'), ('614', '5/10'), ('604', '5/10'), ('607', '9/10')])
        inv = self.package('efatura-uret')
        self.ok(inv.run('--input', str(p.out('fatura_satirlari_tevkifatli.csv'))))
        heads = inv.csv('sonuc.csv')
        self.assertEqual({h['tip'] for h in heads}, {'TEVKIFAT'})
        self.assertEqual([float(h['tevkifat']) for h in heads], [8640.0, 8860.0, 18345.0])  # 9600×0.9; 3800×0.7 + 12400×0.5; 6090×0.5 + 17000×0.9

    def test_log_parser_checks_patterns_on_the_whole_file(self):
        bad = {'desenler': [{'ad': 'uygulama', 'desen': r'(?P<zaman>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (?P<seviye>[A-Z]+) \[(?P<servis>[^\]]+)\] (?P<mesaj>.*)'}]}
        p, done = self.run_agent('log-ayristirici', '--yes', answers=[bad, GOOD['log-ayristirici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('desenler satırların %47.9’ini ayrıştırdı (gereken %98)', retry)  # only the app log format: 34 of 71 records
        self.assertIn('[14/Oct/2026:', retry)  # unmatched nginx lines are shown
        rows = p.csv('log_tablosu.csv')
        self.assertEqual(len(rows), 70)
        self.assertEqual(sum(r['desen'] == 'nginx_erisim' for r in rows), 36)
        err = next(r for r in rows if r['seviye'] == 'ERROR')
        self.assertEqual(err['devam_satiri'], '3')
        self.assertIn('SocketTimeoutException', err['mesaj'])  # stack trace joined to its record
        self.assertEqual(rows[2]['zaman'], '2026-10-14 09:01:09+03:00')  # nginx time read with its offset
        self.assertEqual(p.out('eslesmeyen.txt').read_text(encoding='utf-8').strip(), '--- günlük dosyası döndürüldü ---')
        self.assertIn('(?<zaman>', p.out('fluent-bit-parsers.conf').read_text(encoding='utf-8'))

    def test_survey_coder_masks_and_counts_locally(self):
        small = {**GOOD['anket-kodlayici'], 'temalar': GOOD['anket-kodlayici']['temalar'] + [{'kod': 'T7', 'ad': 'Diğer', 'tanim': 'Konu dışı'}],
                 'atamalar': [{**a, 'temalar': ['T7']} if a['id'] == '25' else a for a in GOOD['anket-kodlayici']['atamalar']]}
        p, done = self.run_agent('anket-kodlayici', '--yes', answers=[small, GOOD['anket-kodlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('ayse.k@ornek.com', sent)
        self.assertIn('[E-POSTA]', sent)
        self.assertNotIn('mobil', sent)  # the segment column stays local
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('2 yanıttan az atanan tema: T7 (1)', retry)
        summary = {r['kod']: r for r in p.csv('tema_ozeti.csv')}
        self.assertEqual((summary['T5']['yanit'], summary['T5']['oran']), ('8', '28.6'))  # 8 of 28
        self.assertNotEqual(summary['T6']['ornek_1'], 'asdfgh')  # quotes prefer real sentences
        coded = {r['yanit_id']: r for r in p.csv('kodlanmis_yanitlar.csv')}
        self.assertEqual(coded['9']['temalar'], 'T1;T2')
        self.assertIn('| Mobil uygulama sorunları | 0 | 89 | 0 |', p.out('anket-temalari.md').read_text(encoding='utf-8'))  # 8 of 9 mobile answers

    def test_alarm_rules_are_backtested_and_shipped_as_a_script(self):
        bad = {'kurallar': [{'metrik': 'satis_satir', 'tip': 'sabit_alt', 'esik': 800, 'gerekce': 'x'},
                            {'metrik': 'yukleme_suresi_dk', 'tip': 'sabit_ust', 'esik': 20, 'gerekce': 'x'},
                            {'metrik': 'musteri_bos_orani', 'tip': 'sabit_ust', 'esik': 5, 'gerekce': 'x'}]}
        p, done = self.run_agent('alarm-esigi-onerici', '--yes', answers=[bad, GOOD['alarm-esigi-onerici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('kaçırılan olaylar: satis_satir 2026-09-16 değer=2100, satis_satir 2026-10-04 değer=900', retry)
        self.assertIn('yukleme_suresi_dk: 45 yanlış alarm (en fazla 2)', retry)
        rows = p.csv('geriye_donuk_test.csv')
        self.assertEqual(sorted((r['metrik'], r['tarih']) for r in rows if r['alarm']),
                         sorted((r['metrik'], r['tarih']) for r in rows if r['olay'] == '1'))  # every incident, nothing else
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        script = subprocess.run([sys.executable, str(p.out('alarm_kontrol.py')), str(p.dir / 'ornek' / 'metrik_gecmisi.csv'), str(p.out('alarm_kurallari.json'))],
                                capture_output=True, text=True, encoding='utf-8', env=env)
        self.assertEqual(script.returncode, 0, script.stderr)  # no alarm on the last day
        self.assertIn('5 alarm', script.stdout)
        late = (p.dir / 'ornek' / 'metrik_gecmisi.csv').read_text(encoding='utf-8') + '2026-10-12;satis_satir;3000;\n'
        (p.dir / 'yeni.csv').write_text(late, encoding='utf-8')
        script = subprocess.run([sys.executable, str(p.out('alarm_kontrol.py')), str(p.dir / 'yeni.csv'), str(p.out('alarm_kurallari.json'))],
                                capture_output=True, text=True, encoding='utf-8', env=env)
        self.assertEqual(script.returncode, 2)  # a Monday with a quarter of the usual rows
        self.assertIn('SON GÜN ALARM 2026-10-12 satis_satir=3000', script.stdout)

    def test_column_standard_names_and_verified_sql(self):
        import sqlite3
        names = {'SIP_TRH': 'siparis_trh', 'aktif?': 'aktif', 'Şehir': 'şehir', 'user': 'musteri_adi'}
        bad = {'eslemeler': [{**e, 'yeni_ad': names.get(e['kolon'], e['yeni_ad'])} for e in GOOD['kolon-adi-standardi']['eslemeler'] if e['kolon'] != 'TEL']}
        p, done = self.run_agent('kolon-adi-standardi', '--yes', answers=[bad, GOOD['kolon-adi-standardi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('SIPARIS.Tutar (TL): decimal(18,2)', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('SIPARIS.SIP_TRH → “siparis_trh”: tarih/zaman kolonu', 'MUSTERI.aktif? → “aktif”: mantıksal kolon _mi / _mu ile bitmeli',
                     'MUSTERI.Şehir → “şehir”: küçük harf ASCII', 'eşlenmemiş kolon: MUSTERI.TEL', 'MUSTERI: MusteriAdi, user → musteri_adi'):
            self.assertIn(text, retry)
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE "SIPARIS" ("SIP_NO", "MUST_KOD", "SIP_TRH", "TeslimTarihi", "Tutar (TL)", "KDV", "iptal", "order", "durum")')
        con.execute('CREATE TABLE "MUSTERI" ("MusteriKodu", "MusteriAdi", "Şehir", "aktif?", "KAYIT_ZAMANI", "user", "TEL")')
        con.executescript(p.out('yeniden_adlandir.sql').read_text(encoding='utf-8'))  # the PostgreSQL script also runs on SQLite
        self.assertEqual([r[1] for r in con.execute('PRAGMA table_info("SIPARIS")')],
                         ['siparis_no', 'musteri_kodu', 'siparis_tarihi', 'teslim_tarihi', 'tutar_tl', 'kdv_tutari_tl', 'iptal_mi', 'sira_no', 'durum'])
        self.assertIn('CREATE VIEW "stg_musteri"', p.out('gorunumler.sql').read_text(encoding='utf-8'))
        ss, done = self.run_agent('kolon-adi-standardi', '--yes', '--dialect', 'sqlserver', answers=[GOOD['kolon-adi-standardi']])
        self.ok(done)
        self.assertIn("EXEC sp_rename N'SIPARIS.[Tutar (TL)]', N'tutar_tl', N'COLUMN';", ss.out('yeniden_adlandir.sql').read_text(encoding='utf-8'))

    def test_address_agent_only_sends_what_the_rules_cannot_parse(self):
        def a(i, m, c, no, d, ilce, il, pk):
            return {'id': i, 'mahalle': m, 'cadde_sokak': c, 'no': no, 'kat': '', 'daire': d, 'ilce': ilce, 'il': il, 'posta_kodu': pk}
        bad = {'adresler': [a('3', 'Fenerbahçe Mahallesi', 'Moda Caddesi', '18', '4', 'Kadıköy', 'İstanbul', '34710'),
                            a('6', 'Odunluk Mahallesi', 'Akpınar Caddesi', '9', '', 'Nilüfer', 'İstanbul', '16110'),
                            a('9', 'Esentepe Mahallesi', 'Büyükdere Caddesi', '199', '', 'Şişli', 'Konyaaltı', '34394')]}
        p, done = self.run_agent('adres-cozumleyici', '--yes', answers=[bad, GOOD['adres-cozumleyici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('Ayşe Yılmaz', sent)  # only the address column leaves the machine
        self.assertNotIn('Ümraniye', sent)  # complete addresses stay with the rule parser
        self.assertIn('6: NİLÜFER BURSA ODUNLUK', sent)  # the rule put the district into the neighbourhood, so it is sent
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('3: mahalle “Fenerbahçe Mahallesi” metinde geçmiyor', retry)
        self.assertIn('6: posta kodu 16110 Bursa iline ait, il İstanbul verilmiş', retry)
        self.assertIn('il “Konyaaltı” 81 il listesinde yok', retry)
        self.assertIn('cevapsız adres: 5, 10', retry)
        rows = {r['musteri_no']: r for r in p.csv('adresler_ayrisik.csv')}
        self.assertEqual({r['adr_tam'] for r in rows.values()}, {'evet'})
        self.assertEqual((rows['M06']['adr_mahalle'], rows['M06']['adr_ilce'], rows['M06']['adr_kaynak']), ('Odunluk Mahallesi', 'Nilüfer', 'model'))
        self.assertEqual(rows['M03']['adr_kaynak'], 'model (il çıkarım)')  # İstanbul from postal code 34710
        self.assertEqual(rows['M01']['adr_kaynak'], 'kural')

    def test_category_agent_uses_leaf_categories_only(self):
        bad = {'eslemeler': [{**e, 'kategori': {'Siyah Çay 1 kg': 'K12', 'Kettle 1,7 L': 'K99'}.get(e['urun'], e['kategori'])}
                             for e in GOOD['urun-kategori-eslestirici']['eslemeler'] if e['urun'] != 'Hediye Seti']}
        p, done = self.run_agent('urun-kategori-eslestirici', '--yes', answers=[bad, GOOD['urun-kategori-eslestirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('Doğal Kaynak Suyu', sent)  # already categorised
        self.assertNotIn('türk kahvesi 100g', sent)  # same product name, sent once
        self.assertNotIn('189,90', sent)  # prices stay local
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('“Siyah Çay 1 kg”: K12 (İçecek) ara düğüm', retry)
        self.assertIn('“Kettle 1,7 L”: K99 kategori ağacında yok', retry)
        self.assertIn('cevapsız ürün: Hediye Seti', retry)
        rows = {r['urun_kodu']: r for r in p.csv('urun_kategorileri.csv')}
        self.assertEqual(rows['U013']['kategori_kodu'], 'K122')  # follows U005
        self.assertEqual((rows['U004']['kategori_kodu'], rows['U004']['kaynak']), ('K121', 'mevcut'))
        self.assertEqual(rows['U014']['kaynak'], 'eşlenemedi')
        self.assertEqual(rows['U015']['kategori_yolu'], 'Elektronik > Küçük Ev Aletleri')

    def test_schema_change_impact_is_found_in_code(self):
        bad = {'degisiklikler': [{**GOOD['sema-degisiklik-etkisi']['degisiklikler'][0], 'seviye': 'orta'},
                                 {**GOOD['sema-degisiklik-etkisi']['degisiklikler'][2], 'dosyalar': ['rapor_aylik_ciro.sql', 'segment_ozeti.sql']}]}
        p, done = self.run_agent('sema-degisiklik-etkisi', '--yes', answers=[bad, GOOD['sema-degisiklik-etkisi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('kampanya_listesi.sql: SELECT m.musteri_no, m.segment, m.eposta', sent)
        self.assertNotIn("WHERE m.segment IN ('Altın', 'Platin')", sent)  # only lines that mention the changed column
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('seviye en az kırıcı olmalı (silinen kolon sorgularda kullanılıyor)', retry)
        self.assertIn('bu değişikliği kullanmayan: segment_ozeti.sql', retry)
        self.assertIn('anlatılmayan değişiklik: 2', retry)
        rows = {r['sutun']: r for r in p.csv('etki.csv')}
        self.assertEqual((rows['eposta']['seviye'], rows['eposta']['etkilenen_dosyalar']), ('kırıcı', 'kampanya_listesi.sql'))
        self.assertEqual(rows['tutar']['en_dusuk_seviye'], 'riskli')  # decimal(18,2) → decimal(12,2) narrows
        self.assertEqual(rows['kanal']['etkilenen_dosyalar'], '')

    def test_segment_claims_are_checked_against_the_clusters(self):
        bad = json.loads(json.dumps(GOOD['segment-yorumlayici']))
        bad['segmentler'][2]['ozellikler'] = [{'metrik': 'son_alisveris_gun', 'yon': 'düşük'}]
        bad['segmentler'][3]['ad'] = 'Kayıp riski'
        p, done = self.run_agent('segment-yorumlayici', '--yes', answers=[bad, GOOD['segment-yorumlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('M0001', sent)  # customer rows stay local
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('son_alisveris_gun “düşük” denmiş ama küme ortalamadan +1.86 standart sapma uzakta', retry)
        self.assertIn('segment 3: ad boş ya da başka segmentle aynı', retry)
        summary = {int(r['segment_no']): r for r in p.csv('segment_ozeti.csv')}
        self.assertEqual([summary[j]['musteri'] for j in range(4)], ['40', '30', '25', '25'])  # the four generated groups come back
        rows = p.csv('musteri_segmentleri.csv')
        self.assertEqual(sum(r['segment'] == 'Sık iade edenler' for r in rows), 25)
        self.assertTrue(all(float(r['iade_orani'].replace(',', '.')) > 0.15 for r in rows if r['segment'] == 'Sık iade edenler'))

    def test_synthetic_data_never_carries_real_values(self):
        bad = json.loads(json.dumps(GOOD['sentetik-veri-tasarimcisi']))
        bad['kolonlar']['eposta'] = {'tur': 'kategori', 'degerler': {'a@b.com': 1}}
        bad['kolonlar']['segment']['degerler']['Kamu'] = 3
        bad['kolonlar']['toplam_harcama']['max'] = 90000
        p, done = self.run_agent('sentetik-veri-tasarimcisi', '--yes', answers=[bad, GOOD['sentetik-veri-tasarimcisi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        real = (p.dir / 'ornek' / 'musteri_kayitlari.csv').read_text(encoding='utf-8')
        self.assertNotIn('posta.com.tr', sent)
        self.assertNotIn('[TELEFON]', sent)  # phone numbers are not even profiled as numbers
        self.assertNotIn(real.splitlines()[1].split(';')[1], sent)  # first real name
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('eposta: kişisel veri (e-posta) kolonu yalnızca', retry)
        self.assertIn('segment: profilde olmayan değer: Kamu', retry)
        self.assertIn('gerçek aralığın (195.34–47984.79) dışında', retry)
        rows = p.csv('sentetik.csv')
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({r['musteri_no'] for r in rows}), 200)
        real_values = {v.strip().lower() for line in real.splitlines()[1:] for v in line.split(';')[1:4] if v.strip()}
        self.assertFalse(real_values & {r[c].lower() for r in rows for c in ('ad_soyad', 'eposta', 'telefon')})
        self.assertTrue(all(195.34 <= float(r['toplam_harcama']) <= 47984.79 for r in rows))
        self.assertEqual({r['segment'] for r in rows}, {'Bireysel', 'KOBİ', 'Kurumsal'})
        again = self.package('sentetik-veri-tasarimcisi')  # same seed, same data
        self.model.answers = [GOOD['sentetik-veri-tasarimcisi']]
        self.ok(again.run('--yes', env=self.model.env()))
        self.assertEqual(again.csv('sentetik.csv'), rows)

    def test_document_fields_need_matching_quotes(self):
        bad = json.loads(json.dumps(GOOD['belge-alan-cikarici']))
        first = bad['belgeler'][0]['alanlar']
        first['aylik_bedel'] = {'deger': '158000', 'kanit': 'aylık 185.000,00 TL + KDV ödeyecektir'}
        first['bitis_tarihi'] = {'deger': '2028-01-15', 'kanit': '14.01.2028 tarihine kadar geçerlidir'}
        first['tedarikci'] = {'deger': 'Bulutçu Bilişim', 'kanit': 'Bulutçu Bilişim A.Ş. hizmet sağlayıcıdır'}
        bad['belgeler'][2]['alanlar']['para_birimi'] = {'deger': 'Dolar', 'kanit': '4.500 USD'}
        p, done = self.run_agent('belge-alan-cikarici', '--yes', answers=[bad, GOOD['belge-alan-cikarici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('hukuk@ornek-lojistik.com.tr', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('tedarikci = “Bulutçu Bilişim”: alıntı belgede birebir geçmiyor', '2028-01-15 alıntıdaki tarihlerle uyuşmuyor',
                     '158000 alıntıdaki sayılarla uyuşmuyor', 'para_birimi = “Dolar”: seçenek TRY/USD/EUR olmalı'):
            self.assertIn(text, retry)
        rows = {r['belge']: r for r in p.csv('alanlar.csv')}
        self.assertEqual((rows['bulut_hizmet.txt']['sozlesme_tarihi'], rows['bulut_hizmet.txt']['aylik_bedel']), ('2026-01-15', '185000'))  # “15 Ocak 2026”, “185.000,00”
        self.assertEqual(rows['yazilim_destek.txt']['bitis_tarihi'], '2027-02-28')  # “28/02/2027”
        self.assertEqual(rows['guvenlik_teklifi.md']['fesih_bildirim_gun'], '')

    def test_join_keys_are_measured_on_both_tables(self):
        bad = {'anahtarlar': [{'sol': 'vergi_no', 'sag': 'Tax ID', 'donusum_sol': [], 'donusum_sag': ['digits']}], 'iliski': 'n-1'}
        p, done = self.run_agent('birlestirme-anahtari', '--yes', answers=[bad, GOOD['birlestirme-anahtari']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('eşleşme oranı %77.5 (gereken %90)', retry)  # tax ids are blank in 8 CRM rows
        self.assertIn('iliski n-1 denmiş ama dönüşüm sonrası ölçülen 1-1', retry)
        rows = p.csv('birlesik.csv')
        self.assertEqual((len(rows), sum(r['eslesti'] == 'evet' for r in rows)), (40, 38))
        self.assertEqual(len(p.csv('eslesmeyen.csv')), 2)
        sql = p.out('birlestirme.sql').read_text(encoding='utf-8')
        self.assertIn("LTRIM(CASE WHEN UPPER(TRIM(s.\"cari_kodu\")) LIKE 'C%'", sql)
        import sqlite3  # the same keys through SQLite string functions agree with the Python join
        con = sqlite3.connect(':memory:')
        for name, path in (('sol', 'erp_cari_listesi.csv'), ('sag', 'crm_musteri_listesi.csv')):
            lines = (p.dir / 'ornek' / path).read_text(encoding='utf-8').splitlines()
            cols = lines[0].split(';')
            con.execute(f'CREATE TABLE {name} ({", ".join(chr(34) + c + chr(34) for c in cols)})')
            con.executemany(f'INSERT INTO {name} VALUES ({", ".join("?" * len(cols))})', [l.split(';') for l in lines[1:]])
        key = "COALESCE(NULLIF(LTRIM(CASE WHEN UPPER(TRIM({c})) LIKE 'C%' THEN SUBSTR(UPPER(TRIM({c})), 2) ELSE UPPER(TRIM({c})) END, '0'), ''), '0')"
        n = con.execute(f'SELECT COUNT(*) FROM sol s JOIN sag g ON {key.format(c="s.cari_kodu")} = {key.format(c="g.ERP_REF")}').fetchone()[0]
        self.assertEqual(n, 38)

    def test_error_rules_run_on_every_message(self):
        bad = {'kategoriler': [{'kod': 'GENEL', 'ad': 'Genel', 'desen': 'error', 'yeniden_dene': True, 'aksiyon': 'Yeniden dene.', 'ornekler': [5]},
                               {'kod': 'BAGLANTI', 'ad': 'Bağlantı', 'desen': 'timed out', 'yeniden_dene': True, 'aksiyon': 'x', 'ornekler': [1, 2]}]}
        p, done = self.run_agent('hata-siniflandirici', '--yes', answers=[bad, GOOD['hata-siniflandirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('1. (8 kez) psycopg2.OperationalError', sent)  # 36 runs sent as 9 templates
        self.assertIn('host "<IP>"', sent)
        self.assertNotIn('bordro_2026', sent)  # file paths reduced to <YOL>
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('BAGLANTI: örnek gösterdiği şablon 1 bu kurala düşmüyor (GENEL)', retry)  # shadowed by the earlier rule
        self.assertIn('sınıflanmayan şablonlar:', retry)
        rows = p.csv('siniflandirilmis.csv')
        self.assertEqual(sum(r['kategori'] == 'BAGLANTI' for r in rows), 14)
        self.assertEqual([r['kategori'] for r in rows if 'SIGTERM' in r['hata_mesaji']], ['SINIFLANMADI'])
        rules = p.json('kurallar.json')
        self.assertEqual(rules[0]['kod'], 'BAGLANTI')
        self.assertIn('%97.2', p.out('hata-siniflari.md').read_text(encoding='utf-8'))

    def test_glossary_terms_map_to_typed_columns(self):
        t = lambda term, cols: {'terim': term, 'kolonlar': [{'tablo': a, 'kolon': b} for a, b in cols], 'gerekce': 'x'}
        bad = {'terimler': [t('Net Ciro', [('dwh.fct_satis', 'brut_tutar')]), t('Brüt Satış Tutarı', [('dwh.fct_satis', 'brut_tutar')]),
                            t('Sipariş Tarihi', [('dwh.fct_satis', 'siparis_no')]), t('Aktif Müşteri', [('dwh.dim_musteri', 'aktif')])]}
        p, done = self.run_agent('sozluk-kolon-eslestirici', '--yes', answers=[bad, GOOD['sozluk-kolon-eslestirici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('dwh.fct_satis.brut_tutar hem “Net Ciro” hem “Brüt Satış Tutarı” terimine bağlanmış', 'Sipariş Tarihi (tarih): dwh.fct_satis.siparis_no tipi varchar(20) uymuyor',
                     'Aktif Müşteri: dwh.dim_musteri.aktif şemada yok', 'cevapsız terim: Müşteri Kodu, Satış Kanalı, Müşteri Yaşam Boyu Değeri'):
            self.assertIn(text, retry)
        rows = p.csv('terim_kolon.csv')
        self.assertEqual(sorted(r['tablo'] for r in rows if r['terim'] == 'Müşteri Kodu'), ['dwh.dim_musteri', 'dwh.fct_satis'])
        md = p.out('sozluk-esleme.md').read_text(encoding='utf-8')
        self.assertIn('dwh.dim_musteri.kayit_kanali', md.split('## Terimi olmayan kolonlar')[1])
        yml = p.out('dbt_sozluk.yml').read_text(encoding='utf-8')
        self.assertIn('      - name: aktif_mi\n        description: "Son 12 ayda en az bir siparişi olan müşteri"\n        meta:\n          terim: "Aktif Müşteri"', yml)

    def test_payment_matching_checks_amounts_and_parties(self):
        e = lambda h, f, k=False: {'hareket': h, 'faturalar': f, 'kismi': k, 'gerekce': 'x'}
        bad = {'eslesmeler': [e('H02', ['FTR-2026-1003']), e('H03', ['FTR-2026-1001']), e('H04', ['FTR-2026-1004', 'FTR-2026-1006']), e('H05', ['FTR-2026-1006'])]}
        p, done = self.run_agent('tahsilat-eslestirici', '--yes', answers=[bad, GOOD['tahsilat-eslestirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('H01:', sent)  # invoice number + exact amount: matched by the rule
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('H03: açık listede olmayan fatura: FTR-2026-1001', 'H04: faturalar farklı carilere ait', 'H05: hareket 10000.00 ≠ faturaların kalan toplamı 19990.00', 'cevapsız hareket: H06'):
            self.assertIn(text, retry)
        rows = {r['hareket_no']: r for r in p.csv('eslesmeler.csv')}
        self.assertEqual((rows['H01']['kaynak'], rows['H04']['faturalar'], rows['H05']['tur'], rows['H06']['tur']), ('kural', 'FTR-2026-1004;FTR-2026-1005', 'kısmi', 'eşlenmedi'))
        left = {r['fatura_no']: r['kalan'] for r in p.csv('kalan_faturalar.csv')}
        self.assertEqual(left, {'FTR-2026-1006': '9990.00', 'FTR-2026-1007': '3300.00'})

    def test_masking_plan_is_applied_and_rescanned(self):
        k = lambda m, **x: {'yontem': m, **x}
        bad = {'kolonlar': {**GOOD['maskeleme-plani']['kolonlar'], 'sicil_no': k('koru', anahtar=True), 'ad_soyad': k('koru'), 'departman': k('genellestir'), 'maas': k('genellestir')}}
        p, done = self.run_agent('maskeleme-plani', '--yes', '--salt', 'test-tuzu', answers=[bad, GOOD['maskeleme-plani']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        real = (p.dir / 'ornek' / 'personel_listesi.csv').read_text(encoding='utf-8').splitlines()[1].split(';')
        self.assertNotIn(real[1], sent)  # name
        self.assertNotIn(real[2], sent)  # TCKN
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('sicil_no: birleştirme anahtarı takma adla maskelenmeli', 'ad_soyad: kişisel veri (ad soyad) korunamaz',
                     'departman: genelleştirme yalnızca tarih/sayı kolonunda', 'maas: sayı genelleştirmede "aralik"'):
            self.assertIn(text, retry)
        rows = p.csv('maskeli.csv')
        self.assertTrue(all(r['sicil_no'].startswith('KV-') and r['ad_soyad'] == '' for r in rows))
        self.assertRegex(rows[0]['dogum_tarihi'], r'^\d{4}-\d{2}$')
        self.assertRegex(rows[0]['maas'], r'^\d+0000-\d+0000$')
        self.assertFalse(any(common.pii_kind(v) for r in rows for v in r.values()))  # embedded phones in notes are masked too
        self.assertEqual(len({r['sicil_no'] for r in rows}), len(rows))

    def test_type_mapping_fits_measured_values(self):
        names = {'SIPARIS_ID': 'integer', 'SIPARIS_TARIHI': 'date', 'KUR': 'numeric(10,2)', 'ADET': 'boolean', 'UNVAN': 'varchar(100)'}
        bad = {'eslemeler': [{**e, 'hedef_tip': names.get(e['kolon'], e['hedef_tip'])} for e in GOOD['tip-donusum-eslestirici']['eslemeler'] if e['kolon'] != 'NOTLAR']}
        p, done = self.run_agent('tip-donusum-eslestirici', '--yes', answers=[bad, GOOD['tip-donusum-eslestirici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('SIPARIS_ID (NUMBER) → integer: 10 haneli değer integer aralığına sığmaz', 'SIPARIS_TARIHI (DATE) → date: değerlerde saat var',
                     'KUR (NUMBER) → numeric(10,2): ölçek 2 < ölçülen ondalık hane 6', 'UNVAN (NVARCHAR2(200)) → varchar(100): uzunluk 100 < ölçülen en uzun değer 143',
                     'eşlenmeyen kolon: MUSTERI.NOTLAR'):
            self.assertIn(text, retry)
        ddl = p.out('hedef_ddl.sql').read_text(encoding='utf-8')
        self.assertIn('"siparis_id" BIGINT NOT NULL', ddl)
        self.assertIn('"teslim_tarihi" DATE,', ddl)
        ss, done = self.run_agent('tip-donusum-eslestirici', '--yes', '--target', 'sqlserver',
                                  answers=[{'eslemeler': [{**e, 'hedef_tip': {'varchar(200)': 'varchar(200)'}.get(e['hedef_tip'], 'nvarchar(max)')} for e in GOOD['tip-donusum-eslestirici']['eslemeler']]}] * 2)
        self.assertEqual(done.returncode, 1)
        self.assertIn('Unicode karakter var; SQL Server’da nvarchar gerekir', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_dbt_tests_must_pass_on_todays_data(self):
        bad = {'kolonlar': {'siparis_no': [{'test': 'not_null'}], 'kanal': [{'test': 'not_null'}], 'durum': [{'test': 'accepted_values', 'values': ['yeni', 'teslim']}],
                            'musteri_kodu': [{'test': 'relationships', 'to': 'dim_urun', 'field': 'kod'}]}}
        p, done = self.run_agent('dbt-test-onerici', '--yes', answers=[bad, GOOD['dbt-test-onerici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('kanal not_null: bugünkü veride 15 satır geçmiyor', 'durum accepted_values: bugünkü veride 33 satır geçmiyor',
                     'relationships hedefi dim_urun.kod yok (verilen modeller: dim_musteri)', 'aday anahtar siparis_no için unique testi eksik'):
            self.assertIn(text, retry)
        yml = p.out('schema.yml').read_text(encoding='utf-8')
        self.assertIn("          - relationships:\n              arguments:\n                to: ref('dim_musteri')\n                field: musteri_kodu", yml)
        self.assertIn('          - dbt_utils.accepted_range:\n              arguments:\n                min_value: 0\n                max_value: 100', yml)

    def test_funnel_findings_match_the_computed_table(self):
        bad = {'en_buyuk_kayip': 'satin_alma', 'en_kotu_segment': {'satin_alma': 'mobil'}, 'ozet': 'Kullanıcıların %15’i satın aldı.', 'oneriler': ['x']}
        p, done = self.run_agent('huni-analisti', '--yes', answers=[bad, GOOD['huni-analisti']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('Huni adımları: ziyaret → urun_goruntuleme → sepete_ekleme → odeme_baslatma → satin_alma', sent)  # typical order found in code
        self.assertNotIn('U0001', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('en büyük kayıp “satin_alma” değil', 'satin_alma adımında en düşük dönüşüm mobil segmentinde değil', 'hesaplanan sonuçlarda olmayan sayılar: 15'):
            self.assertIn(text, retry)
        main = [r for r in p.csv('huni.csv') if r['grup'] == 'tümü']
        self.assertEqual([r['kullanici'] for r in main], ['200', '123', '50', '32', '22'])

    def test_regression_reading_is_checked(self):
        bad = {'degiskenler': [{'ad': 'reklam_harcamasi', 'etki': 'negatif', 'yorum': 'x'}, {'ad': 'indirim_orani', 'etki': 'anlamsız', 'yorum': 'x'}],
               'ozet': 'Reklam harcaması satışları artırır; her 2,5 TL reklam bir satış getirir.'}
        p, done = self.run_agent('regresyon-yorumlayici', '--yes', answers=[bad, GOOD['regresyon-yorumlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('reklam_harcamasi · 1.3004 · 0.3265 · 3.983 · 0.00023', sent)
        self.assertNotIn('2025-H01', sent)  # rows stay local
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('reklam_harcamasi: katsayı 1.3004, p=0.00023 → etki “pozitif” olmalı', 'indirim_orani: katsayı 8208.6979, p=1e-05 → etki “pozitif” olmalı',
                     'nedensellik iddiası (“artırır”)', 'tabloda olmayan sayılar: 2,5'):
            self.assertIn(text, retry)
        self.assertEqual({r['degisken']: r['etki'] for r in p.csv('katsayilar.csv')}, {'sabit': '', 'reklam_harcamasi': 'pozitif', 'indirim_orani': 'pozitif'})

    def test_shift_plan_covers_every_interval(self):
        bad = {'vardiyalar': [{'baslangic': '08:00', 'sure_saat': 8, 'kisi': 10}, {'baslangic': '12:00', 'sure_saat': 8, 'kisi': 10}]}
        p, done = self.run_agent('vardiya-planlayici', '--yes', answers=[bad, GOOD['vardiya-planlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('14:30 · 95 · 19', sent)  # Erlang C need at the peak (with 30% shrinkage)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('eksik aralıklar: 09:30 (10/13), 10:00 (10/16)', retry)
        cover = p.csv('kapsama.csv')
        self.assertTrue(all(int(r['planlanan']) >= int(r['gereken']) for r in cover))
        self.assertEqual(sum(int(r['gereken']) for r in cover), 316)  # 158 person-hours
        over = {'vardiyalar': [{'baslangic': '08:00', 'sure_saat': 8, 'kisi': 20}, {'baslangic': '12:00', 'sure_saat': 8, 'kisi': 20}]}
        p2, done = self.run_agent('vardiya-planlayici', '--yes', answers=[over, over])
        self.assertEqual(done.returncode, 1)
        self.assertIn('planlanan 320 adam-saat, gereken 158; fazlalık %103 (en fazla %25)', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_messy_report_header_is_resolved(self):
        bad = {'baslik_satirlari': [5, 6], 'veri_ilk': 7, 'veri_son': 14, 'atlanacak_satirlar': [],
               'kolon_adlari': ['bolge', 'temsilci', 'temmuz_adet', 'temmuz_tutar', 'agustos_adet', 'agustos_tutar']}
        p, done = self.run_agent('excel-baslik-cozucu', '--yes', answers=[bad, GOOD['excel-baslik-cozucu']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('satis.raporlama@ornek.com.tr', sent)
        self.assertIn('7: Marmara | Ayşe K. | <sayı> | <sayı> | <sayı> | <sayı>', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('veri aralığında toplam satırı: 10, 14', retry)
        rows = p.csv('temiz.csv')
        self.assertEqual([r['bolge'] for r in rows], ['Marmara', 'Marmara', 'Ege', 'İç Anadolu', 'Akdeniz', 'Karadeniz'])
        self.assertEqual(rows[0]['agustos_tutar'], '54.250,00')

    def test_kafka_topic_settings_follow_measured_load(self):
        k = lambda n, b, ret, cl, rf=3, isr=2: {'konu': n, 'bolum': b, 'cogaltma': rf, 'min_insync': isr, 'saklama_saat': ret, 'cleanup': cl}
        bad = {'konular': [k('siparis-olaylari', 4, 168, 'delete'), k('tiklama-akisi', 24, 24, 'compact', rf=4, isr=1), k('musteri-profil', 6, -1, 'compact'),
                           k('odeme-sonuclari', 12, 72, 'delete')]}
        p, done = self.run_agent('kafka-konu-tasarimcisi', '--yes', answers=[bad, GOOD['kafka-konu-tasarimcisi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('tiklama-akisi: tepe 60000 mesaj/sn × 0.8 KB; tüketici örneği 5000 mesaj/sn', sent)
        self.assertIn('en az bölüm 12 (yazma 4.69, okuma 12.0)', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('siparis-olaylari: bölüm 4 < en az 6', 'çoğaltma 4; 3–3 arası olmalı (3 broker)', 'compact yalnızca anahtarlı durum konusunda',
                     '8 farklı anahtar var; 12 bölümün bir kısmı boş kalır', 'saklama 72 sa < geri oynatma ihtiyacı 336 sa', 'yazılmayan konu: stok-seviyesi'):
            self.assertIn(text, retry)
        sh = p.out('kafka-konulari.sh').read_text(encoding='utf-8')
        self.assertIn('--topic odeme-sonuclari --partitions 6 --replication-factor 3 --config min.insync.replicas=2 --config cleanup.policy=delete --config retention.ms=1209600000', sh)
        self.assertIn('--topic musteri-profil --partitions 6 --replication-factor 3 --config min.insync.replicas=2 --config cleanup.policy=compact --config retention.ms=-1', sh)

    def test_slo_risk_flag_matches_history(self):
        s = lambda j, th, t, risk: {'is': j, 'bitis_esigi': th, 'hedef': t, 'pencere_gun': 28, 'risk': risk}
        bad = {'slolar': [s('erp_stg_yukle', '04:45', 0.9, False), s('satis_dwh', '06:45', 0.95, False), s('gunluk_pano_yenile', '07:00', 0.999, False)]}
        p, done = self.run_agent('slo-tasarimcisi', '--yes', answers=[bad, GOOD['slo-tasarimcisi']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('satis_dwh: eşik 06:45, Satış panosu ihtiyacı 06:30’dan geç', retry)
        self.assertIn('gunluk_pano_yenile: son 28 günde 07:00 eşiğiyle gerçekleşen %92.9, hedef %99.9; risk true olmalı', retry)
        self.assertNotIn('SLO yazılmayan iş', retry)
        rows = {r['is']: r for r in p.csv('slo_durumu.csv')}
        self.assertEqual((rows['satis_dwh']['gerceklesen_%'], rows['satis_dwh']['risk'], rows['satis_dwh']['hata_butcesi_gun'], rows['satis_dwh']['harcanan_gun']), ('89.3', 'evet', '1', '3'))
        self.assertIn('timeWindow: [{duration: 28d, isRolling: true}]', p.out('slo.yaml').read_text(encoding='utf-8'))

    def test_tag_rules_fill_only_missing_tags(self):
        t = lambda d, e: {'desen': d, 'alan': 'kaynak_id', 'etiketler': e}
        bad = {'kurallar': [t('^prod-', {'tag_env': 'prod'}), t('-emr$', {'tag_env': 'test'}), t('-finans-', {'tag_owner': 'finans-ekibi', 'tag_cost_center': 'CC200'})]}
        p, done = self.run_agent('etiket-kurali-yazari', '--yes', answers=[bad, GOOD['etiket-kurali-yazari']])
        self.ok(done)
        self.assertIn('çakışan kurallar: prod-veri-emr.tag_env', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = {r['kaynak_id']: r for r in p.csv('etiketlenmis.csv')}
        self.assertEqual((rows['prod-veri-emr']['tag_owner'], rows['prod-veri-emr']['tag_cost_center'], rows['prod-veri-emr']['tag_env']), ('veri-ekibi', 'CC300', 'prod'))
        self.assertEqual(rows['test-finans-db']['tag_cost_center'], 'CC200')  # existing value kept
        self.assertEqual(rows['eski-deneme-sunucu']['tag_owner'], '')  # no rule guesses it
        self.assertIn('%97', p.out('etiket-kurallari.md').read_text(encoding='utf-8'))  # 1850 of 1905 untagged dollars
        p2, done = self.run_agent('etiket-kurali-yazari', '--yes', answers=[{'kurallar': [t('^prod-', {'tag_env': 'production'}), t('-veri-', {'tag_owner': 'veri-ekibi', 'tag_cost_center': 'CC999'})]}] * 2)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('tag_env=production izinli değil (prod/test/dev)', retry)
        self.assertIn('tag_cost_center=CC999 mevcut etiketlerde hiç görülmemiş', retry)

    def test_feature_engineering_blocks_leakage(self):
        f = lambda ad, op, **k: {'ad': ad, 'islem': op, **k}
        bad = {'ozellikler': [f('iptal_var', 'bos_mu', a='iptal_tarihi'), f('kanal_oran', 'oran', a='kanal', b='gelir'), f('churn_kopya', 'log', a='churn')]}
        p, done = self.run_agent('ozellik-muhendisi', '--yes', '--reference-date', '2026-10-01', answers=[bad, GOOD['ozellik-muhendisi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('iptal_tarihi: metin/tarih, dolu %17, 1 tekil, değerler: 2026-09-10 (SIZINTI: boşluğu hedefi belirliyor; kullanma)', sent)
        self.assertNotIn('M00001', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('iptal_tarihi sızıntı kolonu', 'kanal sayısal değil', 'churn hedef ya da kimlik kolonu'):
            self.assertIn(text, retry)
        rows = p.csv('ozellikli_veri.csv')
        self.assertEqual((rows[0]['yas_kova'], rows[1]['magaza_mi']), ('2', '1'))  # yas 53 → bucket 2 (>30, >45); M00001 shops in store
        self.assertAlmostEqual(float(rows[0]['sepet_gelir_orani']), 1243.36 / 22478, places=5)
        import importlib.util
        spec = importlib.util.spec_from_file_location('ozellikler_test', p.out('ozellikler.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertAlmostEqual(mod.ekle({'sepet': '1243.36', 'gelir': '22478', 'yas': '53', 'kanal': 'web'})['sepet_gelir_orani'], 1243.36 / 22478)

    def test_drift_reading_follows_psi_bands(self):
        bad = {'ozellikler': [{'ozellik': 'kanal', 'durum': 'izlenmeli'}, {'ozellik': 'yas', 'durum': 'belirgin kayma'}, {'ozellik': 'gelir', 'durum': 'kararlı'},
                              {'ozellik': 'sepet', 'durum': 'kararlı'}], 'yeniden_egit': False, 'ozet': 'Mobil payı %60 oldu.'}
        p, done = self.run_agent('kayma-yorumlayici', '--yes', answers=[bad, GOOD['kayma-yorumlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('kanal · kategorik · 0.464', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('kanal: PSI 0.464 → durum “belirgin kayma” olmalı', 'yas: belirgin kaymada aksiyon yazılmalı', 'yeniden_egit true olmalı', 'tabloda olmayan sayılar: 60'):
            self.assertIn(text, retry)
        self.assertEqual({r['ozellik']: r['durum'] for r in p.csv('kayma.csv')}, {'kanal': 'belirgin kayma', 'yas': 'belirgin kayma', 'gelir': 'kararlı', 'sepet': 'kararlı'})

    def test_faq_answers_are_quoted_and_conflicts_reported(self):
        q = lambda s, c, k, a: {'soru': s, 'cevap': c, 'kaynak': k, 'kanit': a}
        bad = {'sss': [q('Ürünü kaç gün içinde iade edebilirim?', '30 gün içinde iade edebilirsiniz.', 'iade_politikasi.md', 'Ürünler teslimattan itibaren 14 gün içinde iade edilebilir.'),
                       q('Kargo ücreti ne kadar?', '49,90 TL.', 'kargo.md', 'Altındaki siparişlerde sabit 49,90 TL kargo ücreti alınır.')], 'celiskiler': []}
        p, done = self.run_agent('sss-yazari', '--yes', answers=[bad, GOOD['sss-yazari']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('kargo.md: kargo_kopya.md ile sayılar farklı: 49,90, 59,90', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('cevaptaki sayılar alıntıda yok: 30', 'kargo.md başka bir belgeyle çelişiyor', 'bildirilmeyen çelişkili belge: kargo.md, kargo_kopya.md'):
            self.assertIn(text, retry)
        faq = p.json('sss.json')
        self.assertEqual(len(faq), 4)
        self.assertTrue(all(x['kaynak'] == 'iade_politikasi.md' for x in faq))
        self.assertIn('## Çelişkiler', p.out('sss.md').read_text(encoding='utf-8'))

    def test_delta_maintenance_rules(self):
        d = lambda t, o, z, v: {'tablo': t, 'optimize': o, 'zorder': z, 'vacuum_saat': v}
        bad = {'tablolar': [d('satis.fct_siparis', True, ['siparis_tarihi', 'tutar'], 168), d('satis.dim_musteri', True, [], None), d('log.web_olaylari', True, ['kullanici_id'], 24)]}
        p, done = self.run_agent('delta-bakim-plani', '--yes', answers=[bad, GOOD['delta-bakim-plani']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('sorgularda filtrelenmeyen kolon: tutar', 'bölüm kolonu ZORDER’a girmez: siparis_tarihi', 'ortalama dosya 819 MB; OPTIMIZE gereksiz',
                     'VACUUM saklaması en az 168 saat olmalı', 'plansız tablo: finans.gl_hareket'):
            self.assertIn(text, retry)
        sql = p.out('bakim.sql').read_text(encoding='utf-8')
        self.assertIn('OPTIMIZE log.web_olaylari ZORDER BY (kullanici_id, olay);', sql)
        self.assertNotIn('OPTIMIZE satis.dim_musteri', sql)

    def test_cohort_reading_is_checked(self):
        bad = {'en_iyi_kohort': '2025-12', 'en_kotu_kohort': '2026-05', 'egilim': 'azalıyor', 'ozet': 'Tutundurma %80’den düştü.', 'oneriler': []}
        p, done = self.run_agent('kohort-yorumlayici', '--yes', answers=[bad, GOOD['kohort-yorumlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('M036', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('en iyi kohort 2025-12 değil', 'en kötü kohort 2026-05 değil', 'eğilimi “azalıyor” değil (kohort başına eğim -0.12 puan)', 'matriste olmayan sayılar: 80'):
            self.assertIn(text, retry)
        self.assertEqual(len(p.csv('kohort.csv')), 11)

    def test_local_ids_are_unique(self):
        from collections import Counter
        dup = [k for k, n in Counter(i['id'] for i in LOCAL).items() if n > 1]
        self.assertEqual(dup, [])

    def test_fixed_width_layout_is_applied_to_the_whole_file(self):
        a = lambda n, s, l, t='metin': {'alan': n, 'baslangic': s, 'uzunluk': l, 'tip': t}
        bad = {'alanlar': [a('hesap_no', 1, 10), a('islem_tarihi', 11, 8, 'tarih:%Y%m%d'), a('islem_tipi', 19, 3), a('yon', 22, 1), a('tutar', 23, 12, 'sayi:2'),
                           a('aciklama', 36, 20, 'sayi'), a('sube_kodu', 56, 4)]}
        p, done = self.run_agent('sabit-genislik-cozucu', '--yes', answers=[bad, GOOD['sabit-genislik-cozucu']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('KIRA', sent)  # only the shape (9/A) of the lines is sent
        self.assertIn('999999999999999999AAAA9999999999999AAAA', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('hiçbir alana girmeyen dolu karakterler: sütun 35', retry)
        self.assertIn('alan değeri çevrilemedi: satır 1 aciklama', retry)
        rows = p.csv('sonuc.csv')
        self.assertEqual((len(rows), rows[0]['islem_tarihi'], rows[0]['aciklama']), (40, '2026-09-09', 'KIRA'))
        self.assertEqual(rows[0]['tutar'], '63113.37')  # 0000006311337 with two decimals
        spec = p.csv('alan_tanimi.csv')
        self.assertEqual([r['alan'] for r in spec], ['hesap_no', 'islem_tarihi', 'islem_tipi', 'yon', 'tutar', 'aciklama', 'sube_kodu'])

    def test_cross_column_rules_must_say_something(self):
        k = lambda r, t: {'kural': r, 'tur': t}
        bad = {'kurallar': [k('teslim_tarihi >= siparis_tarihi', 'kesin'), k('adet > 0', 'kesin'), k('tutar = adet * birim_fiyat', 'kesin')]}
        p, done = self.run_agent('capraz-kural-yazari', '--yes', answers=[bad, GOOD['capraz-kural-yazari']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('“teslim_tarihi >= siparis_tarihi” satırların %70.0’inde tutuyor', retry)
        self.assertIn('“adet > 0” kolonlar karıştırılınca da tutuyor', retry)
        self.assertIn('“tutar = adet * birim_fiyat” satırların %58.8’inde tutuyor', retry)
        viol = p.csv('ihlaller.csv')
        self.assertEqual(sorted((r['siparis_no'], r['kural'][:8]) for r in viol), [('S0017', 'empty(te'), ('S0044', 'abs(tuta')])  # the two planted errors
        rules = p.out('kurallar.txt').read_text(encoding='utf-8').strip()
        blk = self.package('satir-kurallari')
        self.ok(blk.run('--input', str(p.dir / 'ornek' / 'siparis_teslimat.csv'), '--rules', rules))
        self.assertEqual(blk.json('ozet.json')['ihlal'], 2)  # the same rules run unchanged in the satir-kurallari block

    def test_scd_types_follow_measured_changes(self):
        bad = {'kolonlar': {'ad': 0, 'dogum_yili': 0, 'segment': 1, 'il': 2, 'telefon': 0, 'puan': 1}}
        p, done = self.run_agent('scd-tipi-onerici', '--yes', answers=[bad, GOOD['scd-tipi-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('Müşteri 1', sent)  # values stay local
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('segment: tarihçesi zorunlu (--history); tip 2 olmalı', retry)
        self.assertIn('telefon: 17 değişiklik görülmüş; tip 0 (değişmez) olamaz', retry)
        self.assertIn('      check_cols: [segment, il]', p.out('snapshot.yml').read_text(encoding='utf-8'))
        grow = {'kolonlar': {**GOOD['scd-tipi-onerici']['kolonlar'], 'puan': 2}}
        p2, done = self.run_agent('scd-tipi-onerici', '--yes', answers=[grow, grow])
        self.assertEqual(done.returncode, 1)
        self.assertIn('yeni sürüm üretir (sınır %50)', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_cutover_plan_respects_dependencies_teams_and_gate(self):
        pl = lambda g, b: {'gorev': g, 'baslangic': b}
        bad = {'plan': [{**x, 'baslangic': {'Bakiye mutabakatı': '00:30', 'Duman testleri': '01:50'}.get(x['gorev'], x['baslangic'])} for x in GOOD['kesinti-plani']['plan']],
               'karar_noktasi': '02:00'}
        p, done = self.run_agent('kesinti-plani', '--yes', answers=[bad, GOOD['kesinti-plani']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('Bakiye mutabakatı 00:30’da başlıyor ama bağımlı olduğu Delta veri aktarımı 00:40’da bitiyor', 'Uygulama ekibi aynı anda iki görevde',
                     'karar noktası 02:00, geri alınamaz ilk görev 01:30’da başlıyor'):
            self.assertIn(text, retry)
        rows = p.csv('kesinti_plani.csv')
        self.assertEqual((rows[-1]['gorev'], rows[-1]['bitis']), ('Kullanıcı erişimini aç', '02:50'))
        md = p.out('kesinti-plani.md').read_text(encoding='utf-8')
        self.assertIn('Devam / geri dön :milestone, 2026-11-15 01:30, 0m', md)  # after midnight: next day
        self.assertIn('Entegrasyonları yeni sisteme çevir :crit, 2026-11-15 01:30, 40m', md)

    def test_code_list_mapping_uses_target_codes(self):
        e = lambda v, k, g='x': {'deger': v, 'kod': k, 'gerekce': g}
        bad = {'eslemeler': [{**x, 'kod': 'KK'} if x['deger'] == 'KK' else x for x in GOOD['kod-listesi-eslestirici']['eslemeler']]}
        p, done = self.run_agent('kod-listesi-eslestirici', '--yes', answers=[bad, GOOD['kod-listesi-eslestirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('Kuralla eşlenenler (değiştirme): Kredi Kartı=KREDI_KARTI, KREDI KARTI=KREDI_KARTI', sent)
        self.assertNotIn('- Nakit (', sent)
        self.assertIn('“KK”: KK hedef kod listesinde yok', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = {r['deger']: r for r in p.csv('kod_esleme.csv')}
        self.assertEqual((rows['Kapida Odeme']['kod'], rows['Kapida Odeme']['kaynak'], rows['Çek']['kaynak']), ('KAPIDA_ODEME', 'kural', 'eşlenemedi'))
        sql = p.out('kod_esleme.sql').read_text(encoding='utf-8')
        self.assertIn("WHEN 'Havale/EFT' THEN 'BANKA_TRANSFERI'", sql)
        import sqlite3
        con = sqlite3.connect(':memory:')
        self.assertEqual(con.execute(f"SELECT {sql.strip()} FROM (SELECT ' KK ' AS odeme_tipi)").fetchone()[0], 'KREDI_KARTI')

    def test_index_suggestions_are_used_by_the_planner(self):
        i = lambda s: {'sql': s}
        bad = {'indeksler': [i('CREATE INDEX ix_siparis_tutar ON siparis (tutar)'), i('CREATE INDEX ix_siparis_durum2 ON siparis (durum)'), i('CREATE INDEX ix_kalem_urun ON siparis_kalem (urun_id)')]}
        p, done = self.run_agent('sorgu-indeks-onerici', '--yes', answers=[bad, GOOD['sorgu-indeks-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('-- Plan: SCAN siparis | USE TEMP B-TREE FOR ORDER BY', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('ix_siparis_tutar (siparis: tutar) hiçbir sorgunun planında kullanılmıyor', retry)
        self.assertIn('ix_siparis_durum2 (durum) ix_siparis_durum (durum) indeksinin baş kolonlarını tekrarlıyor', retry)
        rows = p.csv('plan_karsilastirma.csv')
        self.assertEqual([r['iyilesti'] for r in rows], ['evet', 'evet', 'evet', 'hayır', 'hayır'])
        self.assertIn('USING INDEX ix_siparis_musteri_tarih', rows[0]['sonra'])
        self.assertNotIn('TEMP B-TREE', rows[0]['sonra'])  # the index also serves ORDER BY

    def test_owner_suggestions_follow_query_log(self):
        o = lambda t, e, s: {'tablo': t, 'ekip': e, 'sorumlu': s}
        bad = {'oneriler': [o('dwh.fct_satis', 'Satış Analitiği', 'ayse.k'), o('dwh.dim_urun', 'Veri Mühendisliği', 'eski.calisan'), o('mart.kampanya_performans', 'Veri Mühendisliği', 'zeynep.a')]}
        p, done = self.run_agent('veri-sahibi-onerici', '--yes', answers=[bad, GOOD['veri-sahibi-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('dwh.fct_satis: yazma Veri Mühendisliği 32; okuma Satış Analitiği 235', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('Satış Analitiği yazmaların %0’ini yapıyor', 'sorumlu eski.calisan Veri Mühendisliği ekibinin aktif üyesi değil',
                     'sahip en çok okuyan iki ekipten biri olmalı (Pazarlama, Satış Analitiği)', 'öneri yazılmayan tablo: stg.crm_musteri'):
            self.assertIn(text, retry)
        rows = {r['tablo']: r for r in p.csv('sahiplik_onerileri.csv')}
        self.assertEqual((rows['dwh.fct_satis']['yazma_payi_%'], rows['mart.kampanya_performans']['yazma_payi_%']), ('100', ''))

    def test_chart_choice_matches_field_types(self):
        g = lambda t, x, y, agg='sum': {'tur': t, 'x': x, 'y': y, 'toplama': agg}
        bad = {'grafikler': [g('cizgi', 'bolge', 'tutar'), g('cubuk', 'siparis_no', 'tutar'), g('pasta', 'urun', 'tutar', 'mean'), g('kpi', None, 'kanal')]}
        p, done = self.run_agent('grafik-secici', '--yes', answers=[bad, GOOD['grafik-secici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('@ornek.test', sent)
        self.assertIn('musteri_eposta: metin, 57 farklı değer (kişisel veri)', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('çizgi grafiğin x’i tarih olmalı (bolge metin)', 'çubuk grafiğin x’i en fazla 25 kategorili boyut olmalı (siparis_no: 240 değer)',
                     'pasta en fazla 6 kategori', '4. soru (kpi): ölçü (y) sayısal olmalı'):
            self.assertIn(text, retry)
        kpi = json.loads(p.out('grafikler/4.vl.json').read_text(encoding='utf-8'))
        self.assertEqual(kpi['data']['values'], [{'deger': 5912910.0}])  # total of satis_detay
        pie = json.loads(p.out('grafikler/3.vl.json').read_text(encoding='utf-8'))
        self.assertEqual((pie['mark']['type'], len(pie['data']['values'])), ('arc', 3))
        line = json.loads(p.out('grafikler/1.vl.json').read_text(encoding='utf-8'))
        self.assertEqual(line['encoding']['x']['type'], 'temporal')
        self.assertIn('vegaEmbed("#g1"', p.out('grafikler.html').read_text(encoding='utf-8'))

    def test_delivery_days_respect_capacity_and_geography(self):
        days = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum']
        scattered = {'atamalar': [{'musteri': a['musteri'], 'gun': 'Cum' if a['musteri'] == 'B03' else days[i % 5]} for i, a in enumerate(GOOD['rota-gun-atayici']['atamalar'])]}
        p, done = self.run_agent('rota-gun-atayici', '--yes', answers=[scattered, GOOD['rota-gun-atayici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('kapasite (3000 kg) aşılıyor: Cum 3039 kg', retry)
        self.assertIn('günler coğrafi olarak dağınık: toplam ortalama uzaklık 57.2 km, kapasiteye uyan basit süpürme çözümü 16.8 km', retry)
        rows = p.csv('gun_atamasi.csv')
        self.assertEqual(next(r['gun'] for r in rows if r['musteri'] == 'B03'), 'Cum')
        gj = json.loads(p.out('gunler.geojson').read_text(encoding='utf-8'))
        self.assertEqual(len(gj['features']), 40)

    def test_retry_policy_follows_run_history(self):
        r = lambda d, t, n, de, to: {'dag': d, 'gorev': t, 'retries': n, 'retry_delay_dk': de, 'timeout_dk': to}
        bad = {'gorevler': [r('satis_gunluk', 'extract_erp', 2, 5, 5), r('satis_gunluk', 'load_dwh', 3, 10, 30), r('crm_gunluk', 'api_cek', 0, 10, 25)]}
        p, done = self.run_agent('yeniden-deneme-politikasi', '--yes', answers=[bad, GOOD['yeniden-deneme-politikasi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- satis_gunluk.load_dwh: 30 · 0 · 2 · ', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('zaman aşımı 5 dk, en uzun başarılı deneme 5.9 dk; en az 7.1 dk olmalı', 'load_dwh: hiçbir çalışma yeniden denemeyle kurtulmamış, 2 kalıcı hata var',
                     'api_cek: 6 çalışma yeniden denemeyle kurtulmuş; retries ≥ 1 olmalı', 'ayarı yazılmayan görev: satis_gunluk.refresh_pbi'):
            self.assertIn(text, retry)
        py = p.out('retry_ayarlari.py').read_text(encoding='utf-8')
        compile(py, 'retry_ayarlari.py', 'exec')
        self.assertIn('"crm_gunluk.api_cek": {"retries": 3, "retry_delay": timedelta(minutes=10), "retry_exponential_backoff": True, "execution_timeout": timedelta(minutes=25)}', py)

    def test_autovacuum_trigger_fits_daily_churn(self):
        v = lambda t, sf, th, a: {'tablo': t, 'vacuum_scale_factor': sf, 'vacuum_threshold': th, 'analyze_scale_factor': a}
        bad = {'tablolar': [v('public.siparis', 0.2, 50, 0.1), v('public.oturum', 0.001, 50, 0.05), v('public.urun', 0.5, 50, 0.01), v('public.audit_log', 0.2, 50, 0.1)]}
        p, done = self.run_agent('autovacuum-ayarlayici', '--yes', answers=[bad, GOOD['autovacuum-ayarlayici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('public.siparis: vacuum eşiği 9,600,050 ölü satır, günlük ölü satır 1,200,000', 'public.oturum: vacuum eşiği 2,550, saatlik ölü satır 100,000',
                     'public.urun: scale_factor 0–0,2 arasında olmalı', 'ayarı yazılmayan tablo: public.ayarlar'):
            self.assertIn(text, retry)
        sql = p.out('autovacuum.sql').read_text(encoding='utf-8')
        self.assertIn('ALTER TABLE public.siparis SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 10000, autovacuum_analyze_scale_factor = 0.01);', sql)
        rows = {r['tablo']: r for r in p.csv('autovacuum_plani.csv')}
        self.assertEqual((rows['public.siparis']['tetik_olu_satir'], rows['public.siparis']['gunde_tahmini_vacuum']), ('490000', '2.4'))

    def test_chunk_size_is_chosen_from_measured_coverage(self):
        p, done = self.run_agent('parca-boyutu-secici', '--yes', answers=[{'parca_boyu': 200, 'ortusme': 20}, GOOD['parca-boyutu-secici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('400 · 40 · 100.0', sent)
        self.assertNotIn('Hijyen ürünleri', sent)  # document text is not sent
        self.assertIn('200/20 ayarında cevapların %67’i tek parçaya sığıyor (gereken %95)', self.model.requests[-1]['body']['messages'][-1]['content'])
        cfg = p.json('parcalama.json')
        self.assertEqual((cfg['parca_boyu_karakter'], cfg['ortusme_karakter'], cfg['kapsama']), (400, 40, 1.0))
        self.assertEqual(len(p.csv('kapsama_tablosu.csv')), 28)

    def test_expense_rules_are_applied_in_code(self):
        a = lambda d, k, g='': {'aciklama': d, 'kategori': k, 'gerekce': g}
        bad = {'atamalar': [x if x['aciklama'] != 'akşam yemeği' else a('akşam yemeği', 'musteri_agirlama') for x in GOOD['masraf-politika-denetcisi']['atamalar']] + []}
        bad['atamalar'] = [x if x['aciklama'] != 'Taksi havalimanı' else a('Taksi havalimanı', 'taksi') for x in bad['atamalar']]
        p, done = self.run_agent('masraf-politika-denetcisi', '--yes', answers=[bad, GOOD['masraf-politika-denetcisi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertNotIn('Ayşe K.', sent)
        self.assertNotIn('3.200,00', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('“Taksi havalimanı”: taksi politikada yok', retry)
        self.assertIn('aynı açıklama farklı kategoriye gitmiş: aksam yemegi', retry)
        issues = sorted((r['personel'], r['sorun'].split(' (')[0]) for r in p.csv('ihlaller.csv'))
        self.assertEqual(issues, [('Ayşe K.', 'yemek kişi-gün limiti 750 aşıldı'), ('Mehmet D.', 'kapsam dışı: Trafik cezaları şirketçe karşılanmaz.'),
                                  ('Mehmet D.', 'konaklama kişi-gün limiti 3500 aşıldı'), ('Zeynep A.', 'fiş yok'), ('Zeynep A.', 'fiş yok'),
                                  ('Zeynep A.', 'kapsam dışı: Kişisel harcama.')])

    def test_period_files_are_merged_with_consistent_types(self):
        bad = json.loads(json.dumps(GOOD['baslik-birlestirici']))
        bad['eslemeler']['2026_09_sales.csv'].update({'Discount': None, 'Sales Rep': 'iskonto'})
        p, done = self.run_agent('baslik-birlestirici', '--yes', answers=[bad, GOOD['baslik-birlestirici']])
        self.ok(done)
        self.assertIn('farklı tipte veriyi aynı kolona bağlıyor: iskonto (2026_08_satis.csv: sayi, 2026_09_sales.csv: metin)', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = p.csv('birlesik.csv')
        self.assertEqual(len(rows), 9)
        self.assertEqual([r['musteri'] for r in rows if r['kaynak_dosya'] == '2026_09_sales.csv'], ['Ada Gıda', 'Ege Tekstil', 'Deniz Lojistik'])
        self.assertEqual(rows[0]['kanal'], '')  # July has no channel column
        self.assertIn('2026_07_satis.csv: Not', p.out('baslik-birlestirme.md').read_text(encoding='utf-8'))

    def test_purchase_orders_follow_reorder_point_and_pack_rules(self):
        o = lambda u, q: {'urun': u, 'miktar': q}
        bad = {'hizmet_seviyeleri': {'A': 0.98, 'B': 0.95, 'C': 0.9}, 'siparisler': [o('Dizüstü Bilgisayar', 95), o('Kablosuz Fare', 100), o('Kulaklık', 300), o('USB Bellek', 1500)]}
        p, done = self.run_agent('siparis-onerici', '--yes', answers=[bad, GOOD['siparis-onerici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('Dizüstü Bilgisayar (95): 10 koli katı değil', 'Kablosuz Fare: pozisyon 380 > ROP 332; bu dönem sipariş gerekmez',
                     'Monitör: pozisyon 60 ≤ ROP 145; sipariş gerekli', 'USB Bellek (1500): sipariş sonrası 1710, hedefi (717) gereğinden çok aşıyor'):
            self.assertIn(text, retry)
        rows = {r['urun']: r for r in p.csv('siparis_onerileri.csv')}
        self.assertEqual((rows['Kulaklık']['rop'], rows['Kulaklık']['emniyet_stoku'], rows['Klavye']['siparis']), ('419', '69', '0'))
        p2, done = self.run_agent('siparis-onerici', '--yes', answers=[{**GOOD['siparis-onerici'], 'hizmet_seviyeleri': {'A': 0.9, 'B': 0.95, 'C': 0.9}}] * 2)
        self.assertIn('A ≥ B ≥ C', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_cost_anomaly_explanation_matches_contributors(self):
        bad = {'anomaliler': [{'tarih': '2026-09-18', 'ana_servis': 'Amazon EC2', 'ana_kaynak': 'glue-pazarlama', 'aciklama': 'Maliyet 1500 oldu.', 'aksiyon': 'x'}]}
        p, done = self.run_agent('maliyet-anomali-aciklayici', '--yes', answers=[bad, GOOD['maliyet-anomali-aciklayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('2026-09-18: maliyet 1072.36, beklenen (medyan) 787.28, fark 285.08 (%36.2)', sent)
        self.assertIn('servis katkısı: AWS Glue +294.86', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('artışa en çok katkı yapan kaynak glue-pazarlama değil', 'artışa en çok katkı yapan servis Amazon EC2 değil', 'hesaplanmayan sayılar: 1500'):
            self.assertIn(text, retry)
        rows = p.csv('anomaliler.csv')
        self.assertEqual([(r['tarih'], r['ana_kaynak'], r['kaynak_katkisi']) for r in rows], [('2026-09-18', 'glue-satis', '172.26')])

    def test_valid_ranges_must_exclude_outliers(self):
        k = lambda c, lo, hi: {'kolon': c, 'min': lo, 'max': hi}
        bad = {'kolonlar': [k('fiyat', 0.01, None), k('agirlik_kg', 0, None), k('stok', 0, None), k('indirim_orani', 0, 100), k('garanti_ay', 0, 60), k('urun_kodu', 0, 1)]}
        p, done = self.run_agent('gecerli-aralik-onerici', '--yes', answers=[bad, GOOD['gecerli-aralik-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- agirlik_kg: 200 · 0.22 · ', sent)
        self.assertNotIn('U0089', sent)  # rows are not sent
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('fiyat: aralık uç değerleri kabul ediyor (2035.83, 2193.44, 3210.61', 'agirlik_kg: aralık uç değerleri kabul ediyor (9999; Tukey dış sınırı -15.9–27.5)',
                     'kolon “urun_kodu” profilde yok'):
            self.assertIn(text, retry)
        self.assertEqual(sorted((r['anahtar'], r['kolon']) for r in p.csv('aralik_ihlalleri.csv')),
                         [('U0018', 'fiyat'), ('U0065', 'garanti_ay'), ('U0089', 'agirlik_kg'), ('U0121', 'indirim_orani'), ('U0152', 'stok')])
        self.assertIn('CHECK (indirim_orani IS NULL OR (indirim_orani >= 0 AND indirim_orani <= 100))', p.out('aralik_kontrolleri.sql').read_text(encoding='utf-8'))
        self.assertEqual(len(p.json('aralik_kurallari.json')['expectations']), 5)

    def test_hypothesis_test_choice_follows_assumptions(self):
        p, done = self.run_agent('hipotez-testi-secici', '--yes', answers=[{'hedef': 'cozum_suresi_saat', 'grup': 'kanal', 'test': 'anova'}, GOOD['hipotez-testi-secici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('çarpık gruplar: canli_destek (3.35), eposta (2.18), telefon (2.16)', retry)
        self.assertIn('en büyük/en küçük varyans oranı 12.5', retry)
        res = p.json('test_sonucu.json')
        self.assertEqual((res['test'], res['deger'], res['sd']), ('kruskal', 71.0119, 2))  # scipy.stats.kruskal: H = 71.0119
        self.assertIn(['canli_destek – telefon', 1736.5, 0.003, 0.009, 'anlamlı (p<0,05)'], res['ikili'])
        q = 'Yeniden açılma kanala bağlı mı?'
        p2, done = self.run_agent('hipotez-testi-secici', '--yes', '--question', q,
                                  answers=[{'hedef': 'yeniden_acildi', 'grup': 'kanal', 'test': 'mann_whitney'}, {'hedef': 'yeniden_acildi', 'grup': 'kanal', 'test': 'ki_kare'}])
        self.ok(done)
        self.assertIn('mann_whitney sayısal hedef ister; yeniden_acildi kategorik', self.model.requests[-1]['body']['messages'][-1]['content'])
        res = p2.json('test_sonucu.json')
        self.assertEqual((res['test'], res['deger'], res['p'], res['soru']), ('ki_kare', 0.428, 0.807276, q))  # scipy chi2_contingency: 0.4282, p 0.8073

    def test_watermark_covers_measured_lateness(self):
        w = lambda k, wm, al: {'kaynak': k, 'watermark_sn': wm, 'izinli_gecikme_sn': al}
        p, done = self.run_agent('filigran-onerici', '--yes', answers=[{'kaynaklar': [w('mobil', 30, 0), w('pos', 60, 0), w('web', 15, 10)]}, GOOD['filigran-onerici']])
        self.ok(done)
        self.assertIn('- mobil: 477 · 6.0/25.4/279.3/843.9/873.5/883.9 · 886.0', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('mobil: watermark’tan sonra gelen olay payı %8.8 (en fazla %5)', 'mobil: watermark + izinli gecikmeden sonra gelip atılan olay payı %8.81',
                     'pos: watermark 60 sn, p95 gecikme 4.2 sn; 2×p95’ten uzun'):
            self.assertIn(text, retry)
        self.assertIn("WATERMARK FOR olay_zamani AS olay_zamani - INTERVAL '300' SECOND", p.out('watermark.sql').read_text(encoding='utf-8'))
        self.assertIn('mobil_df = mobil_df.withWatermark("olay_zamani", "900 seconds")', p.out('spark_watermark.py').read_text(encoding='utf-8'))
        rows = {r['kaynak']: r for r in p.csv('filigran_plani.csv')}
        self.assertEqual((rows['mobil']['gec_olay_orani'], rows['mobil']['atilan_olay_orani']), ('0.0482', '0.0'))

    def test_incremental_strategy_is_replayed_over_loads(self):
        m = lambda t, mat, key, st, lb: {'tablo': t, 'materialized': mat, 'unique_key': key, 'strateji': st, 'geriye_bakis_gun': lb}
        bad = {'modeller': [m('magazalar', 'incremental', ['magaza_id'], 'merge', 0), m('siparisler', 'incremental', ['siparis_id', 'durum'], 'merge', 3),
                            m('web_olaylari', 'incremental', None, 'append', 0)]}
        p, done = self.run_agent('incremental-strateji-onerici', '--yes', answers=[bad, GOOD['incremental-strateji-onerici']])
        self.ok(done)
        self.assertIn('siparis_id değerlerinin 181/630 kadarı birden fazla yüklemede geliyor', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        # dbt-duckdb replay of the same configs gives the same counts (append without lookback keeps 1411 of 1540 events)
        for text in ('magazalar: 84 satırlık küçük tablo', 'siparisler: merge + 3 gün geriye bakış 7 yükleme simülasyonunda 214 yinelenen, 0 eksik',
                     'web_olaylari: append + 0 gün geriye bakış 7 yükleme simülasyonunda 0 yinelenen, 129 eksik'):
            self.assertIn(text, retry)
        sql = p.out('models/siparisler.sql').read_text(encoding='utf-8')
        self.assertIn("{{ config(materialized='incremental', incremental_strategy='merge', unique_key='siparis_id', on_schema_change='append_new_columns') }}", sql)
        self.assertIn("where guncellenme > (select max(guncellenme) from {{ this }}) - interval '3 days'", sql)
        self.assertIn("materialized='table'", p.out('models/magazalar.sql').read_text(encoding='utf-8'))
        self.assertIn('      - name: web_olaylari', p.out('sources.yml').read_text(encoding='utf-8'))

    def test_medallion_assignment_passes_layer_rules(self):
        no_bridge = {'tablolar': GOOD['katman-atayici']['tablolar']}
        p, done = self.run_agent('katman-atayici', '--yes', answers=[no_bridge, GOOD['katman-atayici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('pazarlama.kampanya → mart.kampanya_performans (bronze.pazarlama_kampanya → gold.kampanya_performans): katman atlama', retry)
        rows = {r['eski_ad']: r for r in p.csv('katman_haritasi.csv')}
        self.assertEqual(len(rows), 19)
        self.assertEqual(rows['web.olay']['yeni_ad'], 'bronze.web_olay')
        self.assertIn('CREATE TABLE IF NOT EXISTS silver.kampanya AS SELECT * FROM bronze.pazarlama_kampanya;', p.out('gecis.sql').read_text(encoding='utf-8'))
        self.assertIn('["silver.kampanya"]:::silver', p.out('soy.mmd').read_text(encoding='utf-8'))
        bronze = [t if t['tablo'] != 'stg.web_olay' else {**t, 'katman': 'bronze', 'yeni_ad': 'bronze.stg_web_olay'} for t in GOOD['katman-atayici']['tablolar']]
        self.run_agent('katman-atayici', '--yes', answers=[{**GOOD['katman-atayici'], 'tablolar': bronze}] * 2)
        self.assertIn('stg.web_olay başka tablolardan besleniyor (web.olay); bronze ham veridir', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_connection_pools_fit_the_database_budget(self):
        h = lambda s, mx, w: {'servis': s, 'havuz_max': mx, 'havuz_min': 1, 'baglanti_bekleme_ms': w}
        bad = {'servisler': [h('siparis-api', 50, 30000), h('odeme-api', 4, 30), h('katalog-api', 30, 30000), h('raporlama', 40, 30000), h('bildirim-worker', 25, 30000)]}
        p, done = self.run_agent('baglanti-havuzu-boyutlandirici', '--yes', answers=[bad, GOOD['baglanti-havuzu-boyutlandirici']])
        self.ok(done)
        self.assertIn('- odeme-api: 2 · 60 · 4 · 9/40 · 1.08/4.80 · 20', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('siparis-api: instance başına havuz 50, gereken 2.97; 11 üstü boşta bağlantı tutar', 'odeme-api: instance başına havuz 4, tepe yükte p95 eşzamanlılık 4.80; en az 5 olmalı',
                     'odeme-api: bağlantı bekleme 30 ms', 'toplam bağlantı 388 (havuz × instance), kullanılabilir 90'):
            self.assertIn(text, retry)
        self.assertIn('    maximum-pool-size: 15\n    minimum-idle: 2\n    connection-timeout: 10000', p.out('havuz_ayarlari.yaml').read_text(encoding='utf-8'))
        self.assertEqual(sum(int(r['toplam_baglanti']) for r in p.csv('havuz_plani.csv')), 67)

    def test_geofence_radius_covers_visits_without_overlap(self):
        g = lambda m, r: {'magaza': m, 'yaricap_m': r}
        bad = {'magazalar': [g('MG01', 400), g('MG02', 400), g('MG03', 120), g('MG04', 290), g('MG05', 280), g('MG06', 270)]}
        p, done = self.run_agent('geofence-yaricap-onerici', '--yes', answers=[bad, GOOD['geofence-yaricap-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- MG01 (Kadıköy Çarşı): 80 · 42/108/146/381 · MG02 697 m', sent)
        self.assertNotIn('cihaz-', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('MG01: 400 m, p90 mesafesinin (146 m) 1,5 katını aşıyor', 'MG03: 120 m yarıçapın kapsadığı ziyaret pingi payı %56 (en az %85)',
                     'MG01 (400 m) ile MG02 (400 m) çakışıyor; mağazalar arası 697 m'):
            self.assertIn(text, retry)
        gj = p.json('geofence.geojson')
        self.assertEqual([len(f['geometry']['coordinates'][0]) for f in gj['features']], [33] * 6)
        self.assertEqual({r['magaza']: r['kapsama'] for r in p.csv('geofence_plani.csv')}['MG01'], '0.925')

    def test_threshold_choice_is_checked_against_cost_curve(self):
        bad = {'esik': 0.6, 'aciklama': '0,6 eşiği F1 açısından en iyisi; maliyet yaklaşık 5000 ve 3500 yanlış alarm önleniyor.'}
        p, done = self.run_agent('esik-secici', '--yes', answers=[bad, GOOD['esik-secici']])
        self.ok(done)
        self.assertIn('0.45 · 218 · 99 · 119 · 12 · 0.4541 · 0.8919 · 0.6018 · 4180', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('eşik 0.6: maliyet 5210, kısıtlar altındaki en düşük maliyet 4180 (eşik 0.45)', retry)
        self.assertIn('açıklamada taramada olmayan sayılar: 5000, 3500', retry)
        res = p.json('esik.json')
        self.assertEqual((res['esik'], res['metrikler']['maliyet'], res['en_dusuk_maliyetli_esik']), (0.45, 4180, 0.45))
        self.assertEqual(len(p.csv('esik_taramasi.csv')), 91)
        p2, done = self.run_agent('esik-secici', '--yes', '--max-alerts', '150', answers=[GOOD['esik-secici']] * 2)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('eşik 0.45: 218 alarm, kapasite 150', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_k8s_requests_follow_usage_percentiles(self):
        k = lambda d, cr, mr, ml, cl=None: {'deployment': d, 'cpu_request_m': cr, 'cpu_limit_m': cl, 'mem_request_mib': mr, 'mem_limit_mib': ml}
        bad = {'deploymentlar': [k('siparis-api', 100, 640, 800), k('sepet-api', 230, 400, 512, 300), k('arama', 1800, 2700, 3072), k('resim-isleyici', 'x', 512, 1024),
                                 k('etl-worker', 800, 1600, 2048), k('bildirim', 500, 100, 256)]}
        p, done = self.run_agent('k8s-kaynak-ayarlayici', '--yes', answers=[bad, GOOD['k8s-kaynak-ayarlayici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('siparis-api: CPU request 100m, medyan kullanım 180m', 'sepet-api: CPU limiti 300m, en yüksek kullanım 380m; throttling olur',
                     'arama: bellek limiti 3072 MiB, en yüksek kullanım 3100 MiB; en az 3410 MiB olmalı (OOMKilled)', 'bildirim: CPU request 500m, p95 kullanım 45m; en fazla 58m olmalı',
                     'bildirim: bellek request 100 MiB, p95 kullanım 120 MiB', 'resim-isleyici: değerler tam sayı'):
            self.assertIn(text, retry)
        self.assertNotIn('ayarı yazılmayan', retry)  # an invalid answer is not also reported as missing
        rows = {r['deployment']: r for r in p.csv('kaynak_plani.csv')}
        self.assertEqual((rows['etl-worker']['aylik_once'], rows['etl-worker']['aylik_sonra']), ('128.0', '25.47'))
        self.assertEqual(round(sum(float(r['fark']) for r in rows.values()), 2), -270.84)
        self.assertIn('                memory: 3500Mi', p.out('kaynak_yamalari.yaml').read_text(encoding='utf-8'))

    def test_api_pull_plan_fits_rate_limit_and_window(self):
        e = lambda u, m, s, r: {'uc_nokta': u, 'mod': m, 'sayfa_boyutu': s, 'dakikada_istek': r}
        bad = {'uc_noktalar': [e('/musteriler', 'artimli', 500, 30), e('/siparisler', 'tam', 1000, 40), e('/urunler', 'artimli', 200, 10), e('/faturalar', 'artimli', 500, 3),
                               e('/stok', 'tam', 2000, 5), e('/kampanyalar', 'tam', 100, 2)]}
        p, done = self.run_agent('toplu-cekim-planlayici', '--yes', answers=[bad, GOOD['toplu-cekim-planlayici']])
        self.ok(done)
        self.assertIn('- /siparisler: 2600000 · 1000 · 1400 · evet · 52000 · 2600 · 52', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('/siparisler: artımlı çekim destekleniyor; tam çekim 2600 istek, artımlı 52 istek', '/siparisler: 2600 istek dakikada 40 istekle 65.0 dk sürer; pencere 30 dk',
                     '/urunler: API artımlı çekimi (updated_since) desteklemiyor', '/stok: sayfa boyutu 1–1000 olmalı', 'dakikalık istek payları toplamı 75, API hız sınırı 60'):
            self.assertIn(text, retry)
        self.assertNotIn('planı yazılmayan', retry)
        rows = {r['uc_nokta']: r for r in p.csv('cekim_plani.csv')}
        self.assertEqual((rows['/urunler']['istek'], rows['/urunler']['sure_dk']), ('210', '21.0'))
        self.assertIn('  - yol: "/siparisler"\n    mod: artimli', p.out('cekim_plani.yaml').read_text(encoding='utf-8'))

    def test_backfill_plan_is_simulated_with_shared_slots(self):
        d = lambda g, m, s, o: {'dag': g, 'max_active_runs': m, 'sira': s, 'oncelik': o}
        bad = {'daglar': [d('musteri_bakiye', 2, 'eskiden_yeniye', 1), d('kpi_ozet', 1, 'yeniden_eskiye', 2), d('satis_gunluk', 2, 'eskiden_yeniye', 3), d('stok_snapshot', 2, 'eskiden_yeniye', 4)]}
        p, done = self.run_agent('backfill-planlayici', '--yes', answers=[bad, GOOD['backfill-planlayici']])
        self.ok(done)
        self.assertIn('- musteri_bakiye: 15 · 2026-09-01–2026-09-29 · 12.1 · 181 · evet', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('musteri_bakiye: depends_on_past=True; her bölüm bir öncekini bekler', retry)
        self.assertIn('kpi_ozet: depends_on_past=True', retry)
        sh = p.out('backfill.sh').read_text(encoding='utf-8')
        self.assertIn('airflow backfill create --dag-id stok_snapshot --from-date 2026-09-01 --to-date 2026-10-10 --max-active-runs 2 --reprocess-behavior none --run-backwards', sh)
        rows = p.csv('backfill_takvimi.csv')
        self.assertEqual(len(rows), 75)
        self.assertEqual(max(float(r['bitis_dk']) for r in rows), 255.5)
        serial = [r for r in rows if r['dag'] == 'musteri_bakiye']
        self.assertTrue(all(float(a['bitis_dk']) <= float(b['baslangic_dk']) for a, b in zip(serial, serial[1:])))
        slow = {'daglar': [d(g, 1, 'eskiden_yeniye', i) for i, g in enumerate(('satis_gunluk', 'stok_snapshot', 'musteri_bakiye', 'kpi_ozet'), 1)]}
        self.run_agent('backfill-planlayici', '--yes', answers=[slow, GOOD['backfill-planlayici']])
        self.assertIn('simülasyonda backfill 6.31 saat sürüyor, pencere 4.5 saat; DAG bitişleri: satis_gunluk 6.31 sa', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_budget_phasing_follows_history_and_notes(self):
        bad = json.loads(json.dumps(GOOD['butce-fazlandirici']))
        bad['hesaplar'][0]['istisna_aylar'] = []
        bad['hesaplar'][1].update({'agirliklar': [round(1 / 12, 6)] * 11 + [round(1 - 11 * round(1 / 12, 6), 6)], 'istisna_aylar': [7]})
        bad['hesaplar'][3]['agirliklar'] = bad['hesaplar'][3]['agirliklar'][:11]
        p, done = self.run_agent('butce-fazlandirici', '--yes', answers=[bad, GOOD['butce-fazlandirici']])
        self.ok(done)
        self.assertIn('- 770 Genel Yönetim Giderleri: 1,200,000 · 8.2/8.1/', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('770 Ocak: pay %19.7, geçen yıl %8.2', '760: Temmuz istisna denmiş ama hesabın notunda bu ay geçmiyor', '760 Kasım: pay %8.3, geçen yıl %16.1',
                     '621: 12 negatif olmayan ağırlık gerekli'):
            self.assertIn(text, retry)
        rows = p.csv('aylik_butce.csv')
        self.assertEqual(len(rows), 48)
        totals = {}
        for r in rows:
            totals[r['hesap']] = round(totals.get(r['hesap'], 0) + float(r['tutar']), 2)
        self.assertEqual(totals, {'770': 1200000.0, '760': 900000.0, '600': 24000000.0, '621': 14400000.0})
        self.assertEqual(next(r['tutar'] for r in rows if (r['hesap'], r['donem']) == ('770', '2026-01')), '235994.4')

    def test_k_anonymity_is_reached_with_minimal_generalisation(self):
        a = lambda c, t, d=0: {'kolon': c, 'tip': t, 'deger': d}
        coarse = {'kolonlar': [a('yas', 'aralik', 10), a('cinsiyet', 'gizle'), a('posta_kodu', 'gizle'), a('meslek', 'gizle')]}
        p, done = self.run_agent('k-anonimlik-genellestirici', '--yes', answers=[coarse, GOOD['k-anonimlik-genellestirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- posta_kodu: metin · 14 · en fazla 5 karakter', sent)  # 06420 is a code, not a number
        self.assertNotIn('P0001', sent)
        self.assertIn('yas (aralik 10 yerine aralik 5 ile de k=5 sağlanıyor, bastırma %0.0); cinsiyet (gizle yerine koru ile de k=5 sağlanıyor, bastırma %1.0)',
                      self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = p.csv('anonim_veri.csv')
        self.assertEqual(len(rows), 394)
        self.assertNotIn('katilimci_id', rows[0])
        from collections import Counter
        classes = Counter((r['yas'], r['cinsiyet'], r['posta_kodu'], r['meslek']) for r in rows)
        self.assertGreaterEqual(min(classes.values()), 5)
        self.assertEqual(p.json('genellestirme.json')['bastirilan_satir'], 6)
        raw = {'kolonlar': [a('yas', 'koru'), a('cinsiyet', 'koru'), a('posta_kodu', 'onek', 3), a('meslek', 'koru')]}
        self.run_agent('k-anonimlik-genellestirici', '--yes', answers=[raw, GOOD['k-anonimlik-genellestirici']])
        self.assertIn('364 eşdeğerlik sınıfından 362 tanesi 5 kişiden küçük; bastırılacak satır payı %97.5', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_load_order_respects_foreign_keys_and_breaks_cycles(self):
        bad = {'sira': ['ulke', 'sehir', 'urun', 'departman', 'calisan', 'siparis', 'musteri', 'siparis_kalem', 'fatura'],
               'ertelenen': [{'tablo': 'departman', 'kolon': 'mudur_id'}, {'tablo': 'siparis', 'kolon': 'musteri_id'}, {'tablo': 'fatura', 'kolon': 'siparis_id'}]}
        p, done = self.run_agent('yukleme-sirasi-planlayici', '--yes', answers=[bad, GOOD['yukleme-sirasi-planlayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        for text in ('- musteri.sehir_id → sehir · evet', '- departman.mudur_id → calisan · evet', '- siparis.musteri_id → musteri · hayır (NOT NULL)'):
            self.assertIn(text, sent)  # one-line CREATE TABLE and ALTER TABLE foreign keys are read
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('siparis.musteri_id NOT NULL; ertelenemez', 'fatura.siparis_id → siparis bir döngüde değil; ertelemek gereksiz',
                     'calisan.yonetici_id tablonun kendisine referans veriyor; ertelenmeli'):
            self.assertIn(text, retry)
        rows = {r['tablo']: r for r in p.csv('yukleme_sirasi.csv')}
        self.assertEqual([rows[t]['dalga'] for t in ('ulke', 'urun', 'departman', 'calisan', 'fatura')], ['1', '1', '3', '4', '7'])
        self.assertIn('ALTER TABLE departman ADD CONSTRAINT fk_departman_mudur_id FOREIGN KEY (mudur_id) REFERENCES calisan;', p.out('yukleme_plani.sql').read_text(encoding='utf-8'))

    def test_aggregate_tables_answer_logged_queries(self):
        o = lambda a, d, m: {'ad': a, 'boyutlar': d, 'olcumler': m}
        bad = {'ozet_tablolar': [o('agg_gun_magaza_kategori', ['tarih', 'magaza', 'kategori'], ['sum(tutar)']), o('agg_ay_bolge', ['ay', 'bolge'], ['count_distinct(musteri)'])]}
        p, done = self.run_agent('ozet-tablo-onerici', '--yes', answers=[bad, GOOD['ozet-tablo-onerici']])
        self.ok(done)
        self.assertIn('İşlevsel bağımlılıklar (x → türetilebilen): tarih → ay; magaza → bolge; urun → kategori', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('agg_gun_magaza_kategori: 5242 satır, olgu tablosunun %87’i (en fazla %20)', retry)
        self.assertIn('agg_ay_bolge: hiçbir sorguyu karşılamıyor', retry)
        route = {r['sorgu']: r['ozet_tablo'] for r in p.csv('sorgu_yonlendirme.csv')}
        self.assertEqual((route['Q01'], route['Q07'], route['Q05']), ('agg_ay_magaza', 'agg_ay_tekil_musteri', ''))
        # the generated SQL runs and the aggregates add up to the fact table
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        cols, facts = tabular.read(p.dir / 'ornek' / 'satis_olgu.csv')
        db.execute(f'CREATE TABLE satis_olgu ({", ".join(cols)})')
        db.executemany(f'INSERT INTO satis_olgu VALUES ({",".join("?" * len(cols))})', [[float(r[c]) if c in ('adet', 'tutar') else r[c] for c in cols] for r in facts])
        db.executescript(p.out('ozet_tablolar.sql').read_text(encoding='utf-8'))
        self.assertEqual(db.execute('SELECT COUNT(*), SUM(satir_sayisi) FROM agg_ay_bolge_kategori_kanal').fetchone(), (432, 6000))
        self.assertAlmostEqual(db.execute('SELECT SUM(tutar_sum) FROM agg_ay_magaza').fetchone()[0], db.execute('SELECT SUM(tutar) FROM satis_olgu').fetchone()[0], places=2)
        self.assertEqual(db.execute("SELECT musteri_count_distinct FROM agg_ay_tekil_musteri WHERE ay = '2026-03'").fetchone(),
                         db.execute("SELECT COUNT(DISTINCT musteri) FROM satis_olgu WHERE ay = '2026-03'").fetchone())

    def test_roles_reproduce_each_users_access_exactly(self):
        bad = json.loads(json.dumps(GOOD['rol-madenciligi']))
        bad['atamalar'][4]['roller'] = ['satis_temel']
        bad['atamalar'][5]['roller'] = ['satis_temel', 'analist_okuma', 'dwh_okuma']
        p, done = self.run_agent('rol-madenciligi', '--yes', answers=[bad, GOOD['rol-madenciligi']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- K6: 1 · Satış 1 · crm.okuma, crm.yazma, dwh.okuma, rapor.satis, siparis.okuma, siparis.yazma', sent)
        self.assertNotIn('@firma.com.tr', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('K5: eksik yetki: rapor.yonetim, siparis.onay', 'K6: fazla yetki (en az yetki ilkesi): fatura.okuma, rapor.finans', 'hiçbir kümeye atanmayan rol: satis_yonetici_ek'):
            self.assertIn(text, retry)
        roles = {}
        for r in p.csv('rol_yetkileri.csv'):
            roles.setdefault(r['rol'], set()).add(r['yetki'])
        got = {}
        for r in p.csv('kullanici_rolleri.csv'):
            got.setdefault(r['kullanici'], set()).update(roles[r['rol']])
        want = {}
        for r in tabular.read(p.dir / 'ornek' / 'kullanici_yetkileri.csv')[1]:
            want.setdefault(r['kullanici'], set()).add(r['yetki'])
        self.assertEqual(got, want)

    def test_retrieval_setting_is_smallest_that_meets_hit_rate(self):
        p, done = self.run_agent('getirme-ayari-onerici', '--yes', answers=[{'top_k': 6, 'min_score': 0}, GOOD['getirme-ayari-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('5 · 0.7 · 0.9 · 0.7175 · 1850', sent)
        self.assertNotIn('dok17#4', sent)  # chunk ids and questions are not sent
        self.assertIn('top_k 5 ile de bulma oranı %90.0; top_k gereğinden büyük', self.model.requests[-1]['body']['messages'][-1]['content'])
        cfg = p.json('getirme_ayari.json')
        self.assertEqual((cfg['top_k'], cfg['min_score'], cfg['bulma_orani'], cfg['ort_baglam_token']), (5, 0.7, 0.9, 1850))
        self.assertEqual(len(p.csv('bulunamayan_sorular.csv')), 4)
        self.assertEqual(len(p.csv('ayar_taramasi.csv')), 60)
        self.run_agent('getirme-ayari-onerici', '--yes', answers=[{'top_k': 3, 'min_score': 0.6}, GOOD['getirme-ayari-onerici']])
        self.assertIn('top_k 3, min_score 0.6: doğru parçanın bağlamda olduğu soru payı %82.5 (en az %90)', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_role_timeouts_spare_normal_queries_and_stop_runaways(self):
        z = lambda r, st, lt, it: {'rol': r, 'statement_timeout_ms': st, 'lock_timeout_ms': lt, 'idle_in_transaction_ms': it}
        bad = {'roller': [z('analist', 7200000, 2000, 600000), z('app_batch', 15000, 5000, 300000), z('app_web', 2000, 3000, 60000), z('rapor', 400000, 3000, 30000)]}
        p, done = self.run_agent('zaman-asimi-onerici', '--yes', answers=[bad, GOOD['zaman-asimi-onerici']])
        self.ok(done)
        self.assertIn('- app_web: 2000 · 35/79/117/150/231 · 93/30000 · 31000', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('analist: statement_timeout 7200000 ms, p99,9 süre 349089 ms; 10 katından uzun', 'analist: elle iptal edilen 3728696 ms’lik sorgu bu sürede de durmazdı',
                     'app_batch: statement_timeout 15000 ms ile kesilecek başarılı sorgu payı %1.67 (en fazla %0.1)', 'app_web: lock_timeout 3000 ms; p99 kilit beklemesi (93 ms)',
                     'rapor: idle_in_transaction 1 dk ile 1 saat arasında olmalı'):
            self.assertIn(text, retry)
        sql = p.out('zaman_asimlari.sql').read_text(encoding='utf-8')
        self.assertIn("ALTER ROLE analist SET statement_timeout = '600000ms';", sql)
        self.assertEqual({r['rol']: r['kesilecek_basarili'] for r in p.csv('zaman_asimi_plani.csv')}, {'analist': '0', 'app_batch': '0', 'app_web': '0', 'rapor': '0'})

    def test_source_freshness_thresholds_are_replayed_on_history(self):
        t = lambda k, w, wp, e, ep: {'kaynak': k, 'warn_after': {'count': w, 'period': wp}, 'error_after': {'count': e, 'period': ep}}
        bad = {'kaynaklar': [t('crm.musteriler', 1, 'week', 2, 'week'), t('erp.siparisler', 1, 'hour', 6, 'hour'), t('finans.kur', 36, 'hour', 30, 'hour'),
                             t('web.olaylar', 30, 'minute', 12, 'hour')]}
        p, done = self.run_agent('kaynak-tazelik-onerici', '--yes', answers=[bad, GOOD['kaynak-tazelik-onerici']])
        self.ok(done)
        self.assertIn('- crm.musteriler: 23 · 31.72/46.59/63.31/67.75', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('crm.musteriler: warn_after ve error_after', 'erp.siparisler: warn 1 sa; geçmiş 720 yükleme aralığının 339 tanesinde uyarı verirdi',
                     'finans.kur: error_after (30 sa) warn_after’dan (36 sa) uzun olmalı', 'web.olaylar: error 12 sa, p95 aralık 0.27 sa'):
            self.assertIn(text, retry)
        self.assertNotIn('eşiği yazılmayan', retry)
        yml = p.out('sources.yml').read_text(encoding='utf-8')
        self.assertIn('  - name: erp\n    loaded_at_field: _yuklenme_zamani\n    tables:\n      - name: siparisler\n        freshness:\n          warn_after: {count: 2, period: hour}', yml)
        self.assertEqual({r['kaynak']: r['gecmiste_uyari'] for r in p.csv('tazelik_plani.csv')}['crm.musteriler'], '0')

    def test_zorder_columns_skip_partitions_and_cover_filters(self):
        t = lambda n, c: {'tablo': n, 'kolonlar': c}
        bad = {'tablolar': [t('satis.siparis', ['siparis_tarihi', 'musteri_id', 'durum']), t('web.olay', ['kullanici_id', 'ulke'])]}
        p, done = self.run_agent('zorder-secici', '--yes', answers=[bad, GOOD['zorder-secici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('satis.siparis.siparis_tarihi bölümleme kolonu', 'satis.siparis.durum yalnızca 5 farklı değer', 'web.olay.ulke yalnızca 40 farklı değer',
                     'web.olay: seçilen kolonlar sorgu çalışmalarının %50 kadarını kapsıyor (en az %80); kapsanmayan: oturum_id (1100)'):
            self.assertIn(text, retry)
        self.assertIn('OPTIMIZE satis.siparis ZORDER BY (musteri_id, magaza_id, urun_id);', p.out('zorder.sql').read_text(encoding='utf-8'))
        self.assertEqual({r['tablo']: r['kapsama'] for r in p.csv('zorder_plani.csv')}, {'satis.siparis': '0.853', 'web.olay': '0.829'})

    def test_survey_weights_match_population_margins(self):
        p, done = self.run_agent('anket-agirliklandirici', '--yes', answers=[{'degiskenler': ['yas_grubu', 'bolge', 'cinsiyet'], 'kirpma_alt': 0.5, 'kirpma_ust': 2},
                                                                          GOOD['anket-agirliklandirici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- yas_grubu (en büyük fark 27.2 puan): 18-34 57.2/30.0, 35-54 29.2/38.0, 55+ 13.7/32.0', sent)
        self.assertNotIn('Y0001', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('belirgin sapan değişken ağırlıklandırmaya alınmamış: egitim (en büyük fark 9.3 puan)', retry)
        self.assertIn('yas_grubu: ağırlıklı paylar nüfustan 8.4 puan sapıyor', retry)
        w = {r['yanit_id']: float(r['agirlik']) for r in p.csv('agirliklar.csv')}
        rows = tabular.read(p.dir / 'ornek' / 'anket_demografik_yanitlar.csv')[1]
        old = sum(w[r['yanit_id']] for r in rows if r['yas_grubu'] == '55+') / sum(w.values())
        self.assertAlmostEqual(old, 0.32, places=2)
        self.assertTrue(0.299 < min(w.values()) and max(w.values()) < 3.01)
        res = p.json('agirliklandirma.json')
        self.assertEqual((res['tasarim_etkisi'], res['memnuniyet']['agirliksiz']), (1.937, 3.033))
        self.assertGreater(res['memnuniyet']['agirlikli'], res['memnuniyet']['agirliksiz'])  # older, non-Marmara respondents are more satisfied

    def test_warehouse_choice_is_near_the_exhaustive_optimum(self):
        p, done = self.run_agent('depo-yeri-secici', '--yes', answers=[{'depolar': ['D-Gebze', 'D-Ankara', 'D-Konya']}, GOOD['depo-yeri-secici']])
        self.ok(done)
        self.assertIn('seçilen depoların kapasitesi 3200 ton, haftalık talep 3435 ton', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = {r['nokta']: r for r in p.csv('depo_atamalari.csv')}
        self.assertEqual((rows['Konya']['depo'], rows['Trabzon']['depo'], rows['İzmir']['depo']), ('D-Antalya', 'D-Samsun', 'D-Manisa'))
        self.assertIn('Haftalık maliyet 238,825 (taramada bulunan en iyi 238,825', p.out('depo-secimi.md').read_text(encoding='utf-8'))
        self.assertEqual(len(p.json('depolar.geojson')['features']), 8 + 15)
        everything = {'depolar': ['D-Gebze', 'D-Ankara', 'D-Manisa', 'D-Konya', 'D-Adana', 'D-Samsun', 'D-Kayseri', 'D-Antalya']}
        self.run_agent('depo-yeri-secici', '--yes', answers=[everything, GOOD['depo-yeri-secici']])
        self.assertIn('haftalık maliyet 254,441, bulunan en iyi çözüm 238,825', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_dedup_window_is_replayed_on_delivery_log(self):
        p, done = self.run_agent('tekrar-penceresi-onerici', '--yes', answers=[{'anahtar': ['islem_no'], 'pencere_sn': 60}, GOOD['tekrar-penceresi-onerici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('Aday anahtar sütunları (aynı değeri paylaşan farklı olay sayısı): olay_id 0, islem_no 888, tutar 0', sent)
        self.assertIn('p50/p90/p99/p99,5/max: 21.6/78.8/253.0/253.1/253.2', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('(islem_no) anahtarı 20 farklı olayı yinelenen sayıp atıyor', retry)
        self.assertIn('60 sn pencere ile yakalanan yinelenen teslim payı %88.9 (en az %99.5); 11 yinelenen geçiyor', retry)
        self.assertIn("SET 'table.exec.state.ttl' = '300000 ms';", p.out('tekillestirme.sql').read_text(encoding='utf-8'))
        self.assertEqual(p.json('tekrar_plani.json')['yakalanan_oran'], 1.0)
        self.run_agent('tekrar-penceresi-onerici', '--yes', answers=[{'anahtar': ['olay_id'], 'pencere_sn': 600}, GOOD['tekrar-penceresi-onerici']])
        self.assertIn('pencere boyunca ~350 anahtar tutulur', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_survivorship_rules_are_scored_on_approved_records(self):
        a = lambda f, k, o=None: {'alan': f, 'kural': k, 'kaynak_sirasi': o or []}
        bad = {'alanlar': [a('ad', 'en_guncel'), a('telefon', 'en_guncel'), a('eposta', 'en_guncel'), a('adres', 'kaynak_onceligi', ['erp', 'crm', 'web'])]}
        p, done = self.run_agent('altin-kayit-kurali', '--yes', answers=[bad, GOOD['altin-kayit-kurali']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- adres: en_guncel %75, en_sik %85, en_uzun %50 · kaynaklar: crm dolu %100, doğru %100; erp dolu %100, doğru %40', sent)
        self.assertNotIn('@ornek.com.tr', sent)
        self.assertNotIn('Cad.', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('telefon: en_guncel onaylı kümelerde %75 doğru; en iyi kural en_uzun %100', retry)
        self.assertIn('adres: kaynak_onceligi erp>crm>web onaylı kümelerde %40 doğru', retry)
        self.assertNotIn('eposta:', retry)
        golden = p.csv('altin_kayitlar.csv')
        self.assertEqual(len(golden), 60)
        approved = {(r['kume'], r['alan']): r['onayli_deger'] for r in tabular.read(p.dir / 'ornek' / 'altin_kayit_onayli.csv')[1]}
        self.assertTrue(all(g['adres'] == approved[(g['kume'], 'adres')] for g in golden if (g['kume'], 'adres') in approved))
        self.assertIn('  telefon: {kural: kaynak_onceligi, kaynak_sirasi: [crm, erp, web]}', p.out('kurallar.yaml').read_text(encoding='utf-8'))

    def test_log_sampling_keeps_errors_and_fits_budget(self):
        p, done = self.run_agent('log-ornekleme-onerici', '--yes', answers=[{'oranlar': [{**g, 'oran': 0.5} for g in GOOD['log-ornekleme-onerici']['oranlar']]},
                                                                         GOOD['log-ornekleme-onerici']])
        self.ok(done)
        self.assertIn('- ingress/INFO: 120,000,000 · 58.11', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('siparis-api/ERROR: uyarı ve hata logları örneklenmez (oran 1 olmalı)', retry)
        p2, done = self.run_agent('log-ornekleme-onerici', '--yes', answers=[{'oranlar': [{**g, 'oran': 1} for g in GOOD['log-ornekleme-onerici']['oranlar']]},
                                                                          GOOD['log-ornekleme-onerici']])
        self.assertIn('günlük hacim 102.5 GB, bütçe 15 GB', self.model.requests[-1]['body']['messages'][-1]['content'])
        rows = {(r['servis'], r['seviye']): r for r in p.csv('ornekleme_plani.csv')}
        self.assertEqual(rows[('ingress', 'INFO')]['gunluk_satir_sonra'], '12000000')
        self.assertEqual(round(sum(float(r['gb_sonra']) for r in rows.values()), 1), 12.3)
        yml = p.out('otel_ornekleme.yaml').read_text(encoding='utf-8')
        self.assertIn('  probabilistic_sampler/ingress_info:\n    sampling_percentage: 10', yml)
        self.assertIn('  filter/katalog_api_debug:', yml)

    def test_promotion_decision_follows_interval_and_segments(self):
        bad = {'karar': 'terfi', 'aciklama': 'Aday model AUC’yi yüzde 12 artırıyor, hemen canlıya alınmalı.'}
        p, done = self.run_agent('model-terfi-karari', '--yes', answers=[bad, GOOD['model-terfi-karari']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('AUC mevcut 0.8368, aday 0.8838, fark 0.0470; eşleştirilmiş bootstrap %95 güven aralığı [0.0338, 0.0601]', sent)
        self.assertIn('- kurumsal: 144 · 23 · 0.7961 · 0.5771 · -0.219', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        self.assertIn('karar “terfi” olamaz; güven aralığı [0.0338, 0.0601], kötüleşen segment: kurumsal → “bekle”', retry)
        self.assertIn('açıklamada tabloda olmayan sayılar: 12', retry)
        res = p.json('terfi_karari.json')
        self.assertEqual((res['karar'], res['kotulesen_segmentler']), ('bekle', ['kurumsal']))
        import random as _r
        rows = tabular.read(p.dir / 'ornek' / 'model_karsilastirma.csv')[1]
        from ml import auc
        self.assertAlmostEqual(auc([int(r['gercek']) for r in rows], [float(r['skor_aday']) for r in rows]), 0.8838, places=4)

    def test_null_policy_is_checked_against_the_data(self):
        p_ = lambda c, p, d=None: {'kolon': c, 'politika': p, 'varsayilan': d}
        bad = {'kolonlar': [p_('siparis_no', 'izin'), p_('musteri_no', 'zorunlu'), p_('siparis_tarihi', 'zorunlu'), p_('kanal', 'zorunlu'), p_('kargo_ucreti', 'izin'),
                            p_('para_birimi', 'varsayilan', 'USD'), p_('kupon_kodu', 'izin'), p_('tutar', 'zorunlu'), p_('durum', 'zorunlu')]}
        p, done = self.run_agent('bos-deger-politikasi', '--yes', answers=[bad, GOOD['bos-deger-politikasi']])
        self.ok(done)
        self.assertIn('- kargo_ucreti: sayi · 86 · 204 · hayır · 0.03–39.95 · %100 kanal=magaza', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('siparis_no: her satırda tekil, anahtar sütun; boşa izin verilemez', 'kargo_ucreti: boşların %100 kadarı kanal=magaza satırlarında; politikanın gerekçesini yaz',
                     'para_birimi: varsayılan “USD” mevcut değerlerden biri olmalı (EUR, TRY)', 'durum: 6 boş satır var; varsayılansız NOT NULL eklenemez'):
            self.assertIn(text, retry)
        sql = p.out('bos_deger.sql').read_text(encoding='utf-8')
        self.assertIn("UPDATE siparis_baslik SET para_birimi = 'TRY' WHERE para_birimi IS NULL;  -- 15 satır", sql)
        self.assertIn('ALTER TABLE siparis_baslik ALTER COLUMN kargo_ucreti SET DEFAULT 0;', sql)
        # the generated SQL leaves no NULLs behind in the constrained columns
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        cols, rows = tabular.read(p.dir / 'ornek' / 'siparis_basliklari.csv')
        db.execute(f'CREATE TABLE siparis_baslik ({", ".join(cols)})')
        db.executemany(f'INSERT INTO siparis_baslik VALUES ({",".join("?" * len(cols))})', [[r[c] or None for c in cols] for r in rows])
        for stmt in [s for s in sql.split('\n') if s.startswith('UPDATE')]:
            db.execute(stmt.split('--')[0])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM siparis_baslik WHERE kargo_ucreti IS NULL OR para_birimi IS NULL OR durum IS NULL').fetchone()[0], 0)

    def test_erasure_plan_respects_legal_retention(self):
        p, done = self.run_agent('silme-talebi-planlayici', '--yes', answers=[json.loads(json.dumps(BAD31['silme'])), GOOD['silme-talebi-planlayici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('crm.iletisim_izni: analitik kolon yok', 'satis.fatura: yasal saklama yükümlülüğü var', 'satis.siparis: yasal dayanak yok; kişisel veri saklanamaz',
                     'dwh.gunluk_satis_ozet: kimlik kolonu yok, kişiye bağlanamaz'):
            self.assertIn(text, retry)
        sql = p.out('silme_plani.sql').read_text(encoding='utf-8')
        self.assertIn('DELETE FROM crm.iletisim_izni WHERE musteri_id = :kisi_id;', sql)
        self.assertIn('UPDATE satis.siparis SET teslimat_adresi = NULL, telefon = NULL, musteri_id = NULL WHERE musteri_id = :kisi_id;', sql)
        self.assertNotIn('DELETE FROM satis.fatura', sql)
        self.assertEqual({r['tablo']: r['islem'] for r in p.csv('silme_plani.csv')}['muhasebe.yevmiye'], 'sakla')

    def test_warehouse_size_meets_sla_at_lowest_cost(self):
        p, done = self.run_agent('ambar-boyutu-secici', '--yes', answers=[BAD31['ambar'], GOOD['ambar-boyutu-secici']])
        self.ok(done)
        self.assertIn('XS 2400/6.9, S 1440/8.4, M 864/10.4', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('etl_gece: XL günlük 86.4 kredi; SLA’yı karşılayan en ucuz seçenek M 55.7 kredi', 'bi_dashboard: L ile tahmini p95 20 sn, SLA 15 sn',
                     'adhoc_analist: 1 küme × 8 = 8 eşzamanlı sorgu, tepe 12', 'ml_ozellik: otomatik askıya alma 60–600 sn olmalı'):
            self.assertIn(text, retry)
        sql = p.out('ambar_ayarlari.sql').read_text(encoding='utf-8')
        self.assertIn("ALTER WAREHOUSE wh_bi_dashboard SET WAREHOUSE_SIZE = 'XLARGE' MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = 5 AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;", sql)
        self.assertIn("WAREHOUSE_SIZE = 'MEDIUM'", sql)
        self.assertIn('414.7 → 361.3', p.out('ambar-boyutu.md').read_text(encoding='utf-8'))

    def test_reconciliation_causes_are_tested_row_by_row(self):
        p, done = self.run_agent('mutabakat-fark-aciklayici', '--yes', answers=[BAD31['mutabakat'], GOOD['mutabakat-fark-aciklayici']])
        self.ok(done)
        sent = self.model.requests[-2]['body']['messages'][-1]['content']
        self.assertIn('- tarih: 113 fark · mekanik nedenlerin açıkladığı pay: yuvarlama %0, saat_dilimi %100', sent)
        self.assertIn('  - eksik satırlarda durum: iptal 7', sent)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('tutar: farkların %95 kadarı yuvarlama ile açıklanıyor; gerçek hata denemez', 'musteri: yuvarlama farkların yalnızca %0 kadarını açıklıyor',
                     'HEDEFTE EKSİK: musteri = Deniz Lojistik koşulu eşleşen 21 satırda da doğru'):
            self.assertIn(text, retry)
        self.assertEqual([(r['anahtar'], r['kaynak'], r['hedef']) for r in p.csv('gercek_hatalar.csv')],
                         [('F00023', '7921.90', '9506.28'), ('F00088', '6976.49', '8371.79')])  # the two planted double-VAT rows

    def test_dbt_test_thresholds_are_replayed_on_history(self):
        p, done = self.run_agent('dbt-test-siddeti', '--yes', answers=[BAD31['dbt'], GOOD['dbt-test-siddeti']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('not_null_siparis_musteri_id: 2 gerçek olaydan 1 tanesi hata üretir; kaçan: 2026-09-10 (95 satır)',
                     'relationships_siparis_urun: olay olmayan 16 günde pipeline durur (en fazla 1)', 'accepted_values_siparis_durum: warn_if (>5) error_if’ten (>0) büyük olamaz'):
            self.assertIn(text, retry)
        rows = {r['test']: r for r in p.csv('test_siddeti.csv')}
        self.assertEqual((rows['relationships_siparis_urun']['hata_gun'], rows['relationships_siparis_urun']['yakalanan']), ('1', '1'))
        self.assertIn('      severity: warn\n      warn_if: ">0"\n      error_if: ">10"', p.out('test_ayarlari.yml').read_text(encoding='utf-8'))

    def test_sensor_timeouts_fit_arrival_history(self):
        p, done = self.run_agent('sensor-ayarlayici', '--yes', answers=[BAD31['sensor'], GOOD['sensor-ayarlayici']])
        self.ok(done)
        self.assertIn('- pazarlama.bekle_kampanya_api: 58 · 2 · 90.8/197.6/330.0/330.0', self.model.requests[-2]['body']['messages'][-1]['content'])
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('erp_gece.bekle_erp_dosyasi: medyan bekleme 39.8 dk; poke modu', 'erp_gece.bekle_erp_dosyasi: timeout 60 dk, verinin geldiği günlerin %15.3 kadarında erken biter',
                     'banka.bekle_ekstre: poke_interval 10 sn', 'pazarlama.bekle_kampanya_api: timeout 120 dk'):
            self.assertIn(text, retry)
        py = p.out('sensor_ayarlari.py').read_text(encoding='utf-8')
        compile(py, 'sensor_ayarlari.py', 'exec')
        self.assertIn('"erp_gece.bekle_erp_dosyasi": {"mode": "reschedule", "poke_interval": 300, "timeout": int(timedelta(minutes=150).total_seconds())', py)

    def test_lifecycle_rules_are_costed_against_all_options(self):
        p, done = self.run_agent('yasam-dongusu-onerici', '--yes', answers=[BAD31['yasam'], GOOD['yasam-dongusu-onerici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('ham/loglar/: eşikler yaş dilimi sınırlarından biri olmalı', 'ml/egitim-verisi/: önerilen kurallar aylık maliyeti 801 → 1,140 artırıyor',
                     'yedek/veritabani/: Glacier IR eşiği (30) IA eşiğinden (90) sonra olmalı'):
            self.assertIn(text, retry)
        rules = {r['Filter']['Prefix']: r for r in p.json('lifecycle.json')['Rules']}
        self.assertEqual(rules['ham/loglar/']['Transitions'], [{'Days': 30, 'StorageClass': 'GLACIER_IR'}])
        self.assertEqual(rules['ham/loglar/']['Expiration'], {'Days': 730})
        self.assertNotIn('Expiration', rules['ml/egitim-verisi/'])
        self.assertIn('28,569 → 6,614', p.out('yasam-dongusu.md').read_text(encoding='utf-8'))

    def test_stock_transfers_keep_donors_covered(self):
        p, done = self.run_agent('stok-transfer-onerici', '--yes', answers=[BAD31['stok'], GOOD['stok-transfer-onerici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('Ç-Kahve-250g Ataşehir: transfer sonrası 65 adet = 4.7 gün; veren mağaza en az 7 gün tutmalı', 'Ç-Termos-500ml Ümraniye: zaten 28.0 gün stoklu',
                     'Ç-Kupa-Seti: ağdaki fazla (207 adet) yetiyor ama Kadıköy 1.5 gün'):
            self.assertIn(text, retry)
        rows = p.csv('stok_gunleri.csv')
        self.assertTrue(all(float(r['gun_sonra']) >= 7 for r in rows))
        self.assertEqual(sum(int(r['stok_once']) for r in rows), sum(int(r['stok_sonra']) for r in rows))
        self.assertEqual(len(p.csv('transferler.csv')), 7)

    def test_cdc_method_matches_table_capabilities(self):
        p, done = self.run_agent('cdc-yontem-secici', '--yes', answers=[BAD31['cdc'], GOOD['cdc-yontem-secici']])
        self.ok(done)
        retry = self.model.requests[-1]['body']['messages'][-1]['content']
        for text in ('erp.siparis_kalem: fiziksel silme var; sorgu tabanlı çekim silinen satırları kaçırır', 'erp.musteri: 2,100,000 satır; tam kopya yalnızca',
                     'erp.stok_hareket: birincil anahtar yok; log tabanlı CDC', 'günlük tam kopya 2,143,200 satır, bütçe 2,000,000'):
            self.assertIn(text, retry)
        cfg = p.json('debezium_baglayici.json')['config']
        self.assertEqual(cfg['table.include.list'], 'erp.siparis,erp.siparis_kalem,erp.musteri')
        p2, done = self.run_agent('cdc-yontem-secici', '--yes', '--no-log-access', answers=[GOOD['cdc-yontem-secici']] * 2)
        self.assertIn('erp.siparis: kaynakta log (binlog/WAL) erişimi yok', self.model.requests[-1]['body']['messages'][-1]['content'])

    def test_invalid_answer_gets_one_corrective_retry(self):
        bad = {'sql': 'SELECT s.KalemlerToplamCiro FROM dbo.Siparisler s'}
        p, done = self.run_agent('sql-yazari', '--yes', answers=[bad, GOOD['sql-yazari']])
        self.ok(done)
        self.assertEqual(len(self.model.requests), 2)
        retry = self.model.requests[1]['body']['messages'][-1]['content']
        self.assertIn('kalemlertoplamciro', retry)
        self.assertEqual(p.json('dogrulama.json')['meta']['attempts'], 2)

    def test_two_invalid_answers_stop_without_output(self):
        for item_id, bad in [('sql-yazari', {'sql': 'DELETE FROM dbo.Urunler'}), ('dax-yazari', {'olculer': [{'ad': 'X', 'tablo': 'Satislar', 'ifade': 'SUM(Satislar[Yok])'}]}),
                             ('qlik-powerbi', {'tablolar': []}), ('veri-sozlugu', {'sutunlar': [{'ad': 'siparis_no', 'aciklama': 'x'}]}),
                             ('kalite-kurali', {'rules': [{'column': 'olmayan', 'check': 'not_null'}]}), ('kpi-tanimlayici', 'bu json değil'),
                             ('sql-aciklayici', {**GOOD['sql-aciklayici'], 'ifadeler': GOOD['sql-aciklayici']['ifadeler'][:-1]}),
                             ('donem-yorumu', {**GOOD['donem-yorumu'], 'ozet': 'Ciro %9 düştü.'}),
                             ('kvkk-envanteri', {'kayitlar': GOOD['kvkk-envanteri']['kayitlar'][:-1]}),
                             ('olay-analisti', {'imzalar': GOOD['olay-analisti']['imzalar'][:2]}),
                             ('boyutsal-model', {**GOOD['boyutsal-model'], 'olgu': {**GOOD['boyutsal-model']['olgu'], 'olculer': ['adet', 'durum']}}),
                             ('regex-yazari', {'desen': r'^\d{2} [A-Z]+ \d+$'}),
                             ('migrasyon-esleme', {'eslemeler': GOOD['migrasyon-esleme']['eslemeler'][:-1]}),
                             ('dbt-model-yazari', {**GOOD['dbt-model-yazari'], 'kaynaklar': GOOD['dbt-model-yazari']['kaynaklar'][:2]}),
                             ('zamanlama-planlayici', {'plan': GOOD['zamanlama-planlayici']['plan'][:-1]}),
                             ('tasarruf-plani', {**GOOD['tasarruf-plani'], 'ozet': 'Yüzde 45 indirim mümkün.'}),
                             ('model-karti', {**GOOD['model-karti'], 'riskler': []}),
                             ('eslestirme-hakemi', {'kararlar': GOOD['eslestirme-hakemi']['kararlar'][:-1]}),
                             ('sql-optimizasyon', {**GOOD['sql-optimizasyon'], 'sorgu': 'DELETE FROM siparisler'}),
                             ('avro-sema-tasarimci', {'sema': {'type': 'record', 'name': 'X', 'fields': [{'name': 'olay_id', 'type': 'string'}]}}),
                             ('rag-test-seti', {'sorular': GOOD['rag-test-seti']['sorular'][:-1]}),
                             ('kalite-kok-neden', {**GOOD['kalite-kok-neden'], 'kurallar': GOOD['kalite-kok-neden']['kurallar'][:1]}),
                             ('ab-test-karari', {**GOOD['ab-test-karari'], 'karar': 'deneyi yayına al'}),
                             ('veri-sozlesmesi-yazari', {**GOOD['veri-sozlesmesi-yazari'], 'bilinen_ihlaller': []}),
                             ('butce-sapma-yorumu', {**GOOD['butce-sapma-yorumu'], 'kalemler': GOOD['butce-sapma-yorumu']['kalemler'][1:]}),
                             ('hesap-plani-esleyici', {'eslemeler': GOOD['hesap-plani-esleyici']['eslemeler'][:-1]}),
                             ('json-esleme-yazari', {**GOOD['json-esleme-yazari'], 'kayit_yolu': 'sayfa'}),
                             ('bolumleme-danismani', {**GOOD['bolumleme-danismani'], 'ayrinti': 'gün', 'bolum_sutunu': 'durum'}),
                             ('deney-analisti', {**GOOD['deney-analisti'], 'test': 'welch-t'}),
                             ('sube-planlayici', {**GOOD['sube-planlayici'], 'oneriler': []}),
                             ('rapor-tasarimcisi', {**GOOD['rapor-tasarimcisi'], 'boyutlar': ['musteri_eposta']}),
                             ('qlik-ifade-yazari', {'ifadeler': [{'ad': 'Ciro', 'ifade': 'Sum([Ciro])'}]}),
                             ('tableau-hesap-yazari', {'alanlar': [{'ad': 'Oran', 'formul': 'SUM([Tutar]) / [Adet]', 'tip': 'real', 'rol': 'measure'}]}),
                             ('birim-kodu-eslestirici', {'eslemeler': [{'birim': 'Kasa', 'kod': 'KASA'}]}),
                             ('tevkifat-kodu-onerici', {'oneriler': [{'urun': 'Ofis kırtasiye malzemesi', 'kod': '650', 'oran': '50', 'gerekce': 'x'}]}),
                             ('log-ayristirici', {'desenler': [{'ad': 'hepsi', 'desen': '(?P<zaman>.+) (?P<mesaj>.*)'}]}),
                             ('anket-kodlayici', {**GOOD['anket-kodlayici'], 'atamalar': GOOD['anket-kodlayici']['atamalar'][:-1]}),
                             ('alarm-esigi-onerici', {'kurallar': [{**r, 'esik': 0.1} if r['tip'] == 'oran_alt' else r for r in GOOD['alarm-esigi-onerici']['kurallar']]}),
                             ('kolon-adi-standardi', {'eslemeler': [{**e, 'yeni_ad': 'order'} if e['kolon'] == 'order' else e for e in GOOD['kolon-adi-standardi']['eslemeler']]}),
                             ('adres-cozumleyici', {'adresler': [{**a, 'mahalle': 'Uydurma Mahallesi'} for a in GOOD['adres-cozumleyici']['adresler']]}),
                             ('urun-kategori-eslestirici', {'eslemeler': [{**e, 'kategori': 'K1'} for e in GOOD['urun-kategori-eslestirici']['eslemeler']]}),
                             ('sema-degisiklik-etkisi', {'degisiklikler': [{**e, 'dosyalar': []} for e in GOOD['sema-degisiklik-etkisi']['degisiklikler']]}),
                             ('segment-yorumlayici', {'segmentler': [{**x, 'ozellikler': []} for x in GOOD['segment-yorumlayici']['segmentler']]}),
                             ('sentetik-veri-tasarimcisi', {**GOOD['sentetik-veri-tasarimcisi'], 'kolonlar': {**GOOD['sentetik-veri-tasarimcisi']['kolonlar'], 'ad_soyad': {'tur': 'kategori', 'degerler': {'X': 1}}}}),
                             ('belge-alan-cikarici', {'belgeler': [{**b, 'alanlar': {k: {'deger': '1', 'kanit': 'yok böyle bir cümle'} for k in b['alanlar']}} for b in GOOD['belge-alan-cikarici']['belgeler']]}),
                             ('birlestirme-anahtari', {**GOOD['birlestirme-anahtari'], 'iliski': 'n-n'}),
                             ('hata-siniflandirici', {'kategoriler': [{**c, 'desen': '('} for c in GOOD['hata-siniflandirici']['kategoriler']]}),
                             ('sozluk-kolon-eslestirici', {'terimler': [{**x, 'kolonlar': [{'tablo': 'dwh.yok', 'kolon': 'yok'}]} for x in GOOD['sozluk-kolon-eslestirici']['terimler']]}),
                             ('tahsilat-eslestirici', {'eslesmeler': [{**e, 'faturalar': ['FTR-YOK']} for e in GOOD['tahsilat-eslestirici']['eslesmeler']]}),
                             ('maskeleme-plani', {'kolonlar': {c: {'yontem': 'koru'} for c in GOOD['maskeleme-plani']['kolonlar']}}),
                             ('tip-donusum-eslestirici', {'eslemeler': [{**e, 'hedef_tip': 'varchar(1)'} for e in GOOD['tip-donusum-eslestirici']['eslemeler']]}),
                             ('dbt-test-onerici', {'kolonlar': {'kanal': [{'test': 'not_null'}]}}),
                             ('huni-analisti', {**GOOD['huni-analisti'], 'en_buyuk_kayip': 'ziyaret'}),
                             ('regresyon-yorumlayici', {**GOOD['regresyon-yorumlayici'], 'ozet': 'Reklam satışı artırır.'}),
                             ('vardiya-planlayici', {'vardiyalar': [{'baslangic': '08:00', 'sure_saat': 4, 'kisi': 1}]}),
                             ('excel-baslik-cozucu', {**GOOD['excel-baslik-cozucu'], 'kolon_adlari': ['a', 'a']}),
                             ('kafka-konu-tasarimcisi', {'konular': [{**k, 'bolum': 1} for k in GOOD['kafka-konu-tasarimcisi']['konular']]}),
                             ('slo-tasarimcisi', {'slolar': [{**x, 'hedef': 0.5} for x in GOOD['slo-tasarimcisi']['slolar']]}),
                             ('etiket-kurali-yazari', {'kurallar': [{'desen': '^prod-', 'etiketler': {'tag_env': 'canli'}}]}),
                             ('ozellik-muhendisi', {'ozellikler': [{'ad': 'x', 'islem': 'bos_mu', 'a': 'iptal_tarihi'}]}),
                             ('kayma-yorumlayici', {**GOOD['kayma-yorumlayici'], 'yeniden_egit': False}),
                             ('sss-yazari', {**GOOD['sss-yazari'], 'celiskiler': []}),
                             ('delta-bakim-plani', {'tablolar': [{**t, 'vacuum_saat': 1} for t in GOOD['delta-bakim-plani']['tablolar']]}),
                             ('kohort-yorumlayici', {**GOOD['kohort-yorumlayici'], 'egilim': 'artıyor'}),
                             ('sabit-genislik-cozucu', {'alanlar': [{'alan': 'tum_satir', 'baslangic': 1, 'uzunluk': 10, 'tip': 'metin'}]}),
                             ('capraz-kural-yazari', {'kurallar': [{'kural': 'adet > 0', 'tur': 'kesin'}, {'kural': 'tutar > 0', 'tur': 'kesin'}]}),
                             ('scd-tipi-onerici', {'kolonlar': {k: 0 for k in GOOD['scd-tipi-onerici']['kolonlar']}}),
                             ('kesinti-plani', {**GOOD['kesinti-plani'], 'karar_noktasi': '05:00'}),
                             ('kod-listesi-eslestirici', {'eslemeler': [{**e, 'kod': 'YOK'} for e in GOOD['kod-listesi-eslestirici']['eslemeler']]}),
                             ('sorgu-indeks-onerici', {'indeksler': [{'sql': 'CREATE INDEX ix_x ON siparis (tutar)'}]}),
                             ('veri-sahibi-onerici', {'oneriler': [{**o, 'ekip': 'Yok Ekibi'} for o in GOOD['veri-sahibi-onerici']['oneriler']]}),
                             ('grafik-secici', {'grafikler': [{**g, 'tur': 'cizgi', 'x': 'bolge'} for g in GOOD['grafik-secici']['grafikler']]}),
                             ('rota-gun-atayici', {'atamalar': [{**a, 'gun': 'Pzt'} for a in GOOD['rota-gun-atayici']['atamalar']]}),
                             ('yeniden-deneme-politikasi', {'gorevler': [{**g, 'timeout_dk': 1} for g in GOOD['yeniden-deneme-politikasi']['gorevler']]}),
                             ('autovacuum-ayarlayici', {'tablolar': [{**t, 'vacuum_scale_factor': 0.9} for t in GOOD['autovacuum-ayarlayici']['tablolar']]}),
                             ('parca-boyutu-secici', {'parca_boyu': 100, 'ortusme': 0}),
                             ('masraf-politika-denetcisi', {'atamalar': [{**a, 'kategori': 'diger'} for a in GOOD['masraf-politika-denetcisi']['atamalar']]}),
                             ('baslik-birlestirici', {**GOOD['baslik-birlestirici'], 'kolonlar': ['Tarih']}),
                             ('siparis-onerici', {**GOOD['siparis-onerici'], 'siparisler': []}),
                             ('maliyet-anomali-aciklayici', {'anomaliler': [{**a, 'ana_kaynak': 'yok'} for a in GOOD['maliyet-anomali-aciklayici']['anomaliler']]}),
                             ('gecerli-aralik-onerici', {'kolonlar': [{**k, 'uc_degerler_gecerli': False} for k in GOOD['gecerli-aralik-onerici']['kolonlar']]}),
                             ('hipotez-testi-secici', {**GOOD['hipotez-testi-secici'], 'test': 'student_t'}),
                             ('filigran-onerici', {'kaynaklar': [{**k, 'watermark_sn': 0, 'izinli_gecikme_sn': 0} for k in GOOD['filigran-onerici']['kaynaklar']]}),
                             ('incremental-strateji-onerici', {'modeller': [{**m, 'geriye_bakis_gun': 0} for m in GOOD['incremental-strateji-onerici']['modeller']]}),
                             ('katman-atayici', {'tablolar': [{**t, 'katman': 'gold'} for t in GOOD['katman-atayici']['tablolar']]}),
                             ('baglanti-havuzu-boyutlandirici', {'servisler': [{**v, 'havuz_max': 1} for v in GOOD['baglanti-havuzu-boyutlandirici']['servisler']]}),
                             ('geofence-yaricap-onerici', {'magazalar': [{**g, 'yaricap_m': 30} for g in GOOD['geofence-yaricap-onerici']['magazalar']]}),
                             ('esik-secici', {**GOOD['esik-secici'], 'esik': 0.9}),
                             ('k8s-kaynak-ayarlayici', {'deploymentlar': [{**d, 'mem_limit_mib': 64} for d in GOOD['k8s-kaynak-ayarlayici']['deploymentlar']]}),
                             ('toplu-cekim-planlayici', {'uc_noktalar': [{**u, 'dakikada_istek': 50} for u in GOOD['toplu-cekim-planlayici']['uc_noktalar']]}),
                             ('backfill-planlayici', {'daglar': [{**d, 'max_active_runs': 4} for d in GOOD['backfill-planlayici']['daglar']]}),
                             ('butce-fazlandirici', {'hesaplar': [{**h, 'istisna_aylar': []} for h in GOOD['butce-fazlandirici']['hesaplar']]}),
                             ('k-anonimlik-genellestirici', {'kolonlar': [{**c, 'tip': 'koru'} for c in GOOD['k-anonimlik-genellestirici']['kolonlar']]}),
                             ('yukleme-sirasi-planlayici', {**GOOD['yukleme-sirasi-planlayici'], 'sira': GOOD['yukleme-sirasi-planlayici']['sira'][::-1]}),
                             ('ozet-tablo-onerici', {'ozet_tablolar': GOOD['ozet-tablo-onerici']['ozet_tablolar'][2:]}),
                             ('rol-madenciligi', {**GOOD['rol-madenciligi'], 'atamalar': GOOD['rol-madenciligi']['atamalar'][:3]}),
                             ('getirme-ayari-onerici', {'top_k': 1, 'min_score': 0.9}),
                             ('zaman-asimi-onerici', {'roller': [{**r, 'statement_timeout_ms': 10} for r in GOOD['zaman-asimi-onerici']['roller']]}),
                             ('kaynak-tazelik-onerici', {'kaynaklar': [{**k, 'warn_after': {'count': 1, 'period': 'minute'}} for k in GOOD['kaynak-tazelik-onerici']['kaynaklar']]}),
                             ('zorder-secici', {'tablolar': [{**t, 'kolonlar': ['ulke']} for t in GOOD['zorder-secici']['tablolar']]}),
                             ('anket-agirliklandirici', {**GOOD['anket-agirliklandirici'], 'degiskenler': ['cinsiyet']}),
                             ('depo-yeri-secici', {'depolar': ['D-Gebze']}),
                             ('tekrar-penceresi-onerici', {'anahtar': ['islem_no'], 'pencere_sn': 300}),
                             ('altin-kayit-kurali', {'alanlar': [{**a, 'kural': 'en_uzun', 'kaynak_sirasi': []} for a in GOOD['altin-kayit-kurali']['alanlar']]}),
                             ('log-ornekleme-onerici', {'oranlar': [{**g, 'oran': 0} for g in GOOD['log-ornekleme-onerici']['oranlar']]}),
                             ('model-terfi-karari', {**GOOD['model-terfi-karari'], 'karar': 'reddet'}),
                             ('bos-deger-politikasi', {'kolonlar': [{**c, 'politika': 'zorunlu'} for c in GOOD['bos-deger-politikasi']['kolonlar']]}),
                             ('silme-talebi-planlayici', BAD31['silme']),
                             ('ambar-boyutu-secici', BAD31['ambar']),
                             ('mutabakat-fark-aciklayici', BAD31['mutabakat']),
                             ('dbt-test-siddeti', BAD31['dbt']),
                             ('sensor-ayarlayici', BAD31['sensor']),
                             ('yasam-dongusu-onerici', BAD31['yasam']),
                             ('stok-transfer-onerici', BAD31['stok']),
                             ('cdc-yontem-secici', BAD31['cdc'])]:
            with self.subTest(item_id):
                p, done = self.run_agent(item_id, '--yes', answers=[bad, bad])
                self.assertEqual(done.returncode, 1)
                self.assertIn('doğrulamayı geçmedi', done.stderr)
                self.assertNotIn('Traceback', done.stderr)
                self.assertFalse(p.out(OUTPUTS[item_id]).exists())

    def test_dry_run_and_missing_consent_never_call_the_model(self):
        p, done = self.run_agent('veri-sozlugu', '--dry-run')
        self.ok(done)
        self.assertEqual(self.model.requests, [])
        request = p.json('gonderilecek-istek.json')
        self.assertIn('musteri_eposta', request['user'])
        self.assertNotIn('@', request['user'])
        p, done = self.run_agent('sql-yazari')  # no --yes and stdin is not a terminal
        self.assertEqual(done.returncode, 1)
        self.assertIn('--yes', done.stderr)
        self.assertEqual(self.model.requests, [])

    def test_anthropic_and_ollama_formats(self):
        p, done = self.run_agent('sql-yazari', '--yes', provider='anthropic', answers=[GOOD['sql-yazari']])
        self.ok(done)
        req = self.model.requests[-1]
        self.assertEqual(req['path'], '/v1/messages')
        self.assertEqual(req['headers']['x-api-key'], 'ak-test')
        p, done = self.run_agent('sql-yazari', '--yes', provider='ollama', answers=['```json\n' + json.dumps(GOOD['sql-yazari']) + '\n```'])
        self.ok(done)
        self.assertEqual(self.model.requests[-1]['path'], '/api/chat')
        self.assertTrue(self.model.requests[-1]['body']['stream'])

    def test_http_error_is_readable_and_does_not_echo_body(self):
        self.model.status = 500
        p, done = self.run_agent('sql-yazari', '--yes')
        self.assertEqual(done.returncode, 1)
        self.assertIn('HTTP 500', done.stderr)
        self.assertNotIn('secret request echo', done.stderr + done.stdout)
        self.assertNotIn('Traceback', done.stderr)

    def test_missing_model_name_is_explained(self):
        p = self.package('sql-yazari')
        done = p.run('--yes', env={'PROVIDER': 'openai'})
        self.assertEqual(done.returncode, 1)
        self.assertIn('MODEL', done.stderr)


def write_xlsx(path, rows):
    """Minimal XLSX with shared strings, numbers and an inline string — enough to test the stdlib reader."""
    strings, xml_rows = [], []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = chr(65 + c) + str(r)
            if isinstance(value, str):
                if value not in strings:
                    strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sayfa1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', f'<sst {ns}>' + ''.join(f'<si><t>{s}</t></si>' for s in strings) + '</sst>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')


if __name__ == '__main__':
    unittest.main()
