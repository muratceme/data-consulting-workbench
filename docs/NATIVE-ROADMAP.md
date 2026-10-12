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
- SAP B1/S4 ve Oracle Fusion: gerçek servis şemasını keşfetme. ERP belge/rapor üretimi sonraki aşamadır.
- BigQuery: API'de SELECT doğrulama, bütçeli WRITE_EMPTY hedef tablo işi; şemadan SQL üreten agent için yalnızca dry-run ve `.sql` teslimi.

## Öncelikli kalan işler

- **BI:** Qlik QIX ile şema keşfi ve onaylı sheet/chart tanımından sıfırdan uygulama üretimi; Qlik Windows QRS sertifika/virtual proxy adaptörü; Power BI/Fabric rapor tanımı ve semantic model oluşturma; Tableau template/data-source bağlama ve publish. Her ürün için ayrı gerçek açılış testi.
- **ERP:** SAP belge taslağı ve eşleme, S/4 communication scenario bazlı uygulama; Logo Tiger/GO Objects ve Netsis NetOpenX sürüm/lisans sözleşmeleri; Oracle ERP belge/reporting servisleri. Genel POST endpoint uydurulmayacak. Logo resmi doküman erişiminde bu çalışmada hata alındı; eski üçüncü taraf örnekler ürün kodu kaynağı yapılmadı.
- **Veri platformları:** Databricks/Snowflake/Redshift vb. resmi job/query API ve notebook/model artifact'ları.
- **ETL/DataOps:** ADF/Fabric pipeline, Airflow/Dagster/Prefect tanımları; ürünün kabul ettiği tanımı dağıtma ve çalışma kaydı.
- **Kalite/yönetişim/MDM/güvenlik:** metadata keşfi → kural tasarımı → vendor tanımı → sandbox doğrulaması.
- **ML/GenAI/analitik/GIS/planlama/migrasyon/FinOps/dosya araçları:** matriste belirtilen SDK/API yolunun ürün/sürüm bazında doğrulanması; çıktı sözleşmesi, uygulama ve testler.

185 ürünün tamamı `INTEGRATION-MATRIX.md` ve `dist/integration-plan.json` içinde yer alır. Ortak taşıma altyapısının bulunması bu ürünlerin entegrasyonlarının tamamlandığı anlamına gelmez.

## Mevcut sınırlar

- Otomatik OAuth consent/token yenileme, streaming büyük indirme, checkpoint'ten resume henüz yok.
- Download redirect'leri güvenlik için reddedilir; bu bazı gerçek tenant akışlarını ayrıca geliştirmeyi gerektirebilir.
- Qlik Cloud dışında QVF uyumluluğu kanıtlanmadı. PBIX ve Tableau test dosyaları yerel fixture, gerçek uygulama dosyası değil.
- Başarısız yazma isteği otomatik tekrar edilmez; kullanıcı kaydedilen job/app ID'yi incelemelidir.
- Yerel testler gerçek hesap, lisans ve kurulum erişiminin yerini tutmaz.
