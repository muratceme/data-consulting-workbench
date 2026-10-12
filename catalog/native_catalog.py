"""Executable operations are explicit; taxonomy coverage is not implementation coverage."""
COMMON = 'Python 3.10+. Anahtarlar yalnızca yerel .env/ortam değişkenlerinden okunur. --plan ağ çağrısı yapmaz; --execute API işlemini başlatır. HTML raporu teslim edilmez.'
OPERATIONS = [
 dict(operation='qlik-export', product='Qlik Cloud', domain='bi', title='Mevcut uygulamayı QVF olarak indir', output='QVF', kind='code',
      description='Mevcut Qlik Cloud uygulamasını Apps export ve Temporary Contents API ile indirir. Yeni grafik tasarlamaz.',
      config={'tenant':'https://YOUR_TENANT.eu.qlikcloud.com','app_id':'YOUR_APP_ID'}, secrets=['QLIK_ACCESS_TOKEN'],
      requirements='Uygulama personal/shared space içinde olmalı; API erişimi ve export yetkisi gerekir. Managed space export kısıtlarına tabidir.',
      effects='Export isteği geçici sunucu içeriği üretir; kaynak uygulama değiştirilmez.',
      sources=['https://www.qlik.dev/manage/platform-operations/deploy-content-to-a-tenant/']),
 dict(operation='qlik-template', product='Qlik Cloud', domain='bi', title='Şablondan yenilenmiş QVF üret', output='QVF + tenant uygulaması', kind='code',
      description='Hazır grafikler içeren uygulamayı kopyalar, mevcut veri bağlantılarıyla reload yapar, başarıdan sonra QVF indirir. Sıfırdan görsel tasarım yapmaz.',
      config={'tenant':'https://YOUR_TENANT.eu.qlikcloud.com','app_id':'YOUR_TEMPLATE_APP_ID','name':'Datawork report'}, secrets=['QLIK_ACCESS_TOKEN'],
      requirements='Okunabilir şablon app, kopyalama/reload/export yetkisi ve çalışan veri bağlantıları gerekir. Şablonun load script’i çalıştırılır; önceden incelenmelidir.',
      effects='Tenant üzerinde yeni uygulama oluşturur ve reload çalıştırır. Başarısızlıkta kaynak silinmez; yeni app ve reload ID run-state.json içinde tutulur. Tekrar --execute yeni kopya yaratır.',
      sources=['https://www.qlik.dev/apis/rest/apps/','https://www.qlik.dev/apis/rest/reloads/']),
 dict(operation='powerbi-export', product='Power BI Service', domain='bi', title='Mevcut raporu PBIX olarak indir', output='PBIX', kind='code',
      description='Workspace içindeki indirilebilir raporu Power BI REST API ile PBIX olarak alır. Yeni rapor tasarlamaz; RDL desteklenmez.',
      config={'workspace_id':'YOUR_WORKSPACE_ID','report_id':'YOUR_REPORT_ID'}, secrets=['POWERBI_ACCESS_TOKEN'],
      requirements='Microsoft Entra access token, rapor/workspace erişimi ve uygun Report.Read.All veya Report.ReadWrite.All izinleri. PBIX indirme kısıtları ve model türü belirleyicidir.',
      effects='Kaynak rapor değişmez. Cross-origin yönlendirme alınırsa güvenli biçimde durur.',
      sources=['https://learn.microsoft.com/en-us/rest/api/power-bi/reports/export-report-in-group']),
 dict(operation='tableau-export', product=['Tableau Cloud','Tableau Server'], domain='bi', title='Workbook’u yerel Tableau biçiminde indir', output='TWB veya TWBX', kind='code',
      description='PAT ile oturum açar; workbook ve mevcut extract içeriğini indirir. Gerçek yanıt TWB ise TWB, paketli ise TWBX olarak kaydeder.',
      config={'server':'https://YOUR_SERVER','api_version':'3.24','site_content_url':'','workbook_id':'YOUR_WORKBOOK_ID'}, secrets=['TABLEAU_PAT_NAME','TABLEAU_PAT_SECRET'],
      requirements='Sunucunun desteklediği API sürümü, PAT ve workbook indirme yetkisi. TWBX yeni dashboard üretmek değildir.',
      effects='Workbook değişmez; indirmeden sonra API oturumu kapatılır.',
      sources=['https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_authentication.htm','https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_workbooks_and_views.htm']),
 dict(operation='sap-b1-schema', product='Business One', domain='erp', title='Service Layer şemasını EDMX olarak al', output='EDMX servis sözleşmesi', kind='code',
      description='SAP Business One Service Layer oturumu açar; $metadata ile gerçek entity ve alan sözleşmesini alır. İş belgesi veya rapor oluşturmaz.',
      config={'service_url':'https://YOUR_SERVER:50000/b1s/v2/','company_db':'YOUR_COMPANY_DB'}, secrets=['SAP_USERNAME','SAP_PASSWORD'],
      requirements='SAP B1 Service Layer, şirket veritabanı, kullanıcı/parola ve geçerli TLS sertifikası gerekir. SessionId ile B1SESSION kullanılır.',
      effects='Salt okunur metadata. İşlem sonunda Logout çağrılır.',
      sources=['https://help.sap.com/doc/fc2f5477516c404c8bf9ad1315a17238/10.0/en-US/Working_with_SAP_Business_One_Service_Layer.pdf']),
 dict(operation='sap-odata-schema', product='S/4HANA', domain='erp', title='Etkin OData servisinin EDMX sözleşmesini al', output='EDMX servis sözleşmesi', kind='code',
      description='Kullanıcının etkinleştirdiği OData service root üzerinden $metadata alır. Servis yolu tahmin edilmez; ERP belgesi oluşturmaz.',
      config={'service_url':'https://YOUR_SERVER/sap/opu/odata/sap/YOUR_SERVICE/'}, secrets=['SAP_ACCESS_TOKEN'],
      requirements='Etkin OData servisi ve bu servis için Bearer token kabul eden communication arrangement gerekir. Basic/mTLS/SAML kurulumları bu adaptörde desteklenmez.',
      effects='Salt okunur metadata.',sources=['https://help.sap.com/docs/SAP_S4HANA_CLOUD/0f69f8fb28ac4bf48d2b57b9637e81fa/facdec3013fe4b8da6191b27d9d4bf9a.html','https://help.sap.com/docs/integration-suite/sap-integration-suite/metadata?locale=en-US']),
 dict(operation='oracle-schema', product='Fusion Cloud ERP', domain='erp', title='Fusion kaynak sözleşmesini API’den al', output='Fusion describe JSON', kind='code',
      description='ERP REST describe endpointinden gerçek kaynak/alan sözleşmesini indirir. BI Publisher raporu veya ERP iş belgesi üretmez.',
      config={'server':'https://YOUR_SERVER.fa.oraclecloud.com','api_version':'11.13.18.05'}, secrets=['ORACLE_ACCESS_TOKEN'],
      requirements='Fusion OAuth access token, ilgili REST kaynak yetkileri ve tenantta desteklenen API sürümü.',
      effects='Salt okunur metadata.',sources=['https://docs.oracle.com/en/cloud/saas/applications-common/26b/farca/Access_Metadata.html']),
 dict(operation='bigquery-table', product='BigQuery', domain='warehouse', title='Doğrulanmış SQL ile hedef tablo oluştur', output='BigQuery tablosu', kind='code',
      description='Kullanıcının SELECT sorgusunu API dry-run ile doğrular; maliyet sınırıyla hedef BigQuery tablosuna yazar. Doluyken üzerine yazmaz.',
      config={'project_id':'YOUR_PROJECT','location':'EU','sql_file':'query.sql','destination_dataset':'YOUR_DATASET','destination_table':'YOUR_NEW_TABLE','maximum_bytes_billed':1000000000}, secrets=['GOOGLE_ACCESS_TOKEN'],
      requirements='Google OAuth token (BigQuery/cloud-platform scope), jobs.create, kaynak tablo okuma ve hedef tablo oluşturma/yazma izinleri. Hedef dataset önceden var olmalı.',
      effects='BigQuery işi ve tablo oluşturur; faturalandırılabilir tarama yapar. WRITE_EMPTY dolu tabloyu korur. Job ID run-state.json içine ağ yazmasından önce kaydedilir.',
      sources=['https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/insert','https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/Job']),
 dict(operation='bigquery-agent', product='BigQuery', domain='warehouse', title='Canlı şemadan SQL tasarla ve API’de doğrula', output='BigQuery Standard SQL (.sql)', kind='agent',
      description='Tablonun gerçek şemasını API’den alır. Model bu şema ve iş isteğiyle SELECT tasarlar; BigQuery dry-run doğrulamasından geçen SQL teslim edilir. Sorgu çalıştırılmaz.',
      config={'project_id':'YOUR_PROJECT','location':'EU','dataset_id':'YOUR_DATASET','table_id':'YOUR_TABLE','context_file':'context.md'}, secrets=['GOOGLE_ACCESS_TOKEN','MODEL','PROVIDER','OPENAI_API_KEY','ANTHROPIC_API_KEY'],
      requirements='BigQuery metadata okuma ve jobs.create; ayrıca seçilen OpenAI uyumlu/Anthropic/Ollama model erişimi. Şema adları, açıklamaları ve context.md seçilen modele gönderilir; satır verisi çekilmez.',
      effects='Model API çağrısı ve BigQuery dry-run yapar. Model maliyeti olabilir. SQL hiçbir zaman otomatik yürütülmez; insan incelemesi gerekir.',
      sources=['https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/tables/get','https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/insert']),
]

# These are design targets, not claims of a verified vendor capability.
TARGETS = {
 'bi':('Rapor/uygulama API + görsel model SDK','Platformun rapor/proje dosyası veya yayınlanmış rapor'),
 'erp':('Ürüne özgü OData/REST/SOAP veya on-prem SDK','ERP içindeki taslak belge, eşleme veya rapor tanımı'),
 'warehouse':('Sorgu işi API / SQL sürücüsü','Platform tablosu, view veya SQL modeli'),
 'lakehouse':('Workspace/catalog API + SQL/Spark SDK','Notebook, job veya native tablo'),
 'integration':('ETL yönetim API/SDK','Platformun pipeline tanımı ve çalışma kaydı'),
 'transform':('Ürünün proje/derleme/iş API’si','dbt/Dataform/SQLMesh proje dosyası'),
 'orchestration':('Orkestratör REST/GraphQL/SDK','DAG, flow veya job tanımı'),
 'stream':('Broker yönetim API + istemci SDK','Topic, schema veya stream job tanımı'),
 'database':('Resmi DB sürücüsü / yönetim API','SQL/DDL veya veritabanı nesnesi'),
 'quality':('Kalite ürünü API / SDK','Ürüne özgü expectation/check tanımı'),
 'governance':('Katalog metadata REST/GraphQL API','Platformda terim, owner veya lineage kaydı'),
 'mdm':('MDM API / vendor SDK','Eşleme ve survivorship kuralı/taslağı'),
 'privacy':('Sınıflandırma/politika API','Platformda erişim/maskeleme politikası taslağı'),
 'observability':('Monitör/olay API','Platform monitor ve alarm tanımı'),
 'science':('Resmi dil/kütüphane SDK','Notebook veya analiz projesi'),
 'ml':('ML yönetim API / framework SDK','Training pipeline, model veya deployment tanımı'),
 'ai':('Model/vektör API veya framework SDK','RAG iş akışı, değerlendirme seti veya index'),
 'product':('Analitik ürünün query/admin API','Platform metrik/segment/event tanımı'),
 'spatial':('Coğrafi servis API / GIS SDK','Layer, geoprocessing job veya GIS projesi'),
 'planning':('Planlama API / vendor SDK','Platform senaryo/model/veri yükleme işi'),
 'migration':('Migrasyon yönetim API / CLI','Platform migration task ve doğrulama kaydı'),
 'finops':('Platform billing/query API','Platform maliyet sorgusu veya monitor'),
 'files':('Office/Sheets API veya yerel kütüphane SDK','XLSX, Power Query M veya platform tablosu'),
}


def catalog(domains):
    result=[]
    for did,name,desc,icon,tools,engine,title,agent_title in domains:
        domain={'id':did,'name':name,'description':desc,'icon':icon,'tools':[]}
        for ti,(vendor,products) in enumerate(tools):
            tool={'id':f'{did}-{ti}','name':vendor,'products':[]}
            for pi,product in enumerate(products):
                pid=f'{did}-{ti}-{pi}'
                available=[op for op in OPERATIONS if op['domain']==did and product in (op['product'] if isinstance(op['product'],list) else [op['product']])]
                tasks=[]
                for op in available:
                    tasks.append({**op,'id':pid+'-'+op['operation'],'engine':'native','execution':'Platform API + LLM' if op['kind']=='agent' else 'Platform API','status':'contract-tested','input':op['requirements'],'reason':'Şema ve iş amacı yorumlanır; sonuç platform API’sinde doğrulanır.' if op['kind']=='agent' else 'İşlem sırası ve girdi/çıktı sözleşmesi sabittir; LLM gerekmez.','verification':{'contract':'tested','live':'not-tested','native_open':'not-tested'}})
                interface,output=TARGETS[did]
                # Preserve the full product/task intent, but never ship a placeholder as a runnable package.
                for kind,task_title in [('code','Uygulamaya özgü veri hazırlama otomasyonu' if did=='files' else title),('agent',agent_title)]:
                    tasks.append({'id':pid+'-planned-'+kind,'title':task_title,'kind':kind,'engine':'planned','description':f'{product} üzerinde uygulamaya özgü {"otomasyon" if kind=="code" else "karar ve tasarım"} akışı. Entegrasyon henüz uygulanmadı.','reason':'Sabit sözleşme ile çalışacak.' if kind=='code' else 'İş amacı, şema ve kısıtları değerlendirecek.','input':'Ürün sürümü, API/SDK erişimi ve kaynak/çıktı sözleşmesi belirlenecek.','execution':'Planlanan entegrasyon','output':output+' (tasarım hedefi)','status':'planned','interface_target':interface,'verification':{'contract':'not-tested','live':'not-tested','native_open':'not-tested'}})
                tool['products'].append({'id':pid,'name':product,'tasks':tasks,'integration':{'status':'partial' if available else 'planned','interface_target':interface,'output_target':output,'note':'API/SDK yolu ve format sürüme göre doğrulanacak. Python/SQL gibi yerel araçlarda web API yerine resmi SDK/sürücü gerekir.'}})
            domain['tools'].append(tool)
        result.append(domain)
    return result
