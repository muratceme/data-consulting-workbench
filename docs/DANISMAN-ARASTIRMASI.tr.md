# Danışmanlar bu platformlarda gerçekte ne yapıyor?

[English](DANISMAN-ARASTIRMASI.md) · **Türkçe**

İnceleme tarihi: 23 Ekim 2026. Amaç: sitedeki 23 alan / 207 ürün için, o platformda çalışan veri danışmanlarının
gerçekten sattığı ve teslim ettiği işleri bulmak; sitede karşılığı olmayanları eklemek. Hiçbir paket kaldırılmadı.

Yöntem: danışmanlık firmalarının pazar yeri ilanları (Microsoft Marketplace, AWS Marketplace, G-Cloud), iş ilanları,
üretici dokümantasyonu ve açık kaynak denetim araçları tarandı. Kaynakların çoğu satıcı tanıtımıdır; kapsamı gösterir,
standart değildir.

## Bulgu: danışman işi platform başına aynı dört kalıba oturuyor

1. **Sağlık kontrolü / denetim** (2–3 hafta, puanlı bulgu raporu + önceliklendirilmiş yol haritası). Platformun kendi
   dışa aktarımları okunur: Power BI'da VertiPaq + Best Practice Analyzer, SQL Server'da yapılandırma + bekleme + indeks,
   Oracle'da AWR, PostgreSQL'de pg_settings + pg_stat_statements + şişme, Snowflake'te sorgu geçmişi + RBAC.
2. **Geçiş değerlendirmesi** (envanter + karmaşıklık puanı + efor tahmini + dalga planı): Tableau→Power BI,
   QlikView→Qlik Sense, PowerCenter→IDMC, SSIS→ADF/Fabric, Teradata→Snowflake, SAS→Python, Alteryx→dbt,
   Hive metastore→Unity Catalog, legacy ERP→S/4HANA.
3. **Maliyet / kapasite optimizasyonu**: Databricks system.billing, Fabric Capacity Metrics, BigQuery JOBS,
   Snowflake kredileri, Fivetran MAR.
4. **Kurulum / kural yazımı**: GX ve Soda kontrolleri, Purview/Collibra sözlüğü, MDM eşleştirme ve survivorship kuralları,
   GA4 / tracking plan denetimi.

## Sitede zaten karşılığı olanlar

Power BI model dokümanı (.pbit), Tableau envanteri, Qlik soy ağacı, LookML envanteri, dbt denetimi ve DAG analizi,
Airflow DAG denetimi, ADF ve SSIS envanteri, Snowflake sorgu geçmişi ve depolama, BigQuery iş maliyeti ve tablo tasarımı,
Redshift tablo tasarımı, PostgreSQL pg_stat_statements ve şişme, SQL Server bekleme / indeks / deadlock / yedek,
MySQL yavaş log, MongoDB profiler, Kafka topic denetimi ve tüketici gecikmesi, rapor kullanım envanteri, erişim gözden
geçirmesi, mükerrer kayıt ve golden record, KVKK envanteri. Bunlar araştırmadaki işlerle örtüşüyor; kaldırılacak paket yok.

## Eksik bulunan ve eklenen işler

Toplam 32 iş, dört paket hâlinde eklendi (86430ac, d9e93c0, 01dd3f5 ve son paket). Hepsi örnek verisiyle testte çalışır; canlı platform üzerinde denenmedi.

| Alan | Ürün | Danışmanın yaptığı iş | Eklenen paket |
|---|---|---|---|
| BI | Power BI Desktop | VertiPaq Analyzer ile model boyutu ve kardinalite incelemesi | `pbi-vertipaq` |
| BI | Power BI Desktop | Tabular Editor Best Practice Analyzer kuralları | `pbi-bpa` |
| BI | Tableau Desktop | Tableau→Power BI geçişi için çalışma kitabı karmaşıklık puanı | `tableau-gecis-karmasikligi` |
| BI | QlikView / Qlik Sense | Sentetik anahtar, döngüsel ilişki ve ada tablo denetimi | `qlik-veri-modeli` |
| BI | Looker | System Activity ile kullanılmayan alan/explore temizliği | `looker-kullanilmayan-alanlar` |
| Ambar | Snowflake | RBAC denetimi (ACCOUNTADMIN, rol hiyerarşisi, PUBLIC yetkileri) | `snowflake-rbac-denetimi` |
| Ambar | Teradata Vantage | DBQL iş yükü analizi ve dönüştürülecek söz dizimi envanteri | `teradata-is-yuku` |
| Lakehouse | Unity Catalog | Hive metastore → Unity Catalog geçiş hazırlığı (UCX değerlendirmesi benzeri) | `uc-gecis-hazirligi` |
| FinOps | Databricks System Tables | system.billing.usage: all-purpose'ta çalışan işler, etiketsiz kullanım | `databricks-faturalama` |
| FinOps | Fabric Capacity Metrics | CU aşımı, kısıtlama (throttling) pencereleri, en çok tüketen öğeler | `fabric-kapasite` |
| Entegrasyon | Fivetran | MAR'ı bağlantı ve tablo bazında analiz | `fivetran-mar` |
| Entegrasyon | PowerCenter | Repository XML envanteri ve IDMC geçiş karmaşıklığı | `powercenter-envanter` |
| Veritabanı | Oracle Database | AWR raporu: yük profili, en çok bekleme olayı, en pahalı SQL | `oracle-awr` |
| Veritabanı | PostgreSQL | pg_settings yapılandırma denetimi ve wraparound riski | `pg-yapilandirma` |
| Veritabanı | SQL Server | Sunucu ve veritabanı yapılandırma denetimi (sp_Blitz benzeri) | `sqlserver-yapilandirma` |
| Veritabanı | MongoDB Atlas | $indexStats ile kullanılmayan ve tekrar eden indeksler | `mongodb-indeks-kullanimi` |
| Kalite | GX / Soda | Profilden GX expectation suite ve SodaCL kontrol dosyası üretimi | `gx-soda-kurallari` |
| Yönetişim | Purview / Collibra | İş sözlüğünü içe aktarım dosyasına çevirme ve doğrulama | `sozluk-ice-aktarim` |
| MDM | MDM araçları | Eşleştirme eşiği kalibrasyonu (fazla/eksik birleştirme) | `eslestirme-kalibrasyonu` |
| Ürün analitiği | GA4 | BigQuery dışa aktarım denetimi (boş günler, eksik parametreler) | `ga4-export-denetimi` |
| Ürün analitiği | Segment / Amplitude | Tracking plan uyumu | `tracking-plan-uyumu` |
| Migrasyon | SAP Migration Cockpit | Doldurulmuş şablonun yükleme öncesi doğrulaması | `sap-sablon-dogrulama` |
| Veri bilimi | SAS Viya | SAS kod envanteri ve Python geçiş karmaşıklığı | `sas-kod-envanteri` |
| Veri bilimi | Alteryx Designer | .yxmd iş akışı envanteri ve dbt/Python geçiş karmaşıklığı | `alteryx-envanter` |
| ML | MLflow | Deney ve model kayıt defteri denetimi | `mlflow-denetimi` |
| Streaming | Kinesis / Event Hubs | Throughput'a göre shard / throughput unit boyutlandırma | `akis-kapasite` |
| Orkestrasyon | Airflow | Havuz (pool) doygunluğu, kuyrukta bekleme ve zombi görevler | `airflow-havuz-analizi` |
| Analytics engineering | dbt Core | dbt_project_evaluator modelleme kuralları (kaynak fan-out, yeniden birleşme vb.) | `dbt-modelleme-kurallari` |
| BI | Power BI Service | Scanner API ile kiracı envanteri ve yönetişim | `pbi-tarayici-envanteri` |
| Veritabanı | Redis | INFO çıktısıyla bellek, tahliye ve kalıcılık kontrolü | `redis-saglik` |
| Ambar | ClickHouse | system.parts ile parça ve bölümleme analizi | `clickhouse-parca-analizi` |
| Zaman serisi | InfluxDB | Seri kardinalitesi analizi | `influxdb-kardinalite` |

## İkinci tur: sıfırdan kurulum işleri

İlk tur çoğunlukla var olan bir kurulumu denetleyen ve iyileştiren işleri ekledi. Danışmanlar aynı platformları müşteride sıfırdan da kurar: verilen tablolardan
model ve pano, boş bir hesapta rol/depo/bütçe düzeni, ilk yükleme hattı, ilk DAG. Sitede tek dosyalık rapor şablonları zaten vardı (`qlik-rapor-sablonu`,
`pbi-rapor-sablonu`, `tableau-rapor-sablonu`, `lookml-rapor-sablonu`, `dbt-proje-sablonu`, `excel-rapor-sablonu`, `pptx-rapor-sablonu`); eksik olan, birden çok
tablodan ilişkili model kurmak ve BI dışındaki platformların ilk kurulumuydu. Bu turda 14 kod bloğu ve 3 agent eklendi. Bu turda yeni web araştırması yapılmadı;
işler ilk turdaki kaynaklarda anlatılan kurulum hizmetlerine ve platformların kendi kurulum belgelerindeki adımlara göre seçildi.

| Alan | Ürün | Sıfırdan kurulum işi | Eklenen paket |
|---|---|---|---|
| BI | Qlik Sense | Tablo klasöründen ilişkili veri modeli, ana öğeler ve sayfalarla uygulama (sentetik anahtar/döngü yok) | `qlik-cok-tablolu-uygulama` |
| BI | Power BI Desktop | Yıldız model, çoktan bire ilişkiler, takvim, DAX ölçüleri ve iki sayfalık rapor (PBIP) | `pbi-yildiz-model-projesi` |
| BI | Looker | Tablo başına view, many_to_one join’li explore, LookML panosu | `lookml-cok-tablolu-proje` |
| BI | Tableau Desktop | Sol birleştirmeli veri kaynağı, KPI/trend/kırılım sayfaları ve pano | `tableau-cok-tablolu-calisma-kitabi` |
| BI | Superset | Sanal veri kümesi, grafikler ve filtrelerle içe aktarma paketi | `superset-pano-paketi` |
| BI | Metabase | SQL kartları ve API ile koleksiyon + pano kuran betik | `metabase-pano-kurulumu` |
| BI | Rapor gereksinimleri | İsteğe göre çok tablolu pano tasarlayıp yukarıdaki projeleri kuran agent | `pano-kurucu` |
| Veritabanı | PostgreSQL / SQL Server | Düz dışa aktarımdan 3NF tablolar, anahtarlar, DDL ve yükleme | `veritabani-normalizasyon` |
| Dönüşüm | dbt Core | Çok tablolu kaynak → staging → dim/fct, ilişki testleri | `dbt-yildiz-projesi` |
| Ambar | Snowflake | Veritabanları, erişim/işlevsel roller, depolar, resource monitor, servis kullanıcıları | `snowflake-hesap-kurulumu` |
| Lakehouse | Unity Catalog | Kataloglar, şemalar, grup ve servis sorumlusu yetkileri | `unity-catalog-kurulumu` |
| Ambar | BigQuery | Ortam projelerinde veri kümeleri, IAM ve servis hesapları (Terraform) | `bigquery-terraform-kurulumu` |
| Ambar | Snowflake / UC / BigQuery | Serbest metindeki kurulum isteğini erişim planına çevirip kuran agent | `ambar-kurucu` |
| Entegrasyon | Azure Data Factory | Kontrol tablosu, tam/artımlı kopya, watermark ile metadata güdümlü hat | `adf-metadata-pipeline` |
| Orkestrasyon | Airflow | Görev listesinden DAG, havuzlar, kritik yol ve son saat kontrolü | `airflow-dag-kurulumu` |
| Orkestrasyon | Airflow | Serbest metindeki veri hattını doğrulanmış DAG’e çeviren agent | `pipeline-kurucu` |
| ML | scikit-learn | Sızıntı ayıklama, özellik seçimi, eğitim ve tahmin betikleriyle proje | `sklearn-proje-kurulumu` |

Hepsi örnek verisiyle testte çalışır. Üretilen projeler (QVF, PBIP, LookML, TWB, Superset paketi, Metabase betiği, Snowflake/UC SQL’i, Terraform, ADF JSON’u,
DAG, ML betikleri) hedef uygulamalarda açılarak veya çalıştırılarak denenmedi.

## Üçüncü tur: kurulumu API ile hesaba göndermek

Her programın resmî API belgesine bakılarak, kurulum bloklarının ürettiği dosyaların API ile platforma kurulup kurulamayacağı incelendi. Uygulanan on paket ve
API’nin izin verdiği sınır:

| Program | API ile yapılabilen | API ile yapılamayan (paket ne yapar) | Paket |
|---|---|---|---|
| Qlik Cloud | Data Files yükleme, uygulama oluşturma, load script yazma (`/apps/{id}/scripts`), reload, veri modeli metadata’sı | Ana öğe ve sayfalar Engine API (websocket) ister; build.cmd ile qlik-cli’ye bırakılır | `qlik-cloud-build` |
| Fabric Lakehouse | Lakehouse oluşturma, OneLake’e dosya yazma, Load Table ile Delta tablosu, tablo listesi | Şema etkin lakehouse’ta Load Table desteklenmez | `fabric-lakehouse-build` |
| Power BI Service | TMDL tanımıyla semantic model oluşturma ve geri okuma | Rapor (PBIR) oluşturma API’sinde `definition.pbir` biçimi konusunda kaynaklar çelişiyor; rapor Desktop’tan yayımlanır. Veri yenilemesi için kimlik bilgisi platformda bağlanır | `fabric-semantic-model` |
| Metabase | Koleksiyon, SQL kartı, pano ve kart yerleşimi (`X-API-Key`) | Kart yerleşim uç noktası sürüme göre değişti; paket 0.47+ biçimini kullanır | `metabase-dashboard` |
| Looker | Explore alanlarını okuma, sorgu, kullanıcı tanımlı pano ve öğe oluşturma | LookML dosyaları API ile yazılmaz, Git ile yayımlanır | `looker-dashboard` |
| Snowflake | SQL API ile her DDL/GRANT komutu, SHOW GRANTS ile doğrulama | Rol değişimi token kapsamına bağlı | `snowflake-setup` |
| Unity Catalog | Katalog/şema oluşturma, yetki ekleme ve okuma, sahiplik | Grup ve servis sorumlusu oluşturma hesap API’sindedir; önceden var olmalı | `unity-catalog-setup` |
| BigQuery | Servis hesabı, erişim listeli veri kümesi | Proje IAM politikası (jobUser) okuma-değiştirme-yazma ister; paket dokunmaz | `bigquery-setup` |
| Azure Data Factory | Bağlı hizmet, veri kümesi, pipeline, tetikleyici oluşturma | Tetikleyici başlatılmaz, veri taşınmaz | `adf-solution` |
| Airflow | İçe aktarma hataları, DAG ve görev listesi, unpause | REST API DAG dosyası yüklemez; dosya Git/CI ile konur | `airflow-activate` |

Uygulanmayanlar: Superset’in içe aktarma uç noktası oturum çerezi ve CSRF belirteci ister; Tableau Cloud’a yayımlanan çalışma kitabı yerel CSV’ye bağlanamaz
(çıkarım veya yayımlanmış veri kaynağı gerekir). İkisi için yerel bloklar dosya üretmeye devam eder. Paketler yerel sözleşme sunucusuyla test edildi; canlı hesapta
denenmedi.

Kaynaklar: [Qlik data files](https://qlik.dev/manage/data-files/create-datafiles/), [Qlik load script REST](https://community.qlik.com/t5/Integration-Extension-APIs/Qlik-Cloud-Change-Load-script-through-REST-API/m-p/2423987),
[Fabric lakehouse API](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-api), [Fabric semantic model oluşturma](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model),
[Fabric rapor oluşturma tartışması](https://community.fabric.microsoft.com/t5/Developer/Fabric-Create-Reports-model-via-Microsoft-Fabric-API/m-p/4719497),
[Metabase API anahtarları](https://www.metabase.com/docs/latest/people-and-groups/api-keys), [Metabase kart yerleşimi](https://discourse.metabase.com/t/how-to-add-dashboard-tabs-programmatically-in-metabase-0-49-which-endpoints-to-use/255084),
[Looker create_dashboard_element](https://cloud.google.com/looker/docs/reference/looker-api/4.0/25.14/methods/Dashboard/create_dashboard_element),
[Unity Catalog yetki güncelleme](https://docs.databricks.com/api/uc-grants/v1/update-permissions), [Airflow API istemcisi](https://apache.googlesource.com/airflow-client-python).

## Dördüncü tur: API ile kurulum dışı işler

Üçüncü turdaki paketler yalnızca yerel kurulum çıktısını hesaba gönderiyordu. Bu turda API’nin izin verdiği üç iş grubu daha eklendi (33 paket):

| Grup | Program ve paket | API’nin sınırı / paketin tavrı |
|---|---|---|
| Yeni platform kurulumu | Confluent Cloud `confluent-topics`, dbt Cloud `dbt-cloud-job`, Databricks `databricks-job-create`, Prefect `prefect-deployment`, Dagster `dagster-schedule`, Airbyte `airbyte-connection`, Fivetran `fivetran-schema` | Dagster tanımları kodda olduğu için API yalnızca doğrulayıp başlatır; Prefect aynı adlı deployment’ı güncellediğinden önce adın boş olduğu denetlenir; Airbyte/Fivetran kaynak kimlik bilgileri pakete girmez |
| Katalog ve kalite | Purview `purview-glossary`, OpenMetadata `openmetadata-glossary`, DataHub `datahub-glossary`, Monte Carlo `montecarlo-rules`, Soda Cloud `soda-scan` | Purview hiyerarşisi API’de belgeli alanla verilemediği için üst terim `isA` ilişkisiyle bağlanır; sorumlular yalnızca Entra nesne kimliğiyle eklenir. Soda Cloud API kontrol tanımı yazmaz, yalnızca tanımlı taramayı çalıştırır |
| Ürün analitiği ve vektör | GA4 `ga4-admin`, Pinecone `pinecone-index`, Qdrant `qdrant-collection` | GA4 özel tanımları silinemez, yalnızca arşivlenir; kota yazmadan önce denetlenir. BigQuery bağlantısı v1alpha uç noktasındadır |
| Canlı denetim | Power BI `powerbi-scan`, Snowflake `snowflake-usage`, Databricks `databricks-cost`, Qlik `qlik-reload-history`, Tableau `tableau-usage`, Airflow `airflow-history`, ADF `adf-history` | Yalnızca okur; Scanner çıktısı yerel `pbi-tarayici-envanteri` bloğunun girdisidir. Databricks tutarları liste fiyatıdır |
| Bakım ve yayın | Power BI `powerbi-deploy`, `powerbi-refresh-schedule`, `powerbi-rls-members`, `powerbi-workspace-access`; Qlik `qlik-publish`, `qlik-reload-schedule`, `qlik-space-access`; Tableau `tableau-permissions`, `tableau-extract-schedule`; Snowflake `snowflake-grant-sync`; Unity Catalog `uc-grant-sync` | Power BI REST API RLS üyeliği vermez; paket Fabric tanım API’siyle TMDL rol dosyasına üye ekler (canlı modelde önce test alanında denenmeli). Erişim paketleri yalnızca ekler; farklı yetki ve fazla hak raporlanır, geri alınmaz |

Bütün paketler yerel sözleşme sunucusuyla test edildi; canlı hesapta denenmedi.

Kaynaklar: [Confluent Topic v3](https://docs.confluent.io/cloud/current/api.html#tag/Topic-(v3)), [Schema Registry API](https://docs.confluent.io/platform/current/schema-registry/develop/api.html),
[dbt Cloud API v2](https://docs.getdbt.com/dbt-cloud/api-v2), [Databricks jobs/create](https://docs.databricks.com/api/workspace/jobs/create), [Prefect deployments](https://docs.prefect.io/v3/api-ref/rest-api/server/deployments/create-deployment),
[Dagster GraphQL](https://docs.dagster.io/guides/operate/graphql), [Airbyte createConnection](https://reference.airbyte.com/reference/createconnection), [Fivetran şema yapılandırması](https://fivetran.com/docs/rest-api/api-reference/connection-schema/connection-schema-config),
[Purview Create Term](https://learn.microsoft.com/en-us/rest/api/purview/datamapdataplane/glossary/create-term), [Soda Cloud scans](https://docs.soda.io/soda-cloud-api-v1/scans),
[GA4 Admin API](https://developers.google.com/analytics/devguides/config/admin/v1/rest), [Pinecone create_index](https://docs.pinecone.io/reference/api/2025-04/control-plane/create_index), [Qdrant collections](https://api.qdrant.tech/api-reference/collections/create-collection),
[Power BI Scanner](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/workspace-info-post-workspace-info), [Power BI deployAll](https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/deploy-all),
[Fabric updateDefinition](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/update-semantic-model-definition), [TMDL](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview),
[Qlik reload-tasks](https://qlik.dev/apis/rest/reload-tasks/), [Tableau yetkiler](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_permissions.htm), [Databricks billing tabloları](https://docs.databricks.com/aws/en/admin/system-tables/billing).

## Kaynaklar

- Power BI sağlık kontrolü ilanları: [Protiviti](https://appsource.microsoft.com/en/marketplace/consulting-services/roberthalf_protiviti.powerbiperformance), [Sword](https://marketplace.microsoft.com/ja-jp/marketplace/consulting-services/swordgroup1676561891488.powerbi_healthcheck_3wk), [Coeo](https://coeo.com/wp-content/uploads/2024/09/Power-BI-Health-Check-.pdf), [endjin araç özeti](https://endjin.com/blog/performance-optimisation-tools-for-power-bi)
- Power BI yönetişim: [Unused artifacts API](https://powerbi.microsoft.com/ar-sa/blog/power-bi-unused-artifacts-api-preview)
- Tableau: [Atrium](https://atrium.ai/services/salesforce-consulting/tableau/server-health-check/), [InterWorks Platform 360](https://community.curator.interworks.com/platform360), [Resource Monitoring Tool](https://help.tableau.com/current/server/en-us/rmt-content-pages.htm), [MAQ Tableau→Power BI değerlendirmesi](https://marketplace.microsoft.com/en-us/product/maqsoftware.tableau_to_microsoft_powerbi_migration_assessment)
- Qlik: [uygulama performansı ve sentetik anahtarlar](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/Apps/app-performance.htm)
- Looker: [kullanılmayan alanları bulma](https://developers.looker.com/api/tutorials/finding-unused-fields), [Henry](https://pypi.org/project/henry), [Looker health tools](https://discuss.google.dev/t/introducing-looker-health-tools-in-mcp-toolbox/283239)
- Snowflake: [Intricity health check](https://intricity.com/services/snowflake-health-check), [Crayon](https://aws.amazon.com/marketplace/pp/prodview-udqpmpxjxyha4), [Operational excellence](https://www.snowflake.com/en/developers/guides/operational-excellence/)
- Teradata: [Datafold rehberi](https://datafold.com/resources/teradata-to-snowflake-migration), [SnowConvert](https://mobilize.net/products/database-migrations/snowconvert/teradata-get-started), [Aegis](https://www.aegissofttech.com/snowflake-services/migration/teradata-to-snowflake)
- Databricks: [UCX](https://docs.databricks.com/aws/data-governance/unity-catalog/ucx), [UCX blog](https://databricks.com/blog/automating-unity-catalog-upgrade-workflows-ucx), [maliyet en iyi uygulamaları](https://docs.databricks.com/aws/lakehouse-architecture/cost-optimization/best-practices)
- dbt: [dbt_project_evaluator](https://github.com/dbt-labs/dbt-project-evaluator), [Datatonic dbt health check](https://datatonic.com/partners/dbt/dbt-health-check-americas/)
- Fabric: [throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling), [Metrics app compute](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)
- Fivetran: [kullanımı izleme ve azaltma](https://fivetran.com/docs/core-concepts/usage-based-pricing/tracking-and-optimizing-usage)
- Informatica: [CTI PowerCenter→IDMC](https://ctidata.com/wp-content/uploads/2023/04/Informatica-PowerCenter-to-IDMC-Migration.pdf), [Informatica upgrade readiness](https://success.informatica.com/success-accelerators/cdipc--platform-compatibility---upgrade-readiness.html)
- ADF/SSIS: [SSIS geçişi](https://learn.microsoft.com/en-us/azure/data-factory/scenario-ssis-migration-overview), [ADF→Fabric değerlendirmesi](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Assessing-Your-Azure-Data-Factory-for-Migration-to-Fabric-Data/ba-p/5172414)
- SQL Server: [Denny Cherry 3 günlük sağlık kontrolü](https://azuremarketplace.microsoft.com/en-us/marketplace/consulting-services/dennycherry_associates_consulting.sql_health_check_nq)
- Oracle: [Oracle Healthcheck örneği](https://www.oracle.com/oce/dc/assets/CONT652A3C4564DA4F888292CAEB5A523BA0/native/oracle-healtcheck-service-example.pdf), [IBM AWR rehberi](https://www.ibm.com/downloads/cas/WNK5D2QZ)
- PostgreSQL: [postgres.ai checkup](https://v2.postgres.ai/docs/monitoring/checkup), [pg-healthcheck](https://www.cybrosys.com/research-and-development/postgres/how-to-use-the-pg-healthcheck-tool-in-postgresql)
- MongoDB: [yavaş sorgular](https://www.mongodb.com/docs/v8.2/troubleshooting/slow-queries-in-production/), [performans kontrol listesi](https://delbridge.solutions/mongodb-performance-checklist)
- Veri kalitesi: [Soda ve GX karşılaştırması](https://www.dataexpert.io/blog/soda-vs-great-expectations-data-quality-tools)
- Yönetişim: [Purview ve Collibra deneyimi](https://dzone.com/articles/data-catalog-azure-purview-collibra-lessons)
- MDM: [Informatica→Reltio geçiş planı](https://digihub.mastechdigital.com/assets/Reltio/ReltiLabs/Platform%20Migration/RELTIO_Informatica_MDM_to_Reltio_Migration.docx)
- MLOps: [Blackthorn çerçevesi](https://blackthorn-vision.com/blog/mlops-consulting-services/)
- GA4: [BigQuery dışa aktarım denetimi](https://www.optizent.com/?p=7863), [GA4 denetim listesi](https://piotrlitwa.com/articles/en/ga4-audit-checklist.html)
- SAP: [veri geçişi ilanı](https://builtin.com/job/sap-data-migration-consultant/4747735)
- SAS: [RiskSpan](https://riskspan.com/sas-to-python-migration/), [Hexaware değerlendirmesi](https://marketplace.microsoft.com/de-lu/product/hexawaretechnologiesltd-nj1734355056208.modernize-sas-to-pyspark-assessment)
- Alteryx: [dbt ile yeniden yazım](https://docs.getdbt.com/blog/framework-refactor-alteryx-dbt), [Lakebridge Alteryx analizi](https://community.databricks.com/t5/community-articles/stop-translating-alteryx-boxes-a-lakebridge-assisted-test-driven/td-p/163424)
- Kafka: [tüketici gecikmesi](https://redpanda.com/guides/kafka-performance-kafka-consumer-lag)
- Airflow: [airflow-diagnostics](https://pypi.org/project/airflow-diagnostics-mcp/)
- Business Central: [configuration packages](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/administration/apply-company-configuration-packages)
