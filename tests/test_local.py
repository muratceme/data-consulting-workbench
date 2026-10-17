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
        self.assertEqual(len([i for i in LOCAL if not i.get('op')]), 43)
        self.assertEqual(sum(i['kind'] == 'agent' for i in LOCAL), 31)
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
                             ('rapor-tasarimcisi', {**GOOD['rapor-tasarimcisi'], 'boyutlar': ['musteri_eposta']})]:
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
