from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.alert_context_line import format_alert_context_tag
from core.market_catalog import (
    FAMILY_MATCH_RESULT,
    market_family,
    parse_totals_market,
    totals_market_key,
)

__all__ = (
    "format_beginner_market_label",
    "format_beginner_tier_header",
    "format_beginner_context_note",
    "format_beginner_match_schedule",
    "format_live_scan_provenance",
    "build_beginner_alert_message",
    "build_hero_alert_message",
    "build_beginner_play_confirmation",
    "build_beginner_settlement_notice",
)

_MARKET_LABELS = {
    "MS1": "Ev sahibi kazanir",
    "MS2": "Deplasman kazanir",
    "X": "Beraberlik",
}

# Yan pazar etiketleri (Asama B): sabit secenekli pazarlarin acik adlari.
# KG canli kupon acar; IY yalnizca golge (skoru otomatik kapanamiyor).
_FIXED_SIDE_LABELS = {
    "KG VAR": "Karsilikli gol VAR",
    "KG YOK": "Karsilikli gol YOK",
    "IY1": "Ilk yari: ev sahibi onde",
    "IYX": "Ilk yari: beraberlik",
    "IY2": "Ilk yari: deplasman onde",
}

_LABEL_TO_MARKET = {label: code for code, label in _MARKET_LABELS.items()}
_LABEL_TO_MARKET.update({label: code for code, label in _FIXED_SIDE_LABELS.items()})

_TR_TZ = ZoneInfo("Europe/Istanbul")
_LIVE_WINDOW_SECONDS = 7200


def format_beginner_market_label(market: str) -> str:
    key = market.strip().upper()
    if key in _MARKET_LABELS:
        return _MARKET_LABELS[key]
    if key in _FIXED_SIDE_LABELS:
        return _FIXED_SIDE_LABELS[key]
    totals = parse_totals_market(key)
    if totals is not None:
        side, line = totals
        line_text = ("%g" % line).replace(".", ",")
        return f"{line_text} gol ustu" if side == "UST" else f"{line_text} gol alti"
    return key or "Mac sonucu"


def _totals_code_from_label(label: str) -> str | None:
    """"2,5 gol ustu" -> "UST 2.5". Etiket geri cozulmezse None."""
    parts = label.strip().lower().split()
    if len(parts) != 3 or parts[1] != "gol":
        return None
    side = {"ustu": "UST", "alti": "ALT"}.get(parts[2])
    if side is None:
        return None
    return totals_market_key(side, parts[0])


def market_code_from_label(label: str) -> str:
    cleaned = label.strip()
    if cleaned.upper() in _MARKET_LABELS or cleaned.upper() in _FIXED_SIDE_LABELS:
        return cleaned.upper()
    if cleaned in _LABEL_TO_MARKET:
        return _LABEL_TO_MARKET[cleaned]
    totals_code = _totals_code_from_label(cleaned)
    if totals_code is not None:
        return totals_code
    return cleaned.upper() or "MS1"


def format_beginner_tier_header(tier: str) -> str:
    normalized = tier.strip().upper()
    if normalized == "WATCH":
        return "🔵 IZLE — para onerilmez"
    if normalized == "HIGH":
        return "🟠 GUCLU ONERI"
    return "🟠 ONERI"


def format_beginner_context_note(match: dict[str, Any], *, market: str, sharp_odds: float) -> str:
    tag = format_alert_context_tag(match, market=market, sharp_odds=sharp_odds)
    if not tag:
        return ""
    cleaned = tag.replace("| Baglam:", "").strip()
    if cleaned.startswith("ev"):
        return "Kisa not: Form ev sahibi lehine."
    if cleaned.startswith("dep"):
        return "Kisa not: Form deplasman lehine."
    return ""


def _parse_commence_time(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_beginner_match_schedule(match: dict[str, Any]) -> tuple[str, str]:
    # Durum satiri yalnizca dikkat gerektiginde dolu doner (canli/bitmis);
    # baslamamis mac icin bos kalir — acemi icin gereksiz kalabalik olmasin.
    kickoff = _parse_commence_time(str(match.get("commence_time", "")))
    if kickoff is None:
        return "", ""

    now = datetime.now(timezone.utc)
    if kickoff > now:
        status = ""
    elif (now - kickoff).total_seconds() <= _LIVE_WINDOW_SECONDS:
        status = "🔴 Mac basladi (canli)"
    else:
        status = "⚠️ Mac bitmis olabilir, oynamadan once kontrol et"

    local_time = kickoff.astimezone(_TR_TZ).strftime("%d.%m.%Y %H:%M")
    return status, f"⏰ Mac saati: {local_time} (Turkiye saati)"


def _resolve_confidence_percent(match: dict[str, Any], sharp_odds: float) -> int:
    """Guven yuzdesi: marji temizlenmis olasilik (fair_probability) varsa onu,
    yoksa ham 1/oran degerini kullanir."""
    fair = match.get("fair_probability")
    if not isinstance(fair, bool) and isinstance(fair, (int, float)):
        numeric = float(fair)
        if 0.0 < numeric < 1.0:
            return round(numeric * 100.0)
    try:
        return round(100.0 / float(sharp_odds)) if float(sharp_odds) > 1.0 else 0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


def _format_risk_percent(risk_per_trade: float) -> str:
    risk = float(risk_per_trade)
    if risk <= 1.0:
        return f"%{risk * 100:.0f}"
    return f"%{risk:.1f}"


def _format_budget_line(kasa_value: float) -> str:
    return f"Guncel butce: {kasa_value:.2f} TL"


def format_live_scan_provenance(
    match: dict[str, Any],
    *,
    scan_time: str,
    cycle_id: str = "",
    reference_at: float | None = None,
) -> str:
    # Acemi icin tek satir yeter: tarama saati + oranlarin tazeligi.
    # Mac kimligi / dongu no / book sayisi gibi teknik ayrintilar kaldirildi;
    # ayni bilgiler zaten veritabaninda ve panelde duruyor.
    _ = cycle_id
    ref = float(reference_at if reference_at is not None else time.time())
    soft_at = float(match.get("soft_observed_at", 0.0) or 0.0)
    sharp_at = float(match.get("sharp_observed_at", 0.0) or 0.0)
    if soft_at > 0.0 and sharp_at > 0.0:
        age_seconds = int(max(0.0, min(ref - soft_at, ref - sharp_at)))
        return f"Tarama: {scan_time} · oranlar {age_seconds} sn once guncellendi"
    return f"Tarama: {scan_time}"


def build_beginner_alert_message(
    match: dict[str, Any],
    *,
    home_team: str,
    away_team: str,
    league_name: str,
    market: str,
    tier: str,
    stake: float,
    soft_odds: float,
    sharp_odds: float,
    scan_time: str,
    current_kasa: float,
    risk_per_trade: float,
    test_probe: bool = False,
    ev_percent: str = "",
    cycle_id: str = "",
    reference_at: float | None = None,
) -> str:
    _ = (test_probe, ev_percent)
    header = format_beginner_tier_header(tier)
    market_label = format_beginner_market_label(market)
    status_line, kickoff_line = format_beginner_match_schedule(match)
    stake_value = round(float(stake), 2)
    kasa_value = round(float(current_kasa), 2)
    risk_text = _format_risk_percent(risk_per_trade)
    # Guven = marji temizlenmis olasilik (varsa), yoksa 1/oran. Deger/EV
    # kontrolu arka planda sessiz kalkan olarak calismaya devam eder.
    confidence_pct = _resolve_confidence_percent(match, sharp_odds)

    # Satir kurallari (dokunma!): mac adi satiri " - " icermeli ve baska hicbir
    # satirda " - " olmamali (uzun cizgi — serbest); "Bahis turu:", "Nesine orani:",
    # "Guncel butce:", "Oynanacak tutar:" ve 'Nesine'de "..." secenegine X TL'
    # kaliplari Oynadim akisi + kasa tazeleme tarafindan okunur.
    lines = [
        f"[SQE-V1] {header}",
        f"{home_team} - {away_team}",
        f"Lig: {league_name}",
    ]
    if status_line:
        lines.append(status_line)
    if kickoff_line:
        lines.append(kickoff_line)

    lines.extend(
        [
            f"Bahis turu: {market_label}",
            f"Nesine orani: {soft_odds:.2f}",
            f"Guven: %{confidence_pct}",
        ]
    )

    if tier == "WATCH":
        lines.append("Sistem bu maci sadece izliyor; oynamak icin daha guclu sinyal bekle.")
    else:
        lines.extend(
            [
                f"💰 Oynanacak tutar: {stake_value:.2f} TL (butcenin {risk_text} riskine gore)",
                _format_budget_line(kasa_value),
                f"▶ Nesine'de \"{market_label}\" secenegine {stake_value:.2f} TL koy, sonra ✅ Oynadim'a bas.",
                "Sonuc ve butce otomatik guncellenir — sana baska is dusmez.",
            ]
        )

    context_note = format_beginner_context_note(match, market=market, sharp_odds=sharp_odds)
    if context_note:
        lines.append(context_note)

    lines.append(
        format_live_scan_provenance(
            match,
            scan_time=scan_time,
            cycle_id=cycle_id,
            reference_at=reference_at,
        )
    )
    return "\n".join(lines)


def _hero_form_status_label(match: dict[str, Any], *, market: str, sharp_odds: float) -> str:
    from core.context_fusion import resolve_context_features_from_match
    from core.context_score import score_context_for_market

    # Form modeli yalnizca mac sonucu pazarlarini bilir; alt/ust icin notr etiket.
    if market_family(market) != FAMILY_MATCH_RESULT:
        return "Form: kapsam disi"

    features = resolve_context_features_from_match(match)
    if features is None:
        return "Form: ek veri yok"
    score = score_context_for_market(features, market, sharp_odds=sharp_odds)
    if score.final_market_score > 0.0:
        return "Form uyumlu"
    return "Form zayif"


def build_hero_alert_message(
    match: dict[str, Any],
    *,
    home_team: str,
    away_team: str,
    league_name: str,
    market: str,
    stake: float,
    soft_odds: float,
    sharp_odds: float,
    hero_confidence: float,
    scan_time: str,
    current_kasa: float,
    risk_per_trade: float,
    cycle_id: str = "",
    reference_at: float | None = None,
) -> str:
    market_label = format_beginner_market_label(market)
    status_line, kickoff_line = format_beginner_match_schedule(match)
    stake_value = round(float(stake), 2)
    kasa_value = round(float(current_kasa), 2)
    risk_text = _format_risk_percent(risk_per_trade)
    confidence_pct = round(float(hero_confidence) * 100.0, 1)
    form_label = _hero_form_status_label(match, market=market.strip().upper(), sharp_odds=sharp_odds)

    # Satir kurallari beginner mesajiyla ayni: mac adi disinda " - " yok,
    # okunan kaliplar ("Bahis turu:", "Nesine orani:", butce/tutar/oyna) korunur.
    lines = [
        f"[SQE-V1] 🟠 GUCLU SINYAL · Guven %{confidence_pct:.1f}",
        f"{home_team} - {away_team}",
        f"Lig: {league_name}",
    ]
    if status_line:
        lines.append(status_line)
    if kickoff_line:
        lines.append(kickoff_line)

    lines.extend(
        [
            f"Bahis turu: {market_label}",
            f"Nesine orani: {soft_odds:.2f}",
            # Alt/ust gibi form modelinin bilmedigi pazarlarda "kapsam disi"
            # ibaresi acemiyi sasirtir; o durumda yalnizca piyasa bilgisi kalir.
            ("Piyasa uyumlu" if form_label.startswith("Form: kapsam") else f"{form_label} · Piyasa uyumlu"),
            f"💰 Oynanacak tutar: {stake_value:.2f} TL (butcenin {risk_text} riskine gore)",
            _format_budget_line(kasa_value),
            f"▶ Nesine'de \"{market_label}\" secenegine {stake_value:.2f} TL koy, sonra ✅ Oynadim'a bas.",
            "Sonuc ve butce otomatik guncellenir — sana baska is dusmez.",
        ]
    )

    match_payload = dict(match)
    match_payload.setdefault("sharp_odds", sharp_odds)
    lines.append(
        format_live_scan_provenance(
            match_payload,
            scan_time=scan_time,
            cycle_id=cycle_id,
            reference_at=reference_at,
        )
    )
    return "\n".join(lines)


def build_beginner_play_confirmation(
    *,
    mac_adi: str,
    market: str,
    stake: float,
    new_kasa: float,
    kupon_id: int | None = None,
) -> str:
    market_label = format_beginner_market_label(market)
    header = "[SQE-V1] ✅ Kupon kaydedildi"
    if kupon_id is not None and kupon_id > 0:
        header = f"[SQE-V1] ✅ Kupon kaydedildi (no {kupon_id})"
    lines = [
        header,
        f"Mac: {mac_adi}",
        f"Bahis: {market_label} · {round(float(stake), 2):.2f} TL (butceden dusuldu)",
        f"Yeni butce: {round(float(new_kasa), 2):.2f} TL",
        "Mac bitince sonuc kendiliginden gelir — sana baska is dusmez.",
    ]
    return "\n".join(lines)


def build_beginner_settlement_notice(
    *,
    mac_adi: str,
    market: str,
    outcome: str,
    stake: float,
    soft_oran: float,
    kasa_before: float,
    kasa_after: float,
) -> str:
    market_label = format_beginner_market_label(market)
    stake_value = round(float(stake), 2)
    _ = kasa_before

    if outcome == "WON":
        payout = round(stake_value * float(soft_oran), 2)
        net_profit = round(payout - stake_value, 2)
        lines = [
            f"[SQE-V1] 🟢 KAZANDIN · net +{net_profit:.2f} TL",
            f"Mac: {mac_adi}",
            f"Bahis: {market_label} · {stake_value:.2f} TL",
            f"Odeme: {payout:.2f} TL butceye eklendi ({stake_value:.2f} ana para + {net_profit:.2f} kar)",
            f"Guncel butce: {round(float(kasa_after), 2):.2f} TL",
        ]
    else:
        lines = [
            f"[SQE-V1] 🔴 Kaybetti · -{stake_value:.2f} TL",
            f"Mac: {mac_adi}",
            f"Bahis: {market_label} · {stake_value:.2f} TL",
            "Tutar zaten oynarken dusmustu; butcede yeni bir kesinti yok.",
            f"Guncel butce: {round(float(kasa_after), 2):.2f} TL",
        ]
    return "\n".join(lines)
