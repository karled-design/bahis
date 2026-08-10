from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.hero_daily_guard import HERO_DAY_TIMEZONE, hero_local_date_key
from core.hero_mode import is_hero_mode_enabled
from database.db_manager import get_latest_bakiye

__all__ = (
    "HERO_WEEKLY_DRAWDOWN_STOP",
    "build_hero_weekly_payload",
    "build_hero_weekly_stop_message",
    "evaluate_hero_weekly_send",
    "hero_local_week_key",
    "mark_hero_weekly_notice_sent",
    "maybe_should_send_hero_weekly_notice",
    "refresh_hero_weekly_guard",
    "reset_hero_weekly_guard",
)

HERO_WEEKLY_DRAWDOWN_STOP = 0.12
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "database" / "hero_weekly_state.json"
_LOCK = threading.Lock()


def hero_local_week_key(*, reference: datetime | None = None) -> str:
    moment = reference or datetime.now(timezone.utc)
    local_date = moment.astimezone(HERO_DAY_TIMEZONE).date()
    monday = local_date - timedelta(days=local_date.weekday())
    return monday.isoformat()


def _default_state(week_key: str, *, week_start_kasa: float) -> dict[str, Any]:
    return {
        "week_key": week_key,
        "week_start_kasa": round(float(week_start_kasa), 2),
        "weekly_stop_active": False,
        "weekly_stop_notice_sent": False,
    }


def _load_state() -> dict[str, Any]:
    week_key = hero_local_week_key()
    if not _STATE_PATH.is_file():
        return _default_state(week_key, week_start_kasa=get_latest_bakiye())
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state(week_key, week_start_kasa=get_latest_bakiye())
    stored_week = str(payload.get("week_key", "")).strip()
    if stored_week != week_key:
        return _default_state(week_key, week_start_kasa=get_latest_bakiye())
    return {
        "week_key": week_key,
        "week_start_kasa": round(float(payload.get("week_start_kasa", 0.0) or 0.0), 2),
        "weekly_stop_active": bool(payload.get("weekly_stop_active", False)),
        "weekly_stop_notice_sent": bool(payload.get("weekly_stop_notice_sent", False)),
    }


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_hero_weekly_guard() -> None:
    with _LOCK:
        _save_state(_default_state(hero_local_week_key(), week_start_kasa=get_latest_bakiye()))


def refresh_hero_weekly_guard() -> dict[str, Any]:
    week_key = hero_local_week_key()
    current_kasa = round(float(get_latest_bakiye()), 2)

    with _LOCK:
        state = _load_state()
        if state["week_key"] != week_key:
            state = _default_state(week_key, week_start_kasa=current_kasa)

        week_start = float(state["week_start_kasa"] or 0.0)
        if week_start <= 0.0:
            week_start = current_kasa
            state["week_start_kasa"] = current_kasa

        drawdown = 0.0
        if week_start > 0.0:
            drawdown = round(((week_start - current_kasa) / week_start) * 100.0, 1)

        stop_active = drawdown >= round(HERO_WEEKLY_DRAWDOWN_STOP * 100.0, 1)
        if not stop_active:
            state["weekly_stop_notice_sent"] = False
        state["weekly_stop_active"] = stop_active
        _save_state(state)

    return build_hero_weekly_payload(state=state, current_kasa=current_kasa, drawdown_percent=drawdown)


def build_hero_weekly_payload(
    *,
    state: dict[str, Any] | None = None,
    current_kasa: float | None = None,
    drawdown_percent: float | None = None,
) -> dict[str, Any]:
    loaded = state or _load_state()
    kasa_value = round(float(current_kasa if current_kasa is not None else get_latest_bakiye()), 2)
    week_start = float(loaded.get("week_start_kasa", 0.0) or 0.0)
    if drawdown_percent is None:
        drawdown_percent = (
            round(((week_start - kasa_value) / week_start) * 100.0, 1) if week_start > 0.0 else 0.0
        )
    stop_active = bool(loaded.get("weekly_stop_active", False))
    return {
        "week_key": str(loaded.get("week_key", hero_local_week_key())),
        "week_start_kasa": week_start,
        "current_kasa": kasa_value,
        "drawdown_percent": float(drawdown_percent),
        "max_drawdown_percent": round(HERO_WEEKLY_DRAWDOWN_STOP * 100.0, 1),
        "weekly_stop_active": stop_active,
        "status_label": (
            f"Hafta basi {week_start:.0f} TL · Simdi {kasa_value:.0f} TL · "
            f"Dusus %{float(drawdown_percent):.1f}"
        ),
    }


def evaluate_hero_weekly_send() -> tuple[bool, str]:
    if not is_hero_mode_enabled():
        return True, ""
    payload = refresh_hero_weekly_guard()
    if payload["weekly_stop_active"]:
        return False, "weekly_stop"
    return True, ""


def maybe_should_send_hero_weekly_notice() -> bool:
    payload = refresh_hero_weekly_guard()
    if not payload["weekly_stop_active"]:
        return False
    state = _load_state()
    return not bool(state.get("weekly_stop_notice_sent", False))


def mark_hero_weekly_notice_sent() -> None:
    with _LOCK:
        state = _load_state()
        state["weekly_stop_notice_sent"] = True
        _save_state(state)


def build_hero_weekly_stop_message(payload: dict[str, Any] | None = None) -> str:
    data = payload or build_hero_weekly_payload()
    return (
        "[SQE-V1] Haftalik mola\n\n"
        f"Butceniz bu hafta basindan %{data['drawdown_percent']:.1f} dustu "
        f"(sinir -%{data['max_drawdown_percent']:.0f}).\n"
        "Pazartesi yeni hafta baslayana kadar yeni HERO onerisi gonderilmez."
    )
