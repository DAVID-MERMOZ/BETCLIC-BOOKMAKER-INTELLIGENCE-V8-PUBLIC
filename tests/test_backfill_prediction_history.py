import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backfill_prediction_history import (admissible_row, audit_existing_database, audit_name_date_crosswalk)


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


class HistoricalCrosswalkTests(unittest.TestCase):
    def test_crosswalk_is_read_only_strict_and_aggregate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "history.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE matches (match_id TEXT, match_name TEXT, match_date TEXT, sport TEXT)")
                conn.executemany(
                    "INSERT INTO matches VALUES (?, ?, ?, ?)",
                    [
                        ("private-1", "Arsenal - Chelsea", "2026-11-27T20:00:00Z", "football"),
                        ("private-2", "Liverpool - Everton", "2026-11-27T20:00:00Z", "football"),
                        ("private-3", "Liverpool - Everton", "2026-11-27T20:00:00Z", "football"),
                        ("private-4", "Arsenal Women - Chelsea Women", "2026-11-27T20:00:00Z", "football"),
                    ],
                )
            fixtures = {
                "archive-a": {("Arsenal - Chelsea", "2026-11-27T20:00:00Z")},
                "archive-b": {("Liverpool - Everton", "2026-11-27T20:00:00Z")},
                "archive-c": {("Manchester - City", "2026-11-27T20:00:00Z")},
                "archive-d": {
                    ("Arsenal - Chelsea", "2026-11-27T20:00:00Z"),
                    ("Arsenal - Chelsea", "2026-11-27T21:00:00Z"),
                },
                "archive-e": {("Chelsea - Arsenal", "2026-11-27T20:00:00Z")},
                "archive-f": {("Arsenal - Chelsea", "2026-11-28T10:00:00Z")},
            }
            with patch("backfill_prediction_history.DB_PATH", db_path):
                audit = audit_name_date_crosswalk(fixtures)
            self.assertEqual(audit["fixtures_examined"], 6)
            self.assertEqual(audit["unique_crosswalk_candidates"], 1)
            self.assertEqual(audit["ambiguous_crosswalk_candidates"], 2)
            self.assertEqual(audit["no_crosswalk_candidate"], 3)
            self.assertEqual(audit["conflicting_archive_fixtures"], 1)
            self.assertEqual(audit["crosswalk_policy"], "READ_ONLY_AGGREGATE_ONLY_NO_LINKS")
            self.assertNotIn("private-1", str(audit))
            self.assertNotIn("Arsenal", str(audit))
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 4)
                self.assertFalse(conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='validated_match_links'"
                ).fetchone())


if __name__ == "__main__":
    unittest.main()
