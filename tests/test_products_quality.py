"""Data quality product packages: results from each quality engine/API and the root-cause agent."""
import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import quality  # noqa: E402


class Results(Base):
    def rows(self):
        with open(self.out / 'kontrol_sonuclari.csv', encoding='utf-8') as fh:
            return list(csv.DictReader(fh))

    def test_gx_checkpoint_run(self):
        result = {'run_results': {'x': {'validation_result': {'meta': {'active_batch_definition': {'data_asset_name': 'orders'}}, 'results': [
            {'success': True, 'expectation_config': {'type': 'expect_column_values_to_not_be_null', 'kwargs': {'column': 'id'}}, 'result': {'unexpected_percent': 0}},
            {'success': False, 'expectation_config': {'type': 'expect_column_values_to_be_between', 'kwargs': {'column': 'amount', 'mostly': 0.99}},
             'result': {'unexpected_percent': 4.2, 'unexpected_count': 42, 'partial_unexpected_list': ['SECRET_VALUE']}}]}}}}
        run = type('R', (), {'to_json_dict': lambda s: result})()
        checkpoint = type('C', (), {'run': lambda s: run})()
        context = type('Ctx', (), {'checkpoints': type('CS', (), {'get': lambda s, n: checkpoint})()})()
        gx = type('gx', (), {'get_context': staticmethod(lambda **k: context)})
        with patch.object(quality, 'driver', return_value=gx):
            self.run_op('gx-rule-check', project_dir='p', checkpoint='daily')
        rows = self.rows()
        self.assertEqual([r['sonuc'] for r in rows], ['geçti', 'kaldı'])
        self.assertNotIn('SECRET_VALUE', (self.out / 'kontrol_sonuclari.csv').read_text(encoding='utf-8'))
        self.assertFalse(self.read('ozet.json')['basarili'])

    def test_soda_core_scan(self):
        class Scan:
            def set_data_source_name(self, n): self.ds = n
            def add_configuration_yaml_file(self, file_path): pass
            def add_sodacl_yaml_file(self, f): pass
            def set_scan_definition_name(self, n): pass
            def execute(self): return 1
            def get_scan_results(self):
                return {'checks': [{'name': 'row_count > 0', 'table': 'orders', 'outcome': 'pass', 'diagnostics': {'value': 10}},
                                   {'name': 'missing_count(email) = 0', 'table': 'orders', 'column': 'email', 'outcome': 'fail', 'diagnostics': {'value': 3}}], 'logs': []}
        with patch.object(quality, 'driver', return_value=type('soda', (), {'Scan': Scan})):
            self.run_op('soda-core-rule-check', data_source='pg', configuration_file='c.yml', checks_files=['checks.yml'])
        self.assertEqual([r['sonuc'] for r in self.rows()], ['geçti', 'kaldı'])

    def test_soda_cloud_paging(self):
        self.server.add('GET', r'/api/v1/checks\?size=1000&page=0', {'content': [{'name': 'freshness', 'evaluationStatus': 'warn', 'datasets': [{'name': 'orders'}]}], 'last': True})
        self.run_op('soda-cloud-rule-check', server='https://cloud.soda.io')
        self.assertEqual(self.rows()[0]['sonuc'], 'uyarı')
        self.assertTrue(self.server.received[0][3]['Authorization'].startswith('Basic '))

    def test_deequ_repository_rules(self):
        repo = [{'resultKey': {'dataSetDate': 1, 'tags': {}}, 'analyzerContext': {'metricMap': [{'metric': {'name': 'Completeness', 'instance': 'email', 'value': 1.0}}]}},
                {'resultKey': {'dataSetDate': 2, 'tags': {'run': 'b'}}, 'analyzerContext': {'metricMap': [{'metric': {'name': 'Completeness', 'instance': 'email', 'value': 0.9}},
                                                                                                          {'metric': {'name': 'Size', 'instance': '*', 'value': 100}}]}}]
        (self.root / 'metrics.json').write_text(json.dumps(repo))
        self.run_op('deequ-rule-check', metrics_repository_file='metrics.json', rules=[{'metrik': 'Completeness', 'kolon': 'email', 'min': 0.99}, {'metrik': 'Size', 'kolon': '*', 'min': 1},
                                                                                     {'metrik': 'Uniqueness', 'kolon': 'id', 'min': 1}])
        self.assertEqual([r['sonuc'] for r in self.rows()], ['kaldı', 'geçti', 'hata'])

    def test_talend_trust_scores(self):
        self.server.add('POST', '/api/data-governance/trust-scores/results/data-sets/actions/filter', {'data': [
            {'datasetId': 'ds1', 'score': 72, 'axes': [{'id': 'VALIDITY', 'score': 60, 'enabled': True, 'applicable': True, 'metrics': [{'id': 'VALIDITY_QUALITY', 'score': 60}]}]}]})
        self.run_op('talend-dq-rule-check', tenant='https://t.eu.qlikcloud.com', dataset_ids=['ds1', 'ds2'], min_score=80)
        self.assertEqual([(r['kontrol'], r['sonuc']) for r in self.rows()], [('TRUST_SCORE', 'kaldı'), ('VALIDITY', 'kaldı'), ('TRUST_SCORE', 'hata')])

    def test_ataccama_counts(self):
        self.server.add('POST', '/graphql', {'data': {'monitoringProject': {'publishedVersion': {'dqCheckResultOverTime': [
            {'processingId': 'p1', 'startedAt': 't', 'results': [{'dqCheckId': 'c1', 'results': [{'id': '1', 'name': 'VALID', 'count': 98}, {'id': '2', 'name': 'INVALID', 'count': 2}]}]}]}}}})
        self.run_op('ataccama-rule-check', server='https://one.example', monitoring_project_gid='mp1', catalog_item_gid='ci1', check_gids=['c1'], max_invalid_ratio=0.01)
        self.assertEqual(self.rows()[0]['deger'], '0.02')

    def test_informatica_profile_export(self):
        self.server.add('POST', '/saas/public/core/v3/login', {'userInfo': {'sessionId': 'S'}})
        self.server.add('GET', r"/metric-store/api/v1/Profiles\('P123'\)/Export", b'Column Name,Null %\nEMAIL,3.5\n')
        self.run_op('informatica-cdq-rule-check', login_url='https://dm-us.informaticacloud.com', profiling_url='https://p.example', profile_id='P123',
                    rules=[{'metrik': 'Null %', 'kolon': 'EMAIL', 'max': 1}])
        self.assertEqual(self.server.received[1][3]['Infa-Session-Id'], 'S')
        self.assertEqual(self.rows()[0]['sonuc'], 'kaldı')


class RootCause(Base):
    def test_deequ_trend_root_cause(self):
        repo = [{'resultKey': {'dataSetDate': d}, 'analyzerContext': {'metricMap': [{'metric': {'name': 'Completeness', 'instance': 'email', 'value': v}}]}} for d, v in ((1, 1.0), (2, 0.8))]
        (self.root / 'metrics.json').write_text(json.dumps(repo))
        bad = {'bulgular': [{'kontrol': 'Uniqueness', 'kategori': 'kaynak_sistem', 'kok_neden': 'x', 'oncelik': 'orta'}]}
        good = {'bulgular': [{'kontrol': 'Completeness', 'kategori': 'sema_degisikligi', 'kok_neden': 'email kolonu kaynakta yeniden adlandırıldı', 'kanit': '1.0 → 0.8',
                              'duzeltme': 'eşlemeyi güncelle', 'oncelik': 'yuksek'}]}
        with model_reply(bad, good):
            self.run_op('deequ-quality-root-cause', metrics_repository_file='metrics.json', rules=[{'metrik': 'Completeness', 'kolon': 'email', 'min': 0.99}])
        self.assertIn('gecmis', json.dumps(self.read('kanit.json')))
        self.assertIn('sema_degisikligi', self.read('kok_neden.csv'))

    def test_all_passing_needs_no_agent(self):
        self.server.add('GET', r'/api/v1/checks\?size=1000&page=0', {'content': [{'name': 'x', 'evaluationStatus': 'pass'}], 'last': True})
        with self.assertRaises(native.NativeError):
            self.run_op('soda-cloud-quality-root-cause', server='https://cloud.soda.io')
