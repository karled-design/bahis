from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import ODDS_API_KEY, SOFT_MARKET_LAG_TIMEOUT
from core.scan_league_settings import SHARP_LEAGUE_CATALOG, get_enabled_sharp_sport_keys

__all__ = (
    "annotate_scan_leagues_payload",
    "filter_scannable_sport_keys",
    "get_league_kickoffs",
    "has_kickoff_data",
    "refresh_league_discovery",
)

# Ucretsiz kesif katmani (kredi diyeti - Adim 2).
# Odds API'nin 0 kredilik iki ucunu kullanir:
#   /v4/sports/            -> sezonu ACIK sporlarin listesi (aktiflik isareti)
#   /v4/sports/{k}/events/ -> ligdeki yaklasan maclarin takvimi (oran YOK)
# Amac: sezonu kapali veya yakin pencerede maci olmayan lige o gun HIC oran
# sorgusu (4 kredi) gitmemesi. Kesif verisi yoksa/bayatsa filtre ACIK KALIR
# (fail-open): kesif asla taramayi engellememeli, sadece bosa sorguyu keser.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _PROJECT_ROOT / "database" / "league_discovery.json"
_LOCK = threading.Lock()

_SPORTS_URL = "https://api.the-odds-api.com/v4/sports/"
_EVENTS_URL_TEMPLATE = "https://api.the-odds-api.com/v4/sports/{sport_key}/events/"

_REFRESH_INTERVAL_SECONDS = 6 * 3600  # takvim gunde ~4 kez tazelenir (ucretsiz)
_FAILURE_COOLDOWN_SECONDS = 30 * 60  # API'ye ulasamayinca 30 dk yeniden deneme yok
_HARD_STALE_SECONDS = 24 * 3600  # bundan eski kesif verisi = guvenilmez, filtre acik kalir
_MATCH_WINDOW_HOURS = 36  # bu pencerede maci olmayan lige oran sorgusu gitmez
_MATCH_LOOKBACK_HOURS = 3  # yeni baslamis (canli) maci da "mac var" say
_KICKOFF_KEEP_HOURS = 72  # Adim 3 icin takvimde tutulan ufuk
_MAX_KICKOFFS_PER_LEAGUE = 30
_MAX_EVENTS_LEAGUES = 12  # tek tazelemede en fazla bu kadar lige takvim sorusu

_CATALOG_KEYS = frozenset(entry["sport_key"] for entry in SHARP_LEAGUE_CATALOG)

_MEMORY_CACHE: dict[str, Any] | None = None
_LAST_FAILURE_MONO: float | None = None

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _http_get_json(url: str) -> Any | None:
    """Kucuk, sessiz JSON GET. Hata durumunda None (kesif fail-open calisir)."""
    request = urllib.request.Request(url, method="GET", headers=_BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=SOFT_MARKET_LAG_TIMEOUT) as response:
            headers = getattr(response, "headers", None)
            if headers is not None:
                # Kredi defterine "kesif" olarak isle: canli kanit, bu uclarin
                # 0 kredi yaktigini panel/defterden gorecegiz.
                try:
                    from core.api_credit_ledger import record_odds_api_usage

                    record_odds_api_usage(
                        last_cost=headers.get("x-requests-last"),
                        remaining=headers.get("x-requests-remaining"),
                        used=headers.get("x-requests-used"),
                        endpoint="kesif",
                    )
                except Exception:
                    pass
            raw = response.read(5_000_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[SQE-V1] Kesif istegi basarisiz | {url.split('?')[0]} | {exc}", file=sys.stderr)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[SQE-V1] Kesif cevabi JSON degil | {url.split('?')[0]}", file=sys.stderr)
        return None


def _parse_kickoff(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _default_cache() -> dict[str, Any]:
    return {"checked_at": "", "leagues": {}}


def _load_cache_from_disk() -> dict[str, Any]:
    if not _CACHE_PATH.is_file():
        return _default_cache()
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_cache()
    if not isinstance(payload, dict) or not isinstance(payload.get("leagues"), dict):
        return _default_cache()
    return payload


def _save_cache_to_disk(cache: dict[str, Any]) -> None:
    # Atomik yazim: yarim dosya okunmasin (hero/kredi defteri ile ayni desen).
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _CACHE_PATH.with_name(_CACHE_PATH.name + ".tmp")
        tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, _CACHE_PATH)
    except OSError as exc:
        print(f"[SQE-V1] Kesif onbellegi yazilamadi | {exc}", file=sys.stderr)


def _cache_age_seconds(cache: dict[str, Any]) -> float | None:
    checked_at = _parse_kickoff(cache.get("checked_at"))
    if checked_at is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - checked_at).total_seconds())


def _get_cache_locked() -> dict[str, Any]:
    global _MEMORY_CACHE
    if _MEMORY_CACHE is None:
        _MEMORY_CACHE = _load_cache_from_disk()
    return _MEMORY_CACHE


def _fetch_active_sport_keys() -> frozenset[str] | None:
    """Sezonu acik sporlar (0 kredi). None = ulasilamadi."""
    if not ODDS_API_KEY:
        return None
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY})
    payload = _http_get_json(f"{_SPORTS_URL}?{query}")
    if not isinstance(payload, list):
        return None
    keys: set[str] = set()
    for item in payload:
        if isinstance(item, dict):
            key = str(item.get("key", "")).strip()
            if key:
                keys.add(key)
    return frozenset(keys)


def _fetch_league_kickoffs(sport_key: str) -> list[str] | None:
    """Ligin yaklasan mac saatleri (0 kredi). None = ulasilamadi (bilinmiyor)."""
    if not ODDS_API_KEY:
        return None
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY})
    url = _EVENTS_URL_TEMPLATE.format(sport_key=sport_key) + f"?{query}"
    payload = _http_get_json(url)
    if not isinstance(payload, list):
        return None

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=_KICKOFF_KEEP_HOURS)
    lookback = now - timedelta(hours=_MATCH_LOOKBACK_HOURS)
    kickoffs: list[str] = []
    for event in payload:
        if not isinstance(event, dict):
            continue
        moment = _parse_kickoff(event.get("commence_time"))
        if moment is None:
            continue
        if lookback <= moment <= horizon:
            kickoffs.append(moment.isoformat().replace("+00:00", "Z"))
    kickoffs.sort()
    return kickoffs[:_MAX_KICKOFFS_PER_LEAGUE]


def refresh_league_discovery(*, force: bool = False) -> dict[str, Any]:
    """Kesif verisini gerekiyorsa tazeler; her durumda eldeki onbellegi dondurur.

    Kendi kendini frenler: taze veri varsa veya son deneme kisa sure once
    basarisiz olduysa API'ye gitmez. Hata durumunda ESKI onbellek korunur.
    """
    global _MEMORY_CACHE, _LAST_FAILURE_MONO

    with _LOCK:
        cache = _get_cache_locked()
        age = _cache_age_seconds(cache)
        if not force:
            if age is not None and age < _REFRESH_INTERVAL_SECONDS:
                return cache
            if (
                _LAST_FAILURE_MONO is not None
                and (time.monotonic() - _LAST_FAILURE_MONO) < _FAILURE_COOLDOWN_SECONDS
            ):
                return cache
        enabled_keys = [key for key in get_enabled_sharp_sport_keys() if key in _CATALOG_KEYS]

    # Ag islerini kilidin DISINDA yap: kesif surerken tarama/panel bloklanmasin.
    active_keys = _fetch_active_sport_keys()
    if active_keys is None:
        with _LOCK:
            _LAST_FAILURE_MONO = time.monotonic()
            return _get_cache_locked()

    leagues: dict[str, Any] = {}
    events_budget = _MAX_EVENTS_LEAGUES
    for sport_key in enabled_keys:
        is_active = sport_key in active_keys
        entry: dict[str, Any] = {"active": is_active, "kickoffs": []}
        if is_active and events_budget > 0:
            events_budget -= 1
            kickoffs = _fetch_league_kickoffs(sport_key)
            entry["kickoffs"] = kickoffs  # None = bilinmiyor (fail-open)
        elif is_active:
            entry["kickoffs"] = None
        leagues[sport_key] = entry

    fresh_cache = {
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "leagues": leagues,
    }

    with _LOCK:
        _MEMORY_CACHE = fresh_cache
        _LAST_FAILURE_MONO = None
        _save_cache_to_disk(fresh_cache)
        active_count = sum(1 for item in leagues.values() if item.get("active"))
        print(
            f"[SQE-V1] Kesif tazelendi (0 kredi) | lig={len(leagues)} | "
            f"sezonda={active_count}",
            file=sys.stderr,
        )
        return fresh_cache


def _has_match_in_window(kickoffs: list[str]) -> bool:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=_MATCH_WINDOW_HOURS)
    lookback = now - timedelta(hours=_MATCH_LOOKBACK_HOURS)
    for raw in kickoffs:
        moment = _parse_kickoff(raw)
        if moment is not None and lookback <= moment <= window_end:
            return True
    return False


def _league_state(cache: dict[str, Any], sport_key: str) -> str:
    """Tek ligin kesif durumu: aktif | sezon_disi | mac_yok | bilinmiyor."""
    entry = cache.get("leagues", {}).get(sport_key)
    if not isinstance(entry, dict):
        return "bilinmiyor"
    if entry.get("active") is False:
        return "sezon_disi"
    kickoffs = entry.get("kickoffs")
    if not isinstance(kickoffs, list):
        return "bilinmiyor"
    return "aktif" if _has_match_in_window(kickoffs) else "mac_yok"


def filter_scannable_sport_keys(sport_keys: list[str]) -> tuple[list[str], str]:
    """Oran sorgusu hak eden ligleri secer; gerekirse kesifi tazeler.

    Donen not, atlanan ligleri ozetler (teshis satiri icin). Kesif verisi
    yoksa/bayatsa TUM ligler aynen kalir (fail-open).
    """
    try:
        cache = refresh_league_discovery()
    except Exception as exc:  # kesif asla taramayi durdurmamali
        print(f"[SQE-V1] Kesif tazeleme hatasi | {exc}", file=sys.stderr)
        cache = _load_cache_from_disk()

    age = _cache_age_seconds(cache)
    if age is None or age > _HARD_STALE_SECONDS:
        return list(sport_keys), "kesif_verisi_yok"

    scannable: list[str] = []
    season_off: list[str] = []
    no_match: list[str] = []
    for sport_key in sport_keys:
        state = _league_state(cache, sport_key)
        if state == "sezon_disi":
            season_off.append(sport_key)
        elif state == "mac_yok":
            no_match.append(sport_key)
        else:  # aktif veya bilinmiyor -> acik birak
            scannable.append(sport_key)

    note_parts: list[str] = []
    if season_off:
        note_parts.append(f"sezon_disi={','.join(season_off)}")
    if no_match:
        note_parts.append(f"mac_yok={','.join(no_match)}")
    return scannable, " | ".join(note_parts)


def has_kickoff_data(sport_key: str) -> bool:
    """Bu lig icin TAZE mac takvimi bilgisi var mi?

    False ise pencere planlayicisi (Adim 3) kilitlenmemek icin yedek
    tempoya duser; True ise takvim guvenilirdir, pencereler ona kurulur.
    """
    with _LOCK:
        cache = _get_cache_locked()
    age = _cache_age_seconds(cache)
    if age is None or age > _HARD_STALE_SECONDS:
        return False
    entry = cache.get("leagues", {}).get(sport_key)
    return isinstance(entry, dict) and isinstance(entry.get("kickoffs"), list)


def get_league_kickoffs(sport_key: str) -> list[datetime]:
    """Ligin bilinen yaklasan mac saatleri (Adim 3 pencere plani icin)."""
    with _LOCK:
        cache = _get_cache_locked()
    entry = cache.get("leagues", {}).get(sport_key)
    if not isinstance(entry, dict):
        return []
    kickoffs = entry.get("kickoffs")
    if not isinstance(kickoffs, list):
        return []
    parsed = [_parse_kickoff(raw) for raw in kickoffs]
    return sorted(moment for moment in parsed if moment is not None)


def annotate_scan_leagues_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Panel lig listesine kesif durumunu isler. SALT OKUR (ag cagrisi yapmaz).

    Panel 5 sn'de bir sorguladigi icin burada asla tazeleme tetiklenmez;
    eldeki onbellek neyse o gosterilir.
    """
    try:
        with _LOCK:
            cache = _get_cache_locked()
        age = _cache_age_seconds(cache)
        leagues = payload.get("leagues")
        if not isinstance(leagues, list):
            return payload

        if age is None or age > _HARD_STALE_SECONDS:
            for item in leagues:
                if isinstance(item, dict):
                    item["season_state"] = "bilinmiyor"
            payload["discovery_summary"] = "Kesif verisi henuz yok — ilk taramada toplanir (0 kredi)."
            return payload

        season_off = 0
        no_match = 0
        for item in leagues:
            if not isinstance(item, dict):
                continue
            state = _league_state(cache, str(item.get("sport_key", "")))
            item["season_state"] = state
            if item.get("enabled"):
                if state == "sezon_disi":
                    season_off += 1
                elif state == "mac_yok":
                    no_match += 1

        bits: list[str] = []
        if season_off:
            bits.append(f"{season_off} lig sezon disi")
        if no_match:
            bits.append(f"{no_match} ligde yakin pencerede mac yok")
        if bits:
            payload["discovery_summary"] = (
                "Kesif (0 kredi): " + ", ".join(bits) + " — bunlara oran sorgusu gitmiyor."
            )
        else:
            payload["discovery_summary"] = "Kesif (0 kredi): acik liglerin hepsinde yakin pencerede mac var."
        return payload
    except Exception:
        return payload
