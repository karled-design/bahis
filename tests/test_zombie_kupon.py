from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto_settler import is_zombie_kupon
from database import db_manager
from database.db_manager import (
    add_kupon,
    get_latest_bakiye,
    init_db,
    save_bakiye,
    suspend_kupon,
)


class ZombieKuponTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_db_path = db_manager._DB_PATH
        db_manager._DB_PATH = Path(self._tmp.name) / "zombie.db"
        init_db()
        save_bakiye(1000.0, "test baslangic")

    def tearDown(self) -> None:
        db_manager._DB_PATH = self._previous_db_path
        self._tmp.cleanup()

    def test_prematch_old_bet_not_zombie_before_kickoff(self) -> None:
        now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        kupon = {
            "eklenme_tarihi": "2026-06-19T16:09:09+00:00",
            "commence_time": "2026-06-28T02:00:00Z",
        }
        self.assertFalse(is_zombie_kupon(kupon, reference_at=now))

    def test_zombie_after_kickoff_buffer(self) -> None:
        now = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)
        kupon = {
            "eklenme_tarihi": "2026-06-19T16:09:09+00:00",
            "commence_time": "2026-06-28T02:00:00Z",
        }
        self.assertTrue(is_zombie_kupon(kupon, reference_at=now))

    def test_fallback_zombie_without_commence_time(self) -> None:
        now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        kupon = {
            "eklenme_tarihi": (now - timedelta(hours=73)).isoformat(),
            "commence_time": "",
        }
        self.assertTrue(is_zombie_kupon(kupon, reference_at=now))

    def test_suspend_kupon_refunds_stake(self) -> None:
        save_bakiye(900.0, "Kupon yatirimi | Turkey - USA | -100 TL")
        add_kupon(
            "m1",
            "Turkey - USA",
            "MS1",
            100.0,
            2.0,
            1.8,
            ev_at_alert=0.05,
            event_id="test-live-event-usa",
            commence_time="2026-06-28T02:00:00Z",
            sport_key="soccer_fifa_world_cup",
        )
        self.assertTrue(suspend_kupon(1, refund_stake=True))
        self.assertEqual(get_latest_bakiye(), 1000.0)


if __name__ == "__main__":
    unittest.main()
