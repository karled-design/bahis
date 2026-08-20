"""Matchbook borsasi: kimlik bilgisi ve kota istemeyen ikinci keskin kaynak.

The Odds API ucretsiz kotasi ayda 500 kredidir; tarama gunde birkac lige
sikistigi icin maclarin cogunda karsilastirilacak keskin fiyat hic olusmuyor.
Matchbook'un halka acik "edge" ucu anonim okunabilir: borsa fiyati (Pinnacle
ile ayni sinifta bir referans, bkz. core/price_reference.py) kredi
harcamadan alinir. Betfair'in aksine Turkiye'den erisilebilir ve uygulama
anahtari gerektirmez.

Bu modul yalnizca OKUR — hicbir kosulda bahis gondermez. Istek basarisiz
olursa `{}` doner ve mevcut boru hatti aynen calismaya devam eder.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from config.settings import SOFT_MARKET_LAG_TIMEOUT
from core.market_catalog import (
    family_outcome_count,
    market_family_group_key,
    totals_market_key,
)
from core.price_reference import REFERENCE_EXCHANGE
from scrapers.sharp_feed import attach_fair_probabilities, classify_feed_phase

__all__ = (
    "get_matchbook_sharp_odds",
    "get_last_matchbook_diag",
    "normalize_matchbook_events",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Matchbook Borsa Verisi Alinamadi"
_EVENTS_URL = "https://api.matchbook.com/edge/rest/events"
_SOCCER_SPORT_ID = "15"
# Fiyatli sayfa govdesi buyuktur; kirpma JSON'u bozar (bkz. soft_feed).
_MAX_RESPONSE_BYTES = 32_000_000
_TIMEOUT_SECONDS = max(10, int(SOFT_MARKET_LAG_TIMEOUT) * 3)
_USER_AGENT = "sqe-v1-readonly/1.0"

_PAGE_SIZE = 50
_MAX_PAGES = 8
# Yalnizca en iyi back/lay gerekir; derinlik istemek govdeyi bosuna sisirir.
_PRICE_DEPTH = 1
# Islem gormemis pazarin fiyati referans degildir (birkac dolarlik emir yaniltir).
_MIN_MARKET_VOLUME = 250.0
# Back/lay makasi bu kadar genisse borsanin fiyat gorusu net degildir.
_MAX_SPREAD_RATIO = 1.12
# Nesine bulteninde satilan toplam gol cizgileri (MTID 11/12/13). Borsa daha cok
# cizgi acar; karsiligi olmayan cizgiyi kaydetmek bosuna kayit uretir.
_TOTALS_LINES = (1.5, 2.5, 3.5)
_SPORT_KEY = "matchbook_exchange"
# Back/lay ortasi zaten marjsizdir: aile toplami cogu zaman 1.0'in altina duser
# ve paylasilan Shin devig'i (band: 1.0 < toplam <= 1.30) o aileyi atlar. Bu
# bandda kalan aileler dogrudan normalize edilir; asagisi veri hatasidir.
_MIN_EXCHANGE_OVERROUND = 0.94

_LAST_DIAG: dict[str, Any] = {"kind": "unknown", "detail": ""}


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _set_diag(kind: str, detail: str) -> None:
    global _LAST_DIAG
    _LAST_DIAG = {"kind": kind, "detail": detail}


def get_last_matchbook_diag() -> dict[str, Any]:
    return dict(_LAST_DIAG)


# --- Istek --------------------------------------------------------------------


def _fetch_page(offset: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "sport-ids": _SOCCER_SPORT_ID,
            "states": "open",
            "per-page": _PAGE_SIZE,
            "offset": offset,
            "include-prices": "true",
            "price-depth": _PRICE_DEPTH,
            "odds-type": "DECIMAL",
            "exchange-type": "back-lay",
        }
    )
    request = urllib.request.Request(
        f"{_EVENTS_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        _emit_operator_diag(f"olay listesi alinamadi | offset={offset} | {exc}")
        _set_diag("network", str(exc))
        return []

    if not isinstance(payload, dict):
        _set_diag("schema", "beklenmeyen yanit govdesi")
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        _set_diag("schema", "events alani yok")
        return []
    return [event for event in events if isinstance(event, dict)]


# --- Ayristirma ---------------------------------------------------------------


def _match_name(event_name: str) -> tuple[str, str, str]:
    """'Home vs Away' -> ("Home - Away", "Home", "Away")."""
    text = str(event_name or "").strip()
    for separator in (" vs ", " v ", " VS "):
        if separator in text:
            home, away = text.split(separator, 1)
            home, away = home.strip(), away.strip()
            if home and away:
                return (f"{home} - {away}", home, away)
    return ("", "", "")


def _to_float(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _best_prices(runner: dict[str, Any]) -> tuple[float | None, float | None]:
    """En iyi back ve en iyi lay fiyati (ondalik)."""
    raw_prices = runner.get("prices")
    if not isinstance(raw_prices, list):
        return (None, None)

    best_back: float | None = None
    best_lay: float | None = None
    for entry in raw_prices:
        if not isinstance(entry, dict):
            continue
        odds = _to_float(entry.get("odds"))
        if odds is None or odds <= 1.0:
            continue
        side = str(entry.get("side", "")).strip().casefold()
        if side == "back" and (best_back is None or odds > best_back):
            best_back = odds
        elif side == "lay" and (best_lay is None or odds < best_lay):
            best_lay = odds
    return (best_back, best_lay)


def _mid_odds(runner: dict[str, Any]) -> float | None:
    """Back/lay ortasi (olasilik uzayinda) = borsanin gercek fiyat gorusu.

    Yalniz back tarafi alinirsa sistematik olarak yuksek bir referans cikar ve
    sahte 'deger' uretir; makas cok genisse fiyat hic kullanilmaz.
    """
    back, lay = _best_prices(runner)
    if back is None or lay is None or lay <= back:
        return None
    if lay / back > _MAX_SPREAD_RATIO:
        return None
    implied = ((1.0 / back) + (1.0 / lay)) / 2.0
    if implied <= 0.0:
        return None
    return round(1.0 / implied, 4)


def _market_label(
    market: dict[str, Any],
    runner: dict[str, Any],
    *,
    home: str,
    away: str,
) -> str | None:
    market_type = str(market.get("market-type", "")).strip().casefold()
    runner_name = str(runner.get("name", "")).strip()
    folded = runner_name.casefold()

    if market_type == "one_x_two":
        if folded in {"draw", "the draw"}:
            return "X"
        if folded == home.casefold():
            return "MS1"
        if folded == away.casefold():
            return "MS2"
        return None

    if market_type == "total":
        line = _to_float(market.get("handicap"))
        if line not in _TOTALS_LINES:
            return None
        if folded.startswith("over"):
            return totals_market_key("UST", line)
        if folded.startswith("under"):
            return totals_market_key("ALT", line)
        return None

    if market_type == "both_to_score":
        if folded == "yes":
            return "KG VAR"
        if folded == "no":
            return "KG YOK"
    return None


def _is_prematch_market(market: dict[str, Any], event_phase: str) -> bool:
    if str(market.get("status", "")).strip().casefold() != "open":
        return False
    if market.get("in-running-flag") is True:
        return False
    volume = _to_float(market.get("volume")) or 0.0
    if volume < _MIN_MARKET_VOLUME:
        return False
    return event_phase == "prematch"


def normalize_matchbook_events(
    events: list[dict[str, Any]],
    *,
    observed_at: float,
) -> dict[str, dict[str, str | float]]:
    """Matchbook olaylarini hattin bekledigi keskin kayit sozlugune cevirir."""
    normalized: dict[str, dict[str, str | float]] = {}

    for event in events:
        if str(event.get("status", "")).strip().casefold() != "open":
            continue
        if event.get("in-running-flag") is True:
            continue

        match_name, home, away = _match_name(str(event.get("name", "")))
        event_id = str(event.get("id", "")).strip()
        commence_time = str(event.get("start", "")).strip()
        if not match_name or not event_id:
            continue
        event_phase = classify_feed_phase(commence_time)
        if event_phase != "prematch":
            continue

        markets = event.get("markets")
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict) or not _is_prematch_market(market, event_phase):
                continue
            runners = market.get("runners")
            if not isinstance(runners, list):
                continue

            for runner in runners:
                if not isinstance(runner, dict):
                    continue
                if str(runner.get("status", "")).strip().casefold() != "open":
                    continue
                label = _market_label(market, runner, home=home, away=away)
                if label is None:
                    continue
                odds = _mid_odds(runner)
                if odds is None:
                    continue

                normalized[f"{event_id}:{label}"] = {
                    "match_name": match_name,
                    "market": label,
                    "sharp_odds": odds,
                    "consensus_books": 1,
                    "consensus_source": REFERENCE_EXCHANGE,
                    "event_id": event_id,
                    "commence_time": commence_time,
                    "feed_phase": event_phase,
                    "sport_key": _SPORT_KEY,
                    "league_name": "Matchbook Exchange",
                    "observed_at": observed_at,
                }

    attach_fair_probabilities(normalized)
    _attach_vig_free_probabilities(normalized)
    return normalized


def _attach_vig_free_probabilities(normalized: dict[str, dict[str, str | float]]) -> None:
    """Toplami 1.0'in altinda kalan (marjsiz) borsa ailelerini normalize eder.

    Shin devig'i yalnizca marjli kitapci fiyatlari icindir; borsanin back/lay
    ortasinda cikarilacak marj yoktur, dogrudan olcekleme dogru tahmindir.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for bucket_key, entry in normalized.items():
        if "fair_probability" in entry:
            continue
        group_key = market_family_group_key(str(entry.get("market", "")))
        if group_key is None:
            continue
        event_key = str(entry.get("event_id", "")).strip() or bucket_key.split(":", 1)[0]
        groups.setdefault((event_key, group_key), []).append(bucket_key)

    for (_event_key, group_key), bucket_keys in groups.items():
        expected = family_outcome_count(group_key)
        if expected is None or len(bucket_keys) != expected:
            continue

        implied: list[float] = []
        for bucket_key in bucket_keys:
            odds = _to_float(normalized[bucket_key].get("sharp_odds"))
            if odds is None or odds <= 1.0:
                implied = []
                break
            implied.append(1.0 / odds)
        if not implied:
            continue

        overround = sum(implied)
        if not (_MIN_EXCHANGE_OVERROUND <= overround <= 1.0):
            continue

        for bucket_key, value in zip(bucket_keys, implied):
            normalized[bucket_key]["fair_probability"] = round(value / overround, 6)
            normalized[bucket_key]["market_overround"] = round(overround, 4)


def get_matchbook_sharp_odds() -> dict[str, dict[str, str | float]]:
    """Matchbook borsasindan mac-oncesi keskin fiyatlar (0 API kredisi)."""
    events: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        batch = _fetch_page(page * _PAGE_SIZE)
        events.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break

    if not events:
        _set_diag("empty", "olay listesi bos")
        return {}

    normalized = normalize_matchbook_events(events, observed_at=time.time())
    _set_diag("ok", f"matchbook kayit={len(normalized)}")
    print(
        f"[SQE-V1] Matchbook borsa | olay={len(events)} | kayit={len(normalized)} | kredi=0",
        file=sys.stderr,
    )
    return normalized
