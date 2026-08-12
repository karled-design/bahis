from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from core.context_fusion import (
    apply_action_tier_context_fusion,
    passes_context_fusion_for_action,
)
from core.scan_pipeline import evaluate_matches

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "context" / "tur_super_lig_gs_fb.json"


class ContextFusionTests(unittest.TestCase):
    def _positive_bundle_match(self) -> dict:
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        match = dict(payload["match"])
        match.update(
            {
                "market": "MS1",
                "sharp_odds": 2.10,
                "soft_odds": 2.26,
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

    def test_positive_context_passes_action_fusion(self) -> None:
        match = self._positive_bundle_match()
        decision = passes_context_fusion_for_action(
            match,
            market="MS1",
            sharp_odds=2.10,
            soft_odds=2.26,
            consensus_books=3,
        )
        self.assertTrue(decision.applied)
        self.assertTrue(decision.passed)

    def test_negative_context_blocks_action_fusion(self) -> None:
        match = self._positive_bundle_match()
        match["context_bundle"]["standings"]["teams"] = [
            {
                "team_key": "galatasaray",
                "team_name": "Galatasaray",
                "rank": 6,
                "points": 30,
                "played": 20,
                "goals_for": 28,
                "goals_against": 26,
                "form": "LLWDL",
            },
            {
                "team_key": "fenerbahce",
                "team_name": "Fenerbahce",
                "rank": 1,
                "points": 50,
                "played": 20,
                "goals_for": 48,
                "goals_against": 18,
                "form": "WWWWW",
            },
        ]
        decision = passes_context_fusion_for_action(
            match,
            market="MS1",
            sharp_odds=2.10,
            soft_odds=2.26,
            consensus_books=3,
        )
        self.assertTrue(decision.applied)
        self.assertFalse(decision.passed)

    def test_no_context_bundle_keeps_ev_only_path(self) -> None:
        match = self._positive_bundle_match()
        match.pop("context_bundle", None)
        decision = passes_context_fusion_for_action(
            match,
            market="MS1",
            sharp_odds=2.10,
            soft_odds=2.26,
            consensus_books=3,
        )
        self.assertFalse(decision.applied)
        self.assertTrue(decision.passed)


class ScanPipelineFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._hero_patch = mock.patch("core.scan_pipeline.is_hero_mode_enabled", return_value=False)
        self._hero_patch.start()

    def tearDown(self) -> None:
        self._hero_patch.stop()

    def test_pipeline_filters_action_when_context_opposes(self) -> None:
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        match = {
            "match_name": "Galatasaray - Fenerbahce",
            "market": "MS1",
            "sharp_odds": 2.10,
            "soft_odds": 2.26,
            "consensus_books": 3,
            "consensus_source": "pinnacle",
            "sport_key": "soccer_turkey_super_league",
            "event_id": "fusion-test-001",
            "match_quality": "ok",
            "context_bundle": {
                "standings": {
                    "teams": [
                        {
                            "team_key": "galatasaray",
                            "team_name": "Galatasaray",
                            "rank": 6,
                            "points": 30,
                            "played": 20,
                            "goals_for": 28,
                            "goals_against": 26,
                            "form": "LLWDL",
                        },
                        {
                            "team_key": "fenerbahce",
                            "team_name": "Fenerbahce",
                            "rank": 1,
                            "points": 50,
                            "played": 20,
                            "goals_for": 48,
                            "goals_against": 18,
                            "form": "WWWWW",
                        },
                    ]
                }
            },
        }
        candidates, stats = evaluate_matches([match], current_kasa=1000.0, skip_freshness=True)
        self.assertEqual(len(candidates), 0)
        self.assertEqual(stats.context_filter, 1)

    def test_pipeline_keeps_action_without_context_bundle(self) -> None:
        match = {
            "match_name": "Galatasaray - Fenerbahce",
            "market": "MS1",
            "sharp_odds": 2.10,
            "soft_odds": 2.26,
            "consensus_books": 3,
            "consensus_source": "pinnacle",
            "sport_key": "soccer_turkey_super_league",
            "event_id": "fusion-test-002",
            "match_quality": "ok",
        }
        candidates, stats = evaluate_matches([match], current_kasa=1000.0, skip_freshness=True)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(stats.context_filter, 0)

    def test_fusion_off_preserves_action_even_with_bad_context(self) -> None:
        original = settings.CONTEXT_FUSION_MODE
        settings.CONTEXT_FUSION_MODE = "off"
        try:
            payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
            match = {
                "match_name": "Galatasaray - Fenerbahce",
                "market": "MS1",
                "sharp_odds": 2.10,
                "soft_odds": 2.26,
                "consensus_books": 3,
                "consensus_source": "pinnacle",
                "sport_key": "soccer_turkey_super_league",
                "event_id": "fusion-test-003",
                "match_quality": "ok",
                "context_bundle": payload,
            }
            tier, _ = apply_action_tier_context_fusion(
                "ACTION",
                match,
                market="MS1",
                sharp_odds=2.10,
                soft_odds=2.26,
                consensus_books=3,
            )
            self.assertEqual(tier, "ACTION")
        finally:
            settings.CONTEXT_FUSION_MODE = original


if __name__ == "__main__":
    unittest.main()
