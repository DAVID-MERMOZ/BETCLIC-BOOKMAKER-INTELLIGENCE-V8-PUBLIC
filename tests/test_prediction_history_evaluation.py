import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from prediction_history_evaluation import (
    build_report, market_probabilities_if_fresh, score_metrics,
    select_latest_pre_match, load_evaluated_rows, write_report,
)


def row(match_id="m1", generated="2026-11-01T10:00:00Z",
        kickoff="2026-11-01T12:00:00Z", outcome="HOME", score=(2, 0),
        model=(0.6, 0.2, 0.2), market=(0.5, 0.3, 0.2),
        market_stamp="2026-11-01T09:30:00Z", status="EXPERIMENTAL_NOT_VALIDATED",
        row_id=1):
    return {
        "id": row_id, "match_id": match_id, "generated_at_utc": generated,
        "kickoff_utc": kickoff, "actual_outcome": outcome,
        "actual_home_score": score[0], "actual_away_score": score[1],
        "prob_home": model[0], "prob_draw": model[1], "prob_away": model[2],
        "model_status": status, "market_status": "AVAILABLE",
        "market_snapshot_utc": market_stamp,
        "market_prob_home": market[0], "market_prob_draw": market[1],
        "market_prob_away": market[2],
    }


class PredictionHistoryEvaluationTests(unittest.TestCase):
    def test_selects_one_latest_pre_kickoff_forecast_per_match(self):
        older = row(row_id=1)
        newer = row(generated="2026-11-01T11:00:00Z", row_id=2,
                    model=(0.7, 0.15, 0.15))
        post_kickoff = row(generated="2026-11-01T12:01:00Z", row_id=3,
                           model=(0.1, 0.1, 0.8))
        selected, diagnostics = select_latest_pre_match(
            [older, newer, post_kickoff])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["id"], 2)
        self.assertEqual(diagnostics["conflicting_matches"], 0)

    def test_conflicting_confirmed_scores_exclude_match(self):
        a = row()
        b = row(score=(0, 1), outcome="AWAY", row_id=2)
        selected, diagnostics = select_latest_pre_match([a, b])
        self.assertEqual(selected, [])
        self.assertEqual(diagnostics["conflicting_matches"], 1)

    def test_market_snapshot_must_be_fresh_and_pre_prediction(self):
        fresh = row()
        self.assertIsNotNone(market_probabilities_if_fresh(fresh))
        stale = row(market_stamp="2026-11-01T07:00:00Z")
        self.assertIsNone(market_probabilities_if_fresh(stale))
        future = row(market_stamp="2026-11-01T10:30:00Z")
        self.assertIsNone(market_probabilities_if_fresh(future))

    def test_model_and_market_use_identical_matched_sample(self):
        fresh = row()
        stale = row(match_id="m2", market_stamp="2026-11-01T07:00:00Z")
        report, summary = build_report([fresh, stale])
        model = next(r for r in report
                     if r["evaluation_scope"] == "MODEL_MATCHED_FRESH_MARKET")
        market = next(r for r in report
                      if r["evaluation_scope"] == "MARKET_MATCHED_FRESH")
        self.assertEqual(model["n_matches"], 1)
        self.assertEqual(market["n_matches"], 1)
        self.assertEqual(model["n_matches"], market["n_matches"])
        self.assertEqual(summary["selected_unique_matches"], 2)

    def test_metrics_are_calculated_on_unique_rows(self):
        metrics = score_metrics([row()])
        self.assertEqual(metrics["n"], 1)
        self.assertAlmostEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["log_loss"], 0.510826)

    def test_post_kickoff_forecasts_are_never_selected(self):
        selected, _ = select_latest_pre_match([
            row(generated="2026-11-01T12:00:00Z")
        ])
        self.assertEqual(selected, [])


    def test_read_only_database_load_and_aggregate_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "history.db"
            report_path = root / "evaluation.csv"
            with sqlite3.connect(db) as conn:
                conn.execute("""
                    CREATE TABLE prediction_history (
                        id INTEGER, workflow_run_id TEXT, match_id TEXT,
                        match_name TEXT, generated_at_utc TEXT, kickoff_utc TEXT,
                        prediction TEXT, prob_home REAL, prob_draw REAL, prob_away REAL,
                        model_status TEXT, market_status TEXT, market_snapshot_utc TEXT,
                        market_prob_home REAL, market_prob_draw REAL, market_prob_away REAL,
                        actual_outcome TEXT, actual_home_score INTEGER,
                        actual_away_score INTEGER
                    )
                """)
                conn.execute("""
                    INSERT INTO prediction_history VALUES
                    (1,'r1','m1','A - B','2026-11-01T10:00:00Z',
                     '2026-11-01T12:00:00Z','HOME',.6,.2,.2,
                     'EXPERIMENTAL_NOT_VALIDATED','AVAILABLE',
                     '2026-11-01T09:30:00Z',.5,.3,.2,'HOME',2,0)
                """)
                conn.execute("""
                    INSERT INTO prediction_history VALUES
                    (2,'r2','m2','C - D','2026-11-01T10:00:00Z',
                     '2026-11-01T12:00:00Z','AWAY',.2,.2,.6,
                     'EXPERIMENTAL_NOT_VALIDATED','AVAILABLE',
                     '2026-11-01T09:30:00Z',.3,.3,.4,NULL,NULL,NULL)
                """)
                conn.commit()

            rows, pending = load_evaluated_rows(db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(pending, 1)
            summary = write_report(rows, report_path, pending_snapshots=pending)
            self.assertEqual(summary["selected_unique_matches"], 1)
            with report_path.open(newline="", encoding="utf-8-sig") as stream:
                output = list(csv.DictReader(stream))
            self.assertEqual(len(output), 3)
            self.assertNotIn("match_id", output[0])
            self.assertEqual(output[0]["n_matches"], "1")


if __name__ == "__main__":
    unittest.main()
