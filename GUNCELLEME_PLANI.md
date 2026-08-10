# SQE-V1 Güncelleme Planı — Panel Odak Yenilemesi + Stratejiler

> **Oluşturulma:** 2026-07-12 (Claude ile sohbette kararlaştırıldı)
> **Durum:** Aşama A TAMAMLANDI (12 Tem): A1-A6 bitti. A4 renk kararı = **KEHRİBAR** (kullanıcı "1" seçti); renk yalnızca açık bölüm + seçili menü/düğme + durum bilgisinde. Bakım haritası `docs/panel-ui-manifest.md` 2.0.0'a güncellendi. Sıradaki: **Aşama B** (Stratejiler kartları + Kart 1 motoru). Yedekler: `yedekler/20260712_asamaA_panel_oncesi/`, `..._sadelestirme_oncesi/`, `..._renk_oncesi/` (geri dönüş: index.html'i geri kopyala + sayfayı yenile).
> **Ek (12 Tem, kullanıcı isteği) — SADELEŞTİRME UYGULANDI:** Risk kadranı tek kumanda yapıldı; altına 3 hazır seviye düğmesi eklendi (Güvenli/Dengeli/Agresif). Mükerrer olan her şey gizlendi (SİLİNMEDİ): Bildirim sıklığı kutusu (kadran aynı ayarı yönetiyor), eski bütçe formu + hazır tutar düğmeleri, ayar/özet satırları, "Son oynanan maçlar" (kupon listesiyle mükerrerdi), 3 tekrar eden metrik kartı. Lig seçimi "Gelişmiş" adıyla kapalı kutuda kaldı (API kredi diyeti için gerekli araç). Yedek: `yedekler/20260712_sadelestirme_oncesi/`.
> **Not:** Eski plan (`CONTEXT_LAYER_UYGULAMA_PLANI.md`) tamamlandığı için silindi; güncel plan bu dosyadır.

---

## Bu plan nereden çıktı

Kullanıcı iki şey istedi:
1. Panel çok karışık — kullanıcı dostu, **katlanabilir, odak hissi veren** yeni bir arayüz.
2. Para kazanma mantıklarının araştırılması — en iyi 3 mantık seçildi ve sıraya kondu.

Seçilen 3 mantık (kanıtlanmışlık sırasına göre):
1. **Av sahası genişletme** — yan pazarlarda (handikap, karşılıklı gol, ilk yarı) fiyat hatası avı. En kanıtlı yöntem.
2. **Değerli kombine** — avantajlı seçimleri 2-3'lü kuponda birleştirme. Kazancı da iniş-çıkışı da katlar; ancak sistem kanıt verince açılmalı.
3. **Canlı avcılık** — maç sırasında fiyat hatası avı. Teoride en kârlı, pratikte en zor; ertelendi.

---

## Değişmez kurallar (her aşamada geçerli)

- **Yalnız Nesine.** Yurtdışı bahis siteleri Türkiye'de yasa dışı — onlara dayalı hiçbir özellik kodlanmaz.
- **Zarar kovalama yok.** "Kaybedince katlayarak geri alma" tarzı sistemler matematiksel olarak kaybettirir; kodlanmaz.
- **Sadelik kuralı (12 Tem'de eklendi).** Başlıklar ve işlevler sürekli birleştirilir/sadeleştirilir: her yeni özellik önce "mevcut bir başlıkla veya işlevle birleşir mi?" sorusuyla sınanır; mükerrer gösterge/başlık eklenmez; ekran kalabalığı artıran her şey ya birleştirilir ya gizlenir. Kaldırırken silme değil gizleme tercih edilir (işlev kaybı olmaz, geri dönüş tek hamle).
- Her aşamadan önce **yedek** alınır (`yedekler/` klasörüne tarih damgalı).
- **Tek seferde tek adım**; her adım sonrası doğrulama, sonra bir sonraki.
- **Motor açıkken** test koşulmaz, `database/` içindeki canlı dosyalar elle düzenlenmez.
- Panel ekranı (`index.html`) her sayfa yenilemesinde diskten okunur → **görünüm değişikliği motor yeniden başlatma istemez**. Motor kodu (Python) değişirse yeniden başlatma gerekir ve kullanıcıya o an açıkça söylenir.

---

## Aşama A — Panel odak yenilemesi (ONAYLANDI)

Hedef: 4 sekmeli karışık panel yerine tek sayfalık "odak paneli". Maket kullanıcıya gösterildi ve beğenildi.

Tasarımın üç kuralı:
- Her zaman görünen sadece iki şey: **üst durum şeridi** (çalışıyor mu + kasa + tarama düğmesi) ve **"Şu an" kartı** (sıradaki tarama, askıda kupon, bugünkü bildirim, API kredisi).
- Geri kalan her şey **katlanır bölümlerde**; bölüm kapalıyken bile başlığında özet değer görünür.
- **Aynı anda tek bölüm açık** olur (biri açılınca diğeri kapanır).

Bölüm sırası: Kasa ve performans → Kuponlar ve maçlar → Stratejiler (yeni) → Ayarlar → Yardım.

Adımlar:
- **A1 — Yedek:** `index.html` + `docs/panel-ui-manifest.md` yedeklenir.
- **A2 — İskelet:** Üst şerit + "Şu an" kartı + 5 katlanır bölüm kurulur. Koyu tema korunur.
- **A3 — Taşıma:** Mevcut TÜM işlevler yeni bölümlere birebir taşınır (bütçe girişi, risk kadranı, kanıt karnesi, kupon listesi, lig seçimi, bildirim sıklığı, gelişmiş ayarlar, durum/teşhis, yardım). Hiçbir işlev silinmez; motorun beynine dokunulmaz.
- **A4 — Renk:** Tek vurgu rengi kararı — renk yalnızca o an açık olan bölüme ve durum bilgisine (yeşil=iyi, sarı=bekliyor, kırmızı=sorun) verilir. İki varyant maketle karşılaştırılıp kullanıcı seçer.
- **A5 — Mobil:** Alttaki menü sekme değiştirmek yerine ilgili bölümü açar ve oraya kaydırır.
- **A6 — Doğrulama:** Tarayıcıda tüm düğmeler denenir, kanıt (ekran görüntüsü) paylaşılır. Sorun çıkarsa yedekten anında geri dönülür.

Motor yeniden başlatma: **gerekmez**.

---

## Aşama B — Stratejiler bölümü + Kart 1 motoru

Panele "Stratejiler" bölümü: 3 kart, hepsi kullanıcının kontrolünde.

- **B1 — Kart iskeleti: ✅ YAPILDI (12 Tem).** 3 kart kuruldu; Kart 1-2 düğmeleri motor bağlanana dek devre dışı; Kart 2'de canlı kanıt sayacı (karne kaç kupon ölçtüyse kartta yazıyor); Kart 3 bilgi kartı. Tarayıcıda doğrulandı.
- **B2 — Keşif (sadece okuma): ✅ YAPILDI (12 Tem).** Üç hedef pazarın ÜÇÜ de Nesine'de var (96 futbol maçının 90+'ında). Ayrıntılı rapor: `docs/nesine-yan-pazar-kesif-20260712.md` (pazar kimlikleri + yapısal kanıtlar + B4 için teknik notlar). Not: Nesine verisi pazar adı vermiyor; kimlikler oran-yapısı testleriyle teşhis edildi, kesin teyit B3'te sayısal eşleştirmeyle. Misli'ye gidilmedi (kullanıcı tercihi: yalnız Nesine).
- **B3 — Referans fiyat + maliyet hesabı: ✅ YAPILDI (12 Tem, 5 kredi harcandı — onaylıydı).** Fransa–İspanya finali üzerinde sayısal teyit: **İY Sonucu ve KG kesinleşti** (KG'de Var=N1); handikap yapısı farklı çıktı (Nesine 3 seçenekli Avrupa tipi ↔ keskin 2 seçenekli Asya tipi) → **Kart 1 ilk sürüm önerisi: KG + İY** (handikap sonraya). Maliyet modeli iki rejim için çıkarıldı; **20.000/ay profesyonel paket Kart 1 için fazlasıyla yeter, daha üst paket ancak Kart 3 (canlı) açılırsa gerekir.** Ayrıntı: `docs/nesine-yan-pazar-kesif-20260712.md`.
- **B4 — Motor genişletmesi: ✅ KOD YAZILDI (12 Tem).** Karar: **KG (karşılıklı gol) CANLI, İY (ilk yarı) GÖLGE** (İY skoru otomatik kapanamıyor → kupon açsa zombi olur; gölge dosyasına kanıt için yazılır). Değişen dosyalar: `core/market_catalog.py` (KG+İY aileleri), `core/strategy_settings.py` (yeni — Kart 1 aç/kapa + günlük yan-pazar kredi tavanı 30/gün), `scrapers/soft_feed.py` (Nesine KG 89/38 + İY 88/7 ayrıştırma), `scrapers/sharp_feed.py` (maç-başına btts+h2h_h1 çekimi 3 frenli: düğme+pencere+tavan; KG sonuçlandırma; konsensüs etiketleri), `core/first_half_shadow.py` (yeni — İY gölge kaydı), `core/beginner_alert.py` (KG/İY etiketleri), `core/api_credit_ledger.py` (uç-bazlı harcama sayacı), `bahis/main.py` (İY ayrımı), `ui/web_server.py` (`/api/toggle_side_markets` + strateji payload), `index.html` (Kart 1 düğmesi canlı). Varsayılan KAPALI (açılmadan tek kredi harcamaz). DB'ye dokunmayan kanıtlar geçti (ayrıştırma + boru hattı + panel). Yedek: `yedekler/20260712_asamaB1_oncesi`.
- **B5 — Uçtan uca test + yeniden başlatma: TEST GEÇTİ (12 Tem) · RESTART BEKLİYOR.** Motor KAPALIYKEN tam set koşuldu → **225/225 OK**, B4 mevcut hiçbir şeyi bozmadı. Ayrıca B4 modülleri temiz ithal edildi + KG (2 seçim) / İY (3 seçim) aileleri doğru + **Kart 1 varsayılan KAPALI** teyit edildi. Kalan: kullanıcı motoru yeniden başlatır (yeni Python kodu ancak o zaman devreye girer), Kart 1'i açar, ilk gün gözlemi.

Motor yeniden başlatma: **B5'te gerekir** (kod diskte hazır; motor eski sürümü çalıştırıyor).

---

## Aşama C — Kart 2: Değerli kombine (KOŞULLU — bekliyor)

**Açılma şartı:** Kanıt karnesi en az 30 sonuçlanmış kuponda artıya dönmüş olmalı. (12 Temmuz itibarıyla sadece 5 sonuçlanmış kupon var ve karne hafif ekside — henüz erken.)

- **C1 — Mantık:** Aynı günün bağımsız maçlarından 2-3 avantajlı seçim tek kuponda birleştirilir. Kombine için kasadan ayrılan pay ayrı ve küçük tutulur.
- **C2 — Gölge dönemi:** Önce 2 hafta gölgede (gerçek para önerisi göndermeden, sadece kayıt tutarak) izlenir.
- **C3 — Canlıya alma:** Gölge sonuçları kullanıcıyla birlikte değerlendirilir, onayla açılır.

---

## Aşama D — Kart 3: Canlı avcılık (ERTELENDİ)

Ön şartlar: (1) paralı API paketine geçme kararı, (2) kullanıcının maç sırasında dakikalar içinde elle kupon oynayabilmesi. İkisi de yokken bu aşamaya girilmez; panelde bilgi kartı olarak durur.

---

## Yardım bölümü — acemi rehberi (12 Tem, kullanıcı isteği · TAMAM)

Kullanıcı: "Yardım sekmesini geliştir, acemi biri tüm ayarlara vâkıf olup politikamızı anlayarak bilinçli bahis yapabilsin." Yapıldı: Yardım bölümüne `settings-zone-help` bölgesi + 6 tek-açık akordeon eklendi — **Bu sistem ne yapar?** (değer bahsi mantığı), **Altın kurallar** (5 politika kuralı: yalnız Nesine / zarar kovalama yok / kanıt-önce / küçük dilim / sadelik), **Ayarları tanı** (risk kadranı+presetler, canlı bildirim, HERO, 3 strateji kartı, bütçe — her biri sade dille), **Adım adım bahis** (6 adımlık bilinçli akış), **Kazanıyor muyuz?** (Kanıt Karnesi / kapanış değeri), **Sık sorulanlar** (5 SSS + kumar-bağımlılığı uyarısı). Saf statik içerik: JS'e ve motora dokunulmadı, restart gerekmez. Koyu tema + kehribar vurgu korundu; tarayıcıda doğrulandı (0 konsol hatası). Manifest 2.3.0. Yedek: `yedekler/20260712_yardim_oncesi/`.

## Ayrıca bekleyen küçük işler

- **Askıdaki kupon:** Kanada–Fas (3 Temmuz) hâlâ sonuçlanmadı — sonuçlandırma zinciri kontrol edilecek.
- **API kredi gözlemi:** Yeni anahtarın ilk hafta harcaması izleniyor (panelde "API kredisi" etiketi).
- **Bakım haritası:** `docs/panel-ui-manifest.md` Aşama A bitince yeni düzene göre güncellenecek.
