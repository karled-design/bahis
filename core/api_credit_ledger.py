from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import ODDS_API_KEY, ODDS_API_REGIONS

__all__ = (
    "build_credit_panel_payload",
    "get_daily_allowance",
    "get_last_remaining",
    "get_month_spent",
    "get_today_cap_info",
    "get_today_spent",
    "record_odds_api_usage",
)

# Odds API kredi defteri (tarama diyeti - Adim 1).
# Her API cevabinin basliginda "bu istek kac kredi yakti / kac kaldi" bilgisi
# gelir; burada gune gore biriktirilir. Amac: 500 kredilik aylik hakkin nereye
# gittigini panelden gormek ve sonraki adimlarda gunluk tavani buna baglamak.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LEDGER_PATH = _PROJECT_ROOT / "database" / "api_credit_ledger.json"
_LOCK = threading.Lock()
_DAY_TIMEZONE = ZoneInfo("Europe/Istanbul")
_MAX_DAYS_KEPT = 90
# Gunluk tavan (Adim 3): kalan kredi bilinmiyorsa (yeni anahtar/ilk gun) bu
# taban kullanilir; bilindiginde tavan = kalan // ayin kalan gunu olur.
_DAILY_ALLOWANCE_FALLBACK = 16
# Tek oran sorgusunun tahmini bedeli: bolge sayisi x 2 pazar (h2h,totals).
# Gercek bedel her cevapta x-requests-last ile olculur; bu deger yalnizca
# on-kontrol icindir.
_ODDS_FETCH_COST_ESTIMATE = 2 * max(1, len([part for part in ODDS_API_REGIONS.split(",") if part]))


def _current_key_tail() -> str:
    return ODDS_API_KEY[-4:] if ODDS_API_KEY else ""


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(_DAY_TIMEZONE)


def _day_key(moment: datetime | None = None) -> str:
    return (moment or _local_now()).date().isoformat()


def _month_prefix(moment: datetime | None = None) -> str:
    return (moment or _local_now()).strftime("%Y-%m")


def _parse_credit_number(raw: object) -> float | None:
    """Basliktan gelen '466' / '4.0' gibi degerleri guvenle sayiya cevirir."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _default_ledger() -> dict[str, Any]:
    return {"days": {}, "last_remaining": None, "last_used": None, "updated_at": ""}


def _load_ledger() -> dict[str, Any]:
    if not _LEDGER_PATH.is_file():
        return _default_ledger()
    try:
        payload = json.loads(_LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_ledger()
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), dict):
        return _default_ledger()
    return payload


def _save_ledger(ledger: dict[str, Any]) -> None:
    # Atomik yazim (hero_daily_guard ile ayni desen): once .tmp, sonra tek
    # hamlede yerine koy; baska bir okuma yarim/bozuk JSON gormesin.
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _LEDGER_PATH.with_name(_LEDGER_PATH.name + ".tmp")
    tmp_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, _LEDGER_PATH)


def _prune_old_days(days: dict[str, Any]) -> dict[str, Any]:
    if len(days) <= _MAX_DAYS_KEPT:
        return days
    keep = sorted(days)[-_MAX_DAYS_KEPT:]
    return {key: days[key] for key in keep}


def record_odds_api_usage(
    *,
    last_cost: object = None,
    remaining: object = None,
    used: object = None,
    endpoint: str = "",
) -> None:
    """Tek API cevabinin kota bilgisini deftere isler. ASLA hata firlatmaz."""
    try:
        cost = _parse_credit_number(last_cost)
        remaining_val = _parse_credit_number(remaining)
        used_val = _parse_credit_number(used)
        endpoint_key = str(endpoint or "diger").strip() or "diger"

        with _LOCK:
            ledger = _load_ledger()
            days = ledger.get("days")
            if not isinstance(days, dict):
                days = {}
            day = _day_key()
            entry = days.get(day)
            if not isinstance(entry, dict):
                entry = {"spent": 0.0, "requests": 0, "endpoints": {}}
            entry["spent"] = float(entry.get("spent", 0) or 0) + (cost or 0.0)
            entry["requests"] = int(entry.get("requests", 0) or 0) + 1
            per_endpoint = entry.get("endpoints")
            if not isinstance(per_endpoint, dict):
                per_endpoint = {}
            per_endpoint[endpoint_key] = float(per_endpoint.get(endpoint_key, 0) or 0) + (
                cost or 0.0
            )
            entry["endpoints"] = per_endpoint
            days[day] = entry
            ledger["days"] = _prune_old_days(days)
            if remaining_val is not None:
                ledger["last_remaining"] = remaining_val
            if used_val is not None:
                ledger["last_used"] = used_val
            # Hangi anahtarin kota bilgisi oldugunu isaretle: anahtar
            # degisince eski "kalan" degeri yeni anahtari yaniltmasin.
            ledger["key_tail"] = _current_key_tail()
            ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_ledger(ledger)
    except Exception as exc:  # defter tutulamasa bile tarama asla durmamali
        print(f"[SQE-V1] Kredi defteri yazilamadi | {exc}", file=sys.stderr)


def _day_spent(ledger: dict[str, Any], day: str) -> float:
    entry = ledger.get("days", {}).get(day)
    if not isinstance(entry, dict):
        return 0.0
    value = _parse_credit_number(entry.get("spent"))
    return value if value is not None else 0.0


def get_today_spent() -> float:
    with _LOCK:
        return _day_spent(_load_ledger(), _day_key())


def get_today_endpoint_spent(endpoint: str) -> float:
    """Bugun tek bir uc sinifina (or. "yan_pazar") harcanan kredi."""
    key = str(endpoint or "").strip() or "diger"
    with _LOCK:
        entry = _load_ledger().get("days", {}).get(_day_key())
        if not isinstance(entry, dict):
            return 0.0
        per_endpoint = entry.get("endpoints")
        if not isinstance(per_endpoint, dict):
            return 0.0
        value = _parse_credit_number(per_endpoint.get(key))
        return value if value is not None else 0.0


def get_month_spent(month_prefix: str | None = None) -> float:
    prefix = month_prefix or _month_prefix()
    with _LOCK:
        ledger = _load_ledger()
        return sum(
            _day_spent(ledger, day)
            for day in ledger.get("days", {})
            if isinstance(day, str) and day.startswith(prefix)
        )


def _remaining_for_current_key(ledger: dict[str, Any]) -> float | None:
    """Defterdeki 'kalan' bilgisi, su an .env'deki anahtara aitse dondurur."""
    if str(ledger.get("key_tail", "") or "") != _current_key_tail():
        return None
    return _parse_credit_number(ledger.get("last_remaining"))


def get_last_remaining() -> float | None:
    with _LOCK:
        return _remaining_for_current_key(_load_ledger())


def get_daily_allowance() -> int:
    """Gunluk kredi tavani: kalan // ayin kalan gunu (bilinmiyorsa taban).

    Kalan cok azalsa bile (>= tek sorgu bedeli) gunde en az 1 sorguya izin
    verilir; kalan sifirsa tavan 0'dir (olu anahtarla bosa deneme olmaz).
    """
    remaining = get_last_remaining()
    if remaining is None:
        return _DAILY_ALLOWANCE_FALLBACK
    if remaining < _ODDS_FETCH_COST_ESTIMATE:
        return 0
    return max(1, int(remaining // _days_left_in_month()))


def get_today_cap_info() -> dict[str, Any]:
    """On-kontrol ozeti: bugunku harcama, tavan, daha kac sorguya yeter."""
    allowance = get_daily_allowance()
    spent = get_today_spent()
    left = max(0.0, float(allowance) - spent)
    affordable = int(left // _ODDS_FETCH_COST_ESTIMATE)
    return {
        "allowance": allowance,
        "spent": spent,
        "affordable_fetches": affordable,
        "cap_reached": affordable <= 0,
    }


def _days_left_in_month(moment: datetime | None = None) -> int:
    now = moment or _local_now()
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    return max(1, (next_month.date() - now.date()).days)


def _allowance_from_remaining(remaining: float | None) -> int:
    if remaining is None:
        return _DAILY_ALLOWANCE_FALLBACK
    if remaining < _ODDS_FETCH_COST_ESTIMATE:
        return 0
    return max(1, int(remaining // _days_left_in_month()))


def build_credit_panel_payload() -> dict[str, Any]:
    """Panel icin ozet: bugun/ay harcanan, kalan, gunluk tavan durumu."""
    with _LOCK:
        ledger = _load_ledger()
        today = _day_key()
        today_spent = _day_spent(ledger, today)
        month_spent = sum(
            _day_spent(ledger, day)
            for day in ledger.get("days", {})
            if isinstance(day, str) and day.startswith(_month_prefix())
        )
        remaining = _remaining_for_current_key(ledger)
        used = _parse_credit_number(ledger.get("last_used")) if remaining is not None else None
        updated_at = str(ledger.get("updated_at", "") or "")

    plan_size = (remaining + used) if remaining is not None and used is not None else None
    days_left = _days_left_in_month()
    daily_allowance = _allowance_from_remaining(remaining)
    cap_reached = (float(daily_allowance) - today_spent) < _ODDS_FETCH_COST_ESTIMATE

    if updated_at:
        remaining_text = str(int(remaining)) if remaining is not None else "?"
        status_label = (
            f"Bugun {int(round(today_spent))}/{daily_allowance} kredi | Kalan {remaining_text}"
        )
        if cap_reached:
            status_label += " — gunluk tavan doldu"
    else:
        status_label = "Henuz API kullanimi olculmedi"

    return {
        "today_spent": round(today_spent, 1),
        "month_spent": round(month_spent, 1),
        "remaining": remaining,
        "plan_size": plan_size,
        "days_left_in_month": days_left,
        "daily_allowance": daily_allowance,
        "cap_reached": cap_reached,
        "updated_at": updated_at,
        "status_label": status_label,
    }
