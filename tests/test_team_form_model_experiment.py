import unittest
from datetime import datetime, timezone, timedelta

from team_form_model_experiment import team_form, evaluate
from baseline_market_evaluation import CLASSES


class TeamFormTests(unittest.TestCase):
    def test_form_uses_only_prior_matches(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [
            {"date": t - timedelta(days=i), "home": "alpha", "away": "beta",
             "hg": 2, "ag": 0}
            for i in range(1, 4)
        ]
        history.append({"date": t, "home": "alpha", "away": "gamma",
                        "hg": 0, "ag": 5})
        form = team_form(history, "alpha", t)
        self.assertIsNotNone(form)
        self.assertEqual(form[0], 3.0)
        self.assertEqual(form[1], 2.0)

    def test_requires_three_prior_matches(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [{"date": t - timedelta(days=1), "home": "alpha",
                    "away": "beta", "hg": 1, "ag": 0}]
        self.assertIsNone(team_form(history, "alpha", t))

    def test_small_sample_rejected(self):
        rows = [{"kickoff": datetime(2026, 1, i + 1, tzinfo=timezone.utc),
                 "outcome": CLASSES[i % 3], "features": [0.0] * 8}
                for i in range(10)]
        self.assertEqual(evaluate(rows, [])["status"],
                         "INSUFFICIENT_TEAM_FORM_SAMPLE")


if __name__ == "__main__":
    unittest.main()
