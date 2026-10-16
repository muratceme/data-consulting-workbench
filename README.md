# Datawork — uygulamaya özgü veri otomasyonları

Veri işleri için iki tür paket: **kod blokları** (aynı girdiden hep aynı sonuç; platform API çalıştırıcıları ve internete çıkmayan yerel araçlar) ve **AI agent’lar** (muhakeme adımı modelde, ayrıştırma ve denetim kodda). Kullanıcı sitede türü seçer. Site yalnızca keşif ve indirme arayüzüdür; anahtar veya veri toplamaz.

## Güncel durum

16 Ekim 2026: 23 alan / 189 ürün bağlamı. **57 indirilebilir paket**: 37 platform API paketi (36 kod bloğu + BigQuery agent) ve 20 yerel paket (12 kod bloğu + 8 agent). Toplam 48 kod bloğu, 9 agent. Diğer 372 görev **planlanan entegrasyon**; indirme bağlantıları yok.

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

### Yerel AI agent’lar (Ollama, OpenAI veya Anthropic)

SQL yazarı, DAX ölçü yazarı, veri sözlüğü, kalite kuralı önerici, Qlik → Power BI geçişi, KPI tanımlayıcı, SQL açıklayıcı (yanıtta SQL’de olmayan tablo adı reddedilir) ve dönem yorumu (toplamlar kodla hesaplanır; metindeki her sayı bu olgularla eşleştirilir). Ortak kurallar (`library/agents/llm.py`): kişisel veri maskelenir, giden metin `output/gonderilecek-istek.json` dosyasına yazılır ve onay istenir (`--yes`), `--dry-run` modeli çağırmaz, model yanıtı JSON olarak yerelde denetlenir ve kurala uymazsa bir kez düzeltme istenir. Model üretimi hiçbir sistemde otomatik çalıştırılmaz.

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

SAP/Oracle keşif paketleri rapor otomasyonu olarak etiketlenmez. SAP satış teklifi taslağı ayrı bir yazma işlemidir; kesin belge oluşturmaz. Qlik şablon akışı mevcut grafik ve load script'ini kullanır; sıfırdan dashboard tasarlamaz. Logo entegrasyonu henüz uygulanmadı; doğrulanmamış endpoint üretilmez.

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

Toplam **197 test** geçiyor. Yerel paket testleri (`tests/test_local.py`) paketlenmiş kopyayı örnek veriyle çalıştırır; agent’lar OpenAI/Anthropic/Ollama biçiminde konuşan sahte model sunucusuyla test edilir (maskeleme, onay, dry-run, düzeltme denemesi, HTTP hatası). Agent’lar ayrıca yerel bir Ollama modeliyle (qwen3 14B) elle denendi. Test sayısı canlı tenant doğrulaması değildir.

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
- `catalog/local_catalog.py`: yerel kod blokları ve agent’ların tanımı.
- `library/blocks/`, `library/agents/`, `library/samples/`: yerel paket kodu ve kurgusal örnek veriler (`scripts/make_samples.py` ikili örnekleri üretir).
- `scripts/build.py` + `scripts/local_package.py`: native ve yerel paketleri üretir; eski üretilmiş ZIP'leri kaldırır.
- `dist/integration-plan.json`: 189 ürünün makine tarafından okunabilir entegrasyon planı.
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
