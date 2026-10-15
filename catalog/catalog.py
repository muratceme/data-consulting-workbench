"""Full taxonomy. Legacy CSV templates remain for historical engine tests only."""
DOMAINS = [
 ('bi','Business Intelligence','Panolar, KPI’lar ve yönetim raporları','◫',[
 ('Qlik',['Qlik Cloud','Qlik Sense Enterprise','QlikView']),('Microsoft Power BI',['Power BI Service','Power BI Desktop']),('Tableau',['Tableau Cloud','Tableau Server','Tableau Desktop']),('Looker',['Looker','Looker Studio']),('SAP',['SAP BusinessObjects','SAP Analytics Cloud']),('Oracle',['Oracle Analytics Cloud']),('MicroStrategy',['MicroStrategy ONE']),('Apache Superset',['Superset']),('Metabase',['Metabase']),('Genel',['Rapor gereksinimleri'])], 'report','Otomatik rapor hazırlama','KPI ve dashboard tasarımı'),
 ('erp','ERP & İş Uygulamaları','Finans, operasyon ve ana veri uzlaştırma','▦',[
 ('SAP',['S/4HANA','ECC','Business One']),('Logo',['Tiger 3','GO 3','Netsis','j-Platform']),('Oracle',['Fusion Cloud ERP','E-Business Suite']),('Microsoft',['Dynamics 365 Finance','Business Central']),('Odoo',['Odoo']),('IFS',['IFS Cloud']),('Workday',['Workday'])], 'reconcile','Kaynak ve hedef veri mutabakatı','ERP veri dönüşümü ve eşleme planı'),
 ('warehouse','Veri Ambarı','Analitik depolama, modeller ve sorgular','▤',[
 ('Google',['BigQuery']),('Snowflake',['Snowflake']),('Microsoft',['Fabric Warehouse','Azure Synapse']),('Amazon',['Redshift']),('Oracle',['Autonomous Data Warehouse']),('Teradata',['Vantage']),('ClickHouse',['ClickHouse'])], 'schema','Şema değişikliği karşılaştırma','Boyutsal model ve veri ambarı tasarımı'),
 ('lakehouse','Data Lake & Lakehouse','Açık tablo biçimleri ve veri katmanları','◈',[
 ('Databricks',['Databricks Lakehouse','Unity Catalog']),('Microsoft',['Fabric Lakehouse','OneLake']),('Amazon',['S3 / Lake Formation']),('Google',['Cloud Storage / BigLake']),('Apache',['Iceberg','Hudi']),('Delta Lake',['Delta Lake']),('Dremio',['Dremio'])], 'schema','Katmanlar arası şema kontrolü','Bronze / Silver / Gold mimarisi'),
 ('integration','Veri Entegrasyonu','ETL, ELT, aktarım ve eşleme','⇄',[
 ('Microsoft',['Azure Data Factory','SSIS','Fabric Data Factory']),('Informatica',['IDMC','PowerCenter']),('Qlik',['Talend Cloud','Qlik Replicate']),('Fivetran',['Fivetran']),('Airbyte',['Airbyte']),('SAP',['Data Services']),('Oracle',['Data Integrator','GoldenGate']),('Meltano',['Meltano'])], 'reconcile','Aktarım sonrası satır mutabakatı','Kaynak-hedef eşleme ve ETL planı'),
 ('transform','Analytics Engineering','Dönüşüm, test ve semantik katman','⌘',[
 ('dbt',['dbt Core','dbt Cloud']),('Google',['Dataform']),('SQLMesh',['SQLMesh']),('Coalesce',['Coalesce']),('Cube',['Cube'])], 'sql','SQL model envanteri','Dönüşüm modeli ve test stratejisi'),
 ('orchestration','Orkestrasyon & DataOps','Pipeline işletimi ve çalışma takibi','↗',[
 ('Apache',['Airflow']),('Dagster',['Dagster']),('Prefect',['Prefect']),('Astronomer',['Astro']),('Kestra',['Kestra']),('GitHub',['Actions']),('Jenkins',['Jenkins'])], 'report','Pipeline metrik raporu','Pipeline hata kök neden analizi'),
 ('stream','Streaming & Gerçek Zaman','Olay verisi ve akış mimarileri','≋',[
 ('Apache',['Kafka','Flink','Spark Streaming']),('Confluent',['Confluent Cloud']),('Amazon',['Kinesis']),('Microsoft',['Event Hubs']),('Google',['Pub/Sub','Dataflow']),('Redpanda',['Redpanda'])], 'quality','Olay dışa aktarımı kalite kontrolü','Olay şeması ve akış mimarisi'),
 ('database','Veritabanı Yönetimi','İlişkisel, NoSQL ve operasyonel veri','▱',[
 ('PostgreSQL',['PostgreSQL']),('Microsoft',['SQL Server','Azure SQL']),('Oracle',['Oracle Database']),('MySQL',['MySQL']),('MongoDB',['Atlas']),('Redis',['Redis']),('Couchbase',['Capella']),('Amazon',['DynamoDB']),('Neo4j',['Neo4j'])], 'schema','Tablo şeması değişiklik raporu','İndeks ve sorgu iyileştirme incelemesi'),
 ('quality','Veri Kalitesi','Tamlık, tutarlılık ve doğruluk','✓',[
 ('Great Expectations',['GX']),('Soda',['Soda Core','Soda Cloud']),('AWS',['Deequ']),('Informatica',['Cloud Data Quality']),('Qlik',['Talend Data Quality']),('Ataccama',['ONE'])], 'quality','Veri kalite kural kontrolü','Kalite sorunu kök neden analizi'),
 ('governance','Yönetişim & Veri Kataloğu','Sahiplik, sözlük ve veri keşfi','◎',[
 ('Microsoft',['Purview']),('Collibra',['Data Intelligence Cloud']),('Alation',['Data Catalog']),('Google',['Dataplex']),('Atlan',['Atlan']),('OpenMetadata',['OpenMetadata']),('DataHub',['DataHub'])], 'dictionary','Veri sözlüğü oluşturma','Veri sahipliği ve yönetişim modeli'),
 ('mdm','Master Data Management','Müşteri, ürün ve tedarikçi ana verisi','◇',[
 ('SAP',['Master Data Governance']),('Informatica',['MDM SaaS']),('Semarchy',['xDM']),('Reltio',['Reltio']),('TIBCO',['EBX']),('Stibo Systems',['STEP'])], 'dedupe','Ana veri mükerrer kayıt tespiti','Golden record ve eşleştirme stratejisi'),
 ('privacy','Gizlilik & Veri Güvenliği','Hassas veri, erişim ve maskeleme','⊡',[
 ('Microsoft',['Purview Information Protection']),('OneTrust',['Data Discovery']),('BigID',['BigID']),('Immuta',['Immuta']),('Privacera',['Privacera']),('IBM',['Guardium'])], 'mask','Belirlenen alanları maskeleme','Erişim ve veri sınıflandırma tasarımı'),
 ('observability','Veri Gözlemlenebilirliği','Freshness, hacim ve veri olayları','⌁',[
 ('Monte Carlo',['Monte Carlo']),('Bigeye',['Bigeye']),('Datadog',['Data Streams Monitoring']),('Acceldata',['Acceldata']),('Elementary',['Elementary'])], 'quality','Veri hacmi ve eksik değer raporu','Veri olayı incelemesi ve aksiyon planı'),
 ('science','Veri Bilimi & İstatistik','Keşif, deneyler ve hipotezler','∑',[
 ('Python',['pandas','SciPy','Jupyter']),('R',['R / Posit']),('SAS',['Viya']),('IBM',['SPSS']),('KNIME',['Analytics Platform']),('Alteryx',['Designer'])], 'profile','Keşifsel veri profilleme','Hipotez ve analiz yöntemi tasarımı'),
 ('ml','Machine Learning & MLOps','Model geliştirme, değerlendirme ve işletim','⟁',[
 ('scikit-learn',['scikit-learn']),('PyTorch',['PyTorch']),('TensorFlow',['TensorFlow']),('MLflow',['MLflow']),('Google',['Vertex AI']),('Amazon',['SageMaker']),('Microsoft',['Azure Machine Learning']),('DataRobot',['DataRobot'])], 'profile','Eğitim verisi profil raporu','Model değerlendirme ve drift stratejisi'),
 ('ai','GenAI & Vektör Veri','RAG, doküman verisi ve değerlendirme','✳',[
 ('OpenAI',['API']),('Anthropic',['Claude API']),('LangChain',['LangChain','LangGraph']),('LlamaIndex',['LlamaIndex']),('Pinecone',['Pinecone']),('Qdrant',['Qdrant']),('Weaviate',['Weaviate']),('Milvus',['Milvus']),('PostgreSQL',['pgvector'])], 'dedupe','RAG veri kümesi mükerrer kontrolü','RAG mimarisi ve değerlendirme tasarımı'),
 ('product','Ürün & Pazarlama Analitiği','Davranış, funnel ve ölçümleme','◔',[
 ('Google',['GA4']),('Adobe',['Analytics']),('Amplitude',['Amplitude']),('Mixpanel',['Mixpanel']),('Segment',['Segment']),('Salesforce',['Data Cloud']),('HubSpot',['CRM'])], 'report','Dışa aktarılan etkinlik raporu','Funnel ve ölçümleme planı'),
 ('spatial','Coğrafi & IoT Verisi','Konum, sensör ve zaman serileri','⌖',[
 ('Esri',['ArcGIS']),('QGIS',['QGIS']),('PostgreSQL',['PostGIS','TimescaleDB']),('InfluxData',['InfluxDB']),('Azure',['IoT Hub']),('Amazon',['IoT Core'])], 'quality','Sensör ve konum alanı kontrolü','Zaman serisi ve konum analizi planı'),
 ('planning','Kurumsal Planlama & EPM','Bütçe, tahmin ve finansal konsolidasyon','▥',[
 ('SAP',['Analytics Cloud Planning']),('Oracle',['Cloud EPM']),('Anaplan',['Anaplan']),('IBM',['Planning Analytics']),('Workday',['Adaptive Planning']),('Board',['Board'])], 'reconcile','Bütçe ve gerçekleşen mutabakatı','Tahmin modeli ve senaryo tasarımı'),
 ('migration','Veri Migrasyonu','Keşif, eşleme ve geçiş doğrulaması','→',[
 ('AWS',['Database Migration Service']),('Microsoft',['Azure Database Migration']),('Google',['Database Migration Service']),('SAP',['Migration Cockpit']),('Oracle',['Zero Downtime Migration'])], 'reconcile','Geçiş öncesi ve sonrası mutabakat','Migrasyon dalgası ve cutover planı'),
 ('finops','Data FinOps & Performans','Kullanım, maliyet ve kapasite','↘',[
 ('Google',['BigQuery Billing Export']),('Snowflake',['Account Usage']),('Databricks',['System Tables']),('Microsoft',['Fabric Capacity Metrics']),('Amazon',['Cost and Usage Report'])], 'report','Kullanım ve maliyet özet raporu','Maliyet azaltma ve kapasite planı'),
 ('files','Dosya & Self-Service Veri','Elektronik tablolar ve açık veri','▧',[
 ('Microsoft',['Excel','Power Query']),('Google',['Sheets']),('DuckDB',['DuckDB']),('OpenRefine',['OpenRefine']),('Apache',['Arrow / Parquet']),('Genel',['CSV / Excel','SQL dosyaları'])], 'report','CSV verisinden otomatik HTML rapor','Self-service veri hazırlama planı')
]

ENGINES = {
 'profile':('Veri profilleme','Sütun tipleri, boş değerler, sayısal özetler ve benzersizlik.'),
 'quality':('Veri kalite kontrolü','Zorunlu alan, tür, aralık ve benzersiz anahtar kontrolleri.'),
 'report':('Otomatik rapor hazırlama','Sayısal alanları toplar; seçilen kırılımla HTML ve JSON üretir.'),
 'schema':('Şema karşılaştırma','İki CSV’nin alanlarını ve çıkarılan türlerini karşılaştırır.'),
 'reconcile':('Veri mutabakatı','İki CSV’yi anahtara göre karşılaştırır; eksik, fazla ve değişen kayıtları bulur.'),
 'dictionary':('Veri sözlüğü','CSV alanları için tür, tamlık ve kardinalite içeren Markdown sözlük üretir.'),
 'dedupe':('Mükerrer kayıt kontrolü','Seçilen anahtar alanlarda tekrarlanan kayıtları listeler.'),
 'mask':('Alan maskeleme','Konfigürasyonda seçilen alanları sabit metinle değiştirir.'),
 'sql':('SQL model envanteri','SQL dosyalarındaki tablo başvurularını kaba regex ile çıkarır; SQL parser değildir.')
}

ADVISORY = {
 'bi':('Semantik model ve ölçü sözleşmesi','Dashboard performans incelemesi','KPI tanımı, grain, boyutlar, ölçü formülü, filtre davranışı ve kullanıcı kabul kriterleri; veri kanıtı olmadan KPI değeri uydurma.'),
 'erp':('Cari ve ürün ana veri eşlemesi','Finansal veri dönüşüm kontrol planı','Şirket kodu, para birimi, muhasebe dönemi, ana veri anahtarı ve mutabakat toleransı; platforma yazmadan eşleme ve kontrol teslimi.'),
 'warehouse':('SCD ve artımlı yükleme stratejisi','Partition ve sorgu performans incelemesi','Fact grain, doğal ve surrogate anahtar, SCD tipi, geç gelen veri, partition ve maliyet ölçüm planı.'),
 'lakehouse':('Tablo biçimi ve katman seçimi','Veri saklama ve compaction planı','Şema evrimi, ACID ihtiyacı, tablo formatı, katman sözleşmesi, retention ve işletim maliyeti.'),
 'integration':('CDC ve artımlı aktarım tasarımı','Hata ve yeniden çalıştırma stratejisi','Watermark, CDC, idempotency, yeniden deneme, schema drift, kaynak yükü ve hedef kontrolü.'),
 'transform':('Model bağımlılığı ve test tasarımı','Semantik katman sözleşmesi','Model grain, unique/not-null/referential testler, lineage varsayımları ve metrik sahipliği.'),
 'orchestration':('SLA ve yeniden deneme planı','Pipeline bağımlılık tasarımı','DAG bağımlılıkları, timeout, retry, idempotency, backfill, çalışma penceresi ve uyarı sahibi.'),
 'stream':('Event-time ve geç veri stratejisi','Exactly-once ihtiyacı değerlendirmesi','Olay anahtarı, event-time, watermark, window, replay, schema compatibility ve teslim semantiği.'),
 'database':('Yedekleme ve geri dönüş tasarımı','Kapasite ve indeks stratejisi','Sorgu planı kanıtı, workload, indeks maliyeti, kilitlenme, RPO/RTO, restore testi; DDL yürütme yok.'),
 'quality':('Veri kalite kural seti tasarımı','Kalite SLA ve sahiplik planı','Ölçülebilir kalite boyutu, iş kuralı, eşik, örneklem sınırlaması, hata sahibi ve remediation adımı.'),
 'governance':('İş sözlüğü ve terim sözleşmesi','Lineage ve sorumluluk incelemesi','İş terimi, veri sahibi, steward, sınıflandırma, lineage kanıtı, erişim ilkesi ve onay süreci.'),
 'mdm':('Müşteri ve tedarikçi eşleşme incelemesi','Survivorship kural tasarımı','Eşleme anahtarları, normalize etme, confidence eşiği, false merge riski ve insan onaylı survivorship.'),
 'privacy':('Hassas alan sınıflandırma incelemesi','Maskeleme ve saklama stratejisi','Kullanım amacı, minimum erişim, hassas veri sınıfı, saklama süresi, maskeleme sınırlaması; hukuki uygunluk kararı verme.'),
 'observability':('Freshness ve hacim alarm tasarımı','Veri olayı müdahale runbook’u','Baseline, değişim penceresi, freshness SLA, false alarm maliyeti, root cause kanıtı ve incident runbook.'),
 'science':('Deney ve A/B test tasarımı','Veri sızıntısı ve örneklem incelemesi','Hedef değişken, hipotez, örneklem, confounder, etki büyüklüğü ve test varsayımları; nedensellik iddiasını kanıtla.'),
 'ml':('Feature ve veri sızıntısı incelemesi','Model izleme ve yeniden eğitim planı','Train/test ayrımı, leakage, veri/konsept drift, baseline, metrik seçimi ve yeniden eğitim kabul kriteri.'),
 'ai':('Chunking ve retrieval stratejisi','RAG değerlendirme veri seti tasarımı','Kaynak izinleri, chunk boyutu, embedding, retrieval metrikleri, grounding, prompt injection ve cevap değerlendirme rubric’i.'),
 'product':('Tracking plan ve event taksonomisi','Attribution ve A/B test incelemesi','Olay ve kullanıcı tanımı, consent bağlamı, session, funnel adımları, attribution varsayımları ve deney metrikleri.'),
 'spatial':('Koordinat ve zaman serisi incelemesi','Sensör anomali stratejisi','CRS, koordinat sınırı, zaman dilimi, sensör kalibrasyonu, resampling, missing interval ve anomali kanıtı.'),
 'planning':('Bütçe senaryosu ve varsayım incelemesi','Konsolidasyon eşleme tasarımı','Hesap, şirket, dönem, kur, senaryo, versiyon, varsayım, elimination ve kabul edilen tolerans.'),
 'migration':('Kaynak-hedef alan eşleme tasarımı','Rollback ve cutover runbook’u','Veri hacmi, dönüşüm eşlemesi, anahtar, geçiş dalgası, freeze, rollback, reconciliation ve iş kabul kriteri.'),
 'finops':('Sorgu maliyeti optimizasyon incelemesi','Kapasite ve kullanım tahmini','Faturalama export kanıtı, fiyat varsayımları, workload, partition, saklama, birim maliyet; tasarruf garantisi verme.'),
 'files':('Veri hazırlama adımları tasarımı','Spreadsheet hata ve kontrol incelemesi','Alan tipleri, ayırıcı, locale, tarih biçimi, formül bağımlılıkları ve tekrarlanabilir hazırlama adımları.')
}

def legacy_catalog():
 result=[]
 for did,name,desc,icon,tools,engine,title,agent_title in DOMAINS:
  domain={'id':did,'name':name,'description':desc,'icon':icon,'tools':[]}
  for ti,(vendor,products) in enumerate(tools):
   tool={'id':f'{did}-{ti}','name':vendor,'products':[]}
   for pi,product in enumerate(products):
    base=f'{did}-{ti}-{pi}'
    tasks=[]
    for operation,task_title in [(engine,title),('profile','Veri keşfi ve profil raporu'),('quality','Dışa aktarım kalite kontrolü')]:
     if any(t['engine']==operation for t in tasks): continue
     tasks.append({'id':f'{base}-{operation}','title':task_title,'kind':'code','engine':operation,'description':ENGINES[operation][1],'reason':'Girdi ve kurallar açık; sonuç deterministik olarak hesaplanır.','input':'SQL klasörü' if operation=='sql' else 'CSV dışa aktarımı','execution':'Yerel dosya','status':'ready'})
    for ai,atitle in enumerate([agent_title,*ADVISORY[did][:2]]):
     tasks.append({'id':f'{base}-agent-{ai}','title':atitle,'kind':'agent','engine':'agent','brief':ADVISORY[did][2],'description':f'{product} bağlamında kanıta dayalı öneri, eksik bilgi ve danışmanlık teslimleri üretir.','reason':'Bağlam, belirsizlik, alternatifler ve uzman değerlendirmesi gerektirir.','input':'Profil raporu + proje bağlamı','execution':'LLM API / Ollama','status':'ready'})
    tool['products'].append({'id':base,'name':product,'tasks':tasks})
   domain['tools'].append(tool)
  result.append(domain)
 return result


def catalog():
 from native_catalog import catalog as native_catalog
 return native_catalog(DOMAINS)
