from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from core.context_features import ContextFeatures

__all__ = (
    "ContextScoreError",
    "ContextScoreResult",
    "OVERCONFIDENCE_PROB_GAP",
    "score_context_for_market",
    "score_home_bias",
)

_OVERCONFIDENCE_PROB_GAP = 0.08
_MAX_ABS_SCORE = 100.0
_HOME_NEUTRAL_PROB = 0.5

# Theory-lab default agirliklar; ROI icin kalibre edilmedi (Adim 6 backtest gerekir).
_WEIGHT_RANK = 8.0
_WEIGHT_PPG = 15.0
_WEIGHT_FORM = 12.0
_WEIGHT_H2H = 20.0
_WEIGHT_INJURY = 3.0

_H2H_MARKETS = frozenset({"MS1", "MS2", "X"})


class ContextScoreError(ValueError):
    """Gecersiz market veya yetersiz baglam."""


@dataclass(frozen=True)
class ContextScoreResult:
    market: str
    home_bias_score: float
    market_aligned_score: float
    final_market_score: float
    overconfidence: bool
    sharp_implied_prob: float | None
    context_implied_prob: float | None
    prob_gap: float | None
    data_completeness: float
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "home_bias_score": self.home_bias_score,
            "market_aligned_score": self.market_aligned_score,
            "final_market_score": self.final_market_score,
            "overconfidence": self.overconfidence,
            "sharp_implied_prob": self.sharp_implied_prob,
            "context_implied_prob": self.context_implied_prob,
            "prob_gap": self.prob_gap,
            "data_completeness": self.data_completeness,
            "components": dict(self.components),
        }


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _safe_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _component_rank(features: ContextFeatures) -> float | None:
    if features.rank_diff is None:
        return None
    # rank_diff = home_rank - away_rank; negatif deger ev sahibi lider.
    return -float(features.rank_diff) * _WEIGHT_RANK


def _component_ppg(features: ContextFeatures) -> float | None:
    delta = _safe_delta(features.home_points_per_game, features.away_points_per_game)
    if delta is None:
        return None
    return delta * _WEIGHT_PPG


def _component_form(features: ContextFeatures) -> float | None:
    delta = _safe_delta(features.home_form_ppg, features.away_form_ppg)
    if delta is None:
        return None
    return delta * _WEIGHT_FORM


def _component_h2h(features: ContextFeatures) -> float | None:
    if features.h2h_home_win_rate is None:
        return None
    return (float(features.h2h_home_win_rate) - _HOME_NEUTRAL_PROB) * _WEIGHT_H2H


def _component_injury(features: ContextFeatures) -> float:
    return float(features.away_injury_count - features.home_injury_count) * _WEIGHT_INJURY


def score_home_bias(features: ContextFeatures) -> tuple[float, dict[str, float]]:
    """Ev sahibi lehine pozitif, deplasman lehine negatif baglam skoru."""
    components: dict[str, float] = {}

    rank = _component_rank(features)
    if rank is not None:
        components["rank"] = round(rank, 4)

    ppg = _component_ppg(features)
    if ppg is not None:
        components["ppg"] = round(ppg, 4)

    form = _component_form(features)
    if form is not None:
        components["form"] = round(form, 4)

    h2h = _component_h2h(features)
    if h2h is not None:
        components["h2h"] = round(h2h, 4)

    injury = _component_injury(features)
    if injury != 0.0:
        components["injury"] = round(injury, 4)

    raw_total = sum(components.values())
    completeness = _clamp(float(features.data_completeness), 0.0, 1.0)
    scaled = raw_total * completeness if completeness > 0.0 else 0.0
    final_score = round(_clamp(scaled, -_MAX_ABS_SCORE, _MAX_ABS_SCORE), 4)
    return final_score, components


def _home_win_prob_from_bias(home_bias_score: float) -> float:
    """Baglam skorunu olasiliga map eder; kalibre edilmemis theory-lab tahmini."""
    return _clamp(_HOME_NEUTRAL_PROB + (home_bias_score / 200.0), 0.05, 0.95)


def _market_context_prob(home_bias_score: float, market: str) -> float:
    market_key = market.strip().upper()
    home_prob = _home_win_prob_from_bias(home_bias_score)
    if market_key == "MS1":
        return home_prob
    if market_key == "MS2":
        return 1.0 - home_prob
    if market_key == "X":
        closeness = 1.0 - min(abs(home_bias_score) / _MAX_ABS_SCORE, 1.0)
        return _clamp(0.18 + (0.20 * closeness), 0.05, 0.45)
    raise ContextScoreError(f"desteklenmeyen market: {market}")


def _market_aligned_score(home_bias_score: float, market: str) -> float:
    market_key = market.strip().upper()
    if market_key == "MS1":
        return home_bias_score
    if market_key == "MS2":
        return -home_bias_score
    if market_key == "X":
        closeness = 1.0 - min(abs(home_bias_score) / _MAX_ABS_SCORE, 1.0)
        return round(closeness * _MAX_ABS_SCORE, 4)
    raise ContextScoreError(f"desteklenmeyen market: {market}")


def _sharp_implied_prob(sharp_odds: float) -> float | None:
    if isinstance(sharp_odds, bool) or not isinstance(sharp_odds, (int, float)):
        return None
    odds = float(sharp_odds)
    if odds <= 1.0 or not math.isfinite(odds):
        return None
    return 1.0 / odds


def _apply_overconfidence_dampening(
    market_aligned_score: float,
    *,
    context_prob: float,
    sharp_prob: float,
) -> tuple[float, bool, float]:
    gap = abs(context_prob - sharp_prob)
    if gap <= _OVERCONFIDENCE_PROB_GAP:
        return market_aligned_score, False, round(gap, 6)

    excess = gap - _OVERCONFIDENCE_PROB_GAP
    damp_factor = _clamp(1.0 - (excess / 0.20), 0.50, 1.0)
    damped = round(market_aligned_score * damp_factor, 4)
    return damped, True, round(gap, 6)


def score_context_for_market(
    features: ContextFeatures,
    market: str,
    *,
    sharp_odds: float | None = None,
) -> ContextScoreResult:
    market_key = market.strip().upper()
    if market_key not in _H2H_MARKETS:
        raise ContextScoreError(f"desteklenmeyen market: {market}")

    home_bias_score, components = score_home_bias(features)
    market_aligned = _market_aligned_score(home_bias_score, market_key)
    context_prob = _market_context_prob(home_bias_score, market_key)

    sharp_prob = _sharp_implied_prob(sharp_odds) if sharp_odds is not None else None
    overconfidence = False
    prob_gap: float | None = None
    final_market_score = market_aligned

    if sharp_prob is not None:
        final_market_score, overconfidence, prob_gap = _apply_overconfidence_dampening(
            market_aligned,
            context_prob=context_prob,
            sharp_prob=sharp_prob,
        )

    return ContextScoreResult(
        market=market_key,
        home_bias_score=home_bias_score,
        market_aligned_score=round(market_aligned, 4),
        final_market_score=final_market_score,
        overconfidence=overconfidence,
        sharp_implied_prob=round(sharp_prob, 6) if sharp_prob is not None else None,
        context_implied_prob=round(context_prob, 6),
        prob_gap=prob_gap,
        data_completeness=float(features.data_completeness),
        components=components,
    )


# Public alias for plan docs / tests
OVERCONFIDENCE_PROB_GAP = _OVERCONFIDENCE_PROB_GAP
