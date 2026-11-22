import unittest
from datetime import datetime, timedelta, timezone

from predictive_model_experiment import evaluate_chronological_model, filter_to_eligible_matches, top_label_calibration_error


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

    def test_matches_without_sufficient_history_are_excluded(self):
        market = [
            {"match_id": "valid", "outcome": "HOME"},
            {"match_id": "invalid", "outcome": "AWAY"},
        ]
        eligible = [{"match_id": "valid"}]
        self.assertEqual(
            [r["match_id"] for r in filter_to_eligible_matches(market, eligible)],
            ["valid"],
        )


    def test_top_label_calibration_error_is_zero_for_perfectly_calibrated_bin(self):
        rows = [
            {"outcome": "HOME", "probs": {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2}},
            {"outcome": "DRAW", "probs": {"HOME": 0.2, "DRAW": 0.5, "AWAY": 0.3}},
        ]
        result = top_label_calibration_error(rows, bins=5)
        self.assertEqual(result["ece"], 0.0)
        self.assertEqual(sum(item["n"] for item in result["bins"]), 2)

    def test_missing_training_class_is_rejected(self):
        rows = self.make_rows(30)
        for row in rows[:21]:
            row["outcome"] = "HOME"
        result = evaluate_chronological_model(rows)
        self.assertEqual(result["status"], "TRAINING_SET_MISSING_CLASS")


if __name__ == "__main__":
    unittest.main()
