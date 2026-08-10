from __future__ import annotations

import time
from dataclasses import dataclass, field

_ASCII_TRANSLATION = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

_VIRTUAL_MATCH_KEYWORDS = (
    "e-futbol",
    "efutbol",
    "e futbol",
    "esoccer",
    "e soccer",
    "esports",
    "e-sports",
    "cyber",
    "srl",
    "simulated",
)

_FEED_FRESHNESS_SECONDS = 900
_MAX_FEED_SKEW_SECONDS = 120
_SUSPICIOUS_ODDS_RATIO_THRESHOLD = 3.0

__all__ = (
    "ScanCycleState",
    "SCAN_CYCLE",
    "SUSPICIOUS_ODDS_RATIO_THRESHOLD",
    "build_notification_key",
    "build_stable_match_id",
    "is_feed_timestamp_fresh",
    "is_feed_pair_synchronized",
    "is_suspicious_match_record",
    "is_suspicious_odds_pair",
    "is_virtual_match_text",
    "odds_pair_ratio",
    "reset_scan_cycle",
)


@dataclass
class ScanCycleState:
    cycle_id: str = ""
    started_at: float = 0.0
    live_match_keys: set[str] = field(default_factory=set)
    qualifying_keys: set[str] = field(default_factory=set)

    def reset(self) -> str:
        self.cycle_id = str(time.time_ns())
        self.started_at = time.time()
        self.live_match_keys.clear()
        self.qualifying_keys.clear()
        return self.cycle_id

    def register_live_match(self, match_name: str, market: str) -> str:
        key = build_notification_key(match_name, market)
        self.live_match_keys.add(key)
        return key

    def register_qualifying(self, notify_key: str) -> None:
        self.qualifying_keys.add(notify_key)

    @property
    def has_qualifying(self) -> bool:
        return bool(self.qualifying_keys)


SCAN_CYCLE = ScanCycleState()


def reset_scan_cycle() -> str:
    return SCAN_CYCLE.reset()


def is_virtual_match_text(*texts: object) -> bool:
    for raw in texts:
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().casefold().translate(_ASCII_TRANSLATION)
        for keyword in _VIRTUAL_MATCH_KEYWORDS:
            if keyword.casefold() in normalized:
                return True
    return False


def build_notification_key(match_name: str, market: str) -> str:
    normalized = match_name.strip().lower().translate(_ASCII_TRANSLATION)
    market_key = market.strip().upper()
    return f"{normalized}|{market_key}"


def build_stable_match_id(match_name: str, market: str) -> str:
    slug = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in build_notification_key(match_name, market)
    ).strip("_")
    return slug or "mac"


def is_feed_timestamp_fresh(
    observed_at: float,
    *,
    reference_at: float | None = None,
) -> bool:
    if observed_at <= 0.0:
        return False
    ref = reference_at if reference_at is not None else time.time()
    return (ref - observed_at) <= _FEED_FRESHNESS_SECONDS


def is_feed_pair_synchronized(soft_observed_at: float, sharp_observed_at: float) -> bool:
    if soft_observed_at <= 0.0 or sharp_observed_at <= 0.0:
        return False
    return abs(soft_observed_at - sharp_observed_at) <= _MAX_FEED_SKEW_SECONDS


def odds_pair_ratio(soft_odds: float, sharp_odds: float) -> float:
    if soft_odds <= 1.0 or sharp_odds <= 1.0:
        return 0.0
    return max(soft_odds / sharp_odds, sharp_odds / soft_odds)


def is_suspicious_odds_pair(soft_odds: float, sharp_odds: float) -> bool:
    return odds_pair_ratio(soft_odds, sharp_odds) > _SUSPICIOUS_ODDS_RATIO_THRESHOLD


def is_suspicious_match_record(match: object) -> bool:
    if not isinstance(match, dict):
        return False

    quality = match.get("match_quality")
    if isinstance(quality, str) and quality.strip().casefold() == "suspicious":
        return True

    soft_odds = match.get("soft_odds")
    sharp_odds = match.get("sharp_odds")
    if isinstance(soft_odds, bool) or isinstance(sharp_odds, bool):
        return False
    if not isinstance(soft_odds, (int, float)) or not isinstance(sharp_odds, (int, float)):
        return False

    return is_suspicious_odds_pair(float(soft_odds), float(sharp_odds))
