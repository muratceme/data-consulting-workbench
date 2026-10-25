"""Product packages that replace the planned per-product tasks (library/products/<domain>.py).

Every product of a domain gets one code block (the domain's deterministic task, reading from the platform's own
API/SQL interface/driver) and one agent (the domain's design task, using only evidence read from the platform).
An entry's `slot` tells the catalog which planned placeholder it fulfils."""

READ_ONLY = ' Yalnızca okuma yapar; platformda hiçbir şey oluşturmaz, değiştirmez veya silmez.'
AGENT_EFFECT = (' Modele yalnızca platformdan okunan metadata/istatistik gider (ham satır gönderilmez); kişisel veri izleri gönderimden önce maskelenir, '
                'gönderilecek metin output/gonderilecek-istek.json dosyasına yazılır ve onay istenir (--dry-run modeli çağırmaz, --yes onayı atlar). '
                'Model yanıtı JSON olarak okunur; kanıtta olmayan her tablo/kolon/nesne başvurusu reddedilir ve model bir kez düzeltmeye zorlanır. '
                'Üretilen dosyalar taslaktır; hiçbir şey platformda çalıştırılmaz.')
AGENT_REQ = ' Model için .env içinde PROVIDER (openai, anthropic veya ollama) ve MODEL; seçilen sağlayıcının anahtarı.'

DOMAIN = {
    'warehouse': dict(
        code=('{label} şemasını canlı oku ve önceki anlık görüntüyle karşılaştır', 'sema.json (anlık görüntü) + sema_farki.csv + ozet.json',
              'Seçilen şema/veri kümelerindeki tüm tablo ve kolonları platformun katalog görünümlerinden okur ({how}). İlk çalışmada anlık görüntü (sema.json) üretir; '
              'baseline_file ile önceki görüntü verilirse eklenen/kaldırılan tablo ve kolonları, tip ve NULL kuralı değişikliklerini kırıcı değişiklik sayısıyla raporlar.'),
        agent=('{label} şemasından boyutsal model tasarla', 'boyutsal_model.json + boyutsal_model.md + ddl_taslak.sql',
               'Seçilen şemaların kolon tipleri ve satır sayılarını {how} okur; model olgu tablolarını (grain, ölçüler, kaynak kolonlar) ve boyutları (doğal anahtar, SCD tipi) önerir. '
               'Her kaynak tablo/kolon kanıta karşı denetlenir; geçen modelden {label} lehçesinde taslak CREATE TABLE ifadeleri kodla üretilir (çalıştırılmaz).')),
    'database': dict(
        code=('{label} şemasını canlı oku ve değişiklikleri raporla', 'sema.json (anlık görüntü) + sema_farki.csv + ozet.json',
              'Yapıyı {how} okur ve anlık görüntü üretir. baseline_file ile önceki görüntü verilirse eklenen/kaldırılan nesne ve alanları, tip ve NULL/zorunluluk '
              'değişikliklerini kırıcı değişiklik sayısıyla raporlar.'),
        agent=('{label} indeks ve sorgu incelemesi', 'oneriler.json + oneriler.md + degisiklik_taslagi.txt',
               'Yapıyı, mevcut indeksleri ve iş yükü istatistiklerini {how} okur; model indeks ekleme/kaldırma ve sorgu iyileştirme önerir. '
               'Her öneri kanıttaki nesne, kolon ve indeks adlarına karşı denetlenir; geçen önerilerden {label} için taslak değişiklik komutları kodla üretilir (çalıştırılmaz).')),
    'lakehouse': dict(
        code=('{label} katmanları arasında şema tutarlılığını denetle', 'katman_farki.csv + sema.json + ozet.json',
              'Sıralı katmanların (ör. bronze → silver → gold) tablo ve kolonlarını {how} okur. Aynı adlı (veya table_map ile eşlenen) tablolar arasında sonraki katmanda olmayan '
              'tabloları, taşınmayan kolonları, tip değişikliklerini, gevşeyen NOT NULL kurallarını ve türetilmiş kolonları raporlar.'),
        agent=('{label} için Bronze / Silver / Gold mimarisi tasarla', 'medallion_plani.json + medallion_plani.md + akislar.csv',
               'Mevcut katmanların tablo/kolon yapısını {how} okur; model her tabloyu bir katmana atar ve katmanlar arası akışları (kaynak, hedef, artımlı yöntem, anahtar, kalite '
               'kontrolleri) önerir. Kaynak tablolar ve anahtar kolonlar kanıta karşı denetlenir; yeni hedefler açıkça işaretlenmek zorundadır.')),
    'finops': dict(
        code=('{label} verisinden kullanım ve maliyet özeti çıkar', 'maliyet_detay.csv + servis_ozeti.csv + en_pahali_kalemler.csv + gunluk_maliyet.csv + ozet.json',
              'Son N günün faturalama verisini {how} okur; servis, kalem ve kapsam (proje/hesap/kaynak) bazında maliyeti, kredileri, günlük seriyi ve dönemin iki yarısı arasındaki değişimi hesaplar.'),
        agent=('{label} için maliyet azaltma ve kapasite planı', 'tasarruf_plani.json + tasarruf_plani.csv + tasarruf_plani.md',
               'Maliyet dağılımını ve kapasite ayarlarını {how} okur; model hedef kaynak başına aksiyon ve tasarruf oranı önerir. Hedefler kanıta karşı denetlenir; '
               'tasarruf tutarı modelden değil, kanıttaki dönem maliyeti × oran olarak kodla hesaplanır.')),
    'spatial': dict(
        code=('{label} sensör ve konum alanlarını denetle', 'kontrol_sonuclari.csv + kaynak_bilgisi.json + ozet.json',
              'Kayıtları {how} okur ve kodla denetler: eksik/aralık dışı/0,0 koordinatlar, bbox dışı noktalar, geometri sorunları, okunamayan veya gelecek zaman damgaları, '
              'aynı cihaz+zaman tekrarları, beklenen aralıktan uzun veri boşlukları, uzun süredir veri göndermeyen cihazlar ve value_ranges dışındaki ölçümler.'),
        agent=('{label} zaman serisi ve konum analizi planı', 'analiz_plani.json + analiz_plani.md',
               'Alan yapısını ve kontrol bulgularını {how} okur; model analizleri (yöntem, alanlar, pencere, toplama) ve ön işleme adımlarını önerir. Alanlar kanıta karşı '
               'denetlenir; geçen analizler için ürünün sorgu dilinde taslak sorgu kodla üretilir (çalıştırılmaz).')),
    'ai': dict(
        code=('{label} RAG veri kümesinde mükerrer parçaları bul', 'birebir_tekrar.csv + benzer_parcalar.csv + kaynak_bilgisi.json + ozet.json',
              'Parça/dokümanları (metin, metadata ve varsa vektör) {how} okur. Normalize metin özetiyle birebir tekrarları, MinHash LSH + Jaccard ile benzer metinleri ve '
              'rastgele düzlem LSH + kosinüs ile benzer vektörleri bulur; uzunluk dağılımını, boş parçaları ve metadata doluluğunu raporlar. Hiçbir şey silinmez.'),
        agent=('{label} için RAG mimarisi ve değerlendirme tasarımı', 'rag_tasarimi.json + rag_tasarimi.md + degerlendirme_sablonu.jsonl',
               'Depo ayarlarını ve parça istatistiklerini {how} okur; model parçalama, getirme, temizlik ve değerlendirme (metrikler, soru sayısı, kabul eşikleri) önerir. '
               'Metadata filtreleri kanıttaki alanlara, sayılar geçerli aralıklara karşı denetlenir; gerçek parça kimlikleriyle doldurulacak değerlendirme şablonu kodla üretilir.')),
    'bi': dict(
        code=('{label} raporunu platformun kendi motoruyla hazırla', 'rapor dosyası + rapor.json',
              '{how} Paket grafik çizmez veya rakam hesaplamaz; çıktıyı platformun kendisi üretir, dosya türü imzası doğrulanarak indirilir.'),
        agent=('{label} için KPI ve pano tasarla', 'kpi_tasarimi.json + kpi_tasarimi.md',
               'Alan ve ölçü listesini {how} okur; model KPI’ları (alan, toplama, oran böleni, iyi yön, grain) ve sayfa/görsel düzenini önerir. Her alan ve boyut kanıta karşı '
               'denetlenir; KPI ifadeleri modelden değil, doğrulanan parçalardan ürünün diliyle (DAX, Qlik, Tableau, LookML, SQL) kodla üretilir.')),
    'erp': dict(
        code=('{label} verisini hedef dışa aktarımla mutabık et', 'erp_cekimi.csv + mutabakat_farklari.csv + toplam_karsilastirma.csv + mutabakat.json',
              'Seçilen varlığı {how} okur ve hedef dosyayla (ambar/rapor dışa aktarımı) anahtar kolonlarla eşleştirir: yalnız ERP’de / yalnız hedefte olan kayıtlar, '
              'toleransı aşan sayısal farklar, değişen metin alanları ve kolon toplamları. ERP’de hiçbir belge oluşturulmaz veya değiştirilmez.'),
        agent=('{label} için veri dönüşümü ve eşleme planı', 'esleme_plani.json + esleme.csv + esleme_plani.md',
               'Hedef ERP alanlarının tip, zorunluluk ve uzunluk tanımlarını {how} okur, kaynak dosyanın kolon profilini çıkarır; model kaynak → ERP alan eşlemesini ve dönüşüm '
               'kurallarını önerir. Hedef alanlar ve kaynak kolonlar kanıta karşı denetlenir; her zorunlu alan eşlenmeli ya da gerekçelendirilmelidir. Uzunluk riskleri kodla hesaplanır.')),
    'integration': dict(
        code=('{label} aktarımlarında okunan ve yazılan satırları mutabık et', 'satir_mutabakati.csv + ozet.json',
              'Son N günün çalışmalarında aracın kendi sayaçlarını {how} okur; her çalışma/nesne için okunan, yazılan ve atlanan/reddedilen satırları karşılaştırır: '
              'eşit, farkın atlanan satırlarla açıklandığı ve açıklanamayan FARK durumlarını raporlar.'),
        agent=('{label} için kaynak-hedef eşleme ve ETL planı', 'etl_plani.json + etl_plani.md + kolon_eslemeleri.csv',
               'Kaynak ve hedef nesneleri ile kolonlarını {how} okur; model akışları (yöntem, anahtar, watermark, kolon eşlemeleri, sıklık, hata yönetimi) önerir. '
               'Nesne ve kolon adları kanıta karşı denetlenir; artımlı/upsert/CDC/SCD2 yöntemleri anahtar veya watermark olmadan kabul edilmez.')),
    'transform': dict(
        code=('{label} SQL model envanteri ve soy ağacı', 'model_envanteri.csv + soy_agaci.csv + kaynak_bilgisi.json + envanter.json',
              'Modelleri, materyalizasyonlarını, SQL’lerini ve bağımlılıklarını {how} okur; her modelin okuduğu tabloları ve onu kullanan modelleri, dış tablo bağımlılıklarını, '
              'SELECT * kullanımını, açıklaması olmayan ve hiçbir model tarafından kullanılmayan (yaprak) modelleri raporlar.'),
        agent=('{label} için dönüşüm modeli ve test stratejisi', 'test_stratejisi.json + test_stratejisi.md + test_sorgulari.sql (+ ürünün test dosyası)',
               'Modelleri, kolonları, bağımlılıkları ve mevcut testleri {how} okur; model grain/anahtar testleri, ilişki, kabul edilen değer ve ifade testleri önerir. '
               'Model ve kolonlar kanıta karşı denetlenir; geçen testler ürünün kendi sözdizimine (dbt schema.yml, Dataform assertions, SQLMesh audits) ve başarısız satırları döndüren SQL’e kodla çevrilir.')),
    'orchestration': dict(
        code=('{label} pipeline metrik raporu', 'pipeline_metrikleri.csv + calismalar.csv + ozet.json',
              'Son N günün çalışmalarını {how} okur; pipeline başına çalışma, başarılı/başarısız sayısı, başarı oranı, p50/p95/en uzun süre ve son hata zamanını hesaplar.'),
        agent=('{label} pipeline hata kök neden analizi', 'kok_neden.json + kok_neden.csv + kok_neden.md',
               'Başarısız çalışmaları, başarısız görev/adımları ve log sonlarını {how} okur; model her olay için kategori, kök neden, kanıt, düzeltme ve önleme önerir. '
               'Çalışma kimlikleri ve kategoriler kanıta karşı denetlenir; loglar gönderimden önce maskelenir ve son satırlarla sınırlanır.')),
    'stream': dict(
        code=('{label} olay akışında kalite kontrolü', 'kontrol_sonuclari.csv + alan_profili.csv/metrikler.csv + ozet.json',
              '{how} Kontroller: çözümlenemeyen mesajlar, zorunlu alan eksikleri, tekrarlanan anahtar/kimlik, okunamayan veya gelecek olay zamanı, olay ile broker zamanı arasındaki '
              'gecikme, bölüm içi sıra bozukluğu ve alan tipi tutarsızlığı (motorlarda geç atılan kayıt, backpressure, filigran gecikmesi, başarısız checkpoint). Mesaj değerleri kaydedilmez.'),
        agent=('{label} için olay şeması ve akış mimarisi', 'akis_mimarisi.json + akis_mimarisi.md + <Olay>.avsc + konu_ayarlari.json',
               '{how} Model olay şemalarını, anahtar ve uyumluluk kuralını, konu ayarlarını ve akış adımlarını önerir; alanlar örneklem profiline, konular kanıta karşı denetlenir. '
               'Avro şemaları ve konu ayarları doğrulanan JSON’dan kodla üretilir; hiçbir konu veya şema oluşturulmaz.')),
    'quality': dict(
        code=('{label} kalite kurallarını çalıştır ve sonuçlarını raporla', 'kontrol_sonuclari.csv + ozet.json',
              'Kalite kontrollerinin sonuçlarını {how} alır; her kontrol için nesne, kolon, sonuç (geçti/kaldı/uyarı/hata), ölçülen değer, eşik ve ayrıntıyı tek tabloda toplar. '
              'Kural, kontrol veya profil tanımı oluşturulmaz ya da değiştirilmez.'),
        agent=('{label} kalite sorunu kök neden analizi', 'kok_neden.json + kok_neden.csv + kok_neden.md',
               'Başarısız/uyarı veren kontrolleri ve ek kanıtı (eğilim, eksen puanları, sayaçlar) {how} okur; model kök neden kategorisi, kanıt, düzeltme, sahip ve kural önerileri üretir. '
               'Kontrol adları kanıta karşı denetlenir; kuralların kendisi değiştirilmez.')),
    'governance': dict(
        code=('{label} kataloğundan veri sözlüğü oluştur', 'veri_sozlugu.csv + veri_sozlugu.md + eksikler.csv + sozluk.json',
              'Tablo ve kolon varlıklarını; açıklama, sahip, etiket/sınıflandırma ve sözlük terimleriyle {how} okur. Tablo başına Markdown sözlük ve açıklaması ya da sahibi eksik varlık listesi üretir.'),
        agent=('{label} için veri sahipliği ve yönetişim modeli', 'yonetisim_modeli.json + sahiplik_atamalari.csv + yonetisim_modeli.md',
               'Varlıkları, mevcut sahipleri, etiketleri ve terimleri {how} okur; model iş alanlarını, varlık başına sahip/steward rolünü, sınıflandırmayı ve politikaları önerir. '
               'Varlıklar kataloğa, önerilen kişiler yalnızca katalogdaki mevcut sahiplere karşı denetlenir; katalogda hiçbir şey değiştirilmez.')),
    'mdm': dict(
        code=('{label} ana verisinde mükerrer kayıtları bul', 'birebir_mukerrer.csv + benzer_adaylar.csv + kaynak_bilgisi.json + mukerrer.json',
              'Ana veri kayıtlarını {how} okur; seçilen eşleşme alanlarında (Türkçe karakter, büyük/küçük harf ve noktalama normalize edilerek) birebir tekrar gruplarını ve '
              'benzerlik eşiğini geçen aday çiftleri bulur. Platformda birleştirme yapılmaz; adaylar insan onayı içindir.'),
        agent=('{label} için golden record ve eşleştirme stratejisi', 'eslestirme_stratejisi.json + eslestirme_stratejisi.md',
               'Kayıtların alan profilini ve alan bazlı tekrar denemelerini {how} çıkarır; model eşleşme kuralları, eşikler, otomatik/inceleme aksiyonları ve hayatta kalma kuralları önerir. '
               'Alanlar kanıta karşı denetlenir, benzerlikle otomatik birleştirme 0,95 altı eşikte reddedilir ve her kuralın etkisi aynı kayıtlar üzerinde kodla ölçülür.')),
    'privacy': dict(
        code=('{label} sınıflandırmasına göre alanları maskele', 'maskelenmis.csv + maskelenen_kolonlar.csv + siniflandirma_kaynagi.json + maskeleme.json',
              'Hassas olarak sınıflandırılmış kolonları {how} alır ve yerel dışa aktarım dosyasında bu kolonları (ve extra_fields ile seçtiklerinizi) tuzlu HMAC-SHA256, kısmi, '
              'gizleme veya boşaltma yöntemiyle maskeler. Kaynak dosya değiştirilmez; platformda politika oluşturulmaz.'),
        agent=('{label} için erişim ve veri sınıflandırma tasarımı', 'erisim_tasarimi.json + kolon_siniflari.csv + erisim_tasarimi.md',
               'Kolon sınıflandırmalarını {how} okur; model sınıf şemasını, kolon atamalarını, rol bazlı erişim/maskeleme kurallarını ve saklama sürelerini önerir. '
               'Kolonlar kanıta, sınıf ve maske türleri izinli listelere karşı denetlenir; Privacera için kapalı (isEnabled: false) Ranger maskeleme politikası taslakları kodla üretilir.')),
    'observability': dict(
        code=('{label} verisinden hacim ve eksik değer raporu', 'hacim_ve_eksik_deger.csv + ozet.json',
              'Hacim, satır sayısı ve null/eksik değer metriklerini ya da olaylarını {how} okur; her seride son değeri önceki noktaların medyanıyla karşılaştırır (sapma, boş yükleme), '
              'eşik üstü null oranlarını ve veri gelmeyen aralıkları işaretler.'),
        agent=('{label} veri olayı incelemesi ve aksiyon planı', 'olay_plani.json + olay_plani.md',
               'Sorunlu metrik ve olayları {how} okur; model olay başına etki, olası neden, kanıt, aksiyonlar, sahip, öncelik ve kapanış kriteri ile runbook önerir. '
               'Referanslar kanıttaki nesne/olay adlarına karşı denetlenir; monitor ve olaylar platformda değiştirilmez.')),
}


def pair(domain, product, key, *, label, how, config, secrets, requirements, sources, agent_config=None, packages=(), code_effects='', agent_effects='', only=('code', 'agent'), outputs=None, agent_how=None, files=None):
    """Two catalog entries (code + agent) for one product of a domain."""
    spec = DOMAIN[domain]
    entries = []
    for slot in only:
        title, output, description = spec[slot]
        output = (outputs or {}).get(slot, output)
        op = f'{key}-{OPS[domain][slot]}'
        cfg = {**config, **(agent_config or {})} if slot == 'agent' else dict(config)
        entries.append(dict(operation=op, product=product, domain=domain, slot=slot, kind=slot, module=domain, packages=list(packages),
                            title=title.format(label=label), output=output, description=description.format(label=label, how=agent_how if slot == 'agent' and agent_how else how),
                            config=cfg, secrets=list(secrets), requirements=requirements + (AGENT_REQ if slot == 'agent' else ''),
                            effects=(code_effects + READ_ONLY) if slot == 'code' else (agent_effects + AGENT_EFFECT), sources=list(sources),
                            **({'input_files': dict(files)} if files else {})))
    return entries


OPS = {'warehouse': {'code': 'schema-diff', 'agent': 'dimensional-model'}, 'database': {'code': 'schema-diff', 'agent': 'index-review'},
       'lakehouse': {'code': 'layer-check', 'agent': 'medallion'}, 'finops': {'code': 'cost-summary', 'agent': 'savings-plan'},
       'spatial': {'code': 'sensor-check', 'agent': 'analysis-plan'}, 'ai': {'code': 'rag-duplicates', 'agent': 'rag-design'},
       'bi': {'code': 'report-export', 'agent': 'kpi-design'}, 'erp': {'code': 'reconcile', 'agent': 'mapping-plan'},
       'integration': {'code': 'row-reconcile', 'agent': 'etl-plan'}, 'transform': {'code': 'model-inventory', 'agent': 'test-strategy'},
       'orchestration': {'code': 'pipeline-metrics', 'agent': 'root-cause'}, 'stream': {'code': 'event-quality', 'agent': 'event-architecture'},
       'quality': {'code': 'rule-check', 'agent': 'quality-root-cause'}, 'governance': {'code': 'data-dictionary', 'agent': 'ownership-model'},
       'mdm': {'code': 'duplicates', 'agent': 'golden-record'}, 'privacy': {'code': 'mask-fields', 'agent': 'access-design'},
       'observability': {'code': 'volume-report', 'agent': 'incident-plan'}}

GCP_SCOPE = 'Google OAuth erişim belirteci (GOOGLE_ACCESS_TOKEN; ör. `gcloud auth print-access-token`), BigQuery Job User + Metadata Viewer rolleri.'
MSSQL_DRIVER = 'Microsoft ODBC Driver 18 for SQL Server ve pyodbc. Parola odbc dizesine yazılmaz: Entra ID için Authentication=ActiveDirectoryInteractive/ActiveDirectoryServicePrincipal kullanın veya DB_PASSWORD verin.'

PRODUCTS = []
PRODUCTS += pair('warehouse', 'BigQuery', 'bigquery', label='BigQuery', how='bölgesel INFORMATION_SCHEMA.COLUMNS ve TABLE_STORAGE, jobs.query API',
                 config={'project_id': 'YOUR_PROJECT_ID', 'location': 'EU', 'datasets': ['YOUR_DATASET'], 'maximum_bytes_billed': 1073741824,
                         'baseline_file': 'onceki/sema.json'}, agent_config={'target_schema': 'dwh'}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements=GCP_SCOPE + ' INFORMATION_SCHEMA sorguları küçük bir tarama ücreti doğurabilir; maximum_bytes_billed üst sınırdır.',
                 sources=['https://cloud.google.com/bigquery/docs/information-schema-columns', 'https://cloud.google.com/bigquery/docs/information-schema-table-storage',
                          'https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query'])
PRODUCTS += pair('warehouse', 'Snowflake', 'snowflake', label='Snowflake', how='veritabanı INFORMATION_SCHEMA.COLUMNS/TABLES, SQL API v2',
                 config={'server': 'https://YOUR_ACCOUNT.snowflakecomputing.com', 'warehouse': 'COMPUTE_WH', 'role': 'YOUR_READ_ROLE', 'database': 'YOUR_DATABASE',
                         'schemas': ['PUBLIC'], 'timeout_seconds': 120, 'baseline_file': 'onceki/sema.json'}, agent_config={'target_schema': 'DWH'},
                 secrets=['SNOWFLAKE_ACCESS_TOKEN'], requirements='OAuth erişim belirteci; rolün veritabanı/şema USAGE ve tablolarda en az REFERENCES/SELECT hakkı olmalı. Sorgular depo kredisi harcar.',
                 sources=['https://docs.snowflake.com/en/sql-reference/info-schema/columns', 'https://docs.snowflake.com/en/sql-reference/info-schema/tables',
                          'https://docs.snowflake.com/en/developer-guide/sql-api/submitting-requests'])
PRODUCTS += pair('warehouse', 'Fabric Warehouse', 'fabric-warehouse', label='Fabric Warehouse', how='TDS SQL uç noktasında INFORMATION_SCHEMA.COLUMNS, ODBC',
                 config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_ENDPOINT.datawarehouse.fabric.microsoft.com;Database=YOUR_WAREHOUSE;Encrypt=yes;Authentication=ActiveDirectoryInteractive',
                         'schemas': ['dbo'], 'baseline_file': 'onceki/sema.json'}, agent_config={'target_schema': 'dwh'}, secrets=[], packages=['pyodbc>=5'],
                 requirements='Warehouse üzerinde en az Read yetkisi (Viewer rolü). ' + MSSQL_DRIVER,
                 sources=['https://learn.microsoft.com/en-us/fabric/data-warehouse/connectivity', 'https://learn.microsoft.com/en-us/sql/relational-databases/system-information-schema-views/columns-transact-sql'])
PRODUCTS += pair('warehouse', 'Azure Synapse', 'synapse', label='Azure Synapse', how='dedicated/serverless SQL havuzunda INFORMATION_SCHEMA.COLUMNS, ODBC',
                 config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:YOUR_WORKSPACE.sql.azuresynapse.net,1433;Database=YOUR_POOL;Encrypt=yes;Authentication=ActiveDirectoryInteractive',
                         'schemas': ['dbo'], 'baseline_file': 'onceki/sema.json'}, agent_config={'target_schema': 'dwh'}, secrets=[], packages=['pyodbc>=5'],
                 requirements='SQL havuzunda VIEW DEFINITION/okuma yetkisi. ' + MSSQL_DRIVER,
                 sources=['https://learn.microsoft.com/en-us/azure/synapse-analytics/sql/connect-overview', 'https://learn.microsoft.com/en-us/sql/relational-databases/system-information-schema-views/columns-transact-sql'])
PRODUCTS += pair('warehouse', 'Redshift', 'redshift', label='Redshift', how='SVV_COLUMNS ve SVV_TABLE_INFO, Redshift Data API (SigV4)',
                 config={'region': 'eu-central-1', 'workgroup_name': 'YOUR_WORKGROUP', 'database': 'dev', 'schemas': ['public'], 'baseline_file': 'onceki/sema.json'},
                 agent_config={'target_schema': 'dwh'}, secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='redshift-data:ExecuteStatement/DescribeStatement/GetStatementResult ve Serverless için redshift-serverless:GetCredentials (provisioned küme için cluster_identifier + db_user veya secret_arn). '
                              'Geçici kimlik için AWS_SESSION_TOKEN da verilebilir.',
                 sources=['https://docs.aws.amazon.com/redshift-data/latest/APIReference/API_ExecuteStatement.html', 'https://docs.aws.amazon.com/redshift/latest/dg/r_SVV_COLUMNS.html',
                          'https://docs.aws.amazon.com/redshift/latest/dg/r_SVV_TABLE_INFO.html'])
PRODUCTS += pair('warehouse', 'Autonomous Data Warehouse', 'adw', label='Autonomous Data Warehouse', how='ALL_TAB_COLUMNS ve ALL_TABLES, ORDS REST-Enabled SQL',
                 config={'ords_url': 'https://YOUR_ADB-ID.adb.YOUR_REGION.oraclecloudapps.com/ords/YOUR_SCHEMA/', 'schemas': ['YOUR_SCHEMA'], 'baseline_file': 'onceki/sema.json'},
                 agent_config={'target_schema': 'DWH'}, secrets=['ORACLE_USER', 'ORACLE_PASSWORD'],
                 requirements='Şema için ORDS REST-Enabled SQL açık olmalı (ORDS.ENABLE_SCHEMA); kullanıcının hedef şemalarda tablo okuma yetkisi. NUM_ROWS istatistik toplandığında doludur.',
                 sources=['https://docs.oracle.com/en/database/oracle/oracle-rest-data-services/24.4/orddg/rest-enabled-sql-service.html',
                          'https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TAB_COLUMNS.html'])
PRODUCTS += pair('warehouse', 'Vantage', 'teradata', label='Teradata Vantage', how='DBC.ColumnsV, teradatasql sürücüsü',
                 config={'host': 'YOUR_HOST', 'user': 'YOUR_USER', 'logmech': 'TD2', 'databases': ['YOUR_DATABASE'], 'baseline_file': 'onceki/sema.json'},
                 agent_config={'target_schema': 'dwh'}, secrets=['DB_PASSWORD'], packages=['teradatasql>=20'],
                 requirements='DBC.ColumnsV üzerinde SELECT (varsayılan olarak açık); hedef veritabanlarında görünürlük. Tip adları DBC tip kodlarından üretilir (karakter uzunluğu bayttır).',
                 sources=['https://github.com/Teradata/python-driver', 'https://docs.teradata.com/r/Enterprise_IntelliFlex_VMware/Data-Dictionary/View-Column-Values/ColumnType-Column'])
PRODUCTS += pair('warehouse', 'ClickHouse', 'clickhouse', label='ClickHouse', how='system.columns ve system.tables, HTTP arayüzü (readonly=1)',
                 config={'server': 'https://YOUR_HOST:8443', 'databases': ['default'], 'baseline_file': 'onceki/sema.json'}, agent_config={'target_schema': 'dwh'},
                 secrets=['CLICKHOUSE_USER', 'CLICKHOUSE_PASSWORD'], requirements='HTTPS uç noktası (ClickHouse Cloud: 8443) ve system tablolarını okuyabilen kullanıcı.',
                 sources=['https://clickhouse.com/docs/en/interfaces/http', 'https://clickhouse.com/docs/en/operations/system-tables/columns',
                          'https://clickhouse.com/docs/en/operations/system-tables/tables'])

# ---------- Database ----------
PRODUCTS += pair('database', 'PostgreSQL', 'postgres', label='PostgreSQL', how='information_schema, pg_indexes, pg_stat_user_* ve pg_stat_statements görünümlerinden psycopg ile (salt-okunur oturum)',
                 config={'host': 'YOUR_HOST', 'port': 5432, 'database': 'YOUR_DATABASE', 'user': 'YOUR_READ_USER', 'sslmode': 'require', 'schemas': ['public'],
                         'baseline_file': 'onceki/sema.json'}, secrets=['DB_PASSWORD'], packages=['psycopg[binary]>=3.1'],
                 requirements='pg_read_all_stats rolü (veya istatistik görünümlerini okuma yetkisi); sorgu istatistikleri için pg_stat_statements eklentisi (yoksa bölüm atlanır).',
                 sources=['https://www.postgresql.org/docs/current/infoschema-columns.html', 'https://www.postgresql.org/docs/current/monitoring-stats.html',
                          'https://www.postgresql.org/docs/current/pgstatstatements.html', 'https://www.psycopg.org/psycopg3/docs/'])
for _key, _product, _server in (('sqlserver', 'SQL Server', 'YOUR_SERVER'), ('azure-sql', 'Azure SQL', 'tcp:YOUR_SERVER.database.windows.net,1433')):
    PRODUCTS += pair('database', _product, _key, label=_product, how='INFORMATION_SCHEMA, sys.indexes, index kullanım/eksik index DMV’leri ve sys.dm_exec_query_stats üzerinden ODBC ile',
                     config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=' + _server + ';Database=YOUR_DATABASE;Encrypt=yes;Authentication=ActiveDirectoryInteractive',
                             'schemas': ['dbo'], 'baseline_file': 'onceki/sema.json'}, secrets=[], packages=['pyodbc>=5'],
                     requirements='VIEW DATABASE STATE (Azure SQL) / VIEW SERVER STATE (SQL Server) ve VIEW DEFINITION. ' + MSSQL_DRIVER,
                     sources=['https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-db-index-usage-stats-transact-sql',
                              'https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-db-missing-index-details-transact-sql',
                              'https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql'])
PRODUCTS += pair('database', 'Oracle Database', 'oracle-db', label='Oracle Database', how='ALL_TAB_COLUMNS, ALL_INDEXES/ALL_IND_COLUMNS, ALL_TABLES ve V$SQL üzerinden python-oracledb ile',
                 config={'user': 'YOUR_READ_USER', 'dsn': 'YOUR_HOST:1521/YOUR_SERVICE', 'schemas': ['YOUR_SCHEMA'], 'baseline_file': 'onceki/sema.json'},
                 secrets=['DB_PASSWORD'], packages=['oracledb>=2'],
                 requirements='Hedef şemalarda SELECT ve sözlük görünümü erişimi; V$SQL için SELECT_CATALOG_ROLE (yoksa sorgu bölümü atlanır). oracledb thin modu istemci kurulumu gerektirmez.',
                 sources=['https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_TAB_COLUMNS.html', 'https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/V-SQL.html',
                          'https://python-oracledb.readthedocs.io/'])
PRODUCTS += pair('database', 'MySQL', 'mysql', label='MySQL', how='information_schema.columns/statistics, sys.schema_unused_indexes ve performance_schema özetlerinden PyMySQL ile',
                 config={'host': 'YOUR_HOST', 'port': 3306, 'database': 'YOUR_DATABASE', 'user': 'YOUR_READ_USER', 'databases': ['YOUR_DATABASE'], 'baseline_file': 'onceki/sema.json'},
                 secrets=['DB_PASSWORD'], packages=['PyMySQL>=1.1'],
                 requirements='Şemalarda SELECT, performance_schema ve sys şemasında SELECT (yoksa ilgili bölüm atlanır). TLS varsayılan açıktır.',
                 sources=['https://dev.mysql.com/doc/refman/8.4/en/information-schema-statistics-table.html', 'https://dev.mysql.com/doc/refman/8.4/en/sys-schema-unused-indexes.html',
                          'https://dev.mysql.com/doc/refman/8.4/en/performance-schema-statement-summary-tables.html'])
PRODUCTS += pair('database', 'Atlas', 'mongodb', label='MongoDB Atlas', how='koleksiyonlardan $sample ile alan/tip çıkarımı, listIndexes, $indexStats ve collStats üzerinden PyMongo ile',
                 config={'databases': ['YOUR_DATABASE'], 'collections': [], 'sample_size': 1000, 'read_preference': 'secondaryPreferred', 'baseline_file': 'onceki/sema.json'},
                 secrets=['MONGODB_URI'], packages=['pymongo>=4'],
                 requirements='MONGODB_URI (mongodb+srv://… salt-okunur kullanıcı) .env içinde; read rolü ve $indexStats/collStats için clusterMonitor. Şema örneklemden çıkarılır; örneklemde olmayan alanlar görünmez.',
                 sources=['https://www.mongodb.com/docs/manual/reference/operator/aggregation/sample/', 'https://www.mongodb.com/docs/manual/reference/operator/aggregation/indexStats/',
                          'https://pymongo.readthedocs.io/'], code_effects='$sample örneklemi kümeye okuma yükü bindirir.')
PRODUCTS += pair('database', 'Redis', 'redis', label='Redis', how='SCAN ile anahtar desenleri ve tipleri, INFO ve SLOWLOG üzerinden redis-py ile',
                 config={'match': '*', 'max_keys': 5000, 'baseline_file': 'onceki/sema.json'}, secrets=['REDIS_URL'], packages=['redis>=5'],
                 requirements='REDIS_URL (rediss://kullanici:parola@host:port/0) .env içinde; SCAN, TYPE, HKEYS, INFO ve SLOWLOG komutları (ACL). KEYS kullanılmaz; SCAN max_keys ile sınırlıdır.',
                 sources=['https://redis.io/docs/latest/commands/scan/', 'https://redis.io/docs/latest/commands/slowlog-get/', 'https://redis.io/docs/latest/commands/info/'],
                 code_effects='SCAN artımlıdır ama büyük anahtar uzaylarında yük oluşturabilir. Değerler okunmaz; yalnızca hash alan adları örneklenir.')
PRODUCTS += pair('database', 'Capella', 'capella', label='Couchbase Capella', how='INFER, system:indexes ve system:completed_requests ile sorgu servisi (salt-okunur istek)',
                 config={'query_url': 'https://YOUR_CLUSTER.cloud.couchbase.com:18093', 'collections': ['YOUR_BUCKET._default._default'], 'sample_size': 1000,
                         'baseline_file': 'onceki/sema.json'}, secrets=['COUCHBASE_USER', 'COUCHBASE_PASSWORD'],
                 requirements='Capella veritabanı erişim kimliği: koleksiyonlarda Query Select/Data Reader; completed_requests için Query System Catalog (yoksa bölüm atlanır).',
                 sources=['https://docs.couchbase.com/server/current/n1ql/n1ql-language-reference/infer.html', 'https://docs.couchbase.com/server/current/n1ql/n1ql-rest-api/index.html',
                          'https://docs.couchbase.com/server/current/manage/monitor/monitoring-n1ql-query.html'])
PRODUCTS += pair('database', 'DynamoDB', 'dynamodb', label='DynamoDB', how='ListTables ve DescribeTable (anahtar şeması, GSI/LSI, kapasite) üzerinden SigV4 imzalı API ile',
                 config={'region': 'eu-central-1', 'tables': [], 'baseline_file': 'onceki/sema.json'}, secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='dynamodb:ListTables ve dynamodb:DescribeTable izinleri. DynamoDB yalnızca anahtar özniteliklerini şema olarak tutar; diğer öznitelikler kapsam dışıdır.',
                 sources=['https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_DescribeTable.html', 'https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_ListTables.html'])
PRODUCTS += pair('database', 'Neo4j', 'neo4j', label='Neo4j', how='db.schema.nodeTypeProperties/relTypeProperties, SHOW INDEXES ve SHOW CONSTRAINTS ile Query API (HTTP)',
                 config={'server': 'https://YOUR_INSTANCE.databases.neo4j.io', 'database': 'neo4j', 'baseline_file': 'onceki/sema.json'}, secrets=['NEO4J_USER', 'NEO4J_PASSWORD'],
                 requirements='Neo4j 5.19+ Query API (/db/{db}/query/v2; Aura’da açık) ve reader rolü; SHOW INDEXES için uygun yetki.',
                 sources=['https://neo4j.com/docs/query-api/current/', 'https://neo4j.com/docs/operations-manual/current/procedures/', 'https://neo4j.com/docs/cypher-manual/current/indexes/'])

# ---------- Lakehouse ----------
LAYERS = ['bronze', 'silver', 'gold']
DELTA_SRC = 'https://github.com/delta-io/delta/blob/master/PROTOCOL.md'
PRODUCTS += pair('lakehouse', 'Databricks Lakehouse', 'databricks-lakehouse', label='Databricks Lakehouse', how='katalogun information_schema.columns görünümünden SQL Statement Execution API ile',
                 config={'workspace_url': 'https://YOUR_WORKSPACE.cloud.databricks.com', 'warehouse_id': 'YOUR_WAREHOUSE_ID', 'catalog': 'main', 'layers': LAYERS},
                 secrets=['DATABRICKS_ACCESS_TOKEN'], requirements='SQL warehouse üzerinde CAN USE; katalog/şemalarda USE CATALOG, USE SCHEMA ve tablolarda BROWSE/SELECT. Sorgular warehouse maliyeti doğurur.',
                 sources=['https://docs.databricks.com/aws/en/sql/language-manual/information-schema/columns', 'https://docs.databricks.com/api/workspace/statementexecution'])
PRODUCTS += pair('lakehouse', 'Unity Catalog', 'unity-catalog', label='Unity Catalog', how='Unity Catalog REST API’nin tablo listesinden (kolon bilgisiyle, sayfalı)',
                 config={'workspace_url': 'https://YOUR_WORKSPACE.cloud.databricks.com', 'catalog': 'main', 'layers': LAYERS}, secrets=['DATABRICKS_ACCESS_TOKEN'],
                 requirements='Katalog ve şemalarda USE CATALOG/USE SCHEMA ile tablolarda BROWSE veya SELECT. Warehouse gerekmez.',
                 sources=['https://docs.databricks.com/api/workspace/tables/list'])
PRODUCTS += pair('lakehouse', 'Fabric Lakehouse', 'fabric-lakehouse', label='Fabric Lakehouse', how='her katman lakehouse’ının SQL analytics endpoint’indeki INFORMATION_SCHEMA.COLUMNS görünümünden ODBC ile',
                 config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_ENDPOINT.datawarehouse.fabric.microsoft.com;Database=YOUR_BRONZE_LAKEHOUSE;Encrypt=yes;Authentication=ActiveDirectoryInteractive',
                         'layers': ['bronze_lakehouse', 'silver_lakehouse', 'gold_lakehouse']}, secrets=[], packages=['pyodbc>=5'],
                 requirements='Katman lakehouse’ları aynı çalışma alanında olmalı (çapraz veritabanı sorgusu); ReadData yetkisi. ' + MSSQL_DRIVER,
                 sources=['https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint', 'https://learn.microsoft.com/en-us/fabric/data-warehouse/connectivity'])
PRODUCTS += pair('lakehouse', 'OneLake', 'onelake', label='OneLake', how='OneLake DFS (ADLS Gen2) API ile tabloların Delta işlem günlüğünden (_delta_log metaData)',
                 config={'workspace': 'YOUR_WORKSPACE_NAME_OR_ID', 'item_path': 'YOUR_LAKEHOUSE.Lakehouse', 'tables_prefix': 'Tables', 'layers': LAYERS},
                 secrets=['ONELAKE_ACCESS_TOKEN'], packages=['pyarrow>=14'],
                 requirements='https://storage.azure.com/ kapsamlı Entra ID belirteci (ONELAKE_ACCESS_TOKEN) ve öğede ReadAll. Katmanlar Tables/ altındaki şema klasörleridir (şema etkin lakehouse). '
                              'Şema yalnızca Parquet checkpoint’te kalmışsa pyarrow gerekir.',
                 sources=['https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api', DELTA_SRC])
PRODUCTS += pair('lakehouse', 'S3 / Lake Formation', 'lake-formation', label='S3 / Lake Formation', how='AWS Glue Data Catalog GetTables API’sinden (Lake Formation izinleriyle, SigV4)',
                 config={'region': 'eu-central-1', 'layers': ['bronze', 'silver', 'gold']}, secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='glue:GetTables ve Lake Formation’da veritabanı/tablo DESCRIBE izni. Katmanlar Glue veritabanlarıdır.',
                 sources=['https://docs.aws.amazon.com/glue/latest/webapi/API_GetTables.html', 'https://docs.aws.amazon.com/lake-formation/latest/dg/lf-permissions-reference.html'])
PRODUCTS += pair('lakehouse', 'Cloud Storage / BigLake', 'biglake', label='Cloud Storage / BigLake', how='katman veri kümelerinin INFORMATION_SCHEMA.COLUMNS görünümünden BigQuery jobs.query API ile',
                 config={'project_id': 'YOUR_PROJECT_ID', 'location': 'EU', 'layers': LAYERS, 'maximum_bytes_billed': 1073741824}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements=GCP_SCOPE + ' BigLake tabloları BigQuery veri kümelerinde tanımlı olmalı; katmanlar veri kümesi adlarıdır.',
                 sources=['https://cloud.google.com/bigquery/docs/biglake-intro', 'https://cloud.google.com/bigquery/docs/information-schema-columns'])
PRODUCTS += pair('lakehouse', 'Iceberg', 'iceberg', label='Apache Iceberg', how='Iceberg REST Catalog API’sinden (listTables + loadTable, güncel şema)',
                 config={'catalog_uri': 'https://YOUR_CATALOG', 'prefix': '', 'layers': LAYERS}, secrets=['ICEBERG_TOKEN'],
                 requirements='Iceberg REST Catalog uç noktası (Polaris, Unity, Glue REST, Nessie vb.) ve namespace’leri okuyabilen OAuth/bearer belirteci. Katmanlar namespace adlarıdır.',
                 sources=['https://iceberg.apache.org/rest-catalog-spec/', 'https://iceberg.apache.org/spec/#schemas'])
PRODUCTS += pair('lakehouse', 'Hudi', 'hudi', label='Apache Hudi', how='tablo .hoodie zaman çizelgesindeki son commit’in yazar şemasından (S3 veya yerel/bağlı disk)',
                 config={'storage': 's3', 'region': 'eu-central-1', 'bucket': 'YOUR_BUCKET', 'root_prefix': 'lake', 'tables_prefix': '', 'layers': LAYERS},
                 secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='S3 için s3:ListBucket ve s3:GetObject (yalnızca .hoodie altı okunur); yerel kullanım için storage=local ve root_path. Hudi 0.x (timeline v1) .commit dosyaları beklenir.',
                 sources=['https://hudi.apache.org/docs/timeline', 'https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html'])
PRODUCTS += pair('lakehouse', 'Delta Lake', 'delta-lake', label='Delta Lake', how='tabloların _delta_log işlem günlüğündeki son metaData eyleminden (yerel/bağlı disk veya S3)',
                 config={'storage': 'local', 'root_path': 'YOUR_LAKE_ROOT', 'tables_prefix': '', 'layers': LAYERS}, secrets=[], packages=['pyarrow>=14'],
                 requirements='Katman klasörlerine okuma erişimi (storage=s3 için bucket/region ve AWS anahtarları). Veri dosyaları okunmaz; yalnızca _delta_log. Eski günlükler temizlenmişse checkpoint için pyarrow gerekir.',
                 sources=[DELTA_SRC])
PRODUCTS += pair('lakehouse', 'Dremio', 'dremio', label='Dremio', how='INFORMATION_SCHEMA."COLUMNS" görünümünden Dremio SQL/Job REST API ile',
                 config={'server': 'https://YOUR_DREMIO', 'project_id': '', 'layers': ['lake.bronze', 'lake.silver', 'lake.gold']}, secrets=['DREMIO_TOKEN'],
                 requirements='Kişisel erişim belirteci (PAT) ve kaynak/klasörlerde SELECT. Dremio Cloud için server https://api.dremio.cloud ve project_id.',
                 sources=['https://docs.dremio.com/current/reference/api/sql/', 'https://docs.dremio.com/current/reference/sql/information-schema/'])

# ---------- FinOps ----------
PRODUCTS += pair('finops', 'BigQuery Billing Export', 'bigquery-billing', label='BigQuery Billing Export', how='Cloud Billing’in BigQuery’ye dışa aktardığı tablodan jobs.query API ile',
                 config={'project_id': 'YOUR_PROJECT_ID', 'location': 'EU', 'billing_table': 'YOUR_PROJECT.billing.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX', 'days': 30,
                         'maximum_bytes_billed': 5368709120}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements=GCP_SCOPE + ' Billing export veri kümesinde BigQuery Data Viewer. Standart veya detaylı (resource) export tablosu desteklenir.',
                 sources=['https://cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage', 'https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query'])
PRODUCTS += pair('finops', 'Cost and Usage Report', 'aws-cur', label='AWS Cost and Usage Report', how='CUR 2.0 tablosundan Athena StartQueryExecution/GetQueryResults API ile',
                 config={'region': 'us-east-1', 'workgroup': 'primary', 'database': 'YOUR_CUR_DATABASE', 'cur_table': 'YOUR_CUR_DATABASE.YOUR_CUR_TABLE', 'days': 30},
                 secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='athena:StartQueryExecution/GetQueryExecution/GetQueryResults, Glue tablosunda okuma ve CUR S3 kovasında s3:GetObject; çalışma grubunun sonuç konumu tanımlı olmalı. Athena tarama ücreti doğar.',
                 sources=['https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html', 'https://docs.aws.amazon.com/athena/latest/APIReference/API_StartQueryExecution.html'])
PRODUCTS += pair('finops', 'Fabric Capacity Metrics', 'fabric-capacity', label='Fabric kapasite', how='Azure Cost Management Query API (microsoft.fabric/capacities) ve Fabric REST kapasite listesinden',
                 config={'subscription_id': 'YOUR_SUBSCRIPTION_ID', 'days': 30}, secrets=['AZURE_ACCESS_TOKEN', 'FABRIC_ACCESS_TOKEN'],
                 requirements='Abonelikte Cost Management Reader (management.azure.com belirteci) ve Fabric API belirteci (kapasite listesi için kapasite yöneticisi/izin). '
                              'Capacity Metrics uygulamasının anlamsal modeli belgelenmiş bir API olmadığından CU kullanımı değil faturalanan maliyet okunur.',
                 sources=['https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage', 'https://learn.microsoft.com/en-us/rest/api/fabric/core/capacities/list-capacities'])
PRODUCTS += pair('finops', 'Account Usage', 'snowflake-usage', label='Snowflake Account Usage', only=('agent',),
                 how='WAREHOUSE_METERING_HISTORY, QUERY_HISTORY ve SHOW WAREHOUSES ile SQL API v2 üzerinden',
                 config={'server': 'https://YOUR_ACCOUNT.snowflakecomputing.com', 'warehouse': 'COMPUTE_WH', 'role': 'ACCOUNTADMIN', 'days': 30, 'timeout_seconds': 120},
                 secrets=['SNOWFLAKE_ACCESS_TOKEN'], requirements='SNOWFLAKE veritabanında IMPORTED PRIVILEGES olan rol ve depoları görebilme (MONITOR). Account Usage 45 dk–3 saat gecikmelidir.',
                 sources=['https://docs.snowflake.com/en/sql-reference/account-usage/warehouse_metering_history', 'https://docs.snowflake.com/en/sql-reference/sql/show-warehouses'])
PRODUCTS += pair('finops', 'System Tables', 'databricks-system-tables', label='Databricks System Tables', only=('agent',),
                 how='system.billing.usage/list_prices ve SQL Warehouses API üzerinden',
                 config={'workspace_url': 'https://YOUR_WORKSPACE.cloud.databricks.com', 'warehouse_id': 'YOUR_WAREHOUSE_ID', 'days': 30}, secrets=['DATABRICKS_ACCESS_TOKEN'],
                 requirements='system.billing şemasında SELECT ve SQL warehouse listesini görme yetkisi. Tutarlar liste fiyatıdır; indirimli sözleşme fiyatını yansıtmaz.',
                 sources=['https://docs.databricks.com/aws/en/admin/system-tables/billing', 'https://docs.databricks.com/api/workspace/warehouses/list'])

# ---------- Geospatial & IoT ----------
CHECKS = {'bbox': [25.6, 35.8, 44.8, 42.1], 'value_ranges': {}, 'expected_interval_seconds': 300, 'stale_hours': 24}
PRODUCTS += pair('spatial', 'ArcGIS', 'arcgis', label='ArcGIS', how='feature layer bilgisi ve sayfalı query işlemiyle (outSR=4326) ArcGIS REST API üzerinden',
                 config={'layer_url': 'https://services.arcgis.com/YOUR_ORG/arcgis/rest/services/YOUR_SERVICE/FeatureServer/0', 'where': '1=1', 'device_field': '', 'time_field': '',
                         'max_features': 50000, **CHECKS}, secrets=['ARCGIS_TOKEN'],
                 requirements='ArcGIS Online/Enterprise erişim belirteci (OAuth veya API anahtarı) ve katmanda sorgu yetkisi. Nokta dışı geometrilerde yalnızca boş/eksik geometri denetlenir.',
                 sources=['https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/', 'https://developers.arcgis.com/rest/users-groups-and-items/authentication/'])
PRODUCTS += pair('spatial', 'QGIS', 'qgis', label='QGIS', how='QGIS proje dosyasından (.qgz/.qgs) katmanları ve CRS’leri, GeoPackage katmanlarını SQLite üzerinden salt-okunur',
                 config={'project_file': 'proje.qgz', 'layers': [], **CHECKS}, secrets=[],
                 requirements='Proje dosyası ve GeoPackage katmanları yerel diskte. QGIS kurulumu gerekmez; koordinat kontrolü EPSG:4326 nokta katmanlarında yapılır, diğerlerinde geometri ve CRS tutarlılığı denetlenir.',
                 sources=['https://docs.qgis.org/latest/en/docs/user_manual/introduction/project_files.html', 'https://www.geopackage.org/spec/#gpb_format'])
PRODUCTS += pair('spatial', 'PostGIS', 'postgis', label='PostGIS', how='ST_IsValid/ST_IsValidReason/ST_SRID istatistikleri ve ST_Transform ile WGS84 temsil noktalarından psycopg ile',
                 config={'host': 'YOUR_HOST', 'port': 5432, 'database': 'YOUR_DATABASE', 'user': 'YOUR_READ_USER', 'table': 'public.YOUR_TABLE', 'geometry_column': 'geom',
                         'id_column': 'id', 'time_column': '', 'device_column': '', 'value_columns': [], 'max_rows': 100000, **CHECKS},
                 secrets=['DB_PASSWORD'], packages=['psycopg[binary]>=3.1'], requirements='Tabloda SELECT; PostGIS 3.x.',
                 sources=['https://postgis.net/docs/ST_IsValid.html', 'https://postgis.net/docs/ST_Transform.html', 'https://postgis.net/docs/ST_PointOnSurface.html'])
PRODUCTS += pair('spatial', 'TimescaleDB', 'timescaledb', label='TimescaleDB', how='timescaledb_information.hypertables ve son N günün ölçümlerinden psycopg ile',
                 config={'host': 'YOUR_HOST', 'port': 5432, 'database': 'tsdb', 'user': 'YOUR_READ_USER', 'table': 'public.YOUR_HYPERTABLE', 'time_column': 'time',
                         'device_column': 'device_id', 'value_columns': ['temperature'], 'days': 7, 'max_rows': 500000, **{**CHECKS, 'value_ranges': {'temperature': [-40, 85]}}},
                 secrets=['DB_PASSWORD'], packages=['psycopg[binary]>=3.1'], requirements='Hypertable’da SELECT. Büyük tablolarda days ve max_rows ile kapsamı sınırlayın.',
                 sources=['https://docs.timescale.com/api/latest/informational-views/hypertables/', 'https://docs.timescale.com/api/latest/hyperfunctions/time_bucket/'])
PRODUCTS += pair('spatial', 'InfluxDB', 'influxdb', label='InfluxDB', how='Flux sorgusuyla (pivot) /api/v2/query üzerinden InfluxDB v2 API ile',
                 config={'server': 'https://YOUR_REGION.aws.cloud2.influxdata.com', 'org': 'YOUR_ORG', 'bucket': 'YOUR_BUCKET', 'measurement': 'sensor', 'device_tag': 'device',
                         'fields': ['temperature'], 'days': 7, 'max_rows_per_series': 50000, **{**CHECKS, 'value_ranges': {'temperature': [-40, 85]}}},
                 secrets=['INFLUX_TOKEN'], requirements='Bucket’ta okuma yetkili API belirteci (InfluxDB 2.x veya Cloud TSM). InfluxDB 3 SQL uç noktası bu pakette yoktur.',
                 sources=['https://docs.influxdata.com/influxdb/v2/api/#operation/PostQuery', 'https://docs.influxdata.com/flux/v0/stdlib/universe/aggregatewindow/'])
PRODUCTS += pair('spatial', 'IoT Hub', 'iot-hub', label='Azure IoT Hub', how='cihaz ikizleri sorgusundan (connectionState, lastActivityTime, reported konum) IoT Hub REST API ile',
                 config={'hub_name': 'YOUR_HUB', 'location_property': 'location', **CHECKS}, secrets=['IOTHUB_POLICY_NAME', 'IOTHUB_POLICY_KEY'],
                 requirements='registryRead izinli paylaşılan erişim ilkesi (ör. registryRead); SAS belirteci yerelde üretilir. Telemetri mesajları okunmaz; ikiz özellikleri denetlenir.',
                 sources=['https://learn.microsoft.com/en-us/rest/api/iothub/service/query/get-twins', 'https://learn.microsoft.com/en-us/azure/iot-hub/authenticate-authorize-sas'])
PRODUCTS += pair('spatial', 'IoT Core', 'iot-core', label='AWS IoT Core', how='filo indeksleme SearchIndex sorgusundan (bağlantı durumu ve shadow reported konum) SigV4 ile',
                 config={'region': 'eu-central-1', 'query_string': 'thingName:*', 'location_property': 'location', **CHECKS}, secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
                 requirements='iot:SearchIndex izni; filo indekslemede connectivity ve shadow indeksleme açık olmalı. Telemetri mesajları okunmaz.',
                 sources=['https://docs.aws.amazon.com/iot/latest/apireference/API_SearchIndex.html', 'https://docs.aws.amazon.com/iot/latest/developerguide/managing-fleet-index.html'])

# ---------- GenAI & vector data ----------
SIM = {'text_similarity': 0.85, 'vector_similarity': 0.98, 'max_chunks': 10000}
PRODUCTS += pair('ai', 'API', 'openai', label='OpenAI vector store', how='vector store dosya listesi ve ayrıştırılmış dosya içeriği uç noktalarından OpenAI API ile',
                 config={'vector_store_id': 'vs_YOUR_STORE', **SIM}, secrets=['OPENAI_API_KEY'],
                 requirements='Vector store’u okuyabilen proje API anahtarı. Agent paketinde aynı anahtar model sağlayıcısı için de kullanılabilir.',
                 sources=['https://platform.openai.com/docs/api-reference/vector-stores-files/listFiles', 'https://platform.openai.com/docs/api-reference/vector-stores-files/getContent'])
PRODUCTS += pair('ai', 'Claude API', 'claude', label='Claude API', how='Files API listesi (ad+boyut tekrarları), indirilebilir metin dosyaları ve corpus_dir içindeki yerel belgelerden',
                 config={'corpus_dir': 'belgeler', **SIM}, secrets=['ANTHROPIC_API_KEY'],
                 requirements='Files API (beta başlığı files-api-2025-04-14) için API anahtarı. Yüklenen dosyaların içeriği API’den indirilemez; içerik kontrolü için belgelerin yerel kopyası corpus_dir’e konur.',
                 sources=['https://docs.anthropic.com/en/docs/build-with-claude/files', 'https://docs.anthropic.com/en/api/files-list'])
PRODUCTS += pair('ai', 'LangChain', 'langchain', label='LangChain', how='Document listesinin JSONL dökümünden (page_content, metadata; dumpd biçimi de)',
                 config={'documents_file': 'documents.jsonl', **SIM}, secrets=[],
                 requirements='Parçalanmış Document nesnelerinin JSONL dökümü (ör. [d.model_dump() for d in docs]). Ağ bağlantısı gerekmez.',
                 sources=['https://python.langchain.com/api_reference/core/documents/langchain_core.documents.base.Document.html'])
PRODUCTS += pair('ai', 'LangGraph', 'langgraph', label='LangGraph', how='LangGraph Server Store API’sinin öğe aramasından (sayfalı)',
                 config={'deployment_url': 'https://YOUR_DEPLOYMENT.us.langgraph.app', 'namespace_prefix': ['documents'], 'text_field': 'text', **SIM}, secrets=['LANGSMITH_API_KEY'],
                 requirements='LangGraph Platform/Server dağıtımı ve x-api-key (LangSmith) erişimi; uzun süreli belleğin Store’da tutulması.',
                 sources=['https://langchain-ai.github.io/langgraph/cloud/reference/api/api_ref/', 'https://langchain-ai.github.io/langgraph/concepts/persistence/#memory-store'])
PRODUCTS += pair('ai', 'LlamaIndex', 'llamaindex', label='LlamaIndex', how='kalıcı storage_context’in docstore.json dosyasındaki düğümlerden',
                 config={'docstore_file': 'storage/docstore.json', **SIM}, secrets=[],
                 requirements='storage_context.persist() ile yazılmış docstore.json (SimpleDocumentStore). Ağ bağlantısı gerekmez.',
                 sources=['https://docs.llamaindex.ai/en/stable/module_guides/storing/docstores/', 'https://docs.llamaindex.ai/en/stable/module_guides/storing/save_load/'])
PRODUCTS += pair('ai', 'Pinecone', 'pinecone', label='Pinecone', how='describe_index_stats, vectors/list ve vectors/fetch veri düzlemi uç noktalarından',
                 config={'index_host': 'https://YOUR_INDEX-YOUR_PROJECT.svc.YOUR_REGION.pinecone.io', 'namespace': '', 'text_field': 'text', 'source_field': 'source', **SIM},
                 secrets=['PINECONE_API_KEY'], requirements='Serverless indeks (vectors/list yalnızca serverless’te) ve okuma yetkili API anahtarı; metin metadata’da text_field alanında tutulmalı.',
                 sources=['https://docs.pinecone.io/reference/api/2025-04/data-plane/list', 'https://docs.pinecone.io/reference/api/2025-04/data-plane/fetch'])
PRODUCTS += pair('ai', 'Qdrant', 'qdrant', label='Qdrant', how='koleksiyon bilgisi ve points/scroll (payload + vektör) uç noktalarından',
                 config={'server': 'https://YOUR_CLUSTER.cloud.qdrant.io:6333', 'collection': 'YOUR_COLLECTION', 'text_field': 'text', 'source_field': 'source', 'vector_name': '', **SIM},
                 secrets=['QDRANT_API_KEY'], requirements='Koleksiyonu okuyabilen API anahtarı (salt-okunur anahtar yeterli).',
                 sources=['https://api.qdrant.tech/api-reference/points/scroll-points', 'https://api.qdrant.tech/api-reference/collections/get-collection'])
PRODUCTS += pair('ai', 'Weaviate', 'weaviate', label='Weaviate', how='sınıf şeması ve imleçli /v1/objects (include=vector) uç noktalarından',
                 config={'server': 'https://YOUR_CLUSTER.weaviate.cloud', 'collection': 'Document', 'text_field': 'text', 'source_field': 'source', **SIM},
                 secrets=['WEAVIATE_API_KEY'], requirements='Koleksiyonu okuyabilen API anahtarı. Çok kiracılı koleksiyonlar bu pakette desteklenmez.',
                 sources=['https://weaviate.io/developers/weaviate/api/rest#tag/objects/get/objects', 'https://weaviate.io/developers/weaviate/manage-data/read-all-objects'])
PRODUCTS += pair('ai', 'Milvus', 'milvus', label='Milvus', how='RESTful API v2 koleksiyon tanımı ve entities/query uç noktalarından',
                 config={'server': 'https://YOUR_ENDPOINT.zillizcloud.com', 'collection': 'YOUR_COLLECTION', 'primary_field': 'id', 'text_field': 'text', 'vector_field': 'vector',
                         'metadata_fields': ['source'], 'source_field': 'source', **SIM}, secrets=['MILVUS_TOKEN'],
                 requirements='Milvus 2.4+ / Zilliz Cloud RESTful API v2 ve okuma yetkili belirteç (kullanici:parola veya API anahtarı). REST sorgusu en fazla 16.384 kayıt okur.',
                 sources=['https://milvus.io/api-reference/restful/v2.4.x/v2/Vector%20(v2)/Query.md', 'https://milvus.io/api-reference/restful/v2.4.x/v2/Collection%20(v2)/Describe.md'])
PRODUCTS += pair('ai', 'pgvector', 'pgvector', label='pgvector', how='parça tablosundan (metin, embedding, metadata kolonları) ve pg_indexes’teki HNSW/IVFFlat indekslerinden psycopg ile',
                 config={'host': 'YOUR_HOST', 'port': 5432, 'database': 'YOUR_DATABASE', 'user': 'YOUR_READ_USER', 'table': 'public.chunks', 'id_column': 'id',
                         'text_column': 'content', 'embedding_column': 'embedding', 'metadata_columns': ['source'], 'source_field': 'source', **SIM},
                 secrets=['DB_PASSWORD'], packages=['psycopg[binary]>=3.1'], requirements='Parça tablosunda SELECT; pgvector eklentisi.',
                 sources=['https://github.com/pgvector/pgvector', 'https://www.postgresql.org/docs/current/view-pg-indexes.html'])

# ---------- Business intelligence ----------
QLIK_CLOUD = {'tenant': 'https://YOUR_TENANT.eu.qlikcloud.com', 'app_id': 'YOUR_APP_ID'}
QSE = {'server': 'https://YOUR_QLIK_SERVER', 'virtual_proxy': 'jwt', 'app_id': 'YOUR_APP_ID'}
TABLEAU = {'api_version': '3.24', 'site_content_url': 'YOUR_SITE'}
PRODUCTS += pair('bi', 'Qlik Cloud', 'qlik-cloud', label='Qlik Cloud', how='Reporting API (sense-sheet-1.0) ile seçilen sayfa PDF/PPTX olarak üretilir; durum adresi izlenir ve sonuç indirilir.',
                 agent_how='uygulamanın veri modeli metadata uç noktasından (/apps/{id}/data/metadata)',
                 config={**QLIK_CLOUD, 'sheet_id': 'YOUR_SHEET_ID', 'format': 'pdf', 'page_size': 'A4', 'selections_by_state': {}}, secrets=['QLIK_ACCESS_TOKEN'],
                 outputs={'code': 'rapor.pdf/pptx + rapor.json'},
                 requirements='Uygulamada görüntüleme yetkisi olan API anahtarı veya OAuth belirteci; kiracıda raporlama özelliği (dakikada 10 istek sınırı).',
                 sources=['https://qlik.dev/apis/rest/reports', 'https://qlik.dev/embed/reports/reporting-api-samples/', 'https://qlik.dev/apis/rest/apps'])
PRODUCTS += pair('bi', 'Qlik Sense Enterprise', 'qlik-sense', label='Qlik Sense Enterprise', how='Engine API (WebSocket JSON-RPC) ile seçilen nesneler ExportData ile XLSX’e aktarılır ve virtual proxy üzerinden indirilir.',
                 agent_how='Engine API GetTablesAndKeys çağrısıyla (tablolar, alanlar, anahtarlar)',
                 config={**QSE, 'object_ids': ['YOUR_OBJECT_ID']}, secrets=['QLIK_JWT'], outputs={'code': '<nesne>.xlsx + rapor.json'},
                 requirements='JWT kimlik doğrulamalı virtual proxy (Authorization: Bearer) ve uygulamada okuma/dışa aktarma yetkisi. Sertifika denetimi açıktır.',
                 sources=['https://help.qlik.com/en-US/sense-developer/Subsystems/EngineAPI/Content/Sense_EngineAPI/WorkingWithAppsAndVisualizations/export-data.htm',
                          'https://help.qlik.com/en-US/sense-admin/Subsystems/DeployAdministerQSE/Content/Sense_DeployAdminister/QSEoW/Administer_QSEoW/Managing_QSEoW/JWT-authentication.htm'])
PRODUCTS += pair('bi', 'QlikView', 'qlikview', label='QlikView', how='QMS API (SOAP, Windows kimlik doğrulaması, X-Service-Key) ile QMC’de tanımlı yeniden yükleme/dağıtım görevi başlatılır ve tamamlanana kadar izlenir.',
                 agent_how='belgenin -prj klasöründeki LoadScript.txt dosyasından (LOAD alanları)',
                 config={'qms_url': 'https://YOUR_QV_SERVER:4799/QMS/Service', 'allow_http': False, 'task_id': 'YOUR_TASK_GUID'}, agent_config={'prj_folder': 'Belge-prj'},
                 secrets=['QV_USER', 'QV_PASSWORD'], packages=['requests>=2.31', 'requests-ntlm>=1.2'], outputs={'code': 'qlikview-gorev.json (görev sonucu; dağıtılan belgeler görev hedeflerinde)'},
                 requirements='Kullanıcı “QlikView Management API” yerel grubunda ve Document Folder Administrator rolünde olmalı (DOMAIN\\kullanici). QlikView 12.10+ (IQMS2).',
                 sources=['https://help.qlik.com/en-US/qlikview-developer/Subsystems/QMSAPIref/Content/PIX.Services.V12.IQMS.RunTask.htm',
                          'https://community.qlik.com/t5/Knowledge/QlikView-Call-QMS-API-with-PowerShell/ta-p/1713678'])
PRODUCTS += pair('bi', 'Power BI Service', 'powerbi-service', label='Power BI Service', how='Export To File API ile rapor PDF/PPTX/PNG olarak üretilir; dışa aktarma işi izlenip dosya indirilir.',
                 agent_how='semantik model üzerinde executeQueries ile INFO.VIEW.COLUMNS/MEASURES işlevlerinden',
                 config={'workspace_id': 'YOUR_WORKSPACE_UUID', 'report_id': 'YOUR_REPORT_UUID', 'format': 'PDF', 'pages': []}, agent_config={'dataset_id': 'YOUR_DATASET_UUID'},
                 secrets=['POWERBI_ACCESS_TOKEN'], outputs={'code': 'rapor.pdf/pptx (çok sayfalı PNG: rapor.zip) + rapor.json'},
                 requirements='Report.Read.All ve Dataset.Read.All; dışa aktarma için Premium/Fabric/Embedded kapasite. executeQueries için semantik modelde Build izni ve kiracı ayarı.',
                 sources=['https://learn.microsoft.com/en-us/rest/api/power-bi/reports/export-to-file-in-group',
                          'https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group', 'https://learn.microsoft.com/en-us/dax/info-view-columns-function-dax'])
PRODUCTS += pair('bi', 'Power BI Desktop', 'powerbi-desktop', label='Power BI Desktop', only=('agent',), how='PBIP projesinin TMDL dosyalarından (tablolar, kolonlar, ölçüler)',
                 config={'semantic_model_dir': 'Rapor.SemanticModel'}, secrets=[], requirements='Projeyi Power BI Desktop’ta “Power BI proje (.pbip)” olarak kaydedin (TMDL biçimi). Ağ bağlantısı gerekmez.',
                 sources=['https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-dataset', 'https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview'])
for _key, _product, _server in (('tableau-cloud', 'Tableau Cloud', 'https://YOUR_POD.online.tableau.com'), ('tableau-server', 'Tableau Server', 'https://YOUR_TABLEAU_SERVER')):
    PRODUCTS += pair('bi', _product, _key, label=_product, how='REST API ile görünüm PDF, PNG, Excel çapraz tablo veya CSV olarak sunucuda render edilir ve indirilir.',
                     agent_how='Metadata API (GraphQL) ile çalışma kitabının gömülü/yayınlanmış veri kaynağı alanlarından',
                     config={'server': _server, **TABLEAU, 'workbook_name': 'YOUR_WORKBOOK', 'view_name': 'YOUR_VIEW', 'format': 'pdf', 'page_size': 'A4', 'orientation': 'Landscape'},
                     agent_config={'workbook_id': 'YOUR_WORKBOOK_LUID'}, secrets=['TABLEAU_PAT_NAME', 'TABLEAU_PAT_SECRET'], outputs={'code': 'rapor.pdf/png/xlsx/csv + rapor.json'},
                     requirements='Kişisel erişim belirteci; görünümde görüntüleme ve indirme (Download Image/PDF, Download Full Data) yetkileri. Metadata API sunucuda açık olmalı.',
                     sources=['https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_view_data.htm', 'https://help.tableau.com/current/api/metadata_api/en-us/index.html'])
PRODUCTS += pair('bi', 'Tableau Desktop', 'tableau-desktop', label='Tableau Desktop', only=('agent',), how='.twb/.twbx çalışma kitabındaki veri kaynağı kolonlarından',
                 config={'workbook_file': 'pano.twbx'}, secrets=[], requirements='Yerel .twb veya .twbx dosyası. Ağ bağlantısı gerekmez.',
                 sources=['https://help.tableau.com/current/pro/desktop/en-us/save_savework.htm'])
PRODUCTS += pair('bi', 'Looker', 'looker', label='Looker', how='API 4.0 render task ile pano PDF/PNG/JPG olarak üretilir; görev izlenip sonuç indirilir.',
                 agent_how='LookML explore metadata’sından (boyutlar ve ölçüler)',
                 config={'server': 'https://YOUR_INSTANCE.looker.com', 'dashboard_id': 'YOUR_DASHBOARD_ID', 'format': 'pdf', 'paper_size': 'a4', 'width': 1680, 'height': 1200},
                 agent_config={'model': 'YOUR_MODEL', 'explore': 'YOUR_EXPLORE'}, secrets=['LOOKER_CLIENT_ID', 'LOOKER_CLIENT_SECRET'], outputs={'code': 'rapor.pdf/png/jpg + rapor.json'},
                 requirements='API3 istemci kimliği/gizli anahtarı; panoyu görme ve render_tasks yetkisi.',
                 sources=['https://cloud.google.com/looker/docs/reference/looker-api/latest/methods/RenderTask/create_dashboard_render_task',
                          'https://cloud.google.com/looker/docs/reference/looker-api/latest/methods/LookmlModel/lookml_model_explore'])
PRODUCTS += pair('bi', 'Looker Studio', 'looker-studio', label='Looker Studio', how='BigQuery tablosu doğrulanır ve Linking API ile (isteğe bağlı şablon raporla) rapor oluşturma bağlantısı üretilir; rapor bağlantı açılınca kullanıcının hesabında oluşur.',
                 agent_how='bağlanacak BigQuery tablosunun şemasından',
                 config={'project_id': 'YOUR_PROJECT_ID', 'dataset_id': 'YOUR_DATASET', 'table_id': 'YOUR_TABLE', 'report_name': 'Satış raporu', 'template_report_id': ''},
                 secrets=['GOOGLE_ACCESS_TOKEN'], outputs={'code': 'rapor_olustur.url + veri_kaynagi_alanlari.csv + rapor.json'},
                 requirements='BigQuery tablosunda Data Viewer (tables.get). Looker Studio’nun rapor oluşturan bir REST API’si yoktur; Linking API tarayıcıda çalışır.',
                 sources=['https://developers.google.com/looker-studio/integrate/linking-api', 'https://cloud.google.com/bigquery/docs/reference/rest/v2/tables/get'])
PRODUCTS += pair('bi', 'SAP BusinessObjects', 'sap-bo', label='SAP BusinessObjects', how='RESTful Web Services (Raylight) ile Web Intelligence belgesi isteğe bağlı yenilenip PDF/XLSX/CSV olarak alınır; oturum kapatılır.',
                 agent_how='belgenin veri sağlayıcılarındaki sözlük ifadelerinden (boyut/ölçü)',
                 config={'server': 'https://YOUR_BO_SERVER:6405', 'auth': 'secEnterprise', 'document_id': '12345', 'format': 'pdf', 'refresh': False},
                 secrets=['BO_USER', 'BO_PASSWORD'], outputs={'code': 'rapor.pdf/xlsx/csv + rapor.json'},
                 requirements='BI 4.2+ RESTful Web Services (WACS, varsayılan 6405) ve belgede görüntüleme/dışa aktarma hakkı. Komut istemli belgelerde refresh kayıtlı istem değerleriyle çalışır.',
                 sources=['https://help.sap.com/docs/SAP_BUSINESSOBJECTS_BUSINESS_INTELLIGENCE_PLATFORM',
                          'https://blogs.sap.com/2013/09/13/export-webi-report-using-the-restful-sdk/'])
PRODUCTS += pair('bi', 'SAP Analytics Cloud', 'sac', label='SAP Analytics Cloud', how='Hikâye dışa aktarımı için REST API olmadığından modelin olgu verisi Data Export API (OData) ile okunur ve seçilen kırılımlarla özet tabloya dönüştürülür.',
                 agent_how='Data Export API’nin FactData $metadata tanımından',
                 config={'tenant_url': 'https://YOUR_TENANT.YOUR_DC.sapanalytics.cloud', 'provider_id': 'YOUR_MODEL_ID', 'dimensions': ['Date'], 'measures': ['SignedData'], 'filter': ''},
                 secrets=['SAC_ACCESS_TOKEN'], outputs={'code': 'rapor.csv + rapor.json'},
                 requirements='Data Export API kapsamlı OAuth istemcisinden alınan belirteç ve modelde okuma yetkisi.',
                 sources=['https://help.sap.com/docs/SAP_ANALYTICS_CLOUD',
                          'https://blogs.sap.com/2023/01/03/announcement-new-series-exploring-the-sap-analytics-cloud-data-export-api/'])
PRODUCTS += pair('bi', 'Oracle Analytics Cloud', 'oac', label='Oracle Analytics Cloud', how='REST API (20210901) ile çalışma kitabı tuvalleri PDF/PNG olarak dışa aktarılır; iş isteği izlenip dosya indirilir.',
                 agent_how='katalog listesi ve veri kümesi kolon dökümünden (columns_file; OAC kolonları belgelenmiş bir REST uç noktasında vermez)',
                 config={'server': 'https://YOUR_INSTANCE.analytics.ocp.oraclecloud.com', 'workbook_path': '/shared/Sales/MySalesWorkbook', 'canvas_ids': ['snapshot!canvas!1'], 'format': 'pdf'},
                 agent_config={'columns_file': 'kolonlar.csv'}, secrets=['OAC_ACCESS_TOKEN'], outputs={'code': 'rapor.pdf/png + rapor.json'},
                 requirements='OCI IAM/IDCS OAuth belirteci; çalışma kitabında okuma ve “Export Workbook to Document” yetkisi (BI Consumer).',
                 sources=['https://docs.oracle.com/en/cloud/paas/analytics-cloud/acapi/op-20210901-catalog-workbooks-urlbase64workbookid-exports-post.html',
                          'https://docs.oracle.com/en/cloud/paas/analytics-cloud/acapi/op-20210901-workrequests-id-get.html'])
PRODUCTS += pair('bi', 'MicroStrategy ONE', 'microstrategy', label='MicroStrategy ONE', how='REST API ile pano (dashboard/dossier) örneği açılır ve PDF olarak dışa aktarılır; oturum kapatılır.',
                 agent_how='küpün tanımındaki öznitelik ve metriklerden (/api/v2/cubes/{id})',
                 config={'server': 'https://YOUR_ENV.microstrategy.com', 'project_id': 'YOUR_PROJECT_ID', 'dashboard_id': 'YOUR_32_CHAR_DASHBOARD_ID', 'login_mode': 1},
                 agent_config={'cube_id': 'YOUR_32_CHAR_CUBE_ID'}, secrets=['MSTR_USER', 'MSTR_PASSWORD'], outputs={'code': 'rapor.pdf + rapor.json'},
                 requirements='Library REST API erişimi, projede pano çalıştırma ve PDF dışa aktarma hakkı. login_mode 1 standart, 16 LDAP.',
                 sources=['https://www2.microstrategy.com/producthelp/Current/RESTSDK/Content/topics/REST_API/REST_API.htm', 'https://github.com/MicroStrategy/data-api-demo'])
PRODUCTS += pair('bi', 'Superset', 'superset', label='Superset', how='panodaki her grafiğin verisi Superset’in kendi sorgu motoruyla chart data API’sinden CSV olarak üretilir.',
                 agent_how='veri kümesinin kolon ve kayıtlı metrik tanımlarından',
                 config={'server': 'https://YOUR_SUPERSET', 'auth_provider': 'db', 'dashboard_id': 1}, agent_config={'dataset_id': 1},
                 secrets=['SUPERSET_USER', 'SUPERSET_PASSWORD'], outputs={'code': 'grafik_<id>.csv + rapor.json'},
                 requirements='Panoda ve veri kümelerinde can_read; can_csv izni. provider db veya ldap.',
                 sources=['https://superset.apache.org/docs/api', 'https://superset.apache.org/user-docs/using-superset/exporting-dashboard-data'])
PRODUCTS += pair('bi', 'Metabase', 'metabase', label='Metabase', how='panodaki her soru (kart) Metabase’in sorgu motoruyla XLSX/CSV olarak dışa aktarılır.',
                 agent_how='tablonun query_metadata alan tanımlarından (tip ve semantik tip)',
                 config={'server': 'https://YOUR_METABASE', 'dashboard_id': 1, 'format': 'xlsx'}, agent_config={'table_id': 1}, secrets=['METABASE_API_KEY'],
                 outputs={'code': 'kart_<id>.xlsx/csv + rapor.json'}, requirements='Panoyu ve kartları görebilen gruba bağlı API anahtarı; indirme (download results) izni.',
                 sources=['https://www.metabase.com/docs/latest/api#tag/apicard/POST/api/card/{card-id}/query/{export-format}', 'https://www.metabase.com/docs/latest/api#tag/apitable'])

# ---------- ERP ----------
REC = {'keys': ['kod'], 'compare_fields': [], 'tolerance': 0.01, 'target_file': 'hedef.csv'}
MAP = {'source_file': 'kaynak.csv'}
for _key, _product, _service in (('s4hana', 'S/4HANA', '/sap/opu/odata/sap/API_BUSINESS_PARTNER'), ('ecc', 'ECC', '/sap/opu/odata/sap/YOUR_GATEWAY_SERVICE_SRV')):
    PRODUCTS += pair('erp', _product, _key, label='SAP ' + _product, how='SAP Gateway/OData servisinden ($select/$filter, sunucu sayfalaması; $metadata)',
                     config={'server': 'https://YOUR_SAP_HOST', 'sap_client': '100', 'service': _service, 'entity_set': 'A_BusinessPartner' if _key == 's4hana' else 'YOUR_ENTITY_SET',
                             'fields': [], 'filter': '', **REC}, agent_config=MAP, secrets=['SAP_USER', 'SAP_PASSWORD'],
                     requirements=('İletişim kullanıcısı (communication arrangement) veya diyalog kullanıcısı; servis ve varlıkta okuma yetkisi. OAuth için .env’e SAP_ACCESS_TOKEN yazılırsa o kullanılır.'
                                   if _key == 's4hana' else 'ECC’de verinin bir SAP Gateway OData servisiyle (SEGW/standart servis) yayınlanmış olması gerekir; RFC/BAPI erişimi bu pakette yoktur.'),
                     sources=['https://api.sap.com/api/API_BUSINESS_PARTNER/overview', 'https://help.sap.com/docs/SAP_NETWEAVER_750'] if _key == 's4hana' else
                             ['https://help.sap.com/docs/SAP_NETWEAVER_750', 'https://www.odata.org/documentation/odata-version-2-0/'])
PRODUCTS += pair('erp', 'Business One', 'business-one', label='SAP Business One', how='Service Layer oturumuyla (B1SESSION) OData varlığından; $metadata ile',
                 config={'server': 'https://YOUR_B1_SERVER:50000', 'company_db': 'YOUR_COMPANY_DB', 'entity_set': 'BusinessPartners', 'fields': ['CardCode', 'CardName', 'CurrentAccountBalance'],
                         'filter': "CardType eq 'cCustomer'", **{**REC, 'keys': ['CardCode']}}, agent_config=MAP, secrets=['B1_USER', 'B1_PASSWORD'],
                 requirements='Service Layer (HANA/SQL) erişimi ve B1 kullanıcısının varlıkta okuma yetkisi. Oturum sonunda Logout çağrılır.',
                 sources=['https://help.sap.com/doc/056f69366b5345a386bb8149f1700c19/10.0/en-US/Service%20Layer%20API%20Reference.html'])
for _key, _product, _label in (('logo-tiger', 'Tiger 3', 'Logo Tiger 3'), ('logo-go', 'GO 3', 'Logo GO 3')):
    PRODUCTS += pair('erp', _product, _key, label=_label, how='Logo’nun firma/dönem tablolarından (LG_FFF_CLCARD, LG_FFF_DD_CLFLINE, LG_FFF_ITEMS) salt-okunur SQL ile',
                     config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_SQL_SERVER;Database=YOUR_LOGO_DB;Encrypt=yes;TrustServerCertificate=no;UID=YOUR_READ_USER',
                             'firm': '001', 'period': '01', 'preset': 'cari', **REC}, agent_config=MAP, secrets=['DB_PASSWORD'], packages=['pyodbc>=5'],
                     requirements='Logo veritabanında yalnızca SELECT yetkili SQL kullanıcısı. Hazır sorgular: cari (kod, unvan, vergi no, iptal edilmemiş hareketlerden bakiye) ve malzeme; '
                                  'preset: ozel_sorgu ile sorgu.sql içindeki tek SELECT çalışır. Logo’nun REST/Objects arayüzü kullanılmaz; ERP’ye yazılmaz.',
                     sources=['https://docs.logo.com.tr/', 'https://learn.microsoft.com/en-us/sql/connect/odbc/microsoft-odbc-driver-for-sql-server'])
PRODUCTS += pair('erp', 'Netsis', 'netsis', label='Logo Netsis', how='Netsis tablolarından (TBLCASABIT, TBLCAHAR, TBLSTSABIT) salt-okunur SQL ile',
                 config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_SQL_SERVER;Database=YOUR_NETSIS_DB;Encrypt=yes;UID=YOUR_READ_USER', 'preset': 'cari', **REC},
                 agent_config=MAP, secrets=['DB_PASSWORD'], packages=['pyodbc>=5'],
                 requirements='Şirket veritabanında SELECT yetkili kullanıcı. Hazır sorgular: cari (borç-alacak bakiyesi) ve stok; ozel_sorgu ile tek SELECT. NetOpenX kullanılmaz.',
                 sources=['https://docs.logo.com.tr/', 'https://learn.microsoft.com/en-us/sql/connect/odbc/microsoft-odbc-driver-for-sql-server'])
PRODUCTS += pair('erp', 'j-Platform', 'j-platform', label='Logo j-Platform', how='j-Platform veritabanında sizin verdiğiniz tek salt-okunur SELECT ile (sorgu.sql; tablo adları sürüme göre değiştiğinden hazır sorgu yok)',
                 config={'database_type': 'mssql', 'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_SQL_SERVER;Database=YOUR_JPLATFORM_DB;Encrypt=yes;UID=YOUR_READ_USER',
                         'preset': 'ozel_sorgu', 'query_file': 'sorgu.sql', **REC}, agent_config={**MAP, 'target_tables': ['YOUR_TARGET_TABLE']}, secrets=['DB_PASSWORD'], packages=['pyodbc>=5'],
                 files={'sorgu.sql': '-- Tek bir salt-okunur SELECT yazın; anahtar kolonu config.json keys ile aynı adla döndürün.\nSELECT 1 AS kod\n'},
                 requirements='SELECT yetkili veritabanı kullanıcısı (SQL Server; Oracle/PostgreSQL için database_type ve ilgili bağlantı alanları). Sorgu yerelde tek SELECT olarak denetlenir.',
                 sources=['https://docs.logo.com.tr/'])
PRODUCTS += pair('erp', 'Fusion Cloud ERP', 'fusion', label='Oracle Fusion Cloud ERP', how='Fusion REST API kaynağından (fields, q, sayfalı) ve kaynağın /describe tanımından',
                 config={'server': 'https://YOUR_POD.fa.ocs.oraclecloud.com', 'resource': 'receivablesInvoices', 'fields': ['TransactionNumber', 'InvoiceBalanceAmount'], 'filter': '',
                         **{**REC, 'keys': ['TransactionNumber']}}, agent_config=MAP, secrets=['FUSION_USER', 'FUSION_PASSWORD'],
                 requirements='Kaynağı görüntüleme rolü olan kullanıcı (temel kimlik doğrulama) veya .env’de FUSION_ACCESS_TOKEN (OAuth). Bazı kaynaklar özellik etkinleştirmesi gerektirir.',
                 sources=['https://docs.oracle.com/en/cloud/saas/financials/25a/farfa/index.html', 'https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/26a/fasrp/Quick_Start.html'])
PRODUCTS += pair('erp', 'E-Business Suite', 'ebs', label='Oracle E-Business Suite', how='EBS standart tablolarından (HZ_CUST_ACCOUNTS, HZ_PARTIES, AP_SUPPLIERS, AR_PAYMENT_SCHEDULES_ALL) python-oracledb ile salt-okunur',
                 config={'user': 'YOUR_READ_USER', 'dsn': 'YOUR_HOST:1521/YOUR_SERVICE', 'preset': 'musteri', **REC}, agent_config=MAP, secrets=['DB_PASSWORD'], packages=['oracledb>=2'],
                 requirements='APPS şemasına SELECT eşanlamlısı olan salt-okunur kullanıcı. Çoklu kuruluş (MOAC) görünümleri yerine _ALL tabloları okunur; org filtresini ozel_sorgu ile ekleyin.',
                 sources=['https://docs.oracle.com/cd/E26401_01/doc.122/e48826/toc.htm', 'https://python-oracledb.readthedocs.io/'])
PRODUCTS += pair('erp', 'Dynamics 365 Finance', 'd365-finance', label='Dynamics 365 Finance', how='OData veri varlığından (/data, cross-company) ve $metadata belgesinden',
                 config={'environment_url': 'https://YOUR_ENV.operations.dynamics.com', 'entity_set': 'CustomersV3', 'fields': ['CustomerAccount', 'OrganizationName', 'dataAreaId'],
                         'filter': '', 'cross_company': True, **{**REC, 'keys': ['CustomerAccount', 'dataAreaId']}}, agent_config=MAP, secrets=['D365_ACCESS_TOKEN'],
                 requirements='Entra ID uygulaması ile alınmış ortam belirteci (kaynak = ortam URL’si) ve uygulamaya D365’te güvenlik rolü atanmış olmalı. $metadata büyüktür (on MB’larca).',
                 sources=['https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/data-entities/odata', 'https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/data-entities/services-home-page'])
PRODUCTS += pair('erp', 'Business Central', 'business-central', label='Business Central', how='API v2.0 şirket varlığından ve $metadata belgesinden',
                 config={'tenant_id': 'YOUR_TENANT_ID', 'environment': 'production', 'company_id': 'YOUR_COMPANY_GUID', 'entity_set': 'customers', 'fields': ['number', 'displayName', 'balanceDue'],
                         'filter': '', **{**REC, 'keys': ['number']}}, agent_config=MAP, secrets=['BC_ACCESS_TOKEN'],
                 requirements='API.ReadWrite.All veya Financials.ReadWrite.All kapsamlı belirteç ve Business Central’da izin kümesi atanmış Entra uygulaması/kullanıcı.',
                 sources=['https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/', 'https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/api/dynamics_customer_get'])
PRODUCTS += pair('erp', 'Odoo', 'odoo', label='Odoo', how='JSON-RPC dış API’siyle (search_read, fields_get)',
                 config={'server': 'https://YOUR_ODOO', 'database': 'YOUR_DB', 'model': 'res.partner', 'fields': ['ref', 'name', 'vat', 'credit'], 'domain': [['customer_rank', '>', 0]],
                         **{**REC, 'keys': ['ref']}}, agent_config=MAP, secrets=['ODOO_USER', 'ODOO_API_KEY'],
                 requirements='Kullanıcı ve API anahtarı (Tercihler > Hesap Güvenliği); modelde okuma erişimi. JSON-RPC Odoo 19’da hâlâ desteklenir, Odoo 20’de kaldırılması planlanmıştır.',
                 sources=['https://www.odoo.com/documentation/18.0/developer/reference/external_api.html'])
PRODUCTS += pair('erp', 'IFS Cloud', 'ifs', label='IFS Cloud', how='projeksiyon OData servisinden (/main/ifsapplications/projection/v1) ve $metadata belgesinden',
                 config={'server': 'https://YOUR_IFS', 'projection': 'CustomerHandling', 'entity_set': 'CustomerInfoSet', 'fields': ['CustomerId', 'Name'], 'filter': '',
                         **{**REC, 'keys': ['CustomerId']}}, agent_config=MAP, secrets=['IFS_ACCESS_TOKEN'],
                 requirements='IFS IAM’dan alınmış OAuth belirteci ve projeksiyona izin seti (permission set) erişimi.',
                 sources=['https://docs.ifs.com/techdocs/'])
PRODUCTS += pair('erp', 'Workday', 'workday', label='Workday', how='Rapor-hizmet olarak (RaaS) özel rapordan JSON ve rapor XSD tanımından',
                 config={'server': 'https://wd2-impl-services1.workday.com', 'tenant': 'YOUR_TENANT', 'report_owner': 'ISU_USER', 'report_name': 'YOUR_RAAS_REPORT', 'report_params': {},
                         **{**REC, 'keys': ['Employee_ID']}}, agent_config=MAP, secrets=['WORKDAY_USER', 'WORKDAY_PASSWORD'],
                 requirements='Raporun web hizmeti olarak etkinleştirilmesi (Enable As Web Service) ve entegrasyon sistem kullanıcısının (ISU) rapor alanlarına erişimi. Eşleme hedefi raporun alan yapısıdır.',
                 sources=['https://doc.workday.com/'])

# ---------- Data integration ----------
ADF_CFG = {'subscription_id': 'YOUR_SUBSCRIPTION_ID', 'resource_group': 'YOUR_RESOURCE_GROUP', 'factory_name': 'YOUR_FACTORY'}
PRODUCTS += pair('integration', 'Azure Data Factory', 'adf', label='Azure Data Factory', how='queryPipelineRuns ve Copy etkinliklerinin queryActivityruns çıktısındaki rowsRead/rowsCopied/rowsSkipped alanlarından',
                 agent_how='veri kümesi şemalarından (schema/structure) ve pipeline etkinlik listesinden',
                 config={**ADF_CFG, 'pipelines': [], 'days': 7, 'max_runs': 50}, secrets=['AZURE_ACCESS_TOKEN'],
                 requirements='Fabrikada Data Factory Contributor veya okuma rolü (management.azure.com belirteci).',
                 sources=['https://learn.microsoft.com/en-us/rest/api/datafactory/activity-runs/query-by-pipeline-run', 'https://learn.microsoft.com/en-us/azure/data-factory/copy-activity-monitoring'])
PRODUCTS += pair('integration', 'SSIS', 'ssis', label='SSIS', how='SSISDB catalog.executions ve catalog.execution_data_statistics görünümlerinden (veri yolu satırları; kaynak/hedef/hata yolları) ODBC ile',
                 agent_how='.dtsx paketlerindeki kaynak/hedef bileşenlerinin dış metadata kolonlarından',
                 config={'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_SSIS_SERVER;Database=SSISDB;Encrypt=yes;Trusted_Connection=yes', 'packages': [], 'days': 7},
                 agent_config={'packages_dir': 'paketler'}, secrets=[], packages=['pyodbc>=5'],
                 requirements='SSISDB’de ssis_logreader veya okuma yetkisi. Satır istatistikleri yalnızca Verbose (veya özel) günlük düzeyindeki çalışmalarda tutulur. ' + MSSQL_DRIVER,
                 sources=['https://learn.microsoft.com/en-us/sql/integration-services/system-views/catalog-execution-data-statistics', 'https://learn.microsoft.com/en-us/sql/integration-services/performance/integration-services-ssis-logging'])
PRODUCTS += pair('integration', 'Fabric Data Factory', 'fabric-data-factory', label='Fabric Data Factory', how='pipeline iş örneklerinden ve queryactivityruns API’sinin Copy etkinliği çıktısından',
                 agent_how='pipeline tanımındaki (getDefinition) Copy etkinliklerinin kaynak/hedef ve kolon eşlemelerinden',
                 config={'workspace_id': 'YOUR_WORKSPACE_UUID', 'pipeline_id': 'YOUR_PIPELINE_UUID', 'days': 7, 'max_runs': 20}, secrets=['FABRIC_ACCESS_TOKEN'],
                 requirements='Çalışma alanında Viewer/Contributor ve Item.Read.All/DataPipeline.Read.All kapsamlı belirteç; getDefinition için okuma-yazma kapsamı gerekebilir.',
                 sources=['https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-rest-api', 'https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/list-item-job-instances'])
PRODUCTS += pair('integration', 'IDMC', 'idmc', label='Informatica IDMC', how='v2 oturumuyla activityLog kayıtlarındaki successSourceRows/successTargetRows/failedTargetRows alanlarından',
                 agent_how='kaynak ve hedef bağlantılarının nesne metadata uç noktasından (alan adları ve tipleri)',
                 config={'login_url': 'https://dm-us.informaticacloud.com', 'task_id': '', 'max_runs': 200},
                 agent_config={'source_connection_id': 'YOUR_SOURCE_CONN', 'source_objects': ['YOUR_SOURCE_TABLE'], 'target_connection_id': 'YOUR_TARGET_CONN', 'target_objects': ['YOUR_TARGET_TABLE']},
                 secrets=['IDMC_USER', 'IDMC_PASSWORD'], requirements='Bölgenize uygun login_url (dm-us, dm-em, dm-ap…) ve izleme/bağlantı okuma yetkili IDMC kullanıcısı.',
                 sources=['https://docs.informatica.com/integration-cloud/data-integration/current-version/rest-api-reference/platform-rest-api-version-2-resources/activitylog.html',
                          'https://docs.informatica.com/integration-cloud/data-integration/current-version/rest-api-reference/platform-rest-api-version-2-resources/connections.html'])
PRODUCTS += pair('integration', 'PowerCenter', 'powercenter', label='Informatica PowerCenter', how='repository MX görünümü REP_SESS_LOG’daki başarılı/başarısız kaynak ve hedef satırlarından',
                 agent_how='repository MX görünümlerindeki (REP_SRC_TBL_FLDS, REP_TARG_TBL_COLS) kaynak/hedef kolonlarından',
                 config={'repository_type': 'oracle', 'user': 'YOUR_REPO_READ_USER', 'dsn': 'YOUR_HOST:1521/YOUR_SERVICE', 'folders': [], 'days': 7},
                 agent_config={'folders': ['YOUR_FOLDER']}, secrets=['DB_PASSWORD'], packages=['oracledb>=2'],
                 requirements='Repository veritabanında MX görünümlerine SELECT (repository_type mssql ise odbc alanı ve pyodbc). PowerCenter servislerine bağlanılmaz.',
                 sources=['https://docs.informatica.com/data-integration/powercenter/10-5/repository-guide/using-metadata-exchange--mx--views.html'])
PRODUCTS += pair('integration', 'Talend Cloud', 'talend-cloud', label='Talend Cloud', how='görev çalışmaları ve gözlem (observability) bileşen metriklerinden (component_connection_rows_total; kaynak/hedef/ret bağlantıları)',
                 agent_how='dışa aktarılmış iş (.item) dosyalarındaki giriş/çıkış bileşenlerinin şema kolonlarından',
                 config={'region': 'eu', 'task_id': 'YOUR_TASK_ID', 'days': 7, 'max_runs': 20}, agent_config={'jobs_dir': 'talend_proje'}, secrets=['TALEND_TOKEN'],
                 requirements='Operator rolü/TMC_OPERATOR izinli kişisel erişim belirteci; Cloud Engine veya Remote Engine 2.12+. Metrikler 31 gün tutulur.',
                 sources=['https://talend.qlik.dev/apis/observability-metrics/2021-03/', 'https://help.qlik.com/talend/en-US/api-user-guide/Cloud/component-metrics-api-in-talend-cloud-management-console'])
PRODUCTS += pair('integration', 'Qlik Replicate', 'qlik-replicate', label='Qlik Replicate', how='Enterprise Manager API GetTableStatuses yanıtından (tam yükleme aktarılan satır, CDC ekleme/güncelleme/silme) ve isteğe bağlı hedef sayım dosyasından',
                 agent_how='görev tanımının dışa aktarımından (seçilen tablolar ve dönüşümler)',
                 config={'server': 'https://YOUR_QEM_SERVER', 'replicate_server': 'YOUR_REPLICATE_SERVER', 'task': 'YOUR_TASK', 'counts_file': 'hedef_sayimlari.csv'},
                 secrets=['QEM_USER', 'QEM_PASSWORD'], requirements='Enterprise Manager’da Viewer rolü (dışa aktarma için Designer). counts_file (tablo,satir) verilirse hedefteki COUNT(*) değerleri karşılaştırılır.',
                 sources=['https://help.qlik.com/en-US/enterprise-manager/May2026/Content/EnterpriseManager/EnterpriseManager_APIGuide/CurlAPI/api_getTableStatus.htm'])
PRODUCTS += pair('integration', 'Fivetran', 'fivetran', label='Fivetran', how='hedef ambarda tablo başına COUNT(*) ile (kaynak sayımları counts_file’dan) ve isteğe bağlı Fivetran Platform Connector LOG tablosundaki records_modified olaylarından',
                 agent_how='bağlantının şema yapılandırmasından (etkin şema/tablo/kolonlar, senkron modu)',
                 config={'destination': 'snowflake', 'server': 'https://YOUR_ACCOUNT.snowflakecomputing.com', 'warehouse': 'COMPUTE_WH', 'role': 'YOUR_READ_ROLE', 'timeout_seconds': 120,
                         'tables': ['YOUR_SCHEMA.YOUR_TABLE'], 'counts_file': 'kaynak_sayimlari.csv', 'log_schema': 'FIVETRAN_METADATA', 'connection_id': 'YOUR_CONNECTION_ID', 'days': 7},
                 secrets=['SNOWFLAKE_ACCESS_TOKEN', 'FIVETRAN_API_KEY', 'FIVETRAN_API_SECRET'],
                 requirements='Hedef ambarda okuma (destination: snowflake/bigquery/databricks/redshift/postgres/mssql; bağlantı alanları ilgili ambarınkiyle aynı) ve agent için Fivetran API anahtarı.',
                 sources=['https://fivetran.com/docs/logs/fivetran-platform', 'https://fivetran.com/docs/rest-api/api-reference/connection-schema/connection-schema-config'])
PRODUCTS += pair('integration', 'Airbyte', 'airbyte', label='Airbyte', how='kendi kurulumunuzda Config API iş denemelerinin akış istatistiklerinden (recordsEmitted/recordsCommitted), Airbyte Cloud’da iş başına rowsSynced değerinden',
                 agent_how='kaynak-hedef çiftinin akış listesinden (özellik alanları, senkron modları, birincil anahtar)',
                 config={'connection_id': 'YOUR_CONNECTION_ID', 'config_api': False, 'server': '', 'max_runs': 10}, agent_config={'source_id': 'YOUR_SOURCE_ID', 'destination_id': 'YOUR_DESTINATION_ID'},
                 secrets=['AIRBYTE_ACCESS_TOKEN'], requirements='Airbyte Cloud API belirteci (Cloud) veya config_api: true ile kendi sunucunuzun adresi ve AIRBYTE_TOKEN. Cloud genel API’si akış bazında okunan sayısı vermez.',
                 sources=['https://reference.airbyte.com/reference/listjobs', 'https://reference.airbyte.com/reference/getstreamproperties'])
PRODUCTS += pair('integration', 'Data Services', 'sap-data-services', label='SAP Data Services', how='iş sunucusunun monitor_*.txt günlüklerindeki nesne satır sayılarından (kaynak ve hedef nesne adlarıyla)',
                 agent_how='repository ALVW_COLUMNINFO görünümündeki datastore tablo/kolonlarından',
                 config={'monitor_log_dir': 'YOUR_LINK_DIR/log/JobServer/repo', 'source_objects': ['YOUR_SOURCE_TABLE'], 'target_objects': ['YOUR_TARGET_TABLE'], 'max_runs': 20},
                 agent_config={'repository_type': 'mssql', 'odbc': 'Driver={ODBC Driver 18 for SQL Server};Server=YOUR_SERVER;Database=YOUR_DS_REPO;Encrypt=yes;Trusted_Connection=yes',
                               'datastores': ['YOUR_SOURCE_DS', 'YOUR_TARGET_DS'], 'source_datastore': 'YOUR_SOURCE_DS', 'target_datastore': 'YOUR_TARGET_DS'},
                 secrets=[], packages=['pyodbc>=5'],
                 requirements='Monitor günlüklerine okuma erişimi (yalnızca dosya okunur); agent için repository veritabanında SELECT. Kaynak/hedef nesne adları dataflow’daki adlarla aynı olmalı.',
                 sources=['https://help.sap.com/docs/SAP_DATA_SERVICES', 'https://dwbi.org/pages/152'])
PRODUCTS += pair('integration', 'Data Integrator', 'odi', label='Oracle Data Integrator', how='iş repository’sinin SNP_STEP_LOG/SNP_SESSION tablolarındaki NB_ROW, NB_INS, NB_UPD, NB_ERR sayaçlarından',
                 agent_how='SNP_MODEL/SNP_TABLE/SNP_COL tablolarındaki kaynak ve hedef model datastore kolonlarından',
                 config={'user': 'YOUR_REPO_READ_USER', 'dsn': 'YOUR_HOST:1521/YOUR_SERVICE', 'work_schema': 'ODI_REPO', 'days': 7},
                 agent_config={'models': ['SRC_MODEL', 'TGT_MODEL'], 'source_model': 'SRC_MODEL', 'target_model': 'TGT_MODEL'}, secrets=['DB_PASSWORD'], packages=['oracledb>=2'],
                 requirements='İş (work) ve ana repository şemalarında SELECT yetkili salt-okunur kullanıcı.',
                 sources=['https://docs.oracle.com/en/middleware/fusion-middleware/data-integrator/12.2.1.4/odidg/index.html'])
PRODUCTS += pair('integration', 'GoldenGate', 'goldengate', label='Oracle GoldenGate', how='Microservices REST API ile Extract ve Replicat üzerinde STATS TOTAL komutunun tablo bazında toplam işlem sayılarından',
                 agent_how='Extract/Replicat parametre dosyalarındaki TABLE ve MAP ifadelerinden',
                 config={'server': 'https://YOUR_GG_DEPLOYMENT', 'extract': 'EXT1', 'replicat': 'REP1', 'table_map': {}}, secrets=['GG_USER', 'GG_PASSWORD'],
                 requirements='Dağıtımda Operator rolü. STATS çıktısı süreç başlangıcından beri birikir; kaynak→hedef farklı adlandırmada table_map kullanın. Komut yanıt biçimi sürüme göre değişebilir.',
                 sources=['https://docs.oracle.com/en/middleware/goldengate/core/23/oggra/index.html', 'https://blogs.oracle.com/dataintegration/post/using-rest-apis-with-oracle-cloud-infrastructure-oci-goldengate'])
PRODUCTS += pair('integration', 'Meltano', 'meltano', label='Meltano', how='meltano run günlüklerindeki Singer SDK record_count metriklerinden (tap üretir, target tüketir)',
                 agent_how='tap’in keşif kataloğundan (catalog.json akışları, özellikleri ve anahtarları) ve meltano.yml’den',
                 config={'log_files': ['meltano_run.log']}, agent_config={'catalog_file': 'catalog.json', 'meltano_file': 'meltano.yml'}, secrets=[],
                 requirements='Meltano/Singer SDK tabanlı tap ve target’ların metrik günlüğü açık olmalı (varsayılan). Ağ bağlantısı gerekmez.',
                 sources=['https://sdk.meltano.com/en/latest/implementation/metrics.html', 'https://docs.meltano.com/reference/command-line-interface/#run'])

# ---------- Analytics engineering ----------
PRODUCTS += pair('transform', 'dbt Core', 'dbt-core', label='dbt Core', how='target/manifest.json (ve varsa catalog.json) artefaktlarından',
                 config={'manifest_file': 'target/manifest.json', 'catalog_file': 'target/catalog.json'}, secrets=[],
                 requirements='`dbt parse` veya `dbt compile` ile üretilmiş manifest.json; kolon tipleri için `dbt docs generate` catalog.json. Ağ bağlantısı gerekmez.',
                 sources=['https://docs.getdbt.com/reference/artifacts/manifest-json', 'https://docs.getdbt.com/reference/resource-properties/data-tests'])
PRODUCTS += pair('transform', 'dbt Cloud', 'dbt-cloud', label='dbt Cloud', how='işin son başarılı çalışmasının manifest.json artefaktından Admin API v2 ile',
                 config={'server': 'https://cloud.getdbt.com', 'account_id': 'YOUR_ACCOUNT_ID', 'job_id': 'YOUR_JOB_ID'}, secrets=['DBT_CLOUD_TOKEN'],
                 requirements='Hesapta Read-only/Job Viewer izinli servis belirteci; bölgesel adres (cloud.getdbt.com, emea.dbt.com, au.dbt.com veya hücre adresi).',
                 sources=['https://docs.getdbt.com/dbt-cloud/api-v2#/operations/Retrieve%20Run%20Artifact', 'https://docs.getdbt.com/reference/artifacts/manifest-json'])
PRODUCTS += pair('transform', 'Dataform', 'dataform', label='Dataform', how='son (veya verilen) derleme sonucunun compilationResults.query eylemlerinden (relation/operations, bağımlılık hedefleri, assertion’lar)',
                 config={'repository': 'projects/YOUR_PROJECT/locations/YOUR_LOCATION/repositories/YOUR_REPOSITORY', 'compilation_result': ''}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements='dataform.compilationResults.list/get/query izinleri (Dataform Viewer). Derleme sonucu yoksa önce Dataform’da derleyin.',
                 sources=['https://docs.cloud.google.com/dataform/reference/rest/v1/projects.locations.repositories.compilationResults/query',
                          'https://cloud.google.com/dataform/docs/assertions'])
PRODUCTS += pair('transform', 'SQLMesh', 'sqlmesh', label='SQLMesh', how='proje models/ klasöründeki MODEL(...) başlıklı SQL modellerinden (ad, kind, audits)',
                 config={'project_dir': 'sqlmesh_proje'}, secrets=[], requirements='SQLMesh proje klasörü (SQL modelleri; Python modelleri kapsam dışı). Ağ bağlantısı gerekmez.',
                 sources=['https://sqlmesh.readthedocs.io/en/stable/concepts/models/overview/', 'https://sqlmesh.readthedocs.io/en/stable/concepts/audits/'])
PRODUCTS += pair('transform', 'Coalesce', 'coalesce', label='Coalesce', how='ortamın düğüm listesinden (detail=true; kolonlar ve kaynak eşleme bağımlılıkları)',
                 config={'server': 'https://app.coalescesoftware.io', 'environment_id': 'YOUR_ENVIRONMENT_ID'}, secrets=['COALESCE_TOKEN'],
                 requirements='Coalesce erişim belirteci (Deploy > Access Tokens) ve ortamı görme yetkisi. Ayrıntılı düğüm alan adları sürüme göre değişebilir; eksik alanlar boş bırakılır.',
                 sources=['https://docs.coalesce.io/docs/api/coalesce/get-nodes'])
PRODUCTS += pair('transform', 'Cube', 'cube', label='Cube', how='REST /v1/meta küp, ölçü ve boyut tanımlarından (varsa model_dir’deki YAML/JS dosyalarının sql_table ve join’lerinden)',
                 config={'server': 'https://YOUR_DEPLOYMENT.cubecloud.dev', 'model_dir': 'model'}, secrets=['CUBE_TOKEN'],
                 requirements='API secret ile imzalanmış JWT (CUBE_TOKEN) ve meta uç noktasına erişim. Cube’un test sözdizimi olmadığından agent testleri SQL olarak verir.',
                 sources=['https://cube.dev/docs/product/apis-integrations/rest-api/reference#base_pathv1meta', 'https://cube.dev/docs/product/data-modeling/reference/cube'])

# ---------- Orchestration & DataOps ----------
PRODUCTS += pair('orchestration', 'Airflow', 'airflow', label='Apache Airflow', only=('agent',), how='Airflow 3 public API v2 DAG çalışmaları, başarısız görev örnekleri ve görev loglarından',
                 config={'server': 'https://YOUR_AIRFLOW', 'days': 7, 'max_logs': 15}, secrets=['AIRFLOW_ACCESS_TOKEN'],
                 requirements='DAG run, task instance ve log okuma yetkili Bearer belirteç (Airflow 3 /auth/token veya kimlik sağlayıcınız).',
                 sources=['https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html'])
PRODUCTS += pair('orchestration', 'Dagster', 'dagster', label='Dagster', how='GraphQL runsOrError ve logsForRun (ExecutionStepFailureEvent) sorgularından',
                 config={'server': 'https://YOUR_ORG.dagster.cloud/prod', 'days': 7, 'max_logs': 15}, secrets=['DAGSTER_CLOUD_API_TOKEN'],
                 requirements='Dagster+ için kullanıcı/ajan API belirteci (Dagster-Cloud-Api-Token); açık kaynak kurulumda belirteç boş bırakılır ve server Dagster web adresidir.',
                 sources=['https://docs.dagster.io/api/graphql', 'https://docs.dagster.io/deployment/dagster-plus/management/tokens'])
PRODUCTS += pair('orchestration', 'Prefect', 'prefect', label='Prefect', how='flow_runs/filter, flows/filter, task_runs/filter ve logs/filter uç noktalarından',
                 config={'api_url': 'https://api.prefect.cloud/api/accounts/YOUR_ACCOUNT/workspaces/YOUR_WORKSPACE', 'days': 7, 'max_logs': 15}, secrets=['PREFECT_API_KEY'],
                 requirements='Çalışma alanında okuma yetkili API anahtarı (Prefect Cloud) veya kendi sunucunuzun API adresi.',
                 sources=['https://docs.prefect.io/v3/api-ref/rest-api/server/flow-runs/read-flow-runs', 'https://docs.prefect.io/v3/api-ref/rest-api/server/logs/read-logs'])
PRODUCTS += pair('orchestration', 'Astro', 'astro', label='Astro', how='Astro dağıtımının Airflow API v2 uç noktasından (DAG çalışmaları, başarısız görevler, loglar)',
                 config={'deployment_url': 'https://YOUR_ORG.astronomer.run/YOUR_DEPLOYMENT', 'days': 7, 'max_logs': 15}, secrets=['ASTRO_API_TOKEN'],
                 requirements='Dağıtım veya çalışma alanı API belirteci (Deployment API token) ve Airflow 3 çalıştıran Astro dağıtımı.',
                 sources=['https://www.astronomer.io/docs/astro/airflow-api', 'https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html'])
PRODUCTS += pair('orchestration', 'Kestra', 'kestra', label='Kestra', how='executions/search ve yürütme logları (ERROR düzeyi) uç noktalarından',
                 config={'server': 'https://YOUR_KESTRA', 'tenant': '', 'namespace': '', 'days': 7, 'max_logs': 15}, secrets=['KESTRA_USER', 'KESTRA_PASSWORD'],
                 requirements='Temel kimlik doğrulama veya .env’de KESTRA_TOKEN (Enterprise API token); çok kiracılı kurulumda tenant.',
                 sources=['https://kestra.io/docs/api-reference/open-source'])
PRODUCTS += pair('orchestration', 'Actions', 'github-actions', label='GitHub Actions', how='workflow çalışmaları, başarısız işler/adımlar ve check-run açıklamalarından REST API ile',
                 config={'repository': 'YOUR_ORG/YOUR_REPO', 'days': 7, 'max_logs': 15}, secrets=['GITHUB_TOKEN'],
                 requirements='Depoda actions:read ve checks:read izinli ince ayarlı erişim belirteci.',
                 sources=['https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository', 'https://docs.github.com/en/rest/checks/runs#list-check-run-annotations'])
PRODUCTS += pair('orchestration', 'Jenkins', 'jenkins', label='Jenkins', how='işlerin build listesi (JSON API) ve başarısız build’lerin consoleText sonundan',
                 config={'server': 'https://YOUR_JENKINS', 'jobs': [], 'days': 7, 'max_logs': 15}, secrets=['JENKINS_USER', 'JENKINS_API_TOKEN'],
                 requirements='Overall/Read ve Job/Read izinli kullanıcı ve API belirteci. Klasördeki işler için jobs alanına klasor/is biçiminde yazın.',
                 sources=['https://www.jenkins.io/doc/book/using/remote-access-api/'])

# ---------- Streaming ----------
EVQ = {'required_fields': [], 'id_field': '', 'event_time_field': '', 'max_lag_seconds': 3600, 'out_of_order_tolerance_seconds': 0}
KAFKA_HOW = 'Konunun her bölümünden son N mesaj, offset işlemeden (açık bölüm ataması, otomatik commit kapalı) confluent-kafka istemcisiyle örneklenir.'
KAFKA_REQ = ' Konuda Describe ve Read ACL’i yeterlidir; tüketici grubu offset’i işlenmez.'
PRODUCTS += pair('stream', 'Kafka', 'kafka', label='Apache Kafka', how=KAFKA_HOW, agent_how='Konudan offset işlemeden alınan örneklemin alan profilinden ve kalite bulgularından',
                 config={'bootstrap_servers': 'YOUR_BROKER:9093', 'security_protocol': 'SASL_SSL', 'sasl_mechanism': 'SCRAM-SHA-512', 'topic': 'YOUR_TOPIC', 'messages_per_partition': 500,
                         'max_seconds': 60, **EVQ}, secrets=['KAFKA_USERNAME', 'KAFKA_PASSWORD'], packages=['confluent-kafka>=2.3'],
                 requirements='SASL kullanıcı adı/parola (SCRAM/PLAIN) veya SSL; kimlik yoksa .env alanlarını boş bırakın.' + KAFKA_REQ,
                 sources=['https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html', 'https://kafka.apache.org/documentation/#consumerconfigs'])
PRODUCTS += pair('stream', 'Confluent Cloud', 'confluent-cloud', label='Confluent Cloud', how=KAFKA_HOW, agent_how='konu örnekleminin alan profilinden ve Schema Registry’deki güncel şema ile uyumluluk ayarından',
                 config={'bootstrap_servers': 'pkc-XXXXX.YOUR_REGION.confluent.cloud:9092', 'topic': 'YOUR_TOPIC', 'messages_per_partition': 500, 'max_seconds': 60,
                         'schema_registry_url': 'https://psrc-XXXXX.YOUR_REGION.confluent.cloud', 'subject': '', **EVQ},
                 secrets=['KAFKA_API_KEY', 'KAFKA_API_SECRET', 'SR_API_KEY', 'SR_API_SECRET'], packages=['confluent-kafka>=2.3'],
                 requirements='Küme API anahtarı (DeveloperRead) ve agent için Schema Registry API anahtarı. Avro/Protobuf değerler ikili olduğundan alan profili yalnızca JSON (JSON Schema) mesajlarda çıkar.' + KAFKA_REQ,
                 sources=['https://docs.confluent.io/cloud/current/client-apps/config-client.html', 'https://docs.confluent.io/platform/current/schema-registry/develop/api.html'])
PRODUCTS += pair('stream', 'Event Hubs', 'event-hubs', label='Azure Event Hubs', how=KAFKA_HOW.replace('confluent-kafka istemcisiyle', 'Event Hubs Kafka uç noktası (9093, SASL PLAIN) üzerinden confluent-kafka istemcisiyle'),
                 agent_how='Event Hub örnekleminin alan profilinden', config={'bootstrap_servers': 'YOUR_NAMESPACE.servicebus.windows.net:9093', 'topic': 'YOUR_EVENT_HUB',
                                                                              'messages_per_partition': 500, 'max_seconds': 60, **EVQ},
                 secrets=['EVENTHUBS_CONNECTION_STRING'], packages=['confluent-kafka>=2.3'],
                 requirements='Standard veya üstü katman (Kafka uç noktası) ve Listen hakkı olan paylaşılan erişim ilkesi bağlantı dizesi.',
                 sources=['https://learn.microsoft.com/en-us/azure/event-hubs/azure-event-hubs-apache-kafka-overview', 'https://learn.microsoft.com/en-us/azure/event-hubs/event-hubs-quickstart-kafka-enabled-event-hubs'])
PRODUCTS += pair('stream', 'Redpanda', 'redpanda', label='Redpanda', how=KAFKA_HOW.replace('confluent-kafka istemcisiyle', 'Redpanda’nın Kafka API’si üzerinden confluent-kafka istemcisiyle'),
                 agent_how='konu örnekleminin alan profilinden', config={'bootstrap_servers': 'YOUR_CLUSTER.cloud.redpanda.com:9092', 'security_protocol': 'SASL_SSL', 'sasl_mechanism': 'SCRAM-SHA-256',
                                                                      'topic': 'YOUR_TOPIC', 'messages_per_partition': 500, 'max_seconds': 60, **EVQ},
                 secrets=['KAFKA_USERNAME', 'KAFKA_PASSWORD'], packages=['confluent-kafka>=2.3'], requirements='SCRAM kullanıcısı ve konuda read ACL.' + KAFKA_REQ,
                 sources=['https://docs.redpanda.com/current/develop/kafka-clients/', 'https://docs.redpanda.com/current/manage/security/authorization/acl/'])
PRODUCTS += pair('stream', 'Kinesis', 'kinesis', label='Kinesis Data Streams', how='Her shard’dan son N dakikanın kayıtları AT_TIMESTAMP shard iterator’ı ve GetRecords ile (SigV4) okunur; tüketici kaydı oluşturulmaz.',
                 agent_how='akış örnekleminin alan profilinden', config={'region': 'eu-central-1', 'stream_name': 'YOUR_STREAM', 'minutes': 60, 'messages_per_partition': 500, **EVQ},
                 secrets=['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'], requirements='kinesis:ListShards, GetShardIterator ve GetRecords izinleri. Okuma, akışın okuma kapasitesinden pay kullanır.',
                 sources=['https://docs.aws.amazon.com/kinesis/latest/APIReference/API_GetRecords.html', 'https://docs.aws.amazon.com/kinesis/latest/APIReference/API_GetShardIterator.html'])
PRODUCTS += pair('stream', 'Pub/Sub', 'pubsub', label='Pub/Sub', how='Abonelikten pull ile alınan mesajlar onaylanmadan hemen geri bırakılır (modifyAckDeadline 0).',
                 agent_how='abonelik örnekleminin alan profilinden', config={'subscription': 'projects/YOUR_PROJECT/subscriptions/YOUR_QUALITY_SUBSCRIPTION', 'max_messages': 500,
                                                                         'key_attribute': '', **EVQ},
                 secrets=['GOOGLE_ACCESS_TOKEN'], requirements='Abonelikte pubsub.subscriptions.consume izni. Konuya bağlı ayrı bir “kalite” aboneliği kullanın: geri bırakılan mesajlar yeniden teslim edilir.',
                 sources=['https://cloud.google.com/pubsub/docs/reference/rest/v1/projects.subscriptions/pull', 'https://cloud.google.com/pubsub/docs/reference/rest/v1/projects.subscriptions/modifyAckDeadline'],
                 code_effects='Mesajlar onaylanmaz; ölü mektup (dead-letter) politikası varsa teslim denemesi sayacı artar.')
PRODUCTS += pair('stream', 'Flink', 'flink', label='Apache Flink', how='Çalışan işin operatör metrikleri (numRecordsIn/Out, numLateRecordsDropped), filigranları, backpressure ve checkpoint sayıları Flink REST API’sinden okunur.',
                 agent_how='işin yürütme planından (operatörler, paralellik) ve çalışma metriklerinden', config={'server': 'https://YOUR_FLINK_JOBMANAGER', 'job_id': 'YOUR_32_HEX_JOB_ID', 'max_lag_seconds': 3600},
                 secrets=[], requirements='JobManager REST adresine erişim (kimlik doğrulayan bir proxy varsa .env’e FLINK_TOKEN yazın).',
                 sources=['https://nightlies.apache.org/flink/flink-docs-stable/docs/ops/rest_api/', 'https://nightlies.apache.org/flink/flink-docs-stable/docs/ops/metrics/'])
PRODUCTS += pair('stream', 'Spark Streaming', 'spark-streaming', label='Spark Structured Streaming', how='Spark olay günlüğündeki QueryProgressEvent kayıtlarından (girdi/işlem hızı, batch süresi, filigran nedeniyle atılan satırlar) okunur.',
                 agent_how='sorgu ilerleme metriklerinden (kaynaklar, hızlar, filigran farkı)', config={'event_log_file': 'eventlog/app-XXXX', 'trigger_interval_seconds': 60}, secrets=[],
                 requirements='spark.eventLog.enabled=true ile yazılmış olay günlüğü (yerel kopya). Eski DStreams API’si kapsam dışıdır.',
                 sources=['https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html#monitoring-streaming-queries', 'https://spark.apache.org/docs/latest/monitoring.html'])
PRODUCTS += pair('stream', 'Dataflow', 'dataflow', label='Dataflow', how='İşin durumu ve metrikleri (ElementCount, geç atılan öğe sayaçları) Dataflow API v1b3 ile okunur.',
                 agent_how='işin adım listesinden ve metriklerinden', config={'project_id': 'YOUR_PROJECT_ID', 'location': 'europe-west1', 'job_id': 'YOUR_JOB_ID'}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements='dataflow.jobs.get ve dataflow.metrics.get izinleri (Dataflow Viewer).',
                 sources=['https://cloud.google.com/dataflow/docs/reference/rest/v1b3/projects.locations.jobs/getMetrics', 'https://cloud.google.com/dataflow/docs/reference/rest/v1b3/projects.locations.jobs/get'])

# ---------- Data quality ----------
PRODUCTS += pair('quality', 'GX', 'gx', label='Great Expectations (GX Core)', how='GX projenizdeki checkpoint’i GX Core kütüphanesiyle çalıştırarak (veya validation_dir’deki doğrulama sonucu JSON’larından)',
                 config={'project_dir': 'gx_proje', 'checkpoint': 'YOUR_CHECKPOINT', 'validation_dir': ''}, secrets=[], packages=['great_expectations>=1.0'],
                 requirements='GX Core 1.x dosya tabanlı bağlam (gx/ klasörü) ve veri kaynağı bağlantısı; checkpoint’in tanımladığı eylemler (ör. Data Docs) de çalışır. validation_dir verilirse kütüphane gerekmez.',
                 sources=['https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/run_a_checkpoint', 'https://docs.greatexpectations.io/docs/reference/api/checkpoint/CheckpointResult_class'],
                 code_effects='Checkpoint veri kaynağında sorgu çalıştırır ve yapılandırılmışsa sonuç eylemlerini tetikler.')
PRODUCTS += pair('quality', 'Soda Core', 'soda-core', label='Soda Core', how='SodaCL check dosyalarınızla Soda Core Scan API’sini yerelde çalıştırarak',
                 config={'data_source': 'YOUR_DATA_SOURCE', 'configuration_file': 'configuration.yml', 'checks_files': ['checks.yml']}, secrets=[], packages=['soda-core>=3.3'],
                 requirements='Kullandığınız veri kaynağının Soda paketi (ör. soda-core-postgres, soda-core-snowflake) ve configuration.yml içinde bağlantı (parolalar ortam değişkeninden).',
                 sources=['https://docs.soda.io/soda-library/programmatic.html', 'https://docs.soda.io/soda-cl/soda-cl-overview.html'],
                 code_effects='Kontroller veri kaynağında sorgu çalıştırır.')
PRODUCTS += pair('quality', 'Soda Cloud', 'soda-cloud', label='Soda Cloud', how='Soda Cloud API v1 checks listesinden (son değerlendirme sonucu ve veri kümeleri)',
                 config={'server': 'https://cloud.soda.io', 'dataset_id': ''}, secrets=['SODA_API_KEY_ID', 'SODA_API_KEY_SECRET'],
                 requirements='Soda Cloud API anahtarı; yalnızca görebildiğiniz veri kümelerinin kontrolleri listelenir (dakikada 60 istek). Yanıt alan adları sürüme göre değişebilir.',
                 sources=['https://docs.soda.io/soda-cloud-api-v1/checks'])
PRODUCTS += pair('quality', 'Deequ', 'deequ', label='Deequ', how='Deequ FileSystemMetricsRepository JSON’undaki son çalışmanın metriklerini kurallarınızla karşılaştırarak (geçmiş eğilimiyle)',
                 config={'metrics_repository_file': 'metrics.json', 'rules': [{'metrik': 'Completeness', 'kolon': 'email', 'min': 0.99}, {'metrik': 'Size', 'kolon': '*', 'min': 1}]},
                 secrets=[], requirements='Deequ/PyDeequ ile FileSystemMetricsRepository’ye yazılmış metrikler (yerel kopya). Spark gerekmez.',
                 sources=['https://github.com/awslabs/deequ/blob/master/src/main/scala/com/amazon/deequ/examples/metrics_repository_example.md', 'https://github.com/awslabs/python-deequ'])
PRODUCTS += pair('quality', 'Cloud Data Quality', 'informatica-cdq', label='Informatica Cloud Data Quality', how='Cloud Data Profiling profilinin metric-store dışa aktarımından (IDMC v3 oturumu) ve kolon/metrik kurallarınızdan',
                 config={'login_url': 'https://dm-us.informaticacloud.com', 'profiling_url': 'https://usw3-dqprofile.dm-us.informaticacloud.com', 'profile_id': 'YOUR_PROFILE_ID',
                         'rules': [{'metrik': 'Null %', 'kolon': 'EMAIL', 'max': 1}]}, secrets=['IDMC_USER', 'IDMC_PASSWORD'],
                 requirements='Data Profiling hizmetine erişimi olan IDMC kullanıcısı ve bölgenize uygun profiling_url. Dışa aktarım kolon adları sürüme göre değişebilir; eşleşmeyen kural “hata” olarak raporlanır.',
                 sources=['https://docs.informatica.com/data-governance-and-quality-cloud/cloud-data-profiling/current-version/data-profiling/data-profiling/data-profiling-rest-api.html'])
PRODUCTS += pair('quality', 'Talend Data Quality', 'talend-dq', label='Talend Data Quality (Qlik Trust Score)', how='Qlik Talend Cloud Trust Scores API’sinden (genel puan ve eksen/metrik kırılımı)',
                 config={'tenant': 'https://YOUR_TENANT.eu.qlikcloud.com', 'dataset_ids': ['YOUR_DATASET_ID'], 'min_score': 80}, secrets=['QLIK_ACCESS_TOKEN'],
                 requirements='dataset:read ve dataquality:read izinli belirteç; puanı hesaplanmamış veri kümeleri “hata” olarak raporlanır.',
                 sources=['https://qlik.dev/apis/rest/data-governance/trust-scores/'])
PRODUCTS += pair('quality', 'ONE', 'ataccama', label='Ataccama ONE', how='ONE GraphQL API’sinde izleme projesinin son işleme ait kontrol sonuç sayımlarından (dqCheckResultOverTime)',
                 config={'server': 'https://YOUR_ONE_INSTANCE', 'monitoring_project_gid': 'YOUR_PROJECT_GID', 'catalog_item_gid': 'YOUR_ITEM_GID', 'check_gids': ['YOUR_CHECK_GID'],
                         'max_invalid_ratio': 0.0}, secrets=['ATACCAMA_TOKEN'],
                 requirements='ONE için OIDC erişim belirteci ve izleme projesini görme yetkisi. Sonuç adlarında invalid/failed geçen sayımlar geçersiz kabul edilir.',
                 sources=['https://docs.ataccama.com/', 'https://community.ataccama.com/data-quality-governance-94/query-the-monitoring-report-results-at-item-level-1490'])

# ---------- Governance & data catalog ----------
PRODUCTS += pair('governance', 'Purview', 'purview', label='Microsoft Purview', how='Data Map arama sorgusu ve Atlas varlık uç noktasından (kolonlar, sahip kişiler, sınıflandırmalar, terimler)',
                 config={'account': 'YOUR_PURVIEW_ACCOUNT', 'entity_type': 'azure_sql_table', 'keywords': '*', 'collection_id': '', 'max_assets': 200}, secrets=['PURVIEW_ACCESS_TOKEN'],
                 requirements='https://purview.azure.net kapsamlı belirteç ve koleksiyonda Data Reader rolü. Sahipler Entra nesne kimliği olarak gelir.',
                 sources=['https://learn.microsoft.com/en-us/rest/api/purview/datamapdataplane/discovery/query', 'https://learn.microsoft.com/en-us/rest/api/purview/datamapdataplane/entity/get'])
PRODUCTS += pair('governance', 'Data Intelligence Cloud', 'collibra', label='Collibra', how='REST 2.0 varlık, öznitelik (Description), sorumluluk ve “Column is part of Table” ilişkilerinden',
                 config={'server': 'https://YOUR_INSTANCE.collibra.com', 'community_id': '', 'domain_id': 'YOUR_DOMAIN_ID', 'max_assets': 200}, secrets=['COLLIBRA_USER', 'COLLIBRA_PASSWORD'],
                 requirements='Alanı görebilen kullanıcı. Standart işletim modeli varlık/ilişki tipi kimlikleri varsayılandır; özelleştirilmiş modelde table_type, column_type, column_of_table, description alanlarını verin.',
                 sources=['https://developer.collibra.com/rest/rest-core-api/', 'https://productresources.collibra.com/docs/collibra/latest/Content/Assets/co_asset-types.htm'])
PRODUCTS += pair('governance', 'Data Catalog', 'alation', label='Alation', how='Integration API v2 tablo ve kolon uç noktalarından (açıklamalar)',
                 config={'server': 'https://YOUR_INSTANCE.alationcloud.com', 'datasource_id': '', 'schema_id': 'YOUR_SCHEMA_ID', 'max_assets': 200}, secrets=['ALATION_TOKEN'],
                 requirements='API erişim belirteci (Refresh token ile alınmış, TOKEN başlığı) ve kaynakta görüntüleme yetkisi. Sahiplik/steward alanları özel alan olduğundan bu pakette okunmaz.',
                 sources=['https://developer.alation.com/dev/reference/gettables', 'https://developer.alation.com/dev/reference/getcolumns'])
PRODUCTS += pair('governance', 'Dataplex', 'dataplex', label='Dataplex Universal Catalog', how='searchEntries ve entries.get (view=ALL; schema, overview ve contacts aspect’leri) ile',
                 config={'project_id': 'YOUR_PROJECT_ID', 'query': 'type=table projectid=YOUR_PROJECT_ID', 'max_assets': 200}, secrets=['GOOGLE_ACCESS_TOKEN'],
                 requirements='dataplex.entries.get/search izinleri (Dataplex Catalog Viewer) ve kaynak sistemde (ör. BigQuery) metadata görme yetkisi.',
                 sources=['https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations/searchEntries', 'https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.entryGroups.entries/get'])
PRODUCTS += pair('governance', 'Atlan', 'atlan', label='Atlan', how='indexsearch (Elastic DSL) ile Table ve Column varlıklarından (açıklama, sahip kullanıcı/grup, terimler, sınıflandırmalar)',
                 config={'server': 'https://YOUR_TENANT.atlan.com', 'qualified_name_prefix': 'default/snowflake/1700000000/DB/SCHEMA', 'max_assets': 200}, secrets=['ATLAN_API_TOKEN'],
                 requirements='Varlıkları okuyabilen persona/purpose’a bağlı API belirteci.',
                 sources=['https://developer.atlan.com/search/advanced/', 'https://developer.atlan.com/getting-started/'])
PRODUCTS += pair('governance', 'OpenMetadata', 'openmetadata', label='OpenMetadata', how='/api/v1/tables uç noktasından (columns, owners, tags, domains alanlarıyla, imleçli)',
                 config={'server': 'https://YOUR_OPENMETADATA', 'database_fqn': 'service.database', 'schema_fqn': '', 'max_assets': 200}, secrets=['OPENMETADATA_TOKEN'],
                 requirements='Bot veya kişisel erişim belirteci; tablolarda ViewAll yetkisi.',
                 sources=['https://docs.open-metadata.org/latest/main-concepts/metadata-standard/apis', 'https://docs.open-metadata.org/swagger.html'])
PRODUCTS += pair('governance', 'DataHub', 'datahub', label='DataHub', how='GraphQL searchAcrossEntities ile DATASET varlıklarından (şema alanları, düzenlenmiş açıklamalar, sahipler, terimler, etiketler)',
                 config={'server': 'https://YOUR_DATAHUB', 'platform': 'snowflake', 'query': '*', 'max_assets': 200}, secrets=['DATAHUB_TOKEN'],
                 requirements='Kişisel erişim belirteci (GMS /api/graphql) ve varlık okuma yetkisi.',
                 sources=['https://docs.datahub.com/docs/api/graphql/overview', 'https://docs.datahub.com/docs/graphql/queries#searchacrossentities'])

# ---------- Master data management ----------
DUP = {'similarity_threshold': 0.92, 'max_records': 20000}
PRODUCTS += pair('mdm', 'Master Data Governance', 'sap-mdg', label='SAP Master Data Governance', how='S/4HANA iş ortağı OData servisinden (MDG ile yönetilen ana veri; $select/$filter, sayfalı)',
                 config={'server': 'https://YOUR_S4_HOST', 'sap_client': '100', 'service': '/sap/opu/odata/sap/API_BUSINESS_PARTNER', 'entity_set': 'A_BusinessPartner',
                         'fields': ['BusinessPartner', 'BusinessPartnerFullName', 'SearchTerm1'], 'filter': "BusinessPartnerCategory eq '2'", 'match_fields': ['BusinessPartnerFullName'],
                         'id_field': 'BusinessPartner', **DUP}, secrets=['SAP_USER', 'SAP_PASSWORD'],
                 requirements='İletişim kullanıcısı veya OAuth (SAP_ACCESS_TOKEN) ile iş ortağı API’sinde okuma. MDG değişiklik talepleri oluşturulmaz.',
                 sources=['https://api.sap.com/api/API_BUSINESS_PARTNER/overview', 'https://help.sap.com/docs/SAP_MASTER_DATA_GOVERNANCE'])
PRODUCTS += pair('mdm', 'MDM SaaS', 'informatica-mdm', label='Informatica MDM SaaS', how='Business 360 Search API’sinden (IDMC v3 oturumu, IDS-SESSION-ID, sayfalı)',
                 config={'login_url': 'https://dm-us.informaticacloud.com', 'mdm_url': 'https://use4-mdm.dm-us.informaticacloud.com', 'entity_type': 'c360.organization', 'search': '*',
                         'model_version': '', 'match_fields': ['c360organization.name'], 'id_field': 'businessId', **DUP}, secrets=['IDMC_USER', 'IDMC_PASSWORD'],
                 requirements='Business 360 varlığında okuma yetkili IDMC kullanıcısı; mdm_url login yanıtındaki baseApiUrl’dir. Arama sayfa başına en fazla 10.000 kayıt döndürür.',
                 sources=['https://onlinehelp.informatica.com/IICS/dev/b360/en/wz-b360-rest-api/Search_API.html'])
PRODUCTS += pair('mdm', 'xDM', 'semarchy', label='Semarchy xDM', how='REST query API’sinin Golden Data (GD) görünümünden (API-Key)',
                 config={'server': 'https://YOUR_SEMARCHY', 'data_location': 'CustomerHub', 'entity': 'Customer', 'view': 'GD', 'match_fields': ['CustomerName'], 'id_field': 'CustomerID', **DUP},
                 secrets=['SEMARCHY_API_KEY'], requirements='Semarchy Configuration’da oluşturulmuş API anahtarı ve modelde Integration API erişimi.',
                 sources=['https://semarchy.com/doc/semarchy-xdm/xdm/5.3/Integrate/consume-data-with-rest.html', 'https://www.semarchy.com/tutorials-content/query-data-via-rest/'])
PRODUCTS += pair('mdm', 'Reltio', 'reltio', label='Reltio', how='entities API’sinden (tip filtresiyle, sayfalı) ve agent için varlık tipinin eşleşme/hayatta kalma yapılandırmasından',
                 config={'environment': 'eu-360', 'tenant': 'YOUR_TENANT', 'entity_type': 'Organization', 'match_fields': ['Name'], 'id_field': 'uri', **DUP}, secrets=['RELTIO_ACCESS_TOKEN'],
                 requirements='auth.reltio.com’dan alınmış OAuth erişim belirteci ve kiracıda okuma rolü.',
                 sources=['https://docs.reltio.com/en/developer-resources/entities-api', 'https://docs.reltio.com/en/explore/get-going-with-match-and-merge'])
PRODUCTS += pair('mdm', 'EBX', 'tibco-ebx', label='TIBCO EBX', how='yerleşik REST veri hizmetlerinin tablo seçiminden (sayfalı)',
                 config={'server': 'https://YOUR_EBX', 'dataspace': 'Reference', 'dataset': 'YOUR_DATASET', 'table_path': '/root/Customers', 'match_fields': ['name'], 'id_field': 'id', **DUP},
                 secrets=['EBX_USER', 'EBX_PASSWORD'], requirements='ebx-dataservices uygulaması dağıtılmış olmalı; kullanıcının veri alanında okuma yetkisi. Dataspace adı B önekiyle (branch) çağrılır.',
                 sources=['https://docs.tibco.com/pub/ebx/6.2.1/doc/html/en/data_services/restapi_introduction.html'])
PRODUCTS += pair('mdm', 'STEP', 'stibo-step', label='Stibo Systems STEP', how='STEPXML dışa aktarımındaki nesnelerden (ID, ad, öznitelik değerleri)',
                 config={'stepxml_file': 'step_export.xml', 'object_kind': 'Product', 'match_fields': ['Name'], 'id_field': 'ID', **DUP}, secrets=[],
                 requirements='STEP dışa aktarma profiliyle alınmış STEPXML dosyası (yerel). STEP REST API v2 başvurusu kuruluma özel olduğundan bu paket dosya üzerinden çalışır.',
                 sources=['https://doc.stibosystems.com/'])

# ---------- Privacy & data security ----------
MASK = {'input_file': 'veri.csv', 'mask_method': 'hash', 'extra_fields': [], 'table_filter': ''}
MASK_REQ = ' hash yöntemi için .env içinde en az 16 karakterlik MASK_SALT gerekir (aynı tuz aynı değeri aynı özete çevirir; tuzu gizli tutun).'
PRODUCTS += pair('privacy', 'Purview Information Protection', 'purview-ip', label='Microsoft Purview Information Protection', how='Purview Data Map varlığının kolon sınıflandırmalarından ve duyarlılık etiketlerinden',
                 config={'account': 'YOUR_PURVIEW_ACCOUNT', 'entity_type': 'azure_sql_table', 'qualified_name': 'mssql://YOUR_SERVER/YOUR_DB/dbo/YOUR_TABLE', **MASK},
                 secrets=['PURVIEW_ACCESS_TOKEN', 'MASK_SALT'], requirements='Koleksiyonda Data Reader rolü.' + MASK_REQ,
                 sources=['https://learn.microsoft.com/en-us/rest/api/purview/datamapdataplane/entity/get-by-unique-attribute', 'https://learn.microsoft.com/en-us/purview/concept-classification'])
PRODUCTS += pair('privacy', 'Data Discovery', 'onetrust', label='OneTrust Data Discovery', how='OneTrust Data Discovery tarama sonuçlarının dışa aktarım dosyasından (kolon ve sınıflandırma başlıkları ayarlanabilir)',
                 config={'results_file': 'onetrust_sonuclari.csv', 'column_header': 'Column Name', 'classification_header': 'Classification', 'table_header': 'Table Name', **MASK},
                 secrets=['MASK_SALT'], requirements='Data Discovery sonuçlarının CSV/JSON dışa aktarımı. OneTrust’un herkese açık bir Data Discovery REST başvurusu bulunmadığından paket dosya üzerinden çalışır.' + MASK_REQ,
                 sources=['https://www.onetrust.com/products/data-discovery/'])
PRODUCTS += pair('privacy', 'BigID', 'bigid', label='BigID', how='BigID veri kataloğunun nesne kolon ayrıntılarından (sınıflandırıcı öznitelikleri)',
                 config={'server': 'https://YOUR_BIGID', 'object_name': 'YOUR_DATASOURCE.YOUR_SCHEMA.YOUR_TABLE', **MASK}, secrets=['BIGID_TOKEN', 'MASK_SALT'],
                 requirements='BigID oturum/sistem belirteci (Authorization başlığı) ve katalog okuma yetkisi. Yanıt alan adları sürüme göre değişebilir.' + MASK_REQ,
                 sources=['https://developer.bigid.com/api/bigid-api-metadata-export-tutorial/'])
PRODUCTS += pair('privacy', 'Immuta', 'immuta', label='Immuta', how='veri kaynağının veri sözlüğündeki kolon etiketlerinden (/dictionary)',
                 config={'server': 'https://YOUR_TENANT.immuta.com', 'data_source_name': 'YOUR_DATA_SOURCE', **MASK}, secrets=['IMMUTA_API_KEY', 'MASK_SALT'],
                 requirements='Immuta API anahtarı ve veri kaynağını görme yetkisi.' + MASK_REQ,
                 sources=['https://documentation.immuta.com/policy-as-code/v1-api/connect-data/data-dictionary/'])
PRODUCTS += pair('privacy', 'Privacera', 'privacera', label='Privacera', how='Privacera’nın Apache Ranger etiket deposundan (tags/resources, tags/tags, tagresourcemaps)',
                 config={'ranger_url': 'https://YOUR_PRIVACERA_RANGER', 'tag_service': 'privacera_tag', 'resource_service': 'privacera_hive', **MASK}, secrets=['RANGER_USER', 'RANGER_PASSWORD', 'MASK_SALT'],
                 requirements='Ranger Admin’de etiket servisini okuyabilen kullanıcı (Privacera Discovery etiketleri buraya yazar).' + MASK_REQ,
                 sources=['https://ranger.apache.org/apidocs/resource_TagREST.html', 'https://docs.privacera.com/'])
PRODUCTS += pair('privacy', 'Guardium', 'guardium', label='IBM Guardium', how='Guardium REST API online_report ile sınıflandırıcı sonuç raporundan (rapor adı ve alan başlıkları ayarlanabilir)',
                 config={'server': 'https://YOUR_GUARDIUM:8443', 'report_name': 'Classifier Results', 'report_parameters': {}, 'column_field': 'Column Name',
                         'classification_field': 'Rule Description', 'table_field': 'Table Name', 'fetch_size': 5000, **MASK}, secrets=['GUARDIUM_TOKEN', 'MASK_SALT'],
                 requirements='Guardium’da kayıtlı OAuth istemcisiyle alınmış belirteç ve rapora erişim. Sınıflandırıcı sonuçlarını içeren raporun adını ve alan başlıklarını kendi kurulumunuza göre yazın.' + MASK_REQ,
                 sources=['https://www.ibm.com/docs/en/SSMPHH_12.x/com.ibm.guardium.doc/reports/work_with_api_calls.html', 'https://github.com/IBM/guardium-data-protection-apis'])

# ---------- Data observability ----------
OBS = {'volume_tolerance': 0.3, 'max_null_rate': 0.05}
PRODUCTS += pair('observability', 'Monte Carlo', 'montecarlo', label='Monte Carlo', how='GraphQL getEvents sorgusundan (hacim, alan sağlığı ve tazelik olayları; tablo ve durum)',
                 config={'days': 7, 'event_type_pattern': '(?i)volume|size|row|null|field|freshness|unchanged', **OBS}, secrets=['MCD_ID', 'MCD_TOKEN'],
                 requirements='Monte Carlo API anahtarı (x-mcd-id/x-mcd-token) ve olayları görebilen rol. Olay tipi adları hesabınıza göre değişebilir; event_type_pattern ile süzün.',
                 sources=['https://docs.getmontecarlo.com/docs/using-the-api', 'https://pypi.org/project/pycarlo/'])
PRODUCTS += pair('observability', 'Bigeye', 'bigeye', label='Bigeye', how='tabloların metrik bilgisi uç noktasından (metrik tipi, kolon, son çalışma değeri ve durumu)',
                 config={'server': 'https://app.bigeye.com', 'table_ids': [], **OBS}, secrets=['BIGEYE_API_KEY'],
                 requirements='Bigeye kişisel API anahtarı (Authorization: apikey …) ve tablolarda görüntüleme yetkisi. Yanıt alan adları sürüme göre değişebilir.',
                 sources=['https://docs.bigeye.com/', 'https://pypi.org/project/bigeye-cli/'])
PRODUCTS += pair('observability', 'Data Streams Monitoring', 'datadog-dsm', label='Datadog Data Streams Monitoring', how='Metrics Query API’sinden (data_streams.kafka.lag_messages/lag_seconds ve payload_size sayımı; konu ve tüketici grubu bazında)',
                 config={'site': 'datadoghq.eu', 'env': 'prod', 'hours': 24, 'max_lag_seconds': 300, **OBS}, secrets=['DD_API_KEY', 'DD_APP_KEY'],
                 requirements='timeseries_query kapsamlı uygulama anahtarı ve API anahtarı; servislerde Data Streams Monitoring etkin olmalı.',
                 sources=['https://docs.datadoghq.com/data_streams/metrics_and_tags/', 'https://docs.datadoghq.com/api/latest/metrics/#query-timeseries-points'])
PRODUCTS += pair('observability', 'Acceldata', 'acceldata', label='Acceldata', how='ADOC Catalog API’sinin varlık başına son politika yürütmelerinden (rulesWithLatestExecution)',
                 config={'server': 'https://YOUR_TENANT.acceldata.app', 'asset_uids': ['YOUR_DATASOURCE.YOUR_DB.YOUR_SCHEMA.YOUR_TABLE'], **OBS},
                 secrets=['ACCELDATA_ACCESS_KEY', 'ACCELDATA_SECRET_KEY'],
                 requirements='ADOC’ta oluşturulmuş API erişim/gizli anahtarı ve varlıklarda görüntüleme yetkisi.',
                 sources=['https://docs.acceldata.io/products/adoc/api/catalog', 'https://docs.acceldata.io/api'])
PRODUCTS += pair('observability', 'Elementary', 'elementary', label='Elementary', how='ambardaki Elementary şemasının data_monitoring_metrics (row_count, null_count, null_percent) ve elementary_test_results tablolarından',
                 config={'destination': 'snowflake', 'server': 'https://YOUR_ACCOUNT.snowflakecomputing.com', 'warehouse': 'COMPUTE_WH', 'role': 'YOUR_READ_ROLE', 'timeout_seconds': 120,
                         'elementary_schema': 'ANALYTICS.ELEMENTARY', 'days': 14, **OBS}, secrets=['SNOWFLAKE_ACCESS_TOKEN'],
                 requirements='Elementary dbt paketinin yazdığı şemada SELECT (destination: snowflake/bigquery/databricks/redshift/postgres; bağlantı alanları ilgili ambarınki).',
                 sources=['https://docs.elementary-data.com/data-tests/anomaly-detection-tests/volume-anomalies', 'https://docs.elementary-data.com/guides/modules-overview/dbt-package'])
