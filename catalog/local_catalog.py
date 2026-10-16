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
         main='agents/sql_yazari.py', modules=['common', 'tabular', 'llm', 'sql_soyagaci', 'sema_farki'], samples={'sema.sql': 'sema.sql', 'istek.md': 'istek.md'},
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

    # Added in the second batch (appended so existing task IDs stay stable).
    dict(id='kvkk-maskeleme', kind='code', domain='files', product='CSV / Excel', title='Dosyadaki kişisel verileri maskele (KVKK)',
         main='blocks/kvkk_maskeleme.py', modules=['common', 'tabular'], samples={'musteriler.csv': 'musteriler.csv'},
         description='E-posta, TCKN (sağlama haneli), IBAN, telefon, kart no, ad soyad, adres ve doğum tarihi sütunlarını bulur; takma ad (tuzlu HMAC, birleştirmeler bozulmaz), kısmi maskeleme veya silme ile paylaşılabilir kopya üretir. Not alanlarına gömülü kişisel verileri de maskeler ve sonucu yeniden tarar.',
         input='CSV/XLSX; isteğe bağlı --columns / --keep / --salt', output='maskeli.csv + maskeleme-raporu.md + ozet.json',
         command='python main.py --input musteri.xlsx --method takma --salt <gizli-tuz>'),
    dict(id='sema-farki', kind='code', domain='files', product='SQL dosyaları', title='İki veritabanı şemasını karşılaştır',
         main='blocks/sema_farki.py', modules=['common', 'tabular'], samples={'sema_v1.sql': 'sema_v1.sql', 'sema_v2.sql': 'sema_v2.sql'},
         description='İki DDL dosyasını (dev ↔ prod, sürüm 1 ↔ 2) karşılaştırır: eklenen/silinen tablo ve sütunları, tip ve NULL değişikliklerini, olası yeniden adlandırmaları bulur. Veri kaybı riski olan daralmaları işaretler ve gözden geçirilecek bir ALTER taslağı yazar; --strict ile CI’da riskli farkta durur.',
         input='İki .sql dosyası veya klasörü (CREATE TABLE)', output='sema-farki.md + farklar.csv + alter-taslak.sql + fark.json',
         command='python main.py --old dev.sql --new prod.sql --strict'),
    dict(id='tarih-tablosu', kind='code', domain='bi', product='Power BI Desktop', title='Türkçe tarih tablosu (takvim boyutu) üret',
         main='blocks/tarih_tablosu.py', modules=['common', 'tabular'], samples={'tatiller.csv': 'tatiller.csv'},
         description='Türkçe ay/gün adları, ISO hafta, çeyrek, mali yıl, hafta sonu, resmî tatil, yarım gün ve aylık iş günü sırası içeren tarih boyutu üretir. Sabit ulusal tatiller kodda; dinî bayramlar düzenlenebilir tatil dosyasından gelir. Power BI, Qlik, Tableau ve veri ambarı için CSV ve SQL çıktısı verir.',
         input='Başlangıç/bitiş tarihi, mali yıl başlangıç ayı + isteğe bağlı tatil dosyası', output='tarih-tablosu.csv + tarih-tablosu.sql + tarih-ozeti.md',
         command='python main.py --start 2024-01-01 --end 2027-12-31 --fiscal-start 4'),
    dict(id='dosya-birlestir', kind='code', domain='files', product='CSV / Excel', title='Klasördeki Excel/CSV dosyalarını birleştir',
         main='blocks/dosya_birlestir.py', modules=['common', 'tabular'],
         samples={'aylik/2026-07.csv': 'aylik/2026-07.csv', 'aylik/2026-08.csv': 'aylik/2026-08.csv', 'aylik/2026-09.csv': 'aylik/2026-09.csv'},
         description='Aylık veya şube bazlı dışa aktarımları tek tabloda toplar. “Sipariş Tarihi”, “siparis_tarihi” ve “SIPARIS TARIHI” gibi yazımları aynı sütun sayar, her satıra kaynak dosyayı ekler; eksik/fazla sütunları, boş dosyaları ve mükerrer satırları raporlar.',
         input='CSV/XLSX dosyaları olan klasör (alt klasörler dahil); isteğe bağlı --pattern', output='birlesik.csv + birlestirme-raporu.md + ozet.json',
         command='python main.py --folder C:\\raporlar\\2026 --pattern "satis_*.xlsx"'),
    dict(id='sql-aciklayici', kind='agent', domain='files', product='SQL dosyaları', title='Devralınan SQL’i iş diliyle belgeleyen agent',
         main='agents/sql_aciklayici.py', modules=['common', 'tabular', 'llm', 'sql_soyagaci'],
         samples={'sql/01_staging.sql': 'sql/01_staging.sql', 'sql/02_mart.sql': 'sql/02_mart.sql', 'sql/03_bakim.sql': 'sql/03_bakim.sql'},
         description='Her ifadenin hedefini, kaynaklarını ve risklerini kodla çıkarır; model amacı, iş kurallarını ve dikkat noktalarını yazar. Model yanıtında SQL’de olmayan bir tablo adı geçerse ya da bir ifade atlanırsa yanıt reddedilir. Hiçbir sorgu çalıştırılmaz.',
         input='.sql dosyası veya klasörü + isteğe bağlı iş bağlamı', output='sql-aciklama.md (Mermaid akışıyla) + aciklama.json + lineage.json',
         command='python main.py --input C:\\proje\\sql --context baglam.md'),
    dict(id='donem-yorumu', kind='agent', domain='bi', product='Rapor gereksinimleri', title='Sayıları doğrulanmış dönem yorumu yazan agent',
         main='agents/donem_yorumu.py', modules=['common', 'tabular', 'llm'], samples={'gunluk_satis.csv': 'gunluk_satis.csv'},
         description='Son dönemi (ay, çeyrek, hafta) öncekiyle kodla karşılaştırır; toplam değişimi ve değişimi en çok sürükleyen kırılımları numaralı olgulara çevirir. Model yalnızca bu olgulardan yönetim özeti yazar; metindeki her sayı olgularla yerelde eşleştirilir, olmayan bir sayı yanıtı reddettirir.',
         input='CSV/XLSX + tarih, ölçü ve kırılım sütunları; satırlar gönderilmez, yalnızca toplamlar', output='donem-yorumu.md + olgular.csv + olgular.json',
         command='python main.py --input satis.xlsx --date tarih --measure ciro --dims bolge,kanal --period ay'),
]

# Single-purpose blocks registered with islem.op in category modules (library/blocks/*.py).
import importlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'library' / 'blocks'))
import islem
CATEGORY_MODULES = ['temizlik', 'dogrulama', 'finans', 'anaveri', 'analiz', 'musteri', 'sqlgen', 'bicim', 'bigen', 'zaman', 'planlama', 'orkestrasyon', 'entegrasyon', 'lakehouse', 'veritabani', 'maliyet', 'gecis', 'ml', 'rag', 'akis']
for _name in CATEGORY_MODULES:
    importlib.import_module(_name)
for _spec in islem.OPS.values():
    _deps = getattr(sys.modules[_spec['module']], 'REQUIRES', []) + _spec['requires']
    LOCAL.append(dict(id=_spec['id'], kind='code', domain=_spec['domain'], product=_spec['product'], title=_spec['title'], main=None, op=_spec['id'],
                      spec=_spec, tag=_spec['tag'], modules=list(dict.fromkeys(['common', 'tabular', 'islem', *_deps, _spec['module']])), samples=_spec['samples'],
                      description=_spec['description'], input=_spec['input'], output=_spec['output'], command=_spec['command']))

for item in LOCAL:
    item['requirements'] = AGENT_NOTE if item['kind'] == 'agent' else BLOCK_NOTE
    item['effects'] = ('Yalnızca output klasörüne yazar. Seçtiğin model sağlayıcısına maskelenmiş özet gönderilir; Ollama ile hiçbir veri bilgisayardan çıkmaz.'
                       if item['kind'] == 'agent' else 'Yalnızca output klasörüne yazar; hiçbir sisteme bağlanmaz.')
    item['secrets'] = ['PROVIDER', 'MODEL', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OLLAMA_URL'] if item['kind'] == 'agent' else []
