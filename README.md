# Datawork — uygulamaya özgü veri otomasyonları

Veri işleri için iki tür paket: **kod blokları** (aynı girdiden hep aynı sonuç; platform API çalıştırıcıları ve internete çıkmayan yerel araçlar) ve **AI agent’lar** (muhakeme adımı modelde, ayrıştırma ve denetim kodda). Kullanıcı sitede türü seçer. Site yalnızca keşif ve indirme arayüzüdür; anahtar veya veri toplamaz.

## Güncel durum

17 Ekim 2026: 23 alan / 206 ürün bağlamı. **387 indirilebilir paket**: 37 platform API paketi (36 kod bloğu + BigQuery agent) ve 350 yerel paket (322 kod bloğu + 28 agent). Toplam **358 kod bloğu, 29 agent**; 23 alanın hepsinde en az bir indirilebilir paket var. Diğer 372 görev **planlanan entegrasyon**; indirme bağlantıları yok.

### Yerel kod blokları (internet yok, Python standart kütüphanesi)

| Paket | Girdi | Çıktı |
|---|---|---|
| Veri profili | CSV/XLSX | Tip, boşluk, aralık, mükerrer, kişisel veri izi |
| Kalite kontrolü | CSV/XLSX + rules.json | İhlal listesi (Excel satır no), `--strict` çıkış kodu |
| Tablo mutabakatı | İki CSV/XLSX + anahtar | Yalnız kaynakta/hedefte, alan farkları, toplamlar |
| SQL soy ağacı | .sql klasörü | Kaynak/ara/son tablolar, riskli ifadeler, Mermaid |
| Power BI model dokümanı | .pbit / model.bim | Tablo, ölçü, ilişki, model riskleri |
| Tableau envanteri | .twb/.twbx | Kaynaklar, hesaplanan alanlar, kullanılmayan alanlar |
| Qlik soy ağacı | .qvs | Tablolar, kaynaklar, STORE/DROP, uyarılar, Mermaid |
| dbt denetimi | manifest.json | Test/doküman kapsamı, katman kuralları, freshness |
| KVKK maskeleme | CSV/XLSX | Takma ad (tuzlu HMAC) / kısmi / sil; not alanındaki gömülü veriler; yeniden tarama |
| Şema farkı | İki DDL dosyası/klasörü | Eklenen/silinen tablo-sütun, tip daralması, NOT NULL, olası yeniden adlandırma, ALTER taslağı |
| Tarih tablosu | Tarih aralığı + tatil dosyası | Türkçe takvim boyutu, resmî tatil, iş günü sırası, mali yıl; CSV + SQL |
| Dosya birleştirme | CSV/XLSX klasörü | Tek tablo + kaynak dosya; şema kayması, ondalık ayırıcı farkı, mükerrer satır |

### Tek amaçlı yerel kod blokları (310)

Her blok tek bir işi yapar; `library/blocks/<kategori>.py` içinde `islem.op(...)` ile kaydedilir. Kayıt; katalog metnini, komut satırı seçeneklerini, örnek veriyi ve örnek veride **elle doğrulanmış beklenen sonucu** birlikte taşır. Paketteki `main.py` iki satırlık bir başlatıcıdır; ortak çalıştırıcı (`islem.py`) girdiyi okur, hiçbir dosyayı değiştirmez, ağa çıkmaz ve `output/` altına `sonuc.csv`, `rapor.md`, `ozet.json` yazar.

| Kategori | Blok | Örnekler |
|---|---|---|
| Veri temizleme ve dönüştürme (`temizlik.py`) | 27 | boşluk/görünmez karakter, snake_case, Türkçe büyük-küçük harf, bozuk karakter (Ã¼) onarma, tarih ve sayı biçimi, filtre ve formül (güvenli ifade), JOIN, DÜŞEYARA, pivot/unpivot, gruplama, dosya bölme, kodlama |
| Veri doğrulama (`dogrulama.py`) | 15 | TCKN, VKN, IBAN (+banka), telefon (E.164), e-posta yazım önerisi, plaka, 81 il, posta kodu–il, kart Luhn + maskeleme, referans bütünlüğü, anahtar adayı, A→B tutarlılığı, desen profili, kontrol toplamı, satır kuralları |
| Muhasebe ve finans (`finans.py`) | 17 | TDHP mizan kontrolü, özet bilanço/gelir tablosu, yevmiye denetimi, cari yaşlandırma, FIFO tahsilat eşleştirme, banka mutabakatı, MT940, e-Fatura (UBL-TR), KDV, kur çevirme, Benford, mükerrer ödeme, amortisman, kredi planı, stok maliyeti (FIFO/ortalama), gider sınıflandırma, nakit akışı |
| Ana veri (`anaveri.py`) | 12 | benzer firma kümeleme, ünvan standardı, adres ayrıştırma, golden record + kaynak izi, ad-soyad ayırma, hiyerarşi (döngü/yetim), referans kod listesine eşleme, kayıt tamlık/geçerlilik skoru, paylaşılan vergi no/IBAN/telefon, hiyerarşi düzleştirme, ana veri değişiklik denetimi (IBAN + iletişim birlikte değişti), ürün özelliklerinin kategori şablonuna uyumu |
| İstatistik (`analiz.py`) | 17 | tanımlayıcı istatistik, korelasyon, aykırı değer, histogram, ki-kare, A/B testi, örneklem büyüklüğü, regresyon, mevsimsellik, geriye dönük testli tahmin, seri anomalisi, dilimler, ölçekleme, Pareto, bootstrap güven aralığı, normallik (Jarque-Bera), Mann-Whitney U |
| Satış ve müşteri (`musteri.py`) | 14 | RFM, kohort, ABC, XYZ, sepet analizi, churn riski, CLV, huni, oturum, ürün performansı, dönem karşılaştırma, yeniden sipariş noktası, özellik benimseme, NPS ve hata payı |
| SQL (`sqlgen.py`) | 10 | CSV→CREATE TABLE/INSERT (6 lehçe), MERGE/UPSERT, SCD2, güvenli biçimlendirici, lint, DDL→veri sözlüğü/ER (Mermaid), lehçe çevirisi, veri testleri |
| Dosya biçimleri (`bicim.py`) | 9 | iç içe JSON, JSON Lines, XML, sabit genişlik, Excel sayfaları, CSV→XLSX, erişim logu, HTML rapor |
| BI üreticileri (`bigen.py`) | 7 | DAX zaman zekâsı, Power Query M, BigQuery şeması, Qlik LOAD, Power BI RLS, KPI trafik ışığı, dbt iskeleti |
| Hazır rapor şablonları (`rapor.py`) | 7 | tek CSV/Excel dosyasından Qlik Sense uygulaması (script, ana öğeler, rapor sayfası), Power BI projesi (PBIP: TMDL model + PBIR rapor), Tableau çalışma kitabı (.twb), Looker projesi (view, explore, pano), formüllü ve grafikli Excel raporu, çalışır dbt projesi, tek dosyalık HTML pano |
| Konum ve sensör (`zaman.py`) | 6 | koordinat kontrolü, en yakın nokta, eşik ihlali, veri boşluğu, yeniden örnekleme, iş saati SLA |
| Konum (`konum.py`) | 8 | GeoJSON bölgelerine nokta atama (delikli/çoklu çokgen), araç GPS rota, durak ve sıçrama özeti, mesafe matrisi ve en yakın komşu, geofence giriş/çıkış, GPS durak tespiti, geohash ızgara yoğunluğu, mağaza kapsama alanı, DBSCAN konum kümeleme |
| Bütçe ve planlama (`planlama.py`) | 13 | bütçe–gerçekleşen, bütçe dağıtımı, yıl sonu tahmini, senaryo, konsolidasyon + grup içi eliminasyon, çağrı merkezi personel ihtiyacı (Erlang C), fiyat–hacim–karma sapması, çok ürünlü başa baş noktası, formül tabanlı duyarlılık (tornado), personel maliyet planı, tahmin doğruluğu (WAPE, bias, FVA), kapasite planlama (doluluk, ek kapasite), ay içi satış hedef takibi |
| Orkestrasyon (`orkestrasyon.py`) | 7 | cron açıklama + sonraki çalışmalar, Airflow DAG statik denetimi (ast), GitHub Actions güvenlik denetimi, iş bağımlılığı ve kritik yol, zamanlama eş zamanlılığı ve kendi üstüne binen işler, bakım penceresi çakışması, yeniden deneme ve kararsız görevler |
| Entegrasyon (`entegrasyon.py`) | 2 | ADF/Synapse pipeline envanteri (Mermaid), SSIS .dtsx paket envanteri |
| Lakehouse (`lakehouse.py`) | 7 | Delta _delta_log özeti, Iceberg metadata özeti, nesne depolamada küçük dosya analizi, Delta VACUUM tahmini, bölüm yolu tutarlılığı ve eksik tarihler, medallion katman kuralları, OPTIMIZE/VACUUM bakım planı |
| Veritabanı (`veritabani.py`) | 14 | SQL Server plan (.sqlplan), bekleme istatistikleri, indeks kullanımı, deadlock XML; PostgreSQL EXPLAIN JSON, pg_stat_statements, tablo şişmesi/vacuum; MySQL slow query log, SQL Server eksik indeks önerileri, PostgreSQL kilit bekleme ağacı, yedek geçmişi denetimi, MongoDB profiler sorgu şekilleri, uzun işlemler ve idle in transaction, tekrar ve kapsanan indeksler |
| Ambar ve FinOps (`maliyet.py`) | 14 | Snowflake sorgu geçmişi ve kredi, BigQuery iş maliyeti, bulut faturası özeti, ay sonu maliyet tahmini, zorunlu etiket uyumu, taahhüt (reserved/savings plan) önerisi, rightsizing, ortak maliyet dağıtımı (showback), birim maliyet trendi, depolama sınıfı (tier) önerisi, atıl kaynaklar, kullanılmayan lisanslar, egress maliyeti |
| Ambar tasarımı (`ambar.py`) | 6 | Redshift dağıtım/sıralama/sıkıştırma denetimi, Snowflake Time Travel/Fail-safe/klon depolama, BigQuery bölümleme/kümeleme denetimi, sorgulanmayan tablolar, SCD2 bütünlüğü (çakışma, boşluk, çok güncel), Snowflake clustering derinliği |
| Migrasyon (`gecis.py`) | 13 | alan eşleme doğrulama, tablo satır sayısı mutabakatı, hedef uzunluk/kodlama riski, anahtarsız sütun profili karşılaştırması, T-SQL/Oracle → PostgreSQL sorgu çevirisi, bağımlılık ve hacme göre geçiş dalgaları, anahtara göre satır mutabakatı, identity/sequence çakışma kontrolü, hedef tip uyumu (taşma, yuvarlama, geçersiz tarih), dalga bazında süre tahmini, saat dilimi kayması, boş/NULL dönüşümü, bozuk Türkçe karakter (mojibake) tespiti ve onarımı |
| dbt (`donusum.py`) | 3 | manifest.json test ve doküman kapsamı, source freshness özeti, incremental model riskleri (unique_key, filtre, geri bakış) |
| ML (`ml.py`) | 12 | sınıflandırma ve regresyon metrikleri, PSI veri kayması, sızıntısız ayırma, sızıntı kontrolü, F1/maliyete göre eşik seçimi, kalibrasyon (Brier, ECE), WoE/IV, çok sınıflı metrikler, lift/kazanç ve KS, grup adaleti (4/5 kuralı, TPR/FPR farkı), tahmin aralığı kapsaması (Winkler) |
| GenAI / RAG (`rag.py`) | 14 | başlık duyarlı parçalama (JSONL), yakın kopya doküman, token/maliyet, prompt enjeksiyon taraması, hit@k/MRR, cevap değerlendirme (EM, token F1), LLM log maskeleme, LLM kullanım/maliyet/gecikme analizi, prompt şablonu değişken kontrolü, kaynak atıf doğrulama, kullanıcı geri bildirimi (Wilson alt sınırı), embedding vektör kontrolü, geri getirme kaynak kapsaması, prompt sürümü karşılaştırma (işaret testi) |
| Streaming (`akis.py`) | 13 | olaylardan JSON Schema, Avro uyumluluğu, olay gecikmesi/watermark, Kafka consumer lag, Kafka topic replikasyon denetimi, tekrar/çelişkili kopya/sıra boşluğu, Debezium CDC özeti, olay zamanına göre pencere toplamı ve geç olaylar, mesaj boyutu dağılımı, partition anahtarı çarpıklığı, DLQ hata grupları, outbox ↔ yayın mutabakatı, Schema Registry uyumluluk denetimi |
| Gizlilik ve yönetişim (`yonetisim.py`) | 5 | klasör kişisel veri envanteri, saklama süresi, KVKK 30 gün başvuru takibi, erişim gözden geçirme, sahiplik kapsamı |
| Gizlilik (`gizlilik.py`) | 9 | k-anonimlik ve l-çeşitlilik, genelleştirerek anonimleştirme, İYS izin kontrolü, yurt dışı aktarım envanteri, 72 saatlik ihlal bildirim takibi, çerez envanteri denetimi, ilgili kişi başvurusu için klasör araması, test ortamında gerçek görünümlü kişisel veri, maskeleme doğrulama (sızıntı, tutarlılık, çakışma) |
| Yönetişim (`katalog.py`) | 11 | iş sözlüğü kalitesi, SQL betiklerinden soy ağacı ve etki analizi, görevler ayrılığı (SoD), sınıflandırma tutarlılığı, veri sözleşmesi (data contract), erişim talebi denetimi (kendi onayı, süresi dolan erişim), BI rapor kullanım envanteri, sınıflandırmanın soy ağacı boyunca yayılımı, iki yetki listesi farkı, politika istisna kaydı, kullanılmayan yetkiler (en az yetki) |
| Gözlemlenebilirlik (`gozlem.py`) | 12 | tazelik SLA, hacim anomalisi, şema değişikliği, NULL oranı sıçraması, iş çalışma geçmişi (başarı, p95, yavaşlama), log hata imzaları, kalite testi skor kartı (yeni/kararsız/kronik), teslim SLA uyumu, katmanlar arası satır kaybı, alarm gürültüsü (MTTA/MTTR), uçtan uca veri gecikmesi (p50/p95, SLA), kalıcı seviye değişimi (değişim noktası) |
| BI ve dbt çeviri/analiz (`bi_ceviri.py`) | 6 | dbt run_results ve DAG, LookML envanteri, Power BI ölçü bağımlılığı, Tableau→DAX, Qlik→DAX |

**Hazır rapor şablonları.** Yedi şablon aynı rapor tasarımını kullanır: ölçüler (toplam/ortalama), farklı sayı KPI’ı, en çok üç kırılım boyutu, takvim ve aylık trend, filtreler ve detay tablo. Sütunlar veriden seçilir, `--measures`, `--dimensions`, `--date` ile değiştirilir. Her şablon aynı rakamları gösteren bir HTML önizleme de yazar. Geliştirme sırasında biçimler resmî doğrulayıcılardan geçirildi: Power BI rapor JSON’ları Microsoft’un PBIR JSON şemalarından, TMDL modeli Microsoft’un TMDL ayrıştırıcısından (Analysis Services kütüphanesi), Tableau çalışma kitabı Tableau’nun resmî TWB XSD’sinden (2026.1), Excel dosyası Open XML SDK doğrulayıcısından geçti; Excel formülleri bağımsız bir formül motoruyla yeniden hesaplanıp önbellek değerleriyle karşılaştırıldı. dbt projesi dbt-core 1.12 + DuckDB ile `dbt build` çalıştırılarak denendi (tüm modeller ve testler geçti, sonuç tabloları Python hesabıyla aynı). Qlik nesneleri corectl’in açık kaynak build biçimine, LookML lkml ayrıştırıcısına göre denetlendi. **Hiçbiri Qlik, Power BI Desktop, Tableau veya Looker’ın kendisinde açılarak test edilmedi.** Kirli veride (aynı değerin farklı büyük/küçük harfle yazılması, boş boyut) şablonlar uyarı yazar; Excel ve Power BI bu değerleri tek, Qlik ve Tableau ayrı sayar.

İstatistik bloklarındaki dağılım fonksiyonları (normal, t, ki-kare) standart kütüphaneyle yazıldı ve tablo değerleriyle karşılaştırıldı. Finans blokları kontrol amaçlıdır; muhasebe kaydı veya beyan yerine geçmez.

### Yerel AI agent’lar (Ollama, OpenAI veya Anthropic)

SQL yazarı, DAX ölçü yazarı, veri sözlüğü, kalite kuralı önerici, Qlik → Power BI geçişi, KPI tanımlayıcı, SQL açıklayıcı (yanıtta SQL’de olmayan tablo adı reddedilir), dönem yorumu (toplamlar kodla hesaplanır; metindeki her sayı bu olgularla eşleştirilir), KVKK işleme envanteri (yalnızca sütun adı ve tespit edilen tür gider; geçersiz hukuki sebep kodu reddedilir), log olay analisti (hata imzaları kodla çıkarılır; FATAL her zaman P1), boyutsal model tasarımcısı (her sütun tam bir kez kullanılmalı; anahtar → nitelik bağımlılığı gerçek veride ölçülür, DDL kodla yazılır), regex yazarı (desen her örnekte yerelde denenir; iç içe niceleyici reddedilir) ve migrasyon eşleme yazarı (eşlenmeyen hedef sütun ve başka tablodan sütun reddedilir; tip daralması kodla işaretlenir, INSERT … SELECT kodla yazılır), dbt model yazarı (sorguda ham tablo adı kalırsa reddedilir; model SQL’i ve yml dosyaları kodla yazılır), gece işi zamanlayıcı (plan yerelde simüle edilir: bağımlılık, pencere ve eşzamanlılık), FinOps tasarruf planı (tasarruf dayandığı kalemin maliyetini aşamaz; sayılar olgularla eşleştirilir), model kartı (metrikler kodla hesaplanır; metindeki her sayı denetlenir) ve ana veri eşleştirme hakemi (VKN/telefon gönderilmez; birleşme grupları kodla kurulur), SQL optimizasyon danışmanı (yeniden yazım yalnızca özgün tabloları okuyabilir; indeks sorguda geçmeyen sütuna konamaz), Avro şema tasarımcısı (şema tüm örnek olaylara karşı yerelde doğrulanır), RAG test seti üretici (her cevabın dayanağı parçada birebir aranır), kalite kök neden analisti (hataların yoğunlaştığı değerler kodla ölçülür) ve A/B test karar notu (anlamsız farkta “yayına al” ve SRM varken geçerli karar reddedilir), veri sözleşmesi yazarı (sözleşme örnek teslimata uygulanır; açıklanmayan ihlal reddedilir), bütçe sapma yorumcusu (olumsuz sapma atlanamaz ve olumlu sunulamaz), TDHP hesap planı eşleyici (KDV, kasa, gider sınıfı gibi kurallar kodla denetlenir), JSON eşleme yazarı (eşleme örnek JSON’a uygulanıp tipler ve zorunlu alanlar ölçülür) ve bölümleme danışmanı (bölüm sayısı ve bölüm başına boyut örnekte ölçülür). Ortak kurallar (`library/agents/llm.py`): kişisel veri maskelenir, giden metin `output/gonderilecek-istek.json` dosyasına yazılır ve onay istenir (`--yes`), `--dry-run` modeli çağırmaz, model yanıtı JSON olarak yerelde denetlenir ve kurala uymazsa bir kez düzeltme istenir. Model üretimi hiçbir sistemde otomatik çalıştırılmaz.

### Platform API paketleri

| Uygulama | Uygulanmış işlem | Teslim |
|---|---|---|
| Qlik Cloud | Mevcut uygulama export; şablonu kopyala → reload → export | `.qvf`, şablon akışında yeni tenant uygulaması |
| Power BI Service | Mevcut indirilebilir raporu export | `.pbix` (yeni rapor tasarlamaz) |
| Power BI enhanced refresh | Mevcut semantic model full/transactional yenileme ve kesin refresh ID takibi | Yenilenmiş model + refresh kaydı; shared capacity desteklenmez |
| Tableau Cloud / Server | PAT oturumu → workbook download → signout | Yanıta göre `.twb` / `.twbx` |
| SAP Business One | Service Layer metadata; yeni satış teklifi taslağı oluşturma ve geri okuma | `.edmx` veya ERP içinde Drafts kaydı/DocEntry |
| SAP S/4HANA | Kullanıcının etkin Bearer OData servisinden metadata | `.edmx` servis sözleşmesi |
| Oracle Fusion ERP | REST describe | Kaynak sözleşmesi JSON; ERP belgesi değildir |
| BigQuery | SELECT dry-run → bütçe kontrolü → hedef tablo işi → sonuç kontrolü | BigQuery'de tablo, yerelde iş makbuzu |
| BigQuery agent | Canlı API şeması → model → BigQuery SELECT dry-run | `.sql`; sorgu çalıştırılmaz |
| Fabric Lakehouse | İncelenmiş ipynb yayımla → asenkron sonucu izle → geri indir ve kaynak hücreleri karşılaştır; mevcut notebook için ayrı export | Workspace notebook + `.ipynb`; otomatik çalıştırılmaz |
| Fabric Notebook Jobs | Mevcut notebook RunNotebook çalıştırma; ayrı GET takip paketi | Platform job instance + sonuç kaydı; parametre/session override yok |
| Databricks Lakehouse | Profil notebook’u üret → SOURCE import → get-status → export | Workspace notebook + Python kaynak dosyası; otomatik çalıştırılmaz |
| Databricks Jobs | Mevcut job’ı parametrelerle çalıştır, job/run kimliklerini ve terminal sonucu doğrula | Platform run + sonuç kaydı; görev çıktıları indirilmez |
| Databricks mevcut run takibi | Kesinti sonrası job_id/run_id ile yalnızca GET üzerinden takip | Yeni iş başlatmadan sonuç kaydı |
| Azure Data Factory | Yeni isimle pipeline yayımlama; ayrıca mevcut pipeline çalıştırma/takip | Native pipeline veya pipeline run |
| Airflow 3.2 | Public API v2 ile mevcut DAG tetikleme ve takip | DAG run; başarısızlıkta başarı çıktısı yok |
| dbt Cloud | Mevcut dönüşüm/test job’ını başlat, takip et ve artifact indir | dbt run + platformun ürettiği `run_results.json` |
| Prefect 3 Cloud | Deployment’tan parametreli flow run oluştur ve takip et | Platformda flow run + yalnızca kimlik/durum içeren makbuz |
| Snowflake | OAuth SQL API ile yeni profil view’ı oluştur, sorgula ve aggregate sayımları doğrula | Kalıcı view + `quality-view.sql` + sorgu kimliği/sayımlar; kalite kararı verilmez |
| GitHub Actions | Mevcut workflow_dispatch işini başlat, kesin run kimliğiyle takip et | Platform workflow run + sonuç kaydı; artifact indirme içermez |
| Fivetran | Manual bağlantıyı force=false ile sync et, yeni başarı zamanını izle | Platform sync + zaman damgası kaydı; kesin run ID yok, eşzamanlı tetikleme desteklenmez |
| Airbyte Cloud | Mevcut bağlantı için sync job oluştur, job/connection kimliğiyle izle | Platform job + varsa satır/bayt sayaçları; reset çağrısı yapılmaz |
| Airbyte mevcut job takibi | Kesinti sonrası connection_id/job_id ile yalnızca GET üzerinden takip | Yeni aktarım başlatmadan iş sonucu ve sayaçlar |
| Google Cloud Dataform | Sabit Git SHA’dan compilationResult oluştur/geri oku; ayrıca mevcut derlemeyi çalıştır ve takip et | Platform compilationResult veya workflow invocation; yeni SQLX/model tasarlamaz |

**Canlı vendor/tenant testi: 0. Uygulamada dosya açılışı testi: 0.** Testler yerel HTTP sözleşme sunucusunu kullanır; gerçek QVF/PBIX üretildiğini kanıtlamaz. Agent model yanıtı testte taklit edilir. Bu paketler henüz üretim onaylı değildir.

SAP/Oracle keşif paketleri rapor otomasyonu olarak etiketlenmez. SAP satış teklifi taslağı ayrı bir yazma işlemidir; kesin belge oluşturmaz. Qlik şablon akışı (platform paketi) mevcut grafik ve load script'ini kullanır; sıfırdan rapor kurmak için yerel `qlik-rapor-sablonu` bloğu kullanılır. Logo entegrasyonu henüz uygulanmadı; doğrulanmamış endpoint üretilmez.

## Yerelde çalıştırma

Python 3.10+ ve Node.js 22+:

```sh
npm run build
npm test
npm start
```

Sunucu varsayılan olarak `http://localhost:4322` üzerinde çalışır. Uygun değilse boş bir porta ilerler. Arayüz `site/` altında: `index.html`, `style.css` (Aurora, yalnız koyu) ve `app.js`. Listede “Tümü / Kod blokları / AI agent’lar” seçimi vardır (`#k=code`, `#k=agent`). Planlı ürünler kapsam haritasında gösterilir; indirme bağlantısı yoktur.

## Paketi kullanma

Yerel kod bloğu: ZIP’i aç, `python main.py` örnek veriyle çalışır; kendi dosyan için `python main.py --help`. Yerel agent: `.env.example` → `.env` (PROVIDER, MODEL), önce `python main.py --dry-run`, sonra `python main.py`.

Platform paketi:

1. ZIP'i çıkarın; README'deki desteklenen ürün, izin ve platform etkilerini okuyun.
2. `.env.example` → `.env`; ilgili platformun kendi token/erişim bilgilerini girin. OAuth token edinme ve yenileme sizin kimlik sağlayıcınızda yapılır.
3. `config.json` içindeki kaynak/hedef kimliklerini, agent için `context.md`, SQL işi için `query.sql` dosyasını düzenleyin.
4. `python native.py --plan` ağsız önizleme yapar.
5. `python native.py --execute --output output/run-001` gerçek platform çağrılarını yapar.

Yeni/boş çıktı klasörü gerekir. Yazma ve LLM maliyeti olabilen işlemler README'de belirtilir. Şema ve proje bağlamı seçilen modele gönderilir; agent satır verisi çekmez. Kısmi çalışma kimlikleri `run-state.json`, dosya özeti `manifest.json` içindedir. Otomatik rollback veya üretimde otomatik model-kodu yürütme yoktur.

Toplam **213 test** geçiyor (310 tek amaçlı blok, bir testin alt testleri olarak tek tek); tek amaçlı blokların her biri paketlenmiş kopyasıyla kendi örnek verisinde çalıştırılıp beklenen sonuçla karşılaştırılır. Yerel paket testleri (`tests/test_local.py`) paketlenmiş kopyayı örnek veriyle çalıştırır; agent’lar OpenAI/Anthropic/Ollama biçiminde konuşan sahte model sunucusuyla test edilir (maskeleme, onay, dry-run, düzeltme denemesi, HTTP hatası). İlk sekiz agent ayrıca yerel bir Ollama modeliyle (qwen3 14B) elle denendi; son yirmi agent yalnızca sahte model sunucusuyla test edildi. Test sayısı canlı tenant doğrulaması değildir.

## Test katmanları

- Eski yerel motorların regresyon testleri (bu motorlar artık ürün paketleri olarak dağıtılmaz).
- Platformlara özgü gerçek localhost HTTP çağrıları: path, payload, auth headers, polling, hata ve çıktı türü.
- Eksik erişim bilgisi, redirect/origin, GET retry/POST tekrar etmeme, bütçe sınırı, başarısız reload, mevcut çıktıyı koruma.
- Her ZIP'te bütünlük/SHA-256; tüm paketlerin ağsız `--plan` çalışması; planlanan görevlerde indirme olmaması.
- Yerel kod blokları ve agent’ların paketlenmiş kopyayla uçtan uca testleri.

Canlı uçtan uca test ayrı bir aşamadır: lisanslı sandbox, ilgili sürüm, geçerli erişim ve artifact'ı hedef uygulamada açıp inceleme gerekir. Bunlar yerel sözleşme testinin yerine konmaz.

## Yapı

- `catalog/catalog.py`: tüm alan ve ürün taksonomisi; önceki CSV taslakları tarihsel yardımcı olarak durur.
- `catalog/native_catalog.py`: uygulanmış işlemler, erişim, etkiler, resmi kaynaklar; diğer ürünlerin açık plan durumu.
- `library/native.py`: API transport, platform akışları ve çıktı denetimi.
- `library/agent.py`: model sağlayıcısı; yeni akışta yalnızca model çağrısı için kullanılır.
- `catalog/local_catalog.py`: yerel kod blokları ve agent’ların tanımı; `CATEGORY_MODULES` tek amaçlı blok modüllerini yükler.
- `library/blocks/islem.py`: tek amaçlı blokların ortak çalıştırıcısı ve kayıt mekanizması.
- `library/blocks/`, `library/agents/`, `library/samples/`: yerel paket kodu ve kurgusal örnek veriler (`scripts/make_samples.py` ikili örnekleri üretir).
- `scripts/build.py` + `scripts/local_package.py`: native ve yerel paketleri üretir; eski üretilmiş ZIP'leri kaldırır.
- `dist/integration-plan.json`: 197 ürünün makine tarafından okunabilir entegrasyon planı.
- `docs/INTEGRATION-MATRIX.md`: tüm ürünler ve kalan kapsam.
- `docs/NATIVE-ROADMAP.md`: tamamlanma ölçütleri ve sonraki geliştirme işleri.

[GitHub](https://github.com/muratceme/data-consulting-workbench) · MIT.

## Databricks takibine kesinti sonrası devam

Katalogdaki **Mevcut Databricks çalışmasını yeniden başlatmadan takip et** paketini indirin. Önceki `run-state.json` içindeki `job_id` ve `run_id` değerlerini yeni paketin `config.json` dosyasına, aynı workspace adresiyle girin. Geçerli token yalnızca yerel `.env` içinde kalır. `python native.py --execute --output output/continued-001` yalnızca mevcut run durumunu okur. Yeni boş çıktı klasörü kullanın; önceki kayıt korunur. Run ID hiç alınamadıysa önce Databricks geçmişinden bulun; eski başlatma paketini tekrar çalıştırmak yeni job run oluşturabilir.

## Airbyte takibine kesinti sonrası devam

**Mevcut Airbyte aktarımını yeniden başlatmadan takip et** paketinde, önceki `run-state.json` içindeki `connection_id` ve `job_id` değerlerini `config.json` içine girin. Geçerli token yerel `.env` içinde kalır. `python native.py --execute --output output/continued-001` yalnızca mevcut işi okur; yeni/boş çıktı klasörü kullanın. Başlatma paketi tekrar çalıştırılırsa yeni sync oluşabilir; takip için ayrı monitor paketini kullanın.

## Diğer platformlarda kesinti sonrası takip

ADF, Airflow, dbt Cloud, Prefect, Dataform ve Power BI için de ayrı takip paketleri bulunur. Bu paketler yeni çalışma başlatmadan mevcut kimliği sorgular; dbt başarılı çalışma artifact’ını indirir. Platform ve yapılandırma eşlemeleri [kurtarma rehberinde](docs/RECOVERY.md).

Güncel dokuz takip akışında `--resume-from onceki/run-state.json` kullanılabilir: çalışma kimliği otomatik aktarılır, config kaynak kimlikleri karşılaştırılır, yeni iş başlatılmaz. Komut örnekleri [kurtarma rehberinde](docs/RECOVERY.md).
