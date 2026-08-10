from __future__ import annotations

import unittest

from core.experimental.mode import bootstrap_experimental_mode, set_experimental_mode
from core.experimental.shadow_log import reset_experimental_shadow_log
from core.experimental_features import (
    apply_experimental_tier_gate,
    build_experimental_features_payload,
    build_experimental_panel_payload,
    format_experimental_status_line,
    update_experimental_features,
)
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor


class ExperimentalFeaturesTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_experimental_shadow_log()
        bootstrap_experimental_mode()
        set_experimental_mode("shadow")
        update_experimental_features(
            {
                "score_model": False,
                "news_signals": False,
                "social_signals": False,
            }
        )

    def test_default_all_off(self) -> None:
        payload = build_experimental_features_payload()
        self.assertEqual(len(payload), 3)
        self.assertTrue(all(not row["enabled"] for row in payload))
        self.assertIn("kapali", format_experimental_status_line())

    def test_toggle_score_model(self) -> None:
        update_experimental_features({"score_model": True})
        payload = build_experimental_features_payload()
        score = next(row for row in payload if row["key"] == "score_model")
        self.assertTrue(score["enabled"])
        self.assertEqual(score["status"], "bekliyor")
        self.assertIn("Skor tahmini", format_experimental_status_line())

    def test_news_default_golge_status(self) -> None:
        update_experimental_features({"news_signals": True})
        news = next(row for row in build_experimental_features_payload() if row["key"] == "news_signals")
        self.assertEqual(news["status"], "golge")
        panel = build_experimental_panel_payload()
        self.assertEqual(panel["mode"]["mode"], "shadow")

    def test_gate_stub_does_not_block(self) -> None:
        update_experimental_features(
            {"score_model": True, "news_signals": True, "social_signals": True}
        )
        match = {"match_name": "A - B"}
        result = apply_experimental_tier_gate("ACTION", match, market="MS1")
        self.assertEqual(result.tier, "ACTION")
        self.assertEqual(result.modules_checked, 3)
        self.assertFalse(result.would_filter)


if __name__ == "__main__":
    unittest.main()
