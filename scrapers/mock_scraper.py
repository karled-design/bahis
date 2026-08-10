from __future__ import annotations

from typing import Any, TypedDict

__all__ = ("get_live_market_data",)


class MarketSnapshot(TypedDict):
    match_name: str
    market: str
    sharp_odds: float
    soft_odds: float


def _build_snapshot(
    match_name: str,
    market: str,
    sharp_odds: float,
    soft_odds: float,
) -> MarketSnapshot:
    if not match_name.strip():
        raise ValueError("match_name must not be empty")
    if not market.strip():
        raise ValueError("market must not be empty")
    if sharp_odds <= 1.0 or soft_odds <= 1.0:
        raise ValueError("odds must be greater than 1.0")
    return {
        "match_name": match_name.strip(),
        "market": market.strip(),
        "sharp_odds": float(sharp_odds),
        "soft_odds": float(soft_odds),
    }


def get_live_market_data() -> list[MarketSnapshot]:
    return [
        _build_snapshot(
            match_name="Dry-Run | Avantajsiz Mac - Takim B",
            market="MS1",
            sharp_odds=1.85,
            soft_odds=1.80,
        ),
        _build_snapshot(
            match_name="Dry-Run | IZLE Mac - Takim C",
            market="MS1",
            sharp_odds=2.00,
            soft_odds=2.04,
        ),
        _build_snapshot(
            match_name="Dry-Run | ACTION Mac - Takim D",
            market="MS1",
            sharp_odds=2.00,
            soft_odds=2.06,
        ),
        _build_snapshot(
            match_name="Dry-Run | YUKSEK Mac - Takim E",
            market="MS2",
            sharp_odds=2.00,
            soft_odds=2.12,
        ),
        _build_snapshot(
            match_name="Dry-Run | Absurt EV Mac - Takim F",
            market="MS1",
            sharp_odds=1.60,
            soft_odds=2.10,
        ),
    ]
