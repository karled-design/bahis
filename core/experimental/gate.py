from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.experimental.aggregator import evaluate_experimental_signals
from core.experimental.mode import get_experimental_mode, is_experimental_live_filter
from core.experimental.shadow_log import log_experimental_shadow
from core.experimental.features_state import is_any_experimental_enabled

__all__ = (
    "ExperimentalGateResult",
    "ExperimentalHeroResult",
    "apply_experimental_hero_gate",
    "apply_experimental_tier_gate",
)


@dataclass(frozen=True)
class ExperimentalGateResult:
    tier: str | None
    modules_checked: int = 0
    would_filter: bool = False
    reasons: tuple[str, ...] = ()
    mode: str = "shadow"


@dataclass(frozen=True)
class ExperimentalHeroResult:
    passed: bool
    reason: str
    modules_checked: int = 0
    would_filter: bool = False
    mode: str = "shadow"


def _finalize_tier(
    tier: str | None,
    evaluation,
    *,
    path: str,
    match: dict[str, Any],
    market: str,
) -> ExperimentalGateResult:
    mode = get_experimental_mode()
    would_filter = bool(evaluation.would_filter)
    if would_filter:
        log_experimental_shadow(
            path=path,
            match_name=str(match.get("match_name", "")),
            market=market,
            evaluation=evaluation.as_dict(),
            mode=mode,
        )
    if would_filter and is_experimental_live_filter():
        return ExperimentalGateResult(
            tier=None,
            modules_checked=evaluation.modules_checked,
            would_filter=True,
            reasons=tuple(evaluation.reasons),
            mode=mode,
        )
    return ExperimentalGateResult(
        tier=tier,
        modules_checked=evaluation.modules_checked,
        would_filter=would_filter,
        reasons=tuple(evaluation.reasons),
        mode=mode,
    )


def apply_experimental_tier_gate(
    tier: str | None,
    match: dict[str, Any],
    *,
    market: str,
) -> ExperimentalGateResult:
    if tier is None or not is_any_experimental_enabled():
        return ExperimentalGateResult(tier=tier)

    evaluation = evaluate_experimental_signals(match, market=market, tier=tier)
    if evaluation.modules_checked <= 0:
        return ExperimentalGateResult(tier=tier)

    return _finalize_tier(
        tier,
        evaluation,
        path="ev",
        match=match,
        market=market,
    )


def apply_experimental_hero_gate(
    match: dict[str, Any],
    *,
    market: str,
) -> ExperimentalHeroResult:
    if not is_any_experimental_enabled():
        return ExperimentalHeroResult(passed=True, reason="")

    evaluation = evaluate_experimental_signals(match, market=market, tier="ACTION")
    if evaluation.modules_checked <= 0:
        return ExperimentalHeroResult(passed=True, reason="")

    mode = get_experimental_mode()
    would_filter = bool(evaluation.would_filter)
    reason = evaluation.reasons[0] if evaluation.reasons else ""

    if would_filter:
        log_experimental_shadow(
            path="hero",
            match_name=str(match.get("match_name", "")),
            market=market,
            evaluation=evaluation.as_dict(),
            mode=mode,
        )

    if would_filter and is_experimental_live_filter():
        return ExperimentalHeroResult(
            passed=False,
            reason=reason or "experimental_filter",
            modules_checked=evaluation.modules_checked,
            would_filter=True,
            mode=mode,
        )

    return ExperimentalHeroResult(
        passed=True,
        reason=reason,
        modules_checked=evaluation.modules_checked,
        would_filter=would_filter,
        mode=mode,
    )
