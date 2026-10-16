-- Geliştirme ortamı, sürüm 1
CREATE TABLE dbo.Musteriler (
    MusteriNo VARCHAR(10) NOT NULL PRIMARY KEY,
    MusteriAdi NVARCHAR(200),
    Sehir NVARCHAR(50),
    Telefon VARCHAR(20),
    KayitTarihi DATE
);

CREATE TABLE dbo.Siparisler (
    SiparisNo VARCHAR(12) NOT NULL,
    MusteriNo VARCHAR(10) NOT NULL,
    SiparisTarihi DATE NOT NULL,
    Tutar DECIMAL(18,2),
    Durum NVARCHAR(20),
    Aciklama NVARCHAR(MAX),
    CONSTRAINT PK_Siparisler PRIMARY KEY (SiparisNo)
);

CREATE TABLE dbo.EskiKampanyalar (
    KampanyaNo INT NOT NULL,
    Ad NVARCHAR(100)
);
