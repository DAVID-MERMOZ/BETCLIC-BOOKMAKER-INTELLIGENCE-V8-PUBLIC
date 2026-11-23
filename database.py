import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("data/betclic_history.db")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def initialize_database():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                match_name TEXT,
                match_date TEXT,
                sport TEXT,
                is_live INTEGER,
                competition_name TEXT
            )
        """)
        # Migration idempotente pour les bases historiques déjà existantes.
        match_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
        }
        if "competition_name" not in match_columns:
            conn.execute("ALTER TABLE matches ADD COLUMN competition_name TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                market_name TEXT,
                selection_name TEXT,
                odds REAL,
                suspended INTEGER,
                collected_at TEXT NOT NULL
            )
        """)


        # Resultats externes bruts : conserves separement, sans association
        # automatique aux identifiants Betclic.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_results (
                provider TEXT NOT NULL,
                provider_match_id TEXT NOT NULL,
                competition_code TEXT,
                competition_name TEXT,
                utc_date TEXT,
                status TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                home_score INTEGER,
                away_score INTEGER,
                score_json TEXT,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (provider, provider_match_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_provider_results_date
            ON provider_results (utc_date, competition_code)
        """)

        # Index de recherche pour empêcher les doublons exacts à l'écriture
        # et accélérer également la fusion distante.
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odds_history_exact
            ON odds_history (
                match_id, market_name, selection_name,
                odds, suspended, collected_at
            )
        """)


def save_match(match):
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO matches (
                match_id, match_name, match_date, sport, is_live, competition_name
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(match.id),
            match.name,
            str(match.date),
            "football",
            int(bool(match.is_live)),
            (getattr(match, "competition", None)
             or getattr(match, "competition_name", None)
             or None)
        ))


def save_odds(match):
    # Une seule horodatation par instant de collecte : deux entrées
    # identiques du flux au même instant sont donc des doublons exacts.
    collected_at = datetime.now(timezone.utc).isoformat()
    match_id = str(match.id)
    inserted = 0
    seen_in_batch = set()

    with connect() as conn:
        for market in match.markets:
            market_name = market.name
            suspended = int(bool(market.suspended))

            for selection in market.selections:
                selection_name = selection.name
                odds = float(selection.odds)
                key = (
                    match_id, market_name, selection_name,
                    odds, suspended, collected_at
                )

                # Évite les doublons répétés dans la même réponse Betclic.
                if key in seen_in_batch:
                    continue
                seen_in_batch.add(key)

                cursor = conn.execute("""
                    INSERT INTO odds_history (
                        match_id, market_name, selection_name,
                        odds, suspended, collected_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM odds_history
                        WHERE match_id IS ?
                          AND market_name IS ?
                          AND selection_name IS ?
                          AND odds IS ?
                          AND suspended IS ?
                          AND collected_at IS ?
                    )
                """, key + key)
                inserted += cursor.rowcount

    return inserted


if __name__ == "__main__":
    initialize_database()
    print("BASE SQLITE : OK")
