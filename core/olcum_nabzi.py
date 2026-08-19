"""Olcum modu nabzi: surec devam ederken duzenli Telegram durum raporu.

Olcum modunda sinyal cikmadigi surece Telegram sessiz kalir; operator
"motor calisiyor mu, kanit serisi nereye geldi" sorusunu ancak panele bakarak
cevaplayabilir. Bu modul her `NABIZ_ARALIK_DAKIKA` dakikada bir tek mesaj
gonderir: kac tur donduk, huninin neresi tikali, olcum karnesi ne durumda,
kredi ne kadar kaldi.

Tarama motoruna dokunmaz: ana dongu her turda `maybe_send_measurement_pulse()`
cagirir; zamani gelmemisse veya olcum modu kapaliysa hicbir sey yapmadan doner.
Durum diske yazilir, boylece yeniden baslatma mesaj yagmuruna yol acmaz.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.api_credit_ledger import build_credit_panel_payload
from core.clv_tracker import CLV_PROOF_MIN_SAMPLE
from core.measurement_mode import is_measurement_mode_enabled, report

__all__ = (
    "NABIZ_ARALIK_DAKIKA",
    "build_pulse_message",
    "is_pulse_due",
    "mark_pulse_sent",
    "maybe_send_measurement_pulse",
    "record_cycle",
)

_TR_TZ = ZoneInfo("Europe/Istanbul")
_STATE_PATH = Path(__file__).resolve().parent.parent / "database" / "olcum_nabiz_state.json"
_RETRY_WAIT_SECONDS = 300.0
_DEFAULT_INTERVAL_MINUTES = 60
_MIN_INTERVAL_MINUTES = 5


def _resolve_interval_minutes() -> int:
    raw = os.getenv("OLCUM_NABIZ_DAKIKA", "").strip()
    try:
        minutes = int(raw)
    except ValueError:
        return _DEFAULT_INTERVAL_MINUTES
    return max(_MIN_INTERVAL_MINUTES, minutes)


NABIZ_ARALIK_DAKIKA: int = _resolve_interval_minutes()

_last_attempt_monotonic: float = 0.0
# Iki nabiz arasinda biriken tur sayaclari (surec bir sonraki mesajda ozetlenir).
_cycle_counter: int = 0
_last_cycle_stats: dict[str, int] = {}
_last_cycle_at: str = ""


def _now_tr() -> datetime:
    return datetime.now(timezone.utc).astimezone(_TR_TZ)


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
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=True, indent=2)
        os.replace(tmp_path, _STATE_PATH)
    except OSError:
        pass


def record_cycle(cycle_stats: dict[str, int]) -> None:
    """Her tarama turundan sonra cagrilir: sayaclari biriktirir."""
    global _cycle_counter, _last_cycle_stats, _last_cycle_at
    _cycle_counter += 1
    _last_cycle_stats = {str(key): int(value) for key, value in cycle_stats.items() if isinstance(value, int)}
    _last_cycle_at = _now_tr().strftime("%H:%M")


def is_pulse_due(*, now: datetime | None = None) -> bool:
    if not is_measurement_mode_enabled():
        return False
    moment = now.astimezone(_TR_TZ) if now is not None else _now_tr()
    last_sent = str(_load_state().get("sent_at", "") or "")
    if not last_sent:
        return True
    try:
        previous = datetime.fromisoformat(last_sent)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=_TR_TZ)
    elapsed_minutes = (moment - previous.astimezone(_TR_TZ)).total_seconds() / 60.0
    return elapsed_minutes >= float(NABIZ_ARALIK_DAKIKA)


def mark_pulse_sent(*, now: datetime | None = None) -> None:
    moment = now.astimezone(_TR_TZ) if now is not None else _now_tr()
    _save_state({"sent_at": moment.isoformat(timespec="seconds")})


def _funnel_line(stats: dict[str, int]) -> str:
    if not stats:
        return "Son tur: henuz tamamlanmadi"
    return (
        f"Son tur ({_last_cycle_at}): {stats.get('birlesik', 0)} mac karsilastirildi | "
        f"esik alti {stats.get('pasif_ev', 0)} | aday {stats.get('aday', 0)} | "
        f"izle {stats.get('izle', 0)} | bildirim {stats.get('telegram', 0)}"
    )


def build_pulse_message(*, scan_enabled: bool = True) -> str:
    card = report()
    total = card.get("toplam", {})
    signal_count = int(total.get("sinyal", 0) or 0)
    measured = int(total.get("olculen", 0) or 0)
    avg_clv = float(total.get("ort_clv_pct", 0.0) or 0.0)
    positive_ratio = float(total.get("pozitif_clv_orani", 0.0) or 0.0)
    remaining_for_proof = max(0, CLV_PROOF_MIN_SAMPLE - measured)

    credit = build_credit_panel_payload()

    lines = [
        f"OLCUM NABZI — {_now_tr().strftime('%d.%m %H:%M')}",
        "",
        (
            f"Motor: calisiyor | son nabizdan beri {_cycle_counter} tarama turu"
            if scan_enabled
            else "Motor: calisiyor | TARAMA KAPALI — Telegram'dan [Taramayi Baslat]"
        ),
        _funnel_line(_last_cycle_stats),
        "",
        f"Olcum karnesi: {signal_count} sinyal | {measured} tanesi CLV ile olculdu",
    ]
    if measured:
        lines.append(f"Ortalama CLV %{avg_clv:.2f} | kapanisi gecen %{positive_ratio:.1f}")
    if remaining_for_proof:
        lines.append(f"Kanit esigi icin {remaining_for_proof} olculmus sinyal daha gerekiyor")
    else:
        lines.append("Kanit esigi doldu: karne degerlendirmeye hazir")

    lines.extend(
        [
            "",
            f"API kredi: {credit.get('status_label', 'bilinmiyor')}",
            "Not: olcum modu — gercek kupon acilmiyor, kasa degismiyor.",
        ]
    )
    return "\n".join(lines)


def maybe_send_measurement_pulse(
    send_func: Callable[[str], bool], *, scan_enabled: bool = True
) -> bool:
    """Zamani geldiyse nabiz mesajini kurar ve yollar; aksi halde hemen doner."""
    global _last_attempt_monotonic, _cycle_counter

    if not is_pulse_due():
        return False
    if _last_attempt_monotonic > 0.0 and (time.monotonic() - _last_attempt_monotonic) < _RETRY_WAIT_SECONDS:
        return False
    _last_attempt_monotonic = time.monotonic()

    try:
        message = build_pulse_message(scan_enabled=scan_enabled)
    except Exception as exc:  # nabiz kurulamazsa motoru asla dusurme
        print(f"[SQE-V1] Olcum nabzi olusturulamadi | {exc}")
        return False

    if not send_func(message):
        return False

    mark_pulse_sent()
    _cycle_counter = 0
    print("[SQE-V1] Olcum nabzi gonderildi")
    return True
