from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.context_features import (
    ContextFeatureError,
    extract_context_features,
    form_string_to_ppg,
    normalize_team_key,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "context"


def _load_fixture(name: str) -> dict:
    path = _FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


class ContextFeaturesRealMatchTests(unittest.TestCase):
    def test_extract_features_from_super_lig_fixture(self) -> None:
        payload = _load_fixture("tur_super_lig_gs_fb.json")
        features = extract_context_features(
            payload["match"],
            standings=payload["standings"],
            home_form=payload["home_form"],
            away_form=payload["away_form"],
            h2h=payload["h2h"],
            injuries=payload["injuries"],
        )
        expected = payload["expected"]

        self.assertEqual(features.home_team_key, "galatasaray")
        self.assertEqual(features.away_team_key, "fenerbahce")
        self.assertEqual(features.home_rank, expected["home_rank"])
        self.assertEqual(features.away_rank, expected["away_rank"])
        self.assertEqual(features.rank_diff, expected["rank_diff"])
        self.assertAlmostEqual(features.home_points_per_game, expected["home_points_per_game"], places=4)
        self.assertAlmostEqual(features.away_points_per_game, expected["away_points_per_game"], places=4)
        self.assertAlmostEqual(features.home_form_ppg, expected["home_form_ppg"], places=4)
        self.assertAlmostEqual(features.away_form_ppg, expected["away_form_ppg"], places=4)
        self.assertAlmostEqual(features.h2h_home_win_rate, expected["h2h_home_win_rate"], places=4)
        self.assertAlmostEqual(features.h2h_avg_total_goals, expected["h2h_avg_total_goals"], places=4)
        self.assertEqual(features.home_injury_count, expected["home_injury_count"])
        self.assertEqual(features.away_injury_count, expected["away_injury_count"])
        self.assertGreater(features.data_completeness, 0.5)

    def test_rejects_virtual_match(self) -> None:
        payload = _load_fixture("tur_super_lig_gs_fb.json")
        virtual_match = {
            **payload["match"],
            "match_name": "E-Futbol | Galatasaray - Fenerbahce",
        }
        with self.assertRaises(ContextFeatureError):
            extract_context_features(virtual_match, standings=payload["standings"])

    def test_rejects_suspicious_match(self) -> None:
        payload = _load_fixture("tur_super_lig_gs_fb.json")
        suspicious_match = {
            **payload["match"],
            "match_quality": "suspicious",
            "soft_odds": 11.9,
            "sharp_odds": 1.32,
        }
        with self.assertRaises(ContextFeatureError):
            extract_context_features(suspicious_match, standings=payload["standings"])

    def test_form_string_to_ppg(self) -> None:
        self.assertAlmostEqual(form_string_to_ppg("WWDLW"), 2.0, places=4)
        self.assertIsNone(form_string_to_ppg(""))

    def test_normalize_team_key_handles_turkish_chars(self) -> None:
        self.assertEqual(normalize_team_key("Fenerbahçe"), "fenerbahce")


if __name__ == "__main__":
    unittest.main()
