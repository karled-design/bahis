from __future__ import annotations

from typing import Any

from core.context_fusion import resolve_context_features_from_match
from core.context_score import ContextScoreError, score_context_for_market
from core.operator_risk_settings import is_context_info_enabled

__all__ = ("format_alert_context_tag",)


def format_alert_context_tag(
    match: dict[str, Any],
    *,
    market: str,
    sharp_odds: float,
) -> str:
    """Telegram icin tek satirlik, kisa baglam etiketi. Baglam yoksa bos string."""
    if not is_context_info_enabled():
        return ""

    features = resolve_context_features_from_match(match)
    if features is None:
        return ""

    try:
        score = score_context_for_market(features, market, sharp_odds=sharp_odds)
    except ContextScoreError:
        return ""

    bias = float(score.home_bias_score)
    if bias >= 0:
        lean = f"ev {bias:+.0f}"
    else:
        lean = f"dep {abs(bias):.0f}"

    return f" | Baglam: {lean}"
