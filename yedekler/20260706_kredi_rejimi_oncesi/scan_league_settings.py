from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

__all__ = (
    "SHARP_LEAGUE_CATALOG",
    "ScanLeagueSettings",
    "build_scan_leagues_payload",
    "compute_auto_enabled_keys",
    "get_enabled_sharp_sport_keys",
    "get_leagues_per_scan",
    "get_scan_league_settings",
    "is_auto_season_enabled",
    "set_auto_season_enabled",
    "update_scan_league_settings",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "scan_leagues.json"

_PRIORITY_SPORT_KEY = "soccer_turkey_super_league"
_DEFAULT_LEAGUES_PER_SCAN = 3
_MIN_LEAGUES_PER_SCAN = 1
_MAX_LEAGUES_PER_SCAN = 11


class _LeagueCatalogEntry(TypedDict):
    sport_key: str
    label: str
    tip: str


SHARP_LEAGUE_CATALOG: tuple[_LeagueCatalogEntry, ...] = (
    {
        "sport_key": "soccer_fifa_world_cup",
        "label": "FIFA World Cup",
        "tip": "Milli takim maclari. Panelden acilir; varsayilan taramada kapali.",
    },
    {
        "sport_key": "soccer_turkey_super_league",
        "label": "Turkiye Super Lig",
        "tip": "Turk ligleri. Sezon acikken Nesine maclari ile eslesir.",
    },
    {
        "sport_key": "soccer_uefa_champs_league",
        "label": "UEFA Champions League",
        "tip": "Sampiyonlar Ligi referans oranlari.",
    },
    {
        "sport_key": "soccer_epl",
        "label": "Premier League",
        "tip": "Ingiltere Premier League.",
    },
    {
        "sport_key": "soccer_spain_la_liga",
        "label": "La Liga",
        "tip": "Ispanya La Liga.",
    },
    {
        "sport_key": "soccer_germany_bundesliga",
        "label": "Bundesliga",
        "tip": "Almanya Bundesliga.",
    },
    {
        "sport_key": "soccer_italy_serie_a",
        "label": "Serie A",
        "tip": "Italya Serie A — genisletilmis tarama.",
    },
    {
        "sport_key": "soccer_france_ligue_one",
        "label": "Ligue 1",
        "tip": "Fransa Ligue 1 — genisletilmis tarama.",
    },
    {
        "sport_key": "soccer_uefa_europa_league",
        "label": "UEFA Europa League",
        "tip": "Avrupa Ligi referans oranlari.",
    },
    {
        "sport_key": "soccer_netherlands_eredivisie",
        "label": "Eredivisie",
        "tip": "Hollanda Eredivisie.",
    },
    {
        "sport_key": "soccer_brazil_campeonato",
        "label": "Brezilya Serie A",
        "tip": "Brezilya ust ligi. Yaz sezonu (Nisan-Aralik) — Avrupa kapaliyken acik.",
    },
    {
        "sport_key": "soccer_brazil_serie_b",
        "label": "Brezilya Serie B",
        "tip": "Brezilya 2. lig. Yaz sezonu (Nisan-Aralik).",
    },
    {
        "sport_key": "soccer_conmebol_copa_libertadores",
        "label": "Copa Libertadores",
        "tip": "Guney Amerika sampiyonlar ligi. Yaz boyunca aktif.",
    },
    {
        "sport_key": "soccer_conmebol_copa_sudamericana",
        "label": "Copa Sudamericana",
        "tip": "Guney Amerika Avrupa Ligi muadili. Yaz boyunca aktif.",
    },
    {
        "sport_key": "soccer_norway_eliteserien",
        "label": "Norvec Eliteserien",
        "tip": "Norvec ust ligi. Yaz sezonu (Mart-Kasim).",
    },
    {
        "sport_key": "soccer_sweden_allsvenskan",
        "label": "Isvec Allsvenskan",
        "tip": "Isvec ust ligi. Yaz sezonu (Nisan-Kasim).",
    },
    {
        "sport_key": "soccer_finland_veikkausliiga",
        "label": "Finlandiya Veikkausliiga",
        "tip": "Finlandiya UST ligi (Ykkonen degil). Yaz sezonu (Nisan-Ekim).",
    },
    {
        "sport_key": "soccer_korea_kleague1",
        "label": "Guney Kore K League 1",
        "tip": "Guney Kore ust ligi. Ilkbahar-sonbahar.",
    },
    {
        "sport_key": "soccer_china_superleague",
        "label": "Cin Super Lig",
        "tip": "Cin ust ligi. Yaz sezonu.",
    },
    {
        "sport_key": "soccer_league_of_ireland",
        "label": "Irlanda Ligi",
        "tip": "Irlanda ust ligi. Yaz sezonu (Subat-Kasim).",
    },
)

_VALID_SPORT_KEYS = frozenset(entry["sport_key"] for entry in SHARP_LEAGUE_CATALOG)
_DEFAULT_ENABLED_KEYS: tuple[str, ...] = tuple(
    entry["sport_key"]
    for entry in SHARP_LEAGUE_CATALOG
    if entry["sport_key"] != "soccer_fifa_world_cup"
)

# --- Mevsim takvimi (otomatik lig secimi) ---
# Her ligin hangi aylarda (1-12) acik oldugu. Avrupa ligleri Agustos-Mayis;
# yaz ligleri (Brezilya, Iskandinav, Asya, Guney Amerika) ilkbahar-sonbahar.
_EUROPEAN_KEYS: tuple[str, ...] = (
    "soccer_turkey_super_league",
    "soccer_uefa_champs_league",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_europa_league",
    "soccer_netherlands_eredivisie",
)
_SUMMER_KEYS: tuple[str, ...] = (
    "soccer_brazil_campeonato",
    "soccer_brazil_serie_b",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana",
    "soccer_norway_eliteserien",
    "soccer_sweden_allsvenskan",
    "soccer_finland_veikkausliiga",
    "soccer_korea_kleague1",
    "soccer_china_superleague",
    "soccer_league_of_ireland",
)
_EUROPEAN_MONTHS = frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5})
_WORLD_CUP_MONTHS = frozenset({6, 7})
_SEASON_MONTHS: dict[str, frozenset[int]] = {
    **{key: _EUROPEAN_MONTHS for key in _EUROPEAN_KEYS},
    "soccer_fifa_world_cup": _WORLD_CUP_MONTHS,
    "soccer_brazil_campeonato": frozenset({4, 5, 6, 7, 8, 9, 10, 11, 12}),
    "soccer_brazil_serie_b": frozenset({4, 5, 6, 7, 8, 9, 10, 11, 12}),
    "soccer_conmebol_copa_libertadores": frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_conmebol_copa_sudamericana": frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_norway_eliteserien": frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_sweden_allsvenskan": frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_finland_veikkausliiga": frozenset({4, 5, 6, 7, 8, 9, 10}),
    "soccer_korea_kleague1": frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_china_superleague": frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11}),
    "soccer_league_of_ireland": frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
}
_DEFAULT_AUTO_SEASON = True


def _current_month() -> int:
    return datetime.now(timezone.utc).month


def compute_auto_enabled_keys(month: int | None = None) -> list[str]:
    """Mevsime gore otomatik acik lig listesi.

    Avrupa ligleri sezonunda (Agustos-Mayis) -> Avrupa tercih edilir.
    Avrupa kapaliyken (yaz) -> Dunya Kupasi + acik yaz ligleri.
    """
    m = int(month) if month is not None else _current_month()
    world_cup = ["soccer_fifa_world_cup"] if m in _SEASON_MONTHS["soccer_fifa_world_cup"] else []
    european = [k for k in _EUROPEAN_KEYS if m in _SEASON_MONTHS.get(k, frozenset())]
    if european:
        return european + world_cup
    summer = [k for k in _SUMMER_KEYS if m in _SEASON_MONTHS.get(k, frozenset())]
    return world_cup + summer


class ScanLeagueSettings(TypedDict):
    leagues_per_scan: int
    enabled_sport_keys: list[str]


def _clamp_per_scan(value: int) -> int:
    return max(_MIN_LEAGUES_PER_SCAN, min(_MAX_LEAGUES_PER_SCAN, int(value)))


def _load_persisted_state() -> ScanLeagueSettings | None:
    """Diske kaydedilmis lig secimini oku (varsa). Bozuk/eksik veriyi yok say."""
    if not _PERSIST_PATH.is_file():
        return None
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_keys = payload.get("enabled_sport_keys")
    if not isinstance(raw_keys, list):
        return None
    keys: list[str] = []
    for item in raw_keys:
        if isinstance(item, str) and item.strip() in _VALID_SPORT_KEYS and item.strip() not in keys:
            keys.append(item.strip())
    if not keys:
        return None
    raw_per_scan = payload.get("leagues_per_scan", _DEFAULT_LEAGUES_PER_SCAN)
    per_scan = _clamp_per_scan(raw_per_scan) if isinstance(raw_per_scan, int) and not isinstance(raw_per_scan, bool) else _DEFAULT_LEAGUES_PER_SCAN
    return {"leagues_per_scan": min(per_scan, len(keys)), "enabled_sport_keys": keys}


def _load_auto_season() -> bool:
    if not _PERSIST_PATH.is_file():
        return _DEFAULT_AUTO_SEASON
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_AUTO_SEASON
    val = payload.get("auto_season", _DEFAULT_AUTO_SEASON)
    return bool(val) if isinstance(val, bool) else _DEFAULT_AUTO_SEASON


def _save_persisted_state() -> None:
    """Aktif lig secimini diske yaz (restart'tan sonra korunur)."""
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "auto_season": bool(_AUTO_SEASON),
        "enabled_sport_keys": list(_STATE["enabled_sport_keys"]),
        "leagues_per_scan": int(_STATE["leagues_per_scan"]),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_LOCK = threading.Lock()
_STATE: ScanLeagueSettings = _load_persisted_state() or {
    "leagues_per_scan": _DEFAULT_LEAGUES_PER_SCAN,
    "enabled_sport_keys": list(_DEFAULT_ENABLED_KEYS),
}
_AUTO_SEASON: bool = _load_auto_season()


def _clamp_leagues_per_scan(value: int) -> int:
    return max(_MIN_LEAGUES_PER_SCAN, min(_MAX_LEAGUES_PER_SCAN, int(value)))


def _normalize_enabled_keys(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in SHARP_LEAGUE_CATALOG:
        sport_key = entry["sport_key"]
        if sport_key in keys and sport_key not in seen:
            ordered.append(sport_key)
            seen.add(sport_key)
    if not ordered:
        return list(_DEFAULT_ENABLED_KEYS)
    return ordered


def get_scan_league_settings() -> ScanLeagueSettings:
    with _LOCK:
        return {
            "leagues_per_scan": int(_STATE["leagues_per_scan"]),
            "enabled_sport_keys": list(_STATE["enabled_sport_keys"]),
        }


def get_leagues_per_scan() -> int:
    with _LOCK:
        return int(_STATE["leagues_per_scan"])


def is_auto_season_enabled() -> bool:
    with _LOCK:
        return bool(_AUTO_SEASON)


def set_auto_season_enabled(enabled: bool) -> bool:
    global _AUTO_SEASON
    with _LOCK:
        _AUTO_SEASON = bool(enabled)
        _save_persisted_state()
        return _AUTO_SEASON


def _effective_enabled_keys_locked() -> list[str]:
    if _AUTO_SEASON:
        auto = compute_auto_enabled_keys()
        if auto:
            return auto
    return list(_STATE["enabled_sport_keys"])


def get_enabled_sharp_sport_keys() -> tuple[str, ...]:
    with _LOCK:
        return tuple(_effective_enabled_keys_locked())


def update_scan_league_settings(payload: dict[str, Any]) -> ScanLeagueSettings:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    global _AUTO_SEASON
    with _LOCK:
        if "auto_season" in payload:
            raw_auto = payload.get("auto_season")
            if not isinstance(raw_auto, bool):
                raise TypeError("auto_season must be a bool")
            _AUTO_SEASON = raw_auto

        if "leagues_per_scan" in payload:
            raw = payload.get("leagues_per_scan")
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise TypeError("leagues_per_scan must be an int")
            _STATE["leagues_per_scan"] = _clamp_leagues_per_scan(raw)

        if "enabled_sport_keys" in payload:
            # Elle lig secimi = otomatik mevsim modundan cikis.
            _AUTO_SEASON = False
            raw_keys = payload.get("enabled_sport_keys")
            if not isinstance(raw_keys, list):
                raise TypeError("enabled_sport_keys must be a list")
            normalized: list[str] = []
            for item in raw_keys:
                if not isinstance(item, str):
                    raise TypeError("enabled_sport_keys items must be strings")
                key = item.strip()
                if key not in _VALID_SPORT_KEYS:
                    raise ValueError(f"unknown sport_key: {key}")
                if key not in normalized:
                    normalized.append(key)
            if not normalized:
                raise ValueError("at least one league must remain enabled")
            _STATE["enabled_sport_keys"] = normalized

        max_allowed = max(1, len(_effective_enabled_keys_locked()))
        if _STATE["leagues_per_scan"] > max_allowed:
            _STATE["leagues_per_scan"] = max_allowed

        _save_persisted_state()

        return {
            "leagues_per_scan": int(_STATE["leagues_per_scan"]),
            "enabled_sport_keys": list(_STATE["enabled_sport_keys"]),
        }


def build_scan_leagues_payload() -> dict[str, Any]:
    settings = get_scan_league_settings()
    auto_on = is_auto_season_enabled()
    # Panelde gosterilen "acik" isaretleri = taramada gercekten kullanilan ligler.
    effective = set(get_enabled_sharp_sport_keys())
    leagues: list[dict[str, Any]] = []
    for entry in SHARP_LEAGUE_CATALOG:
        sport_key = entry["sport_key"]
        leagues.append(
            {
                "sport_key": sport_key,
                "label": entry["label"],
                "tip": entry["tip"],
                "enabled": sport_key in effective,
            }
        )
    active_labels = [entry["label"] for entry in SHARP_LEAGUE_CATALOG if entry["sport_key"] in effective]
    season_mode = "avrupa" if any(k in effective for k in _EUROPEAN_KEYS) else "yaz"
    return {
        "leagues_per_scan": settings["leagues_per_scan"],
        "min_leagues_per_scan": _MIN_LEAGUES_PER_SCAN,
        "max_leagues_per_scan": _MAX_LEAGUES_PER_SCAN,
        "enabled_count": len(effective),
        "leagues": leagues,
        "auto_season": auto_on,
        "season_mode": season_mode,
        "auto_active_labels": active_labels,
        "auto_summary": (
            ("Otomatik mevsim ACIK — su an " + ("Avrupa ligleri" if season_mode == "avrupa" else "yaz ligleri") + " taraniyor.")
            if auto_on
            else "Otomatik mevsim KAPALI — ligleri elle seciyorsun."
        ),
        "tip": (
            "Otomatik mevsim acikken sistem, Avrupa sezonunda Avrupa liglerini, "
            "yazin acik yaz liglerini kendiliginden secer. Elle lig secersen otomatik kapanir."
        ),
    }


def select_rotated_sharp_sport_keys(*, rotation_index: int) -> tuple[list[str], int]:
    """Pick sport keys for one sharp scan using enabled leagues and rotation."""
    enabled = list(get_enabled_sharp_sport_keys())
    if not enabled:
        enabled = list(_DEFAULT_ENABLED_KEYS)

    leagues_per_scan = min(get_leagues_per_scan(), len(enabled))
    selected: list[str] = []
    if _PRIORITY_SPORT_KEY in enabled:
        selected.append(_PRIORITY_SPORT_KEY)

    cursor = int(rotation_index)
    while len(selected) < leagues_per_scan:
        candidate = enabled[cursor % len(enabled)]
        cursor += 1
        if candidate in selected:
            if len(enabled) <= len(selected):
                break
            continue
        selected.append(candidate)

    return selected, cursor
