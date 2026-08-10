from __future__ import annotations

from typing import Any

from core.experimental.types import ModuleVerdict

__all__ = ("evaluate_score_model",)


def evaluate_score_model(
    match: dict[str, Any],
    *,
    market: str,
    tier: str | None = None,
) -> ModuleVerdict:
    """Poisson/API model E2'de gelecek; simdilik her zaman gecirir."""
    _ = (match, market, tier)
    return ModuleVerdict(
        module="score_model",
        keep=True,
        reason="skor_model_bekliyor",
        detail="Faz E2 Poisson henuz aktif degil",
    )
