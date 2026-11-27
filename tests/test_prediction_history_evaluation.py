import unittest
from datetime import datetime, timezone

from prediction_history_evaluation import (
    build_report, market_probabilities_if_fresh, score_metrics,
    select_latest_pre_match,
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


if __name__ == "__main__":
    unittest.main()
