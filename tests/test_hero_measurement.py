from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.hero_measurement import (
    MEASUREMENT_MIN_ACTIVE_DAYS,
    MEASUREMENT_MIN_COUPONS,
    MEASUREMENT_TARGET_WIN_RATE_PERCENT,
    build_hero_measurement_payload,
    ensure_hero_measurement_started,
    record_hero_measurement_scan_day,
    reset_hero_measurement,
)
from database.db_manager import add_kupon, init_db, result_kupon
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS


class HeroMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._state_path = Path(self._tmpdir.name) / "hero_measurement.json"
        self._state_patch = mock.patch("core.hero_measurement._STATE_PATH", self._state_path)
        self._state_patch.start()
        reset_hero_measurement()

    def tearDown(self) -> None:
        self._state_patch.stop()
        self._tmpdir.cleanup()

    def test_idle_when_hero_disabled(self) -> None:
        with mock.patch("core.hero_measurement.is_hero_mode_enabled", return_value=False):
            payload = build_hero_measurement_payload()
        self.assertEqual(payload["phase"], "idle")

    def test_collecting_phase_before_targets(self) -> None:
        ensure_hero_measurement_started(starting_kasa=1000.0)
        with mock.patch("core.hero_measurement.is_hero_mode_enabled", return_value=True):
            with mock.patch("core.hero_measurement.get_latest_bakiye", return_value=1000.0):
                with mock.patch(
                    "core.hero_measurement.get_settled_kupon_stats_since",
                    return_value={"settled": 5, "won": 3, "lost": 2, "win_rate_percent": 60.0},
                ):
                    payload = build_hero_measurement_payload()
        self.assertEqual(payload["phase"], "collecting")
        self.assertIn("5/30", payload["status_label"])

    def test_pass_phase_when_targets_met(self) -> None:
        with mock.patch("core.hero_measurement.is_hero_mode_enabled", return_value=True):
            ensure_hero_measurement_started(starting_kasa=1000.0)
            for day in (
                "2026-06-01",
                "2026-06-02",
                "2026-06-03",
                "2026-06-04",
                "2026-06-05",
                "2026-06-06",
                "2026-06-07",
            ):
                record_hero_measurement_scan_day(date_key=day)
            with mock.patch("core.hero_measurement.get_latest_bakiye", return_value=1050.0):
                with mock.patch(
                    "core.hero_measurement.get_settled_kupon_stats_since",
                    return_value={
                        "settled": MEASUREMENT_MIN_COUPONS,
                        "won": 18,
                        "lost": 12,
                        "win_rate_percent": MEASUREMENT_TARGET_WIN_RATE_PERCENT + 1.0,
                    },
                ):
                    payload = build_hero_measurement_payload()
        self.assertEqual(payload["phase"], "pass")
        self.assertGreaterEqual(payload["active_scan_days"], MEASUREMENT_MIN_ACTIVE_DAYS)

    def test_review_when_win_rate_low(self) -> None:
        with mock.patch("core.hero_measurement.is_hero_mode_enabled", return_value=True):
            ensure_hero_measurement_started(starting_kasa=1000.0)
            for idx in range(MEASUREMENT_MIN_ACTIVE_DAYS):
                record_hero_measurement_scan_day(date_key=f"2026-06-{idx + 1:02d}")
            with mock.patch("core.hero_measurement.get_latest_bakiye", return_value=980.0):
                with mock.patch(
                    "core.hero_measurement.get_settled_kupon_stats_since",
                    return_value={
                        "settled": MEASUREMENT_MIN_COUPONS,
                        "won": 10,
                        "lost": 20,
                        "win_rate_percent": 33.3,
                    },
                ):
                    payload = build_hero_measurement_payload()
        self.assertEqual(payload["phase"], "review")
        self.assertIn("Azalt", payload["recommendation"])


class HeroMeasurementDbTests(unittest.TestCase):
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

    def test_settled_stats_since_timestamp(self) -> None:
        from database.db_manager import get_settled_kupon_stats_since

        self.assertTrue(add_kupon("m1", "A - B", "MS1", 50.0, 2.0, 1.8, **LIVE_KUPON_KWARGS))
        self.assertTrue(result_kupon(1, "WON"))
        stats = get_settled_kupon_stats_since("2000-01-01 00:00:00")
        self.assertEqual(stats["settled"], 1)
        self.assertEqual(stats["won"], 1)


if __name__ == "__main__":
    unittest.main()
