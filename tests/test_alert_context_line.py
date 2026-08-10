from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from core.alert_context_line import format_alert_context_tag

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "context" / "tur_super_lig_gs_fb.json"


def _match_with_bundle() -> dict:
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    match = dict(payload["match"])
    match["context_bundle"] = {
        "standings": payload["standings"],
        "home_form": payload.get("home_form"),
        "away_form": payload.get("away_form"),
        "h2h": payload.get("h2h"),
        "injuries": payload.get("injuries"),
    }
    return match


class AlertContextLineTests(unittest.TestCase):
    def test_no_context_keeps_message_clean(self) -> None:
        match = {
            "event_id": "plain-1",
            "sport_key": "soccer_turkey_super_league",
            "match_name": "Galatasaray - Fenerbahce",
            "match_quality": "ok",
        }
        self.assertEqual(format_alert_context_tag(match, market="MS1", sharp_odds=2.05), "")

    def test_short_inline_tag_for_home_lean(self) -> None:
        with patch("core.alert_context_line.is_context_info_enabled", return_value=True):
            tag = format_alert_context_tag(_match_with_bundle(), market="MS1", sharp_odds=2.05)
        self.assertTrue(tag.startswith(" | Baglam:"))
        self.assertIn("ev", tag)

    def test_hidden_when_context_mode_off(self) -> None:
        with patch("core.alert_context_line.is_context_info_enabled", return_value=False):
            tag = format_alert_context_tag(_match_with_bundle(), market="MS1", sharp_odds=2.05)
        self.assertEqual(tag, "")


if __name__ == "__main__":
    unittest.main()
