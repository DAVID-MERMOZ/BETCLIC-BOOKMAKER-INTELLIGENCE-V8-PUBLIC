import sqlite3
import sys
from pathlib import Path

LOCAL_DB = Path("data/betclic_history.db")

# Index composite utilisé par le contrôle anti-doublon.
# Il permet une recherche indexée au lieu de rescanner toute l'historique
# pour chaque ligne distante.
ODDS_EXACT_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_odds_history_exact
    ON odds_history (
        match_id, market_name, selection_name,
        odds, suspended, collected_at
    )
"""


def table_exists(conn, database, table):
    result = conn.execute(
        f"SELECT name FROM {database}.sqlite_master "
        "WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return result is not None


def merge_databases(remote_path):
    remote_path = Path(remote_path)

    if not LOCAL_DB.exists():
        raise FileNotFoundError("Base locale introuvable.")

    if not remote_path.exists():
        raise FileNotFoundError("Base distante introuvable.")

    conn = sqlite3.connect(LOCAL_DB)

    try:
        conn.execute("ATTACH DATABASE ? AS remote", (str(remote_path),))

        if table_exists(conn, "remote", "matches"):
            conn.execute("""
                INSERT OR IGNORE INTO main.matches
                SELECT * FROM remote.matches
            """)

        if table_exists(conn, "remote", "odds_history"):
            # Index persistant : créé une seule fois, puis réutilisé à
            # chaque synchronisation. Les observations historiques restent intactes.
            conn.execute(ODDS_EXACT_INDEX)

            # DISTINCT retire les copies internes à la source distante.
            # IS assure une comparaison NULL-safe sur les six champs.
            # L'index composite rend chaque recherche locale efficace.
            conn.execute("""
                INSERT INTO main.odds_history (
                    match_id, market_name, selection_name,
                    odds, suspended, collected_at
                )
                SELECT DISTINCT
                    r.match_id, r.market_name, r.selection_name,
                    r.odds, r.suspended, r.collected_at
                FROM remote.odds_history r
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM main.odds_history l
                    WHERE l.match_id IS r.match_id
                      AND l.market_name IS r.market_name
                      AND l.selection_name IS r.selection_name
                      AND l.odds IS r.odds
                      AND l.suspended IS r.suspended
                      AND l.collected_at IS r.collected_at
                )
            """)

        if (
            table_exists(conn, "main", "odds_alerts")
            and table_exists(conn, "remote", "odds_alerts")
        ):
            conn.execute("""
                INSERT OR IGNORE INTO main.odds_alerts (
                    match_id, match_name, market_name, selection_name,
                    alert_type, direction, variation, observed_at
                )
                SELECT
                    match_id, match_name, market_name, selection_name,
                    alert_type, direction, variation, observed_at
                FROM remote.odds_alerts
            """)

        # Preserve les resultats fournisseur lors de la fusion distante.
        # Ils restent independants des identifiants Betclic.
        if table_exists(conn, "remote", "provider_results"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS main.provider_results (
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
                INSERT INTO main.provider_results (
                    provider, provider_match_id, competition_code,
                    competition_name, utc_date, status, home_team, away_team,
                    home_score, away_score, score_json, fetched_at
                )
                SELECT provider, provider_match_id, competition_code,
                       competition_name, utc_date, status, home_team, away_team,
                       home_score, away_score, score_json, fetched_at
                FROM remote.provider_results
                WHERE 1
                ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                    competition_code=excluded.competition_code,
                    competition_name=excluded.competition_name,
                    utc_date=excluded.utc_date,
                    status=excluded.status,
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    home_score=excluded.home_score,
                    away_score=excluded.away_score,
                    score_json=excluded.score_json,
                    fetched_at=excluded.fetched_at
            """)

        # Persister les checkpoints SportScore pour que les runs horaires
        # ne retelechargent pas les 365 jours deja collectes.
        if table_exists(conn, "remote", "provider_collection_days"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS main.provider_collection_days (
                    provider TEXT NOT NULL,
                    utc_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status = 'COMPLETE'),
                    fixtures_seen INTEGER NOT NULL DEFAULT 0,
                    results_saved INTEGER NOT NULL DEFAULT 0,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (provider, utc_date)
                )
            """)
            conn.execute("""
                INSERT INTO main.provider_collection_days (
                    provider, utc_date, status, fixtures_seen, results_saved, fetched_at
                )
                SELECT provider, utc_date, status, fixtures_seen, results_saved, fetched_at
                FROM remote.provider_collection_days
                WHERE 1
                ON CONFLICT(provider, utc_date) DO UPDATE SET
                    status=excluded.status,
                    fixtures_seen=excluded.fixtures_seen,
                    results_saved=excluded.results_saved,
                    fetched_at=excluded.fetched_at
            """)

        conn.commit()
        print("FUSION SQLITE : SUCCES")

        for table in ("matches", "odds_history", "odds_alerts", "provider_results", "provider_collection_days"):
            if table_exists(conn, "main", table):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM main.{table}"
                ).fetchone()[0]
                print(f"{table} :", count)

    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Utilisation : python merge_database.py fichier_distant.db"
        )
    merge_databases(sys.argv[1])
