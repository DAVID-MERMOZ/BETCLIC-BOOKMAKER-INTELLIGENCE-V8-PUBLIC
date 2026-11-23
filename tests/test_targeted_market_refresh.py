import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from targeted_market_refresh import build_targeted_refresh


class TargetedMarketRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "db.sqlite"
        self.changes = root / "changes.csv"
        self.predictions = root / "predictions.csv"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE matches (
                    match_id TEXT, match_name TEXT, match_date TEXT,
                    sport TEXT, is_live INTEGER
                );
                CREATE TABLE odds_history (
                    match_id TEXT, market_name TEXT, selection_name TEXT,
                    odds REAL, suspended INTEGER, collected_at TEXT
                );
            """)
            conn.execute("INSERT INTO matches VALUES ('m1','Alpha - Beta','2026-11-24T18:00:00Z','football',0)")
            conn.executemany(
                "INSERT INTO odds_history VALUES (?,?,?,?,?,?)",
                [
                    ("m1", "Résultat du match (t. rég)", "Alpha", 2.0, 0, "2026-11-23T10:00:00+00:00"),
                    ("m1", "Résultat du match (t. rég)", "Nul", 3.0, 0, "2026-11-23T10:00:00+00:00"),
                    ("m1", "Résultat du match (t. rég)", "Beta", 4.0, 0, "2026-11-23T10:00:00+00:00"),
                ],
            )
        with self.changes.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["match_id"])
            writer.writeheader()
            writer.writerow({"match_id": "m1"})
        with self.predictions.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["match_id", "match_name", "model_status"])
            writer.writeheader()
            writer.writerow({"match_id": "m1", "match_name": "Alpha - Beta", "model_status": "EXPERIMENTAL_NOT_VALIDATED"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_refreshes_only_market_probabilities_and_preserves_model_status(self):
        rows = build_targeted_refresh(
            self.db, self.changes, self.predictions,
            datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["targeted_refresh_status"], "MARKET_ONLY_UPDATED")
        self.assertEqual(row["model_probabilities_status"], "UNCHANGED_NOT_RECALCULATED")
        self.assertEqual(row["model_status"], "EXPERIMENTAL_NOT_VALIDATED")
        self.assertAlmostEqual(
            row["market_prob_home"] + row["market_prob_draw"] + row["market_prob_away"],
            1.0, places=5,
        )

    def test_marks_missing_complete_market_as_incomplete(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM odds_history WHERE selection_name='Beta'")
        rows = build_targeted_refresh(
            self.db, self.changes, self.predictions,
            datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["targeted_refresh_status"], "INCOMPLETE")


    def test_excludes_quotes_collected_after_kickoff(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE odds_history SET collected_at='2026-11-24T18:01:00+00:00'")
        rows = build_targeted_refresh(
            self.db, self.changes, self.predictions,
            datetime(2026, 11, 24, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["targeted_refresh_status"], "INCOMPLETE")

    def test_rejects_match_already_started(self):
        rows = build_targeted_refresh(
            self.db, self.changes, self.predictions,
            datetime(2026, 11, 24, 18, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["market_status"], "MATCH_NOT_ELIGIBLE_PREMATCH")
        self.assertEqual(rows[0]["targeted_refresh_status"], "INCOMPLETE")

    def test_rejects_live_match(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE matches SET is_live=1 WHERE match_id='m1'")
        rows = build_targeted_refresh(
            self.db, self.changes, self.predictions,
            datetime(2026, 11, 23, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["market_status"], "MATCH_NOT_ELIGIBLE_PREMATCH")

if __name__ == "__main__":
    unittest.main()
