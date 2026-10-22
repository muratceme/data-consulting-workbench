-- Eski İK/satış şeması (hedefe yüklenecek)
CREATE TABLE ulke (ulke_kodu CHAR(2) PRIMARY KEY, ad VARCHAR(60));
CREATE TABLE sehir (sehir_id INT PRIMARY KEY, ulke_kodu CHAR(2) REFERENCES ulke(ulke_kodu), ad VARCHAR(60));
CREATE TABLE departman (departman_id INT PRIMARY KEY, ad VARCHAR(60), mudur_id INT, sehir_id INT REFERENCES sehir(sehir_id));
CREATE TABLE calisan (
  calisan_id INT PRIMARY KEY,
  ad VARCHAR(80),
  departman_id INT REFERENCES departman(departman_id),
  yonetici_id INT REFERENCES calisan(calisan_id)
);
ALTER TABLE departman ADD CONSTRAINT fk_departman_mudur FOREIGN KEY (mudur_id) REFERENCES calisan(calisan_id);
CREATE TABLE musteri (musteri_id INT PRIMARY KEY, ad VARCHAR(80), sehir_id INT REFERENCES sehir(sehir_id), temsilci_id INT REFERENCES calisan(calisan_id));
CREATE TABLE urun (urun_id INT PRIMARY KEY, ad VARCHAR(80), fiyat NUMERIC(12,2));
CREATE TABLE siparis (siparis_id INT PRIMARY KEY, musteri_id INT NOT NULL REFERENCES musteri(musteri_id), tarih DATE);
CREATE TABLE siparis_kalem (
  siparis_id INT REFERENCES siparis(siparis_id),
  satir_no INT,
  urun_id INT REFERENCES urun(urun_id),
  adet INT,
  PRIMARY KEY (siparis_id, satir_no)
);
CREATE TABLE fatura (fatura_id INT PRIMARY KEY, siparis_id INT REFERENCES siparis(siparis_id), tutar NUMERIC(14,2));
