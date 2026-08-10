from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from config.settings import (
    CONTEXT_FUSION_MODE,
    MIN_VALUE_THRESHOLD,
    RISK_PER_TRADE,
    WATCH_EV_THRESHOLD,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "operator_risk.json"

__all__ = (
    "OperatorRiskSettings",
    "RiskZone",
    "ContextMode",
    "RiskPresetId",
    "get_operator_risk_settings",
    "update_operator_risk_settings",
    "get_watch_ev_threshold",
    "get_action_ev_threshold",
    "get_soft_odds_min",
    "get_soft_odds_max",
    "get_operator_risk_per_trade",
    "get_context_mode",
    "is_context_info_enabled",
    "classify_slider_zone",
    "build_slider_payload",
    "build_risk_presets_payload",
    "apply_risk_preset",
    "bootstrap_operator_risk_settings",
    "detect_active_risk_preset",
    "CONTEXT_MODE_OPTIONS",
    "RISK_PROFILE_PRESETS",
)

_DEFAULT_SOFT_ODDS_MIN = 1.15
_DEFAULT_SOFT_ODDS_MAX = 7.0

RiskZone = str  # "low" | "acceptable" | "max"
ContextMode = str  # "off" | "info" | "filter"
RiskPresetId = str  # "low" | "medium" | "high"

_CONTEXT_MODE_OFF = "off"
_CONTEXT_MODE_INFO = "info"
_CONTEXT_MODE_FILTER = "filter"
_VALID_CONTEXT_MODES = frozenset({_CONTEXT_MODE_OFF, _CONTEXT_MODE_INFO, _CONTEXT_MODE_FILTER})

CONTEXT_MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": _CONTEXT_MODE_OFF,
        "label": "Kapali",
        "tip": "Arka plan sadece mac tarar. Form/baglam kullanilmaz.",
    },
    {
        "value": _CONTEXT_MODE_INFO,
        "label": "Bilgi",
        "tip": "Telegram'da kisa form notu gelir. Karar yine fiyat farkina gore verilir.",
    },
    {
        "value": _CONTEXT_MODE_FILTER,
        "label": "Akilli (onerilen)",
        "tip": "Form zayif olan oneriler arka planda elenir. Yeni baslayanlar icin en guvenli mod.",
    },
)


def _default_context_mode() -> str:
    env_mode = str(CONTEXT_FUSION_MODE).strip().casefold()
    if env_mode == _CONTEXT_MODE_FILTER:
        return _CONTEXT_MODE_FILTER
    if env_mode == _CONTEXT_MODE_INFO:
        return _CONTEXT_MODE_INFO
    return _CONTEXT_MODE_OFF


class OperatorRiskSettings(TypedDict):
    watch_ev: float
    action_ev: float
    soft_odds_min: float
    soft_odds_max: float
    risk_per_trade: float
    context_mode: str


class _SliderSpec(TypedDict):
    key: str
    label: str
    min: float
    max: float
    step: float
    default: float
    unit: str
    tip: str
    format: str


SLIDER_SPECS: tuple[_SliderSpec, ...] = (
    {
        "key": "watch_ev",
        "label": "Izleme uyarisi",
        "min": 0.003,
        "max": 0.025,
        "step": 0.001,
        "default": float(WATCH_EV_THRESHOLD),
        "unit": "%",
        "tip": "Sol: daha cok izle mesaji (dusuk esik). Sag: daha az izle mesaji.",
        "format": "percent",
    },
    {
        "key": "action_ev",
        "label": "Oyna onerisi",
        "min": 0.005,
        "max": 0.050,
        "step": 0.001,
        "default": float(MIN_VALUE_THRESHOLD),
        "unit": "%",
        "tip": "Sol: daha cok oyna mesaji (dusuk esik). Sag: daha az mesaj (yuksek esik).",
        "format": "percent",
    },
    {
        "key": "soft_odds_min",
        "label": "En dusuk oran",
        "min": 1.05,
        "max": 1.35,
        "step": 0.01,
        "default": _DEFAULT_SOFT_ODDS_MIN,
        "unit": "",
        "tip": "Alt sinir: cok dusuk oranli agir favoriler elenir.",
        "format": "odds",
    },
    {
        "key": "soft_odds_max",
        "label": "En yuksek oran",
        "min": 4.0,
        "max": 12.0,
        "step": 0.5,
        "default": _DEFAULT_SOFT_ODDS_MAX,
        "unit": "",
        "tip": "Ust sinir: cok yuksek oranli surpriz maclar elenir.",
        "format": "odds",
    },
    {
        "key": "risk_per_trade",
        "label": "Mac basi tutar",
        "min": 0.005,
        "max": 0.040,
        "step": 0.001,
        "default": float(RISK_PER_TRADE),
        "unit": "%",
        "tip": "Nesine butcenizden mac basina onerilen pay (Telegram tutar satiri).",
        "format": "percent",
    },
)

_LOCK = threading.Lock()
_SETTINGS: OperatorRiskSettings = {
    "watch_ev": float(WATCH_EV_THRESHOLD),
    "action_ev": float(MIN_VALUE_THRESHOLD),
    "soft_odds_min": _DEFAULT_SOFT_ODDS_MIN,
    "soft_odds_max": _DEFAULT_SOFT_ODDS_MAX,
    "risk_per_trade": float(RISK_PER_TRADE),
    "context_mode": _default_context_mode(),
}

RISK_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "low": {
        "label": "Dusuk risk",
        "short": "Az mesaj, kucuk tutar, siki filtre",
        "tip": (
            "En guvenli profil. Telegram'a nadiren mesaj gelir; gelenler daha guclu firsatlardir. "
            "Mac basi tutar dusuktur (or. 1000 TL kasada ~10-25 TL). Yeni baslayanlar icin onerilir."
        ),
        "values": {
            "context_mode": _CONTEXT_MODE_FILTER,
            "watch_ev": 0.018,
            "action_ev": 0.035,
            "soft_odds_min": 1.20,
            "soft_odds_max": 5.5,
            "risk_per_trade": 0.010,
        },
    },
    "medium": {
        "label": "Orta risk",
        "short": "Dengeli — varsayilan onerilen",
        "tip": (
            "Denge modu. Oyna mesaji icin yaklasik +%2,5 avantaj gerekir; mac basi tutar ~%2 "
            "(or. 1000 TL kasada ~20-40 TL). Cogu kullanici bu profilde kalabilir."
        ),
        "values": {
            "context_mode": _CONTEXT_MODE_FILTER,
            "watch_ev": 0.015,
            "action_ev": 0.025,
            "soft_odds_min": 1.15,
            "soft_odds_max": 7.0,
            "risk_per_trade": 0.020,
        },
    },
    "high": {
        "label": "Yuksek risk",
        "short": "Sik mesaj, buyuk tutar, gevsek filtre",
        "tip": (
            "Oyna esigi ~+%1,8; tutar ~%3,8. Form filtresi acik. Pozitif avantajli maclarda mesaj gelir."
        ),
        "values": {
            "context_mode": _CONTEXT_MODE_FILTER,
            "watch_ev": 0.010,
            "action_ev": 0.018,
            "soft_odds_min": 1.08,
            "soft_odds_max": 9.0,
            "risk_per_trade": 0.038,
        },
    },
}

_PRESET_ORDER: tuple[str, ...] = ("low", "medium", "high")
_PRESET_MATCH_EPSILON = 0.0006


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _spec_for_key(key: str) -> _SliderSpec | None:
    for spec in SLIDER_SPECS:
        if spec["key"] == key:
            return spec
    return None


def classify_slider_zone(key: str, value: float) -> RiskZone:
    spec = _spec_for_key(key)
    if spec is None:
        return "acceptable"

    low = float(spec["min"])
    high = float(spec["max"])
    span = high - low
    if span <= 0.0:
        return "acceptable"

    ratio = (float(value) - low) / span
    if ratio <= 0.25:
        return "low"
    if ratio >= 0.75:
        return "max"
    return "acceptable"


_PERSISTED_KEYS: tuple[str, ...] = (
    "watch_ev",
    "action_ev",
    "soft_odds_min",
    "soft_odds_max",
    "risk_per_trade",
    "context_mode",
)


def _load_persisted_settings() -> dict[str, Any] | None:
    """database/operator_risk.json'dan kayitli ayarlari guvenli okur.

    Dosya yoksa veya bozuksa None doner (motor varsayilan/seviye ile devam eder).
    """
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    result: dict[str, Any] = {}
    for key in _PERSISTED_KEYS:
        if key in payload:
            result[key] = payload[key]
    return result or None


def _save_persisted_settings(settings: OperatorRiskSettings) -> None:
    """Guncel ayarlari diske yazar (panel degisikliginde cagirilir).

    Kalicilik en iyi cabadir; disk hatasi motoru durdurmaz.
    """
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {key: settings[key] for key in _PERSISTED_KEYS}  # type: ignore[literal-required]
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        _PERSIST_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def get_operator_risk_settings() -> OperatorRiskSettings:
    with _LOCK:
        return dict(_SETTINGS)


def update_operator_risk_settings(
    payload: dict[str, Any], *, persist: bool = False
) -> OperatorRiskSettings:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    with _LOCK:
        for raw_key, raw_value in payload.items():
            key = str(raw_key).strip()
            if key == "context_mode":
                mode = str(raw_value).strip().casefold()
                if mode not in _VALID_CONTEXT_MODES:
                    raise ValueError(f"invalid context_mode: {raw_value}")
                _SETTINGS["context_mode"] = mode
                continue

            spec = _spec_for_key(key)
            if spec is None:
                continue
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise TypeError(f"{key} must be numeric")
            numeric = float(raw_value)
            _SETTINGS[key] = _clamp(numeric, float(spec["min"]), float(spec["max"]))

        if float(_SETTINGS["soft_odds_min"]) >= float(_SETTINGS["soft_odds_max"]):
            raise ValueError("soft_odds_min must be < soft_odds_max")
        if float(_SETTINGS["watch_ev"]) >= float(_SETTINGS["action_ev"]):
            raise ValueError("watch_ev must be < action_ev")

        result = dict(_SETTINGS)
        if persist:
            _save_persisted_settings(result)
        return result


def get_watch_ev_threshold() -> float:
    return float(get_operator_risk_settings()["watch_ev"])


def get_action_ev_threshold() -> float:
    return float(get_operator_risk_settings()["action_ev"])


def get_soft_odds_min() -> float:
    return float(get_operator_risk_settings()["soft_odds_min"])


def get_soft_odds_max() -> float:
    return float(get_operator_risk_settings()["soft_odds_max"])


def get_operator_risk_per_trade() -> float:
    return float(get_operator_risk_settings()["risk_per_trade"])


def get_context_mode() -> str:
    return str(get_operator_risk_settings()["context_mode"])


def is_context_info_enabled() -> bool:
    return get_context_mode() in {_CONTEXT_MODE_INFO, _CONTEXT_MODE_FILTER}


def _settings_match_preset(current: OperatorRiskSettings, expected: dict[str, Any]) -> bool:
    for key, expected_val in expected.items():
        current_val = current.get(key)
        if current_val is None:
            return False
        if key == "context_mode":
            if str(current_val).casefold() != str(expected_val).casefold():
                return False
            continue
        if abs(float(current_val) - float(expected_val)) > _PRESET_MATCH_EPSILON:
            return False
    return True


def detect_active_risk_preset() -> str | None:
    current = get_operator_risk_settings()
    for preset_id in _PRESET_ORDER:
        preset = RISK_PROFILE_PRESETS.get(preset_id)
        if not isinstance(preset, dict):
            continue
        values = preset.get("values")
        if isinstance(values, dict) and _settings_match_preset(current, values):
            return preset_id
    return None


def _normalize_preset_id(preset_id: str) -> str:
    return str(preset_id).strip().casefold()


def apply_risk_preset(preset_id: str, *, persist: bool = False) -> OperatorRiskSettings | None:
    preset_key = _normalize_preset_id(preset_id)
    preset = RISK_PROFILE_PRESETS.get(preset_key)
    if not isinstance(preset, dict):
        return None
    values = preset.get("values")
    if not isinstance(values, dict):
        return None
    return update_operator_risk_settings(dict(values), persist=persist)


def bootstrap_operator_risk_settings() -> bool:
    """Acilista, kayitli ham risk ince ayarini (varsa) mevcut durumun UZERINE bindirir.

    Sira onemli: notify-frequency bootstrap'i seviyeyi uyguladiktan SONRA
    cagrilmalidir; boylece kullanicinin elle ince ayari seviyenin uzerinde kalir.
    Dosya yoksa hicbir sey yapmaz (mevcut/geriye-donuk davranis korunur).
    """
    saved = _load_persisted_settings()
    if not saved:
        return False
    try:
        update_operator_risk_settings(saved, persist=False)
    except (TypeError, ValueError):
        return False
    return True


def build_risk_presets_payload() -> list[dict[str, Any]]:
    active = detect_active_risk_preset()
    rows: list[dict[str, Any]] = []
    for preset_id in _PRESET_ORDER:
        preset = RISK_PROFILE_PRESETS[preset_id]
        values = dict(preset["values"])
        rows.append(
            {
                "id": preset_id,
                "label": str(preset["label"]),
                "short": str(preset["short"]),
                "tip": str(preset["tip"]),
                "active": preset_id == active,
                "values": values,
                "summary": {
                    "action_ev_percent": round(float(values["action_ev"]) * 100.0, 1),
                    "risk_per_trade_percent": round(float(values["risk_per_trade"]) * 100.0, 1),
                    "soft_odds_min": float(values["soft_odds_min"]),
                    "soft_odds_max": float(values["soft_odds_max"]),
                    "context_mode": str(values["context_mode"]),
                },
            }
        )
    return rows


def build_slider_payload() -> list[dict[str, Any]]:
    current = get_operator_risk_settings()
    rows: list[dict[str, Any]] = []

    context_mode = str(current.get("context_mode", _CONTEXT_MODE_OFF))
    rows.append(
        {
            "key": "context_mode",
            "label": "Baglam modu",
            "format": "choice",
            "value": context_mode,
            "options": [
                {"value": opt["value"], "label": opt["label"], "tip": opt["tip"]}
                for opt in CONTEXT_MODE_OPTIONS
            ],
            "zone": "acceptable",
        }
    )

    for spec in SLIDER_SPECS:
        key = spec["key"]
        value = float(current[key])
        rows.append(
            {
                "key": key,
                "label": spec["label"],
                "format": spec["format"],
                "value": value,
                "min": float(spec["min"]),
                "max": float(spec["max"]),
                "step": float(spec["step"]),
                "unit": spec["unit"],
                "tip": spec["tip"],
                "zone": classify_slider_zone(key, value),
            }
        )
    return rows
