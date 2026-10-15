CREATE TABLE dbo.Musteriler (
    MusteriNo VARCHAR(10) NOT NULL PRIMARY KEY,
    MusteriAdi NVARCHAR(200),
    Sehir NVARCHAR(50),
    Segment NVARCHAR(20),
    KayitTarihi DATE
);

CREATE TABLE dbo.Siparisler (
    SiparisNo VARCHAR(12) NOT NULL,
    MusteriNo VARCHAR(10) NOT NULL,
    SiparisTarihi DATE NOT NULL,
    Durum NVARCHAR(20),
    CONSTRAINT PK_Siparisler PRIMARY KEY (SiparisNo)
);

CREATE TABLE dbo.SiparisKalemleri (
    SiparisNo VARCHAR(12) NOT NULL,
    SatirNo INT NOT NULL,
    UrunKodu VARCHAR(10) NOT NULL,
    Adet INT,
    BirimFiyat DECIMAL(18,2),
    IndirimOrani DECIMAL(5,4) DEFAULT 0
);

CREATE TABLE dbo.Urunler (
    UrunKodu VARCHAR(10) NOT NULL,
    UrunAdi NVARCHAR(200),
    Kategori NVARCHAR(50)
);
