from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core import clv_tracker
from core.settlement_log import append_settlement_cycle_log
from core.time_utils import parse_utc
from database.db_manager import (
    get_latest_bakiye,
    get_pending_kupons,
    get_performance_stats,
    init_db,
    backfill_pending_kupon_fixture_metadata,
    result_kupon,
    suspend_kupon,
)
from notifiers import telegram_worker
from scrapers.live_feed_gateway import _match_names_fuzzy, get_settlement_feed
from ui.web_server import update_sistem_durumu

__all__ = (
    "run_auto_settler_loop",
    "run_settlement_pass",
    "collect_pending_sport_keys",
    "fixture_names_match_for_settlement",
    "is_zombie_kupon",
    "SettlementIndexes",
    "build_settlement_indexes",
    "resolve_kupon_settlement_outcome",
)

_LIVE_FEED_BACKOFF_SECONDS = 300
_ZOMBIE_POST_KICKOFF = timedelta(hours=6)
_ZOMBIE_FALLBACK_AGE = timedelta(hours=72)
# Kredi diyeti (Adim 4): skor sorgusu ATILAN (faturali) turdan sonra 45 dk
# beklenir — eski 90 sn'lik tempo acik kuponda saatte onlarca kredi yakiyordu.
# Sorgu atilmayan turlar (mac bitmemis / kupon yok) ucretsizdir ve 15 dk'lik
# tempoda tekrar kontrol edilir; hazir an en fazla 15 dk gecikmeyle yakalanir.
_POST_FETCH_SLEEP_SECONDS = 2700
_IDLE_CYCLE_SLEEP_SECONDS = 900
# Mac bitmeden skor sormak bosa kredi: kickoff + bu tampon gecmeden kuponun
# ligi sorgulanmaz (mac ~2 saatte biter; skorun API'ye dusmesi de dahil).
_SETTLEMENT_READY_AFTER_KICKOFF = timedelta(hours=2)
_DIAG_TIMEZONE = ZoneInfo("Europe/Istanbul")
_PILOT_SETTLEMENT_BYPASS_AFTER = timedelta(hours=2)
_PILOT_SETTLEMENT_BYPASS_ENABLED = False
_FUZZY_MATCH_RATIO = 0.65
_TEAM_MATCH_MIN_RATIO = 0.75
_VALID_OUTCOMES = frozenset({"WON", "LOST"})
_ASCII_TRANSLATION = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

logger = logging.getLogger("sqe.auto_settler")


def _configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _emit_settlement_diag(message: str) -> None:
    print(f"[SQE-V1] Settlement teşhis | {message}", file=sys.stderr)


def _normalize_match_key(match_name: str, market: str) -> tuple[str, str]:
    normalized_name = (
        match_name.strip().lower().translate(_ASCII_TRANSLATION).replace("  ", " ")
    )
    normalized_market = market.strip().upper()
    return normalized_name, normalized_market


def _parse_utc_timestamp(raw_value: str) -> datetime | None:
    return parse_utc(raw_value)


def _kupon_age(kupon: dict[str, Any]) -> timedelta | None:
    created_at = _parse_utc_timestamp(str(kupon.get("eklenme_tarihi", "")))
    if created_at is None:
        return None
    return datetime.now(timezone.utc) - created_at


def is_zombie_kupon(
    kupon: dict[str, Any],
    *,
    reference_at: datetime | None = None,
) -> bool:
    """Prematch: zombi yalnizca kickoff + buffer gectikten sonra; yoksa 72 saat fallback."""
    if not isinstance(kupon, dict):
        return False

    now = reference_at or datetime.now(timezone.utc)
    kickoff = _parse_utc_timestamp(str(kupon.get("commence_time", "")).strip())
    if kickoff is not None:
        return now > kickoff + _ZOMBIE_POST_KICKOFF

    created_at = _parse_utc_timestamp(str(kupon.get("eklenme_tarihi", "")))
    if created_at is None:
        return False
    return now - created_at >= _ZOMBIE_FALLBACK_AGE


def _is_pilot_bypass_eligible(kupon: dict[str, Any]) -> bool:
    if not _PILOT_SETTLEMENT_BYPASS_ENABLED:
        return False
    age = _kupon_age(kupon)
    if age is None:
        return False
    return age >= _PILOT_SETTLEMENT_BYPASS_AFTER


def _pilot_bypass_outcome(kupon_id: int) -> str:
    return "WON" if kupon_id % 2 == 0 else "LOST"


def collect_pending_sport_keys(pending_kupons: list[dict[str, Any]]) -> tuple[str, ...]:
    """Bekleyen kuponlarin KENDI ligleri — dolgu lig yok (Adim 4 kredi diyeti).

    Bos donebilir (lig bilgisi olmayan eski kuponlar); o durumda sharp_feed
    eski rotasyon secimine duser ki bu kuponlar da sonuclanabilsin.
    """
    keys: list[str] = []
    for kupon in pending_kupons:
        if not isinstance(kupon, dict):
            continue
        sport_key = str(kupon.get("sport_key", "")).strip()
        if sport_key and sport_key not in keys:
            keys.append(sport_key)
    return tuple(keys)


def _settlement_ready_at(kupon: dict[str, Any]) -> datetime | None:
    """Kuponun maci en erken ne zaman 'bitmis' sayilir (kickoff + tampon)."""
    kickoff = _parse_utc_timestamp(str(kupon.get("commence_time", "")).strip())
    if kickoff is None:
        return None  # takvim bilinmiyor -> hemen sorgulanabilir (fail-open)
    return kickoff + _SETTLEMENT_READY_AFTER_KICKOFF


def _split_settlement_ready(
    pending_kupons: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Maci bitmis olabilecek kuponlari ayirir; kalanlarin en erken hazir anini verir."""
    moment = now or datetime.now(timezone.utc)
    ready: list[dict[str, Any]] = []
    earliest: datetime | None = None
    for kupon in pending_kupons:
        ready_at = _settlement_ready_at(kupon)
        if ready_at is None or ready_at <= moment:
            ready.append(kupon)
        elif earliest is None or ready_at < earliest:
            earliest = ready_at
    return ready, earliest


@dataclass(frozen=True)
class SettlementIndexes:
    by_event_id: dict[tuple[str, str], str]
    by_match_name: dict[tuple[str, str], str]


def build_settlement_indexes(feed: list[dict[str, Any]]) -> SettlementIndexes:
    by_event_id: dict[tuple[str, str], str] = {}
    by_match_name: dict[tuple[str, str], str] = {}

    for entry in feed:
        if not isinstance(entry, dict):
            continue
        match_name = entry.get("match_name")
        market = entry.get("market")
        outcome = entry.get("outcome")
        if not isinstance(match_name, str) or not isinstance(market, str):
            continue
        if not isinstance(outcome, str):
            continue
        normalized_outcome = outcome.strip().upper()
        if normalized_outcome not in _VALID_OUTCOMES:
            continue

        market_key = market.strip().upper()
        by_match_name[_normalize_match_key(match_name, market)] = normalized_outcome

        event_id = entry.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            by_event_id[(event_id.strip(), market_key)] = normalized_outcome

    return SettlementIndexes(by_event_id=by_event_id, by_match_name=by_match_name)


def _build_settlement_index(feed: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return build_settlement_indexes(feed).by_match_name


def _lookup_outcome_by_event_id(
    settlement_index: SettlementIndexes,
    event_id: str,
    market: str,
) -> str | None:
    normalized_event_id = event_id.strip()
    if not normalized_event_id:
        return None
    return settlement_index.by_event_id.get(
        (normalized_event_id, market.strip().upper())
    )


def _lookup_outcome_exact(
    settlement_index: SettlementIndexes,
    mac_adi: str,
    market: str,
) -> str | None:
    return settlement_index.by_match_name.get(_normalize_match_key(mac_adi, market))


def _split_fixture_teams(match_name: str) -> tuple[str, str] | None:
    if " - " not in match_name:
        return None
    home_team, away_team = match_name.split(" - ", 1)
    home = home_team.strip()
    away = away_team.strip()
    if not home or not away:
        return None
    return home, away


def fixture_names_match_for_settlement(
    db_mac: str,
    api_mac: str,
    *,
    min_ratio: float = _TEAM_MATCH_MIN_RATIO,
) -> bool:
    """Both teams must match (order or swapped); prevents wrong-fixture settlement."""
    db_teams = _split_fixture_teams(db_mac)
    api_teams = _split_fixture_teams(api_mac)
    if db_teams is None or api_teams is None:
        return _match_names_fuzzy(db_mac, api_mac) >= max(min_ratio, _FUZZY_MATCH_RATIO)

    db_home, db_away = db_teams
    api_home, api_away = api_teams
    direct = min(
        _match_names_fuzzy(db_home, api_home),
        _match_names_fuzzy(db_away, api_away),
    )
    swapped = min(
        _match_names_fuzzy(db_home, api_away),
        _match_names_fuzzy(db_away, api_home),
    )
    return max(direct, swapped) >= min_ratio


def _lookup_outcome_fuzzy(
    settlement_feed: list[dict[str, Any]],
    mac_adi: str,
    market: str,
) -> tuple[str | None, str | None, float]:
    market_key = market.strip().upper()
    best_outcome: str | None = None
    best_name: str | None = None
    best_ratio = 0.0

    for entry in settlement_feed:
        if not isinstance(entry, dict):
            continue
        entry_market = entry.get("market")
        entry_match = entry.get("match_name")
        entry_outcome = entry.get("outcome")
        if not isinstance(entry_market, str) or entry_market.strip().upper() != market_key:
            continue
        if not isinstance(entry_match, str) or not entry_match.strip():
            continue
        if not isinstance(entry_outcome, str):
            continue
        normalized_outcome = entry_outcome.strip().upper()
        if normalized_outcome not in _VALID_OUTCOMES:
            continue

        if not fixture_names_match_for_settlement(mac_adi, entry_match.strip()):
            continue

        ratio = _match_names_fuzzy(mac_adi, entry_match)
        if ratio > best_ratio:
            best_ratio = ratio
            best_outcome = normalized_outcome
            best_name = entry_match.strip()

    return best_outcome, best_name, best_ratio


def resolve_kupon_settlement_outcome(
    kupon: dict[str, Any],
    settlement_index: SettlementIndexes,
    settlement_feed: list[dict[str, Any]],
) -> tuple[str | None, str, str | None, float]:
    mac_adi = str(kupon.get("mac_adi", ""))
    market = str(kupon.get("market", ""))
    event_id = str(kupon.get("event_id", "")).strip()

    if event_id:
        event_outcome = _lookup_outcome_by_event_id(settlement_index, event_id, market)
        if event_outcome is not None:
            return event_outcome, "kazandi_kaybetti_event_id", mac_adi, 1.0

    exact = _lookup_outcome_exact(settlement_index, mac_adi, market)
    if exact is not None:
        return exact, "kazandi_kaybetti", mac_adi, 1.0

    fuzzy_outcome, api_name, ratio = _lookup_outcome_fuzzy(
        settlement_feed,
        mac_adi,
        market,
    )
    if fuzzy_outcome is not None:
        return fuzzy_outcome, "kazandi_kaybetti_fuzzy", api_name, ratio

    return None, "beklemede", None, 0.0


def _resolve_outcome_for_kupon(
    kupon: dict[str, Any],
    settlement_index: SettlementIndexes,
    settlement_feed: list[dict[str, Any]],
) -> tuple[str | None, str, str | None, float]:
    return resolve_kupon_settlement_outcome(kupon, settlement_index, settlement_feed)


def _settlement_source_label(api_status: str, match_ratio: float) -> str:
    if api_status == "kazandi_kaybetti_event_id":
        return "api_event_id"
    if match_ratio >= 1.0:
        return "api_exact"
    return "api_fuzzy"


def _calculate_kasa_impact(stake: float, soft_oran: float, outcome: str) -> float:
    if outcome == "WON":
        return round(float(stake) * float(soft_oran), 2)
    return 0.0


def _process_zombie_kupons(pending_kupons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_pending: list[dict[str, Any]] = []
    for kupon in pending_kupons:
        kupon_id = int(kupon["id"])
        if not is_zombie_kupon(kupon):
            active_pending.append(kupon)
            continue

        if suspend_kupon(kupon_id, refund_stake=True):
            logger.warning(
                "Zombi korumasi: Kupon %s kickoff sonrasi cozulemedi, ASKIDA + stake iade.",
                kupon_id,
            )
        else:
            logger.error(
                "Zombi korumasi basarisiz: Kupon %s ASKIDA durumuna alinamadi.",
                kupon_id,
            )
    return active_pending


def _apply_kupon_result(
    kupon: dict[str, Any],
    outcome: str,
    *,
    source: str,
) -> bool:
    kupon_id = int(kupon["id"])
    mac_adi = str(kupon.get("mac_adi", ""))
    market = str(kupon.get("market", ""))
    stake = float(kupon.get("stake", 0.0))
    soft_oran = float(kupon.get("soft_oran", 0.0))

    kasa_before = get_latest_bakiye()
    if not result_kupon(kupon_id, outcome):
        logger.error(
            "Kupon sonuclandirma basarisiz: id=%s | mac=%s | sonuc=%s | kaynak=%s",
            kupon_id,
            mac_adi,
            outcome,
            source,
        )
        return False

    kasa_after = get_latest_bakiye()
    kasa_impact = round(kasa_after - kasa_before, 2)
    if kasa_impact == 0.0 and outcome == "WON":
        kasa_impact = _calculate_kasa_impact(stake, soft_oran, outcome)

    update_sistem_durumu(
        total_kasa=kasa_after,
        performance=get_performance_stats(),
    )

    logger.info(
        "İşlem Tamamlandı: %s - Kasa Etkisi: %s - Kaynak: %s",
        kupon_id,
        kasa_impact,
        source,
    )
    try:
        telegram_worker.send_alert(
            build_beginner_settlement_notice(
                mac_adi=mac_adi,
                market=market,
                outcome=outcome,
                stake=stake,
                soft_oran=soft_oran,
                kasa_before=kasa_before,
                kasa_after=kasa_after,
            ),
            bypass_scan_gate=True,
        )
    except Exception as exc:
        logger.warning("Settlement telegram bildirimi gonderilemedi | %s", exc)
    return True


def _log_pending_kupon_diag(
    kupon: dict[str, Any],
    api_status: str,
    outcome: str | None,
    api_match_name: str | None,
    match_ratio: float,
) -> None:
    kupon_id = int(kupon["id"])
    match_id = str(kupon.get("match_id", ""))
    mac_adi = str(kupon.get("mac_adi", ""))
    market = str(kupon.get("market", ""))
    norm_key = _normalize_match_key(mac_adi, market)
    age = _kupon_age(kupon)
    age_minutes = int(age.total_seconds() // 60) if age is not None else -1

    if outcome in _VALID_OUTCOMES:
        sonuc_label = "KAZANDI" if outcome == "WON" else "KAYBETTI"
    elif _is_pilot_bypass_eligible(kupon):
        sonuc_label = "beklemede_pilot_bypass_hazir"
    else:
        sonuc_label = "BEKLEMEDE"

    api_match_text = api_match_name or "-"
    ratio_text = f"{match_ratio:.2f}" if match_ratio > 0.0 else "-"
    event_id = str(kupon.get("event_id", "")).strip() or "-"
    _emit_settlement_diag(
        f"kupon_id={kupon_id} | match_id={match_id} | db_event_id={event_id} | "
        f"db_mac={mac_adi} | db_market={market} | db_anahtar={norm_key[0]}|{norm_key[1]} | "
        f"yas_dk={age_minutes} | api_durum={api_status} | api_mac={api_match_text} | "
        f"eslesme={ratio_text} | sonuc={sonuc_label}"
        + (f" | sonuc_kaynak={outcome}" if outcome in _VALID_OUTCOMES else "")
    )


def _log_settlement_cycle_summary(
    pending_kupons: list[dict[str, Any]],
    extra_sport_keys: tuple[str, ...],
    settlement_feed: list[dict[str, Any]],
    settlement_index: SettlementIndexes,
) -> None:
    _emit_settlement_diag(
        f"dongu | bekleyen_kupon={len(pending_kupons)} | "
        f"kupon_ligleri={','.join(extra_sport_keys) or 'yok (rotasyon yedegi)'} | "
        f"api_sonuc_kayit={len(settlement_feed)} | "
        f"api_eslesen_pazar_isim={len(settlement_index.by_match_name)} | "
        f"api_eslesen_pazar_event_id={len(settlement_index.by_event_id)}"
    )


def _settle_pending_kupons(
    pending_kupons: list[dict[str, Any]],
    settlement_index: SettlementIndexes,
    settlement_feed: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    settled_count = 0
    entries: list[dict[str, Any]] = []
    for kupon in pending_kupons:
        outcome, api_status, api_match_name, match_ratio = _resolve_outcome_for_kupon(
            kupon,
            settlement_index,
            settlement_feed,
        )

        _log_pending_kupon_diag(kupon, api_status, outcome, api_match_name, match_ratio)
        entry: dict[str, Any] = {
            "kupon_id": int(kupon["id"]),
            "event_id": str(kupon.get("event_id", "")).strip(),
            "mac_adi": str(kupon.get("mac_adi", "")),
            "market": str(kupon.get("market", "")),
            "api_status": api_status,
            "outcome": outcome,
            "match_ratio": round(float(match_ratio), 4),
        }
        if outcome is None:
            entries.append(entry)
            continue

        source = _settlement_source_label(api_status, match_ratio)
        entry["source"] = source
        entries.append(entry)
        if _apply_kupon_result(kupon, outcome, source=source):
            settled_count += 1
    return settled_count, entries


def _interruptible_wait(stop_event: threading.Event, timeout_seconds: float) -> bool:
    return stop_event.wait(timeout=timeout_seconds)


def _next_sleep_seconds(stats: dict[str, int]) -> float:
    """Faturali sorgu atildiysa 45 dk bekle; atilmadiysa (kupon yok ya da
    maclar bitmedi) 15 dk'lik ucretsiz kontrol temposunda kal."""
    if stats.get("fetched"):
        return _POST_FETCH_SLEEP_SECONDS
    return _IDLE_CYCLE_SLEEP_SECONDS


def _should_persist_settlement_log(
    log_record: dict[str, Any],
    stats: dict[str, int],
) -> bool:
    if stats.get("settled", 0) > 0:
        return True
    if log_record.get("error"):
        return True
    if int(log_record.get("pending_before", 0)) > 0:
        return True
    return False


def run_settlement_pass() -> dict[str, int]:
    """Tek settlement turu: Odds API skorlari ile bekleyen kuponlari kapatir."""
    stats = {"pending": 0, "settled": 0, "waiting": 0, "fetched": 0}
    log_record: dict[str, Any] = {"kupons": []}

    try:
        backfill_pending_kupon_fixture_metadata()
        try:
            clv_summary = clv_tracker.backfill_missing()
            if clv_summary.get("yakalandi"):
                log_record["clv_captured"] = clv_summary["yakalandi"]
        except Exception as exc:
            logger.error("Teşhis: CLV kapanis yakalama hatasi | %s", exc)
        pending_kupons = get_pending_kupons()
        log_record["pending_before"] = len(pending_kupons)
        if not pending_kupons:
            return stats

        stats["pending"] = len(pending_kupons)
        active_pending = _process_zombie_kupons(pending_kupons)
        log_record["zombie_suspended"] = len(pending_kupons) - len(active_pending)
        if not active_pending:
            return stats

        # Kredi diyeti (Adim 4): maci bitmemis kupon icin skor sorgusu atma.
        ready_kupons, earliest_ready = _split_settlement_ready(active_pending)
        log_record["ready"] = len(ready_kupons)
        if not ready_kupons:
            stats["waiting"] = len(active_pending)
            log_record["skipped_fetch"] = "mac_bitmedi"
            earliest_text = (
                earliest_ready.astimezone(_DIAG_TIMEZONE).strftime("%H:%M")
                if earliest_ready is not None
                else "?"
            )
            _emit_settlement_diag(
                f"skor sorgusu ertelendi: mac bitmedi | bekleyen={len(active_pending)} | "
                f"en_erken_hazir={earliest_text} | 0 kredi"
            )
            return stats

        priority_sport_keys = collect_pending_sport_keys(ready_kupons)
        log_record["sport_keys"] = list(priority_sport_keys)

        try:
            settlement_feed = get_settlement_feed(
                extra_sport_keys=priority_sport_keys,
            )
            stats["fetched"] = 1
        except Exception as exc:
            logger.error("Teşhis: Settlement feed erişilemiyor | %s", exc)
            _emit_settlement_diag(f"tarama_sonrasi_hata={exc} | bekleyen={len(active_pending)}")
            stats["waiting"] = len(active_pending)
            log_record["error"] = str(exc)
            return stats

        settlement_index = build_settlement_indexes(settlement_feed)
        log_record["feed_records"] = len(settlement_feed)
        log_record["index_event_id"] = len(settlement_index.by_event_id)
        log_record["index_name"] = len(settlement_index.by_match_name)
        _log_settlement_cycle_summary(
            active_pending,
            priority_sport_keys,
            settlement_feed,
            settlement_index,
        )
        settled, kupon_entries = _settle_pending_kupons(
            active_pending,
            settlement_index,
            settlement_feed,
        )
        log_record["kupons"] = kupon_entries
        stats["settled"] = settled
        stats["waiting"] = max(0, len(active_pending) - settled)
        return stats
    finally:
        log_record["settled"] = stats["settled"]
        log_record["waiting"] = stats["waiting"]
        log_record["pending"] = stats["pending"]
        if _should_persist_settlement_log(log_record, stats):
            append_settlement_cycle_log(log_record)


def run_auto_settler_loop(stop_event: threading.Event | None = None) -> None:
    shutdown_event = stop_event or threading.Event()
    _configure_logging()
    logger.info("Auto settler dongusu baslatildi.")

    while not shutdown_event.is_set():
        try:
            stats = run_settlement_pass()
            sleep_seconds = _next_sleep_seconds(stats)
        except Exception as exc:
            logger.error("Teşhis: Auto settler islem hatasi | %s", exc)
            sleep_seconds = _LIVE_FEED_BACKOFF_SECONDS

        if _interruptible_wait(shutdown_event, sleep_seconds):
            break

    logger.info("Auto settler dongusu durduruldu.")


def main() -> None:
    init_db()
    run_auto_settler_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Auto settler operatör komutu ile durduruldu.")
