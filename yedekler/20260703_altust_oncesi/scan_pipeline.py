from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from config.settings import MAX_EV_THRESHOLD
from core.clv_engine import calculate_stake_amount
from core.context_fusion import apply_action_tier_context_fusion
from core.experimental_features import (
    apply_experimental_hero_gate,
    apply_experimental_tier_gate,
)
from core.hero_gate import (
    build_market_prob_index,
    evaluate_hero_gate,
    is_hero_mode_enabled,
)
from core.hero_profile import get_active_hero_profile_values
from core.operator_risk_settings import get_soft_odds_max, get_soft_odds_min
from core.match_filters import (
    SCAN_CYCLE,
    build_notification_key,
    build_stable_match_id,
    is_feed_pair_synchronized,
    is_feed_timestamp_fresh,
    is_suspicious_match_record,
    is_virtual_match_text,
)
from core.passion_engine import (
    calculate_expected_value,
    classify_ev_tier,
    is_ev_absurd,
)

__all__ = (
    "ScanPipelineStats",
    "ScanCandidate",
    "evaluate_matches",
    "count_legacy_action_candidates",
)

_NESINE_MIN_STAKE_TL = 50.0
_LEGACY_MIN_EV = 0.03


@dataclass
class ScanPipelineStats:
    birlesik: int = 0
    efutbol: int = 0
    stale: int = 0
    tolerans: int = 0
    pasif_ev: int = 0
    absurd_ev: int = 0
    suspicious_match: int = 0
    context_filter: int = 0
    experimental_checked: int = 0
    experimental_would_filter: int = 0
    watch: int = 0
    action: int = 0
    high: int = 0
    aday: int = 0
    hero_pass: int = 0
    hero_reject_low_confidence: int = 0
    hero_reject_context: int = 0
    hero_reject_market: int = 0
    hero_reject_consensus: int = 0
    hero_reject_odds_band: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "birlesik": self.birlesik,
            "efutbol": self.efutbol,
            "stale": self.stale,
            "tolerans": self.tolerans,
            "pasif_ev": self.pasif_ev,
            "absurd_ev": self.absurd_ev,
            "suspicious_match": self.suspicious_match,
            "context_filter": self.context_filter,
            "experimental_checked": self.experimental_checked,
            "experimental_would_filter": self.experimental_would_filter,
            "watch": self.watch,
            "action": self.action,
            "high": self.high,
            "aday": self.aday,
            "hero_pass": self.hero_pass,
            "hero_reject_low_confidence": self.hero_reject_low_confidence,
            "hero_reject_context": self.hero_reject_context,
            "hero_reject_market": self.hero_reject_market,
            "hero_reject_consensus": self.hero_reject_consensus,
            "hero_reject_odds_band": self.hero_reject_odds_band,
        }


@dataclass
class ScanCandidate:
    notify_key: str
    match_name: str
    market: str
    match_id: str
    tier: str
    ev: float
    ev_percent: str
    soft_odds: float
    sharp_odds: float
    stake: float
    consensus_books: int
    league_name: str = "Bilinmiyor"
    hero_confidence: float | None = None


def _format_ev_percent(ev: float) -> str:
    return f"{ev * 100.0:.2f}"


def _resolve_league_name(match: dict[str, Any]) -> str:
    for key in ("league_name", "comp_name", "lig", "league", "competition"):
        value = match.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Bilinmiyor"


def _is_match_fresh_for_cycle(match: dict[str, Any]) -> bool:
    cycle_started_at = SCAN_CYCLE.started_at
    if cycle_started_at <= 0.0:
        return True

    soft_observed_at = float(match.get("soft_observed_at", match.get("observed_at", time.time())))
    sharp_observed_at = float(match.get("sharp_observed_at", match.get("observed_at", time.time())))
    if soft_observed_at < cycle_started_at or sharp_observed_at < cycle_started_at:
        return False
    if not is_feed_timestamp_fresh(soft_observed_at):
        return False
    if not is_feed_timestamp_fresh(sharp_observed_at):
        return False
    return is_feed_pair_synchronized(soft_observed_at, sharp_observed_at)


def _resolve_stake(current_kasa: float, ev: float, tier: str) -> float:
    if tier == "WATCH":
        return 0.0
    stake_amount = calculate_stake_amount(current_kasa, ev)
    if stake_amount < _NESINE_MIN_STAKE_TL:
        return _NESINE_MIN_STAKE_TL
    return stake_amount


def _resolve_hero_stake(current_kasa: float, risk_per_trade: float) -> float:
    stake_amount = round(float(current_kasa) * float(risk_per_trade), 2)
    if stake_amount < _NESINE_MIN_STAKE_TL:
        return _NESINE_MIN_STAKE_TL
    return stake_amount


def _record_hero_rejection(stats: ScanPipelineStats, reason: str) -> None:
    if reason in {"dusuk_guven", "gecersiz_oran", "kapanis_esigi"}:
        stats.hero_reject_low_confidence += 1
    elif reason in {"form_uyumsuz", "baglam_okunamadi"}:
        stats.hero_reject_context += 1
    elif reason in {"net_ustunluk_yetersiz", "piyasa_uyumsuz"}:
        stats.hero_reject_market += 1
    elif reason == "referans_yetersiz":
        stats.hero_reject_consensus += 1


def evaluate_matches(
    matches: list[dict[str, Any]],
    *,
    current_kasa: float,
    skip_freshness: bool = False,
) -> tuple[list[ScanCandidate], ScanPipelineStats]:
    if is_hero_mode_enabled():
        return _evaluate_matches_hero(
            matches,
            current_kasa=current_kasa,
            skip_freshness=skip_freshness,
        )
    return _evaluate_matches_ev(
        matches,
        current_kasa=current_kasa,
        skip_freshness=skip_freshness,
    )


def _evaluate_matches_hero(
    matches: list[dict[str, Any]],
    *,
    current_kasa: float,
    skip_freshness: bool,
) -> tuple[list[ScanCandidate], ScanPipelineStats]:
    stats = ScanPipelineStats()
    candidates: list[ScanCandidate] = []
    stats.birlesik = len(matches)
    profile = get_active_hero_profile_values()
    prob_index = build_market_prob_index(matches)
    soft_min = float(profile["soft_odds_min"])
    soft_max = float(profile["soft_odds_max"])
    risk_per_trade = float(profile["risk_per_trade"])

    for match in matches:
        if not isinstance(match, dict):
            continue

        match_name = str(match.get("match_name", ""))
        market = str(match.get("market", ""))
        if not match_name.strip() or not market.strip():
            continue

        league_name = _resolve_league_name(match)
        notify_key = build_notification_key(match_name, market)

        if is_virtual_match_text(match_name, league_name):
            stats.efutbol += 1
            continue

        if not skip_freshness and not _is_match_fresh_for_cycle(match):
            stats.stale += 1
            continue

        if is_suspicious_match_record(match):
            stats.suspicious_match += 1
            continue

        try:
            sharp_odds = float(match["sharp_odds"])
            soft_odds = float(match["soft_odds"])
        except (KeyError, TypeError, ValueError):
            continue

        if not (soft_min <= soft_odds <= soft_max):
            stats.hero_reject_odds_band += 1
            stats.tolerans += 1
            continue

        gate = evaluate_hero_gate(match, prob_index, profile)
        if not gate.passed:
            _record_hero_rejection(stats, gate.reason)
            if gate.reason == "form_uyumsuz":
                stats.context_filter += 1
            continue

        experimental = apply_experimental_hero_gate(
            match,
            market=market.strip().upper(),
        )
        if experimental.modules_checked:
            stats.experimental_checked += experimental.modules_checked
        if experimental.would_filter:
            stats.experimental_would_filter += 1
        if not experimental.passed:
            stats.hero_reject_context += 1
            continue

        ev = calculate_expected_value(sharp_odds, soft_odds)
        if is_ev_absurd(ev):
            # Kahraman modunda da absurt EV = muhtemel eslesme hatasi; ele.
            stats.absurd_ev += 1
            continue

        stats.hero_pass += 1
        stats.action += 1

        win_probability = float(gate.win_probability or 0.0)
        candidates.append(
            ScanCandidate(
                notify_key=notify_key,
                match_name=match_name,
                market=market,
                match_id=build_stable_match_id(match_name, market),
                tier="ACTION",
                ev=ev,
                ev_percent=_format_ev_percent(ev),
                soft_odds=soft_odds,
                sharp_odds=sharp_odds,
                stake=_resolve_hero_stake(current_kasa, risk_per_trade),
                consensus_books=int(match.get("consensus_books", 0) or 0),
                league_name=league_name,
                hero_confidence=win_probability,
            )
        )

    stats.aday = len(candidates)
    return candidates, stats


def _evaluate_matches_ev(
    matches: list[dict[str, Any]],
    *,
    current_kasa: float,
    skip_freshness: bool,
) -> tuple[list[ScanCandidate], ScanPipelineStats]:
    stats = ScanPipelineStats()
    candidates: list[ScanCandidate] = []
    stats.birlesik = len(matches)

    for match in matches:
        if not isinstance(match, dict):
            continue

        match_name = str(match.get("match_name", ""))
        market = str(match.get("market", ""))
        if not match_name.strip() or not market.strip():
            continue

        league_name = _resolve_league_name(match)
        notify_key = build_notification_key(match_name, market)

        if is_virtual_match_text(match_name, league_name):
            stats.efutbol += 1
            continue

        if not skip_freshness and not _is_match_fresh_for_cycle(match):
            stats.stale += 1
            continue

        sharp_odds = float(match["sharp_odds"])
        soft_odds = float(match["soft_odds"])

        if is_suspicious_match_record(match):
            stats.suspicious_match += 1
            continue

        soft_min = get_soft_odds_min()
        soft_max = get_soft_odds_max()
        if not (soft_min <= soft_odds <= soft_max):
            stats.tolerans += 1
            continue

        ev = calculate_expected_value(sharp_odds, soft_odds)
        consensus_books = int(match.get("consensus_books", 0) or 0)
        tier = classify_ev_tier(ev, consensus_books=consensus_books)

        if tier is None:
            if is_ev_absurd(ev):
                stats.absurd_ev += 1
            else:
                stats.pasif_ev += 1
            continue

        tier, fusion_decision = apply_action_tier_context_fusion(
            tier,
            match,
            market=market.strip().upper(),
            sharp_odds=sharp_odds,
            soft_odds=soft_odds,
            consensus_books=consensus_books,
        )
        if tier is None:
            if fusion_decision.applied:
                stats.context_filter += 1
            continue

        experimental = apply_experimental_tier_gate(
            tier,
            match,
            market=market.strip().upper(),
        )
        if experimental.modules_checked:
            stats.experimental_checked += experimental.modules_checked
        if experimental.would_filter:
            stats.experimental_would_filter += 1
        tier = experimental.tier
        if tier is None:
            continue

        if tier == "WATCH":
            stats.watch += 1
        elif tier == "HIGH":
            stats.high += 1
        else:
            stats.action += 1

        candidates.append(
            ScanCandidate(
                notify_key=notify_key,
                match_name=match_name,
                market=market,
                match_id=build_stable_match_id(match_name, market),
                tier=tier,
                ev=ev,
                ev_percent=_format_ev_percent(ev),
                soft_odds=soft_odds,
                sharp_odds=sharp_odds,
                stake=_resolve_stake(current_kasa, ev, tier),
                consensus_books=consensus_books,
                league_name=league_name,
            )
        )

    stats.aday = len(candidates)
    return candidates, stats


def count_legacy_action_candidates(matches: list[dict[str, Any]]) -> int:
    """Eski kural: yalnizca EV >= %3 ACTION (WATCH yok)."""
    count = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        match_name = str(match.get("match_name", ""))
        if is_virtual_match_text(match_name, _resolve_league_name(match)):
            continue
        soft_odds = float(match.get("soft_odds", 0.0))
        sharp_odds = float(match.get("sharp_odds", 0.0))
        soft_min = get_soft_odds_min()
        soft_max = get_soft_odds_max()
        if not (soft_min <= soft_odds <= soft_max):
            continue
        ev = calculate_expected_value(sharp_odds, soft_odds)
        if _LEGACY_MIN_EV <= ev <= float(MAX_EV_THRESHOLD):
            count += 1
    return count


def count_current_work_orders(candidates: list[ScanCandidate]) -> dict[str, int]:
    return {
        "total": len(candidates),
        "watch": sum(1 for item in candidates if item.tier == "WATCH"),
        "action": sum(1 for item in candidates if item.tier == "ACTION"),
        "high": sum(1 for item in candidates if item.tier == "HIGH"),
        "actionable": sum(1 for item in candidates if item.tier in ("ACTION", "HIGH")),
    }
