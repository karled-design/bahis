from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

__all__ = (
    "DEFAULT_HERO_PROFILE_LEVEL",
    "HERO_PROFILE_LEVELS",
    "apply_hero_profile_level",
    "bootstrap_hero_profile",
    "build_hero_profile_payload",
    "get_active_hero_profile_level",
    "get_active_hero_profile_values",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "hero_profile.json"
_DEFAULT_LEVEL = "4"
_LOCK = threading.Lock()
_ACTIVE_LEVEL: str = _DEFAULT_LEVEL


class HeroProfileValues(TypedDict):
    min_confidence: float
    net_superiority: float
    soft_odds_min: float
    soft_odds_max: float
    risk_per_trade: float
    max_daily_picks: int


class _HeroLevel(TypedDict):
    id: str
    label: str
    short: str
    summary: str
    increase_hint: str
    decrease_hint: str
    values: HeroProfileValues


HERO_PROFILE_LEVELS: dict[str, _HeroLevel] = {
    "1": {
        "id": "1",
        "label": "Sessiz Kahraman",
        "short": "En secici — az bildirim",
        "summary": "Tutma ihtimali ~%62+. Gunde en fazla 2 aday.",
        "increase_hint": "Ust seviye (Temkinli): guven ~%60, gunde 2 aday, stake %2.",
        "decrease_hint": "En dusuk seviye. Daha az bildirim icin zaten buradasiniz.",
        "values": {
            "min_confidence": 0.62,
            "net_superiority": 0.12,
            "soft_odds_min": 1.50,
            "soft_odds_max": 2.10,
            "risk_per_trade": 0.015,
            "max_daily_picks": 2,
        },
    },
    "2": {
        "id": "2",
        "label": "Temkinli",
        "short": "Guvenli — dusuk bildirim",
        "summary": "Tutma ihtimali ~%60+. Gunde en fazla 2 aday, stake %2.",
        "increase_hint": "Ust seviye (Dengeli): guven ~%57, gunde 3 aday, stake %2,5.",
        "decrease_hint": "Alt seviye (Sessiz Kahraman): guven ~%62, gunde 2 aday.",
        "values": {
            "min_confidence": 0.60,
            "net_superiority": 0.11,
            "soft_odds_min": 1.45,
            "soft_odds_max": 2.15,
            "risk_per_trade": 0.020,
            "max_daily_picks": 2,
        },
    },
    "3": {
        "id": "3",
        "label": "Dengeli",
        "short": "Dengeli profil",
        "summary": "Tutma ihtimali ~%57+. Gunde en fazla 3 aday, stake %2,5.",
        "increase_hint": "Ust seviye (Aktif): guven ~%55, gunde 8 aday, daha sik fikir.",
        "decrease_hint": "Alt seviye (Temkinli): guven ~%60, gunde 2 aday, daha az mesaj.",
        "values": {
            "min_confidence": 0.57,
            "net_superiority": 0.10,
            "soft_odds_min": 1.45,
            "soft_odds_max": 2.25,
            "risk_per_trade": 0.025,
            "max_daily_picks": 3,
        },
    },
    "4": {
        "id": "4",
        "label": "Aktif",
        "short": "Daha sik fikir — varsayilan",
        "summary": "Tutma ihtimali ~%55+. Gunde en fazla 8 aday, stake %3.",
        "increase_hint": "Ust seviye (Cesur): guven ~%54, gunde 12 aday (pratikte sinirsiz).",
        "decrease_hint": "Alt seviye (Dengeli): guven ~%57, gunde 3 aday, daha secici.",
        "values": {
            "min_confidence": 0.55,
            "net_superiority": 0.08,
            "soft_odds_min": 1.40,
            "soft_odds_max": 2.30,
            "risk_per_trade": 0.030,
            "max_daily_picks": 8,
        },
    },
    "5": {
        "id": "5",
        "label": "Cesur",
        "short": "Maksimum test bildirimi",
        "summary": "Tutma ihtimali ~%54+. Gunde en fazla 12 aday (pratikte sinirsiz), stake %3,5; win rate dusabilir.",
        "increase_hint": "En yuksek seviye. Daha fazla bildirim icin profil siniri yok.",
        "decrease_hint": "Alt seviye (Aktif): guven ~%55, gunde 8 aday, daha az risk.",
        "values": {
            "min_confidence": 0.54,
            "net_superiority": 0.08,
            "soft_odds_min": 1.35,
            "soft_odds_max": 2.40,
            "risk_per_trade": 0.035,
            "max_daily_picks": 12,
        },
    },
}

DEFAULT_HERO_PROFILE_LEVEL = _DEFAULT_LEVEL
_LEVEL_ORDER: tuple[str, ...] = ("1", "2", "3", "4", "5")


def _load_persisted_level() -> str | None:
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    level = str(payload.get("level", "")).strip()
    if level in HERO_PROFILE_LEVELS:
        return level
    return None


def _save_persisted_level(level: str) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "level": level,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bootstrap_hero_profile() -> str:
    global _ACTIVE_LEVEL
    with _LOCK:
        persisted = _load_persisted_level()
        _ACTIVE_LEVEL = persisted or _DEFAULT_LEVEL
        return _ACTIVE_LEVEL


def get_active_hero_profile_level() -> str:
    with _LOCK:
        return _ACTIVE_LEVEL


def get_active_hero_profile_values() -> HeroProfileValues:
    level = get_active_hero_profile_level()
    return dict(HERO_PROFILE_LEVELS[level]["values"])


def apply_hero_profile_level(level_id: str) -> HeroProfileValues:
    normalized = str(level_id).strip()
    if normalized not in HERO_PROFILE_LEVELS:
        raise ValueError(f"unknown hero profile level: {level_id}")
    global _ACTIVE_LEVEL
    with _LOCK:
        _ACTIVE_LEVEL = normalized
        _save_persisted_level(normalized)
        return dict(HERO_PROFILE_LEVELS[normalized]["values"])


def build_hero_profile_payload() -> dict[str, Any]:
    with _LOCK:
        active = _ACTIVE_LEVEL
    level = HERO_PROFILE_LEVELS[active]
    values = level["values"]
    idx = _LEVEL_ORDER.index(active)
    prev_level = _LEVEL_ORDER[idx - 1] if idx > 0 else None
    next_level = _LEVEL_ORDER[idx + 1] if idx < len(_LEVEL_ORDER) - 1 else None
    return {
        "active_level": active,
        "active_label": level["label"],
        "active_summary": level["summary"],
        "active_short": level["short"],
        "increase_hint": level["increase_hint"],
        "decrease_hint": level["decrease_hint"],
        "prev_level": prev_level,
        "next_level": next_level,
        "can_decrease": prev_level is not None,
        "can_increase": next_level is not None,
        "min_confidence_percent": round(float(values["min_confidence"]) * 100.0, 1),
        "net_superiority_percent": round(float(values["net_superiority"]) * 100.0, 1),
        "soft_odds_min": float(values["soft_odds_min"]),
        "soft_odds_max": float(values["soft_odds_max"]),
        "risk_per_trade_percent": round(float(values["risk_per_trade"]) * 100.0, 1),
        "max_daily_picks": int(values["max_daily_picks"]),
        "levels": [
            {
                "id": item["id"],
                "label": item["label"],
                "short": item["short"],
                "active": item["id"] == active,
            }
            for item in (HERO_PROFILE_LEVELS[key] for key in _LEVEL_ORDER)
        ],
    }
