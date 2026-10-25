"""Geospatial & IoT product packages: checks on platform records, GeoPackage parsing, SAS/SigV4 auth and query drafts."""
import datetime as dt
import json
import sqlite3
import struct
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import spatial  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def iso(minutes_ago):
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat()


class Checks(Base):
    def test_run_checks_finds_each_problem(self):
        records = [{'id': '1', 'cihaz': 'a', 'zaman': iso(30), 'lat': 41, 'lon': 29, 't': 20},
                   {'id': '2', 'cihaz': 'a', 'zaman': iso(25), 'lat': 0, 'lon': 0, 't': 200},
                   {'id': '3', 'cihaz': 'a', 'zaman': iso(25), 'lat': 95, 'lon': 29, 't': 'x'},
                   {'id': '4', 'cihaz': 'b', 'zaman': iso(60 * 50), 'lat': 51.5, 'lon': -0.1, 't': 10},
                   {'id': '5', 'cihaz': 'c', 'zaman': (NOW + dt.timedelta(days=1)).isoformat(), 'lat': None, 'lon': None, 't': 1}]
        rows = {r['kontrol']: r['adet'] for r in spatial.run_checks(records, {'bbox': [25, 35, 45, 43], 'value_ranges': {'t': [-40, 85]},
                                                                           'expected_interval_seconds': 60, 'stale_hours': 24})}
        self.assertEqual(rows['0,0 koordinatı (null island)'], 1); self.assertEqual(rows['koordinat aralık dışı (WGS84)'], 1)
        self.assertEqual(rows['bbox dışında'], 1); self.assertEqual(rows['konum eksik'], 1); self.assertEqual(rows['gelecek zaman damgası'], 1)
        self.assertEqual(rows['aynı cihaz+zaman tekrarı'], 1); self.assertEqual(rows['24 saattir veri yok'], 1)
        self.assertEqual(rows['t aralık dışı [-40, 85]'], 1); self.assertEqual(rows['t eksik/sayısal değil'], 1)
        self.assertIn('veri boşluğu > 60 sn', rows)

    def test_arcgis_paging_and_token_header(self):
        self.server.add('GET', r'/o/arcgis/rest/services/s/FeatureServer/0\?f=json', {'objectIdField': 'OBJECTID', 'geometryType': 'esriGeometryPoint',
                                                                                    'fields': [{'name': 'OBJECTID', 'type': 'esriFieldTypeOID'}]})
        self.server.add('GET', r'/o/arcgis/rest/services/s/FeatureServer/0/query\?.*resultOffset=0.*', {'exceededTransferLimit': True,
                        'features': [{'attributes': {'OBJECTID': 1}, 'geometry': {'x': 29, 'y': 41}}, {'attributes': {'OBJECTID': 2}}]})
        self.server.add('GET', r'/o/arcgis/rest/services/s/FeatureServer/0/query\?.*resultOffset=2.*', {'features': [{'attributes': {'OBJECTID': 3}, 'geometry': {'x': 0, 'y': 0}}]})
        self.run_op('arcgis-sensor-check', layer_url='https://services.arcgis.com/o/arcgis/rest/services/s/FeatureServer/0')
        self.assertEqual(self.server.received[0][3]['X-Esri-Authorization'], 'Bearer test-only-secret')
        self.assertIn('outSR=4326', self.server.received[1][1])
        csv_text = self.read('kontrol_sonuclari.csv')
        self.assertIn('geometri yok,1,2', csv_text); self.assertIn('0,0 koordinatı', csv_text)

    def test_qgis_project_with_geopackage(self):
        gpkg = self.root / 'data.gpkg'
        con = sqlite3.connect(gpkg)
        con.execute('CREATE TABLE gpkg_geometry_columns (table_name, column_name, srs_id)')
        con.execute("INSERT INTO gpkg_geometry_columns VALUES ('stations', 'geom', 4326)")
        con.execute('CREATE TABLE stations (fid INTEGER PRIMARY KEY, geom BLOB)')
        point = lambda x, y: b'GP\x00\x01' + struct.pack('<i', 4326) + b'\x01' + struct.pack('<Idd', 1, x, y)
        empty = b'GP\x00\x11' + struct.pack('<i', 4326) + b'\x01' + struct.pack('<I', 1)
        con.executemany('INSERT INTO stations (geom) VALUES (?)', [(point(29.0, 41.0),), (point(200.0, 41.0),), (None,), (empty,)])
        con.commit(); con.close()
        (self.root / 'p.qgs').write_text('<qgis><projectCrs><spatialrefsys><authid>EPSG:3857</authid></spatialrefsys></projectCrs><projectlayers>'
                                         '<maplayer><layername>İstasyonlar</layername><provider>ogr</provider><datasource>./data.gpkg|layername=stations</datasource>'
                                         '<srs><spatialrefsys><authid>EPSG:4326</authid></spatialrefsys></srs></maplayer></projectlayers></qgis>', encoding='utf-8')
        self.run_op('qgis-sensor-check', project_file='p.qgs')
        rows = self.read('kontrol_sonuclari.csv')
        for expected in ('koordinat aralık dışı (WGS84),1', 'geometri yok,1', 'boş geometri,1', 'katman CRS proje CRS’sinden farklı,1'):
            self.assertIn(expected, rows)

    def test_postgis_stats_and_records(self):
        fake = FakeSql({'stats': ([], [{'toplam': 3, 'bos': 1, 'gecersiz': 1, 'bos_geometri': 0, 'srid_sayisi': 2, 'srid': 4326}]),
                        'invalid': ([], [{'id': '7', 'neden': 'Self-intersection[1 2]'}]), 'records': ([], [{'id': '1', 'lat': 41, 'lon': 29}])})
        with patch.object(spatial, 'sql_source', return_value=fake):
            self.run_op('postgis-sensor-check', host='h', database='d', user='u', table='public.parcels', geometry_column='geom', id_column='id')
        rows = self.read('kontrol_sonuclari.csv')
        self.assertIn('geçersiz geometri (ST_IsValid),1,7: Self-intersection[1 2]', rows); self.assertIn('birden fazla SRID', rows)

    def test_timescale_requires_hypertable(self):
        with patch.object(spatial, 'sql_source', return_value=FakeSql({'hypertable': ([], [])})), self.assertRaises(native.NativeError):
            self.run_op('timescaledb-sensor-check', host='h', database='d', user='u', table='public.m', time_column='time', device_column='device_id')

    def test_influx_flux_annotated_csv(self):
        body = (',result,table,_time,device,temperature\r\n,_result,0,' + iso(10) + ',d1,21.5\r\n,_result,0,' + iso(5) + ',d1,99\r\n\r\n').encode()
        self.server.add('POST', r'/api/v2/query\?org=acme', body)
        self.run_op('influxdb-sensor-check', server='https://i.example', org='acme', bucket='b', measurement='sensor', device_tag='device', fields=['temperature'],
                    value_ranges={'temperature': [-40, 85]})
        self.assertEqual(self.server.received[0][3]['Authorization'], 'Token test-only-secret')
        self.assertIn('r._field == "temperature"', self.server.received[0][2]['query'])
        self.assertIn('"temperature aralık dışı [-40, 85]",1,d1', self.read('kontrol_sonuclari.csv'))

    def test_influx_rejects_injection(self):
        with self.assertRaises(native.NativeError):
            self.run_op('influxdb-sensor-check', server='https://i.example', org='acme', bucket='b") |> drop(', measurement='m', device_tag='d', fields=['t'])
        self.assertFalse(self.server.received)

    def test_iothub_sas_query_and_continuation(self):
        self.server.add('POST', r'/devices/query\?api-version=2021-04-12', [{'deviceId': 'd1', 'connectionState': 'Disconnected', 'lastActivityTime': iso(3000),
                                                                             'properties': {'reported': {'location': {'lat': 41, 'lon': 29}}}}], headers={'x-ms-continuation': 'c1'})
        self.server.add('POST', r'/devices/query\?api-version=2021-04-12', [{'deviceId': 'd2', 'lastActivityTime': '0001-01-01T00:00:00Z', 'properties': {'reported': {}}}])
        with patch.dict('os.environ', {'IOTHUB_POLICY_KEY': 'a2V5'}):
            self.run_op('iot-hub-sensor-check', hub_name='myhub')
        self.assertRegex(self.server.received[0][3]['Authorization'], r'^SharedAccessSignature sr=myhub.azure-devices.net&sig=[^&]+&se=\d+&skn=test-only-secret$')
        self.assertEqual(self.server.received[1][3]['X-Ms-Continuation'], 'c1')
        rows = self.read('kontrol_sonuclari.csv')
        self.assertIn('bağlı değil,1,d1', rows); self.assertIn('konum eksik,1,d2', rows); self.assertIn('zaman okunamadı/eksik,1,d2', rows)

    def test_iot_core_fleet_index(self):
        self.server.add('POST', '/indices/search', {'things': [{'thingName': 't1', 'shadow': json.dumps({'reported': {'location': {'latitude': 41, 'longitude': 29}}}),
                                                                'connectivity': {'connected': True, 'timestamp': int(NOW.timestamp() * 1000)}}]})
        self.run_op('iot-core-sensor-check', region='eu-central-1')
        self.assertIn('/eu-central-1/iot/aws4_request', self.server.received[0][3]['Authorization'])
        self.assertEqual(self.read('ozet.json')['toplam_bulgu'], 0)

    def test_analysis_plan_builds_timescale_query(self):
        fake = FakeSql({'hypertable': ([], [{'hypertable_name': 'm'}]), 'records': ([], [{'zaman': iso(5), 'cihaz': 'd1', 'temperature': 20}])})
        reply = {'analizler': [{'ad': 'saatlik ortalama', 'soru': 'trend', 'yontem': 'yeniden_ornekleme', 'alanlar': ['TEMPERATURE'], 'pencere_dakika': 60, 'toplama': 'mean'}],
                 'on_isleme': [], 'sorular': []}
        bad = {'analizler': [{'ad': 'x', 'yontem': 'trend', 'alanlar': ['humidity'], 'pencere_dakika': 60}]}
        with patch.object(spatial, 'sql_source', return_value=fake), model_reply(bad, reply):
            self.run_op('timescaledb-analysis-plan', host='h', database='d', user='u', table='public.m', time_column='time', device_column='device_id', value_columns=['temperature'])
        plan = self.read('analiz_plani.json')
        self.assertEqual(plan['analizler'][0]['sorgu_taslagi'],
                         "SELECT time_bucket(INTERVAL '60 minutes', time) AS kova, device_id, avg(temperature) AS temperature_mean FROM public.m "
                         "WHERE time >= now() - INTERVAL '30 days' GROUP BY 1, 2 ORDER BY 1;")
