import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from prediction_history import persist_predictions


class PredictionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "history.db"
        self.csv = root / "predictions.csv"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE validated_match_links (
                    betclic_match_id TEXT, home_score INTEGER, away_score INTEGER,
                    validation_status TEXT
                );
            """)
        fields = [
            "match_id", "match_name", "kickoff_utc", "prediction",
            "prob_home", "prob_draw", "prob_away", "model_status",
            "history_quality", "market_status", "market_snapshot_utc",
            "market_prob_home", "market_prob_draw", "market_prob_away",
            "action_status",
        ]
        with self.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "match_id": "m1", "match_name": "Alpha - Beta",
                "kickoff_utc": "2026-11-24T18:00:00+00:00",
                "prediction": "HOME", "prob_home": ".5", "prob_draw": ".3",
                "prob_away": ".2", "model_status": "EXPERIMENTAL_NOT_VALIDATED",
                "history_quality": "STANDARD_3_PLUS", "market_status": "AVAILABLE",
                "market_snapshot_utc": "2026-11-23T10:00:00+00:00",
                "market_prob_home": ".4", "market_prob_draw": ".35",
                "market_prob_away": ".25", "action_status": "NO_BET_MODEL_UNVALIDATED",
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_is_persisted_and_idempotent_per_run(self):
        first = persist_predictions(self.db, self.csv, "run-1",
                                    "2026-11-23T11:00:00+00:00")
        second = persist_predictions(self.db, self.csv, "run-1",
                                     "2026-11-23T11:00:00+00:00")
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM prediction_history").fetchone()[0], 1)

    def test_confirmed_result_evaluates_prediction_and_conflicts_do_not(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO validated_match_links VALUES ('m1', 2, 1, 'CONFIRMED')")
        result = persist_predictions(self.db, self.csv, "run-2",
                                     "2026-11-23T12:00:00+00:00")
        self.assertEqual(result["evaluated"], 1)
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("""
                SELECT actual_outcome, actual_home_score, actual_away_score
                FROM prediction_history
            """).fetchone()
        self.assertEqual(row, ("HOME", 2, 1))
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO validated_match_links VALUES ('m1', 0, 2, 'CONFIRMED')")
        persist_predictions(self.db, self.csv, "run-3",
                            "2026-11-23T13:00:00+00:00")
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("""
                SELECT actual_outcome FROM prediction_history ORDER BY id
            """).fetchall()
        self.assertEqual(rows, [("HOME",), (None,)])

if __name__ == "__main__":
    unittest.main()
