from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, TypedDict

from config.settings import PILOT_MODE, SCAN_INTERVAL_SECONDS, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from core.match_filters import is_virtual_match_text
from core.operator_risk_settings import get_action_ev_threshold, get_watch_ev_threshold
from core.passion_engine import calculate_expected_value, get_active_min_ev_threshold
from core.beginner_alert import build_beginner_play_confirmation, build_beginner_settlement_notice, market_code_from_label, build_beginner_settlement_notice
from core.kasa_sync import refresh_alert_budget_lines
from core.time_utils import parse_utc
from database.db_manager import (
    add_kupon,
    get_latest_bakiye,
    get_performance_stats,
    has_any_kupon_for_event,
    has_any_kupon_for_fixture,
    save_bakiye,
)
from ui.web_server import SISTEM_DURUMU, update_sistem_durumu

__all__ = (
    "send_alert",
    "send_scan_empty_report",
    "send_system_ready",
    "wait_for_scan_start",
    "start_telegram_listener",
    "stop_telegram_listener",
    "purge_stale_alert_state",
    "drain_pending_bot_queue",
    "begin_scan_cycle",
    "arm_qualifying_alerts",
    "send_scan_empty_report_for_cycle",
    "send_scan_cycle_summary_for_cycle",
    "build_last_scan_panel_payload",
    "send_hero_daily_notice",
)

# Telegram yalnizca oynanabilir / izle onerileri gonderir; funnel ve bos tur ozetleri panele gider.
TELEGRAM_RECOMMENDATIONS_ONLY = True

_OPERATOR_DIAG = "Donanim Erisilemiyor: Telegram API Hatasi"
_KUPON_DIAG = "Donanim Erisilemiyor: Kupon Onay Hatasi"
_SEND_MESSAGE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
_GET_UPDATES_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
_DELETE_WEBHOOK_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook"
_ANSWER_CALLBACK_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
_EDIT_REPLY_MARKUP_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup"
_REQUEST_TIMEOUT_SECONDS = 10.0
_GET_UPDATES_POLL_SECONDS = 25
_GET_UPDATES_SOCKET_TIMEOUT_SECONDS = _GET_UPDATES_POLL_SECONDS + 10
_TELEGRAM_RECONNECT_FAILURE_THRESHOLD = 3
_CONFLICT_HEAL_DELAY_SECONDS = 3.0
_CONFLICT_HEAL_DIAG = "Teşhis: Telegram çakışması otonom olarak onarıldı"
_CONFLICT_HEAL_ESCALATION_DIAG = (
    "Teşhis: Telegram cakismasi suruyor. Motoru durdurup calistirin: "
    "PYTHONPATH=. python reset_telegram.py"
)
_CONFLICT_HEAL_ESCALATION_THRESHOLD = 5
_CONFLICT_HEAL_COUNT = 0
_MAX_CALLBACK_DATA_BYTES = 64
_START_SCAN_CALLBACK = "start_scan"
_ALERT_TTL_SECONDS = 900
_DEFAULT_SHARP_ORAN = 1.50

_CMD_START = "[🚀 Taramayı Başlat]"
_CMD_STOP = "[🛑 Taramayı Durdur]"
_START_ALIASES = {_CMD_START, "🚀 Taramayı Başlat"}
_STOP_ALIASES = {_CMD_STOP, "🛑 Taramayı Durdur"}

_LISTENER_THREAD: threading.Thread | None = None
_LISTENER_STOP = threading.Event()
_UPDATE_OFFSET: int | None = None
_OFFSET_LOCK = threading.Lock()
_ALERT_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}
_ALERT_CACHE_LOCK = threading.Lock()
_ACTIVE_SCAN_CYCLE_ID = ""
_ALERT_DISPATCH_ENABLED = False
_QUALIFYING_ALERT_KEYS: set[str] = set()
_TELEGRAM_CONSECUTIVE_FAILURES = 0
_PROCESSED_CALLBACK_IDS: dict[str, float] = {}
_PROCESSED_CALLBACK_LOCK = threading.Lock()
_CALLBACK_ID_TTL_SECONDS = 3600.0


class ScanCycleStats(TypedDict):
    birlesik: int
    efutbol: int
    stale: int
    tolerans: int
    pasif_ev: int
    absurd_ev: int
    suspicious_match: int
    context_filter: int
    watch: int
    action: int
    high: int
    aday: int
    telegram: int
    izle: int
    notify_gate: int
    cooldown: int


def _emit_operator_diag(detail: str) -> None:
    print(f"{_OPERATOR_DIAG}: {detail}", file=sys.stderr)


def _is_long_poll_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason if exc.reason is not None else exc
        return "timed out" in str(reason).casefold()
    return "timed out" in str(exc).casefold()


def _note_telegram_transport_success() -> None:
    global _TELEGRAM_CONSECUTIVE_FAILURES
    _TELEGRAM_CONSECUTIVE_FAILURES = 0


def _note_telegram_transport_failure(detail: str) -> None:
    global _TELEGRAM_CONSECUTIVE_FAILURES

    _TELEGRAM_CONSECUTIVE_FAILURES += 1
    _emit_operator_diag(detail)
    if _TELEGRAM_CONSECUTIVE_FAILURES >= _TELEGRAM_RECONNECT_FAILURE_THRESHOLD:
        print(
            f"[SQE-V1] Telegram yeniden baglaniyor | ardisik_hata={_TELEGRAM_CONSECUTIVE_FAILURES}",
            file=sys.stderr,
        )
        _prepare_polling_session()
        _TELEGRAM_CONSECUTIVE_FAILURES = 0


def _emit_kupon_diag(detail: str) -> None:
    print(f"{_KUPON_DIAG}: {detail}", file=sys.stderr)


def _telegram_get(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if parsed.get("ok") is not True:
        return None
    return parsed


def _delete_webhook(drop_pending_updates: bool = True) -> bool:
    query = urllib.parse.urlencode(
        {"drop_pending_updates": "true" if drop_pending_updates else "false"}
    )
    return _telegram_get(f"{_DELETE_WEBHOOK_URL}?{query}") is not None


def _prepare_polling_session() -> None:
    from notifiers.telegram_reset import reset_telegram_api_session

    reset_telegram_api_session(drop_pending_updates=True)


def _self_heal_telegram_conflict() -> None:
    global _CONFLICT_HEAL_COUNT

    from notifiers.telegram_reset import drain_telegram_update_queue, reset_telegram_api_session

    time.sleep(_CONFLICT_HEAL_DELAY_SECONDS)
    reset_telegram_api_session(drop_pending_updates=True)
    drain_telegram_update_queue()
    _CONFLICT_HEAL_COUNT += 1
    print(_CONFLICT_HEAL_DIAG)
    if _CONFLICT_HEAL_COUNT >= _CONFLICT_HEAL_ESCALATION_THRESHOLD:
        print(_CONFLICT_HEAL_ESCALATION_DIAG, file=sys.stderr)


def _is_telegram_conflict_error(
    exc: BaseException | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 409:
        return True
    if payload is not None and payload.get("ok") is not True:
        description = str(payload.get("description", "")).lower()
        if "conflict" in description:
            return True
    return False


def _telegram_post(url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        detail = f"HTTP {exc.code} {exc.reason}"
        if err_body:
            detail = f"{detail} | {err_body}"
        _emit_operator_diag(detail)
        return None
    except urllib.error.URLError as exc:
        reason = exc.reason if exc.reason is not None else exc
        _emit_operator_diag(str(reason))
        return None
    except TimeoutError:
        _emit_operator_diag("request timeout")
        return None
    except OSError as exc:
        _emit_operator_diag(str(exc))
        return None

    if status_code is None or status_code < 200 or status_code >= 300:
        _emit_operator_diag(f"unexpected HTTP status {status_code}")
        return None

    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        _emit_operator_diag("non-JSON response body")
        return None

    if parsed.get("ok") is not True:
        description = parsed.get("description", "ok=false")
        _emit_operator_diag(str(description))
        return None

    return parsed


def _build_operator_reply_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": _CMD_START}, {"text": _CMD_STOP}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _normalize_match_id(match_id: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in match_id.strip()
    )
    return cleaned or "mac"


def _fit_callback_data(data: str) -> str:
    encoded = data.encode("utf-8")
    if len(encoded) <= _MAX_CALLBACK_DATA_BYTES:
        return data
    trimmed = data
    while trimmed and len(trimmed.encode("utf-8")) > _MAX_CALLBACK_DATA_BYTES:
        trimmed = trimmed[:-1]
    return trimmed or "skip_mac"


def _build_inline_keyboard(match_id: str, stake: float) -> dict[str, Any]:
    safe_match_id = _normalize_match_id(match_id)
    stake_value = round(float(stake), 2)
    play_callback = _fit_callback_data(f"play_{safe_match_id}_{stake_value}")
    skip_callback = _fit_callback_data(f"skip_{safe_match_id}")
    play_label = "✅ Oynadım"
    return {
        "inline_keyboard": [
            [
                {"text": play_label, "callback_data": play_callback},
                {"text": "❌ Pas Geç", "callback_data": skip_callback},
            ]
        ]
    }


def _build_message_payload(
    text: str,
    match_id: str | None,
    stake: float | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    # Test modunda "Oyna" dugmesi gosterilmez: sanal kupon otomatik kaydedilir,
    # dugme yalnizca kafa karistirir. Gercek modda (PILOT_MODE=False) aynen kalir.
    if match_id is not None and stake is not None and not PILOT_MODE:
        payload["reply_markup"] = _build_inline_keyboard(str(match_id), float(stake))
    return payload


def _parse_alert_message(message_text: str) -> dict[str, Any]:
    mac_adi = "Bilinmeyen Mac"
    market = "MS1"
    soft_oran = 2.0
    sharp_oran = _DEFAULT_SHARP_ORAN
    sharp_parsed = False

    stripped_text = message_text.strip()
    if stripped_text.startswith("[SQE-V1] Alarm |"):
        for segment in stripped_text.split("|"):
            part = segment.strip()
            if part.startswith("mac="):
                mac_adi = part.split("=", 1)[1].strip() or mac_adi
            elif part.startswith("market="):
                market = part.split("=", 1)[1].strip() or market
            elif part.startswith("soft="):
                try:
                    soft_oran = float(part.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif part.startswith("sharp="):
                try:
                    sharp_oran = float(part.split("=", 1)[1].strip())
                    sharp_parsed = True
                except ValueError:
                    pass
        if not sharp_parsed and soft_oran > 1.0:
            sharp_oran = max(round(soft_oran / 1.25, 2), 1.01)
        return {
            "mac_adi": mac_adi,
            "market": market,
            "soft_oran": soft_oran,
            "sharp_oran": sharp_oran,
        }

    for line in message_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[SQE-V1]"):
            continue
        if "Maç:" in stripped:
            mac_adi = stripped.split("Maç:", 1)[1].strip() or mac_adi
        elif stripped.startswith("Mac:"):
            mac_adi = stripped.split(":", 1)[1].strip() or mac_adi
        elif " - " in stripped and not stripped.startswith(
            ("Mac ", "Lig:", "Bahis", "Nesine", "Guncel", "Oynanacak", "Nasil", "Kisa", "Tarama", "Bu mesaj", "(Butce", "Nesine'de oynadiktan", "Sizin ekstra")
        ):
            mac_adi = stripped or mac_adi
        elif "Seçenek:" in stripped:
            market = stripped.split("Seçenek:", 1)[1].strip() or market
        elif stripped.startswith("Bahis turu:"):
            market = market_code_from_label(stripped.split(":", 1)[1].strip())
        elif "Yasal Oran:" in stripped:
            try:
                soft_oran = float(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif stripped.startswith("Nesine orani:"):
            try:
                soft_oran = float(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass

    if soft_oran > 1.0:
        sharp_oran = max(round(soft_oran / 1.25, 2), 1.01)

    return {
        "mac_adi": mac_adi,
        "market": market,
        "soft_oran": soft_oran,
        "sharp_oran": sharp_oran,
    }


def _cache_alert_context(
    match_id: str,
    message_text: str,
    soft_odds: float | None = None,
    *,
    mac_adi: str | None = None,
    market: str | None = None,
    sport_key: str | None = None,
    event_id: str | None = None,
    commence_time: str | None = None,
    stake: float | None = None,
) -> None:
    safe_id = _normalize_match_id(match_id)
    parsed = _parse_alert_message(message_text)
    if isinstance(mac_adi, str) and mac_adi.strip():
        parsed["mac_adi"] = mac_adi.strip()
    if isinstance(market, str) and market.strip():
        parsed["market"] = market.strip().upper()
    if soft_odds is not None and not isinstance(soft_odds, bool) and isinstance(soft_odds, (int, float)):
        parsed["soft_oran"] = float(soft_odds)
    # Onerilen tutar (panel bildirim akisi icin saklanir; Telegram callback'i
    # tutari yine callback_data'dan okur, buradaki deger yalnizca gosterimlik).
    stake_value: float | None = None
    if stake is not None and not isinstance(stake, bool) and isinstance(stake, (int, float)):
        stake_value = float(stake)
    with _ALERT_CACHE_LOCK:
        _ALERT_CONTEXT_CACHE[safe_id] = {
            "match_id": match_id.strip(),
            "mac_adi": parsed["mac_adi"],
            "market": parsed["market"],
            "soft_oran": float(parsed["soft_oran"]),
            "sharp_oran": float(parsed["sharp_oran"]),
            "sport_key": sport_key.strip() if isinstance(sport_key, str) else "",
            "event_id": event_id.strip() if isinstance(event_id, str) else "",
            "commence_time": (
                commence_time.strip() if isinstance(commence_time, str) else ""
            ),
            "stake": stake_value,
            "cached_at": time.time(),
        }


def _get_alert_context(match_id: str, message_text: str) -> dict[str, Any]:
    safe_id = _normalize_match_id(match_id)
    with _ALERT_CACHE_LOCK:
        cached = _ALERT_CONTEXT_CACHE.get(safe_id)
    if cached is not None:
        return dict(cached)
    parsed = _parse_alert_message(message_text)
    return {
        "match_id": match_id,
        "mac_adi": str(parsed["mac_adi"]),
        "market": str(parsed["market"]),
        "soft_oran": float(parsed["soft_oran"]),
        "sharp_oran": float(parsed["sharp_oran"]),
        "sport_key": "",
        "event_id": "",
        "commence_time": "",
    }


def get_active_recommendations() -> list[dict[str, Any]]:
    """Panel bildirim akisi icin su an gecerli bahis onerilerini dondurur.

    Salt-okuma: yalnizca hafizadaki alarm onbelleginden (_ALERT_CONTEXT_CACHE)
    turetilir; hicbir sey yazmaz, hicbir kupon/butce islemez. Suresi dolmus
    (TTL) oneriler gosterilmez. Telegram'daki mesajla ayni pazar etiketini
    kullanmak icin beginner_alert yardimcisi cagirilir.
    """
    from core.beginner_alert import format_beginner_market_label

    now = time.time()
    with _ALERT_CACHE_LOCK:
        entries = list(_ALERT_CONTEXT_CACHE.values())

    items: list[dict[str, Any]] = []
    for entry in entries:
        cached_at = float(entry.get("cached_at", 0.0) or 0.0)
        if cached_at > 0.0 and (now - cached_at) >= _ALERT_TTL_SECONDS:
            continue  # suresi dolmus oneri panelde gosterilmez
        market = str(entry.get("market", "") or "")
        try:
            market_label = format_beginner_market_label(market)
        except Exception:
            market_label = market
        raw_stake = entry.get("stake")
        stake_value: float | None = None
        if raw_stake is not None and not isinstance(raw_stake, bool) and isinstance(raw_stake, (int, float)):
            stake_value = round(float(raw_stake), 2)
        items.append(
            {
                "match_id": str(entry.get("match_id", "") or ""),
                "mac_adi": str(entry.get("mac_adi", "") or ""),
                "market": market,
                "market_label": market_label,
                "soft_oran": float(entry.get("soft_oran", 0.0) or 0.0),
                "stake": stake_value,
                "commence_time": str(entry.get("commence_time", "") or ""),
                "cached_at": cached_at,
            }
        )

    # Maca en yakin (commence_time bilinen) oneriler ustte; zamansizlar sona.
    items.sort(key=lambda it: (0, it["commence_time"]) if it["commence_time"] else (1, ""))
    return items


def _is_operator_chat(chat_id: object) -> bool:
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


def _set_scan_enabled(enabled: bool) -> None:
    update_sistem_durumu(scan_enabled=enabled)


def _answer_callback(callback_id: object, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = False
    _telegram_post(_ANSWER_CALLBACK_URL, payload)


def _clear_inline_buttons(callback: dict[str, Any]) -> None:
    message = callback.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return
    _telegram_post(
        _EDIT_REPLY_MARKUP_URL,
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        },
    )


def _send_operator_notice(text: str) -> None:
    _telegram_post(
        _SEND_MESSAGE_URL,
        {"chat_id": TELEGRAM_CHAT_ID, "text": text},
    )


def _parse_play_callback(callback_data: str) -> tuple[str, float] | None:
    if not callback_data.startswith("play_"):
        return None
    body = callback_data[5:]
    if "_" not in body:
        return None
    match_part, stake_part = body.rsplit("_", 1)
    try:
        stake = float(stake_part)
    except ValueError:
        return None
    if not match_part or stake <= 0.0:
        return None
    return match_part, stake


def _parse_skip_callback(callback_data: str) -> str | None:
    if not callback_data.startswith("skip_"):
        return None
    match_part = callback_data[5:]
    return match_part or None


def _claim_callback_once(callback_id: str | None) -> bool:
    if not isinstance(callback_id, str) or not callback_id.strip():
        return True
    now = time.time()
    with _PROCESSED_CALLBACK_LOCK:
        expired = [
            key
            for key, seen_at in _PROCESSED_CALLBACK_IDS.items()
            if (now - seen_at) >= _CALLBACK_ID_TTL_SECONDS
        ]
        for key in expired:
            del _PROCESSED_CALLBACK_IDS[key]
        if callback_id in _PROCESSED_CALLBACK_IDS:
            return False
        _PROCESSED_CALLBACK_IDS[callback_id] = now
        return True


def _is_duplicate_play(context: dict[str, Any]) -> bool:
    market = str(context.get("market", "")).strip()
    mac_adi = str(context.get("mac_adi", "")).strip()
    event_id = str(context.get("event_id", "")).strip()
    if event_id and has_any_kupon_for_event(event_id, market):
        return True
    if mac_adi and market:
        return has_any_kupon_for_fixture(mac_adi, market)
    return False


def _handle_play_callback(callback: dict[str, Any], callback_data: str) -> bool:
    parsed = _parse_play_callback(callback_data)
    if parsed is None:
        _emit_kupon_diag(f"invalid play callback | {callback_data}")
        return False

    match_key, stake = parsed
    if is_virtual_match_text(match_key):
        _emit_kupon_diag(f"E-Futbol callback engellendi | {callback_data}")
        _clear_inline_buttons(callback)
        return False

    message = callback.get("message")
    message_text = ""
    if isinstance(message, dict) and isinstance(message.get("text"), str):
        message_text = message.get("text", "")

    try:
        context = _get_alert_context(match_key, message_text)
        if is_virtual_match_text(context.get("mac_adi"), context.get("market")):
            _emit_kupon_diag(f"E-Futbol callback engellendi | {callback_data}")
            _clear_inline_buttons(callback)
            return False

        cached_at = float(context.get("cached_at", 0.0))
        if cached_at > 0.0 and (time.time() - cached_at) >= _ALERT_TTL_SECONDS:
            _emit_kupon_diag(f"suresi dolmus callback engellendi | {callback_data}")
            _clear_inline_buttons(callback)
            purge_stale_alert_state()
            return False

        if _is_duplicate_play(context):
            _emit_kupon_diag(
                f"duplicate play blocked | mac={context['mac_adi']} | market={context['market']}"
            )
            _send_operator_notice(
                "Bu mac ve bahis icin test kuponu zaten kayitli. Ayni maca tekrar bildirim gelmez."
            )
            _clear_inline_buttons(callback)
            return False

        from core.kupon_live_guard import is_live_kupon_metadata

        live_ok, live_reason = is_live_kupon_metadata(
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
            sport_key=str(context.get("sport_key", "")),
            mac_adi=str(context.get("mac_adi", "")),
        )
        if not live_ok:
            _emit_kupon_diag(
                f"canli metadata eksik | mac={context.get('mac_adi')} | reason={live_reason}"
            )
            _send_operator_notice(
                "Bu bildirim canli tarama kaniti tasimiyor; kupon kaydedilmedi. "
                "Yeni tarama sonrasi gelen guncel mesajdan tekrar deneyin."
            )
            _clear_inline_buttons(callback)
            return False

        current_kasa = get_latest_bakiye()
        if stake > current_kasa:
            _emit_kupon_diag(
                f"insufficient balance | kasa={current_kasa} | stake={stake}"
            )
            _send_operator_notice("Yetersiz bakiye. Kupon islenemedi.")
            return False

        new_kasa = round(current_kasa - stake, 2)
        if not save_bakiye(new_kasa, f"Kupon yatirimi | {context['mac_adi']} | -{stake} TL"):
            _emit_kupon_diag("save_bakiye failed after stake deduction")
            return False

        saved = add_kupon(
            match_id=str(context["match_id"]),
            mac_adi=str(context["mac_adi"]),
            market=str(context["market"]),
            stake=stake,
            soft_oran=float(context["soft_oran"]),
            sharp_oran=float(context["sharp_oran"]),
            ev_at_alert=calculate_expected_value(
                float(context["sharp_oran"]),
                float(context["soft_oran"]),
            ),
            sport_key=str(context.get("sport_key", "")),
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
        )
        if not saved:
            save_bakiye(current_kasa, f"Kupon iptal iadesi | {context['mac_adi']}")
            _emit_kupon_diag("add_kupon failed, balance rolled back")
            return False

        update_sistem_durumu(
            total_kasa=new_kasa,
            performance=get_performance_stats(),
        )
        _clear_inline_buttons(callback)
        _send_operator_notice(
            build_beginner_play_confirmation(
                mac_adi=str(context["mac_adi"]),
                market=str(context["market"]),
                stake=stake,
                new_kasa=new_kasa,
            )
        )
        _schedule_settlement_check()
        return True
    except (TypeError, ValueError, KeyError) as exc:
        _emit_kupon_diag(f"play flow failed | {exc}")
        return False


def _handle_skip_callback(callback: dict[str, Any], callback_data: str) -> bool:
    match_key = _parse_skip_callback(callback_data)
    if match_key is None:
        _emit_kupon_diag(f"invalid skip callback | {callback_data}")
        return False

    if is_virtual_match_text(match_key):
        _emit_kupon_diag(f"E-Futbol callback engellendi | {callback_data}")
        _clear_inline_buttons(callback)
        purge_stale_alert_state()
        return False

    try:
        _clear_inline_buttons(callback)
        _send_operator_notice("Pas gecildi")
        return True
    except (TypeError, ValueError, KeyError) as exc:
        _emit_kupon_diag(f"skip flow failed | {exc}")
        return False


def play_recommendation_from_panel(
    match_id: str,
    alinan_oran: float | None = None,
    stake: float | None = None,
) -> dict[str, Any]:
    """Panelden 'Oynadim': hafizadaki sinyalden kupon olusturur.

    Telegram callback akisini HIC degistirmeden ayni dusuk-seviye primitifleri
    kullanir (onbellek + canli kontrol + kasa + add_kupon). 'alinan_oran'
    verilirse kupona kullanicinin Nesine'de GERCEKTE girdigi oran islenir.
    Sozluk doner: {"ok": bool, "reason"/"mac_adi"/"stake"/"new_kasa"/...}.
    """
    if not isinstance(match_id, str) or not match_id.strip():
        return {"ok": False, "reason": "gecersiz_mac"}
    if is_virtual_match_text(match_id):
        return {"ok": False, "reason": "efutbol_engellendi"}
    if alinan_oran is not None:
        if (
            isinstance(alinan_oran, bool)
            or not isinstance(alinan_oran, (int, float))
            or float(alinan_oran) <= 1.0
        ):
            return {"ok": False, "reason": "gecersiz_oran"}

    try:
        context = _get_alert_context(match_id, "")
        if is_virtual_match_text(context.get("mac_adi"), context.get("market")):
            return {"ok": False, "reason": "efutbol_engellendi"}

        cached_at = float(context.get("cached_at", 0.0))
        if cached_at <= 0.0:
            return {"ok": False, "reason": "sinyal_bulunamadi"}
        if (time.time() - cached_at) >= _ALERT_TTL_SECONDS:
            purge_stale_alert_state()
            return {"ok": False, "reason": "sinyal_suresi_doldu"}

        if _is_duplicate_play(context):
            return {"ok": False, "reason": "zaten_kayitli"}

        from core.kupon_live_guard import is_live_kupon_metadata

        live_ok, live_reason = is_live_kupon_metadata(
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
            sport_key=str(context.get("sport_key", "")),
            mac_adi=str(context.get("mac_adi", "")),
        )
        if not live_ok:
            _emit_kupon_diag(f"panel play | canli metadata eksik | {live_reason}")
            return {"ok": False, "reason": "canli_kanit_yok"}

        # Tutar: panel override > onbellekteki onerilen tutar.
        resolved_stake: float | None = None
        if (
            stake is not None
            and not isinstance(stake, bool)
            and isinstance(stake, (int, float))
            and float(stake) > 0.0
        ):
            resolved_stake = round(float(stake), 2)
        else:
            ctx_stake = context.get("stake")
            if (
                ctx_stake is not None
                and not isinstance(ctx_stake, bool)
                and isinstance(ctx_stake, (int, float))
                and float(ctx_stake) > 0.0
            ):
                resolved_stake = round(float(ctx_stake), 2)
        if resolved_stake is None or resolved_stake <= 0.0:
            return {"ok": False, "reason": "tutar_yok"}

        current_kasa = get_latest_bakiye()
        if resolved_stake > current_kasa:
            return {"ok": False, "reason": "yetersiz_bakiye"}

        new_kasa = round(current_kasa - resolved_stake, 2)
        if not save_bakiye(
            new_kasa,
            f"Kupon yatirimi (panel) | {context['mac_adi']} | -{resolved_stake} TL",
        ):
            _emit_kupon_diag("panel play | save_bakiye basarisiz")
            return {"ok": False, "reason": "bakiye_yazilamadi"}

        saved = add_kupon(
            match_id=str(context["match_id"]),
            mac_adi=str(context["mac_adi"]),
            market=str(context["market"]),
            stake=resolved_stake,
            soft_oran=float(context["soft_oran"]),
            sharp_oran=float(context["sharp_oran"]),
            ev_at_alert=calculate_expected_value(
                float(context["sharp_oran"]),
                float(context["soft_oran"]),
            ),
            sport_key=str(context.get("sport_key", "")),
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
            alinan_oran=alinan_oran,
        )
        if not saved:
            save_bakiye(current_kasa, f"Kupon iptal iadesi (panel) | {context['mac_adi']}")
            _emit_kupon_diag("panel play | add_kupon basarisiz, bakiye iade edildi")
            return {"ok": False, "reason": "kupon_yazilamadi"}

        # Oynanan sinyali onbellekten cikar: listeden kalksin, tekrar oynanmasin.
        with _ALERT_CACHE_LOCK:
            _ALERT_CONTEXT_CACHE.pop(_normalize_match_id(match_id), None)

        update_sistem_durumu(
            total_kasa=new_kasa,
            performance=get_performance_stats(),
        )
        _schedule_settlement_check()
        return {
            "ok": True,
            "mac_adi": str(context["mac_adi"]),
            "market": str(context["market"]),
            "stake": resolved_stake,
            "new_kasa": new_kasa,
            "alinan_oran": (round(float(alinan_oran), 2) if alinan_oran is not None else None),
        }
    except (TypeError, ValueError, KeyError) as exc:
        _emit_kupon_diag(f"panel play flow failed | {exc}")
        return {"ok": False, "reason": "beklenmeyen_hata"}


def skip_recommendation_from_panel(match_id: str) -> dict[str, Any]:
    """Panelden 'Pas Gec': oneriyi hafiza onbelleginden cikarir.

    Boylece oneri panel listesinden kalkar. Kasa/kupona dokunmaz.
    """
    if not isinstance(match_id, str) or not match_id.strip():
        return {"ok": False, "reason": "gecersiz_mac"}
    safe_id = _normalize_match_id(match_id)
    with _ALERT_CACHE_LOCK:
        existed = _ALERT_CONTEXT_CACHE.pop(safe_id, None) is not None
    return {"ok": True, "removed": existed}


def _schedule_settlement_check() -> None:
    def _run() -> None:
        try:
            from auto_settler import run_settlement_pass

            run_settlement_pass()
        except Exception as exc:
            _emit_kupon_diag(f"settlement check failed | {exc}")

    threading.Thread(target=_run, name="SQE-SettlementKick", daemon=True).start()


def _process_callback_query(callback: dict[str, Any]) -> bool:
    callback_data = callback.get("data")
    callback_id = callback.get("id")

    if callback_id is not None:
        _answer_callback(callback_id)

    if not _claim_callback_once(str(callback_id) if callback_id is not None else None):
        _emit_kupon_diag(f"duplicate callback ignored | id={callback_id}")
        return False

    if not isinstance(callback_data, str):
        return False

    if callback_data == _START_SCAN_CALLBACK:
        _set_scan_enabled(True)
        return True

    if callback_data.startswith("play_"):
        return _handle_play_callback(callback, callback_data)

    if callback_data.startswith("skip_"):
        return _handle_skip_callback(callback, callback_data)

    return False


def _process_message(message: dict[str, Any]) -> bool:
    chat = message.get("chat")
    if not isinstance(chat, dict) or not _is_operator_chat(chat.get("id")):
        return False

    text = message.get("text")
    if not isinstance(text, str):
        return False

    normalized = text.strip()
    if normalized in _START_ALIASES:
        _set_scan_enabled(True)
        return True
    if normalized in _STOP_ALIASES:
        _set_scan_enabled(False)
        return True
    return False


def _process_update(update: dict[str, Any]) -> bool:
    try:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return _process_callback_query(callback)

        message = update.get("message")
        if isinstance(message, dict):
            return _process_message(message)
    except (TypeError, ValueError, KeyError) as exc:
        _emit_kupon_diag(f"update process failed | {exc}")
    return False


def _fetch_updates(timeout_seconds: int = _GET_UPDATES_POLL_SECONDS) -> list[dict[str, Any]]:
    global _UPDATE_OFFSET

    with _OFFSET_LOCK:
        offset = _UPDATE_OFFSET

    query: dict[str, Any] = {"timeout": timeout_seconds}
    if offset is not None:
        query["offset"] = offset

    request_url = f"{_GET_UPDATES_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(request_url, method="GET")

    payload: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(
            request,
            timeout=_GET_UPDATES_SOCKET_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if _is_telegram_conflict_error(exc):
            _self_heal_telegram_conflict()
            return []
        _note_telegram_transport_failure(f"getUpdates | HTTP {exc.code} {exc.reason}")
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if _is_long_poll_timeout(exc):
            return []
        _note_telegram_transport_failure(f"getUpdates | {exc}")
        return []

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _note_telegram_transport_failure("getUpdates | non-JSON response body")
        return []

    if (payload or {}).get("ok") is not True:
        if _is_telegram_conflict_error(payload=payload):
            _self_heal_telegram_conflict()
            return []
        _note_telegram_transport_failure(
            f"getUpdates | {(payload or {}).get('description', 'ok=false')}"
        )
        return []

    _note_telegram_transport_success()

    updates = (payload or {}).get("result")
    if not isinstance(updates, list):
        return []

    with _OFFSET_LOCK:
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                if _UPDATE_OFFSET is None or update_id >= _UPDATE_OFFSET:
                    _UPDATE_OFFSET = update_id + 1

    return [item for item in updates if isinstance(item, dict)]


def _send_play_reminder(context: dict[str, Any], lead_seconds: float, *, final: bool) -> None:
    mac = str(context.get("mac_adi", "")).strip() or "Mac"
    market = str(context.get("market", "")).strip()
    dakika = max(1, int(lead_seconds // 60))
    baslik = "⏰ SON HATIRLATMA" if final else "⏰ Hatirlatma"
    _send_operator_notice(
        f"{baslik} | {mac} | {market} | maca ~{dakika} dk kaldi. "
        "Hala oynamadin — oynayacaksan yukaridaki bildirimde 'Oyna'ya bas."
    )


def process_pending_reminders(*, now: float | None = None) -> int:
    """Gercek modda gonderilmis ama oynanmamis bildirimleri uygun anda hatirlatir.

    Iki dalga: (1) ilk bildirimden ~5 dk sonra, (2) maca ~10 dk kala son dürtme.
    Test modunda (PILOT_MODE) hicbir sey yapmaz; test kuponlari otomatik kaydedilir.
    Bir hatirlatma cikinca orijinal 'Oyna' dugmesinin gecerlilik suresi yenilenir.
    """
    if PILOT_MODE:
        return 0

    current = now if now is not None else time.time()
    with _ALERT_CACHE_LOCK:
        keys = list(_ALERT_CONTEXT_CACHE.keys())

    sent = 0
    for key in keys:
        with _ALERT_CACHE_LOCK:
            entry = _ALERT_CONTEXT_CACHE.get(key)
            context = dict(entry) if entry is not None else None
        if context is None:
            continue

        lead = _seconds_to_kickoff(str(context.get("commence_time", "")), now=current)
        if lead is None:
            continue  # zaman bilinmiyor -> dokunma
        if lead <= 0:
            with _ALERT_CACHE_LOCK:
                _ALERT_CONTEXT_CACHE.pop(key, None)  # mac basladi -> dustur
            continue
        if _is_duplicate_play(context):
            with _ALERT_CACHE_LOCK:
                _ALERT_CONTEXT_CACHE.pop(key, None)  # oynandi -> dustur
            continue

        age = current - float(context.get("cached_at", current))
        fire_final = (not context.get("reminder_final_sent")) and lead <= _REMINDER_FINAL_LEAD_SECONDS
        fire_first = (
            (not context.get("reminder_first_sent"))
            and not fire_final
            and lead > _REMINDER_FINAL_LEAD_SECONDS
            and age >= _REMINDER_FIRST_AFTER_SECONDS
        )
        if not (fire_final or fire_first):
            continue

        _send_play_reminder(context, lead, final=fire_final)
        with _ALERT_CACHE_LOCK:
            live = _ALERT_CONTEXT_CACHE.get(key)
            if live is not None:
                if fire_final:
                    live["reminder_final_sent"] = True
                else:
                    live["reminder_first_sent"] = True
                live["cached_at"] = current  # 'Oyna' dugmesinin TTL'ini yenile
        sent += 1
    return sent


def _listener_loop(poll_interval_seconds: float) -> None:
    global _last_reminder_sweep_at
    while not _LISTENER_STOP.is_set():
        updates = _fetch_updates(timeout_seconds=_GET_UPDATES_POLL_SECONDS)
        for update in updates:
            _process_update(update)
        sweep_now = time.time()
        if sweep_now - _last_reminder_sweep_at >= _REMINDER_SWEEP_INTERVAL_SECONDS:
            _last_reminder_sweep_at = sweep_now
            try:
                process_pending_reminders(now=sweep_now)
            except Exception as exc:
                _emit_operator_diag(f"hatirlatma taramasi hatasi | {exc}")
        if not updates:
            time.sleep(poll_interval_seconds)


def start_telegram_listener(poll_interval_seconds: float = 1.0) -> None:
    global _LISTENER_THREAD

    if _LISTENER_THREAD is not None and _LISTENER_THREAD.is_alive():
        return

    _prepare_polling_session()
    _LISTENER_STOP.clear()
    _LISTENER_THREAD = threading.Thread(
        target=_listener_loop,
        args=(poll_interval_seconds,),
        name="SQE-TelegramListener",
        daemon=True,
    )
    _LISTENER_THREAD.start()


def stop_telegram_listener() -> None:
    global _LISTENER_THREAD

    _LISTENER_STOP.set()
    if _LISTENER_THREAD is not None:
        _LISTENER_THREAD.join(timeout=3.0)
        _LISTENER_THREAD = None


def begin_scan_cycle(cycle_id: str) -> None:
    global _ACTIVE_SCAN_CYCLE_ID, _ALERT_DISPATCH_ENABLED

    _ACTIVE_SCAN_CYCLE_ID = cycle_id
    _ALERT_DISPATCH_ENABLED = False
    _QUALIFYING_ALERT_KEYS.clear()

    with _ALERT_CACHE_LOCK:
        _ALERT_CONTEXT_CACHE.clear()

    drain_pending_bot_queue()
    print(f"[SQE-V1] Tarama dongusu sifirlandi | cycle_id={cycle_id}")


def arm_qualifying_alerts(cycle_id: str, notify_keys: set[str]) -> None:
    global _ALERT_DISPATCH_ENABLED

    if cycle_id != _ACTIVE_SCAN_CYCLE_ID:
        _emit_operator_diag("alarm dagitimi engellendi | eski_tarama_dongusu")
        return

    _QUALIFYING_ALERT_KEYS.clear()
    _QUALIFYING_ALERT_KEYS.update(notify_keys)
    _ALERT_DISPATCH_ENABLED = bool(_QUALIFYING_ALERT_KEYS)


def _scan_interval_label() -> str:
    minutes = SCAN_INTERVAL_SECONDS / 60.0
    if minutes.is_integer():
        return f"~{int(minutes)} dakika"
    return f"~{SCAN_INTERVAL_SECONDS} saniye"


def _format_empty_report_hint(stats: ScanCycleStats) -> str:
    birlesik = stats["birlesik"]
    min_action_pct = get_active_min_ev_threshold() * 100.0
    watch_pct = get_watch_ev_threshold() * 100.0
    if birlesik <= 0:
        return "Neden: Nesine veya referans borsadan mac verisi gelmedi."

    reasons: list[tuple[int, str]] = [
        (
            stats["pasif_ev"],
            f"Nesine oranlari referansa gore avantajli degil (EV < IZLE %{watch_pct:.1f}).",
        ),
        (stats["stale"], "Zaman damgasi veya senkron kaynakli kayitlar elendi."),
        (stats["tolerans"], "Oran 1.15-7.0 araligi disinda."),
        (stats["efutbol"], "E-futbol/sanal maclar elendi."),
        (
            stats.get("context_filter", 0),
            "Baglam market yonune ters (ACTION/HIGH elendi, kazanma kalitesi filtresi).",
        ),
        (stats["absurd_ev"], "Absurt EV (eslesme hatasi suphesi) elendi."),
        (
            stats.get("suspicious_match", 0),
            "Soft-sharp oran orani 3x uzerinde (yanlis mac eslesmesi suphesi) elendi.",
        ),
    ]
    reasons.sort(key=lambda item: item[0], reverse=True)
    for count, hint in reasons:
        if count > 0:
            return f"Neden: {hint}"

    if stats.get("watch", 0) > 0 and stats.get("aday", 0) == 0:
        return (
            f"Neden: IZLE adaylari vardi ancak ACTION esigi (%{min_action_pct:.1f}) "
            f"gecilemedi."
        )
    return "Neden: Tum maclar filtrelendi; detay icin funnel satirina bakin."


def _build_scan_empty_report_text(stats: ScanCycleStats | None) -> str:
    header = (
        "⚠️ [SQE-V1] Tarama Raporu\n"
        "Kriterlere uygun aktif is emri bulunamadi.\n"
    )
    interval = _scan_interval_label()
    if stats is None:
        return (
            f"{header}"
            f"Sistem aktif, sonraki tarama {interval} sonradir."
        )

    funnel = (
        f"Funnel: birlesik={stats['birlesik']} | pasif_ev={stats['pasif_ev']} | "
        f"watch={stats.get('watch', 0)} | action={stats.get('action', 0)} | "
        f"high={stats.get('high', 0)} | tolerans={stats['tolerans']} | "
        f"stale={stats['stale']} | efutbol={stats['efutbol']} | absurd_ev={stats['absurd_ev']} | "
        f"suspicious_match={stats.get('suspicious_match', 0)} | "
        f"context_filter={stats.get('context_filter', 0)}"
    )
    hint = _format_empty_report_hint(stats)
    return f"{header}{funnel}\n{hint}\nSonraki tarama {interval} sonradir."


def build_last_scan_panel_payload(
    stats: ScanCycleStats | None,
    *,
    scan_time: str,
    sent_alerts: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scanned_at": scan_time,
        "sent_alerts": int(sent_alerts),
        "has_recommendation": sent_alerts > 0,
    }
    if stats is None:
        payload["hint"] = "Tarama verisi yok."
        payload["funnel"] = {}
        return payload

    payload["funnel"] = {
        "birlesik": int(stats.get("birlesik", 0)),
        "pasif_ev": int(stats.get("pasif_ev", 0)),
        "watch": int(stats.get("watch", 0)),
        "action": int(stats.get("action", 0)),
        "high": int(stats.get("high", 0)),
        "tolerans": int(stats.get("tolerans", 0)),
        "stale": int(stats.get("stale", 0)),
        "efutbol": int(stats.get("efutbol", 0)),
        "absurd_ev": int(stats.get("absurd_ev", 0)),
        "suspicious_match": int(stats.get("suspicious_match", 0)),
        "context_filter": int(stats.get("context_filter", 0)),
        "experimental_would_filter": int(stats.get("experimental_would_filter", 0)),
        "telegram": int(stats.get("telegram", 0)),
        "izle": int(stats.get("izle", 0)),
        "cooldown": int(stats.get("cooldown", 0)),
        "notify_gate": int(stats.get("notify_gate", 0)),
        "hero_pass": int(stats.get("hero_pass", 0)),
        "hero_reject_low_confidence": int(stats.get("hero_reject_low_confidence", 0)),
        "hero_reject_context": int(stats.get("hero_reject_context", 0)),
        "hero_reject_market": int(stats.get("hero_reject_market", 0)),
        "hero_reject_consensus": int(stats.get("hero_reject_consensus", 0)),
        "hero_reject_odds_band": int(stats.get("hero_reject_odds_band", 0)),
        "hero_daily_limit": int(stats.get("hero_daily_limit", 0)),
        "hero_loss_stop": int(stats.get("hero_loss_stop", 0)),
        "hero_weekly_stop": int(stats.get("hero_weekly_stop", 0)),
    }
    payload["hint"] = _format_empty_report_hint(stats)
    return payload


def _build_scan_cycle_summary_text(stats: ScanCycleStats) -> str:
    interval = _scan_interval_label()
    sent_total = stats.get("telegram", 0) + stats.get("izle", 0)
    if sent_total > 0:
        headline = f"✅ Tarama tamamlandi | gonderilen={sent_total}"
    else:
        headline = "ℹ️ Tarama tamamlandi | gonderilen=0"

    return (
        f"{headline}\n"
        f"Ozet: birlesik={stats['birlesik']} | watch={stats.get('watch', 0)} | "
        f"action={stats.get('action', 0)} | high={stats.get('high', 0)} | "
        f"alarm={stats.get('telegram', 0)} | izle={stats.get('izle', 0)} | "
        f"cooldown={stats.get('cooldown', 0)} | pasif_ev={stats['pasif_ev']}\n"
        f"Sonraki tarama {interval} sonradir."
    )


def send_scan_cycle_summary_for_cycle(cycle_id: str, cycle_stats: ScanCycleStats) -> bool:
    if cycle_id != _ACTIVE_SCAN_CYCLE_ID:
        return False

    if TELEGRAM_RECOMMENDATIONS_ONLY:
        return True

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": _build_scan_cycle_summary_text(cycle_stats),
    }
    sent = _telegram_post(_SEND_MESSAGE_URL, payload) is not None
    if not sent:
        _emit_operator_diag("tarama ozet raporu gonderilemedi")
    return sent


def send_scan_empty_report_for_cycle(
    cycle_id: str,
    cycle_stats: ScanCycleStats | None = None,
) -> bool:
    global _ALERT_DISPATCH_ENABLED

    if cycle_id != _ACTIVE_SCAN_CYCLE_ID:
        _emit_operator_diag("bos tarama raporu engellendi | eski_tarama_dongusu")
        return False

    _ALERT_DISPATCH_ENABLED = False
    _QUALIFYING_ALERT_KEYS.clear()
    with _ALERT_CACHE_LOCK:
        _ALERT_CONTEXT_CACHE.clear()

    if TELEGRAM_RECOMMENDATIONS_ONLY:
        return True

    return send_scan_empty_report(cycle_stats)


def purge_stale_alert_state() -> int:
    now = time.time()
    removed = 0
    with _ALERT_CACHE_LOCK:
        for cache_key in list(_ALERT_CONTEXT_CACHE.keys()):
            entry = _ALERT_CONTEXT_CACHE.get(cache_key)
            if not isinstance(entry, dict):
                del _ALERT_CONTEXT_CACHE[cache_key]
                removed += 1
                continue

            cached_at = float(entry.get("cached_at", 0.0))
            mac_adi = str(entry.get("mac_adi", ""))
            market = str(entry.get("market", ""))
            expired = cached_at <= 0.0 or (now - cached_at) >= _ALERT_TTL_SECONDS
            virtual_match = is_virtual_match_text(mac_adi, market, cache_key)
            if expired or virtual_match:
                del _ALERT_CONTEXT_CACHE[cache_key]
                removed += 1

    if removed:
        print(
            f"[SQE-V1] Telegram uyari onbellegi | temizlenen={removed} | "
            f"neden=zaman_asimi_veya_e_futbol",
            file=sys.stderr,
        )
    return removed


def drain_pending_bot_queue() -> bool:
    drained = _delete_webhook(drop_pending_updates=True)
    if drained:
        print("[SQE-V1] Telegram bot kuyrugu | bekleyen_eski_guncellemeler=temizlendi")
    else:
        _emit_operator_diag("Telegram bot kuyrugu temizlenemedi")
    return drained


def send_system_ready() -> bool:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "Sistem Hazır",
        "reply_markup": _build_operator_reply_keyboard(),
    }
    sent = _telegram_post(_SEND_MESSAGE_URL, payload) is not None
    if sent:
        start_telegram_listener()
    return sent


def send_hero_daily_notice(message: str) -> bool:
    if not isinstance(message, str):
        _emit_operator_diag("hero daily notice | message must be a string")
        return False
    text = message.strip()
    if not text:
        _emit_operator_diag("hero daily notice | message must not be empty")
        return False
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    sent = _telegram_post(_SEND_MESSAGE_URL, payload) is not None
    if not sent:
        _emit_operator_diag("hero daily notice gonderilemedi")
    return sent


def send_scan_empty_report(cycle_stats: ScanCycleStats | None = None) -> bool:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": _build_scan_empty_report_text(cycle_stats),
    }
    sent = _telegram_post(_SEND_MESSAGE_URL, payload) is not None
    if not sent:
        _emit_operator_diag("bos tarama raporu gonderilemedi")
    return sent


def wait_for_scan_start(poll_interval_seconds: float = 1.0) -> bool:
    """Block until scan_enabled becomes True.

    Telegram updates are handled only by the background listener thread.
    Do not call getUpdates here — a second poll causes HTTP 409 conflict and
    the start button is never processed.
    """
    _prepare_polling_session()
    start_telegram_listener(poll_interval_seconds=poll_interval_seconds)

    while True:
        if bool(SISTEM_DURUMU.get("scan_enabled", False)):
            return True
        time.sleep(poll_interval_seconds)


# Bildirim zamanlama kurallari (operatorun Nesine'de oynamasi icin pay birakir)
_MIN_ALERT_LEAD_SECONDS = 15 * 60        # ilk bildirim: maca en az 15 dk kala
_REMINDER_FIRST_AFTER_SECONDS = 5 * 60   # oynanmadiysa ~5 dk sonra 1. hatirlatma
_REMINDER_FINAL_LEAD_SECONDS = 10 * 60   # maca ~10 dk kala son hatirlatma
_REMINDER_SWEEP_INTERVAL_SECONDS = 30    # hatirlatma taramasi en fazla 30 sn'de bir
_last_reminder_sweep_at = 0.0


def _seconds_to_kickoff(commence_time: str | None, *, now: float | None = None) -> float | None:
    """Maca kalan saniye. commence_time bilinmiyor/gecersizse None."""
    parsed = parse_utc(commence_time)
    if parsed is None:
        return None
    reference = now if now is not None else datetime.now(timezone.utc).timestamp()
    return parsed.timestamp() - reference


def _auto_record_play_in_test(match_id: str, stake: float, message_text: str) -> bool:
    """TEST modunda bildirim cikinca operator 'Oyna' basmis gibi sanal kuponu kaydeder.

    Yalnizca PILOT_MODE (test) icin cagrilir; gercek mod kayit yolu (callback)
    bu fonksiyondan etkilenmez. Callback'teki kayit adimlarinin yalin kopyasidir:
    cift-kayit korumasi -> canli metadata -> bakiye -> dusum -> add_kupon.
    """
    try:
        context = _get_alert_context(match_id, message_text)
        if is_virtual_match_text(context.get("mac_adi"), context.get("market")):
            return False
        if _is_duplicate_play(context):
            return False

        from core.kupon_live_guard import is_live_kupon_metadata

        live_ok, live_reason = is_live_kupon_metadata(
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
            sport_key=str(context.get("sport_key", "")),
            mac_adi=str(context.get("mac_adi", "")),
        )
        if not live_ok:
            _emit_kupon_diag(f"test-oto kayit atlandi | metadata eksik | {live_reason}")
            return False

        current_kasa = get_latest_bakiye()
        if stake > current_kasa:
            _emit_kupon_diag(
                f"test-oto kayit atlandi | yetersiz bakiye | kasa={current_kasa} | stake={stake}"
            )
            return False

        new_kasa = round(current_kasa - stake, 2)
        if not save_bakiye(new_kasa, f"Kupon yatirimi (TEST-oto) | {context['mac_adi']} | -{stake} TL"):
            _emit_kupon_diag("test-oto kayit | save_bakiye basarisiz")
            return False

        saved = add_kupon(
            match_id=str(context["match_id"]),
            mac_adi=str(context["mac_adi"]),
            market=str(context["market"]),
            stake=stake,
            soft_oran=float(context["soft_oran"]),
            sharp_oran=float(context["sharp_oran"]),
            ev_at_alert=calculate_expected_value(
                float(context["sharp_oran"]),
                float(context["soft_oran"]),
            ),
            sport_key=str(context.get("sport_key", "")),
            event_id=str(context.get("event_id", "")),
            commence_time=str(context.get("commence_time", "")),
        )
        if not saved:
            save_bakiye(current_kasa, f"Kupon iptal iadesi (TEST-oto) | {context['mac_adi']}")
            _emit_kupon_diag("test-oto kayit | add_kupon basarisiz, bakiye iade edildi")
            return False

        update_sistem_durumu(total_kasa=new_kasa, performance=get_performance_stats())
        _send_operator_notice(
            f"✓ Otomatik kaydedildi (TEST) | {context['mac_adi']} | {context['market']} | "
            f"{stake} TL | Kasa: {new_kasa} TL"
        )
        _schedule_settlement_check()
        return True
    except (TypeError, ValueError, KeyError) as exc:
        _emit_kupon_diag(f"test-oto kayit hatasi | {exc}")
        return False


_TEST_ALERT_BANNER = (
    "🧪 TEST · SANAL PARA\n"
    "Gerçek para değil — Nesine'de OYNAMA. Sistem otomatik kaydeder, sen sadece izle.\n"
    "———————————————\n\n"
)


def send_alert(
    message: str,
    match_id: str | None = None,
    stake: float | None = None,
    soft_odds: float | None = None,
    *,
    mac_adi: str | None = None,
    market: str | None = None,
    sport_key: str | None = None,
    event_id: str | None = None,
    commence_time: str | None = None,
    cycle_id: str | None = None,
    notify_key: str | None = None,
    bypass_scan_gate: bool = False,
) -> bool:
    if not isinstance(message, str):
        _emit_operator_diag("message must be a string")
        return False

    text = message.strip()
    if not text:
        _emit_operator_diag("message must not be empty")
        return False

    if is_virtual_match_text(text):
        _emit_operator_diag("E-Futbol bildirimi engellendi | kanal=ATILMADI")
        return False

    if not bypass_scan_gate:
        if cycle_id is None or cycle_id != _ACTIVE_SCAN_CYCLE_ID:
            _emit_operator_diag("Stale alert engellendi | eski_tarama_dongusu")
            return False
        if not _ALERT_DISPATCH_ENABLED:
            _emit_operator_diag("Stale alert engellendi | alarm_dagitimi_kapali")
            return False
        if notify_key is None or notify_key not in _QUALIFYING_ALERT_KEYS:
            _emit_operator_diag("Stale alert engellendi | kriter_disi_onbellek")
            return False

    # Zaman kurali: maca cok az kalmissa bildirim atma (operatorun Nesine'de
    # oynayacak vakti olmali). commence_time bilinmiyorsa kural uygulanmaz.
    if not bypass_scan_gate and commence_time:
        lead_seconds = _seconds_to_kickoff(commence_time)
        if lead_seconds is not None and lead_seconds < _MIN_ALERT_LEAD_SECONDS:
            _emit_operator_diag(
                f"Bildirim atilmadi | maca az kaldi | kalan_sn={int(lead_seconds)} | "
                f"esik_sn={_MIN_ALERT_LEAD_SECONDS} | mac={mac_adi}"
            )
            return False

    normalized_match_id: str | None = None
    normalized_stake: float | None = None

    if match_id is not None:
        if not isinstance(match_id, str) or not match_id.strip():
            _emit_operator_diag("match_id must be a non-empty string when provided")
            return False
        normalized_match_id = match_id

    if stake is not None:
        if isinstance(stake, bool) or not isinstance(stake, (int, float)):
            _emit_operator_diag("stake must be numeric when provided")
            return False
        normalized_stake = float(stake)

    if normalized_stake is not None and normalized_match_id is not None and not bypass_scan_gate:
        live_kasa = round(float(get_latest_bakiye()), 2)
        text = refresh_alert_budget_lines(
            text,
            live_kasa=live_kasa,
            stake=normalized_stake,
        )

    # Test modunda gosterilen mesaja sasmaz bir "TEST / sanal para" basligi eklenir.
    # Orijinal metin (text) onbellek ve otomatik kayit icin degismeden kalir; yalnizca
    # Telegram'da gorunen kopya degisir, boylece eslestirme/ayristirma etkilenmez.
    display_text = (_TEST_ALERT_BANNER + text) if PILOT_MODE else text
    request_payload = _build_message_payload(display_text, normalized_match_id, normalized_stake)
    sent = _telegram_post(_SEND_MESSAGE_URL, request_payload) is not None
    if sent and normalized_match_id is not None:
        _cache_alert_context(
            normalized_match_id,
            text,
            soft_odds=soft_odds,
            mac_adi=mac_adi,
            market=market,
            sport_key=sport_key,
            event_id=event_id,
            commence_time=commence_time,
            stake=normalized_stake,
        )
        # TEST modu: operator dokunmadan sanal kuponu otomatik kaydet.
        # Gercek modda (PILOT_MODE=False) bu satir calismaz; kayit yine
        # operatorun "Oyna" dokunusuyla yapilir.
        if PILOT_MODE and normalized_stake is not None:
            _auto_record_play_in_test(normalized_match_id, normalized_stake, text)
    return sent
