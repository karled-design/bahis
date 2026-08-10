from __future__ import annotations

import unittest
from unittest import mock

from core.experimental.gate import apply_experimental_hero_gate, apply_experimental_tier_gate
from core.experimental.mode import bootstrap_experimental_mode, set_experimental_mode
from core.experimental.shadow_log import reset_experimental_shadow_log
from core.experimental_features import update_experimental_features
from scrapers.news_feed import attach_experimental_news_to_matches
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor


class ExperimentalNewsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_experimental_shadow_log()
        bootstrap_experimental_mode()
        set_experimental_mode("shadow")
        update_experimental_features(
            {
                "score_model": False,
                "news_signals": True,
                "social_signals": False,
            }
        )

    def test_injury_block_in_shadow_passes_through(self) -> None:
        match = {
            "match_name": "Galatasaray - Fenerbahce",
            "context_bundle": {
                "injuries": {
                    "home_absences": [{"name": f"P{i}"} for i in range(3)],
                    "away_absences": [],
                }
            },
        }
        result = apply_experimental_tier_gate("ACTION", match, market="MS1")
        self.assertEqual(result.tier, "ACTION")
        self.assertTrue(result.would_filter)

    def test_injury_block_in_live_filters(self) -> None:
        set_experimental_mode("live")
        match = {
            "match_name": "Galatasaray - Fenerbahce",
            "context_bundle": {
                "injuries": {
                    "home_absences": [{"name": f"P{i}"} for i in range(3)],
                    "away_absences": [],
                }
            },
        }
        result = apply_experimental_tier_gate("ACTION", match, market="MS1")
        self.assertIsNone(result.tier)
        self.assertTrue(result.would_filter)

    def test_hero_gate_shadow_does_not_block(self) -> None:
        match = {
            "match_name": "Besiktas - Trabzonspor",
            "context_bundle": {
                "injuries": {
                    "home_absences": [{"name": f"P{i}"} for i in range(4)],
                    "away_absences": [],
                }
            },
        }
        result = apply_experimental_hero_gate(match, market="MS1")
        self.assertTrue(result.passed)
        self.assertTrue(result.would_filter)

    def test_attach_experimental_news_from_rss(self) -> None:
        rows = [
            {
                "match_name": "Galatasaray - Fenerbahce",
                "event_id": "e1",
                "sport_key": "soccer_turkey_super_league",
            }
        ]
        fake_items = [
            {
                "source_id": "tff",
                "source_label": "TFF",
                "title": "Galatasaray oyuncusu sakatlik nedeniyle oynamayacak",
                "published_at": "",
            }
        ]
        with mock.patch("scrapers.news_feed._refresh_cache_if_needed", return_value=fake_items):
            enriched = attach_experimental_news_to_matches(rows)
        self.assertIn("experimental_news", enriched[0])
        hits = enriched[0]["experimental_news"]["rss_hits"]
        self.assertEqual(len(hits), 1)
        result = apply_experimental_tier_gate("ACTION", enriched[0], market="MS1")
        self.assertTrue(result.would_filter)


if __name__ == "__main__":
    unittest.main()
