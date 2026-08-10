from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.operator_risk_settings import (
    apply_risk_preset,
    build_risk_presets_payload,
    detect_active_risk_preset,
    get_context_mode,
    is_context_info_enabled,
    update_operator_risk_settings,
)
from core.paper_trade import (
    compute_ev_only_action_count,
    get_pilot_ab_summary,
    init_pilot_ab_schema,
    record_pilot_ab_cycle,
)
from core.alert_context_line import format_alert_context_tag
from database.db_manager import (
    add_kupon,
    init_db,
    record_pilot_ab_scan,
    reset_operator_tracking,
    get_pilot_ab_summary as db_get_pilot_ab_summary,
    get_performance_stats,
)
from tests.kupon_test_helpers import LIVE_KUPON_KWARGS


class ContextModeSettingsTests(unittest.TestCase):
    def test_default_context_mode_from_env(self) -> None:
        with patch("core.operator_risk_settings._SETTINGS", {
            "watch_ev": 0.015,
            "action_ev": 0.025,
            "soft_odds_min": 1.15,
            "soft_odds_max": 7.0,
            "risk_per_trade": 0.02,
            "context_mode": "filter",
        }):
            self.assertEqual(get_context_mode(), "filter")
            self.assertTrue(is_context_info_enabled())

    def test_update_context_mode(self) -> None:
        updated = update_operator_risk_settings({"context_mode": "info"})
        self.assertEqual(updated["context_mode"], "info")
        self.assertTrue(is_context_info_enabled())
        self.assertFalse(updated["context_mode"] == "filter")

    def test_alert_tag_hidden_when_context_off(self) -> None:
        with patch("core.alert_context_line.is_context_info_enabled", return_value=False):
            tag = format_alert_context_tag(
                {"context_bundle": {"standings": {}}},
                market="MS1",
                sharp_odds=2.0,
            )
        self.assertEqual(tag, "")


class RiskPresetTests(unittest.TestCase):
    def test_apply_medium_preset(self) -> None:
        applied = apply_risk_preset("medium")
        self.assertIsNotNone(applied)
        self.assertEqual(detect_active_risk_preset(), "medium")
        self.assertAlmostEqual(applied["action_ev"], 0.025)
        self.assertAlmostEqual(applied["risk_per_trade"], 0.02)

    def test_apply_calibration_preset_removed(self) -> None:
        self.assertIsNone(apply_risk_preset("calibration"))
        self.assertIsNone(apply_risk_preset("test_mode"))

    def test_build_risk_presets_payload(self) -> None:
        apply_risk_preset("low")
        presets = build_risk_presets_payload()
        self.assertEqual(len(presets), 3)
        low = next(item for item in presets if item["id"] == "low")
        self.assertTrue(low["active"])
        self.assertIn("tip", low)


class ResetTrackingTests(unittest.TestCase):
    def test_reset_clears_kupon_and_ab_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "reset.db"
            import database.db_manager as db_manager

            original = db_manager._DB_PATH
            db_manager._DB_PATH = db_path
            try:
                self.assertTrue(init_db())
                self.assertTrue(
                    add_kupon(
                        "m1",
                        "Team A - Team B",
                        "MS1",
                        50.0,
                        2.1,
                        2.0,
                        **LIVE_KUPON_KWARGS,
                    )
                )
                self.assertTrue(
                    record_pilot_ab_scan(
                        cycle_id="c1",
                        action=1,
                        high=0,
                        context_filtered=0,
                        context_bundle_count=0,
                    )
                )
                result = reset_operator_tracking(bakiye=1000.0)
                self.assertTrue(result["ok"])
                self.assertEqual(result["total_kasa"], 1000.0)
                self.assertEqual(result["cleared"]["kuponlar"], 1)
                self.assertEqual(result["cleared"]["pilot_ab_cycles"], 1)
                stats = get_performance_stats()
                self.assertEqual(stats["total_kupon"], 0)
                self.assertEqual(stats["pending"], 0)
            finally:
                db_manager._DB_PATH = original


class PilotAbPaperTradeTests(unittest.TestCase):
    def test_compute_ev_only_action_count(self) -> None:
        self.assertEqual(compute_ev_only_action_count(action=2, high=1, context_filtered=3), 6)

    def test_record_and_summarize_cycles(self) -> None:
        connection = sqlite3.connect(":memory:")
        init_pilot_ab_schema(connection)
        record_pilot_ab_cycle(
            connection,
            cycle_id="c1",
            action=2,
            high=1,
            context_filtered=1,
            context_bundle_count=2,
        )
        record_pilot_ab_cycle(
            connection,
            cycle_id="c2",
            action=1,
            high=0,
            context_filtered=0,
            context_bundle_count=1,
        )
        summary = get_pilot_ab_summary(connection)
        self.assertEqual(summary["cycles"], 2)
        self.assertEqual(summary["last_ev_only"], 1)
        self.assertEqual(summary["last_fusion"], 1)
        self.assertEqual(summary["ev_only_total"], 5)

    def test_db_manager_record_pilot_ab_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "pilot_ab.db"
            import database.db_manager as db_manager

            original = db_manager._DB_PATH
            db_manager._DB_PATH = db_path
            try:
                self.assertTrue(init_db())
                self.assertTrue(
                    record_pilot_ab_scan(
                        cycle_id="scan-1",
                        action=1,
                        high=2,
                        context_filtered=1,
                        context_bundle_count=3,
                    )
                )
                summary = db_get_pilot_ab_summary()
                self.assertEqual(summary["cycles"], 1)
                self.assertEqual(summary["last_fusion"], 3)
                self.assertEqual(summary["last_ev_only"], 4)
            finally:
                db_manager._DB_PATH = original


if __name__ == "__main__":
    unittest.main()
