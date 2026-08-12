import unittest

from bahis.main import _build_ui_matches
from core.passion_engine import resolve_match_ev
from core.scan_pipeline import _calculate_match_ev


class SingleEvSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "match_name": "Test A - Test B",
            "market": "MS1",
            "sharp_odds": 2.0,
            "soft_odds": 2.2,
            "fair_probability": 0.52,
        }

    def test_engine_and_panel_report_same_ev(self) -> None:
        panel_ev = float(_build_ui_matches([self.match])[0]["ev"])
        engine_ev = _calculate_match_ev(self.match, 2.0, 2.2)
        self.assertAlmostEqual(panel_ev, engine_ev, places=9)

    def test_fair_probability_is_preferred_over_raw_odds(self) -> None:
        ev = resolve_match_ev(self.match, 2.0, 2.2)
        self.assertAlmostEqual(ev, 0.52 * 2.2 - 1.0, places=9)

    def test_falls_back_to_raw_odds_without_fair_probability(self) -> None:
        match = dict(self.match)
        del match["fair_probability"]
        ev = resolve_match_ev(match, 2.0, 2.2)
        self.assertAlmostEqual(ev, 2.2 / 2.0 - 1.0, places=9)

    def test_invalid_fair_probability_is_ignored(self) -> None:
        for bad_value in (0.0, 1.0, -0.2, True, "0.5", None):
            match = dict(self.match)
            match["fair_probability"] = bad_value
            self.assertAlmostEqual(
                resolve_match_ev(match, 2.0, 2.2), 2.2 / 2.0 - 1.0, places=9
            )


if __name__ == "__main__":
    unittest.main()
