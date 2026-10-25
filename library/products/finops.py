"""Data FinOps: usage & cost summary from the platform's billing data (code) and an evidence-bound savings/capacity plan (agent).

Snowflake Account Usage and Databricks System Tables keep their existing summary packages (snowflake-usage, databricks-cost);
this module adds their savings agents and both tasks for BigQuery billing export, Fabric capacities and AWS CUR."""
import datetime as dt
import re
from collections import defaultdict

from products.core import (NativeError, agent_task, bearer, ident, journal, name, need_list, need_text, number, opt_int, plan_json,
                           receipt, required, rows_md, sql_source, write_csv)


def since(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def bq_billing(cfg, factory, out, days):
    table = required(cfg, 'billing_table')
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z0-9_]{1,1024}\.gcp_billing_export(_resource)?_v1_[A-Za-z0-9_]+', table):
        raise NativeError('billing_table proje.veri_kumesi.gcp_billing_export_v1_XXXX biçiminde olmalı.')
    src = sql_source('bigquery', cfg, factory, out)
    sql = ('SELECT service.description AS servis, sku.description AS kalem, project.id AS kapsam, CAST(DATE(usage_start_time) AS STRING) AS gun, '
           'ROUND(SUM(cost), 4) AS maliyet, ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 4) AS kredi, ANY_VALUE(currency) AS para_birimi '
           f'FROM `{table}` WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY) GROUP BY 1, 2, 3, 4')
    return src.query(sql, 'billing')[1]


def cur(cfg, factory, out, days):
    table = name(cfg, 'cur_table')
    src = sql_source('athena', cfg, factory, out)
    sql = ('SELECT line_item_product_code AS servis, line_item_usage_type AS kalem, line_item_usage_account_id AS kapsam, '
           'CAST(date(line_item_usage_start_date) AS varchar) AS gun, round(sum(line_item_unblended_cost), 4) AS maliyet, '
           "round(sum(CASE WHEN line_item_line_item_type = 'Credit' THEN line_item_unblended_cost ELSE 0 END), 4) AS kredi, max(line_item_currency_code) AS para_birimi "
           f"FROM {table} WHERE line_item_usage_start_date >= date_add('day', -{days}, current_timestamp) GROUP BY 1, 2, 3, 4")
    return src.query(sql, 'cur')[1]


def cur_resources(cfg, factory, out, days):
    src = sql_source('athena', cfg, factory, out)
    sql = ('SELECT line_item_product_code AS servis, line_item_resource_id AS kaynak, round(sum(line_item_unblended_cost), 2) AS maliyet '
           f"FROM {name(cfg, 'cur_table')} WHERE line_item_usage_start_date >= date_add('day', -{days}, current_timestamp) AND line_item_resource_id <> '' "
           'GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50')
    return src.query(sql, 'cur_resources')[1]


def azure_cost(cfg, factory, out, days):
    """Cost Management query for Fabric capacity resources, daily, per resource."""
    sub = required(cfg, 'subscription_id')
    client = bearer(factory, 'https://management.azure.com', 'AZURE_ACCESS_TOKEN')
    body = {'type': 'ActualCost', 'timeframe': 'Custom', 'timePeriod': {'from': since(days) + 'T00:00:00Z', 'to': dt.date.today().isoformat() + 'T23:59:59Z'},
            'dataset': {'granularity': 'Daily', 'aggregation': {'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                        'grouping': [{'type': 'Dimension', 'name': 'ResourceId'}, {'type': 'Dimension', 'name': 'MeterSubCategory'}],
                        'filter': {'dimensions': {'name': 'ResourceType', 'operator': 'In', 'values': ['microsoft.fabric/capacities']}}}}
    path = '/subscriptions/' + ident(sub) + '/providers/Microsoft.CostManagement/query?api-version=2023-11-01'
    rows = []
    for _ in range(50):
        r = client.json('POST', path, body)
        props = r.get('properties') or {}
        cols = [c.get('name') for c in props.get('columns', [])]
        for values in props.get('rows', []):
            x = dict(zip(cols, values))
            day = str(x.get('UsageDate', ''))
            rows.append({'servis': 'Microsoft Fabric', 'kalem': x.get('MeterSubCategory') or '', 'kapsam': str(x.get('ResourceId', '')).rsplit('/', 1)[-1],
                         'gun': f'{day[:4]}-{day[4:6]}-{day[6:8]}' if len(day) == 8 else day, 'maliyet': x.get('Cost'), 'kredi': 0, 'para_birimi': x.get('Currency', '')})
        nxt = props.get('nextLink')
        if not nxt:
            return rows
        path = nxt
    raise NativeError('Cost Management sayfalama sınırı aşıldı.')


def fabric_capacities(cfg, factory):
    client = bearer(factory, 'https://api.fabric.microsoft.com', 'FABRIC_ACCESS_TOKEN')
    return [{'kaynak': c.get('displayName'), 'sku': c.get('sku'), 'bolge': c.get('region'), 'durum': c.get('state')} for c in client.json('GET', '/v1/capacities').get('value', [])]


def cost_summary(rows, days):
    if not rows:
        raise NativeError('Seçilen dönemde maliyet satırı yok; tablo/abonelik ve gecikmeyi (billing verisi 24–48 saat gecikebilir) kontrol edin.')
    for r in rows:
        r['maliyet'] = number(r.get('maliyet')) or 0.0
        r['kredi'] = number(r.get('kredi')) or 0.0
    total = sum(r['maliyet'] for r in rows)
    by = lambda key: sorted(({'ad': k, 'maliyet': round(v, 2), 'pay': round(v / total, 4) if total else 0} for k, v in group(rows, key).items()), key=lambda x: -x['maliyet'])
    days_sorted = sorted({r['gun'] for r in rows})
    half = len(days_sorted) // 2
    first = sum(r['maliyet'] for r in rows if r['gun'] in days_sorted[:half])
    second = sum(r['maliyet'] for r in rows if r['gun'] in days_sorted[half:])
    currency = sorted({str(r.get('para_birimi') or '') for r in rows} - {''})
    return {'gun': days, 'toplam': round(total, 2), 'kredi': round(sum(r['kredi'] for r in rows), 2), 'para_birimi': currency,
            'servis': by('servis'), 'kalem': by('kalem')[:50], 'kapsam': by('kapsam')[:50],
            'trend': {'ilk_yari': round(first, 2), 'ikinci_yari': round(second, 2), 'degisim': round((second - first) / first, 4) if first else None},
            'gunluk': [{'gun': d, 'maliyet': round(sum(r['maliyet'] for r in rows if r['gun'] == d), 2)} for d in days_sorted]}


def group(rows, key):
    out = defaultdict(float)
    for r in rows:
        out[str(r.get(key) or '(boş)')] += r['maliyet']
    return out


READERS = {'bigquery-billing': ('BigQuery Billing Export', bq_billing), 'aws-cur': ('AWS Cost and Usage Report', cur),
           'fabric-capacity': ('Fabric Capacity Metrics', azure_cost)}


def cost_report(cfg, out, factory):
    key = cfg['operation'].rsplit('-cost-summary', 1)[0]
    label, reader = READERS[key]
    days = opt_int(cfg, 'days', 30, 1, 400)
    rows = reader(cfg, factory, out, days)
    summary = cost_summary(rows, days)
    write_csv(out, 'maliyet_detay.csv', rows, ['gun', 'servis', 'kalem', 'kapsam', 'maliyet', 'kredi', 'para_birimi'])
    write_csv(out, 'servis_ozeti.csv', summary['servis'], ['ad', 'maliyet', 'pay'])
    write_csv(out, 'en_pahali_kalemler.csv', summary['kalem'], ['ad', 'maliyet', 'pay'])
    write_csv(out, 'gunluk_maliyet.csv', summary['gunluk'], ['gun', 'maliyet'])
    if key == 'fabric-capacity':
        write_csv(out, 'kapasiteler.csv', fabric_capacities(cfg, factory), ['kaynak', 'sku', 'bolge', 'durum'])
    journal(out, state='verified')
    return receipt(out, 'ozet.json', {'urun': label, **{k: v for k, v in summary.items() if k not in ('kalem', 'kapsam', 'gunluk')},
                                      'not': 'Liste/gerçekleşen maliyet platformun faturalama verisidir; indirim ve vergi farkları sözleşmenize göre değişebilir.'})


# ---------- Savings and capacity agent ----------
def evidence_bigquery(cfg, factory, out, days):
    return {'maliyet': cost_summary(bq_billing(cfg, factory, out, days), days)}


def evidence_cur(cfg, factory, out, days):
    return {'maliyet': cost_summary(cur(cfg, factory, out, days), days), 'en_pahali_kaynaklar': cur_resources(cfg, factory, out, days)}


def evidence_fabric(cfg, factory, out, days):
    return {'maliyet': cost_summary(azure_cost(cfg, factory, out, days), days), 'kapasiteler': fabric_capacities(cfg, factory)}


def evidence_snowflake(cfg, factory, out, days):
    src = sql_source('snowflake', cfg, factory, out)
    credits = src.query('SELECT warehouse_name AS kaynak, ROUND(SUM(credits_used), 2) AS kredi, ROUND(SUM(credits_used_cloud_services), 2) AS bulut_servis_kredi '
                        f'FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY WHERE start_time >= DATEADD(day, -{days}, CURRENT_TIMESTAMP()) GROUP BY 1 ORDER BY 2 DESC', 'credits')[1]
    idle = src.query('SELECT warehouse_name AS kaynak, COUNT(DISTINCT query_id) AS sorgu, ROUND(AVG(execution_time) / 1000, 1) AS ort_sure_sn, '
                     'ROUND(SUM(bytes_spilled_to_remote_storage) / POWER(1024, 3), 2) AS uzak_tasma_gb '
                     f'FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY WHERE start_time >= DATEADD(day, -{days}, CURRENT_TIMESTAMP()) AND warehouse_name IS NOT NULL GROUP BY 1', 'queries')[1]
    _, warehouses = src.query('SHOW WAREHOUSES', 'warehouses')
    sizes = [{'kaynak': w.get('name'), 'boyut': w.get('size'), 'otomatik_durdurma_sn': w.get('auto_suspend'), 'min_kume': w.get('min_cluster_count'),
              'max_kume': w.get('max_cluster_count'), 'tur': w.get('type')} for w in warehouses]
    for r in credits:
        r['maliyet'] = number(r.get('kredi')) or 0
    return {'birim': 'Snowflake kredisi', 'depo_kredileri': credits, 'depo_is_yuku': idle, 'depolar': sizes}


def evidence_databricks(cfg, factory, out, days):
    src = sql_source('databricks', cfg, factory, out)
    usage = src.query('SELECT u.sku_name AS kaynak, u.billing_origin_product AS urun, ROUND(SUM(u.usage_quantity), 2) AS dbu, '
                      'ROUND(SUM(u.usage_quantity * p.pricing.default), 2) AS maliyet FROM system.billing.usage u LEFT JOIN system.billing.list_prices p '
                      'ON u.sku_name = p.sku_name AND u.cloud = p.cloud AND u.usage_unit = p.usage_unit AND u.usage_end_time >= p.price_start_time '
                      'AND (p.price_end_time IS NULL OR u.usage_end_time < p.price_end_time) '
                      f'WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), {days}) GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 60', 'usage')[1]
    warehouses = [{'kaynak': w.get('name'), 'boyut': w.get('cluster_size'), 'otomatik_durdurma_dk': w.get('auto_stop_mins'), 'min_kume': w.get('min_num_clusters'),
                   'max_kume': w.get('max_num_clusters'), 'serverless': w.get('enable_serverless_compute')}
                  for w in src.client.json('GET', '/api/2.0/sql/warehouses').get('warehouses', [])]
    return {'birim': 'liste fiyatı (USD)', 'sku_maliyetleri': usage, 'sql_warehouselar': warehouses}


EVIDENCE = {'bigquery-billing': ('BigQuery Billing Export', evidence_bigquery), 'aws-cur': ('AWS Cost and Usage Report', evidence_cur),
            'fabric-capacity': ('Fabric Capacity Metrics', evidence_fabric), 'snowflake-usage': ('Snowflake Account Usage', evidence_snowflake),
            'databricks-system-tables': ('Databricks System Tables', evidence_databricks)}
KINDS = {'boyut_kucult', 'otomatik_durdur', 'rezervasyon_taahhut', 'saklama_yasam_dongusu', 'sorgu_optimizasyonu', 'kapat_birlestir', 'zamanlama', 'diger'}
SCHEMA = ('{"aksiyonlar":[{"hedef":"kanıttaki kaynak/servis/kalem adı","tur":"boyut_kucult|otomatik_durdur|rezervasyon_taahhut|saklama_yasam_dongusu|'
          'sorgu_optimizasyonu|kapat_birlestir|zamanlama|diger","aksiyon":"somut adım","tahmini_tasarruf_orani":0.15,"kanit":"dayandığı rakam",'
          '"risk":"performans/SLA etkisi","oncelik":"yuksek|orta|dusuk"}],"kapasite":[{"hedef":"...","oneri":"...","gerekce":"..."}],"sorular":["..."]}')


def cost_names(evidence):
    names_, costs = {}, {}
    def walk(v):
        if isinstance(v, dict):
            for k in ('ad', 'kaynak'):
                if isinstance(v.get(k), str) and v[k]:
                    names_[v[k].lower()] = v[k]
                    if number(v.get('maliyet')) is not None:
                        costs[v[k].lower()] = max(costs.get(v[k].lower(), 0), number(v['maliyet']))
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(evidence)
    return names_, costs


def savings(cfg, out, factory):
    key = cfg['operation'].rsplit('-savings-plan', 1)[0]
    label, collect = EVIDENCE[key]
    days = opt_int(cfg, 'days', 30, 7, 400)
    evidence = collect(cfg, factory, out, days)
    allowed, costs = cost_names(evidence)

    def validate(obj):
        for i, a in enumerate(need_list(obj, 'aksiyonlar', 'aksiyonlar', maximum=40), 1):
            target = need_text(a, 'hedef', f'aksiyon {i}')
            if target.lower() not in allowed:
                raise ValueError(f'aksiyon {i}: hedef kanıtta yok: {target}')
            need_text(a, 'tur', f'aksiyon {i}', choices=KINDS)
            need_text(a, 'aksiyon', f'aksiyon {i}')
            need_text(a, 'oncelik', f'aksiyon {i}', choices={'yuksek', 'orta', 'dusuk'})
            rate = a.get('tahmini_tasarruf_orani')
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 0.9:
                raise ValueError(f'aksiyon {i}: tahmini_tasarruf_orani 0–0.9 arası sayı olmalı.')
        for c in obj.get('kapasite', []) or []:
            if not isinstance(c, dict) or str(c.get('hedef', '')).lower() not in allowed:
                raise ValueError('kapasite önerisi kanıtta olmayan hedefi gösteriyor: ' + str(c.get('hedef') if isinstance(c, dict) else c))
        return obj

    def render(result):
        actions = []
        for a in result['aksiyonlar']:
            base = costs.get(a['hedef'].lower())
            actions.append({**a, 'donem_maliyeti': round(base, 2) if base is not None else '',
                            'tahmini_tasarruf': round(base * a['tahmini_tasarruf_orani'], 2) if base is not None else 'maliyet kanıtı yok'})
        actions.sort(key=lambda a: -(a['tahmini_tasarruf'] if isinstance(a['tahmini_tasarruf'], float) else 0))
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['oncelik', 'hedef', 'tur', 'aksiyon', 'donem_maliyeti', 'tahmini_tasarruf_orani', 'tahmini_tasarruf', 'kanit', 'risk'],
                           extrasaction='ignore', lineterminator='\n')
        w.writeheader(); w.writerows(actions)
        return {'tasarruf_plani.json': plan_json({**result, 'aksiyonlar': actions}), 'tasarruf_plani.csv': buf.getvalue(),
                'tasarruf_plani.md': rows_md(f'Maliyet azaltma ve kapasite planı — {label} (son {days} gün)',
                                             [('Aksiyonlar', actions, ['oncelik', 'hedef', 'tur', 'aksiyon', 'donem_maliyeti', 'tahmini_tasarruf', 'risk']),
                                              ('Kapasite', result.get('kapasite') or [], ['hedef', 'oneri', 'gerekce']),
                                              ('Sorular', [{'soru': q} for q in result.get('sorular', [])], ['soru'])])}
    return agent_task(cfg, out, product=label, title='Maliyet azaltma ve kapasite planı', evidence=evidence, schema=SCHEMA, validate=validate, render=render,
                      rules='Tasarruf tutarını sen hesaplama; yalnızca oran ver, tutarı kod kanıttaki dönem maliyetinden hesaplar. '
                            'Taahhüt/rezervasyon önerilerinde kullanımın istikrarlı olduğunu kanıttaki günlük seriden göster; tasarruf garantisi verme.')


HANDLERS = {k + '-cost-summary': cost_report for k in READERS}
HANDLERS.update({k + '-savings-plan': savings for k in EVIDENCE})
