"""Baslama saati dogrulamali mac eslestirme testleri (ag erisimi yok)."""

from __future__ import annotations

import unittest

from scrapers.live_feed_gateway import _find_fuzzy_sharp_entry
from scrapers.soft_feed import _extract_nesine_prematch_payload, _nesine_kickoff_iso


def _entry(match_name: str, commence_time: str = "") -> dict[str, str | float]:
    entry: dict[str, str | float] = {"match_name": match_name, "market": "MS1"}
    if commence_time:
        entry["commence_time"] = commence_time
    return entry


class KickoffVerifiedMatchingTests(unittest.TestCase):
    def test_short_name_matches_when_kickoff_is_identical(self) -> None:
        soft = _entry("Avai SC - Recife", "2026-08-19T22:00:00Z")
        sharp = [_entry("Avaí FC - Sport Club do Recife", "2026-08-19T22:00:00.000Z")]

        found = _find_fuzzy_sharp_entry("Avai SC - Recife", "MS1", sharp, set(), soft)

        self.assertIs(found, sharp[0])

    def test_same_names_different_kickoff_is_rejected(self) -> None:
        soft = _entry("Getafe - Racing Santander", "2026-08-19T22:00:00Z")
        sharp = [_entry("Getafe - Racing Santander", "2026-08-21T19:00:00.000Z")]

        found = _find_fuzzy_sharp_entry(
            "Getafe - Racing Santander", "MS1", sharp, set(), soft
        )

        self.assertIsNone(found)

    def test_short_name_stays_rejected_when_kickoff_is_unknown(self) -> None:
        sharp = [_entry("Avaí FC - Sport Club do Recife")]

        found = _find_fuzzy_sharp_entry(
            "Avai SC - Recife", "MS1", sharp, set(), _entry("Avai SC - Recife")
        )

        self.assertIsNone(found)

    def test_namesake_club_at_another_kickoff_is_rejected(self) -> None:
        soft = _entry("Arsenal - Chelsea", "2026-08-19T19:00:00Z")
        sharp = [_entry("Arsenal Tula - Chelsea Kiev", "2026-08-20T13:00:00.000Z")]

        found = _find_fuzzy_sharp_entry("Arsenal - Chelsea", "MS1", sharp, set(), soft)

        self.assertIsNone(found)

    def test_kickoff_within_tolerance_counts_as_same_match(self) -> None:
        soft = _entry("Atl Mineiro - Bragantino", "2026-08-19T22:00:00Z")
        sharp = [
            _entry(
                "Atlético Mineiro - Red Bull Bragantino",
                "2026-08-19T22:10:00.000Z",
            )
        ]

        found = _find_fuzzy_sharp_entry(
            "Atl Mineiro - Bragantino", "MS1", sharp, set(), soft
        )

        self.assertIs(found, sharp[0])

    def test_matching_kickoff_candidate_wins_over_different_kickoff(self) -> None:
        soft = _entry("Barcelona - Elche", "2026-08-19T19:00:00Z")
        sharp = [
            _entry("Barcelona - Elche", "2026-08-25T19:00:00.000Z"),
            _entry("FC Barcelona - Elche CF", "2026-08-19T19:00:00.000Z"),
        ]

        found = _find_fuzzy_sharp_entry("Barcelona - Elche", "MS1", sharp, set(), soft)

        self.assertIs(found, sharp[1])


class NesineKickoffTests(unittest.TestCase):
    def test_epoch_milliseconds_become_utc_iso(self) -> None:
        self.assertEqual(
            _nesine_kickoff_iso({"ESD": 1795824000000}), "2026-11-28T00:00:00Z"
        )

    def test_missing_or_invalid_kickoff_is_empty(self) -> None:
        self.assertEqual(_nesine_kickoff_iso({}), "")
        self.assertEqual(_nesine_kickoff_iso({"ESD": "28.11.2026"}), "")
        self.assertEqual(_nesine_kickoff_iso({"ESD": 0}), "")

    def test_prematch_records_carry_kickoff(self) -> None:
        payload = {
            "sg": {
                "EA": [
                    {
                        "C": 2948011,
                        "HN": "Galatasaray",
                        "AN": "Fenerbahce",
                        "ESD": 1795824000000,
                        "MA": [
                            {
                                "MST": 1,
                                "MTID": 1,
                                "ID": 7,
                                "OCA": [
                                    {"N": 1, "O": 2.10},
                                    {"N": 2, "O": 3.40},
                                    {"N": 3, "O": 3.20},
                                ],
                            }
                        ],
                    }
                ]
            }
        }

        records = _extract_nesine_prematch_payload(payload)

        self.assertEqual(len(records), 3)
        for record in records.values():
            self.assertEqual(record["commence_time"], "2026-11-28T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
