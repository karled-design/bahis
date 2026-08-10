# Nesine yan pazar keşfi — 2026-07-12 (Aşama B2, salt okuma)

**Kaynak:** `https://cdnbulten.nesine.com/api/bulten/getprebultenfull` (ücretsiz, kredi harcamaz; motorun zaten kullandığı uç nokta). Tek örnek çekildi: 227 etkinlik, **96 futbol maçı**, futbolda ~80 farklı pazar türü.

**Önemli:** Bu uç nokta pazar ADI vermiyor (yalnız `MST` + `MTID` kimlikleri; `MN` adı yalnız birkaç pazarda dolu). Kimlikler aşağıda **oran-yapısı testleriyle** teşhis edildi; kesin ad teyidi B3'te keskin kaynakla (adları açık yazar: spreads/btts/h2h_h1) aynı maç üzerinde sayısal eşleştirmeyle yapılacak.

## Hedef 3 pazar — ÜÇÜ DE VAR

| Pazar (hipotez) | Kimlik | Yaygınlık | Yapısal kanıt |
|---|---|---|---|
| Handikaplı Maç Sonucu | `MST=100 MTID=268` | 90/96 maç (maç başına birden çok çizgi, SOV ±1..±4) | Ev +1 çizgisinde ev oranı 1X2'ye göre hep düşük: **29/29** |
| Karşılıklı Gol Var/Yok | `MST=89 MTID=38` | 94/96 | Maçtan maça geniş dağılım (1.07–2.40), 50/50 pazarı değil; hangi seçenek "Var" B3'te teyit edilecek |
| İlk Yarı Sonucu (1X2) | `MST=88 MTID=7` | 93/96 | Beraberlik oranı maç beraberliğinden hep küçük: **93/93** |

## Bonus bulunanlar (ileride aday)

| Pazar (hipotez) | Kimlik | Not |
|---|---|---|
| 2. Yarı Sonucu | `MST=36 MTID=9` | 89/89 beraberlik-sıkışması; İY'den daha yüksek X → 2Y deseni |
| Tek/Çift | `MST=91 MTID=49` | 90 maç, iki oran simetrik (ort fark 0.09) |
| İY Toplam Gol A/Ü | `MST=60 MTID=209(0.5)/14(1.5)/15(2.5)` | çizgiler SOV'da |
| Takım golleri A/Ü | `MST=722 MTID=455` ve `MST=723 MTID=457` | SOV 0.5/1.5/2.5; ev/dep ayrımı B3'te teyit |
| "İki Yarı da 1,5 Alt/Üst" | `MST=729/730 MTID=528/529` | Adı payload'da AÇIK (MN dolu) |
| Maç toplam gol A/Ü | `MST=101 MTID=11/12/13` (+155/207 ek çizgiler) | Motor 1.5/2.5/3.5'i ZATEN kullanıyor |

## Teknik notlar (B4 için)

- Payload yolu: `payload["sg"]["EA"]` → etkinlik `{HN, AN, C/EV, GT(spor; futbol=1), MA:[pazarlar]}`; pazar `{MST, MTID, SOV(çizgi), OCA:[{N,O}], NO(İddaa program no), MBS}`.
- Mevcut ayrıştırıcı `scrapers/soft_feed.py` → `_extract_nesine_prematch_payload` yalnız `MST=1 MTID=1` (1X2) + `MST=101 MTID=11/12/13` (A/Ü) alıyor; genişletme noktası orası.
- Ham örnek scratchpad'te (`nesine_prebulten_ornek.json`) — oturuma özel, kalıcı değil; gerekirse keşif betiği `nesine_pazar_kesif.py` yeniden koşulur.
- Misli API'sine GİDİLMEDİ (kullanıcı tercihi: yalnız Nesine).

## B3 — Keskin kaynak teyidi (12 Tem, Fransa–İspanya finali üzerinde, 5 kredi)

Aynı maç iki kaynaktan çekilip sayısal eşleştirildi (Pinnacle referans):

| Pazar | Nesine | Keskin | Sonuç |
|---|---|---|---|
| İlk Yarı Sonucu | `88/7`: %39/%51/%30 | `h2h_h1`: %34/%46/%25 | **KESİN TEYİT** — aynı profil, beraberlik iki tarafta da baskın |
| Karşılıklı Gol | `89/38`: N1 %66 / N2 %53 | `btts`: Var %60 / Yok %46 | **KESİN TEYİT** — ve **N=1 → VAR, N=2 → YOK** |
| Handikap | `100/268`: 3 seçenekli (1/X/2), tam sayı çizgiler ±1..±4 | `spreads`: 2 seçenekli Asya tipi, çeyrek çizgi (−0.25/+0.25) | **YAPI FARKLI** — aşağıya bak |

**Handikap uyarısı (B4 kapsam kararını etkiler):** Nesine'nin handikabı Avrupa tipi (beraberlik seçeneği VAR, 3 sonuç); keskin kaynağın `spreads`'i Asya tipi (2 sonuç, çeyrek çizgiler). Bire bir fiyat karşılaştırması YAPILAMAZ; araya gol-dağılımı modeli (dönüşüm) gerekir → hata riski ve iş yükü artar. Öneri: **Kart 1'in ilk sürümü = KG + İY Sonucu** (ikisi de bire bir eşleşiyor, alt/üst genişletmesiyle aynı basitlikte); handikap sonraki sürüme (dönüşüm modeliyle ya da hiç).

## B3 — Kredi maliyet modeli (ölçülmüş, tahmin değil)

Ölçülen maliyetler: ana bülten çağrısı = pazar × bölge (eu × 3 pazar = **3 kredi/lig-tarama**); yan pazarlar (btts, h2h_h1) yalnız **maç başına** ayrı uçtan çekilebiliyor: eu × 2 pazar = **2 kredi/maç/çekim**.

- **Bugünkü 500/ay diyeti:** Yan pazarlar ancak sıkı tavanla sürdürülebilir (ör. günlük yan-pazar bütçesi ~10 kredi = 5 maç). Dünya Kupası döneminde (1-2 maç) sorunsuz; normal sezonda (5 lig × çok maç) 500 ile yan pazar avı DARALIR.
- **Profesyonel 20.000/ay (kullanıcının geçmeyi planladığı paket):** Rahat sığar. Örnek yoğun senaryo: 5 lig, günde 3 tarama, tarama başına 8 maça yan pazar → ≈ 93 kredi/gün ≈ **2.800/ay**. Çok agresif senaryoda bile (günde 6 tarama, 15 maç, 4 yan pazar) ≈ 13.500/ay < 20.000. 
- **Daha üst paket:** Şimdilik GEREKSİZ. Ancak Kart 3 (canlı avcılık) devreye alınırsa gerekir — canlıda dakikada bir çekim günde binlerce kredi yer; o karar zaten Aşama D ön şartı.
- Bölge notu: `uk,eu` → yalnız `eu` yeterli (Pinnacle + borsalar eu'da); mevcut 4 kredi/tarama → 2'ye iner, spreads eklense bile 3'te kalır.

Ham teyit dosyaları scratchpad'te: `sharp_featured_ornek.json`, `sharp_ek_pazar_ornek.json` (oturuma özel).
