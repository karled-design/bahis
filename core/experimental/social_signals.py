from __future__ import annotations

from typing import Any

from core.experimental.types import ModuleVerdict

__all__ = ("evaluate_social_signals",)


def evaluate_social_signals(match: dict[str, Any], *, tier: str | None = None) -> ModuleVerdict:
    _ = (match, tier)
    return ModuleVerdict(
        module="social_signals",
        keep=True,
        reason="sosyal_bekliyor",
        detail="Faz E5 — simdilik kapali",
    )
