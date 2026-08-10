from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from core.hero_profile import get_active_hero_profile_values
from database.db_manager import count_kupon_losses_between

__all__ = (
    "HERO_DAILY_LOSS_STOP",
    "HERO_DAY_TIMEZONE",
    "HeroDailyStatus",
    "build_hero_daily_limit_message",
    "build_hero_daily_payload",
    "build_hero_daily_status",
    "evaluate_hero_daily_send",
    "hero_local_date_key",
    "mark_hero_daily_notice_sent",
    "maybe_should_send_hero_daily_notice",
    "record_hero_alert_sent",
    "refresh_hero_daily_losses",
)

HERO_DAY_TIMEZONE = ZoneInfo("Europe/Istanbul")
HERO_DAILY_LOSS_STOP = 2
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "database" / "hero_daily_state.json"
_LOCK = threading.Lock()

HeroBlockReason = Literal["", "daily_limit", "loss_stop", "weekly_stop"]
NoticeKind = Literal["daily_limit", "loss_stop", "weekly_stop"]


class HeroDailyStatus(TypedDict):
    date_key: str
    alerts_sent: int
    max_alerts: int
    losses_today: int
    max_losses: int
    can_send: bool
    block_reason: HeroBlockReason


def hero_local_date_key(*, reference: datetime | None = None) -> str:
    moment = reference or datetime.now(timezone.utc)
    return moment.astimezone(HERO_DAY_TIMEZONE).date().isoformat()


def _istanbul_day_utc_bounds(date_key: str) -> tuple[str, str]:
    year, month, day = (int(part) for part in date_key.split("-"))
    start_local = datetime(year, month, day, 0, 0, 0, tzinfo=HERO_DAY_TIMEZONE)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return start_utc, end_utc


def _default_state(date_key: str) -> dict[str, Any]:
    return {
        "date_key": date_key,
        "alerts_sent": 0,
        "limit_notice_sent": False,
        "loss_stop_notice_sent": False,
    }


def _load_state(date_key: str) -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return _default_state(date_key)
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state(date_key)
    if str(payload.get("date_key", "")).strip() != date_key:
        return _default_state(date_key)
    return {
        "date_key": date_key,
        "alerts_sent": max(0, int(payload.get("alerts_sent", 0) or 0)),
        "limit_notice_sent": bool(payload.get("limit_notice_sent", False)),
        "loss_stop_notice_sent": bool(payload.get("loss_stop_notice_sent", False)),
    }


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_hero_daily_losses(*, date_key: str | None = None) -> int:
    day_key = date_key or hero_local_date_key()
    start_utc, end_utc = _istanbul_day_utc_bounds(day_key)
    return count_kupon_losses_between(start_utc, end_utc)


def build_hero_daily_status(*, date_key: str | None = None) -> HeroDailyStatus:
    from core.hero_weekly_guard import evaluate_hero_weekly_send

    day_key = date_key or hero_local_date_key()
    profile = get_active_hero_profile_values()
    max_alerts = max(1, int(profile["max_daily_picks"]))
    max_losses = HERO_DAILY_LOSS_STOP

    with _LOCK:
        state = _load_state(day_key)
        alerts_sent = int(state["alerts_sent"])

    losses_today = refresh_hero_daily_losses(date_key=day_key)

    block_reason: HeroBlockReason = ""
    weekly_ok, weekly_reason = evaluate_hero_weekly_send()
    if not weekly_ok:
        block_reason = weekly_reason  # type: ignore[assignment]
    elif losses_today >= max_losses:
        block_reason = "loss_stop"
    elif alerts_sent >= max_alerts:
        block_reason = "daily_limit"

    return {
        "date_key": day_key,
        "alerts_sent": alerts_sent,
        "max_alerts": max_alerts,
        "losses_today": losses_today,
        "max_losses": max_losses,
        "can_send": block_reason == "",
        "block_reason": block_reason,
    }


def evaluate_hero_daily_send() -> tuple[bool, HeroBlockReason]:
    status = build_hero_daily_status()
    return status["can_send"], status["block_reason"]


def record_hero_alert_sent() -> HeroDailyStatus:
    day_key = hero_local_date_key()
    with _LOCK:
        state = _load_state(day_key)
        state["alerts_sent"] = int(state["alerts_sent"]) + 1
        _save_state(state)
    return build_hero_daily_status(date_key=day_key)


def maybe_should_send_hero_daily_notice(status: HeroDailyStatus) -> NoticeKind | None:
    if status["block_reason"] not in {"daily_limit", "loss_stop", "weekly_stop"}:
        return None

    day_key = status["date_key"]
    with _LOCK:
        state = _load_state(day_key)
        if status["block_reason"] == "weekly_stop":
            from core.hero_weekly_guard import maybe_should_send_hero_weekly_notice

            if maybe_should_send_hero_weekly_notice():
                return "weekly_stop"
            return None
        if status["block_reason"] == "loss_stop":
            if state.get("loss_stop_notice_sent"):
                return None
            return "loss_stop"
        if state.get("limit_notice_sent"):
            return None
        return "daily_limit"


def mark_hero_daily_notice_sent(kind: NoticeKind) -> None:
    if kind == "weekly_stop":
        from core.hero_weekly_guard import mark_hero_weekly_notice_sent

        mark_hero_weekly_notice_sent()
        return
    day_key = hero_local_date_key()
    with _LOCK:
        state = _load_state(day_key)
        if kind == "loss_stop":
            state["loss_stop_notice_sent"] = True
        else:
            state["limit_notice_sent"] = True
        _save_state(state)


def build_hero_daily_limit_message(status: HeroDailyStatus, *, kind: NoticeKind) -> str:
    if kind == "weekly_stop":
        from core.hero_weekly_guard import build_hero_weekly_stop_message

        return build_hero_weekly_stop_message()
    if kind == "loss_stop":
        return (
            "[SQE-V1] Bugun mola\n\n"
            f"Bugun {status['losses_today']} kayip oldu. Kahraman modu bugun yeni oneri gondermez.\n"
            "Yarin tekrar devam eder."
        )

    return (
        "[SQE-V1] Bugun doldu\n\n"
        f"Gunluk oneri limitine ulasildi ({status['alerts_sent']}/{status['max_alerts']}).\n"
        "Yeni sinyal yarin veya limit sifirlaninca gelir."
    )


def build_hero_daily_payload() -> dict[str, Any]:
    status = build_hero_daily_status()
    return {
        "date_key": status["date_key"],
        "alerts_sent": status["alerts_sent"],
        "max_alerts": status["max_alerts"],
        "losses_today": status["losses_today"],
        "max_losses": status["max_losses"],
        "can_send": status["can_send"],
        "block_reason": status["block_reason"],
        "status_label": (
            f"{status['alerts_sent']}/{status['max_alerts']} oneri · "
            f"{status['losses_today']} kayip"
        ),
    }
