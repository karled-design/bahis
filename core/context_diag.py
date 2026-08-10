from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from core.context_fusion import (
    is_context_fusion_filter_enabled,
    passes_context_fusion_for_action,
    resolve_context_features_from_match,
)
from core.context_score import score_context_for_market, score_home_bias
from database.context_cache import is_cacheable_real_match

__all__ = (
    "ContextDiagReport",
    "ContextMatchDiag",
    "ContextMarketDiag",
    "build_context_diag_report",
    "dedupe_matches_by_event",
    "filter_matches_for_date",
    "format_context_diag_report",
    "group_matches_by_event",
    "parse_commence_date",
    "resolve_reference_date",
)

_MARKETS = ("MS1", "X", "MS2")


@dataclass(frozen=True)
class ContextMarketDiag:
    market: str
    sharp_odds: float | None
    soft_odds: float | None
    final_market_score: float | None
    home_bias_score: float | None
    overconfidence: bool | None
    fusion_applied: bool
    fusion_passed: bool
    fusion_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "sharp_odds": self.sharp_odds,
            "soft_odds": self.soft_odds,
            "final_market_score": self.final_market_score,
            "home_bias_score": self.home_bias_score,
            "overconfidence": self.overconfidence,
            "fusion_applied": self.fusion_applied,
            "fusion_passed": self.fusion_passed,
            "fusion_reason": self.fusion_reason,
        }


@dataclass(frozen=True)
class ContextMatchDiag:
    event_id: str
    match_name: str
    league_name: str
    sport_key: str
    commence_time: str
    has_context: bool
    data_completeness: float | None
    home_bias_score: float | None
    markets: tuple[ContextMarketDiag, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "match_name": self.match_name,
            "league_name": self.league_name,
            "sport_key": self.sport_key,
            "commence_time": self.commence_time,
            "has_context": self.has_context,
            "data_completeness": self.data_completeness,
            "home_bias_score": self.home_bias_score,
            "markets": [item.as_dict() for item in self.markets],
        }


@dataclass(frozen=True)
class ContextDiagReport:
    reference_date: str
    fusion_mode: str
    input_rows: int
    event_groups: int
    skipped_virtual: int
    skipped_no_date: int
    skipped_wrong_date: int
    with_context: int
    without_context: int
    rows: tuple[ContextMatchDiag, ...] = field(default_factory=tuple)
    cache_stats: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_date": self.reference_date,
            "fusion_mode": self.fusion_mode,
            "input_rows": self.input_rows,
            "event_groups": self.event_groups,
            "skipped_virtual": self.skipped_virtual,
            "skipped_no_date": self.skipped_no_date,
            "skipped_wrong_date": self.skipped_wrong_date,
            "with_context": self.with_context,
            "without_context": self.without_context,
            "cache_stats": dict(self.cache_stats),
            "rows": [row.as_dict() for row in self.rows],
        }


def resolve_reference_date(value: str | None = None) -> date:
    if value is None or not str(value).strip():
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Gecersiz tarih formati (YYYY-MM-DD bekleniyor): {value}") from exc


def parse_commence_date(match: dict[str, Any]) -> date | None:
    raw = str(match.get("commence_time", "")).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def filter_matches_for_date(
    matches: list[dict[str, Any]],
    *,
    reference_date: date,
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Gercek maclari hedef tarihe gore filtreler; (rows, virtual_skip, no_date, wrong_date)."""
    kept: list[dict[str, Any]] = []
    skipped_virtual = 0
    skipped_no_date = 0
    skipped_wrong_date = 0

    for match in matches:
        if not isinstance(match, dict):
            continue
        if not is_cacheable_real_match(match):
            skipped_virtual += 1
            continue
        commence_date = parse_commence_date(match)
        if commence_date is None:
            skipped_no_date += 1
            continue
        if commence_date != reference_date:
            skipped_wrong_date += 1
            continue
        kept.append(match)

    return kept, skipped_virtual, skipped_no_date, skipped_wrong_date


def group_matches_by_event(matches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        event_id = str(match.get("event_id", "")).strip()
        sport_key = str(match.get("sport_key", "")).strip()
        if not event_id or not sport_key:
            key = f"anon:{str(match.get('match_name', '')).strip()}:{sport_key}"
        else:
            key = f"{sport_key}:{event_id}"
        groups.setdefault(key, []).append(match)
    return groups


def _pick_canonical_match(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if isinstance(row.get("context_bundle"), dict):
            return dict(row)
    for row in rows:
        market = str(row.get("market", "")).strip().upper()
        if market == "MS1":
            return dict(row)
    return dict(rows[0])


def _collect_market_odds(rows: list[dict[str, Any]]) -> dict[str, tuple[float | None, float | None]]:
    collected: dict[str, tuple[float | None, float | None]] = {}
    for row in rows:
        market = str(row.get("market", "")).strip().upper()
        if market not in _MARKETS:
            continue
        sharp = row.get("sharp_odds")
        soft = row.get("soft_odds")
        sharp_odds = float(sharp) if isinstance(sharp, (int, float)) and float(sharp) > 1.0 else None
        soft_odds = float(soft) if isinstance(soft, (int, float)) and float(soft) > 1.0 else None
        collected[market] = (sharp_odds, soft_odds)
    return collected


def _resolve_league_name(match: dict[str, Any]) -> str:
    for key in ("league_name", "comp_name", "lig", "league", "competition"):
        value = match.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Bilinmiyor"


def _build_market_diag(
    match: dict[str, Any],
    *,
    market: str,
    sharp_odds: float | None,
    soft_odds: float | None,
    home_bias_score: float | None,
) -> ContextMarketDiag:
    features = resolve_context_features_from_match(match)
    if features is None:
        return ContextMarketDiag(
            market=market,
            sharp_odds=sharp_odds,
            soft_odds=soft_odds,
            final_market_score=None,
            home_bias_score=home_bias_score,
            overconfidence=None,
            fusion_applied=False,
            fusion_passed=True,
            fusion_reason="baglam_verisi_yok",
        )

    try:
        score = score_context_for_market(features, market, sharp_odds=sharp_odds)
    except ValueError:
        return ContextMarketDiag(
            market=market,
            sharp_odds=sharp_odds,
            soft_odds=soft_odds,
            final_market_score=None,
            home_bias_score=home_bias_score,
            overconfidence=None,
            fusion_applied=False,
            fusion_passed=True,
            fusion_reason="skor_hesaplanamadi",
        )

    fusion = passes_context_fusion_for_action(
        match,
        market=market,
        sharp_odds=sharp_odds or 2.0,
        soft_odds=soft_odds or 2.0,
        consensus_books=int(match.get("consensus_books", 0) or 0),
    )
    return ContextMarketDiag(
        market=market,
        sharp_odds=sharp_odds,
        soft_odds=soft_odds,
        final_market_score=score.final_market_score,
        home_bias_score=score.home_bias_score,
        overconfidence=score.overconfidence,
        fusion_applied=fusion.applied,
        fusion_passed=fusion.passed,
        fusion_reason=fusion.reason,
    )


def build_match_context_diag(rows: list[dict[str, Any]]) -> ContextMatchDiag:
    canonical = _pick_canonical_match(rows)
    market_odds = _collect_market_odds(rows)
    features = resolve_context_features_from_match(canonical)
    home_bias_score: float | None = None
    data_completeness: float | None = None
    if features is not None:
        home_bias_score, _ = score_home_bias(features)
        data_completeness = float(features.data_completeness)

    markets: list[ContextMarketDiag] = []
    for market in _MARKETS:
        sharp_odds, soft_odds = market_odds.get(market, (None, None))
        markets.append(
            _build_market_diag(
                canonical,
                market=market,
                sharp_odds=sharp_odds,
                soft_odds=soft_odds,
                home_bias_score=home_bias_score,
            )
        )

    return ContextMatchDiag(
        event_id=str(canonical.get("event_id", "")).strip(),
        match_name=str(canonical.get("match_name", "")).strip(),
        league_name=_resolve_league_name(canonical),
        sport_key=str(canonical.get("sport_key", "")).strip(),
        commence_time=str(canonical.get("commence_time", "")).strip(),
        has_context=isinstance(canonical.get("context_bundle"), dict),
        data_completeness=data_completeness,
        home_bias_score=home_bias_score,
        markets=tuple(markets),
    )


def dedupe_matches_by_event(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_pick_canonical_match(rows) for rows in group_matches_by_event(matches).values()]


def build_context_diag_report(
    matches: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
    cache_stats: dict[str, int] | None = None,
) -> ContextDiagReport:
    ref = reference_date or datetime.now(timezone.utc).date()
    filtered, skipped_virtual, skipped_no_date, skipped_wrong_date = filter_matches_for_date(
        matches,
        reference_date=ref,
    )
    groups = group_matches_by_event(filtered)
    rows = tuple(build_match_context_diag(group_rows) for group_rows in groups.values())
    with_context = sum(1 for row in rows if row.has_context)
    fusion_mode = "filter" if is_context_fusion_filter_enabled() else "off"

    return ContextDiagReport(
        reference_date=ref.isoformat(),
        fusion_mode=fusion_mode,
        input_rows=len(matches),
        event_groups=len(rows),
        skipped_virtual=skipped_virtual,
        skipped_no_date=skipped_no_date,
        skipped_wrong_date=skipped_wrong_date,
        with_context=with_context,
        without_context=len(rows) - with_context,
        rows=rows,
        cache_stats=dict(cache_stats or {}),
    )


def _format_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}"


def _format_odds(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def format_context_diag_report(report: ContextDiagReport) -> str:
    lines = [
        "=== SQE-V1 Context Diag ===",
        f"tarih={report.reference_date} | fusion={report.fusion_mode}",
        (
            f"girdi={report.input_rows} | mac_grubu={report.event_groups} | "
            f"baglam_var={report.with_context} | baglam_yok={report.without_context}"
        ),
        (
            f"atlanan: sanal={report.skipped_virtual} | tarih_yok={report.skipped_no_date} | "
            f"baska_gun={report.skipped_wrong_date}"
        ),
    ]
    if report.cache_stats:
        cache_line = " | ".join(f"{key}={value}" for key, value in sorted(report.cache_stats.items()))
        lines.append(f"cache: {cache_line}")

    if not report.rows:
        lines.append("")
        lines.append("Bugun icin raporlanacak gercek mac bulunamadi.")
        lines.append("NOT: Telegram yok; yalnizca terminal teshis raporu.")
        return "\n".join(lines)

    lines.append("")
    for row in report.rows:
        lines.append(
            f"--- {row.match_name} | {row.league_name} | {row.commence_time or 'tarih_yok'} ---"
        )
        lines.append(
            f"  event_id={row.event_id or '-'} | sport={row.sport_key or '-'} | "
            f"baglam={'EVET' if row.has_context else 'HAYIR'} | "
            f"tamlik={row.data_completeness if row.data_completeness is not None else '-'} | "
            f"home_bias={_format_score(row.home_bias_score)}"
        )
        for market in row.markets:
            fusion_flag = "OK" if market.fusion_passed else "ELENDI"
            if not market.fusion_applied:
                fusion_flag = "N/A"
            lines.append(
                f"  {market.market}: skor={_format_score(market.final_market_score)} | "
                f"sharp={_format_odds(market.sharp_odds)} soft={_format_odds(market.soft_odds)} | "
                f"overconf={'EVET' if market.overconfidence else 'HAYIR' if market.overconfidence is not None else '-'} | "
                f"fusion={fusion_flag} ({market.fusion_reason})"
            )

    lines.append("")
    lines.append("NOT: Telegram yok; yalnizca terminal teshis raporu.")
    return "\n".join(lines)
