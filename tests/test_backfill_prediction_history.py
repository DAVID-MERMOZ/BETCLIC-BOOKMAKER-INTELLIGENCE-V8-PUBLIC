import unittest
from backfill_prediction_history import admissible_row


class BackfillValidationTests(unittest.TestCase):
    def row(self):
        return {
            "match_id": "m-1", "match_name": "Home - Away",
            "kickoff_utc": "2026-11-27T20:00:00Z",
            "prediction": "HOME", "prob_home": "0.55",
            "prob_draw": "0.25", "prob_away": "0.20",
            "model_status": "EXPERIMENTAL",
        }

    def test_accepts_valid_strictly_pre_match_snapshot(self):
        self.assertTrue(admissible_row(self.row(), "2026-11-27T18:00:00Z"))

    def test_rejects_snapshot_at_or_after_kickoff(self):
        self.assertFalse(admissible_row(self.row(), "2026-11-27T20:00:00Z"))
        self.assertFalse(admissible_row(self.row(), "2026-11-27T21:00:00Z"))

    def test_rejects_invalid_probabilities(self):
        row = self.row()
        row["prob_away"] = "0.80"
        self.assertFalse(admissible_row(row, "2026-11-27T18:00:00Z"))
        row["prob_away"] = "nan"
        self.assertFalse(admissible_row(row, "2026-11-27T18:00:00Z"))

    def test_rejects_missing_required_fields_and_invalid_outcome(self):
        row = self.row()
        row["match_id"] = ""
        self.assertFalse(admissible_row(row, "2026-11-27T18:00:00Z"))
        row = self.row()
        row["prediction"] = "NO_BET"
        self.assertFalse(admissible_row(row, "2026-11-27T18:00:00Z"))


if __name__ == "__main__":
    unittest.main()
