from __future__ import annotations

from typing import Any

from core.context_features import normalize_team_key, split_match_teams
from core.experimental.types import ModuleVerdict

__all__ = ("evaluate_news_signals",)

_INJURY_BLOCK_THRESHOLD = 3
_RSS_NEGATIVE_KEYWORDS = (
    "sakat",
    "sakatlik",
    "oynamayacak",
    "oynayamayacak",
    "kadro disi",
    "cezali",
    "injury",
    "injured",
    "suspended",
    "out of",
)


def _pick_side_absence_count(
    market: str,
    *,
    home_count: int,
    away_count: int,
) -> tuple[int, str]:
    normalized = str(market).strip().upper()
    if normalized == "MS1":
        return home_count, "ev_sahibi"
    if normalized == "MS2":
        return away_count, "deplasman"
    return 0, ""


def _injuries_from_match(match: dict[str, Any]) -> dict[str, Any] | None:
    bundle = match.get("context_bundle")
    if isinstance(bundle, dict) and isinstance(bundle.get("injuries"), dict):
        return bundle["injuries"]
    experimental = match.get("experimental_news")
    if isinstance(experimental, dict) and isinstance(experimental.get("injuries"), dict):
        return experimental["injuries"]
    return None


def _count_absences(injuries: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(injuries, dict):
        return 0, 0
    home = injuries.get("home_absences")
    away = injuries.get("away_absences")
    home_count = len(home) if isinstance(home, list) else 0
    away_count = len(away) if isinstance(away, list) else 0
    return home_count, away_count


def _rss_hits_for_match(match: dict[str, Any]) -> list[dict[str, str]]:
    payload = match.get("experimental_news")
    if not isinstance(payload, dict):
        return []
    hits = payload.get("rss_hits")
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def _team_keys(match: dict[str, Any]) -> tuple[str, str]:
    match_name = str(match.get("match_name", "")).strip()
    home, away = split_match_teams(match_name)
    return normalize_team_key(home), normalize_team_key(away)


def evaluate_news_signals(
    match: dict[str, Any],
    *,
    market: str,
) -> ModuleVerdict:
    normalized_market = str(market).strip().upper()
    home_count, away_count = _count_absences(_injuries_from_match(match))
    side_count, side_label = _pick_side_absence_count(
        market,
        home_count=home_count,
        away_count=away_count,
    )

    normalized_market = str(market).strip().upper()
    if side_count >= _INJURY_BLOCK_THRESHOLD:
        return ModuleVerdict(
            module="news_signals",
            keep=False,
            reason="haber_sakatlik",
            detail=f"{side_label} tarafinda {side_count} eksik (esik {_INJURY_BLOCK_THRESHOLD}+)",
        )

    rss_hits = _rss_hits_for_match(match)
    if rss_hits:
        home_key, away_key = _team_keys(match)
        for hit in rss_hits:
            title = str(hit.get("title", "")).casefold()
            teams = hit.get("teams")
            team_keys = (
                {normalize_team_key(str(item)) for item in teams if str(item).strip()}
                if isinstance(teams, list)
                else set()
            )
            if not any(keyword in title for keyword in _RSS_NEGATIVE_KEYWORDS):
                continue
            targets_pick = False
            if normalized_market == "MS1" and home_key in team_keys:
                targets_pick = True
            elif normalized_market == "MS2" and away_key in team_keys:
                targets_pick = True
            if targets_pick:
                return ModuleVerdict(
                    module="news_signals",
                    keep=False,
                    reason="haber_rss",
                    detail=str(hit.get("title", "")).strip()[:120],
                )

    from core.context_fusion import resolve_context_features_from_match

    features = resolve_context_features_from_match(match)
    if features is not None and side_count >= 2 and normalized_market in {"MS1", "MS2"}:
        return ModuleVerdict(
            module="news_signals",
            keep=False,
            reason="haber_sakatlik_hafif",
            detail=f"{side_label} tarafinda {side_count} eksik (2+ uyari)",
        )

    if side_count > 0 or rss_hits:
        detail_parts: list[str] = []
        if side_count > 0:
            detail_parts.append(f"eksik={side_count}")
        if rss_hits:
            detail_parts.append(f"rss={len(rss_hits)}")
        return ModuleVerdict(
            module="news_signals",
            keep=True,
            reason="haber_ok",
            detail=", ".join(detail_parts),
        )

    return ModuleVerdict(
        module="news_signals",
        keep=True,
        reason="haber_veri_yok",
        detail="sakatlik/rss verisi yok",
    )
