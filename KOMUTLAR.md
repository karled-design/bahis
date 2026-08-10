# Slash ( / ) Komutları — Sade Rehber

Bu dosya, çalışmalarımızda işine yarayacak `/` komutlarını ve her birinin ne işe
yaradığını basit dille anlatır. Hepsi **mesaj kutusunun başına** yazılır, sonra
Enter'a basılır. Çoğu tek başına çalışır; bazılarına arkasından bir cümle ekleyebilirsin.

---

## En çok kullanacakların

### /compact
- **Ne yapar:** Sohbet uzayınca hafızayı toparlar, gereksiz detayı atar, önemli
  noktaları özet olarak tutar. Böylece konuşma yavaşlamaz ve şaşmaz.
- **Nasıl:** `/compact` yaz, Enter. İstersen arkasına yön ver:
  `/compact arayüz değişikliklerini koru`.
- **Ne zaman:** Bir işi bitirip doğruladıktan sonra, yeni konuya geçmeden önce.

### /clear
- **Ne yapar:** Sohbeti tamamen sıfırdan başlatır (hiçbir şeyi hatırlamaz).
- **Ne zaman:** Tamamen yeni, alakasız bir işe geçerken. Dikkat: geçmişi siler,
  `/compact` gibi özet bırakmaz.

### /code-review
- **Ne yapar:** Yaptığımız kod değişikliklerini gözden geçirir, hata/risk arar.
- **Nasıl:** `/code-review` yaz, Enter.

---

## Faydalı diğerleri

### /security-review
- **Ne yapar:** Kodu güvenlik açıkları (sızıntı, açık kapı) için tarar.
- **Bizim için:** Telegram tokeni, `.env`, gizli anahtarlar güvende mi diye bakmak iyi olur.

### /fast
- **Ne yapar:** Yanıtları aynı kalitede ama daha hızlı üretir (aç/kapa).
- **Nasıl:** `/fast` yaz, Enter ile açılır; tekrar yazınca kapanır.

### /help
- **Ne yapar:** Mevcut tüm komutların listesini gösterir.

---

## Kullanım kuralı (hepsi için)
1. Komutu **satırın başına** yaz.
2. Tek başına yeterli; istersen arkasından kısa bir açıklama ekle.
3. Enter'a bas.

> Not: Bazı komutlar sadece terminaldeki Claude'da açılan ayar ekranlarıdır
> (`/config`, `/permissions` gibi) ve bu sohbette çalışmayabilir. İşine en çok
> yarayacaklar yukarıdakiler: **/compact**, **/clear**, **/code-review**.

cd /Users/apple/esp/bahis
source .venv/bin/activate
PYTHONPATH=. python reset_telegram.py
PYTHONPATH=. python bahis/main.py
