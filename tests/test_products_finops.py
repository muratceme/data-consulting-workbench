"""FinOps product packages: billing summaries and savings agents (savings amounts are computed by code, never by the model)."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import finops  # noqa: E402

ROWS = [{'servis': 'BigQuery', 'kalem': 'Analysis', 'kapsam': 'p1', 'gun': '2026-10-01', 'maliyet': '10', 'kredi': '-1', 'para_birimi': 'USD'},
        {'servis': 'BigQuery', 'kalem': 'Storage', 'kapsam': 'p1', 'gun': '2026-10-02', 'maliyet': '30', 'kredi': '0', 'para_birimi': 'USD'},
        {'servis': 'Compute Engine', 'kalem': 'N2', 'kapsam': 'p2', 'gun': '2026-10-02', 'maliyet': '60', 'kredi': '0', 'para_birimi': 'USD'}]
BQ = {'project_id': 'my-project', 'location': 'EU', 'billing_table': 'my-project.billing.gcp_billing_export_v1_ABC'}


class FinOps(Base):
    def test_bigquery_billing_summary(self):
        fake = FakeSql({'billing': ([], [dict(r) for r in ROWS])})
        with patch.object(finops, 'sql_source', return_value=fake):
            self.run_op('bigquery-billing-cost-summary', **BQ, days=14)
        self.assertIn('INTERVAL 14 DAY', fake.queries[0][1]); self.assertIn('UNNEST(credits)', fake.queries[0][1])
        summary = self.read('ozet.json')
        self.assertEqual((summary['toplam'], summary['kredi']), (100.0, -1.0))
        self.assertEqual(summary['servis'][0], {'ad': 'Compute Engine', 'maliyet': 60.0, 'pay': 0.6})
        self.assertEqual(summary['trend'], {'ilk_yari': 10.0, 'ikinci_yari': 90.0, 'degisim': 8.0})

    def test_billing_table_name_is_validated(self):
        with patch.object(finops, 'sql_source') as source, self.assertRaises(native.NativeError):
            self.run_op('bigquery-billing-cost-summary', **{**BQ, 'billing_table': 'x`; DROP'})
        source.assert_not_called()

    def test_fabric_cost_management_paging_and_capacities(self):
        page = {'properties': {'columns': [{'name': 'Cost'}, {'name': 'UsageDate'}, {'name': 'ResourceId'}, {'name': 'MeterSubCategory'}, {'name': 'Currency'}],
                               'rows': [[12.5, 20261001, '/subscriptions/s/resourceGroups/r/providers/Microsoft.Fabric/capacities/cap1', 'F64', 'EUR']],
                               'nextLink': '/subscriptions/s/providers/Microsoft.CostManagement/query?api-version=2023-11-01&$skiptoken=x'}}
        self.server.add('POST', r'/subscriptions/s/providers/Microsoft.CostManagement/query\?api-version=2023-11-01', page)
        self.server.add('POST', r'/subscriptions/s/providers/Microsoft.CostManagement/query\?api-version=2023-11-01&\$skiptoken=x', {'properties': {'columns': [], 'rows': []}})
        self.server.add('GET', '/v1/capacities', {'value': [{'displayName': 'cap1', 'sku': 'F64', 'region': 'West Europe', 'state': 'Active'}]})
        self.run_op('fabric-capacity-cost-summary', subscription_id='s')
        body = self.server.received[0][2]
        self.assertEqual(body['dataset']['filter']['dimensions']['values'], ['microsoft.fabric/capacities'])
        self.assertIn('2026-10-01,Microsoft Fabric,F64,cap1,12.5', self.read('maliyet_detay.csv'))
        self.assertIn('cap1,F64', self.read('kapasiteler.csv'))
        self.assertEqual(self.read('ozet.json')['toplam'], 12.5)

    def test_savings_amount_comes_from_evidence_not_model(self):
        reply = {'aksiyonlar': [{'hedef': 'Compute Engine', 'tur': 'rezervasyon_taahhut', 'aksiyon': 'CUD', 'tahmini_tasarruf_orani': 0.3, 'kanit': '60 USD', 'risk': 'taahhüt', 'oncelik': 'yuksek',
                                 'tahmini_tasarruf': 999999}], 'kapasite': [], 'sorular': []}
        with patch.object(finops, 'sql_source', return_value=FakeSql({'billing': ([], [dict(r) for r in ROWS])})), model_reply(reply):
            self.run_op('bigquery-billing-savings-plan', **BQ)
        plan = self.read('tasarruf_plani.json')
        self.assertEqual(plan['aksiyonlar'][0]['tahmini_tasarruf'], 18.0)

    def test_savings_rejects_unknown_target_and_bad_rate(self):
        bad1 = {'aksiyonlar': [{'hedef': 'Kubernetes', 'tur': 'diger', 'aksiyon': 'x', 'tahmini_tasarruf_orani': 0.1, 'oncelik': 'orta'}]}
        bad2 = {'aksiyonlar': [{'hedef': 'BigQuery', 'tur': 'diger', 'aksiyon': 'x', 'tahmini_tasarruf_orani': 1.5, 'oncelik': 'orta'}]}
        with patch.object(finops, 'sql_source', return_value=FakeSql({'billing': ([], [dict(r) for r in ROWS])})), model_reply(bad1, bad2):
            with self.assertRaises(native.NativeError):
                self.run_op('bigquery-billing-savings-plan', **BQ)

    def test_snowflake_savings_evidence(self):
        fake = FakeSql({'credits': ([], [{'kaynak': 'BI_WH', 'kredi': '120'}]), 'queries': ([], [{'kaynak': 'BI_WH', 'sorgu': 5}]),
                        'warehouses': ([], [{'name': 'BI_WH', 'size': 'X-Large', 'auto_suspend': 3600}])})
        reply = {'aksiyonlar': [{'hedef': 'BI_WH', 'tur': 'otomatik_durdur', 'aksiyon': 'AUTO_SUSPEND=60', 'tahmini_tasarruf_orani': 0.25, 'oncelik': 'yuksek'}]}
        with patch.object(finops, 'sql_source', return_value=fake), model_reply(reply):
            self.run_op('snowflake-usage-savings-plan', server='https://x.snowflakecomputing.com', warehouse='W', role='R')
        self.assertEqual(self.read('tasarruf_plani.json')['aksiyonlar'][0]['tahmini_tasarruf'], 30.0)
        self.assertEqual(self.read('kanit.json')['depolar'][0]['otomatik_durdurma_sn'], 3600)
