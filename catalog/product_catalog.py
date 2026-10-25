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
}


def pair(domain, product, key, *, label, how, config, secrets, requirements, sources, agent_config=None, packages=(), code_effects='', agent_effects='', only=('code', 'agent')):
    """Two catalog entries (code + agent) for one product of a domain."""
    spec = DOMAIN[domain]
    entries = []
    for slot in only:
        title, output, description = spec[slot]
        op = f'{key}-{OPS[domain][slot]}'
        cfg = {**config, **(agent_config or {})} if slot == 'agent' else dict(config)
        entries.append(dict(operation=op, product=product, domain=domain, slot=slot, kind=slot, module=domain, packages=list(packages),
                            title=title.format(label=label), output=output, description=description.format(label=label, how=how),
                            config=cfg, secrets=list(secrets), requirements=requirements + (AGENT_REQ if slot == 'agent' else ''),
                            effects=(code_effects + READ_ONLY) if slot == 'code' else (agent_effects + AGENT_EFFECT), sources=list(sources)))
    return entries


OPS = {'warehouse': {'code': 'schema-diff', 'agent': 'dimensional-model'}, 'database': {'code': 'schema-diff', 'agent': 'index-review'},
       'lakehouse': {'code': 'layer-check', 'agent': 'medallion'}, 'finops': {'code': 'cost-summary', 'agent': 'savings-plan'},
       'spatial': {'code': 'sensor-check', 'agent': 'analysis-plan'}, 'ai': {'code': 'rag-duplicates', 'agent': 'rag-design'}}

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
