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


if __name__ == "__main__":
    unittest.main()
