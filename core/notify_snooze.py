"""Bildirim erteleme defteri: operator "Ertele" dedigi sinyali gecici susturur.

Erteleme suresi dolunca ayni mac+pazar icin TEK bir hatirlatma bildirimine izin
verilir; bunun icin dedup kapisi o tur atlanir. Defter diskte tutulur, boylece
motor yeniden baslasa da erteleme kaybolmaz. Para hareketi yoktur.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

__all__ = (
    "DEFAULT_SNOOZE_SECONDS",
    "SNOOZE_STATE_PATH",
    "consume_expired_snooze",
    "is_snoozed",
    "snooze_match",
    "snooze_remaining_seconds",
)

DEFAULT_SNOOZE_SECONDS = 30 * 60
SNOOZE_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "notify_snooze.json"

_OPERATOR_DIAG = "Donanim Erisilemiyor: Erteleme Defteri Hatasi"
_STATE_LOCK = threading.Lock()
_MAX_ENTRY_AGE_SECONDS = 24 * 3600


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _read_state() -> dict[str, float]:
    try:
        payload = json.loads(SNOOZE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            state[key] = float(value)
    return state


def _write_state(state: dict[str, float]) -> bool:
    temporary = SNOOZE_STATE_PATH.with_suffix(".json.tmp")
    try:
        SNOOZE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SNOOZE_STATE_PATH)
        return True
    except OSError as exc:
        _emit_operator_diag(f"defter yazilamadi | {exc}")
        return False


def _prune(state: dict[str, float], now: float) -> dict[str, float]:
    return {
        key: due
        for key, due in state.items()
        if (now - due) < _MAX_ENTRY_AGE_SECONDS
    }


def snooze_match(
    match_id: str,
    *,
    seconds: int = DEFAULT_SNOOZE_SECONDS,
    now: float | None = None,
) -> float | None:
    """Sinyali `seconds` boyunca susturur; hatirlatma zamanini (epoch) doner."""
    key = str(match_id).strip()
    if not key or seconds <= 0:
        return None
    current = time.time() if now is None else float(now)
    due = current + float(seconds)
    with _STATE_LOCK:
        state = _prune(_read_state(), current)
        state[key] = due
        if not _write_state(state):
            return None
    return due


def snooze_remaining_seconds(match_id: str, *, now: float | None = None) -> float:
    key = str(match_id).strip()
    if not key:
        return 0.0
    current = time.time() if now is None else float(now)
    due = _read_state().get(key)
    if due is None:
        return 0.0
    return max(0.0, due - current)


def is_snoozed(match_id: str, *, now: float | None = None) -> bool:
    return snooze_remaining_seconds(match_id, now=now) > 0.0


def consume_expired_snooze(match_id: str, *, now: float | None = None) -> bool:
    """Suresi dolmus erteleme varsa defterden siler ve True doner.

    True donmesi "bu sinyal icin hatirlatma bildirimi gonderilmeli" demektir;
    cagiran taraf dedup kapisini o tur atlar. Tekrar cagrildiginda False doner,
    yani hatirlatma tek seferliktir.
    """
    key = str(match_id).strip()
    if not key:
        return False
    current = time.time() if now is None else float(now)
    with _STATE_LOCK:
        state = _read_state()
        due = state.get(key)
        if due is None or due > current:
            return False
        del state[key]
        _write_state(_prune(state, current))
    return True
