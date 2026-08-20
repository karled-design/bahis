from __future__ import annotations

__all__ = (
    "should_skip_fixture_telegram",
    "record_fixture_telegram_sent",
)

from core.match_filters import build_match_id_from_notification_key
from core.notify_snooze import consume_expired_snooze, is_snoozed
from database.db_manager import (
    has_any_kupon_for_fixture,
    was_fixture_recently_notified,
    record_fixture_notification,
)


def should_skip_fixture_telegram(
    notify_key: str,
    mac_adi: str,
    market: str,
    soft_odds: float,
    *,
    odds_change_bypass_pct: float,
    bypass_dedup: bool = False,
) -> tuple[bool, str]:
    if bypass_dedup:
        return False, ""

    if has_any_kupon_for_fixture(mac_adi, market):
        return True, "fixture_kupon_mevcut"

    match_id = build_match_id_from_notification_key(notify_key)
    if is_snoozed(match_id):
        return True, "fixture_ertelendi"
    if consume_expired_snooze(match_id):
        # Erteleme suresi doldu: tek seferlik hatirlatma icin dedup atlanir.
        return False, ""

    if was_fixture_recently_notified(
        notify_key,
        soft_odds,
        odds_change_bypass_pct=odds_change_bypass_pct,
    ):
        return True, "fixture_bildirim_gonderildi"

    return False, ""


def record_fixture_telegram_sent(
    notify_key: str,
    mac_adi: str,
    market: str,
    soft_odds: float,
) -> None:
    record_fixture_notification(notify_key, mac_adi, market, soft_odds)
