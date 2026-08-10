from __future__ import annotations

import time
from typing import Any

from scrapers.mock_scraper import get_live_market_data
from core.match_filters import is_suspicious_odds_pair

__all__ = ("get_dry_run_unified_matches",)

_DRY_RUN_LEAGUE = "SQE Dry-Run Lig"


def get_dry_run_unified_matches() -> list[dict[str, Any]]:
    now = time.time()
    unified: list[dict[str, Any]] = []
    for index, snapshot in enumerate(get_live_market_data()):
        sharp_odds = float(snapshot["sharp_odds"])
        soft_odds = float(snapshot["soft_odds"])
        unified.append(
            {
                "match_name": snapshot["match_name"],
                "market": snapshot["market"],
                "sharp_odds": sharp_odds,
                "soft_odds": soft_odds,
                "league_name": _DRY_RUN_LEAGUE,
                "event_id": f"dry-run-event-{index}",
                "commence_time": "2026-06-19T18:00:00Z",
                "sport_key": "soccer_turkey_super_league",
                "match_quality": "suspicious" if is_suspicious_odds_pair(soft_odds, sharp_odds) else "ok",
                "observed_at": now,
                "soft_observed_at": now,
                "sharp_observed_at": now,
                "consensus_books": 3 if index >= 2 else 2,
                "soft_source": "nesine",
            }
        )
    return unified
