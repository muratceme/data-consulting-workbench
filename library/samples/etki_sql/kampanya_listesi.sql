-- Pazarlama kampanya listesi (her pazartesi CRM'e aktarılır)
SELECT m.musteri_no, m.segment, m.eposta
FROM dwh.dim_musteri m
WHERE m.segment IN ('Altın', 'Platin')
  AND m.eposta IS NOT NULL;
