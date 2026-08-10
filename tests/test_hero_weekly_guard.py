from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.hero_weekly_guard import (
    HERO_WEEKLY_DRAWDOWN_STOP,
    build_hero_weekly_payload,
    build_hero_weekly_stop_message,
    evaluate_hero_weekly_send,
    hero_local_week_key,
    mark_hero_weekly_notice_sent,
    maybe_should_send_hero_weekly_notice,
    refresh_hero_weekly_guard,
    reset_hero_weekly_guard,
)


class HeroWeeklyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmpdir.name) / "hero_weekly_state.json"
        self._state_patch = mock.patch("core.hero_weekly_guard._STATE_PATH", self._state_path)
        self._state_patch.start()

    def tearDown(self) -> None:
        self._state_patch.stop()
        self._tmpdir.cleanup()

    def test_fresh_week_no_stop(self) -> None:
        with mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=1000.0):
            payload = refresh_hero_weekly_guard()
        self.assertFalse(payload["weekly_stop_active"])
        self.assertAlmostEqual(payload["drawdown_percent"], 0.0)

    def test_drawdown_triggers_weekly_stop(self) -> None:
        week_key = hero_local_week_key()
        self._state_path.write_text(
            json.dumps(
                {
                    "week_key": week_key,
                    "week_start_kasa": 1000.0,
                    "weekly_stop_active": False,
                    "weekly_stop_notice_sent": False,
                }
            ),
            encoding="utf-8",
        )
        threshold = HERO_WEEKLY_DRAWDOWN_STOP * 100.0
        with mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=870.0):
            payload = refresh_hero_weekly_guard()
        self.assertTrue(payload["weekly_stop_active"])
        self.assertGreaterEqual(payload["drawdown_percent"], threshold)

    def test_evaluate_blocks_when_stop_active(self) -> None:
        week_key = hero_local_week_key()
        self._state_path.write_text(
            json.dumps(
                {
                    "week_key": week_key,
                    "week_start_kasa": 1000.0,
                    "weekly_stop_active": True,
                    "weekly_stop_notice_sent": False,
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch("core.hero_weekly_guard.is_hero_mode_enabled", return_value=True),
            mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=850.0),
        ):
            allow, reason = evaluate_hero_weekly_send()
        self.assertFalse(allow)
        self.assertEqual(reason, "weekly_stop")

    def test_weekly_notice_once_per_stop(self) -> None:
        week_key = hero_local_week_key()
        self._state_path.write_text(
            json.dumps(
                {
                    "week_key": week_key,
                    "week_start_kasa": 1000.0,
                    "weekly_stop_active": True,
                    "weekly_stop_notice_sent": False,
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=850.0):
            self.assertTrue(maybe_should_send_hero_weekly_notice())
            mark_hero_weekly_notice_sent()
            self.assertFalse(maybe_should_send_hero_weekly_notice())

    def test_stop_message_contains_drawdown(self) -> None:
        payload = build_hero_weekly_payload(
            state={
                "week_key": "2026-06-22",
                "week_start_kasa": 1000.0,
                "weekly_stop_active": True,
            },
            current_kasa=850.0,
            drawdown_percent=15.0,
        )
        message = build_hero_weekly_stop_message(payload)
        self.assertIn("Haftalik mola", message)
        self.assertIn("15.0", message)

    def test_new_week_resets_state(self) -> None:
        old_week = (datetime.now(timezone.utc) - timedelta(days=14)).date()
        monday = old_week - timedelta(days=old_week.weekday())
        self._state_path.write_text(
            json.dumps(
                {
                    "week_key": monday.isoformat(),
                    "week_start_kasa": 1000.0,
                    "weekly_stop_active": True,
                    "weekly_stop_notice_sent": True,
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=900.0):
            payload = refresh_hero_weekly_guard()
        self.assertEqual(payload["week_key"], hero_local_week_key())
        self.assertFalse(payload["weekly_stop_active"])

    def test_reset_clears_stop(self) -> None:
        week_key = hero_local_week_key()
        self._state_path.write_text(
            json.dumps(
                {
                    "week_key": week_key,
                    "week_start_kasa": 1000.0,
                    "weekly_stop_active": True,
                    "weekly_stop_notice_sent": True,
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("core.hero_weekly_guard.get_latest_bakiye", return_value=850.0):
            reset_hero_weekly_guard()
            payload = build_hero_weekly_payload()
        self.assertFalse(payload["weekly_stop_active"])


if __name__ == "__main__":
    unittest.main()
