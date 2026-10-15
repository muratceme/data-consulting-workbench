/* Satış olgu tablosu */
CREATE OR REPLACE VIEW mart.v_satis_ozet AS
WITH aylik AS (
    SELECT musteri_id, DATE_TRUNC('month', tarih) AS ay, SUM(tutar) AS tutar
    FROM stg.siparis
    GROUP BY 1, 2
), segment AS (
    SELECT m.musteri_id, COALESCE(sg.segment, 'Bilinmiyor') AS segment
    FROM stg.musteri m
    LEFT JOIN ref.segmentler sg ON sg.kod = m.segment_kodu
)
SELECT a.ay, s.segment, SUM(a.tutar) AS tutar
FROM aylik a
JOIN segment s ON s.musteri_id = a.musteri_id
GROUP BY a.ay, s.segment;

MERGE INTO mart.musteri_ozet AS h
USING stg.musteri AS k
ON h.musteri_id = k.musteri_id
WHEN MATCHED THEN UPDATE SET h.ad = k.ad
WHEN NOT MATCHED THEN INSERT (musteri_id, ad) VALUES (k.musteri_id, k.ad);
