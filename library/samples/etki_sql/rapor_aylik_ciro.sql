-- Aylık ciro raporu (Power BI kaynağı)
SELECT date_trunc('month', s.tarih) AS ay,
       SUM(s.tutar) AS ciro,
       COUNT(DISTINCT s.siparis_no) AS siparis
FROM dwh.fct_satis s
GROUP BY 1;
