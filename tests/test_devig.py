import unittest

from core.devig import (
    fair_probabilities,
    proportional_probabilities,
    shin_probabilities,
)


class ProportionalDevigTests(unittest.TestCase):
    def test_probabilities_sum_to_one(self) -> None:
        probabilities = proportional_probabilities([2.0, 3.5, 4.0])
        assert probabilities is not None
        self.assertAlmostEqual(sum(probabilities), 1.0, places=9)

    def test_rejects_invalid_odds(self) -> None:
        self.assertIsNone(proportional_probabilities([2.0, 1.0]))
        self.assertIsNone(proportional_probabilities([2.0]))
        self.assertIsNone(proportional_probabilities([2.0, "3.0"]))  # type: ignore[list-item]


class ShinDevigTests(unittest.TestCase):
    def test_probabilities_sum_to_one(self) -> None:
        probabilities = shin_probabilities([1.8, 3.6, 4.5])
        assert probabilities is not None
        self.assertAlmostEqual(sum(probabilities), 1.0, places=9)

    def test_longshot_probability_below_proportional(self) -> None:
        """Shin, uzak ihtimalden daha fazla marj cikarir -> olasilik daha dusuk."""
        odds = [1.4, 5.0, 9.0]
        proportional = proportional_probabilities(odds)
        shin = shin_probabilities(odds)
        assert proportional is not None and shin is not None
        self.assertLess(shin[2], proportional[2])
        self.assertGreater(shin[0], proportional[0])

    def test_margin_free_market_falls_back_to_proportional(self) -> None:
        odds = [2.0, 2.0]
        shin = shin_probabilities(odds)
        assert shin is not None
        self.assertAlmostEqual(shin[0], 0.5, places=9)

    def test_two_way_market_matches_proportional_for_symmetric_prices(self) -> None:
        odds = [1.9, 1.9]
        shin = shin_probabilities(odds)
        proportional = proportional_probabilities(odds)
        assert shin is not None and proportional is not None
        self.assertAlmostEqual(shin[0], proportional[0], places=6)


class FairProbabilityDispatchTests(unittest.TestCase):
    def test_method_selection(self) -> None:
        odds = [1.5, 4.0, 7.0]
        self.assertEqual(fair_probabilities(odds, method="shin"), shin_probabilities(odds))
        self.assertEqual(
            fair_probabilities(odds, method="proportional"),
            proportional_probabilities(odds),
        )

    def test_unknown_method_returns_none(self) -> None:
        self.assertIsNone(fair_probabilities([2.0, 2.0], method="bilinmeyen"))

    def test_default_method_is_shin(self) -> None:
        odds = [1.5, 4.0, 7.0]
        self.assertEqual(fair_probabilities(odds), shin_probabilities(odds))


if __name__ == "__main__":
    unittest.main()
