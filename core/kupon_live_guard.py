from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = (
    "is_live_kupon_metadata",
    "is_live_kupon_row",
)

_DRY_RUN_EVENT_PREFIX = "dry-run-"


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


def is_live_kupon_metadata(
    *,
    event_id: str = "",
    commence_time: str = "",
    sport_key: str = "",
    mac_adi: str = "",
) -> tuple[bool, str]:
    normalized_event_id = event_id.strip()
    if not normalized_event_id:
        return False, "event_id_yok"
    if normalized_event_id.casefold().startswith(_DRY_RUN_EVENT_PREFIX):
        return False, "dry_run_event"
    if "dry-run" in mac_adi.strip().casefold():
        return False, "dry_run_mac"

    normalized_commence = commence_time.strip()
    if not normalized_commence:
        return False, "commence_time_yok"
    if _parse_commence_time(normalized_commence) is None:
        return False, "commence_time_gecersiz"

    if not sport_key.strip():
        return False, "sport_key_yok"

    return True, ""


def is_live_kupon_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    ok, _ = is_live_kupon_metadata(
        event_id=str(row.get("event_id", "")),
        commence_time=str(row.get("commence_time", "")),
        sport_key=str(row.get("sport_key", "")),
        mac_adi=str(row.get("mac_adi", row.get("match_name", ""))),
    )
    return ok
