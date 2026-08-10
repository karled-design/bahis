from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.kasa_sync import refresh_alert_budget_lines
from database.db_manager import (
    add_kupon,
    compute_kasa_from_kupon_ledger,
    get_latest_bakiye,
    init_db,
    recalibrate_kasa_from_baseline,
    reset_operator_tracking,
    result_kupon,
)
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS


class KasaSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        import database.db_manager as db_manager

        self._previous_db_path = db_manager._DB_PATH
        db_manager._DB_PATH = Path(self._tmp.name) / "kasa_sync.db"
        init_db()

    def tearDown(self) -> None:
        import database.db_manager as db_manager

        db_manager._DB_PATH = self._previous_db_path
        self._tmp.cleanup()

    def test_refresh_alert_budget_lines(self) -> None:
        text = refresh_alert_budget_lines(
            "Guncel butce: 1000.00 TL\nOynanacak tutar: 20.00 TL",
            live_kasa=950.0,
            stake=20.0,
        )
        self.assertIn("Guncel butce: 950.00 TL", text)
        self.assertIn("Oynanacak tutar: 20.00 TL", text)

    def test_compute_kasa_from_kupon_ledger(self) -> None:
        kupons = [
            {"stake": 100.0, "soft_oran": 2.0, "durum": "WON"},
            {"stake": 50.0, "soft_oran": 1.9, "durum": "LOST"},
        ]
        kasa = compute_kasa_from_kupon_ledger(20000.0, kupons)
        self.assertAlmostEqual(kasa, 20050.0, places=2)

    def test_recalibrate_kasa_from_baseline_replays_kupons(self) -> None:
        self.assertTrue(
            add_kupon("m1", "A - B", "MS1", 100.0, 2.0, 1.8, ev_at_alert=0.05, **LIVE_KUPON_KWARGS)
        )
        self.assertTrue(result_kupon(1, "WON"))
        recalibrated = recalibrate_kasa_from_baseline(20000.0)
        self.assertTrue(recalibrated["ok"])
        self.assertAlmostEqual(float(recalibrated["total_kasa"]), 20100.0, places=2)


if __name__ == "__main__":
    unittest.main()
