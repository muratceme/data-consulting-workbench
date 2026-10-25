"""Business intelligence: automatic report preparation through the vendor's own render/export engine (code) and an
evidence-bound KPI & dashboard design (agent).

The code packages never draw charts themselves: the platform renders the existing sheet/report/dashboard (PDF, PPTX,
XLSX, CSV) and the file is downloaded and checked. Desktop tools are covered by the local template builders
(pbi-rapor-sablonu, tableau-rapor-sablonu); their agents read the local project/workbook files."""
import base64
import csv
import hashlib
import io
import json
import os
import re
import socket
import ssl
import struct
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import native
from products.core import (NativeError, agent_task, bearer, driver, ident, journal, name, names, need_list, need_text, odata_metadata, opt_int,
                           plan_json, receipt, required, rows_md, secret, summarize, write_artifact, write_csv, NAME)


# ---------- Rendered files ----------
MAGIC = {'pdf': lambda b: b.startswith(b'%PDF'), 'png': lambda b: b.startswith(b'\x89PNG'),
         'xlsx': lambda b: zipfile.is_zipfile(io.BytesIO(b)), 'pptx': lambda b: zipfile.is_zipfile(io.BytesIO(b)),
         'zip': lambda b: zipfile.is_zipfile(io.BytesIO(b)), 'csv': lambda b: not b.lstrip().startswith((b'{', b'<')), 'jpg': lambda b: b.startswith(b'\xff\xd8')}


def save(out, filename, raw):
    ext = filename.rsplit('.', 1)[-1]
    if not raw or not MAGIC[ext](raw):
        raise NativeError(f'Platform {ext.upper()} yerine hata/giriş sayfası döndürdü; dosya kaydedilmedi.')
    return write_artifact(out, filename, raw)


def poll_json(client, path, state, done, failed, attempts=150, delay=2):
    for _ in range(attempts):
        r = client.json('GET', path)
        s = state(r)
        if s in done:
            return r
        if s in failed:
            raise NativeError('Platform işi başarısız: ' + str(s))
        time.sleep(delay)
    raise NativeError('Dışa aktarma zaman aşımına uğradı; iş kimliği run-state.json içinde.')


# ---------- Qlik Cloud ----------
def qlik_cloud_export(cfg, out, factory):
    client = native.qlik_client(cfg, factory)
    app, sheet = required(cfg, 'app_id'), required(cfg, 'sheet_id')
    kind = cfg.get('format', 'pdf')
    if kind not in ('pdf', 'pptx'):
        raise NativeError('format pdf veya pptx olmalı.')
    output = {'outputId': 'datawork', 'type': kind}
    if kind == 'pdf':
        output['pdfOutput'] = {'size': cfg.get('page_size', 'A4'), 'orientation': 'A', 'resizeType': 'autofit'}
    body = {'type': 'sense-sheet-1.0', 'senseSheetTemplate': {'appId': app, 'selectionsByState': cfg.get('selections_by_state') or {}, 'sheet': {'id': sheet}}, 'output': output}
    _, headers, status = client.request('POST', '/api/v1/reports', body, with_status=True)
    location = native.header(headers, 'Location')
    if status != 202 or not location:
        raise NativeError('Qlik raporlama isteği kabul edilmedi.')
    status_path = urlsplit(location).path
    if not re.fullmatch(r'/api/v1/reports/[A-Za-z0-9-]+/status', status_path):
        raise NativeError('Qlik durum adresi beklenen biçimde değil.')
    journal(out, report_status=status_path, state='rendering')
    done = poll_json(client, status_path, lambda r: r.get('status'), {'done'}, {'failed', 'aborted'})
    result = next((r for r in done.get('results', []) if r.get('outputId') == 'datawork'), None)
    if not result:
        raise NativeError('Qlik rapor çıktısı bulunamadı.')
    raw, _ = client.request('GET', urlsplit(result['location']).path, accept='application/octet-stream')
    artifact = save(out, 'rapor.' + kind, raw)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'app_id': app, 'sheet_id': sheet, 'format': kind, 'dosya': artifact})
    return artifact


# ---------- Qlik Sense Enterprise (Engine API over WebSocket) ----------
class WebSocket:
    """Minimal RFC 6455 client (text frames, client masking, ping/pong) for the Qlik Engine JSON-RPC API."""
    def __init__(self, url, headers, timeout=60):
        parts = urlsplit(url)
        if parts.scheme != 'wss':
            raise NativeError('Engine adresi wss:// olmalı.')
        raw = socket.create_connection((parts.hostname, parts.port or 443), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path + ('?' + parts.query if parts.query else '')
        lines = [f'GET {path} HTTP/1.1', f'Host: {parts.hostname}', 'Upgrade: websocket', 'Connection: Upgrade',
                 f'Sec-WebSocket-Key: {key}', 'Sec-WebSocket-Version: 13'] + [f'{k}: {v}' for k, v in headers.items()]
        self.sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise NativeError('Engine WebSocket el sıkışması tamamlanmadı.')
            response += chunk
        head, self.buffer = response.split(b'\r\n\r\n', 1)
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if not head.startswith(b'HTTP/1.1 101') or accept.encode() not in head:
            raise NativeError('Engine WebSocket bağlantısını reddetti (yetki/virtual proxy).')

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise NativeError('Engine bağlantısı kapandı.')
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, text, opcode=1):
        payload = text.encode() if isinstance(text, str) else text
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n) if n < 65536 else bytes([0x80 | 127]) + struct.pack('>Q', n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        message = b''
        while True:
            b1, b2 = self._read(2)
            opcode, length = b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack('>H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._read(8))[0]
            if length > 64 * 1024 * 1024:
                raise NativeError('Engine yanıtı boyut sınırını aştı.')
            data = self._read(length)
            if opcode == 9:
                self.send(data, 10)
                continue
            if opcode == 8:
                raise NativeError('Engine bağlantıyı kapattı.')
            message += data
            if b1 & 0x80:
                return message.decode('utf-8')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def ws_connect(url, headers):
    return WebSocket(url, headers)


class Engine:
    def __init__(self, cfg):
        server = required(cfg, 'server')
        if not server.startswith('https://'):
            raise NativeError('server https:// ile başlamalı.')
        prefix = (cfg.get('virtual_proxy') or '').strip('/')
        if prefix and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', prefix):
            raise NativeError('virtual_proxy geçersiz.')
        self.base = server.rstrip('/') + ('/' + prefix if prefix else '')
        self.headers = {'Authorization': 'Bearer ' + secret('QLIK_JWT')}
        self.app = required(cfg, 'app_id')
        self.ws = ws_connect('wss://' + self.base.split('://', 1)[1] + '/app/' + quote(self.app), self.headers)
        self.seq = 0

    def call(self, handle, method, params):
        self.seq += 1
        self.ws.send(json.dumps({'jsonrpc': '2.0', 'id': self.seq, 'handle': handle, 'method': method, 'params': params}))
        for _ in range(1000):
            msg = json.loads(self.ws.recv())
            if msg.get('id') != self.seq:
                continue  # OnConnected and change notifications
            if msg.get('error'):
                raise NativeError(f'Engine {method} hata döndürdü (kod {msg["error"].get("code")}).')
            return msg.get('result') or {}
        raise NativeError('Engine yanıtı alınamadı: ' + method)

    def doc(self):
        return self.call(-1, 'OpenDoc', [self.app, '', '', '', True])['qReturn']['qHandle']

    def close(self):
        self.ws.close()


def qlik_sense_export(cfg, out, factory):
    objects = names(cfg, 'object_ids', limit=20, pattern=re.compile(r'[A-Za-z0-9_-]{1,64}'))
    engine = Engine(cfg)
    files = []
    try:
        doc = engine.doc()
        for oid in objects:
            obj = engine.call(doc, 'GetObject', {'qId': oid})['qReturn']['qHandle']
            url = engine.call(obj, 'ExportData', {'qFileType': 'OOXML', 'qPath': '/qHyperCubeDef', 'qExportState': 'A'}).get('qUrl')
            if not url or not url.startswith('/'):
                raise NativeError('Engine dışa aktarma adresi döndürmedi: ' + oid)
            client = factory(engine.base, engine.headers)
            raw, _ = client.request('GET', url.lstrip('/'), accept='application/octet-stream')
            files.append(save(out, f'{oid}.xlsx', raw))
            journal(out, exported=[f['file'] for f in files])
    finally:
        engine.close()
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'app_id': cfg['app_id'], 'nesneler': objects, 'dosyalar': files})
    return files[-1]


# ---------- QlikView (QMS API, SOAP with Windows authentication) ----------
QMS_NS = 'http://ws.qliktech.com/QMS/12/2/'


def qms(cfg):
    requests = driver('requests')
    try:
        from requests_ntlm import HttpNtlmAuth
    except ImportError:
        raise NativeError('requests ve requests-ntlm gerekli: pip install -r requirements.txt') from None
    url = required(cfg, 'qms_url')
    if not url.startswith('https://') and not cfg.get('allow_http'):
        raise NativeError('QMS adresi https olmalı (iç ağda http için allow_http: true açıkça verin).')
    session = requests.Session()
    session.auth = HttpNtlmAuth(secret('QV_USER'), secret('QV_PASSWORD'))

    def call(method, body='', key=None):
        envelope = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<{method} xmlns="{QMS_NS}">{body}</{method}></s:Body></s:Envelope>')
        headers = {'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': f'"{QMS_NS}IQMS2/{method}"'}
        if key:
            headers['X-Service-Key'] = key
        r = session.post(url, data=envelope.encode('utf-8'), headers=headers, timeout=60, allow_redirects=False)
        if r.status_code != 200:
            raise NativeError(f'QMS {method} HTTP {r.status_code} (grup üyeliği/yetki).')
        return ET.fromstring(r.content)
    return call


def text_of(root, tag):
    return next((el.text for el in root.iter() if el.tag.rsplit('}', 1)[-1] == tag), None)


def qlikview_task(cfg, out, factory):
    task = required(cfg, 'task_id')
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', task):
        raise NativeError('task_id QMC’deki görev GUID’i olmalı.')
    call = qms(cfg)
    key = text_of(call('GetTimeLimitedServiceKey'), 'GetTimeLimitedServiceKeyResult')
    if not key:
        raise NativeError('QMS servis anahtarı alınamadı.')
    journal(out, task_id=task, state='run_requested')
    call('RunTask', f'<taskID>{task}</taskID>', key)
    for _ in range(360):
        key = text_of(call('GetTimeLimitedServiceKey'), 'GetTimeLimitedServiceKeyResult')
        status = call('GetTaskStatus', f'<taskID>{task}</taskID><scope>All</scope>', key)
        state = text_of(status, 'Status')
        if state in ('Completed', 'Warning'):
            break
        if state in ('Failed', 'Aborting', 'Disabled'):
            raise NativeError('QlikView görevi başarısız: ' + state)
        time.sleep(5)
    else:
        raise NativeError('QlikView görevi 30 dakikada bitmedi; QMC’den izleyin.')
    info = {'task_id': task, 'durum': state, 'son_bitis': text_of(status, 'FinishTime') or text_of(status, 'LastLogMessages') or '',
            'not': 'Görev QMC’de tanımlı yeniden yükleme/dağıtım adımlarını çalıştırdı; dağıtılan belgeler görevin hedeflerindedir.'}
    journal(out, state='verified')
    return receipt(out, 'qlikview-gorev.json', info)


# ---------- Power BI Service ----------
def powerbi_export(cfg, out, factory):
    client = bearer(factory, 'https://api.powerbi.com', 'POWERBI_ACCESS_TOKEN')
    kind = cfg.get('format', 'PDF').upper()
    if kind not in ('PDF', 'PPTX', 'PNG'):
        raise NativeError('format PDF, PPTX veya PNG olmalı.')
    base = '/v1.0/myorg/groups/' + ident(required(cfg, 'workspace_id')) + '/reports/' + ident(required(cfg, 'report_id'))
    pages = cfg.get('pages') or []
    body = {'format': kind, **({'powerBIReportConfiguration': {'pages': [{'pageName': p} for p in pages]}} if pages else {})}
    export = required(client.json('POST', base + '/ExportTo', body), 'id')
    journal(out, export_id=export, state='exporting')
    done = poll_json(client, base + '/exports/' + ident(export), lambda r: r.get('status'), {'Succeeded'}, {'Failed'}, delay=5)
    raw, _ = client.request('GET', base + '/exports/' + ident(export) + '/file', accept='application/octet-stream')
    # A PNG export of more than one page arrives as a ZIP of images.
    artifact = save(out, 'rapor.' + ('zip' if kind == 'PNG' and len(pages) != 1 else kind.lower()), raw)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'export_id': export, 'format': kind, 'sayfalar': pages, 'dosya': artifact, 'percentComplete': done.get('percentComplete')})
    return artifact


# ---------- Tableau Cloud / Server ----------
def tableau_view(client, site, cfg):
    if cfg.get('view_id'):
        return required(cfg, 'view_id')
    workbook, view = required(cfg, 'workbook_name'), required(cfg, 'view_name')
    found = native.tableau_pages(client, site + '/views?filter=' + ident('name:eq:' + view) + '&fields=_default_,workbook.name', 'views', 'view')
    match = [v for v in found if (v.get('workbook') or {}).get('name') == workbook]
    if len(match) != 1:
        raise NativeError('Görünüm bulunamadı veya birden fazla: ' + workbook + ' / ' + view)
    return required(match[0], 'id')


def tableau_export(cfg, out, factory):
    kind = cfg.get('format', 'pdf')
    if kind not in ('pdf', 'png', 'xlsx', 'csv'):
        raise NativeError('format pdf, png, xlsx veya csv olmalı.')
    client, prefix, site = native.tableau_session(cfg, factory)
    try:
        view = tableau_view(client, site, cfg)
        path = {'pdf': '/pdf?' + urlencode({'type': cfg.get('page_size', 'A4'), 'orientation': cfg.get('orientation', 'Landscape')}),
                'png': '/image?resolution=high', 'xlsx': '/crosstab/excel', 'csv': '/data'}[kind]
        raw, _ = client.request('GET', site + '/views/' + ident(view) + path, accept='application/octet-stream')
        artifact = save(out, 'rapor.' + kind, raw)
    finally:
        native.tableau_signout(client, prefix, out)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'view_id': view, 'format': kind, 'dosya': artifact})
    return artifact


# ---------- Looker ----------
def looker_client(cfg, factory):
    server = required(cfg, 'server')
    login = factory(server, {})
    form = urlencode({'client_id': secret('LOOKER_CLIENT_ID'), 'client_secret': secret('LOOKER_CLIENT_SECRET')}).encode()
    raw, _ = login.request('POST', '/api/4.0/login', raw=form, content_type='application/x-www-form-urlencoded')
    return factory(server, {'Authorization': 'token ' + required(native.json_object(raw), 'access_token')})


def looker_export(cfg, out, factory):
    client = looker_client(cfg, factory)
    dashboard = required(cfg, 'dashboard_id')
    kind = cfg.get('format', 'pdf')
    if kind not in ('pdf', 'png', 'jpg'):
        raise NativeError('format pdf, png veya jpg olmalı.')
    q = {'width': opt_int(cfg, 'width', 1680, 300, 4000), 'height': opt_int(cfg, 'height', 1200, 300, 8000)}
    if kind == 'pdf':
        q.update(pdf_paper_size=cfg.get('paper_size', 'a4'), pdf_landscape='true')
    task = required(client.json('POST', f'/api/4.0/render_tasks/dashboards/{ident(dashboard)}/{kind}?' + urlencode(q),
                                {'dashboard_style': cfg.get('dashboard_style', 'tiled'), 'dashboard_filters': cfg.get('dashboard_filters', '')}), 'id')
    journal(out, render_task_id=task, state='rendering')
    poll_json(client, '/api/4.0/render_tasks/' + ident(task), lambda r: r.get('status'), {'success'}, {'failure'})
    raw, _ = client.request('GET', '/api/4.0/render_tasks/' + ident(task) + '/results', accept='application/octet-stream')
    artifact = save(out, 'rapor.' + kind, raw)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'dashboard_id': dashboard, 'render_task_id': task, 'dosya': artifact})
    return artifact


# ---------- Looker Studio (Linking API) ----------
def bigquery_table(cfg, factory):
    client = bearer(factory, 'https://bigquery.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    project, dataset, table = (name(cfg, k, re.compile(r'[A-Za-z0-9_-]{1,1024}')) for k in ('project_id', 'dataset_id', 'table_id'))
    t = client.json('GET', f'/bigquery/v2/projects/{ident(project)}/datasets/{ident(dataset)}/tables/{ident(table)}')
    return project, dataset, table, t


def looker_studio_link(cfg, out, factory):
    project, dataset, table, t = bigquery_table(cfg, factory)
    params = {'r.reportName': required(cfg, 'report_name'), 'ds.ds0.connector': 'bigQuery', 'ds.ds0.type': 'TABLE', 'ds.ds0.projectId': project,
              'ds.ds0.datasetId': dataset, 'ds.ds0.tableId': table, 'ds.ds0.datasourceName': cfg.get('datasource_name') or table}
    if cfg.get('template_report_id'):
        params['c.reportId'] = name(cfg, 'template_report_id', re.compile(r'[A-Za-z0-9_-]{8,64}'))
        params['c.mode'] = 'edit'
    url = 'https://lookerstudio.google.com/reporting/create?' + urlencode(params, quote_via=quote)
    fields = [{'alan': f.get('name'), 'tip': f.get('type')} for f in (t.get('schema') or {}).get('fields', [])]
    write_artifact(out, 'rapor_olustur.url', ('[InternetShortcut]\nURL=' + url + '\n').encode())
    write_csv(out, 'veri_kaynagi_alanlari.csv', fields, ['alan', 'tip'])
    journal(out, state='verified')
    return receipt(out, 'rapor.json', {'baglanti': url, 'tablo': f'{project}.{dataset}.{table}', 'satir': t.get('numRows'),
                                       'not': 'Looker Studio rapor oluşturma için yalnızca tarayıcıda açılan Linking API sunar; bağlantıyı açan kullanıcının tabloya erişimi olmalı. '
                                              'Rapor bağlantı açılınca kullanıcının hesabında oluşturulur.'})


# ---------- SAP BusinessObjects (RESTful Web Services / Raylight) ----------
BO_TYPES = {'pdf': 'application/pdf', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'csv': 'text/csv'}


def bo_session(cfg, factory):
    base = required(cfg, 'server').rstrip('/') + '/biprws'
    login = factory(base, {})
    _, headers = login.request('POST', 'logon/long', {'userName': secret('BO_USER'), 'password': secret('BO_PASSWORD'), 'auth': cfg.get('auth', 'secEnterprise')})
    token = native.header(headers, 'X-SAP-LogonToken')
    if not token:
        raise NativeError('BusinessObjects oturum belirteci dönmedi.')
    return factory(base, {'X-SAP-LogonToken': '"' + token.strip('"') + '"'})


def bo_logoff(client, out):
    try:
        client.request('POST', 'logoff')
    except NativeError:
        journal(out, logoff='failed; token may remain valid until expiry')


def bo_export(cfg, out, factory):
    doc = required(cfg, 'document_id')
    if not re.fullmatch(r'\d{1,12}', str(doc)):
        raise NativeError('document_id sayısal Web Intelligence belge kimliği olmalı.')
    kind = cfg.get('format', 'pdf')
    if kind not in BO_TYPES:
        raise NativeError('format pdf, xlsx veya csv olmalı.')
    client = bo_session(cfg, factory)
    try:
        if cfg.get('refresh'):
            client.request('PUT', f'raylight/v1/documents/{doc}/parameters', {})  # refreshes data providers with saved prompts
        raw, _ = client.request('GET', f'raylight/v1/documents/{doc}', accept=BO_TYPES[kind])
        artifact = save(out, 'rapor.' + kind, raw)
    finally:
        bo_logoff(client, out)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'document_id': doc, 'format': kind, 'yenilendi': bool(cfg.get('refresh')), 'dosya': artifact})
    return artifact


# ---------- SAP Analytics Cloud (Data Export API) ----------
def sac_client(cfg, factory):
    return bearer(factory, required(cfg, 'tenant_url'), 'SAC_ACCESS_TOKEN')


def sac_export(cfg, out, factory):
    from products.core import odata_rows
    client = sac_client(cfg, factory)
    provider = name(cfg, 'provider_id', re.compile(r'[A-Za-z0-9_.:-]{1,128}'))
    dims = names(cfg, 'dimensions', limit=10)
    measures = names(cfg, 'measures', limit=10)
    rows = odata_rows(client, f'/api/v1/dataexport/providers/sac/{ident(provider)}/FactData', select=dims + measures, filter_=cfg.get('filter') or None, v2=False)
    if not rows:
        raise NativeError('Model filtreyle eşleşen olgu verisi döndürmedi.')
    table = summarize(rows, dims, measures)
    write_csv(out, 'rapor.csv', table, dims + ['kayit'] + measures)
    journal(out, state='verified')
    return receipt(out, 'rapor.json', {'model': provider, 'satir': len(rows), 'ozet_satir': len(table), 'kirilimlar': dims, 'olculer': measures,
                                       'not': 'SAC hikâye (story) dışa aktarımı için REST API yoktur; rapor modelin Data Export API ile okunan olgu verisinden üretildi.'})


# ---------- Oracle Analytics Cloud ----------
def oac_export(cfg, out, factory):
    client = bearer(factory, required(cfg, 'server'), 'OAC_ACCESS_TOKEN')
    workbook = required(cfg, 'workbook_path')
    canvases = names(cfg, 'canvas_ids', limit=5, pattern=re.compile(r'[^\x00-\x1f]{1,2000}'))
    kind = cfg.get('format', 'pdf')
    if kind not in ('pdf', 'png'):
        raise NativeError('format pdf veya png olmalı.')
    b64 = base64.urlsafe_b64encode(workbook.encode()).decode().rstrip('=')
    body = {'name': cfg.get('file_name', 'datawork'), 'type': 'file', 'format': kind, 'canvasIds': canvases,
            'screenwidth': opt_int(cfg, 'screen_width', 1440, 300, 3600), 'screenheight': opt_int(cfg, 'screen_height', 900, 300, 3600)}
    _, headers, status = client.request('POST', f'/api/20210901/catalog/workbooks/{b64}/exports', body, with_status=True)
    work = native.header(headers, 'oa-work-request-id')
    if status != 202 or not work:
        raise NativeError('OAC dışa aktarma isteği kabul edilmedi.')
    journal(out, work_request_id=work, state='exporting')
    poll_json(client, '/api/20210901/workRequests/' + ident(work), lambda r: r.get('status'), {'SUCCEEDED'}, {'FAILED', 'CANCELED', 'CANCELING', 'PARTIAL_SUCCESS'})
    raw, _ = client.request('GET', f'/api/20210901/catalog/workbooks/{b64}/exports/{ident(work)}', accept='application/octet-stream')
    artifact = save(out, 'rapor.' + kind, raw)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'workbook': workbook, 'canvases': canvases, 'work_request_id': work, 'dosya': artifact})
    return artifact


# ---------- MicroStrategy ONE ----------
def mstr_session(cfg, factory):
    base = required(cfg, 'server').rstrip('/') + '/MicroStrategyLibrary/api' if not cfg['server'].rstrip('/').endswith('/api') else cfg['server'].rstrip('/')
    login = factory(base, {})
    _, headers = login.request('POST', 'auth/login', {'username': secret('MSTR_USER'), 'password': secret('MSTR_PASSWORD'), 'loginMode': cfg.get('login_mode', 1)})
    token = native.header(headers, 'X-MSTR-AuthToken')
    cookie = '; '.join(c.split(';', 1)[0] for c in (native.header(headers, 'Set-Cookie') or '').split(', ') if '=' in c.split(';', 1)[0])
    if not token:
        raise NativeError('MicroStrategy oturum belirteci dönmedi.')
    return factory(base, {'X-MSTR-AuthToken': token, 'X-MSTR-ProjectID': required(cfg, 'project_id'), **({'Cookie': cookie} if cookie else {})})


def mstr_logout(client, out):
    try:
        client.request('POST', 'auth/logout')
    except NativeError:
        journal(out, logout='failed')


def mstr_export(cfg, out, factory):
    dashboard = name(cfg, 'dashboard_id', re.compile(r'[0-9A-F]{32}'))
    client = mstr_session(cfg, factory)
    try:
        instance = required(client.json('POST', f'dossiers/{dashboard}/instances', {}), 'mid')
        journal(out, instance_id=instance)
        body = {'includeOverview': True, 'includeDetailedPages': True, 'includeHeader': True, 'includeFooter': True, 'includeToc': False,
                'orientation': cfg.get('orientation', 'LANDSCAPE'), 'pageOption': 'ALL', 'fitToPage': True}
        data = client.json('POST', f'documents/{dashboard}/instances/{ident(instance)}/pdf', body).get('data')
        try:
            raw = base64.b64decode(data or '', validate=True)
        except ValueError:
            raise NativeError('MicroStrategy PDF verisi okunamadı.') from None
        artifact = save(out, 'rapor.pdf', raw)
    finally:
        mstr_logout(client, out)
    journal(out, state='verified')
    receipt(out, 'rapor.json', {'dashboard_id': dashboard, 'instance_id': instance, 'dosya': artifact})
    return artifact


# ---------- Superset ----------
def superset_client(cfg, factory):
    server = required(cfg, 'server')
    login = factory(server, {})
    token = required(login.json('POST', '/api/v1/security/login', {'username': secret('SUPERSET_USER'), 'password': secret('SUPERSET_PASSWORD'),
                                                                   'provider': cfg.get('auth_provider', 'db'), 'refresh': False}), 'access_token')
    return factory(server, {'Authorization': 'Bearer ' + token})


def superset_export(cfg, out, factory):
    client = superset_client(cfg, factory)
    dashboard = opt_int(cfg, 'dashboard_id', 0, 1, 10 ** 9)
    charts = [c for c in client.json('GET', f'/api/v1/dashboard/{dashboard}/charts').get('result', []) if isinstance(c, dict)]
    if not charts:
        raise NativeError('Panoda grafik bulunamadı.')
    files = []
    for c in charts[:50]:
        cid = c.get('id')
        raw, _ = client.request('GET', f'/api/v1/chart/{int(cid)}/data/?format=csv&type=full', accept='text/csv')
        files.append({'grafik': c.get('slice_name'), 'id': cid, **save(out, f'grafik_{int(cid)}.csv', raw)})
        journal(out, exported=len(files))
    journal(out, state='verified')
    return receipt(out, 'rapor.json', {'dashboard_id': dashboard, 'grafikler': files,
                                       'not': 'Her grafiğin verisi Superset’in kendi sorgu motoruyla (kayıtlı filtre ve metriklerle) CSV olarak üretildi.'})


# ---------- Metabase ----------
def metabase_export(cfg, out, factory):
    client = factory(required(cfg, 'server'), {'X-API-Key': secret('METABASE_API_KEY')})
    dashboard = opt_int(cfg, 'dashboard_id', 0, 1, 10 ** 9)
    kind = cfg.get('format', 'xlsx')
    if kind not in ('xlsx', 'csv'):
        raise NativeError('format xlsx veya csv olmalı.')
    dash = client.json('GET', f'/api/dashboard/{dashboard}')
    cards = [d.get('card') or {} for d in dash.get('dashcards') or dash.get('ordered_cards') or [] if (d.get('card') or {}).get('id')]
    if not cards:
        raise NativeError('Panoda soru (kart) bulunamadı.')
    files = []
    for card in cards[:50]:
        raw, _ = client.request('POST', f'/api/card/{int(card["id"])}/query/{kind}', raw=b'', content_type='application/json', accept='application/octet-stream')
        files.append({'kart': card.get('name'), 'id': card['id'], **save(out, f'kart_{int(card["id"])}.{kind}', raw)})
        journal(out, exported=len(files))
    journal(out, state='verified')
    return receipt(out, 'rapor.json', {'dashboard_id': dashboard, 'pano': dash.get('name'), 'kartlar': files})


# ---------- KPI & dashboard design agent: field evidence per product ----------
def field(table, column, kind, role=''):
    return {'tablo': str(table or ''), 'alan': str(column), 'tip': str(kind or ''), 'rol': role}


def ev_qlik_cloud(cfg, factory, out):
    client = native.qlik_client(cfg, factory)
    meta = client.json('GET', '/api/v1/apps/' + ident(required(cfg, 'app_id')) + '/data/metadata')
    fields = [field(','.join(f.get('src_tables') or []), f.get('name'), ','.join(f.get('tags') or []), 'alan') for f in meta.get('fields', []) if not f.get('is_system')]
    return fields, {'tablolar': [{'tablo': t.get('name'), 'satir': t.get('no_of_rows')} for t in meta.get('tables', [])]}


def ev_qlik_sense(cfg, factory, out):
    engine = Engine(cfg)
    try:
        doc = engine.doc()
        r = engine.call(doc, 'GetTablesAndKeys', {'qWindowSize': {'qcx': 0, 'qcy': 0}, 'qNullSize': {'qcx': 0, 'qcy': 0}, 'qCellHeight': 0,
                                                  'qSyntheticMode': False, 'qIncludeSysVars': False})
    finally:
        engine.close()
    fields, tables = [], []
    for t in r.get('qtr', []):
        tables.append({'tablo': t.get('qName'), 'satir': t.get('qNoOfRows')})
        fields += [field(t.get('qName'), f.get('qName'), ','.join(f.get('qTags') or []), 'anahtar' if f.get('qKeyType') not in (None, 'NOT_KEY') else 'alan')
                   for f in t.get('qFields', [])]
    return fields, {'tablolar': tables}


LOAD = re.compile(r'(?:^|\n)\s*(?:\[?([\w ]+?)\]?\s*:\s*)?(?:NOCONCATENATE\s+|CONCATENATE\s*(?:\(\w+\))?\s*)?LOAD\s+(?:DISTINCT\s+)?(.*?)\s+(?:FROM|RESIDENT|INLINE|AUTOGENERATE)\b', re.I | re.S)


def split_fields(text):
    parts, depth, cur = [], 0, ''
    for ch in text:
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur); cur = ''
        else:
            cur += ch
    return parts + [cur]


def ev_qlikview(cfg, factory, out):
    folder = Path(required(cfg, 'prj_folder'))
    script = folder / 'LoadScript.txt'
    if not script.exists():
        raise NativeError('-prj klasöründe LoadScript.txt yok (QlikView’da belge ile aynı adlı -prj klasörü oluşturup kaydedin).')
    text = script.read_bytes().decode('utf-8-sig', errors='replace')
    fields = []
    for i, (table, body) in enumerate(LOAD.findall(text), 1):
        for part in split_fields(body):
            alias = re.search(r'\bas\s+\[?([^\]\n,]+?)\]?\s*$', part.strip(), re.I)
            name_ = alias.group(1) if alias else part.strip().strip('[]"')
            if name_ and name_ != '*' and re.fullmatch(r'[^\x00-\x1f()]{1,128}', name_):
                fields.append(field(table or f'tablo_{i}', name_.strip(), '', 'alan'))
    return fields, {'kaynak': 'LoadScript.txt', 'not': 'Alanlar load script’ten ayrıştırıldı; ifadelerle türetilen alanlar takma adlarıyla listelenir.'}


def ev_powerbi(cfg, factory, out):
    client = bearer(factory, 'https://api.powerbi.com', 'POWERBI_ACCESS_TOKEN')
    path = '/v1.0/myorg/groups/' + ident(required(cfg, 'workspace_id')) + '/datasets/' + ident(required(cfg, 'dataset_id')) + '/executeQueries'
    def run(query):
        r = client.json('POST', path, {'queries': [{'query': query}], 'serializerSettings': {'includeNulls': True}})
        tables = ((r.get('results') or [{}])[0].get('tables') or [{}])
        if (r.get('results') or [{}])[0].get('error'):
            raise NativeError('executeQueries hata döndürdü (INFO.VIEW işlevleri için Build izni gerekir).')
        return [{k.strip('[]'): v for k, v in row.items()} for row in tables[0].get('rows', [])]
    cols = run('EVALUATE SELECTCOLUMNS(FILTER(INFO.VIEW.COLUMNS(), NOT [IsHidden]), "Table", [Table], "Name", [Name], "DataType", [DataType])')
    measures = run('EVALUATE SELECTCOLUMNS(INFO.VIEW.MEASURES(), "Table", [Table], "Name", [Name], "Expression", [Expression])')
    fields = [field(c.get('Table'), c.get('Name'), c.get('DataType'), 'kolon') for c in cols] + [field(m.get('Table'), m.get('Name'), 'ölçü', 'olcu') for m in measures]
    return fields, {'olcu_ifadeleri': [{'olcu': m.get('Name'), 'ifade': str(m.get('Expression'))[:400]} for m in measures[:100]]}


TMDL_TABLE = re.compile(r"^table\s+('?)(.+?)\1\s*$")
TMDL_ITEM = re.compile(r"^\t(column|measure)\s+('?)(.+?)\2(\s*=.*)?$")


def ev_powerbi_desktop(cfg, factory, out):
    root = Path(required(cfg, 'semantic_model_dir'))
    tables_dir = root / 'definition' / 'tables'
    if not tables_dir.is_dir():
        raise NativeError('semantic_model_dir PBIP projesindeki <Ad>.SemanticModel klasörü olmalı (TMDL biçimi).')
    fields = []
    for f in sorted(tables_dir.glob('*.tmdl')):
        table, current = None, None
        for line in f.read_text(encoding='utf-8-sig').splitlines():
            if m := TMDL_TABLE.match(line):
                table = m.group(2)
            elif m := TMDL_ITEM.match(line):
                current = field(table, m.group(3).strip(), 'ölçü' if m.group(1) == 'measure' else '', 'olcu' if m.group(1) == 'measure' else 'kolon')
                fields.append(current)
            elif current and line.strip().startswith('dataType:'):
                current['tip'] = line.split(':', 1)[1].strip()
    return fields, {'proje': root.name}


TABLEAU_MD = ('query($luid:String!){workbooks(filter:{luid:$luid}){name embeddedDatasources{name fields{name __typename ... on ColumnField{dataType role} '
              '... on CalculatedField{dataType role formula}}} upstreamDatasources{name fields{name __typename ... on ColumnField{dataType role} ... on CalculatedField{dataType role}}}}}')


def ev_tableau(cfg, factory, out):
    client, prefix, site = native.tableau_session(cfg, factory)
    try:
        data = native.graphql(client, '/api/metadata/graphql', TABLEAU_MD, {'luid': required(cfg, 'workbook_id')})
    finally:
        native.tableau_signout(client, prefix, out)
    fields = []
    for wb in data.get('workbooks', []):
        for ds in (wb.get('embeddedDatasources') or []) + (wb.get('upstreamDatasources') or []):
            fields += [field(ds.get('name'), f.get('name'), f.get('dataType'), 'olcu' if f.get('role') == 'MEASURE' else 'boyut') for f in ds.get('fields', [])]
    if not fields:
        raise NativeError('Metadata API çalışma kitabı alanı döndürmedi (Metadata API açık mı, workbook_id LUID mi?).')
    return fields, {}


def ev_tableau_desktop(cfg, factory, out):
    path = Path(required(cfg, 'workbook_file'))
    raw = path.read_bytes()
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(next(n for n in z.namelist() if n.lower().endswith('.twb')))
    root = ET.fromstring(raw)
    fields = []
    for ds in root.iter('datasource'):
        if ds.get('name') == 'Parameters':
            continue
        for col in ds.findall('column'):
            label = col.get('caption') or (col.get('name') or '').strip('[]')
            if label:
                fields.append(field(ds.get('caption') or ds.get('name'), label, col.get('datatype'), 'olcu' if col.get('role') == 'measure' else 'boyut'))
    return fields, {'dosya': path.name}


def ev_looker(cfg, factory, out):
    client = looker_client(cfg, factory)
    explore = client.json('GET', '/api/4.0/lookml_models/' + ident(required(cfg, 'model')) + '/explores/' + ident(required(cfg, 'explore')))
    fields = [field(f.get('view'), f.get('name'), f.get('type'), 'olcu' if kind == 'measures' else 'boyut')
              for kind in ('dimensions', 'measures') for f in (explore.get('fields') or {}).get(kind, [])]
    return fields, {'explore': explore.get('name')}


def ev_looker_studio(cfg, factory, out):
    project, dataset, table, t = bigquery_table(cfg, factory)
    return [field(table, f.get('name'), f.get('type'), 'olcu' if f.get('type') in ('INTEGER', 'INT64', 'FLOAT', 'FLOAT64', 'NUMERIC', 'BIGNUMERIC') else 'boyut')
            for f in (t.get('schema') or {}).get('fields', [])], {'tablo': f'{project}.{dataset}.{table}', 'satir': t.get('numRows')}


def ev_bo(cfg, factory, out):
    doc = required(cfg, 'document_id')
    client = bo_session(cfg, factory)
    fields = []
    try:
        dps = client.json('GET', f'raylight/v1/documents/{int(doc)}/dataproviders').get('dataproviders', {}).get('dataprovider', [])
        for dp in dps if isinstance(dps, list) else [dps]:
            d = client.json('GET', f'raylight/v1/documents/{int(doc)}/dataproviders/{ident(dp.get("id"))}').get('dataprovider', {})
            exprs = (d.get('dictionary') or {}).get('expression', [])
            fields += [field(dp.get('name'), e.get('name'), e.get('@dataType') or e.get('dataType'), 'olcu' if (e.get('@qualification') or e.get('qualification')) == 'Measure' else 'boyut')
                       for e in (exprs if isinstance(exprs, list) else [exprs])]
    finally:
        bo_logoff(client, out)
    return fields, {}


def ev_sac(cfg, factory, out):
    client = sac_client(cfg, factory)
    provider = name(cfg, 'provider_id', re.compile(r'[A-Za-z0-9_.:-]{1,128}'))
    raw, _ = client.request('GET', f'/api/v1/dataexport/providers/sac/{ident(provider)}/$metadata', accept='application/xml')
    meta = odata_metadata(raw, ['FactData'])
    return [field(provider, m['alan'], m['tip'], 'olcu' if m['tip'] in ('Decimal', 'Double', 'Int32', 'Int64') else 'boyut') for m in meta], {}


def ev_oac(cfg, factory, out):
    client = bearer(factory, required(cfg, 'server'), 'OAC_ACCESS_TOKEN')
    items = []
    for kind in ('workbooks', 'datasets', 'subjectAreas'):
        raw, _ = client.request('GET', '/api/20210901/catalog?' + urlencode({'type': kind}))
        try:
            listing = json.loads(raw)
        except ValueError:
            listing = []
        items += [{'tur': kind, 'ad': i.get('name'), 'id': i.get('id')} for i in (listing if isinstance(listing, list) else listing.get('items', [])) if isinstance(i, dict)]
    fields = []
    columns = cfg.get('columns_file')
    if columns and Path(columns).exists():
        with open(columns, encoding='utf-8-sig', newline='') as fh:
            fields = [field(r.get('tablo'), r.get('alan'), r.get('tip'), r.get('rol', '')) for r in csv.DictReader(fh) if r.get('alan')]
    if not fields:
        raise NativeError('OAC REST API veri kümesi kolonlarını belgelenmiş bir uç noktada vermez; veri kümesi incelemesinden kolon listesini columns_file (tablo,alan,tip,rol) olarak verin.')
    return fields, {'katalog': items[:200]}


def ev_mstr(cfg, factory, out):
    client = mstr_session(cfg, factory)
    try:
        cube = client.json('GET', 'v2/cubes/' + name(cfg, 'cube_id', re.compile(r'[0-9A-F]{32}')))
    finally:
        mstr_logout(client, out)
    objs = (cube.get('definition') or {}).get('availableObjects') or {}
    fields = [field(cube.get('name'), a.get('name'), 'attribute', 'boyut') for a in objs.get('attributes', [])]
    fields += [field(cube.get('name'), m.get('name'), 'metric', 'olcu') for m in objs.get('metrics', [])]
    return fields, {}


def ev_superset(cfg, factory, out):
    client = superset_client(cfg, factory)
    ds = client.json('GET', f'/api/v1/dataset/{opt_int(cfg, "dataset_id", 0, 1, 10 ** 9)}').get('result') or {}
    fields = [field(ds.get('table_name'), c.get('column_name'), c.get('type'), 'zaman' if c.get('is_dttm') else 'kolon') for c in ds.get('columns', [])]
    fields += [field(ds.get('table_name'), m.get('metric_name'), 'metric', 'olcu') for m in ds.get('metrics', [])]
    return fields, {'veri_kumesi': ds.get('table_name')}


def ev_metabase(cfg, factory, out):
    client = factory(required(cfg, 'server'), {'X-API-Key': secret('METABASE_API_KEY')})
    t = client.json('GET', f'/api/table/{opt_int(cfg, "table_id", 0, 1, 10 ** 9)}/query_metadata')
    return [field(t.get('name'), f.get('name'), f.get('base_type'), f.get('semantic_type') or '') for f in t.get('fields', [])], {'tablo': t.get('display_name')}


AGG = {'sum', 'avg', 'count', 'count_distinct', 'min', 'max'}
VISUALS = {'kpi', 'cubuk', 'cizgi', 'alan', 'tablo', 'pasta', 'harita', 'dagilim', 'filtre', 'matris'}


def expression(kind, f, agg):
    t, c = f['tablo'], f['alan']
    if f['rol'] in ('olcu',) and f['tip'] in ('ölçü', 'metric'):
        return {'dax': f'[{c}]', 'qlik': f'[{c}]', 'tableau': f'[{c}]', 'looker': c, 'sql': c}.get(kind, c)
    dax = {'sum': 'SUM', 'avg': 'AVERAGE', 'count': 'COUNT', 'count_distinct': 'DISTINCTCOUNT', 'min': 'MIN', 'max': 'MAX'}[agg]
    qlik = {'sum': 'Sum', 'avg': 'Avg', 'count': 'Count', 'count_distinct': 'Count(DISTINCT', 'min': 'Min', 'max': 'Max'}[agg]
    tableau = {'sum': 'SUM', 'avg': 'AVG', 'count': 'COUNT', 'count_distinct': 'COUNTD', 'min': 'MIN', 'max': 'MAX'}[agg]
    sql = {'sum': 'SUM', 'avg': 'AVG', 'count': 'COUNT', 'count_distinct': 'COUNT(DISTINCT', 'min': 'MIN', 'max': 'MAX'}[agg]
    return {'dax': f"{dax}('{t}'[{c}])", 'qlik': f'{qlik}([{c}])' + (')' if agg == 'count_distinct' else ''),
            'tableau': f'{tableau}([{c}])', 'looker': f'type: {"count_distinct" if agg == "count_distinct" else {"avg": "average"}.get(agg, agg)}  sql: ${{TABLE}}.{c} ;;',
            'sql': f'{sql}({c})' + (')' if agg == 'count_distinct' else '')}.get(kind, f'{agg}({c})')


EVIDENCE = {
    'qlik-cloud': ('Qlik Cloud', ev_qlik_cloud, 'qlik'), 'qlik-sense': ('Qlik Sense Enterprise', ev_qlik_sense, 'qlik'), 'qlikview': ('QlikView', ev_qlikview, 'qlik'),
    'powerbi-service': ('Power BI Service', ev_powerbi, 'dax'), 'powerbi-desktop': ('Power BI Desktop', ev_powerbi_desktop, 'dax'),
    'tableau-cloud': ('Tableau Cloud', ev_tableau, 'tableau'), 'tableau-server': ('Tableau Server', ev_tableau, 'tableau'),
    'tableau-desktop': ('Tableau Desktop', ev_tableau_desktop, 'tableau'), 'looker': ('Looker', ev_looker, 'looker'),
    'looker-studio': ('Looker Studio', ev_looker_studio, 'sql'), 'sap-bo': ('SAP BusinessObjects', ev_bo, 'text'),
    'sac': ('SAP Analytics Cloud', ev_sac, 'text'), 'oac': ('Oracle Analytics Cloud', ev_oac, 'text'), 'microstrategy': ('MicroStrategy ONE', ev_mstr, 'text'),
    'superset': ('Superset', ev_superset, 'sql'), 'metabase': ('Metabase', ev_metabase, 'sql'),
}
SCHEMA = ('{"kpiler":[{"ad":"Net satış","tanim":"iş tanımı","alan":"kanıttaki alan (ölçü veya kolon)","tablo":"alanın tablosu","toplama":"sum|avg|count|count_distinct|min|max",'
          '"bolen":{"alan":"...","tablo":"...","toplama":"..."},"iyi_yon":"artis|azalis","grain":"gün/ay/müşteri"}],'
          '"sayfalar":[{"ad":"Özet","amac":"kim hangi kararı verir","gorseller":[{"tur":"kpi|cubuk|cizgi|alan|tablo|pasta|harita|dagilim|filtre|matris",'
          '"baslik":"...","kpi":"kpiler içindeki ad","boyutlar":["kanıttaki alan"]}]}],"sorular":["..."]}')


def kpi_design(cfg, out, factory):
    key = cfg['operation'].rsplit('-kpi-design', 1)[0]
    label, collect, dialect = EVIDENCE[key]
    fields, extra = collect(cfg, factory, out)
    if not fields:
        raise NativeError('Platform alan listesi döndürmedi.')
    index = defaultdict(list)
    for f in fields:
        index[f['alan'].lower()].append(f)

    def resolve(item, label_):
        name_ = need_text(item, 'alan', label_).lower()
        options = index.get(name_)
        if not options:
            raise ValueError(f'{label_}: kanıtta olmayan alan: {item.get("alan")}')
        table = str(item.get('tablo') or '').lower()
        chosen = [o for o in options if not table or o['tablo'].lower() == table] or options
        return chosen[0]

    def validate(obj):
        kpis = need_list(obj, 'kpiler', 'KPI listesi', maximum=40)
        names_ = set()
        for i, k in enumerate(kpis, 1):
            names_.add(need_text(k, 'ad', f'KPI {i}'))
            resolve(k, f'KPI {i}')
            need_text(k, 'toplama', f'KPI {i}', choices=AGG)
            if k.get('bolen'):
                if not isinstance(k['bolen'], dict):
                    raise ValueError(f'KPI {i}: bolen nesne olmalı.')
                resolve(k['bolen'], f'KPI {i} böleni')
                need_text(k['bolen'], 'toplama', f'KPI {i} böleni', choices=AGG)
        for p in need_list(obj, 'sayfalar', 'sayfalar', maximum=12):
            need_text(p, 'ad', 'sayfa')
            for v in need_list(p, 'gorseller', p['ad'] + ' görselleri', maximum=30):
                need_text(v, 'tur', 'görsel', choices=VISUALS)
                if v.get('kpi') and v['kpi'] not in names_:
                    raise ValueError(f'görsel "{v.get("baslik")}": tanımsız KPI: {v["kpi"]}')
                for d in v.get('boyutlar') or []:
                    if str(d).lower() not in index:
                        raise ValueError(f'görsel "{v.get("baslik")}": kanıtta olmayan boyut: {d}')
        return obj

    def render(result):
        rows = []
        for k in result['kpiler']:
            num = expression(dialect, resolve(k, k['ad']), k['toplama'])
            formula = num
            if k.get('bolen'):
                den = expression(dialect, resolve(k['bolen'], k['ad']), k['bolen']['toplama'])
                formula = {'dax': f'DIVIDE({num}, {den})', 'sql': f'{num} / NULLIF({den}, 0)'}.get(dialect, f'({num}) / ({den})')
            rows.append({**k, 'ifade': formula if dialect != 'text' else f'{k["toplama"]}({k["alan"]})' + (f' / {k["bolen"]["toplama"]}({k["bolen"]["alan"]})' if k.get('bolen') else ''),
                         'bolen': f'{k["bolen"]["toplama"]}({k["bolen"]["alan"]})' if k.get('bolen') else ''})
        visuals = [{'sayfa': p['ad'], **v} for p in result['sayfalar'] for v in p['gorseller']]
        lang = {'dax': 'DAX', 'qlik': 'Qlik ifadesi', 'tableau': 'Tableau hesaplaması', 'looker': 'LookML ölçü gövdesi', 'sql': 'SQL toplaması', 'text': 'tanım'}[dialect]
        return {'kpi_tasarimi.json': plan_json({**result, 'kpiler': rows}),
                'kpi_tasarimi.md': rows_md(f'KPI ve pano tasarımı — {label} (ifadeler: {lang})',
                                           [('KPI’lar', rows, ['ad', 'tanim', 'ifade', 'iyi_yon', 'grain']),
                                            ('Sayfalar ve görseller', visuals, ['sayfa', 'tur', 'baslik', 'kpi', 'boyutlar']),
                                            ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
    evidence = {'alanlar': fields[:2000], **extra}
    return agent_task(cfg, out, product=label, title='KPI ve dashboard tasarımı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Her KPI tek bir kanıt alanına (ve oran ise bir bölene) dayansın; hazır ölçü varsa onu tercih et. KPI değeri uydurma; '
                            'ifade kodu kod tarafından ürün diline çevrilir. Sayfa başına en fazla 8 görsel öner.')


EXPORTS = {'qlik-cloud': qlik_cloud_export, 'qlik-sense': qlik_sense_export, 'qlikview': qlikview_task, 'powerbi-service': powerbi_export,
           'tableau-cloud': tableau_export, 'tableau-server': tableau_export, 'looker': looker_export, 'looker-studio': looker_studio_link,
           'sap-bo': bo_export, 'sac': sac_export, 'oac': oac_export, 'microstrategy': mstr_export, 'superset': superset_export, 'metabase': metabase_export}
HANDLERS = {k + '-report-export': v for k, v in EXPORTS.items()}
HANDLERS.update({k + '-kpi-design': kpi_design for k in EVIDENCE})
