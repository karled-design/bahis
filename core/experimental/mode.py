from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

__all__ = (
    "DEFAULT_EXPERIMENTAL_MODE",
    "get_experimental_mode",
    "is_experimental_live_filter",
    "set_experimental_mode",
)

DEFAULT_EXPERIMENTAL_MODE = "shadow"
_VALID_MODES = frozenset({"shadow", "live"})
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "experimental_mode.json"
_LOCK = threading.Lock()
_MODE: str = DEFAULT_EXPERIMENTAL_MODE


def _load_persisted() -> str | None:
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = str(payload.get("mode", "")).strip().casefold()
    if mode in _VALID_MODES:
        return mode
    return None


def _save(mode: str) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PERSIST_PATH.write_text(
        json.dumps({"mode": mode}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def bootstrap_experimental_mode() -> str:
    global _MODE
    with _LOCK:
        _MODE = _load_persisted() or DEFAULT_EXPERIMENTAL_MODE
        return _MODE


def get_experimental_mode() -> str:
    with _LOCK:
        return _MODE


def is_experimental_live_filter() -> bool:
    return get_experimental_mode() == "live"


def set_experimental_mode(mode: str) -> str:
    normalized = str(mode).strip().casefold()
    if normalized not in _VALID_MODES:
        raise ValueError(f"unknown experimental mode: {mode}")
    global _MODE
    with _LOCK:
        _MODE = normalized
        _save(normalized)
    return normalized


def build_experimental_mode_payload() -> dict[str, Any]:
    mode = get_experimental_mode()
    return {
        "mode": mode,
        "live_filter": mode == "live",
        "label": "Canli filtre" if mode == "live" else "Golge mod",
        "summary": (
            "Uyumsuz adaylar gercekten elenir."
            if mode == "live"
            else "Elenirdi adaylar loglanir; Telegram degismez."
        ),
    }
