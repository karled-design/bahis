"""Matchbook borsa kaynagi testleri (ag erisimi yok)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from scrapers.live_feed_gateway import _merge_sharp_sources
from scrapers.matchbook_feed import normalize_matchbook_events


def _future_iso(hours: float) -> str:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _runner(name: str, back: float, lay: float) -> dict[str, Any]:
    return {
        "name": name,
        "status": "open",
        "prices": [
            {"side": "back", "odds": back, "available-amount": 120.0},
            {"side": "lay", "odds": lay, "available-amount": 90.0},
        ],
    }


def _match_odds_market(**overrides: Any) -> dict[str, Any]:
    market: dict[str, Any] = {
        "market-type": "one_x_two",
        "name": "Match Odds",
        "status": "open",
        "in-running-flag": False,
        "volume": 4200.0,
        "runners": [
            _runner("Galatasaray", 2.00, 2.06),
            _runner("Fenerbahce", 4.00, 4.20),
            _runner("Draw", 3.60, 3.75),
        ],
    }
    market.update(overrides)
    return market


def _totals_market(line: float = 2.5, **overrides: Any) -> dict[str, Any]:
    market: dict[str, Any] = {
        "market-type": "total",
        "name": "Total",
        "handicap": line,
        "status": "open",
        "in-running-flag": False,
        "volume": 1800.0,
        "runners": [
            _runner(f"OVER {line}", 1.90, 1.96),
            _runner(f"UNDER {line}", 2.00, 2.08),
        ],
    }
    market.update(overrides)
    return market


def _event(markets: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": 991,
        "name": "Galatasaray vs Fenerbahce",
        "status": "open",
        "in-running-flag": False,
        "start": _future_iso(6),
        "markets": markets,
    }
    event.update(overrides)
    return event


class NormalizationTests(unittest.TestCase):
    def test_match_odds_runners_map_to_local_markets(self) -> None:
        records = normalize_matchbook_events(
            [_event([_match_odds_market()])], observed_at=1000.0
        )

        self.assertEqual(sorted(record["market"] for record in records.values()), ["MS1", "MS2", "X"])
        home = records["991:MS1"]
        self.assertEqual(home["match_name"], "Galatasaray - Fenerbahce")
        self.assertEqual(home["consensus_source"], "exchange_consensus")
        self.assertEqual(home["sport_key"], "matchbook_exchange")
        # Fiyat back (2.00) ile lay (2.06) arasindaki orta noktadir; tek tarafli
        # back alinsaydi referans sistematik olarak dusuk cikardi.
        self.assertGreater(float(home["sharp_odds"]), 2.00)
        self.assertLess(float(home["sharp_odds"]), 2.06)

    def test_vig_free_family_still_gets_fair_probability(self) -> None:
        records = normalize_matchbook_events(
            [_event([_match_odds_market()])], observed_at=1000.0
        )
        probabilities = [float(record["fair_probability"]) for record in records.values()]

        self.assertEqual(len(probabilities), 3)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=5)

    def test_only_the_two_and_a_half_line_is_taken(self) -> None:
        records = normalize_matchbook_events(
            [_event([_totals_market(2.5), _totals_market(3.5)])], observed_at=1000.0
        )

        self.assertEqual(
            sorted(record["market"] for record in records.values()), ["ALT 2.5", "UST 2.5"]
        )

    def test_inplay_started_and_thin_markets_are_dropped(self) -> None:
        events = [
            _event([_match_odds_market()], id=1, **{"in-running-flag": True}),
            _event([_match_odds_market()], id=2, start=_future_iso(-1)),
            _event([_match_odds_market(volume=10.0)], id=3),
            _event([_match_odds_market(**{"in-running-flag": True})], id=4),
            _event([_match_odds_market()], id=5, status="closed"),
        ]
        self.assertEqual(normalize_matchbook_events(events, observed_at=1000.0), {})

    def test_wide_back_lay_spread_is_rejected(self) -> None:
        wide = _match_odds_market(
            runners=[
                _runner("Galatasaray", 2.00, 3.00),
                _runner("Fenerbahce", 4.00, 4.20),
                _runner("Draw", 3.60, 3.75),
            ]
        )
        records = normalize_matchbook_events([_event([wide])], observed_at=1000.0)

        self.assertNotIn("991:MS1", records)
        self.assertIn("991:MS2", records)

    def test_unknown_runner_name_is_ignored(self) -> None:
        market = _match_odds_market(
            runners=[
                _runner("Some Other Team", 2.00, 2.06),
                _runner("Draw", 3.60, 3.75),
            ]
        )
        records = normalize_matchbook_events([_event([market])], observed_at=1000.0)

        self.assertEqual(list(records), ["991:X"])


class SharpMergeTests(unittest.TestCase):
    def test_primary_source_wins_on_same_match_and_market(self) -> None:
        primary = {
            "evt:MS1": {
                "match_name": "Galatasaray - Fenerbahce",
                "market": "MS1",
                "sharp_odds": 1.95,
            }
        }
        secondary = {
            "991:MS1": {
                "match_name": "Galatasaray - Fenerbahce",
                "market": "MS1",
                "sharp_odds": 2.05,
            },
            "991:X": {
                "match_name": "Galatasaray - Fenerbahce",
                "market": "X",
                "sharp_odds": 3.70,
            },
        }

        merged = _merge_sharp_sources(primary, secondary, label="matchbook")

        self.assertEqual(merged["evt:MS1"]["sharp_odds"], 1.95)
        self.assertNotIn("matchbook:991:MS1", merged)
        self.assertEqual(merged["matchbook:991:X"]["sharp_odds"], 3.70)

    def test_empty_secondary_returns_primary_untouched(self) -> None:
        primary = {"evt:MS1": {"match_name": "A - B", "market": "MS1", "sharp_odds": 1.9}}
        self.assertIs(_merge_sharp_sources(primary, {}, label="matchbook"), primary)


if __name__ == "__main__":
    unittest.main()
