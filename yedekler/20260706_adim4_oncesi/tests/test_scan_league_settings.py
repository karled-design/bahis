from __future__ import annotations

import unittest

from core.scan_league_settings import (
    SHARP_LEAGUE_CATALOG,
    build_scan_leagues_payload,
    select_rotated_sharp_sport_keys,
    update_scan_league_settings,
)


class ScanLeagueSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        update_scan_league_settings(
            {
                "leagues_per_scan": 3,
                "enabled_sport_keys": [
                    "soccer_fifa_world_cup",
                    "soccer_turkey_super_league",
                    "soccer_epl",
                    "soccer_italy_serie_a",
                ],
            }
        )

    def test_default_payload_lists_all_catalog_leagues(self) -> None:
        update_scan_league_settings(
            {
                "leagues_per_scan": 3,
                "enabled_sport_keys": [entry["sport_key"] for entry in SHARP_LEAGUE_CATALOG],
            }
        )
        payload = build_scan_leagues_payload()
        self.assertEqual(payload["leagues_per_scan"], 3)
        self.assertEqual(len(payload["leagues"]), len(SHARP_LEAGUE_CATALOG))

    def test_select_rotated_includes_turkey_first(self) -> None:
        selected, _ = select_rotated_sharp_sport_keys(rotation_index=0)
        self.assertEqual(selected[0], "soccer_turkey_super_league")
        self.assertEqual(len(selected), 3)

    def test_cannot_disable_all_leagues(self) -> None:
        with self.assertRaises(ValueError):
            update_scan_league_settings({"enabled_sport_keys": []})


if __name__ == "__main__":
    unittest.main()
