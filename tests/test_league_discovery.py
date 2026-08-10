from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import league_discovery
from core.scan_league_settings import select_rotated_sharp_sport_keys


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LeagueDiscoveryTests(unittest.TestCase):
    """Kesif katmani: canli dosyalara DOKUNMAZ, aga CIKMAZ (sahte cevaplar)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cache_path = Path(self._tmp.name) / "league_discovery.json"
        for target, value in (
            ("_CACHE_PATH", cache_path),
            ("_MEMORY_CACHE", None),
            ("_LAST_FAILURE_MONO", None),
        ):
            patcher = mock.patch.object(league_discovery, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.enabled = [
            "soccer_fifa_world_cup",
            "soccer_epl",
            "soccer_brazil_campeonato",
        ]
        patcher = mock.patch.object(
            league_discovery,
            "get_enabled_sharp_sport_keys",
            return_value=tuple(self.enabled),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _prime_cache(self) -> None:
        """Sahte API: Dunya Kupasi aktif+yakin mac, EPL sezon disi, Brezilya
        aktif ama maci 50 saat sonra (36 saatlik pencerenin disinda)."""
        now = datetime.now(timezone.utc)
        active = frozenset({"soccer_fifa_world_cup", "soccer_brazil_campeonato"})
        kickoffs = {
            "soccer_fifa_world_cup": [_iso(now + timedelta(hours=2))],
            "soccer_brazil_campeonato": [_iso(now + timedelta(hours=50))],
        }
        with (
            mock.patch.object(
                league_discovery, "_fetch_active_sport_keys", return_value=active
            ),
            mock.patch.object(
                league_discovery,
                "_fetch_league_kickoffs",
                side_effect=lambda key: kickoffs.get(key, []),
            ),
        ):
            league_discovery.refresh_league_discovery(force=True)

    def test_filter_drops_off_season_and_no_match_leagues(self) -> None:
        self._prime_cache()
        scannable, note = league_discovery.filter_scannable_sport_keys(list(self.enabled))
        self.assertEqual(scannable, ["soccer_fifa_world_cup"])
        self.assertIn("sezon_disi=soccer_epl", note)
        self.assertIn("mac_yok=soccer_brazil_campeonato", note)

    def test_fail_open_without_discovery_data(self) -> None:
        with mock.patch.object(
            league_discovery, "_fetch_active_sport_keys", return_value=None
        ):
            scannable, note = league_discovery.filter_scannable_sport_keys(
                list(self.enabled)
            )
        self.assertEqual(scannable, self.enabled)
        self.assertEqual(note, "kesif_verisi_yok")

    def test_unknown_league_stays_scannable(self) -> None:
        self._prime_cache()
        keys = list(self.enabled) + ["soccer_turkey_super_league"]
        scannable, _ = league_discovery.filter_scannable_sport_keys(keys)
        self.assertIn("soccer_turkey_super_league", scannable)

    def test_live_match_counts_as_match_in_window(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertTrue(
            league_discovery._has_match_in_window([_iso(now - timedelta(hours=1))])
        )
        self.assertFalse(
            league_discovery._has_match_in_window([_iso(now - timedelta(hours=9))])
        )

    def test_refresh_is_throttled_when_fresh(self) -> None:
        self._prime_cache()
        with mock.patch.object(
            league_discovery, "_fetch_active_sport_keys", return_value=frozenset()
        ) as fetcher:
            league_discovery.refresh_league_discovery()
            league_discovery.refresh_league_discovery()
        self.assertEqual(fetcher.call_count, 0)

    def test_failure_cooldown_blocks_immediate_retry(self) -> None:
        with mock.patch.object(
            league_discovery, "_fetch_active_sport_keys", return_value=None
        ) as fetcher:
            league_discovery.refresh_league_discovery()
            league_discovery.refresh_league_discovery()
        self.assertEqual(fetcher.call_count, 1)

    def test_annotate_marks_states_and_summary(self) -> None:
        self._prime_cache()
        payload = {
            "leagues": [
                {"sport_key": "soccer_fifa_world_cup", "enabled": True},
                {"sport_key": "soccer_epl", "enabled": True},
                {"sport_key": "soccer_brazil_campeonato", "enabled": True},
            ]
        }
        result = league_discovery.annotate_scan_leagues_payload(payload)
        states = {item["sport_key"]: item["season_state"] for item in result["leagues"]}
        self.assertEqual(states["soccer_fifa_world_cup"], "aktif")
        self.assertEqual(states["soccer_epl"], "sezon_disi")
        self.assertEqual(states["soccer_brazil_campeonato"], "mac_yok")
        self.assertIn("1 lig sezon disi", result["discovery_summary"])
        self.assertIn("1 ligde yakin pencerede mac yok", result["discovery_summary"])

    def test_annotate_without_data_says_unknown(self) -> None:
        payload = {"leagues": [{"sport_key": "soccer_epl", "enabled": True}]}
        result = league_discovery.annotate_scan_leagues_payload(payload)
        self.assertEqual(result["leagues"][0]["season_state"], "bilinmiyor")
        self.assertIn("henuz yok", result["discovery_summary"])

    def test_kickoff_helper_returns_sorted_datetimes(self) -> None:
        self._prime_cache()
        kickoffs = league_discovery.get_league_kickoffs("soccer_fifa_world_cup")
        self.assertEqual(len(kickoffs), 1)
        self.assertIsNotNone(kickoffs[0].tzinfo)

    def test_rotation_respects_candidates(self) -> None:
        with mock.patch(
            "core.scan_league_settings.get_leagues_per_scan", return_value=2
        ):
            selected, _ = select_rotated_sharp_sport_keys(
                rotation_index=0,
                candidates=["soccer_fifa_world_cup", "soccer_epl"],
            )
        self.assertEqual(len(selected), 2)
        self.assertNotIn("soccer_turkey_super_league", selected)
        for key in selected:
            self.assertIn(key, ["soccer_fifa_world_cup", "soccer_epl"])


if __name__ == "__main__":
    unittest.main()
