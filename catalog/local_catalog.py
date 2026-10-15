"""Local packages: code blocks that run fully offline on exported files, and AI agents that add one model step.
Each entry is built into a ZIP with main.py, its helper modules, fictional sample data (ornek/) and a README.
Every entry has an end-to-end test on its sample data (tests/test_local.py)."""

BLOCK_NOTE = 'İnternete bağlanmaz; ağ ve kabuk komutu kullanmaz. Girdi dosyaları değiştirilmez. Python 3.10+, ek kütüphane gerekmez.'
AGENT_NOTE = ('Ayrıştırma, profil ve denetim yerelde kodla yapılır; model yalnızca muhakeme adımına girer. Kişisel veriler (e-posta, telefon, '
              'TCKN, IBAN, kart no) gönderilmeden önce maskelenir; gönderilecek metin output/gonderilecek-istek.json dosyasına yazılır ve '
              'onayın istenir. --dry-run modeli hiç çağırmaz. OpenAI, Anthropic veya bilgisayarındaki Ollama ile çalışır. Python 3.10+, ek kütüphane gerekmez.')

LOCAL = [
    dict(id='veri-profili', kind='code', domain='files', product='CSV / Excel', title='CSV/Excel dosyasının veri profilini çıkar',
         main='blocks/veri_profili.py', modules=['common', 'tabular'], samples={'satislar.csv': 'satislar.csv'},
         description='Her sütunun tipini, boşluk oranını, farklı değer sayısını, aralığını ve en sık değerlerini çıkarır. Mükerrer satırları, aday anahtarları, karışık tarih biçimlerini ve kişisel veri izlerini (e-posta, TCKN, IBAN, telefon) işaretler.',
         input='CSV (virgül, noktalı virgül, sekme) veya XLSX. Türkçe sayı biçimi (1.234,56) tanınır.',
         output='profil.md (okunur rapor) + profile.json (agent’lar için)', command='python main.py --input veri.xlsx'),
    dict(id='kalite-kontrol', kind='code', domain='files', product='CSV / Excel', title='Veri kalitesi kurallarını dosyada çalıştır',
         main='blocks/kalite_kontrol.py', modules=['common', 'tabular'], samples={'satislar.csv': 'satislar.csv', 'rules.json': 'rules.json'},
         description='rules.json içindeki kuralları (boş olamaz, benzersiz, aralık, izinli değer, regex, tarih, birlikte benzersiz…) uygular. Bozulan her satırı Excel satır numarasıyla listeler. --strict ile zamanlanmış görevde hata kodu döner.',
         input='CSV/XLSX + rules.json (13 kontrol türü; örnek dosya pakette)',
         output='kalite-raporu.md + ihlaller.csv + sonuc.json', command='python main.py --input veri.csv --rules rules.json --strict'),
    dict(id='tablo-mutabakati', kind='code', domain='files', product='CSV / Excel', title='Kaynak ve hedef tabloyu satır satır mutabık et',
         main='blocks/tablo_mutabakati.py', modules=['common', 'tabular'], samples={'kaynak_bakiye.csv': 'kaynak_bakiye.csv', 'hedef_bakiye.csv': 'hedef_bakiye.csv'},
         description='İki dışa aktarımı anahtar sütun(lar)la eşleştirir: yalnız kaynakta / yalnız hedefte olan satırları, değeri farklı alanları ve sayısal sütun toplamlarını raporlar. Migrasyon, ETL ve ERP→ambar aktarımı doğrulaması için.',
         input='İki CSV/XLSX dosyası + anahtar sütun(lar); isteğe bağlı sayısal tolerans',
         output='mutabakat.md + farklar.csv + sadece-kaynakta.csv + sadece-hedefte.csv', command='python main.py --source erp.csv --target dwh.csv --keys hesap_kodu --tolerance 0.01'),
    dict(id='sql-soyagaci', kind='code', domain='files', product='SQL dosyaları', title='SQL dosyalarından tablo soy ağacı çıkar',
         main='blocks/sql_soyagaci.py', modules=['common', 'tabular'], samples={'sql/01_staging.sql': 'sql/01_staging.sql', 'sql/02_mart.sql': 'sql/02_mart.sql', 'sql/03_bakim.sql': 'sql/03_bakim.sql'},
         description='Klasördeki .sql dosyalarında hangi ifadenin hangi tabloları okuyup hangisine yazdığını çıkarır; ham kaynakları, ara tabloları ve son hedefleri ayırır. WHERE’siz UPDATE/DELETE, TRUNCATE ve SELECT * kullanımını işaretler; Mermaid akış şeması üretir.',
         input='.sql dosyası veya klasörü (alt klasörler dahil); CTE’ler ayıklanır',
         output='sql-soyagaci.md + lineage.csv + lineage.json', command='python main.py --input C:\\proje\\sql'),
    dict(id='pbit-model', kind='code', domain='bi', product='Power BI Desktop', title='Power BI modelini belgele ve risklerini bul',
         main='blocks/pbit_model.py', modules=['common', 'tabular'], samples={'satis-modeli.pbit': 'satis-modeli.pbit'},
         description='.pbit şablonundan tabloları, sütunları, DAX ölçülerini, ilişkileri, Power Query kaynaklarını ve rapor sayfalarını çıkarır. Çift yönlü filtreleri, otomatik tarih tablolarını, ilişkisiz tabloları, modelde olmayan ölçü başvurularını ve tüm tabloyu tarayan FILTER kullanımını işaretler.',
         input='.pbit (Dosya > Dışarı aktar > Power BI şablonu), model.bim veya DataModelSchema. .pbix modeli sıkıştırılmış olduğundan okunmaz.',
         output='veri-modeli.md + olculer.csv + sutunlar.csv + iliskiler.csv + model.json', command='python main.py --input rapor.pbit'),
    dict(id='tableau-envanter', kind='code', domain='bi', product='Tableau Desktop', title='Tableau çalışma kitabının envanterini çıkar',
         main='blocks/tableau_envanter.py', modules=['common', 'tabular'], samples={'satis-panosu.twbx': 'satis-panosu.twbx'},
         description='Veri kaynaklarını, bağlantıları, hesaplanan alanları (formüller okunur adlarla), parametreleri, sayfaları ve panoları listeler. Kullanılmayan alanları, yerel dosya bağlantılarını, LOD ve tablo hesaplamalarını işaretler. Migrasyon ve devir teslim için.',
         input='.twb veya .twbx (paketli çalışma kitabı)', output='tableau-envanter.md + hesaplanan-alanlar.csv + envanter.json', command='python main.py --input pano.twbx'),
    dict(id='qlik-soyagaci', kind='code', domain='bi', product='Qlik Sense Enterprise', title='Qlik load script soy ağacını çıkar',
         main='blocks/qlik_soyagaci.py', modules=['common'], samples={'satis.qvs': 'satis.qvs'},
         description='Load script’teki bağlantıları, değişkenleri, tabloları ve kaynaklarını (dosya, SQL, RESIDENT, INLINE), JOIN/CONCATENATE hedeflerini, STORE çıktılarını ve DROP adımlarını çıkarır. Etiketsiz tabloları, lib:// dışı yolları ve henüz yüklenmemiş tabloya RESIDENT okumayı işaretler.',
         input='.qvs dosyası veya veri yükleme düzenleyicisinden kopyalanan script (.txt)', output='qlik-soyagaci.md (Mermaid şeması) + lineage.json', command='python main.py --input uygulama.qvs'),
    dict(id='dbt-denetim', kind='code', domain='transform', product='dbt Core', title='dbt projesinin test ve dokümantasyon denetimi',
         main='blocks/dbt_denetim.py', modules=['common', 'tabular'], samples={'manifest.json': 'manifest.json'},
         description='manifest.json’dan model açıklamalarını, belgelenmiş sütunları, test kapsamını ve anahtar testlerini, katman kurallarını (stg/int/fct/dim), sabit tablo adlarını, freshness’i tanımsız ve kullanılmayan kaynakları raporlar.',
         input='target/manifest.json (dbt parse) ve isteğe bağlı catalog.json (dbt docs generate)', output='dbt-denetim.md + modeller.csv + audit.json', command='python main.py --manifest target/manifest.json --catalog target/catalog.json'),

    dict(id='sql-yazari', kind='agent', domain='files', product='SQL dosyaları', title='Şemadan SQL sorgusu yazan agent',
         main='agents/sql_yazari.py', modules=['common', 'tabular', 'llm', 'sql_soyagaci'], samples={'sema.sql': 'sema.sql', 'istek.md': 'istek.md'},
         description='Tablo şemanı (CREATE TABLE, profile.json veya JSON) ve iş isteğini okur; seçtiğin lehçede (T-SQL, BigQuery, Snowflake, PostgreSQL…) tek bir SELECT yazar. Sonuç yerelde denetlenir: yazma komutu, ikinci ifade, şemada olmayan tablo veya sütun varsa model düzeltmeye zorlanır.',
         input='Şema dosyası + istek metni; veri satırı gönderilmez', output='sorgu.sql + sorgu-aciklama.md + dogrulama.json', command='python main.py --schema sema.sql --request istek.md --dialect bigquery'),
    dict(id='dax-yazari', kind='agent', domain='bi', product='Power BI Desktop', title='Power BI modeline DAX ölçüsü yazan agent',
         main='agents/dax_yazari.py', modules=['common', 'tabular', 'llm', 'pbit_model'], samples={'satis-modeli.pbit': 'satis-modeli.pbit', 'dax-istek.md': 'dax-istek.md'},
         description='.pbit modelinin tablo, sütun, ilişki ve mevcut ölçülerine bakarak istenen ölçüleri yazar. Her Tablo[Sütun] ve [Ölçü] başvurusu, parantez dengesi ve ad çakışması yerelde denetlenir. Çıktı DAX sorgu görünümüne yapıştırılıp “Modeli güncelle” ile eklenir.',
         input='.pbit / model.bim + istek metni; veri satırı gönderilmez', output='olculer.dax + olculer.md', command='python main.py --model-file rapor.pbit --request istek.md'),
    dict(id='veri-sozlugu', kind='agent', domain='files', product='CSV / Excel', title='Tablo için veri sözlüğü yazan agent',
         main='agents/veri_sozlugu.py', modules=['common', 'tabular', 'llm', 'veri_profili'], samples={'satislar.csv': 'satislar.csv', 'baglam.md': 'baglam.md'},
         description='Dosyanın profilini yerelde çıkarır, modele yalnızca sütun istatistiklerini gönderir; her sütun için iş adı, açıklama, hassasiyet sınıfı ve kural önerisi yazar, veri sahibine soruları listeler. Kişisel veri izi bulunan sütunlar kodla “kişisel” işaretlenir.',
         input='CSV/XLSX + isteğe bağlı iş bağlamı; satırlar gönderilmez (--no-values ile sık değerler de)', output='veri-sozlugu.md + veri-sozlugu.csv', command='python main.py --input musteri.xlsx --context baglam.md'),
    dict(id='kalite-kurali', kind='agent', domain='files', product='CSV / Excel', title='Veri kalitesi kurallarını öneren agent',
         main='agents/kalite_kurali.py', modules=['common', 'tabular', 'llm', 'veri_profili', 'veri_sozlugu', 'kalite_kontrol'], samples={'satislar.csv': 'satislar.csv', 'baglam.md': 'baglam.md'},
         description='Profil ve iş bağlamından kalite kuralları önerir, kuralları kalite-kontrol bloğunun şemasına göre denetler ve hemen dosyada çalıştırır. Üretilen rules.json, kalite-kontrol kod bloğuyla her gün tekrar kullanılabilir.',
         input='CSV/XLSX + isteğe bağlı iş bağlamı; satırlar gönderilmez', output='rules.json + gerekce.md + kalite-raporu.md + ihlaller.csv', command='python main.py --input siparis.csv --context baglam.md'),
    dict(id='qlik-powerbi', kind='agent', domain='bi', product='Qlik Sense Enterprise', title='Qlik script’ini Power BI’a taşıyan agent',
         main='agents/qlik_powerbi.py', modules=['common', 'llm', 'qlik_soyagaci'], samples={'satis.qvs': 'satis.qvs'},
         description='Script’in soy ağacını yerelde çıkarır; uygulamada kalan her tablo için Power Query (M) sorgusu, ApplyMap/JOIN/CONCATENATE karşılıkları, model önerisi ve riskleri yazar. Her son tablonun karşılandığı ve her M sorgusunun let … in yapısında olduğu yerelde denetlenir.',
         input='.qvs script + isteğe bağlı grafik ifadeleri', output='gecis-plani.md + power-query/*.m', command='python main.py --script uygulama.qvs'),
    dict(id='kpi-tanimlayici', kind='agent', domain='bi', product='Rapor gereksinimleri', title='Toplantı notlarından KPI kataloğu çıkaran agent',
         main='agents/kpi_tanimlayici.py', modules=['common', 'tabular', 'llm'], samples={'toplanti-notlari.md': 'toplanti-notlari.md', 'satislar.csv': 'satislar.csv'},
         description='Rapor ihtiyaç notlarından her KPI için tanım, formül, birim, periyot, kırılım, kaynak, sahip ve açık soruları çıkarır. Veri dosyası verilirse formüllerde dosyada olmayan alanlar yerelde işaretlenir. Notlardaki kişisel veriler maskelenir.',
         input='Toplantı notu / istek metni + isteğe bağlı CSV/XLSX (yalnızca sütun adları)', output='kpi-katalogu.md + kpi-katalogu.csv', command='python main.py --notes notlar.md --data satis.csv'),
]

for item in LOCAL:
    item['requirements'] = AGENT_NOTE if item['kind'] == 'agent' else BLOCK_NOTE
    item['effects'] = ('Yalnızca output klasörüne yazar. Seçtiğin model sağlayıcısına maskelenmiş özet gönderilir; Ollama ile hiçbir veri bilgisayardan çıkmaz.'
                       if item['kind'] == 'agent' else 'Yalnızca output klasörüne yazar; hiçbir sisteme bağlanmaz.')
    item['secrets'] = ['PROVIDER', 'MODEL', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OLLAMA_URL'] if item['kind'] == 'agent' else []
