"""Iki makinede acilan motorlarin Telegram dinleyicisini kaosa dusurmemesi."""

import unittest
from unittest import mock

from core import telegram_owner_lock
from notifiers import telegram_worker


class OwnerClaimCodecTest(unittest.TestCase):
    def test_claim_yazilip_okunur(self) -> None:
        published: dict[str, object] = {}

        def fake_request(url: str, payload: dict[str, object] | None = None):
            if payload is not None:
                published.update(payload)
                return {"ok": True, "result": True}
            return {"ok": True, "result": {"short_description": published["short_description"]}}

        with mock.patch.object(telegram_owner_lock, "_request", side_effect=fake_request):
            claim = telegram_owner_lock.publish_claim("mac#42", now=1000.0)
            read_back = telegram_owner_lock.read_claim()

        self.assertIsNotNone(claim)
        self.assertEqual(read_back, telegram_owner_lock.OwnerClaim("mac#42", 1000.0))
        self.assertLessEqual(len(str(published["short_description"])), 120)

    def test_ilgisiz_aciklama_kayit_sayilmaz(self) -> None:
        with mock.patch.object(
            telegram_owner_lock,
            "_request",
            return_value={"ok": True, "result": {"short_description": "bahis botu"}},
        ):
            self.assertIsNone(telegram_owner_lock.read_claim())


class ListenerOwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        notice_patch = mock.patch.object(telegram_worker, "_send_operator_notice")
        self.notice = notice_patch.start()
        self.addCleanup(notice_patch.stop)

        telegram_worker._LISTENER_INSTANCE_ID = "vm#1"
        telegram_worker._LISTENER_CLAIMED_AT = 100.0
        telegram_worker._OWNERSHIP_YIELDED = False
        telegram_worker._LISTENER_STOP.clear()
        self.addCleanup(telegram_worker._LISTENER_STOP.clear)

    def _claim(self, instance_id: str, ts: float):
        return mock.patch(
            "core.telegram_owner_lock.read_claim",
            return_value=telegram_owner_lock.OwnerClaim(instance_id, ts),
        )

    def test_daha_yeni_motor_devralinca_dinleme_birakilir(self) -> None:
        with self._claim("mac#9", 200.0):
            self.assertEqual(telegram_worker._remote_owner_took_over(), "mac#9")
            telegram_worker._yield_listener_to("mac#9")

        self.assertTrue(telegram_worker._LISTENER_STOP.is_set())
        self.notice.assert_called_once()
        message = self.notice.call_args.args[0]
        self.assertIn("mac#9", message)
        self.assertIn("TELEGRAM_TOKEN", message)

    def test_eski_kayit_devralma_sayilmaz(self) -> None:
        with self._claim("mac#9", 50.0):
            self.assertEqual(telegram_worker._remote_owner_took_over(), "")

    def test_kendi_kaydimiz_devralma_sayilmaz(self) -> None:
        with self._claim("vm#1", 500.0):
            self.assertEqual(telegram_worker._remote_owner_took_over(), "")

    def test_devretme_bir_kez_bildirilir(self) -> None:
        telegram_worker._yield_listener_to("mac#9")
        telegram_worker._yield_listener_to("mac#9")

        self.assertEqual(self.notice.call_count, 1)

    def test_cakismada_esik_asilinca_devredilir(self) -> None:
        telegram_worker._CONFLICT_HEAL_COUNT = telegram_worker._OWNERSHIP_YIELD_THRESHOLD - 1
        with mock.patch.object(telegram_worker.time, "sleep"):
            with self._claim("mac#9", 200.0):
                telegram_worker._self_heal_telegram_conflict()

        self.assertTrue(telegram_worker._LISTENER_STOP.is_set())
        self.notice.assert_called_once()

    def test_baslarken_sahiplik_devralinir(self) -> None:
        with mock.patch(
            "core.telegram_owner_lock.build_instance_id", return_value="vm#2"
        ):
            with mock.patch(
                "core.telegram_owner_lock.read_claim",
                return_value=telegram_owner_lock.OwnerClaim("mac#9", 10.0),
            ):
                with mock.patch(
                    "core.telegram_owner_lock.publish_claim",
                    return_value=telegram_owner_lock.OwnerClaim("vm#2", 900.0),
                ) as publish:
                    telegram_worker._claim_listener_ownership()

        publish.assert_called_once_with("vm#2")
        self.assertEqual(telegram_worker._LISTENER_INSTANCE_ID, "vm#2")
        self.assertEqual(telegram_worker._LISTENER_CLAIMED_AT, 900.0)


if __name__ == "__main__":
    unittest.main()
