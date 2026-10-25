"""Planning product packages: plan exports per tool, budget vs actual variance and the scenario agent."""
import csv
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import planning  # noqa: E402


class Variance(Base):
    def rows(self):
        with open(self.out / 'butce_sapma.csv', encoding='utf-8') as fh:
            return {(r[list(r)[0]], r[list(r)[1]]): r for r in csv.DictReader(fh)}

    def test_sac_two_versions(self):
        budget = {'value': [{'Account': 'Gelir', 'Date': '202601', 'SignedData': 100}, {'Account': 'Gider', 'Date': '202601', 'SignedData': 50}]}
        actual = {'value': [{'Account': 'Gelir', 'Date': '202601', 'SignedData': 120}, {'Account': 'Gider', 'Date': '202601', 'SignedData': 51}]}
        self.server.add('GET', r"/api/v1/dataexport/providers/sac/M1/FactData\?\$select=Account,Date,SignedData&\$filter=Version%20eq%20'public.Budget'", budget)
        self.server.add('GET', r"/api/v1/dataexport/providers/sac/M1/FactData\?\$select=Account,Date,SignedData&\$filter=Version%20eq%20'public.Actual'", actual)
        self.run_op('sac-planning-budget-variance', tenant_url='https://t.sapanalytics.cloud', provider_id='M1', dimensions=['Account', 'Date'], measure='SignedData',
                    actual_measure='SignedData', budget_version='public.Budget', actual_version='public.Actual', keys=['Account', 'Date'], tolerance_pct=5)
        rows = self.rows()
        self.assertEqual((rows[('Gelir', '202601')]['fark_yuzde'], rows[('Gelir', '202601')]['durum']), ('20.0', 'eşik üstü sapma'))
        self.assertEqual(rows[('Gider', '202601')]['durum'], 'eşik içinde')

    def test_tm1_mdx_cells_and_actuals_file(self):
        (self.root / 'butce.mdx').write_text('SELECT {[Period].[Jan]} ON COLUMNS, {[Account].[Sales]} ON ROWS FROM [Plan]')
        (self.root / 'gerceklesen.csv').write_text('Account,Period,tutar\nSales,Jan,90\nOther,Jan,5\n')
        self.server.add('POST', r'/api/v1/ExecuteMDX\?.*', {'Axes': [{'Hierarchies': [{'Name': 'Period'}], 'Tuples': [{'Members': [{'Name': 'Jan'}]}]},
                                                                    {'Hierarchies': [{'Name': 'Account'}], 'Tuples': [{'Members': [{'Name': 'Sales'}]}]}],
                                                           'Cells': [{'Ordinal': 0, 'Value': 100}]})
        self.run_op('planning-analytics-budget-variance', server='https://tm1.example', cube='Plan', mdx_file='butce.mdx', keys=['Account', 'Period'], actuals_file='gerceklesen.csv', actual_measure='tutar')
        rows = self.rows()
        self.assertEqual(rows[('Sales', 'Jan')]['fark'], '-10.0')
        self.assertEqual(rows[('Other', 'Jan')]['durum'], 'yalnız gerçekleşende')

    def test_tm1_rejects_non_select(self):
        (self.root / 'b.mdx').write_text('CREATE MEMBER x')
        with self.assertRaises(native.NativeError):
            self.run_op('planning-analytics-budget-variance', server='https://tm1.example', cube='Plan', mdx_file='b.mdx', keys=['Account'])
        self.assertFalse(self.server.received)

    def test_anaplan_bulk_export(self):
        ws, model = 'a' * 32, 'b' * 32
        base = f'/2/0/workspaces/{ws}/models/{model}'
        self.server.add('POST', '/token/authenticate', {'tokenInfo': {'tokenValue': 'tk'}})
        self.server.add('POST', f'{base}/exports/116000000001/tasks', {'task': {'taskId': 't1'}})
        self.server.add('GET', f'{base}/exports/116000000001/tasks/t1', {'task': {'taskState': 'COMPLETE', 'result': {'successful': True}}})
        self.server.add('GET', f'{base}/files/116000000001/chunks', {'chunks': [{'id': '0'}, {'id': '1'}]})
        self.server.add('GET', f'{base}/files/116000000001/chunks/0', b'Account,Period,Value\nSales,Jan,')
        self.server.add('GET', f'{base}/files/116000000001/chunks/1', b'100\n')
        (self.root / 'gerceklesen.csv').write_text('Account,Period,tutar\nSales,Jan,100\n')
        self.run_op('anaplan-budget-variance', workspace_id=ws, model_id=model, export_id='116000000001', keys=['Account', 'Period'], measure='Value', actuals_file='gerceklesen.csv', actual_measure='tutar')
        self.assertEqual(self.server.received[1][3]['Authorization'], 'AnaplanAuthToken tk')
        self.assertEqual(self.rows()[('Sales', 'Jan')]['durum'], 'eşik içinde')

    def test_adaptive_export_data(self):
        out = '<?xml version="1.0"?><response success="true"><output><![CDATA[Account Code,Level Name,Value\nREV,TR,100\n]]></output></response>'
        self.server.add('POST', '/api/v41', out.encode())
        self.server.add('POST', '/api/v41', out.replace('100', '80').encode())
        self.run_op('adaptive-planning-budget-variance', budget_version='Budget', actual_version='Actuals', accounts=['REV'], keys=['Account Code', 'Level Name'],
                    measure='Value', actual_measure='Value')
        self.assertIn('<version name="Budget"', self.server.received[0][2])
        self.assertNotIn('test-only-secret', json.dumps(self.read('ozet.json')))
        self.assertEqual(self.rows()[('REV', 'TR')]['fark_yuzde'], '-20.0')


class Scenario(Base):
    def test_drivers_validated(self):
        (self.root / 'b.csv').write_text('Account,Period,deger\nSales,Jan,100\nCOGS,Jan,60\n')
        bad = {'suruculer': [{'ad': 'fiyat', 'boyut': 'Marketing'}], 'tahmin_yontemleri': [{'kalem': 'Sales', 'yontem': 'trend'}], 'senaryolar': [{'ad': 'x', 'varsayimlar': []}]}
        good = {'suruculer': [{'ad': 'fiyat', 'boyut': 'Sales'}], 'tahmin_yontemleri': [{'kalem': 'Sales', 'yontem': 'surucu_bazli'}, {'kalem': 'COGS', 'yontem': 'yuzde_buyume'}],
                'senaryolar': [{'ad': 'Kötümser', 'varsayimlar': [{'surucu': 'fiyat', 'degisim_yuzde': -10}]}]}
        with model_reply(bad, good):
            self.run_op('board-scenario-design', budget_file='b.csv', keys=['Account', 'Period'])
        self.assertIn('Kötümser,fiyat,-10', self.read('senaryo_varsayimlari.csv'))
