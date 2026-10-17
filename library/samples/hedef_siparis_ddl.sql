-- Hedef: PostgreSQL / SQL Server ortak tipler
CREATE TABLE satis.siparis (
    siparis_id INT NOT NULL,
    adet SMALLINT,
    tutar DECIMAL(8, 2),
    tarih DATE,
    aktif BIT,
    kod VARCHAR(5),
    PRIMARY KEY (siparis_id)
);
