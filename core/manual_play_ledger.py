"""Elle oynama defteri: operator "Oynadim" dedigi sinyali isaretler.

Olcum modunda gercek kupon acilmaz, bakiye degismez. Bu defter yalnizca
"bu sinyali Nesine'de kendim oynadim" bilgisini tutar; CLV olcumu zaten
`olcum_sinyalleri` uzerinden yurur. Ayni sinyal iki kez isaretlenirse ikinci
kayit yazilmaz (idempotent).
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = (
    "MANUAL_PLAY_STATE_PATH",
    "is_marked_played",
    "mark_played",
    "marked_plays",
)

MANUAL_PLAY_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "manual_play.json"

_OPERATOR_DIAG = "Donanim Erisilemiyor: Elle Oynama Defteri Hatasi"
_STATE_LOCK = threading.Lock()


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _read_entries() -> list[dict[str, Any]]:
    try:
        payload = json.loads(MANUAL_PLAY_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_entries(entries: list[dict[str, Any]]) -> bool:
    temporary = MANUAL_PLAY_STATE_PATH.with_suffix(".json.tmp")
    try:
        MANUAL_PLAY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(MANUAL_PLAY_STATE_PATH)
        return True
    except OSError as exc:
        _emit_operator_diag(f"defter yazilamadi | {exc}")
        return False


def marked_plays() -> list[dict[str, Any]]:
    return _read_entries()


def is_marked_played(match_id: str) -> bool:
    key = str(match_id).strip()
    if not key:
        return False
    return any(str(entry.get("match_id", "")) == key for entry in _read_entries())


def mark_played(match_id: str, *, mac_adi: str = "", market: str = "") -> bool:
    """Sinyali "oynadim" olarak isaretler. Zaten isaretliyse False doner."""
    key = str(match_id).strip()
    if not key:
        return False
    with _STATE_LOCK:
        entries = _read_entries()
        if any(str(entry.get("match_id", "")) == key for entry in entries):
            return False
        entries.append(
            {
                "match_id": key,
                "mac_adi": str(mac_adi),
                "market": str(market),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return _write_entries(entries)
