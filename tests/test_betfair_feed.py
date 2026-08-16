"""Betfair Exchange (gecikmeli anahtar) kaynagi testleri."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

from scrapers import betfair_feed, live_feed_gateway


def _kickoff_iso(hours_ahead: float) -> str:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _catalogue_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "marketId": "1.24",
        "marketName": "Match Odds",
        "marketStartTime": _kickoff_iso(4.0),
        "description": {"marketType": "MATCH_ODDS"},
        "event": {"id": "3300", "name": "Fenerbahce v Galatasaray"},
        "competition": {"name": "Turkish Super Lig"},
        "runners": [
            {"selectionId": 1, "runnerName": "Fenerbahce", "sortPriority": 1},
            {"selectionId": 2, "runnerName": "Galatasaray", "sortPriority": 2},
            {"selectionId": 3, "runnerName": "The Draw", "sortPriority": 3},
        ],
    }
    item.update(overrides)
    return item


def _runner_book(selection_id: int, back: float, lay: float) -> dict[str, Any]:
    return {
        "selectionId": selection_id,
        "status": "ACTIVE",
        "ex": {
            "availableToBack": [{"price": back, "size": 100.0}],
            "availableToLay": [{"price": lay, "size": 100.0}],
        },
    }


def _book(**overrides: Any) -> dict[str, Any]:
    book: dict[str, Any] = {
        "marketId": "1.24",
        "status": "OPEN",
        "inplay": False,
        "totalMatched": 25_000.0,
        "runners": [
            _runner_book(1, 2.00, 2.02),
            _runner_book(2, 4.00, 4.10),
            _runner_book(3, 3.50, 3.55),
        ],
    }
    book.update(overrides)
    return book


class BetfairNormalizationTests(unittest.TestCase):
    def test_match_odds_are_mapped_to_local_market_labels(self) -> None:
        normalized = betfair_feed._normalize_catalogue(
            [_catalogue_item()], {"1.24": _book()}, observed_at=1000.0
        )

        self.assertEqual(
            sorted(normalized),
            ["3300:MS1", "3300:MS2", "3300:X"],
        )
        entry = normalized["3300:MS1"]
        self.assertEqual(entry["match_name"], "Fenerbahce - Galatasaray")
        self.assertEqual(entry["consensus_source"], "exchange_consensus")
        self.assertEqual(entry["sport_key"], "betfair_exchange")
        self.assertEqual(entry["league_name"], "Turkish Super Lig")
        # Back 2.00 / lay 2.02 -> olasilik uzayinda orta nokta.
        self.assertAlmostEqual(float(entry["sharp_odds"]), 2.01, places=2)

    def test_over_under_runners_become_totals_markets(self) -> None:
        item = _catalogue_item(
            marketId="1.25",
            marketName="Over/Under 2.5 Goals",
            description={"marketType": "OVER_UNDER_25"},
            runners=[
                {"selectionId": 7, "runnerName": "Over 2.5 Goals", "sortPriority": 1},
                {"selectionId": 8, "runnerName": "Under 2.5 Goals", "sortPriority": 2},
            ],
        )
        book = _book(
            marketId="1.25",
            runners=[_runner_book(7, 1.90, 1.92), _runner_book(8, 2.00, 2.02)],
        )

        normalized = betfair_feed._normalize_catalogue([item], {"1.25": book}, observed_at=1.0)

        self.assertEqual(sorted(normalized), ["3300:ALT 2.5", "3300:UST 2.5"])

    def test_illiquid_or_inplay_markets_are_dropped(self) -> None:
        thin = betfair_feed._normalize_catalogue(
            [_catalogue_item()], {"1.24": _book(totalMatched=10.0)}, observed_at=1.0
        )
        inplay = betfair_feed._normalize_catalogue(
            [_catalogue_item()], {"1.24": _book(inplay=True)}, observed_at=1.0
        )
        started = betfair_feed._normalize_catalogue(
            [_catalogue_item(marketStartTime=_kickoff_iso(-1.0))],
            {"1.24": _book()},
            observed_at=1.0,
        )

        self.assertEqual(thin, {})
        self.assertEqual(inplay, {})
        self.assertEqual(started, {})

    def test_wide_spread_price_is_rejected(self) -> None:
        book = _book(runners=[_runner_book(1, 2.00, 3.00)])
        normalized = betfair_feed._normalize_catalogue(
            [_catalogue_item()], {"1.24": book}, observed_at=1.0
        )
        self.assertEqual(normalized, {})

    def test_disabled_source_returns_empty_without_network(self) -> None:
        with mock.patch.object(betfair_feed, "BETFAIR_APP_KEY", ""), \
                mock.patch.object(
                    betfair_feed,
                    "_betting_call",
                    side_effect=AssertionError("kapali kaynak istek atti"),
                ):
            self.assertEqual(betfair_feed.get_betfair_sharp_odds(), {})


class SharpSourceMergeTests(unittest.TestCase):
    def test_betfair_fills_gaps_but_never_overrides_pinnacle(self) -> None:
        primary = {
            "evt:MS1": {
                "match_name": "Fenerbahce - Galatasaray",
                "market": "MS1",
                "sharp_odds": 2.05,
                "consensus_source": "pinnacle",
            }
        }
        secondary = {
            "3300:MS1": {
                "match_name": "Fenerbahce - Galatasaray",
                "market": "MS1",
                "sharp_odds": 2.01,
                "consensus_source": "exchange_consensus",
            },
            "3300:MS2": {
                "match_name": "Fenerbahce - Galatasaray",
                "market": "MS2",
                "sharp_odds": 4.05,
                "consensus_source": "exchange_consensus",
            },
        }

        merged = live_feed_gateway._merge_sharp_sources(primary, secondary)

        self.assertEqual(sorted(merged), ["betfair:3300:MS2", "evt:MS1"])
        self.assertEqual(merged["evt:MS1"]["sharp_odds"], 2.05)

    def test_empty_secondary_returns_primary_unchanged(self) -> None:
        primary = {"evt:MS1": {"match_name": "A - B", "market": "MS1", "sharp_odds": 2.0}}
        self.assertIs(live_feed_gateway._merge_sharp_sources(primary, {}), primary)


if __name__ == "__main__":
    unittest.main()
