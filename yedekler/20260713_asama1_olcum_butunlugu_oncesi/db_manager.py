from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.kupon_live_guard import is_live_kupon_metadata, is_live_kupon_row
from core.paper_trade import (
    apply_paper_slippage,
    get_pilot_ab_summary as _read_pilot_ab_summary,
    init_pilot_ab_schema,
    record_pilot_ab_cycle,
)
from config.settings import PILOT_MODE, SQE_DB_PATH, TOTAL_KASA

from database.context_cache import get_context_cache_stats as _read_context_cache_stats
from database.context_cache import init_context_cache

__all__ = (
    "init_db",
    "save_bakiye",
    "get_latest_bakiye",
    "is_operator_budget_configured",
    "add_kupon",
    "result_kupon",
    "get_performance_stats",
    "get_pending_kupons",
    "has_pending_kupon_for_fixture",
    "has_any_kupon_for_fixture",
    "has_any_kupon_for_event",
    "was_fixture_recently_notified",
    "record_fixture_notification",
    "get_recent_kupons",
    "suspend_kupon",
    "add_authorized_ip",
    "is_ip_authorized",
    "get_context_cache_stats",
    "run_context_cache_operation",
    "record_pilot_ab_scan",
    "get_pilot_ab_summary",
    "reset_operator_tracking",
    "count_kupon_losses_between",
    "get_recent_settled_win_rate",
    "get_settled_kupon_stats_since",
    "recalibrate_kasa_from_baseline",
    "compute_kasa_from_kupon_ledger",
    "backfill_pending_kupon_fixture_metadata",
    "purge_non_live_kupons",
    "get_db_path",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Veritabani Yazma Hatasi"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = SQE_DB_PATH
_DB_LOCK = threading.Lock()
_DB_CONNECT_TIMEOUT_SECONDS = 10.0
_DB_LOCK_RETRY_DELAY_SECONDS = 0.5
_DB_LOCK_MAX_RETRIES = 3
_DB_LOCK_TIMEOUT_DIAG = "Teşhis: Veritabanı kilidi aşılamadı (Timeout)"
_DB_LOCK_ALERT_LOCK = threading.Lock()
_DB_LOCK_ALERT_SENT = False

_KUPON_PENDING = "PENDING"
_KUPON_SUSPENDED = "ASKIDA"
_VALID_RESULTS = frozenset({"WON", "LOST"})
_LOCALHOST_IPS = frozenset({"127.0.0.1", "::1"})


def get_db_path() -> Path:
    return _DB_PATH


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _is_database_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _notify_db_lock_timeout() -> None:
    global _DB_LOCK_ALERT_SENT

    print(_DB_LOCK_TIMEOUT_DIAG, file=sys.stderr)
    with _DB_LOCK_ALERT_LOCK:
        if _DB_LOCK_ALERT_SENT:
            return
        try:
            from notifiers import telegram_worker

            telegram_worker.send_alert(
                "Sarı Alarm: DB Yazma Kilidi (Concurrency). İşlem askıda.",
                bypass_scan_gate=True,
            )
            _DB_LOCK_ALERT_SENT = True
        except Exception as exc:
            _emit_operator_diag(f"db lock alert failed | {exc}")


def _execute_critical_write(operation_name: str, operation: Callable[[], bool]) -> bool:
    global _DB_LOCK_ALERT_SENT

    for attempt in range(_DB_LOCK_MAX_RETRIES + 1):
        try:
            result = operation()
            if result:
                with _DB_LOCK_ALERT_LOCK:
                    _DB_LOCK_ALERT_SENT = False
            return result
        except sqlite3.OperationalError as exc:
            if not _is_database_locked_error(exc):
                _emit_operator_diag(f"{operation_name} failed | {exc}")
                return False
            if attempt < _DB_LOCK_MAX_RETRIES:
                time.sleep(_DB_LOCK_RETRY_DELAY_SECONDS)
                continue
            _notify_db_lock_timeout()
            return False
    return False


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection | None:
    try:
        connection = sqlite3.connect(
            _DB_PATH,
            timeout=_DB_CONNECT_TIMEOUT_SECONDS,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        return connection
    except sqlite3.Error as exc:
        _emit_operator_diag(f"connect failed | {exc}")
        return None


def _read_latest_bakiye_from_connection(connection: sqlite3.Connection) -> float:
    row = connection.execute(
        """
        SELECT bakiye
        FROM kasa_gecmisi
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return float(TOTAL_KASA)
    bakiye = float(row["bakiye"])
    if bakiye <= 0.0:
        return float(TOTAL_KASA)
    return bakiye


def _is_valid_odds(value: float) -> bool:
    return value > 1.0


def _normalize_ip_address(ip: str) -> str | None:
    if not isinstance(ip, str):
        return None
    normalized = ip.strip()
    if not normalized:
        return None
    if normalized in _LOCALHOST_IPS:
        return normalized
    parts = normalized.split(".")
    if len(parts) != 4:
        return None
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return None
    if any(octet < 0 or octet > 255 for octet in octets):
        return None
    return normalized


def _kupon_table_columns(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(kuponlar)").fetchall()
    return {str(row[1]) for row in rows}


def _migrate_kupon_schema(connection: sqlite3.Connection) -> None:
    columns = _kupon_table_columns(connection)
    if "ev_at_alert" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN ev_at_alert REAL")
    if "sport_key" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN sport_key TEXT NOT NULL DEFAULT ''")
    if "event_id" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
    if "commence_time" not in columns:
        connection.execute(
            "ALTER TABLE kuponlar ADD COLUMN commence_time TEXT NOT NULL DEFAULT ''"
        )
    if "sonuc_tarihi" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN sonuc_tarihi TEXT")
    if "closing_sharp_oran" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN closing_sharp_oran REAL")
    if "closing_captured_at" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN closing_captured_at TEXT")
    if "clv_pct" not in columns:
        connection.execute("ALTER TABLE kuponlar ADD COLUMN clv_pct REAL")


def _kupon_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    ev_raw = row["ev_at_alert"]
    ev_value = float(ev_raw) if ev_raw is not None else None
    return {
        "id": int(row["id"]),
        "match_id": str(row["match_id"]),
        "mac_adi": str(row["mac_adi"]),
        "market": str(row["market"]),
        "stake": float(row["stake"]),
        "soft_oran": float(row["soft_oran"]),
        "sharp_oran": float(row["sharp_oran"]),
        "ev_at_alert": ev_value,
        "durum": str(row["durum"]),
        "eklenme_tarihi": str(row["eklenme_tarihi"]),
        "sport_key": str(row["sport_key"]) if "sport_key" in keys else "",
        "event_id": str(row["event_id"]) if "event_id" in keys else "",
        "commence_time": str(row["commence_time"]) if "commence_time" in keys else "",
        "closing_sharp_oran": (
            float(row["closing_sharp_oran"])
            if "closing_sharp_oran" in keys and row["closing_sharp_oran"] is not None
            else None
        ),
        "closing_captured_at": (
            str(row["closing_captured_at"])
            if "closing_captured_at" in keys and row["closing_captured_at"] is not None
            else ""
        ),
        "clv_pct": (
            float(row["clv_pct"])
            if "clv_pct" in keys and row["clv_pct"] is not None
            else None
        ),
    }


def _load_feed_snapshot_lookup() -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    snapshots_dir = _PROJECT_ROOT / "database" / "feed_snapshots"
    if not snapshots_dir.is_dir():
        return lookup

    for path in sorted(snapshots_dir.glob("*_live.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for match in payload.get("matches", []):
            if not isinstance(match, dict):
                continue
            match_name = str(match.get("match_name", "")).strip()
            if not match_name or match_name in lookup:
                continue
            lookup[match_name] = {
                "event_id": str(match.get("event_id", "")).strip(),
                "commence_time": str(match.get("commence_time", "")).strip(),
                "sport_key": str(match.get("sport_key", "")).strip(),
            }
    return lookup


def _backfill_pending_kupon_metadata(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT id, mac_adi, event_id, commence_time, sport_key
        FROM kuponlar
        WHERE durum = ?
          AND (commence_time = '' OR event_id = '' OR sport_key = '')
        """,
        (_KUPON_PENDING,),
    ).fetchall()
    if not rows:
        return 0

    snapshot_lookup = _load_feed_snapshot_lookup()
    updated = 0
    for row in rows:
        kupon_id = int(row["id"])
        mac_adi = str(row["mac_adi"]).strip()
        current_event_id = str(row["event_id"]).strip()
        current_commence = str(row["commence_time"]).strip()
        current_sport_key = str(row["sport_key"]).strip()

        meta: dict[str, str] | None = None
        fixture_row = connection.execute(
            """
            SELECT event_id, sport_key, commence_time
            FROM context_fixtures
            WHERE lower(trim(match_name)) = lower(trim(?))
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (mac_adi,),
        ).fetchone()
        if fixture_row is not None:
            meta = {
                "event_id": str(fixture_row["event_id"]).strip(),
                "sport_key": str(fixture_row["sport_key"]).strip(),
                "commence_time": str(fixture_row["commence_time"]).strip(),
            }
        elif mac_adi in snapshot_lookup:
            meta = snapshot_lookup[mac_adi]

        if meta is None:
            continue

        new_event_id = current_event_id or meta.get("event_id", "")
        new_commence = current_commence or meta.get("commence_time", "")
        new_sport_key = current_sport_key or meta.get("sport_key", "")
        if (
            new_event_id == current_event_id
            and new_commence == current_commence
            and new_sport_key == current_sport_key
        ):
            continue

        connection.execute(
            """
            UPDATE kuponlar
            SET event_id = ?, commence_time = ?, sport_key = ?
            WHERE id = ?
            """,
            (new_event_id, new_commence, new_sport_key, kupon_id),
        )
        updated += 1
    return updated


def backfill_pending_kupon_fixture_metadata() -> int:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return 0
        try:
            _migrate_kupon_schema(connection)
            updated = _backfill_pending_kupon_metadata(connection)
            if updated > 0:
                connection.commit()
            return updated
        except sqlite3.Error as exc:
            _emit_operator_diag(f"backfill_pending_kupon_fixture_metadata failed | {exc}")
            return 0
        finally:
            connection.close()


def init_db() -> bool:
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _emit_operator_diag(f"directory create failed | {exc}")
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS kasa_gecmisi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tarih TEXT NOT NULL,
                    bakiye REAL NOT NULL,
                    aciklama TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kuponlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL,
                    mac_adi TEXT NOT NULL,
                    market TEXT NOT NULL,
                    stake REAL NOT NULL,
                    soft_oran REAL NOT NULL,
                    sharp_oran REAL NOT NULL,
                    ev_at_alert REAL,
                    durum TEXT NOT NULL,
                    eklenme_tarihi TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS yetkili_ipler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT NOT NULL UNIQUE,
                    eklenme_tarihi TEXT NOT NULL
                );
                """
            )
            _migrate_kupon_schema(connection)
            init_context_cache(connection)
            init_pilot_ab_schema(connection)
            _backfill_pending_kupon_metadata(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fixture_notifications (
                    notify_key TEXT PRIMARY KEY,
                    mac_adi TEXT NOT NULL,
                    market TEXT NOT NULL,
                    soft_odds REAL NOT NULL,
                    notified_at TEXT NOT NULL
                );
                """
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"init_db failed | {exc}")
            return False
        finally:
            connection.close()


def get_context_cache_stats() -> dict[str, int]:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return {table: 0 for table in (
                "context_fixtures",
                "context_standings",
                "context_team_form",
                "context_injuries",
                "context_h2h",
            )}

        try:
            return _read_context_cache_stats(connection)
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_context_cache_stats failed | {exc}")
            return {}
        finally:
            connection.close()


def record_pilot_ab_scan(
    *,
    cycle_id: str,
    action: int,
    high: int,
    context_filtered: int,
    context_bundle_count: int,
) -> bool:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False
        try:
            init_pilot_ab_schema(connection)
            saved = record_pilot_ab_cycle(
                connection,
                cycle_id=cycle_id,
                action=action,
                high=high,
                context_filtered=context_filtered,
                context_bundle_count=context_bundle_count,
            )
            if saved:
                connection.commit()
            return saved
        except sqlite3.Error as exc:
            _emit_operator_diag(f"record_pilot_ab_scan failed | {exc}")
            return False
        finally:
            connection.close()


def get_pilot_ab_summary(*, limit: int = 500) -> dict[str, Any]:
    empty = {
        "cycles": 0,
        "ev_only_total": 0,
        "fusion_total": 0,
        "filtered_total": 0,
        "saved_by_fusion": 0,
        "last_ev_only": 0,
        "last_fusion": 0,
        "last_filtered": 0,
    }
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return dict(empty)
        try:
            init_pilot_ab_schema(connection)
            return _read_pilot_ab_summary(connection, limit=limit)
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_pilot_ab_summary failed | {exc}")
            return dict(empty)
        finally:
            connection.close()


def run_context_cache_operation(operation: Callable[[sqlite3.Connection], Any]) -> Any:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return None
        try:
            result = operation(connection)
            connection.commit()
            return result
        except sqlite3.Error as exc:
            _emit_operator_diag(f"run_context_cache_operation failed | {exc}")
            return None
        finally:
            connection.close()


def add_authorized_ip(ip: str) -> bool:
    normalized_ip = _normalize_ip_address(ip)
    if normalized_ip is None:
        _emit_operator_diag("add_authorized_ip | invalid ip")
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            connection.execute(
                """
                INSERT OR IGNORE INTO yetkili_ipler (ip, eklenme_tarihi)
                VALUES (?, ?)
                """,
                (normalized_ip, _utc_timestamp()),
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"add_authorized_ip failed | {exc}")
            return False
        finally:
            connection.close()


def is_ip_authorized(ip: str) -> bool:
    normalized_ip = _normalize_ip_address(ip) if isinstance(ip, str) else None
    if normalized_ip is None:
        return False
    if normalized_ip in _LOCALHOST_IPS:
        return True

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            row = connection.execute(
                """
                SELECT 1
                FROM yetkili_ipler
                WHERE ip = ?
                LIMIT 1
                """,
                (normalized_ip,),
            ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            _emit_operator_diag(f"is_ip_authorized failed | {exc}")
            return False
        finally:
            connection.close()


def reset_operator_tracking(
    *,
    bakiye: float | None = None,
    aciklama: str = "Gercek takip baslangici",
) -> dict[str, Any]:
    """Clear kupon history, pilot A/B cycles, and kasa ledger; start fresh tracking."""
    baseline = float(bakiye) if bakiye is not None else None
    if baseline is not None and baseline <= 0.0:
        _emit_operator_diag("reset_operator_tracking | bakiye must be > 0")
        return {"ok": False, "error": "bakiye must be > 0"}

    cleared = {"kuponlar": 0, "pilot_ab_cycles": 0, "fixture_notifications": 0, "kasa_gecmisi": 0}

    def _attempt_reset() -> bool:
        nonlocal cleared, baseline
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                _migrate_kupon_schema(connection)
                init_pilot_ab_schema(connection)

                if baseline is None:
                    try:
                        baseline = _read_latest_bakiye_from_connection(connection)
                    except sqlite3.Error:
                        baseline = float(TOTAL_KASA)

                kupon_count = int(
                    connection.execute("SELECT COUNT(*) FROM kuponlar").fetchone()[0]
                )
                ab_count = int(
                    connection.execute("SELECT COUNT(*) FROM pilot_ab_cycles").fetchone()[0]
                )
                kasa_count = int(
                    connection.execute("SELECT COUNT(*) FROM kasa_gecmisi").fetchone()[0]
                )
                notify_count = int(
                    connection.execute("SELECT COUNT(*) FROM fixture_notifications").fetchone()[0]
                )

                connection.execute("DELETE FROM kuponlar")
                connection.execute("DELETE FROM pilot_ab_cycles")
                connection.execute("DELETE FROM fixture_notifications")
                connection.execute("DELETE FROM kasa_gecmisi")
                connection.execute(
                    """
                    INSERT INTO kasa_gecmisi (tarih, bakiye, aciklama)
                    VALUES (?, ?, ?)
                    """,
                    (_utc_timestamp(), baseline, aciklama.strip()),
                )
                connection.commit()

                cleared = {
                    "kuponlar": kupon_count,
                    "pilot_ab_cycles": ab_count,
                    "fixture_notifications": notify_count,
                    "kasa_gecmisi": kasa_count,
                }
                return True
            except sqlite3.OperationalError:
                raise
            except sqlite3.Error as exc:
                _emit_operator_diag(f"reset_operator_tracking failed | {exc}")
                return False
            finally:
                connection.close()

    saved = _execute_critical_write("reset_operator_tracking", _attempt_reset)
    if not saved:
        return {"ok": False, "error": "reset failed"}

    try:
        from core.hero_measurement import reset_hero_measurement

        reset_hero_measurement()
    except (ImportError, OSError, TypeError, ValueError):
        pass

    try:
        from core.hero_weekly_guard import reset_hero_weekly_guard

        reset_hero_weekly_guard()
    except (ImportError, OSError, TypeError, ValueError):
        pass

    try:
        from core.settlement_log import clear_settlement_log

        clear_settlement_log()
    except (ImportError, OSError):
        pass

    return {
        "ok": True,
        "total_kasa": baseline,
        "cleared": cleared,
        "performance": get_performance_stats(),
    }


def purge_non_live_kupons() -> dict[str, Any]:
    """Remove kupon rows that lack live-scan metadata (event_id, commence_time, sport_key)."""
    removed = 0

    def _attempt_purge() -> bool:
        nonlocal removed
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False
            try:
                _migrate_kupon_schema(connection)
                rows = connection.execute(
                    """
                    SELECT id, match_id, mac_adi, market, stake, soft_oran, sharp_oran, ev_at_alert,
                           durum, eklenme_tarihi, sport_key, event_id, commence_time
                    FROM kuponlar
                    """
                ).fetchall()
                stale_ids: list[int] = []
                for row in rows:
                    try:
                        payload = _kupon_row_to_dict(row)
                    except (TypeError, ValueError, KeyError):
                        stale_ids.append(int(row["id"]))
                        continue
                    if not is_live_kupon_row(payload):
                        stale_ids.append(int(payload["id"]))

                for kupon_id in stale_ids:
                    connection.execute("DELETE FROM kuponlar WHERE id = ?", (kupon_id,))
                removed = len(stale_ids)
                if removed:
                    connection.commit()
                return True
            except sqlite3.Error as exc:
                _emit_operator_diag(f"purge_non_live_kupons failed | {exc}")
                return False
            finally:
                connection.close()

    saved = _execute_critical_write("purge_non_live_kupons", _attempt_purge)
    if not saved:
        return {"ok": False, "error": "purge failed", "removed": 0}
    return {"ok": True, "removed": removed, "performance": get_performance_stats()}


def _fetch_all_kupons_chronological(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, mac_adi, market, stake, soft_oran, durum
        FROM kuponlar
        ORDER BY eklenme_tarihi ASC, id ASC
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "id": int(row["id"]),
                "mac_adi": str(row["mac_adi"]),
                "market": str(row["market"]),
                "stake": float(row["stake"]),
                "soft_oran": float(row["soft_oran"]),
                "durum": str(row["durum"]),
            }
        )
    return results


def compute_kasa_from_kupon_ledger(
    baseline: float,
    kupons: list[dict[str, Any]],
) -> float:
    """Replay stake deductions and WON payouts from a kupon list."""
    if baseline <= 0.0:
        raise ValueError("baseline must be > 0")
    kasa = round(float(baseline), 2)
    for kupon in kupons:
        stake = round(float(kupon["stake"]), 2)
        soft_oran = float(kupon["soft_oran"])
        durum = str(kupon.get("durum", "")).strip().upper()
        kasa = round(kasa - stake, 2)
        if durum == "WON":
            kasa = round(kasa + round(stake * soft_oran, 2), 2)
    return kasa


def recalibrate_kasa_from_baseline(
    baseline: float,
    *,
    aciklama: str | None = None,
) -> dict[str, Any]:
    """Set kasa by replaying all kupon plays from a new baseline balance."""
    if isinstance(baseline, bool) or not isinstance(baseline, (int, float)) or float(baseline) <= 0.0:
        _emit_operator_diag("recalibrate_kasa_from_baseline | baseline must be > 0")
        return {"ok": False, "error": "baseline must be > 0"}

    baseline_value = round(float(baseline), 2)
    kupon_count = 0
    computed_kasa = baseline_value

    def _attempt_recalibrate() -> bool:
        nonlocal kupon_count, computed_kasa
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                kupons = _fetch_all_kupons_chronological(connection)
                kupon_count = len(kupons)
                computed_kasa = compute_kasa_from_kupon_ledger(baseline_value, kupons)
                note = aciklama or (
                    f"Kasa kalibrasyonu | baslangic={baseline_value:.2f} TL | "
                    f"kupon={kupon_count} | yeniden_hesaplandi"
                )
                connection.execute(
                    """
                    INSERT INTO kasa_gecmisi (tarih, bakiye, aciklama)
                    VALUES (?, ?, ?)
                    """,
                    (_utc_timestamp(), computed_kasa, note.strip()),
                )
                connection.commit()
                return True
            except sqlite3.OperationalError:
                raise
            except sqlite3.Error as exc:
                _emit_operator_diag(f"recalibrate_kasa_from_baseline failed | {exc}")
                return False
            finally:
                connection.close()

    saved = _execute_critical_write("recalibrate_kasa_from_baseline", _attempt_recalibrate)
    if not saved:
        return {"ok": False, "error": "recalibrate failed"}

    return {
        "ok": True,
        "baseline": baseline_value,
        "total_kasa": computed_kasa,
        "kupon_count": kupon_count,
        "net_pl_from_baseline": round(computed_kasa - baseline_value, 2),
        "performance": get_performance_stats(),
    }


def save_bakiye(bakiye: float, aciklama: str) -> bool:
    if isinstance(bakiye, bool) or not isinstance(bakiye, (int, float)):
        _emit_operator_diag("bakiye must be numeric")
        return False
    if not isinstance(aciklama, str) or not aciklama.strip():
        _emit_operator_diag("aciklama must be a non-empty string")
        return False

    def _attempt_save() -> bool:
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                connection.execute(
                    """
                    INSERT INTO kasa_gecmisi (tarih, bakiye, aciklama)
                    VALUES (?, ?, ?)
                    """,
                    (_utc_timestamp(), float(bakiye), aciklama.strip()),
                )
                connection.commit()
                return True
            except sqlite3.OperationalError:
                raise
            except sqlite3.Error as exc:
                _emit_operator_diag(f"save_bakiye failed | {exc}")
                return False
            finally:
                connection.close()

    return _execute_critical_write("save_bakiye", _attempt_save)


def get_latest_bakiye() -> float:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return float(TOTAL_KASA)

        try:
            return _read_latest_bakiye_from_connection(connection)
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_latest_bakiye failed | {exc}")
            return float(TOTAL_KASA)
        finally:
            connection.close()


def is_operator_budget_configured() -> bool:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False
        try:
            row = connection.execute("SELECT COUNT(*) FROM kasa_gecmisi").fetchone()
            return bool(row is not None and int(row[0]) > 0)
        except sqlite3.Error as exc:
            _emit_operator_diag(f"is_operator_budget_configured failed | {exc}")
            return False
        finally:
            connection.close()


def add_kupon(
    match_id: str,
    mac_adi: str,
    market: str,
    stake: float,
    soft_oran: float,
    sharp_oran: float,
    ev_at_alert: float | None = None,
    *,
    sport_key: str = "",
    event_id: str = "",
    commence_time: str = "",
) -> bool:
    if not isinstance(match_id, str) or not match_id.strip():
        _emit_operator_diag("add_kupon | match_id must be a non-empty string")
        return False
    if not isinstance(mac_adi, str) or not mac_adi.strip():
        _emit_operator_diag("add_kupon | mac_adi must be a non-empty string")
        return False
    if not isinstance(market, str) or not market.strip():
        _emit_operator_diag("add_kupon | market must be a non-empty string")
        return False
    if isinstance(stake, bool) or not isinstance(stake, (int, float)) or float(stake) <= 0.0:
        _emit_operator_diag("add_kupon | stake must be > 0")
        return False
    if isinstance(soft_oran, bool) or not isinstance(soft_oran, (int, float)):
        _emit_operator_diag("add_kupon | soft_oran must be numeric")
        return False
    if isinstance(sharp_oran, bool) or not isinstance(sharp_oran, (int, float)):
        _emit_operator_diag("add_kupon | sharp_oran must be numeric")
        return False

    soft_value = apply_paper_slippage(float(soft_oran)) if PILOT_MODE else float(soft_oran)
    sharp_value = float(sharp_oran)
    if not _is_valid_odds(soft_value) or not _is_valid_odds(sharp_value):
        _emit_operator_diag("add_kupon | odds must be > 1.0")
        return False

    ev_value: float | None = None
    if ev_at_alert is not None:
        if isinstance(ev_at_alert, bool) or not isinstance(ev_at_alert, (int, float)):
            _emit_operator_diag("add_kupon | ev_at_alert must be numeric")
            return False
        ev_value = float(ev_at_alert)

    normalized_sport_key = sport_key.strip() if isinstance(sport_key, str) else ""
    normalized_event_id = event_id.strip() if isinstance(event_id, str) else ""
    normalized_commence_time = (
        commence_time.strip() if isinstance(commence_time, str) else ""
    )

    live_ok, live_reason = is_live_kupon_metadata(
        event_id=normalized_event_id,
        commence_time=normalized_commence_time,
        sport_key=normalized_sport_key,
        mac_adi=mac_adi.strip(),
    )
    if not live_ok:
        _emit_operator_diag(f"add_kupon | canli metadata eksik | reason={live_reason}")
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            _migrate_kupon_schema(connection)
            connection.execute(
                """
                INSERT INTO kuponlar (
                    match_id, mac_adi, market, stake, soft_oran, sharp_oran, ev_at_alert,
                    durum, eklenme_tarihi, sport_key, event_id, commence_time
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id.strip(),
                    mac_adi.strip(),
                    market.strip(),
                    round(float(stake), 2),
                    round(soft_value, 2),
                    round(sharp_value, 2),
                    round(ev_value, 6) if ev_value is not None else None,
                    _KUPON_PENDING,
                    _utc_timestamp(),
                    normalized_sport_key,
                    normalized_event_id,
                    normalized_commence_time,
                ),
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"add_kupon failed | {exc}")
            return False
        finally:
            connection.close()


def get_recent_kupons(limit: int = 30) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        limit = 30
    limit = min(limit, 100)

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return []

        try:
            rows = connection.execute(
                """
                SELECT id, match_id, mac_adi, market, stake, soft_oran, sharp_oran, ev_at_alert,
                       durum, eklenme_tarihi, sport_key, event_id, commence_time
                FROM kuponlar
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_recent_kupons failed | {exc}")
            return []
        finally:
            connection.close()

    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = _kupon_row_to_dict(row)
        except (TypeError, ValueError, KeyError):
            continue
        if is_live_kupon_row(payload):
            results.append(payload)
    return results


def has_any_kupon_for_fixture(mac_adi: str, market: str) -> bool:
    if not isinstance(mac_adi, str) or not mac_adi.strip():
        return False
    if not isinstance(market, str) or not market.strip():
        return False

    normalized_market = market.strip().upper()
    normalized_mac = mac_adi.strip().casefold()

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            row = connection.execute(
                """
                SELECT 1
                FROM kuponlar
                WHERE lower(trim(mac_adi)) = ?
                  AND upper(trim(market)) = ?
                LIMIT 1
                """,
                (normalized_mac, normalized_market),
            ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            _emit_operator_diag(f"has_any_kupon_for_fixture failed | {exc}")
            return False
        finally:
            connection.close()


def has_any_kupon_for_event(event_id: str, market: str) -> bool:
    if not isinstance(event_id, str) or not event_id.strip():
        return False
    if not isinstance(market, str) or not market.strip():
        return False

    normalized_event_id = event_id.strip()
    normalized_market = market.strip().upper()

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            row = connection.execute(
                """
                SELECT 1
                FROM kuponlar
                WHERE trim(event_id) = ?
                  AND upper(trim(market)) = ?
                LIMIT 1
                """,
                (normalized_event_id, normalized_market),
            ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            _emit_operator_diag(f"has_any_kupon_for_event failed | {exc}")
            return False
        finally:
            connection.close()


def was_fixture_recently_notified(
    notify_key: str,
    soft_odds: float,
    *,
    odds_change_bypass_pct: float,
) -> bool:
    if not isinstance(notify_key, str) or not notify_key.strip():
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            row = connection.execute(
                """
                SELECT soft_odds
                FROM fixture_notifications
                WHERE notify_key = ?
                """,
                (notify_key.strip(),),
            ).fetchone()
            if row is None:
                return False

            previous_soft = float(row["soft_odds"])
            current_soft = float(soft_odds)
            if previous_soft <= 0.0:
                return True
            odds_change_ratio = abs(current_soft - previous_soft) / previous_soft
            return odds_change_ratio < float(odds_change_bypass_pct)
        except sqlite3.Error as exc:
            _emit_operator_diag(f"was_fixture_recently_notified failed | {exc}")
            return False
        finally:
            connection.close()


def get_fixture_last_notified_at(notify_key: str) -> str | None:
    """Bu fixture en son ne zaman bildirildi (ISO metin) -- kayit yoksa None."""
    if not isinstance(notify_key, str) or not notify_key.strip():
        return None

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return None

        try:
            row = connection.execute(
                """
                SELECT notified_at
                FROM fixture_notifications
                WHERE notify_key = ?
                """,
                (notify_key.strip(),),
            ).fetchone()
            if row is None:
                return None
            value = str(row["notified_at"] or "").strip()
            return value or None
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_fixture_last_notified_at failed | {exc}")
            return None
        finally:
            connection.close()


def record_fixture_notification(
    notify_key: str,
    mac_adi: str,
    market: str,
    soft_odds: float,
) -> bool:
    if not isinstance(notify_key, str) or not notify_key.strip():
        return False

    def _attempt_record() -> bool:
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                connection.execute(
                    """
                    INSERT INTO fixture_notifications (notify_key, mac_adi, market, soft_odds, notified_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(notify_key) DO UPDATE SET
                        mac_adi = excluded.mac_adi,
                        market = excluded.market,
                        soft_odds = excluded.soft_odds,
                        notified_at = excluded.notified_at
                    """,
                    (
                        notify_key.strip(),
                        mac_adi.strip(),
                        market.strip().upper(),
                        round(float(soft_odds), 4),
                        _utc_timestamp(),
                    ),
                )
                connection.commit()
                return True
            except sqlite3.Error as exc:
                _emit_operator_diag(f"record_fixture_notification failed | {exc}")
                return False
            finally:
                connection.close()

    return _execute_critical_write("record_fixture_notification", _attempt_record)


def has_pending_kupon_for_fixture(mac_adi: str, market: str) -> bool:
    if not isinstance(mac_adi, str) or not mac_adi.strip():
        return False
    if not isinstance(market, str) or not market.strip():
        return False

    normalized_market = market.strip().upper()
    normalized_mac = mac_adi.strip().casefold()

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False

        try:
            row = connection.execute(
                """
                SELECT 1
                FROM kuponlar
                WHERE durum = ?
                  AND lower(trim(mac_adi)) = ?
                  AND upper(trim(market)) = ?
                LIMIT 1
                """,
                (_KUPON_PENDING, normalized_mac, normalized_market),
            ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            _emit_operator_diag(f"has_pending_kupon_for_fixture failed | {exc}")
            return False
        finally:
            connection.close()


def get_pending_kupons() -> list[dict[str, Any]]:
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return []

        try:
            _migrate_kupon_schema(connection)
            rows = connection.execute(
                """
                SELECT id, match_id, mac_adi, market, stake, soft_oran, sharp_oran, ev_at_alert,
                       durum, eklenme_tarihi, sport_key, event_id, commence_time
                FROM kuponlar
                WHERE durum = ?
                ORDER BY id ASC
                """,
                (_KUPON_PENDING,),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = _kupon_row_to_dict(row)
                except (TypeError, ValueError, KeyError):
                    continue
                if is_live_kupon_row(payload):
                    results.append(payload)
            return results
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_pending_kupons failed | {exc}")
            return []
        finally:
            connection.close()


def suspend_kupon(kupon_id: int, *, refund_stake: bool = True) -> bool:
    if isinstance(kupon_id, bool) or not isinstance(kupon_id, int) or kupon_id <= 0:
        _emit_operator_diag("suspend_kupon | kupon_id must be a positive int")
        return False

    def _attempt_suspend() -> bool:
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                row = connection.execute(
                    """
                    SELECT id, durum, stake, mac_adi
                    FROM kuponlar
                    WHERE id = ?
                    """,
                    (kupon_id,),
                ).fetchone()

                if row is None:
                    _emit_operator_diag(f"suspend_kupon | kupon not found | id={kupon_id}")
                    return False

                current_status = str(row["durum"])
                if current_status != _KUPON_PENDING:
                    _emit_operator_diag(
                        f"suspend_kupon | kupon not pending | id={kupon_id} | durum={current_status}"
                    )
                    return False

                connection.execute(
                    """
                    UPDATE kuponlar
                    SET durum = ?
                    WHERE id = ?
                    """,
                    (_KUPON_SUSPENDED, kupon_id),
                )

                if refund_stake:
                    stake_value = round(float(row["stake"]), 2)
                    mac_adi = str(row["mac_adi"])
                    current_kasa = _read_latest_bakiye_from_connection(connection)
                    new_kasa = round(current_kasa + stake_value, 2)
                    connection.execute(
                        """
                        INSERT INTO kasa_gecmisi (tarih, bakiye, aciklama)
                        VALUES (?, ?, ?)
                        """,
                        (
                            _utc_timestamp(),
                            new_kasa,
                            f"Askida iade | {mac_adi} | id={kupon_id} | +{stake_value} TL",
                        ),
                    )

                connection.commit()
                return True
            except sqlite3.Error as exc:
                _emit_operator_diag(f"suspend_kupon failed | {exc}")
                return False
            finally:
                connection.close()

    return _execute_critical_write("suspend_kupon", _attempt_suspend)


def result_kupon(kupon_id: int, sonuc: str) -> bool:
    if isinstance(kupon_id, bool) or not isinstance(kupon_id, int) or kupon_id <= 0:
        _emit_operator_diag("result_kupon | kupon_id must be a positive int")
        return False
    if not isinstance(sonuc, str):
        _emit_operator_diag("result_kupon | sonuc must be a string")
        return False

    normalized_sonuc = sonuc.strip().upper()
    if normalized_sonuc not in _VALID_RESULTS:
        _emit_operator_diag("result_kupon | sonuc must be WON or LOST")
        return False

    def _attempt_result() -> bool:
        with _DB_LOCK:
            connection = _connect()
            if connection is None:
                return False

            try:
                row = connection.execute(
                    """
                    SELECT id, mac_adi, stake, soft_oran, durum
                    FROM kuponlar
                    WHERE id = ?
                    """,
                    (kupon_id,),
                ).fetchone()

                if row is None:
                    _emit_operator_diag(f"result_kupon | kupon not found | id={kupon_id}")
                    return False

                current_status = str(row["durum"])
                if current_status != _KUPON_PENDING:
                    _emit_operator_diag(
                        f"result_kupon | kupon not pending | id={kupon_id} | durum={current_status}"
                    )
                    return False

                connection.execute(
                    """
                    UPDATE kuponlar
                    SET durum = ?, sonuc_tarihi = ?
                    WHERE id = ?
                    """,
                    (normalized_sonuc, _utc_timestamp(), kupon_id),
                )

                if normalized_sonuc == "WON":
                    stake_value = float(row["stake"])
                    soft_value = float(row["soft_oran"])
                    payout = round(stake_value * soft_value, 2)
                    current_kasa = _read_latest_bakiye_from_connection(connection)
                    new_kasa = round(current_kasa + payout, 2)
                    mac_adi = str(row["mac_adi"])
                    connection.execute(
                        """
                        INSERT INTO kasa_gecmisi (tarih, bakiye, aciklama)
                        VALUES (?, ?, ?)
                        """,
                        (
                            _utc_timestamp(),
                            new_kasa,
                            f"Kupon kazanci | {mac_adi} | id={kupon_id} | +{payout} TL",
                        ),
                    )

                connection.commit()
                return True
            except sqlite3.OperationalError:
                raise
            except sqlite3.Error as exc:
                _emit_operator_diag(f"result_kupon failed | {exc}")
                return False
            finally:
                connection.close()

    return _execute_critical_write("result_kupon", _attempt_result)


def get_kupons_needing_closing() -> list[dict[str, Any]]:
    """Kapanis orani henuz yakalanmamis, kickoff bilgisi olan kuponlar."""
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return []
        try:
            _migrate_kupon_schema(connection)
            rows = connection.execute(
                """
                SELECT id, market, sharp_oran, soft_oran, event_id, commence_time, mac_adi
                FROM kuponlar
                WHERE closing_sharp_oran IS NULL
                  AND commence_time != ''
                ORDER BY id ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_kupons_needing_closing failed | {exc}")
            return []
        finally:
            connection.close()

    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            result.append(
                {
                    "id": int(row["id"]),
                    "market": str(row["market"]),
                    "sharp_oran": float(row["sharp_oran"]),
                    "soft_oran": float(row["soft_oran"]),
                    "event_id": str(row["event_id"]),
                    "commence_time": str(row["commence_time"]),
                    "mac_adi": str(row["mac_adi"]),
                }
            )
        except (TypeError, ValueError):
            continue
    return result


def set_kupon_closing(
    kupon_id: int,
    closing_sharp_oran: float,
    captured_at: str,
    clv_pct: float,
) -> bool:
    """Bir kupona kapanis orani + CLV degerini yazar."""
    if isinstance(kupon_id, bool) or not isinstance(kupon_id, int) or kupon_id <= 0:
        _emit_operator_diag("set_kupon_closing | kupon_id must be a positive int")
        return False
    if (
        isinstance(closing_sharp_oran, bool)
        or not isinstance(closing_sharp_oran, (int, float))
        or float(closing_sharp_oran) <= 1.0
    ):
        _emit_operator_diag("set_kupon_closing | closing_sharp_oran must be > 1.0")
        return False
    if isinstance(clv_pct, bool) or not isinstance(clv_pct, (int, float)):
        _emit_operator_diag("set_kupon_closing | clv_pct must be numeric")
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False
        try:
            _migrate_kupon_schema(connection)
            connection.execute(
                """
                UPDATE kuponlar
                SET closing_sharp_oran = ?, closing_captured_at = ?, clv_pct = ?
                WHERE id = ?
                """,
                (
                    round(float(closing_sharp_oran), 2),
                    str(captured_at),
                    round(float(clv_pct), 6),
                    kupon_id,
                ),
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"set_kupon_closing failed | {exc}")
            return False
        finally:
            connection.close()


def get_clv_scorecard() -> dict[str, Any]:
    """CLV karnesi: kac kupon olculdu, kacinda oran lehe hareket etti, ortalama."""
    empty: dict[str, Any] = {
        "olculen": 0,
        "lehte": 0,
        "lehte_oran": 0.0,
        "ort_clv_pct": 0.0,
    }
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return empty
        try:
            _migrate_kupon_schema(connection)
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS olculen,
                    SUM(CASE WHEN clv_pct >= 0 THEN 1 ELSE 0 END) AS lehte,
                    AVG(clv_pct) AS ort
                FROM kuponlar
                WHERE clv_pct IS NOT NULL
                """
            ).fetchone()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_clv_scorecard failed | {exc}")
            return empty
        finally:
            connection.close()

    olculen = int(row["olculen"] or 0)
    if olculen == 0:
        return empty
    lehte = int(row["lehte"] or 0)
    ort = float(row["ort"] or 0.0)
    return {
        "olculen": olculen,
        "lehte": lehte,
        "lehte_oran": round(lehte / olculen, 4),
        "ort_clv_pct": round(ort, 6),
    }


def get_settled_kupon_stats_since(started_at: str) -> dict[str, Any]:
    empty = {
        "settled": 0,
        "won": 0,
        "lost": 0,
        "win_rate_percent": 0.0,
    }
    start_value = str(started_at or "").strip()
    if not start_value:
        return dict(empty)

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return dict(empty)

        try:
            _migrate_kupon_schema(connection)
            rows = connection.execute(
                """
                SELECT durum
                FROM kuponlar
                WHERE durum IN ('WON', 'LOST')
                  AND COALESCE(sonuc_tarihi, eklenme_tarihi) >= ?
                """,
                (start_value,),
            ).fetchall()
            won = sum(1 for row in rows if str(row["durum"]) == "WON")
            settled = len(rows)
            lost = settled - won
            win_rate = round((won / settled) * 100.0, 1) if settled > 0 else 0.0
            return {
                "settled": settled,
                "won": won,
                "lost": lost,
                "win_rate_percent": win_rate,
            }
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_settled_kupon_stats_since failed | {exc}")
            return dict(empty)
        finally:
            connection.close()


def count_kupon_losses_between(start_utc: str, end_utc: str) -> int:
    if not isinstance(start_utc, str) or not isinstance(end_utc, str):
        return 0
    start_value = start_utc.strip()
    end_value = end_utc.strip()
    if not start_value or not end_value:
        return 0

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return 0

        try:
            _migrate_kupon_schema(connection)
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM kuponlar
                WHERE durum = 'LOST'
                  AND sonuc_tarihi IS NOT NULL
                  AND sonuc_tarihi >= ?
                  AND sonuc_tarihi < ?
                """,
                (start_value, end_value),
            ).fetchone()
            return int(row["cnt"]) if row is not None else 0
        except sqlite3.Error as exc:
            _emit_operator_diag(f"count_kupon_losses_between failed | {exc}")
            return 0
        finally:
            connection.close()


def get_recent_settled_win_rate(*, limit: int = 20) -> dict[str, Any]:
    sample_limit = max(1, int(limit))
    empty = {
        "sample_size": 0,
        "won": 0,
        "lost": 0,
        "win_rate_percent": 0.0,
    }

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return dict(empty)

        try:
            _migrate_kupon_schema(connection)
            rows = connection.execute(
                """
                SELECT durum
                FROM kuponlar
                WHERE durum IN ('WON', 'LOST')
                ORDER BY COALESCE(sonuc_tarihi, eklenme_tarihi) DESC, id DESC
                LIMIT ?
                """,
                (sample_limit,),
            ).fetchall()
            won = sum(1 for row in rows if str(row["durum"]) == "WON")
            total = len(rows)
            lost = total - won
            win_rate = round((won / total) * 100.0, 1) if total > 0 else 0.0
            return {
                "sample_size": total,
                "won": won,
                "lost": lost,
                "win_rate_percent": win_rate,
            }
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_recent_settled_win_rate failed | {exc}")
            return dict(empty)
        finally:
            connection.close()


def get_performance_stats() -> dict[str, Any]:
    empty_stats: dict[str, Any] = {
        "total_kupon": 0,
        "pending": 0,
        "won": 0,
        "lost": 0,
        "win_rate": 0.0,
        "total_staked": 0.0,
        "total_payout": 0.0,
        "net_pl": 0.0,
        "roi_percent": 0.0,
    }

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return dict(empty_stats)

        try:
            summary_rows = connection.execute(
                """
                SELECT durum,
                       COUNT(*) AS cnt,
                       COALESCE(SUM(stake), 0) AS staked
                FROM kuponlar
                GROUP BY durum
                """
            ).fetchall()

            pending = 0
            won = 0
            lost = 0
            settled_staked = 0.0
            total_payout = 0.0

            for row in summary_rows:
                status = str(row["durum"])
                count = int(row["cnt"])
                staked = float(row["staked"])

                if status == _KUPON_PENDING:
                    pending = count
                elif status == "WON":
                    won = count
                    settled_staked += staked
                elif status == "LOST":
                    lost = count
                    settled_staked += staked

            won_rows = connection.execute(
                """
                SELECT stake, soft_oran
                FROM kuponlar
                WHERE durum = 'WON'
                """
            ).fetchall()

            won_profit = 0.0
            for row in won_rows:
                stake_value = float(row["stake"])
                soft_value = float(row["soft_oran"])
                payout_value = round(stake_value * soft_value, 2)
                total_payout += payout_value
                won_profit += round(stake_value * (soft_value - 1.0), 2)

            lost_stake = float(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(stake), 0)
                    FROM kuponlar
                    WHERE durum = 'LOST'
                    """
                ).fetchone()[0]
            )

            total_kupon = pending + won + lost
            settled = won + lost
            win_rate = round((won / settled) * 100.0, 2) if settled > 0 else 0.0
            net_pl = round(won_profit - lost_stake, 2)
            roi_percent = round((net_pl / settled_staked) * 100.0, 2) if settled_staked > 0.0 else 0.0

            return {
                "total_kupon": total_kupon,
                "pending": pending,
                "won": won,
                "lost": lost,
                "win_rate": win_rate,
                "total_staked": round(settled_staked, 2),
                "total_payout": round(total_payout, 2),
                "net_pl": net_pl,
                "roi_percent": roi_percent,
            }
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_performance_stats failed | {exc}")
            return dict(empty_stats)
        finally:
            connection.close()


def count_notifications_between(start_utc: str, end_utc: str) -> int:
    """Verilen UTC araliginda gonderilen bildirim sayisi (gunluk ozet icin)."""
    start_value = str(start_utc or "").strip()
    end_value = str(end_utc or "").strip()
    if not start_value or not end_value:
        return 0

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return 0
        try:
            row = connection.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM fixture_notifications
                WHERE notified_at >= ? AND notified_at < ?
                """,
                (start_value, end_value),
            ).fetchone()
            return int(row["cnt"]) if row is not None else 0
        except sqlite3.Error as exc:
            _emit_operator_diag(f"count_notifications_between failed | {exc}")
            return 0
        finally:
            connection.close()


def get_notifications_between(start_utc: str, end_utc: str) -> list[dict[str, Any]]:
    """Verilen UTC araliginda gonderilen bildirimlerin listesi (gunluk ozet icin)."""
    start_value = str(start_utc or "").strip()
    end_value = str(end_utc or "").strip()
    if not start_value or not end_value:
        return []

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return []
        try:
            rows = connection.execute(
                """
                SELECT mac_adi, market, soft_odds, notified_at
                FROM fixture_notifications
                WHERE notified_at >= ? AND notified_at < ?
                ORDER BY notified_at
                """,
                (start_value, end_value),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_notifications_between failed | {exc}")
            return []
        finally:
            connection.close()


def get_settled_kupons_between(start_utc: str, end_utc: str) -> list[dict[str, Any]]:
    """Verilen UTC araliginda sonuclanan kuponlar (gunluk ozet icin)."""
    start_value = str(start_utc or "").strip()
    end_value = str(end_utc or "").strip()
    if not start_value or not end_value:
        return []

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return []
        try:
            _migrate_kupon_schema(connection)
            rows = connection.execute(
                """
                SELECT mac_adi, market, durum, stake, soft_oran
                FROM kuponlar
                WHERE durum IN ('WON', 'LOST')
                  AND sonuc_tarihi IS NOT NULL
                  AND sonuc_tarihi >= ? AND sonuc_tarihi < ?
                ORDER BY id ASC
                """,
                (start_value, end_value),
            ).fetchall()
            return [
                {
                    "mac_adi": str(row["mac_adi"]),
                    "market": str(row["market"]),
                    "durum": str(row["durum"]),
                    "stake": float(row["stake"]),
                    "soft_oran": float(row["soft_oran"]),
                }
                for row in rows
            ]
        except sqlite3.Error as exc:
            _emit_operator_diag(f"get_settled_kupons_between failed | {exc}")
            return []
        finally:
            connection.close()
