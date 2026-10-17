"""End-to-end tests of local code blocks and agents. Every test runs the *packaged* copy (as a user would after unzipping)."""
import csv, io, json, os, subprocess, sys, tempfile, threading, unittest, zipfile
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
        self.assertEqual(len([i for i in LOCAL if not i.get('op')]), 25)
        self.assertEqual(sum(i['kind'] == 'agent' for i in LOCAL), 13)
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
                             ('migrasyon-esleme', {'eslemeler': GOOD['migrasyon-esleme']['eslemeler'][:-1]})]:
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
