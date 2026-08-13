import unittest

from notifiers import telegram_worker


class AlertEvContextTests(unittest.TestCase):
    def test_cached_engine_ev_is_used(self) -> None:
        context = {"sharp_oran": 2.0, "soft_oran": 2.2, "ev_at_alert": 0.144}
        self.assertAlmostEqual(telegram_worker._context_ev(context), 0.144, places=9)

    def test_falls_back_to_odds_when_ev_missing(self) -> None:
        context = {"sharp_oran": 2.0, "soft_oran": 2.2}
        self.assertAlmostEqual(
            telegram_worker._context_ev(context), 2.2 / 2.0 - 1.0, places=9
        )

    def test_ignores_non_numeric_ev(self) -> None:
        for bad_value in ("0.1", None, True):
            context = {"sharp_oran": 2.0, "soft_oran": 2.2, "ev_at_alert": bad_value}
            self.assertAlmostEqual(
                telegram_worker._context_ev(context), 2.2 / 2.0 - 1.0, places=9
            )

    def test_cache_stores_engine_ev(self) -> None:
        telegram_worker._cache_alert_context(
            "test-match-1",
            "Maç: Test A - Test B",
            soft_odds=2.2,
            mac_adi="Test A - Test B",
            market="MS1",
            ev=0.087,
        )
        context = telegram_worker._get_alert_context("test-match-1", "")
        self.assertAlmostEqual(float(context["ev_at_alert"]), 0.087, places=9)
        self.assertAlmostEqual(telegram_worker._context_ev(context), 0.087, places=9)


if __name__ == "__main__":
    unittest.main()
