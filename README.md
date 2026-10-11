# Datawork — Veri danışmanlığı çalışma alanı

Alan → Yazılım → Ürün / ortam → Danışmanlık görevi → Kod bloğu veya AI agent.

Türkçe, yerelde çalışan bir otomasyon kataloğu. Workers / Workless projesinin hiyerarşi yaklaşımından esinlenir; kaynak projeyi değiştirmez.

Arayüzde sağ üstte açık, koyu ve sistem teması seçilebilir. İlk açılışta işletim sistemi tercihi izlenir; elle seçilen tema tarayıcıda hatırlanır. Tema tüm kart, filtre, paket ayrıntısı ve demo ekranlarını kapsar. Hareket azaltma tercihi desteklenir; mobil menü klavye ve Escape ile kullanılabilir.

## Başlat

Node.js 18+ ve Python 3.10+ gerekir. Harici paket kurulumu gerekmez.

```sh
npm run build
npm start
```

Sunucu 127.0.0.1 üzerinde 4322’den başlayarak ilk boş portu bulur ve adresi terminale yazar. 4321 portundaki referans projeye dokunmaz. Sabit port için PORT ortam değişkenini kullanın. Python komutu farklıysa PYTHON değişkenini ayarlayın.

## Kapsam ve gerçek çalışma durumu

- 23 ana alan: BI, ERP, ambar, lakehouse, entegrasyon, analytics engineering, DataOps, streaming, veritabanı, kalite, yönetişim, MDM, gizlilik, observability, veri bilimi, MLOps, GenAI, ürün analitiği, coğrafi / IoT, EPM, migrasyon, FinOps, dosya verisi.
- SAP, Logo, Oracle, Qlik, Power BI, Tableau, BigQuery, Snowflake, Databricks ve diğer ürünler için bağlamlı paketler.
- **9 ortak yerel motor**: profil, kalite, rapor, şema karşılaştırma, mutabakat, sözlük, mükerrer, maskeleme, SQL envanteri. Çok sayıda katalog paketi bu ortak motorları yeniden kullanır; her paket bağımsız bir native ürün entegrasyonu değildir.
- **3 salt okunur API bağlayıcısı**: Qlik Cloud uygulama envanteri, Power BI workspace rapor envanteri, BigQuery dataset tablo envanteri. GET ile JSON / HTML rapor üretir. Tenant access token kullanıcının ortamında sağlanır. Mock testleri vardır; canlı hesap testi yapılmadı.
- **Agent paketleri**: ürün ve göreve göre bağlamlandırılmış danışmanlık iş akışları; OpenAI uyumlu Chat Completions API, Anthropic Messages API ve Ollama destekler. Bağlam / kanıt okur, muhakeme ve danışmanlık taslağı üretir. Üretim sisteminde işlem veya oluşturduğu kodu yürütmez. Ağsız dry-run test edildi; canlı model yanıtı test edilmedi.
- Yerel web demosu örnek veya kullanıcı CSV’siyle gerçek Python motorunu çalıştırır. 2 MB sınır vardır. Geçici girdi / çıktı işlem sonunda silinir. Demo hassas veriyi harici model API’sine göndermez.

Bu genişletilebilir başlangıç sürümüdür; bütün yazılımlar ve danışmanlık süreçleri tamamlanmış değildir. Ürün görevleri arttıkça native bağlayıcılar ve ürüne özgü motorlar eklenmelidir. Platforma doğrudan bağlanmayan tüm paketler arayüzde `Yerel dosya` olarak işaretlenir.

## Paket kullanımı

ZIP’i indirin ve açın. README ve config.json içindeki girdi alanlarını düzenleyin. Python komutunu veya Windows run.bat dosyasını çalıştırın. Harici Python kütüphanesi gerekmez. Çıktı `output/` altında oluşur.

Agent için `.env.example` dosyasını `.env` olarak kopyalayın. PROVIDER = openai / anthropic / ollama, MODEL = hesabınızdaki model adı. API anahtarları yalnızca yerel .env içinde tutulur. Önce `python agent.py --dry-run`; ardından `python agent.py`. Sağlayıcıya yalnızca belirtilen context.md ve evidence.json gönderilir. Yerel Ollama için anahtar gerekmez.

CSV: UTF-8, virgül ayırıcı, nokta ondalık. Mutabakat için boş olmayan benzersiz anahtar gerekir. Maskeleme yalnızca seçilen alanları `[MASKED]` yapar. SQL envanteri regex tabanlıdır; tam SQL parsing veya lineage sağlamaz.

## Doğrulama ve geliştirme

```sh
npm run build
npm test
```

Motorlar, hatalı veriler, mock API sayfalaması, ZIP bütünlüğü, SHA-256 ve her motorun indirilen paketinden çalıştırılması test edilir. CI aynı kontrolleri Windows ve Linux’ta yapar.

`catalog/catalog.py`: taksonomi ve görevler. `library/`: ortak motorlar. `scripts/build.py`: deterministik ZIP ve katalog üretimi. `site/`: bağımlılıksız web arayüzü. `server.mjs`: yerel sunucu ve demo. `tests/`: anlamlı çalışma testleri.

Yeni alan eklemek için DOMAINS listesine ürün / görev bağlamını ekleyin; yeni algoritma için ENGINES ve library/runner.py’yi genişletin, davranış testi ekleyin. Derlemeden sonra sunucu güncel dosyaları sunar.

Resmi kaynaklar ve doğrulama kapsamı: [catalog/SOURCES.md](catalog/SOURCES.md). MIT lisansı.
