---
name: testing-bahis-panel
description: SQE-V1 (bahis) uygulamasini yerelde ayaga kaldirip web panelini (http://127.0.0.1:8765), olcum modunu ve Telegram bildirim yolunu ucdan uca test etme rehberi.
---

# SQE-V1 / bahis — yerel test rehberi

## Uygulamayi baslatma (tercih: motor_ctl.sh)
En kolay yol `tools/motor_ctl.sh` (baslat | durdur | durum | yeniden). Betik `.env`'i
`set -a` ile yukler (yani `.env` degerleri dis ortamdaki bayat degiskenleri EZER),
`python -u` kullanir, PID'i `database/motor.pid`'e, logu `database/motor.log`'a yazar:

```bash
cd <repo>
./tools/motor_ctl.sh baslat      # ~10 sn; "[SQE-V1] Motor basladi | PID=... | Panel: http://127.0.0.1:8765"
tail -f database/motor.log | grep --line-buffered -E 'nabzi|Tarama|hazir'
./tools/motor_ctl.sh durdur      # bazen SIGTERM'e 20 sn cevap vermez -> betik SIGKILL'e duser (bilinen davranis)
```
Env ile ayar gerekiyorsa prefix atama calisir: `OLCUM_NABIZ_DAKIKA=5 ./tools/motor_ctl.sh baslat`
(`.env` icinde tanimli DEGILSE korunur; `.env`'de varsa ezilir).

### Tek instance temizligi (`yabanci_motorlari_kapat`)
`baslat` artik makinedeki diger motor sureclerini (`pgrep -f "${MOTOR_SUREC_DESENI:-python.*bahis/main\.py}"`)
SIGTERM -> `MOTOR_KILL_TIMEOUT` (vars. 10 sn) -> SIGKILL ile kapatir; PID dosyasindaki
motor ve betigi calistiran kabugun ppid zinciri korunur. Cikti: `[SQE-V1] N eski motor kapatildi (tek instance korunuyor).`
Test ederken:
```bash
# sahte "yabanci motor" (cmdline desene uyar, gercek python degil):
bash -c 'sleep 300; true' "/tmp/eski/.venv/bin/python -u bahis/main.py" &
# kontrol grubu: alakasiz surecler
sleep 300 & python3 -c 'import time; time.sleep(300)' &
./tools/motor_ctl.sh baslat
pgrep -f 'python.*bahis/main\.py' | wc -l   # daima 1 olmali
```
DIKKAT: kendi test komut satirinizda desen gecerse (`pgrep -af 'python.*bahis/main\.py'`)
o kabuk da eslesir — betik kendi ata zincirini korudugu icin motor_ctl'i calistiran
kabuk guvenlidir, ama BASKA bir terminalde acik duran desen-icerikli komut kapatilabilir.
PID dosyasi silinirse calisan motor "yabanci" sayilir: `baslat` onu kapatip yenisini acar (tek instance).

## Uygulamayi elle baslatma (alternatif)
Dis ortamda eski/gecersiz `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `ODDS_API_KEY`
degiskenleri olabilir; `config/settings.py` `.env` dosyasini `load_dotenv` ile
okur ama ortam degiskeni varsa o kazanir. Bu yuzden bu degiskenleri **temizleyerek**
baslatin ve loglarin akmasi icin `python -u` kullanin:

```bash
cd <repo>
env -u TELEGRAM_CHAT_ID -u TELEGRAM_TOKEN -u ODDS_API_KEY PYTHONPATH=. \
  setsid nohup .venv/bin/python -u bahis/main.py > /home/ubuntu/bahis_main.log 2>&1 < /dev/null &
```
- `-u` olmadan stdout tamponlanir ve log ~30 sn geride kalir; "Sistem hazir" satiri gorunmez, sistem calismiyor sanilir.
- Hazir olma isareti: log'da `[SQE-V1] Sistem hazir | Telegram: [Taramayi Baslat] bekleniyor` + `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/` -> 200 (yaklasik 30-40 sn surer).
- Yeniden baslatmadan once `pkill -f bahis/main.py`; iki surec ayni anda `getUpdates` yaparsa Telegram HTTP 409 olur.

## Taramayi UI'dan baslatma (Telegram'a gerek yok)
`telegram_worker.wait_for_scan_start()` sadece `SISTEM_DURUMU["scan_enabled"]`
bayragini okur; panel `/api/toggle_scan` bu bayragi set eder. Yani panelin
kirmizi seridindeki **"Taramayi Baslat"** dugmesi taramayi gercekten baslatir —
Telegram butonuna basmaya gerek yok. Serit yesile doner ("Sistem calisiyor ...").

## Telegram bildirim kaniti
- DIKKAT: `notifiers/telegram_worker.py` icinde `TELEGRAM_RECOMMENDATIONS_ONLY = True`
  (ilk commit'ten beri). Bu bayrak acikken `send_scan_empty_report*` ve
  `send_scan_cycle_summary*` **hic mesaj atmaz, sadece True doner**. Yani "bos tarama
  raporu gonderildi" diye rapor YAZMAYIN; hata diag'inin yoklugu gonderim kaniti degildir.
  Gercekten `sendMessage` atan yollar: `send_system_ready`, `send_hero_daily_notice`
  (gunluk ozet + olcum nabzi) ve gercek sinyal bildirimleri.
- Chat id gecerliligini tek seferlik test mesajiyla dogrulayin (token/chat id ekrana YAZMADAN):
  `set -a; . ./.env; set +a; curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" -d chat_id=$TELEGRAM_CHAT_ID -d text=test`
- Bot tokeninin gecerliligi: `curl -s https://api.telegram.org/bot$TELEGRAM_TOKEN/getMe` (getUpdates KULLANMAYIN — calisan listener ile 409 catisir).
- Sohbeti goremezsiniz; mesajin ulastigini kullaniciya teyit ettirin.
- Tarama araligi 600 sn (`LIVE_SCAN_INTERVAL_SECONDS`); ikinci rapor icin 10 dk bekleyin.

## Olcum modu (para riski yok)
- Durum dosyasi: `database/measurement_mode.json` -> `{"enabled": true}`.
- Panel: **Ayarlar** akordiyonu > "Olcum modu (para riski yok)" (`#measurementToggleBtn`).
  Acikken buton "Acik" ve alt metin "…kupon acilmaz, bakiye degismez.".
- Adversarial dogrulama: butona basip JSON dosyasinin `enabled` alaninin
  degistigini gorun, sonra eski haline dondurun.
- Olcum modunda `bahis/main.py` sinyali `match_id/stake=None` ile gonderir ->
  Telegram mesajinda "Oyna" dugmesi cikmaz; eski mesajlar icin callback
  `notifiers/telegram_worker.py:_handle_play_callback` icinde engellenir.

## Olcum nabzi (core/olcum_nabzi.py) testi
- Varsayilan aralik 60 dk; `OLCUM_NABIZ_DAKIKA` ile ezilir, taban 5 dk. Aralik **import
  aninda** okunur, yani env'i surec baslamadan once verin.
- Son gonderim zamani `database/olcum_nabiz_state.json` icinde. Dosya YOKSA ilk dongu
  turunda nabiz **hemen** atilir — hizli kanit icin motoru baslatmadan once dosyayi silin.
- Nabiz yalnizca ana dongude (yani panelden "Taramayi Baslat" ile dongu basladiktan
  sonra) tetiklenir. Kanit satiri: log'da `[SQE-V1] Olcum nabzi gonderildi` — bu satir
  yalnizca gercek `sendMessage` basarili olursa basilir.
- Nabiz, suresi dolduktan sonraki ilk dongu basinda gider; olcum modunda tur suresi
  120 sn oldugu icin gozlenen aralik 5 dk degil ~5-7 dk olabilir (spam degil, normal).
- Icerik dogrulamasi icin surec disi bir harness yazin: `record_cycle({...})` cagirip
  `build_pulse_message()` yazdirin, sonra `core.measurement_mode.report()` ve
  `core.api_credit_ledger.build_credit_panel_payload()['status_label']` ile karsilastirin.
  Huni satiri insan diliyle yazilir ("44 mac karsilastirildi | esik alti 36 | aday 1"),
  ham `birlesik=44` metnini aramayin.
- Kredi satiri surece baglidir: motor sureci gecersiz ODDS anahtariyla kota basligi
  alamazsa "Bugun 0/16 kredi | Kalan ?" gorunur, ayri bir harness sureci defterden
  "Bugun 0/33 kredi | Kalan 496" uretebilir. Ikisi de ayni builder'dan gelir.
- Adversarial kosumlarda `database/olcum_nabiz_state.json` ve `measurement_mode.json`
  dosyalarini yedekleyip sonunda geri yukleyin ve **once motoru durdurun** (calisan
  motor ayni dosyalari okur/yazar).

## Telegram 409 / cakisma teshisi
`database/motor.log` icinde `Teşhis: Telegram çakışması otonom olarak onarıldı` satiri
normalde motor basina 1-2 kez cikar (kullanicinin baska makinesindeki motor ayni botu
polluyor olabilir). Tekrar tekrar donen (saniyeler icinde onlarca) satir = 409 dongusu = regresyon.
Logda ham `409` aramak yaniltici: `cycle_id` sayilari icinde "409" gecebilir, once satiri okuyun.
Sayimi mutlaka son `[SQE-V1] Sistem hazir` satirindan sonrasi icin yapin (log restartlarda append edilir).

## Bilinen kisit
`ODDS_API_KEY` gecersizse the-odds-api HTTP 401 (INVALID_KEY) doner; kuresel
konsensus feed bos kalir, hicbir sinyal uretilemez. Bu durumda "olcum modunda
Oyna dugmesi cikmiyor" iddiasi ucdan uca gosterilemez — gecerli bir anahtar
gerekir; alternatif olarak `scrapers/dry_run_feed.py` mock feed'i vardir ama
`bahis/main.py` icinden acilacak bir bayrak/CLI yoktur (yalnizca
`live_feed_gateway.set_dry_run_mode` cagrilirsa devreye girer).

## Devin Secrets Needed
- `TELEGRAM_TOKEN` (bot tokeni, @kolakoz_bot)
- `TELEGRAM_CHAT_ID` (.env icinde mevcut)
- `ODDS_API_KEY` (gecerli bir the-odds-api anahtari — sinyal uretimi testi icin sart)
