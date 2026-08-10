from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from core.operator_risk_settings import (
    OperatorRiskSettings,
    get_action_ev_threshold,
    get_context_mode,
    get_operator_risk_per_trade,
    get_operator_risk_settings,
    get_soft_odds_max,
    get_soft_odds_min,
    get_watch_ev_threshold,
    update_operator_risk_settings,
)
from core.scan_league_settings import get_leagues_per_scan, get_scan_league_settings, update_scan_league_settings

__all__ = (
    "DEFAULT_NOTIFY_FREQUENCY_LEVEL",
    "NOTIFY_FREQUENCY_LEVELS",
    "apply_notify_frequency_level",
    "bootstrap_notify_frequency_profile",
    "build_notify_frequency_payload",
    "detect_active_notify_frequency_level",
    "get_active_notify_frequency_level",
    "is_extreme_data_bypass_active",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "notify_frequency.json"
_MATCH_EPSILON = 0.0008
_DEFAULT_LEVEL = "3"
_EXTREME_LEVEL = "6"

_LOCK = threading.Lock()
_ACTIVE_LEVEL: str = _DEFAULT_LEVEL


class _LevelRisk(TypedDict):
    watch_ev: float
    action_ev: float
    soft_odds_min: float
    soft_odds_max: float
    risk_per_trade: float
    context_mode: str


class _NotifyLevel(TypedDict):
    id: str
    label: str
    short: str
    summary: str
    increase_hint: str
    decrease_hint: str
    risk: _LevelRisk
    leagues_per_scan: int


NOTIFY_FREQUENCY_LEVELS: dict[str, _NotifyLevel] = {
    "1": {
        "id": "1",
        "label": "Cok Az",
        "short": "Secici — nadir mesaj",
        "summary": (
            "Telegram nadiren calisir. Oyna icin yaklasik +%3,5 gercek avantaj gerekir; "
            "form filtresi acik. Sezon disinda cok sessiz kalabilir."
        ),
        "decrease_hint": "En dusuk seviyedesiniz. Daha da azaltmak icin asagidaki Oyna slider'ini saga cekin.",
        "increase_hint": (
            "Bir ust seviye (Az): esik ~%3,0'a duser; mac basi tutar biraz artar; "
            "tur basina 3 lig taranir."
        ),
        "risk": {
            "watch_ev": 0.018,
            "action_ev": 0.035,
            "soft_odds_min": 1.20,
            "soft_odds_max": 5.5,
            "risk_per_trade": 0.010,
            "context_mode": "filter",
        },
        "leagues_per_scan": 3,
    },
    "2": {
        "id": "2",
        "label": "Az",
        "short": "Guvenli — az mesaj",
        "summary": (
            "Oyna icin yaklasik +%3,0 EV gerekir. Form zayif maclar elenir. "
            "Gunde genelde 0–2 oneri bekleyin."
        ),
        "decrease_hint": (
            "Alt seviye (Cok Az): esik ~%3,5'e cikar; daha az mesaj, daha secici firsatlar."
        ),
        "increase_hint": (
            "Ust seviye (Dengeli): esik ~%2,5'e duser; tur basina 4 lig; biraz daha sik bildirim."
        ),
        "risk": {
            "watch_ev": 0.016,
            "action_ev": 0.030,
            "soft_odds_min": 1.18,
            "soft_odds_max": 6.0,
            "risk_per_trade": 0.015,
            "context_mode": "filter",
        },
        "leagues_per_scan": 3,
    },
    "3": {
        "id": "3",
        "label": "Dengeli",
        "short": "Standart gercek EV",
        "summary": (
            "Oyna icin yaklasik +%2,5 gercek Nesine avantaji gerekir. Form filtresi acik. "
            "Sahte sinyal yok; sezon acikken dengeli akis."
        ),
        "decrease_hint": (
            "Alt seviye (Az): daha secici +%3,0 esik; daha az Telegram mesaji."
        ),
        "increase_hint": (
            "Ust seviye (Sik): esik ~%2,0; tur basina 5 lig; mac havuzu genisler, bildirim artar."
        ),
        "risk": {
            "watch_ev": 0.014,
            "action_ev": 0.022,
            "soft_odds_min": 1.15,
            "soft_odds_max": 7.0,
            "risk_per_trade": 0.022,
            "context_mode": "filter",
        },
        "leagues_per_scan": 4,
    },
    "4": {
        "id": "4",
        "label": "Sik",
        "short": "Daha sik gercek EV",
        "summary": (
            "Oyna icin yaklasik +%1,7 gercek avantaj yeter; "
            "tur basina 6 lig taranir. Gunde daha fazla bildirim."
        ),
        "decrease_hint": (
            "Alt seviye (Dengeli): esik ~%2,2; daha az mesaj ama daha secici firsatlar."
        ),
        "increase_hint": (
            "Ust seviye (Cok Sik): esik ~%1,2; form filtresi kapanir; tur basina 11 lig (hepsi) — "
            "en fazla gercek bildirim (yine pozitif EV sart)."
        ),
        "risk": {
            "watch_ev": 0.008,
            "action_ev": 0.017,
            "soft_odds_min": 1.10,
            "soft_odds_max": 8.5,
            "risk_per_trade": 0.030,
            "context_mode": "filter",
        },
        "leagues_per_scan": 6,
    },
    "5": {
        "id": "5",
        "label": "Cok Sik",
        "short": "Varsayilan — maksimum gercek bildirim",
        "summary": (
            "Oyna icin yaklasik +%1,2 EV yeter; form filtresi kapali; tur basina 11 lig (hepsi). "
            "En sik mesaj modu — yine de Nesine'de gercek avantaj olmadan bildirim gitmez."
        ),
        "decrease_hint": (
            "Alt seviye (Sik): esik ~%1,7; form filtresi tekrar acilir; mesaj sayisi azalir."
        ),
        "increase_hint": (
            "En ust seviyedesiniz. Daha da artirmak icin asagidaki Oyna slider'ini sola cekin "
            "veya Kalibrasyon risk profilini deneyin."
        ),
        "risk": {
            "watch_ev": 0.006,
            "action_ev": 0.012,
            "soft_odds_min": 1.08,
            "soft_odds_max": 9.5,
            "risk_per_trade": 0.035,
            "context_mode": "off",
        },
        "leagues_per_scan": 11,
    },
    "6": {
        "id": "6",
        "label": "Sinirsiz",
        "short": "Guvenlik filtreleri kapali — riskli test modu",
        "summary": (
            "Veri filtreleri kapali: bayat oran, supheli/hatali oran, sanal-efutbol ve "
            "+%15 absurt avantaj freni atlanir. Yine de pozitif deger sart (esik ~+%1,0). "
            "Gelen fazladan sinyallerin cogu veri hatasi olabilir; Nesine'de elle oynarken dikkatli olun."
        ),
        "decrease_hint": (
            "Alt seviye (Cok Sik): guvenlik filtreleri geri acilir; sadece taze, tutarli firsatlar gelir."
        ),
        "increase_hint": "En ust ve en riskli seviyedesiniz. Daha yukarisi yok.",
        "risk": {
            "watch_ev": 0.005,
            "action_ev": 0.010,
            "soft_odds_min": 1.05,
            "soft_odds_max": 11.0,
            "risk_per_trade": 0.035,
            "context_mode": "off",
        },
        "leagues_per_scan": 11,
    },
}

DEFAULT_NOTIFY_FREQUENCY_LEVEL = _DEFAULT_LEVEL
_LEVEL_ORDER: tuple[str, ...] = ("1", "2", "3", "4", "5", "6")


def _context_mode_label(mode: str) -> str:
    normalized = str(mode).strip().casefold()
    if normalized == "filter":
        return "Form filtresi acik"
    if normalized == "info":
        return "Form bilgi modu"
    return "Form filtresi kapali"


def _load_persisted_level() -> str | None:
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    level = str(payload.get("level", "")).strip()
    if level in NOTIFY_FREQUENCY_LEVELS:
        return level
    return None


def _save_persisted_level(level: str) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "level": level,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _settings_match_level(settings: OperatorRiskSettings, leagues_per_scan: int, level: _NotifyLevel) -> bool:
    risk = level["risk"]
    pairs = (
        ("watch_ev", risk["watch_ev"]),
        ("action_ev", risk["action_ev"]),
        ("soft_odds_min", risk["soft_odds_min"]),
        ("soft_odds_max", risk["soft_odds_max"]),
        ("risk_per_trade", risk["risk_per_trade"]),
    )
    for key, expected in pairs:
        if abs(float(settings[key]) - float(expected)) > _MATCH_EPSILON:
            return False
    if str(settings.get("context_mode", "")).strip().casefold() != str(risk["context_mode"]).strip().casefold():
        return False
    return int(leagues_per_scan) == int(level["leagues_per_scan"])


def detect_active_notify_frequency_level() -> str | None:
    settings = get_operator_risk_settings()
    leagues = int(get_leagues_per_scan())
    for level_id in _LEVEL_ORDER:
        level = NOTIFY_FREQUENCY_LEVELS[level_id]
        if _settings_match_level(settings, leagues, level):
            return level_id
    return None


def get_active_notify_frequency_level() -> str:
    with _LOCK:
        return _ACTIVE_LEVEL


def is_extreme_data_bypass_active() -> bool:
    """En uc seviye (6) secili mi? Secilliyse taramada veri filtreleri
    (bayat oran, supheli oran, sanal-efutbol, +%15 absurt EV freni) atlanir.
    Gercek deger esigi (action/watch EV) yine de korunur."""
    return get_active_notify_frequency_level() == _EXTREME_LEVEL


def _apply_level_values(level: _NotifyLevel) -> OperatorRiskSettings:
    risk_settings = update_operator_risk_settings(dict(level["risk"]))
    update_scan_league_settings({"leagues_per_scan": int(level["leagues_per_scan"])})
    return risk_settings


def apply_notify_frequency_level(level_id: str, *, persist: bool = True) -> dict[str, Any]:
    global _ACTIVE_LEVEL
    normalized = str(level_id).strip()
    if normalized not in NOTIFY_FREQUENCY_LEVELS:
        raise ValueError(f"unknown notify frequency level: {level_id}")

    level = NOTIFY_FREQUENCY_LEVELS[normalized]
    _apply_level_values(level)

    with _LOCK:
        _ACTIVE_LEVEL = normalized

    if persist:
        _save_persisted_level(normalized)

    return build_notify_frequency_payload()


def bootstrap_notify_frequency_profile(*, force_default: bool = False) -> str:
    level = _DEFAULT_LEVEL if force_default else (_load_persisted_level() or _DEFAULT_LEVEL)
    apply_notify_frequency_level(level, persist=True)
    return level


def _build_level_row(level_id: str, *, active: bool) -> dict[str, Any]:
    level = NOTIFY_FREQUENCY_LEVELS[level_id]
    risk = level["risk"]
    return {
        "id": level_id,
        "label": level["label"],
        "short": level["short"],
        "summary": level["summary"],
        "increase_hint": level["increase_hint"],
        "decrease_hint": level["decrease_hint"],
        "active": active,
        "leagues_per_scan": int(level["leagues_per_scan"]),
        "action_ev_percent": round(float(risk["action_ev"]) * 100.0, 1),
        "watch_ev_percent": round(float(risk["watch_ev"]) * 100.0, 1),
        "risk_per_trade_percent": round(float(risk["risk_per_trade"]) * 100.0, 1),
        "context_mode": str(risk["context_mode"]),
        "context_mode_label": _context_mode_label(str(risk["context_mode"])),
    }


def build_notify_frequency_payload() -> dict[str, Any]:
    global _ACTIVE_LEVEL
    detected = detect_active_notify_frequency_level()
    with _LOCK:
        stored = _ACTIVE_LEVEL
    active = detected or stored
    if detected:
        with _LOCK:
            _ACTIVE_LEVEL = detected

    level = NOTIFY_FREQUENCY_LEVELS.get(active, NOTIFY_FREQUENCY_LEVELS[_DEFAULT_LEVEL])
    idx = _LEVEL_ORDER.index(active) if active in _LEVEL_ORDER else _LEVEL_ORDER.index(_DEFAULT_LEVEL)
    prev_id = _LEVEL_ORDER[idx - 1] if idx > 0 else None
    next_id = _LEVEL_ORDER[idx + 1] if idx < len(_LEVEL_ORDER) - 1 else None

    return {
        "active_level": active,
        "default_level": _DEFAULT_LEVEL,
        "is_custom": detected is None,
        "custom_note": (
            "Slider'larda ozel ayar var; asagidan bir bildirim seviyesi secerek profile donebilirsiniz."
            if detected is None
            else ""
        ),
        "active_label": level["label"],
        "active_summary": level["summary"],
        "increase_hint": level["increase_hint"],
        "decrease_hint": level["decrease_hint"],
        "can_increase": next_id is not None,
        "can_decrease": prev_id is not None,
        "next_level": next_id,
        "prev_level": prev_id,
        "levels": [_build_level_row(level_id, active=(level_id == active)) for level_id in _LEVEL_ORDER],
        "live": {
            "action_ev_percent": round(get_action_ev_threshold() * 100.0, 2),
            "watch_ev_percent": round(get_watch_ev_threshold() * 100.0, 2),
            "risk_per_trade_percent": round(get_operator_risk_per_trade() * 100.0, 2),
            "soft_odds_min": round(get_soft_odds_min(), 2),
            "soft_odds_max": round(get_soft_odds_max(), 2),
            "leagues_per_scan": int(get_leagues_per_scan()),
            "context_mode": get_context_mode(),
            "context_mode_label": _context_mode_label(get_context_mode()),
        },
    }
