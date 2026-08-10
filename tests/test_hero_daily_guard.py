from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core.hero_daily_guard import (
    HERO_DAILY_LOSS_STOP,
    build_hero_daily_limit_message,
    build_hero_daily_status,
    evaluate_hero_daily_send,
    hero_local_date_key,
    mark_hero_daily_notice_sent,
    maybe_should_send_hero_daily_notice,
    record_hero_alert_sent,
)
from core.hero_profile import apply_hero_profile_level, bootstrap_hero_profile, DEFAULT_HERO_PROFILE_LEVEL
from database.db_manager import add_kupon, count_kupon_losses_between, init_db, result_kupon
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor


class HeroDailyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_hero_profile()
        apply_hero_profile_level(DEFAULT_HERO_PROFILE_LEVEL)
        self._tmpdir = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmpdir.name) / "hero_daily_state.json"
        self._state_patch = mock.patch("core.hero_daily_guard._STATE_PATH", self._state_path)
        self._state_patch.start()

    def tearDown(self) -> None:
        self._state_patch.stop()
        self._tmpdir.cleanup()
        apply_hero_profile_level(DEFAULT_HERO_PROFILE_LEVEL)

    def test_fresh_day_allows_send(self) -> None:
        allow, reason = evaluate_hero_daily_send()
        self.assertTrue(allow)
        self.assertEqual(reason, "")

    def test_daily_limit_blocks_after_max_alerts(self) -> None:
        apply_hero_profile_level("3")
        record_hero_alert_sent()
        record_hero_alert_sent()
        record_hero_alert_sent()
        status = build_hero_daily_status()
        self.assertFalse(status["can_send"])
        self.assertEqual(status["block_reason"], "daily_limit")
        self.assertEqual(status["alerts_sent"], 3)

    def test_loss_stop_blocks_after_two_losses(self) -> None:
        with mock.patch(
            "core.hero_daily_guard.refresh_hero_daily_losses",
            return_value=HERO_DAILY_LOSS_STOP,
        ):
            status = build_hero_daily_status()
        self.assertFalse(status["can_send"])
        self.assertEqual(status["block_reason"], "loss_stop")

    def test_limit_notice_sent_once(self) -> None:
        apply_hero_profile_level("1")
        record_hero_alert_sent()
        record_hero_alert_sent()
        status = build_hero_daily_status()
        self.assertEqual(maybe_should_send_hero_daily_notice(status), "daily_limit")
        mark_hero_daily_notice_sent("daily_limit")
        self.assertIsNone(maybe_should_send_hero_daily_notice(status))

    def test_loss_stop_message_text(self) -> None:
        status = build_hero_daily_status()
        status = {
            **status,
            "can_send": False,
            "block_reason": "loss_stop",
            "losses_today": 2,
        }
        text = build_hero_daily_limit_message(status, kind="loss_stop")  # type: ignore[arg-type]
        self.assertIn("Bugun mola", text)
        self.assertIn("2 kayip", text)

    def test_daily_limit_message_text(self) -> None:
        status = build_hero_daily_status()
        status = {
            **status,
            "can_send": False,
            "block_reason": "daily_limit",
            "alerts_sent": 2,
            "max_alerts": 2,
        }
        text = build_hero_daily_limit_message(status, kind="daily_limit")  # type: ignore[arg-type]
        self.assertIn("Bugun doldu", text)
        self.assertIn("2/2", text)

    def test_state_resets_on_new_day(self) -> None:
        record_hero_alert_sent()
        yesterday = "2020-01-01"
        self._state_path.write_text(
            json.dumps({"date_key": yesterday, "alerts_sent": 5, "limit_notice_sent": True}),
            encoding="utf-8",
        )
        status = build_hero_daily_status()
        self.assertEqual(status["date_key"], hero_local_date_key())
        self.assertEqual(status["alerts_sent"], 0)
        self.assertTrue(status["can_send"])


class HeroDailyDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_patch = mock.patch(
            "database.db_manager._DB_PATH",
            Path(self._tmpdir.name) / "test.db",
        )
        self._db_patch.start()
        init_db()

    def tearDown(self) -> None:
        self._db_patch.stop()
        self._tmpdir.cleanup()

    def test_result_kupon_sets_sonuc_tarihi_for_loss_count(self) -> None:
        self.assertTrue(
            add_kupon(
                "match-1",
                "A - B",
                "MS1",
                50.0,
                2.0,
                1.8,
                **LIVE_KUPON_KWARGS,
            )
        )
        self.assertTrue(result_kupon(1, "LOST"))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        count = count_kupon_losses_between("2000-01-01 00:00:00", "2099-01-01 00:00:00")
        self.assertEqual(count, 1)
        _ = now


class HeroDailyDispatchTests(unittest.TestCase):
    def test_dispatch_skips_when_daily_limit_reached(self) -> None:
        from bahis.main import _dispatch_scan_notifications, _new_scan_cycle_stats

        candidate = {
            "notify_key": "abc",
            "match_name": "A - B",
            "market": "MS1",
            "match_id": "mid-1",
            "alert_message": "test",
            "stake": 50.0,
            "soft_odds": 1.9,
            "sharp_odds": 1.7,
            "ev_percent": "3.0",
            "tier": "ACTION",
            "ev": 0.03,
            "sport_key": "soccer",
            "test_probe": False,
            "match_record": {},
            "hero_confidence": 0.62,
        }
        stats = _new_scan_cycle_stats()

        with mock.patch("bahis.main.is_hero_mode_enabled", return_value=True):
            with mock.patch(
                "bahis.main.build_hero_daily_status",
                return_value={
                    "date_key": "2026-06-24",
                    "alerts_sent": 2,
                    "max_alerts": 2,
                    "losses_today": 0,
                    "max_losses": 2,
                    "can_send": False,
                    "block_reason": "daily_limit",
                },
            ):
                with mock.patch(
                    "bahis.main.evaluate_notification_quality",
                    return_value=mock.Mock(allow=True, reason=""),
                ):
                    with mock.patch("bahis.main._is_on_fixture_cooldown", return_value=False):
                        with mock.patch("bahis.main.telegram_worker.arm_qualifying_alerts"):
                            with mock.patch("bahis.main.telegram_worker.send_alert") as send_alert:
                                _dispatch_scan_notifications("cycle-1", [candidate], {}, stats)

        send_alert.assert_not_called()
        self.assertEqual(stats["hero_daily_limit"], 1)


if __name__ == "__main__":
    unittest.main()
