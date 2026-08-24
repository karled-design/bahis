# SQE-V1 KULLANIM KILAVUZU

Bu kılavuz, bahis terimlerini bilmeseniz bile sistemi güvenle kullanmanız için yazıldı. SQE-V1 sizin yerinize arka planda maçları tarar, form/sakatlık gibi verileri toplar ve Telegram’a **yalnızca oynanabilir öneri** gönderir. Öneri yokken Telegram **sessiz kalır**; teknik tarama özeti panelde **Ayarlar → Durum ve teşhis → Son tarama** bölümündedir.

---

## Bahis bilmiyorsanız — 4 adımda

| Adım | Siz ne yaparsınız? | Sistem arka planda ne yapar? |
|------|--------------------|------------------------------|
| **1** | Bilgisayarda motoru başlatın (aşağıdaki komut) | Nesine + referans oranları tarar, sanal maçları atlar |
| **2** | Telegram’da **Taramayı Başlat** | Form/bağlam verisi varsa toplar, zayıf önerileri eler |
| **3** | Öneri gelince Nesine’de **yazılan tutarı** oynayın; Telegram’da **✅ Oynadım** | Tutarı test kasadan düşer, kuponu bekleyen listeye ekler |
| **4** | Bekleyin — **hiçbir şey yapmayın** | Maç bitince skor API ile sonuç bulunur; Telegram + bütçe otomatik güncellenir |

**Bütçe nasıl hesaplanır?** Panelde girdiğiniz **Nesine bütçeniz** × risk yüzdesi × fırsat gücü. Mesajda `Guncel butce` ve `Oynanacak tutar` satırları bunu gösterir.

**Telegram mesajı örneği (sade dil):**

```text
[SQE-V1] GUCLU ONERI — oynanabilir
Galatasaray - Fenerbahce
Mac durumu: Mac baslamadan (prematch)
Mac saati: 19.06.2026 21:00 (Turkiye saati)
Lig: Turkiye Super Lig
Bahis turu: Ev sahibi kazanir
Nesine orani: 2.20
Guncel butce: 1000.00 TL
Oynanacak tutar: 100.00 TL
(Butcenizin %2 risk ayarina gore hesaplandi)
Nasil oyna: Nesine'de "Ev sahibi kazanir" secenegine 100.00 TL koy.
Nesine'de oynadiktan sonra asagidaki Oynadim tusuna basin.
```

**Oynadım sonrası Telegram:**

```text
[SQE-V1] Kupon kaydedildi
Mac: Galatasaray - Fenerbahce
Oynanan tutar: 100.00 TL (kasadan dusuldu)
Yeni butce: 900.00 TL
Mac bitince sonuc otomatik kontrol edilir.
```

**Maç bitince Telegram (otomatik):**

```text
[SQE-V1] Mac sonucu islendi
Sonuc: KAZANDI — 220.00 TL butceye eklendi
Guncel butce: 1120.00 TL
```

**Telegram’da olmayanlar (panelde):** birleşik maç sayısı, pasif EV, funnel istatistikleri, “kriterlere uygun bulunamadı” tekrarlayan raporlar.

**Bahis türleri (kısa sözlük):**

| Kod | Anlamı |
|-----|--------|
| **Ev sahibi kazanır (MS1)** | Ev sahibi takım maçı kazanır |
| **Beraberlik (X)** | Skor eşit biter |
| **Deplasman kazanır (MS2)** | Deplasman takım kazanır |

**Önemli:** Sistem **otomatik para yatırmaz**. Yalnızca öneri gönderir. Oynayıp oynamamak size kalmış.

---

## Panel — sizin gördüğünüz

Panel adresi: **http://127.0.0.1:8765** (Tailscale ile telefondan da açılabilir).

Dört sekme vardır: **Özet · Maçlar · Kasa · Ayarlar**. Mobilde alt menü, masaüstünde üst menü aynı sekmeleri açar.

| Sekme | Ne işe yarar? |
|-------|----------------|
| **Özet** | Bütçe, win rate / BUGÜN, açık maç; üstte kısa durum şeridi (profil, son tarama, ligler) |
| **Maçlar** | Telegram’da **Oynadım** dediğiniz kuponlar (mobilde kart, masaüstünde tablo) |
| **Kasa** | Alternatif bütçe girişi ve kasa notları |
| **Ayarlar** | HERO, bildirim sıklığı, ligler, gelişmiş risk — katlanır bölümlerde |

Sistem arka planda diğer maçları tarar; **tüm tarama listesini** panelde görmeniz gerekmez. Teknik özet: **Ayarlar → Durum ve teşhis → Son tarama**.

---

## Panel arayüzü (4 sekme) — detay

> **Geliştirici / bakım:** Teknik manifest → [`docs/panel-ui-manifest.md`](docs/panel-ui-manifest.md)  
> Panel UI güncellenince bu bölüm ve manifest birlikte kontrol edilir.

### Sekmeler ve görünüm modu

Panelin üstünde (telefonda altında) kalıcı bir menü var:

| Sekme | Ne gösterir |
|-------|-------------|
| **Firsatlar** | Sistemin bulduğu öneriler — ne yapmanız gerektiği burada yazar |
| **Kuponlarim** | Oynadığınız kuponlar ve sonuçları |
| **Karne** | Bütçeniz, kâr oranınız ve sistemin kanıtı (Kanıt Karnesi) |
| **Strateji** | Yalnızca **Uzman** görünümünde — seçici mod (HERO) ve strateji kartları |
| **Ayarlar** | Risk seviyesi, bildirimler, ligler |
| **Yardim** | Rehber ve teknik teşhis |

Sağ üstteki **Basit / Uzman** anahtarı görünümü belirler. **Basit** (varsayılan) yalnızca
kullanmanız gereken bilgileri gösterir; **Uzman** strateji kartlarını ve API kredisi gibi
teknik ayrıntıları açar. Seçiminiz tarayıcıda saklanır.

Başlıkların yanındaki küçük **?** düğmesine dokununca terimin tek cümlelik açıklaması
ekranda belirir (Getiri, Kanıt Karnesi, Seçici mod, Aktif öneriler).

### Fırsatlar sekmesi — öneriyi okumak

Her öneri bir kart. Kartta önce ne olduğu, sonra ne yapmanız gerektiği yazar:

> Nesine bu bahse **2.20** veriyor, keskin piyasanın adil fiyatı **2.00**.
> Yani Nesine yaklaşık **%10 fazla ödüyor**; değer farkı bu.
>
> **Ne yapmalı:** Nesine'de bu oranı (ya da daha iyisini) bul, tutarı oyna ve
> aşağıdan **Oynadım**'a bas.

İki tür kart vardır:

| Rozet | Anlamı | Ne yapmalı |
|-------|--------|------------|
| **ONERI** | Oyna adayı — tutar da yazılmıştır | Oranı bulup oynayın, sonra **Oynadım** |
| **IZLE** | Fark henüz oynamaya yetmiyor | Şimdilik bir şey yapmayın; sistem izliyor |

Üstteki **Tumu / Oyna adayi / Sadece izle** düğmeleri listeyi süzer; yanlarındaki
sayı o türden kaç kayıt olduğunu gösterir. İlk kez gördüğünüz kartlarda **YENI**
etiketi çıkar. **Pas Gec** kartı listeden düşürür, kasaya dokunmaz.

**Ölçüm modu açıkken** kartların üstünde sarı bir not durur: buradaki hiçbir işlem
gerçek kupon açmaz ve kasanızı değiştirmez — **Oynadım** yalnızca "ben bunu oynadım"
notu düşer.

Liste boşsa panel nedenini yazar (ör. "Son tarama 14:32 · 186 maçta Nesine ile keskin
piyasa karşılaştırıldı, hiçbirinde Nesine yeterince yüksek kalmadı"). **Boş liste
normaldir** — fiyat farkı çıkmadıkça sistem sessiz kalır.

### Özet sekmesi

**Üstte 3 kart (her zaman görünür):**

| Kart | İçerik |
|------|--------|
| **Butceniz (Nesine)** | TL girin — otomatik kaydedilir (min 50 TL) |
| **Win Rate (Son 20)** veya **BUGÜN** | HERO kapalıyken win rate; HERO açıkken günlük öneri/kayıp |
| **Acik Mac** | Bekleyen kupon sayısı |

**Hemen altında tek satır durum şeridi** (salt okunur):

| Satır | Örnek |
|-------|--------|
| **Profil** | `Aktif · %55+ · 4/gun` (HERO) veya `Orta · +2.5% EV` (klasik) |
| **Son tarama** | `14:32 · 2 oneri` |
| **Lig** | `9 aktif · 5/tur` |

**Diğer özetler** (varsayılan kapalı): ROI, performans metrikleri, HERO açıkken **Gerçek Ölçüm** paneli.

---

### Maçlar sekmesi

| Durum | Anlam |
|-------|--------|
| **Beklemede** | Oynadınız; maç bitince sistem sonuç arar |
| **Kazandi** | Kupon kazandı; bütçe maç sonrası güncellenir |
| **Kaybetti** | Kupon kaybetti; tutar oynama anında düşülmüştü |

**Geçmişi temizle:** Test / sıfırlama için sağ üstteki düğme (onay ister).

---

### Ayarlar sekmesi — üç bölge

Panel **kontrol** ile **gözlem**i ayırır; gri kesikli kutular **değiştirilemez** durum satırlarıdır.

#### 1) Üst durum şeridi

`HERO: … · Bildirim: … · Lig: …` — accordion açmadan anlık özet.

#### 2) Ayarlar (değiştirilebilir)

| Accordion | Ne yaparsınız? | Accordion başlığında örnek |
|-----------|----------------|----------------------------|
| **Strateji** | HERO anahtarı + (açıkken) kahraman profili chip’leri | `Aktif · %55+ · 4/gun` |
| **Bildirim sıklığı** | HERO **kapalıyken** — 5 profil chip’i | `Orta · +2.5% EV` |
| **Tarama** | Hangi ligler taranır, tur başına lig sayısı | `9 lig · 5/tur` |
| **Gelişmiş risk** | Hazır profil + slider’lar (varsayılan kapalı) | Aktif profil adı |
| **Deneysel** | Gölge / canlı filtre (varsayılan kapalı) | `Golge` |

**Kahraman profili:** Yalnızca chip seçin (Temkinli → Cesur). Altında iki durum bandı:

- **Profil etkisi** — güven eşiği, günlük max, stake, oran bandı  
- **Canli durum** — bugün / hafta limitleri  

#### 3) Durum ve teşhis + Yardım

- **Son tarama** — funnel sayıları; Telegram’a gitmeyen adımlar burada  
- **Nasıl kullanılır?** — 4 adımlı kısa rehber  

**Mobil ipucu:** Aynı bölgede bir accordion açınca diğerleri kapanır (kaydırma kısalır).

---

### Ayar vs durum — nasıl ayırt edilir?

| Değiştirebilirsiniz | Salt okunur |
|---------------------|-------------|
| Toggle, chip, slider, lig aç/kapa | Snapshot şeritleri, durum grid’leri, son tarama funnel |
| Accordion içi **“Profil seçin”** etiketi | **“Durum — salt okunur”** etiketi |

---

## İyi Adam Modu (HERO) — kısa özet

**Ne farklı?** Klasik mod **oran farkı (EV)** arar; HERO modu **“iyi adam kazanır”** mantığıyla gider: güven + form + piyasa uyumu. Sanal pilot yok — gerçek maç, gerçek Nesine oranları, gerçek **Oynadım** akışı. Bütçeyi siz panelden girersiniz.

| Konu | Açıklama |
|------|----------|
| **Açma** | Panel → **Ayarlar → Strateji** → **İyi Adam Modu (HERO)** anahtarı (veya `.env` içinde `HERO_MODE=true`) |
| **Kahraman profili** | Strateji accordion’unda 5 chip (Temkinli → Cesur); güven eşiği, günlük max öneri, maç başı stake, oran bandı |
| **Günlük limit** | Profildeki max öneri sayısına ulaşınca o gün yeni Telegram önerisi yok |
| **2 kayıp stop** | Aynı gün 2 kayıp kupon sonuçlanınca o gün durur (İstanbul saati) |
| **Haftalık mola** | Pazartesi sabahı kaydedilen bütçeden hafta içinde **-%12** düşüş olursa, yeni haftaya kadar HERO önerisi yok |
| **Kapanış avantajı** | Maça ~90 dk kala veya canlı ilk 2 saatte güven ve net üstünlük eşikleri **+%1** sıkılaşır |
| **Gerçek ölçüm** | Panelde: en az 30 kupon, 7 tarama günü, win rate ≥ %52, bütçe düşüşü ≤ %15 hedefleri |

**Panelde HERO açıkken gördükleriniz:**

- **Özet** sekmesi — **BUGÜN** kartı; üst snapshot’ta profil satırı
- **Diğer özetler** (açılırsa) — **Gerçek Ölçüm** — hedeflere ne kadar yakınsınız
- **Ayarlar → Strateji** — kahraman profili chip’leri; **Canli durum** bandında hafta / MOLA
- **Win Rate (Son 20)** — Özet kartında (HERO kapalıyken aynı kart win rate gösterir)
- **Durum → Son tarama** funnel — **Haftalik mola** satırı haftalık stop sayısını gösterir

**Telegram — haftalık mola örneği:**

```text
[SQE-V1] Haftalik mola

Butceniz bu hafta basindan %12.5 dustu (sinir -%12).
Pazartesi yeni hafta baslayana kadar yeni HERO onerisi gonderilmez.
```

**Önemli:** HERO açıkken **Bildirim sıklığı** bölümü gizlenir; profil **Strateji** accordion’undadır. Kapatmak için HERO anahtarını kapatın — klasik EV moduna dönersiniz.

---

## Panel — Ayarlar (risk ve bildirim) ne işe yarar?

Paneldeki **Ayarlar** sekmesi, Telegram’a **hangi önerilerin gideceğini** ve **ne kadar tutar yazılacağını** belirler. Bölümler katlanır (accordion); üstteki **durum şeridi** anlık özeti gösterir.

**En kolay yol (klasik mod):** **Ayarlar → Bildirim sıklığı** veya **Gelişmiş risk → Orta risk**. Her profilin yanında **?** işareti vardır.

**En kolay yol (HERO):** **Ayarlar → Strateji** → HERO aç → kahraman profili chip’i seçin.

**Emin değilseniz slider’ları tek tek oynamayın** — önce profil/chip seçin; ince ayar ancak sonra.

---

### Hazır profiller (üç düğme + ?)

| Profil | Ne zaman? | Telegram | Tutar (1000 TL kasa) | Oyna eşiği |
|--------|-----------|----------|----------------------|------------|
| **Düşük risk** | Yeni başlıyorsanız | Seyrek mesaj | ~10–25 TL | ~+%3,5 |
| **Orta risk** | **Önerilen varsayılan** | Dengeli | ~20–40 TL | ~+%2,5 |
| **Yüksek risk** | Daha çok mesaj istiyorsanız | Sık mesaj | ~35–70 TL | ~+%2 |

Her profil **Akıllı bağlam filtresini** açık bırakır (e-futbol yine yoktur).

**? işareti ne gösterir?** O profilin tam açıklamasını — kaç mesaj gelir, tutar ne kadar olur, kime uygun.

Profil seçtikten sonra alttaki slider’lar o profile göre konumlanır. Tek bir slider’ı değiştirirseniz profil adı kaybolabilir (özel ayar olursunuz); bu normaldir.

---

### Her slider tek tek (ince ayar)

#### 1) Bağlam modu (üç düğme)

| Seçim | Etki |
|-------|------|
| **Kapalı** | Sadece oran farkına bak |
| **Bilgi** | Oran + kısa form notu Telegram’da |
| **Akıllı (önerilen)** | Form zayıfsa öneri **gitmez** |

**Örnek:** Oran iyi ama form kötü → **Akıllı** modda Telegram sessiz.

---

#### 2) Maç başı tutar

**Görev:** Telegram’daki **Oynanacak tutar** satırı.

`Tutar ≈ Test kasa × bu % × fırsat gücü`

| Ayar | 1000 TL kasada |
|------|----------------|
| **%1** | ~10–20 TL |
| **%2** | ~20–40 TL |
| **%4** | ~40–80 TL |

---

#### 3) Oyna önerisi

**Görev:** “Oyna” mesajı için minimum **EV avantaj** (yüzde).

| Ayar | Sonuç |
|------|--------|
| **Sol (~%1,5)** | Daha çok mesaj |
| **Orta (~%2,5)** | Dengeli |
| **Sağ (~%5)** | Az, seçici mesaj |

**Örnek:** Eşik **%3** iken avantaj **%2** → mesaj yok. **%3,5** → mesaj gelir.

---

#### 4) İzleme uyarısı

**Görev:** Parasız bilgi mesajları (çoğu gün Telegram’da görmezsiniz).

Sol = neredeyse hiç · Orta = ara sıra · Sağ = daha sık.

---

#### 5) En düşük oran / En yüksek oran

**Görev:** Hangi oran aralığına bakılsın.

| Ayar | Örnek |
|------|--------|
| En düşük **1,15** | Oran **1,08** favori elenir |
| En yüksek **7,0** | Oran **9,50** sürpriz elenir |

---

### Üç profilin tam değerleri (referans)

| Ayar | Düşük | Orta | Yüksek |
|------|-------|------|--------|
| Oyna önerisi | %3,5 | %2,5 | %2,0 |
| Maç başı tutar | %1,0 | %2,0 | %3,5 |
| İzleme uyarısı | %1,8 | %1,5 | %1,2 |
| En düşük oran | 1,20 | 1,15 | 1,10 |
| En yüksek oran | 5,50 | 7,00 | 8,50 |
| Bağlam | Akıllı | Akıllı | Akıllı |

---

### Akış özeti

1. Sistem arka planda tarar (panelde görmezsiniz).  
2. Oran + form filtreler.  
3. Eşik geçilirse Telegram → **Oynadım** → **Oynadığım Maçlar**.  
4. Maç biter → sistem sonucu bulur → Telegram + bütce.

**Sizin işiniz:** Profil seç (Orta) → Telegram’ı bekle → oyna → **Oynadım**.

---

## Panel — Bağlam modu (özet)

Panel → **Baglam modu** (üç düğme). Bahis bilgisi gerekmez; varsayılan **Akilli (onerilen)** bırakın.

| Mod | Sizin gördüğünüz | Arka planda |
|-----|------------------|-------------|
| **Kapali** | Sadece fiyat farkına göre öneri | Form verisi kullanılmaz |
| **Bilgi** | Öneride kısa form notu | Filtre yok |
| **Akilli (önerilen)** | Daha az ama daha tutarlı öneri | Form zayıfsa öneri elenir |

**API_FOOTBALL_KEY** yoksa form katmanı sessizce kapalı kalır; sistem yine çalışır.

---

## Arka planda çalışan katmanlar (sizin dokunmanız gerekmez)

| Katman | Ne işe yarar? |
|--------|----------------|
| **Fiyat taraması** | Nesine oranı, referans orandan iyi mi diye bakar |
| **Form/bağlam** | Puan durumu, son maçlar, sakatlık (API key varsa) |
| **Akilli filtre** | Form ile çelişen önerileri göndermez |
| **Auto-Settler** | Maç bitince kuponu mümkünse otomatik kapatır |
| **Pilot A/B** | Panelde “elenen / gönderilen” sayıları (izleme) |

Teşhis aracı (isteğe bağlı, terminal):

```bash
PYTHONPATH=. python3 tools/context_diag.py --source fixture
```

---

Bu kılavuz, SQE-V1 operatör panelini günlük kullanımda nasıl açıp kapatacağınızı, yeni bir telefon veya tableti nasıl güvenle bağlayacağınızı ve ev ya da ofis dışından nasıl erişeceğinizi adım adım anlatır.

---

## Sistemi Başlatma ve Durdurma

SQE-V1’i çalıştırmak, bir makineyi çalışma moduna almaya benzer. Sistem açıldığında arka planda veri takibi, panel sunucusu ve Auto-Settler servisi devreye girer. Kupon kapanışı scores tabanlı otomatik denenir; eşleşmezse panelden manuel sonuçlandırma yapılır (aşağıdaki ilgili bölümlere bakın).

**Başlatmak için**

1. Bilgisayarınızda proje klasörüne gidin.
2. Şu komutu çalıştırın:

```bash
PYTHONPATH=. python3 bahis/main.py
```

3. Ekranda hazır mesajını gördüğünüzde sistem çalışıyor demektir.
4. Operatör paneline tarayıcıdan şu adreslerden birini açın:
   - http://127.0.0.1:8765
   - http://localhost:8765

**Güvenli kapatmak için**

Sistemi kapatırken ani kesinti yapmayın. Tıpkı bir makineyi durdurmadan önce eldeki işin bitmesini beklemek gibi, kapatma komutunu verin ve sistemin kendini toparlamasına izin verin.

1. Komut satırının açık olduğu pencereye geçin.
2. Klavyeden **Ctrl+C** tuşlarına basın.
3. “Operatör Komutu ile Sistem Kapatildi.” benzeri bir mesaj görürseniz kapanış düzgün tamamlanmıştır.

Bu yöntem, o anda süren kayıtların ve kasa işlemlerinin yarım kalmasını engellemeye yardımcı olur. Pencereyi doğrudan kapatmak veya bilgisayarı aniden kapatmak yerine her zaman **Ctrl+C** kullanın.

---

## Auto-Settler ve Kupon Sonuçlandırma

`main.py` başlatıldığında arka planda **Auto-Settler** thread’i açılır. Bekleyen kupon **yoksa** API çağrısı yapmadan **15 dakikada bir** kontrol eder; bekleyen kupon **varsa** Odds API **scores** endpoint’inden bitmiş maç skorlarını okur (tarama başına **2 lig**, dönüşümlü — 6A ile aynı lig listesi).

Terminalde `Auto settler dongusu baslatildi.` ve `Sistem: Auto-Settler otonom servisi aktif edildi` mesajları bunu gösterir.

### Otomatik sonuçlandırma (6B — aktif)

`get_settlement_feed()` artık Odds API `/scores/?daysFrom=1` üzerinden **tamamlanmış maçları** okur. Skor → **MS1 / MS2 / X** sonucu türetilir; kupon marketine göre **WON** veya **LOST** yazılır.

| Davranış | Koşul | Sonuç |
|----------|--------|--------|
| Otomatik kapanma | Bitmiş maç + `mac_adi`/`market` eşleşmesi | Kupon **WON/LOST**, kasa güncellenir |
| Eşleşme yok | İsim uyuşmazlığı veya maç henüz bitmemiş | Kupon **PENDING** kalır (güvenli) |
| Zombi koruması | Bekleyen kupon **48 saatten** eskiyse | Durum **ASKIDA** yapılır |
| Bağlantı alarmı | Tüm scores istekleri başarısız | Telegram kırmızı alarm; **5 dk** beklenir |
| Kota | Bekleyen kupon yokken | **API çağrısı yapılmaz** |

Terminalde `İşlem Tamamlandı: <kupon_id> - Kasa Etkisi: ...` logu, eşleşen bitmiş maç bulunduğunda görülür.

Settlement log örneği:

```text
[SQE-V1] Settlement tarama | ligler=soccer_turkey_super_league,soccer_uefa_champs_league | basarili=... | kayit=N
```

### Operatör yedek akışı (manuel)

Sistem maç bitince **otomatik** sonuç arar (yaklaşık her 1–3 dakikada). API skor bulamazsa kupon “Sonuc bekleniyor” kalır; operatör müdahalesi nadiren gerekir.

```bash
curl -X POST http://127.0.0.1:8765/api/result_kupon \
  -H "Content-Type: application/json" \
  -d '{"kupon_id": 1, "sonuc": "WON"}'
```

`kupon_id` paneldeki satırdan alınır. `sonuc` yalnızca `WON` veya `LOST` olabilir.

### Özet

- Auto-Settler: zombi koruması + scores tabanlı otomatik kapanma.
- Eşleşme şüpheliyse kupon **açık kalır** — yanlış kapanma riski azaltılır.
- Panel manuel sonuçlandırma **yedek** olarak geçerlidir.

**Not:** Kupon `mac_adi` alarmdaki `Home - Away` formatındadır (sharp tarafı); Odds API skorlarıyla aynı isimlendirme hedeflenir.

---

## EV Eşiği ve Tutar (teknik not — isteğe bağlı)

> Günlük kullanımda bu bölümü okumanız **şart değil**. Telegram’daki **Onerilen tutar** satırına güvenin.

Sistem **otomatik bahis oynamaz**; yalnızca avantajlı görünen fırsatları Telegram’a yollar.

### EV eşiği nedir?

**EV (Expected Value)** = uzun vadede bu bahsin beklenen getirisi. Pozitif EV, soft (Nesine) oranının sharp (referans) orana göre “pahalı” olduğu anlamına gelir.

Güncel eşik: **`config/settings.py` → `MIN_VALUE_THRESHOLD = 0.03`** yani **+%3**.

| EV | Alarm |
|----|--------|
| **≥ %3** | QC geçer, aday olabilir |
| **< %3** | Elendi (`Pasif` logu) |

Terminal QC satırı:

```text
[SQE-V1] QC Aktif | EV esigi>=%3.00 | Oran=1.15-7.0
```

Eşiği değiştirmek için yalnızca `MIN_VALUE_THRESHOLD` değerini güncelleyin ve sistemi yeniden başlatın.

### Stake (yatırım tutarı) nasıl hesaplanır?

Formül:

```text
Stake = Kasa × %2 × min(EV ÷ %3, 2)
```

- **%2** → `RISK_PER_TRADE` (config)
- **EV = %3** iken çarpan **×1** (taban stake)
- **EV = %6** iken çarpan **×2** (maksimum; kasanın **%4’ü** tavan)
- **Min stake:** 50 TL (Nesine alt sınırı, `main.py`)
- **EV < %3** → stake **0** (alarm zaten gitmez)

**Örnek (kasa 5000 TL):**

| EV | Hesaplanan stake |
|----|------------------|
| %3 | 100 TL |
| %6 | 200 TL |
| %10+ | 200 TL (tavan) |

Telegram alarmında `stake=... TL` bu hesaba göre gelir; **Oyna** dediğinizde bu tutar kasadan düşer.

### Diğer QC filtreleri (kısa)

- Soft oran **1.15 – 7.0** aralığında olmalı
- E-futbol / sanal maçlar elenir
- Aynı maça **4 saat** içinde tekrar alarm gitmez
- Tarama **900 sn** (15 dk); sharp ligler **2’şer** dönüşümlü taranır

---

## Para Kazanma Mantığı — Gerçekçi Beklenti

SQE-V1 bir **yarı-otomatik sinyal + disiplin** sistemidir. “Her gün garanti kâr” vaat etmez.

**Teorik kazanç zinciri:**

1. Nesine (soft) ile Odds API (sharp) aynı maçta eşleşir  
2. EV ≥ %3 ve diğer QC filtreleri geçilir  
3. Operatör Telegram’da **Oyna** der  
4. Maç biter → Auto-Settler skoru bulur → Telegram + bütçe güncellenir  
5. Uzun vadede **gerçekten +EV** olan bahislerin toplamı kâr üretir  

**Operatörün bilmesi gereken sınırlar:**

| Konu | Gerçek |
|------|--------|
| Sharp = mutlak gerçek | Hayır; bookmaker konsensusu, hata/lag olabilir |
| EV = garanti kâr | Hayır; kısa vadede varyans yüksek |
| Her taramada sinyal | Hayır; lig rotasyonu + eşleşme + %3 eşik sık boş tur üretir |
| Otomatik zenginlik | Hayır; operatör disiplini ve yeterli hacim gerekir |
| Settlement | Scores eşleşmezse manuel kapanış gerekir |

**Kârlılık için pratik koşullar:** yeterli birleşik maç hacmi, gerçek +EV (model doğruluğu), disiplinli stake, yeterli örneklem (yüzlerce+ kupon istatistiği). Pilot kasa (1000 TL) öğrenme içindir; sonuçları genellemek için erken olabilir.

---

## Yeni Telefon/Tablet Bağlama (Güvenli Erişim)

Panel, tanımadığı cihazlardan gelen istekleri güvenlik için geri çevirir. Yetkisiz bir cihaz bağlanmaya çalışırsa ekranda **403 Forbidden** uyarısı görülebilir. Bu, sistemin sizi korumaya çalıştığı anlamına gelir; panik yapmanıza gerek yok.

Yeni bir telefon veya tableti güvenle eklemek için önce o cihazın aynı ağda olduğundan emin olun. Ardından izin verme işlemini **panelin kurulu olduğu bilgisayardan** yapın. Yabancı veya tanımsız cihazlar kendi kendine izin alamaz.

**Yeni cihaza izin vermek için**

1. Yeni cihazın bağlı olduğu ağdaki adresini öğrenin (örneğin 192.168.1.25 gibi).
2. Panelin kurulu olduğu bilgisayarda aşağıdaki komutu çalıştırın.
3. `192.168.x.x` kısmını kendi cihazınızın gerçek adresiyle değiştirin.

```bash
curl -X POST http://127.0.0.1:8765/api/force_add_ip -H "Content-Type: application/json" -d '{"ip":"192.168.x.x"}'
```

4. İşlem başarılı olduğunda cihaz yetkili listeye eklenir.
5. Artık o telefon veya tabletten panel adresini açarak kullanabilirsiniz.

**Önemli notlar**

- Bu izin komutu yalnızca panelin kurulu olduğu bilgisayardan (yerel makine) çalıştırılabilir.
- Tanımsız cihazlar finans ve kasa ile ilgili hassas bölümlere erişemez; sistem bunları otomatik olarak reddeder.
- Her yeni cihaz için adresi tek tek eklemeniz gerekir.

---

## Ev/Ofis Dışından Bağlanma

Evde veya ofiste değilken panele bakmak isteyebilirsiniz. Bunu yapmanın en güvenli yolu, modem üzerinden dış dünyaya kapı açmak değildir. Modeme port yönlendirmesi eklemek, kapınıza anahtarı asmak gibi riskli bir yöntemdir; herkesin denemesine açık hale getirebilir.

**Önerilen yöntem: Tailscale**

Tailscale, cihazlarınız arasında gizli ve güvenli bir tünel kurar. İki cihazı sanki aynı odadaymış gibi birbirine bağlar; dışarıdan gelen rastgele kişiler bu yolu göremez.

**Basit kurulum özeti**

1. Hem panelin kurulu olduğu bilgisayara hem de dışarıdan bağlanacak telefon/tablet/bilgisayara **Tailscale** uygulamasını yükleyin.
2. Her iki cihazda da aynı Tailscale hesabıyla giriş yapın.
3. Tailscale açıkken, panel bilgisayarının Tailscale adresini öğrenin.
4. Dışarıdayken tarayıcıdan bu güvenli adres üzerinden panele bağlanın.

Bu sayede ev veya ofis ağını internete açmadan, kapalı bir tünel içinden panele erişirsiniz.

**Dışarıdan bağlanırken unutmayın**

- Yeni cihaz yine yetkili listede olmalıdır. Gerekirse önce “Yeni Telefon/Tablet Bağlama” bölümündeki izin adımını uygulayın.
- Tailscale kapalıysa uzaktan erişim çalışmayabilir; bağlanmadan önce her iki tarafta da uygulamanın açık olduğundan emin olun.
- Güvenlik için modem port yönlendirmesi kullanmayın; Tailscale bu iş için daha uygun ve daha güvenli bir yoldur.

---

*SQE-V1 — Operatör kullanım kılavuzu*

---

## Operatör Terminal Hızlı Referansı

Sorun olduğunda sistemi durdurmak, onarmak ve yeniden başlatmak için aşağıdaki komutları kullanın. Tüm komutlar **macOS Terminal** içindir.

### Proje dizini (her oturumda önce buraya gidin)

```bash
cd /Users/apple/esp/bahis
```

SQE-V1 kodunun, sanal ortamın (`.venv`) ve veritabanının bulunduğu ana klasördür.

### Sanal ortamı aktif etme (önerilen)

```bash
source .venv/bin/activate
```

Komut satırının başında `(.venv)` görünür. Bağımlılıklar (Playwright, python-dotenv vb.) bu ortamdan yüklenir.

---

### Sistemi başlatma (normal)

Günlük kullanımda yalnızca motoru açmak yeterliyse:

```bash
cd /Users/apple/esp/bahis
source .venv/bin/activate
PYTHONPATH=. python bahis/main.py
```

Panel: http://127.0.0.1:8765 — **İlk açılışta Kasa sekmesinden Nesine bakiyenizi girin** (min 50 TL). Otomatik test kasası yok.

İlk kurulum, bağımlılık güncellemesi veya soft feed sorunu varsa aşağıdaki **Projeyi kaldırma (standart terminal akışı)** bölümünü uygulayın.

---

### Sistemi güvenli durdurma

Terminal penceresinde:

```text
Ctrl+C
```

“Operatör Komutu ile Sistem Kapatildi.” mesajını bekleyin. Pencereyi doğrudan kapatmayın.

---

### Asılı kalan motoru zorla durdurma

Sistem yanıt vermiyorsa veya “zaten çalışıyor” hatası alıyorsanız:

```bash
pkill -f "bahis/main.py"
```

Hâlâ çalışıyorsa (son çare):

```bash
pkill -9 -f "bahis/main.py"
```

**Ne işe yarar:** Eski SQE-V1 sürecini sonlandırır; yeni başlatmadan önce tek kopya çalışsın diye.

---

### Panel / API canlı mı kontrol

```bash
curl -s http://127.0.0.1:8765/api/status | head -c 400
```

JSON yanıt geliyorsa web sunucusu ayaktadır. Bağlantı reddedilirse motor kapalıdır.

8765 portunu kim kullanıyor:

```bash
lsof -i :8765
```

---

### Performans izleme (panel / curl)

100 kupon sonrası karar için panelde **ROI**, **Net Kar**, **Win Rate**, **Toplam Kupon** kutularına bakın. Sayaca dahil olan: **WON + LOST** (bekleyen kuponlar sayılmaz).

**Tam performans özeti (terminal):**

```bash
curl -s http://127.0.0.1:8765/api/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get('performance', {})
print('kupon_toplam=', p.get('total_kupon'))
print('won=', p.get('won'), 'lost=', p.get('lost'), 'pending=', p.get('pending'))
print('win_rate%=', p.get('win_rate'))
print('roi%=', p.get('roi_percent'))
print('net_kar_TL=', p.get('net_pl'))
print('kasa_TL=', d.get('total_kasa'))
"
```

**Sadece ROI + net kar:**

```bash
curl -s http://127.0.0.1:8765/api/finance_metrics | python3 -m json.tool
```

**Drawdown (elle):** Kasanın gördüğünüz en yüksek değerini not edin; güncel kasadan fark yüzdesi = düşüş.

| ROI (100+ sonuçlanmış kupon) | Drawdown | Karar |
|------------------------------|----------|--------|
| ≥ +2% | ≤ %25 | Devam |
| 0 … +2% | %25–35 | Bekle, risk artırma |
| < -5% | > %35 | Dur / incele |

---

### Dünya Kupası (soccer_fifa_world_cup)

Sharp lig listesine **Dünya Kupası** eklendi. Nesine milli maçları ile fuzzy eşleşme testinde **32 maç ≥ %70** eşleşme bulundu (ör. Türkiye–ABD, Brezilya–Fas).

- Rotasyon: 6 lig, tur başına 2 → DK ligi yaklaşık **45 dakikada** bir sharp taramada gelir.
- DK bittikten sonra lig listeden çıkarılabilir (kod güncellemesi gerekir).
- Tüm milli maçlar eşleşmez (~9 zayıf eşleşme); bu normal.

---

### Telegram çakışması (409) onarımı

“Conflict / başka getUpdates oturumu” veya terminalde tekrarlayan  
`Teşhis: Telegram çakışması otonom olarak onarıldı` görürseniz:

**Neden:** Aynı bot token’ına **iki getUpdates** bağlanmış (eski `main.py`, ikinci terminal). **Taramayı Başlat** işlenmeyebilir.

**Otonom onarım (motor çalışırken):** Kod webhook + kuyruk temizler; 5+ tekrarda terminalde tam onarım komutunu önerir.

**Tam onarım (tek komut — önerilen):** `reset_telegram.py` artık motor süreçlerini, panel portunu (8765) ve Telegram oturumunu birlikte temizler:

```bash
cd /Users/apple/esp/bahis
source .venv/bin/activate
PYTHONPATH=. python reset_telegram.py
PYTHONPATH=. python bahis/main.py
```

Yalnızca API temizliği (motoru durdurmadan — nadiren):

```bash
PYTHONPATH=. python reset_telegram.py --api-only
```

Ardından Telegram’da **bir kez** **[Taramayı Başlat]** deyin. Panelde salter **Acik** / live **CANLI** olmalı.

---

### Projeyi kaldırma (standart terminal akışı)

**Tek seferde yapıştır (tüm komutlar — önerilen)**

Aşağıdaki bloğu olduğu gibi terminale yapıştırın; sırayla dizin, venv, bağımlılık, Playwright, `.env` uyarısı, eski süreç temizliği, Nesine testi ve motoru başlatır. Son satır terminali meşgul eder (motor çalışırken normal).

```bash
cd /Users/apple/esp/bahis && \
([ -d .venv ] || python3 -m venv .venv) && \
source .venv/bin/activate && \
pip install -r requirements.txt && \
playwright install chrome && \
playwright install chromium && \
(test -f .env && grep -q 'ODDS_API_KEY=' .env && echo ".env OK" || echo "UYARI: .env icinde ODDS_API_KEY=... satiri ekleyin") && \
PYTHONPATH=. python reset_telegram.py && \
PYTHONPATH=. python -c "from scrapers.soft_feed import get_yasal_live_odds; print('nesine_mac=', len(get_yasal_live_odds()))" && \
PYTHONPATH=. python bahis/main.py
```

Mac uyku modunu engelleyerek başlatmak için (son satırı değiştirin):

```bash
cd /Users/apple/esp/bahis && \
([ -d .venv ] || python3 -m venv .venv) && \
source .venv/bin/activate && \
pip install -r requirements.txt && \
playwright install chrome && \
playwright install chromium && \
(test -f .env && grep -q 'ODDS_API_KEY=' .env && echo ".env OK" || echo "UYARI: .env icinde ODDS_API_KEY=... satiri ekleyin") && \
(pkill -f "bahis/main.py" 2>/dev/null || true) && \
(lsof -ti:8765 | xargs kill -9 2>/dev/null || true) && \
PYTHONPATH=. python -c "from scrapers.soft_feed import get_yasal_live_odds; print('nesine_mac=', len(get_yasal_live_odds()))" && \
PYTHONPATH=. caffeinate -s python bahis/main.py
```

Motor açıldıktan sonra:
- Panel: http://127.0.0.1:8765
- Telegram botunda **[Taramayı Başlat]** — tarama bu onay olmadan başlamaz.

Nesine testini atlamak isterseniz (daha hızlı başlatma), `nesine_mac=` satırını silin veya o satırın sonundaki `&& \` ile birlikte tüm satırı kaldırın.

---

**Adım adım (isteğe bağlı referans)**

**1) Dizin, sanal ortam, bağımlılıklar**

```bash
cd /Users/apple/esp/bahis
source .venv/bin/activate
pip install -r requirements.txt
playwright install chrome
playwright install chromium
```

İlk kurulumda `.venv` yoksa önce: `python3 -m venv .venv` — ardından `source .venv/bin/activate`.

Kod önce **Chrome** kanalını dener; macOS’ta Google Chrome yüklü değilse **chromium** yedek motor olarak kullanılır.

**2) `.env` kontrolü (zorunlu)**

Sharp feed için proje kökünde `.env` içinde `ODDS_API_KEY` tanımlı olmalıdır:

```bash
test -f .env && grep -q 'ODDS_API_KEY=' .env && echo ".env OK" || echo "UYARI: .env icinde ODDS_API_KEY=... satiri ekleyin"
```

Anahtar boşsa motor `ODDS_API_KEY: must not be empty` ile kapanır.

**3) Eski süreçleri temizle (önerilen)**

```bash
pkill -f "bahis/main.py" 2>/dev/null
lsof -ti:8765 | xargs kill -9 2>/dev/null
```

**4) Yerel bülten testi (isteğe bağlı; soft feed şüphesi varsa çalıştırın)**

```bash
PYTHONPATH=. python -c "from scrapers.soft_feed import get_yasal_live_odds; print('nesine_mac=', len(get_yasal_live_odds()))"
```

Playwright hatası alırsanız yalnızca **1)** adımını tekrar edin. `nesine_mac=0` bazı saatlerde normal olabilir.

**5) Motoru başlat**

```bash
PYTHONPATH=. python bahis/main.py
```

Mac’in uyku moduna girmesini engellemek için (isteğe bağlı):

```bash
PYTHONPATH=. caffeinate -s python bahis/main.py
```

**6) Operatör adımı**

- Panel: http://127.0.0.1:8765
- Telegram botunda **[Taramayı Başlat]** — tarama bu onay olmadan başlamaz.

---

### Playwright / yerel bülten (Nesine–Misli) ek sorun giderme

Yalnızca tarayıcı motoru eksikse veya soft feed sürekli hata veriyorsa **1)** ve **4)** adımlarını tekrarlayın; ardından **5)** ile yeniden başlatın.

---

### Bağımlılıkları yeniden kurma

```bash
cd /Users/apple/esp/bahis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chrome
playwright install chromium
```

---

### Pilot kasa 1000 TL’ye sıfırlama (dikkatli)

Mevcut kasa geçmişini silmeden panelden güncellemek daha güvenlidir. Acil terminal ile sıfırlamak için (veritabanı yedeği alın):

```bash
cd /Users/apple/esp/bahis
source .venv/bin/activate
PYTHONPATH=. python -c "
from database.db_manager import init_db, save_bakiye
init_db()
save_bakiye(1000.0, 'Pilot test kasasi manuel kalibrasyon')
print('Kasa 1000 TL olarak yazildi.')
"
```

---

### Sorun giderme sırası (özet)

1. `Ctrl+C` ile düzgün kapatmayı dene  
2. `pkill -f "bahis/main.py"` ile asılı süreci temizle  
3. Gerekirse `PYTHONPATH=. python reset_telegram.py` (motor + port + Telegram oturumu)  
4. `source .venv/bin/activate` + `PYTHONPATH=. python bahis/main.py` ile yeniden başlat  
5. Panel: http://127.0.0.1:8765 — Telegram’dan **[Taramayı Başlat]**

---

### Tailscale sıfırlama (uzaktan erişim bozulduysa)

```bash
pkill -9 -f Tailscale
rm -rf ~/Library/Containers/io.tailscale.ipn.macsys
rm -rf ~/Library/Containers/io.tailscale.ipn.macos.network-extension
rm -rf ~/Library/Group\ Containers/group.io.tailscale.ipc
open -a Tailscale
```

Tailscale uygulamasında tekrar giriş yapın; panel bilgisayarı ve telefon aynı hesapta olmalı.

---

*Son güncelleme: Context/bağlam katmanı (Faz 0–3), sade Telegram önerileri, panel Baglam modu + Pilot A/B, Auto-Settler scores, pilot kasa 1000 TL*

---

## OPERATÖR KONTROL VE ACİL MÜDAHALE KOMUTLARI

Sistemde bir kilitlenme, port çakışması veya hafıza hatası yaşandığında Terminal üzerinden uygulanacak endüstriyel komut setidir. Her işlemden önce mutlaka proje dizinine girilmeli ve sanal ortam aktif edilmelidir.

### 1. Fabrika Zeminine Giriş ve İzolasyon (Zorunlu İlk Adım)

```bash
cd /Users/apple/esp/bahis
source .venv/bin/activate
```

**Etkisi:** Üretim hattının bulunduğu klasöre girer ve motorun ihtiyaç duyduğu sanal donanımları (`.venv`) aktif eder.

### 2. Acil Durdurma (Şalter İndirme)

```bash
pkill -9 -f python
```

**Etkisi:** Arka planda asılı kalan, kilitlenen veya gizlice çalışmaya devam eden tüm motor süreçlerini acımasızca sonlandırır.

> **Uyarı:** Bu komut bilgisayardaki *tüm* Python süreçlerini kapatır. Mümkünse önce daha hedefli komutu deneyin: `pkill -9 -f "bahis/main.py"`

### 3. Operatör Paneli (HMI) Port Tıkanıklığını Açma

```bash
lsof -ti:8765 | xargs kill -9
```

**Etkisi:** "Address already in use" (Port 8765 dolu) hatası alınırsa, iletişim hattını işgal eden hayalet süreci bulup yok eder.

### 4. Hafıza ve Kasa Sıfırlama (Factory Reset)

```bash
rm -f database/sqe_storage.db database/sqe_storage.db-wal database/sqe_storage.db-shm
```

**Etkisi:** Sistemin eski maç geçmişini ve kasa bakiyesini (veritabanını) fiziksel olarak siler. Motor bir sonraki açılışta **butce panelden tekrar girilene kadar** tarama baslamaz.

> SQE-V1 veritabanı kök dizinde değil; `database/sqe_storage.db` yolundadır.

### 5. Üretim Bandını Ateşleme (Start)

```bash
PYTHONPATH=. python bahis/main.py
```

**Etkisi:** Sistemi temiz bir şekilde ayağa kaldırır, piyasa taramasını başlatır ve Telegram'a iş emirlerini göndermeye hazır hale gelir.

Panel: http://127.0.0.1:8765 — Telegram'dan **[Taramayı Başlat]** beklenir.

### 6. Yeni Donanım / Kütüphane Kurulumu (Bakım Modu)

```bash
pip install -r requirements.txt
playwright install chrome
playwright install chromium
```

**Etkisi:** Sisteme yeni bir modül entegre edildiğinde eksik kütüphaneleri indirir ve Playwright tarayıcı çekirdeğini günceller (Chrome öncelikli, Chromium yedek).

---

### Acil müdahale sırası (tek bakışta)

```text
cd /Users/apple/esp/bahis && source .venv/bin/activate
pkill -9 -f python
lsof -ti:8765 | xargs kill -9
rm -f database/sqe_storage.db database/sqe_storage.db-wal database/sqe_storage.db-shm

PYTHONPATH=. caffeinate -s python bahis/main.py
```

---

*SQE-V1 — Acil müdahale komut seti (operatör)*

api kota kontrolu.
cd /Users/apple/esp/bahis
source .venv/bin/activate
curl -sI "https://api.the-odds-api.com/v4/sports/?apiKey=$(grep ODDS_API_KEY .env | cut -d= -f2)" | grep -i x-requests


Ayaga kaldırma

cd /Users/apple/esp/bahis && \
([ -d .venv ] || python3 -m venv .venv) && \
source .venv/bin/activate && \
pip install -r requirements.txt && \
playwright install chrome && \
playwright install chromium && \
(test -f .env && grep -q 'ODDS_API_KEY=' .env && echo ".env OK" || echo "UYARI: .env icinde ODDS_API_KEY=... satiri ekleyin") && \
PYTHONPATH=. python reset_telegram.py && \
PYTHONPATH=. python -c "from scrapers.soft_feed import get_yasal_live_odds; print('nesine_mac=', len(get_yasal_live_odds()))" && \
PYTHONPATH=. python bahis/main.py

Panelde ne var?
AYARLAR bölümünde, slider’ların üstünde Hazır risk profili alanı var:

Düşük risk · Orta risk · Yüksek risk
Her birinin yanında ? — üzerine gelince o profilin tam açıklaması çıkar
Seçili profil yeşil çerçeveyle vurgulanır
Profil tıklanınca alttaki slider’lar otomatik konumlanır ve kaydedilir
Tek bir slider’ı elle oynatırsanız profil adı kaybolur (özel ayar); bu normal
Değişiklikleri görmek için paneli Cmd+Shift+R ile yenileyin veya motoru yeniden başlatın.

Her slider ne yapar?
Ayar	Görevi	Sola kaydırırsanız	Sağa kaydırırsanız
Bağlam modu
Form/bağlam kullanımı
Kapalı → sadece oran
Akıllı → zayıf form elenir
Maç başı tutar
Telegram’daki Oynanacak tutar
Küçük tutar (~%1)
Büyük tutar (~%4)
Oyna önerisi
“Oyna” mesajı için minimum avantaj
Daha seçici, az mesaj
Daha çok mesaj
İzleme uyarısı
Parasız bilgi mesajları
Neredeyse hiç
Daha sık (çoğu gün görmezsiniz)
En düşük oran
Alt sınır
Ağır favoriler elenir
Daha düşük oranlara da bakılır
En yüksek oran
Üst sınır
Sürpriz bahisler elenir
Daha yüksek oranlara da bakılır
Formül (tutar): Oynanacak tutar ≈ Test kasa × Maç başı tutar % × fırsat gücü

Üç hazır profil
Düşük risk	Orta risk (önerilen)	Yüksek risk
Kime uygun
Yeni başlıyorsanız
Çoğu kullanıcı
Daha sık mesaj istiyorsanız
Telegram
Seyrek, güçlü fırsatlar
Dengeli
Daha sık
Tutar (1000 TL)
~10–25 TL
~20–40 TL
~35–70 TL
Oyna eşiği
+%3,5
+%2,5
+%2,0
Maç başı %
%1
%2
%3,5
Oran aralığı
1,20 – 5,50
1,15 – 7,00
1,10 – 8,50
Bağlam
Akıllı
Akıllı
Akıllı
? metinleri (panelde):

Düşük: En güvenli; az mesaj, küçük tutar, sıkı filtre
Orta: Denge; ~+%2,5 avantaj, ~%2 tutar — varsayılan
Yüksek: Daha sık mesaj, daha büyük tutar; form filtresi yine açık, e-futbol yine yok
Sizin için öneri
Emin olmadan slider’lara dokunmayın:

Orta risk profiline tıklayın
Birkaç gün Telegram’ı izleyin
İnce ayar ancak sonra (ör. tutar çok büyük geliyorsa Maç başı tutar)
Profil değerlerini sizin onayınız olmadan değiştirmedim; mevcut kod ve kılavuz (SQE_V1_Kullanim_Kilavuzu.md) aynı tabloları kullanıyor.

Bir profilin eşiğini veya tutar aralığını değiştirmek isterseniz hangisini hedeflediğinizi yazın; onayınızdan sonra ayarları güncellerim. 

Günlük kullanım için tek satır:

cd /Users/apple/esp/bahis && source .venv/bin/activate && (pkill -f "bahis/main.py" 2>/dev/null || true) && (lsof -ti:8765 | xargs kill -9 2>/dev/null || true) && PYTHONPATH=. python bahis/main.py


Mac uyumasın diye:

---

## Deneysel modüller (Faz E0 + E1)

**Varsayılan:** Gölge mod + yalnızca **Haber sinyalleri** açık.

| Mod | Davranış |
|-----|----------|
| **Gölge** | Uyumsuz adaylar `database/experimental_shadow.jsonl` dosyasına yazılır; Telegram çıktısı **değişmez**. |
| **Canlı filtre** | Sakatlık/RSS uyumsuzluğu olan adaylar gerçekten elenir. |

**Haber sinyalleri** kaynakları:
- API-Football sakatlık (context bundle)
- Resmi RSS whitelist (`config/experimental_rss.json`)
- Maça **2 saat kala** sakatlık önbelleği yenilenir

**Panel:** Ayarlar → Deneysel modüller — modül aç/kapa, gölge/canlı geçiş, gölge istatistik kartı.

**Diğer modüller:** Skor modeli (E2) ve sosyal sinyaller (E5) şimdilik “bekliyor”; açılsa bile filtre uygulamaz.

**API:** `POST /api/experimental_mode` body: `{"mode":"shadow"}` veya `{"mode":"live"}`

---

## Panel UI — bakım ve güncelleme (geliştirici)

Panel arayüzü tek dosyada: `index.html` (HTML + CSS + JS). Backend API: `ui/web_server.py`.

| Kaynak | İçerik |
|--------|--------|
| [`docs/panel-ui-manifest.md`](docs/panel-ui-manifest.md) | DOM ID listesi, JS fonksiyonları, faz geçmişi, güncelleme protokolü |
| [`.cursor/rules/panel-ui.mdc`](.cursor/rules/panel-ui.mdc) | Cursor agent kuralı — panel görevlerinde manifest okunur |
| Bu kılavuz → *Panel arayüzü (4 sekme)* | Operatör / son kullanıcı metni |

**Panel UI değişince güncelle:**

1. `index.html`
2. `docs/panel-ui-manifest.md` (sürüm + changelog)
3. `SQE_V1_Kullanim_Kilavuzu.md` (kullanıcıya görünen metin)
4. `python -m unittest discover -s tests -q`

**Manifest sürümü (son):** 1.0.0 · **2026-06-24**

cd /Users/apple/esp/bahis
source .venv/bin/activate
pkill -f "bahis/main.py"; sleep 2
PYTHONPATH=. python reset_telegram.py
PYTHONPATH=. python bahis/main.py