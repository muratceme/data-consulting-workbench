Her gece 02:00’de çalışacak bir veri hattı lazım (Europe/Istanbul).

- ERP gece dosyası /veri/erp/<tarih>/bitti.flag gelince ERP yükleme betiği çalışsın.
- CRM API’den müşteri verisi etl.crm modülündeki yukle fonksiyonuyla çekilsin.
- İkisi bitince staging SQL’i, sonra dbt mart modelleri ve testleri çalışsın.
- En sonda Power BI veri kümesi yenilensin. Her şey 05:00’e kadar bitmeli.
