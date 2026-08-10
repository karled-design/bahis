from __future__ import annotations

import threading
from typing import Any, TypedDict

__all__ = (
    "ExperimentalFeatureId",
    "build_feature_specs",
    "disable_all_experimental_features",
    "get_experimental_features_state",
    "is_any_experimental_enabled",
    "is_feature_enabled",
    "update_experimental_features",
)

ExperimentalFeatureId = str  # score_model | news_signals | social_signals


class _FeatureSpec(TypedDict):
    key: ExperimentalFeatureId
    label: str
    tip: str
    enabled: bool


_FEATURE_SPECS: tuple[_FeatureSpec, ...] = (
    {
        "key": "score_model",
        "label": "Skor tahmini modeli",
        "tip": (
            "Mac skoru / galibiyet olasiligi modeli (deneysel). Faz E2 Poisson ile genisletilecek. "
            "Varsayilan: kapali."
        ),
        "enabled": False,
    },
    {
        "key": "news_signals",
        "label": "Haber sinyalleri",
        "tip": (
            "API-Football sakatlik + resmi RSS whitelist. Golge modda loglar; "
            "canli modda uyumsuz adaylari eler. Varsayilan: acik (golge mod)."
        ),
        "enabled": True,
    },
    {
        "key": "social_signals",
        "label": "Sosyal medya sinyalleri",
        "tip": (
            "Sosyal medya sinyalleri (deneysel). Faz E5 — simdilik kapali. "
            "Varsayilan: kapali."
        ),
        "enabled": False,
    },
)

_LOCK = threading.Lock()
_STATE: dict[str, bool] = {spec["key"]: bool(spec["enabled"]) for spec in _FEATURE_SPECS}
_VALID_KEYS = frozenset(_STATE.keys())


def build_feature_specs() -> tuple[_FeatureSpec, ...]:
    return _FEATURE_SPECS


def get_experimental_features_state() -> dict[str, bool]:
    with _LOCK:
        return dict(_STATE)


def is_feature_enabled(key: str) -> bool:
    with _LOCK:
        return bool(_STATE.get(key, False))


def is_any_experimental_enabled() -> bool:
    with _LOCK:
        return any(_STATE.values())


def disable_all_experimental_features() -> dict[str, bool]:
    with _LOCK:
        for key in _STATE:
            _STATE[key] = False
        return dict(_STATE)


def update_experimental_features(payload: dict[str, Any]) -> dict[str, bool]:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    with _LOCK:
        for raw_key, raw_value in payload.items():
            key = str(raw_key).strip()
            if key not in _VALID_KEYS:
                continue
            if not isinstance(raw_value, bool):
                raise TypeError(f"{key} must be a boolean")
            _STATE[key] = raw_value
        return dict(_STATE)
