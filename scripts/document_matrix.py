"""Regenerate the committed product matrix (English and Turkish) from the built catalog without secrets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The catalog text is Turkish; the English matrix translates the shared labels.
EN = {
    'Coğrafi & IoT Verisi': 'Geospatial & IoT Data', 'Data FinOps & Performans': 'Data FinOps & Performance', 'Dosya & Self-Service Veri': 'Files & Self-Service Data',
    'ERP & İş Uygulamaları': 'ERP & Business Applications', 'GenAI & Vektör Veri': 'GenAI & Vector Data', 'Gizlilik & Veri Güvenliği': 'Privacy & Data Security',
    'Kurumsal Planlama & EPM': 'Enterprise Planning & EPM', 'Orkestrasyon & DataOps': 'Orchestration & DataOps', 'Streaming & Gerçek Zaman': 'Streaming & Real Time',
    'Veri Ambarı': 'Data Warehouse', 'Veri Bilimi & İstatistik': 'Data Science & Statistics', 'Veri Entegrasyonu': 'Data Integration',
    'Veri Gözlemlenebilirliği': 'Data Observability', 'Veri Kalitesi': 'Data Quality', 'Veri Migrasyonu': 'Data Migration', 'Veritabanı Yönetimi': 'Database Management',
    'Yönetişim & Veri Kataloğu': 'Governance & Data Catalog', 'Ürün & Pazarlama Analitiği': 'Product & Marketing Analytics', 'Genel': 'General',
    'Analitik ürünün query/admin API': 'Analytics product query/admin API', 'Platform metrik/segment/event tanımı': 'Platform metric/segment/event definition',
    'Broker yönetim API + istemci SDK': 'Broker admin API + client SDK', 'Topic, schema veya stream job tanımı': 'Topic, schema or stream job definition',
    'Coğrafi servis API / GIS SDK': 'Geospatial service API / GIS SDK', 'Layer, geoprocessing job veya GIS projesi': 'Layer, geoprocessing job or GIS project',
    'ETL yönetim API/SDK': 'ETL management API/SDK', 'Platformun pipeline tanımı ve çalışma kaydı': 'Platform pipeline definition and run record',
    'Kalite ürünü API / SDK': 'Quality product API / SDK', 'Ürüne özgü expectation/check tanımı': 'Product-specific expectation/check definition',
    'Katalog metadata REST/GraphQL API': 'Catalog metadata REST/GraphQL API', 'Platformda terim, owner veya lineage kaydı': 'Term, owner or lineage record on the platform',
    'MDM API / vendor SDK': 'MDM API / vendor SDK', 'Eşleme ve survivorship kuralı/taslağı': 'Matching and survivorship rule/draft',
    'ML yönetim API / framework SDK': 'ML management API / framework SDK', 'Training pipeline, model veya deployment tanımı': 'Training pipeline, model or deployment definition',
    'Migrasyon yönetim API / CLI': 'Migration management API / CLI', 'Platform migration task ve doğrulama kaydı': 'Platform migration task and validation record',
    'Model/vektör API veya framework SDK': 'Model/vector API or framework SDK', 'RAG iş akışı, değerlendirme seti veya index': 'RAG workflow, evaluation set or index',
    'Monitör/olay API': 'Monitor/incident API', 'Platform monitor ve alarm tanımı': 'Platform monitor and alert definition',
    'Office/Sheets API veya yerel kütüphane SDK': 'Office/Sheets API or local library SDK', 'XLSX, Power Query M veya platform tablosu': 'XLSX, Power Query M or platform table',
    'Orkestratör REST/GraphQL/SDK': 'Orchestrator REST/GraphQL/SDK', 'DAG, flow veya job tanımı': 'DAG, flow or job definition',
    'Planlama API / vendor SDK': 'Planning API / vendor SDK', 'Platform senaryo/model/veri yükleme işi': 'Platform scenario/model/data load job',
    'Platform billing/query API': 'Platform billing/query API', 'Platform maliyet sorgusu veya monitor': 'Platform cost query or monitor',
    'Rapor/uygulama API + görsel model SDK': 'Report/app API + visual model SDK', 'Platformun rapor/proje dosyası veya yayınlanmış rapor': 'Platform report/project file or published report',
    'Resmi DB sürücüsü / yönetim API': 'Official DB driver / admin API', 'SQL/DDL veya veritabanı nesnesi': 'SQL/DDL or database object',
    'Resmi dil/kütüphane SDK': 'Official language/library SDK', 'Notebook veya analiz projesi': 'Notebook or analysis project',
    'Sorgu işi API / SQL sürücüsü': 'Query job API / SQL driver', 'Platform tablosu, view veya SQL modeli': 'Platform table, view or SQL model',
    'Sınıflandırma/politika API': 'Classification/policy API', 'Platformda erişim/maskeleme politikası taslağı': 'Access/masking policy draft on the platform',
    'Workspace/catalog API + SQL/Spark SDK': 'Workspace/catalog API + SQL/Spark SDK', 'Notebook, job veya native tablo': 'Notebook, job or native table',
    'Yerel dosya': 'Local file', 'Rapor / kod taslağı': 'Report / code draft',
    'Ürüne özgü OData/REST/SOAP veya on-prem SDK': 'Product-specific OData/REST/SOAP or on-prem SDK', 'ERP içindeki taslak belge, eşleme veya rapor tanımı': 'Draft document, mapping or report definition in the ERP',
    'Ürünün proje/derleme/iş API’si': 'Product project/compile/job API', 'dbt/Dataform/SQLMesh proje dosyası': 'dbt/Dataform/SQLMesh project file',
    'Ana veri temizliği': 'Master data cleansing', 'Boyutsal model (SQL)': 'Dimensional model (SQL)', 'Bütçe ve tahmin dosyaları': 'Budget and forecast files',
    'Doküman ve RAG verisi': 'Documents and RAG data', 'Dosya biçimi dönüştürme': 'File format conversion', 'Erişim ve sahiplik listeleri': 'Access and ownership lists',
    'Geçiş eşleme dosyaları': 'Migration mapping files', 'Hassas veri envanteri': 'Sensitive data inventory', 'Konum ve sensör dosyaları': 'Location and sensor files',
    'Maliyet dışa aktarımları': 'Cost exports', 'Model değerlendirme dosyaları': 'Model evaluation files', 'Muhasebe dışa aktarımları': 'Accounting exports',
    'Olay şemaları ve örnekleri': 'Event schemas and samples', 'Rapor gereksinimleri': 'Report requirements', 'SQL dosyaları': 'SQL files',
    'Satış ve müşteri analitiği': 'Sales and customer analytics', 'Tablo yükleme metrikleri': 'Table load metrics', 'Veri doğrulama kuralları': 'Data validation rules',
    'Zamanlama ve iş akışı dosyaları': 'Schedule and workflow files', 'İstatistiksel analiz': 'Statistical analysis',
}

TEXT = {
    'tr': dict(title='Tüm alan ve ürünlerin entegrasyon matrisi', link='[English](INTEGRATION-MATRIX.md) · **Türkçe**',
               intro='{n} ürün bağlamı. `Kısmi` yalnızca listelenen işlemlerin kodlandığını ve yerel sözleşme testlerinden geçtiğini gösterir. '
                     'Hiçbirinde canlı hesap/uygulamada açılış doğrulaması yapılmadı. API/SDK yolu ve çıktı sütunları, uygulanmamış ürünlerde tasarım hedefidir; doğrulanmış vendor desteği iddiası değildir.',
               header='| Alan | Yazılım | Ürün | Durum / uygulanmış işlemler | Planlanan bağlantı | Çıktı hedefi |', partial='Kısmi: ', planned='Planlandı; paket yok'),
    'en': dict(title='Integration matrix of all domains and products', link='**English** · [Türkçe](INTEGRATION-MATRIX.tr.md)',
               intro='{n} product contexts. `Partial` only means the listed operations are implemented and passed the local contract tests. '
                     'None were verified in a live account or by opening the output in the application. For unimplemented products the API/SDK path and output columns are design targets, not a claim of verified vendor support.',
               header='| Domain | Software | Product | Status / implemented operations | Planned connection | Output target |', partial='Partial: ', planned='Planned; no package'),
}


def render(rows, lang):
    t = TEXT[lang]
    tr = (lambda v: EN.get(v, v)) if lang == 'en' else (lambda v: v)
    text = f"# {t['title']}\n\n{t['link']}\n\n{t['intro'].format(n=len(rows))}\n\n{t['header']}\n|---|---|---|---|---|---|\n"
    for row in rows:
        status = (t['partial'] + ', '.join(row['implemented_operations'])) if row['implemented_operations'] else t['planned']
        values = [tr(row['domain']), tr(row['tool']), tr(row['product']), status, tr(row['interface_target']), tr(row['output_target'])]
        text += '| ' + ' | '.join(value.replace('|', ' / ').replace('\n', ' ') for value in values) + ' |\n'
    return text


def main():
    rows = json.loads((ROOT/'dist/integration-plan.json').read_text(encoding='utf-8'))
    (ROOT/'docs/INTEGRATION-MATRIX.md').write_text(render(rows, 'en'), encoding='utf-8', newline='\n')
    (ROOT/'docs/INTEGRATION-MATRIX.tr.md').write_text(render(rows, 'tr'), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
