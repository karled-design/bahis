from __future__ import annotations

import math

from config.settings import HIGH_EV_THRESHOLD, MAX_EV_THRESHOLD, MIN_CONSENSUS_BOOKMAKERS
from core.operator_risk_settings import get_action_ev_threshold, get_watch_ev_threshold

__all__ = (
    "calculate_expected_value",
    "calculate_expected_value_from_probability",
    "classify_ev_tier",
    "get_active_min_ev_threshold",
    "is_advantageous",
    "is_ev_absurd",
    "is_ev_actionable",
    "is_ev_plausible",
    "is_ev_watchable",
)

_MIN_VALID_ODDS_EXCLUSIVE = 1.0
_EV_TIER_HIGH = "HIGH"
_EV_TIER_ACTION = "ACTION"
_EV_TIER_WATCH = "WATCH"


def _is_valid_ev_number(ev: object) -> bool:
    if isinstance(ev, bool) or not isinstance(ev, (int, float)):
        return False
    return math.isfinite(float(ev))


def _is_valid_odds(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and numeric > _MIN_VALID_ODDS_EXCLUSIVE


def get_active_min_ev_threshold() -> float:
    return get_action_ev_threshold()


def calculate_expected_value(sharp_odds: float, soft_odds: float) -> float:
    if not _is_valid_odds(sharp_odds) or not _is_valid_odds(soft_odds):
        return 0.0

    sharp = float(sharp_odds)
    soft = float(soft_odds)
    p_true = 1.0 / sharp
    ev = (p_true * soft) - 1.0
    return ev if math.isfinite(ev) else 0.0


def calculate_expected_value_from_probability(true_probability: float, soft_odds: float) -> float:
    """Marji temizlenmis (fair) olasilikla EV: p_fair * oran - 1.

    1/oran yaklasimindan farki: kitapci kar payi cikarilmis gercekci olasilik kullanir.
    """
    if isinstance(true_probability, bool) or not isinstance(true_probability, (int, float)):
        return 0.0
    prob = float(true_probability)
    if not math.isfinite(prob) or not (0.0 < prob < 1.0):
        return 0.0
    if not _is_valid_odds(soft_odds):
        return 0.0
    ev = (prob * float(soft_odds)) - 1.0
    return ev if math.isfinite(ev) else 0.0


def classify_ev_tier(ev: float, *, consensus_books: int = 0) -> str | None:
    if not _is_valid_ev_number(ev):
        return None

    numeric = float(ev)
    if numeric > float(MAX_EV_THRESHOLD):
        return None

    min_action = get_active_min_ev_threshold()
    books = max(0, int(consensus_books))

    if numeric >= float(HIGH_EV_THRESHOLD) and books >= int(MIN_CONSENSUS_BOOKMAKERS):
        return _EV_TIER_HIGH
    if numeric >= min_action:
        return _EV_TIER_ACTION
    if numeric >= get_watch_ev_threshold():
        return _EV_TIER_WATCH
    return None


def is_ev_actionable(ev: float, *, consensus_books: int = 0) -> bool:
    tier = classify_ev_tier(ev, consensus_books=consensus_books)
    return tier in (_EV_TIER_ACTION, _EV_TIER_HIGH)


def is_ev_watchable(ev: float, *, consensus_books: int = 0) -> bool:
    return classify_ev_tier(ev, consensus_books=consensus_books) == _EV_TIER_WATCH


def is_advantageous(ev: float) -> bool:
    if not _is_valid_ev_number(ev):
        return False
    return float(ev) >= get_active_min_ev_threshold()


def is_ev_absurd(ev: float) -> bool:
    if not _is_valid_ev_number(ev):
        return False
    numeric = float(ev)
    return numeric > float(MAX_EV_THRESHOLD) and numeric >= get_watch_ev_threshold()


def is_ev_plausible(ev: float, *, consensus_books: int = 0) -> bool:
    """ACTION veya HIGH katmanina uygun EV."""
    return is_ev_actionable(ev, consensus_books=consensus_books)
