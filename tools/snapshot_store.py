from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

__all__ = ("save_feed_snapshot", "load_latest_snapshot", "list_snapshots")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SNAPSHOT_DIR = _PROJECT_ROOT / "database" / "feed_snapshots"


def _ensure_snapshot_dir() -> Path:
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return _SNAPSHOT_DIR


def save_feed_snapshot(matches: list[dict[str, Any]], *, label: str = "live") -> Path:
    snapshot_dir = _ensure_snapshot_dir()
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    path = snapshot_dir / f"{timestamp}_{label}.json"
    payload = {
        "saved_at": time.time(),
        "label": label,
        "match_count": len(matches),
        "matches": matches,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_snapshots(limit: int = 20) -> list[Path]:
    snapshot_dir = _ensure_snapshot_dir()
    files = sorted(snapshot_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[: max(1, limit)]


def load_latest_snapshot() -> list[dict[str, Any]]:
    files = list_snapshots(limit=1)
    if not files:
        return []
    return load_snapshot(files[0])


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    return [item for item in matches if isinstance(item, dict)]
