# Integration matrix of all domains and products

**English** · [Türkçe](INTEGRATION-MATRIX.tr.md)

207 product contexts. `Partial` only means the listed operations are implemented and passed the local contract tests. None were verified in a live account or by opening the output in the application. For unimplemented products the API/SDK path and output columns are design targets, not a claim of verified vendor support.

| Domain | Software | Product | Status / implemented operations | Planned connection | Output target |
|---|---|---|---|---|---|
| Business Intelligence | Qlik | Qlik Cloud | Partial: qlik-export, qlik-template, qlik-cloud-build, qlik-reload-history, qlik-publish, qlik-reload-schedule, qlik-space-access | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Qlik | Qlik Sense Enterprise | Partial: qlik-soyagaci, qlik-powerbi, qlik-ifade-yazari, qlik-load-script, qlik-dax-ceviri, qlik-rapor-sablonu, qlik-zaman-zekasi, qlik-section-access, qlik-artimli-yukleme, qlik-cok-tablolu-uygulama | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Qlik | QlikView | Partial: qlik-veri-modeli | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Microsoft Power BI | Power BI Service | Partial: powerbi-refresh, powerbi-export, powerbi-monitor, fabric-semantic-model, powerbi-scan, powerbi-deploy, powerbi-refresh-schedule, powerbi-rls-members, powerbi-workspace-access, pbi-tarayici-envanteri | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Microsoft Power BI | Power BI Desktop | Partial: pbit-model, dax-yazari, tarih-tablosu, dax-zaman-zekasi, power-query-m, rls-rolleri, pbi-olcu-bagimliligi, pbi-rapor-sablonu, pbi-zaman-hesaplama-grubu, pbi-rls-tmdl, pbi-vertipaq, pbi-bpa, pbi-yildiz-model-projesi | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Tableau | Tableau Cloud | Partial: tableau-export, tableau-usage, tableau-permissions, tableau-extract-schedule | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Tableau | Tableau Server | Partial: tableau-export, tableau-usage, tableau-permissions | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Tableau | Tableau Desktop | Partial: tableau-envanter, tableau-hesap-yazari, tableau-dax-ceviri, tableau-rapor-sablonu, tableau-gecis-karmasikligi, tableau-cok-tablolu-calisma-kitabi | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Looker | Looker | Partial: looker-dashboard, lookml-envanter, lookml-rapor-sablonu, looker-kullanilmayan-alanlar, lookml-cok-tablolu-proje | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Looker | Looker Studio | Planned; no package | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | SAP | SAP BusinessObjects | Planned; no package | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | SAP | SAP Analytics Cloud | Planned; no package | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Oracle | Oracle Analytics Cloud | Planned; no package | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | MicroStrategy | MicroStrategy ONE | Planned; no package | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Apache Superset | Superset | Partial: superset-pano-paketi | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | Metabase | Metabase | Partial: metabase-dashboard, metabase-pano-kurulumu | Report/app API + visual model SDK | Platform report/project file or published report |
| Business Intelligence | General | Report requirements | Partial: kpi-tanimlayici, donem-yorumu, rapor-tasarimcisi, grafik-secici, pano-kurucu, kpi-durum, excel-rapor-sablonu, html-rapor, pptx-rapor-sablonu, excel-pivot-rapor | Local file | Report / code draft |
| ERP & Business Applications | SAP | S/4HANA | Partial: sap-odata-schema | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | SAP | ECC | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | SAP | Business One | Partial: sap-quotation-draft, sap-b1-schema | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Logo | Tiger 3 | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Logo | GO 3 | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Logo | Netsis | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Logo | j-Platform | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Oracle | Fusion Cloud ERP | Partial: oracle-schema | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Oracle | E-Business Suite | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Microsoft | Dynamics 365 Finance | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Microsoft | Business Central | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Odoo | Odoo | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | IFS | IFS Cloud | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | Workday | Workday | Planned; no package | Product-specific OData/REST/SOAP or on-prem SDK | Draft document, mapping or report definition in the ERP |
| ERP & Business Applications | General | Accounting exports | Partial: hesap-plani-esleyici, birim-kodu-eslestirici, tevkifat-kodu-onerici, tahsilat-eslestirici, masraf-politika-denetcisi, eirsaliye-uret, mizan-kontrol, mizan-tablolar, yevmiye-kontrol, cari-yaslandirma, tahsilat-eslestir, banka-mutabakat, mt940-csv, efatura-csv, kdv-kontrol, kur-cevir, benford-analizi, mukerrer-odeme, amortisman-plani, kredi-odeme-plani, stok-maliyeti, gider-siniflandir, nakit-akisi, efatura-uret | Local file | Report / code draft |
| Data Warehouse | Google | BigQuery | Partial: bigquery-table, bigquery-agent, bigquery-setup, bigquery-sema, bigquery-is-maliyeti, bigquery-tablo-tasarim, bigquery-terraform-kurulumu | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Snowflake | Snowflake | Partial: snowflake-quality, snowflake-setup, snowflake-grant-sync, boyutsal-model, ambar-boyutu-secici, ambar-kurucu, snowflake-sorgu-gecmisi, snowflake-depolama, kullanilmayan-tablolar, scd2-butunluk, clustering-derinligi, snowflake-rbac-denetimi, snowflake-hesap-kurulumu | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Microsoft | Fabric Warehouse | Planned; no package | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Microsoft | Azure Synapse | Planned; no package | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Amazon | Redshift | Partial: redshift-tablo-tasarim | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Oracle | Autonomous Data Warehouse | Planned; no package | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | Teradata | Vantage | Partial: teradata-is-yuku | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | ClickHouse | ClickHouse | Partial: clickhouse-parca-analizi | Query job API / SQL driver | Platform table, view or SQL model |
| Data Warehouse | General | Dimensional model (SQL) | Partial: kolon-adi-standardi, scd-tipi-onerici, ozet-tablo-onerici, yildiz-sema | Local file | Report / code draft |
| Data Lake & Lakehouse | Databricks | Databricks Lakehouse | Partial: databricks-monitor, databricks-run, databricks-profile, databricks-job-create, katman-atayici, medallion-katman-kurallari | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Databricks | Unity Catalog | Partial: unity-catalog-setup, uc-grant-sync, uc-gecis-hazirligi, unity-catalog-kurulumu | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Microsoft | Fabric Lakehouse | Partial: fabric-notebook-run, fabric-notebook-export, fabric-notebook, fabric-notebook-monitor, fabric-lakehouse-build | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Microsoft | OneLake | Planned; no package | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Amazon | S3 / Lake Formation | Planned; no package | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Google | Cloud Storage / BigLake | Planned; no package | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Apache | Iceberg | Partial: iceberg-metadata-ozet | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Apache | Hudi | Planned; no package | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Delta Lake | Delta Lake | Partial: bolumleme-danismani, delta-bakim-plani, zorder-secici, delta-log-ozet, nesne-dosya-boyutu, delta-vacuum-tahmini, bolum-yapisi-denetimi, tablo-bakim-plani | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Lake & Lakehouse | Dremio | Dremio | Planned; no package | Workspace/catalog API + SQL/Spark SDK | Notebook, job or native table |
| Data Integration | Microsoft | Azure Data Factory | Partial: adf-deploy, adf-run, adf-monitor, adf-solution, adf-history, adf-pipeline-envanter, adf-metadata-pipeline | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Microsoft | SSIS | Partial: ssis-paket-envanter | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Microsoft | Fabric Data Factory | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Informatica | IDMC | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Informatica | PowerCenter | Partial: powercenter-envanter | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Qlik | Talend Cloud | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Qlik | Qlik Replicate | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Fivetran | Fivetran | Partial: fivetran-sync, fivetran-schema, fivetran-mar | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Airbyte | Airbyte | Partial: airbyte-monitor, airbyte-sync, airbyte-connection, toplu-cekim-planlayici, cdc-yontem-secici | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | SAP | Data Services | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Oracle | Data Integrator | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Oracle | GoldenGate | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | Meltano | Meltano | Planned; no package | ETL management API/SDK | Platform pipeline definition and run record |
| Data Integration | General | File format conversion | Partial: json-esleme-yazari, birlestirme-anahtari, sabit-genislik-cozucu, json-csv, csv-json, jsonl-csv, xml-csv, sabit-genislik, excel-sayfalar, csv-xlsx, erisim-logu, csv-html-rapor | Local file | Report / code draft |
| Analytics Engineering | dbt | dbt Core | Partial: dbt-denetim, dbt-model-yazari, dbt-test-onerici, incremental-strateji-onerici, kaynak-tazelik-onerici, dbt-test-siddeti, dbt-iskelet, dbt-calisma-sonuclari, dbt-dag-analizi, dbt-test-kapsami, dbt-kaynak-tazeligi, dbt-artimli-model-kontrol, dbt-artimli-model, dbt-snapshot-sablonu, dbt-modelleme-kurallari, dbt-proje-sablonu, dbt-yildiz-projesi | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Analytics Engineering | dbt | dbt Cloud | Partial: dbt-run, dbt-monitor, dbt-cloud-job | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Analytics Engineering | Google | Dataform | Partial: dataform-compile, dataform-run, dataform-monitor | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Analytics Engineering | SQLMesh | SQLMesh | Planned; no package | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Analytics Engineering | Coalesce | Coalesce | Planned; no package | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Analytics Engineering | Cube | Cube | Planned; no package | Product project/compile/job API | dbt/Dataform/SQLMesh project file |
| Orchestration & DataOps | Apache | Airflow | Partial: airflow-run, airflow-monitor, airflow-activate, airflow-history, backfill-planlayici, sensor-ayarlayici, pipeline-kurucu, airflow-dag-denetim, airflow-havuz-analizi, airflow-dag-kurulumu | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | Dagster | Dagster | Partial: dagster-schedule | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | Prefect | Prefect | Partial: prefect-run, prefect-monitor, prefect-deployment | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | Astronomer | Astro | Planned; no package | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | Kestra | Kestra | Planned; no package | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | GitHub | Actions | Partial: github-actions-run, github-actions-denetim | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | Jenkins | Jenkins | Planned; no package | Orchestrator REST/GraphQL/SDK | DAG, flow or job definition |
| Orchestration & DataOps | General | Schedule and workflow files | Partial: zamanlama-planlayici, hata-siniflandirici, yeniden-deneme-politikasi, cron-aciklama, is-bagimlilik-analizi, zamanlama-yogunlugu, bakim-penceresi-kontrol, yeniden-deneme-analizi | Local file | Report / code draft |
| Streaming & Real Time | Apache | Kafka | Partial: kafka-konu-tasarimcisi, sema-uyumlulugu, kafka-tuketici-gecikmesi, kafka-konu-denetimi, cdc-olay-ozeti, mesaj-boyutu-analizi, anahtar-carpikligi, dlq-analizi, schema-registry-denetimi | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Apache | Flink | Partial: filigran-onerici, tekrar-penceresi-onerici | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Apache | Spark Streaming | Planned; no package | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Confluent | Confluent Cloud | Partial: confluent-topics | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Amazon | Kinesis | Partial: akis-kapasite | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Microsoft | Event Hubs | Planned; no package | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Google | Pub/Sub | Planned; no package | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Google | Dataflow | Planned; no package | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | Redpanda | Redpanda | Planned; no package | Broker admin API + client SDK | Topic, schema or stream job definition |
| Streaming & Real Time | General | Event schemas and samples | Partial: avro-sema-tasarimci, olay-semasi-cikar, olay-gecikmesi, olay-tekrar-sira, pencere-toplam, outbox-mutabakati | Local file | Report / code draft |
| Database Management | PostgreSQL | PostgreSQL | Partial: sql-optimizasyon, autovacuum-ayarlayici, baglanti-havuzu-boyutlandirici, zaman-asimi-onerici, postgres-explain-analizi, pg-stat-statements, pg-tablo-sisme, pg-kilit-agaci, pg-uzun-islemler, indeks-tekrari, pg-yapilandirma, veritabani-normalizasyon | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Microsoft | SQL Server | Partial: sqlserver-plan-analizi, sqlserver-bekleme-analizi, indeks-kullanim-analizi, sqlserver-deadlock, eksik-indeks-onerisi, yedek-kontrolu, sqlserver-yapilandirma | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Microsoft | Azure SQL | Planned; no package | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Oracle | Oracle Database | Partial: oracle-awr | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | MySQL | MySQL | Partial: mysql-yavas-log | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | MongoDB | Atlas | Partial: mongodb-profil-analizi, mongodb-indeks-kullanimi | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Redis | Redis | Partial: redis-saglik | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Couchbase | Capella | Planned; no package | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Amazon | DynamoDB | Planned; no package | Official DB driver / admin API | SQL/DDL or database object |
| Database Management | Neo4j | Neo4j | Planned; no package | Official DB driver / admin API | SQL/DDL or database object |
| Data Quality | Great Expectations | GX | Partial: gx-soda-kurallari | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | Soda | Soda Core | Planned; no package | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | Soda | Soda Cloud | Partial: soda-scan | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | AWS | Deequ | Planned; no package | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | Informatica | Cloud Data Quality | Planned; no package | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | Qlik | Talend Data Quality | Planned; no package | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | Ataccama | ONE | Planned; no package | Quality product API / SDK | Product-specific expectation/check definition |
| Data Quality | General | Data validation rules | Partial: kalite-kok-neden, capraz-kural-yazari, gecerli-aralik-onerici, bos-deger-politikasi, tckn-dogrula, vkn-dogrula, iban-dogrula, telefon-normalize, eposta-dogrula, plaka-dogrula, il-normalize, posta-kodu-dogrula, kart-luhn, referans-butunlugu, anahtar-bul, bagimlilik-kontrol, desen-profili, kontrol-toplami, satir-kurallari | Local file | Report / code draft |
| Governance & Data Catalog | Microsoft | Purview | Partial: purview-glossary, sozluk-ice-aktarim | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | Collibra | Data Intelligence Cloud | Planned; no package | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | Alation | Data Catalog | Planned; no package | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | Google | Dataplex | Planned; no package | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | Atlan | Atlan | Planned; no package | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | OpenMetadata | OpenMetadata | Partial: openmetadata-glossary | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | DataHub | DataHub | Partial: datahub-glossary | Catalog metadata REST/GraphQL API | Term, owner or lineage record on the platform |
| Governance & Data Catalog | General | Access and ownership lists | Partial: veri-sozlesmesi-yazari, sema-degisiklik-etkisi, sozluk-kolon-eslestirici, veri-sahibi-onerici, rol-madenciligi, erisim-gozden-gecirme, veri-sahipligi-kapsami, is-sozlugu-kontrol, sql-soy-agaci, gorev-ayriligi, siniflandirma-tutarliligi, veri-sozlesmesi, erisim-talep-denetimi, rapor-kullanim-envanteri, siniflandirma-yayilimi, yetki-degisim-denetimi, istisna-kaydi-kontrol, kullanilmayan-yetkiler | Local file | Report / code draft |
| Master Data Management | SAP | Master Data Governance | Planned; no package | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | Informatica | MDM SaaS | Partial: eslestirme-kalibrasyonu | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | Semarchy | xDM | Planned; no package | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | Reltio | Reltio | Planned; no package | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | TIBCO | EBX | Planned; no package | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | Stibo Systems | STEP | Planned; no package | MDM API / vendor SDK | Matching and survivorship rule/draft |
| Master Data Management | General | Master data cleansing | Partial: eslestirme-hakemi, adres-cozumleyici, urun-kategori-eslestirici, kod-listesi-eslestirici, altin-kayit-kurali, benzer-kayit, unvan-normalize, adres-ayristir, golden-record, ad-soyad-ayir, hiyerarsi-kontrol, referans-esle, ana-veri-tamlik, paylasilan-kimlik, hiyerarsi-duzlestir, ana-veri-degisiklik, urun-ozellik-tutarlilik | Local file | Report / code draft |
| Privacy & Data Security | Microsoft | Purview Information Protection | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | OneTrust | Data Discovery | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | BigID | BigID | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | Immuta | Immuta | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | Privacera | Privacera | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | IBM | Guardium | Planned; no package | Classification/policy API | Access/masking policy draft on the platform |
| Privacy & Data Security | General | Sensitive data inventory | Partial: kvkk-envanteri, sentetik-veri-tasarimcisi, maskeleme-plani, k-anonimlik-genellestirici, silme-talebi-planlayici, kisisel-veri-envanteri, saklama-suresi-kontrol, kvkk-basvuru-sure, k-anonimlik, genellestir, iys-izin-kontrol, yurt-disi-aktarim, ihlal-bildirim-suresi, cerez-envanteri, ilgili-kisi-arama, test-ortami-gercek-veri, maskeleme-dogrulama | Local file | Report / code draft |
| Data Observability | Monte Carlo | Monte Carlo | Partial: montecarlo-rules | Monitor/incident API | Platform monitor and alert definition |
| Data Observability | Bigeye | Bigeye | Planned; no package | Monitor/incident API | Platform monitor and alert definition |
| Data Observability | Datadog | Data Streams Monitoring | Planned; no package | Monitor/incident API | Platform monitor and alert definition |
| Data Observability | Acceldata | Acceldata | Planned; no package | Monitor/incident API | Platform monitor and alert definition |
| Data Observability | Elementary | Elementary | Planned; no package | Monitor/incident API | Platform monitor and alert definition |
| Data Observability | General | Table load metrics | Partial: olay-analisti, log-ayristirici, alarm-esigi-onerici, slo-tasarimcisi, log-ornekleme-onerici, tazelik-sla, hacim-anomalisi, sema-degisikligi-izle, null-orani-izle, pipeline-calisma-analizi, log-hata-ozeti, kalite-skor-karti, teslim-sla-uyumu, katman-sayim-kaybi, alarm-gurultu-analizi, uctan-uca-gecikme, degisim-noktasi | Local file | Report / code draft |
| Data Science & Statistics | Python | pandas | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | Python | SciPy | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | Python | Jupyter | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | R | R / Posit | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | SAS | Viya | Partial: sas-kod-envanteri | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | IBM | SPSS | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | KNIME | Analytics Platform | Planned; no package | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | Alteryx | Designer | Partial: alteryx-envanter | Official language/library SDK | Notebook or analysis project |
| Data Science & Statistics | General | Statistical analysis | Partial: deney-analisti, regresyon-yorumlayici, hipotez-testi-secici, anket-agirliklandirici, tanimlayici-istatistik, korelasyon-matrisi, aykiri-deger, histogram, capraz-tablo, ab-testi, orneklem-buyuklugu, dogrusal-regresyon, mevsimsellik-ayristir, basit-tahmin, seri-anomali, yuzdelik-dilim, olcekle, pareto-analizi, guven-araligi, normallik-testi, mann-whitney | Local file | Report / code draft |
| Machine Learning & MLOps | scikit-learn | scikit-learn | Partial: sklearn-proje-kurulumu | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | PyTorch | PyTorch | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | TensorFlow | TensorFlow | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | MLflow | MLflow | Partial: mlflow-denetimi | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | Google | Vertex AI | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | Amazon | SageMaker | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | Microsoft | Azure Machine Learning | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | DataRobot | DataRobot | Planned; no package | ML management API / framework SDK | Training pipeline, model or deployment definition |
| Machine Learning & MLOps | General | Model evaluation files | Partial: model-karti, ozellik-muhendisi, kayma-yorumlayici, esik-secici, model-terfi-karari, siniflandirma-metrikleri, regresyon-metrikleri, veri-kaymasi-psi, egitim-test-ayir, sizinti-kontrol, esik-optimizasyonu, kalibrasyon, woe-iv, cok-sinifli-metrikler, lift-tablosu, adalet-analizi, tahmin-araligi-kapsama | Local file | Report / code draft |
| GenAI & Vector Data | OpenAI | API | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | Anthropic | Claude API | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | LangChain | LangChain | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | LangChain | LangGraph | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | LlamaIndex | LlamaIndex | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | Pinecone | Pinecone | Partial: pinecone-index | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | Qdrant | Qdrant | Partial: qdrant-collection | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | Weaviate | Weaviate | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | Milvus | Milvus | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | PostgreSQL | pgvector | Planned; no package | Model/vector API or framework SDK | RAG workflow, evaluation set or index |
| GenAI & Vector Data | General | Documents and RAG data | Partial: rag-test-seti, belge-alan-cikarici, sss-yazari, parca-boyutu-secici, getirme-ayari-onerici, dokuman-parcala, dokuman-tekrar, token-maliyet-tahmini, prompt-enjeksiyon-tarama, rag-geri-getirme-degerlendirme, llm-cevap-degerlendir, llm-log-maskele, llm-kullanim-analizi, prompt-sablon-kontrol, atif-kontrol, geri-bildirim-analizi, embedding-kontrol, rag-kaynak-kapsama, prompt-surum-karsilastirma | Local file | Report / code draft |
| Product & Marketing Analytics | Google | GA4 | Partial: ga4-admin, ga4-export-denetimi | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | Adobe | Analytics | Planned; no package | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | Amplitude | Amplitude | Planned; no package | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | Mixpanel | Mixpanel | Planned; no package | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | Segment | Segment | Partial: tracking-plan-uyumu | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | Salesforce | Data Cloud | Planned; no package | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | HubSpot | CRM | Planned; no package | Analytics product query/admin API | Platform metric/segment/event definition |
| Product & Marketing Analytics | General | Sales and customer analytics | Partial: ab-test-karari, anket-kodlayici, segment-yorumlayici, huni-analisti, kohort-yorumlayici, rfm-segment, kohort-tutundurma, abc-analizi, sepet-analizi, churn-riski, musteri-degeri, donusum-hunisi, oturumlar, xyz-analizi, urun-performansi, donem-karsilastir, yeniden-siparis-noktasi, ozellik-benimseme, nps-analizi | Local file | Report / code draft |
| Geospatial & IoT Data | Esri | ArcGIS | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | QGIS | QGIS | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | PostgreSQL | PostGIS | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | PostgreSQL | TimescaleDB | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | InfluxData | InfluxDB | Partial: influxdb-kardinalite | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | Azure | IoT Hub | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | Amazon | IoT Core | Planned; no package | Geospatial service API / GIS SDK | Layer, geoprocessing job or GIS project |
| Geospatial & IoT Data | General | Location and sensor files | Partial: sube-planlayici, rota-gun-atayici, geofence-yaricap-onerici, depo-yeri-secici, koordinat-kontrol, en-yakin-nokta, sensor-esik, veri-boslugu, yeniden-ornekle, is-saati-sla, nokta-bolge-eslestir, arac-rota-ozeti, mesafe-matrisi, bolge-giris-cikis, durak-tespiti, izgara-yogunluk, kapsama-alani, konum-kumeleme | Local file | Report / code draft |
| Enterprise Planning & EPM | SAP | Analytics Cloud Planning | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | Oracle | Cloud EPM | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | Anaplan | Anaplan | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | IBM | Planning Analytics | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | Workday | Adaptive Planning | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | Board | Board | Planned; no package | Planning API / vendor SDK | Platform scenario/model/data load job |
| Enterprise Planning & EPM | General | Budget and forecast files | Partial: butce-sapma-yorumu, vardiya-planlayici, siparis-onerici, butce-fazlandirici, stok-transfer-onerici, butce-gerceklesen, butce-dagit, yil-sonu-tahmini, senaryo-analizi, konsolidasyon, cagri-merkezi-personel, fiyat-hacim-karma, basa-bas-analizi, duyarlilik-analizi, personel-maliyet-plani, tahmin-dogrulugu, kapasite-planlama, satis-hedef-takip | Local file | Report / code draft |
| Data Migration | AWS | Database Migration Service | Planned; no package | Migration management API / CLI | Platform migration task and validation record |
| Data Migration | Microsoft | Azure Database Migration | Planned; no package | Migration management API / CLI | Platform migration task and validation record |
| Data Migration | Google | Database Migration Service | Planned; no package | Migration management API / CLI | Platform migration task and validation record |
| Data Migration | SAP | Migration Cockpit | Partial: sap-sablon-dogrulama | Migration management API / CLI | Platform migration task and validation record |
| Data Migration | Oracle | Zero Downtime Migration | Planned; no package | Migration management API / CLI | Platform migration task and validation record |
| Data Migration | General | Migration mapping files | Partial: migrasyon-esleme, tip-donusum-eslestirici, kesinti-plani, yukleme-sirasi-planlayici, mutabakat-fark-aciklayici, alan-esleme-kontrol, tablo-sayim-mutabakati, kodlama-uzunluk-riski, kolon-profil-karsilastir, sql-lehce-cevir, gecis-dalga-plani, anahtar-mutabakati, sequence-kontrol, veri-tipi-uyumu, gecis-sure-tahmini, saat-dilimi-kaymasi, bos-deger-donusumu, mojibake-tespiti | Local file | Report / code draft |
| Data FinOps & Performance | Google | BigQuery Billing Export | Planned; no package | Platform billing/query API | Platform cost query or monitor |
| Data FinOps & Performance | Snowflake | Account Usage | Partial: snowflake-usage, snowflake-kredi-analizi | Platform billing/query API | Platform cost query or monitor |
| Data FinOps & Performance | Databricks | System Tables | Partial: databricks-cost, databricks-faturalama | Platform billing/query API | Platform cost query or monitor |
| Data FinOps & Performance | Microsoft | Fabric Capacity Metrics | Partial: fabric-kapasite | Platform billing/query API | Platform cost query or monitor |
| Data FinOps & Performance | Amazon | Cost and Usage Report | Planned; no package | Platform billing/query API | Platform cost query or monitor |
| Data FinOps & Performance | General | Cost exports | Partial: tasarruf-plani, etiket-kurali-yazari, maliyet-anomali-aciklayici, k8s-kaynak-ayarlayici, yasam-dongusu-onerici, bulut-maliyet-ozeti, ay-sonu-maliyet-tahmini, etiket-uyumu, rezervasyon-onerisi, kaynak-boyutlandirma, maliyet-paylastir, birim-maliyet, depolama-katman-onerisi, atil-kaynaklar, lisans-kullanimi, egress-analizi | Local file | Report / code draft |
| Files & Self-Service Data | Microsoft | Excel | Partial: excel-baslik-cozucu | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | Microsoft | Power Query | Planned; no package | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | Google | Sheets | Planned; no package | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | DuckDB | DuckDB | Planned; no package | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | OpenRefine | OpenRefine | Planned; no package | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | Apache | Arrow / Parquet | Planned; no package | Office/Sheets API or local library SDK | XLSX, Power Query M or platform table |
| Files & Self-Service Data | General | CSV / Excel | Partial: veri-profili, kalite-kontrol, tablo-mutabakati, veri-sozlugu, kalite-kurali, kvkk-maskeleme, dosya-birlestir, regex-yazari, baslik-birlestirici, bosluk-temizle, sutun-adlari, harf-donustur, karakter-onar, tarih-bicimi, sayi-bicimi, bos-doldur, tekrar-sil, sutun-sec, filtrele, hesaplanan-sutun, sirala, ornekle, sira-no, sutun-bol, sutun-birlestir, bul-degistir, deger-esle, tablo-birlestir, grupla, pivot, unpivot, kumulatif-toplam, tarih-farki, bos-satir-sutun, dosya-bol, csv-donustur | Local file | Report / code draft |
| Files & Self-Service Data | General | SQL files | Partial: sql-soyagaci, sql-yazari, sema-farki, sql-aciklayici, sorgu-indeks-onerici, csv-ddl, csv-insert, merge-sql, sql-bicimlendir, sql-denetim, ddl-veri-sozlugu, ddl-er-diyagrami, ddl-lehce-cevir, sql-veri-testleri, scd2-sql | Local file | Report / code draft |
