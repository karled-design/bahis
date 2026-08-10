from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable

from config.settings import API_FOOTBALL_KEY, CONTEXT_ENRICH_MODE, SOFT_MARKET_LAG_TIMEOUT
from core.context_features import normalize_team_key, split_match_teams
from core.operator_risk_settings import get_context_mode
from core.team_resolver import find_fixture_by_team_ids, resolve_match_teams
from database.context_cache import (
    is_cacheable_real_match,
    read_context_bundle_if_fresh,
    read_h2h_if_fresh,
    read_injuries_if_fresh,
    read_standings_if_fresh,
    write_context_bundle_cache,
    write_h2h_cache,
    write_injuries_cache,
    write_standings_cache,
)
from database.db_manager import init_db, run_context_cache_operation

__all__ = (
    "build_context_bundle_for_match",
    "enrich_match_feed_safely",
    "enrich_matches_with_context",
    "get_last_context_feed_diag",
    "is_api_football_configured",
    "should_run_context_enrich",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Context Feed Hatasi"
_API_BASE_URL = "https://v3.football.api-sports.io"
_MAX_RESPONSE_BYTES = 2_000_000
_MATCH_RATIO_THRESHOLD = 0.75
_STANDINGS_TTL_SECONDS = 3600
_INJURIES_TTL_SECONDS = 1800
_INJURIES_PREMATCH_TTL_SECONDS = 600
_H2H_TTL_SECONDS = 86400
_BUNDLE_TTL_SECONDS = 1800
_FIXTURES_MEMORY_TTL_SECONDS = 1800

# API-Football league id eslemesi (yalnizca dogrulanmis domestic/top ligler).
_SPORT_KEY_TO_LEAGUE_ID: dict[str, int] = {
    "soccer_turkey_super_league": 203,
    "soccer_epl": 39,
    "soccer_spain_la_liga": 140,
    "soccer_germany_bundesliga": 78,
    "soccer_uefa_champs_league": 2,
    "soccer_italy_serie_a": 135,
    "soccer_france_ligue_one": 61,
    "soccer_uefa_europa_league": 3,
    "soccer_netherlands_eredivisie": 88,
}

_LAST_DIAG: dict[str, Any] = {
    "configured": False,
    "api_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "bundle_attached": 0,
    "skipped_no_league": 0,
    "skipped_no_fixture_match": 0,
    "skipped_not_real_match": 0,
    "errors": 0,
}

_FIXTURES_MEMORY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _emit_scan_info(message: str) -> None:
    print(f"[SQE-V1] Context Feed: {message}")


def is_api_football_configured() -> bool:
    return bool(API_FOOTBALL_KEY.strip())


def should_run_context_enrich() -> bool:
    """Canli taramada baglam zenginlestirme acik mi? API key yoksa kapali."""
    if get_context_mode() == "off":
        return False
    mode = CONTEXT_ENRICH_MODE.strip().casefold()
    if mode == "off":
        return False
    if mode == "on":
        return is_api_football_configured()
    return is_api_football_configured()


def get_last_context_feed_diag() -> dict[str, Any]:
    payload = dict(_LAST_DIAG)
    payload["configured"] = is_api_football_configured()
    total = int(payload["cache_hits"]) + int(payload["cache_misses"])
    payload["cache_hit_rate"] = round(int(payload["cache_hits"]) / total, 4) if total > 0 else 0.0
    return payload


def _reset_diag_for_run() -> None:
    global _LAST_DIAG
    _LAST_DIAG = {
        "configured": is_api_football_configured(),
        "api_requests": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "bundle_attached": 0,
        "skipped_no_league": 0,
        "skipped_no_fixture_match": 0,
        "skipped_not_real_match": 0,
        "errors": 0,
    }


def _parse_commence_time(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_prematch_injury_refresh_window(commence_time: str) -> bool:
    kickoff = _parse_commence_time(commence_time)
    if kickoff is None:
        return False
    now = datetime.now(timezone.utc)
    if kickoff <= now:
        return False
    return (kickoff - now).total_seconds() <= _PREMATCH_INJURY_REFRESH_SECONDS


def _record_cache_hit() -> None:
    _LAST_DIAG["cache_hits"] = int(_LAST_DIAG.get("cache_hits", 0)) + 1


def _record_cache_miss() -> None:
    _LAST_DIAG["cache_misses"] = int(_LAST_DIAG.get("cache_misses", 0)) + 1


def _sequence_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _resolve_season(commence_time: str) -> str:
    if commence_time.strip():
        try:
            parsed = datetime.fromisoformat(commence_time.strip().replace("Z", "+00:00"))
            return str(parsed.year)
        except ValueError:
            pass
    return str(datetime.now(timezone.utc).year)


def _resolve_fixture_date(commence_time: str) -> str:
    if commence_time.strip():
        try:
            parsed = datetime.fromisoformat(commence_time.strip().replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _fetch_api_json(path: str, params: dict[str, str | int]) -> dict[str, Any] | None:
    if not is_api_football_configured():
        return None

    query = urllib.parse.urlencode(params)
    url = f"{_API_BASE_URL}{path}?{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "x-apisports-key": API_FOOTBALL_KEY.strip(),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SOFT_MARKET_LAG_TIMEOUT) as response:
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        _LAST_DIAG["api_requests"] = int(_LAST_DIAG.get("api_requests", 0)) + 1
        payload = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        _LAST_DIAG["errors"] = int(_LAST_DIAG.get("errors", 0)) + 1
        _emit_operator_diag(f"api request failed | {path} | {exc}")
        return None

    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        _LAST_DIAG["errors"] = int(_LAST_DIAG.get("errors", 0)) + 1
        _emit_operator_diag(f"api errors | {path} | {errors[:1]}")
        return None
    if isinstance(errors, dict) and errors:
        _LAST_DIAG["errors"] = int(_LAST_DIAG.get("errors", 0)) + 1
        _emit_operator_diag(f"api errors | {path} | {errors}")
        return None
    return payload


def _normalize_standings_payload(api_payload: dict[str, Any], *, sport_key: str, season: str) -> dict[str, Any] | None:
    response = api_payload.get("response")
    if not isinstance(response, list) or not response:
        return None
    first = response[0]
    if not isinstance(first, dict):
        return None
    league = first.get("league")
    if not isinstance(league, dict):
        return None
    standings_groups = league.get("standings")
    if not isinstance(standings_groups, list):
        return None

    teams: list[dict[str, Any]] = []
    for group in standings_groups:
        if not isinstance(group, list):
            continue
        for row in group:
            if not isinstance(row, dict):
                continue
            team_info = row.get("team")
            all_stats = row.get("all")
            if not isinstance(team_info, dict) or not isinstance(all_stats, dict):
                continue
            team_name = str(team_info.get("name", "")).strip()
            if not team_name:
                continue
            goals = all_stats.get("goals") if isinstance(all_stats.get("goals"), dict) else {}
            teams.append(
                {
                    "team_name": team_name,
                    "team_key": normalize_team_key(team_name),
                    "rank": int(row.get("rank", 0) or 0),
                    "points": int(row.get("points", 0) or 0),
                    "played": int(all_stats.get("played", 0) or 0),
                    "goals_for": int(goals.get("for", 0) or 0),
                    "goals_against": int(goals.get("against", 0) or 0),
                    "form": str(row.get("form", "")).strip(),
                }
            )

    if not teams:
        return None
    return {"sport_key": sport_key, "season": season, "teams": teams}


def _normalize_injuries_payload(
    api_payload: dict[str, Any],
    *,
    home_team_id: int,
    away_team_id: int,
) -> dict[str, Any]:
    response = api_payload.get("response")
    if not isinstance(response, list):
        return {"home_absences": [], "away_absences": []}

    home_absences: list[dict[str, str]] = []
    away_absences: list[dict[str, str]] = []
    for row in response:
        if not isinstance(row, dict):
            continue
        team = row.get("team")
        player = row.get("player")
        if not isinstance(team, dict) or not isinstance(player, dict):
            continue
        team_id = team.get("id")
        player_name = str(player.get("name", "")).strip()
        if not player_name:
            continue
        reason = str(player.get("reason") or row.get("type") or "injury").strip()
        entry = {"player": player_name, "reason": reason or "injury"}
        if team_id == home_team_id:
            home_absences.append(entry)
        elif team_id == away_team_id:
            away_absences.append(entry)

    return {"home_absences": home_absences, "away_absences": away_absences}


def _normalize_h2h_payload(
    api_payload: dict[str, Any],
    *,
    home_team_key: str,
    away_team_key: str,
) -> dict[str, Any] | None:
    response = api_payload.get("response")
    if not isinstance(response, list) or not response:
        return None

    matches: list[dict[str, Any]] = []
    for row in response[:3]:
        if not isinstance(row, dict):
            continue
        teams = row.get("teams")
        goals = row.get("goals")
        if not isinstance(teams, dict) or not isinstance(goals, dict):
            continue
        home = teams.get("home")
        away = teams.get("away")
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        home_name = str(home.get("name", "")).strip()
        away_name = str(away.get("name", "")).strip()
        home_goals = goals.get("home")
        away_goals = goals.get("away")
        if home_goals is None or away_goals is None:
            continue
        matches.append(
            {
                "home_team_key": normalize_team_key(home_name),
                "away_team_key": normalize_team_key(away_name),
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
            }
        )

    if not matches:
        return None
    return {
        "home_team_key": home_team_key,
        "away_team_key": away_team_key,
        "matches": matches,
    }


def _load_fixtures_for_league_date(
    *,
    sport_key: str,
    league_id: int,
    season: str,
    fixture_date: str,
    fetch_api: Callable[[str, dict[str, str | int]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    cache_key = f"{sport_key}:{league_id}:{season}:{fixture_date}"
    now = datetime.now(timezone.utc).timestamp()
    cached = _FIXTURES_MEMORY_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) <= _FIXTURES_MEMORY_TTL_SECONDS:
        _record_cache_hit()
        return list(cached[1])

    _record_cache_miss()
    payload = fetch_api(
        "/fixtures",
        {"league": league_id, "season": season, "date": fixture_date},
    )
    if payload is None:
        return []

    response = payload.get("response")
    if not isinstance(response, list):
        return []

    fixtures: list[dict[str, Any]] = []
    for row in response:
        if isinstance(row, dict):
            fixtures.append(row)

    _FIXTURES_MEMORY_CACHE[cache_key] = (now, fixtures)
    return fixtures


def _match_api_fixture(
    match: dict[str, Any],
    fixtures: list[dict[str, Any]],
    *,
    sport_key: str,
) -> dict[str, Any] | None:
    match_name = str(match.get("match_name", "")).strip()
    home_name, away_name = split_match_teams(match_name)
    if not home_name or not away_name:
        return None

    resolved = resolve_match_teams(home_name, away_name, sport_key=sport_key)
    if resolved is not None:
        home_res, away_res = resolved
        alias_fixture = find_fixture_by_team_ids(
            fixtures,
            home_team_id=home_res.team_id,
            away_team_id=away_res.team_id,
        )
        if alias_fixture is not None:
            return alias_fixture

    home_norm = normalize_team_key(home_name)
    away_norm = normalize_team_key(away_name)
    best_row: dict[str, Any] | None = None
    best_ratio = 0.0

    for row in fixtures:
        teams = row.get("teams")
        if not isinstance(teams, dict):
            continue
        api_home = teams.get("home")
        api_away = teams.get("away")
        if not isinstance(api_home, dict) or not isinstance(api_away, dict):
            continue
        api_home_name = normalize_team_key(str(api_home.get("name", "")))
        api_away_name = normalize_team_key(str(api_away.get("name", "")))
        if not api_home_name or not api_away_name:
            continue

        direct = (_sequence_ratio(home_norm, api_home_name) + _sequence_ratio(away_norm, api_away_name)) / 2
        swapped = (_sequence_ratio(home_norm, api_away_name) + _sequence_ratio(away_norm, api_home_name)) / 2
        ratio = max(direct, swapped)
        if ratio >= _MATCH_RATIO_THRESHOLD and ratio > best_ratio:
            best_ratio = ratio
            best_row = row

    return best_row


def build_context_bundle_for_match(
    match: dict[str, Any],
    *,
    fetch_api: Callable[[str, dict[str, str | int]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    if not is_cacheable_real_match(match):
        _LAST_DIAG["skipped_not_real_match"] = int(_LAST_DIAG.get("skipped_not_real_match", 0)) + 1
        return None

    event_id = str(match.get("event_id", "")).strip()
    sport_key = str(match.get("sport_key", "")).strip()
    if not event_id or not sport_key:
        return None

    init_db()
    api_fetch = fetch_api if fetch_api is not None else _fetch_api_json

    def _load_bundle(connection: Any) -> dict[str, Any] | None:
        cached = read_context_bundle_if_fresh(connection, event_id=event_id, sport_key=sport_key)
        if cached is not None:
            _record_cache_hit()
            return cached
        _record_cache_miss()
        return None

    cached_bundle = run_context_cache_operation(_load_bundle)
    if isinstance(cached_bundle, dict):
        return cached_bundle

    league_id = _SPORT_KEY_TO_LEAGUE_ID.get(sport_key)
    if league_id is None:
        _LAST_DIAG["skipped_no_league"] = int(_LAST_DIAG.get("skipped_no_league", 0)) + 1
        return None

    if not is_api_football_configured() and fetch_api is None:
        return None

    commence_time = str(match.get("commence_time", "")).strip()
    season = _resolve_season(commence_time)
    fixture_date = _resolve_fixture_date(commence_time)

    fixtures = _load_fixtures_for_league_date(
        sport_key=sport_key,
        league_id=league_id,
        season=season,
        fixture_date=fixture_date,
        fetch_api=api_fetch,
    )
    api_fixture = _match_api_fixture(match, fixtures, sport_key=sport_key)
    if api_fixture is None:
        _LAST_DIAG["skipped_no_fixture_match"] = int(_LAST_DIAG.get("skipped_no_fixture_match", 0)) + 1
        return None

    teams = api_fixture.get("teams")
    fixture_meta = api_fixture.get("fixture")
    if not isinstance(teams, dict) or not isinstance(fixture_meta, dict):
        return None
    api_home = teams.get("home")
    api_away = teams.get("away")
    if not isinstance(api_home, dict) or not isinstance(api_away, dict):
        return None

    home_team_id = int(api_home.get("id", 0) or 0)
    away_team_id = int(api_away.get("id", 0) or 0)
    api_fixture_id = int(fixture_meta.get("id", 0) or 0)
    home_team_key = normalize_team_key(str(api_home.get("name", "")))
    away_team_key = normalize_team_key(str(api_away.get("name", "")))

    def _load_standings(connection: Any) -> dict[str, Any] | None:
        cached = read_standings_if_fresh(connection, sport_key=sport_key, season=season)
        if cached is not None:
            _record_cache_hit()
            return cached
        _record_cache_miss()
        payload = api_fetch("/standings", {"league": league_id, "season": season})
        if payload is None:
            return None
        normalized = _normalize_standings_payload(payload, sport_key=sport_key, season=season)
        if normalized is not None:
            write_standings_cache(
                connection,
                sport_key=sport_key,
                season=season,
                payload=normalized,
                ttl_seconds=_STANDINGS_TTL_SECONDS,
                league_api_id=league_id,
            )
        return normalized

    force_injury_refresh = _is_prematch_injury_refresh_window(commence_time)

    def _load_injuries(connection: Any) -> dict[str, Any] | None:
        if not force_injury_refresh:
            cached = read_injuries_if_fresh(connection, event_id=event_id)
            if cached is not None:
                _record_cache_hit()
                return cached
        else:
            _record_cache_miss()
        if api_fixture_id <= 0:
            return None
        payload = api_fetch("/injuries", {"fixture": api_fixture_id})
        if payload is None:
            return None
        normalized = _normalize_injuries_payload(
            payload,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
        write_injuries_cache(
            connection,
            event_id=event_id,
            payload=normalized,
            ttl_seconds=(
                _INJURIES_PREMATCH_TTL_SECONDS
                if force_injury_refresh
                else _INJURIES_TTL_SECONDS
            ),
            api_fixture_id=api_fixture_id,
        )
        return normalized

    def _load_h2h(connection: Any) -> dict[str, Any] | None:
        if home_team_id <= 0 or away_team_id <= 0:
            return None
        cached = read_h2h_if_fresh(
            connection,
            home_team_key=home_team_key,
            away_team_key=away_team_key,
            sport_key=sport_key,
        )
        if cached is not None:
            _record_cache_hit()
            return cached
        _record_cache_miss()
        payload = api_fetch("/fixtures/headtohead", {"h2h": f"{home_team_id}-{away_team_id}"})
        if payload is None:
            return None
        normalized = _normalize_h2h_payload(
            payload,
            home_team_key=home_team_key,
            away_team_key=away_team_key,
        )
        if normalized is not None:
            write_h2h_cache(
                connection,
                home_team_key=home_team_key,
                away_team_key=away_team_key,
                sport_key=sport_key,
                payload=normalized,
                ttl_seconds=_H2H_TTL_SECONDS,
            )
        return normalized

    def _assemble_bundle(connection: Any) -> dict[str, Any] | None:
        standings = _load_standings(connection)
        injuries = _load_injuries(connection)
        h2h = _load_h2h(connection)
        if standings is None:
            return None
        bundle: dict[str, Any] = {"standings": standings}
        if injuries is not None:
            bundle["injuries"] = injuries
        if h2h is not None:
            bundle["h2h"] = h2h
        write_context_bundle_cache(
            connection,
            event_id=event_id,
            sport_key=sport_key,
            bundle=bundle,
            ttl_seconds=_BUNDLE_TTL_SECONDS,
        )
        return bundle

    bundle = run_context_cache_operation(_assemble_bundle)
    return bundle if isinstance(bundle, dict) else None


def _match_context_dedupe_key(match: dict[str, Any]) -> str | None:
    event_id = str(match.get("event_id", "")).strip()
    sport_key = str(match.get("sport_key", "")).strip()
    if event_id and sport_key:
        return f"{sport_key}:{event_id}"
    match_name = str(match.get("match_name", "")).strip()
    if match_name and sport_key:
        return f"{sport_key}:{normalize_team_key(match_name)}"
    return None


def enrich_matches_with_context(
    matches: list[dict[str, Any]],
    *,
    fetch_api: Callable[[str, dict[str, str | int]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    _reset_diag_for_run()
    if not matches:
        return []

    resolved_bundles: dict[str, dict[str, Any] | None] = {}
    enriched: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        item = dict(match)
        dedupe_key = _match_context_dedupe_key(item)
        bundle: dict[str, Any] | None
        if dedupe_key is not None and dedupe_key in resolved_bundles:
            bundle = resolved_bundles[dedupe_key]
        else:
            bundle = build_context_bundle_for_match(item, fetch_api=fetch_api)
            if dedupe_key is not None:
                resolved_bundles[dedupe_key] = bundle
        if bundle is not None:
            item["context_bundle"] = bundle
            _LAST_DIAG["bundle_attached"] = int(_LAST_DIAG.get("bundle_attached", 0)) + 1
        enriched.append(item)

    diag = get_last_context_feed_diag()
    _emit_scan_info(
        f"baglam_zenginlestirme | toplam={len(enriched)} | bundle={diag['bundle_attached']} | "
        f"api_req={diag['api_requests']} | cache_hit_rate={diag['cache_hit_rate']}"
    )
    return enriched


def enrich_match_feed_safely(
    matches: list[dict[str, Any]],
    *,
    fetch_api: Callable[[str, dict[str, str | int]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Canli feed'e baglam ekler; hata veya kapali modda orijinal listeyi bozmadan doner."""
    if not matches or not should_run_context_enrich():
        return matches
    try:
        return enrich_matches_with_context(matches, fetch_api=fetch_api)
    except Exception as exc:
        _LAST_DIAG["errors"] = int(_LAST_DIAG.get("errors", 0)) + 1
        _emit_operator_diag(f"enrich_match_feed_safely failed | {exc}")
        return matches
