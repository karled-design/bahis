from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import HERO_MODE as _ENV_HERO_DEFAULT

__all__ = (
    "bootstrap_hero_mode",
    "build_hero_panel_payload",
    "is_hero_mode_enabled",
    "set_hero_mode_enabled",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "hero_mode.json"
_LOCK = threading.Lock()


def _load_persisted_enabled() -> bool | None:
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if "enabled" not in payload:
        return None
    return bool(payload.get("enabled"))


def _save_persisted_enabled(enabled: bool) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(enabled),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bootstrap_hero_mode() -> bool:
    return is_hero_mode_enabled()


def is_hero_mode_enabled() -> bool:
    with _LOCK:
        persisted = _load_persisted_enabled()
        if persisted is not None:
            return persisted
        return bool(_ENV_HERO_DEFAULT)


def set_hero_mode_enabled(enabled: bool, *, persist: bool = True) -> bool:
    from core.hero_measurement import ensure_hero_measurement_started

    normalized = bool(enabled)
    with _LOCK:
        if persist:
            _save_persisted_enabled(normalized)
    if normalized:
        ensure_hero_measurement_started()
    return normalized


def build_hero_panel_payload() -> dict[str, Any]:
    from core.hero_daily_guard import build_hero_daily_payload
    from core.hero_measurement import build_hero_measurement_payload
    from core.hero_profile import build_hero_profile_payload
    from core.hero_weekly_guard import build_hero_weekly_payload
    from database.db_manager import get_recent_settled_win_rate

    return {
        "enabled": is_hero_mode_enabled(),
        "env_default": bool(_ENV_HERO_DEFAULT),
        "profile": build_hero_profile_payload(),
        "daily": build_hero_daily_payload(),
        "weekly": build_hero_weekly_payload(),
        "win_rate_last_20": get_recent_settled_win_rate(limit=20),
        "measurement": build_hero_measurement_payload(),
    }
