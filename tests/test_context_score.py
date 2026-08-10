from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.context_features import extract_context_features
from core.context_score import (
    OVERCONFIDENCE_PROB_GAP,
    score_context_for_market,
    score_home_bias,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "context"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _build_features() -> object:
    payload = _load_fixture("tur_super_lig_gs_fb.json")
    return extract_context_features(
        payload["match"],
        standings=payload["standings"],
        home_form=payload["home_form"],
        away_form=payload["away_form"],
        h2h=payload["h2h"],
        injuries=payload["injuries"],
    )


class ContextScoreDirectionTests(unittest.TestCase):
    def test_home_bias_positive_for_gs_fb_fixture(self) -> None:
        features = _build_features()
        score, components = score_home_bias(features)
        self.assertGreater(score, 0.0)
        self.assertIn("rank", components)

    def test_ms1_aligned_with_home_bias(self) -> None:
        features = _build_features()
        home_bias, _ = score_home_bias(features)
        ms1 = score_context_for_market(features, "MS1")
        self.assertGreater(ms1.market_aligned_score, 0.0)
        self.assertEqual(ms1.market_aligned_score, home_bias)

    def test_ms2_opposes_home_bias(self) -> None:
        features = _build_features()
        home_bias, _ = score_home_bias(features)
        ms2 = score_context_for_market(features, "MS2")
        self.assertLess(ms2.market_aligned_score, 0.0)
        self.assertAlmostEqual(ms2.market_aligned_score, -home_bias, places=4)

    def test_x_market_prefers_balanced_context(self) -> None:
        features = _build_features()
        x_result = score_context_for_market(features, "X")
        self.assertGreater(x_result.market_aligned_score, 0.0)


class ContextScoreOverconfidenceTests(unittest.TestCase):
    def test_no_overconfidence_when_sharp_near_context(self) -> None:
        features = _build_features()
        result = score_context_for_market(features, "MS1", sharp_odds=2.05)
        self.assertFalse(result.overconfidence)
        self.assertIsNotNone(result.prob_gap)
        assert result.prob_gap is not None
        self.assertLessEqual(result.prob_gap, OVERCONFIDENCE_PROB_GAP)

    def test_overconfidence_dampens_when_gap_exceeds_threshold(self) -> None:
        features = _build_features()
        result = score_context_for_market(features, "MS1", sharp_odds=5.0)
        self.assertTrue(result.overconfidence)
        self.assertIsNotNone(result.prob_gap)
        assert result.prob_gap is not None
        self.assertGreater(result.prob_gap, OVERCONFIDENCE_PROB_GAP)
        self.assertLess(abs(result.final_market_score), abs(result.market_aligned_score))

    def test_invalid_market_rejected(self) -> None:
        features = _build_features()
        with self.assertRaises(Exception):
            score_context_for_market(features, "ALT")


if __name__ == "__main__":
    unittest.main()
