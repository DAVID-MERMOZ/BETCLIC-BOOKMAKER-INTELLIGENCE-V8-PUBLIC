import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backfill_prediction_history import admissible_row, audit_existing_database


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


class BackfillDatabaseAuditTests(unittest.TestCase):
    def test_audit_counts_unique_existing_new_and_confirmed_candidates_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE prediction_history (
                        workflow_run_id TEXT, match_id TEXT, actual_outcome TEXT
                    )
                """)
                conn.execute(
                    "INSERT INTO prediction_history VALUES ('run-1', 'm-1', 'HOME')"
                )
                conn.execute("""
                    CREATE TABLE validated_match_links (
                        betclic_match_id TEXT, home_score INTEGER, away_score INTEGER,
                        validation_status TEXT
                    )
                """)
                conn.execute(
                    "INSERT INTO validated_match_links VALUES ('m-2', 2, 1, 'CONFIRMED')"
                )
                conn.execute(
                    "INSERT INTO validated_match_links VALUES ('m-3', 1, 0, 'CONFIRMED')"
                )
                conn.execute(
                    "INSERT INTO validated_match_links VALUES ('m-3', 0, 1, 'CONFIRMED')"
                )

            candidates = {("run-1", "m-1"), ("run-2", "m-1"),
                          ("run-3", "m-2"), ("run-4", "m-3")}
            with patch("backfill_prediction_history.DB_PATH", db_path):
                audit = audit_existing_database(candidates)

            self.assertEqual(audit["unique_candidate_keys"], 4)
            self.assertEqual(audit["unique_candidate_match_ids"], 3)
            self.assertEqual(audit["already_in_database_keys"], 1)
            self.assertEqual(audit["new_candidate_keys"], 3)
            self.assertEqual(audit["candidate_matches_with_evaluated_prediction"], 1)
            self.assertEqual(audit["candidate_matches_with_unambiguous_confirmed_score"], 1)
            self.assertNotIn("candidate_ids_sample", audit)
            self.assertNotIn("m-1", audit["candidate_id_fingerprints_sample"])
            self.assertNotIn("m-2", audit["confirmed_betclic_id_fingerprints_sample"])

            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
