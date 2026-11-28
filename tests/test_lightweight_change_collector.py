import csv
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from lightweight_change_collector import collect_changes, write_report


class LightweightChangeCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE matches (
                    match_id TEXT PRIMARY KEY, match_name TEXT, match_date TEXT,
                    sport TEXT, is_live INTEGER, competition_name TEXT
                );
                CREATE TABLE odds_history (
                    id INTEGER PRIMARY KEY, match_id TEXT, market_name TEXT,
                    selection_name TEXT, odds REAL, suspended INTEGER,
                    collected_at TEXT
                );
            """)
            conn.execute(
                "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?)",
                ("m1", "A - B", "2026-11-24T18:00:00+00:00", "football", 0, "League"),
            )
            conn.executemany(
                """INSERT INTO odds_history
                   (match_id, market_name, selection_name, odds, suspended, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    ("m1", "1X2", "A", 2.0, 0, "2026-11-23T10:00:00+00:00"),
                    ("m1", "1X2", "A", 1.8, 0, "2026-11-23T11:00:00+00:00"),
                    ("m1", "1X2", "B", 3.0, 0, "2026-11-23T10:00:00+00:00"),
                    ("m1", "1X2", "B", 3.0, 1, "2026-11-23T11:00:00+00:00"),
                ],
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_price_and_suspension_changes(self):
        changes = collect_changes(
            self.db, datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(len(changes), 2)
        by_selection = {row["selection_name"]: row for row in changes}
        self.assertEqual(by_selection["A"]["change_type"], "ODDS_CHANGED")
        self.assertAlmostEqual(by_selection["A"]["odds_change_pct"], -10.0)
        self.assertEqual(by_selection["B"]["change_type"], "SUSPENSION_CHANGED")

    def test_ignores_single_snapshot_and_past_matches(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE matches SET match_date=? WHERE match_id='m1'",
                ("2026-11-22T18:00:00+00:00",),
            )
        changes = collect_changes(
            self.db, datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(changes, [])

    def test_writes_idempotent_report(self):
        changes = collect_changes(
            self.db, datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        )
        output = Path(self.tmp.name) / "changes.csv"
        write_report(changes, output)
        first = output.read_text(encoding="utf-8")
        write_report(changes, output)
        self.assertEqual(output.read_text(encoding="utf-8"), first)
        with output.open(encoding="utf-8") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_main_logs_aggregate_counts_without_fixture_names_or_ids(self):
        from lightweight_change_collector import main

        private_row = {
            "match_id": "private-match-123",
            "match_name": "Private Home - Private Away",
            "match_date": "2026-11-30T18:00:00+00:00",
            "market_name": "1X2",
            "selection_name": "Private Home",
            "previous_odds": 2.0,
            "latest_odds": 1.9,
            "odds_change_pct": -5.0,
            "previous_suspended": 0,
            "latest_suspended": 0,
            "previous_snapshot_at": "2026-11-28T08:00:00+00:00",
            "latest_snapshot_at": "2026-11-28T08:05:00+00:00",
            "change_type": "ODDS_CHANGED",
        }
        output = io.StringIO()
        report = Path(self.tmp.name) / "public-safe.csv"
        with patch("lightweight_change_collector.collect_changes", return_value=[private_row]), \
             patch("sys.argv", ["lightweight_change_collector.py", "--db", str(self.db),
                                "--output", str(report)]), \
             redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertIn("MATCHS A REEVALUER : 1", output.getvalue())
        self.assertNotIn("private-match-123", output.getvalue())
        self.assertNotIn("Private Home", output.getvalue())

    def test_ignores_change_when_latest_snapshot_is_stale(self):
        changes = collect_changes(
            self.db, datetime(2026, 11, 23, 14, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
