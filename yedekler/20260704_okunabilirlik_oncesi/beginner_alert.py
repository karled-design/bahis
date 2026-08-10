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

_LABEL_TO_MARKET = {label: code for code, label in _MARKET_LABELS.items()}

_TR_TZ = ZoneInfo("Europe/Istanbul")
_LIVE_WINDOW_SECONDS = 7200


def format_beginner_market_label(market: str) -> str:
    key = market.strip().upper()
    if key in _MARKET_LABELS:
        return _MARKET_LABELS[key]
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
    if cleaned.upper() in _MARKET_LABELS:
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
        return "IZLE — sadece bilgi (para onerme)"
    if normalized == "HIGH":
        return "GUCLU ONERI — oynanabilir"
    return "ONERI — oynanabilir"


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
    kickoff = _parse_commence_time(str(match.get("commence_time", "")))
    if kickoff is None:
        return "Mac durumu: Bilinmiyor", ""

    now = datetime.now(timezone.utc)
    if kickoff > now:
        status = "Mac durumu: Mac baslamadan (prematch)"
    elif (now - kickoff).total_seconds() <= _LIVE_WINDOW_SECONDS:
        status = "Mac durumu: Canli mac"
    else:
        status = "Mac durumu: Mac bitmis olabilir"

    local_time = kickoff.astimezone(_TR_TZ).strftime("%d.%m.%Y %H:%M")
    return status, f"Mac saati: {local_time} (Turkiye saati)"


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
    ref = float(reference_at if reference_at is not None else time.time())
    soft_at = float(match.get("soft_observed_at", 0.0) or 0.0)
    sharp_at = float(match.get("sharp_observed_at", 0.0) or 0.0)
    if soft_at > 0.0 and sharp_at > 0.0:
        age_seconds = int(max(0.0, min(ref - soft_at, ref - sharp_at)))
        age_text = f"{age_seconds} sn once guncellendi"
    else:
        age_text = "oran zamani bilinmiyor"

    event_id = str(match.get("event_id", "")).strip()
    event_short = f"{event_id[:8]}..." if len(event_id) > 8 else event_id

    sharp_odds = match.get("sharp_odds")
    consensus_books = match.get("consensus_books")
    ref_parts = ["Canli tarama: Nesine + referans oran"]
    if isinstance(sharp_odds, (int, float)) and not isinstance(sharp_odds, bool):
        ref_parts.append(f"Referans orani: {float(sharp_odds):.2f}")
    if isinstance(consensus_books, (int, float)) and not isinstance(consensus_books, bool):
        ref_parts.append(f"Referans book sayisi: {int(consensus_books)}")

    lines = [
        f"{ref_parts[0]} ({age_text})",
    ]
    for part in ref_parts[1:]:
        lines.append(part)
    if event_short:
        lines.append(f"Mac kimligi: {event_short}")
    cycle_tail = cycle_id.strip()[-8:]
    if cycle_tail:
        lines.append(f"Tarama dongusu: ...{cycle_tail}")
    lines.append(f"Tarama zamani: {scan_time}")
    return "\n".join(lines)


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
    # Guven = piyasanin bu secime verdigi ihtimal (1/oran). Tahmin+Guven gorunumu;
    # deger/EV kontrolu arka planda sessiz kalkan olarak calismaya devam eder.
    try:
        confidence_pct = round(100.0 / float(sharp_odds)) if float(sharp_odds) > 1.0 else 0
    except (TypeError, ValueError, ZeroDivisionError):
        confidence_pct = 0

    lines = [
        f"[SQE-V1] {header}",
        f"{home_team} - {away_team}",
        status_line,
    ]
    if kickoff_line:
        lines.append(kickoff_line)

    lines.extend(
        [
            f"Tahmin: {market_label}",
            f"Guven: %{confidence_pct}",
            f"Lig: {league_name}",
            f"Bahis turu: {market_label}",
            f"Nesine orani: {soft_odds:.2f}",
        ]
    )

    if tier == "WATCH":
        lines.append("Bu mesaj yalnizca bilgi icindir; tutar onerilmez.")
    else:
        lines.extend(
            [
                _format_budget_line(kasa_value),
                f"Oynanacak tutar: {stake_value:.2f} TL",
                f"(Butcenizin {risk_text} risk ayarina gore hesaplandi)",
                f"Nasil oyna: Nesine'de \"{market_label}\" secenegine {stake_value:.2f} TL koy.",
                "Nesine'de oynadiktan sonra asagidaki Oynadim tusuna basin.",
                "Mac bitince sonucu sistem otomatik bulur; Telegram'a sonuc mesaji gelir.",
                "Sizin ekstra bir sey yapmaniza gerek yok.",
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

    lines = [
        f"[SQE-V1] Guclu sinyal · Guven %{confidence_pct:.1f}",
        f"{home_team} - {away_team}",
        status_line,
    ]
    if kickoff_line:
        lines.append(kickoff_line)

    lines.extend(
        [
            f"Lig: {league_name}",
            f"Bahis turu: {market_label}",
            f"Nesine orani: {soft_odds:.2f}",
            f"Guven: %{confidence_pct:.1f} · {form_label} · Piyasa uyumlu",
            _format_budget_line(kasa_value),
            f"Oynanacak tutar: {stake_value:.2f} TL",
            f"(Butcenizin {risk_text} risk ayarina gore hesaplandi)",
            f"Nasil oyna: Nesine'de \"{market_label}\" secenegine {stake_value:.2f} TL koy.",
            "Nesine'de oynadiktan sonra asagidaki Oynadim tusuna basin.",
            "Mac bitince sonucu sistem otomatik bulur; Telegram'a sonuc mesaji gelir.",
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
    stake_line = f"Oynanan tutar: {round(float(stake), 2):.2f} TL (butceden dusuldu)"
    lines = [
        "[SQE-V1] Kupon kaydedildi",
        f"Mac: {mac_adi}",
        f"Bahis: {market_label}",
        stake_line,
        f"Yeni butce: {round(float(new_kasa), 2):.2f} TL",
        "Mac bitince sonuc otomatik aranir.",
        "Kazandinizsa veya kaybettiyseniz Telegram'a mesaj gelir; butce guncellenir.",
        "Sizin bir sey yapmaniza gerek yok.",
    ]
    if kupon_id is not None and kupon_id > 0:
        lines.append(f"Kupon no: {kupon_id}")
    lines.append("Panelde Oynadigim Maclar bolumunden takip edebilirsiniz.")
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
    kasa_impact = round(float(kasa_after) - float(kasa_before), 2)

    if outcome == "WON":
        payout = round(stake_value * float(soft_oran), 2)
        result_line = f"Sonuc: KAZANDI — {payout:.2f} TL butceye eklendi"
    else:
        result_line = f"Sonuc: KAYBETTI — {stake_value:.2f} TL zaten kasadan dusmustu"

    return "\n".join(
        [
            "[SQE-V1] Mac sonucu islendi",
            f"Mac: {mac_adi}",
            f"Bahis: {market_label}",
            result_line,
            f"Butce degisimi: {kasa_impact:+.2f} TL",
            f"Guncel butce: {round(float(kasa_after), 2):.2f} TL",
        ]
    )
