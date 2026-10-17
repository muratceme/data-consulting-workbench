-- Hedef: yeni veri ambarı (kurgusal)
CREATE TABLE crm.musteri (
    musteri_id VARCHAR(10) NOT NULL PRIMARY KEY,
    ad_soyad VARCHAR(150),
    sehir VARCHAR(50),
    telefon VARCHAR(20),
    kayit_tarihi DATE NOT NULL,
    kaynak_sistem VARCHAR(10) NOT NULL
);

CREATE TABLE satis.siparis (
    siparis_id VARCHAR(12) NOT NULL PRIMARY KEY,
    musteri_id VARCHAR(10) NOT NULL,
    siparis_tarihi DATE NOT NULL,
    tutar DECIMAL(12,2),
    durum VARCHAR(20),
    yukleme_zamani TIMESTAMP NOT NULL
);
