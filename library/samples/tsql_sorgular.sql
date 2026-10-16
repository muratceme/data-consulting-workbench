-- Son 7 günün en çok satan 10 ürünü (rapor sunucusundan)
SELECT TOP 10 u.[Urun Adi], ISNULL(SUM(s.tutar), 0) AS ciro, LEN(u.kod) AS kod_uzunluk
FROM dbo.satislar s WITH (NOLOCK)
JOIN dbo.urunler u ON u.id = s.urun_id
WHERE s.tarih >= DATEADD(day, -7, GETDATE())
GROUP BY u.[Urun Adi], u.kod
ORDER BY ciro DESC;

/* Müşteri kartı: 'ISNULL' gibi yorum ve metinlere dokunulmaz */
SELECT musteri_no, 'TR-' + musteri_no AS etiket, IIF(bakiye < 0, 'borçlu', 'alacaklı') AS durum,
       CONVERT(varchar(10), acilis_tarihi, 104) AS acilis, CONVERT(int, puan) AS puan,
       DATEDIFF(day, acilis_tarihi, GETDATE()) AS gun, DATEDIFF(month, acilis_tarihi, GETDATE()) AS ay,
       CHARINDEX('@', eposta) AS at_konum, NEWID() AS iz
FROM dbo.musteriler;

DECLARE @esik INT = 100;
SELECT * INTO #gecici FROM dbo.musteriler WHERE puan > @esik;
