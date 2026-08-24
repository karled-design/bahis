import unittest

from notifiers import telegram_worker


class PanelRecommendationPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        with telegram_worker._ALERT_CACHE_LOCK:
            telegram_worker._ALERT_CONTEXT_CACHE.clear()

    tearDown = setUp

    def test_payload_carries_fair_odds_and_ev(self) -> None:
        telegram_worker._cache_alert_context(
            "panel-rec-1",
            "Mac: Test A - Test B\nSharp: 2.00\nNesine: 2.20",
            soft_odds=2.2,
            mac_adi="Test A - Test B",
            market="MS1",
            stake=25.0,
            ev=0.1,
        )
        items = telegram_worker.get_active_recommendations()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertAlmostEqual(float(item["soft_oran"]), 2.2, places=6)
        self.assertAlmostEqual(float(item["ev"]), 0.1, places=6)
        self.assertIn("sharp_oran", item)

    def test_ev_falls_back_to_odds_when_engine_value_missing(self) -> None:
        telegram_worker._cache_alert_context(
            "panel-rec-2",
            "Mac: Test C - Test D\nSharp: 2.00\nNesine: 2.20",
            soft_odds=2.2,
            mac_adi="Test C - Test D",
            market="MS1",
        )
        items = telegram_worker.get_active_recommendations()
        self.assertEqual(len(items), 1)
        sharp = float(items[0]["sharp_oran"])
        if sharp > 1.0:
            self.assertAlmostEqual(float(items[0]["ev"]), 2.2 / sharp - 1.0, places=6)
        else:
            self.assertEqual(float(items[0]["ev"]), 0.0)


if __name__ == "__main__":
    unittest.main()
