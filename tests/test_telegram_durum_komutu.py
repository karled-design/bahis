"""Telegram /durum komutu: operator anlik durum karti isteyebilmeli."""

import unittest
from unittest import mock

from config import settings
from notifiers import telegram_worker


class TelegramDurumKomutuTest(unittest.TestCase):
    def setUp(self) -> None:
        notice_patch = mock.patch.object(telegram_worker, "_send_operator_notice")
        self.notice = notice_patch.start()
        self.addCleanup(notice_patch.stop)

        scan_patch = mock.patch.object(telegram_worker, "_set_scan_enabled")
        self.set_scan = scan_patch.start()
        self.addCleanup(scan_patch.stop)

        self.chat_id = str(settings.TELEGRAM_CHAT_ID)

    def _message(self, text: str, chat_id: str | None = None) -> dict[str, object]:
        return {"chat": {"id": chat_id or self.chat_id}, "text": text}

    def test_durum_komutu_nabiz_kartini_gonderir(self) -> None:
        with mock.patch.object(telegram_worker, "_build_status_text", return_value="KART"):
            handled = telegram_worker._process_message(self._message("/durum"))

        self.assertTrue(handled)
        self.notice.assert_called_once_with("KART")
        self.set_scan.assert_not_called()

    def test_bot_adiyla_ve_buyuk_harfle_de_calisir(self) -> None:
        with mock.patch.object(telegram_worker, "_build_status_text", return_value="KART"):
            self.assertTrue(
                telegram_worker._process_message(self._message("/durum@kolakoz_bot"))
            )
            self.assertTrue(telegram_worker._process_message(self._message("/Status")))

        self.assertEqual(self.notice.call_count, 2)

    def test_yetkisiz_sohbet_durum_alamaz(self) -> None:
        handled = telegram_worker._process_message(self._message("/durum", chat_id="-999"))

        self.assertFalse(handled)
        self.notice.assert_not_called()

    def test_bilinmeyen_metin_yoksayilir(self) -> None:
        self.assertFalse(telegram_worker._process_message(self._message("merhaba")))
        self.notice.assert_not_called()

    def test_durum_metni_kurulmazsa_guvenli_mesaj_doner(self) -> None:
        with mock.patch(
            "core.olcum_nabzi.build_pulse_message", side_effect=ValueError("bozuk")
        ):
            text = telegram_worker._build_status_text()

        self.assertIn("Durum", text)


if __name__ == "__main__":
    unittest.main()
