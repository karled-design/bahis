from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from config.settings import (
    MIN_CONSENSUS_BOOKMAKERS,
    ODDS_API_KEY,
    ODDS_API_REGIONS,
    SOFT_MARKET_LAG_TIMEOUT,
)
from core.market_catalog import (
    family_outcome_count,
    market_family_group_key,
    totals_market_key,
)
from core.devig import fair_probabilities
from core.price_reference import (
    EXCHANGE_BOOKMAKERS,
    PINNACLE_KEY,
    REFERENCE_EXCHANGE,
    REFERENCE_MARKET,
    REFERENCE_PINNACLE,
    REFERENCE_SECONDARY,
    SECONDARY_SHARP_BOOKMAKERS,
    probability_space_mean_odds,
)
from core.scan_league_settings import (
    SHARP_LEAGUE_CATALOG,
    get_enabled_sharp_sport_keys,
    select_rotated_sharp_sport_keys,
)

__all__ = (
    "attach_fair_probabilities",
    "classify_feed_phase",
    "get_sharp_live_odds",
    "fetch_settlement_results",
    "check_odds_api_health",
    "get_last_sharp_scan_diag",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Kuresel Borsa Verisi Alinamadi"
_MAX_RESPONSE_BYTES = 5_000_000
_ODDS_API_ODDS_TEMPLATE = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
_ODDS_API_SCORES_TEMPLATE = "https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
_SETTLEMENT_DAYS_FROM = 3
_SETTLEMENT_LEAGUES_PER_FETCH = 3
_SETTLEMENT_ROTATION_INDEX = 0
_H2H_MARKETS = frozenset({"MS1", "MS2", "X"})
# Yan pazarlar (Asama B): btts + h2h_h1 ana bulten cagrisiyla GELMEZ; yalnizca
# mac-basina uctan cekilir (_enrich_events_with_side_markets). Ana cagrinin
# pazar listesi degismez (h2h,totals) — kredi maliyeti sabit kalir.
_SIDE_MARKETS_QUERY = "btts,h2h_h1"
_SIDE_MARKET_REGIONS = "eu"
_SIDE_MARKET_HORIZON_HOURS = 36.0
_SIDE_MARKET_PER_EVENT_COST = 2.0  # eu bolgesi x 2 pazar — 2026-07-12'de olculdu
_BTTS_LABELS = {"yes": "KG VAR", "no": "KG YOK"}
_ODDS_API_EVENT_ODDS_TEMPLATE = (
    "https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
)
# Odds API'den istenen pazarlar: mac sonucu + toplam gol alt/ust.
# DIKKAT: buraya pazar eklemek istek basina kredi maliyetini katlar.
_SUPPORTED_API_MARKETS = frozenset({"h2h", "totals", "btts", "h2h_h1"})
# Devig sinirlari: ayni pazar ailesinin ortuk olasilik toplami bu bandin
# disindaysa veri suphelidir, marj temizligi uygulanmaz (ham 1/oran kalir).
_DEVIG_MIN_OVERROUND = 1.0
_DEVIG_MAX_OVERROUND = 1.30
# Canli referans kalitesi core/price_reference.py'de tanimli (Pinnacle > borsa >
# ikincil kitapci > piyasa). Asagidaki kume artik yalnizca GOLGE defterinin
# "keskin sayilan" kolonu icin durur; degistirilirse gecmis kiyas bozulur.
_SHARP_BOOKMAKER_KEYS = frozenset(
    {
        "pinnacle",
        "betfair_ex_uk",
        "betfair_sb_uk",
        "matchbook",
        "smarkets",
        "bet365",
        "unibet_uk",
    }
)

# Katalog: core/scan_league_settings.SHARP_LEAGUE_CATALOG (panelden ac/kapa).
_SPORT_KEY_LABELS: dict[str, str] = {
    entry["sport_key"]: entry["label"] for entry in SHARP_LEAGUE_CATALOG
}
_SHARP_ROTATION_INDEX = 0
_ODDS_API_SPORTS_URL = "https://api.the-odds-api.com/v4/sports/"
_LAST_SHARP_SCAN_DIAG: dict[str, Any] = {
    "kind": "unknown",
    "detail": "",
    "quota_remaining": None,
    "quota_used": None,
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def get_last_sharp_scan_diag() -> dict[str, Any]:
    return dict(_LAST_SHARP_SCAN_DIAG)


def _set_sharp_scan_diag(kind: str, detail: str, *, quota_remaining: object = None, quota_used: object = None) -> None:
    global _LAST_SHARP_SCAN_DIAG
    _LAST_SHARP_SCAN_DIAG = {
        "kind": kind,
        "detail": detail,
        "quota_remaining": quota_remaining,
        "quota_used": quota_used,
    }


def _read_quota_headers(response: Any) -> tuple[str | None, str | None, str | None]:
    if response is None:
        return None, None, None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None, None, None
    remaining = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    last_cost = headers.get("x-requests-last")
    return (
        str(remaining) if remaining is not None else None,
        str(used) if used is not None else None,
        str(last_cost) if last_cost is not None else None,
    )


def _classify_http_error(status_code: int, err_body: str) -> str:
    lowered = err_body.lower()
    if status_code in {401, 403} or "api key" in lowered or "authorization" in lowered:
        return "auth"
    if status_code == 429 or "quota" in lowered or "usage" in lowered or "exceeded" in lowered:
        return "quota"
    if status_code in {502, 503, 504}:
        return "upstream"
    return "http"


def _emit_fetch_diag(
    *,
    sport_key: str,
    error_kind: str,
    detail: str,
    quota_remaining: str | None = None,
    quota_used: str | None = None,
    mac_sayisi: int | None = None,
) -> None:
    quota_bits: list[str] = []
    if quota_remaining is not None:
        quota_bits.append(f"kalan={quota_remaining}")
    if quota_used is not None:
        quota_bits.append(f"kullanilan={quota_used}")
    quota_suffix = f" | {' | '.join(quota_bits)}" if quota_bits else ""

    if error_kind == "empty":
        print(
            f"Teşhis: Odds API ({sport_key}) | mac_sayisi=0 | neden=lig takviminde mac yok{quota_suffix}",
            file=sys.stderr,
        )
        return

    if error_kind == "ok":
        print(
            f"Teşhis: Odds API ({sport_key}) | mac_sayisi={mac_sayisi}{quota_suffix}",
            file=sys.stderr,
        )
        return

    if error_kind in {"auth", "quota", "timeout", "network", "upstream", "http", "parse"}:
        _emit_operator_diag(f"{sport_key} | {error_kind} | {detail}{quota_suffix}")
        return

    _emit_operator_diag(f"{sport_key} | {detail}{quota_suffix}")


def _build_consensus_feed_url(sport_key: str) -> str | None:
    if not ODDS_API_KEY:
        _emit_operator_diag("ODDS_API_KEY tanimli degil")
        return None
    if not isinstance(sport_key, str) or not sport_key.strip():
        _emit_operator_diag("sport_key gecersiz")
        return None
    query = urllib.parse.urlencode(
        {"apiKey": ODDS_API_KEY, "regions": ODDS_API_REGIONS, "markets": "h2h,totals"}
    )
    return f"{_ODDS_API_ODDS_TEMPLATE.format(sport_key=sport_key.strip())}?{query}"


def _select_rotated_sport_keys() -> list[str]:
    global _SHARP_ROTATION_INDEX

    enabled = get_enabled_sharp_sport_keys()
    if not enabled:
        _emit_operator_diag("sharp lig listesi bos")
        return []

    # Kesif katmani (Adim 2): sezonu kapali veya yakin pencerede maci olmayan
    # ligler 0 kredilik takvim bilgisiyle elenir; kesif verisi yoksa/bayatsa
    # filtre devre disi kalir ve TUM acik ligler taranir (fail-open).
    scannable = list(enabled)
    filter_note = ""
    try:
        from core.league_discovery import filter_scannable_sport_keys

        scannable, filter_note = filter_scannable_sport_keys(list(enabled))
    except Exception as exc:  # kesif asla taramayi engellememeli
        print(f"[SQE-V1] kesif filtresi atlandi | {exc}", file=sys.stderr)
        scannable = list(enabled)
        filter_note = ""

    if not scannable:
        detail = "kesif: yakin pencerede taranacak mac yok"
        if filter_note:
            detail = f"{detail} | {filter_note}"
        _set_sharp_scan_diag("empty", detail)
        print(f"[SQE-V1] Sharp tarama atlandi | {detail} | 0 kredi", file=sys.stderr)
        return []

    if filter_note:
        print(f"[SQE-V1] Kesif filtresi | atlanan: {filter_note}", file=sys.stderr)

    # Pencere plani (Adim 3): oran sorgusu yalnizca maca yakin anlarda hak
    # edilir (T-4s / T-45dk). Pencere kapaliysa bu dongu 0 kredi ile gecer.
    due = list(scannable)
    try:
        from core.scan_scheduler import select_due_leagues

        due, idle_note = select_due_leagues(list(scannable))
        if not due:
            detail = "pencere disi: oran sorgusu bekliyor"
            if idle_note:
                detail = f"{detail} | {idle_note}"
            _set_sharp_scan_diag("empty", detail)
            print(f"[SQE-V1] Sharp tarama atlandi | {detail} | 0 kredi", file=sys.stderr)
            return []
    except Exception as exc:  # planlayici asla taramayi kilitlememeli
        print(f"[SQE-V1] pencere plani atlandi | {exc}", file=sys.stderr)
        due = list(scannable)

    # Gunluk kredi tavani (Adim 3): tavan asilacaksa sorgu sayisini kirp,
    # hic hak kalmadiysa bugunku oran sorgularini durdur.
    try:
        from core.api_credit_ledger import get_today_cap_info

        cap = get_today_cap_info()
        if cap["cap_reached"]:
            detail = (
                f"gunluk kredi tavani doldu ({int(round(cap['spent']))}/{cap['allowance']})"
            )
            _set_sharp_scan_diag("empty", detail)
            print(f"[SQE-V1] Sharp tarama atlandi | {detail} | 0 kredi", file=sys.stderr)
            return []
        if len(due) > int(cap["affordable_fetches"]):
            due = due[: int(cap["affordable_fetches"])]
    except Exception as exc:  # tavan kontrolu asla taramayi kilitlememeli
        print(f"[SQE-V1] tavan kontrolu atlandi | {exc}", file=sys.stderr)

    selected, _SHARP_ROTATION_INDEX = select_rotated_sharp_sport_keys(
        rotation_index=_SHARP_ROTATION_INDEX,
        candidates=due,
    )
    return selected


def _select_rotated_settlement_sport_keys() -> list[str]:
    global _SETTLEMENT_ROTATION_INDEX

    enabled = list(get_enabled_sharp_sport_keys())
    if not enabled:
        return []

    leagues_per_fetch = min(_SETTLEMENT_LEAGUES_PER_FETCH, len(enabled))
    wc_key = "soccer_fifa_world_cup"
    selected: list[str] = []
    if wc_key in enabled:
        selected.append(wc_key)

    while len(selected) < leagues_per_fetch:
        candidate = enabled[_SETTLEMENT_ROTATION_INDEX % len(enabled)]
        _SETTLEMENT_ROTATION_INDEX += 1
        if candidate in selected:
            if len(enabled) <= len(selected):
                break
            continue
        selected.append(candidate)
    return selected


def _build_scores_feed_url(sport_key: str) -> str | None:
    if not ODDS_API_KEY:
        _emit_operator_diag("ODDS_API_KEY tanimli degil")
        return None
    if not isinstance(sport_key, str) or not sport_key.strip():
        _emit_operator_diag("sport_key gecersiz")
        return None
    query = urllib.parse.urlencode(
        {
            "apiKey": ODDS_API_KEY,
            "daysFrom": str(_SETTLEMENT_DAYS_FROM),
        }
    )
    return f"{_ODDS_API_SCORES_TEMPLATE.format(sport_key=sport_key.strip())}?{query}"


def _classify_credit_endpoint(url: str) -> str:
    if "/events/" in url:
        return "yan_pazar"
    if "/odds/" in url:
        return "oran"
    if "/scores/" in url:
        return "skor"
    return "saglik"


def _record_credit_usage(
    url: str,
    last_cost: str | None,
    remaining: str | None,
    used: str | None,
) -> None:
    # Kredi defteri (Adim 1): her cevabin kota basligini gunluge isler.
    # Defter asla canli taramayi bozmamali; hata olursa yut ve teshis bas.
    try:
        from core.api_credit_ledger import record_odds_api_usage

        record_odds_api_usage(
            last_cost=last_cost,
            remaining=remaining,
            used=used,
            endpoint=_classify_credit_endpoint(url),
        )
    except Exception as exc:
        print(f"[SQE-V1] kredi defteri atlandi | {exc}", file=sys.stderr)


def _fetch_odds_api_json(url: str) -> tuple[list[Any] | None, dict[str, Any]]:
    meta: dict[str, Any] = {
        "error_kind": "unknown",
        "detail": "",
        "http_status": None,
        "quota_remaining": None,
        "quota_used": None,
        "quota_last_cost": None,
    }
    request = urllib.request.Request(
        url,
        method="GET",
        headers=_BROWSER_HEADERS,
    )

    try:
        with urllib.request.urlopen(request, timeout=SOFT_MARKET_LAG_TIMEOUT) as response:
            status_code = response.getcode()
            remaining, used, last_cost = _read_quota_headers(response)
            meta["http_status"] = status_code
            meta["quota_remaining"] = remaining
            meta["quota_used"] = used
            meta["quota_last_cost"] = last_cost
            _record_credit_usage(url, last_cost, remaining, used)
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        remaining, used, last_cost = _read_quota_headers(exc)
        meta["http_status"] = exc.code
        meta["quota_remaining"] = remaining
        meta["quota_used"] = used
        meta["quota_last_cost"] = last_cost
        _record_credit_usage(url, last_cost, remaining, used)
        meta["error_kind"] = _classify_http_error(exc.code, err_body)
        meta["detail"] = f"HTTP {exc.code} {exc.reason}"
        if err_body:
            meta["detail"] = f"{meta['detail']} | {err_body[:200]}"
        return None, meta
    except urllib.error.URLError as exc:
        reason = exc.reason if exc.reason is not None else exc
        meta["error_kind"] = "network"
        meta["detail"] = str(reason)
        return None, meta
    except TimeoutError:
        meta["error_kind"] = "timeout"
        meta["detail"] = "request timeout"
        return None, meta
    except OSError as exc:
        meta["error_kind"] = "network"
        meta["detail"] = str(exc)
        return None, meta

    status_code = meta["http_status"]
    if status_code is None or status_code < 200 or status_code >= 300:
        meta["error_kind"] = "http"
        meta["detail"] = f"unexpected HTTP status {status_code}"
        return None, meta

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        meta["error_kind"] = "parse"
        meta["detail"] = "non-JSON response body"
        return None, meta

    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if message:
            lowered = str(message).lower()
            if "quota" in lowered or "usage" in lowered or "exceeded" in lowered:
                meta["error_kind"] = "quota"
            elif "api key" in lowered or "authorization" in lowered:
                meta["error_kind"] = "auth"
            else:
                meta["error_kind"] = "parse"
            meta["detail"] = str(message)
            return None, meta
        # Mac-basina uclar (events/{id}/odds) tek nesne dondurur; tek ogeli
        # listeye sarilir ki tum tuketiciler ayni listeyle calissin.
        return [payload], meta

    if not isinstance(payload, list):
        meta["error_kind"] = "parse"
        meta["detail"] = "unsupported JSON root type"
        return None, meta

    if not payload:
        meta["error_kind"] = "empty"
        meta["detail"] = "lig takviminde mac yok"
    else:
        meta["error_kind"] = "ok"
        meta["detail"] = ""
    return payload, meta


def check_odds_api_health() -> dict[str, Any]:
    if not ODDS_API_KEY:
        result = {"ok": False, "kind": "auth", "detail": "ODDS_API_KEY tanimli degil"}
        _set_sharp_scan_diag(result["kind"], result["detail"])
        return result

    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY})
    url = f"{_ODDS_API_SPORTS_URL}?{query}"
    payload, meta = _fetch_odds_api_json(url)
    if payload is None:
        result = {
            "ok": False,
            "kind": meta.get("error_kind", "unknown"),
            "detail": meta.get("detail", "health-check failed"),
            "quota_remaining": meta.get("quota_remaining"),
            "quota_used": meta.get("quota_used"),
        }
        _set_sharp_scan_diag(result["kind"], result["detail"], quota_remaining=result["quota_remaining"], quota_used=result["quota_used"])
        return result

    result = {
        "ok": True,
        "kind": "ok",
        "detail": "Odds API erisilebilir",
        "active_sports": len(payload),
        "quota_remaining": meta.get("quota_remaining"),
        "quota_used": meta.get("quota_used"),
    }
    _set_sharp_scan_diag(result["kind"], result["detail"], quota_remaining=result["quota_remaining"], quota_used=result["quota_used"])
    return result


def _is_valid_sharp_odds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    odds = float(value)
    if odds <= 1.0:
        return None
    return odds


def _map_market_label(market_key: str, outcome_name: str, home_team: str, away_team: str) -> str:
    normalized_name = outcome_name.strip().lower()
    if market_key == "h2h":
        if normalized_name == home_team.strip().lower():
            return "MS1"
        if normalized_name == away_team.strip().lower():
            return "MS2"
        if normalized_name in {"draw", "beraberlik", "x"}:
            return "X"
    return market_key.upper()


def _map_totals_label(outcome_name: str, point: object) -> str | None:
    """Odds API totals sonucunu ortak anahtara cevirir: Over/2.5 -> "UST 2.5"."""
    normalized_name = outcome_name.strip().lower()
    if normalized_name == "over":
        side = "UST"
    elif normalized_name == "under":
        side = "ALT"
    else:
        return None
    return totals_market_key(side, point)


def _map_first_half_label(outcome_name: str, home_team: str, away_team: str) -> str | None:
    """Odds API h2h_h1 sonucunu ortak anahtara cevirir: ev/dep/Draw -> IY1/IY2/IYX."""
    normalized_name = outcome_name.strip().lower()
    if normalized_name == home_team.strip().lower():
        return "IY1"
    if normalized_name == away_team.strip().lower():
        return "IY2"
    if normalized_name in {"draw", "beraberlik", "x"}:
        return "IYX"
    return None


def _resolve_event_sport_key(event: dict[str, Any], fallback_sport_key: str) -> str:
    raw_key = event.get("sport_key")
    if isinstance(raw_key, str) and raw_key.strip():
        return raw_key.strip()
    return fallback_sport_key.strip()


def _resolve_event_league_name(event: dict[str, Any], sport_key: str) -> str:
    sport_title = event.get("sport_title")
    if isinstance(sport_title, str) and sport_title.strip():
        return sport_title.strip()
    return _SPORT_KEY_LABELS.get(sport_key, sport_key.replace("soccer_", "").replace("_", " ").title())


def _resolve_event_commence_time(event: dict[str, Any]) -> str:
    commence_time = event.get("commence_time")
    if isinstance(commence_time, str) and commence_time.strip():
        return commence_time.strip()
    return ""


def classify_feed_phase(commence_time: str) -> str:
    value = commence_time.strip()
    if not value:
        return ""
    try:
        kickoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return "live" if kickoff <= datetime.now(timezone.utc) else "prematch"


def _collect_price_buckets(
    events: list[Any],
    *,
    sport_key: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, str]]]:
    """Ham fiyat kovalarini toplar: "event:market" -> {"all": [...], "sharp": [...]}.

    Hem canli konsensus (_parse_consensus_feed) hem de golge kaydi
    (core/shadow_consensus) AYNI bu fonksiyonu cagirir; boylece iki yontem
    birebir ayni fiyat kumesini ortalar, aralarinda kayma olmaz. Buradaki
    kovalama mantigini degistirirsen golge karsilastirmasi da ayni kalir.
    """
    price_buckets: dict[str, dict[str, Any]] = {}
    match_names: dict[str, str] = {}
    event_meta: dict[str, dict[str, str]] = {}

    for event in events:
        if not isinstance(event, dict):
            continue

        event_id = event.get("id")
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        if not event_id or not isinstance(home_team, str) or not isinstance(away_team, str):
            continue

        event_key = str(event_id)
        resolved_sport_key = _resolve_event_sport_key(event, sport_key)
        match_names[event_key] = f"{home_team.strip()} - {away_team.strip()}"
        event_meta[event_key] = {
            "event_id": event_key,
            "commence_time": _resolve_event_commence_time(event),
            "sport_key": resolved_sport_key,
            "league_name": _resolve_event_league_name(event, resolved_sport_key),
        }
        bookmakers = event.get("bookmakers")
        if not isinstance(bookmakers, list):
            continue

        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key", "")).strip().lower()
            is_sharp_book = book_key in _SHARP_BOOKMAKER_KEYS
            markets = bookmaker.get("markets")
            if not isinstance(markets, list):
                continue

            for market in markets:
                if not isinstance(market, dict):
                    continue
                market_key = market.get("key")
                outcomes = market.get("outcomes")
                if not isinstance(market_key, str) or not isinstance(outcomes, list):
                    continue
                if market_key not in _SUPPORTED_API_MARKETS:
                    continue

                for outcome in outcomes:
                    if not isinstance(outcome, dict):
                        continue
                    outcome_name = outcome.get("name")
                    price = _is_valid_sharp_odds(outcome.get("price"))
                    if not isinstance(outcome_name, str) or price is None:
                        continue

                    if market_key == "h2h":
                        market_label = _map_market_label(market_key, outcome_name, home_team, away_team)
                        if market_label not in _H2H_MARKETS:
                            continue
                    elif market_key == "totals":
                        # totals: cizgi (point) anahtara gomulur, boylece konsensus
                        # yalnizca AYNI cizgideki fiyatlari ortalar (2.5 ile 3 karismaz).
                        totals_label = _map_totals_label(outcome_name, outcome.get("point"))
                        if totals_label is None:
                            continue
                        market_label = totals_label
                    elif market_key == "btts":
                        btts_label = _BTTS_LABELS.get(outcome_name.strip().lower())
                        if btts_label is None:
                            continue
                        market_label = btts_label
                    elif market_key == "h2h_h1":
                        first_half_label = _map_first_half_label(outcome_name, home_team, away_team)
                        if first_half_label is None:
                            continue
                        market_label = first_half_label
                    else:
                        continue
                    bucket_key = f"{event_key}:{market_label}"
                    if bucket_key not in price_buckets:
                        price_buckets[bucket_key] = {"all": [], "sharp": [], "books": {}}
                    bucket = price_buckets[bucket_key]
                    bucket["all"].append(price)
                    if is_sharp_book:
                        bucket["sharp"].append(price)
                    # Kitapci-bazli fiyat: referans kalitesi (Pinnacle / borsa /
                    # ikincil) ancak boyle ayirt edilebilir.
                    bucket["books"][book_key] = price

    return price_buckets, match_names, event_meta


def _resolve_reference_price(
    bucket: dict[str, Any],
    *,
    min_books: int,
) -> tuple[float, str, int] | None:
    """Referans fiyati kalite sirasina gore secer: Pinnacle > borsa > ikincil > piyasa.

    Ortalama gereken durumlarda fiyatlar OLASILIK uzayinda ortalanir; oran
    uzayinda ortalama ortuk sansi sistematik olarak asagi ceker.
    """
    books: dict[str, float] = dict(bucket.get("books") or {})

    pinnacle_price = books.get(PINNACLE_KEY)
    if isinstance(pinnacle_price, (int, float)) and float(pinnacle_price) > 1.0:
        return float(pinnacle_price), REFERENCE_PINNACLE, 1

    exchange_prices = [price for key, price in books.items() if key in EXCHANGE_BOOKMAKERS]
    if len(exchange_prices) >= min_books:
        odds = probability_space_mean_odds(exchange_prices)
        if odds is not None:
            return odds, REFERENCE_EXCHANGE, len(exchange_prices)

    secondary_prices = [
        price for key, price in books.items() if key in SECONDARY_SHARP_BOOKMAKERS
    ]
    if len(secondary_prices) >= min_books:
        odds = probability_space_mean_odds(secondary_prices)
        if odds is not None:
            return odds, REFERENCE_SECONDARY, len(secondary_prices)

    all_prices: list[float] = list(bucket.get("all") or [])
    if len(all_prices) >= min_books:
        odds = probability_space_mean_odds(all_prices)
        if odds is not None:
            return odds, REFERENCE_MARKET, len(all_prices)
    return None


def _parse_consensus_feed(
    events: list[Any],
    *,
    sport_key: str,
) -> dict[str, dict[str, str | float]]:
    normalized: dict[str, dict[str, str | float]] = {}
    price_buckets, match_names, event_meta = _collect_price_buckets(
        events, sport_key=sport_key
    )

    min_books = max(1, int(MIN_CONSENSUS_BOOKMAKERS))
    for bucket_key, bucket in price_buckets.items():
        event_key, market_label = bucket_key.split(":", 1)
        match_name = match_names.get(event_key)
        if not match_name:
            continue

        resolved = _resolve_reference_price(bucket, min_books=min_books)
        if resolved is None:
            continue
        consensus_odds, source, book_count = resolved

        parsed_odds = _is_valid_sharp_odds(round(consensus_odds, 4))
        if parsed_odds is None:
            continue

        meta = event_meta.get(event_key, {})
        normalized[bucket_key] = {
            "match_name": match_name,
            "market": market_label,
            "sharp_odds": parsed_odds,
            "consensus_books": book_count,
            "consensus_source": source,
            "event_id": meta.get("event_id", event_key),
            "commence_time": meta.get("commence_time", ""),
            "feed_phase": classify_feed_phase(meta.get("commence_time", "")),
            "sport_key": meta.get("sport_key", sport_key),
            "league_name": meta.get("league_name", _SPORT_KEY_LABELS.get(sport_key, sport_key)),
        }

    attach_fair_probabilities(normalized)
    return normalized


def attach_fair_probabilities(normalized: dict[str, dict[str, str | float]]) -> None:
    """Kitapci kar payini (marj) cikarip her kayda fair_probability ekler.

    Ayni macin ayni pazar ailesindeki TUM sonuclar (MS1+X+MS2 veya ALT+UST)
    konsensuste mevcutsa marj Shin yontemiyle cikarilir (core/devig.py). Eksik
    sonuc veya band disi toplam varsa dokunulmaz; tuketici ham 1/oran'a duser.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for bucket_key, entry in normalized.items():
        event_key = str(entry.get("event_id", "")).strip() or bucket_key.split(":", 1)[0]
        group_key = market_family_group_key(str(entry.get("market", "")))
        if group_key is None:
            continue
        groups.setdefault((event_key, group_key), []).append(bucket_key)

    for (_event_key, group_key), bucket_keys in groups.items():
        expected_outcomes = family_outcome_count(group_key)
        if expected_outcomes is None or len(bucket_keys) != expected_outcomes:
            continue

        implied: list[float] = []
        valid = True
        for bucket_key in bucket_keys:
            odds = normalized[bucket_key].get("sharp_odds")
            if isinstance(odds, bool) or not isinstance(odds, (int, float)) or float(odds) <= 1.0:
                valid = False
                break
            implied.append(1.0 / float(odds))
        if not valid:
            continue

        overround = sum(implied)
        if not (_DEVIG_MIN_OVERROUND < overround <= _DEVIG_MAX_OVERROUND):
            continue

        probabilities = fair_probabilities([1.0 / value for value in implied])
        if probabilities is None or len(probabilities) != len(bucket_keys):
            continue

        for bucket_key, fair_prob in zip(bucket_keys, probabilities):
            normalized[bucket_key]["fair_probability"] = round(fair_prob, 6)
            normalized[bucket_key]["market_overround"] = round(overround, 4)


def _parse_score_value(raw_value: object) -> int | None:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
        return None
    try:
        score = int(str(raw_value).strip())
    except ValueError:
        return None
    if score < 0:
        return None
    return score


def _extract_final_scores(event: dict[str, Any]) -> tuple[int, int] | None:
    if event.get("completed") is not True:
        return None

    home_team = event.get("home_team")
    away_team = event.get("away_team")
    if not isinstance(home_team, str) or not isinstance(away_team, str):
        return None

    scores = event.get("scores")
    if not isinstance(scores, list):
        return None

    home_score: int | None = None
    away_score: int | None = None
    home_key = home_team.strip().casefold()
    away_key = away_team.strip().casefold()

    for item in scores:
        if not isinstance(item, dict):
            continue
        team_name = item.get("name")
        if not isinstance(team_name, str):
            continue
        parsed_score = _parse_score_value(item.get("score"))
        if parsed_score is None:
            continue
        team_key = team_name.strip().casefold()
        if team_key == home_key:
            home_score = parsed_score
        elif team_key == away_key:
            away_score = parsed_score

    if home_score is None or away_score is None:
        return None
    return home_score, away_score


def _derive_btts_outcomes(home_score: int, away_score: int) -> dict[str, str]:
    """Karsilikli gol: iki takim da gol attiysa VAR kazanir (mac sonu skoru yeter)."""
    both_scored = home_score > 0 and away_score > 0
    return {
        "KG VAR": "WON" if both_scored else "LOST",
        "KG YOK": "LOST" if both_scored else "WON",
    }


def _derive_h2h_outcomes(home_score: int, away_score: int) -> dict[str, str]:
    if home_score > away_score:
        winning_market = "MS1"
    elif away_score > home_score:
        winning_market = "MS2"
    else:
        winning_market = "X"

    return {
        market: ("WON" if market == winning_market else "LOST")
        for market in _H2H_MARKETS
    }


# Yalnizca yarim cizgiler: tam cizgide (or. 3.0) iade durumu olur, ondan uzak dur.
_TOTALS_SETTLEMENT_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)


def _derive_totals_outcomes(home_score: int, away_score: int) -> dict[str, str]:
    """Toplam golden alt/ust sonuclari: UST kazanir eger toplam > cizgi."""
    total_goals = home_score + away_score
    outcomes: dict[str, str] = {}
    for line in _TOTALS_SETTLEMENT_LINES:
        line_text = "%g" % line
        outcomes[f"UST {line_text}"] = "WON" if total_goals > line else "LOST"
        outcomes[f"ALT {line_text}"] = "WON" if total_goals < line else "LOST"
    return outcomes


def _parse_completed_scores_to_settlement(events: list[Any]) -> list[dict[str, str]]:
    settlement_entries: list[dict[str, str]] = []

    for event in events:
        if not isinstance(event, dict):
            continue

        final_scores = _extract_final_scores(event)
        if final_scores is None:
            continue

        home_team = event.get("home_team")
        away_team = event.get("away_team")
        if not isinstance(home_team, str) or not isinstance(away_team, str):
            continue

        home_score, away_score = final_scores
        match_name = f"{home_team.strip()} - {away_team.strip()}"
        outcomes = _derive_h2h_outcomes(home_score, away_score)
        outcomes.update(_derive_totals_outcomes(home_score, away_score))
        # KG mac sonu skorundan kapanir. IY icin skor kaynagi ilk yari verisi
        # VERMIYOR — o yuzden IY sinyalleri golgede kalir, kupon acilmaz (B4).
        outcomes.update(_derive_btts_outcomes(home_score, away_score))
        event_id = event.get("id")
        normalized_event_id = str(event_id).strip() if event_id is not None else ""

        for market, outcome in outcomes.items():
            settlement_entries.append(
                {
                    "event_id": normalized_event_id,
                    "match_name": match_name,
                    "market": market,
                    "outcome": outcome,
                }
            )

    return settlement_entries


def _fetch_sport_settlement_results(sport_key: str) -> list[dict[str, str]] | None:
    feed_url = _build_scores_feed_url(sport_key)
    if feed_url is None:
        return None

    payload, meta = _fetch_odds_api_json(feed_url)
    if payload is None:
        _emit_fetch_diag(
            sport_key=sport_key,
            error_kind=str(meta.get("error_kind", "unknown")),
            detail=str(meta.get("detail", "scores feed erisilemedi")),
            quota_remaining=meta.get("quota_remaining"),
            quota_used=meta.get("quota_used"),
        )
        return None

    total_events = len(payload)
    completed_events = sum(
        1 for event in payload if isinstance(event, dict) and event.get("completed") is True
    )
    print(
        f"Teşhis: Settlement API ({sport_key}) | mac_toplam={total_events} | "
        f"tamamlanan={completed_events} | daysFrom={_SETTLEMENT_DAYS_FROM} | "
        f"kalan={meta.get('quota_remaining') or '?'}",
        file=sys.stderr,
    )
    return _parse_completed_scores_to_settlement(payload)


def fetch_settlement_results(
    extra_sport_keys: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    # Kredi diyeti (Adim 4): kupon ligleri belliyken YALNIZ onlar sorgulanir.
    # Rotasyon+dolgu secimi, lig bilgisi olmayan eski kuponlar icin yedek kalir.
    if extra_sport_keys:
        sport_keys: list[str] = []
        for key in extra_sport_keys:
            normalized = str(key).strip()
            if normalized and normalized not in sport_keys:
                sport_keys.append(normalized)
    else:
        sport_keys = _select_rotated_settlement_sport_keys()
    if not sport_keys:
        _emit_operator_diag("settlement icin sharp lig listesi bos")
        return []

    merged: list[dict[str, str]] = []
    successful_leagues: list[str] = []
    failed_leagues: list[str] = []

    for sport_key in sport_keys:
        parsed = _fetch_sport_settlement_results(sport_key)
        if parsed is None:
            failed_leagues.append(sport_key)
            continue
        successful_leagues.append(sport_key)
        merged.extend(parsed)

    if failed_leagues and len(failed_leagues) == len(sport_keys):
        raise RuntimeError(
            "settlement scores unavailable | "
            f"ligler={','.join(failed_leagues)}"
        )

    print(
        f"[SQE-V1] Settlement tarama | ligler={','.join(sport_keys)} | "
        f"basarili={','.join(successful_leagues) or 'yok'} | "
        f"kayit={len(merged)}",
        file=sys.stderr,
    )
    return merged


def _build_event_side_markets_url(sport_key: str, event_id: str) -> str | None:
    if not ODDS_API_KEY:
        return None
    query = urllib.parse.urlencode(
        {
            "apiKey": ODDS_API_KEY,
            "regions": _SIDE_MARKET_REGIONS,
            "markets": _SIDE_MARKETS_QUERY,
            "oddsFormat": "decimal",
        }
    )
    template = _ODDS_API_EVENT_ODDS_TEMPLATE.format(
        sport_key=sport_key.strip(), event_id=event_id
    )
    return f"{template}?{query}"


def _side_market_budget_left() -> float:
    """Gunluk yan-pazar kredi tavanindan kalan pay (kredi defterinden)."""
    from core.api_credit_ledger import get_today_endpoint_spent
    from core.strategy_settings import get_side_market_daily_credit_cap

    cap = float(get_side_market_daily_credit_cap())
    spent = float(get_today_endpoint_spent("yan_pazar"))
    return cap - spent


def _is_event_in_side_market_window(event: dict[str, Any]) -> bool:
    commence_time = _resolve_event_commence_time(event)
    if not commence_time:
        return False
    try:
        kickoff = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if kickoff <= now:
        return False
    return (kickoff - now).total_seconds() <= _SIDE_MARKET_HORIZON_HOURS * 3600.0


def _enrich_events_with_side_markets(events: list[Any], sport_key: str) -> None:
    """Kart 1 acikken KG + IY fiyatlarini mac-basina cekip ayni olaya kaynastirir.

    Dort fren birden: strateji dugmesi (varsayilan kapali) + cekirdek pazar CLV
    kaniti (Asama 5 — kanitlanana kadar donduruldu) + kickoff penceresi (36 saat)
    + gunluk yan-pazar kredi tavani. Herhangi bir hata yalnizca yan pazari
    atlatir; ana tarama ETKILENMEZ.
    """
    try:
        from core.strategy_settings import is_side_markets_effective

        if not is_side_markets_effective():
            return
    except Exception as exc:
        print(f"[SQE-V1] strateji ayari okunamadi, yan pazar atlandi | {exc}", file=sys.stderr)
        return

    fetched = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_event_in_side_market_window(event):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue

        try:
            budget_left = _side_market_budget_left()
        except Exception as exc:
            print(f"[SQE-V1] yan pazar tavani okunamadi, cekim durdu | {exc}", file=sys.stderr)
            return
        if budget_left < _SIDE_MARKET_PER_EVENT_COST:
            print(
                "[SQE-V1] Yan pazar tavani doldu | bugunku KG+IY cekimleri durdu",
                file=sys.stderr,
            )
            return

        url = _build_event_side_markets_url(sport_key, event_id)
        if url is None:
            return
        payload, meta = _fetch_odds_api_json(url)
        if not payload:
            print(
                f"[SQE-V1] yan pazar cekilemedi | {event_id} | "
                f"{meta.get('error_kind')} | {meta.get('detail')}",
                file=sys.stderr,
            )
            continue
        event_payload = payload[0] if isinstance(payload, list) else None
        if not isinstance(event_payload, dict):
            continue
        extra_bookmakers = event_payload.get("bookmakers")
        if isinstance(extra_bookmakers, list) and extra_bookmakers:
            existing = event.get("bookmakers")
            if isinstance(existing, list):
                existing.extend(extra_bookmakers)
            else:
                event["bookmakers"] = list(extra_bookmakers)
            fetched += 1

    if fetched:
        print(
            f"[SQE-V1] Yan pazar | {sport_key} | {fetched} mac icin KG+IY fiyati eklendi",
            file=sys.stderr,
        )


def _fetch_sport_sharp_odds(sport_key: str) -> tuple[dict[str, dict[str, str | float]], dict[str, Any]]:
    feed_url = _build_consensus_feed_url(sport_key)
    if feed_url is None:
        return {}, {"error_kind": "auth", "detail": "feed url olusturulamadi"}

    payload, meta = _fetch_odds_api_json(feed_url)
    if payload is None:
        _emit_fetch_diag(
            sport_key=sport_key,
            error_kind=str(meta.get("error_kind", "unknown")),
            detail=str(meta.get("detail", "lig feed erisilemedi")),
            quota_remaining=meta.get("quota_remaining"),
            quota_used=meta.get("quota_used"),
        )
        return {}, meta

    mac_sayisi = len(payload)
    if mac_sayisi == 0:
        _emit_fetch_diag(
            sport_key=sport_key,
            error_kind="empty",
            detail="lig takviminde mac yok",
            quota_remaining=meta.get("quota_remaining"),
            quota_used=meta.get("quota_used"),
            mac_sayisi=0,
        )
    else:
        _emit_fetch_diag(
            sport_key=sport_key,
            error_kind="ok",
            detail="",
            quota_remaining=meta.get("quota_remaining"),
            quota_used=meta.get("quota_used"),
            mac_sayisi=mac_sayisi,
        )

    # Golge kaydi (Asama 2 - Adim 1): YENI olasilik-uzayi ortalamasini ESKININ
    # yaninda deftere yazar. Canli karari ETKILEMEZ; hata olsa bile tarama surer.
    try:
        from core.shadow_consensus import record_consensus_shadow

        record_consensus_shadow(payload, sport_key)
    except Exception as exc:  # golge asla canli taramayi bozmamali
        print(f"[SQE-V1] golge kaydi atlandi | {sport_key} | {exc}", file=sys.stderr)

    # Yan pazar zenginlestirme (Asama B): Kart 1 acikken KG + IY fiyatlari
    # mac-basina cekilip ayni olaylara eklenir; asagidaki ayristirici ek
    # degisiklik gerektirmez. Hata ana taramayi ASLA bozmaz.
    try:
        _enrich_events_with_side_markets(payload, sport_key)
    except Exception as exc:
        print(f"[SQE-V1] yan pazar zenginlestirme atlandi | {sport_key} | {exc}", file=sys.stderr)

    return _parse_consensus_feed(payload, sport_key=sport_key), meta


def get_sharp_live_odds() -> dict:
    sport_keys = _select_rotated_sport_keys()
    if not sport_keys:
        # Kesif "bugun mac yok" dediyse bu normal bir atlama (0 kredi), config
        # hatasi degildir; teshisi ezme.
        if _LAST_SHARP_SCAN_DIAG.get("kind") != "empty":
            _emit_operator_diag("sharp lig listesi bos")
            _set_sharp_scan_diag("config", "sharp lig listesi bos")
        return {}

    merged: dict[str, dict[str, str | float]] = {}
    successful_leagues: list[str] = []
    empty_leagues: list[str] = []
    failed_leagues: list[str] = []
    last_quota_remaining: str | None = None
    last_quota_used: str | None = None

    for sport_key in sport_keys:
        parsed, meta = _fetch_sport_sharp_odds(sport_key)
        last_quota_remaining = meta.get("quota_remaining") or last_quota_remaining
        last_quota_used = meta.get("quota_used") or last_quota_used
        error_kind = str(meta.get("error_kind", "unknown"))

        # Pencere isaretlemesi (Adim 3): cevap faturalandiysa (ok/empty) bu
        # ligin acik penceresi kullanildi sayilir; ag/anahtar hatasinda
        # isaretleme YAPILMAZ ki sonraki dongude yeniden denensin.
        if error_kind in {"ok", "empty"}:
            try:
                from core.scan_scheduler import mark_league_fetched

                mark_league_fetched(sport_key)
            except Exception as exc:
                print(f"[SQE-V1] pencere isareti atlandi | {exc}", file=sys.stderr)

        if parsed:
            successful_leagues.append(sport_key)
            merged.update(parsed)
            continue
        if error_kind == "empty":
            empty_leagues.append(sport_key)
            continue
        failed_leagues.append(sport_key)

    if merged:
        observed_at = time.time()
        for entry in merged.values():
            if isinstance(entry, dict):
                entry["observed_at"] = observed_at
        _set_sharp_scan_diag(
            "ok",
            f"sharp kayit={len(merged)}",
            quota_remaining=last_quota_remaining,
            quota_used=last_quota_used,
        )
        print(
            f"[SQE-V1] Sharp tarama | ligler={','.join(sport_keys)} | "
            f"basarili={','.join(successful_leagues) or 'yok'} | kayit={len(merged)}",
            file=sys.stderr,
        )
        return merged

    if failed_leagues:
        _set_sharp_scan_diag(
            failed_leagues[0] if len(failed_leagues) == len(sport_keys) else "partial",
            f"sharp feed hatasi | basarisiz={','.join(failed_leagues)}",
            quota_remaining=last_quota_remaining,
            quota_used=last_quota_used,
        )
        _emit_operator_diag(
            f"kuresel konsensus feed erisilemedi | basarisiz={','.join(failed_leagues)}"
        )
        return {}

    if empty_leagues and len(empty_leagues) == len(sport_keys):
        _set_sharp_scan_diag(
            "empty",
            f"ligler bos | {','.join(empty_leagues)}",
            quota_remaining=last_quota_remaining,
            quota_used=last_quota_used,
        )
        print(
            f"[SQE-V1] Teşhis: Sharp tarama | ligler={','.join(sport_keys)} | "
            f"mac_sayisi=0 | neden=sezon veya lig takvimi bos | "
            f"kalan={last_quota_remaining or '?'}",
            file=sys.stderr,
        )
        return {}

    _set_sharp_scan_diag(
        "parse",
        "yanit alindi ancak gecerli keskin oran verisi ayiklanamadi",
        quota_remaining=last_quota_remaining,
        quota_used=last_quota_used,
    )
    _emit_operator_diag("yanit alindi ancak gecerli keskin oran verisi ayiklanamadi")
    return {}
