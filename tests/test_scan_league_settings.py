from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import scan_league_settings
from core.scan_league_settings import (
    SHARP_LEAGUE_CATALOG,
    build_scan_leagues_payload,
    select_rotated_sharp_sport_keys,
    update_scan_league_settings,
)


class ScanLeagueSettingsTests(unittest.TestCase):
    """Lig ayarlari: canli database/scan_leagues.json'a DOKUNMAZ.

    Kayit yolu gecici klasore yonlendirilir; modul bellegindeki canli secim
    de test bitiminde aynen geri konur (baska testler etkilenmesin).
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        persist_path = Path(tmp.name) / "scan_leagues.json"
        patcher = mock.patch.object(scan_league_settings, "_PERSIST_PATH", persist_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        saved_per_scan = scan_league_settings._STATE["leagues_per_scan"]
        saved_keys = list(scan_league_settings._STATE["enabled_sport_keys"])
        saved_auto = scan_league_settings._AUTO_SEASON

        def _restore_module_state() -> None:
            scan_league_settings._STATE["leagues_per_scan"] = saved_per_scan
            scan_league_settings._STATE["enabled_sport_keys"] = list(saved_keys)
            scan_league_settings._AUTO_SEASON = saved_auto

        self.addCleanup(_restore_module_state)

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
