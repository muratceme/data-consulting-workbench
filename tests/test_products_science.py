"""Data science product packages: profiling with each engine (real pandas/SciPy when installed, fakes otherwise) and the hypothesis agent."""
import csv
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import science  # noqa: E402

DATA = 'id,grup,tutar,sehir\n' + ''.join(f'{i},{"A" if i % 2 else "B"},{10 + i * 1.5},{"Ankara" if i % 3 else ""}\n' for i in range(1, 31))


class Profiles(Base):
    def setUp(self):
        super().setUp()
        (self.root / 'veri.csv').write_text(DATA, encoding='utf-8')

    def rows(self):
        with open(self.out / 'profil.csv', encoding='utf-8') as fh:
            return {r['kolon']: r for r in csv.DictReader(fh)}

    @unittest.skipUnless(__import__('importlib').util.find_spec('scipy'), 'scipy kurulu değil')
    def test_scipy_real(self):
        self.run_op('scipy-eda-profile', input_file='veri.csv')
        self.assertTrue(self.rows()['tutar']['normallik_p'])

    @unittest.skipUnless(__import__('importlib').util.find_spec('pandas'), 'pandas kurulu değil')
    def test_pandas_real(self):
        self.run_op('pandas-eda-profile', input_file='veri.csv')
        self.assertEqual(self.rows()['sehir']['bos'], '10')
        self.assertTrue((self.out / 'korelasyon.csv').exists())

    def test_r_runs_fixed_script(self):
        def fake_run(cmd, **kw):
            Path(cmd[3]).write_text('kolon,tip,satir,dolu,bos,farkli\ntutar,numeric,30,30,0,30\n', encoding='utf-8')
            self.assertIn('read.csv', Path(cmd[1]).read_text(encoding='utf-8'))
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with patch.object(science.subprocess, 'run', side_effect=fake_run) as run:
            self.run_op('r-posit-eda-profile', input_file='veri.csv')
        self.assertEqual(run.call_args[0][0][0], 'Rscript')
        self.assertEqual(self.rows()['tutar']['farkli'], '30')

    def test_tool_failure_is_reported(self):
        with patch.object(science.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3, '', 'ERROR: workflow broken')), self.assertRaises(native.NativeError) as err:
            self.run_op('knime-eda-profile', input_file='veri.csv', workflow_file='w.knwf')
        self.assertIn('workflow broken', str(err.exception))

    def test_sas_cas_actions(self):
        self.server.add('POST', '/cas-shared-default-http/cas/sessions', {'session': 's-1'})
        self.server.add('POST', '/cas-shared-default-http/cas/sessions/s-1/actions/simple.summary', {'results': {'Summary': {
            'schema': [{'name': 'Column'}, {'name': 'Min'}, {'name': 'Max'}, {'name': 'N'}, {'name': 'NMiss'}, {'name': 'Mean'}, {'name': 'Std'}], 'rows': [['TUTAR', 1, 9, 28, 2, 5, 1.2]]}}})
        self.server.add('POST', '/cas-shared-default-http/cas/sessions/s-1/actions/simple.distinct', {'results': {'Distinct': {
            'schema': [{'name': 'Column'}, {'name': 'NDistinct'}, {'name': 'NMiss'}], 'rows': [['TUTAR', 20, 2], ['SEHIR', 5, 0]]}}})
        self.server.add('DELETE', '/cas-shared-default-http/cas/sessions/s-1', {})
        self.run_op('sas-viya-eda-profile', server='https://v.example', caslib='CASUSER', table='SATIS')
        rows = self.rows()
        self.assertEqual((rows['TUTAR']['farkli'], rows['TUTAR']['satir'], rows['SEHIR']['tip']), ('20', '30.0', 'karakter'))

    def test_spss_labels(self):
        class Meta:
            column_names = ['cinsiyet']; column_labels = ['Cinsiyet']; variable_measure = {'cinsiyet': 'nominal'}; variable_value_labels = {'cinsiyet': {1: 'K', 2: 'E'}}
        class Frame:
            columns = ['cinsiyet']
            def to_dict(self, kind): return [{'cinsiyet': 1}, {'cinsiyet': 2}]
        fake = type('pyreadstat', (), {'read_sav': staticmethod(lambda p, apply_value_formats: (Frame(), Meta()))})
        with patch.object(science, 'driver', return_value=fake):
            self.run_op('spss-eda-profile', input_file='a.sav')
        self.assertEqual((self.rows()['cinsiyet']['etiket'], self.rows()['cinsiyet']['olcek']), ('Cinsiyet', 'nominal'))

    def test_jupyter_kernel_protocol(self):
        sent = []

        class Ws:
            def send(self, text): sent.append(json.loads(text))
            def recv(self):
                parent = {'msg_id': sent[0]['header']['msg_id']}
                return json.dumps(replies.pop(0) | {'parent_header': parent})
            def close(self): pass
        replies = [{'header': {'msg_type': 'status'}, 'content': {'execution_state': 'busy'}},
                   {'header': {'msg_type': 'stream'}, 'content': {'name': 'stdout', 'text': 'DATAWORK_PROFILE' + json.dumps({'satir': 2, 'tekrarli_satir': 0,
                                                                                                                             'kolonlar': [{'kolon': 'x', 'tip': 'int64', 'satir': 2}]}) + '\n'}},
                   {'header': {'msg_type': 'status'}, 'content': {'execution_state': 'idle'}}]
        self.server.add('POST', '/api/kernels', {'id': 'k1'})
        self.server.add('DELETE', '/api/kernels/k1', {})
        with patch.object(science, 'ws_connect', return_value=Ws()) as connect:
            self.run_op('jupyter-eda-profile', server='https://j.example', data_path='/data/veri.csv')
        self.assertEqual(connect.call_args[0][0], 'wss://j.example/api/kernels/k1/channels')
        self.assertIn("'/data/veri.csv'", sent[0]['content']['code'])
        self.assertEqual(self.server.received[0][3]['Authorization'], 'token test-only-secret')
        self.assertIn('x', self.rows())


class Hypotheses(Base):
    def setUp(self):
        super().setUp()
        (self.root / 'veri.csv').write_text(DATA, encoding='utf-8')

    def test_test_choice_validated_and_rendered_per_language(self):
        bad = {'hipotezler': [{'ad': 'h1', 'h0': 'fark yok', 'h1': 'fark var', 'test': 'pearson', 'hedef': 'tutar', 'aciklayicilar': ['grup']}]}
        good = {'hipotezler': [{'ad': 'h1', 'h0': 'fark yok', 'h1': 'fark var', 'test': 'mann_whitney', 'hedef': 'tutar', 'aciklayicilar': ['grup'], 'alfa': 0.05}]}
        def fake_run(cmd, **kw):
            Path(cmd[3]).write_text('kolon,tip,satir,dolu,bos,farkli\ntutar,numeric,30,30,0,30\ngrup,character,30,30,0,2\n', encoding='utf-8')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with patch.object(science.subprocess, 'run', side_effect=fake_run), model_reply(bad, good) as chat:
            self.run_op('r-posit-hypothesis-design', input_file='veri.csv')
        self.assertIn('iki sayısal', chat.call_args_list[1][0][3][-1]['content'])
        self.assertIn('wilcox.test(`tutar` ~ `grup`, data = d)', self.read('analiz.R'))
        for dialect, expected in (('sas', 'proc npar1way data=work.d wilcoxon; class grup; var tutar;'), ('spss', 'NPAR TESTS /M-W=tutar BY grup(1 2).'),
                                  ('python', 'stats.mannwhitneyu(a, b)')):
            self.assertIn(expected, science.script(dialect, {**good['hipotezler'][0]}))
