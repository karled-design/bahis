from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.beginner_alert import (
    build_beginner_alert_message,
    build_beginner_play_confirmation,
    build_beginner_settlement_notice,
    build_hero_alert_message,
    format_beginner_market_label,
    format_beginner_tier_header,
    market_code_from_label,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "context" / "tur_super_lig_gs_fb.json"


def _match_with_bundle() -> dict:
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    match = dict(payload["match"])
    match["context_bundle"] = {
        "standings": payload["standings"],
        "h2h": payload.get("h2h"),
        "injuries": payload.get("injuries"),
    }
    return match


class BeginnerAlertTests(unittest.TestCase):
    def test_market_labels_plain_turkish(self) -> None:
        self.assertEqual(format_beginner_market_label("MS1"), "Ev sahibi kazanir")
        self.assertEqual(format_beginner_market_label("X"), "Beraberlik")

    def test_market_code_from_label(self) -> None:
        self.assertEqual(market_code_from_label("Ev sahibi kazanir"), "MS1")

    def test_tier_headers_no_jargon(self) -> None:
        self.assertIn("GUCLU", format_beginner_tier_header("HIGH"))
        self.assertIn("IZLE", format_beginner_tier_header("WATCH"))
        self.assertNotIn("EV", format_beginner_tier_header("ACTION"))

    def test_action_message_has_budget_and_play_flow(self) -> None:
        text = build_beginner_alert_message(
            _match_with_bundle(),
            home_team="Galatasaray",
            away_team="Fenerbahce",
            league_name="Super Lig",
            market="MS1",
            tier="ACTION",
            stake=100.0,
            soft_odds=2.20,
            sharp_odds=2.05,
            scan_time="2026-06-19 12:00 UTC",
            current_kasa=1000.0,
            risk_per_trade=0.02,
        )
        self.assertIn("Guncel butce: 1000.00 TL", text)
        self.assertIn("Oynanacak tutar: 100.00 TL", text)
        self.assertIn("Nasil oyna:", text)
        self.assertIn("Oynadim tusuna basin", text)
        self.assertIn("Mac durumu:", text)
        self.assertIn("Mac saati:", text)
        self.assertNotIn("Test onerisi:", text)
        self.assertNotIn("EV=", text)

    def test_watch_message_no_stake(self) -> None:
        text = build_beginner_alert_message(
            {},
            home_team="A",
            away_team="B",
            league_name="Lig",
            market="MS2",
            tier="WATCH",
            stake=0.0,
            soft_odds=3.5,
            sharp_odds=3.2,
            scan_time="2026-06-19 12:00 UTC",
            current_kasa=800.0,
            risk_per_trade=0.02,
        )
        self.assertIn("para onerme", text.lower())
        self.assertNotIn("Oynanacak tutar", text)

    def test_play_confirmation_message(self) -> None:
        text = build_beginner_play_confirmation(
            mac_adi="Galatasaray - Fenerbahce",
            market="MS1",
            stake=100.0,
            new_kasa=900.0,
        )
        self.assertIn("Kupon kaydedildi", text)
        self.assertIn("butceden dusuldu", text)
        self.assertIn("900.00 TL", text)

    def test_settlement_notice_won(self) -> None:
        text = build_beginner_settlement_notice(
            mac_adi="A - B",
            market="MS1",
            outcome="WON",
            stake=100.0,
            soft_oran=2.2,
            kasa_before=900.0,
            kasa_after=1120.0,
        )
        self.assertIn("KAZANDI", text)
        self.assertIn("1120.00 TL", text)

    def test_main_build_tiered_alert_uses_beginner_format(self) -> None:
        from bahis.main import _build_tiered_alert

        text = _build_tiered_alert(
            _match_with_bundle(),
            "MS1",
            120.0,
            tier="HIGH",
            current_kasa=1000.0,
            sharp_odds=2.05,
            soft_odds=2.20,
            ev_percent="7.32",
            scan_time="2026-06-19 12:00 UTC",
            consensus_books=3,
        )
        self.assertIn("GUCLU ONERI", text)
        self.assertIn("Galatasaray - Fenerbahce", text)

    def test_hero_message_has_confidence_form_and_market_lines(self) -> None:
        text = build_hero_alert_message(
            _match_with_bundle(),
            home_team="Galatasaray",
            away_team="Fenerbahce",
            league_name="Super Lig",
            market="MS1",
            stake=20.0,
            soft_odds=1.85,
            sharp_odds=1.72,
            hero_confidence=0.62,
            scan_time="2026-06-19 12:00 UTC",
            current_kasa=1000.0,
            risk_per_trade=0.02,
        )
        self.assertIn("Guclu sinyal · Guven %62.0", text)
        self.assertIn("Guven: %62.0 · Form uyumlu · Piyasa uyumlu", text)
        self.assertIn("Nesine orani: 1.85", text)
        self.assertNotIn("EV=", text)
        self.assertNotIn("GUCLU ONERI", text)

    def test_main_build_tiered_alert_uses_hero_format_when_enabled(self) -> None:
        from unittest.mock import patch

        from bahis.main import _build_tiered_alert

        with patch("bahis.main.is_hero_mode_enabled", return_value=True):
            text = _build_tiered_alert(
                _match_with_bundle(),
                "MS1",
                20.0,
                tier="ACTION",
                current_kasa=1000.0,
                sharp_odds=1.72,
                soft_odds=1.85,
                ev_percent="4.50",
                scan_time="2026-06-19 12:00 UTC",
                consensus_books=3,
                hero_confidence=0.615,
            )
        self.assertIn("Guclu sinyal", text)
        self.assertIn("Piyasa uyumlu", text)
        self.assertNotIn("GUCLU ONERI", text)


class TelegramParseTests(unittest.TestCase):
    def test_parse_beginner_alert_message(self) -> None:
        from notifiers.telegram_worker import _parse_alert_message

        text = build_beginner_alert_message(
            _match_with_bundle(),
            home_team="Galatasaray",
            away_team="Fenerbahce",
            league_name="Super Lig",
            market="MS1",
            tier="ACTION",
            stake=100.0,
            soft_odds=2.20,
            sharp_odds=2.05,
            scan_time="2026-06-19 12:00 UTC",
            current_kasa=1000.0,
            risk_per_trade=0.02,
        )
        parsed = _parse_alert_message(text)
        self.assertEqual(parsed["mac_adi"], "Galatasaray - Fenerbahce")
        self.assertEqual(parsed["market"], "MS1")
        self.assertAlmostEqual(parsed["soft_oran"], 2.20)

    def test_parse_hero_alert_message(self) -> None:
        from notifiers.telegram_worker import _parse_alert_message

        text = build_hero_alert_message(
            _match_with_bundle(),
            home_team="Galatasaray",
            away_team="Fenerbahce",
            league_name="Super Lig",
            market="MS1",
            stake=20.0,
            soft_odds=1.85,
            sharp_odds=1.72,
            hero_confidence=0.62,
            scan_time="2026-06-19 12:00 UTC",
            current_kasa=1000.0,
            risk_per_trade=0.02,
        )
        parsed = _parse_alert_message(text)
        self.assertEqual(parsed["mac_adi"], "Galatasaray - Fenerbahce")
        self.assertEqual(parsed["market"], "MS1")
        self.assertAlmostEqual(parsed["soft_oran"], 1.85)


if __name__ == "__main__":
    unittest.main()
