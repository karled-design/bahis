from __future__ import annotations

from typing import Any

from core.experimental.features_state import get_experimental_features_state
from core.experimental.news_signals import evaluate_news_signals
from core.experimental.score_model import evaluate_score_model
from core.experimental.social_signals import evaluate_social_signals
from core.experimental.types import ExperimentalEvaluation

__all__ = ("evaluate_experimental_signals",)


def evaluate_experimental_signals(
    match: dict[str, Any],
    *,
    market: str,
    tier: str | None = None,
) -> ExperimentalEvaluation:
    state = get_experimental_features_state()
    evaluation = ExperimentalEvaluation()

    if state.get("score_model"):
        evaluation.add(
            evaluate_score_model(match, market=market, tier=tier),
        )
    if state.get("news_signals"):
        evaluation.add(
            evaluate_news_signals(match, market=market),
        )
    if state.get("social_signals"):
        evaluation.add(
            evaluate_social_signals(match, tier=tier),
        )

    return evaluation
