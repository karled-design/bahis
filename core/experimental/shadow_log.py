from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = (
    "get_shadow_stats",
    "log_experimental_shadow",
    "reset_experimental_shadow_log",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_PATH = _PROJECT_ROOT / "database" / "experimental_shadow.jsonl"
_LOCK = threading.Lock()
_MAX_LINES = 5000


def reset_experimental_shadow_log() -> None:
    with _LOCK:
        if _LOG_PATH.is_file():
            _LOG_PATH.unlink()


def log_experimental_shadow(
    *,
    path: str,
    match_name: str,
    market: str,
    evaluation: dict[str, Any],
    mode: str,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "match_name": match_name,
        "market": market,
        "mode": mode,
        "evaluation": evaluation,
    }
    with _LOCK:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim_log_file()


def _trim_log_file() -> None:
    if not _LOG_PATH.is_file():
        return
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_LINES:
        return
    tail = lines[-_MAX_LINES:]
    _LOG_PATH.write_text("\n".join(tail) + "\n", encoding="utf-8")


def get_shadow_stats() -> dict[str, Any]:
    would_filter = 0
    total = 0
    if not _LOG_PATH.is_file():
        return {"total": 0, "would_filter": 0, "pass_through": 0}
    try:
        for line in _LOG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            evaluation = payload.get("evaluation")
            if isinstance(evaluation, dict) and evaluation.get("would_filter"):
                would_filter += 1
    except OSError:
        return {"total": 0, "would_filter": 0, "pass_through": 0}
    return {
        "total": total,
        "would_filter": would_filter,
        "pass_through": max(0, total - would_filter),
    }
