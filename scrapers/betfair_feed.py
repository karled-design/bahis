"""Betfair Exchange (gecikmeli anahtar) ikinci keskin fiyat kaynagi.

The Odds API ucretsiz kotasi ayda 500 kredidir; bu yuzden tarama gunde birkac
lige sikisiyor ve maclarin cogu icin karsilastirilacak keskin fiyat hic
olusmuyor. Betfair Exchange'in **delayed** uygulama anahtari ucretsizdir ve
istek sayisi kredi defterine yazilmaz: ayni maclar icin borsa fiyati
(Pinnacle ile ayni sinifta bir referans, bkz. core/price_reference.py) kota
harcamadan alinabilir.

Gecikme (1-180 sn) mac oncesi fiyat icin onemsizdir; canli bahis icin
kullanilmaz. Bu modul yalnizca OKUR — hicbir kosulda bahis gondermez.

Kimlik bilgileri yoksa modul sessizce kapalidir (`{}` doner) ve mevcut boru
hatti hicbir sekilde etkilenmez.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import (
    BETFAIR_APP_KEY,
    BETFAIR_PASSWORD,
    BETFAIR_USERNAME,
    SOFT_MARKET_LAG_TIMEOUT,
)
from core.market_catalog import totals_market_key
from core.price_reference import REFERENCE_EXCHANGE
from scrapers.sharp_feed import attach_fair_probabilities, classify_feed_phase

__all__ = ("get_betfair_sharp_odds", "is_betfair_enabled", "get_last_betfair_diag")

_OPERATOR_DIAG = "Donanim Erisilemiyor: Betfair Borsa Verisi Alinamadi"
_LOGIN_URL = "https://identitysso.betfair.com/api/login"
_BETTING_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"
_MAX_RESPONSE_BYTES = 8_000_000
_SESSION_TTL_SECONDS = 3.0 * 3600.0
_TIMEOUT_SECONDS = max(10, int(SOFT_MARKET_LAG_TIMEOUT) * 3)

_SOCCER_EVENT_TYPE_ID = "1"
_MARKET_TYPES = ("MATCH_ODDS", "OVER_UNDER_25")
# Bulten ufku: Nesine zaten ~1 hafta oncesini satar; yakin maclar oncelikli.
_HORIZON_HOURS = 30.0
_CATALOGUE_LIMIT = 100
# listMarketBook tek istekte cok pazar kabul etmez; kucuk gruplar halinde sorulur.
_BOOK_BATCH_SIZE = 20
# Islem gormemis pazarin fiyati referans degildir (birkac kuruslук emir yaniltir).
_MIN_TOTAL_MATCHED = 500.0
# Back/lay makasi bu kadar genisse fiyat guvenilir sayilmaz.
_MAX_SPREAD_RATIO = 1.10
_SPORT_KEY = "betfair_exchange"

_SESSION_TOKEN: str = ""
_SESSION_CREATED_AT: float = 0.0
_LAST_DIAG: dict[str, Any] = {"kind": "unknown", "detail": ""}


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _set_diag(kind: str, detail: str) -> None:
    global _LAST_DIAG
    _LAST_DIAG = {"kind": kind, "detail": detail}


def get_last_betfair_diag() -> dict[str, Any]:
    return dict(_LAST_DIAG)


def is_betfair_enabled() -> bool:
    """Uc kimlik bilgisi de tanimliysa kaynak aciktir."""
    return bool(BETFAIR_APP_KEY and BETFAIR_USERNAME and BETFAIR_PASSWORD)


# --- Oturum -------------------------------------------------------------------


def _read_json(request: urllib.request.Request) -> Any:
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = response.read(_MAX_RESPONSE_BYTES)
    return json.loads(payload.decode("utf-8", errors="replace"))


def _login() -> str:
    """Etkilesimli oturum acar; token TTL boyunca yeniden kullanilir."""
    global _SESSION_TOKEN, _SESSION_CREATED_AT

    if _SESSION_TOKEN and (time.time() - _SESSION_CREATED_AT) < _SESSION_TTL_SECONDS:
        return _SESSION_TOKEN

    body = urllib.parse.urlencode(
        {"username": BETFAIR_USERNAME, "password": BETFAIR_PASSWORD}
    ).encode("utf-8")
    request = urllib.request.Request(
        _LOGIN_URL,
        data=body,
        headers={
            "X-Application": BETFAIR_APP_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        payload = _read_json(request)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        _emit_operator_diag(f"oturum acilamadi | {exc}")
        _set_diag("auth", str(exc))
        return ""

    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        detail = "" if not isinstance(payload, dict) else str(payload.get("error", payload.get("status", "")))
        _emit_operator_diag(f"oturum reddedildi | {detail}")
        _set_diag("auth", detail)
        return ""

    _SESSION_TOKEN = str(payload.get("token", "")).strip()
    _SESSION_CREATED_AT = time.time()
    return _SESSION_TOKEN


def _betting_call(endpoint: str, payload: dict[str, Any]) -> Any:
    token = _login()
    if not token:
        return None

    request = urllib.request.Request(
        f"{_BETTING_URL}/{endpoint}/",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-Application": BETFAIR_APP_KEY,
            "X-Authentication": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        return _read_json(request)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        _emit_operator_diag(f"{endpoint} istegi basarisiz | {exc}")
        _set_diag("network", f"{endpoint} | {exc}")
        return None


# --- Ayristirma ---------------------------------------------------------------


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_match_name(event_name: str) -> str:
    """Betfair 'Home v Away' -> bizim 'Home - Away' bicimimiz."""
    text = str(event_name or "").strip()
    for separator in (" v ", " vs ", " V "):
        if separator in text:
            home, away = text.split(separator, 1)
            return f"{home.strip()} - {away.strip()}"
    return text


def _market_label(market_type: str, runner_name: str, sort_priority: int) -> str | None:
    name = str(runner_name or "").strip().casefold()
    if market_type == "MATCH_ODDS":
        if name == "the draw":
            return "X"
        return {1: "MS1", 2: "MS2", 3: "X"}.get(int(sort_priority or 0))
    if market_type == "OVER_UNDER_25":
        if name.startswith("over"):
            return totals_market_key("UST", 2.5)
        if name.startswith("under"):
            return totals_market_key("ALT", 2.5)
    return None


def _best_price(prices: Any) -> float | None:
    if not isinstance(prices, list) or not prices:
        return None
    first = prices[0]
    if not isinstance(first, dict):
        return None
    raw = first.get("price")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        price = float(raw)
    except ValueError:
        return None
    return price if price > 1.0 else None


def _mid_odds(runner_book: dict[str, Any]) -> float | None:
    """Back/lay ortasi (olasilik uzayinda) = borsanin gercek fiyat gorusu.

    Yalniz back fiyati alinirsa komisyon oncesi bile sistematik olarak yuksek
    bir referans elde edilir ve sahte 'deger' uretir.
    """
    exchange = runner_book.get("ex")
    if not isinstance(exchange, dict):
        return None
    back = _best_price(exchange.get("availableToBack"))
    lay = _best_price(exchange.get("availableToLay"))
    if back is None or lay is None:
        return None
    if lay / back > _MAX_SPREAD_RATIO:
        return None
    implied = ((1.0 / back) + (1.0 / lay)) / 2.0
    if implied <= 0.0:
        return None
    return round(1.0 / implied, 4)


def _fetch_catalogue() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    payload = {
        "filter": {
            "eventTypeIds": [_SOCCER_EVENT_TYPE_ID],
            "marketTypeCodes": list(_MARKET_TYPES),
            "marketStartTime": {
                "from": _iso_utc(now),
                "to": _iso_utc(now + timedelta(hours=_HORIZON_HOURS)),
            },
        },
        "marketProjection": [
            "EVENT",
            "COMPETITION",
            "MARKET_START_TIME",
            "RUNNER_DESCRIPTION",
        ],
        "sort": "MAXIMUM_TRADED",
        "maxResults": _CATALOGUE_LIMIT,
    }
    response = _betting_call("listMarketCatalogue", payload)
    if not isinstance(response, list):
        return []
    return [item for item in response if isinstance(item, dict)]


def _fetch_books(market_ids: list[str]) -> dict[str, dict[str, Any]]:
    books: dict[str, dict[str, Any]] = {}
    for start in range(0, len(market_ids), _BOOK_BATCH_SIZE):
        batch = market_ids[start : start + _BOOK_BATCH_SIZE]
        response = _betting_call(
            "listMarketBook",
            {
                "marketIds": batch,
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
            },
        )
        if not isinstance(response, list):
            continue
        for book in response:
            if isinstance(book, dict) and book.get("marketId"):
                books[str(book["marketId"])] = book
    return books


def _market_type_of(catalogue_item: dict[str, Any]) -> str:
    description = catalogue_item.get("description")
    if isinstance(description, dict):
        market_type = str(description.get("marketType", "")).strip().upper()
        if market_type:
            return market_type
    # marketName ile geri dusus: "Match Odds" / "Over/Under 2.5 Goals"
    name = str(catalogue_item.get("marketName", "")).strip().casefold()
    if name == "match odds":
        return "MATCH_ODDS"
    if "2.5" in name:
        return "OVER_UNDER_25"
    return ""


def _normalize_catalogue(
    catalogue: list[dict[str, Any]],
    books: dict[str, dict[str, Any]],
    *,
    observed_at: float,
) -> dict[str, dict[str, str | float]]:
    normalized: dict[str, dict[str, str | float]] = {}

    for item in catalogue:
        market_id = str(item.get("marketId", "")).strip()
        book = books.get(market_id)
        if not market_id or not isinstance(book, dict):
            continue
        if str(book.get("status", "")).strip().upper() != "OPEN":
            continue
        if book.get("inplay") is True:
            continue
        try:
            total_matched = float(book.get("totalMatched", 0.0) or 0.0)
        except (TypeError, ValueError):
            total_matched = 0.0
        if total_matched < _MIN_TOTAL_MATCHED:
            continue

        market_type = _market_type_of(item)
        if market_type not in _MARKET_TYPES:
            continue

        raw_event = item.get("event")
        event: dict[str, Any] = raw_event if isinstance(raw_event, dict) else {}
        match_name = _event_match_name(str(event.get("name", "")))
        event_id = str(event.get("id", "")).strip()
        commence_time = str(item.get("marketStartTime", event.get("openDate", ""))).strip()
        if not match_name or not event_id:
            continue
        feed_phase = classify_feed_phase(commence_time)
        if feed_phase != "prematch":
            continue

        raw_competition = item.get("competition")
        competition: dict[str, Any] = raw_competition if isinstance(raw_competition, dict) else {}
        league_name = str(competition.get("name", "")).strip() or "Betfair Exchange"

        runner_names = {
            int(runner.get("selectionId", 0)): (
                str(runner.get("runnerName", "")),
                int(runner.get("sortPriority", 0) or 0),
            )
            for runner in item.get("runners", [])
            if isinstance(runner, dict)
        }

        for runner_book in book.get("runners", []):
            if not isinstance(runner_book, dict):
                continue
            if str(runner_book.get("status", "")).strip().upper() != "ACTIVE":
                continue
            selection_id = int(runner_book.get("selectionId", 0) or 0)
            runner_name, sort_priority = runner_names.get(selection_id, ("", 0))
            market_label = _market_label(market_type, runner_name, sort_priority)
            if market_label is None:
                continue
            odds = _mid_odds(runner_book)
            if odds is None:
                continue

            normalized[f"{event_id}:{market_label}"] = {
                "match_name": match_name,
                "market": market_label,
                "sharp_odds": odds,
                "consensus_books": 1,
                "consensus_source": REFERENCE_EXCHANGE,
                "event_id": event_id,
                "commence_time": commence_time,
                "feed_phase": feed_phase,
                "sport_key": _SPORT_KEY,
                "league_name": league_name,
                "observed_at": observed_at,
            }

    attach_fair_probabilities(normalized)
    return normalized


def get_betfair_sharp_odds() -> dict[str, dict[str, str | float]]:
    """Betfair Exchange'ten mac-oncesi keskin fiyatlari doner (0 API kredisi).

    Kimlik bilgisi yoksa ya da istek basarisizsa `{}` doner; cagiran taraf
    mevcut Odds API akisiyla calismaya devam eder.
    """
    if not is_betfair_enabled():
        _set_diag("off", "kimlik bilgisi yok")
        return {}

    catalogue = _fetch_catalogue()
    if not catalogue:
        _set_diag("empty", "pazar katalogu bos")
        return {}

    market_ids = [
        str(item.get("marketId", "")).strip()
        for item in catalogue
        if str(item.get("marketId", "")).strip()
    ]
    books = _fetch_books(market_ids)
    if not books:
        _set_diag("empty", "fiyat defteri bos")
        return {}

    normalized = _normalize_catalogue(catalogue, books, observed_at=time.time())
    _set_diag("ok", f"betfair kayit={len(normalized)}")
    print(
        f"[SQE-V1] Betfair borsa | pazar={len(market_ids)} | kayit={len(normalized)} | kredi=0",
        file=sys.stderr,
    )
    return normalized
