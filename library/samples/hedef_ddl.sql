CREATE TABLE public.musteriler (
    musteri_no VARCHAR(10) NOT NULL PRIMARY KEY,
    musteri_adi VARCHAR(100),
    sehir VARCHAR(30),
    kayit_tarihi DATE
);

CREATE TABLE public.siparisler (
    siparis_no VARCHAR(12) NOT NULL PRIMARY KEY,
    musteri_no VARCHAR(10) NOT NULL,
    siparis_tarihi TIMESTAMP NOT NULL,
    durum VARCHAR(20)
);
