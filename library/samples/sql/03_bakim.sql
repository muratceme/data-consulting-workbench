-- Elle çalıştırılan bakım betiği
UPDATE mart.musteri_ozet SET aktif = 0;
DELETE FROM stg.siparis WHERE tarih < '2020-01-01';
SELECT COUNT(*) FROM mart.v_satis_ozet;
