import unittest
from datetime import datetime, timezone, timedelta

from team_form_model_experiment import (team_form, evaluate, fit_final_model, team_key,
                                        latest_complete_1x2_snapshot, prediction_action_status,
                                        team_form_features, _history_from_provider_rows)
from baseline_market_evaluation import CLASSES
from daily_fixture_sample import market_anchored_probabilities


class TeamFormTests(unittest.TestCase):

    def test_curated_aliases_match_but_squad_categories_stay_separate(self):
        self.assertEqual(team_key("Naples"), team_key("SSC Napoli"))
        self.assertNotEqual(team_key("Arsenal"), team_key("Arsenal F."))
        self.assertNotEqual(team_key("Barcelona"), team_key("Barcelona U19"))

    def test_history_deduplicates_same_match_across_providers(self):
        rows = [
            ("2026-11-01T15:00:00Z", "Alpha FC", "Beta FC", 2, 1),
            # Même rencontre fournie une seconde fois avec les équipes inversées.
            ("2026-11-01T15:00:30Z", "Beta FC", "Alpha FC", 1, 2),
        ]
        history = _history_from_provider_rows(rows)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["home"], team_key("Alpha FC"))
        self.assertEqual(history[0]["away"], team_key("Beta FC"))
        self.assertEqual((history[0]["hg"], history[0]["ag"]), (2, 1))

    def test_history_excludes_incompatible_squad_categories(self):
        rows = [
            ("2026-11-01T15:00:00Z", "Sturm Graz", "Heart of Midlothian Women", 2, 0),
            ("2026-11-02T15:00:00Z", "Alpha FC", "Beta FC", 1, 0),
        ]
        history = _history_from_provider_rows(rows)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["home"], team_key("Alpha FC"))

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

    def test_venue_features_capture_home_and_away_strength_separately(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [
            {"date": t - timedelta(days=4), "home": team_key("alpha"),
             "away": team_key("beta"), "hg": 2, "ag": 0},
            {"date": t - timedelta(days=3), "home": team_key("gamma"),
             "away": team_key("alpha"), "hg": 3, "ag": 0},
            {"date": t - timedelta(days=2), "home": team_key("alpha"),
             "away": team_key("delta"), "hg": 1, "ag": 0},
            {"date": t - timedelta(days=1), "home": team_key("epsilon"),
             "away": team_key("alpha"), "hg": 2, "ag": 0},
        ]
        features = team_form_features(history, "alpha", t, "HOME")
        self.assertEqual(len(features), 8)
        self.assertAlmostEqual(features[0], 1.5)  # points par match, forme globale
        self.assertAlmostEqual(features[4], 3.0)  # points par match a domicile
        self.assertAlmostEqual(features[5], 1.5)  # buts marques a domicile
        self.assertAlmostEqual(features[6], 0.0)  # buts encaisses a domicile

    def test_venue_features_fall_back_to_overall_with_less_than_two_games(self):
        t = datetime(2026, 1, 10, tzinfo=timezone.utc)
        history = [
            {"date": t - timedelta(days=3), "home": team_key("alpha"),
             "away": team_key("beta"), "hg": 2, "ag": 0},
            {"date": t - timedelta(days=2), "home": team_key("gamma"),
             "away": team_key("alpha"), "hg": 1, "ag": 0},
            {"date": t - timedelta(days=1), "home": team_key("delta"),
             "away": team_key("alpha"), "hg": 2, "ag": 1},
        ]
        features = team_form_features(history, "alpha", t, "HOME")
        self.assertEqual(features[:4], features[4:])

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


    def test_prediction_action_blocks_unvalidated_or_unsafe_signals(self):
        self.assertEqual(prediction_action_status("LOW_HISTORY_EXPERIMENTAL",
            "NO_LARGE_DIVERGENCE", "EXPERIMENTAL_NOT_VALIDATED"),
            "NO_BET_LOW_HISTORY")
        self.assertEqual(prediction_action_status("STANDARD_3_PLUS",
            "REVIEW_LARGE_DIVERGENCE", "EXPERIMENTAL_NOT_VALIDATED"),
            "NO_BET_LARGE_DIVERGENCE")
        self.assertEqual(prediction_action_status("STANDARD_3_PLUS",
            "NO_LARGE_DIVERGENCE", "EXPERIMENTAL_NOT_VALIDATED"),
            "NO_BET_MODEL_UNVALIDATED")
        self.assertEqual(prediction_action_status("STANDARD_3_PLUS",
            "NO_LARGE_DIVERGENCE", "VALIDATED"), "ELIGIBLE_FOR_REVIEW_ONLY")

    def test_daily_probability_export_validates_simplex(self):
        from daily_fixture_sample import probabilities

        class FakeModel:
            classes_ = ["HOME", "DRAW", "AWAY"]
            def predict_proba(self, vectors):
                return [[0.2, 0.3, 0.5]]

        result = probabilities(FakeModel(), [0.0] * 16)
        self.assertAlmostEqual(sum(result.values()), 1.0)
        self.assertEqual(result["AWAY"], 0.5)

        class InvalidModel:
            classes_ = ["HOME", "DRAW", "AWAY"]
            def predict_proba(self, vectors):
                return [[0.2, 0.3, 0.6]]

        with self.assertRaises(ValueError):
            probabilities(InvalidModel(), [0.0] * 16)

    def test_model_probability_maps_are_normalized_and_fail_closed(self):
        from baseline_market_evaluation import validate_probability_map
        valid = validate_probability_map({"HOME": .2, "DRAW": .3, "AWAY": .5})
        self.assertAlmostEqual(sum(valid.values()), 1.0)
        with self.assertRaises(ValueError):
            validate_probability_map({"HOME": .2, "DRAW": .3, "AWAY": .4})
        with self.assertRaises(ValueError):
            validate_probability_map({"HOME": 1.1, "DRAW": -.1, "AWAY": 0.0})

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


    def test_simultaneous_fixtures_never_train_on_each_others_results(self):
        rows = []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(15):
            outcome = CLASSES[i % 3]
            # Les deux derniers matchs partagent exactement le même coup d'envoi.
            kickoff = start + timedelta(days=10 if i in (10, 11) else i)
            rows.append({
                "match_id": str(i), "kickoff": kickoff,
                "outcome": outcome, "features": [float(i % 4)] * 8,
            })
        result = evaluate(rows, [])
        simultaneous = next(
            group for group in result["walk_forward_groups"]
            if set(group["match_ids"]) == {"10", "11"}
        )
        self.assertEqual(simultaneous["test_group_n"], 2)
        self.assertEqual(simultaneous["train_n"], 10)
        self.assertEqual(result["walk_forward_predictions_n"], 5)

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
