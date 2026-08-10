from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.fixture_notify_guard import should_skip_fixture_telegram
from core.match_filters import (
    SCAN_CYCLE,
    is_feed_pair_synchronized,
    is_feed_timestamp_fresh,
    is_suspicious_match_record,
    is_virtual_match_text,
)
from core.hero_mode import is_hero_mode_enabled
from core.passion_engine import is_ev_absurd
from core.scan_league_settings import get_enabled_sharp_sport_keys
from database.context_cache import is_cacheable_real_match

__all__ = (
    "NotificationGateCase",
    "NotificationGateReport",
    "evaluate_notification_quality",
    "load_notification_gate_cases",
    "run_notification_gate_theory_suite",
)

_LIVE_NOTIFY_WINDOW_SECONDS = 7200
_DRY_RUN_EVENT_PREFIX = "dry-run-"


@dataclass(frozen=True)
class NotificationGateCase:
    case_id: str
    match: dict[str, Any]
    notify_key: str
    mac_adi: str
    market: str
    soft_odds: float
    sharp_odds: float
    tier: str
    expect_allow: bool
    expect_reason_prefix: str = ""
    test_probe: bool = False
    bypass_dedup: bool = True
    seed_notification: bool = False
    seed_kupon: bool = False


@dataclass
class NotificationGateReport:
    total: int
    passed: int
    failed: int
    failures: list[str]

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass(frozen=True)
class NotificationGateResult:
    allow: bool
    reason: str = ""


def _parse_commence_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_finished_or_outside_notify_window(
    match: dict[str, Any],
    *,
    reference_at: float | None = None,
) -> bool:
    if match.get("completed") is True:
        return True

    kickoff = _parse_commence_time(match.get("commence_time"))
    if kickoff is None:
        return False

    now = datetime.fromtimestamp(reference_at or time.time(), tz=timezone.utc)
    if kickoff > now:
        return False
    return (now - kickoff).total_seconds() > _LIVE_NOTIFY_WINDOW_SECONDS


def _feed_timestamps_missing(match: dict[str, Any]) -> bool:
    soft_observed_at = float(match.get("soft_observed_at", match.get("observed_at", 0.0)) or 0.0)
    sharp_observed_at = float(match.get("sharp_observed_at", match.get("observed_at", 0.0)) or 0.0)
    return soft_observed_at <= 0.0 or sharp_observed_at <= 0.0


def _is_stale_feed_match(
    match: dict[str, Any],
    *,
    reference_at: float | None = None,
) -> bool:
    soft_observed_at = float(match.get("soft_observed_at", match.get("observed_at", 0.0)) or 0.0)
    sharp_observed_at = float(match.get("sharp_observed_at", match.get("observed_at", 0.0)) or 0.0)
    if not is_feed_timestamp_fresh(soft_observed_at, reference_at=reference_at):
        return True
    if not is_feed_timestamp_fresh(sharp_observed_at, reference_at=reference_at):
        return True
    return not is_feed_pair_synchronized(soft_observed_at, sharp_observed_at)


def _is_enabled_scan_league(match: dict[str, Any]) -> bool:
    sport_key = str(match.get("sport_key", "")).strip()
    if not sport_key:
        return False
    return sport_key in get_enabled_sharp_sport_keys()


def _passes_scan_cycle_gate(notify_key: str) -> tuple[bool, str]:
    if not SCAN_CYCLE.cycle_id or SCAN_CYCLE.started_at <= 0.0:
        return False, "theory_tarama_dongusu_yok"
    if notify_key not in SCAN_CYCLE.qualifying_keys:
        return False, "theory_tarama_dongusu_disi"
    return True, ""


def _passes_theory_real_match_gate(match: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(match, dict):
        return False, "theory_gecersiz_kayit"

    match_name = str(match.get("match_name", "")).strip()
    league_name = str(match.get("league_name", "")).strip()
    if not match_name:
        return False, "theory_mac_adi_yok"

    if is_virtual_match_text(match_name, league_name):
        return False, "theory_sanal_mac"

    event_id = str(match.get("event_id", "")).strip()
    event_id_folded = event_id.casefold()
    if not event_id:
        return False, "theory_event_id_yok"
    if event_id_folded.startswith(_DRY_RUN_EVENT_PREFIX) or "dry-run" in match_name.casefold():
        return False, "theory_dry_run"

    if _parse_commence_time(match.get("commence_time")) is None:
        return False, "theory_baslangic_zamani_yok"

    soft_source = str(match.get("soft_source", "")).strip().lower()
    if soft_source != "nesine":
        return False, "theory_nesine_dogrulanmadi"

    if is_suspicious_match_record(match):
        return False, "theory_supheli_oran"

    if not is_cacheable_real_match(match):
        return False, "theory_gecersiz_mac"

    if not _is_enabled_scan_league(match):
        return False, "theory_lig_kapali"

    return True, ""


def evaluate_notification_quality(
    match: dict[str, Any],
    *,
    notify_key: str,
    mac_adi: str,
    market: str,
    soft_odds: float,
    sharp_odds: float,
    tier: str,
    ev: float,
    test_probe: bool = False,
    odds_change_bypass_pct: float = 0.05,
    bypass_dedup: bool = False,
    reference_at: float | None = None,
) -> NotificationGateResult:
    _ = tier

    theory_ok, theory_reason = _passes_theory_real_match_gate(match)
    if not theory_ok:
        return NotificationGateResult(allow=False, reason=theory_reason)

    cycle_ok, cycle_reason = _passes_scan_cycle_gate(notify_key)
    if not cycle_ok:
        return NotificationGateResult(allow=False, reason=cycle_reason)

    if _is_finished_or_outside_notify_window(match, reference_at=reference_at):
        return NotificationGateResult(allow=False, reason="theory_mac_penceresi_kapandi")

    if _feed_timestamps_missing(match):
        return NotificationGateResult(allow=False, reason="theory_feed_zaman_yok")

    if _is_stale_feed_match(match, reference_at=reference_at):
        return NotificationGateResult(allow=False, reason="theory_veri_eski")

    if not is_hero_mode_enabled() and is_ev_absurd(ev):
        return NotificationGateResult(allow=False, reason="theory_absurt_ev")

    skip_fixture, fixture_reason = should_skip_fixture_telegram(
        notify_key,
        mac_adi,
        market,
        soft_odds,
        odds_change_bypass_pct=odds_change_bypass_pct,
        bypass_dedup=bypass_dedup,
    )
    if skip_fixture:
        return NotificationGateResult(allow=False, reason=fixture_reason)

    return NotificationGateResult(allow=True)


def _parse_gate_case(raw: object) -> NotificationGateCase | None:
    if not isinstance(raw, dict):
        return None

    case_id = str(raw.get("id", "")).strip()
    match = raw.get("match")
    if not case_id or not isinstance(match, dict):
        return None

    try:
        soft_odds = float(raw.get("soft_odds", match.get("soft_odds", 0.0)))
        sharp_odds = float(raw.get("sharp_odds", match.get("sharp_odds", 0.0)))
    except (TypeError, ValueError):
        return None

    mac_adi = str(raw.get("mac_adi", match.get("match_name", ""))).strip()
    market = str(raw.get("market", match.get("market", "MS1"))).strip().upper()
    if not mac_adi or not market:
        return None

    notify_key = str(raw.get("notify_key", f"{mac_adi.casefold()}|{market}")).strip()
    tier = str(raw.get("tier", "ACTION")).strip().upper()
    expect_allow = bool(raw.get("expect_allow", False))
    expect_reason_prefix = str(raw.get("expect_reason_prefix", "")).strip()

    return NotificationGateCase(
        case_id=case_id,
        match=match,
        notify_key=notify_key,
        mac_adi=mac_adi,
        market=market,
        soft_odds=soft_odds,
        sharp_odds=sharp_odds,
        tier=tier,
        expect_allow=expect_allow,
        expect_reason_prefix=expect_reason_prefix,
        test_probe=bool(raw.get("test_probe", False)),
        bypass_dedup=bool(raw.get("bypass_dedup", True)),
        seed_notification=bool(raw.get("seed_notification", False)),
        seed_kupon=bool(raw.get("seed_kupon", False)),
    )


def load_notification_gate_cases(path: Path | str) -> list[NotificationGateCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases_raw = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases_raw, list):
        return []

    cases: list[NotificationGateCase] = []
    for item in cases_raw:
        parsed = _parse_gate_case(item)
        if parsed is not None:
            cases.append(parsed)
    return cases


def _prepare_lab_match_record(
    case: NotificationGateCase,
    match: dict[str, Any],
    *,
    reference_at: float,
) -> dict[str, Any]:
    prepared = dict(match)
    prepared.setdefault("soft_odds", case.soft_odds)
    prepared.setdefault("sharp_odds", case.sharp_odds)
    prepared.setdefault("market", case.market)

    if case.case_id not in {"old-kickoff-block", "completed-block"}:
        prepared["soft_observed_at"] = reference_at - 30.0
        prepared["sharp_observed_at"] = reference_at - 30.0
    prepared.setdefault("soft_source", "nesine")
    return prepared


def run_notification_gate_theory_suite(
    cases: list[NotificationGateCase],
    *,
    db_path: Path | None = None,
    reference_at: float | None = None,
) -> NotificationGateReport:
    import database.db_manager as db_manager
    from database.db_manager import (
        add_kupon,
        init_db,
        record_fixture_notification,
    )

    previous_db_path = db_manager._DB_PATH
    if db_path is not None:
        db_manager._DB_PATH = db_path
        init_db()

    failures: list[str] = []
    lab_clock = float(reference_at if reference_at is not None else time.time())

    from core.scan_league_settings import update_scan_league_settings

    theory_enabled_keys = sorted(
        {
            str(case.match.get("sport_key", "")).strip()
            for case in cases
            if str(case.match.get("sport_key", "")).strip()
        }
    )
    if theory_enabled_keys:
        update_scan_league_settings({"enabled_sport_keys": theory_enabled_keys})

    try:
        for case in cases:
            SCAN_CYCLE.reset()
            SCAN_CYCLE.register_qualifying(case.notify_key)

            if case.seed_notification:
                record_fixture_notification(
                    case.notify_key,
                    case.mac_adi,
                    case.market,
                    case.soft_odds,
                )
            if case.seed_kupon:
                add_kupon(
                    f"theory-{case.case_id}",
                    case.mac_adi,
                    case.market,
                    50.0,
                    case.soft_odds,
                    case.sharp_odds,
                    ev_at_alert=0.05,
                    sport_key=str(case.match.get("sport_key", "soccer_fifa_world_cup")),
                    event_id=str(case.match.get("event_id", f"theory-{case.case_id}")),
                    commence_time=str(case.match.get("commence_time", "2099-06-19T18:00:00Z")),
                )

            match_record = _prepare_lab_match_record(case, case.match, reference_at=lab_clock)

            result = evaluate_notification_quality(
                match_record,
                notify_key=case.notify_key,
                mac_adi=case.mac_adi,
                market=case.market,
                soft_odds=case.soft_odds,
                sharp_odds=case.sharp_odds,
                tier=case.tier,
                ev=0.02,
                test_probe=case.test_probe,
                bypass_dedup=case.bypass_dedup,
                reference_at=lab_clock,
            )

            if result.allow != case.expect_allow:
                failures.append(
                    f"{case.case_id}: allow={result.allow} beklenen={case.expect_allow} reason={result.reason}"
                )
                continue

            if (
                not case.expect_allow
                and case.expect_reason_prefix
                and not result.reason.startswith(case.expect_reason_prefix)
            ):
                failures.append(
                    f"{case.case_id}: reason={result.reason!r} beklenen_on ek={case.expect_reason_prefix!r}"
                )
    finally:
        db_manager._DB_PATH = previous_db_path

    passed = len(cases) - len(failures)
    return NotificationGateReport(
        total=len(cases),
        passed=passed,
        failed=len(failures),
        failures=failures,
    )
