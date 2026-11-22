import unittest
from probability_engine import calculate_fair_probabilities


class FairProbabilityTests(unittest.TestCase):
    def test_removes_overround_and_probabilities_sum_to_one(self):
        result = calculate_fair_probabilities([
            ("Home", 2.0), ("Draw", 3.2), ("Away", 4.0)
        ])
        self.assertIsNotNone(result)
        self.assertGreater(result["bookmaker_margin_pct"], 0)
        total = sum(x["fair_probability"] for x in result["probabilities"])
        self.assertAlmostEqual(total, 1.0, places=12)

    def test_even_market_has_fifty_fifty_probabilities(self):
        result = calculate_fair_probabilities([("A", 2.0), ("B", 2.0)])
        self.assertAlmostEqual(result["probabilities"][0]["fair_probability"], 0.5)
        self.assertAlmostEqual(result["bookmaker_margin_pct"], 0.0)

    def test_invalid_and_duplicate_selections_are_ignored(self):
        result = calculate_fair_probabilities([
            ("A", 2.0), ("A", 2.2), ("B", 1.0), ("C", "bad")
        ])
        self.assertIsNone(result)

    def test_single_outcome_is_not_a_market(self):
        self.assertIsNone(calculate_fair_probabilities([("A", 1.5)]))


if __name__ == "__main__":
    unittest.main()
