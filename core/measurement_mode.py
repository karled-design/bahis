"""Olcum modu: para riski almadan sinyal kalitesini olcer.

Amac: "hangi yontem, hangi pazarda, hangi referansla gercekten kazandiriyor?"
sorusunu 4-6 haftalik veriyle cevaplamak. Mod acikken:

* motor normal calisir, Telegram bildirimi gider;
* mesajda "Oyna" dugmesi CIKMAZ, kupon acilmaz, bakiye degismez;
* her bildirilen sinyal `olcum_sinyalleri` tablosuna yazilir;
* her tarama turunun huni sayaclari `olcum_huni` tablosuna yazilir;
* mac baslayinca kapanis cizgisi (CLV) `capture_pending_clv()` ile islenir.

CLV ölçüsü kararin dogrulugunun en hizli gostergesidir: sonuc (kazandi/kaybetti)
yuzlerce bahis sonra anlam kazanirken, kapanis cizgisini yenip yenmedigimiz
onlarca bahiste gorunur hale gelir.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import SQE_DB_PATH

__all__ = (
    "MEASUREMENT_STATE_PATH",
    "capture_pending_clv",
    "is_measurement_mode_enabled",
    "record_scan_funnel",
    "record_signal",
    "report",
    "set_measurement_mode",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Olcum Modu Hatasi"
_DB_LOCK = threading.Lock()
_CONNECT_TIMEOUT_SECONDS = 10.0

MEASUREMENT_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "measurement_mode.json"


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Mod anahtari -------------------------------------------------------------


def is_measurement_mode_enabled() -> bool:
    """Olcum modu acik mi? Okunamazsa guvenli taraf = KAPALI (normal calisma)."""
    try:
        payload = json.loads(MEASUREMENT_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("enabled", False)) is True


def set_measurement_mode(enabled: bool) -> bool:
    payload = {"enabled": bool(enabled), "updated_at": _now_iso()}
    temporary = MEASUREMENT_STATE_PATH.with_suffix(".json.tmp")
    try:
        MEASUREMENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(MEASUREMENT_STATE_PATH)
        return True
    except OSError as exc:
        _emit_operator_diag(f"mod yazilamadi | {exc}")
        return False


# --- Depolama -----------------------------------------------------------------


def _connect() -> sqlite3.Connection | None:
    try:
        connection = sqlite3.connect(
            SQE_DB_PATH,
            timeout=_CONNECT_TIMEOUT_SECONDS,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        _init_schema(connection)
        return connection
    except sqlite3.Error as exc:
        _emit_operator_diag(f"baglanti kurulamadi | {exc}")
        return None


def _init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS olcum_sinyalleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kaydedilme TEXT NOT NULL,
            cycle_id TEXT NOT NULL DEFAULT '',
            mac_adi TEXT NOT NULL,
            market TEXT NOT NULL,
            lig TEXT NOT NULL DEFAULT '',
            sport_key TEXT NOT NULL DEFAULT '',
            event_id TEXT NOT NULL DEFAULT '',
            commence_time TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL,
            referans_kaynak TEXT NOT NULL DEFAULT '',
            referans_kitapci INTEGER NOT NULL DEFAULT 0,
            soft_oran REAL NOT NULL,
            sharp_oran REAL NOT NULL,
            fair_olasilik REAL,
            ev REAL NOT NULL,
            onerilen_tutar REAL NOT NULL DEFAULT 0,
            kickoffa_kalan_saat REAL,
            kapanis_sharp_oran REAL,
            kapanis_zamani TEXT,
            clv_pct REAL
        );

        CREATE INDEX IF NOT EXISTS idx_olcum_sinyalleri_kaydedilme
            ON olcum_sinyalleri(kaydedilme);
        CREATE INDEX IF NOT EXISTS idx_olcum_sinyalleri_clv
            ON olcum_sinyalleri(clv_pct);

        CREATE TABLE IF NOT EXISTS olcum_huni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kaydedilme TEXT NOT NULL,
            cycle_id TEXT NOT NULL DEFAULT '',
            asama TEXT NOT NULL,
            adet INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_olcum_huni_kaydedilme
            ON olcum_huni(kaydedilme);
        """
    )


def _hours_to_kickoff(commence_time: str) -> float | None:
    from core.time_utils import parse_utc

    kickoff = parse_utc(commence_time)
    if kickoff is None:
        return None
    return round((kickoff.timestamp() - time.time()) / 3600.0, 3)


def record_signal(
    *,
    mac_adi: str,
    market: str,
    tier: str,
    soft_odds: float,
    sharp_odds: float,
    ev: float,
    cycle_id: str = "",
    league_name: str = "",
    sport_key: str = "",
    event_id: str = "",
    commence_time: str = "",
    consensus_source: str = "",
    consensus_books: int = 0,
    fair_probability: float | None = None,
    stake: float = 0.0,
) -> bool:
    """Bildirilen bir sinyali olcum defterine yazar (para hareketi yok)."""
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False
        try:
            connection.execute(
                """
                INSERT INTO olcum_sinyalleri (
                    kaydedilme, cycle_id, mac_adi, market, lig, sport_key, event_id,
                    commence_time, tier, referans_kaynak, referans_kitapci, soft_oran,
                    sharp_oran, fair_olasilik, ev, onerilen_tutar, kickoffa_kalan_saat
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    str(cycle_id),
                    str(mac_adi),
                    str(market).strip().upper(),
                    str(league_name),
                    str(sport_key),
                    str(event_id),
                    str(commence_time),
                    str(tier).strip().upper(),
                    str(consensus_source),
                    int(consensus_books),
                    float(soft_odds),
                    float(sharp_odds),
                    None if fair_probability is None else float(fair_probability),
                    float(ev),
                    float(stake),
                    _hours_to_kickoff(commence_time),
                ),
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"sinyal yazilamadi | {exc}")
            return False
        finally:
            connection.close()


def record_scan_funnel(stats: dict[str, int], *, cycle_id: str = "") -> bool:
    """Tarama turunun huni sayaclarini yazar (hangi asamada kac aday elendi)."""
    rows = [
        (_now_iso(), str(cycle_id), str(stage), int(count))
        for stage, count in stats.items()
        if isinstance(count, (int, float)) and not isinstance(count, bool)
    ]
    if not rows:
        return False

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return False
        try:
            connection.executemany(
                "INSERT INTO olcum_huni (kaydedilme, cycle_id, asama, adet) VALUES (?, ?, ?, ?)",
                rows,
            )
            connection.commit()
            return True
        except sqlite3.Error as exc:
            _emit_operator_diag(f"huni yazilamadi | {exc}")
            return False
        finally:
            connection.close()


# --- CLV yakalama -------------------------------------------------------------


def capture_pending_clv(limit: int | None = None) -> dict[str, int]:
    """Kickoff'u gecmis sinyaller icin kapanis cizgisini ve CLV'yi isler."""
    from core import clv_tracker

    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return {"toplam": 0, "yakalandi": 0, "atlandi": 0, "beklemede": 0}
        try:
            rows = connection.execute(
                """
                SELECT id, mac_adi, market, event_id, commence_time, sharp_oran
                FROM olcum_sinyalleri
                WHERE clv_pct IS NULL
                ORDER BY id ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"bekleyen sinyaller okunamadi | {exc}")
            connection.close()
            return {"toplam": 0, "yakalandi": 0, "atlandi": 0, "beklemede": 0}

        pending = [dict(row) for row in rows]
        if limit is not None and limit > 0:
            pending = pending[:limit]

        now = time.time()
        index = clv_tracker.get_index(refresh=True)
        captured = 0
        skipped = 0
        waiting = 0
        try:
            for signal in pending:
                kickoff = clv_tracker._parse_commence_epoch(signal.get("commence_time", ""))
                if kickoff is not None and kickoff > now:
                    waiting += 1
                    continue
                closing = clv_tracker.find_closing_sharp_odds(
                    event_id=str(signal.get("event_id", "")),
                    market=str(signal.get("market", "")),
                    commence_time=str(signal.get("commence_time", "")),
                    mac_adi=str(signal.get("mac_adi", "")),
                    index=index,
                )
                if closing is None:
                    skipped += 1
                    continue
                closing_odds = float(closing["sharp_odds"])
                clv = clv_tracker.compute_clv_pct(float(signal["sharp_oran"]), closing_odds)
                connection.execute(
                    """
                    UPDATE olcum_sinyalleri
                    SET kapanis_sharp_oran = ?, kapanis_zamani = ?, clv_pct = ?
                    WHERE id = ?
                    """,
                    (
                        round(closing_odds, 4),
                        datetime.fromtimestamp(
                            float(closing["obs_epoch"]), tz=timezone.utc
                        ).isoformat(),
                        round(clv, 6),
                        int(signal["id"]),
                    ),
                )
                captured += 1
            connection.commit()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"clv yazilamadi | {exc}")
        finally:
            connection.close()

        return {
            "toplam": len(pending),
            "yakalandi": captured,
            "atlandi": skipped,
            "beklemede": waiting,
        }


# --- Rapor --------------------------------------------------------------------

_BREAKDOWNS: dict[str, str] = {
    "referans": "referans_kaynak",
    "katman": "tier",
    "pazar": "market",
    "lig": "lig",
}


def _summarize(rows: list[sqlite3.Row]) -> dict[str, Any]:
    measured = [float(row["clv_pct"]) for row in rows if row["clv_pct"] is not None]
    return {
        "sinyal": len(rows),
        "olculen": len(measured),
        "ort_clv_pct": round(sum(measured) / len(measured) * 100.0, 3) if measured else 0.0,
        "pozitif_clv_orani": (
            round(sum(1 for value in measured if value > 0.0) / len(measured) * 100.0, 1)
            if measured
            else 0.0
        ),
    }


def report(*, min_sample: int = 5) -> dict[str, Any]:
    """Olcum karnesi: toplam + referans / katman / pazar / lig kirilimlari.

    `min_sample` altindaki kirilimlar "yetersiz ornek" olarak isaretlenir; az
    veriyle yontem secmek, olcum yapmamaktan daha tehlikelidir.
    """
    with _DB_LOCK:
        connection = _connect()
        if connection is None:
            return {"toplam": _summarize([]), "kirilimlar": {}}
        try:
            rows = connection.execute("SELECT * FROM olcum_sinyalleri").fetchall()
        except sqlite3.Error as exc:
            _emit_operator_diag(f"rapor okunamadi | {exc}")
            return {"toplam": _summarize([]), "kirilimlar": {}}
        finally:
            connection.close()

    breakdowns: dict[str, dict[str, Any]] = {}
    for label, column in _BREAKDOWNS.items():
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            key = str(row[column] or "bilinmiyor")
            grouped.setdefault(key, []).append(row)
        breakdowns[label] = {
            key: {**_summarize(group), "yeterli_ornek": len(group) >= int(min_sample)}
            for key, group in sorted(grouped.items())
        }

    return {"toplam": _summarize(list(rows)), "kirilimlar": breakdowns}
