-- Günlük ERP aktarımı: ham tablolardan staging katmanına
TRUNCATE TABLE stg.siparis;

INSERT INTO stg.siparis (siparis_id, musteri_id, tarih, tutar)
SELECT s.id, s.musteri_id, s.tarih, s.tutar
FROM erp.dbo.siparisler s
WHERE s.tarih >= DATEADD(day, -7, GETDATE());

INSERT INTO stg.musteri
SELECT * FROM erp.dbo.musteriler;  -- tüm sütunlar
