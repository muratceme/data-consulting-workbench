CREATE TABLE musteri (
    musteri_id INTEGER PRIMARY KEY,
    eposta TEXT NOT NULL,
    il TEXT,
    segment TEXT,
    kayit_tarihi TEXT
);
CREATE TABLE siparis (
    siparis_id INTEGER PRIMARY KEY,
    musteri_id INTEGER NOT NULL REFERENCES musteri (musteri_id),
    siparis_tarihi TEXT NOT NULL,
    durum TEXT NOT NULL,
    tutar NUMERIC
);
CREATE TABLE siparis_kalem (
    siparis_id INTEGER NOT NULL,
    satir_no INTEGER NOT NULL,
    urun_id INTEGER NOT NULL,
    adet INTEGER,
    PRIMARY KEY (siparis_id, satir_no)
);
CREATE INDEX ix_siparis_durum ON siparis (durum);
