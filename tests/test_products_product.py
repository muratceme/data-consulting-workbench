"""Product analytics packages: daily event reports per platform and the funnel plan agent."""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402


class Reports(Base):
    def summary(self):
        with open(self.out / 'olay_ozeti.csv', encoding='utf-8') as fh:
            return {r['olay']: r for r in csv.DictReader(fh)}

    def test_ga4_run_report(self):
        rows = [{'dimensionValues': [{'value': d}, {'value': e}], 'metricValues': [{'value': str(c)}, {'value': '1'}]}
                for d, e, c in (('20261001', 'purchase', 5), ('20261002', 'purchase', 0), ('20261001', 'page_view', 100), ('20261002', 'page_view', 80))]
        self.server.add('POST', '/v1beta/properties/123:runReport', {'rows': rows})
        self.run_op('ga4-event-report', property_id='123', days=7)
        s = self.summary()
        self.assertEqual((s['page_view']['toplam'], s['purchase']['sifir_gun']), ('180.0', '1'))
        self.assertEqual(self.server.received[0][2]['dimensions'], [{'name': 'date'}, {'name': 'eventName'}])

    def test_segment_volume(self):
        self.server.add('GET', r'/events/volume\?granularity=DAY.*groupBy=eventName', {'data': {'result': [{'eventName': 'Order Completed', 'series': [
            {'time': '2026-10-01T00:00:00Z', 'count': 3}, {'time': '2026-10-02T00:00:00Z', 'count': 4}]}]}})
        self.run_op('segment-event-report')
        self.assertEqual(self.summary()['Order Completed']['toplam'], '7.0')

    def test_mixpanel_and_amplitude(self):
        self.server.add('GET', r'/api/query/events\?project_id=1&event=.*', {'data': {'values': {'Signup': {'2026-10-01': 2, '2026-10-02': 3}}}})
        self.run_op('mixpanel-event-report', project_id='1', events=['Signup'])
        self.assertEqual(self.summary()['Signup']['toplam'], '5.0')
        self.out = self.root / 'amp'
        self.server.add('GET', r'/api/2/events/segmentation\?e=.*', {'data': {'xValues': ['2026-10-01', '2026-10-02'], 'series': [[10, 12]]}})
        self.run_op('amplitude-event-report', events=['Play Song'])
        self.assertEqual(self.summary()['Play Song']['degisim'], '0.2')
        self.assertTrue(self.server.received[-1][3]['Authorization'].startswith('Basic '))

    def test_hubspot_paging(self):
        self.server.add('GET', r'/events/v3/events\?objectType=contact&eventType=pe1_buy.*', {'results': [{'occurredAt': '2026-10-01T10:00:00Z'}], 'paging': {'next': {'after': 'c2'}}})
        self.server.add('GET', r'/events/v3/events\?.*after=c2', {'results': [{'occurredAt': '2026-10-01T11:00:00Z'}]})
        self.run_op('hubspot-event-report', events=['pe1_buy'])
        self.assertEqual(self.summary()['pe1_buy']['toplam'], '2.0')

    def test_adobe_reports(self):
        self.server.add('POST', '/api/acme/reports', {'rows': [{'value': 'Oct 1, 2026', 'data': [100, 40]}]})
        self.run_op('adobe-analytics-event-report', global_company_id='acme', report_suite_id='rs1', metrics=['metrics/occurrences', 'metrics/visitors'])
        self.assertEqual(self.server.received[0][3]['X-Proxy-Global-Company-Id'], 'acme')
        self.assertIn('metrics/visitors', self.summary())


class Funnel(Base):
    def test_ga4_funnel_body_and_validation(self):
        rows = [{'dimensionValues': [{'value': '20261001'}, {'value': e}], 'metricValues': [{'value': '10'}, {'value': '1'}]} for e in ('view_item', 'add_to_cart', 'purchase')]
        self.server.add('POST', '/v1beta/properties/123:runReport', {'rows': rows})
        self.server.add('GET', '/v1beta/properties/123/metadata', {'dimensions': [{'apiName': 'customEvent:plan', 'customDefinition': True}], 'metrics': []})
        bad = {'funneller': [{'ad': 'satın alma', 'adimlar': [{'olay': 'view_item'}, {'olay': 'begin_checkout'}]}]}
        good = {'funneller': [{'ad': 'satın alma', 'adimlar': [{'olay': 'view_item'}, {'olay': 'add_to_cart'}, {'olay': 'purchase'}], 'donusum_penceresi_gun': 3,
                               'kirilimlar': ['customEvent:plan']}], 'eksik_olaylar': [{'olay': 'begin_checkout', 'neden': 'ödeme adımı izlenmiyor', 'ozellikler': ['value']}],
                'kpiler': [{'ad': 'dönüşüm', 'pay': 'purchase', 'payda': 'view_item'}]}
        with model_reply(bad, good):
            self.run_op('ga4-funnel-plan', property_id='123')
        body = self.read('ga4_runFunnelReport.json')[0]
        self.assertEqual(body['funnel']['steps'][1]['withinDurationFromPriorStep'], '259200s')
        self.assertIn('begin_checkout,yeni (izlenmeli)', self.read('izleme_plani.csv'))
