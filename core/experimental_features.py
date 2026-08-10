from __future__ import annotations

from typing import Any

from core.experimental.features_state import (
    build_feature_specs,
    disable_all_experimental_features,
    get_experimental_features_state,
    is_any_experimental_enabled,
    is_feature_enabled,
    update_experimental_features,
)
from core.experimental.gate import (
    ExperimentalGateResult,
    ExperimentalHeroResult,
    apply_experimental_hero_gate,
    apply_experimental_tier_gate,
)
from core.experimental.mode import (
    build_experimental_mode_payload,
    bootstrap_experimental_mode,
    get_experimental_mode,
    is_experimental_live_filter,
    set_experimental_mode,
)
from core.experimental.shadow_log import get_shadow_stats

__all__ = (
    "ExperimentalFeatureId",
    "ExperimentalGateResult",
    "ExperimentalHeroResult",
    "apply_experimental_hero_gate",
    "apply_experimental_tier_gate",
    "bootstrap_experimental_mode",
    "build_experimental_features_payload",
    "build_experimental_panel_payload",
    "disable_all_experimental_features",
    "format_experimental_status_line",
    "get_experimental_features_state",
    "is_any_experimental_enabled",
    "is_feature_enabled",
    "is_experimental_live_filter",
    "set_experimental_mode",
    "update_experimental_features",
)

ExperimentalFeatureId = str


def _module_status(*, enabled: bool, key: str) -> str:
    if not enabled:
        return "kapali"
    if key == "score_model":
        return "bekliyor"
    if key == "social_signals":
        return "bekliyor"
    mode = get_experimental_mode()
    return "canli" if mode == "live" else "golge"


def build_experimental_features_payload() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = get_experimental_features_state()
    for spec in build_feature_specs():
        key = spec["key"]
        enabled = bool(state.get(key, False))
        rows.append(
            {
                "key": key,
                "label": spec["label"],
                "tip": spec["tip"],
                "enabled": enabled,
                "status": _module_status(enabled=enabled, key=key),
            }
        )
    return rows


def build_experimental_panel_payload() -> dict[str, Any]:
    shadow = get_shadow_stats()
    mode_payload = build_experimental_mode_payload()
    return {
        "features": build_experimental_features_payload(),
        "mode": mode_payload,
        "shadow": {
            **shadow,
            "label": (
                f"Golge: {shadow['would_filter']} elenirdi / {shadow['pass_through']} gecerdi"
                if shadow["total"] > 0
                else "Golge log bos"
            ),
        },
    }


def format_experimental_status_line() -> str:
    if not is_any_experimental_enabled():
        return "Deneysel moduller: kapali"
    enabled_labels: list[str] = []
    for row in build_experimental_features_payload():
        if row.get("enabled"):
            enabled_labels.append(str(row.get("label", row.get("key", ""))))
    mode = get_experimental_mode()
    joined = ", ".join(enabled_labels)
    if mode == "live":
        return f"Deneysel (canli): {joined}"
    return f"Deneysel (golge): {joined}"
