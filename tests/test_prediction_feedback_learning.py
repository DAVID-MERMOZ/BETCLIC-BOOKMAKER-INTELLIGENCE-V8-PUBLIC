import csv
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prediction_history import persist_predictions
from prediction_feedback_learning import (
    CLASSES, apply_calibrator, fit_feedback_calibrator,
    load_completed_forecasts, valid_probs,
)


def make_rows(n=120):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    outcomes = ["HOME"] * 3 + ["DRAW"] * 1 + ["AWAY"] * 1
    for i in range(n):
        rows.append({
            "match_id": str(i),
            "kickoff": start + timedelta(days=i),
            "generated": start + timedelta(days=i, hours=-2),
            "outcome": outcomes[i % len(outcomes)],
            "probs": [0.8, 0.1, 0.1],
        })
    return rows


class PredictionFeedbackLearningTests(unittest.TestCase):
    def test_invalid_probability_map_rejected(self):
        self.assertIsNone(valid_probs({
            "prob_home": 0.7, "prob_draw": 0.2, "prob_away": 0.2
        }))
        self.assertIsNone(valid_probs({
            "prob_home": float("nan"), "prob_draw": 0.2, "prob_away": 0.8
        }))

    def test_small_history_keeps_original_probabilities(self):
        fitted = fit_feedback_calibrator(make_rows(30))
        self.assertIsNone(fitted["model"])
        self.assertEqual(fitted["status"]["status"],
                         "FEEDBACK_DISABLED_INSUFFICIENT_HISTORY")
        raw = [0.6, 0.25, 0.15]
        self.assertEqual(apply_calibrator(raw, fitted), raw)

    def test_disabled_calibrator_preserves_probability_mapping(self):
        raw = {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2}
        fitted = fit_feedback_calibrator(make_rows(30))
        self.assertEqual(apply_calibrator(raw, fitted), raw)

    def test_feedback_fit_waits_until_next_ten_match_milestone(self):
        raw = [0.6, 0.25, 0.15]
        fitted = fit_feedback_calibrator(make_rows(85))
        self.assertIsNone(fitted["model"])
        self.assertEqual(fitted["status"]["status"],
                         "FEEDBACK_AUDIT_WAITING_FOR_NEXT_10")
        self.assertEqual(fitted["status"]["next_audit_at"], 90)
        self.assertEqual(apply_calibrator(raw, fitted), raw)

    def test_feedback_fit_runs_on_ten_match_milestone(self):
        fitted = fit_feedback_calibrator(make_rows(90))
        self.assertEqual(fitted["status"]["history_n"], 90)
        self.assertNotEqual(fitted["status"]["status"],
                            "FEEDBACK_AUDIT_WAITING_FOR_NEXT_10")

    def test_active_calibrator_preserves_probability_mapping(self):
        raw = {"HOME": 0.8, "DRAW": 0.1, "AWAY": 0.1}
        fitted = fit_feedback_calibrator(make_rows())
        calibrated = apply_calibrator(raw, fitted)
        self.assertEqual(set(calibrated), set(CLASSES))
        self.assertAlmostEqual(sum(calibrated.values()), 1.0, places=9)

    def test_calibrator_activates_only_after_holdout_gain(self):
        fitted = fit_feedback_calibrator(make_rows())
        self.assertIsNotNone(fitted["model"])
        self.assertEqual(fitted["status"]["status"],
                         "FEEDBACK_ACTIVE_VALIDATED_ON_CHRONOLOGICAL_HOLDOUT")
        self.assertGreaterEqual(fitted["status"]["brier_gain"], 0.002)
        self.assertGreaterEqual(fitted["status"]["logloss_gain"], 0.005)
        calibrated = apply_calibrator([0.8, 0.1, 0.1], fitted)
        self.assertAlmostEqual(sum(calibrated), 1.0, places=9)
        self.assertNotEqual(calibrated, [0.8, 0.1, 0.1])

    def test_loads_latest_unique_confirmed_forecast_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.db"
            with sqlite3.connect(path) as conn:
                conn.execute("""
                    CREATE TABLE prediction_history (
                        match_id TEXT, generated_at_utc TEXT, kickoff_utc TEXT,
                        prob_home REAL, prob_draw REAL, prob_away REAL,
                        actual_outcome TEXT, actual_home_score INTEGER,
                        actual_away_score INTEGER
                    )
                """)
                kickoff = "2025-01-01T20:00:00+00:00"
                conn.executemany("""
                    INSERT INTO prediction_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    ("m1", "2025-01-01T17:00:00+00:00", kickoff,
                     .5, .3, .2, "HOME", 2, 1),
                    ("m1", "2025-01-01T18:00:00+00:00", kickoff,
                     .6, .25, .15, "HOME", 2, 1),
                    # Post-kickoff snapshot must not be used.
                    ("m1", "2025-01-01T21:00:00+00:00", kickoff,
                     .1, .2, .7, "HOME", 2, 1),
                    # Conflicting score/result for another match excluded.
                    ("m2", "2025-01-01T17:00:00+00:00", kickoff,
                     .5, .3, .2, "HOME", 1, 0),
                    ("m2", "2025-01-01T18:00:00+00:00", kickoff,
                     .5, .3, .2, "AWAY", 0, 1),
                ])
            rows = load_completed_forecasts(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["match_id"], "m1")
            self.assertEqual(rows[0]["probs"], [.6, .25, .15])
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM prediction_history").fetchone()[0], 5)


    def test_archived_forecast_becomes_feedback_eligible_after_confirmed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "history.db"
            csv_path = root / "predictions.csv"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE validated_match_links (
                        betclic_match_id TEXT, home_score INTEGER,
                        away_score INTEGER, validation_status TEXT
                    )
                """)
            fields = [
                "match_id", "match_name", "kickoff_utc", "prediction",
                "prob_home", "prob_draw", "prob_away", "model_status",
            ]
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "match_id": "betclic-42", "match_name": "Alpha - Beta",
                    "kickoff_utc": "2026-11-27T18:00:00+00:00",
                    "prediction": "HOME", "prob_home": "0.55",
                    "prob_draw": "0.25", "prob_away": "0.20",
                    "model_status": "EXPERIMENTAL_NOT_VALIDATED",
                })

            first = persist_predictions(
                db_path, csv_path, "run-before-kickoff",
                "2026-11-27T16:00:00+00:00",
            )
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(load_completed_forecasts(db_path), [])

            # A confirmed Betclic-ID link arrives after the match has finished.
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO validated_match_links
                    VALUES ('betclic-42', 2, 1, 'CONFIRMED')
                """)

            second = persist_predictions(
                db_path, csv_path, "run-after-kickoff",
                "2026-11-27T19:00:00+00:00",
            )
            self.assertEqual(second["inserted"], 1)
            self.assertEqual(second["evaluated"], 2)

            eligible = load_completed_forecasts(db_path)
            self.assertEqual(len(eligible), 1)
            self.assertEqual(eligible[0]["match_id"], "betclic-42")
            self.assertEqual(eligible[0]["outcome"], "HOME")
            self.assertEqual(eligible[0]["probs"], [0.55, 0.25, 0.20])

if __name__ == "__main__":
    unittest.main()
