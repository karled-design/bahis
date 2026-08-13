# SQE-V1 ortak zaman yardimcilari
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ("parse_utc",)


def parse_utc(value: Any) -> datetime | None:
    """ISO 8601 zaman damgasini UTC'ye cevirir; cozulemezse None.

    'Z' soneki Python 3.11 oncesinde `datetime.fromisoformat` tarafindan
    desteklenmedigi icin acikca '+00:00' ile degistirilir. Saat dilimi
    tasimayan degerler UTC kabul edilir.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
