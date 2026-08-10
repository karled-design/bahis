from __future__ import annotations

import unittest
from datetime import datetime, timezone

from auto_settler import collect_pending_sport_keys


class AutoSettlerTests(unittest.TestCase):
    def test_fixture_names_match_for_settlement_rejects_wrong_teams(self) -> None:
        from auto_settler import fixture_names_match_for_settlement

        self.assertFalse(
            fixture_names_match_for_settlement(
                "DR Congo - Uzbekistan",
                "Uzbekistan - Colombia",
            )
        )
        self.assertFalse(
            fixture_names_match_for_settlement(
                "DR Congo - Uzbekistan",
                "Portugal - DR Congo",
            )
        )
        self.assertTrue(
            fixture_names_match_for_settlement(
                "DR Congo - Uzbekistan",
                "DR Congo - Uzbekistan",
            )
        )

    def test_collect_pending_sport_keys_from_kupons(self) -> None:
        keys = collect_pending_sport_keys(
            [
                {"sport_key": "soccer_fifa_world_cup"},
                {"sport_key": "soccer_turkey_super_league"},
            ]
        )
        self.assertIn("soccer_fifa_world_cup", keys)
        self.assertIn("soccer_turkey_super_league", keys)

    def test_collect_pending_sport_keys_fallback_when_empty(self) -> None:
        keys = collect_pending_sport_keys([{"sport_key": ""}])
        self.assertTrue(keys)
        self.assertIn("soccer_fifa_world_cup", keys)

    def test_is_zombie_kupon_respects_future_kickoff(self) -> None:
        from auto_settler import is_zombie_kupon

        now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        kupon = {
            "eklenme_tarihi": "2026-06-19T16:09:09+00:00",
            "commence_time": "2026-06-28T02:00:00Z",
        }
        self.assertFalse(is_zombie_kupon(kupon, reference_at=now))


if __name__ == "__main__":
    unittest.main()
