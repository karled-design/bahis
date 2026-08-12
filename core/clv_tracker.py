"""CLV (Closing Line Value) izleyici.

Amac: Bir kupon icin "alarm anindaki sharp/konsensus oran" ile
"mac baslamadan hemen onceki son sharp/konsensus oran" (kapanis cizgisi)
arasindaki hareketi olcmek. Oran bizim sectigimiz tarafa dogru kisaldiysa
(kapanis < alarm) piyasa bizimle ayni yone hareket etmis demektir -> pozitif CLV.

Kapanis verisi zaten pasif olarak `database/feed_snapshots/*_live.json`
dosyalarinda tutuluyor; bu modul o anlik goruntuleri okuyup en son
mac-oncesi sharp orani bulur. Canli tarama/uyari motoruna DOKUNMAZ.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from core.time_utils import parse_utc
from database import db_manager

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SNAPSHOT_DIR = _PROJECT_ROOT / "database" / "feed_snapshots"

# Mac-oncesi son gozlem icin kickoff toleransi (sn). 0 = kati: yalnizca
# kickoff anindan once gozlenen oranlar kapanis sayilir.
_KICKOFF_TOLERANCE_SECONDS = 0.0

_INDEX_CACHE: Optional[dict[str, dict[tuple[str, str], list[dict[str, Any]]]]] = None


def _normalize_name(name: Any) -> str:
    return " ".join(str(name).strip().casefold().split())


def _parse_commence_epoch(value: Any) -> Optional[float]:
    parsed = parse_utc(str(value))
    return None if parsed is None else parsed.timestamp()


def _observation_epoch(match: dict[str, Any], snapshot_saved_at: float) -> float:
    for key in ("sharp_observed_at", "observed_at"):
        try:
            val = float(match.get(key))
        except (TypeError, ValueError):
            val = 0.0
        if val > 0.0:
            return val
    return float(snapshot_saved_at or 0.0)


def _iter_snapshot_observations() -> Iterator[dict[str, Any]]:
    if not _SNAPSHOT_DIR.is_dir():
        return
    for path in sorted(_SNAPSHOT_DIR.glob("*_live.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        saved_at = float(payload.get("saved_at", 0.0) or 0.0)
        for match in payload.get("matches", []):
            if not isinstance(match, dict):
                continue
            try:
                sharp = float(match.get("sharp_odds"))
            except (TypeError, ValueError):
                continue
            if sharp <= 1.0:
                continue
            yield {
                "event_id": str(match.get("event_id", "")).strip(),
                "market": str(match.get("market", "")).strip(),
                "norm_name": _normalize_name(match.get("match_name", "")),
                "sharp_odds": sharp,
                "obs_epoch": _observation_epoch(match, saved_at),
                "books": int(match.get("consensus_books", 0) or 0),
            }


def _build_index() -> dict[str, dict[tuple[str, str], list[dict[str, Any]]]]:
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for obs in _iter_snapshot_observations():
        if obs["event_id"]:
            by_event.setdefault((obs["event_id"], obs["market"]), []).append(obs)
        if obs["norm_name"]:
            by_name.setdefault((obs["norm_name"], obs["market"]), []).append(obs)
    return {"by_event": by_event, "by_name": by_name}


def get_index(*, refresh: bool = False) -> dict[str, dict[tuple[str, str], list[dict[str, Any]]]]:
    global _INDEX_CACHE
    if _INDEX_CACHE is None or refresh:
        _INDEX_CACHE = _build_index()
    return _INDEX_CACHE


def find_closing_sharp_odds(
    *,
    event_id: str,
    market: str,
    commence_time: str,
    mac_adi: str = "",
    index: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Kickoff'tan onceki en son sharp orani (kapanis cizgisi) dondurur."""
    idx = index or get_index()
    kickoff = _parse_commence_epoch(commence_time)
    market = str(market).strip()
    event_id = str(event_id).strip()

    candidates: list[dict[str, Any]] = []
    if event_id:
        candidates = idx["by_event"].get((event_id, market), [])
    if not candidates and mac_adi:
        candidates = idx["by_name"].get((_normalize_name(mac_adi), market), [])
    if not candidates:
        return None

    best: Optional[dict[str, Any]] = None
    for obs in candidates:
        if kickoff is not None and obs["obs_epoch"] > kickoff + _KICKOFF_TOLERANCE_SECONDS:
            continue
        if best is None or obs["obs_epoch"] > best["obs_epoch"]:
            best = obs
    return best


def compute_clv_pct(sharp_at_alert: float, closing_sharp: float) -> float:
    """CLV = alarm orani / kapanis orani - 1. Pozitif = oran lehe kisaldi."""
    if closing_sharp <= 0.0:
        return 0.0
    return (float(sharp_at_alert) / float(closing_sharp)) - 1.0


def capture_for_kupon(
    kupon: dict[str, Any],
    *,
    index: Optional[dict[str, Any]] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Bir kupon icin kapanis cizgisini bulur, CLV hesaplar, istege bagli yazar."""
    closing = find_closing_sharp_odds(
        event_id=str(kupon.get("event_id", "")),
        market=str(kupon.get("market", "")),
        commence_time=str(kupon.get("commence_time", "")),
        mac_adi=str(kupon.get("mac_adi", "")),
        index=index,
    )
    if closing is None:
        return {"ok": False, "reason": "kapanis_gozlenemedi"}

    sharp_alert = float(kupon.get("sharp_oran", 0.0))
    closing_odds = float(closing["sharp_odds"])
    clv = compute_clv_pct(sharp_alert, closing_odds)
    captured_at_iso = datetime.fromtimestamp(
        float(closing["obs_epoch"]), tz=timezone.utc
    ).isoformat()

    persisted = True
    if persist:
        persisted = db_manager.set_kupon_closing(
            int(kupon["id"]), closing_odds, captured_at_iso, clv
        )
    return {
        "ok": bool(persisted),
        "kupon_id": int(kupon.get("id", 0)),
        "closing_sharp_oran": round(closing_odds, 2),
        "clv_pct": round(clv, 6),
        "books": int(closing.get("books", 0)),
        "captured_at": captured_at_iso,
    }


def backfill_missing(limit: Optional[int] = None) -> dict[str, Any]:
    """Kapanis orani eksik kuponlar icin CLV yakalar.

    Yalnizca kickoff'u GECMIS maclar islenir; baslamamis maclar 'beklemede'
    sayilir ve kapanis NULL kalir (sonraki turda tekrar denenir).
    """
    pending = db_manager.get_kupons_needing_closing()
    if limit is not None and limit > 0:
        pending = pending[:limit]
    if not pending:
        return {"toplam": 0, "yakalandi": 0, "atlandi": 0, "beklemede": 0}

    now = time.time()
    index = get_index(refresh=True)
    captured = 0
    skipped = 0
    waiting = 0
    for kupon in pending:
        kickoff = _parse_commence_epoch(kupon.get("commence_time", ""))
        if kickoff is not None and kickoff > now:
            waiting += 1
            continue
        result = capture_for_kupon(kupon, index=index)
        if result.get("ok"):
            captured += 1
        else:
            skipped += 1
    return {
        "toplam": len(pending),
        "yakalandi": captured,
        "atlandi": skipped,
        "beklemede": waiting,
    }


def scorecard() -> dict[str, Any]:
    """CLV karnesini dondurur (db_manager uzerinden)."""
    return db_manager.get_clv_scorecard()


# --- Asama 4: Miktar guvenligi (kanit kapisi) ---------------------------------
# Sistem "kanitlandi" (normal miktara gec) sayilmasi icin gereken en az olculen
# kupon sayisi. Kullanici karari (2026-07-13): 30 mac + ortalama CLV artida.
CLV_PROOF_MIN_SAMPLE = 30


def is_scorecard_proven(card: dict[str, Any]) -> bool:
    """CLV karnesi 'kanitlandi' mi? Iki sart birlikte:
    (1) en az CLV_PROOF_MIN_SAMPLE kupon olculmus VE
    (2) ortalama CLV sifirin ustunde (kapanis cizgisini yeniyoruz).
    Karne okunamaz/eksikse guvenli taraf = kanitsiz (False) -> kucuk miktar.
    """
    try:
        olculen = int(card.get("olculen", 0) or 0)
        ort = float(card.get("ort_clv_pct", 0.0) or 0.0)
    except (TypeError, ValueError, AttributeError):
        return False
    return olculen >= CLV_PROOF_MIN_SAMPLE and ort > 0.0
