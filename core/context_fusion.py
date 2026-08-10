from __future__ import annotations

from typing import Any

from core.operator_risk_settings import get_context_mode, is_context_info_enabled
from core.context_features import ContextFeatureError, ContextFeatures, extract_context_features
from core.context_score import score_context_for_market
from core.market_catalog import FAMILY_MATCH_RESULT, market_family
from core.passion_engine import is_ev_actionable

__all__ = (
    "CONTEXT_FUSION_MODE_FILTER",
    "CONTEXT_FUSION_MODE_OFF",
    "ContextFusionDecision",
    "apply_action_tier_context_fusion",
    "is_context_fusion_filter_enabled",
    "is_context_info_enabled",
    "passes_context_fusion_for_action",
    "resolve_context_features_from_match",
)

CONTEXT_FUSION_MODE_OFF = "off"
CONTEXT_FUSION_MODE_INFO = "info"
CONTEXT_FUSION_MODE_FILTER = "filter"
_CONTEXT_MIN_ALIGNED_SCORE = 0.0
_CONTEXT_BUNDLE_KEY = "context_bundle"


class ContextFusionDecision:
    __slots__ = (
        "applied",
        "passed",
        "reason",
        "final_market_score",
        "overconfidence",
    )

    def __init__(
        self,
        *,
        applied: bool,
        passed: bool,
        reason: str,
        final_market_score: float | None = None,
        overconfidence: bool | None = None,
    ) -> None:
        self.applied = applied
        self.passed = passed
        self.reason = reason
        self.final_market_score = final_market_score
        self.overconfidence = overconfidence


def is_context_fusion_filter_enabled() -> bool:
    return get_context_mode() == CONTEXT_FUSION_MODE_FILTER


def resolve_context_features_from_match(match: dict[str, Any]) -> ContextFeatures | None:
    bundle = match.get(_CONTEXT_BUNDLE_KEY)
    if not isinstance(bundle, dict):
        return None
    try:
        return extract_context_features(
            match,
            standings=bundle.get("standings"),
            home_form=bundle.get("home_form"),
            away_form=bundle.get("away_form"),
            h2h=bundle.get("h2h"),
            injuries=bundle.get("injuries"),
        )
    except ContextFeatureError:
        return None


def passes_context_fusion_for_action(
    match: dict[str, Any],
    *,
    market: str,
    sharp_odds: float,
    soft_odds: float,
    consensus_books: int,
) -> ContextFusionDecision:
    if not is_context_fusion_filter_enabled():
        return ContextFusionDecision(applied=False, passed=True, reason="fusion_kapali")

    # Form/baglam modeli yalnizca mac sonucu pazarlarini taniyor; alt/ust
    # gibi pazarlarda desteklenmeyen-market hatasina dusmeden notr gec.
    if market_family(market) != FAMILY_MATCH_RESULT:
        return ContextFusionDecision(applied=False, passed=True, reason="baglam_kapsam_disi")

    features = resolve_context_features_from_match(match)
    if features is None:
        return ContextFusionDecision(applied=False, passed=True, reason="baglam_verisi_yok")

    ev = (1.0 / sharp_odds) * soft_odds - 1.0 if sharp_odds > 1.0 else 0.0
    if not is_ev_actionable(ev, consensus_books=consensus_books):
        return ContextFusionDecision(applied=False, passed=True, reason="ev_actionable_degil")

    score = score_context_for_market(features, market, sharp_odds=sharp_odds)
    if score.final_market_score <= _CONTEXT_MIN_ALIGNED_SCORE:
        return ContextFusionDecision(
            applied=True,
            passed=False,
            reason="baglam_market_yonune_ters",
            final_market_score=score.final_market_score,
            overconfidence=score.overconfidence,
        )

    return ContextFusionDecision(
        applied=True,
        passed=True,
        reason="baglam_uyumlu",
        final_market_score=score.final_market_score,
        overconfidence=score.overconfidence,
    )


def apply_action_tier_context_fusion(
    tier: str | None,
    match: dict[str, Any],
    *,
    market: str,
    sharp_odds: float,
    soft_odds: float,
    consensus_books: int,
) -> tuple[str | None, ContextFusionDecision]:
    if tier not in ("ACTION", "HIGH"):
        return tier, ContextFusionDecision(applied=False, passed=True, reason="watch_katmani")

    decision = passes_context_fusion_for_action(
        match,
        market=market,
        sharp_odds=sharp_odds,
        soft_odds=soft_odds,
        consensus_books=consensus_books,
    )
    if decision.applied and not decision.passed:
        return None, decision
    return tier, decision
