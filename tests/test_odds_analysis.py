import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import odds_analysis


class UpcomingMatchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE matches (
                    match_id TEXT PRIMARY KEY,
                    match_name TEXT,
                    match_date TEXT,
                    is_live INTEGER
                )
            """)
        self.db_patch = patch.object(odds_analysis, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def add_match(self, match_id, match_date, is_live=0):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO matches VALUES (?, ?, ?, ?)",
                (match_id, match_id, match_date, is_live),
            )

    def test_excludes_live_past_and_invalid_dates(self):
        now = datetime.now(timezone.utc)
        self.add_match("future", (now + timedelta(hours=2)).isoformat())
        self.add_match("live", (now + timedelta(hours=3)).isoformat(), 1)
        self.add_match("past", (now - timedelta(hours=1)).isoformat())
        self.add_match("invalid", "date-invalide")

        matches, stats = odds_analysis.select_upcoming_matches()

        self.assertEqual([row[0] for row in matches], ["future"])
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["upcoming"], 1)
        self.assertEqual(stats["live_skipped"], 1)
        self.assertEqual(stats["past_skipped"], 1)
        self.assertEqual(stats["invalid_dates"], 1)

    def test_sorts_soonest_first_and_respects_limit(self):
        now = datetime.now(timezone.utc)
        self.add_match("later", (now + timedelta(hours=3)).isoformat())
        self.add_match("sooner", (now + timedelta(hours=1)).isoformat())
        self.add_match("soonest", (now + timedelta(minutes=30)).isoformat())

        matches, _ = odds_analysis.select_upcoming_matches(limit=2)

        self.assertEqual([row[0] for row in matches], ["soonest", "sooner"])

    def test_naive_date_is_interpreted_as_utc(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertEqual(
            odds_analysis.parse_date(future.replace(tzinfo=None).isoformat()).tzinfo,
            timezone.utc,
        )


if __name__ == "__main__":
    unittest.main()
