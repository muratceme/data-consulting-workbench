-- Yavaş rapor sorgusu (kurgusal); planı: explain.json
SELECT s.siparis_id, s.tarih, s.tutar, m.ad, m.sehir
FROM siparisler s
JOIN musteriler m ON m.musteri_id = s.musteri_id
WHERE s.durum = 'Teslim'
ORDER BY s.tutar DESC;
