from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from core.context_features import normalize_team_key

__all__ = (
    "TeamResolution",
    "find_fixture_by_team_ids",
    "get_team_alias_stats",
    "resolve_match_teams",
    "resolve_team_name",
)

_OPERATOR_DIAG = "Donanim Erisilemiyor: Team Resolver Hatasi"
_ALIASES_PATH = Path(__file__).resolve().parent.parent / "config" / "team_aliases.json"
_FUZZY_RATIO_THRESHOLD = 0.88
_ALIAS_INDEX: dict[tuple[str, str], TeamResolution] | None = None
_CANONICAL_INDEX: dict[tuple[str, str], TeamResolution] | None = None


@dataclass(frozen=True)
class TeamResolution:
    team_id: int
    canonical_key: str
    display_name: str
    sport_key: str
    matched_alias: str
    match_method: str


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _sequence_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _build_resolution(
    *,
    sport_key: str,
    team_id: int,
    display_name: str,
    matched_alias: str,
    match_method: str,
) -> TeamResolution:
    return TeamResolution(
        team_id=int(team_id),
        canonical_key=normalize_team_key(display_name),
        display_name=display_name.strip(),
        sport_key=sport_key.strip(),
        matched_alias=matched_alias.strip(),
        match_method=match_method,
    )


def _load_alias_indexes() -> tuple[dict[tuple[str, str], TeamResolution], dict[tuple[str, str], TeamResolution]]:
    global _ALIAS_INDEX, _CANONICAL_INDEX
    if _ALIAS_INDEX is not None and _CANONICAL_INDEX is not None:
        return _ALIAS_INDEX, _CANONICAL_INDEX

    alias_index: dict[tuple[str, str], TeamResolution] = {}
    canonical_index: dict[tuple[str, str], TeamResolution] = {}

    if not _ALIASES_PATH.is_file():
        _emit_operator_diag(f"alias file missing | {_ALIASES_PATH}")
        _ALIAS_INDEX = alias_index
        _CANONICAL_INDEX = canonical_index
        return alias_index, canonical_index

    try:
        payload = json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit_operator_diag(f"alias file unreadable | {exc}")
        _ALIAS_INDEX = alias_index
        _CANONICAL_INDEX = canonical_index
        return alias_index, canonical_index

    teams = payload.get("teams")
    if not isinstance(teams, list):
        _emit_operator_diag("alias file invalid | teams list missing")
        _ALIAS_INDEX = alias_index
        _CANONICAL_INDEX = canonical_index
        return alias_index, canonical_index

    for row in teams:
        if not isinstance(row, dict):
            continue
        sport_key = str(row.get("sport_key", "")).strip()
        team_id = int(row.get("team_id", 0) or 0)
        display_name = str(row.get("display_name", "")).strip()
        if not sport_key or team_id <= 0 or not display_name:
            continue

        canonical = _build_resolution(
            sport_key=sport_key,
            team_id=team_id,
            display_name=display_name,
            matched_alias=display_name,
            match_method="canonical",
        )
        canonical_index[(sport_key, canonical.canonical_key)] = canonical

        alias_names = [display_name]
        raw_aliases = row.get("aliases")
        if isinstance(raw_aliases, list):
            alias_names.extend(str(item).strip() for item in raw_aliases if str(item).strip())

        seen_keys: set[str] = set()
        for alias_name in alias_names:
            alias_key = normalize_team_key(alias_name)
            if not alias_key or alias_key in seen_keys:
                continue
            seen_keys.add(alias_key)
            alias_index[(sport_key, alias_key)] = _build_resolution(
                sport_key=sport_key,
                team_id=team_id,
                display_name=display_name,
                matched_alias=alias_name,
                match_method="alias",
            )

    _ALIAS_INDEX = alias_index
    _CANONICAL_INDEX = canonical_index
    return alias_index, canonical_index


def _candidate_sport_keys(sport_key: str) -> tuple[str, ...]:
    normalized = sport_key.strip()
    if not normalized:
        return ()
    candidates = [normalized]
    lowered = normalized.casefold()
    if any(token in lowered for token in ("fifa", "world", "euro", "nations", "international")):
        candidates.append("international")
    return tuple(dict.fromkeys(candidates))


def resolve_team_name(team_name: str, *, sport_key: str) -> TeamResolution | None:
    """Odds/sharp takim adini API-Football team_id ile eslestirir; lig disi alias kullanilmaz."""
    cleaned = team_name.strip()
    if not cleaned or not sport_key.strip():
        return None

    alias_index, canonical_index = _load_alias_indexes()
    normalized = normalize_team_key(cleaned)
    if not normalized:
        return None

    for candidate_sport in _candidate_sport_keys(sport_key):
        exact = alias_index.get((candidate_sport, normalized))
        if exact is not None:
            return exact
        canonical = canonical_index.get((candidate_sport, normalized))
        if canonical is not None:
            return canonical

    best: TeamResolution | None = None
    best_ratio = 0.0
    for candidate_sport in _candidate_sport_keys(sport_key):
        for (indexed_sport, indexed_key), resolution in alias_index.items():
            if indexed_sport != candidate_sport:
                continue
            ratio = _sequence_ratio(normalized, indexed_key)
            if ratio >= _FUZZY_RATIO_THRESHOLD and ratio > best_ratio:
                best_ratio = ratio
                best = resolution
    return best


def resolve_match_teams(
    home_name: str,
    away_name: str,
    *,
    sport_key: str,
) -> tuple[TeamResolution, TeamResolution] | None:
    home = resolve_team_name(home_name, sport_key=sport_key)
    away = resolve_team_name(away_name, sport_key=sport_key)
    if home is None or away is None:
        return None
    if home.team_id == away.team_id:
        return None
    return home, away


def find_fixture_by_team_ids(
    fixtures: list[dict[str, Any]],
    *,
    home_team_id: int,
    away_team_id: int,
) -> dict[str, Any] | None:
    if home_team_id <= 0 or away_team_id <= 0:
        return None

    for row in fixtures:
        if not isinstance(row, dict):
            continue
        teams = row.get("teams")
        if not isinstance(teams, dict):
            continue
        api_home = teams.get("home")
        api_away = teams.get("away")
        if not isinstance(api_home, dict) or not isinstance(api_away, dict):
            continue
        api_home_id = int(api_home.get("id", 0) or 0)
        api_away_id = int(api_away.get("id", 0) or 0)
        if api_home_id <= 0 or api_away_id <= 0:
            continue
        if {api_home_id, api_away_id} == {home_team_id, away_team_id}:
            return row
    return None


def get_team_alias_stats() -> dict[str, int]:
    alias_index, canonical_index = _load_alias_indexes()
    sport_keys = {sport for sport, _ in alias_index.keys()}
    return {
        "alias_entries": len(alias_index),
        "canonical_entries": len(canonical_index),
        "sport_keys": len(sport_keys),
        "teams": len(canonical_index),
    }
