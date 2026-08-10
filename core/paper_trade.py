from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from config.settings import PILOT_MODE, PAPER_SLIPPAGE_PCT

__all__ = (
    "apply_paper_slippage",
    "record_pilot_ab_cycle",
    "get_pilot_ab_summary",
    "init_pilot_ab_schema",
    "compute_ev_only_action_count",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Pilot AB Hatasi"


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def apply_paper_slippage(soft_odds: float) -> float:
    if not PILOT_MODE:
        return round(float(soft_odds), 2)
    adjusted = float(soft_odds) * (1.0 - float(PAPER_SLIPPAGE_PCT))
    return round(max(adjusted, 1.01), 2)


def compute_ev_only_action_count(*, action: int, high: int, context_filtered: int) -> int:
    """EV-only simulasyon: fusion sonrasi action+high + baglam nedeniyle elenenler."""
    return max(0, int(action)) + max(0, int(high)) + max(0, int(context_filtered))


def init_pilot_ab_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pilot_ab_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            ev_only_action INTEGER NOT NULL,
            fusion_action INTEGER NOT NULL,
            context_filtered INTEGER NOT NULL,
            context_bundle_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pilot_ab_cycles_recorded_at
        ON pilot_ab_cycles(recorded_at)
        """
    )


def record_pilot_ab_cycle(
    connection: sqlite3.Connection,
    *,
    cycle_id: str,
    action: int,
    high: int,
    context_filtered: int,
    context_bundle_count: int,
) -> bool:
    ev_only_action = compute_ev_only_action_count(
        action=action,
        high=high,
        context_filtered=context_filtered,
    )
    fusion_action = max(0, int(action)) + max(0, int(high))
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        connection.execute(
            """
            INSERT INTO pilot_ab_cycles (
                cycle_id, recorded_at, ev_only_action, fusion_action,
                context_filtered, context_bundle_count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id.strip(),
                recorded_at,
                ev_only_action,
                fusion_action,
                max(0, int(context_filtered)),
                max(0, int(context_bundle_count)),
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"record_pilot_ab_cycle failed | {exc}")
        return False


def get_pilot_ab_summary(connection: sqlite3.Connection, *, limit: int = 500) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 5000))
    try:
        rows = connection.execute(
            """
            SELECT ev_only_action, fusion_action, context_filtered, context_bundle_count
            FROM pilot_ab_cycles
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    except sqlite3.Error as exc:
        _emit_operator_diag(f"get_pilot_ab_summary failed | {exc}")
        return {
            "cycles": 0,
            "ev_only_total": 0,
            "fusion_total": 0,
            "filtered_total": 0,
            "saved_by_fusion": 0,
            "last_ev_only": 0,
            "last_fusion": 0,
            "last_filtered": 0,
        }

    if not rows:
        return {
            "cycles": 0,
            "ev_only_total": 0,
            "fusion_total": 0,
            "filtered_total": 0,
            "saved_by_fusion": 0,
            "last_ev_only": 0,
            "last_fusion": 0,
            "last_filtered": 0,
        }

    ev_only_total = sum(int(row[0]) for row in rows)
    fusion_total = sum(int(row[1]) for row in rows)
    filtered_total = sum(int(row[2]) for row in rows)
    last = rows[0]
    return {
        "cycles": len(rows),
        "ev_only_total": ev_only_total,
        "fusion_total": fusion_total,
        "filtered_total": filtered_total,
        "saved_by_fusion": max(0, ev_only_total - fusion_total),
        "last_ev_only": int(last[0]),
        "last_fusion": int(last[1]),
        "last_filtered": int(last[2]),
    }
