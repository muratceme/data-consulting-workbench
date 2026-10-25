"""Observability product packages: metrics/events per platform, series checks and the incident plan agent."""
import csv
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import observability  # noqa: E402


class Volume(Base):
    def rows(self):
        with open(self.out / 'hacim_ve_eksik_deger.csv', encoding='utf-8') as fh:
            return list(csv.DictReader(fh))

    def test_series_check(self):
        series = {('t', '', 'row_count'): [('1', 100), ('2', 110), ('3', 40)], ('t', 'email', 'null_percent'): [('3', 0.2)], ('u', '', 'row_count'): [('1', 5), ('2', 0)]}
        rows = {(r['nesne'], r['metrik']): r for r in observability.series_check(series, {'volume_tolerance': 0.3, 'max_null_rate': 0.05})}
        self.assertEqual(rows[('t', 'row_count')]['durum'], 'sapma'); self.assertEqual(rows[('t', 'null_percent')]['durum'], 'eşik üstü')
        self.assertEqual(rows[('u', 'row_count')]['durum'], 'boş yükleme')

    def test_montecarlo_events(self):
        self.server.add('POST', '/graphql', {'data': {'getEvents': {'edges': [{'node': {'uuid': 'e1', 'eventType': 'volume_anomaly', 'eventState': 'open', 'table': {'fullTableId': 'db:s.orders'}}},
                                                                             {'node': {'uuid': 'e2', 'eventType': 'schema_change', 'table': {'fullTableId': 'db:s.x'}}}],
                                                                   'pageInfo': {'hasNextPage': False}}}})
        self.run_op('montecarlo-volume-report')
        self.assertEqual([r['nesne'] for r in self.rows()], ['db:s.orders'])
        self.assertEqual(self.server.received[0][3]['X-Mcd-Id'], 'test-only-secret')

    def test_datadog_lag_and_gaps(self):
        now = int(time.time() * 1000)
        lag = {'series': [{'tag_set': ['topic:orders', 'consumer_group:etl'], 'pointlist': [[now - 2000, 10], [now - 1000, 900]]}]}
        count = {'series': [{'tag_set': ['topic:orders'], 'pointlist': [[now - 2000, 50], [now - 1000, 0]]}]}
        for body in ({'series': []}, lag, count):
            self.server.add('GET', r'/api/v1/query\?.*', body)
        self.run_op('datadog-dsm-volume-report', site='datadoghq.eu', env='prod', max_lag_seconds=300)
        states = {(r['metrik'], r['durum']) for r in self.rows()}
        self.assertIn(('kafka_lag_saniye', 'gecikme eşik üstü'), states); self.assertIn(('mesaj gelmeyen aralık', 'eksik veri'), states)
        self.assertEqual(self.server.received[0][3]['Dd-Api-Key'], 'test-only-secret')

    def test_acceldata_policies_and_bigeye(self):
        self.server.add('GET', r'/catalog-server/api/assets/byUid/ds.db.s.t/rulesWithLatestExecution\?onlyLatest=true.*',
                        {'rules': [{'rule': {'name': 'not null email', 'ruleType': 'DATA_QUALITY'}, 'latestExecution': {'resultStatus': 'FAILED', 'qualityScore': 82}}]})
        self.run_op('acceldata-volume-report', server='https://a.example', asset_uids=['ds.db.s.t'])
        self.assertEqual(self.rows()[0]['durum'], 'failed')
        self.assertEqual(self.server.received[0][3]['Accesskey'], 'test-only-secret')
        self.out = self.root / 'be'
        self.server.add('POST', '/api/v1/metrics/info', {'metricInfos': [{'metricConfiguration': {'name': 'rows', 'metricType': {'predefinedMetric': {'metricName': 'COUNT_ROWS'}}},
                                                                          'status': 'METRIC_RUN_STATUS_UPPERBOUND_CRITICAL', 'latestMetricRuns': [{'value': 0}]}]})
        self.run_op('bigeye-volume-report', server='https://app.bigeye.com', table_ids=[12])
        self.assertEqual(self.server.received[-1][3]['Authorization'], 'apikey test-only-secret')

    def test_elementary_tables(self):
        fake = FakeSql({'metrics': ([], [{'full_table_name': 'DB.S.ORDERS', 'column_name': None, 'metric_name': 'row_count', 'metric_value': 100, 'bucket_end': '2026-10-01'},
                                         {'full_table_name': 'DB.S.ORDERS', 'column_name': None, 'metric_name': 'row_count', 'metric_value': 102, 'bucket_end': '2026-10-02'}]),
                        'test_results': ([], [{'test_type': 'anomaly_detection', 'test_sub_type': 'null_percent', 'status': 'fail', 'table_name': 'ORDERS', 'column_name': 'EMAIL'}])})
        with patch.object(observability, 'sql_source', return_value=fake):
            self.run_op('elementary-volume-report', destination='snowflake', elementary_schema='ANALYTICS.ELEMENTARY')
        self.assertIn('ANALYTICS.ELEMENTARY.data_monitoring_metrics', fake.queries[0][1])
        self.assertEqual({r['durum'] for r in self.rows()}, {'normal', 'fail'})


class Incident(Base):
    def test_incident_plan_validates_references(self):
        self.server.add('POST', '/graphql', {'data': {'getEvents': {'edges': [{'node': {'uuid': 'e1', 'eventType': 'volume_anomaly', 'eventState': 'open', 'table': {'fullTableId': 'db:s.orders'}}}],
                                                                   'pageInfo': {}}}})
        bad = {'olaylar': [{'referans': 'db:s.payments', 'olasi_neden': 'x', 'oncelik': 'orta', 'aksiyonlar': ['y']}]}
        good = {'olaylar': [{'referans': 'db:s.orders', 'olasi_neden': 'kaynak gecikmesi', 'oncelik': 'yuksek', 'aksiyonlar': ['kaynağı kontrol et'], 'kapanis_kriteri': 'satır sayısı normal'}],
                'runbook': ['1. etkiyi doğrula']}
        with model_reply(bad, good):
            self.run_op('montecarlo-incident-plan')
        self.assertIn('kaynak gecikmesi', self.read('olay_plani.md'))
