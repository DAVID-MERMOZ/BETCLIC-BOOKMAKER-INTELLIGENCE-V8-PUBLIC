import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sportscore_results import collect, value_score


class SportScoreParsingTests(unittest.TestCase):
    def test_nested_full_time_score_parses_both_sides(self):
        match = {"score": {"fullTime": {"home": 2, "away": 1}}}
        self.assertEqual(value_score(match, "home"), 2)
        self.assertEqual(value_score(match, "away"), 1)

    def test_nested_side_score_with_missing_side_does_not_crash(self):
        match = {"score": {"home": {"home": 3}}}
        self.assertEqual(value_score(match, "home"), 3)
        self.assertIsNone(value_score(match, "away"))

    def test_numeric_string_score_is_supported(self):
        match = {"score": {"home": "4", "away": "2"}}
        self.assertEqual(value_score(match, "home"), 4)
        self.assertEqual(value_score(match, "away"), 2)


class SportScoreCheckpointTests(unittest.TestCase):
    def test_completed_day_is_skipped_on_next_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "history.db"
            with sqlite3.connect(db) as conn:
                conn.execute("""
                    CREATE TABLE provider_results (
                        provider TEXT NOT NULL, provider_match_id TEXT NOT NULL,
                        competition_code TEXT, competition_name TEXT, utc_date TEXT,
                        status TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
                        home_score INTEGER, away_score INTEGER, score_json TEXT, fetched_at TEXT NOT NULL,
                        PRIMARY KEY (provider, provider_match_id)
                    )
                """)
            with patch("sportscore_results.fetch_day", return_value=[]) as fetch:
                first = collect(days_back=0, db_path=db)
                self.assertEqual(first[1], 1)
                self.assertEqual(first[5], 0)
                second = collect(days_back=0, db_path=db)
                self.assertEqual(second[1], 0)
                self.assertEqual(second[5], 1)
                self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
