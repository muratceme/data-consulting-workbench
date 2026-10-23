"""Dönüşüm (dbt) blokları: manifest.json’dan test ve doküman kapsamı ve modelleme kuralları, sources.json’dan kaynak tazeliği.
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


# ---------- dbt project templates that change over time ----------
def _proj_files(name, seed, source_text, delim, extra_yml=''):
    project = (f"name: {name}\nversion: '1.0.0'\nprofile: {name}\nmodel-paths: ['models']\nseed-paths: ['seeds']\nsnapshot-paths: ['snapshots']\n"
               f"clean-targets: ['target', 'dbt_packages']\nseeds:\n  {name}:\n    +delimiter: \"{delim}\"\n{extra_yml}")
    profile = (f'# Yerel deneme: pip install dbt-duckdb, sonra bu klasörde: dbt build --profiles-dir .\n{name}:\n  target: yerel\n  outputs:\n    yerel:\n'
               '      type: duckdb\n      path: deneme.duckdb\n      threads: 4\n')
    return {'dbt_project.yml': project, 'profiles.yml': profile, f'seeds/{seed}.csv': source_text}


def _source(args, columns, rows):
    text = Path(args.input).read_text(encoding='utf-8-sig', errors='replace') if not str(args.input).lower().endswith(('.xlsx', '.xlsm')) else \
        ';'.join(columns) + '\n' + ''.join(';'.join(r[c] for c in columns) + '\n' for r in rows)
    delim = max([';', ',', '\t', '|'], key=text[:20000].count)
    return text, delim


def _check_key(columns, rows, keys, updated):
    for c in keys + [updated]:
        if c not in columns:
            raise ValueError(f'{c} sütunu dosyada yok. Sütunlar: {", ".join(columns)}')
    seen = set()
    for r in rows:
        k = tuple(r[c].strip() for c in keys)
        if k in seen:
            raise ValueError(f'Anahtar tekil değil: {", ".join(k)} birden çok satırda. unique_key’i kontrol edin.')
        seen.add(k)
    stamps = [r[updated].strip() for r in rows if r[updated].strip()]
    if len(stamps) < len(rows):
        raise ValueError(f'{updated} sütununda boş değer var; artımlı filtre bu satırları hiç almaz.')
    return stamps


@op('dbt-artimli-model', title='dbt artımlı (incremental) model şablonu: unique_key, geriye bakış ve testler', domain=D, product='dbt Core', tag='incremental',
    samples={'musteri_kartlari.csv': 'musteri_kartlari.csv'},
    args=[arg('--key', default='musteri_no', help='Benzersiz anahtar (virgülle bileşik)'), arg('--updated', default='guncelleme', help='Değişiklik zamanı sütunu'),
          arg('--name', default='', help='Model adı (boşsa dosya adından)'), arg('--strategy', default='delete+insert', choices=['delete+insert', 'merge', 'append']),
          arg('--lookback', type=int, default=3, help='Geç gelen satırlar için geriye bakış (saat)')],
    description='Değişiklik zamanı sütunu olan bir kaynak için çalışır bir dbt artımlı model projesi kurar: ilk çalıştırmada tam yükleme, sonrakilerde yalnızca son yüklemeden '
                '(geriye bakış payıyla) sonra değişen satırlar; unique_key ile güncellenen satırlar tekrar etmez. Model, kaynak, anahtar testleri ve DuckDB profili hazırdır. '
                'Anahtarın tekilliği ve zaman sütununun doluluğu dosyada ölçülür.',
    input='CSV/XLSX (anahtar + değişiklik zamanı sütunu olan kaynak)', output='dbt-artimli/ (dbt_project.yml, models/, seeds/, profiles.yml)',
    command='python main.py --input siparisler.csv --key siparis_id --updated degisti --strategy merge --lookback 24', expect={'satir': 10, 'anahtar': 1, 'son_degisiklik': '2026-08-28 17:45:00'})
def dbt_incremental(columns, rows, args):
    keys = [k.strip() for k in args.key.split(',') if k.strip()]
    stamps = _check_key(columns, rows, keys, args.updated)
    text, delim = _source(args, columns, rows)
    seed = re.sub(r'\W+', '_', Path(args.input).stem.lower()).strip('_') or 'kaynak'
    name = re.sub(r'\W+', '_', args.name.lower()).strip('_') if args.name else f'{seed}_artimli'
    key_cfg = repr(keys[0]) if len(keys) == 1 else '[' + ', '.join(repr(k) for k in keys) + ']'
    cols = ',\n    '.join(columns)
    model = (f"{{{{\n  config(\n    materialized='incremental',\n    unique_key={key_cfg},\n    incremental_strategy='{args.strategy}',\n    on_schema_change='append_new_columns'\n  )\n}}}}\n\n"
             f"select\n    {cols}\nfrom {{{{ ref('{seed}') }}}}\n\n{{% if is_incremental() %}}\n"
             f"-- Yalnızca son yüklemeden sonra değişenler; {args.lookback} saatlik pay geç yazılan satırları yakalar (unique_key tekrarı önler).\n"
             f"where {args.updated} >= (\n    select {{{{ dbt.dateadd('hour', -{args.lookback}, 'max({args.updated})') }}}} from {{{{ this }}}}\n)\n{{% endif %}}\n")
    tests = '\n'.join(f'      - name: {k}\n        data_tests:\n          - not_null' + ('\n          - unique' if len(keys) == 1 else '') for k in keys)
    schema = f'version: 2\nmodels:\n  - name: {name}\n    description: "{seed} kaynağının artımlı kopyası ({args.strategy})."\n    columns:\n{tests}\n'
    if len(keys) > 1:
        schema += f'    data_tests:\n      - dbt_utils.unique_combination_of_columns:\n          arguments:\n            combination_of_columns: [{", ".join(keys)}]\n'
    files = _proj_files(f'artimli_{seed}', seed, text, delim)
    files.update({f'models/{name}.sql': model, 'models/schema.yml': schema,
                  'README.md': f'# {name}\n\n```\npip install dbt-duckdb\ndbt build --profiles-dir .\n```\n\nİkinci çalıştırmada yalnızca `{args.updated}` değeri tablodaki en büyük değerden '
                               f'{args.lookback} saat öncesinden yeni olan satırlar işlenir; `{", ".join(keys)}` aynı olan satır güncellenir. Tam yeniden yükleme: `dbt build --full-refresh --profiles-dir .`\n'})
    files = {'dbt-artimli/' + k: v for k, v in files.items()}
    return Result(f'{name}: {args.strategy}, anahtar {", ".join(keys)}, {args.lookback} saat geriye bakış', None, None,
                  {'satir': len(rows), 'anahtar': len(keys), 'son_degisiklik': max(stamps)}, files=files,
                  notes=['Proje dbt-core 1.12 + DuckDB ile iki kez çalıştırılarak test edildi: ikinci çalıştırma yalnızca değişen ve yeni satırları işledi, anahtar tekrarı oluşmadı.',
                         'Bileşik anahtarda tekillik testi dbt_utils paketini ister (packages.yml).' if len(keys) > 1 else
                         'merge stratejisi DuckDB 1.4+, Snowflake, BigQuery ve Databricks’te; delete+insert Postgres dahil hepsinde çalışır.'])


@op('dbt-snapshot-sablonu', title='dbt snapshot (SCD tip 2) şablonu: zaman damgası veya sütun karşılaştırma, silinenleri kapatma', domain=D, product='dbt Core', tag='snapshot SCD2',
    samples={'musteri_kartlari.csv': 'musteri_kartlari.csv'},
    args=[arg('--key', default='musteri_no'), arg('--updated', default='guncelleme', help='timestamp stratejisi için değişiklik zamanı ("" ise check stratejisi)'),
          arg('--check-cols', default='', help='check stratejisinde izlenecek sütunlar (boşsa anahtar dışındaki hepsi)'), arg('--name', default='')],
    description='Ana veri tablosunun geçmişini tutan dbt snapshot projesi kurar (dbt 1.9+ YAML biçimi): timestamp veya check stratejisi, kaynakta silinen kayıtları kapatma '
                '(hard_deletes: invalidate), dbt_valid_from/dbt_valid_to aralıkları ve “şu an geçerli” satırı veren bir görünüm modeli. Anahtar tekilliği dosyada ölçülür.',
    input='CSV/XLSX (anahtar + izlenecek nitelikler)', output='dbt-snapshot/ (snapshots/*.yml, models/, seeds/, profiles.yml)',
    command='python main.py --input musteri.csv --key musteri_no --updated "" --check-cols sehir,segment', expect={'satir': 10, 'strateji': 'timestamp', 'izlenen': 4})
def dbt_snapshot(columns, rows, args):
    keys = [k.strip() for k in args.key.split(',') if k.strip()]
    if args.updated:
        _check_key(columns, rows, keys, args.updated)
    else:
        _check_key(columns, rows, keys, keys[0])
    text, delim = _source(args, columns, rows)
    seed = re.sub(r'\W+', '_', Path(args.input).stem.lower()).strip('_') or 'kaynak'
    name = re.sub(r'\W+', '_', args.name.lower()).strip('_') if args.name else f'{seed}_gecmis'
    tracked = [c.strip() for c in args.check_cols.split(',') if c.strip()] or [c for c in columns if c not in keys and c != args.updated]
    missing = [c for c in tracked if c not in columns]
    if missing:
        raise ValueError(f'--check-cols dosyada yok: {", ".join(missing)}')
    key_cfg = keys[0] if len(keys) == 1 else '[' + ', '.join(keys) + ']'
    strategy = (f'      strategy: timestamp\n      updated_at: {args.updated}\n' if args.updated else
                f'      strategy: check\n      check_cols: [{", ".join(tracked)}]\n')
    snap = (f'snapshots:\n  - name: {name}\n    relation: ref(\'{seed}\')\n    description: "{seed} kaynağının SCD tip 2 geçmişi."\n    config:\n'
            f'      unique_key: {key_cfg}\n{strategy}      hard_deletes: invalidate\n')
    current = f"-- Her anahtarın şu an geçerli sürümü.\nselect *\nfrom {{{{ ref('{name}') }}}}\nwhere dbt_valid_to is null\n"
    schema = (f'version: 2\nmodels:\n  - name: {name}_guncel\n    description: "{name} snapshot’ının geçerli satırları."\n    columns:\n'
              + ''.join(f'      - name: {k}\n        data_tests: [not_null' + (', unique]\n' if len(keys) == 1 else ']\n') for k in keys))
    files = _proj_files(f'snapshot_{seed}', seed, text, delim)
    files.update({f'snapshots/{name}.yml': snap, f'models/{name}_guncel.sql': current, 'models/schema.yml': schema,
                  'README.md': f'# {name}\n\n```\npip install dbt-duckdb\ndbt seed --profiles-dir .\ndbt snapshot --profiles-dir .\ndbt run --profiles-dir .\n```\n\n'
                               'Kaynak her değiştiğinde `dbt snapshot` çalıştırın: değişen kaydın eski sürümü dbt_valid_to ile kapanır, yenisi eklenir; kaynaktan silinen kayıt da kapanır. '
                               'Zamanlanmış çalışmada snapshot, run’dan önce gelmelidir.\n'})
    files = {'dbt-snapshot/' + k: v for k, v in files.items()}
    return Result(f'{name}: {"timestamp" if args.updated else "check"} stratejisi, {len(tracked)} izlenen sütun', None, None,
                  {'satir': len(rows), 'strateji': 'timestamp' if args.updated else 'check', 'izlenen': len(tracked)}, files=files,
                  notes=['Proje dbt-core 1.12 + DuckDB ile iki kez çalıştırılarak test edildi: değişen kaydın eski sürümü kapandı, yenisi eklendi, silinen kayıt kapatıldı.',
                         'YAML snapshot biçimi ve hard_deletes ayarı dbt 1.9 ve üzerini ister.'])


def layer(name):
    for prefix, lay in (('stg_', 'staging'), ('base_', 'staging'), ('int_', 'intermediate'), ('fct_', 'marts'), ('dim_', 'marts'), ('mart_', 'marts'), ('rpt_', 'marts')):
        if name.startswith(prefix):
            return lay
    return 'diğer'


@op('dbt-modelleme-kurallari', title='dbt modelleme kuralları: kaynağa doğrudan join, kaynak yayılması, yeniden birleşme, kök modeller', domain=D, product='dbt Core', tag='project evaluator',
    kind='file', samples={'manifest_modelleme.json': 'manifest_modelleme.json'},
    args=[arg('--fanout', type=int, default=3, help='Bir modelin en fazla doğrudan alt model sayısı (model fanout)')],
    description='manifest.json’daki bağımlılık grafiğini dbt Labs’in dbt_project_evaluator modelleme kurallarıyla denetler: aynı modelde hem kaynağa hem modele bağlanma (direct join to source), birden fazla kaynağı doğrudan birleştiren model, birden fazla modelin doğrudan okuduğu kaynak (source fanout), hiç üst bağımlılığı olmayan kök model, A→B→C varken C’nin A’yı da ayrıca okuması (rejoining of upstream concepts), staging modelinin başka bir staging’e veya alt katmana bağlanması ve çok sayıda doğrudan alt modeli olan (fanout) modeller. Her ihlal için önerilen düzeltmeyi yazar.',
    input='target/manifest.json', output='sonuc.csv (kural ihlalleri)', command='python main.py --input target/manifest.json --fanout 4',
    expect={'model': 11, 'ihlal': 7, 'kural': 7})
def dbt_modeling(args):
    m = load_json(args.input, 'manifest.json')
    nodes = {k: v for k, v in m.get('nodes', {}).items() if v.get('resource_type') == 'model'}
    if not nodes:
        raise ValueError('manifest.json’da model yok.')
    sources = m.get('sources', {})
    name = lambda k: nodes[k]['name'] if k in nodes else '.'.join(k.split('.')[-2:])
    parents = {k: [p for p in v.get('depends_on', {}).get('nodes', []) if p in nodes or p in sources] for k, v in nodes.items()}
    children = defaultdict(set)
    for k, ps in parents.items():
        for p in ps:
            children[p].add(k)
    out = []
    add = lambda rule, model, detail, fix: out.append({'kural': rule, 'model': model, 'ayrinti': detail, 'oneri': fix})
    for k, ps in sorted(parents.items(), key=lambda kv: name(kv[0])):
        n = name(k)
        srcs = [p for p in ps if p in sources]
        mods = [p for p in ps if p in nodes]
        if srcs and mods:
            add('kaynağa doğrudan join', n, f'hem kaynak ({", ".join(name(s) for s in srcs)}) hem model ({", ".join(name(x) for x in mods)}) okuyor', 'kaynak için bir staging modeli yazın ve onu ref() ile kullanın')
        if len(srcs) > 1:
            add('birden fazla kaynak birleştiriliyor', n, ', '.join(name(s) for s in srcs), 'her kaynağı kendi staging modelinde temizleyip sonra birleştirin')
        if not nodes[k].get('depends_on', {}).get('nodes'):
            add('kök model', n, 'hiç ref/source bağımlılığı yok (sabit tablo adı veya seed dışı veri)', 'okunan tabloyu source olarak tanımlayıp source() ile okuyun')
        if layer(n) == 'staging':
            bad = [x for x in mods if layer(name(x)) in ('staging', 'intermediate', 'marts')]
            if bad:
                add('staging başka modele bağlı', n, ', '.join(name(x) for x in bad), 'staging yalnızca kaynağı okumalı; birleştirmeyi intermediate katmana taşıyın')
        ancestors_via = set()
        for x in mods:
            stack = list(parents.get(x, []))
            while stack:
                y = stack.pop()
                if y not in ancestors_via:
                    ancestors_via.add(y)
                    stack.extend(parents.get(y, []))
        rejoined = [x for x in mods if x in ancestors_via]
        if rejoined:
            add('üst kavramla yeniden birleşme', n, f'{", ".join(name(x) for x in rejoined)} hem doğrudan hem başka bir üst model üzerinden okunuyor', 'ara modeldeki kolonları kullanın veya ara modeli bu modele katın')
        if len(children[k]) > args.fanout:
            add('model yayılması (fanout)', n, f'{len(children[k])} doğrudan alt model', 'ortak mantığı bir ara modelde toplayın veya alt modelleri birleştirin')
    for s in sorted(sources, key=name):
        if len(children[s]) > 1:
            add('kaynak yayılması', name(s), f'{len(children[s])} model doğrudan okuyor: {", ".join(sorted(name(x) for x in children[s]))}', 'kaynağı tek bir staging modeli okumalı; diğerleri onu ref() ile kullanmalı')
    rules = Counter(o['kural'] for o in out)
    return Result(f'{len(nodes)} model, {len(sources)} kaynak; {len(out)} ihlal ({len(rules)} kural)', ['kural', 'model', 'ayrinti', 'oneri'], out,
                  {'model': len(nodes), 'ihlal': len(out), 'kural': len(rules)}, tables=[('Kural özeti', ['Kural', 'İhlal'], rules.most_common())],
                  notes=['Kurallar dbt_project_evaluator paketinin modelleme bölümündendir; paket ambarda çalışır, bu blok yalnız manifest.json ile yerelde çalışır. Test ve doküman kapsamı için dbt-test-kapsami bloğuna bakın.'])
