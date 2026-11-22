import unittest
from datetime import datetime, timezone, timedelta

from team_form_model_experiment import (team_form, evaluate, fit_final_model, team_key,
                                        latest_complete_1x2_snapshot)
from baseline_market_evaluation import CLASSES


class TeamFormTests(unittest.TestCase):

    def test_curated_aliases_match_but_squad_categories_stay_separate(self):
        self.assertEqual(team_key("Naples"), team_key("SSC Napoli"))
        self.assertNotEqual(team_key("Arsenal"), team_key("Arsenal F."))
        self.assertNotEqual(team_key("Barcelona"), team_key("Barcelona U19"))

    def test_form_uses_only_prior_matches(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [
            {"date": t - timedelta(days=i), "home": team_key("alpha"), "away": team_key("beta"),
             "hg": 2, "ag": 0}
            for i in range(1, 4)
        ]
        history.append({"date": t, "home": team_key("alpha"), "away": team_key("gamma"),
                        "hg": 0, "ag": 5})
        form = team_form(history, "alpha", t)
        self.assertIsNotNone(form)
        self.assertEqual(form[0], 3.0)
        self.assertEqual(form[1], 2.0)

    def test_upcoming_fallback_can_use_one_prior_match_but_marks_it_low_history(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [{"date": t - timedelta(days=1), "home": team_key("alpha"),
                    "away": team_key("beta"), "hg": 1, "ag": 0}]
        self.assertIsNone(team_form(history, "alpha", t))
        fallback = team_form(history, "alpha", t, minimum_matches=1)
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback[0], 3.0)

    def test_requires_three_prior_matches(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [{"date": t - timedelta(days=1), "home": "alpha",
                    "away": "beta", "hg": 1, "ag": 0}]
        self.assertIsNone(team_form(history, "alpha", t))

    def test_latest_complete_1x2_snapshot_uses_latest_complete_pre_match_quotes(self):
        cutoff = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        rows = [
            ("LNZ Cherkasy", 2.10, "2026-11-22T10:00:00+00:00"),
            ("X", 3.20, "2026-11-22T10:00:00+00:00"),
            ("Polissya Zhytomyr", 3.40, "2026-11-22T10:00:00+00:00"),
            # Plus recent, mais incomplet : ne doit pas remplacer le snapshot complet.
            ("LNZ Cherkasy", 2.00, "2026-11-22T11:00:00+00:00"),
            # Après cutoff : exclu.
            ("X", 3.00, "2026-11-22T13:00:00+00:00"),
        ]
        result = latest_complete_1x2_snapshot(
            rows, "LNZ Cherkasy - Polissya Zhytomyr", cutoff
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], datetime(2026, 11, 22, 10, tzinfo=timezone.utc))
        self.assertAlmostEqual(sum(result[2].values()), 1.0, places=8)
        self.assertEqual(set(result[2]), set(CLASSES))


    def test_large_model_market_divergence_is_flagged_for_review(self):
        from team_form_model_experiment import market_comparison_status
        model = {"HOME": 0.15, "DRAW": 0.45, "AWAY": 0.40}
        market = {"HOME": 0.60, "DRAW": 0.20, "AWAY": 0.20}
        self.assertEqual(market_comparison_status(model, market),
                         "REVIEW_LARGE_DIVERGENCE")
        self.assertEqual(market_comparison_status(model, None), "NO_MARKET")

    def test_walk_forward_uses_only_prior_samples(self):
        rows = []
        for i in range(15):
            outcome = CLASSES[i % 3]
            base = float(i % 5)
            rows.append({
                "match_id": str(i),
                "kickoff": datetime(2026, 1, 1, tzinfo=timezone.utc)
                           + timedelta(days=i),
                "outcome": outcome,
                "features": [base, base + (outcome == "HOME"),
                             base - (outcome == "AWAY"), base / 2,
                             base + 0.1, base, base - 0.2, base / 3],
            })
        result = evaluate(rows, [])
        self.assertEqual(result["eligible_n"], 15)
        self.assertEqual(result["cross_validation"]["n"], 5)
        self.assertEqual(result["validation"], "expanding_window_chronological_70_30")
        self.assertEqual(result["status"], "TEAM_FORM_WALK_FORWARD_EXPERIMENT_COMPLETED")

    def test_final_pilot_model_accepts_singleton_class_without_claiming_cv(self):
        rows = []
        outcomes = ["HOME"] + ["DRAW"] * 9 + ["AWAY"] * 5
        for i, outcome in enumerate(outcomes):
            rows.append({
                "match_id": str(i),
                "kickoff": datetime(2026, 1, 1, tzinfo=timezone.utc)
                           + timedelta(days=i),
                "outcome": outcome, "features": [float(i)] * 8,
            })
        model = fit_final_model(rows)
        self.assertIsNotNone(model)
        self.assertEqual(set(model.classes_), set(CLASSES))
        probabilities = model.predict_proba([[1.0] * 8])[0]
        self.assertAlmostEqual(float(sum(probabilities)), 1.0, places=6)

    def test_final_pilot_model_rejects_missing_class(self):
        rows = [{
            "match_id": str(i),
            "kickoff": datetime(2026, 1, 1, tzinfo=timezone.utc)
                       + timedelta(days=i),
            "outcome": "DRAW", "features": [float(i)] * 8,
        } for i in range(10)]
        self.assertIsNone(fit_final_model(rows))

    def test_walk_forward_rejects_class_missing_from_initial_training_window(self):
        rows = []
        outcomes = ["HOME"] * 7 + ["DRAW"] * 7 + ["AWAY"]
        for i, outcome in enumerate(outcomes):
            rows.append({
                "match_id": str(i),
                "kickoff": datetime(2026, 1, 1, tzinfo=timezone.utc)
                           + timedelta(days=i),
                "outcome": outcome, "features": [float(i)] * 8,
            })
        self.assertEqual(evaluate(rows, [])["status"],
                         "INSUFFICIENT_WALK_FORWARD_PREDICTIONS")


if __name__ == "__main__":
    unittest.main()
