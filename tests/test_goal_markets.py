import unittest
from datetime import datetime, timezone, timedelta
from goal_markets import poisson_score_probabilities, predict_goal_markets


class GoalMarketTests(unittest.TestCase):
    def test_market_probabilities_are_valid_and_complementary(self):
        p = poisson_score_probabilities(1.45, 1.10)
        for key, value in p.items():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 1)
        self.assertAlmostEqual(p["OVER_1_5"] + p["UNDER_1_5"], 1.0)
        self.assertAlmostEqual(p["OVER_2_5"] + p["UNDER_2_5"], 1.0)
        self.assertAlmostEqual(p["OVER_3_5"] + p["UNDER_3_5"], 1.0)
        self.assertAlmostEqual(p["BTTS_YES"] + p["BTTS_NO"], 1.0)
        self.assertGreater(p["OVER_1_5"], p["OVER_2_5"])
        self.assertGreater(p["OVER_2_5"], p["OVER_3_5"])

    def test_no_future_leakage_and_minimum_history(self):
        t = datetime(2026, 11, 20, tzinfo=timezone.utc)
        hist = []
        for i in range(12):
            hist.append({"date": t - timedelta(days=20-i), "home": "H",
                         "away": "X", "hg": 2, "ag": 1})
        hist += [{"date": t + timedelta(days=1), "home": "H", "away": "A", "hg": 8, "ag": 8}]
        result = predict_goal_markets(hist, "H", "A", t)
        self.assertEqual(result["status"], "NO_BET_INSUFFICIENT_GOAL_HISTORY")
        self.assertEqual(result["home_history_n"], 5)
        self.assertEqual(result["away_history_n"], 0)

    def test_goal_model_returns_experimental_estimates_with_enough_history(self):
        t = datetime(2026, 11, 20, tzinfo=timezone.utc)
        hist = []
        for i in range(20):
            d = t - timedelta(days=30-i)
            hist.append({"date": d, "home": "H" if i % 2 == 0 else "A",
                         "away": "A" if i % 2 == 0 else "H", "hg": 2, "ag": 1})
        result = predict_goal_markets(hist, "H", "A", t)
        self.assertEqual(result["status"], "EXPERIMENTAL_NOT_VALIDATED")
        self.assertAlmostEqual(result["OVER_2_5"] + result["UNDER_2_5"], 1.0)
        self.assertGreater(result["lambda_home"], 0)
        self.assertGreater(result["lambda_away"], 0)


if __name__ == "__main__":
    unittest.main()
