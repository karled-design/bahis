from __future__ import annotations

import time
import unittest

from core.match_filters import is_suspicious_odds_pair, is_suspicious_match_record
from core.scan_pipeline import evaluate_matches
from scrapers.live_feed_gateway import _build_unified_match, _merge_live_feeds
from scrapers.sharp_feed import _parse_consensus_feed


def _sample_odds_api_events() -> list[dict]:
    outcomes = [
        {"name": "Arsenal", "price": 2.10},
        {"name": "Chelsea", "price": 3.40},
        {"name": "Draw", "price": 3.25},
    ]
    bookmaker = {
        "key": "pinnacle",
        "markets": [{"key": "h2h", "outcomes": outcomes}],
    }
    duplicate_bookmaker = {
        "key": "bet365",
        "markets": [{"key": "h2h", "outcomes": outcomes}],
    }
    return [
        {
            "id": "abc123event",
            "sport_key": "soccer_epl",
            "sport_title": "EPL",
            "commence_time": "2026-06-19T15:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [bookmaker, duplicate_bookmaker],
        }
    ]


class SharpMetadataParseTests(unittest.TestCase):
    def test_parse_consensus_feed_includes_event_metadata(self) -> None:
        parsed = _parse_consensus_feed(
            _sample_odds_api_events(),
            sport_key="soccer_epl",
        )
        ms1 = parsed.get("abc123event:MS1")
        self.assertIsNotNone(ms1)
        assert ms1 is not None
        self.assertEqual(ms1["event_id"], "abc123event")
        self.assertEqual(ms1["commence_time"], "2026-06-19T15:00:00Z")
        self.assertEqual(ms1["sport_key"], "soccer_epl")
        self.assertEqual(ms1["league_name"], "EPL")

    def test_parse_consensus_feed_falls_back_to_event_sport_key_label(self) -> None:
        events = _sample_odds_api_events()
        events[0].pop("sport_title", None)
        parsed = _parse_consensus_feed(events, sport_key="soccer_turkey_super_league")
        ms1 = parsed.get("abc123event:MS1")
        self.assertIsNotNone(ms1)
        assert ms1 is not None
        self.assertEqual(ms1["league_name"], "Premier League")
        self.assertEqual(ms1["sport_key"], "soccer_epl")

    def test_parse_consensus_feed_falls_back_to_fetch_sport_key_label(self) -> None:
        events = _sample_odds_api_events()
        events[0].pop("sport_title", None)
        events[0].pop("sport_key", None)
        parsed = _parse_consensus_feed(events, sport_key="soccer_turkey_super_league")
        ms1 = parsed.get("abc123event:MS1")
        self.assertIsNotNone(ms1)
        assert ms1 is not None
        self.assertEqual(ms1["league_name"], "Turkiye Super Lig")
        self.assertEqual(ms1["sport_key"], "soccer_turkey_super_league")


class UnifiedMetadataMergeTests(unittest.TestCase):
    def test_merge_live_feeds_propagates_sharp_metadata(self) -> None:
        now = time.time()
        soft_feed = {
            "soft-1": {
                "match_name": "Arsenal - Chelsea",
                "market": "MS1",
                "soft_odds": 2.20,
                "observed_at": now,
                "soft_source": "nesine",
            }
        }
        sharp_feed = {
            "abc123event:MS1": {
                "match_name": "Arsenal - Chelsea",
                "market": "MS1",
                "sharp_odds": 2.10,
                "observed_at": now,
                "consensus_books": 2,
                "event_id": "abc123event",
                "commence_time": "2026-06-19T15:00:00Z",
                "sport_key": "soccer_epl",
                "league_name": "EPL",
            }
        }

        merged = _merge_live_feeds(soft_feed, sharp_feed, batch_epoch=now)
        self.assertEqual(len(merged), 1)
        match = merged[0]
        self.assertEqual(match["event_id"], "abc123event")
        self.assertEqual(match["commence_time"], "2026-06-19T15:00:00Z")
        self.assertEqual(match["sport_key"], "soccer_epl")
        self.assertEqual(match["league_name"], "EPL")

    def test_build_unified_match_prefers_sharp_league_over_fallback(self) -> None:
        unified = _build_unified_match(
            "Arsenal - Chelsea",
            "MS1",
            2.10,
            2.20,
            raw_packet={
                "league_name": "EPL",
                "event_id": "abc123event",
                "commence_time": "2026-06-19T15:00:00Z",
                "sport_key": "soccer_epl",
            },
        )
        self.assertEqual(unified["league_name"], "EPL")
        self.assertEqual(unified["event_id"], "abc123event")


class SuspiciousMatchQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._hero_patch = unittest.mock.patch(
            "core.scan_pipeline.is_hero_mode_enabled",
            return_value=False,
        )
        self._hero_patch.start()

    def tearDown(self) -> None:
        self._hero_patch.stop()

    def test_suspicious_odds_pair_detects_large_ratio(self) -> None:
        self.assertTrue(is_suspicious_odds_pair(11.9, 1.32))
        self.assertFalse(is_suspicious_odds_pair(2.0, 2.1))
        self.assertFalse(is_suspicious_odds_pair(3.0, 1.0))

    def test_build_unified_match_marks_suspicious_quality(self) -> None:
        unified = _build_unified_match("A - B", "MS1", 1.32, 11.9)
        self.assertEqual(unified["match_quality"], "suspicious")

    def test_build_unified_match_marks_ok_quality(self) -> None:
        unified = _build_unified_match("A - B", "MS1", 2.0, 2.1)
        self.assertEqual(unified["match_quality"], "ok")

    def test_evaluate_matches_excludes_suspicious_records(self) -> None:
        matches = [
            {
                "match_name": "Bad Match - Pair",
                "market": "MS1",
                "sharp_odds": 1.32,
                "soft_odds": 11.9,
                "match_quality": "suspicious",
                "consensus_books": 2,
            },
            {
                "match_name": "Good Match - Pair",
                "market": "MS1",
                "sharp_odds": 2.0,
                "soft_odds": 2.06,
                "match_quality": "ok",
                "consensus_books": 2,
            },
        ]
        candidates, stats = evaluate_matches(matches, current_kasa=1000.0, skip_freshness=True)
        self.assertEqual(stats.suspicious_match, 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].match_name, "Good Match - Pair")

    def test_is_suspicious_match_record_honors_explicit_flag(self) -> None:
        match = {
            "match_name": "Flagged - Match",
            "market": "MS1",
            "sharp_odds": 2.0,
            "soft_odds": 2.05,
            "match_quality": "suspicious",
        }
        self.assertTrue(is_suspicious_match_record(match))


class DryRunMetadataTests(unittest.TestCase):
    def test_dry_run_feed_includes_metadata(self) -> None:
        from scrapers.dry_run_feed import get_dry_run_unified_matches

        matches = get_dry_run_unified_matches()
        self.assertGreater(len(matches), 0)
        first = matches[0]
        self.assertTrue(first.get("event_id"))
        self.assertTrue(first.get("commence_time"))
        self.assertEqual(first.get("sport_key"), "soccer_turkey_super_league")
        self.assertEqual(first.get("match_quality"), "ok")


if __name__ == "__main__":
    unittest.main()
