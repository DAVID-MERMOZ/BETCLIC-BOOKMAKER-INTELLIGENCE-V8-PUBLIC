import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import database


class SaveOddsDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        database.initialize_database()

    @staticmethod
    def make_match(selections):
        market = SimpleNamespace(
            name="Résultat",
            suspended=False,
            selections=[
                SimpleNamespace(name=name, odds=odds)
                for name, odds in selections
            ],
        )
        return SimpleNamespace(id="match-1", markets=[market])

    def test_duplicate_selections_in_one_snapshot_are_saved_once(self):
        match = self.make_match([
            ("Domicile", 1.8),
            ("Domicile", 1.8),
            ("Nul", 3.2),
        ])
        inserted = database.save_odds(match)
        self.assertEqual(inserted, 2)
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM odds_history"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_later_snapshot_is_preserved_even_if_odds_are_unchanged(self):
        match = self.make_match([("Domicile", 1.8)])
        first = database.save_odds(match)
        second = database.save_odds(match)
        self.assertEqual((first, second), (1, 1))
        with sqlite3.connect(self.db_path) as conn:
            count, distinct_dates = conn.execute("""
                SELECT COUNT(*), COUNT(DISTINCT collected_at)
                FROM odds_history
            """).fetchone()
        self.assertEqual(count, 2)
        self.assertEqual(distinct_dates, 2)

    def test_save_match_persists_competition_name(self):
        match = SimpleNamespace(
            id="match-competition", name="Alpha - Beta",
            date="2026-11-24T18:00:00+00:00", is_live=False,
            competition="Ligue de test",
        )
        database.save_match(match)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT competition_name FROM matches WHERE match_id=?",
                ("match-competition",),
            ).fetchone()
        self.assertEqual(row[0], "Ligue de test")

    def test_initialize_migrates_legacy_matches_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE matches (
                    match_id TEXT PRIMARY KEY, match_name TEXT,
                    match_date TEXT, sport TEXT, is_live INTEGER
                )
            """)
        database.initialize_database()
        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
        self.assertIn("competition_name", columns)

    def test_composite_lookup_index_is_created(self):
        with sqlite3.connect(self.db_path) as conn:
            indexes = {
                row[1] for row in conn.execute(
                    "PRAGMA index_list(odds_history)"
                )
            }
        self.assertIn("idx_odds_history_exact", indexes)


if __name__ == "__main__":
    unittest.main()
