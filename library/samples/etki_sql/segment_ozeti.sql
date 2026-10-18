SELECT m.segment, COUNT(*) AS musteri
FROM dwh.dim_musteri m
GROUP BY m.segment;
