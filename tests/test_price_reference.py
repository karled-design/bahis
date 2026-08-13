import unittest

from core.price_reference import (
    REFERENCE_EXCHANGE,
    REFERENCE_MARKET,
    REFERENCE_PINNACLE,
    REFERENCE_SECONDARY,
    is_notifiable_reference,
    max_tier_for_reference,
    probability_space_mean_odds,
)
from core.scan_pipeline import evaluate_matches
from scrapers.sharp_feed import _resolve_reference_price


def _match(source: str) -> dict[str, object]:
    return {
        "match_name": "Test A - Test B",
        "market": "MS1",
        "sharp_odds": 2.0,
        "soft_odds": 2.2,
        "consensus_books": 3,
        "consensus_source": source,
    }


class ReferenceQualityTests(unittest.TestCase):
    def test_tier_authority_by_source(self) -> None:
        self.assertEqual(max_tier_for_reference(REFERENCE_PINNACLE), "HIGH")
        self.assertEqual(max_tier_for_reference(REFERENCE_EXCHANGE), "HIGH")
        self.assertEqual(max_tier_for_reference(REFERENCE_SECONDARY), "WATCH")
        self.assertIsNone(max_tier_for_reference(REFERENCE_MARKET))
        self.assertIsNone(max_tier_for_reference(""))

    def test_is_notifiable_reference(self) -> None:
        self.assertTrue(is_notifiable_reference(REFERENCE_PINNACLE))
        self.assertFalse(is_notifiable_reference(REFERENCE_MARKET))

    def test_probability_space_mean_below_arithmetic_mean(self) -> None:
        prices = [2.0, 4.0]
        mean_odds = probability_space_mean_odds(prices)
        assert mean_odds is not None
        self.assertLess(mean_odds, sum(prices) / len(prices))
        self.assertAlmostEqual(mean_odds, 1.0 / ((0.5 + 0.25) / 2), places=9)

    def test_probability_space_mean_ignores_invalid_prices(self) -> None:
        self.assertIsNone(probability_space_mean_odds([0.0, 1.0]))
        self.assertIsNone(probability_space_mean_odds([]))


class ResolveReferencePriceTests(unittest.TestCase):
    def test_pinnacle_wins_over_everything(self) -> None:
        bucket = {
            "all": [1.9, 2.0, 2.1],
            "books": {"pinnacle": 2.05, "smarkets": 2.2, "bet365": 1.9},
        }
        resolved = _resolve_reference_price(bucket, min_books=2)
        self.assertEqual(resolved, (2.05, REFERENCE_PINNACLE, 1))

    def test_exchange_consensus_when_pinnacle_missing(self) -> None:
        bucket = {
            "all": [2.0, 2.2, 1.8],
            "books": {"smarkets": 2.0, "matchbook": 2.2, "bet365": 1.8},
        }
        resolved = _resolve_reference_price(bucket, min_books=2)
        assert resolved is not None
        odds, source, books = resolved
        self.assertEqual(source, REFERENCE_EXCHANGE)
        self.assertEqual(books, 2)
        self.assertAlmostEqual(odds, 1.0 / ((1 / 2.0 + 1 / 2.2) / 2), places=9)

    def test_secondary_books_marked_as_sharp_consensus(self) -> None:
        bucket = {
            "all": [1.8, 1.9],
            "books": {"bet365": 1.8, "unibet_uk": 1.9},
        }
        resolved = _resolve_reference_price(bucket, min_books=2)
        assert resolved is not None
        self.assertEqual(resolved[1], REFERENCE_SECONDARY)

    def test_falls_back_to_market_consensus(self) -> None:
        bucket = {
            "all": [1.8, 1.9, 2.0],
            "books": {"onexbet": 1.8, "nordicbet": 1.9, "betsson": 2.0},
        }
        resolved = _resolve_reference_price(bucket, min_books=2)
        assert resolved is not None
        self.assertEqual(resolved[1], REFERENCE_MARKET)
        self.assertEqual(resolved[2], 3)

    def test_returns_none_when_not_enough_prices(self) -> None:
        bucket = {"all": [1.9], "books": {"betsson": 1.9}}
        self.assertIsNone(_resolve_reference_price(bucket, min_books=2))


class PipelineReferenceGateTests(unittest.TestCase):
    def _evaluate(self, source: str):
        return evaluate_matches(
            [_match(source)],
            current_kasa=1000.0,
            skip_freshness=True,
        )

    def test_pinnacle_reference_can_produce_action(self) -> None:
        candidates, stats = self._evaluate(REFERENCE_PINNACLE)
        self.assertEqual(len(candidates), 1)
        self.assertIn(candidates[0].tier, ("ACTION", "HIGH"))
        self.assertEqual(stats.zayif_referans, 0)

    def test_secondary_reference_downgraded_to_watch(self) -> None:
        candidates, _stats = self._evaluate(REFERENCE_SECONDARY)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].tier, "WATCH")
        self.assertEqual(candidates[0].stake, 0.0)

    def test_market_consensus_produces_no_candidate(self) -> None:
        candidates, stats = self._evaluate(REFERENCE_MARKET)
        self.assertEqual(candidates, [])
        self.assertEqual(stats.zayif_referans, 1)

    def test_missing_reference_label_is_blocked(self) -> None:
        match = _match(REFERENCE_PINNACLE)
        del match["consensus_source"]
        candidates, stats = evaluate_matches(
            [match],
            current_kasa=1000.0,
            skip_freshness=True,
        )
        self.assertEqual(candidates, [])
        self.assertEqual(stats.zayif_referans, 1)


if __name__ == "__main__":
    unittest.main()
