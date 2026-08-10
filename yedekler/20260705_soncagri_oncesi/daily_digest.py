"""Gunluk aksam ozeti: her aksam saat 22:00'den (Turkiye) sonra tek Telegram mesaji.

Amac: kullanici panele hic girmese de gunun fotografini cebinde gorsun —
kac sinyal cikti, ne oynandi, ne sonuclandi, butce ne durumda, kanit karnesi.

Tarama motoruna dokunmaz. Ana dongu her turda maybe_send_daily_digest()
cagirir; saat gelmemisse veya bugunku ozet zaten gittiyse aninda geri doner.
Gonderim basarisizsa 10 dakika sonra yeniden dener (spam yok).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.beginner_alert import format_beginner_market_label
from database.db_manager import (
    get_clv_scorecard,
    get_latest_bakiye,
    get_notifications_between,
    get_pending_kupons,
    get_recent_kupons,
    get_settled_kupons_between,
)

__all__ = (
    "DIGEST_HOUR_TR",
    "build_daily_digest_message",
    "is_digest_due",
    "mark_digest_sent",
    "maybe_send_daily_digest",
)

_TR_TZ = ZoneInfo("Europe/Istanbul")
DIGEST_HOUR_TR = 22
_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "daily_digest_state.json"
_RETRY_WAIT_SECONDS = 600.0
_DIGEST_SIGNAL_LIMIT = 12

_last_attempt_monotonic: float = 0.0


def _now_tr(now: datetime | None = None) -> datetime:
    base = now if now is not None else datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(_TR_TZ)


def _today_key(now_tr: datetime) -> str:
    return now_tr.strftime("%Y-%m-%d")


def _fmt_tr_hhmm(iso_ts: str) -> str:
    """UTC ISO zaman damgasini Turkiye saatiyle 'SS:DD' bicimine cevirir."""
    try:
        parsed = datetime.fromisoformat(str(iso_ts).strip())
    except (TypeError, ValueError):
        return "--:--"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_TR_TZ).strftime("%H:%M")


def _load_state() -> dict[str, Any]:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    tmp_path = _STATE_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=True, indent=2)
        os.replace(tmp_path, _STATE_PATH)
    except OSError:
        pass


def is_digest_due(now: datetime | None = None) -> bool:
    now_tr = _now_tr(now)
    if now_tr.hour < DIGEST_HOUR_TR:
        return False
    return str(_load_state().get("date_key", "")) != _today_key(now_tr)


def mark_digest_sent(now: datetime | None = None) -> None:
    now_tr = _now_tr(now)
    _save_state(
        {
            "date_key": _today_key(now_tr),
            "sent_at": now_tr.isoformat(timespec="seconds"),
        }
    )


def _tr_day_window_utc(now_tr: datetime) -> tuple[str, str]:
    """Turkiye gununun [00:00, 24:00) araligini UTC ISO metni olarak dondurur.

    Veritabanindaki zaman damgalari _utc_timestamp() formatindadir
    (ornek: 2026-07-04T00:29:52+00:00); ayni formatta uretilir ki metin
    karsilastirmasi dogru calissin.
    """
    day_start_tr = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = day_start_tr.astimezone(timezone.utc).isoformat(timespec="seconds")
    end_utc = (day_start_tr + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
    return start_utc, end_utc


def build_daily_digest_message(now: datetime | None = None) -> str:
    now_tr = _now_tr(now)
    start_utc, end_utc = _tr_day_window_utc(now_tr)

    signals_today = get_notifications_between(start_utc, end_utc)
    signal_count = len(signals_today)
    settled_today = get_settled_kupons_between(start_utc, end_utc)
    pending = get_pending_kupons()
    played_today = [
        kupon
        for kupon in get_recent_kupons(limit=200)
        if start_utc <= str(kupon.get("eklenme_tarihi", "")) < end_utc
    ]

    lines = [f"[SQE-V1] 🌙 Gunluk ozet · {now_tr.strftime('%d.%m.%Y')}"]

    if signal_count > 0:
        lines.append(f"Sinyal: bugun {signal_count} bildirim gitti")
        for item in signals_today[:_DIGEST_SIGNAL_LIMIT]:
            hhmm = _fmt_tr_hhmm(str(item.get("notified_at", "")))
            mac = str(item.get("mac_adi", "")).strip() or "?"
            market_label = format_beginner_market_label(str(item.get("market", "")))
            lines.append(f"  • {hhmm} {mac} · {market_label}")
        if signal_count > _DIGEST_SIGNAL_LIMIT:
            lines.append(f"  • ... ve {signal_count - _DIGEST_SIGNAL_LIMIT} tane daha")
    else:
        lines.append("Sinyal: bugun filtreden gecen firsat olmadi")

    if played_today:
        played_stake = sum(float(kupon.get("stake", 0.0)) for kupon in played_today)
        lines.append(f"Oynanan: {len(played_today)} kupon · {played_stake:.2f} TL")

    if settled_today:
        won_count = sum(1 for kupon in settled_today if kupon["durum"] == "WON")
        lost_count = len(settled_today) - won_count
        net_profit = 0.0
        for kupon in settled_today:
            stake = float(kupon["stake"])
            if kupon["durum"] == "WON":
                net_profit += stake * (float(kupon["soft_oran"]) - 1.0)
            else:
                net_profit -= stake
        result_emoji = "🟢" if net_profit >= 0 else "🔴"
        lines.append(
            f"Sonuclanan: {won_count} kazandi, {lost_count} kaybetti · "
            f"{result_emoji} net {net_profit:+.2f} TL"
        )

    if pending:
        pending_stake = sum(float(kupon.get("stake", 0.0)) for kupon in pending)
        lines.append(
            f"Bekleyen: {len(pending)} kupon · {pending_stake:.2f} TL (sonuclaninca haber veririm)"
        )

    lines.append(f"Guncel butce: {get_latest_bakiye():.2f} TL")

    karne = get_clv_scorecard()
    olculen = int(karne.get("olculen", 0))
    if olculen > 0:
        lehte = int(karne.get("lehte", 0))
        lehte_pct = round(float(karne.get("lehte_oran", 0.0)) * 100.0)
        ort = float(karne.get("ort_clv_pct", 0.0))
        lines.append(
            f"Kanit karnesi: {olculen} olcumun {lehte}'i lehimize (%{lehte_pct}) · ort. %{ort:+.1f}"
        )
    else:
        lines.append("Kanit karnesi: henuz olculen kupon yok")

    lines.append("Iyi aksamlar! 👋")
    return "\n".join(lines)


def maybe_send_daily_digest(send_func: Callable[[str], bool]) -> bool:
    """Saat geldiyse ve bugun gonderilmediyse ozeti kurar ve yollar.

    Ana dongude her turda cagrilmasi guvenlidir: saat/durum uygun degilse
    hicbir is yapmadan doner; basarisiz denemeden sonra 10 dk bekler.
    """
    global _last_attempt_monotonic

    if not is_digest_due():
        return False
    if _last_attempt_monotonic > 0.0 and (time.monotonic() - _last_attempt_monotonic) < _RETRY_WAIT_SECONDS:
        return False
    _last_attempt_monotonic = time.monotonic()

    try:
        message = build_daily_digest_message()
    except Exception as exc:  # ozet kurulamazsa motoru asla dusurme
        print(f"[SQE-V1] Gunluk ozet olusturulamadi | {exc}")
        return False

    if send_func(message):
        mark_digest_sent()
        print("[SQE-V1] Gunluk aksam ozeti gonderildi")
        return True
    return False
