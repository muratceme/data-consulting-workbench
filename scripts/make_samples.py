"""Regenerate the fictional sample files that ship inside code-block packages (binary/zip formats).
CSV, JSON, SQL and Qlik script samples are plain files in library/samples and are edited by hand."""
import json, zipfile
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1] / 'library' / 'samples'
FIXED = (2026, 10, 1, 0, 0, 0)


def write_zip(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, FIXED)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def m_source(table):
    return ['let', f'    Kaynak = Sql.Database("sql.ornek.local", "SatisDB"),',
            f'    Veri = Kaynak{{[Schema="dbo",Item="{table}"]}}[Data]', 'in', '    Veri']


def pbit():
    schema = {
        'name': 'SatisModeli', 'compatibilityLevel': 1567,
        'model': {
            'culture': 'tr-TR',
            'tables': [
                {'name': 'Satislar', 'columns': [
                    {'name': 'SiparisNo', 'dataType': 'string', 'sourceColumn': 'SiparisNo'},
                    {'name': 'Tarih', 'dataType': 'dateTime', 'sourceColumn': 'Tarih'},
                    {'name': 'MusteriNo', 'dataType': 'string', 'sourceColumn': 'MusteriNo'},
                    {'name': 'UrunKodu', 'dataType': 'string', 'sourceColumn': 'UrunKodu'},
                    {'name': 'Adet', 'dataType': 'int64', 'sourceColumn': 'Adet'},
                    {'name': 'Tutar', 'dataType': 'decimal', 'sourceColumn': 'Tutar'},
                    {'name': 'Maliyet', 'dataType': 'decimal', 'sourceColumn': 'Maliyet'},
                    {'name': 'KarMarji', 'dataType': 'double', 'type': 'calculated', 'expression': 'DIVIDE(Satislar[Tutar] - Satislar[Maliyet], Satislar[Tutar])'}],
                 'measures': [
                    {'name': 'Toplam Satış', 'expression': 'SUM ( Satislar[Tutar] )', 'formatString': '#,0 ₺', 'description': 'Seçili dönemdeki brüt satış tutarı.'},
                    {'name': 'Toplam Maliyet', 'expression': 'SUM ( Satislar[Maliyet] )', 'formatString': '#,0 ₺'},
                    {'name': 'Brüt Kâr', 'expression': '[Toplam Satış] - [Toplam Maliyet]', 'formatString': '#,0 ₺', 'description': 'Satış eksi maliyet.'},
                    {'name': 'Geçen Yıl Satış', 'expression': 'CALCULATE ( [Toplam Satış], SAMEPERIODLASTYEAR ( Takvim[Tarih] ) )', 'formatString': '#,0 ₺'},
                    {'name': 'Büyük Siparişler', 'expression': 'CALCULATE ( [Toplam Satış], FILTER ( Satislar, Satislar[Tutar] > 50000 ) )'},
                    {'name': 'İade Oranı', 'expression': 'DIVIDE ( [Toplam İade], [Toplam Satış] )', 'formatString': '0.0%'}],
                 'partitions': [{'name': 'Satislar', 'source': {'type': 'm', 'expression': m_source('Satislar')}}]},
                {'name': 'Musteriler', 'columns': [
                    {'name': 'MusteriNo', 'dataType': 'string'}, {'name': 'MusteriAdi', 'dataType': 'string'},
                    {'name': 'Sehir', 'dataType': 'string'}, {'name': 'Segment', 'dataType': 'string'}],
                 'partitions': [{'name': 'Musteriler', 'source': {'type': 'm', 'expression': m_source('Musteriler')}}]},
                {'name': 'Urunler', 'columns': [
                    {'name': 'UrunKodu', 'dataType': 'string'}, {'name': 'UrunAdi', 'dataType': 'string'}, {'name': 'Kategori', 'dataType': 'string'}],
                 'partitions': [{'name': 'Urunler', 'source': {'type': 'm', 'expression': ['let', '    Kaynak = Excel.Workbook(File.Contents("C:\\\\Veri\\\\urunler.xlsx"), null, true),', '    Sayfa = Kaynak{[Item="Urunler",Kind="Sheet"]}[Data]', 'in', '    Sayfa']}}]},
                {'name': 'Takvim', 'columns': [
                    {'name': 'Tarih', 'dataType': 'dateTime'}, {'name': 'Yil', 'dataType': 'int64', 'type': 'calculated', 'expression': 'YEAR(Takvim[Tarih])'},
                    {'name': 'Ay', 'dataType': 'string', 'type': 'calculated', 'expression': 'FORMAT(Takvim[Tarih], "MMMM")'}],
                 'partitions': [{'name': 'Takvim', 'source': {'type': 'calculated', 'expression': 'CALENDAR ( DATE ( 2024, 1, 1 ), DATE ( 2026, 12, 31 ) )'}}]},
                {'name': 'Hedefler', 'columns': [{'name': 'Yil', 'dataType': 'int64'}, {'name': 'Hedef', 'dataType': 'decimal'}],
                 'partitions': [{'name': 'Hedefler', 'source': {'type': 'm', 'expression': m_source('Hedefler')}}]},
                {'name': 'LocalDateTable_6f1a', 'isHidden': True, 'columns': [{'name': 'Date', 'dataType': 'dateTime'}],
                 'partitions': [{'name': 'LocalDateTable_6f1a', 'source': {'type': 'calculated', 'expression': 'Calendar(Date(2024,1,1), Date(2026,12,31))'}}]},
            ],
            'relationships': [
                {'name': 'r1', 'fromTable': 'Satislar', 'fromColumn': 'MusteriNo', 'toTable': 'Musteriler', 'toColumn': 'MusteriNo'},
                {'name': 'r2', 'fromTable': 'Satislar', 'fromColumn': 'UrunKodu', 'toTable': 'Urunler', 'toColumn': 'UrunKodu', 'crossFilteringBehavior': 'bothDirections'},
                {'name': 'r3', 'fromTable': 'Satislar', 'fromColumn': 'Tarih', 'toTable': 'Takvim', 'toColumn': 'Tarih'},
                {'name': 'r4', 'fromTable': 'Takvim', 'fromColumn': 'Tarih', 'toTable': 'LocalDateTable_6f1a', 'toColumn': 'Date', 'joinOnDateBehavior': 'datePartOnly'},
            ],
        },
    }
    visual = lambda kind: {'config': json.dumps({'singleVisual': {'visualType': kind}})}
    layout = {'sections': [
        {'displayName': 'Özet', 'visualContainers': [visual('card'), visual('card'), visual('lineChart'), visual('slicer')]},
        {'displayName': 'Müşteri', 'visualContainers': [visual('tableEx'), visual('clusteredBarChart')]},
    ]}
    write_zip(SAMPLES / 'satis-modeli.pbit', {
        'Version': '1.28'.encode('utf-16-le'),
        'DataModelSchema': json.dumps(schema, ensure_ascii=False).encode('utf-16-le'),
        'Report/Layout': json.dumps(layout, ensure_ascii=False).encode('utf-16-le'),
        '[Content_Types].xml': b'<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    })


TWB = '''<?xml version='1.0' encoding='utf-8' ?>
<workbook source-build='2024.2.0' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <datasources>
    <datasource caption='Parameters' hasconnection='false' inline='true' name='Parameters'>
      <column caption='Hedef Büyüme' datatype='real' name='[Parameter 1]' param-domain-type='range' role='measure' type='quantitative' value='0.15'>
        <calculation class='tableau' formula='0.15' />
      </column>
    </datasource>
    <datasource caption='Satışlar (SatisDB)' inline='true' name='federated.0abc123'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='sql.ornek.local' name='sqlserver.01'>
            <connection authentication='sspi' class='sqlserver' dbname='SatisDB' server='sql.ornek.local' />
          </named-connection>
        </named-connections>
        <relation connection='sqlserver.01' name='Satislar' table='[dbo].[Satislar]' type='table' />
      </connection>
      <column datatype='string' name='[SiparisNo]' role='dimension' type='nominal' />
      <column datatype='real' name='[Tutar]' role='measure' type='quantitative' />
      <column datatype='real' name='[Maliyet]' role='measure' type='quantitative' />
      <column caption='Kâr' datatype='real' name='[Calculation_101]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='SUM([Tutar]) - SUM([Maliyet])' />
      </column>
      <column caption='Kâr Marjı' datatype='real' name='[Calculation_102]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='[Calculation_101] / SUM([Tutar])' />
      </column>
      <column caption='Hedefe Uzaklık' datatype='real' name='[Calculation_103]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='SUM([Tutar]) * (1 + [Parameters].[Parameter 1]) - SUM([Tutar])' />
      </column>
      <column caption='Müşteri İlk Sipariş' datatype='date' name='[Calculation_104]' role='dimension' type='ordinal'>
        <calculation class='tableau' formula='{ FIXED [MusteriNo] : MIN([Tarih]) }' />
      </column>
    </datasource>
    <datasource caption='Hedefler.xlsx' inline='true' name='excel-direct.1'>
      <connection class='excel-direct' filename='C:/Veri/Hedefler.xlsx' />
      <column datatype='real' name='[Hedef]' role='measure' type='quantitative' />
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Aylık Satış'><table><view><datasource-dependencies datasource='federated.0abc123'><column-instance column='[Tutar]' name='[sum:Tutar:qk]' /></datasource-dependencies></view></table></worksheet>
    <worksheet name='Kârlılık'><table><view><datasource-dependencies datasource='federated.0abc123'><column-instance column='[Calculation_102]' name='[usr:Calculation_102:qk]' /></datasource-dependencies></view></table></worksheet>
    <worksheet name='Hedef Takibi'><table><view><datasource-dependencies datasource='excel-direct.1'><column-instance column='[Hedef]' name='[sum:Hedef:qk]' /></datasource-dependencies></view></table></worksheet>
  </worksheets>
  <dashboards>
    <dashboard name='Yönetim Özeti'><zones><zone name='Aylık Satış' /><zone name='Kârlılık' /></zones></dashboard>
  </dashboards>
</workbook>
'''


def twbx():
    write_zip(SAMPLES / 'satis-panosu.twbx', {'satis-panosu.twb': TWB.encode('utf-8'), 'Data/Hedefler.xlsx': b''})


def manifest():
    """Small dbt 1.8-style manifest for a fictional retail project."""
    def model(name, deps, desc='', columns=None, mat='view', code=''):
        return {'resource_type': 'model', 'name': name, 'unique_id': f'model.perakende.{name}', 'description': desc,
                'columns': {c: {'name': c, 'description': d} for c, d in (columns or {}).items()},
                'config': {'materialized': mat}, 'depends_on': {'nodes': deps}, 'raw_code': code or f'select * from {{{{ ref("{name}") }}}}'}

    def test(kind, model_name, column):
        return {'resource_type': 'test', 'name': f'{kind}_{model_name}_{column}', 'attached_node': f'model.perakende.{model_name}',
                'column_name': column, 'test_metadata': {'name': kind, 'kwargs': {'column_name': column}},
                'depends_on': {'nodes': [f'model.perakende.{model_name}']}}
    src = lambda t: f'source.perakende.erp.{t}'
    m = 'model.perakende.'
    nodes = {}
    for node in [
        model('stg_siparisler', [src('siparisler')], 'ERP siparişleri; tipler ve adlar standartlaştırılmış.', {'siparis_id': 'Sipariş anahtarı', 'musteri_id': 'Müşteri anahtarı', 'tutar': ''},
              code="select id as siparis_id, musteri_id, tutar from {{ source('erp', 'siparisler') }}"),
        model('stg_musteriler', [src('musteriler')], '', {'musteri_id': 'Müşteri anahtarı'}, code="select * from {{ source('erp', 'musteriler') }}"),
        model('stg_urunler', [src('urunler'), src('kategoriler')], 'Ürün ve kategori.', code="select * from {{ source('erp','urunler') }} u join {{ source('erp','kategoriler') }} k on u.kategori_id = k.id"),
        model('int_siparis_kalemleri', [m + 'stg_siparisler', m + 'stg_urunler'], 'Sipariş kalemleri ürünle zenginleştirilmiş.', mat='ephemeral',
              code="select * from {{ ref('stg_siparisler') }} s join analytics.kur_tablosu k on s.para_birimi = k.para_birimi"),
        model('fct_satislar', [m + 'int_siparis_kalemleri', src('iadeler')], 'Günlük satış olgu tablosu.', {'siparis_id': 'Sipariş', 'tutar': 'TL tutar'}, 'incremental',
              code="select * from {{ ref('int_siparis_kalemleri') }} union all select * from {{ source('erp','iadeler') }}"),
        model('dim_musteri', [m + 'stg_musteriler'], 'Müşteri boyutu.', {'musteri_id': 'Anahtar', 'segment': ''}, 'table'),
        model('stg_kampanyalar', [src('kampanyalar')], '', {}, code="select * from {{ source('erp','kampanyalar') }}"),
    ]:
        nodes[node['unique_id']] = node
    for t in [test('unique', 'stg_siparisler', 'siparis_id'), test('not_null', 'stg_siparisler', 'siparis_id'),
              test('not_null', 'fct_satislar', 'siparis_id'), test('unique', 'dim_musteri', 'musteri_id'), test('not_null', 'dim_musteri', 'musteri_id'),
              test('relationships', 'fct_satislar', 'musteri_id')]:
        nodes['test.perakende.' + t['name']] = t
    sources = {}
    for name, fresh in [('siparisler', True), ('musteriler', False), ('urunler', False), ('kategoriler', False), ('iadeler', False), ('kampanyalar', False), ('eski_faturalar', False)]:
        sources[src(name)] = {'resource_type': 'source', 'source_name': 'erp', 'name': name, 'description': 'ERP tablosu' if fresh else '',
                              'loaded_at_field': '_yuklenme_zamani' if fresh else None,
                              'freshness': {'warn_after': {'count': 12 if fresh else None, 'period': 'hour'}, 'error_after': {'count': 24 if fresh else None, 'period': 'hour'}}}
    data = {'metadata': {'dbt_version': '1.8.7', 'project_name': 'perakende'}, 'nodes': nodes, 'sources': sources,
            'exposures': {'exposure.perakende.yonetim_panosu': {'name': 'yonetim_panosu', 'depends_on': {'nodes': [m + 'fct_satislar', m + 'dim_musteri']}}}}
    (SAMPLES / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    pbit()
    twbx()
    manifest()
    print('Samples written to', SAMPLES)
