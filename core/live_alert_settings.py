"""Canli mac bildirimleri aç/kapat ayari.

Amac: operator disaridayken, maci baslamis (canli/in-play) firsatlar icin
Telegram bildirimi gelmesin. Bu sinyaller hemen oynanmayi gerektirdiginden,
ulasilamayan operator icin bosa bildirimdir. Mac ONCESI sinyaller etkilenmez;
operator donunce onlari rahatca oynayabilir.

Varsayilan ACIK (mevcut davranis). Panelden kapatilinca diske yazilir ve
motor yeniden baslasa bile korunur (hero_mode.json ile ayni desen).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = (
    "bootstrap_live_alerts",
    "is_live_alerts_enabled",
    "match_has_started",
    "set_live_alerts_enabled",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "live_alerts.json"
_LOCK = threading.Lock()
_DEFAULT_ENABLED = True


def _load_persisted_enabled() -> bool | None:
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "enabled" not in payload:
        return None
    return bool(payload.get("enabled"))


def _save_persisted_enabled(enabled: bool) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(enabled),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bootstrap_live_alerts() -> bool:
    return is_live_alerts_enabled()


def is_live_alerts_enabled() -> bool:
    with _LOCK:
        persisted = _load_persisted_enabled()
        if persisted is not None:
            return persisted
        return _DEFAULT_ENABLED


def set_live_alerts_enabled(enabled: bool) -> bool:
    normalized = bool(enabled)
    with _LOCK:
        _save_persisted_enabled(normalized)
    return normalized


def _parse_commence_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def match_has_started(match: dict[str, Any], *, reference_at: float | None = None) -> bool:
    """Mac baslamis mi (canli/in-play)? commence_time <= simdi ise True.

    commence_time yoksa veya cozulemezse False doner (mac oncesi/bilinmiyor
    sayilir; bildirim engellenmez). Bu tanim beginner_alert'teki
    "🔴 Mac basladi (canli)" durumuyla ayni esigi kullanir.
    """
    if not isinstance(match, dict):
        return False
    kickoff = _parse_commence_time(match.get("commence_time"))
    if kickoff is None:
        return False
    now = datetime.fromtimestamp(
        reference_at if reference_at is not None else time.time(),
        tz=timezone.utc,
    )
    return kickoff <= now
