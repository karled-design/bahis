from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from core.match_filters import is_suspicious_match_record, is_virtual_match_text

__all__ = (
    "REAL_MATCH_CACHE_FLAG",
    "init_context_cache",
    "is_cacheable_real_match",
    "get_context_cache_stats",
    "upsert_fixture_cache_row",
    "read_standings_if_fresh",
    "write_standings_cache",
    "read_injuries_if_fresh",
    "write_injuries_cache",
    "read_h2h_if_fresh",
    "write_h2h_cache",
    "read_context_bundle_if_fresh",
    "write_context_bundle_cache",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Context Cache Hatasi"
REAL_MATCH_CACHE_FLAG = 1

_CONTEXT_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    sport_key TEXT NOT NULL,
    match_name TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    commence_time TEXT NOT NULL DEFAULT '',
    league_name TEXT NOT NULL DEFAULT '',
    api_fixture_id INTEGER,
    is_real_match INTEGER NOT NULL DEFAULT 1 CHECK (is_real_match = 1),
    match_quality TEXT NOT NULL DEFAULT 'ok',
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(event_id, sport_key)
);

CREATE TABLE IF NOT EXISTS context_standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport_key TEXT NOT NULL,
    league_api_id INTEGER,
    season TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(sport_key, season)
);

CREATE TABLE IF NOT EXISTS context_team_form (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_key TEXT NOT NULL,
    sport_key TEXT NOT NULL,
    form_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(team_key, sport_key)
);

CREATE TABLE IF NOT EXISTS context_injuries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    api_fixture_id INTEGER,
    injuries_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_h2h (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    home_team_key TEXT NOT NULL,
    away_team_key TEXT NOT NULL,
    sport_key TEXT NOT NULL,
    h2h_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(home_team_key, away_team_key, sport_key)
);

CREATE INDEX IF NOT EXISTS idx_context_fixtures_commence
    ON context_fixtures(commence_time);
CREATE INDEX IF NOT EXISTS idx_context_fixtures_expires
    ON context_fixtures(expires_at);
CREATE INDEX IF NOT EXISTS idx_context_standings_expires
    ON context_standings(expires_at);
CREATE INDEX IF NOT EXISTS idx_context_team_form_expires
    ON context_team_form(expires_at);
CREATE INDEX IF NOT EXISTS idx_context_injuries_expires
    ON context_injuries(expires_at);
CREATE INDEX IF NOT EXISTS idx_context_h2h_expires
    ON context_h2h(expires_at);
"""

_CONTEXT_TABLES = (
    "context_fixtures",
    "context_standings",
    "context_team_form",
    "context_injuries",
    "context_h2h",
)

_DRY_RUN_EVENT_PREFIX = "dry-run"


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def init_context_cache(connection: sqlite3.Connection) -> None:
    connection.executescript(_CONTEXT_CACHE_SCHEMA)


def _split_match_teams(match_name: str) -> tuple[str, str]:
    if " - " in match_name:
        home_team, away_team = match_name.split(" - ", 1)
        return home_team.strip(), away_team.strip()
    return match_name.strip(), ""


def is_cacheable_real_match(match: object) -> bool:
    """Yalnizca gercek maclar context cache'e alinir; sanal/e-futbol ve supheli eslesme reddedilir."""
    if not isinstance(match, dict):
        return False

    match_name = str(match.get("match_name", "")).strip()
    if not match_name:
        return False

    league_name = str(match.get("league_name", "")).strip()
    if is_virtual_match_text(match_name, league_name):
        return False

    event_id = str(match.get("event_id", "")).strip().casefold()
    if event_id.startswith(_DRY_RUN_EVENT_PREFIX):
        return False

    if "dry-run" in match_name.casefold():
        return False

    if is_suspicious_match_record(match):
        return False

    sport_key = str(match.get("sport_key", "")).strip()
    if not sport_key:
        return False

    return True


def get_context_cache_stats(connection: sqlite3.Connection) -> dict[str, int]:
    stats: dict[str, int] = {}
    for table in _CONTEXT_TABLES:
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            stats[table] = int(row[0]) if row is not None else 0
        except sqlite3.Error:
            stats[table] = 0
    return stats


def upsert_fixture_cache_row(
    connection: sqlite3.Connection,
    *,
    match: dict[str, Any],
    fetched_at: str,
    expires_at: str,
    api_fixture_id: int | None = None,
) -> bool:
    """Gelecek adimlar icin hazir yazici; sanal mac veya supheli eslesme yazmaz."""
    if not is_cacheable_real_match(match):
        return False

    match_name = str(match["match_name"]).strip()
    home_team, away_team = _split_match_teams(match_name)
    if not home_team or not away_team:
        return False

    event_id = str(match.get("event_id", "")).strip()
    sport_key = str(match.get("sport_key", "")).strip()
    if not event_id or not sport_key:
        return False

    try:
        connection.execute(
            """
            INSERT INTO context_fixtures (
                event_id,
                sport_key,
                match_name,
                home_team,
                away_team,
                commence_time,
                league_name,
                api_fixture_id,
                is_real_match,
                match_quality,
                fetched_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, sport_key) DO UPDATE SET
                match_name = excluded.match_name,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                commence_time = excluded.commence_time,
                league_name = excluded.league_name,
                api_fixture_id = excluded.api_fixture_id,
                match_quality = excluded.match_quality,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                event_id,
                sport_key,
                match_name,
                home_team,
                away_team,
                str(match.get("commence_time", "")).strip(),
                str(match.get("league_name", "")).strip(),
                api_fixture_id,
                REAL_MATCH_CACHE_FLAG,
                str(match.get("match_quality", "ok")).strip() or "ok",
                fetched_at,
                expires_at,
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"upsert_fixture_cache_row failed | {exc}")
        return False


def dumps_json_payload(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_fresh(expires_at: str, *, reference_at: str | None = None) -> bool:
    if not expires_at.strip():
        return False
    ref = reference_at or _utc_now_iso()
    return expires_at.strip() >= ref


def _loads_json_payload(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def read_standings_if_fresh(
    connection: sqlite3.Connection,
    *,
    sport_key: str,
    season: str,
    reference_at: str | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT payload_json, expires_at
        FROM context_standings
        WHERE sport_key = ? AND season = ?
        LIMIT 1
        """,
        (sport_key.strip(), season.strip()),
    ).fetchone()
    if row is None:
        return None
    expires_at = str(row["expires_at"])
    if not _is_fresh(expires_at, reference_at=reference_at):
        return None
    return _loads_json_payload(str(row["payload_json"]))


def write_standings_cache(
    connection: sqlite3.Connection,
    *,
    sport_key: str,
    season: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    league_api_id: int | None = None,
) -> bool:
    fetched_at = _utc_now_iso()
    expires_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + max(60, int(ttl_seconds)),
        tz=timezone.utc,
    ).isoformat(timespec="seconds")
    try:
        connection.execute(
            """
            INSERT INTO context_standings (
                sport_key, league_api_id, season, payload_json, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(sport_key, season) DO UPDATE SET
                league_api_id = excluded.league_api_id,
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                sport_key.strip(),
                league_api_id,
                season.strip(),
                dumps_json_payload(payload),
                fetched_at,
                expires_at,
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"write_standings_cache failed | {exc}")
        return False


def read_injuries_if_fresh(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    reference_at: str | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT injuries_json, expires_at
        FROM context_injuries
        WHERE event_id = ?
        LIMIT 1
        """,
        (event_id.strip(),),
    ).fetchone()
    if row is None:
        return None
    if not _is_fresh(str(row["expires_at"]), reference_at=reference_at):
        return None
    return _loads_json_payload(str(row["injuries_json"]))


def write_injuries_cache(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    api_fixture_id: int | None = None,
) -> bool:
    fetched_at = _utc_now_iso()
    expires_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + max(60, int(ttl_seconds)),
        tz=timezone.utc,
    ).isoformat(timespec="seconds")
    try:
        connection.execute(
            """
            INSERT INTO context_injuries (
                event_id, api_fixture_id, injuries_json, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                api_fixture_id = excluded.api_fixture_id,
                injuries_json = excluded.injuries_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                event_id.strip(),
                api_fixture_id,
                dumps_json_payload(payload),
                fetched_at,
                expires_at,
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"write_injuries_cache failed | {exc}")
        return False


def read_h2h_if_fresh(
    connection: sqlite3.Connection,
    *,
    home_team_key: str,
    away_team_key: str,
    sport_key: str,
    reference_at: str | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT h2h_json, expires_at
        FROM context_h2h
        WHERE home_team_key = ? AND away_team_key = ? AND sport_key = ?
        LIMIT 1
        """,
        (home_team_key.strip(), away_team_key.strip(), sport_key.strip()),
    ).fetchone()
    if row is None:
        return None
    if not _is_fresh(str(row["expires_at"]), reference_at=reference_at):
        return None
    return _loads_json_payload(str(row["h2h_json"]))


def write_h2h_cache(
    connection: sqlite3.Connection,
    *,
    home_team_key: str,
    away_team_key: str,
    sport_key: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> bool:
    fetched_at = _utc_now_iso()
    expires_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + max(60, int(ttl_seconds)),
        tz=timezone.utc,
    ).isoformat(timespec="seconds")
    try:
        connection.execute(
            """
            INSERT INTO context_h2h (
                home_team_key, away_team_key, sport_key, h2h_json, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(home_team_key, away_team_key, sport_key) DO UPDATE SET
                h2h_json = excluded.h2h_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                home_team_key.strip(),
                away_team_key.strip(),
                sport_key.strip(),
                dumps_json_payload(payload),
                fetched_at,
                expires_at,
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"write_h2h_cache failed | {exc}")
        return False


def read_context_bundle_if_fresh(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    sport_key: str,
    reference_at: str | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT form_json, expires_at
        FROM context_team_form
        WHERE team_key = ? AND sport_key = ?
        LIMIT 1
        """,
        (f"bundle:{event_id.strip()}", sport_key.strip()),
    ).fetchone()
    if row is None:
        return None
    if not _is_fresh(str(row["expires_at"]), reference_at=reference_at):
        return None
    return _loads_json_payload(str(row["form_json"]))


def write_context_bundle_cache(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    sport_key: str,
    bundle: dict[str, Any],
    ttl_seconds: int,
) -> bool:
    fetched_at = _utc_now_iso()
    expires_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + max(60, int(ttl_seconds)),
        tz=timezone.utc,
    ).isoformat(timespec="seconds")
    try:
        connection.execute(
            """
            INSERT INTO context_team_form (
                team_key, sport_key, form_json, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(team_key, sport_key) DO UPDATE SET
                form_json = excluded.form_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                f"bundle:{event_id.strip()}",
                sport_key.strip(),
                dumps_json_payload(bundle),
                fetched_at,
                expires_at,
            ),
        )
        return True
    except sqlite3.Error as exc:
        _emit_operator_diag(f"write_context_bundle_cache failed | {exc}")
        return False
