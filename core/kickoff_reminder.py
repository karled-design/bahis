"""Son cagri: kick-off'a yaklasmis, hala degerli ama oynanmamis bahisleri
bir kez daha hatirlatir.

Tasarim: tarama motoruna DOKUNMAZ. Ana dongu her turda, o turun taze
'qualifying_candidates' listesiyle maybe_send_kickoff_reminders() cagirir.
Liste zaten taze oranla filtrelenmis oldugundan, listede olan bir mac
"deger hala gecerli" demektir -- bayat bahse itmeyiz.

Kural: bir mac yalnizca
  - baslamasina 20-60 dk kaldiysa (oynamaya vakit olsun),
  - henuz oynanmadiysa (o mac icin kupon yok),
  - daha once (en az ~10 dk once) bildirildiyse (bu bir HATIRLATMA),
  - ve daha once hatirlatilmadiysa (mac basina tek sefer)
hatirlatilir. Gece maci gece kalir; bu ozellik gunduz/aksam maclari icin
en cok ise yarar.

Gonderim tek atislidir: send_alert_func disaridan verilir (telegram_worker.
send_alert), bypass_scan_gate=True ile cagirilir. send_alert dedup tablosuna
kendisi yazmadigi icin bu hatirlatma gunluk "kac bildirim gitti" sayisini
sismez.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from database.db_manager import (
    get_fixture_last_notified_at,
    has_any_kupon_for_fixture,
)

__all__ = (
    "REMINDER_MAX_LEAD_MIN",
    "REMINDER_MIN_LEAD_MIN",
    "build_reminder_message",
    "maybe_send_kickoff_reminders",
)

REMINDER_MAX_LEAD_MIN = 60
REMINDER_MIN_LEAD_MIN = 20
_MIN_PRIOR_AGE_SECONDS = 600.0  # ilk bildirimden en az 10 dk gecmis olmali
_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "kickoff_reminder_state.json"
_STATE_TTL_SECONDS = 24 * 3600


def _parse_iso_utc(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_state() -> dict[str, float]:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    now = time.time()
    clean: dict[str, float] = {}
    for key, value in data.items():
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if now - ts < _STATE_TTL_SECONDS:
            clean[str(key)] = ts
    return clean


def _save_state(state: dict[str, float]) -> None:
    tmp_path = _STATE_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=True, indent=2)
        os.replace(tmp_path, _STATE_PATH)
    except OSError:
        pass


def _minutes_to_kickoff(commence_time: object, *, now: float) -> float | None:
    kickoff = _parse_iso_utc(commence_time)
    if kickoff is None:
        return None
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    return (kickoff - now_dt).total_seconds() / 60.0


def build_reminder_message(candidate: dict[str, Any], minutes_left: int) -> str:
    base = str(candidate.get("alert_message", "")).strip()
    banner = (
        f"⏰ SON CAGRI · baslamasina ~{minutes_left} dk\n"
        "Bu firsat hala gecerli, henuz oynamadin.\n\n"
    )
    return banner + base


def maybe_send_kickoff_reminders(
    candidates: list[dict[str, Any]],
    send_alert_func: Callable[..., bool],
    *,
    now: float | None = None,
) -> int:
    """Uygun adaylara tek 'son cagri' gonderir; gonderilen sayisini doner."""
    if not candidates:
        return 0

    reference = now if now is not None else time.time()
    state = _load_state()
    sent_count = 0

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        notify_key = str(candidate.get("notify_key", "")).strip()
        if not notify_key or notify_key in state:
            continue
        if str(candidate.get("tier", "")).upper() == "WATCH":
            continue

        match_record = candidate.get("match_record")
        commence_time = ""
        if isinstance(match_record, dict):
            commence_time = str(match_record.get("commence_time", "")).strip()

        minutes_left = _minutes_to_kickoff(commence_time, now=reference)
        if minutes_left is None:
            continue
        if not (REMINDER_MIN_LEAD_MIN <= minutes_left <= REMINDER_MAX_LEAD_MIN):
            continue

        mac_adi = str(candidate.get("match_name", "")).strip()
        market = str(candidate.get("market", "")).strip()
        if has_any_kupon_for_fixture(mac_adi, market):
            continue

        prior = _parse_iso_utc(get_fixture_last_notified_at(notify_key))
        if prior is None:
            continue
        if reference - prior.timestamp() < _MIN_PRIOR_AGE_SECONDS:
            continue

        message = build_reminder_message(candidate, int(round(minutes_left)))
        match_id = candidate.get("match_id")
        stake = candidate.get("stake")
        normalized_stake = (
            float(stake)
            if isinstance(stake, (int, float)) and not isinstance(stake, bool)
            else None
        )

        sent = send_alert_func(
            message=message,
            match_id=str(match_id) if match_id else None,
            stake=normalized_stake,
            soft_odds=candidate.get("soft_odds"),
            mac_adi=mac_adi or None,
            market=market or None,
            sport_key=str(candidate.get("sport_key", "")).strip() or None,
            commence_time=commence_time or None,
            notify_key=notify_key,
            bypass_scan_gate=True,
        )
        if sent:
            state[notify_key] = reference
            sent_count += 1

    if sent_count:
        _save_state(state)
    return sent_count
