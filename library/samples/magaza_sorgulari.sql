-- Müşterinin son siparişleri (sipariş geçmişi sayfası)
SELECT siparis_id, siparis_tarihi, tutar FROM siparis WHERE musteri_id = 42 ORDER BY siparis_tarihi DESC LIMIT 20;
-- Giriş: e-posta ile müşteri bul
SELECT musteri_id FROM musteri WHERE eposta = 'kisi@ornek.test';
-- Bir ürünün geçtiği siparişler
SELECT k.siparis_id, k.adet FROM siparis_kalem k WHERE k.urun_id = 1001;
-- Günlük açık sipariş raporu
SELECT siparis_id, tutar FROM siparis WHERE durum = 'acik' AND siparis_tarihi >= '2026-10-01';
-- Siparişin kalemleri
SELECT urun_id, adet FROM siparis_kalem WHERE siparis_id = 555;
