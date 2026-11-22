"""Conserve les snapshots de predictions et rattache les scores confirmes."""
import csv
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
CSV_PATH = Path("reports/team_form_predictions.csv")


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prediction_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_run_id TEXT NOT NULL,
            match_id TEXT NOT NULL,
            match_name TEXT NOT NULL,
            generated_at_utc TEXT NOT NULL,
            kickoff_utc TEXT NOT NULL,
            prediction TEXT NOT NULL,
            prob_home REAL NOT NULL,
            prob_draw REAL NOT NULL,
            prob_away REAL NOT NULL,
            model_status TEXT NOT NULL,
            history_quality TEXT,
            market_status TEXT,
            market_snapshot_utc TEXT,
            market_prob_home REAL,
            market_prob_draw REAL,
            market_prob_away REAL,
            action_status TEXT,
            actual_outcome TEXT,
            actual_home_score INTEGER,
            actual_away_score INTEGER,
            evaluated_at_utc TEXT,
            UNIQUE(workflow_run_id, match_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_prediction_history_match
        ON prediction_history(match_id, generated_at_utc)
    """)


def _float_or_none(value):
    return None if value in (None, "") else float(value)


def persist_predictions(db_path=DB_PATH, csv_path=CSV_PATH, run_id=None,
                        generated_at=None):
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        print("Aucun CSV de prediction : historique non modifie.")
        return {"inserted": 0, "evaluated": 0}

    run_id = str(run_id or os.getenv("GITHUB_RUN_ID") or
                 "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        inserted = 0
        for row in rows:
            if not row.get("match_id") or not row.get("kickoff_utc"):
                continue
            values = (
                run_id, str(row["match_id"]), row["match_name"],
                generated_at, row["kickoff_utc"], row["prediction"],
                float(row["prob_home"]), float(row["prob_draw"]),
                float(row["prob_away"]), row["model_status"],
                row.get("history_quality"), row.get("market_status"),
                row.get("market_snapshot_utc") or None,
                _float_or_none(row.get("market_prob_home")),
                _float_or_none(row.get("market_prob_draw")),
                _float_or_none(row.get("market_prob_away")),
                row.get("action_status"),
            )
            cursor = conn.execute("""
                INSERT OR IGNORE INTO prediction_history (
                    workflow_run_id, match_id, match_name, generated_at_utc,
                    kickoff_utc, prediction, prob_home, prob_draw, prob_away,
                    model_status, history_quality, market_status,
                    market_snapshot_utc, market_prob_home, market_prob_draw,
                    market_prob_away, action_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            inserted += cursor.rowcount

        # Ne rapproche que les liens confirmes. Si plusieurs fournisseurs
        # confirment des scores contradictoires, le match reste non evalue.
        confirmed = conn.execute("""
            SELECT betclic_match_id, MIN(home_score), MIN(away_score),
                   COUNT(DISTINCT CAST(home_score AS TEXT) || ':' ||
                                      CAST(away_score AS TEXT))
            FROM validated_match_links
            WHERE validation_status='CONFIRMED'
              AND home_score IS NOT NULL AND away_score IS NOT NULL
            GROUP BY betclic_match_id
        """).fetchall()
        evaluated = 0
        evaluated_at = datetime.now(timezone.utc).isoformat()
        for match_id, home_score, away_score, score_variants in confirmed:
            if score_variants != 1:
                continue
            outcome = ("HOME" if home_score > away_score else
                       "AWAY" if home_score < away_score else "DRAW")
            cursor = conn.execute("""
                UPDATE prediction_history
                SET actual_outcome=?, actual_home_score=?, actual_away_score=?,
                    evaluated_at_utc=?
                WHERE match_id=? AND actual_outcome IS NULL
            """, (outcome, home_score, away_score, evaluated_at, str(match_id)))
            evaluated += cursor.rowcount

        total = conn.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0]
        pending = conn.execute("""
            SELECT COUNT(*) FROM prediction_history WHERE actual_outcome IS NULL
        """).fetchone()[0]

    result = {"inserted": inserted, "evaluated": evaluated,
              "total_snapshots": total, "pending_snapshots": pending}
    print("HISTORIQUE PREDICTIONS :", result)
    return result


if __name__ == "__main__":
    persist_predictions()
