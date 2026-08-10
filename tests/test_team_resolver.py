from __future__ import annotations

import unittest

from core.team_resolver import (
    find_fixture_by_team_ids,
    get_team_alias_stats,
    resolve_match_teams,
    resolve_team_name,
)


class TeamResolverSuperLigTests(unittest.TestCase):
    def test_resolves_gs_alias_to_galatasaray(self) -> None:
        resolved = resolve_team_name("GS", sport_key="soccer_turkey_super_league")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 645)
        self.assertEqual(resolved.canonical_key, "galatasaray")
        self.assertEqual(resolved.match_method, "alias")

    def test_resolves_fb_alias_to_fenerbahce(self) -> None:
        resolved = resolve_team_name("FB", sport_key="soccer_turkey_super_league")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 611)
        self.assertEqual(resolved.canonical_key, "fenerbahce")

    def test_resolves_bjk_and_trabzon_aliases(self) -> None:
        bjk = resolve_team_name("BJK", sport_key="soccer_turkey_super_league")
        ts = resolve_team_name("Trabzon", sport_key="soccer_turkey_super_league")
        self.assertIsNotNone(bjk)
        self.assertIsNotNone(ts)
        assert bjk is not None and ts is not None
        self.assertEqual(bjk.team_id, 549)
        self.assertEqual(ts.team_id, 998)

    def test_wrong_sport_key_does_not_cross_match(self) -> None:
        resolved = resolve_team_name("GS", sport_key="soccer_epl")
        self.assertIsNone(resolved)

    def test_resolve_match_teams_pair(self) -> None:
        pair = resolve_match_teams(
            "Galatasaray",
            "Fenerbahce",
            sport_key="soccer_turkey_super_league",
        )
        self.assertIsNotNone(pair)
        assert pair is not None
        home, away = pair
        self.assertEqual(home.team_id, 645)
        self.assertEqual(away.team_id, 611)


class TeamResolverInternationalTests(unittest.TestCase):
    def test_resolves_turkiye_with_turkish_chars(self) -> None:
        resolved = resolve_team_name("Türkiye", sport_key="international")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 777)
        self.assertEqual(resolved.display_name, "Turkey")

    def test_resolves_turkiye_for_world_cup_sport_key(self) -> None:
        resolved = resolve_team_name("Turkiye", sport_key="soccer_fifa_world_cup")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 777)

    def test_resolves_germany_international_alias(self) -> None:
        resolved = resolve_team_name("Almanya", sport_key="international")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 25)


class TeamResolverFixtureLookupTests(unittest.TestCase):
    def test_find_fixture_by_team_ids_direct_and_swapped(self) -> None:
        fixtures = [
            {
                "fixture": {"id": 900001},
                "teams": {
                    "home": {"id": 645, "name": "Galatasaray"},
                    "away": {"id": 611, "name": "Fenerbahce"},
                },
            }
        ]
        direct = find_fixture_by_team_ids(fixtures, home_team_id=645, away_team_id=611)
        swapped = find_fixture_by_team_ids(fixtures, home_team_id=611, away_team_id=645)
        self.assertIsNotNone(direct)
        self.assertIsNotNone(swapped)
        self.assertEqual(direct["fixture"]["id"], 900001)

    def test_alias_stats_loaded(self) -> None:
        stats = get_team_alias_stats()
        self.assertGreaterEqual(int(stats.get("alias_entries", 0)), 8)
        self.assertGreaterEqual(int(stats.get("teams", 0)), 6)


if __name__ == "__main__":
    unittest.main()
