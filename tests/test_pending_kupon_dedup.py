from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.db_manager import (
    add_kupon,
    has_any_kupon_for_event,
    has_any_kupon_for_fixture,
    has_pending_kupon_for_fixture,
    init_db,
)
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS


class PendingKuponDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        import database.db_manager as db_manager

        self._previous_db_path = db_manager._DB_PATH
        db_manager._DB_PATH = Path(self._tmp.name) / "dedup.db"
        init_db()

    def tearDown(self) -> None:
        import database.db_manager as db_manager

        db_manager._DB_PATH = self._previous_db_path
        self._tmp.cleanup()

    def test_has_pending_kupon_for_fixture(self) -> None:
        self.assertFalse(has_pending_kupon_for_fixture("A - B", "MS2"))
        self.assertTrue(
            add_kupon("m1", "A - B", "MS2", 50.0, 2.0, 1.8, ev_at_alert=0.05, **LIVE_KUPON_KWARGS)
        )
        self.assertTrue(has_pending_kupon_for_fixture("A - B", "MS2"))
        self.assertFalse(has_pending_kupon_for_fixture("A - B", "MS1"))

    def test_has_any_kupon_for_event(self) -> None:
        self.assertFalse(has_any_kupon_for_event("evt-001", "MS2"))
        self.assertTrue(
            add_kupon(
                "m1",
                "A - B",
                "MS2",
                50.0,
                2.0,
                1.8,
                ev_at_alert=0.05,
                event_id="evt-001",
                commence_time="2099-06-19T18:00:00Z",
                sport_key="soccer_turkey_super_league",
            )
        )
        self.assertTrue(has_any_kupon_for_event("evt-001", "MS2"))
        self.assertFalse(has_any_kupon_for_event("evt-001", "MS1"))

    def test_has_any_kupon_blocks_resettled_fixture(self) -> None:
        add_kupon("m1", "A - B", "MS2", 50.0, 2.0, 1.8, ev_at_alert=0.05, **LIVE_KUPON_KWARGS)
        self.assertTrue(has_any_kupon_for_fixture("A - B", "MS2"))


if __name__ == "__main__":
    unittest.main()
