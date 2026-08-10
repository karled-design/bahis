from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ("append_settlement_cycle_log", "clear_settlement_log", "get_settlement_log_path")

_LOG_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_PATH = _PROJECT_ROOT / "database" / "settlement_log.jsonl"


def get_settlement_log_path() -> Path:
    return _DEFAULT_LOG_PATH


def append_settlement_cycle_log(
    record: dict[str, Any],
    *,
    log_path: Path | None = None,
) -> None:
    if not isinstance(record, dict):
        return

    path = log_path or _DEFAULT_LOG_PATH
    payload = dict(record)
    payload.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    try:
        line = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        print(f"[SQE-V1] Settlement log | json encode failed | {exc}", file=sys.stderr)
        return

    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        print(f"[SQE-V1] Settlement log | write failed | {exc}", file=sys.stderr)


def clear_settlement_log(*, log_path: Path | None = None) -> bool:
    path = log_path or _DEFAULT_LOG_PATH
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return True
    except OSError as exc:
        print(f"[SQE-V1] Settlement log | clear failed | {exc}", file=sys.stderr)
        return False
