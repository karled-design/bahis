from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.notify_frequency_profile as notify_frequency
from core.notify_frequency_profile import (
    DEFAULT_NOTIFY_FREQUENCY_LEVEL,
    apply_notify_frequency_level,
    bootstrap_notify_frequency_profile,
    build_notify_frequency_payload,
    detect_active_notify_frequency_level,
)
from core.operator_risk_settings import get_action_ev_threshold
from core.scan_league_settings import SHARP_LEAGUE_CATALOG, get_leagues_per_scan, update_scan_league_settings


class NotifyFrequencyProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._persist = Path(self._tmp.name) / "notify_frequency.json"
        self._patch = mock.patch.object(notify_frequency, "_PERSIST_PATH", self._persist)
        self._patch.start()
        update_scan_league_settings(
            {
                "leagues_per_scan": 3,
                "enabled_sport_keys": [entry["sport_key"] for entry in SHARP_LEAGUE_CATALOG],
            }
        )

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_default_level_is_cok_sik(self) -> None:
        self.assertEqual(DEFAULT_NOTIFY_FREQUENCY_LEVEL, "5")

    def test_apply_level_updates_risk_and_leagues(self) -> None:
        apply_notify_frequency_level("4", persist=False)
        self.assertAlmostEqual(get_action_ev_threshold(), 0.017, places=3)
        self.assertEqual(get_leagues_per_scan(), 6)
        self.assertEqual(detect_active_notify_frequency_level(), "4")

    def test_increase_decrease_levels(self) -> None:
        apply_notify_frequency_level("3", persist=False)
        payload = build_notify_frequency_payload()
        self.assertEqual(payload["active_level"], "3")
        self.assertTrue(payload["can_increase"])
        self.assertTrue(payload["can_decrease"])

        apply_notify_frequency_level("5", persist=False)
        payload_high = build_notify_frequency_payload()
        self.assertFalse(payload_high["can_increase"])
        self.assertTrue(payload_high["can_decrease"])

    def test_bootstrap_persists_default(self) -> None:
        level = bootstrap_notify_frequency_profile()
        self.assertEqual(level, "5")
        self.assertTrue(self._persist.is_file())
        payload = build_notify_frequency_payload()
        self.assertIn("Cok Sik", payload["active_label"])

    def test_bootstrap_loads_saved_level(self) -> None:
        self._persist.write_text('{"level": "2"}', encoding="utf-8")
        level = bootstrap_notify_frequency_profile()
        self.assertEqual(level, "2")
        self.assertAlmostEqual(get_action_ev_threshold(), 0.030, places=3)


if __name__ == "__main__":
    unittest.main()
