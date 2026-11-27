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



def merge_prediction_history(conn):
    """Fusionne l'archive des predictions sans perdre les evaluations existantes."""
    if not table_exists(conn, "remote", "prediction_history"):
        print("prediction_history : absente de la sauvegarde distante (aucune fusion).")
        return

    remote_sql = conn.execute(
        "SELECT sql FROM remote.sqlite_master "
        "WHERE type='table' AND name='prediction_history'"
    ).fetchone()[0]
    if not table_exists(conn, "main", "prediction_history"):
        # Reutilise le schema source, y compris sa cle unique (run, match).
        create_sql = remote_sql.replace(
            "CREATE TABLE prediction_history",
            "CREATE TABLE main.prediction_history",
            1,
        )
        conn.execute(create_sql)
    else:
        # Migration additive si la sauvegarde contient des colonnes plus recentes.
        local_info = conn.execute("PRAGMA main.table_info(prediction_history)").fetchall()
        local_columns = {row[1] for row in local_info}
        remote_info = conn.execute("PRAGMA remote.table_info(prediction_history)").fetchall()
        for row in remote_info:
            name, column_type = row[1], row[2] or "TEXT"
            if name not in local_columns:
                safe_name = '"' + name.replace('"', '""') + '"'
                conn.execute(
                    f"ALTER TABLE main.prediction_history ADD COLUMN {safe_name} {column_type}"
                )
                local_columns.add(name)

    local_columns = {
        row[1] for row in conn.execute("PRAGMA main.table_info(prediction_history)")
    }
    remote_columns = {
        row[1] for row in conn.execute("PRAGMA remote.table_info(prediction_history)")
    }
    shared = sorted(local_columns & remote_columns)
    if not {"workflow_run_id", "match_id"}.issubset(shared):
        raise RuntimeError("Schema prediction_history invalide : cles run/match absentes.")

    columns_sql = ", ".join('"' + name.replace('"', '""') + '"' for name in shared)
    conn.execute(
        f"INSERT OR IGNORE INTO main.prediction_history ({columns_sql}) "
        f"SELECT {columns_sql} FROM remote.prediction_history"
    )

    # Si une copie locale est encore pending mais que la sauvegarde distante
    # contient son resultat confirme, completer uniquement les champs de resultat.
    result_fields = [
        name for name in (
            "actual_outcome", "actual_home_score", "actual_away_score",
            "actual_over_1_5", "actual_over_2_5", "actual_over_3_5",
            "actual_btts_yes", "evaluated_at_utc",
        )
        if name in local_columns and name in remote_columns
    ]
    if "actual_outcome" in result_fields:
        assignments = ", ".join(
            '"' + name + '" = (SELECT r."' + name + '" '
            'FROM remote.prediction_history r '
            'WHERE r.workflow_run_id = main.prediction_history.workflow_run_id '
            'AND r.match_id = main.prediction_history.match_id)'
            for name in result_fields
        )
        conn.execute(
            "UPDATE main.prediction_history SET " + assignments + " "
            "WHERE actual_outcome IS NULL AND EXISTS ("
            "SELECT 1 FROM remote.prediction_history r "
            "WHERE r.workflow_run_id = main.prediction_history.workflow_run_id "
            "AND r.match_id = main.prediction_history.match_id "
            "AND r.actual_outcome IS NOT NULL)"
        )

    count = conn.execute(
        "SELECT COUNT(*) FROM main.prediction_history"
    ).fetchone()[0]
    print("prediction_history :", count)


def merge_validated_match_links(conn):
    """Preserve les liaisons de resultats humainement confirmees."""
    if not table_exists(conn, "remote", "validated_match_links"):
        print("validated_match_links : absente de la sauvegarde distante (aucune fusion).")
        return

    conn.execute("""
        CREATE TABLE IF NOT EXISTS main.validated_match_links (
            candidate_id TEXT PRIMARY KEY,
            betclic_match_id TEXT NOT NULL,
            betclic_match TEXT,
            betclic_date TEXT,
            provider TEXT NOT NULL,
            provider_match_id TEXT NOT NULL,
            provider_competition TEXT,
            provider_match TEXT,
            provider_date TEXT,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            validation_status TEXT NOT NULL CHECK (validation_status = 'CONFIRMED'),
            validated_by TEXT,
            validated_at TEXT,
            validation_notes TEXT,
            linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (provider, provider_match_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS main.idx_validated_links_betclic
        ON validated_match_links (betclic_match_id)
    """)

    columns = [
        "candidate_id", "betclic_match_id", "betclic_match", "betclic_date",
        "provider", "provider_match_id", "provider_competition",
        "provider_match", "provider_date", "home_score", "away_score",
        "validation_status", "validated_by", "validated_at",
        "validation_notes", "linked_at",
    ]
    remote_columns = {
        row[1] for row in conn.execute("PRAGMA remote.table_info(validated_match_links)")
    }
    if not set(columns).issubset(remote_columns):
        raise RuntimeError("Schema distant validated_match_links incomplet.")
    selected = ", ".join('"' + name + '"' for name in columns)
    rows = conn.execute(
        f"SELECT {selected} FROM remote.validated_match_links"
    ).fetchall()

    identity_columns = columns[:-1]
    for row in rows:
        record = dict(zip(columns, row))
        by_candidate = conn.execute(
            "SELECT " + selected + " FROM main.validated_match_links WHERE candidate_id=?",
            (record["candidate_id"],)
        ).fetchone()
        if by_candidate is not None and tuple(by_candidate) != tuple(row):
            raise RuntimeError(
                "Lien valide contradictoire pour candidate_id "
                + str(record["candidate_id"])
            )
        by_provider = conn.execute(
            "SELECT candidate_id, betclic_match_id, home_score, away_score "
            "FROM main.validated_match_links WHERE provider=? AND provider_match_id=?",
            (record["provider"], record["provider_match_id"])
        ).fetchone()
        expected_provider = (
            record["candidate_id"], record["betclic_match_id"],
            record["home_score"], record["away_score"]
        )
        if by_provider is not None and tuple(by_provider) != expected_provider:
            raise RuntimeError(
                "Lien fournisseur contradictoire pour "
                + str(record["provider"]) + ":" + str(record["provider_match_id"])
            )
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT OR IGNORE INTO main.validated_match_links ({selected}) "
            f"VALUES ({placeholders})",
            tuple(row)
        )

    count = conn.execute(
        "SELECT COUNT(*) FROM main.validated_match_links"
    ).fetchone()[0]
    print("validated_match_links :", count)


def merge_databases(remote_path):
    remote_path = Path(remote_path)

    if not LOCAL_DB.exists():
        raise FileNotFoundError("Base locale introuvable.")

    if not remote_path.exists():
        raise FileNotFoundError("Base distante introuvable.")

    conn = sqlite3.connect(LOCAL_DB)

    try:
        conn.execute("ATTACH DATABASE ? AS remote", (str(remote_path),))

        merge_prediction_history(conn)
        merge_validated_match_links(conn)

        if table_exists(conn, "remote", "matches"):
            # Support both legacy (5-column) and current (6-column) schemas.
            # Explicit column mapping prevents INSERT failures when the local
            # database has gained competition_name through an idempotent migration.
            local_columns = {
                row[1] for row in conn.execute("PRAGMA main.table_info(matches)")
            }
            remote_columns = {
                row[1] for row in conn.execute("PRAGMA remote.table_info(matches)")
            }
            shared_columns = [
                name for name in ("match_id", "match_name", "match_date", "sport", "is_live", "competition_name")
                if name in local_columns and name in remote_columns
            ]
            if "match_id" not in shared_columns:
                raise RuntimeError("Schema matches distant invalide : match_id absent.")
            column_sql = ", ".join(shared_columns)
            conn.execute(
                f"INSERT OR IGNORE INTO main.matches ({column_sql}) "
                f"SELECT {column_sql} FROM remote.matches"
            )

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

        for table in ("matches", "odds_history", "odds_alerts", "provider_results", "provider_collection_days", "prediction_history", "validated_match_links"):
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
