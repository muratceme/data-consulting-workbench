-- Eski raporlama sorgusu (kurgusal): segment bazında sipariş listesi
CREATE VIEW rapor.v_segment_ciro AS
SELECT s.siparis_id,
       s.musteri_id,
       m.segment,
       s.tarih,
       s.tutar
FROM erp.siparis s
JOIN crm.musteri m ON m.musteri_id = s.musteri_id
LEFT JOIN ref.kampanya k ON k.kampanya_id = s.kampanya_id
WHERE s.durum <> 'IPTAL';
