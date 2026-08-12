from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.hero_gate import (
    build_market_prob_index,
    evaluate_hero_gate,
    implied_win_probability,
)
from core.hero_profile import (
    DEFAULT_HERO_PROFILE_LEVEL,
    apply_hero_profile_level,
    bootstrap_hero_profile,
    get_active_hero_profile_values,
)
from core.scan_pipeline import evaluate_matches
from tests.live_state_isolation import isolate_module

isolate_module(globals())  # canli database dosyalari yerine gecici klasor


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "context" / "tur_super_lig_gs_fb.json"


class HeroProfileTests(unittest.TestCase):
    def test_default_level_is_aktif(self) -> None:
        bootstrap_hero_profile()
        self.assertEqual(get_active_hero_profile_values()["min_confidence"], 0.55)
        self.assertEqual(get_active_hero_profile_values()["max_daily_picks"], 8)

    def test_apply_level_changes_values(self) -> None:
        values = apply_hero_profile_level("5")
        self.assertEqual(values["min_confidence"], 0.54)
        apply_hero_profile_level(DEFAULT_HERO_PROFILE_LEVEL)


class HeroGateTests(unittest.TestCase):
    def _profile(self) -> dict:
        return get_active_hero_profile_values()

    def _hero_match_bundle(self) -> dict:
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        match = dict(payload["match"])
        match.update(
            {
                "market": "MS1",
                "sharp_odds": 1.60,
                "soft_odds": 1.85,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
                "context_bundle": {
                    "standings": payload["standings"],
                    "home_form": payload["home_form"],
                    "away_form": payload["away_form"],
                    "h2h": payload["h2h"],
                    "injuries": payload["injuries"],
                },
            }
        )
        return match

    def test_implied_win_probability(self) -> None:
        self.assertAlmostEqual(implied_win_probability(1.60) or 0.0, 0.625, places=3)

    def test_high_confidence_and_market_superiority_passes(self) -> None:
        match = self._hero_match_bundle()
        matches = [
            match,
            {
                "match_name": match["match_name"],
                "market": "X",
                "sharp_odds": 3.50,
                "soft_odds": 3.40,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
            {
                "match_name": match["match_name"],
                "market": "MS2",
                "sharp_odds": 5.00,
                "soft_odds": 4.80,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
        ]
        prob_index = build_market_prob_index(matches)
        decision = evaluate_hero_gate(match, prob_index, self._profile())
        self.assertTrue(decision.passed)
        self.assertGreaterEqual(decision.win_probability or 0.0, 0.58)

    def test_low_confidence_rejected(self) -> None:
        match = self._hero_match_bundle()
        match["sharp_odds"] = 2.20
        match["soft_odds"] = 2.10
        prob_index = build_market_prob_index([match])
        decision = evaluate_hero_gate(match, prob_index, self._profile())
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "dusuk_guven")

    def test_net_superiority_rejected(self) -> None:
        match = self._hero_match_bundle()
        rival = {
            "match_name": match["match_name"],
            "market": "MS2",
            "sharp_odds": 1.58,
            "soft_odds": 1.80,
            "consensus_books": 3,
            "consensus_source": "pinnacle",
        }
        prob_index = build_market_prob_index([match, rival])
        decision = evaluate_hero_gate(match, prob_index, self._profile())
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "net_ustunluk_yetersiz")

    def test_closing_window_tightens_confidence(self) -> None:
        apply_hero_profile_level("3")
        match = self._hero_match_bundle()
        match["sharp_odds"] = 1.75
        match["soft_odds"] = 1.85
        match["commence_time"] = (
            datetime.now(timezone.utc) + timedelta(minutes=45)
        ).isoformat().replace("+00:00", "Z")
        prob_index = build_market_prob_index([match])
        decision = evaluate_hero_gate(match, prob_index, self._profile())
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "kapanis_esigi")

    def test_closing_window_passes_when_above_tightened_threshold(self) -> None:
        match = self._hero_match_bundle()
        match["commence_time"] = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat().replace("+00:00", "Z")
        matches = [
            match,
            {
                "match_name": match["match_name"],
                "market": "MS2",
                "sharp_odds": 4.50,
                "soft_odds": 4.20,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
        ]
        prob_index = build_market_prob_index(matches)
        decision = evaluate_hero_gate(match, prob_index, self._profile())
        self.assertTrue(decision.passed)


class HeroPipelineTests(unittest.TestCase):
    def test_hero_mode_pipeline_selects_favorite(self) -> None:
        matches = [
            {
                "match_name": "Hero A - Hero B",
                "market": "MS1",
                "sharp_odds": 1.55,
                "soft_odds": 1.70,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
            {
                "match_name": "Hero A - Hero B",
                "market": "MS2",
                "sharp_odds": 4.50,
                "soft_odds": 4.20,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
            {
                "match_name": "Hero C - Hero D",
                "market": "MS1",
                "sharp_odds": 2.40,
                "soft_odds": 2.35,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
            },
        ]
        with mock.patch("core.scan_pipeline.is_hero_mode_enabled", return_value=True):
            candidates, stats = evaluate_matches(
                matches,
                current_kasa=1000.0,
                skip_freshness=True,
            )
        self.assertEqual(stats.hero_pass, 1)
        self.assertEqual(stats.action, 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].match_name, "Hero A - Hero B")
        self.assertEqual(candidates[0].tier, "ACTION")
        self.assertEqual(candidates[0].stake, 50.0)
        self.assertAlmostEqual(candidates[0].hero_confidence or 0.0, 1.0 / 1.55, places=3)

    def test_ev_mode_unchanged_when_hero_off(self) -> None:
        matches = [
            {
                "match_name": "Test A - Test B",
                "market": "MS1",
                "sharp_odds": 2.0,
                "soft_odds": 2.06,
                "consensus_books": 2,
                "consensus_source": "pinnacle",
            },
        ]
        with mock.patch("core.scan_pipeline.is_hero_mode_enabled", return_value=False):
            candidates, stats = evaluate_matches(
                matches,
                current_kasa=1000.0,
                skip_freshness=True,
            )
        self.assertEqual(stats.hero_pass, 0)
        self.assertEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
