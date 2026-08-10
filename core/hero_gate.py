from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config.settings import MIN_CONSENSUS_BOOKMAKERS
from core.context_features import ContextFeatureError, extract_context_features
from core.context_fusion import resolve_context_features_from_match
from core.context_score import score_context_for_market
from core.hero_profile import HeroProfileValues
from core.market_catalog import FAMILY_MATCH_RESULT, market_family_group_key

__all__ = (
    "HeroGateDecision",
    "build_market_prob_index",
    "evaluate_hero_gate",
    "implied_win_probability",
)

_CONTEXT_MIN_ALIGNED_SCORE = 0.0
_CLOSING_ADVANTAGE = 0.01
_CLOSING_PREMATCH_SECONDS = 90 * 60
_CLOSING_LIVE_SECONDS = 2 * 3600


@dataclass(frozen=True)
class HeroGateDecision:
    passed: bool
    reason: str
    win_probability: float | None = None
    net_superiority_gap: float | None = None


def is_hero_mode_enabled() -> bool:
    from core.hero_mode import is_hero_mode_enabled as _runtime_enabled

    return _runtime_enabled()


def implied_win_probability(sharp_odds: float) -> float | None:
    if isinstance(sharp_odds, bool) or not isinstance(sharp_odds, (int, float)):
        return None
    numeric = float(sharp_odds)
    if not math.isfinite(numeric) or numeric <= 1.0:
        return None
    return 1.0 / numeric


def _resolve_win_probability(match: dict[str, Any], sharp_odds: float) -> float | None:
    """Marji temizlenmis olasilik (fair_probability) varsa onu kullanir.

    Yoksa ham 1/oran'a duser; boylece devig verisi olmayan kayitlar da calisir.
    """
    fair = match.get("fair_probability")
    if not isinstance(fair, bool) and isinstance(fair, (int, float)):
        numeric = float(fair)
        if math.isfinite(numeric) and 0.0 < numeric < 1.0:
            return numeric
    return implied_win_probability(sharp_odds)


def build_market_prob_index(matches: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    index: dict[str, dict[str, float]] = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        match_name = str(match.get("match_name", "")).strip()
        market = str(match.get("market", "")).strip().upper()
        if not match_name or not market:
            continue
        prob = _resolve_win_probability(match, float(match.get("sharp_odds", 0.0)))
        if prob is None:
            continue
        index.setdefault(match_name, {})[market] = prob
    return index


def _passes_hero_context_fusion(
    match: dict[str, Any],
    *,
    market: str,
    sharp_odds: float,
) -> tuple[bool, str]:
    # Form/baglam modeli yalnizca mac sonucu yonunu anlar; alt/ust gibi
    # aile disi pazarlarda karar veremez, notr gecer (crash onlemi).
    if market_family_group_key(market) != FAMILY_MATCH_RESULT:
        return True, "baglam_kapsam_disi"

    features = resolve_context_features_from_match(match)
    if features is None:
        return True, "baglam_verisi_yok"

    try:
        score = score_context_for_market(features, market, sharp_odds=sharp_odds)
    except ContextFeatureError:
        return True, "baglam_okunamadi"

    if score.final_market_score <= _CONTEXT_MIN_ALIGNED_SCORE:
        return False, "form_uyumsuz"
    return True, "form_uyumlu"


def _parse_commence_time(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_closing_window(match: dict[str, Any]) -> bool:
    kickoff = _parse_commence_time(str(match.get("commence_time", "")))
    if kickoff is None:
        return False
    now = datetime.now(timezone.utc)
    if kickoff <= now:
        return (now - kickoff).total_seconds() <= _CLOSING_LIVE_SECONDS
    return (kickoff - now).total_seconds() <= _CLOSING_PREMATCH_SECONDS


def _effective_thresholds(profile: HeroProfileValues, match: dict[str, Any]) -> tuple[float, float, bool]:
    min_confidence = float(profile["min_confidence"])
    net_superiority = float(profile["net_superiority"])
    closing = _is_closing_window(match)
    if closing:
        min_confidence += _CLOSING_ADVANTAGE
        net_superiority += _CLOSING_ADVANTAGE
    return min_confidence, net_superiority, closing


def _evaluate_market_superiority(
    prob_index: dict[str, dict[str, float]],
    *,
    match_name: str,
    market: str,
    win_probability: float,
    net_superiority: float,
) -> tuple[bool, str, float | None]:
    markets = prob_index.get(match_name, {})
    # Yalnizca ayni pazar ailesi kiyaslanir: MS1/X/MS2 kendi arasinda,
    # ALT/UST ayni cizgide kendi arasinda. Aile disi kiyas elma-armut olur.
    group = market_family_group_key(market)
    other_probs = [
        prob
        for key, prob in markets.items()
        if key != market and market_family_group_key(key) == group
    ]
    if not other_probs:
        return True, "tek_market", None

    max_other = max(other_probs)
    gap = win_probability - max_other
    if win_probability >= max_other and gap >= net_superiority:
        return True, "piyasa_uyumlu", gap
    return False, "net_ustunluk_yetersiz", gap


def evaluate_hero_gate(
    match: dict[str, Any],
    prob_index: dict[str, dict[str, float]],
    profile: HeroProfileValues,
) -> HeroGateDecision:
    market = str(match.get("market", "")).strip().upper()
    match_name = str(match.get("match_name", "")).strip()
    sharp_odds = float(match.get("sharp_odds", 0.0))
    consensus_books = int(match.get("consensus_books", 0) or 0)

    win_probability = _resolve_win_probability(match, sharp_odds)
    if win_probability is None:
        return HeroGateDecision(passed=False, reason="gecersiz_oran")

    min_confidence, net_superiority, closing = _effective_thresholds(profile, match)
    if win_probability < min_confidence:
        return HeroGateDecision(
            passed=False,
            reason="kapanis_esigi" if closing else "dusuk_guven",
            win_probability=win_probability,
        )

    if consensus_books < int(MIN_CONSENSUS_BOOKMAKERS):
        return HeroGateDecision(
            passed=False,
            reason="referans_yetersiz",
            win_probability=win_probability,
        )

    context_ok, context_reason = _passes_hero_context_fusion(
        match,
        market=market,
        sharp_odds=sharp_odds,
    )
    if not context_ok:
        return HeroGateDecision(
            passed=False,
            reason=context_reason,
            win_probability=win_probability,
        )

    market_ok, market_reason, gap = _evaluate_market_superiority(
        prob_index,
        match_name=match_name,
        market=market,
        win_probability=win_probability,
        net_superiority=net_superiority,
    )
    if not market_ok:
        return HeroGateDecision(
            passed=False,
            reason="kapanis_esigi" if closing and market_reason == "net_ustunluk_yetersiz" else market_reason,
            win_probability=win_probability,
            net_superiority_gap=gap,
        )

    return HeroGateDecision(
        passed=True,
        reason=market_reason if market_reason != "tek_market" else context_reason,
        win_probability=win_probability,
        net_superiority_gap=gap,
    )
