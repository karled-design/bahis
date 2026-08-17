from __future__ import annotations

import logging
import signal
import socket
import sys
import threading
import time
from types import FrameType
from datetime import datetime, timezone
from typing import TypedDict

from auto_settler import run_auto_settler_loop, run_settlement_pass
from config.settings import (
    COOLDOWN_ODDS_CHANGE_BYPASS_PCT,
    EXECUTION_BOOK,
    MAX_EV_THRESHOLD,
    PANEL_PORT,
    SCAN_INTERVAL_SECONDS,
    resolve_scan_interval_seconds,
)
from core.hero_mode import bootstrap_hero_mode, build_hero_panel_payload, is_hero_mode_enabled
from core.hero_measurement import record_hero_measurement_scan_day
from core.hero_profile import bootstrap_hero_profile, get_active_hero_profile_values
from core.hero_daily_guard import (
    HeroDailyStatus,
    build_hero_daily_limit_message,
    build_hero_daily_status,
    mark_hero_daily_notice_sent,
    maybe_should_send_hero_daily_notice,
    record_hero_alert_sent,
)
from database.db_manager import (
    get_db_path,
    get_latest_bakiye,
    get_performance_stats,
    get_pilot_ab_summary,
    init_db,
    is_operator_budget_configured,
    record_pilot_ab_scan,
)
from core.notification_quality_gate import evaluate_notification_quality
from core.notify_frequency_profile import bootstrap_notify_frequency_profile
from core.operator_risk_settings import (
    detect_active_risk_preset,
    get_action_ev_threshold,
    get_operator_risk_per_trade,
    get_soft_odds_max,
    get_soft_odds_min,
    get_watch_ev_threshold,
)
from core.experimental_features import (
    bootstrap_experimental_mode,
    format_experimental_status_line,
    is_feature_enabled,
)
from core.beginner_alert import build_beginner_alert_message, build_hero_alert_message
from core.clv_engine import calculate_stake_amount
from core.daily_digest import maybe_send_daily_digest
from core.kickoff_reminder import maybe_send_kickoff_reminders
from core.live_alert_settings import is_live_alerts_enabled, match_has_started
from core.match_filters import (
    SCAN_CYCLE,
    build_notification_key,
    build_stable_match_id,
    is_feed_pair_synchronized,
    is_feed_timestamp_fresh,
    is_virtual_match_text,
    reset_scan_cycle,
)
from core.passion_engine import get_active_min_ev_threshold, resolve_match_ev
from core.scan_pipeline import ScanCandidate, evaluate_matches
from core.market_catalog import FAMILY_FIRST_HALF, market_family
from core.first_half_shadow import record_first_half_shadow
from core.measurement_mode import (
    is_measurement_mode_enabled,
    record_scan_funnel,
    record_signal as record_measurement_signal,
)
from core.olcum_nabzi import maybe_send_measurement_pulse, record_cycle as record_pulse_cycle
from core.fixture_notify_guard import record_fixture_telegram_sent
from notifiers import telegram_worker
from notifiers.telegram_reset import DEFAULT_PANEL_PORT
from scrapers.live_feed_gateway import get_unified_live_data
from scrapers.context_feed import enrich_match_feed_safely, get_last_context_feed_diag
from scrapers.news_feed import attach_experimental_news_to_matches
from tools.snapshot_store import save_feed_snapshot
from ui.web_server import (
    SISTEM_DURUMU,
    start_web_server,
    stop_web_server,
    update_sistem_durumu,
)


logger = logging.getLogger("sqe.main")
_AUTO_SETTLER_JOIN_TIMEOUT_SECONDS = 5.0
_NESINE_MIN_STAKE_TL = 50.0
_SOFT_ODDS_TOLERANCE_MIN = 1.15
_SOFT_ODDS_TOLERANCE_MAX = 7.0
_FIXTURE_ALERT_COOLDOWN_SECONDS = 4 * 3600


class _NotifiedEntry(TypedDict):
    notified_at: float
    soft_odds: float


class _QualifyingCandidate(TypedDict):
    notify_key: str
    match_name: str
    market: str
    match_id: str
    alert_message: str
    stake: float
    soft_odds: float
    sharp_odds: float
    ev_percent: str
    tier: str
    ev: float
    sport_key: str
    match_record: dict
    hero_confidence: float | None


class _ScanCycleStats(TypedDict):
    birlesik: int
    efutbol: int
    stale: int
    tolerans: int
    pasif_ev: int
    absurd_ev: int
    suspicious_match: int
    context_filter: int
    experimental_checked: int
    experimental_would_filter: int
    zayif_referans: int
    context_bundle: int
    context_api_req: int
    watch: int
    action: int
    high: int
    aday: int
    telegram: int
    izle: int
    notify_gate: int
    cooldown: int
    hero_pass: int
    hero_reject_low_confidence: int
    hero_reject_context: int
    hero_reject_market: int
    hero_reject_consensus: int
    hero_reject_odds_band: int
    hero_daily_limit: int
    hero_loss_stop: int
    hero_weekly_stop: int


def _new_scan_cycle_stats() -> _ScanCycleStats:
    return {
        "birlesik": 0,
        "efutbol": 0,
        "stale": 0,
        "tolerans": 0,
        "pasif_ev": 0,
        "absurd_ev": 0,
        "suspicious_match": 0,
        "context_filter": 0,
        "experimental_checked": 0,
        "experimental_would_filter": 0,
        "zayif_referans": 0,
        "context_bundle": 0,
        "context_api_req": 0,
        "watch": 0,
        "action": 0,
        "high": 0,
        "aday": 0,
        "telegram": 0,
        "izle": 0,
        "notify_gate": 0,
        "cooldown": 0,
        "hero_pass": 0,
        "hero_reject_low_confidence": 0,
        "hero_reject_context": 0,
        "hero_reject_market": 0,
        "hero_reject_consensus": 0,
        "hero_reject_odds_band": 0,
        "hero_daily_limit": 0,
        "hero_loss_stop": 0,
        "hero_weekly_stop": 0,
    }


def _format_scan_cycle_summary(stats: _ScanCycleStats) -> str:
    return (
        f"[SQE-V1] Tarama Ozeti | birlesik={stats['birlesik']} | "
        f"efutbol={stats['efutbol']} | stale={stats['stale']} | "
        f"tolerans={stats['tolerans']} | pasif_ev={stats['pasif_ev']} | "
        f"absurd_ev={stats['absurd_ev']} | suspicious_match={stats['suspicious_match']} | "
        f"context_filter={stats['context_filter']} | "
        f"experimental_would_filter={stats.get('experimental_would_filter', 0)} | "
        f"zayif_referans={stats.get('zayif_referans', 0)} | "
        f"context_bundle={stats['context_bundle']} | context_api_req={stats['context_api_req']} | "
        f"watch={stats['watch']} | "
        f"action={stats['action']} | high={stats['high']} | "
        f"aday={stats['aday']} | telegram={stats['telegram']} | "
        f"izle={stats['izle']} | notify_gate={stats['notify_gate']} | cooldown={stats['cooldown']} | "
        f"hero_pass={stats.get('hero_pass', 0)} | hero_reject_guven={stats.get('hero_reject_low_confidence', 0)} | "
        f"hero_reject_form={stats.get('hero_reject_context', 0)} | hero_reject_piyasa={stats.get('hero_reject_market', 0)} | "
        f"hero_daily_limit={stats.get('hero_daily_limit', 0)} | hero_loss_stop={stats.get('hero_loss_stop', 0)} | "
        f"hero_weekly_stop={stats.get('hero_weekly_stop', 0)}"
    )


def _bootstrap_operator_bankroll() -> float:
    init_db()
    if not is_operator_budget_configured():
        return 0.0
    return get_latest_bakiye()


def _is_soft_odds_within_tolerance(soft_odds: float) -> bool:
    return _SOFT_ODDS_TOLERANCE_MIN <= soft_odds <= _SOFT_ODDS_TOLERANCE_MAX


def _format_qc_status_line() -> str:
    min_ev_pct = get_action_ev_threshold() * 100.0
    watch_pct = get_watch_ev_threshold() * 100.0
    max_ev_pct = MAX_EV_THRESHOLD * 100.0
    mode = "CANLI"
    return (
        f"[SQE-V1] QC Aktif ({mode}) | IZLE>=%{watch_pct:.2f} | "
        f"ACTION>=%{min_ev_pct:.2f} | EV tavan=%{max_ev_pct:.2f} | "
        f"Oran={get_soft_odds_min():.2f}-{get_soft_odds_max():.1f} | "
        f"tarama={SCAN_INTERVAL_SECONDS}sn | execution={EXECUTION_BOOK}"
    )


def _is_match_fresh_for_cycle(match: dict) -> bool:
    cycle_started_at = SCAN_CYCLE.started_at
    if cycle_started_at <= 0.0:
        return False

    soft_observed_at = float(match.get("soft_observed_at", match.get("observed_at", 0.0)))
    sharp_observed_at = float(match.get("sharp_observed_at", match.get("observed_at", 0.0)))
    if soft_observed_at < cycle_started_at or sharp_observed_at < cycle_started_at:
        return False
    if not is_feed_timestamp_fresh(soft_observed_at):
        return False
    if not is_feed_timestamp_fresh(sharp_observed_at):
        return False
    return is_feed_pair_synchronized(soft_observed_at, sharp_observed_at)


def _prune_notified_registry(
    notified_registry: dict[str, _NotifiedEntry],
    now: float | None = None,
) -> None:
    current_time = time.time() if now is None else float(now)
    expired_keys = [
        notify_key
        for notify_key, entry in notified_registry.items()
        if current_time - entry["notified_at"] >= _FIXTURE_ALERT_COOLDOWN_SECONDS
    ]
    for notify_key in expired_keys:
        del notified_registry[notify_key]


def _is_on_fixture_cooldown(
    notify_key: str,
    notified_registry: dict[str, _NotifiedEntry],
    new_soft_odds: float,
    now: float | None = None,
) -> bool:
    entry = notified_registry.get(notify_key)
    if entry is None:
        return False
    current_time = time.time() if now is None else float(now)
    if (current_time - entry["notified_at"]) >= _FIXTURE_ALERT_COOLDOWN_SECONDS:
        return False

    previous_soft_odds = float(entry.get("soft_odds", 0.0))
    if previous_soft_odds > 0.0:
        odds_change_ratio = abs(new_soft_odds - previous_soft_odds) / previous_soft_odds
        if odds_change_ratio >= COOLDOWN_ODDS_CHANGE_BYPASS_PCT:
            return False

    return True


def _reset_scan_cycle_state(
    notified_registry: dict[str, _NotifiedEntry],
    current_kasa: float,
) -> str:
    _prune_notified_registry(notified_registry)
    cycle_id = reset_scan_cycle()
    telegram_worker.begin_scan_cycle(cycle_id)
    update_sistem_durumu(
        active_match_count=0,
        alarm_candidate_count=0,
        matches=[],
        total_kasa=current_kasa,
        is_live=True,
    )
    return cycle_id


def _is_hero_play_candidate(candidate: _QualifyingCandidate) -> bool:
    return (
        is_hero_mode_enabled()
        and candidate.get("hero_confidence") is not None
        and candidate.get("tier") in {"ACTION", "HIGH"}
    )


def _sort_qualifying_for_hero(
    candidates: list[_QualifyingCandidate],
) -> list[_QualifyingCandidate]:
    if not is_hero_mode_enabled():
        return candidates

    def _sort_key(item: _QualifyingCandidate) -> tuple[int, float]:
        hero = item.get("hero_confidence")
        if hero is None:
            return (1, 0.0)
        return (0, -float(hero))

    return sorted(candidates, key=_sort_key)


def _maybe_send_hero_daily_notice(status: HeroDailyStatus) -> None:
    notice_kind = maybe_should_send_hero_daily_notice(status)  # type: ignore[arg-type]
    if notice_kind is None:
        return
    message = build_hero_daily_limit_message(status, kind=notice_kind)  # type: ignore[arg-type]
    if telegram_worker.send_hero_daily_notice(message):
        mark_hero_daily_notice_sent(notice_kind)


def _dispatch_scan_notifications(
    cycle_id: str,
    qualifying_candidates: list[_QualifyingCandidate],
    notified_registry: dict[str, _NotifiedEntry],
    cycle_stats: _ScanCycleStats | None = None,
) -> None:
    if not qualifying_candidates:
        telegram_worker.send_scan_empty_report_for_cycle(cycle_id, cycle_stats)
        print("[SQE-V1] Tarama | kriter_uygun_mac=yok | telegram=sessiz (detay panelde)")
        return

    notify_keys = {item["notify_key"] for item in qualifying_candidates}
    telegram_worker.arm_qualifying_alerts(cycle_id, notify_keys)

    ordered_candidates = _sort_qualifying_for_hero(qualifying_candidates)

    # Canli bildirimler kapaliysa, maci baslamis (in-play) firsatlari Telegram'a
    # yollamayiz; mac oncesi sinyaller normal akar. Ayar diske kaydedildiginden
    # her tarama basinda bir kez okunur.
    live_alerts_on = is_live_alerts_enabled()

    for candidate in ordered_candidates:
        notify_key = candidate["notify_key"]
        tier = candidate["tier"]
        if not live_alerts_on and match_has_started(candidate.get("match_record") or {}):
            print(
                f"[SQE-V1] Bildirim atlandi | mac={candidate['match_name']} | "
                f"market={candidate['market']} | tier={tier} | neden=canli_bildirim_kapali"
            )
            continue
        gate = evaluate_notification_quality(
            candidate.get("match_record") or {},
            notify_key=notify_key,
            mac_adi=candidate["match_name"],
            market=candidate["market"],
            soft_odds=candidate["soft_odds"],
            sharp_odds=float(candidate.get("sharp_odds", candidate["soft_odds"])),
            tier=tier,
            ev=float(candidate.get("ev", 0.0)),
            odds_change_bypass_pct=COOLDOWN_ODDS_CHANGE_BYPASS_PCT,
        )
        if not gate.allow:
            if cycle_stats is not None:
                cycle_stats["notify_gate"] += 1
            print(
                f"[SQE-V1] Bildirim atlandi | mac={candidate['match_name']} | "
                f"market={candidate['market']} | tier={tier} | neden={gate.reason}"
            )
            continue
        if _is_on_fixture_cooldown(notify_key, notified_registry, candidate["soft_odds"]):
            if cycle_stats is not None:
                cycle_stats["cooldown"] += 1
            print(
                f"[SQE-V1] Bildirim atlandi | mac={candidate['match_name']} | "
                f"market={candidate['market']} | tier={tier} | neden=fixture_cooldown_4saat"
            )
            continue

        if _is_hero_play_candidate(candidate):
            daily_status = build_hero_daily_status()
            if not daily_status["can_send"]:
                block_reason = daily_status["block_reason"]
                if cycle_stats is not None:
                    if block_reason == "weekly_stop":
                        cycle_stats["hero_weekly_stop"] += 1
                    elif block_reason == "loss_stop":
                        cycle_stats["hero_loss_stop"] += 1
                    else:
                        cycle_stats["hero_daily_limit"] += 1
                _maybe_send_hero_daily_notice(daily_status)
                print(
                    f"[SQE-V1] Bildirim atlandi | mac={candidate['match_name']} | "
                    f"market={candidate['market']} | tier={tier} | neden=hero_{block_reason}"
                )
                continue

        # Olcum modu: mesaj gider ama "Oyna" dugmesi cikmaz (match_id/stake yok),
        # boylece kupon acilamaz ve bakiye degismez.
        measuring = is_measurement_mode_enabled()
        is_watch = tier == "WATCH"
        no_play = is_watch or measuring
        match_record = candidate.get("match_record") if isinstance(candidate.get("match_record"), dict) else {}
        sent = telegram_worker.send_alert(
            message=candidate["alert_message"],
            match_id=None if no_play else candidate["match_id"],
            stake=None if no_play else candidate["stake"],
            soft_odds=candidate["soft_odds"],
            mac_adi=candidate["match_name"],
            market=candidate["market"],
            sport_key=candidate["sport_key"],
            event_id=str(match_record.get("event_id", "")).strip() or None,
            commence_time=str(match_record.get("commence_time", "")).strip() or None,
            cycle_id=cycle_id,
            notify_key=notify_key,
            ev=candidate["ev"],
        )
        if sent:
            if measuring:
                record_measurement_signal(
                    mac_adi=candidate["match_name"],
                    market=candidate["market"],
                    tier=tier,
                    soft_odds=float(candidate["soft_odds"]),
                    sharp_odds=float(candidate.get("sharp_odds", 0.0)),
                    ev=float(candidate.get("ev", 0.0)),
                    cycle_id=cycle_id or "",
                    league_name=str(match_record.get("league_name", "")),
                    sport_key=candidate["sport_key"],
                    event_id=str(match_record.get("event_id", "")),
                    commence_time=str(match_record.get("commence_time", "")),
                    consensus_source=str(match_record.get("consensus_source", "")),
                    consensus_books=int(match_record.get("consensus_books", 0) or 0),
                    fair_probability=(
                        float(match_record["fair_probability"])
                        if isinstance(match_record.get("fair_probability"), (int, float))
                        and not isinstance(match_record.get("fair_probability"), bool)
                        else None
                    ),
                    stake=float(candidate.get("stake", 0.0)),
                )
            notified_registry[notify_key] = {
                "notified_at": time.time(),
                "soft_odds": candidate["soft_odds"],
            }
            record_fixture_telegram_sent(
                notify_key,
                candidate["match_name"],
                candidate["market"],
                candidate["soft_odds"],
            )
            if cycle_stats is not None:
                if is_watch:
                    cycle_stats["izle"] += 1
                else:
                    cycle_stats["telegram"] += 1
            if _is_hero_play_candidate(candidate):
                updated_status = record_hero_alert_sent()
                if not updated_status["can_send"]:
                    _maybe_send_hero_daily_notice(updated_status)
            channel = "IZLE" if is_watch else "ALARM"
            metric = (
                f"Guven=%{round(float(candidate['hero_confidence'] or 0.0) * 100.0, 1)}"
                if is_hero_mode_enabled() and candidate.get("hero_confidence") is not None
                else f"EV=%{candidate['ev_percent']}"
            )
            print(
                f"[SQE-V1] {channel} | mac={candidate['match_name']} | market={candidate['market']} | "
                f"tier={tier} | match_id={candidate['match_id']} | {metric} | "
                f"stake={candidate['stake']} TL | telegram=GONDERILDI"
            )
        else:
            print(
                f"[SQE-V1] Alarm basarisiz | mac={candidate['match_name']} | "
                f"match_id={candidate['match_id']} | telegram=BASARISIZ",
                file=sys.stderr,
            )

    _ = cycle_stats


def _configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _format_ev_percent(ev: float) -> str:
    return f"{ev * 100.0:.2f}"


def _build_match_id(match_name: str, market: str) -> str:
    return build_stable_match_id(match_name, market)


def _resolve_league_name(match: dict) -> str:
    for key in ("league_name", "comp_name", "lig", "league", "competition"):
        try:
            value = match.get(key)
        except (AttributeError, TypeError):
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Bilinmiyor"


def _split_match_teams(match_name: str) -> tuple[str, str]:
    normalized = match_name.strip() if isinstance(match_name, str) else ""
    if " - " in normalized:
        home_team, away_team = normalized.split(" - ", 1)
        home = home_team.strip() or "Ev Sahibi"
        away = away_team.strip() or "Deplasman"
        return home, away
    if normalized:
        return normalized, "Deplasman"
    return "Ev Sahibi", "Deplasman"


def _format_scan_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _build_tiered_alert(
    match: dict,
    market: str,
    stake: float,
    *,
    tier: str,
    current_kasa: float,
    sharp_odds: float,
    soft_odds: float,
    ev_percent: str,
    scan_time: str,
    consensus_books: int,
    hero_confidence: float | None = None,
    cycle_id: str = "",
    reference_at: float | None = None,
) -> str:
    league_name = _resolve_league_name(match)
    match_name = str(match.get("match_name", "")) if isinstance(match, dict) else ""
    home_team, away_team = _split_match_teams(match_name)
    match_payload = dict(match) if isinstance(match, dict) else {}
    match_payload.setdefault("consensus_books", consensus_books)
    match_payload.setdefault("sharp_odds", sharp_odds)
    _ = consensus_books

    if is_hero_mode_enabled() and hero_confidence is not None and tier in {"ACTION", "HIGH"}:
        return build_hero_alert_message(
            match_payload,
            home_team=home_team,
            away_team=away_team,
            league_name=league_name,
            market=market,
            stake=stake,
            soft_odds=soft_odds,
            sharp_odds=sharp_odds,
            hero_confidence=float(hero_confidence),
            scan_time=scan_time,
            current_kasa=current_kasa,
            risk_per_trade=float(get_active_hero_profile_values()["risk_per_trade"]),
            cycle_id=cycle_id,
            reference_at=reference_at,
        )

    return build_beginner_alert_message(
        match_payload,
        home_team=home_team,
        away_team=away_team,
        league_name=league_name,
        market=market,
        tier=tier,
        stake=stake,
        soft_odds=soft_odds,
        sharp_odds=sharp_odds,
        scan_time=scan_time,
        current_kasa=current_kasa,
        risk_per_trade=get_operator_risk_per_trade(),
        ev_percent=ev_percent,
        cycle_id=cycle_id,
        reference_at=reference_at,
    )


def _build_ui_matches(matches: list) -> list[dict[str, str | float]]:
    ui_rows: list[dict[str, str | float]] = []
    for match in matches:
        sharp_odds = float(match["sharp_odds"])
        soft_odds = float(match["soft_odds"])
        ui_rows.append(
            {
                "match_name": str(match["match_name"]),
                "market": str(match["market"]),
                "sharp_odds": sharp_odds,
                "soft_odds": soft_odds,
                "ev": resolve_match_ev(match, sharp_odds, soft_odds),
                "has_context": bool(match.get("context_bundle")),
            }
        )
    return ui_rows


def _sync_panel_state(
    matches: list,
    is_live: bool,
    current_kasa: float,
    *,
    alarm_candidate_count: int = 0,
) -> None:
    update_sistem_durumu(
        total_kasa=current_kasa,
        risk_per_trade=get_operator_risk_per_trade(),
        active_match_count=len(matches),
        alarm_candidate_count=alarm_candidate_count,
        ev_threshold=get_action_ev_threshold(),
        is_live=is_live,
        matches=_build_ui_matches(matches),
    )


def _graceful_shutdown(
    auto_settler_stop: threading.Event,
    auto_settler_thread: threading.Thread | None,
) -> None:
    auto_settler_stop.set()
    if auto_settler_thread is not None and auto_settler_thread.is_alive():
        auto_settler_thread.join(timeout=_AUTO_SETTLER_JOIN_TIMEOUT_SECONDS)
        if auto_settler_thread.is_alive():
            logger.warning(
                "Auto-Settler thread %s saniye icinde kapanmadi; daemon olarak sonlandirilacak.",
                _AUTO_SETTLER_JOIN_TIMEOUT_SECONDS,
            )
        else:
            logger.info("Sistem: Auto-Settler otonom servisi guvenli sekilde durduruldu.")

    try:
        update_sistem_durumu(
            is_live=False,
            scan_enabled=False,
            active_match_count=0,
            matches=[],
            total_kasa=get_latest_bakiye(),
        )
    except Exception as exc:
        logger.error("Panel durumu guncellenemedi | %s", exc)

    try:
        telegram_worker.stop_telegram_listener()
    except Exception as exc:
        logger.error("Telegram dinleyici kapatilamadi | %s", exc)

    try:
        stop_web_server()
    except Exception as exc:
        logger.error("Web sunucu kapatilamadi | %s", exc)


def _pipeline_candidate_to_qualifying(
    candidate: ScanCandidate,
    match: dict,
    *,
    scan_time: str,
    current_kasa: float,
    cycle_id: str = "",
    reference_at: float | None = None,
) -> _QualifyingCandidate:
    sport_key = ""
    if isinstance(match, dict):
        sport_key = str(match.get("sport_key", "")).strip()
    return {
        "notify_key": candidate.notify_key,
        "match_name": candidate.match_name,
        "market": candidate.market,
        "match_id": candidate.match_id,
        "alert_message": _build_tiered_alert(
            match,
            candidate.market,
            candidate.stake,
            tier=candidate.tier,
            current_kasa=current_kasa,
            sharp_odds=candidate.sharp_odds,
            soft_odds=candidate.soft_odds,
            ev_percent=candidate.ev_percent,
            scan_time=scan_time,
            consensus_books=candidate.consensus_books,
            hero_confidence=candidate.hero_confidence,
            cycle_id=cycle_id,
            reference_at=reference_at,
        ),
        "stake": candidate.stake,
        "soft_odds": candidate.soft_odds,
        "ev_percent": candidate.ev_percent,
        "tier": candidate.tier,
        "ev": candidate.ev,
        "sport_key": sport_key,
        "sharp_odds": candidate.sharp_odds,
        "match_record": dict(match) if isinstance(match, dict) else {},
        "hero_confidence": candidate.hero_confidence,
    }


def _is_panel_already_running(host: str = "127.0.0.1", port: int = DEFAULT_PANEL_PORT) -> bool:
    """Panel portu zaten dinleniyorsa True doner (tek-kopya guvencesi).

    Ikinci bir kopya, calisan kopyanin paneli ve Telegram baglantisiyla cakismasin diye
    motor baslamadan ONCE bu kontrol edilir.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def main() -> None:
    scanning_active = False
    notified_registry: dict[str, _NotifiedEntry] = {}
    auto_settler_stop = threading.Event()
    auto_settler_thread: threading.Thread | None = None

    _configure_logging()

    if _is_panel_already_running():
        print(
            "[SQE-V1] UYARI: Zaten calisan bir kopya var gibi gorunuyor "
            f"(panel portu {DEFAULT_PANEL_PORT} dolu). Cakismayi onlemek icin bu ikinci "
            "kopya BASLATILMADI.",
            file=sys.stderr,
        )
        print(
            "[SQE-V1] Yapilacak: once calisan kopyayi durdurun "
            "(PYTHONPATH=. python reset_telegram.py) veya o pencereyi Control+C ile "
            "kapatin; sonra yeniden baslatin.",
            file=sys.stderr,
        )
        return

    try:
        current_kasa = _bootstrap_operator_bankroll()

        bootstrap_hero_mode()
        bootstrap_notify_frequency_profile()
        bootstrap_experimental_mode()
        if is_hero_mode_enabled():
            bootstrap_hero_profile()
            hero_panel = build_hero_panel_payload()
            hero_payload = hero_panel.get("profile") or {}
            daily_payload = hero_panel.get("daily") or {}
            print(
                f"[SQE-V1] HERO modu ACIK | {hero_payload.get('active_label', '?')} | "
                f"guven>=%{hero_payload.get('min_confidence_percent', '?')} | "
                f"gunde max {hero_payload.get('max_daily_picks', '?')} aday | "
                f"bugun {daily_payload.get('status_label', '?')}"
            )

        exp_status = format_experimental_status_line()
        print(f"[SQE-V1] {exp_status}")

        start_web_server()
        try:
            from core.notify_frequency_profile import build_notify_frequency_payload

            freq_payload = build_notify_frequency_payload()
        except Exception:
            freq_payload = None
        update_sistem_durumu(
            total_kasa=current_kasa,
            risk_per_trade=get_operator_risk_per_trade(),
            active_match_count=0,
            ev_threshold=get_action_ev_threshold(),
            is_live=False,
            scan_enabled=False,
            matches=[],
        )
        panel_label = "CANLI MAC + BUTCE TAKIBI"
        print(f"[SQE-V1] Veritabani: {get_db_path()}")
        print(
            f"[SQE-V1] Nesine paneli ({panel_label}): http://127.0.0.1:{PANEL_PORT} | "
            f"execution={EXECUTION_BOOK} | kasa={current_kasa:.2f} TL"
        )
        if not is_operator_budget_configured():
            print(
                "[SQE-V1] Butce girilmedi | Panel > Kasa sekmesinden Nesine bakiyenizi kaydedin. "
                "Tarama butce kaydedilene kadar baslamaz."
            )
        if isinstance(freq_payload, dict):
            print(
                f"[SQE-V1] Bildirim profili: {freq_payload.get('active_label', '?')} | "
                f"Oyna esigi ~+%{freq_payload.get('live', {}).get('action_ev_percent', '?')} | "
                f"tur/lig={freq_payload.get('live', {}).get('leagues_per_scan', '?')}"
            )

        logging.info("Sistem: Auto-Settler otonom servisi aktif edildi")
        auto_settler_thread = threading.Thread(
            target=run_auto_settler_loop,
            kwargs={"stop_event": auto_settler_stop},
            name="SQE-AutoSettler",
            daemon=True,
        )
        auto_settler_thread.start()

        telegram_worker.send_system_ready()
        print("[SQE-V1] Sistem hazir | Telegram: [Taramayi Baslat] bekleniyor")

        while not scanning_active:
            if telegram_worker.wait_for_scan_start():
                scanning_active = True
                current_kasa = get_latest_bakiye()
                update_sistem_durumu(is_live=True, total_kasa=current_kasa)

        while True:
            # Gunluk aksam ozeti: tarama acik/kapali fark etmez, saat geldiyse
            # gunde bir kez gonderilir (core/daily_digest.py kendi kilidini tutar).
            maybe_send_daily_digest(telegram_worker.send_hero_daily_notice)
            # Olcum nabzi: sinyal cikmasa da surecin yasadigini duzenli bildirir.
            maybe_send_measurement_pulse(telegram_worker.send_hero_daily_notice)

            if not bool(SISTEM_DURUMU.get("scan_enabled", False)):
                time.sleep(2)
                continue

            if not is_operator_budget_configured():
                print(
                    "[SQE-V1] Tarama bekliyor | Panel > Kasa: Nesine bakiyenizi kaydedin (min 50 TL)"
                )
                time.sleep(10)
                continue

            measurement_mode = is_measurement_mode_enabled()
            scan_interval = resolve_scan_interval_seconds(measurement_mode=measurement_mode)
            print("--- [SQE-V1] Canli Piyasa Taramasi Baslatildi ---")
            print(
                f"[SQE-V1] Tarama Frekansi: {scan_interval}sn"
                + (" (olcum modu hizli tempo | ek kredi yok)" if measurement_mode else "")
            )

            current_kasa = get_latest_bakiye()
            cycle_id = _reset_scan_cycle_state(notified_registry, current_kasa)

            feed_started = time.perf_counter()
            try:
                matches = get_unified_live_data()
            except Exception as exc:
                print(
                    f"Donanim Erisilemiyor: Canli Veri Gateway Hatasi | {exc}",
                    file=sys.stderr,
                )
                matches = []
            feed_latency_ms = round((time.perf_counter() - feed_started) * 1000.0, 1)

            if matches:
                try:
                    snapshot_path = save_feed_snapshot(matches, label="live")
                    print(f"[SQE-V1] Feed snapshot | {snapshot_path.name} | latency_ms={feed_latency_ms}")
                except OSError as exc:
                    print(f"[SQE-V1] Feed snapshot kaydi basarisiz | {exc}", file=sys.stderr)

            matches = enrich_match_feed_safely(matches)
            if is_feature_enabled("news_signals"):
                matches = attach_experimental_news_to_matches(matches)
            context_diag = get_last_context_feed_diag()

            scan_time = _format_scan_timestamp()
            pipeline_candidates, pipeline_stats = evaluate_matches(
                matches,
                current_kasa=current_kasa,
            )

            # Ilk yari (IY) sinyalleri GOLGE: skoru otomatik kapanamadigi icin
            # kupon acmaz / Telegram'a gitmez; yalnizca kanit dosyasina yazilir
            # (docs: KG canli, IY golge karari — Asama B4).
            first_half_shadow = [
                candidate
                for candidate in pipeline_candidates
                if market_family(candidate.market) == FAMILY_FIRST_HALF
            ]
            if first_half_shadow:
                shadow_written = record_first_half_shadow(
                    first_half_shadow, scan_time=scan_time
                )
                if shadow_written:
                    print(f"[SQE-V1] IY golge | kaydedildi={shadow_written}")
                pipeline_candidates = [
                    candidate
                    for candidate in pipeline_candidates
                    if market_family(candidate.market) != FAMILY_FIRST_HALF
                ]

            qualifying_candidates: list[_QualifyingCandidate] = []
            match_index = {
                f"{str(item.get('match_name', ''))}|{str(item.get('market', '')).upper()}": item
                for item in matches
                if isinstance(item, dict)
            }

            for match in matches:
                if not isinstance(match, dict):
                    continue
                match_name = str(match["match_name"])
                market = str(match["market"])
                SCAN_CYCLE.register_live_match(match_name, market)

            for candidate in pipeline_candidates:
                SCAN_CYCLE.register_qualifying(candidate.notify_key)
                lookup_key = f"{candidate.match_name}|{candidate.market.upper()}"
                source_match = match_index.get(lookup_key, {"match_name": candidate.match_name})
                qualifying_candidates.append(
                    _pipeline_candidate_to_qualifying(
                        candidate,
                        source_match,
                        scan_time=scan_time,
                        current_kasa=get_latest_bakiye(),
                        cycle_id=cycle_id,
                        reference_at=time.time(),
                    )
                )

            cycle_stats = _new_scan_cycle_stats()
            pipeline_dict = pipeline_stats.as_dict()
            for key in (
                "birlesik",
                "efutbol",
                "stale",
                "tolerans",
                "pasif_ev",
                "absurd_ev",
                "suspicious_match",
                "context_filter",
                "experimental_checked",
                "experimental_would_filter",
                "zayif_referans",
                "watch",
                "action",
                "high",
                "aday",
            ):
                if key in pipeline_dict:
                    cycle_stats[key] = pipeline_dict[key]  # type: ignore[literal-required]

            cycle_stats["context_bundle"] = int(context_diag.get("bundle_attached", 0))
            cycle_stats["context_api_req"] = int(context_diag.get("api_requests", 0))

            record_pilot_ab_scan(
                cycle_id=cycle_id,
                action=int(cycle_stats.get("action", 0)),
                high=int(cycle_stats.get("high", 0)),
                context_filtered=int(cycle_stats.get("context_filter", 0)),
                context_bundle_count=int(cycle_stats.get("context_bundle", 0)),
            )

            _sync_panel_state(
                matches,
                is_live=True,
                current_kasa=current_kasa,
                alarm_candidate_count=len(qualifying_candidates),
            )

            print(_format_qc_status_line())
            exp_status = format_experimental_status_line()
            if "kapali" not in exp_status:
                print(f"[SQE-V1] {exp_status}")
                if int(cycle_stats.get("experimental_would_filter", 0)) > 0:
                    print(
                        "[SQE-V1] Deneysel golge | elenirdi="
                        f"{cycle_stats.get('experimental_would_filter', 0)}"
                    )

            _dispatch_scan_notifications(
                cycle_id,
                qualifying_candidates,
                notified_registry,
                cycle_stats,
            )

            # Son cagri: kick-off'a yaklasmis, hala degerli ama oynanmamis
            # bahisleri bir kez daha hatirlat (tarama beynine dokunmaz; sadece
            # o turun taze aday listesini kullanir).
            reminded = maybe_send_kickoff_reminders(
                qualifying_candidates,
                telegram_worker.send_alert,
            )
            if reminded:
                print(f"[SQE-V1] Son cagri | hatirlatma_gonderildi={reminded}")

            if is_hero_mode_enabled():
                record_hero_measurement_scan_day()

            sent_total = int(cycle_stats.get("telegram", 0)) + int(cycle_stats.get("izle", 0))
            update_sistem_durumu(
                last_scan=telegram_worker.build_last_scan_panel_payload(
                    cycle_stats,
                    scan_time=scan_time,
                    sent_alerts=sent_total,
                ),
            )

            print(_format_scan_cycle_summary(cycle_stats))

            record_pulse_cycle(dict(cycle_stats))

            if measurement_mode:
                # Huni: hangi asamada kac aday eledik. Esikleri degistirmeden
                # once darbogazin nerede oldugunu bu tablo gosterir.
                record_scan_funnel(dict(cycle_stats), cycle_id=cycle_id or "")

            settlement_stats = run_settlement_pass()
            if settlement_stats["settled"] > 0 or settlement_stats["pending"] > 0:
                refreshed_kasa = get_latest_bakiye()
                update_sistem_durumu(
                    total_kasa=refreshed_kasa,
                    performance=get_performance_stats(),
                )
                print(
                    f"[SQE-V1] Settlement | bekleyen={settlement_stats['pending']} | "
                    f"kapanan={settlement_stats['settled']} | "
                    f"hala_bekleyen={settlement_stats['waiting']} | kasa={refreshed_kasa} TL"
                )

            time.sleep(scan_interval)
    finally:
        _graceful_shutdown(auto_settler_stop, auto_settler_thread)


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
    """SIGTERM'i SIGINT ile ayni yola sokar: durdurma betigi de temiz kapatir."""
    del frame
    print(f"[SQE-V1] Kapatma sinyali alindi ({signum}) | guvenli kapanis basliyor")
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        main()
    except KeyboardInterrupt:
        print("Operatör Komutu ile Sistem Kapatildi.")
