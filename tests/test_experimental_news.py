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

    def _hits(self, match_name: str, titles: list[str]) -> list[dict[str, object]]:
        fake_items = [
            {"source_id": "x", "source_label": "X", "title": title, "published_at": ""}
            for title in titles
        ]
        with mock.patch("scrapers.news_feed._refresh_cache_if_needed", return_value=fake_items):
            enriched = attach_experimental_news_to_matches([{"match_name": match_name}])
        news = enriched[0].get("experimental_news")
        if not isinstance(news, dict):
            return []
        hits = news.get("rss_hits")
        return hits if isinstance(hits, list) else []

    def test_turkce_karakterli_takim_adi_eslesir(self) -> None:
        hits = self._hits(
            "Fenerbahce - Galatasaray",
            ["Fenerbahçe'de sakatlik sonrasi ilk 11 degisti"],
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["teams"], ["fenerbahce"])

    def test_cok_kelimeli_takim_adi_eslesir(self) -> None:
        hits = self._hits(
            "Manchester United - Arsenal",
            ["Manchester United handed injury boost before kickoff"],
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["teams"], ["manchester_united"])

    def test_kulup_eki_farki_eslesmeyi_bozmaz(self) -> None:
        hits = self._hits("Roma - Lazio", ["AS Roma confirm starting XI"])
        self.assertEqual(len(hits), 1)

    def test_belirsiz_tek_kelime_yanlis_eslesme_uretmez(self) -> None:
        hits = self._hits(
            "Manchester United - Arsenal",
            ["Leeds United sign a new goalkeeper"],
        )
        self.assertEqual(hits, [])

    def test_alakasiz_baslik_eslesmez(self) -> None:
        hits = self._hits("Fenerbahce - Galatasaray", ["Bugun hava durumu yagisli"])
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
