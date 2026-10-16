-- Oracle rapor sorguları
SELECT m.musteri_no,
       NVL(m.segment, 'Bilinmiyor') AS segment,
       NVL2(m.eposta, 'var', 'yok') AS eposta_durumu,
       DECODE(m.durum, 'A', 'Aktif', 'P', 'Pasif', 'Diğer') AS durum,
       INSTR(m.eposta, '@') AS at_konum,
       CAST(m.puan AS NUMBER(10, 2)) AS puan
FROM musteriler m, siparisler s
WHERE m.id = s.musteri_id(+)
  AND m.kayit_tarihi > SYSDATE - 30
  AND ROWNUM <= 100;

SELECT TO_CHAR(SYSDATE, 'YYYY-MM-DD') AS bugun FROM DUAL;

SELECT kod FROM urunler
MINUS
SELECT urun_kod FROM satislar;

SELECT calisan_id, yonetici_id, LEVEL
FROM calisanlar
START WITH yonetici_id IS NULL
CONNECT BY PRIOR calisan_id = yonetici_id;
