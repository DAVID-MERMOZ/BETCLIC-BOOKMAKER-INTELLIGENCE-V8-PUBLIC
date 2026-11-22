import unittest
from datetime import datetime, timedelta, timezone

from predictive_model_experiment import evaluate_chronological_model


class PredictiveModelExperimentTests(unittest.TestCase):
    def make_rows(self, n=40):
        rows = []
        for i in range(n):
            outcome = ("HOME", "DRAW", "AWAY")[i % 3]
            probs = {"HOME": 0.60, "DRAW": 0.25, "AWAY": 0.15}
            if outcome == "DRAW":
                probs = {"HOME": 0.20, "DRAW": 0.60, "AWAY": 0.20}
            elif outcome == "AWAY":
                probs = {"HOME": 0.15, "DRAW": 0.25, "AWAY": 0.60}
            rows.append({
                "kickoff": datetime(2025, 1, 1, tzinfo=timezone.utc)
                           + timedelta(days=i),
                "outcome": outcome,
                "probs": probs,
            })
        return rows

    def test_chronological_split_and_metrics(self):
        result = evaluate_chronological_model(self.make_rows())
        self.assertEqual(result["status"], "MODEL_EXPERIMENT_COMPLETED")
        self.assertEqual(result["train_n"], 28)
        self.assertEqual(result["test_n"], 12)
        self.assertIn("log_loss", result["model"])
        self.assertIn("brier_score", result["market"])

    def test_small_sample_is_rejected(self):
        result = evaluate_chronological_model(self.make_rows(12))
        self.assertEqual(result["status"], "INSUFFICIENT_TRAIN_OR_TEST_SAMPLE")

    def test_missing_training_class_is_rejected(self):
        rows = self.make_rows(30)
        for row in rows[:21]:
            row["outcome"] = "HOME"
        result = evaluate_chronological_model(rows)
        self.assertEqual(result["status"], "TRAINING_SET_MISSING_CLASS")


if __name__ == "__main__":
    unittest.main()
