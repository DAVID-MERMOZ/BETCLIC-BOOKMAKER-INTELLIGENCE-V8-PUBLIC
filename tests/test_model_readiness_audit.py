import sqlite3
import tempfile
import unittest
from pathlib import Path
from model_readiness_audit import audit_model_readiness, MIN_LABELED_MATCHES, MIN_PRODUCTION_MATCHES


class ModelReadinessAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "audit.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE matches (match_id TEXT PRIMARY KEY, match_date TEXT);
                CREATE TABLE validated_match_links (
                    betclic_match_id TEXT, home_score INTEGER, away_score INTEGER,
                    validation_status TEXT
                );
                CREATE TABLE odds_history (
                    match_id TEXT, market_name TEXT, selection_name TEXT,
                    odds REAL, suspended INTEGER, collected_at TEXT
                );
            """)
            conn.execute("INSERT INTO matches VALUES ('m1', '2026-01-01T20:00:00Z')")
            conn.execute("INSERT INTO validated_match_links VALUES ('m1', 2, 1, 'CONFIRMED')")
            conn.executemany("INSERT INTO odds_history VALUES ('m1','1X2',?,?,0,?)", [
                ("Home", 2.0, "2026-01-01T18:00:00Z"),
                ("Draw", 3.0, "2026-01-01T18:00:00Z"),
                ("Away", 4.0, "2026-01-01T18:00:00Z"),
                ("Home", 1.5, "2026-01-01T21:00:00Z"),
            ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_threshold_is_experimental_launch_30_matches(self):
        self.assertEqual(MIN_LABELED_MATCHES, 30)

    def test_production_threshold_is_separate_and_requires_200_matches(self):
        self.assertEqual(MIN_PRODUCTION_MATCHES, 200)
        result = audit_model_readiness(self.db, min_labeled_matches=1)
        self.assertEqual(result["status"], "READY_FOR_BASELINE_EXPERIMENTS")
        self.assertEqual(result["launch_status"], "COLLECTING_BELOW_EXPERIMENTAL_LAUNCH_THRESHOLD")
        self.assertEqual(result["production_status"], "ROBUST_VALIDATION_SAMPLE_INSUFFICIENT")

    def test_launch_threshold_is_30_matches_with_pre_match_odds(self):
        with sqlite3.connect(self.db) as conn:
            for i in range(2, 31):
                match_id = f"m{i}"
                conn.execute("INSERT INTO matches VALUES (?, ?)", (match_id, "2026-01-01T20:00:00Z"))
                conn.execute("INSERT INTO validated_match_links VALUES (?, 1, 0, 'CONFIRMED')", (match_id,))
                conn.executemany("INSERT INTO odds_history VALUES (?,'1X2',?,?,0,?)", [
                    (match_id, "Home", 2.0, "2026-01-01T18:00:00Z"),
                    (match_id, "Draw", 3.0, "2026-01-01T18:00:00Z"),
                    (match_id, "Away", 4.0, "2026-01-01T18:00:00Z"),
                ])
        result = audit_model_readiness(self.db)
        self.assertEqual(result["labeled_matches"], 30)
        self.assertEqual(result["matches_with_pre_match_odds"], 30)
        self.assertEqual(result["launch_status"], "LAUNCH_ALLOWED_EXPERIMENTAL")
        self.assertEqual(result["production_status"], "ROBUST_VALIDATION_SAMPLE_INSUFFICIENT")

    def test_counts_only_pre_kickoff_prices(self):
        result = audit_model_readiness(self.db, min_labeled_matches=1)
        self.assertEqual(result["labeled_matches"], 1)
        self.assertEqual(result["matches_with_pre_match_odds"], 1)
        self.assertEqual(result["outcomes"], {"HOME": 1})
        self.assertEqual(result["markets"]["1X2"]["matches"], 1)
        self.assertEqual(result["status"], "READY_FOR_BASELINE_EXPERIMENTS")

    def test_does_not_mark_small_sample_ready(self):
        result = audit_model_readiness(self.db, min_labeled_matches=200)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_excludes_suspended_and_invalid_prices(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO odds_history VALUES ('m1','1X2','Other',1.0,0,'2026-01-01T18:00:00Z')")
            conn.execute("INSERT INTO odds_history VALUES ('m1','1X2','Suspended',2.0,1,'2026-01-01T18:00:00Z')")
        result = audit_model_readiness(self.db, min_labeled_matches=1)
        self.assertEqual(result["markets"]["1X2"]["selections"], 3)

    def test_counts_one_label_when_multiple_providers_confirm_same_match(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO validated_match_links VALUES ('m1', 2, 1, 'CONFIRMED')")
        result = audit_model_readiness(self.db, min_labeled_matches=1)
        self.assertEqual(result["confirmed_links"], 2)
        self.assertEqual(result["unique_confirmed_matches"], 1)
        self.assertEqual(result["labeled_matches"], 1)
        self.assertEqual(result["outcomes"], {"HOME": 1})

    def test_excludes_match_when_confirmed_providers_disagree_on_score(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO validated_match_links VALUES ('m1', 0, 1, 'CONFIRMED')")
        result = audit_model_readiness(self.db, min_labeled_matches=1)
        self.assertEqual(result["confirmed_links"], 2)
        self.assertEqual(result["unique_confirmed_matches"], 1)
        self.assertEqual(result["conflicting_labels"], 1)
        self.assertEqual(result["labeled_matches"], 0)
        self.assertEqual(result["matches_with_pre_match_odds"], 0)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_requires_expected_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.db"
            sqlite3.connect(empty).close()
            with self.assertRaises(RuntimeError):
                audit_model_readiness(empty)


if __name__ == "__main__":
    unittest.main()
