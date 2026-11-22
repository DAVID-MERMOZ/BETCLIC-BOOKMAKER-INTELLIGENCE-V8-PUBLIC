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
        metrics = score_metrics(rows)
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertAlmostEqual(metrics["ece_top_label"], .3)
        self.assertTrue(metrics["probability_vectors_valid"])

    def test_metrics_reject_probability_vectors_not_summing_to_one(self):
        rows = [{"outcome": "HOME", "probs": {"HOME": .7, "DRAW": .2, "AWAY": .2}}]
        with self.assertRaises(ValueError):
            score_metrics(rows)

    def test_metrics_reject_non_finite_or_out_of_range_probabilities(self):
        for probs in (
            {"HOME": float("nan"), "DRAW": .2, "AWAY": .8},
            {"HOME": 1.2, "DRAW": -.1, "AWAY": -.1},
        ):
            with self.subTest(probs=probs):
                with self.assertRaises(ValueError):
                    score_metrics([{"outcome": "HOME", "probs": probs}])


if __name__ == "__main__":
    unittest.main()
