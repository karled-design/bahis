from __future__ import annotations

import unittest

from auto_settler import (
    SettlementIndexes,
    build_settlement_indexes,
    resolve_kupon_settlement_outcome,
)
from scrapers.sharp_feed import _parse_completed_scores_to_settlement


class SettlementEventIdTests(unittest.TestCase):
    def test_build_settlement_indexes_tracks_event_id(self) -> None:
        feed = [
            {
                "event_id": "evt-001",
                "match_name": "Turkey - USA",
                "market": "MS1",
                "outcome": "WON",
            }
        ]
        indexes = build_settlement_indexes(feed)
        self.assertEqual(indexes.by_event_id[("evt-001", "MS1")], "WON")
        self.assertEqual(
            indexes.by_match_name[("turkey - usa", "MS1")],
            "WON",
        )

    def test_resolve_prefers_event_id_over_name_mismatch(self) -> None:
        feed = [
            {
                "event_id": "evt-abc",
                "match_name": "Turkiye - United States",
                "market": "MS1",
                "outcome": "LOST",
            }
        ]
        indexes = build_settlement_indexes(feed)
        kupon = {
            "mac_adi": "Turkey - USA",
            "market": "MS1",
            "event_id": "evt-abc",
        }
        outcome, status, _, ratio = resolve_kupon_settlement_outcome(
            kupon,
            indexes,
            feed,
        )
        self.assertEqual(outcome, "LOST")
        self.assertEqual(status, "kazandi_kaybetti_event_id")
        self.assertEqual(ratio, 1.0)

    def test_resolve_falls_back_to_exact_name(self) -> None:
        feed = [
            {
                "event_id": "",
                "match_name": "Brazil - Morocco",
                "market": "MS2",
                "outcome": "WON",
            }
        ]
        indexes = build_settlement_indexes(feed)
        kupon = {
            "mac_adi": "Brazil - Morocco",
            "market": "MS2",
            "event_id": "",
        }
        outcome, status, _, ratio = resolve_kupon_settlement_outcome(
            kupon,
            indexes,
            feed,
        )
        self.assertEqual(outcome, "WON")
        self.assertEqual(status, "kazandi_kaybetti")
        self.assertEqual(ratio, 1.0)

    def test_parse_completed_scores_includes_event_id(self) -> None:
        events = [
            {
                "id": "681895f294b670b4c7b14495dfb583bc",
                "completed": True,
                "home_team": "Ecuador",
                "away_team": "Curacao",
                "scores": [
                    {"name": "Ecuador", "score": "2"},
                    {"name": "Curacao", "score": "1"},
                ],
            }
        ]
        parsed = _parse_completed_scores_to_settlement(events)
        ms1 = next(row for row in parsed if row["market"] == "MS1")
        self.assertEqual(ms1["event_id"], "681895f294b670b4c7b14495dfb583bc")
        self.assertEqual(ms1["outcome"], "WON")


if __name__ == "__main__":
    unittest.main()
