import unittest
from baseline_market_evaluation import classify_selection, fair_probs, score_metrics, split_teams


class BaselineMarketTests(unittest.TestCase):
    def test_exact_team_mapping_and_draw(self):
        home, away = split_teams("Alpha FC - Beta United")
        self.assertEqual(classify_selection("Alpha FC", home, away), "HOME")
        self.assertEqual(classify_selection("Beta United", home, away), "AWAY")
        self.assertEqual(classify_selection("Match nul", home, away), "DRAW")

    def test_unknown_selection_is_not_guessed(self):
        self.assertIsNone(classify_selection("Alpha FC ou nul", "alpha fc", "beta"))

    def test_requires_three_outcomes(self):
        self.assertIsNone(fair_probs({"HOME": 2.0, "AWAY": 3.0}))

    def test_probabilities_sum_to_one(self):
        probs = fair_probs({"HOME": 2.0, "DRAW": 3.0, "AWAY": 4.0})
        self.assertAlmostEqual(sum(probs.values()), 1.0)

    def test_metrics(self):
        rows = [{"outcome": "HOME", "probs": {"HOME": .7, "DRAW": .2, "AWAY": .1}}]
        self.assertEqual(score_metrics(rows)["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
