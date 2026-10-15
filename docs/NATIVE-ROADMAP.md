# Native entegrasyon geliştirme planı

## Ürün kuralı

Kod bloğu: girdisi/çıktısı, API işlemi ve kabul kriteri belli deterministik işlem. Agent: şema ve iş amacı üzerinden seçim/tasarım/yorum gereken iş. Agent çıktısı mümkün olduğunda vendor parser, dry-run, compiler veya sandbox ile doğrulanır; doğrulanmadıysa çalışır artifact etiketi almaz.

`HTML/JSON rapor ürettik` ifadesi tek başına platform entegrasyonu değildir. JSON yalnızca gerçek platform sözleşmesi/job tanımı veya makbuz ise uygun teslimdir. QVF/PBIX gibi kapalı biçimler uzantı değiştirilerek üretilmez; vendor engine/API üretmelidir. SDK/sürücüyle çalışan yerel ürünlere uydurma REST API veya gereksiz API anahtarı zorunluluğu eklenmez.

## Her ürün için tamamlanma kapıları

1. **Sürüm ve işlem sözleşmesi:** Cloud/on-prem ayrımı, resmi endpoint/SDK, lisans, kimlik doğrulama, scope, native çıktı.
2. **Uygulama:** giriş doğrulama, hata/pagination/polling, tekrar ve yan etki davranışı, sırlara erişim, native artifact doğrulama.
3. **Yerel sözleşme testi:** gerçek HTTP test sunucusu veya SDK fixture'ı; başarılı akış ve failure cases. Bu, gerçek vendor testi değildir.
4. **Canlı sandbox testi:** erişim bilgileri ortam değişkenlerinde; vendor job/request ID, izin hatası, minimal scope ve gerçek çıktı.
5. **Uygulamada kabul:** artifact açılır/import edilir; şema, sayfa, veri ve metrik doğruluğu kontrol edilir. Agent için referans iş senaryoları ve insan değerlendirmesi eklenir.

Yalnızca son iki aşama tamamlanınca `live-tested` / `native-open-tested` işaretlenebilir. Şu anda hiçbir görev bu etiketleri taşımaz.

## Uygulanan ilk kesit

- Qlik Cloud: kaynak uygulamayı export; şablon kopyalama, mevcut script ile reload, native QVF export. Kaynak korunur; kopya otomatik silinmez.
- Power BI Service: PBIX export, Tableau Cloud/Server: gerçek TWB/TWBX download. Bunlar yeni rapor tasarımı değildir.
- SAP B1/S4 ve Oracle Fusion: gerçek servis şemasını keşfetme. SAP B1 satış teklifi taslağı artık Drafts API ile oluşturulup DocEntry üzerinden yeniden okunur; kesin belge üretimi yoktur.
- BigQuery: API'de SELECT doğrulama, bütçeli WRITE_EMPTY hedef tablo işi; şemadan SQL üreten agent için yalnızca dry-run ve `.sql` teslimi.

- Databricks: tablo profil notebook’u kaynak kodu oluşturulur, SOURCE/PYTHON olarak yeni workspace nesnesi açılır, get-status ve export ile geri okunur. Notebook otomatik çalıştırılmaz.
- ADF: onaylanmış JSON tanımı yeni UUID isimli pipeline olarak yayımlanır. Ayrı run işlemi mevcut pipeline’ı başlatır ve tamamlanana kadar izler.
- Airflow 3.2: /api/v2 ile mevcut DAG için run açılır; logical_date=null ve conf gönderilir; yalnızca success teslim edilir. Airflow 2 API’si desteklenmez.
- dbt Cloud: mevcut job tetiklenir; account/job/run kimlikleri ve terminal durum denetlenir; başarı sonrası native run_results.json indirilir. Dönüşüm/test komutları job’da önceden tanımlı olmalıdır. Eksik artifact teslim hatasıdır.
- Prefect 3 Cloud: mevcut deployment için parametreli SCHEDULED flow run oluşturulur; idempotency_key çağrı öncesi kaydedilir. COMPLETED dışındaki terminal durumlar hata verir; timeout çalışmayı iptal etmez. Akış kodu veya yeni deployment üretilmez.

- GitHub Actions: REST API 2026-03-10 dispatch yanıtındaki kesin workflow_run_id ile takip; repo/workflow/run/event doğrulaması. Yalnızca completed/success teslim edilir. Eski 204 yanıtı başarı sayılmaz veya yeniden tetiklenmez. Workflow oluşturma ve artifact indirme bu paketin kapsamında değil.

- Fivetran: mevcut manual bağlantıda force=false sync; v2 STARTED sonucu ve succeeded_at ilerlemesi takip edilir. Otomatik schedule, aktif/historical sync ve paused bağlantılar tetiklenmez. Eşzamanlı harici tetikleme varsa run eşlemesi garanti edilmez; endpoint kesin run ID vermez. Config/credential yanıtı saklanmaz.

- Airbyte Cloud: Public API sync job, jobId/connectionId/jobType doğrulaması ve terminal durum takibi. Incomplete/failed/cancelled başarı sayılmaz. Token yenileme ve self-managed Config API kapsam dışı; timeout işi iptal etmez. Native teslim platform job kaydıdır, yeni bağlantı tanımı değildir.

- Dataform: v1 API tam Git SHA ile yeni compilationResult oluşturma/geri okuma, mevcut compilationResult kontrolü ve workflow invocation başlatma/takip. Derleme hatası çalışmayı engeller; invocation/compilation kaynak kimlikleri eşleşmelidir. Yeni SQLX/model üretimi ve canlı BigQuery kabulü henüz yok.

- Power BI enhanced refresh: full/transactional semantic model yenileme, refresh ID ve Location denetimi; HTTP 202 kabul, HTTP 200 + Completed başarı. İptal/başarısızlık ve timeout ayrılır; platform hata ayrıntıları saklanmaz. Shared capacity ve yeni rapor üretimi kapsam dışıdır.

- Databricks Jobs API 2.2: mevcut job run-now, job parametreleri, çağrı öncesi idempotency token ve run ID takibi. Yalnızca TERMINATED/SUCCESS kabul edilir. Job tanımı, notebook sonucu indirme ve token yenileme yok; canlı compute kabulü bekliyor.

- Databricks mevcut run takibi: ayrı read-only paket, önceki job_id/run_id ile GET üzerinden devam eder. Yeni run başlatmaz; eski output korunur. Diğer platformlarda genel checkpoint/resume henüz yok.

- Airbyte mevcut job takibi: connection_id/job_id ile yalnızca GET; kesinti sonrasında aynı sync takip edilir. Yeni aktarım/reset/cancel başlatılmaz; eski çıktı dosyaları korunur.

- Fabric Notebook: kullanıcı ipynb kaynağından yeni öğe oluşturma, 201/202 LRO takibi, workspace/öğe kontrolü ve getDefinition ile geri indirme. Hücre kaynakları karşılaştırılır; çıktı içeren girdi reddedilir. Notebook execution, Lakehouse bağlama ve canlı Fabric kabulü henüz yok.

## Öncelikli kalan işler

Snowflake ilk kesiti: OAuth SQL API, benzersiz isimli kalıcı profil view’ı ve view sorgusu. HTTP 202 polling, HTTP 422 hata, statement kimliği kontrolü ve tek satırlık aggregate sonuç doğrulaması uygulanır. Kaynak satırlar indirilmez; hesapta CREATE VIEW/SELECT yetkileri ve warehouse gerekir. Native SQL ve sorgu kimlikleri kaydedilir. Bu, iş kurallarına göre kalite onayı değildir. Canlı SQL derleme ve tenant kabulü bekliyor.

- **BI:** Qlik QIX ile şema keşfi ve onaylı sheet/chart tanımından sıfırdan uygulama üretimi; Qlik Windows QRS sertifika/virtual proxy adaptörü; Power BI/Fabric rapor tanımı ve semantic model oluşturma; Tableau template/data-source bağlama ve publish. Her ürün için ayrı gerçek açılış testi.
- **ERP:** SAP teklif dışındaki belge türleri ve eşleme, S/4 communication scenario bazlı uygulama; Logo Tiger/GO Objects ve Netsis NetOpenX sürüm/lisans sözleşmeleri; Oracle ERP belge/reporting servisleri. Genel POST endpoint uydurulmayacak. Logo resmi doküman erişiminde bu çalışmada hata alındı; eski üçüncü taraf örnekler ürün kodu kaynağı yapılmadı.
- **Veri platformları:** Databricks/Snowflake/Redshift vb. resmi job/query API ve notebook/model artifact'ları.
- **ETL/DataOps:** ADF pipeline şemasının canlı kabulü, Fabric pipeline, Airflow DAG oluşturma ve Dagster/Prefect tanımları; ürünün kabul ettiği tanımı dağıtma ve çalışma kaydı.
- **Kalite/yönetişim/MDM/güvenlik:** metadata keşfi → kural tasarımı → vendor tanımı → sandbox doğrulaması.
- **ML/GenAI/analitik/GIS/planlama/migrasyon/FinOps/dosya araçları:** matriste belirtilen SDK/API yolunun ürün/sürüm bazında doğrulanması; çıktı sözleşmesi, uygulama ve testler.

185 ürünün tamamı `INTEGRATION-MATRIX.md` ve `dist/integration-plan.json` içinde yer alır. Ortak taşıma altyapısının bulunması bu ürünlerin entegrasyonlarının tamamlandığı anlamına gelmez.

## Mevcut sınırlar

- Otomatik OAuth consent/token yenileme, streaming büyük indirme, genel checkpoint resume henüz yok; Databricks, Airbyte, ADF, Airflow, dbt Cloud, Prefect, Dataform ve Power BI için mevcut iş kimliğiyle ayrı takip paketleri var.
- Download redirect'leri güvenlik için reddedilir; bu bazı gerçek tenant akışlarını ayrıca geliştirmeyi gerektirebilir.
- Qlik Cloud dışında QVF uyumluluğu kanıtlanmadı. PBIX ve Tableau test dosyaları yerel fixture, gerçek uygulama dosyası değil.
- Başarısız yazma isteği otomatik tekrar edilmez; kullanıcı kaydedilen job/app ID'yi incelemelidir.
- Yerel testler gerçek hesap, lisans ve kurulum erişiminin yerini tutmaz.
