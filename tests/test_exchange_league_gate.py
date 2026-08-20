"""Borsa kaynakli keskin kayitlar Odds API lig rotasyonuna bagli olmamalidir."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from core import notification_quality_gate


def _match(sport_key: str) -> dict[str, Any]:
    return {
        "event_id": f"gate-{sport_key}",
        "sport_key": sport_key,
        "match_name": "Arsenal - Coventry City",
        "league_name": "England Championship",
        "match_quality": "ok",
        "commence_time": "2099-06-19T18:00:00Z",
        "soft_source": "nesine",
        "soft_observed_at": 4102444800,
        "sharp_observed_at": 4102444800,
    }


class ExchangeLeagueGateTest(unittest.TestCase):
    def test_matchbook_record_passes_league_gate(self) -> None:
        with patch.object(
            notification_quality_gate,
            "get_enabled_sharp_sport_keys",
            return_value=("soccer_epl",),
        ):
            ok, reason = notification_quality_gate._passes_theory_real_match_gate(
                _match("matchbook_exchange")
            )

        self.assertTrue(ok, reason)

    def test_unscanned_api_league_is_still_blocked(self) -> None:
        with patch.object(
            notification_quality_gate,
            "get_enabled_sharp_sport_keys",
            return_value=("soccer_epl",),
        ):
            ok, reason = notification_quality_gate._passes_theory_real_match_gate(
                _match("soccer_england_efl_cup")
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "theory_lig_kapali")


if __name__ == "__main__":
    unittest.main()
