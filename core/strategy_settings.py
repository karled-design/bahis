"""Strateji kartlari ayarlari (Asama B).

Kart 1 "Av sahasi genisletme" (yan pazarlar: KG canli + IY golge) panelden
acilip kapatilir. Varsayilan KAPALI: acilmadan motor yan pazar icin tek kredi
harcamaz. Panelden degisince diske yazilir ve motor yeniden baslasa bile
korunur (live_alerts.json ile ayni desen).

Gunluk yan-pazar kredi tavani da burada: kart acik olsa bile mac-basina ek
pazar cekimi bu tavani asamaz (500/ay diyetinde fren; 20.000/ay pakette de
emniyet kemeri olarak kalir).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = (
    "get_side_market_daily_credit_cap",
    "get_strategy_panel_payload",
    "is_side_markets_effective",
    "is_side_markets_enabled",
    "is_side_markets_frozen",
    "set_side_markets_enabled",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSIST_PATH = _PROJECT_ROOT / "database" / "strategy_settings.json"
_LOCK = threading.Lock()
_DEFAULT_SIDE_MARKETS_ENABLED = False
# Gunluk yan-pazar kredi tavani: mac basina 2 kredi (eu x 2 pazar) oldugundan
# 30 kredi ~= gunde 15 mac demektir. 500/ay diyetinde guvenli; 20K pakete
# gecilince gerekirse yukseltilir (tek satir).
_SIDE_MARKET_DAILY_CREDIT_CAP = 30.0


def _load_payload() -> dict[str, Any]:
    if not _PERSIST_PATH.is_file():
        return {}
    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_payload(payload: dict[str, Any]) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _PERSIST_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_side_markets_enabled() -> bool:
    with _LOCK:
        payload = _load_payload()
        value = payload.get("side_markets_enabled")
        if isinstance(value, bool):
            return value
        return _DEFAULT_SIDE_MARKETS_ENABLED


def set_side_markets_enabled(enabled: bool) -> bool:
    normalized = bool(enabled)
    with _LOCK:
        payload = _load_payload()
        payload["side_markets_enabled"] = normalized
        _save_payload(payload)
    return normalized


def _is_core_proven() -> bool:
    """Cekirdek pazar (mac sonucu) CLV karnesiyle kanitlandi mi?

    Kanit kurali Asama 4'un TEK kaynagindan okunur (clv_tracker); burada
    tekrar EDILMEZ. Okunamazsa guvenli taraf: kanitsiz (False) -> donduruldu.
    """
    try:
        from core import clv_tracker

        return clv_tracker.is_scorecard_proven(clv_tracker.scorecard())
    except Exception:
        return False


def is_side_markets_effective() -> bool:
    """Yan pazarlar FIILEN calisir mi: kart acik VE cekirdek kanitli.

    Asama 5 (kapsam dondurma): cekirdek pazar kanitlanana kadar yan pazar
    icin tek kredi harcanmaz. Tarama/cekim yalniz bu kapiya bakar.
    """
    return is_side_markets_enabled() and _is_core_proven()


def is_side_markets_frozen() -> bool:
    """Kullanici karti acti AMA cekirdek kanitsiz -> donduruldu (panelde aciklanir)."""
    return is_side_markets_enabled() and not _is_core_proven()


def get_side_market_daily_credit_cap() -> float:
    return _SIDE_MARKET_DAILY_CREDIT_CAP


def get_strategy_panel_payload() -> dict[str, Any]:
    """Panel /api/status icin strateji kartlarinin durumu."""
    return {
        "side_markets_enabled": is_side_markets_enabled(),
        "side_markets_effective": is_side_markets_effective(),
        "side_markets_frozen": is_side_markets_frozen(),
        "side_markets_core_proven": _is_core_proven(),
        "side_market_daily_credit_cap": _SIDE_MARKET_DAILY_CREDIT_CAP,
    }
