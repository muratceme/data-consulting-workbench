"""Geospatial & IoT: sensor/location field checks (code) and an evidence-bound time-series & location analysis plan (agent)."""
import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import re
import sqlite3
import struct
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode

import native
from products.core import (NativeError, agent_task, aws_call, aws_json, bearer, journal, name, names, need_list, need_text, number, opt_float, opt_int,
                           parse_time, plan_json, receipt, region_of, required, rows_md, secret, sql_source, write_csv, NAME)


# ---------- Shared checks over normalised records {id, cihaz, zaman, lat, lon, <values>} ----------
def run_checks(records, cfg, *, geometry_issues=None):
    now = dt.datetime.now(dt.timezone.utc)
    bbox = cfg.get('bbox')
    if bbox is not None and not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(x, (int, float)) for x in bbox)):
        raise NativeError('bbox [min_lon, min_lat, max_lon, max_lat] olmalı.')
    ranges = cfg.get('value_ranges') or {}
    if not isinstance(ranges, dict):
        raise NativeError('value_ranges {"alan": [min, max]} biçiminde olmalı.')
    interval = cfg.get('expected_interval_seconds')
    stale_hours = opt_float(cfg, 'stale_hours', 24, 0.01, 24 * 365)
    issues = defaultdict(list)
    times = defaultdict(list)
    for r in records:
        ref = str(r.get('id') or r.get('cihaz') or '')
        lat, lon = number(r.get('lat')), number(r.get('lon'))
        if 'lat' in r or 'lon' in r:
            if lat is None or lon is None:
                issues['konum eksik'].append(ref)
            elif not (-90 <= lat <= 90 and -180 <= lon <= 180):
                issues['koordinat aralık dışı (WGS84)'].append(ref)
            elif abs(lat) < 1e-9 and abs(lon) < 1e-9:
                issues['0,0 koordinatı (null island)'].append(ref)
            elif bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                issues['bbox dışında'].append(ref)
        if 'zaman' in r:
            t = parse_time(r.get('zaman'))
            if t is None:
                issues['zaman okunamadı/eksik'].append(ref)
            else:
                if t > now + dt.timedelta(minutes=5):
                    issues['gelecek zaman damgası'].append(ref)
                times[str(r.get('cihaz') or '')].append(t)
        for field, bounds in ranges.items():
            if not (isinstance(bounds, list) and len(bounds) == 2):
                raise NativeError(f'value_ranges.{field} [min, max] olmalı.')
            v = number(r.get(field))
            if v is None:
                issues[f'{field} eksik/sayısal değil'].append(ref)
            elif not bounds[0] <= v <= bounds[1]:
                issues[f'{field} aralık dışı [{bounds[0]}, {bounds[1]}]'].append(ref)
    for device, values in times.items():
        values.sort()
        dup = sum(1 for a, b in zip(values, values[1:]) if a == b)
        if dup:
            issues['aynı cihaz+zaman tekrarı'].extend([device] * dup)
        if interval:
            gaps = [(a, b) for a, b in zip(values, values[1:]) if (b - a).total_seconds() > interval * 1.5]
            for a, b in gaps:
                issues[f'veri boşluğu > {interval} sn'].append(f'{device} {a.isoformat()}→{b.isoformat()}')
        if values and (now - values[-1]).total_seconds() > stale_hours * 3600:
            issues[f'{stale_hours:g} saattir veri yok'].append(device or '(tek seri)')
    for key, items in (geometry_issues or {}).items():
        issues[key].extend(items)
    rows = [{'kontrol': k, 'adet': len(v), 'ornekler': '; '.join(v[:5])} for k, v in sorted(issues.items())]
    return rows


def check_report(out, label, records, rows, extra=None):
    write_csv(out, 'kontrol_sonuclari.csv', rows, ['kontrol', 'adet', 'ornekler'])
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, 'kayit': len(records), 'sorunlu_kontrol': len(rows), 'toplam_bulgu': sum(r['adet'] for r in rows), **(extra or {})})


def field_profile(records):
    keys = sorted({k for r in records for k in r})
    return [{'alan': k, 'dolu': sum(1 for r in records if r.get(k) not in (None, '')), 'sayisal': sum(1 for r in records if number(r.get(k)) is not None)} for k in keys]


# ---------- ArcGIS feature layer ----------
def arcgis_read(cfg, factory, out):
    layer = required(cfg, 'layer_url')
    if not re.search(r'/(FeatureServer|MapServer)/\d+/?$', layer):
        raise NativeError('layer_url …/FeatureServer/0 biçiminde olmalı.')
    client = factory(layer, {'X-Esri-Authorization': 'Bearer ' + secret('ARCGIS_TOKEN')})
    info = client.json('GET', '../' + layer.rstrip('/').rsplit('/', 1)[1] + '?f=json')  # base ends with /; ArcGIS layer info has no trailing slash
    if info.get('error'):
        raise NativeError('ArcGIS katman bilgisi okunamadı (yetki/URL).')
    fields = [f.get('name') for f in info.get('fields', [])]
    id_field, time_field = cfg.get('id_field') or info.get('objectIdField') or 'OBJECTID', cfg.get('time_field')
    device_field = cfg.get('device_field')
    for f in (time_field, device_field):
        if f and f not in fields:
            raise NativeError('Katmanda olmayan alan: ' + f)
    records, offset, limit = [], 0, opt_int(cfg, 'max_features', 50000, 1, 200000)
    geometry_issues = defaultdict(list)
    for _ in range(500):
        q = {'where': cfg.get('where', '1=1'), 'outFields': '*', 'returnGeometry': 'true', 'outSR': '4326', 'f': 'json',
             'resultOffset': str(offset), 'resultRecordCount': '2000', 'orderByFields': id_field}
        r = client.json('GET', 'query?' + urlencode(q))
        if r.get('error'):
            raise NativeError('ArcGIS sorgusu hata döndürdü; where ifadesini ve yetkiyi kontrol edin.')
        for feat in r.get('features', []):
            attrs, geom = feat.get('attributes') or {}, feat.get('geometry')
            rec = {k: v for k, v in attrs.items()}
            rec['id'] = attrs.get(id_field)
            if device_field:
                rec['cihaz'] = attrs.get(device_field)
            if time_field:
                rec['zaman'] = attrs.get(time_field)
            if not geom:
                geometry_issues['geometri yok'].append(str(rec['id']))
            elif 'x' in geom:
                rec['lon'], rec['lat'] = geom.get('x'), geom.get('y')
            elif not (geom.get('paths') or geom.get('rings') or geom.get('points')):
                geometry_issues['boş geometri'].append(str(rec['id']))
            records.append(rec)
        offset += len(r.get('features', []))
        if not r.get('exceededTransferLimit') or not r.get('features') or offset >= limit:
            break
    meta = {'geometri_tipi': info.get('geometryType'), 'alanlar': [{'alan': f.get('name'), 'tip': f.get('type')} for f in info.get('fields', [])],
            'koordinat_sistemi': (info.get('extent') or {}).get('spatialReference')}
    return records, geometry_issues, meta


# ---------- QGIS project + GeoPackage layers ----------
def gpkg_point(blob):
    """GeoPackage binary → (x, y) for Point geometries, 'empty' flag or None for other types."""
    if not blob or blob[:2] != b'GP':
        return None, False
    flags = blob[3]
    empty = bool(flags & 0x10)
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get((flags >> 1) & 0x07, 0)
    wkb = blob[8 + envelope:]
    if empty or len(wkb) < 21:
        return None, empty
    order = '<' if wkb[0] == 1 else '>'
    kind = struct.unpack(order + 'I', wkb[1:5])[0] % 1000
    if kind != 1:
        return 'other', False
    return struct.unpack(order + 'dd', wkb[5:21]), False


def qgis_read(cfg, factory, out):
    project = Path(required(cfg, 'project_file'))
    raw = project.read_bytes()
    if project.suffix.lower() == '.qgz':
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(next(n for n in z.namelist() if n.endswith('.qgs')))
    root = ET.fromstring(raw)
    project_crs = root.findtext('.//projectCrs/spatialrefsys/authid') or ''
    wanted = set(names(cfg, 'layers', optional=True, pattern=re.compile(r'[^\x00-\x1f]{1,200}')))
    records, issues, layers = [], defaultdict(list), []
    for ml in root.iter('maplayer'):
        lname, source, provider = ml.findtext('layername') or '', ml.findtext('datasource') or '', ml.findtext('provider') or ''
        crs = ml.findtext('srs/spatialrefsys/authid') or ''
        layers.append({'katman': lname, 'saglayici': provider, 'crs': crs, 'kaynak_turu': source.split('|')[0].rsplit('.', 1)[-1] if '|' in source or '.' in source else provider})
        if wanted and lname not in wanted:
            continue
        if crs and project_crs and crs != project_crs:
            issues['katman CRS proje CRS’sinden farklı'].append(f'{lname} ({crs} ≠ {project_crs})')
        if provider != 'ogr' or '.gpkg' not in source.lower():
            continue
        path_part, _, rest = source.partition('|')
        gpkg = (project.parent / path_part).resolve() if not Path(path_part).is_absolute() else Path(path_part)
        table = dict(p.split('=', 1) for p in rest.split('|') if '=' in p).get('layername')
        if not gpkg.exists() or not table:
            issues['katman kaynağı bulunamadı'].append(lname)
            continue
        con = sqlite3.connect(f'file:{gpkg}?mode=ro', uri=True)
        try:
            meta = con.execute('SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name = ?', (table,)).fetchone()
            if not meta:
                continue
            geom_col, srs = meta
            if not NAME.fullmatch(geom_col) or not NAME.fullmatch(table.replace(' ', '_')):
                raise NativeError('GeoPackage tablo/kolon adı desteklenmiyor: ' + table)
            for fid, blob in con.execute(f'SELECT rowid, "{geom_col}" FROM "{table}" LIMIT 200000'):
                ref = f'{lname}#{fid}'
                if blob is None:
                    issues['geometri yok'].append(ref)
                    continue
                point, empty = gpkg_point(blob)
                if empty:
                    issues['boş geometri'].append(ref)
                elif isinstance(point, tuple):
                    rec = {'id': ref}
                    if srs == 4326:
                        rec['lon'], rec['lat'] = point
                    records.append(rec)
                else:
                    records.append({'id': ref})
        finally:
            con.close()
    return records, issues, {'proje_crs': project_crs, 'katmanlar': layers}


# ---------- PostGIS / TimescaleDB ----------
def postgis_read(cfg, factory, out):
    table, geom, key = name(cfg, 'table'), name(cfg, 'geometry_column'), name(cfg, 'id_column')
    src = sql_source('postgres', cfg, factory, out)
    _, stats = src.query(f'SELECT count(*) AS toplam, count(*) FILTER (WHERE {geom} IS NULL) AS bos, count(*) FILTER (WHERE NOT ST_IsValid({geom})) AS gecersiz, '
                         f'count(*) FILTER (WHERE ST_IsEmpty({geom})) AS bos_geometri, count(DISTINCT ST_SRID({geom})) AS srid_sayisi, min(ST_SRID({geom})) AS srid FROM {table}', 'stats')
    _, invalid = src.query(f'SELECT {key}::text AS id, ST_IsValidReason({geom}) AS neden FROM {table} WHERE {geom} IS NOT NULL AND NOT ST_IsValid({geom}) LIMIT 20', 'invalid')
    extra_cols = [name({'c': c}, 'c') for c in (cfg.get('value_columns') or [])]
    time_col, device_col = cfg.get('time_column'), cfg.get('device_column')
    select = [f'{key}::text AS id', f'ST_Y(ST_Transform(ST_PointOnSurface({geom}), 4326)) AS lat', f'ST_X(ST_Transform(ST_PointOnSurface({geom}), 4326)) AS lon']
    if time_col:
        select.append(f'{name(cfg, "time_column")} AS zaman')
    if device_col:
        select.append(f'{name(cfg, "device_column")}::text AS cihaz')
    select += extra_cols
    _, rows = src.query(f'SELECT {", ".join(select)} FROM {table} WHERE {geom} IS NOT NULL AND NOT ST_IsEmpty({geom}) AND ST_IsValid({geom}) '
                        f'LIMIT {opt_int(cfg, "max_rows", 100000, 1, 1000000)}', 'records')
    s = stats[0] if stats else {}
    issues = defaultdict(list)
    for label, k in (('geometri yok', 'bos'), ('geçersiz geometri (ST_IsValid)', 'gecersiz'), ('boş geometri', 'bos_geometri')):
        n = int(number(s.get(k)) or 0)
        if n:
            issues[label] = [f'{i["id"]}: {i["neden"]}' for i in invalid][:5] if k == 'gecersiz' else [f'{n} kayıt']
            issues[label] += [''] * (n - len(issues[label]))
    if int(number(s.get('srid_sayisi')) or 0) > 1:
        issues['birden fazla SRID'].append(f"{s.get('srid_sayisi')} farklı SRID")
    return rows, issues, {'istatistik': s}


def timescale_read(cfg, factory, out):
    table, time_col, device_col = name(cfg, 'table'), name(cfg, 'time_column'), name(cfg, 'device_column')
    values = [name({'c': c}, 'c') for c in (cfg.get('value_columns') or [])]
    days = opt_int(cfg, 'days', 7, 1, 365)
    src = sql_source('postgres', cfg, factory, out)
    schema, _, tname = table.rpartition('.')
    hyper = src.query('SELECT hypertable_schema, hypertable_name, num_dimensions, num_chunks, compression_enabled FROM timescaledb_information.hypertables '
                      f"WHERE hypertable_name = '{tname}'" + (f" AND hypertable_schema = '{schema}'" if schema else ''), 'hypertable')[1]
    if not hyper:
        raise NativeError('Tablo bir TimescaleDB hypertable değil: ' + table)
    cols = ', '.join([f'{time_col} AS zaman', f'{device_col}::text AS cihaz'] + values)
    _, rows = src.query(f"SELECT {cols} FROM {table} WHERE {time_col} >= now() - interval '{days} days' ORDER BY {device_col}, {time_col} "
                        f'LIMIT {opt_int(cfg, "max_rows", 500000, 1, 2000000)}', 'records')
    return rows, {}, {'hypertable': hyper[0], 'gun': days}


# ---------- InfluxDB v2 (Flux) ----------
FLUX_NAME = re.compile(r'[A-Za-z0-9_.\-]{1,128}')


def influx_csv(raw):
    """Annotated CSV (one or more tables) → list of dicts."""
    rows, header = [], None
    for line in csv.reader(io.StringIO(raw.decode('utf-8'))):
        if not line or not any(line):
            header = None
            continue
        if line[0].startswith('#'):
            continue
        if header is None:
            header = line
            continue
        rows.append(dict(zip(header, line)))
    return rows


def influx_read(cfg, factory, out):
    bucket, measurement = name(cfg, 'bucket', FLUX_NAME), name(cfg, 'measurement', FLUX_NAME)
    device_tag = name(cfg, 'device_tag', FLUX_NAME)
    fields = names(cfg, 'fields', limit=20, pattern=FLUX_NAME)
    days = opt_int(cfg, 'days', 7, 1, 365)
    client = factory(required(cfg, 'server'), {'Authorization': 'Token ' + secret('INFLUX_TOKEN')})
    field_filter = ' or '.join(f'r._field == "{f}"' for f in fields)
    flux = (f'from(bucket: "{bucket}") |> range(start: -{days}d) |> filter(fn: (r) => r._measurement == "{measurement}" and ({field_filter})) '
            f'|> pivot(rowKey: ["_time", "{device_tag}"], columnKey: ["_field"], valueColumn: "_value") '
            f'|> keep(columns: ["_time", "{device_tag}", {", ".join(json.dumps(f) for f in fields)}]) |> limit(n: {opt_int(cfg, "max_rows_per_series", 50000, 1, 500000)})')
    raw, _ = client.request('POST', '/api/v2/query?' + urlencode({'org': required(cfg, 'org')}), {'query': flux, 'type': 'flux', 'dialect': {'annotations': [], 'header': True}},
                            accept='application/csv')
    rows = [{'zaman': r.get('_time'), 'cihaz': r.get(device_tag), **{f: r.get(f) for f in fields}} for r in influx_csv(raw)]
    return rows, {}, {'bucket': bucket, 'olcum': measurement, 'alanlar': fields, 'flux': flux}


# ---------- Device registries ----------
def iothub_sas(host, policy, key, ttl=3600):
    expiry = str(int(time.time()) + ttl)
    resource = quote(host, safe='')
    signature = base64.b64encode(hmac.new(base64.b64decode(key), f'{resource}\n{expiry}'.encode(), hashlib.sha256).digest()).decode()
    return f'SharedAccessSignature sr={resource}&sig={quote(signature, safe="")}&se={expiry}&skn={policy}'


def location_of(reported, path):
    node = reported
    for part in path.split('.'):
        node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        return node.get('lat', node.get('latitude')), node.get('lon', node.get('lng', node.get('longitude')))
    return None, None


def iothub_read(cfg, factory, out):
    hub = required(cfg, 'hub_name')
    if not re.fullmatch(r'[A-Za-z0-9-]{3,50}', hub):
        raise NativeError('hub_name geçersiz.')
    host = hub + '.azure-devices.net'
    client = factory('https://' + host, {'Authorization': iothub_sas(host, secret('IOTHUB_POLICY_NAME'), secret('IOTHUB_POLICY_KEY'))})
    loc = cfg.get('location_property', 'location')
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,100}', loc):
        raise NativeError('location_property geçersiz.')
    records, token = [], None
    for _ in range(200):
        headers = {'x-ms-continuation': token} if token else {}
        client.headers.update(headers)
        raw, resp = client.request('POST', '/devices/query?api-version=2021-04-12', {'query': 'SELECT deviceId, connectionState, lastActivityTime, properties.reported FROM devices'})
        items = json.loads(raw) if raw else []
        for d in items if isinstance(items, list) else []:
            reported = (d.get('properties') or {}).get('reported') or d.get('reported') or {}
            lat, lon = location_of(reported, loc)
            last = d.get('lastActivityTime')
            records.append({'id': d.get('deviceId'), 'cihaz': d.get('deviceId'), 'zaman': None if str(last).startswith('0001-01-01') else last,
                            'lat': lat, 'lon': lon, 'baglanti': d.get('connectionState')})
        token = native.header(resp, 'x-ms-continuation')
        if not token:
            break
    issues = {'bağlı değil': [r['id'] for r in records if r.get('baglanti') == 'Disconnected']}
    return records, {k: v for k, v in issues.items() if v}, {'cihaz': len(records), 'konum_ozelligi': loc}


def iotcore_read(cfg, factory, out):
    region = region_of(cfg)
    loc = cfg.get('location_property', 'location')
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,100}', loc):
        raise NativeError('location_property geçersiz.')
    records, token = [], None
    for _ in range(200):
        body = {'indexName': 'AWS_Things', 'queryString': cfg.get('query_string', 'thingName:*'), 'maxResults': 250, **({'nextToken': token} if token else {})}
        raw = aws_call(factory, 'iot', region, method='POST', path='/indices/search', payload=body, content_type='application/json')
        r = native.json_object(raw)
        for thing in r.get('things', []):
            shadow = json.loads(thing.get('shadow') or '{}')
            reported = ((shadow.get('reported') or (shadow.get('state') or {}).get('reported')) or {})
            lat, lon = location_of(reported, loc)
            conn = thing.get('connectivity') or {}
            ts = conn.get('timestamp')
            records.append({'id': thing.get('thingName'), 'cihaz': thing.get('thingName'), 'zaman': ts, 'lat': lat, 'lon': lon,
                            'baglanti': 'Connected' if conn.get('connected') else 'Disconnected' if conn else ''})
        token = r.get('nextToken')
        if not token:
            break
    issues = {'bağlı değil': [r['id'] for r in records if r.get('baglanti') == 'Disconnected']}
    return records, {k: v for k, v in issues.items() if v}, {'cihaz': len(records), 'konum_ozelligi': loc}


PRODUCTS = {
    'arcgis': dict(label='ArcGIS', read=arcgis_read, query='arcgis'),
    'qgis': dict(label='QGIS', read=qgis_read, query=None),
    'postgis': dict(label='PostGIS', read=postgis_read, query='sql'),
    'timescaledb': dict(label='TimescaleDB', read=timescale_read, query='timescale'),
    'influxdb': dict(label='InfluxDB', read=influx_read, query='flux'),
    'iot-hub': dict(label='Azure IoT Hub', read=iothub_read, query=None),
    'iot-core': dict(label='AWS IoT Core', read=iotcore_read, query=None),
}


def sensor_check(cfg, out, factory):
    key = cfg['operation'].rsplit('-sensor-check', 1)[0]
    records, geometry_issues, meta = PRODUCTS[key]['read'](cfg, factory, out)
    if not records and not geometry_issues:
        raise NativeError('Platform kayıt döndürmedi; filtre ve yetkiyi kontrol edin.')
    rows = run_checks(records, cfg, geometry_issues=geometry_issues)
    receipt(out, 'kaynak_bilgisi.json', meta)
    return check_report(out, PRODUCTS[key]['label'], records, rows)


# ---------- Analysis plan agent ----------
METHODS = {'yeniden_ornekleme', 'anomali', 'trend', 'mevsimsellik', 'mekansal_birlestirme', 'kumeleme', 'yakinlik', 'isi_haritasi', 'esik_alarmi'}
AGGS = {'mean', 'max', 'min', 'sum', 'count', 'last', 'median'}
SCHEMA = ('{"analizler":[{"ad":"...","soru":"iş sorusu","yontem":"yeniden_ornekleme|anomali|trend|mevsimsellik|mekansal_birlestirme|kumeleme|yakinlik|isi_haritasi|esik_alarmi",'
          '"alanlar":["kanıttaki alan"],"pencere_dakika":15,"toplama":"mean|max|min|sum|count|last|median","notlar":"..."}],'
          '"on_isleme":["boşluk doldurma, CRS dönüşümü vb."],"sorular":["..."]}')


def draft_query(key, cfg, a):
    kind = PRODUCTS[key]['query']
    fields = a.get('alanlar') or []
    minutes = int(a.get('pencere_dakika') or 15)
    agg = a.get('toplama') or 'mean'
    if kind == 'flux':
        return (f'from(bucket: "{cfg["bucket"]}") |> range(start: -30d) |> filter(fn: (r) => r._measurement == "{cfg["measurement"]}" and '
                + ' or '.join(f'r._field == "{f}"' for f in fields) + f') |> aggregateWindow(every: {minutes}m, fn: {agg}, createEmpty: true)')
    if kind == 'timescale':
        sql_agg = {'mean': 'avg', 'median': 'percentile_cont(0.5) WITHIN GROUP (ORDER BY {})', 'last': 'last({}, ' + cfg['time_column'] + ')'}.get(agg, agg)
        exprs = ', '.join((sql_agg.format(f) if '{}' in sql_agg else f'{sql_agg}({f})') + f' AS {f}_{agg}' for f in fields)
        return (f"SELECT time_bucket(INTERVAL '{minutes} minutes', {cfg['time_column']}) AS kova, {cfg['device_column']}, {exprs} FROM {cfg['table']} "
                f"WHERE {cfg['time_column']} >= now() - INTERVAL '30 days' GROUP BY 1, 2 ORDER BY 1;")
    if kind == 'sql':
        return f"SELECT {', '.join(fields) or '*'} FROM {cfg['table']} -- {a['yontem']} için PostGIS taslağı; ST_ClusterDBSCAN/ST_DWithin parametrelerini doğrulayın"
    if kind == 'arcgis':
        stats = [{'statisticType': {'mean': 'avg', 'median': 'avg', 'last': 'max'}.get(agg, agg), 'onStatisticField': f, 'outStatisticFieldName': f'{f}_{agg}'} for f in fields]
        return 'query?' + urlencode({'where': '1=1', 'outStatistics': json.dumps(stats), 'f': 'json'})
    return ''


def analysis_plan(cfg, out, factory):
    key = cfg['operation'].rsplit('-analysis-plan', 1)[0]
    records, geometry_issues, meta = PRODUCTS[key]['read'](cfg, factory, out)
    checks = run_checks(records, cfg, geometry_issues=geometry_issues)
    profile = field_profile(records)
    allowed = {p['alan'].lower(): p['alan'] for p in profile}
    for f in (meta.get('alanlar') or []):
        if isinstance(f, dict) and f.get('alan'):
            allowed[str(f['alan']).lower()] = f['alan']
        elif isinstance(f, str):
            allowed[f.lower()] = f
    evidence = {'kaynak': meta, 'alan_profili': profile, 'kontroller': checks, 'kayit': len(records)}

    def validate(obj):
        for i, a in enumerate(need_list(obj, 'analizler', 'analizler', maximum=20), 1):
            need_text(a, 'ad', f'analiz {i}')
            need_text(a, 'yontem', f'analiz {i}', choices=METHODS)
            fields = a.get('alanlar')
            if not isinstance(fields, list) or not fields:
                raise ValueError(f'analiz {i}: alanlar boş olamaz.')
            bad = [f for f in fields if str(f).lower() not in allowed]
            if bad:
                raise ValueError(f'analiz {i}: kanıtta olmayan alan: ' + ', '.join(map(str, bad)))
            a['alanlar'] = [allowed[str(f).lower()] for f in fields]
            if a.get('toplama') and a['toplama'] not in AGGS:
                raise ValueError(f'analiz {i}: toplama şunlardan biri olmalı: ' + ', '.join(sorted(AGGS)))
            w = a.get('pencere_dakika', 15)
            if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= 43200:
                raise ValueError(f'analiz {i}: pencere_dakika 1–43200 tamsayı olmalı.')
            if PRODUCTS[key]['query'] in ('flux', 'timescale') and not all(re.fullmatch(r'[A-Za-z0-9_.\-]{1,128}', f) for f in a['alanlar']):
                raise ValueError(f'analiz {i}: alan adı sorguya konamıyor.')
        return obj

    def render(result):
        analyses = [{**a, 'sorgu_taslagi': draft_query(key, cfg, a)} for a in result['analizler']]
        return {'analiz_plani.json': plan_json({**result, 'analizler': analyses}),
                'analiz_plani.md': rows_md('Zaman serisi ve konum analizi planı — ' + PRODUCTS[key]['label'],
                                           [('Analizler', analyses, ['ad', 'soru', 'yontem', 'alanlar', 'pencere_dakika', 'toplama', 'sorgu_taslagi']),
                                            ('Ön işleme ve sorular', [{'madde': x} for x in result.get('on_isleme', []) + result.get('sorular', [])], ['madde'])])}
    return agent_task(cfg, out, product=PRODUCTS[key]['label'], title='Zaman serisi ve konum analizi planı', evidence=evidence, schema=SCHEMA,
                      validate=validate, render=render,
                      rules='Kontrol bulgularını (boşluk, aralık dışı değer, CRS farkı) ön işleme adımına dönüştür. Pencere süresini beklenen ölçüm aralığından kısa seçme.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-sensor-check'] = sensor_check
    HANDLERS[_key + '-analysis-plan'] = analysis_plan
