# Tüm alan ve ürünlerin entegrasyon matrisi

[English](INTEGRATION-MATRIX.md) · **Türkçe**

207 ürün bağlamı. `Kısmi` yalnızca listelenen işlemlerin kodlandığını ve yerel sözleşme testlerinden geçtiğini gösterir. Hiçbirinde canlı hesap/uygulamada açılış doğrulaması yapılmadı. API/SDK yolu ve çıktı sütunları, uygulanmamış ürünlerde tasarım hedefidir; doğrulanmış vendor desteği iddiası değildir.

| Alan | Yazılım | Ürün | Durum / uygulanmış işlemler | Planlanan bağlantı | Çıktı hedefi |
|---|---|---|---|---|---|
| Business Intelligence | Qlik | Qlik Cloud | Kısmi: qlik-export, qlik-template, qlik-cloud-build, qlik-reload-history, qlik-publish, qlik-reload-schedule, qlik-space-access | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Qlik | Qlik Sense Enterprise | Kısmi: qlik-soyagaci, qlik-powerbi, qlik-ifade-yazari, qlik-load-script, qlik-dax-ceviri, qlik-rapor-sablonu, qlik-zaman-zekasi, qlik-section-access, qlik-artimli-yukleme, qlik-cok-tablolu-uygulama | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Qlik | QlikView | Kısmi: qlik-veri-modeli | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Microsoft Power BI | Power BI Service | Kısmi: powerbi-refresh, powerbi-export, powerbi-monitor, fabric-semantic-model, powerbi-scan, powerbi-deploy, powerbi-refresh-schedule, powerbi-rls-members, powerbi-workspace-access, pbi-tarayici-envanteri | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Microsoft Power BI | Power BI Desktop | Kısmi: pbit-model, dax-yazari, tarih-tablosu, dax-zaman-zekasi, power-query-m, rls-rolleri, pbi-olcu-bagimliligi, pbi-rapor-sablonu, pbi-zaman-hesaplama-grubu, pbi-rls-tmdl, pbi-vertipaq, pbi-bpa, pbi-yildiz-model-projesi | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Tableau | Tableau Cloud | Kısmi: tableau-export, tableau-usage, tableau-permissions, tableau-extract-schedule | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Tableau | Tableau Server | Kısmi: tableau-export, tableau-usage, tableau-permissions | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Tableau | Tableau Desktop | Kısmi: tableau-envanter, tableau-hesap-yazari, tableau-dax-ceviri, tableau-rapor-sablonu, tableau-gecis-karmasikligi, tableau-cok-tablolu-calisma-kitabi | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Looker | Looker | Kısmi: looker-dashboard, lookml-envanter, lookml-rapor-sablonu, looker-kullanilmayan-alanlar, lookml-cok-tablolu-proje | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Looker | Looker Studio | Planlandı; paket yok | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | SAP | SAP BusinessObjects | Planlandı; paket yok | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | SAP | SAP Analytics Cloud | Planlandı; paket yok | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Oracle | Oracle Analytics Cloud | Planlandı; paket yok | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | MicroStrategy | MicroStrategy ONE | Planlandı; paket yok | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Apache Superset | Superset | Kısmi: superset-pano-paketi | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Metabase | Metabase | Kısmi: metabase-dashboard, metabase-pano-kurulumu | Rapor/uygulama API + görsel model SDK | Platformun rapor/proje dosyası veya yayınlanmış rapor |
| Business Intelligence | Genel | Rapor gereksinimleri | Kısmi: kpi-tanimlayici, donem-yorumu, rapor-tasarimcisi, grafik-secici, pano-kurucu, kpi-durum, excel-rapor-sablonu, html-rapor, pptx-rapor-sablonu, excel-pivot-rapor | Yerel dosya | Rapor / kod taslağı |
| ERP & İş Uygulamaları | SAP | S/4HANA | Kısmi: sap-odata-schema | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | SAP | ECC | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | SAP | Business One | Kısmi: sap-quotation-draft, sap-b1-schema | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Logo | Tiger 3 | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Logo | GO 3 | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Logo | Netsis | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Logo | j-Platform | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Oracle | Fusion Cloud ERP | Kısmi: oracle-schema | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Oracle | E-Business Suite | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Microsoft | Dynamics 365 Finance | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Microsoft | Business Central | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Odoo | Odoo | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | IFS | IFS Cloud | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Workday | Workday | Planlandı; paket yok | Ürüne özgü OData/REST/SOAP veya on-prem SDK | ERP içindeki taslak belge, eşleme veya rapor tanımı |
| ERP & İş Uygulamaları | Genel | Muhasebe dışa aktarımları | Kısmi: hesap-plani-esleyici, birim-kodu-eslestirici, tevkifat-kodu-onerici, tahsilat-eslestirici, masraf-politika-denetcisi, eirsaliye-uret, mizan-kontrol, mizan-tablolar, yevmiye-kontrol, cari-yaslandirma, tahsilat-eslestir, banka-mutabakat, mt940-csv, efatura-csv, kdv-kontrol, kur-cevir, benford-analizi, mukerrer-odeme, amortisman-plani, kredi-odeme-plani, stok-maliyeti, gider-siniflandir, nakit-akisi, efatura-uret | Yerel dosya | Rapor / kod taslağı |
| Veri Ambarı | Google | BigQuery | Kısmi: bigquery-table, bigquery-agent, bigquery-setup, bigquery-sema, bigquery-is-maliyeti, bigquery-tablo-tasarim, bigquery-terraform-kurulumu | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Snowflake | Snowflake | Kısmi: snowflake-quality, snowflake-setup, snowflake-grant-sync, boyutsal-model, ambar-boyutu-secici, ambar-kurucu, snowflake-sorgu-gecmisi, snowflake-depolama, kullanilmayan-tablolar, scd2-butunluk, clustering-derinligi, snowflake-rbac-denetimi, snowflake-hesap-kurulumu | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Microsoft | Fabric Warehouse | Planlandı; paket yok | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Microsoft | Azure Synapse | Planlandı; paket yok | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Amazon | Redshift | Kısmi: redshift-tablo-tasarim | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Oracle | Autonomous Data Warehouse | Planlandı; paket yok | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Teradata | Vantage | Kısmi: teradata-is-yuku | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | ClickHouse | ClickHouse | Kısmi: clickhouse-parca-analizi | Sorgu işi API / SQL sürücüsü | Platform tablosu, view veya SQL modeli |
| Veri Ambarı | Genel | Boyutsal model (SQL) | Kısmi: kolon-adi-standardi, scd-tipi-onerici, ozet-tablo-onerici, yildiz-sema | Yerel dosya | Rapor / kod taslağı |
| Data Lake & Lakehouse | Databricks | Databricks Lakehouse | Kısmi: databricks-monitor, databricks-run, databricks-profile, databricks-job-create, katman-atayici, medallion-katman-kurallari | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Databricks | Unity Catalog | Kısmi: unity-catalog-setup, uc-grant-sync, uc-gecis-hazirligi, unity-catalog-kurulumu | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Microsoft | Fabric Lakehouse | Kısmi: fabric-notebook-run, fabric-notebook-export, fabric-notebook, fabric-notebook-monitor, fabric-lakehouse-build | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Microsoft | OneLake | Planlandı; paket yok | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Amazon | S3 / Lake Formation | Planlandı; paket yok | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Google | Cloud Storage / BigLake | Planlandı; paket yok | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Apache | Iceberg | Kısmi: iceberg-metadata-ozet | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Apache | Hudi | Planlandı; paket yok | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Delta Lake | Delta Lake | Kısmi: bolumleme-danismani, delta-bakim-plani, zorder-secici, delta-log-ozet, nesne-dosya-boyutu, delta-vacuum-tahmini, bolum-yapisi-denetimi, tablo-bakim-plani | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Data Lake & Lakehouse | Dremio | Dremio | Planlandı; paket yok | Workspace/catalog API + SQL/Spark SDK | Notebook, job veya native tablo |
| Veri Entegrasyonu | Microsoft | Azure Data Factory | Kısmi: adf-deploy, adf-run, adf-monitor, adf-solution, adf-history, adf-pipeline-envanter, adf-metadata-pipeline | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Microsoft | SSIS | Kısmi: ssis-paket-envanter | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Microsoft | Fabric Data Factory | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Informatica | IDMC | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Informatica | PowerCenter | Kısmi: powercenter-envanter | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Qlik | Talend Cloud | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Qlik | Qlik Replicate | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Fivetran | Fivetran | Kısmi: fivetran-sync, fivetran-schema, fivetran-mar | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Airbyte | Airbyte | Kısmi: airbyte-monitor, airbyte-sync, airbyte-connection, toplu-cekim-planlayici, cdc-yontem-secici | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | SAP | Data Services | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Oracle | Data Integrator | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Oracle | GoldenGate | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Meltano | Meltano | Planlandı; paket yok | ETL yönetim API/SDK | Platformun pipeline tanımı ve çalışma kaydı |
| Veri Entegrasyonu | Genel | Dosya biçimi dönüştürme | Kısmi: json-esleme-yazari, birlestirme-anahtari, sabit-genislik-cozucu, json-csv, csv-json, jsonl-csv, xml-csv, sabit-genislik, excel-sayfalar, csv-xlsx, erisim-logu, csv-html-rapor | Yerel dosya | Rapor / kod taslağı |
| Analytics Engineering | dbt | dbt Core | Kısmi: dbt-denetim, dbt-model-yazari, dbt-test-onerici, incremental-strateji-onerici, kaynak-tazelik-onerici, dbt-test-siddeti, dbt-iskelet, dbt-calisma-sonuclari, dbt-dag-analizi, dbt-test-kapsami, dbt-kaynak-tazeligi, dbt-artimli-model-kontrol, dbt-artimli-model, dbt-snapshot-sablonu, dbt-modelleme-kurallari, dbt-proje-sablonu, dbt-yildiz-projesi | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Analytics Engineering | dbt | dbt Cloud | Kısmi: dbt-run, dbt-monitor, dbt-cloud-job | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Analytics Engineering | Google | Dataform | Kısmi: dataform-compile, dataform-run, dataform-monitor | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Analytics Engineering | SQLMesh | SQLMesh | Planlandı; paket yok | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Analytics Engineering | Coalesce | Coalesce | Planlandı; paket yok | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Analytics Engineering | Cube | Cube | Planlandı; paket yok | Ürünün proje/derleme/iş API’si | dbt/Dataform/SQLMesh proje dosyası |
| Orkestrasyon & DataOps | Apache | Airflow | Kısmi: airflow-run, airflow-monitor, airflow-activate, airflow-history, backfill-planlayici, sensor-ayarlayici, pipeline-kurucu, airflow-dag-denetim, airflow-havuz-analizi, airflow-dag-kurulumu | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Dagster | Dagster | Kısmi: dagster-schedule | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Prefect | Prefect | Kısmi: prefect-run, prefect-monitor, prefect-deployment | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Astronomer | Astro | Planlandı; paket yok | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Kestra | Kestra | Planlandı; paket yok | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | GitHub | Actions | Kısmi: github-actions-run, github-actions-denetim | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Jenkins | Jenkins | Planlandı; paket yok | Orkestratör REST/GraphQL/SDK | DAG, flow veya job tanımı |
| Orkestrasyon & DataOps | Genel | Zamanlama ve iş akışı dosyaları | Kısmi: zamanlama-planlayici, hata-siniflandirici, yeniden-deneme-politikasi, cron-aciklama, is-bagimlilik-analizi, zamanlama-yogunlugu, bakim-penceresi-kontrol, yeniden-deneme-analizi | Yerel dosya | Rapor / kod taslağı |
| Streaming & Gerçek Zaman | Apache | Kafka | Kısmi: kafka-konu-tasarimcisi, sema-uyumlulugu, kafka-tuketici-gecikmesi, kafka-konu-denetimi, cdc-olay-ozeti, mesaj-boyutu-analizi, anahtar-carpikligi, dlq-analizi, schema-registry-denetimi | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Apache | Flink | Kısmi: filigran-onerici, tekrar-penceresi-onerici | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Apache | Spark Streaming | Planlandı; paket yok | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Confluent | Confluent Cloud | Kısmi: confluent-topics | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Amazon | Kinesis | Kısmi: akis-kapasite | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Microsoft | Event Hubs | Planlandı; paket yok | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Google | Pub/Sub | Planlandı; paket yok | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Google | Dataflow | Planlandı; paket yok | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Redpanda | Redpanda | Planlandı; paket yok | Broker yönetim API + istemci SDK | Topic, schema veya stream job tanımı |
| Streaming & Gerçek Zaman | Genel | Olay şemaları ve örnekleri | Kısmi: avro-sema-tasarimci, olay-semasi-cikar, olay-gecikmesi, olay-tekrar-sira, pencere-toplam, outbox-mutabakati | Yerel dosya | Rapor / kod taslağı |
| Veritabanı Yönetimi | PostgreSQL | PostgreSQL | Kısmi: sql-optimizasyon, autovacuum-ayarlayici, baglanti-havuzu-boyutlandirici, zaman-asimi-onerici, postgres-explain-analizi, pg-stat-statements, pg-tablo-sisme, pg-kilit-agaci, pg-uzun-islemler, indeks-tekrari, pg-yapilandirma, veritabani-normalizasyon | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Microsoft | SQL Server | Kısmi: sqlserver-plan-analizi, sqlserver-bekleme-analizi, indeks-kullanim-analizi, sqlserver-deadlock, eksik-indeks-onerisi, yedek-kontrolu, sqlserver-yapilandirma | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Microsoft | Azure SQL | Planlandı; paket yok | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Oracle | Oracle Database | Kısmi: oracle-awr | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | MySQL | MySQL | Kısmi: mysql-yavas-log | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | MongoDB | Atlas | Kısmi: mongodb-profil-analizi, mongodb-indeks-kullanimi | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Redis | Redis | Kısmi: redis-saglik | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Couchbase | Capella | Planlandı; paket yok | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Amazon | DynamoDB | Planlandı; paket yok | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veritabanı Yönetimi | Neo4j | Neo4j | Planlandı; paket yok | Resmi DB sürücüsü / yönetim API | SQL/DDL veya veritabanı nesnesi |
| Veri Kalitesi | Great Expectations | GX | Kısmi: gx-soda-kurallari | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Soda | Soda Core | Planlandı; paket yok | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Soda | Soda Cloud | Kısmi: soda-scan | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | AWS | Deequ | Planlandı; paket yok | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Informatica | Cloud Data Quality | Planlandı; paket yok | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Qlik | Talend Data Quality | Planlandı; paket yok | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Ataccama | ONE | Planlandı; paket yok | Kalite ürünü API / SDK | Ürüne özgü expectation/check tanımı |
| Veri Kalitesi | Genel | Veri doğrulama kuralları | Kısmi: kalite-kok-neden, capraz-kural-yazari, gecerli-aralik-onerici, bos-deger-politikasi, tckn-dogrula, vkn-dogrula, iban-dogrula, telefon-normalize, eposta-dogrula, plaka-dogrula, il-normalize, posta-kodu-dogrula, kart-luhn, referans-butunlugu, anahtar-bul, bagimlilik-kontrol, desen-profili, kontrol-toplami, satir-kurallari | Yerel dosya | Rapor / kod taslağı |
| Yönetişim & Veri Kataloğu | Microsoft | Purview | Kısmi: purview-glossary, sozluk-ice-aktarim | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | Collibra | Data Intelligence Cloud | Planlandı; paket yok | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | Alation | Data Catalog | Planlandı; paket yok | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | Google | Dataplex | Planlandı; paket yok | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | Atlan | Atlan | Planlandı; paket yok | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | OpenMetadata | OpenMetadata | Kısmi: openmetadata-glossary | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | DataHub | DataHub | Kısmi: datahub-glossary | Katalog metadata REST/GraphQL API | Platformda terim, owner veya lineage kaydı |
| Yönetişim & Veri Kataloğu | Genel | Erişim ve sahiplik listeleri | Kısmi: veri-sozlesmesi-yazari, sema-degisiklik-etkisi, sozluk-kolon-eslestirici, veri-sahibi-onerici, rol-madenciligi, erisim-gozden-gecirme, veri-sahipligi-kapsami, is-sozlugu-kontrol, sql-soy-agaci, gorev-ayriligi, siniflandirma-tutarliligi, veri-sozlesmesi, erisim-talep-denetimi, rapor-kullanim-envanteri, siniflandirma-yayilimi, yetki-degisim-denetimi, istisna-kaydi-kontrol, kullanilmayan-yetkiler | Yerel dosya | Rapor / kod taslağı |
| Master Data Management | SAP | Master Data Governance | Planlandı; paket yok | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | Informatica | MDM SaaS | Kısmi: eslestirme-kalibrasyonu | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | Semarchy | xDM | Planlandı; paket yok | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | Reltio | Reltio | Planlandı; paket yok | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | TIBCO | EBX | Planlandı; paket yok | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | Stibo Systems | STEP | Planlandı; paket yok | MDM API / vendor SDK | Eşleme ve survivorship kuralı/taslağı |
| Master Data Management | Genel | Ana veri temizliği | Kısmi: eslestirme-hakemi, adres-cozumleyici, urun-kategori-eslestirici, kod-listesi-eslestirici, altin-kayit-kurali, benzer-kayit, unvan-normalize, adres-ayristir, golden-record, ad-soyad-ayir, hiyerarsi-kontrol, referans-esle, ana-veri-tamlik, paylasilan-kimlik, hiyerarsi-duzlestir, ana-veri-degisiklik, urun-ozellik-tutarlilik | Yerel dosya | Rapor / kod taslağı |
| Gizlilik & Veri Güvenliği | Microsoft | Purview Information Protection | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | OneTrust | Data Discovery | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | BigID | BigID | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | Immuta | Immuta | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | Privacera | Privacera | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | IBM | Guardium | Planlandı; paket yok | Sınıflandırma/politika API | Platformda erişim/maskeleme politikası taslağı |
| Gizlilik & Veri Güvenliği | Genel | Hassas veri envanteri | Kısmi: kvkk-envanteri, sentetik-veri-tasarimcisi, maskeleme-plani, k-anonimlik-genellestirici, silme-talebi-planlayici, kisisel-veri-envanteri, saklama-suresi-kontrol, kvkk-basvuru-sure, k-anonimlik, genellestir, iys-izin-kontrol, yurt-disi-aktarim, ihlal-bildirim-suresi, cerez-envanteri, ilgili-kisi-arama, test-ortami-gercek-veri, maskeleme-dogrulama | Yerel dosya | Rapor / kod taslağı |
| Veri Gözlemlenebilirliği | Monte Carlo | Monte Carlo | Kısmi: montecarlo-rules | Monitör/olay API | Platform monitor ve alarm tanımı |
| Veri Gözlemlenebilirliği | Bigeye | Bigeye | Planlandı; paket yok | Monitör/olay API | Platform monitor ve alarm tanımı |
| Veri Gözlemlenebilirliği | Datadog | Data Streams Monitoring | Planlandı; paket yok | Monitör/olay API | Platform monitor ve alarm tanımı |
| Veri Gözlemlenebilirliği | Acceldata | Acceldata | Planlandı; paket yok | Monitör/olay API | Platform monitor ve alarm tanımı |
| Veri Gözlemlenebilirliği | Elementary | Elementary | Planlandı; paket yok | Monitör/olay API | Platform monitor ve alarm tanımı |
| Veri Gözlemlenebilirliği | Genel | Tablo yükleme metrikleri | Kısmi: olay-analisti, log-ayristirici, alarm-esigi-onerici, slo-tasarimcisi, log-ornekleme-onerici, tazelik-sla, hacim-anomalisi, sema-degisikligi-izle, null-orani-izle, pipeline-calisma-analizi, log-hata-ozeti, kalite-skor-karti, teslim-sla-uyumu, katman-sayim-kaybi, alarm-gurultu-analizi, uctan-uca-gecikme, degisim-noktasi | Yerel dosya | Rapor / kod taslağı |
| Veri Bilimi & İstatistik | Python | pandas | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | Python | SciPy | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | Python | Jupyter | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | R | R / Posit | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | SAS | Viya | Kısmi: sas-kod-envanteri | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | IBM | SPSS | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | KNIME | Analytics Platform | Planlandı; paket yok | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | Alteryx | Designer | Kısmi: alteryx-envanter | Resmi dil/kütüphane SDK | Notebook veya analiz projesi |
| Veri Bilimi & İstatistik | Genel | İstatistiksel analiz | Kısmi: deney-analisti, regresyon-yorumlayici, hipotez-testi-secici, anket-agirliklandirici, tanimlayici-istatistik, korelasyon-matrisi, aykiri-deger, histogram, capraz-tablo, ab-testi, orneklem-buyuklugu, dogrusal-regresyon, mevsimsellik-ayristir, basit-tahmin, seri-anomali, yuzdelik-dilim, olcekle, pareto-analizi, guven-araligi, normallik-testi, mann-whitney | Yerel dosya | Rapor / kod taslağı |
| Machine Learning & MLOps | scikit-learn | scikit-learn | Kısmi: sklearn-proje-kurulumu | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | PyTorch | PyTorch | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | TensorFlow | TensorFlow | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | MLflow | MLflow | Kısmi: mlflow-denetimi | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | Google | Vertex AI | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | Amazon | SageMaker | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | Microsoft | Azure Machine Learning | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | DataRobot | DataRobot | Planlandı; paket yok | ML yönetim API / framework SDK | Training pipeline, model veya deployment tanımı |
| Machine Learning & MLOps | Genel | Model değerlendirme dosyaları | Kısmi: model-karti, ozellik-muhendisi, kayma-yorumlayici, esik-secici, model-terfi-karari, siniflandirma-metrikleri, regresyon-metrikleri, veri-kaymasi-psi, egitim-test-ayir, sizinti-kontrol, esik-optimizasyonu, kalibrasyon, woe-iv, cok-sinifli-metrikler, lift-tablosu, adalet-analizi, tahmin-araligi-kapsama | Yerel dosya | Rapor / kod taslağı |
| GenAI & Vektör Veri | OpenAI | API | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Anthropic | Claude API | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | LangChain | LangChain | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | LangChain | LangGraph | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | LlamaIndex | LlamaIndex | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Pinecone | Pinecone | Kısmi: pinecone-index | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Qdrant | Qdrant | Kısmi: qdrant-collection | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Weaviate | Weaviate | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Milvus | Milvus | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | PostgreSQL | pgvector | Planlandı; paket yok | Model/vektör API veya framework SDK | RAG iş akışı, değerlendirme seti veya index |
| GenAI & Vektör Veri | Genel | Doküman ve RAG verisi | Kısmi: rag-test-seti, belge-alan-cikarici, sss-yazari, parca-boyutu-secici, getirme-ayari-onerici, dokuman-parcala, dokuman-tekrar, token-maliyet-tahmini, prompt-enjeksiyon-tarama, rag-geri-getirme-degerlendirme, llm-cevap-degerlendir, llm-log-maskele, llm-kullanim-analizi, prompt-sablon-kontrol, atif-kontrol, geri-bildirim-analizi, embedding-kontrol, rag-kaynak-kapsama, prompt-surum-karsilastirma | Yerel dosya | Rapor / kod taslağı |
| Ürün & Pazarlama Analitiği | Google | GA4 | Kısmi: ga4-admin, ga4-export-denetimi | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Adobe | Analytics | Planlandı; paket yok | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Amplitude | Amplitude | Planlandı; paket yok | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Mixpanel | Mixpanel | Planlandı; paket yok | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Segment | Segment | Kısmi: tracking-plan-uyumu | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Salesforce | Data Cloud | Planlandı; paket yok | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | HubSpot | CRM | Planlandı; paket yok | Analitik ürünün query/admin API | Platform metrik/segment/event tanımı |
| Ürün & Pazarlama Analitiği | Genel | Satış ve müşteri analitiği | Kısmi: ab-test-karari, anket-kodlayici, segment-yorumlayici, huni-analisti, kohort-yorumlayici, rfm-segment, kohort-tutundurma, abc-analizi, sepet-analizi, churn-riski, musteri-degeri, donusum-hunisi, oturumlar, xyz-analizi, urun-performansi, donem-karsilastir, yeniden-siparis-noktasi, ozellik-benimseme, nps-analizi | Yerel dosya | Rapor / kod taslağı |
| Coğrafi & IoT Verisi | Esri | ArcGIS | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | QGIS | QGIS | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | PostgreSQL | PostGIS | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | PostgreSQL | TimescaleDB | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | InfluxData | InfluxDB | Kısmi: influxdb-kardinalite | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | Azure | IoT Hub | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | Amazon | IoT Core | Planlandı; paket yok | Coğrafi servis API / GIS SDK | Layer, geoprocessing job veya GIS projesi |
| Coğrafi & IoT Verisi | Genel | Konum ve sensör dosyaları | Kısmi: sube-planlayici, rota-gun-atayici, geofence-yaricap-onerici, depo-yeri-secici, koordinat-kontrol, en-yakin-nokta, sensor-esik, veri-boslugu, yeniden-ornekle, is-saati-sla, nokta-bolge-eslestir, arac-rota-ozeti, mesafe-matrisi, bolge-giris-cikis, durak-tespiti, izgara-yogunluk, kapsama-alani, konum-kumeleme | Yerel dosya | Rapor / kod taslağı |
| Kurumsal Planlama & EPM | SAP | Analytics Cloud Planning | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | Oracle | Cloud EPM | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | Anaplan | Anaplan | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | IBM | Planning Analytics | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | Workday | Adaptive Planning | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | Board | Board | Planlandı; paket yok | Planlama API / vendor SDK | Platform senaryo/model/veri yükleme işi |
| Kurumsal Planlama & EPM | Genel | Bütçe ve tahmin dosyaları | Kısmi: butce-sapma-yorumu, vardiya-planlayici, siparis-onerici, butce-fazlandirici, stok-transfer-onerici, butce-gerceklesen, butce-dagit, yil-sonu-tahmini, senaryo-analizi, konsolidasyon, cagri-merkezi-personel, fiyat-hacim-karma, basa-bas-analizi, duyarlilik-analizi, personel-maliyet-plani, tahmin-dogrulugu, kapasite-planlama, satis-hedef-takip | Yerel dosya | Rapor / kod taslağı |
| Veri Migrasyonu | AWS | Database Migration Service | Planlandı; paket yok | Migrasyon yönetim API / CLI | Platform migration task ve doğrulama kaydı |
| Veri Migrasyonu | Microsoft | Azure Database Migration | Planlandı; paket yok | Migrasyon yönetim API / CLI | Platform migration task ve doğrulama kaydı |
| Veri Migrasyonu | Google | Database Migration Service | Planlandı; paket yok | Migrasyon yönetim API / CLI | Platform migration task ve doğrulama kaydı |
| Veri Migrasyonu | SAP | Migration Cockpit | Kısmi: sap-sablon-dogrulama | Migrasyon yönetim API / CLI | Platform migration task ve doğrulama kaydı |
| Veri Migrasyonu | Oracle | Zero Downtime Migration | Planlandı; paket yok | Migrasyon yönetim API / CLI | Platform migration task ve doğrulama kaydı |
| Veri Migrasyonu | Genel | Geçiş eşleme dosyaları | Kısmi: migrasyon-esleme, tip-donusum-eslestirici, kesinti-plani, yukleme-sirasi-planlayici, mutabakat-fark-aciklayici, alan-esleme-kontrol, tablo-sayim-mutabakati, kodlama-uzunluk-riski, kolon-profil-karsilastir, sql-lehce-cevir, gecis-dalga-plani, anahtar-mutabakati, sequence-kontrol, veri-tipi-uyumu, gecis-sure-tahmini, saat-dilimi-kaymasi, bos-deger-donusumu, mojibake-tespiti | Yerel dosya | Rapor / kod taslağı |
| Data FinOps & Performans | Google | BigQuery Billing Export | Planlandı; paket yok | Platform billing/query API | Platform maliyet sorgusu veya monitor |
| Data FinOps & Performans | Snowflake | Account Usage | Kısmi: snowflake-usage, snowflake-kredi-analizi | Platform billing/query API | Platform maliyet sorgusu veya monitor |
| Data FinOps & Performans | Databricks | System Tables | Kısmi: databricks-cost, databricks-faturalama | Platform billing/query API | Platform maliyet sorgusu veya monitor |
| Data FinOps & Performans | Microsoft | Fabric Capacity Metrics | Kısmi: fabric-kapasite | Platform billing/query API | Platform maliyet sorgusu veya monitor |
| Data FinOps & Performans | Amazon | Cost and Usage Report | Planlandı; paket yok | Platform billing/query API | Platform maliyet sorgusu veya monitor |
| Data FinOps & Performans | Genel | Maliyet dışa aktarımları | Kısmi: tasarruf-plani, etiket-kurali-yazari, maliyet-anomali-aciklayici, k8s-kaynak-ayarlayici, yasam-dongusu-onerici, bulut-maliyet-ozeti, ay-sonu-maliyet-tahmini, etiket-uyumu, rezervasyon-onerisi, kaynak-boyutlandirma, maliyet-paylastir, birim-maliyet, depolama-katman-onerisi, atil-kaynaklar, lisans-kullanimi, egress-analizi | Yerel dosya | Rapor / kod taslağı |
| Dosya & Self-Service Veri | Microsoft | Excel | Kısmi: excel-baslik-cozucu | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | Microsoft | Power Query | Planlandı; paket yok | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | Google | Sheets | Planlandı; paket yok | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | DuckDB | DuckDB | Planlandı; paket yok | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | OpenRefine | OpenRefine | Planlandı; paket yok | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | Apache | Arrow / Parquet | Planlandı; paket yok | Office/Sheets API veya yerel kütüphane SDK | XLSX, Power Query M veya platform tablosu |
| Dosya & Self-Service Veri | Genel | CSV / Excel | Kısmi: veri-profili, kalite-kontrol, tablo-mutabakati, veri-sozlugu, kalite-kurali, kvkk-maskeleme, dosya-birlestir, regex-yazari, baslik-birlestirici, bosluk-temizle, sutun-adlari, harf-donustur, karakter-onar, tarih-bicimi, sayi-bicimi, bos-doldur, tekrar-sil, sutun-sec, filtrele, hesaplanan-sutun, sirala, ornekle, sira-no, sutun-bol, sutun-birlestir, bul-degistir, deger-esle, tablo-birlestir, grupla, pivot, unpivot, kumulatif-toplam, tarih-farki, bos-satir-sutun, dosya-bol, csv-donustur | Yerel dosya | Rapor / kod taslağı |
| Dosya & Self-Service Veri | Genel | SQL dosyaları | Kısmi: sql-soyagaci, sql-yazari, sema-farki, sql-aciklayici, sorgu-indeks-onerici, csv-ddl, csv-insert, merge-sql, sql-bicimlendir, sql-denetim, ddl-veri-sozlugu, ddl-er-diyagrami, ddl-lehce-cevir, sql-veri-testleri, scd2-sql | Yerel dosya | Rapor / kod taslağı |
