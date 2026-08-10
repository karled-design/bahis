from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from database.context_cache import is_cacheable_real_match

__all__ = (
    "ContextFeatureError",
    "ContextFeatures",
    "extract_context_features",
    "form_string_to_ppg",
    "normalize_team_key",
    "resolve_standings_team",
    "split_match_teams",
)

_ASCII_TRANSLATION = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_FORM_RESULT_POINTS = {"W": 3.0, "D": 1.0, "L": 0.0}
_MAX_FORM_LENGTH = 5
_MAX_H2H_MATCHES = 3


class ContextFeatureError(ValueError):
    """Gecersiz veya sanal mac icin feature cikarimi reddedildi."""


@dataclass(frozen=True)
class ContextFeatures:
    match_name: str
    sport_key: str
    event_id: str
    home_team_key: str
    away_team_key: str
    home_rank: int | None = None
    away_rank: int | None = None
    rank_diff: int | None = None
    home_points_per_game: float | None = None
    away_points_per_game: float | None = None
    home_goals_for_per_game: float | None = None
    home_goals_against_per_game: float | None = None
    away_goals_for_per_game: float | None = None
    away_goals_against_per_game: float | None = None
    home_form_ppg: float | None = None
    away_form_ppg: float | None = None
    h2h_home_win_rate: float | None = None
    h2h_avg_total_goals: float | None = None
    home_injury_count: int = 0
    away_injury_count: int = 0
    data_fields_present: int = 0
    data_fields_total: int = 10
    extras: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def data_completeness(self) -> float:
        if self.data_fields_total <= 0:
            return 0.0
        return round(self.data_fields_present / self.data_fields_total, 4)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "match_name": self.match_name,
            "sport_key": self.sport_key,
            "event_id": self.event_id,
            "home_team_key": self.home_team_key,
            "away_team_key": self.away_team_key,
            "home_rank": self.home_rank,
            "away_rank": self.away_rank,
            "rank_diff": self.rank_diff,
            "home_points_per_game": self.home_points_per_game,
            "away_points_per_game": self.away_points_per_game,
            "home_goals_for_per_game": self.home_goals_for_per_game,
            "home_goals_against_per_game": self.home_goals_against_per_game,
            "away_goals_for_per_game": self.away_goals_for_per_game,
            "away_goals_against_per_game": self.away_goals_against_per_game,
            "home_form_ppg": self.home_form_ppg,
            "away_form_ppg": self.away_form_ppg,
            "h2h_home_win_rate": self.h2h_home_win_rate,
            "h2h_avg_total_goals": self.h2h_avg_total_goals,
            "home_injury_count": self.home_injury_count,
            "away_injury_count": self.away_injury_count,
            "data_completeness": self.data_completeness,
        }
        payload.update(self.extras)
        return payload


def split_match_teams(match_name: str) -> tuple[str, str]:
    if " - " not in match_name:
        return match_name.strip(), ""
    home_team, away_team = match_name.split(" - ", 1)
    return home_team.strip(), away_team.strip()


def normalize_team_key(team_name: str) -> str:
    normalized = team_name.strip().casefold().translate(_ASCII_TRANSLATION)
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.replace(" ", "_")


def form_string_to_ppg(form: object) -> float | None:
    if not isinstance(form, str) or not form.strip():
        return None
    letters = [char.upper() for char in form.strip() if char.upper() in _FORM_RESULT_POINTS]
    if not letters:
        return None
    sample = letters[-_MAX_FORM_LENGTH:]
    total = sum(_FORM_RESULT_POINTS[letter] for letter in sample)
    return round(total / len(sample), 4)


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _per_game(total: object, played: object) -> float | None:
    total_value = _safe_float(total)
    played_value = _safe_int(played)
    if total_value is None or played_value is None or played_value <= 0:
        return None
    return round(total_value / played_value, 4)


def resolve_standings_team(standings: dict[str, Any], team_key: str) -> dict[str, Any] | None:
    teams = standings.get("teams")
    if not isinstance(teams, list):
        return None
    normalized_key = normalize_team_key(team_key)
    for row in teams:
        if not isinstance(row, dict):
            continue
        row_key = row.get("team_key")
        if isinstance(row_key, str) and normalize_team_key(row_key) == normalized_key:
            return row
        row_name = row.get("team_name")
        if isinstance(row_name, str) and normalize_team_key(row_name) == normalized_key:
            return row
    return None


def _count_present(*values: object) -> int:
    return sum(1 for value in values if value is not None)


def _extract_standings_features(
    standings: dict[str, Any] | None,
    home_team_key: str,
    away_team_key: str,
) -> dict[str, int | float | None]:
    empty: dict[str, int | float | None] = {
        "home_rank": None,
        "away_rank": None,
        "rank_diff": None,
        "home_points_per_game": None,
        "away_points_per_game": None,
        "home_goals_for_per_game": None,
        "home_goals_against_per_game": None,
        "away_goals_for_per_game": None,
        "away_goals_against_per_game": None,
    }
    if not isinstance(standings, dict):
        return empty

    home_row = resolve_standings_team(standings, home_team_key)
    away_row = resolve_standings_team(standings, away_team_key)
    if home_row is None or away_row is None:
        return empty

    home_rank = _safe_int(home_row.get("rank"))
    away_rank = _safe_int(away_row.get("rank"))
    rank_diff = None
    if home_rank is not None and away_rank is not None:
        rank_diff = home_rank - away_rank

    return {
        "home_rank": home_rank,
        "away_rank": away_rank,
        "rank_diff": rank_diff,
        "home_points_per_game": _per_game(home_row.get("points"), home_row.get("played")),
        "away_points_per_game": _per_game(away_row.get("points"), away_row.get("played")),
        "home_goals_for_per_game": _per_game(home_row.get("goals_for"), home_row.get("played")),
        "home_goals_against_per_game": _per_game(home_row.get("goals_against"), home_row.get("played")),
        "away_goals_for_per_game": _per_game(away_row.get("goals_for"), away_row.get("played")),
        "away_goals_against_per_game": _per_game(away_row.get("goals_against"), away_row.get("played")),
    }


def _extract_form_ppg(form_payload: dict[str, Any] | None, standings_row: dict[str, Any] | None) -> float | None:
    if isinstance(form_payload, dict):
        last_matches = form_payload.get("last_matches")
        if isinstance(last_matches, list) and last_matches:
            points: list[float] = []
            for item in last_matches[-_MAX_FORM_LENGTH:]:
                if not isinstance(item, dict):
                    continue
                result = item.get("result")
                if isinstance(result, str):
                    ppg = form_string_to_ppg(result)
                    if ppg is not None:
                        points.append(ppg)
            if points:
                return round(sum(points) / len(points), 4)

    if isinstance(standings_row, dict):
        return form_string_to_ppg(standings_row.get("form"))

    return None


def _extract_h2h_features(
    h2h: dict[str, Any] | None,
    *,
    home_team_key: str,
    away_team_key: str,
) -> tuple[float | None, float | None]:
    if not isinstance(h2h, dict):
        return None, None

    matches = h2h.get("matches")
    if not isinstance(matches, list) or not matches:
        return None, None

    home_wins = 0
    total_goals: list[float] = []
    considered = 0

    for item in matches[-_MAX_H2H_MATCHES:]:
        if not isinstance(item, dict):
            continue
        home_goals = _safe_int(item.get("home_goals"))
        away_goals = _safe_int(item.get("away_goals"))
        row_home_key = normalize_team_key(str(item.get("home_team_key", "")))
        row_away_key = normalize_team_key(str(item.get("away_team_key", "")))
        if home_goals is None or away_goals is None:
            continue

        considered += 1
        total_goals.append(float(home_goals + away_goals))

        if row_home_key == home_team_key and row_away_key == away_team_key:
            if home_goals > away_goals:
                home_wins += 1
        elif row_home_key == away_team_key and row_away_key == home_team_key:
            if away_goals > home_goals:
                home_wins += 1

    if considered <= 0:
        return None, None

    win_rate = round(home_wins / considered, 4)
    avg_goals = round(sum(total_goals) / len(total_goals), 4)
    return win_rate, avg_goals


def _extract_injury_counts(injuries: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(injuries, dict):
        return 0, 0

    home_absences = injuries.get("home_absences")
    away_absences = injuries.get("away_absences")
    home_count = len(home_absences) if isinstance(home_absences, list) else 0
    away_count = len(away_absences) if isinstance(away_absences, list) else 0
    return home_count, away_count


def extract_context_features(
    match: dict[str, Any],
    *,
    standings: dict[str, Any] | None = None,
    home_form: dict[str, Any] | None = None,
    away_form: dict[str, Any] | None = None,
    h2h: dict[str, Any] | None = None,
    injuries: dict[str, Any] | None = None,
) -> ContextFeatures:
    if not is_cacheable_real_match(match):
        raise ContextFeatureError("context features yalnizca gercek maclar icin uretilir")

    match_name = str(match.get("match_name", "")).strip()
    sport_key = str(match.get("sport_key", "")).strip()
    event_id = str(match.get("event_id", "")).strip()
    if not match_name or not sport_key or not event_id:
        raise ContextFeatureError("match_name, sport_key ve event_id zorunludur")

    home_team, away_team = split_match_teams(match_name)
    if not home_team or not away_team:
        raise ContextFeatureError("mac adi Home - Away formatinda olmali")

    home_team_key = normalize_team_key(home_team)
    away_team_key = normalize_team_key(away_team)

    standings_features = _extract_standings_features(standings, home_team_key, away_team_key)
    home_row = resolve_standings_team(standings, home_team_key) if isinstance(standings, dict) else None
    away_row = resolve_standings_team(standings, away_team_key) if isinstance(standings, dict) else None

    home_form_ppg = _extract_form_ppg(home_form, home_row)
    away_form_ppg = _extract_form_ppg(away_form, away_row)
    h2h_home_win_rate, h2h_avg_total_goals = _extract_h2h_features(
        h2h,
        home_team_key=home_team_key,
        away_team_key=away_team_key,
    )
    home_injury_count, away_injury_count = _extract_injury_counts(injuries)

    tracked_values = (
        standings_features["home_rank"],
        standings_features["away_rank"],
        standings_features["rank_diff"],
        standings_features["home_points_per_game"],
        standings_features["away_points_per_game"],
        home_form_ppg,
        away_form_ppg,
        h2h_home_win_rate,
        h2h_avg_total_goals,
    )
    data_fields_present = _count_present(*tracked_values)
    if home_injury_count > 0 or away_injury_count > 0:
        data_fields_present += 1

    return ContextFeatures(
        match_name=match_name,
        sport_key=sport_key,
        event_id=event_id,
        home_team_key=home_team_key,
        away_team_key=away_team_key,
        home_rank=_safe_int(standings_features["home_rank"]),
        away_rank=_safe_int(standings_features["away_rank"]),
        rank_diff=_safe_int(standings_features["rank_diff"]),
        home_points_per_game=_safe_float(standings_features["home_points_per_game"]),
        away_points_per_game=_safe_float(standings_features["away_points_per_game"]),
        home_goals_for_per_game=_safe_float(standings_features["home_goals_for_per_game"]),
        home_goals_against_per_game=_safe_float(standings_features["home_goals_against_per_game"]),
        away_goals_for_per_game=_safe_float(standings_features["away_goals_for_per_game"]),
        away_goals_against_per_game=_safe_float(standings_features["away_goals_against_per_game"]),
        home_form_ppg=home_form_ppg,
        away_form_ppg=away_form_ppg,
        h2h_home_win_rate=h2h_home_win_rate,
        h2h_avg_total_goals=h2h_avg_total_goals,
        home_injury_count=home_injury_count,
        away_injury_count=away_injury_count,
        data_fields_present=data_fields_present,
    )
