# Datawork — uygulamaya özgü veri otomasyonları

Kullanıcının kendi API/SDK erişimiyle çalışan kod blokları ve agentlar. Site sadece keşif ve paket indirme arayüzüdür; API anahtarlarını toplamaz. Teslim, ilgili uygulamanın dosyası veya platform nesnesidir.

## Güncel durum

13 Ekim 2026: 23 alan / 185 ürün bağlamı korunuyor. 19 uygulanmış işlem, Tableau Cloud/Server ayrımıyla **20 indirilebilir paket** var. Diğer 370 görev **planlanan entegrasyon**; indirme bağlantıları yok. Bu sayıların hiçbiri tüm ürünlerin çalıştığı anlamına gelmez.

| Uygulama | Uygulanmış işlem | Teslim |
|---|---|---|
| Qlik Cloud | Mevcut uygulama export; şablonu kopyala → reload → export | `.qvf`, şablon akışında yeni tenant uygulaması |
| Power BI Service | Mevcut indirilebilir raporu export | `.pbix` (yeni rapor tasarlamaz) |
| Tableau Cloud / Server | PAT oturumu → workbook download → signout | Yanıta göre `.twb` / `.twbx` |
| SAP Business One | Service Layer metadata; yeni satış teklifi taslağı oluşturma ve geri okuma | `.edmx` veya ERP içinde Drafts kaydı/DocEntry |
| SAP S/4HANA | Kullanıcının etkin Bearer OData servisinden metadata | `.edmx` servis sözleşmesi |
| Oracle Fusion ERP | REST describe | Kaynak sözleşmesi JSON; ERP belgesi değildir |
| BigQuery | SELECT dry-run → bütçe kontrolü → hedef tablo işi → sonuç kontrolü | BigQuery'de tablo, yerelde iş makbuzu |
| BigQuery agent | Canlı API şeması → model → BigQuery SELECT dry-run | `.sql`; sorgu çalıştırılmaz |
| Databricks Lakehouse | Profil notebook’u üret → SOURCE import → get-status → export | Workspace notebook + Python kaynak dosyası; otomatik çalıştırılmaz |
| Azure Data Factory | Yeni isimle pipeline yayımlama; ayrıca mevcut pipeline çalıştırma/takip | Native pipeline veya pipeline run |
| Airflow 3.2 | Public API v2 ile mevcut DAG tetikleme ve takip | DAG run; başarısızlıkta başarı çıktısı yok |
| dbt Cloud | Mevcut dönüşüm/test job’ını başlat, takip et ve artifact indir | dbt run + platformun ürettiği `run_results.json` |
| Prefect 3 Cloud | Deployment’tan parametreli flow run oluştur ve takip et | Platformda flow run + yalnızca kimlik/durum içeren makbuz |
| Snowflake | OAuth SQL API ile yeni profil view’ı oluştur, sorgula ve aggregate sayımları doğrula | Kalıcı view + `quality-view.sql` + sorgu kimliği/sayımlar; kalite kararı verilmez |
| GitHub Actions | Mevcut workflow_dispatch işini başlat, kesin run kimliğiyle takip et | Platform workflow run + sonuç kaydı; artifact indirme içermez |
| Fivetran | Manual bağlantıyı force=false ile sync et, yeni başarı zamanını izle | Platform sync + zaman damgası kaydı; kesin run ID yok, eşzamanlı tetikleme desteklenmez |

**Canlı vendor/tenant testi: 0. Uygulamada dosya açılışı testi: 0.** Testler yerel HTTP sözleşme sunucusunu kullanır; gerçek QVF/PBIX üretildiğini kanıtlamaz. Agent model yanıtı testte taklit edilir. Bu paketler henüz üretim onaylı değildir.

SAP/Oracle keşif paketleri rapor otomasyonu olarak etiketlenmez. SAP satış teklifi taslağı ayrı bir yazma işlemidir; kesin belge oluşturmaz. Qlik şablon akışı mevcut grafik ve load script'ini kullanır; sıfırdan dashboard tasarlamaz. Logo entegrasyonu henüz uygulanmadı; doğrulanmamış endpoint üretilmez.

## Yerelde çalıştırma

Python 3.10+ ve Node.js 22+:

```sh
npm run build
npm test
npm start
```

Sunucu varsayılan olarak `http://localhost:4322` üzerinde çalışır. Uygun değilse boş bir porta ilerler. Kurumsal palet sabitlendi; geçici palet seçicisi kaldırıldı.

## Paketi kullanma

1. ZIP'i çıkarın; README'deki desteklenen ürün, izin ve platform etkilerini okuyun.
2. `.env.example` → `.env`; ilgili platformun kendi token/erişim bilgilerini girin. OAuth token edinme ve yenileme sizin kimlik sağlayıcınızda yapılır.
3. `config.json` içindeki kaynak/hedef kimliklerini, agent için `context.md`, SQL işi için `query.sql` dosyasını düzenleyin.
4. `python native.py --plan` ağsız önizleme yapar.
5. `python native.py --execute --output output/run-001` gerçek platform çağrılarını yapar.

Yeni/boş çıktı klasörü gerekir. Yazma ve LLM maliyeti olabilen işlemler README'de belirtilir. Şema ve proje bağlamı seçilen modele gönderilir; agent satır verisi çekmez. Kısmi çalışma kimlikleri `run-state.json`, dosya özeti `manifest.json` içindedir. Otomatik rollback veya üretimde otomatik model-kodu yürütme yoktur.

Toplam **94 test** geçti (88 Python + 6 görünüm testi). Test sayısı canlı tenant doğrulaması değildir.

## Test katmanları

- Eski yerel motorların regresyon testleri (bu motorlar artık ürün paketleri olarak dağıtılmaz).
- Platformlara özgü gerçek localhost HTTP çağrıları: path, payload, auth headers, polling, hata ve çıktı türü.
- Eksik erişim bilgisi, redirect/origin, GET retry/POST tekrar etmeme, bütçe sınırı, başarısız reload, mevcut çıktıyı koruma.
- Her ZIP'te bütünlük/SHA-256; tüm paketlerin ağsız `--plan` çalışması; planlanan görevlerde indirme olmaması.
- Açık/koyu görünüm testleri.

Canlı uçtan uca test ayrı bir aşamadır: lisanslı sandbox, ilgili sürüm, geçerli erişim ve artifact'ı hedef uygulamada açıp inceleme gerekir. Bunlar yerel sözleşme testinin yerine konmaz.

## Yapı

- `catalog/catalog.py`: tüm alan ve ürün taksonomisi; önceki CSV taslakları tarihsel yardımcı olarak durur.
- `catalog/native_catalog.py`: uygulanmış işlemler, erişim, etkiler, resmi kaynaklar; diğer ürünlerin açık plan durumu.
- `library/native.py`: API transport, platform akışları ve çıktı denetimi.
- `library/agent.py`: model sağlayıcısı; yeni akışta yalnızca model çağrısı için kullanılır.
- `scripts/build.py`: sadece uygulanmış native paketleri üretir; eski üretilmiş ZIP'leri kaldırır.
- `dist/integration-plan.json`: 185 ürünün makine tarafından okunabilir entegrasyon planı.
- `docs/INTEGRATION-MATRIX.md`: tüm ürünler ve kalan kapsam.
- `docs/NATIVE-ROADMAP.md`: tamamlanma ölçütleri ve sonraki geliştirme işleri.

[GitHub](https://github.com/muratceme/data-consulting-workbench) · MIT.
