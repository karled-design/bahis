from __future__ import annotations

import math

from config.settings import TOTAL_KASA
from core.operator_risk_settings import get_operator_risk_per_trade
from core.passion_engine import get_active_min_ev_threshold

__all__ = ("calculate_stake_amount",)

_SAFE_HARBOR_STAKE = 0.0
_MAX_FINITE_STAKE = 1.0e12
_MAX_EV_STAKE_MULTIPLIER = 2.0


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _resolve_base_kasa(current_kasa: float) -> float:
    if _is_finite_number(current_kasa) and float(current_kasa) > 0.0:
        return float(current_kasa)
    return float(TOTAL_KASA)


def _safe_harbor_stake() -> float:
    return _SAFE_HARBOR_STAKE


def _resolve_ev_stake_multiplier(ev: float) -> float | None:
    if not _is_finite_number(ev):
        return None
    numeric_ev = float(ev)
    threshold = get_active_min_ev_threshold()
    if numeric_ev < threshold:
        return None
    if threshold <= 0.0:
        return None
    return min(numeric_ev / threshold, _MAX_EV_STAKE_MULTIPLIER)


def calculate_stake_amount(current_kasa: float, ev: float) -> float:
    ev_multiplier = _resolve_ev_stake_multiplier(ev)
    if ev_multiplier is None:
        return _safe_harbor_stake()

    base_kasa = _resolve_base_kasa(current_kasa)

    try:
        raw_stake = base_kasa * get_operator_risk_per_trade() * ev_multiplier
    except OverflowError:
        return _safe_harbor_stake()

    if not math.isfinite(raw_stake):
        return _safe_harbor_stake()

    if abs(raw_stake) > _MAX_FINITE_STAKE:
        return _safe_harbor_stake()

    stake = round(raw_stake, 2)

    if not math.isfinite(stake) or stake < 0.0:
        return _safe_harbor_stake()

    return stake
