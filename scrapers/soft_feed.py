from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from config.settings import SOFT_MARKET_LAG_TIMEOUT
from core.market_catalog import totals_market_key

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

__all__ = ("get_yasal_live_odds", "verify_playwright_runtime")

_OPERATOR_DIAG = "Donanim Erisilemiyor: Yerel Bulten Verisi Alinamadi"
_NESINE_LINE_DOWN_DIAG = "Donanım Erişilemiyor: Nesine Canlı Bülten Hattı Kesildi"
_WAF_DIAG = "WAF Blokajı: HTML Yanıt Döndü"
_PLAYWRIGHT_INSTALL_HINT = (
    "[SQE-V1] Playwright tarayici eksik | Kurulum: playwright install chrome"
)
_MAX_RESPONSE_BYTES = 5_000_000
_PREMATCH_HARD_LIMIT_BYTES = 64_000_000
_PREMATCH_CHUNK_BYTES = 1_000_000
_PLAYWRIGHT_WARMUP_MS = 2_000
_PLAYWRIGHT_NAV_TIMEOUT_MS = max(SOFT_MARKET_LAG_TIMEOUT * 1000 * 3, 45_000)
_PLAYWRIGHT_API_TIMEOUT_MS = max(SOFT_MARKET_LAG_TIMEOUT * 1000 * 2, 20_000)

_BROWSER_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_BULLETIN_SOURCES = (
    {
        "name": "nesine",
        "portal": "https://www.nesine.com/canli-bahis",
        "api": "https://bulten.nesine.com/api/bulten/getlivebultenv3?eventVersion=0&oddVersion=0",
        "transport": "context_request",
        "referer": "https://www.nesine.com/canli-bahis",
        "origin": "https://www.nesine.com",
    },
    {
        "name": "misli",
        "portal": "https://www.misli.com/iddaa/canli-bahis/futbol",
        "api": "https://apivx.misli.com/api/web/v1/sportsbook/event/0?sportType=ALL&betType=LIVE",
        "transport": "context_request",
        "referer": "https://www.misli.com/iddaa/canli-bahis/futbol",
        "origin": "https://www.misli.com",
    },
)

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
"""

_VIRTUAL_MATCH_BLACKLIST = (
    "e-futbol",
    "efutbol",
    "esoccer",
    "e soccer",
    "esports",
    "e-sports",
    "cyber",
    "srl",
    "simulated",
)
_VIRTUAL_MATCH_LEAGUE_KEYS = (
    "league_name",
    "lig",
    "LN",
    "LGN",
    "LA",
    "LNA",
    "CN",
    "competition",
    "tournament",
    "sn",
    "cn",
    "cp",
)

_PREMATCH_FULL_URL = "https://cdnbulten.nesine.com/api/bulten/getprebultenfull"
_PREMATCH_SKIP_FRAGMENTS = (
    "grup bahisleri",
    "turnuva -",
    "d. k.",
    "uzt. dahil",
)


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _emit_nesine_line_down(detail: str) -> None:
    print(f"{_NESINE_LINE_DOWN_DIAG} | {detail}", file=sys.stderr)


def _emit_scan_info(message: str) -> None:
    print(f"[SQE-V1] Nesine Hattı: {message}")


def _looks_like_html(raw: str) -> bool:
    sample = raw.lstrip()[:512].lower()
    return (
        sample.startswith("<!doctype")
        or sample.startswith("<html")
        or "<html" in sample
        or "cloudflare" in sample
    )


def _is_virtual_match(*parts: object) -> bool:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip():
            texts.append(part.strip())
            continue
        if not isinstance(part, dict):
            continue
        for key in _VIRTUAL_MATCH_LEAGUE_KEYS:
            value = part.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())

    for text in texts:
        normalized = text.casefold()
        for keyword in _VIRTUAL_MATCH_BLACKLIST:
            if keyword.casefold() in normalized:
                return True
    return False


def _is_prematch_special_match(home: str, away: str, match_name: str) -> bool:
    if "/" in home or "/" in away:
        return True
    blob = f"{home} {away} {match_name}".casefold()
    return any(fragment in blob for fragment in _PREMATCH_SKIP_FRAGMENTS)


def _read_stream_fully(stream: Any, hard_limit_bytes: int) -> bytes | None:
    """Yaniti sonuna kadar okur; sinir asilirsa kirpmak yerine None doner."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_PREMATCH_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > hard_limit_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_nesine_prematch_json() -> dict[str, Any] | None:
    request = urllib.request.Request(
        _PREMATCH_FULL_URL,
        method="GET",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nesine.com/iddaa/futbol",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SOFT_MARKET_LAG_TIMEOUT) as response:
            payload_bytes = _read_stream_fully(response, _PREMATCH_HARD_LIMIT_BYTES)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        _emit_operator_diag(f"nesine prematch | {exc}")
        return None

    if payload_bytes is None:
        _emit_operator_diag("nesine prematch | yanit guvenlik siniri asildi")
        return None

    raw = payload_bytes.decode("utf-8", errors="replace")

    if _looks_like_html(raw):
        _emit_operator_diag("nesine prematch | HTML yanit")
        return None

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        _emit_operator_diag("nesine prematch | JSON parse hatasi")
        return None

    if not isinstance(payload, dict):
        _emit_operator_diag("nesine prematch | beklenmeyen payload")
        return None
    return payload


def verify_playwright_runtime() -> bool:
    if async_playwright is None:
        print(_PLAYWRIGHT_INSTALL_HINT, file=sys.stderr)
        print(
            "[SQE-V1] Python bagimliligi eksik | Kurulum: pip install playwright",
            file=sys.stderr,
        )
        return False

    if shutil.which("google-chrome") or shutil.which("chrome") or _macos_chrome_installed():
        return True

    print(_PLAYWRIGHT_INSTALL_HINT, file=sys.stderr)
    return False


def _macos_chrome_installed() -> bool:
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    try:
        from pathlib import Path

        return Path(chrome_path).is_file()
    except OSError:
        return False


async def _launch_browser(playwright: Any) -> Any:
    errors: list[str] = []
    for label, launch_kwargs in (
        ("chrome", {"channel": "chrome"}),
        ("chromium", {}),
    ):
        try:
            return await playwright.chromium.launch(
                headless=True,
                args=list(_BROWSER_LAUNCH_ARGS),
                **launch_kwargs,
            )
        except Exception as exc:
            errors.append(f"{label}={exc}")

    print(_PLAYWRIGHT_INSTALL_HINT, file=sys.stderr)
    joined = " | ".join(errors)
    raise RuntimeError(joined or "playwright browser launch failed")


async def _new_stealth_context(browser: Any) -> Any:
    context = await browser.new_context(
        locale="tr-TR",
        user_agent=_USER_AGENT,
        viewport={"width": 1366, "height": 768},
        extra_http_headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    await context.add_init_script(_STEALTH_INIT_SCRIPT)
    return context


def _api_request_headers(source: dict[str, str]) -> dict[str, str]:
    referer = source.get("referer") or source["portal"]
    origin = source.get("origin") or _origin_from_url(referer)
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer,
        "Origin": origin,
        "X-Requested-With": "XMLHttpRequest",
    }


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url


async def _fetch_via_context_request(context: Any, source: dict[str, str]) -> str | None:
    api_url = source["api"]
    response = await context.request.get(
        api_url,
        headers=_api_request_headers(source),
        timeout=_PLAYWRIGHT_API_TIMEOUT_MS,
    )
    status_code = response.status
    raw = await response.text()
    if status_code < 200 or status_code >= 300:
        _emit_operator_diag(f"{source['name']} | {api_url} | unexpected HTTP status {status_code}")
        return None
    if len(raw.encode("utf-8", errors="replace")) > _MAX_RESPONSE_BYTES:
        _emit_operator_diag(f"{source['name']} | {api_url} | yanit guvenlik siniri asildi")
        return None
    return raw


async def _fetch_via_browser_fetch(context: Any, source: dict[str, str]) -> str | None:
    portal = source["portal"]
    api_url = source["api"]
    page = await context.new_page()
    try:
        await page.goto(portal, wait_until="domcontentloaded", timeout=_PLAYWRIGHT_NAV_TIMEOUT_MS)
        await page.wait_for_timeout(_PLAYWRIGHT_WARMUP_MS)
        result = await page.evaluate(
            """async (apiUrl) => {
                const response = await fetch(apiUrl, {
                    credentials: "include",
                    headers: {
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                });
                return { status: response.status, body: await response.text() };
            }""",
            api_url,
        )
        if not isinstance(result, dict):
            _emit_operator_diag(f"{source['name']} | {api_url} | invalid browser response")
            return None
        status_code = result.get("status")
        raw = result.get("body")
        if not isinstance(status_code, int) or not isinstance(raw, str):
            _emit_operator_diag(f"{source['name']} | {api_url} | malformed browser response")
            return None
        if status_code < 200 or status_code >= 300:
            _emit_operator_diag(f"{source['name']} | {api_url} | unexpected HTTP status {status_code}")
            return None
        if len(raw.encode("utf-8", errors="replace")) > _MAX_RESPONSE_BYTES:
            _emit_operator_diag(f"{source['name']} | {api_url} | yanit guvenlik siniri asildi")
            return None
        return raw
    finally:
        await page.close()


async def _fetch_bulletin_raw(context: Any, source: dict[str, str]) -> str | None:
    source_name = source.get("name", "yasal")
    api_url = source["api"]

    try:
        raw = await _fetch_via_context_request(context, source)
        if raw is not None and not _looks_like_html(raw):
            return raw

        if raw is not None and _looks_like_html(raw):
            _emit_operator_diag(f"{source_name} | {api_url} | direct API {_WAF_DIAG}, portal fallback deneniyor")

        return await _fetch_via_browser_fetch(context, source)
    except Exception as exc:
        if source_name == "nesine":
            _emit_nesine_line_down(str(exc))
        else:
            _emit_operator_diag(f"{source_name} | {api_url} | {exc}")
        return None


async def _fetch_single_source(source: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | list[Any] | None]:
    if async_playwright is None:
        if source.get("name") == "nesine":
            _emit_nesine_line_down("playwright modulu kurulu degil")
        else:
            _emit_operator_diag("playwright modulu kurulu degil")
        return source, None

    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright)
            context = await _new_stealth_context(browser)
            try:
                raw = await _fetch_bulletin_raw(context, source)
                if raw is None:
                    if source.get("name") == "nesine":
                        _emit_nesine_line_down("canli bulten yaniti alinamadi")
                    return source, None

                if _looks_like_html(raw):
                    detail = f"{source['api']} | {_WAF_DIAG}"
                    if source.get("name") == "nesine":
                        _emit_nesine_line_down(detail)
                    else:
                        _emit_operator_diag(detail)
                    return source, None

                try:
                    payload: Any = json.loads(raw)
                except json.JSONDecodeError:
                    detail = f"{source['api']} | non-JSON response body"
                    if source.get("name") == "nesine":
                        _emit_nesine_line_down(detail)
                    else:
                        _emit_operator_diag(detail)
                    return source, None

                if not isinstance(payload, (dict, list)):
                    _emit_operator_diag(f"{source['api']} | unsupported JSON root type")
                    return source, None

                return source, payload
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:
        if source.get("name") == "nesine":
            _emit_nesine_line_down(f"playwright oturumu | {exc}")
        else:
            _emit_operator_diag(f"playwright oturumu | {source.get('name', 'yasal')} | {exc}")
        return source, None


async def _fetch_data_async() -> list[tuple[dict[str, str], dict[str, Any] | list[Any] | None]]:
    tasks = [_fetch_single_source(source) for source in _BULLETIN_SOURCES]
    return list(await asyncio.gather(*tasks))


def _is_valid_soft_odds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    odds = float(value)
    if odds <= 1.0:
        return None
    return odds


# Nesine mac toplam gol alt/ust pazari: MST=101, cizgi SOV alaninda.
# MTID 11/12/13 = 1.5/2.5/3.5 cizgileri. N=1 Alt, N=2 Ust — canli bultende
# uc cizgi boyunca fiyat monotonlugu ile dogrulandi (2026-07-03).
_PREMATCH_TOTALS_MST = 101
_PREMATCH_TOTALS_MTIDS = frozenset({11, 12, 13})
_TOTALS_SIDE_BY_OUTCOME_NO = {1: "ALT", 2: "UST"}

# Yan pazarlar (Asama B): kimlikler cift kaynakla teyitli —
# docs/nesine-yan-pazar-kesif-20260712.md (2026-07-12, Fransa-Ispanya finali).
# KG: MST=89/MTID=38, N=1 VAR / N=2 YOK (btts Yes/No ile sayisal eslesti).
# IY sonucu: MST=88/MTID=7, N=1/2/3 = IY1/IYX/IY2 (h2h_h1 ile eslesti).
_PREMATCH_BTTS_MST = 89
_PREMATCH_BTTS_MTID = 38
_BTTS_LABEL_BY_OUTCOME_NO = {1: "KG VAR", 2: "KG YOK"}
_PREMATCH_FIRST_HALF_MST = 88
_PREMATCH_FIRST_HALF_MTID = 7
_FIRST_HALF_LABEL_BY_OUTCOME_NO = {1: "IY1", 2: "IYX", 3: "IY2"}


def _nesine_kickoff_iso(event: dict[str, Any]) -> str:
    """Nesine olayinin baslama zamanini ISO-8601 UTC metnine cevirir.

    Bulten `ESD` alaninda epoch milisaniye tasiyor. Bu deger, keskin kaynakla
    isim benzerligi sinirda kalan eslesmeleri baslama saatiyle dogrulamak icin
    kullanilir (bkz. live_feed_gateway).
    """
    raw = event.get("ESD")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return ""
    epoch_seconds = float(raw) / 1000.0
    if epoch_seconds <= 0.0:
        return ""
    try:
        kickoff = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return kickoff.isoformat().replace("+00:00", "Z")


def _ingest_prematch_totals(
    normalized: dict[str, dict[str, str | float]],
    market_item: dict[str, Any],
    *,
    event_id: object,
    match_name: str,
    commence_time: str = "",
) -> None:
    outcomes = market_item.get("OCA")
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        return

    market_id = market_item.get("ID") or market_item.get("NO") or "au"
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        outcome_no = outcome.get("N")
        if not isinstance(outcome_no, int):
            continue
        side = _TOTALS_SIDE_BY_OUTCOME_NO.get(outcome_no)
        parsed_odds = _is_valid_soft_odds(outcome.get("O"))
        if side is None or parsed_odds is None:
            continue
        label = totals_market_key(side, market_item.get("SOV"))
        if label is None:
            continue
        mac_id = f"nesine:pre:{event_id}:{market_id}:{label.replace(' ', '_')}"
        _ingest_record(
            normalized,
            mac_id,
            match_name,
            label,
            parsed_odds,
            soft_source="nesine",
            feed_phase="prematch",
            commence_time=commence_time,
        )


def _ingest_prematch_fixed_market(
    normalized: dict[str, dict[str, str | float]],
    market_item: dict[str, Any],
    *,
    event_id: object,
    match_name: str,
    label_by_outcome_no: dict[int, str],
    market_tag: str,
    commence_time: str = "",
) -> None:
    """Sabit secenekli yan pazari (KG, IY sonucu) ortak kayda cevirir."""
    outcomes = market_item.get("OCA")
    if not isinstance(outcomes, list) or len(outcomes) != len(label_by_outcome_no):
        return

    market_id = market_item.get("ID") or market_item.get("NO") or market_tag
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        outcome_no = outcome.get("N")
        if not isinstance(outcome_no, int):
            continue
        label = label_by_outcome_no.get(outcome_no)
        parsed_odds = _is_valid_soft_odds(outcome.get("O"))
        if label is None or parsed_odds is None:
            continue
        mac_id = f"nesine:pre:{event_id}:{market_id}:{label.replace(' ', '_')}"
        _ingest_record(
            normalized,
            mac_id,
            match_name,
            label,
            parsed_odds,
            soft_source="nesine",
            feed_phase="prematch",
            commence_time=commence_time,
        )


def _ingest_record(
    target: dict[str, dict[str, str | float]],
    mac_id: str,
    match_name: str,
    market: str,
    soft_odds: float,
    *,
    soft_source: str = "",
    feed_phase: str = "",
    commence_time: str = "",
) -> None:
    record: dict[str, str | float] = {
        "match_name": match_name.strip(),
        "market": market.strip(),
        "soft_odds": soft_odds,
        "observed_at": time.time(),
    }
    if soft_source:
        record["soft_source"] = soft_source
    if feed_phase:
        record["feed_phase"] = feed_phase
    if commence_time:
        record["commence_time"] = commence_time
    target[mac_id] = record


def _extract_nesine_live_payload(payload: dict[str, Any]) -> dict[str, dict[str, str | float]]:
    normalized: dict[str, dict[str, str | float]] = {}
    sg = payload.get("sg")
    if not isinstance(sg, dict):
        return normalized

    events = sg.get("EA")
    if not isinstance(events, list):
        return normalized

    market_map = {1: "MS1", 2: "X", 3: "MS2"}

    for event in events:
        if not isinstance(event, dict):
            continue
        home = event.get("HN")
        away = event.get("AN")
        event_id = event.get("C") or event.get("EV")
        if not isinstance(home, str) or not isinstance(away, str) or event_id is None:
            continue

        match_name = f"{home.strip()} - {away.strip()}"
        if _is_virtual_match(home, away, match_name, event):
            continue

        markets = event.get("MA")
        if not isinstance(markets, list):
            continue

        for market_item in markets:
            if not isinstance(market_item, dict):
                continue
            if market_item.get("MST") != 12 or market_item.get("MTID") != 60:
                continue

            outcomes = market_item.get("OCA")
            if not isinstance(outcomes, list) or len(outcomes) != 3:
                continue

            market_id = market_item.get("ID") or market_item.get("NO") or "ms1"
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                outcome_no = outcome.get("N")
                if not isinstance(outcome_no, int):
                    continue
                label = market_map.get(outcome_no)
                parsed_odds = _is_valid_soft_odds(outcome.get("O"))
                if label is None or parsed_odds is None:
                    continue
                mac_id = f"nesine:{event_id}:{market_id}:{label}"
                _ingest_record(
                    normalized,
                    mac_id,
                    match_name,
                    label,
                    parsed_odds,
                    soft_source="nesine",
                    feed_phase="live",
                )

    return normalized


def _extract_nesine_prematch_payload(payload: dict[str, Any]) -> dict[str, dict[str, str | float]]:
    normalized: dict[str, dict[str, str | float]] = {}
    sg = payload.get("sg")
    if not isinstance(sg, dict):
        return normalized

    events = sg.get("EA")
    if not isinstance(events, list):
        return normalized

    market_map = {1: "MS1", 2: "X", 3: "MS2"}

    for event in events:
        if not isinstance(event, dict):
            continue
        home = event.get("HN")
        away = event.get("AN")
        event_id = event.get("C") or event.get("EV")
        if not isinstance(home, str) or not isinstance(away, str) or event_id is None:
            continue

        match_name = f"{home.strip()} - {away.strip()}"
        if _is_prematch_special_match(home, away, match_name):
            continue
        if _is_virtual_match(home, away, match_name, event):
            continue

        markets = event.get("MA")
        if not isinstance(markets, list):
            continue

        commence_time = _nesine_kickoff_iso(event)

        for market_item in markets:
            if not isinstance(market_item, dict):
                continue
            if (
                market_item.get("MST") == _PREMATCH_TOTALS_MST
                and market_item.get("MTID") in _PREMATCH_TOTALS_MTIDS
            ):
                _ingest_prematch_totals(
                    normalized,
                    market_item,
                    event_id=event_id,
                    match_name=match_name,
                    commence_time=commence_time,
                )
                continue
            if (
                market_item.get("MST") == _PREMATCH_BTTS_MST
                and market_item.get("MTID") == _PREMATCH_BTTS_MTID
            ):
                _ingest_prematch_fixed_market(
                    normalized,
                    market_item,
                    event_id=event_id,
                    match_name=match_name,
                    label_by_outcome_no=_BTTS_LABEL_BY_OUTCOME_NO,
                    market_tag="kg",
                    commence_time=commence_time,
                )
                continue
            if (
                market_item.get("MST") == _PREMATCH_FIRST_HALF_MST
                and market_item.get("MTID") == _PREMATCH_FIRST_HALF_MTID
            ):
                _ingest_prematch_fixed_market(
                    normalized,
                    market_item,
                    event_id=event_id,
                    match_name=match_name,
                    label_by_outcome_no=_FIRST_HALF_LABEL_BY_OUTCOME_NO,
                    market_tag="iy",
                    commence_time=commence_time,
                )
                continue
            if market_item.get("MST") != 1 or market_item.get("MTID") != 1:
                continue

            outcomes = market_item.get("OCA")
            if not isinstance(outcomes, list) or len(outcomes) != 3:
                continue

            market_id = market_item.get("ID") or market_item.get("NO") or "ms1"
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                outcome_no = outcome.get("N")
                if not isinstance(outcome_no, int):
                    continue
                label = market_map.get(outcome_no)
                parsed_odds = _is_valid_soft_odds(outcome.get("O"))
                if label is None or parsed_odds is None:
                    continue
                mac_id = f"nesine:pre:{event_id}:{market_id}:{label}"
                _ingest_record(
                    normalized,
                    mac_id,
                    match_name,
                    label,
                    parsed_odds,
                    soft_source="nesine",
                    feed_phase="prematch",
                    commence_time=commence_time,
                )

    return normalized


def _merge_nesine_prematch(
    aggregated: dict[str, dict[str, str | float]],
) -> int:
    payload = _fetch_nesine_prematch_json()
    if payload is None:
        return 0

    prematch = _extract_nesine_prematch_payload(payload)
    if not prematch:
        return 0

    live_keys = {
        (str(entry.get("match_name", "")), str(entry.get("market", "")).upper())
        for entry in aggregated.values()
        if isinstance(entry, dict)
    }
    added = 0
    for mac_id, entry in prematch.items():
        if not isinstance(entry, dict):
            continue
        pair = (str(entry.get("match_name", "")), str(entry.get("market", "")).upper())
        if pair in live_keys:
            continue
        aggregated[mac_id] = entry
        added += 1
    return added


def _extract_misli_live_payload(payload: dict[str, Any]) -> dict[str, dict[str, str | float]]:
    normalized: dict[str, dict[str, str | float]] = {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return normalized

    events = data.get("e")
    if not isinstance(events, list):
        return normalized

    market_map = {"1": "MS1", "0": "X", "2": "MS2"}

    for event in events:
        if not isinstance(event, dict) or event.get("st") != "SOCCER":
            continue

        match_name = event.get("n")
        event_id = event.get("i")
        if not isinstance(match_name, str) or event_id is None:
            continue

        if _is_virtual_match(match_name, event):
            continue

        markets = event.get("m")
        if not isinstance(markets, list):
            continue

        for market_item in markets:
            if not isinstance(market_item, dict) or market_item.get("sbt") != 1:
                continue

            outcomes = market_item.get("o")
            if not isinstance(outcomes, list):
                continue

            market_id = market_item.get("i") or "ms1"
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                label = market_map.get(str(outcome.get("n")))
                parsed_odds = _is_valid_soft_odds(outcome.get("od"))
                if label is None or parsed_odds is None:
                    continue
                mac_id = f"misli:{event_id}:{market_id}:{label}"
                _ingest_record(
                    normalized,
                    mac_id,
                    match_name,
                    label,
                    parsed_odds,
                    soft_source="misli",
                )

    return normalized


def _coerce_match_entry(mac_id: str, entry: object) -> dict[str, str | float] | None:
    if not isinstance(entry, dict):
        return None

    match_name = entry.get("match_name") or entry.get("name") or entry.get("eventName")
    if match_name is None:
        home = entry.get("homeTeam") or entry.get("home")
        away = entry.get("awayTeam") or entry.get("away")
        if home and away:
            match_name = f"{home} - {away}"

    market = entry.get("market") or entry.get("marketType") or entry.get("selection")
    soft_odds = entry.get("soft_odds")
    if soft_odds is None:
        soft_odds = entry.get("odds") or entry.get("price") or entry.get("oran")

    if not isinstance(match_name, str) or not match_name.strip():
        return None
    if _is_virtual_match(match_name, entry):
        return None
    if not isinstance(market, str) or not market.strip():
        return None

    parsed_odds = _is_valid_soft_odds(soft_odds)
    if parsed_odds is None:
        return None

    record: dict[str, str | float] = {
        "match_name": match_name.strip(),
        "market": market.strip(),
        "soft_odds": parsed_odds,
    }
    soft_source = entry.get("soft_source")
    if isinstance(soft_source, str) and soft_source.strip():
        record["soft_source"] = soft_source.strip()
    return record


def _ingest_legacy_record(
    target: dict[str, dict[str, str | float]],
    mac_id: str,
    entry: object,
) -> None:
    normalized = _coerce_match_entry(mac_id, entry)
    if normalized is not None:
        target[mac_id] = normalized


def _extract_from_mapping(
    target: dict[str, dict[str, str | float]],
    payload: dict[str, Any],
) -> None:
    for key, value in payload.items():
        if isinstance(value, dict) and {"match_name", "market", "soft_odds"} <= value.keys():
            _ingest_legacy_record(target, str(key), value)
            continue
        if isinstance(value, dict):
            _ingest_legacy_record(target, str(key), value)


def _extract_from_event_list(
    target: dict[str, dict[str, str | float]],
    events: list[Any],
) -> None:
    for item in events:
        if not isinstance(item, dict):
            continue

        mac_id = (
            item.get("mac_id")
            or item.get("id")
            or item.get("eventId")
            or item.get("event_id")
            or item.get("match_id")
        )
        if mac_id is None:
            continue

        markets = item.get("markets")
        if isinstance(markets, list) and markets:
            for market_item in markets:
                if not isinstance(market_item, dict):
                    continue
                merged = dict(item)
                merged.update(market_item)
                _ingest_legacy_record(target, f"{mac_id}:{merged.get('market', 'MS1')}", merged)
            continue

        _ingest_legacy_record(target, str(mac_id), item)


def _normalize_bulletin_payload(
    payload: dict[str, Any] | list[Any],
    *,
    source_name: str = "",
) -> dict[str, dict[str, str | float]]:
    if isinstance(payload, dict):
        if "sg" in payload:
            parsed = _extract_nesine_live_payload(payload)
            if parsed:
                return parsed
        if "data" in payload and payload.get("success") is True:
            parsed = _extract_misli_live_payload(payload)
            if parsed:
                return parsed

    normalized: dict[str, dict[str, str | float]] = {}

    if isinstance(payload, list):
        _extract_from_event_list(normalized, payload)
        return normalized

    if isinstance(payload, dict) and all(isinstance(value, dict) for value in payload.values()):
        _extract_from_mapping(normalized, payload)
        if normalized:
            return normalized

    if isinstance(payload, dict):
        for container_key in ("data", "events", "matches", "bulten", "items", "results"):
            container = payload.get(container_key)
            if isinstance(container, list):
                _extract_from_event_list(normalized, container)
            elif isinstance(container, dict):
                _extract_from_mapping(normalized, container)

    if source_name and normalized:
        for entry in normalized.values():
            entry.setdefault("soft_source", source_name)

    return normalized


def get_yasal_live_odds() -> dict:
    verify_playwright_runtime()

    aggregated: dict[str, dict[str, str | float]] = {}
    source_status: dict[str, bool] = {source["name"]: False for source in _BULLETIN_SOURCES}
    source_counts: dict[str, int] = {source["name"]: 0 for source in _BULLETIN_SOURCES}

    for source, payload in asyncio.run(_fetch_data_async()):
        source_name = str(source.get("name", "yasal"))
        if payload is None:
            continue

        source_status[source_name] = True
        parsed = _normalize_bulletin_payload(payload, source_name=source_name)
        if parsed:
            aggregated.update(parsed)
            source_counts[source_name] = len(parsed)
            if source_name == "nesine":
                _emit_scan_info(f"{len(parsed)} soft kayit yuklendi")
            continue

        if source_name == "nesine":
            _emit_nesine_line_down("yanit alindi ancak gecerli mac verisi ayiklanamadi")
        else:
            _emit_operator_diag(f"{source_name} | yanit alindi ancak gecerli mac verisi ayiklanamadi")

    prematch_added = _merge_nesine_prematch(aggregated)
    if prematch_added:
        _emit_scan_info(f"{prematch_added} prematch soft kayit eklendi")
        source_status["nesine"] = True

    if aggregated:
        return aggregated

    if not source_status.get("nesine", False):
        if not any(source_status.values()):
            _emit_nesine_line_down("tum yasal bulten uc noktalari erisilemedi")
        elif source_counts.get("nesine", 0) == 0:
            _emit_nesine_line_down("nesine hattindan mac kaydi uretilemedi")
    elif not any(source_status.values()):
        _emit_operator_diag("tum yasal bulten uc noktalari erisilemedi")
    else:
        _emit_operator_diag("yanit alindi ancak gecerli mac verisi ayiklanamadi")

    return {}
