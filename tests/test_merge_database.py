import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import merge_database


class MergeDatabaseCheckpointTests(unittest.TestCase):
    def test_sportscore_collection_checkpoints_are_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local.db"
            remote = Path(directory) / "remote.db"
            # La fusion exige une base locale existante, meme vide.
            sqlite3.connect(local).close()
            with sqlite3.connect(remote) as conn:
                conn.execute("""
                    CREATE TABLE provider_results (
                        provider TEXT NOT NULL, provider_match_id TEXT NOT NULL,
                        competition_code TEXT, competition_name TEXT, utc_date TEXT,
                        status TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
                        home_score INTEGER, away_score INTEGER, score_json TEXT, fetched_at TEXT NOT NULL,
                        PRIMARY KEY (provider, provider_match_id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE provider_collection_days (
                        provider TEXT NOT NULL, utc_date TEXT NOT NULL,
                        status TEXT NOT NULL, fixtures_seen INTEGER NOT NULL,
                        results_saved INTEGER NOT NULL, fetched_at TEXT NOT NULL,
                        PRIMARY KEY (provider, utc_date)
                    )
                """)
                conn.execute("""
                    INSERT INTO provider_collection_days
                    VALUES ('sportscore', '2026-01-01', 'COMPLETE', 4, 4, '2026-01-02T00:00:00Z')
                """)
            with patch.object(merge_database, "LOCAL_DB", local):
                merge_database.merge_databases(remote)
            with sqlite3.connect(local) as conn:
                row = conn.execute("""
                    SELECT provider, utc_date, status, fixtures_seen, results_saved
                    FROM provider_collection_days
                """).fetchone()
            self.assertEqual(row, ("sportscore", "2026-01-01", "COMPLETE", 4, 4))


    def test_prediction_history_is_merged_without_duplicates_and_keeps_confirmed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local.db"
            remote = Path(directory) / "remote.db"
            sqlite3.connect(local).close()
            schema = """
                CREATE TABLE prediction_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_run_id TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    prediction TEXT NOT NULL,
                    actual_outcome TEXT,
                    actual_home_score INTEGER,
                    actual_away_score INTEGER,
                    evaluated_at_utc TEXT,
                    UNIQUE(workflow_run_id, match_id)
                )
            """
            with sqlite3.connect(remote) as conn:
                conn.execute(schema)
                conn.execute(
                    "INSERT INTO prediction_history "
                    "(workflow_run_id, match_id, prediction, actual_outcome, "
                    "actual_home_score, actual_away_score, evaluated_at_utc) "
                    "VALUES ('run-1', 'match-1', 'HOME', 'HOME', 2, 1, '2026-11-25T20:00:00Z')"
                )
                conn.execute(
                    "INSERT INTO prediction_history "
                    "(workflow_run_id, match_id, prediction) "
                    "VALUES ('run-2', 'match-2', 'DRAW')"
                )
            with sqlite3.connect(local) as conn:
                conn.execute(schema)
                conn.execute(
                    "INSERT INTO prediction_history "
                    "(workflow_run_id, match_id, prediction) "
                    "VALUES ('run-1', 'match-1', 'HOME')"
                )
            with patch.object(merge_database, "LOCAL_DB", local):
                merge_database.merge_databases(remote)
            with sqlite3.connect(local) as conn:
                rows = conn.execute(
                    "SELECT workflow_run_id, match_id, prediction, actual_outcome, "
                    "actual_home_score, actual_away_score "
                    "FROM prediction_history ORDER BY workflow_run_id"
                ).fetchall()
            self.assertEqual(rows, [
                ("run-1", "match-1", "HOME", "HOME", 2, 1),
                ("run-2", "match-2", "DRAW", None, None, None),
            ])
            with patch.object(merge_database, "LOCAL_DB", local):
                merge_database.merge_databases(remote)
            with sqlite3.connect(local) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
