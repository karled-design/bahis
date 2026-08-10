from __future__ import annotations

import math

from config.settings import TOTAL_KASA
from core.operator_risk_settings import get_operator_risk_per_trade
from core.passion_engine import get_active_min_ev_threshold

__all__ = ("calculate_stake_amount",)

_SAFE_HARBOR_STAKE = 0.0
_MAX_FINITE_STAKE = 1.0e12
_MAX_EV_STAKE_MULTIPLIER = 2.0
# Yarim-Kelly: tam Kelly cok oynak; profesyoneller tahmin hatasina karsi
# yastik olarak kesirli (1/2) Kelly kullanir.
_KELLY_FRACTION = 0.5
# Profil tavani: tek maca kasanin en fazla (2 x risk_per_trade)'i konur.
# Eski dogrusal formulun ust siniriyla ayni cati; Kelly bu tavani ASMAZ.
_MAX_STAKE_RISK_MULTIPLIER = 2.0
# Organik yuvarlama: onerilen tutar en yakin 10 TL'ye yuvarlanir. Amac,
# bildirimde "47.42 TL" gibi robotik ondalik yerine "50 TL" gibi insansi bir
# tutar gostermek (hem oynamasi kolay hem de bariz makine izi tasimaz).
_ORGANIC_STEP = 10.0


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _resolve_base_kasa(current_kasa: float) -> float:
    if _is_finite_number(current_kasa) and float(current_kasa) > 0.0:
        return float(current_kasa)
    return float(TOTAL_KASA)


def _safe_harbor_stake() -> float:
    return _SAFE_HARBOR_STAKE


def _finalize_stake(raw_stake: float) -> float:
    """Ortak guvenlik kapisi: sonlu, tavanli, negatif-olmayan, 2 haneli."""
    if not math.isfinite(raw_stake):
        return _safe_harbor_stake()
    if abs(raw_stake) > _MAX_FINITE_STAKE:
        return _safe_harbor_stake()
    stake = round(raw_stake, 2)
    if not math.isfinite(stake) or stake < 0.0:
        return _safe_harbor_stake()
    return stake


def _round_to_organic(stake: float, cap: float) -> float:
    """Pozitif tutari en yakin 10 TL'ye yuvarlar (organik/insansi gorunum).

    Guvenlik kurallari:
      * Zaten 0 olan (bahis yok) 0 kalir; gecersiz deger 0'a duser.
      * Profil tavanini (cap) ASLA asmaz: yukari yuvarlama tavani asarsa bir
        alt 10'a inilir.
      * Pozitif bir bahsi yanlislikla 0'a dusurmez: en az bir adim (10 TL)
        birakilir; ama tavan bir adimdan da kucukse (cok kucuk kasa) ham tutar
        korunur.
    """
    if not math.isfinite(stake) or stake <= 0.0:
        return _safe_harbor_stake()
    step = _ORGANIC_STEP
    rounded = round(stake / step) * step
    if math.isfinite(cap) and rounded > cap:
        rounded = math.floor(stake / step) * step
    if rounded <= 0.0:
        rounded = step if (math.isfinite(cap) and step <= cap) else stake
    return round(rounded, 2)


def _resolve_ev_stake_multiplier(ev: float) -> float | None:
    if not _is_finite_number(ev):
        return None
    numeric_ev = float(ev)
    threshold = get_active_min_ev_threshold()
    if numeric_ev < threshold:
        return None
    if threshold <= 0.0:
        return None
    return min(numeric_ev / threshold, _MAX_EV_STAKE_MULTIPLIER)


def _resolve_kelly_stake(
    base_kasa: float, ev: float, soft_odds: object, risk_per_trade: float
) -> float | None:
    """Yarim-Kelly tutari (profil tavaniyla sinirli).

    Tam Kelly kesri f* = EV / (oran - 1); yari-Kelly = 0.5 * f* * kasa.
    Boylece dusuk oranli (yuksek olasilikli) gercek deger firsatina daha cok,
    yuksek oranli (uzak) firsata daha az konur. Oran yok/gecersizse None doner
    ve cagiran eski dogrusal formule duser (geriye donuk guvenli).
    """
    if not _is_finite_number(soft_odds):
        return None
    net_odds = float(soft_odds) - 1.0  # b: net kazanc orani
    if net_odds <= 0.0:
        return None
    if not _is_finite_number(ev) or float(ev) <= 0.0:
        return None
    if not _is_finite_number(risk_per_trade) or float(risk_per_trade) <= 0.0:
        return None
    try:
        kelly_stake = _KELLY_FRACTION * base_kasa * (float(ev) / net_odds)
        cap = _MAX_STAKE_RISK_MULTIPLIER * float(risk_per_trade) * base_kasa
    except OverflowError:
        return _safe_harbor_stake()
    return _finalize_stake(min(kelly_stake, cap))


def calculate_stake_amount(
    current_kasa: float, ev: float, soft_odds: float | None = None
) -> float:
    """Mac basi tutar.

    Oran verilirse yarim-Kelly (profil tavanli); oran yoksa eski dogrusal
    formule (kasa x risk_per_trade x min(EV/esik, 2)) birebir duser.
    """
    ev_multiplier = _resolve_ev_stake_multiplier(ev)
    if ev_multiplier is None:
        return _safe_harbor_stake()

    base_kasa = _resolve_base_kasa(current_kasa)
    risk_per_trade = get_operator_risk_per_trade()
    cap = _MAX_STAKE_RISK_MULTIPLIER * risk_per_trade * base_kasa

    kelly_stake = _resolve_kelly_stake(base_kasa, ev, soft_odds, risk_per_trade)
    if kelly_stake is not None:
        return _round_to_organic(kelly_stake, cap)

    # Yedek: oran bilinmiyorsa eski davranis birebir korunur.
    try:
        raw_stake = base_kasa * risk_per_trade * ev_multiplier
    except OverflowError:
        return _safe_harbor_stake()
    return _round_to_organic(_finalize_stake(raw_stake), cap)
