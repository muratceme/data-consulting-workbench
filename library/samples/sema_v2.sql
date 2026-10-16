-- Üretim ortamı, sürüm 2
CREATE TABLE dbo.Musteriler (
    MusteriNo VARCHAR(10) NOT NULL PRIMARY KEY,
    MusteriAdi NVARCHAR(100),
    Sehir NVARCHAR(50),
    TelefonNo VARCHAR(20),
    KayitTarihi DATE NOT NULL,
    Segment NVARCHAR(20) NOT NULL
);

CREATE TABLE dbo.Siparisler (
    SiparisNo VARCHAR(12) NOT NULL,
    MusteriNo VARCHAR(10) NOT NULL,
    SiparisTarihi DATETIME2 NOT NULL,
    Tutar DECIMAL(12,2),
    Durum NVARCHAR(30),
    CONSTRAINT PK_Siparisler PRIMARY KEY (SiparisNo)
);

CREATE TABLE dbo.Iadeler (
    IadeNo INT NOT NULL,
    SiparisNo VARCHAR(12) NOT NULL,
    IadeTarihi DATE,
    Tutar DECIMAL(18,2)
);
