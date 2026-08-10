from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.fixture_notify_guard import (
    record_fixture_telegram_sent,
    should_skip_fixture_telegram,
)
from database.db_manager import (
    add_kupon,
    init_db,
    record_fixture_notification,
)
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS


class FixtureNotifyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        import database.db_manager as db_manager

        self._previous_db_path = db_manager._DB_PATH
        db_manager._DB_PATH = Path(self._tmp.name) / "fixture_notify.db"
        init_db()

    def tearDown(self) -> None:
        import database.db_manager as db_manager

        db_manager._DB_PATH = self._previous_db_path
        self._tmp.cleanup()

    def test_skip_after_notification_recorded(self) -> None:
        key = "DR Congo - Uzbekistan|MS2"
        skip, reason = should_skip_fixture_telegram(
            key,
            "DR Congo - Uzbekistan",
            "MS2",
            3.0,
            odds_change_bypass_pct=0.05,
        )
        self.assertFalse(skip)

        record_fixture_telegram_sent(key, "DR Congo - Uzbekistan", "MS2", 3.0)
        skip, reason = should_skip_fixture_telegram(
            key,
            "DR Congo - Uzbekistan",
            "MS2",
            3.0,
            odds_change_bypass_pct=0.05,
        )
        self.assertTrue(skip)
        self.assertEqual(reason, "fixture_bildirim_gonderildi")

    def test_skip_when_kupon_exists(self) -> None:
        self.assertTrue(
            add_kupon("m1", "A - B", "MS2", 50.0, 2.0, 1.8, ev_at_alert=0.05, **LIVE_KUPON_KWARGS)
        )
        skip, reason = should_skip_fixture_telegram(
            "A - B|MS2",
            "A - B",
            "MS2",
            2.0,
            odds_change_bypass_pct=0.05,
        )
        self.assertTrue(skip)
        self.assertEqual(reason, "fixture_kupon_mevcut")

    def test_allow_when_odds_moved_enough(self) -> None:
        key = "Team A - Team B|MS1"
        record_fixture_notification(key, "Team A - Team B", "MS1", 2.0)
        skip, _ = should_skip_fixture_telegram(
            key,
            "Team A - Team B",
            "MS1",
            2.2,
            odds_change_bypass_pct=0.05,
        )
        self.assertFalse(skip)


if __name__ == "__main__":
    unittest.main()
