"""Dönüşüm (dbt) blokları: manifest.json’dan test ve doküman kapsamı, sources.json’dan kaynak tazeliği.
dbt’yi çalıştırmaz; target/ klasöründeki çıktı dosyalarını okur."""
import json, re
from collections import Counter, defaultdict
from pathlib import Path
from islem import op, arg, Result, fmt

D = 'transform'


def load_json(path, label):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'{label} okunamadı: {exc}') from None


@op('dbt-test-kapsami', title='dbt modellerinde test ve doküman kapsamı', domain=D, product='dbt Core', tag='test kapsamı', kind='file', samples={'manifest.json': 'manifest.json'},
    args=[arg('--min-doc', type=float, default=80.0, help='Sütun açıklama oranı bu yüzdenin altındaysa uyar')],
    description='dbt manifest.json’daki her model için tanımlı test sayısını, birincil anahtar testinin (aynı sütunda unique + not_null) olup olmadığını, model ve sütun açıklama oranını hesaplar; testsiz, açıklamasız ve anahtarı test edilmemiş modelleri listeler. dbt projesinin bakım kalitesini PR incelemesinde veya CI’da ölçmek için.',
    input='target/manifest.json', output='sonuc.csv (model bazında kapsam)', command='python main.py --input target/manifest.json --min-doc 90',
    expect={'model': 7, 'testsiz_model': 4, 'anahtari_testli': 2})
def coverage(args):
    m = load_json(args.input, 'manifest.json')
    nodes = m.get('nodes', {})
    models = {k: v for k, v in nodes.items() if v.get('resource_type') == 'model'}
    if not models:
        raise ValueError('manifest.json içinde model yok.')
    tests = defaultdict(list)
    for v in nodes.values():
        if v.get('resource_type') != 'test':
            continue
        target = v.get('attached_node') or next((d for d in v.get('depends_on', {}).get('nodes', []) if d in models), None)
        if target:
            tests[target].append(((v.get('test_metadata') or {}).get('name') or v.get('name', ''), v.get('column_name') or (v.get('test_metadata') or {}).get('kwargs', {}).get('column_name')))
    out = []
    for uid, mdl in sorted(models.items(), key=lambda kv: kv[1].get('name', '')):
        cols = mdl.get('columns') or {}
        documented = sum(bool((c.get('description') or '').strip()) for c in cols.values())
        ts = tests.get(uid, [])
        kinds = defaultdict(set)
        for name, col in ts:
            if col:
                kinds[col].add(name)
        pk = sorted(c for c, k in kinds.items() if {'unique', 'not_null'} <= k)
        doc_pct = round(100 * documented / len(cols), 1) if cols else ''
        issues = []
        if not ts:
            issues.append('test yok')
        elif not pk:
            issues.append('anahtar testi (unique + not_null) yok')
        if not (mdl.get('description') or '').strip():
            issues.append('model açıklaması yok')
        if cols and doc_pct < args.min_doc:
            issues.append(f'sütun açıklaması %{doc_pct}')
        if not cols:
            issues.append('sütunlar belgelenmemiş')
        out.append({'model': mdl.get('name', uid), 'materializasyon': (mdl.get('config') or {}).get('materialized', ''), 'test': len(ts), 'anahtar': ', '.join(pk),
                    'sutun': len(cols), 'aciklamali_sutun_%': doc_pct, 'bulgu': '; '.join(issues)})
    out.sort(key=lambda o: (o['test'] > 0, o['model']))
    all_cols = sum(o['sutun'] for o in out)
    doc_cols = sum(round(o['sutun'] * (o['aciklamali_sutun_%'] or 0) / 100) for o in out)
    return Result(f'{len(out)} model; {sum(o["test"] == 0 for o in out)} testsiz, {sum(bool(o["anahtar"]) for o in out)} modelin anahtarı test ediliyor', list(out[0]), out,
                  {'model': len(out), 'testsiz_model': sum(o['test'] == 0 for o in out), 'anahtari_testli': sum(bool(o['anahtar']) for o in out),
                   'aciklamasiz_model': sum('model açıklaması yok' in o['bulgu'] for o in out), 'sutun_dokuman_%': round(100 * doc_cols / all_cols, 1) if all_cols else 0})


def hours(c):
    n, period = (c or {}).get('count'), (c or {}).get('period')
    return None if n is None else n * {'minute': 1 / 60, 'hour': 1, 'day': 24}.get(period, 1)


@op('dbt-kaynak-tazeligi', title='dbt source freshness sonuçlarını özetle', domain=D, product='dbt Core', tag='source freshness', kind='file', samples={'sources.json': 'sources.json'},
    description='dbt source freshness komutunun ürettiği sources.json’dan her kaynak tablonun son yükleme zamanını, kaç saat önce yüklendiğini, uyarı/hata eşiklerini ve durumunu (pass, warn, error) çıkarır; eşiği tanımlanmamış kaynakları ayrıca listeler. Sabah kontrolünü ve kaynak sistem gecikmelerini takip etmek için.',
    input='target/sources.json', output='sonuc.csv (kaynak bazında tazelik)', command='python main.py --input target/sources.json',
    expect={'kaynak_sayisi': 5, 'pass': 3, 'warn': 1, 'error': 1, 'esiksiz': 1})
def freshness(args):
    data = load_json(args.input, 'sources.json')
    res = data.get('results') or []
    if not res:
        raise ValueError('sources.json içinde results yok.')
    out = []
    for r in res:
        crit = r.get('criteria') or {}
        warn, err = hours(crit.get('warn_after')), hours(crit.get('error_after'))
        age = r.get('max_loaded_at_time_ago_in_s')
        status = (r.get('status') or '').lower()
        out.append({'kaynak': r.get('unique_id', '').replace('source.', '', 1), 'son_yukleme': (r.get('max_loaded_at') or '')[:19].replace('T', ' '),
                    'yas_saat': round(age / 3600, 1) if isinstance(age, (int, float)) else '', 'uyari_esik_saat': fmt(warn), 'hata_esik_saat': fmt(err),
                    'durum': status or 'bilinmiyor', 'not': 'eşik tanımlı değil' if warn is None and err is None else ''})
    order = {'error': 0, 'runtime error': 0, 'warn': 1, 'pass': 2}
    out.sort(key=lambda o: (order.get(o['durum'], 3), o['kaynak']))
    c = Counter(o['durum'] for o in out)
    return Result(f'{len(out)} kaynak; {c["pass"]} güncel, {c["warn"]} uyarı, {c["error"] + c["runtime error"]} hata', list(out[0]), out,
                  {'kaynak_sayisi': len(out), 'pass': c['pass'], 'warn': c['warn'], 'error': c['error'] + c['runtime error'], 'esiksiz': sum(bool(o['not']) for o in out),
                   'olusturma': (data.get('metadata') or {}).get('generated_at', '')})


LOOKBACK = re.compile(r'interval|dateadd|date_sub|timestamp_sub|datetime_sub|date_add|-\s*\d|\blookback\b|var\(', re.I)


@op('dbt-artimli-model-kontrol', title='dbt incremental modellerinde anahtar, filtre ve geç gelen veri riskleri', domain=D, product='dbt Core', tag='incremental', kind='file',
    samples={'manifest_artimli.json': 'manifest_artimli.json'},
    description='manifest.json’daki incremental modelleri tek tek inceler: unique_key tanımsız merge/delete+insert modellerini (tekrar eden satır), is_incremental() filtresi olmayan modelleri (her çalışmada tüm tabloyu tarar), MAX(...) ile filtreleyip geri bakış penceresi bırakmayan modelleri (geç gelen veri kaçar) ve on_schema_change ayarı olmayan modelleri (yeni sütunlar sessizce yok sayılır) işaretler.',
    input='target/manifest.json', output='sonuc.csv (incremental model bazında bulgular)', command='python main.py --input target/manifest.json',
    expect={'artimli_model': 4, 'sorunlu': 2, 'unique_key_yok': 1, 'filtre_yok': 1, 'geri_bakis_yok': 1, 'sema_ayari_yok': 2})
def incremental(args):
    m = load_json(args.input, 'manifest.json')
    models = [v for v in m.get('nodes', {}).values() if v.get('resource_type') == 'model' and (v.get('config') or {}).get('materialized') == 'incremental']
    if not models:
        raise ValueError('manifest.json içinde incremental model yok.')
    out = []
    for v in sorted(models, key=lambda x: x.get('name', '')):
        cfg, code = v.get('config') or {}, v.get('raw_code') or v.get('raw_sql') or ''
        strategy = (cfg.get('incremental_strategy') or 'varsayılan').lower()
        key = cfg.get('unique_key')
        issues, warns = [], []
        if not key and strategy not in ('append', 'insert_overwrite', 'microbatch'):
            issues.append('unique_key yok: yeniden çalışmada tekrar eden satır')
        filt = re.search(r'is_incremental\s*\(\s*\)', code)
        if not filt and strategy != 'microbatch':
            issues.append('is_incremental() filtresi yok: her çalışmada tüm kaynak taranır')
        elif filt:
            block = code[filt.end():filt.end() + 400]
            if re.search(r'max\s*\(', block, re.I) and not LOOKBACK.search(block):
                issues.append('MAX(...) filtresinde geri bakış yok: geç gelen veri kaçar')
        if not cfg.get('on_schema_change') or cfg.get('on_schema_change') == 'ignore':
            warns.append('on_schema_change yok/ignore: yeni sütunlar yüklenmez')
        out.append({'model': v.get('name', ''), 'strateji': strategy, 'unique_key': ', '.join(key) if isinstance(key, list) else (key or ''),
                    'on_schema_change': cfg.get('on_schema_change') or '', 'bulgu': '; '.join(issues), 'uyari': '; '.join(warns)})
    out.sort(key=lambda o: (not o['bulgu'], o['model']))
    flags = ' '.join(o['bulgu'] for o in out)
    return Result(f'{len(out)} incremental model; {sum(bool(o["bulgu"]) for o in out)} modelde risk', ['model', 'strateji', 'unique_key', 'on_schema_change', 'bulgu', 'uyari'], out,
                  {'artimli_model': len(out), 'sorunlu': sum(bool(o['bulgu']) for o in out), 'unique_key_yok': flags.count('unique_key yok'),
                   'filtre_yok': flags.count('filtresi yok'), 'geri_bakis_yok': flags.count('geri bakış yok'), 'sema_ayari_yok': sum(bool(o['uyari']) for o in out)},
                  notes=['Geri bakış kontrolü, is_incremental() bloğundaki ilk 400 karakterde interval/dateadd/var() gibi bir pencere arar; özel makrolar kullanıyorsanız elle doğrulayın.'])
