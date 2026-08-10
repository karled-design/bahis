from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.hero_daily_guard import hero_local_date_key
from core.hero_mode import is_hero_mode_enabled
from database.db_manager import get_latest_bakiye, get_settled_kupon_stats_since

__all__ = (
    "MEASUREMENT_MAX_DRAWDOWN_PERCENT",
    "MEASUREMENT_MIN_ACTIVE_DAYS",
    "MEASUREMENT_MIN_COUPONS",
    "MEASUREMENT_TARGET_WIN_RATE_PERCENT",
    "build_hero_measurement_payload",
    "ensure_hero_measurement_started",
    "record_hero_measurement_scan_day",
    "reset_hero_measurement",
)

MEASUREMENT_MIN_COUPONS = 30
MEASUREMENT_MIN_ACTIVE_DAYS = 7
MEASUREMENT_TARGET_WIN_RATE_PERCENT = 52.0
MEASUREMENT_MAX_DRAWDOWN_PERCENT = 15.0

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = _PROJECT_ROOT / "database" / "hero_measurement.json"
_LOCK = threading.Lock()

MeasurementPhase = Literal["idle", "collecting", "pass", "review"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _default_state() -> dict[str, Any]:
    return {
        "started_at": "",
        "starting_kasa": 0.0,
        "scan_days": [],
        "completed_at": "",
        "last_verdict": "idle",
    }


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return _default_state()
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    scan_days_raw = payload.get("scan_days")
    scan_days = (
        [str(item).strip() for item in scan_days_raw if str(item).strip()]
        if isinstance(scan_days_raw, list)
        else []
    )
    return {
        "started_at": str(payload.get("started_at", "")).strip(),
        "starting_kasa": round(float(payload.get("starting_kasa", 0.0) or 0.0), 2),
        "scan_days": scan_days,
        "completed_at": str(payload.get("completed_at", "")).strip(),
        "last_verdict": str(payload.get("last_verdict", "idle")).strip() or "idle",
    }


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_hero_measurement() -> None:
    with _LOCK:
        _save_state(_default_state())


def ensure_hero_measurement_started(*, starting_kasa: float | None = None) -> None:
    with _LOCK:
        state = _load_state()
        if state["started_at"]:
            return
        kasa_value = round(float(starting_kasa if starting_kasa is not None else get_latest_bakiye()), 2)
        state = {
            "started_at": _utc_now_iso(),
            "starting_kasa": kasa_value,
            "scan_days": [],
            "completed_at": "",
            "last_verdict": "collecting",
        }
        _save_state(state)


def record_hero_measurement_scan_day(*, date_key: str | None = None) -> None:
    if not is_hero_mode_enabled():
        return
    day_key = date_key or hero_local_date_key()
    with _LOCK:
        state = _load_state()
        if not state["started_at"]:
            kasa_value = round(float(get_latest_bakiye()), 2)
            state = {
                "started_at": _utc_now_iso(),
                "starting_kasa": kasa_value,
                "scan_days": [],
                "completed_at": "",
                "last_verdict": "collecting",
            }
        scan_days = list(state.get("scan_days") or [])
        if day_key not in scan_days:
            scan_days.append(day_key)
        state["scan_days"] = scan_days
        _save_state(state)


def _recommendation_for_review(
    *,
    win_rate: float,
    kasa_change_percent: float,
    settled_coupons: int,
) -> str:
    if settled_coupons < MEASUREMENT_MIN_COUPONS:
        remaining = MEASUREMENT_MIN_COUPONS - settled_coupons
        return f"Olculuyor: en az {remaining} kupon daha ve tarama gunleri tamamlansin."
    if win_rate < MEASUREMENT_TARGET_WIN_RATE_PERCENT:
        return "Win rate hedefin altinda — Kahraman profilinde Azalt deneyin (daha secici)."
    if kasa_change_percent < -MEASUREMENT_MAX_DRAWDOWN_PERCENT:
        return "Butce dususu sinirin ustunde — stake veya profil seviyesini dusurun."
    return "Sonuclar karisik — bir hafta daha veri toplayin, panik degil profil ayari."


def _resolve_phase(
    *,
    active: bool,
    settled_coupons: int,
    active_scan_days: int,
    win_rate: float,
    kasa_change_percent: float,
) -> MeasurementPhase:
    if not active or not _load_state()["started_at"]:
        return "idle"
    if settled_coupons < MEASUREMENT_MIN_COUPONS or active_scan_days < MEASUREMENT_MIN_ACTIVE_DAYS:
        return "collecting"
    win_ok = win_rate >= MEASUREMENT_TARGET_WIN_RATE_PERCENT
    kasa_ok = kasa_change_percent >= -MEASUREMENT_MAX_DRAWDOWN_PERCENT
    if win_ok and kasa_ok:
        return "pass"
    return "review"


def build_hero_measurement_payload() -> dict[str, Any]:
    state = _load_state()
    active = is_hero_mode_enabled() and bool(state["started_at"])
    started_at = state["started_at"]
    starting_kasa = float(state["starting_kasa"] or 0.0)
    current_kasa = round(float(get_latest_bakiye()), 2)
    scan_days = list(state.get("scan_days") or [])
    active_scan_days = len(scan_days)

    stats = (
        get_settled_kupon_stats_since(started_at)
        if started_at
        else {"settled": 0, "won": 0, "lost": 0, "win_rate_percent": 0.0}
    )
    settled_coupons = int(stats.get("settled", 0))
    win_rate = float(stats.get("win_rate_percent", 0.0))

    if starting_kasa > 0.0:
        kasa_change_percent = round(((current_kasa - starting_kasa) / starting_kasa) * 100.0, 1)
    else:
        kasa_change_percent = 0.0

    phase = _resolve_phase(
        active=active,
        settled_coupons=settled_coupons,
        active_scan_days=active_scan_days,
        win_rate=win_rate,
        kasa_change_percent=kasa_change_percent,
    )

    coupon_progress = min(100, int((settled_coupons / MEASUREMENT_MIN_COUPONS) * 100)) if MEASUREMENT_MIN_COUPONS else 0
    day_progress = min(100, int((active_scan_days / MEASUREMENT_MIN_ACTIVE_DAYS) * 100)) if MEASUREMENT_MIN_ACTIVE_DAYS else 0
    progress_percent = min(coupon_progress, day_progress)

    status_label = (
        f"{settled_coupons}/{MEASUREMENT_MIN_COUPONS} kupon · "
        f"{active_scan_days}/{MEASUREMENT_MIN_ACTIVE_DAYS} gun · "
        f"Win %{win_rate:.1f} · Butce %{kasa_change_percent:+.1f}"
    )

    if phase == "idle":
        verdict_title = "Olcek kapali"
        verdict_detail = "HERO modunu acin; butcenizi Kasa sekmesinden girin. Gercek mac, gercek Nesine akisi."
        recommendation = "Baslangic butcesi = panelde kaydettiginiz kasa tutari."
    elif phase == "collecting":
        verdict_title = "Olculuyor"
        verdict_detail = (
            "Gercek kuponlar ve gercek sonuclar sayilir. Hedef: "
            f"{MEASUREMENT_MIN_COUPONS} kupon, {MEASUREMENT_MIN_ACTIVE_DAYS} tarama gunu."
        )
        recommendation = _recommendation_for_review(
            win_rate=win_rate,
            kasa_change_percent=kasa_change_percent,
            settled_coupons=settled_coupons,
        )
    elif phase == "pass":
        verdict_title = "Olcek basarili"
        verdict_detail = (
            f"Win rate %{win_rate:.1f} (hedef >= %{MEASUREMENT_TARGET_WIN_RATE_PERCENT:.0f}), "
            f"butce degisimi %{kasa_change_percent:+.1f}."
        )
        recommendation = "Profili koruyabilir veya Temkinli seviyeye sikilastirabilirsiniz."
    else:
        verdict_title = "Gozden gecir"
        verdict_detail = (
            f"Yeterli veri var ({settled_coupons} kupon) ama hedeflerin en az biri karsilanmadi."
        )
        recommendation = _recommendation_for_review(
            win_rate=win_rate,
            kasa_change_percent=kasa_change_percent,
            settled_coupons=settled_coupons,
        )

    completed_at = state.get("completed_at") or ""
    if phase in {"pass", "review"} and not completed_at:
        with _LOCK:
            fresh = _load_state()
            fresh["completed_at"] = _utc_now_iso()
            fresh["last_verdict"] = phase
            _save_state(fresh)
        completed_at = fresh["completed_at"]
    elif phase == "collecting":
        with _LOCK:
            fresh = _load_state()
            if fresh.get("completed_at"):
                fresh["completed_at"] = ""
                fresh["last_verdict"] = "collecting"
                _save_state(fresh)

    return {
        "active": active,
        "started_at": started_at,
        "starting_kasa": starting_kasa,
        "current_kasa": current_kasa,
        "settled_coupons": settled_coupons,
        "target_coupons": MEASUREMENT_MIN_COUPONS,
        "active_scan_days": active_scan_days,
        "target_scan_days": MEASUREMENT_MIN_ACTIVE_DAYS,
        "win_rate_percent": win_rate,
        "target_win_rate_percent": MEASUREMENT_TARGET_WIN_RATE_PERCENT,
        "kasa_change_percent": kasa_change_percent,
        "max_drawdown_percent": MEASUREMENT_MAX_DRAWDOWN_PERCENT,
        "phase": phase,
        "progress_percent": progress_percent,
        "status_label": status_label,
        "verdict_title": verdict_title,
        "verdict_detail": verdict_detail,
        "recommendation": recommendation,
        "completed_at": completed_at,
    }
