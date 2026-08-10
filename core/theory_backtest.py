from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import PAPER_SLIPPAGE_PCT
from core.context_fusion import passes_context_fusion_for_action
from core.context_features import ContextFeatureError, ContextFeatures, extract_context_features
from core.match_filters import is_suspicious_match_record
from core.operator_risk_settings import get_soft_odds_max, get_soft_odds_min
from core.paper_trade import apply_paper_slippage
from core.passion_engine import calculate_expected_value, is_ev_actionable
from database.context_cache import is_cacheable_real_match

__all__ = (
    "TheoryBacktestCase",
    "TheoryBacktestReport",
    "StrategyMetrics",
    "FUSION_FP_REDUCTION_TARGET",
    "FUSION_ROI_IMPROVEMENT_TARGET",
    "load_backtest_cases",
    "run_theory_backtest",
    "evaluate_fusion_gate",
)

_FUSION_FP_REDUCTION_TARGET = 0.15
_FUSION_ROI_IMPROVEMENT_TARGET = 0.005
_DEFAULT_STAKE_TL = 100.0
_VALID_RESULTS = frozenset({"WON", "LOST"})


@dataclass(frozen=True)
class TheoryBacktestCase:
    case_id: str
    commence_time: str
    match: dict[str, Any]
    market: str
    sharp_odds: float
    soft_odds: float
    consensus_books: int
    result: str
    context: dict[str, Any] | None = None


@dataclass
class StrategyMetrics:
    strategy: str
    bets: int = 0
    wins: int = 0
    losses: int = 0
    skipped: int = 0
    false_positives: int = 0
    total_staked: float = 0.0
    total_profit: float = 0.0
    brier_sum: float = 0.0
    brier_count: int = 0
    ev_positive_cases: int = 0

    @property
    def false_positive_rate(self) -> float:
        if self.false_positives <= 0:
            return 0.0
        if self.ev_positive_cases <= 0:
            return 0.0
        return round(self.false_positives / self.ev_positive_cases, 6)

    @property
    def roi(self) -> float:
        if self.total_staked <= 0.0:
            return 0.0
        return round(self.total_profit / self.total_staked, 6)

    @property
    def brier_score(self) -> float | None:
        if self.brier_count <= 0:
            return None
        return round(self.brier_sum / self.brier_count, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "bets": self.bets,
            "wins": self.wins,
            "losses": self.losses,
            "skipped": self.skipped,
            "false_positives": self.false_positives,
            "false_positive_rate": self.false_positive_rate,
            "ev_positive_cases": self.ev_positive_cases,
            "total_staked": round(self.total_staked, 2),
            "total_profit": round(self.total_profit, 2),
            "roi": self.roi,
            "brier_score": self.brier_score,
        }


@dataclass
class TheoryBacktestReport:
    case_count: int
    ev_only: StrategyMetrics
    ev_context: StrategyMetrics
    fusion_fp_reduction: float
    fusion_roi_delta: float
    fusion_gate_pass: bool
    fusion_gate_reason: str
    walk_forward: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "ev_only": self.ev_only.as_dict(),
            "ev_context": self.ev_context.as_dict(),
            "fusion_fp_reduction": self.fusion_fp_reduction,
            "fusion_roi_delta": self.fusion_roi_delta,
            "fusion_gate_pass": self.fusion_gate_pass,
            "fusion_gate_reason": self.fusion_gate_reason,
            "walk_forward": list(self.walk_forward),
        }


def _parse_case(raw: dict[str, Any]) -> TheoryBacktestCase | None:
    if not isinstance(raw, dict):
        return None
    case_id = str(raw.get("id", "")).strip()
    match = raw.get("match")
    market = str(raw.get("market", "")).strip().upper()
    result = str(raw.get("result", "")).strip().upper()
    if not case_id or not isinstance(match, dict) or market not in {"MS1", "MS2", "X"}:
        return None
    if result not in _VALID_RESULTS:
        return None
    try:
        sharp_odds = float(raw["sharp_odds"])
        soft_odds = float(raw["soft_odds"])
        consensus_books = int(raw.get("consensus_books", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None
    context = raw.get("context")
    return TheoryBacktestCase(
        case_id=case_id,
        commence_time=str(raw.get("commence_time", "")).strip(),
        match=match,
        market=market,
        sharp_odds=sharp_odds,
        soft_odds=soft_odds,
        consensus_books=consensus_books,
        result=result,
        context=context if isinstance(context, dict) else None,
    )


def load_backtest_cases(path: Path | str) -> list[TheoryBacktestCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases_raw = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases_raw, list):
        return []
    cases: list[TheoryBacktestCase] = []
    for item in cases_raw:
        parsed = _parse_case(item)
        if parsed is not None:
            cases.append(parsed)
    cases.sort(key=lambda item: item.commence_time)
    return cases


def _build_match_record(case: TheoryBacktestCase) -> dict[str, Any]:
    record = dict(case.match)
    record.setdefault("market", case.market)
    record["sharp_odds"] = case.sharp_odds
    record["soft_odds"] = case.soft_odds
    record["consensus_books"] = case.consensus_books
    return record


def _passes_base_filters(case: TheoryBacktestCase) -> bool:
    record = _build_match_record(case)
    if not is_cacheable_real_match(record):
        return False
    if is_suspicious_match_record(record):
        return False
    soft_min = get_soft_odds_min()
    soft_max = get_soft_odds_max()
    return soft_min <= case.soft_odds <= soft_max


def _extract_features(case: TheoryBacktestCase) -> ContextFeatures | None:
    if case.context is None:
        return None
    ctx = case.context
    try:
        return extract_context_features(
            case.match,
            standings=ctx.get("standings"),
            home_form=ctx.get("home_form"),
            away_form=ctx.get("away_form"),
            h2h=ctx.get("h2h"),
            injuries=ctx.get("injuries"),
        )
    except ContextFeatureError:
        return None


def _should_bet_ev_only(case: TheoryBacktestCase) -> tuple[bool, float]:
    if not _passes_base_filters(case):
        return False, 0.0
    ev = calculate_expected_value(case.sharp_odds, case.soft_odds)
    if not is_ev_actionable(ev, consensus_books=case.consensus_books):
        return False, ev
    return True, ev


def _should_bet_ev_context(case: TheoryBacktestCase, features: ContextFeatures | None) -> tuple[bool, float]:
    bet_ev, ev = _should_bet_ev_only(case)
    if not bet_ev:
        return False, ev
    record = _build_match_record(case)
    if case.context is not None:
        record["context_bundle"] = case.context
    decision = passes_context_fusion_for_action(
        record,
        market=case.market,
        sharp_odds=case.sharp_odds,
        soft_odds=case.soft_odds,
        consensus_books=case.consensus_books,
    )
    if not decision.applied:
        return False, ev
    return decision.passed, ev


def _bet_profit(case: TheoryBacktestCase) -> float:
    slipped = apply_paper_slippage(case.soft_odds)
    stake = _DEFAULT_STAKE_TL
    if case.result == "WON":
        return round(stake * (slipped - 1.0), 2)
    return round(-stake, 2)


def _update_brier(metrics: StrategyMetrics, predicted_prob: float | None, outcome: float) -> None:
    if predicted_prob is None or not math.isfinite(predicted_prob):
        return
    metrics.brier_sum += (float(predicted_prob) - outcome) ** 2
    metrics.brier_count += 1


def _record_bet(metrics: StrategyMetrics, case: TheoryBacktestCase, predicted_prob: float | None) -> None:
    metrics.bets += 1
    metrics.total_staked += _DEFAULT_STAKE_TL
    profit = _bet_profit(case)
    metrics.total_profit += profit
    outcome = 1.0 if case.result == "WON" else 0.0
    _update_brier(metrics, predicted_prob, outcome)
    if case.result == "WON":
        metrics.wins += 1
    else:
        metrics.losses += 1


def evaluate_fusion_gate(
    ev_only: StrategyMetrics,
    ev_context: StrategyMetrics,
) -> tuple[bool, str, float, float]:
    fp_base = ev_only.false_positive_rate
    fp_ctx = ev_context.false_positive_rate
    fp_reduction = 0.0
    if fp_base > 0.0:
        fp_reduction = round((fp_base - fp_ctx) / fp_base, 6)

    roi_delta = round(ev_context.roi - ev_only.roi, 6)

    if fp_reduction >= _FUSION_FP_REDUCTION_TARGET:
        return True, f"FP azaltma %{fp_reduction * 100:.2f} >= hedef %{_FUSION_FP_REDUCTION_TARGET * 100:.0f}", fp_reduction, roi_delta
    if roi_delta >= _FUSION_ROI_IMPROVEMENT_TARGET:
        return True, f"ROI delta {roi_delta:.4f} >= hedef {_FUSION_ROI_IMPROVEMENT_TARGET:.4f}", fp_reduction, roi_delta
    return (
        False,
        (
            f"FP azaltma %{fp_reduction * 100:.2f} ve ROI delta {roi_delta:.4f} "
            f"hedefin altinda; context yalnizca bilgi hattinda kalmali"
        ),
        fp_reduction,
        roi_delta,
    )


def run_theory_backtest(cases: list[TheoryBacktestCase]) -> TheoryBacktestReport:
    ev_only = StrategyMetrics(strategy="ev_only")
    ev_context = StrategyMetrics(strategy="ev_context")
    walk_forward: list[dict[str, Any]] = []

    for case in cases:
        features = _extract_features(case)
        bet_ev_only, ev = _should_bet_ev_only(case)
        bet_ev_context, _ = _should_bet_ev_context(case, features)

        if bet_ev_only and case.result == "LOST":
            ev_only.ev_positive_cases += 1
            ev_only.false_positives += 1
        elif bet_ev_only:
            ev_only.ev_positive_cases += 1

        if bet_ev_context and case.result == "LOST":
            ev_context.ev_positive_cases += 1
            ev_context.false_positives += 1
        elif bet_ev_context:
            ev_context.ev_positive_cases += 1

        sharp_prob = 1.0 / case.sharp_odds if case.sharp_odds > 1.0 else None
        context_prob = None
        if features is not None:
            try:
                from core.context_score import score_context_for_market

                context_prob = score_context_for_market(
                    features,
                    case.market,
                    sharp_odds=case.sharp_odds,
                ).context_implied_prob
            except Exception:
                context_prob = None

        if bet_ev_only:
            _record_bet(ev_only, case, sharp_prob)
        else:
            ev_only.skipped += 1

        if bet_ev_context:
            _record_bet(ev_context, case, context_prob)
        else:
            ev_context.skipped += 1

        walk_forward.append(
            {
                "case_id": case.case_id,
                "commence_time": case.commence_time,
                "ev": round(ev, 6),
                "bet_ev_only": bet_ev_only,
                "bet_ev_context": bet_ev_context,
                "result": case.result,
                "cumulative_roi_ev_only": ev_only.roi,
                "cumulative_roi_ev_context": ev_context.roi,
            }
        )

    gate_pass, gate_reason, fp_reduction, roi_delta = evaluate_fusion_gate(ev_only, ev_context)
    return TheoryBacktestReport(
        case_count=len(cases),
        ev_only=ev_only,
        ev_context=ev_context,
        fusion_fp_reduction=fp_reduction,
        fusion_roi_delta=roi_delta,
        fusion_gate_pass=gate_pass,
        fusion_gate_reason=gate_reason,
        walk_forward=walk_forward,
    )


FUSION_FP_REDUCTION_TARGET = _FUSION_FP_REDUCTION_TARGET
FUSION_ROI_IMPROVEMENT_TARGET = _FUSION_ROI_IMPROVEMENT_TARGET
