"""Machine learning & MLOps: training data profile from each framework/platform (code) and an evidence-bound model evaluation &
drift strategy (agent). Platforms are only read; no training job, endpoint or monitor is created."""
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import native
from products.core import (NativeError, agent_task, aws_call, bearer, driver, ident, journal, name, names, need_list, need_text, number, opt_float, opt_int,
                           plain, plan_json, profile, read_table, receipt, region_of, required, rows_md, secret, sql_source, write_csv, BigQuerySql)

COLUMNS = ['ozellik', 'tip', 'satir', 'dolu', 'bos', 'bos_orani', 'farkli', 'min', 'max', 'ortalama', 'std', 'hedefle_iliski', 'uyari']


def warnings_for(item, rows_count, target=None):
    flags = []
    if item.get('farkli') in (0, 1):
        flags.append('sabit')
    if number(item.get('bos_orani')) and number(item['bos_orani']) > 0.3:
        flags.append('yüksek eksik')
    if rows_count and number(item.get('farkli')) and number(item['farkli']) / rows_count > 0.95 and str(item.get('tip')).lower() in ('metin', 'string', 'object', 'text'):
        flags.append('kimlik benzeri')
    if number(item.get('hedefle_iliski')) is not None and abs(number(item['hedefle_iliski'])) > 0.95:
        flags.append('olası sızıntı')
    return ', '.join(flags)


def target_summary(rows, target):
    values = [r.get(target) for r in rows if r.get(target) not in (None, '')]
    nums = [number(v) for v in values]
    if values and all(n is not None for n in nums) and len(set(values)) > 20:
        return {'tur': 'regresyon', 'min': min(nums), 'max': max(nums), 'ortalama': round(sum(nums) / len(nums), 6)}
    counts = Counter(str(v) for v in values)
    total = sum(counts.values())
    return {'tur': 'sınıflandırma', 'sinif': {k: round(n / total, 4) for k, n in counts.most_common(20)},
            'dengesizlik': round(counts.most_common(1)[0][1] / max(1, counts.most_common()[-1][1]), 2) if counts else None}


# ---------- scikit-learn ----------
def sklearn_profile(cfg, factory, out):
    fs = driver('sklearn.feature_selection')
    columns, rows = read_table(required(cfg, 'input_file'))
    target = required(cfg, 'target')
    if target not in columns:
        raise NativeError('target kolonu dosyada yok.')
    feats = [c for c in columns if c != target]
    prof = {p['kolon']: p for p in profile(feats, rows)}
    summary = target_summary(rows, target)
    numeric = [c for c in feats if prof[c]['tip'] in ('tamsayı', 'ondalık')]
    usable = [r for r in rows if r.get(target) not in (None, '') and all(number(r.get(c)) is not None for c in numeric)]
    mi = {}
    if numeric and len(usable) >= 20:
        X = [[number(r[c]) for c in numeric] for r in usable]
        y = [r[target] for r in usable]
        scores = fs.mutual_info_classif(X, y, random_state=0) if summary['tur'] == 'sınıflandırma' else fs.mutual_info_regression(X, [number(v) for v in y], random_state=0)
        mi = {c: round(float(s), 4) for c, s in zip(numeric, scores)}
    out_rows = []
    for c in feats:
        p = prof[c]
        item = {'ozellik': c, **{k: p.get(k, '') for k in ('tip', 'satir', 'dolu', 'bos', 'bos_orani', 'farkli', 'min', 'max', 'ortalama', 'std')}, 'hedefle_iliski': mi.get(c, '')}
        item['uyari'] = warnings_for(item, len(rows))
        out_rows.append(item)
    return out_rows, {'hedef': target, 'hedef_ozeti': summary, 'satir': len(rows), 'iliski_olcusu': 'mutual information (sklearn)'}


# ---------- PyTorch (torchvision ImageFolder layout) ----------
def torch_profile(cfg, factory, out):
    datasets = driver('torchvision.datasets')
    root = Path(required(cfg, 'image_root'))
    classes, index = datasets.ImageFolder.find_classes(str(root)) if hasattr(datasets.ImageFolder, 'find_classes') else datasets.folder.find_classes(str(root))
    exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp'}
    rows, total = [], 0
    sizes = Counter()
    for cls in classes:
        files = [f for f in (root / cls).rglob('*') if f.is_file()]
        images = [f for f in files if f.suffix.lower() in exts]
        empty = [f for f in images if f.stat().st_size == 0]
        total += len(images)
        sizes.update(f.suffix.lower() for f in images)
        rows.append({'ozellik': cls, 'tip': 'sınıf', 'satir': len(images), 'dolu': len(images) - len(empty), 'bos': len(empty), 'bos_orani': round(len(empty) / len(images), 4) if images else 0,
                     'farkli': '', 'min': min((f.stat().st_size for f in images), default=''), 'max': max((f.stat().st_size for f in images), default=''),
                     'ortalama': round(sum(f.stat().st_size for f in images) / len(images)) if images else '', 'std': '', 'hedefle_iliski': '',
                     'uyari': ', '.join(x for x, ok in (('görüntü dışı dosya', len(files) == len(images)), ('boş dosya', not empty)) if not ok)})
    if not rows:
        raise NativeError('image_root altında sınıf klasörü yok (root/sinif/goruntu.jpg).')
    counts = [r['satir'] for r in rows]
    return rows, {'hedef': 'klasör sınıfı', 'hedef_ozeti': {'tur': 'sınıflandırma', 'sinif': {r['ozellik']: round(r['satir'] / total, 4) for r in rows} if total else {},
                                                             'dengesizlik': round(max(counts) / max(1, min(counts)), 2)}, 'satir': total, 'uzantilar': dict(sizes), 'boyut_birimi': 'bayt'}


# ---------- TensorFlow (TensorFlow Data Validation) ----------
def tf_profile(cfg, factory, out):
    tfdv = driver('tensorflow_data_validation')
    from google.protobuf.json_format import MessageToDict
    path = required(cfg, 'input_file')
    stats = tfdv.generate_statistics_from_tfrecord(path) if path.endswith(('.tfrecord', '.tfrecords')) else tfdv.generate_statistics_from_csv(path)
    data = MessageToDict(stats)
    ds = (data.get('datasets') or [{}])[0]
    count = int(ds.get('numExamples', 0))
    rows = []
    for f in ds.get('features', []):
        fname = (f.get('path') or {}).get('step', [f.get('name')])[0]
        num, sstats = f.get('numStats') or {}, f.get('stringStats') or {}
        common = num.get('commonStats') or sstats.get('commonStats') or {}
        missing = int(common.get('numMissing', 0))
        item = {'ozellik': fname, 'tip': f.get('type', ''), 'satir': count, 'dolu': int(common.get('numNonMissing', 0)), 'bos': missing,
                'bos_orani': round(missing / count, 4) if count else 0, 'farkli': sstats.get('unique', ''), 'min': num.get('min', ''), 'max': num.get('max', ''),
                'ortalama': num.get('mean', ''), 'std': num.get('stdDev', ''), 'hedefle_iliski': ''}
        item['uyari'] = warnings_for(item, count)
        rows.append(item)
    schema = MessageToDict(tfdv.infer_schema(stats))
    receipt(out, 'tfdv_sema.json', schema)
    return rows, {'satir': count, 'kaynak': 'TFDV generate_statistics', 'hedef': cfg.get('target', '')}


# ---------- MLflow (logged dataset inputs) ----------
def mlflow_client(cfg, factory):
    import os
    if os.environ.get('MLFLOW_TRACKING_TOKEN'):
        return bearer(factory, required(cfg, 'tracking_uri'), 'MLFLOW_TRACKING_TOKEN')
    return factory(required(cfg, 'tracking_uri'), {'Authorization': native.basic_auth(secret('MLFLOW_TRACKING_USERNAME'), secret('MLFLOW_TRACKING_PASSWORD'))})


def mlflow_runs(cfg, factory):
    client = mlflow_client(cfg, factory)
    r = client.json('POST', '/api/2.0/mlflow/runs/search', {'experiment_ids': names(cfg, 'experiment_ids', limit=20, pattern=re.compile(r'[0-9A-Za-z_-]{1,64}')),
                                                            'max_results': opt_int(cfg, 'max_runs', 20, 1, 500), 'order_by': ['attributes.start_time DESC']})
    return r.get('runs', [])


def mlflow_profile(cfg, factory, out):
    rows, datasets = [], []
    for run in mlflow_runs(cfg, factory):
        for di in ((run.get('inputs') or {}).get('dataset_inputs') or []):
            d = di.get('dataset') or {}
            tags = {t.get('key'): t.get('value') for t in di.get('tags', [])}
            prof = json.loads(d.get('profile') or '{}') if isinstance(d.get('profile'), str) else d.get('profile') or {}
            schema = json.loads(d.get('schema') or '{}') if isinstance(d.get('schema'), str) else d.get('schema') or {}
            datasets.append({'calisma': (run.get('info') or {}).get('run_id'), 'veri_kumesi': d.get('name'), 'ozet': d.get('digest'), 'kaynak_turu': d.get('source_type'),
                             'baglam': tags.get('mlflow.data.context'), 'profil': prof})
            for col in (schema.get('mlflow_colspec') or schema.get('columns') or []):
                rows.append({'ozellik': col.get('name'), 'tip': col.get('type'), 'satir': prof.get('num_rows', ''), 'dolu': '', 'bos': '', 'bos_orani': '', 'farkli': '',
                             'min': '', 'max': '', 'ortalama': '', 'std': '', 'hedefle_iliski': '', 'uyari': f"{d.get('name')} ({tags.get('mlflow.data.context', '')})"})
    if not datasets:
        raise NativeError('Çalışmalarda kayıtlı veri kümesi girdisi yok (mlflow.log_input ile kaydedin).')
    return rows, {'veri_kumeleri': datasets[:100]}


# ---------- Vertex AI (tabular dataset on BigQuery) ----------
def vertex_dataset(cfg, factory):
    location, project, dataset = name(cfg, 'location', re.compile(r'[a-z0-9-]{2,30}')), name(cfg, 'project_id', re.compile(r'[a-z][a-z0-9-]{4,61}[a-z0-9]')), required(cfg, 'dataset_id')
    client = bearer(factory, f'https://{location}-aiplatform.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    return client, client.json('GET', f'/v1/projects/{project}/locations/{location}/datasets/{ident(dataset)}')


def vertex_profile(cfg, factory, out):
    _, ds = vertex_dataset(cfg, factory)
    uri = (((ds.get('metadata') or {}).get('inputConfig') or {}).get('bigquerySource') or {}).get('uri', '')
    if not uri.startswith('bq://'):
        raise NativeError('Bu paket BigQuery kaynaklı tablo veri kümelerini profiller (metadata.inputConfig.bigquerySource).')
    project, dataset, table = uri[5:].split('.')
    bq = bearer(factory, 'https://bigquery.googleapis.com', 'GOOGLE_ACCESS_TOKEN')
    fields = (bq.json('GET', f'/bigquery/v2/projects/{ident(project)}/datasets/{ident(dataset)}/tables/{ident(table)}').get('schema') or {}).get('fields', [])
    numeric = {'INTEGER', 'INT64', 'FLOAT', 'FLOAT64', 'NUMERIC', 'BIGNUMERIC'}
    parts = ['COUNT(*) AS n']
    for i, f in enumerate(fields[:300]):
        col = f"`{f['name']}`"
        parts += [f'COUNTIF({col} IS NULL) AS m{i}', f'APPROX_COUNT_DISTINCT({col}) AS d{i}']
        if f.get('type') in numeric:
            parts += [f'MIN({col}) AS lo{i}', f'MAX({col}) AS hi{i}', f'AVG({col}) AS av{i}', f'STDDEV({col}) AS sd{i}']
    src = BigQuerySql({**cfg, 'project_id': cfg.get('billing_project') or cfg['project_id'], 'location': cfg.get('bigquery_location', 'US')}, factory, out)
    _, res = src.query(f"SELECT {', '.join(parts)} FROM `{project}.{dataset}.{table}`", 'profile')
    r = res[0] if res else {}
    n = int(number(r.get('n')) or 0)
    rows = []
    for i, f in enumerate(fields[:300]):
        miss = int(number(r.get(f'm{i}')) or 0)
        item = {'ozellik': f['name'], 'tip': f.get('type'), 'satir': n, 'dolu': n - miss, 'bos': miss, 'bos_orani': round(miss / n, 4) if n else 0, 'farkli': r.get(f'd{i}'),
                'min': r.get(f'lo{i}', ''), 'max': r.get(f'hi{i}', ''), 'ortalama': r.get(f'av{i}', ''), 'std': r.get(f'sd{i}', ''), 'hedefle_iliski': ''}
        item['uyari'] = warnings_for(item, n)
        rows.append(item)
    return rows, {'veri_kumesi': ds.get('displayName'), 'kaynak': uri, 'satir': n}


# ---------- SageMaker (Model Monitor baseline statistics in S3) ----------
def sagemaker_profile(cfg, factory, out):
    uri = required(cfg, 'statistics_s3_uri')
    m = re.fullmatch(r's3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/(.+)', uri)
    if not m:
        raise NativeError('statistics_s3_uri s3://kova/yol/statistics.json biçiminde olmalı.')
    region = region_of(cfg)
    raw = aws_call(factory, 's3', region, host=f'{m.group(1)}.s3.{region}.amazonaws.com', method='GET', path='/' + quote(m.group(2)), accept='application/octet-stream')
    stats = json.loads(raw)
    count = int((stats.get('dataset') or {}).get('item_count') or 0)
    rows = []
    for f in stats.get('features', []):
        num, txt = f.get('numerical_statistics') or {}, f.get('string_statistics') or {}
        common = num.get('common') or txt.get('common') or {}
        missing = int(common.get('num_missing') or 0)
        item = {'ozellik': f.get('name'), 'tip': f.get('inferred_type'), 'satir': count, 'dolu': common.get('num_present'), 'bos': missing,
                'bos_orani': round(missing / count, 4) if count else 0, 'farkli': txt.get('distinct_count', ''), 'min': num.get('min', ''), 'max': num.get('max', ''),
                'ortalama': num.get('mean', ''), 'std': num.get('std_dev', ''), 'hedefle_iliski': ''}
        item['uyari'] = warnings_for(item, count)
        rows.append(item)
    return rows, {'kaynak': uri, 'satir': count, 'bicim': 'Model Monitor (Deequ) statistics.json'}


# ---------- Azure Machine Learning (data asset) ----------
def azureml_profile(cfg, factory, out):
    arm = bearer(factory, 'https://management.azure.com', 'AZURE_ACCESS_TOKEN')
    ws = (f"/subscriptions/{ident(required(cfg, 'subscription_id'))}/resourceGroups/{ident(required(cfg, 'resource_group'))}"
          f"/providers/Microsoft.MachineLearningServices/workspaces/{ident(required(cfg, 'workspace'))}")
    asset = arm.json('GET', f"{ws}/data/{ident(required(cfg, 'data_name'))}/versions/{ident(str(required(cfg, 'data_version')))}?api-version=2024-04-01")
    uri = (asset.get('properties') or {}).get('dataUri', '')
    m = re.fullmatch(r'azureml://datastores/([^/]+)/paths/(.+)', uri)
    if m:
        store = arm.json('GET', f'{ws}/datastores/{ident(m.group(1))}?api-version=2024-04-01').get('properties') or {}
        account, container, blob = store.get('accountName'), store.get('containerName'), m.group(2)
    else:
        parts = urlsplit(uri)
        if not parts.netloc.endswith('.blob.core.windows.net'):
            raise NativeError('Veri varlığı Blob/ADLS dosya URI’si olmalı (azureml://datastores/... veya https://*.blob.core.windows.net/...).')
        account, (container, blob) = parts.netloc.split('.')[0], parts.path.lstrip('/').split('/', 1)
    if not blob.lower().endswith(('.csv', '.json', '.jsonl')):
        raise NativeError('Bu paket CSV/JSON tek dosya veri varlıklarını profiller (klasör/MLTable için dosyayı seçin).')
    storage = factory(f'https://{account}.blob.core.windows.net', {'Authorization': 'Bearer ' + secret('STORAGE_ACCESS_TOKEN'), 'x-ms-version': '2023-11-03'})
    raw, _ = storage.request('GET', f'/{quote(container)}/{quote(blob)}', accept='application/octet-stream', limit=512 * 1024 * 1024)
    tmp = out / ('.veri' + Path(blob).suffix)
    tmp.write_bytes(raw)
    try:
        columns, rows = read_table(tmp)
    finally:
        tmp.unlink()
    target = cfg.get('target') or ''
    prof = profile([c for c in columns if c != target], rows)
    out_rows = []
    for p in prof:
        item = {'ozellik': p['kolon'], **{k: p.get(k, '') for k in ('tip', 'satir', 'dolu', 'bos', 'bos_orani', 'farkli', 'min', 'max', 'ortalama', 'std')}, 'hedefle_iliski': ''}
        item['uyari'] = warnings_for(item, len(rows))
        out_rows.append(item)
    extra = {'veri_varligi': f"{cfg['data_name']}:{cfg['data_version']}", 'satir': len(rows)}
    if target and target in columns:
        extra.update(hedef=target, hedef_ozeti=target_summary(rows, target))
    return out_rows, extra


# ---------- DataRobot (platform EDA) ----------
def datarobot_client(cfg, factory):
    return bearer(factory, cfg.get('endpoint', 'https://app.datarobot.com'), 'DATAROBOT_API_TOKEN')


def datarobot_profile(cfg, factory, out):
    client = datarobot_client(cfg, factory)
    dataset = required(cfg, 'dataset_id')
    feats, path = [], f'/api/v2/datasets/{ident(dataset)}/allFeaturesDetails/?limit=100'
    for _ in range(100):
        r = client.json('GET', path)
        feats += r.get('data', [])
        nxt = r.get('next')
        if not nxt:
            break
        path = urlsplit(nxt).path + ('?' + urlsplit(nxt).query if urlsplit(nxt).query else '')
    rows = []
    for f in feats:
        item = {'ozellik': f.get('name'), 'tip': f.get('featureType'), 'satir': '', 'dolu': '', 'bos': f.get('naCount'), 'bos_orani': '', 'farkli': f.get('uniqueCount'),
                'min': f.get('min'), 'max': f.get('max'), 'ortalama': f.get('mean'), 'std': f.get('stdDev'), 'hedefle_iliski': ''}
        item['uyari'] = ', '.join(x for x in (warnings_for(item, 0), 'düşük bilgi' if f.get('lowInformation') else '', 'hedef sızıntısı' if f.get('targetLeakage') in ('HIGH_RISK', 'MODERATE_RISK') else '') if x)
        rows.append(item)
    return rows, {'veri_kumesi': dataset, 'kaynak': 'DataRobot EDA (allFeaturesDetails)'}


PRODUCTS = {'sklearn': ('scikit-learn', sklearn_profile), 'pytorch': ('PyTorch', torch_profile), 'tensorflow': ('TensorFlow', tf_profile), 'mlflow': ('MLflow', mlflow_profile),
            'vertex-ai': ('Vertex AI', vertex_profile), 'sagemaker': ('SageMaker', sagemaker_profile), 'azure-ml': ('Azure Machine Learning', azureml_profile),
            'datarobot': ('DataRobot', datarobot_profile)}


def training_profile(cfg, out, factory):
    key = cfg['operation'].rsplit('-training-profile', 1)[0]
    label, reader = PRODUCTS[key]
    rows, extra = reader(cfg, factory, out)
    if not rows:
        raise NativeError('Profil boş döndü.')
    write_csv(out, 'egitim_verisi_profili.csv', [{k: plain(v) for k, v in r.items()} for r in rows], COLUMNS)
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, **{k: v for k, v in extra.items() if k != 'veri_kumeleri'}, 'ozellik': len(rows),
                                      'uyarili': [r['ozellik'] for r in rows if r.get('uyari')][:50]})


# ---------- Evaluation & drift strategy agent ----------
def metrics_evidence(key, cfg, factory):
    if key == 'mlflow':
        return [{'calisma': (r.get('info') or {}).get('run_id'), 'metrikler': {m.get('key'): m.get('value') for m in (r.get('data') or {}).get('metrics', [])}} for r in mlflow_runs(cfg, factory)][:30]
    if key == 'datarobot' and cfg.get('project_id'):
        r = datarobot_client(cfg, factory).json('GET', f"/api/v2/projects/{ident(cfg['project_id'])}/models/?limit=20")
        models = r.get('data', r) if isinstance(r, dict) else r
        return [{'model': m.get('modelType'), 'metrikler': {k: (v or {}).get('validation') for k, v in (m.get('metrics') or {}).items()}} for m in models[:20]]
    path = cfg.get('metrics_file')
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding='utf-8'))
    return []


DRIFT = {'psi', 'ks', 'chi2', 'jensen_shannon', 'wasserstein', 'l_infinity'}
SCHEMA = ('{"degerlendirme_metrikleri":[{"ad":"roc_auc","esik":0.8,"yon":"buyuk_iyi|kucuk_iyi","gerekce":"..."}],'
          '"drift_izleme":[{"ozellik":"kanıttaki özellik","yontem":"psi|ks|chi2|jensen_shannon|wasserstein|l_infinity","esik":0.2,"pencere":"gunluk"}],'
          '"dilimler":[{"ozellik":"kanıttaki özellik","neden":"adalet/segment performansı"}],"yeniden_egitim":{"tetikleyiciler":["..."],"siklik":"aylık","kabul_kriteri":"..."},"sorular":["..."]}')


def vertex_monitoring(result):
    skew = {d['ozellik']: {'value': d['esik']} for d in result['drift_izleme'] if d['yontem'] in ('jensen_shannon', 'l_infinity')}
    return json.dumps({'objectiveConfigs': [{'trainingPredictionSkewDetectionConfig': {'skewThresholds': skew}, 'predictionDriftDetectionConfig': {'driftThresholds': skew}}],
                       'not': 'Vertex AI model izleme; JS/L-inf dışındaki yöntemler kendi izleme işinizde uygulanmalı.'}, ensure_ascii=False, indent=2)


def drift_strategy(cfg, out, factory):
    key = cfg['operation'].rsplit('-drift-strategy', 1)[0]
    label, reader = PRODUCTS[key]
    rows, extra = reader(cfg, factory, out)
    features = {str(r['ozellik']).lower(): r['ozellik'] for r in rows}
    evidence = {'profil': rows[:500], 'veri': {k: v for k, v in extra.items()}, 'model_metrikleri': metrics_evidence(key, cfg, factory)}

    def validate(obj):
        for i, m in enumerate(need_list(obj, 'degerlendirme_metrikleri', 'değerlendirme metrikleri', maximum=12), 1):
            need_text(m, 'ad', f'metrik {i}')
            need_text(m, 'yon', f'metrik {i}', choices={'buyuk_iyi', 'kucuk_iyi'})
            if isinstance(m.get('esik'), bool) or not isinstance(m.get('esik'), (int, float)):
                raise ValueError(f'metrik {i}: esik sayı olmalı.')
        for i, d in enumerate(need_list(obj, 'drift_izleme', 'drift izleme', maximum=300), 1):
            f = str(d.get('ozellik', '')).lower()
            if f not in features:
                raise ValueError(f'drift {i}: kanıtta olmayan özellik: {d.get("ozellik")}')
            d['ozellik'] = features[f]
            need_text(d, 'yontem', f'drift {i}', choices=DRIFT)
            t = d.get('esik')
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t <= 10:
                raise ValueError(f'drift {i}: esik 0–10 arası sayı olmalı.')
        for s in obj.get('dilimler') or []:
            if not isinstance(s, dict) or str(s.get('ozellik', '')).lower() not in features:
                raise ValueError('dilim kanıtta olmayan özelliği gösteriyor.')
        if not isinstance(obj.get('yeniden_egitim'), dict):
            raise ValueError('"yeniden_egitim" nesne olmalı.')
        return obj

    def render(result):
        files = {'drift_stratejisi.json': plan_json(result),
                 'drift_stratejisi.md': rows_md('Model değerlendirme ve drift stratejisi — ' + label,
                                                [('Değerlendirme metrikleri', result['degerlendirme_metrikleri'], ['ad', 'esik', 'yon', 'gerekce']),
                                                 ('Drift izleme', result['drift_izleme'], ['ozellik', 'yontem', 'esik', 'pencere']),
                                                 ('Dilimler', result.get('dilimler') or [], ['ozellik', 'neden']),
                                                 ('Yeniden eğitim', [result['yeniden_egitim']], ['tetikleyiciler', 'siklik', 'kabul_kriteri']),
                                                 ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
        if key == 'vertex-ai':
            files['vertex_izleme_ayari.json'] = vertex_monitoring(result)
        return files
    return agent_task(cfg, out, product=label, title='Model değerlendirme ve drift stratejisi', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Metrik seçimini hedef türü ve sınıf dengesizliğine göre yap; sızıntı uyarısı olan özellikleri sorular bölümünde ele al. '
                            'Kategorik özelliklerde chi2/jensen_shannon, sayısalda psi/ks/wasserstein kullan.')


HANDLERS = {}
for _key in PRODUCTS:
    HANDLERS[_key + '-training-profile'] = training_profile
    HANDLERS[_key + '-drift-strategy'] = drift_strategy
