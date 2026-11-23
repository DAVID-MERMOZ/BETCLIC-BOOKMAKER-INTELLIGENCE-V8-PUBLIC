"""Predictions quotidiennes dynamiques depuis les fixtures Betclic en base."""
import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression

from baseline_market_evaluation import CLASSES, validate_probability_map
from team_form_model_experiment import (
    DB_PATH, MODEL_PATH, build_team_dataset, build_upcoming_dataset,
    market_comparison_status, prediction_action_status,
    load_history_from_db, split_team_keys,
)
from goal_markets import predict_goal_markets

OUT = Path("reports/todays_sample_predictions.csv")


def probabilities(model, vector):
    result = {label: 0.0 for label in CLASSES}
    for label, value in zip(model.classes_, model.predict_proba([vector])[0]):
        result[label] = float(value)
    return validate_probability_map(result)


def main():
    if not MODEL_PATH.exists():
        raise SystemExit("MODELE ABSENT : executer team_form_model_experiment.py avant ce script.")

    payload = joblib.load(MODEL_PATH)
    model = payload["model"]
    upcoming, rejected = build_upcoming_dataset(DB_PATH)
    samples, _ = build_team_dataset(DB_PATH)
    counts = Counter(row["outcome"] for row in samples)

    balanced_model = None
    if len(samples) >= 6 and all(counts[label] >= 1 for label in CLASSES):
        balanced_model = LogisticRegression(
            C=0.1, solver="lbfgs", max_iter=2000,
            random_state=42, class_weight="balanced",
        )
        balanced_model.fit(
            [row["features"] for row in samples],
            [row["outcome"] for row in samples],
        )

    now = datetime.now(timezone.utc)
    rows = []
    for fixture in upcoming:
        vector = fixture["features"]
        probs = probabilities(model, vector)
        balanced = probabilities(balanced_model, vector) if balanced_model else None
        history = load_history_from_db(DB_PATH)
        home_key, away_key = split_team_keys(fixture["match_name"])
        goals = predict_goal_markets(history, home_key, away_key, fixture["kickoff"])
        row = {
            "competition_name": fixture.get("competition_name") or "Championnat non renseigné",
            "generated_at_utc": now.isoformat(),
            "match_id": fixture["match_id"],
            "match_name": fixture["match_name"],
            "kickoff_utc": fixture["kickoff"].isoformat(),
            "prediction_1x2": max(probs, key=probs.get),
            "prob_home": round(probs["HOME"], 6),
            "prob_draw": round(probs["DRAW"], 6),
            "prob_away": round(probs["AWAY"], 6),
            "balanced_prediction": max(balanced, key=balanced.get) if balanced else "",
            "balanced_prob_home": round(balanced["HOME"], 6) if balanced else "",
            "balanced_prob_draw": round(balanced["DRAW"], 6) if balanced else "",
            "balanced_prob_away": round(balanced["AWAY"], 6) if balanced else "",
            "training_n": payload.get("training_n", len(samples)),
            "training_classes": str(payload.get("class_counts", dict(counts))),
            "model_status": payload.get("status", "EXPERIMENTAL"),
            "validation_status": payload.get("validation_status", "NOT_EVALUATED"),
            "calibration_status": payload.get("calibration_status", "NOT_CALIBRATED"),
            "probability_integrity_status": "VALID_SIMPLEX",
            "balanced_probability_status": "CLASS_WEIGHTED_EXPERIMENTAL_NOT_CALIBRATED" if balanced else "NOT_AVAILABLE",
            "history_quality": fixture["history_quality"],
            "form_matches_home": fixture["form_matches_home"],
            "form_matches_away": fixture["form_matches_away"],
            "market_status": "AVAILABLE" if fixture.get("market_snapshot") else "NO_COMPLETE_PREMATCH_1X2",
            "max_abs_edge_pp": round(max(abs(probs[label] - fixture["market_snapshot"][2][label])
                for label in CLASSES) * 100, 2) if fixture.get("market_snapshot") else "",
            "comparison_status": market_comparison_status(
                probs, fixture["market_snapshot"][2] if fixture.get("market_snapshot") else None),
            "action_status": prediction_action_status(
                fixture["history_quality"],
                market_comparison_status(probs,
                    fixture["market_snapshot"][2] if fixture.get("market_snapshot") else None),
                payload.get("status", "EXPERIMENTAL")),
            "goal_model_status": goals.get("status", "NOT_AVAILABLE"),
            "lambda_home": goals.get("lambda_home", ""),
            "lambda_away": goals.get("lambda_away", ""),
            "goal_history_home_n": goals.get("home_history_n", ""),
            "goal_history_away_n": goals.get("away_history_n", ""),
            "over_1_5": goals.get("OVER_1_5", ""),
            "under_1_5": goals.get("UNDER_1_5", ""),
            "over_2_5": goals.get("OVER_2_5", ""),
            "under_2_5": goals.get("UNDER_2_5", ""),
            "over_3_5": goals.get("OVER_3_5", ""),
            "under_3_5": goals.get("UNDER_3_5", ""),
            "btts_yes": goals.get("BTTS_YES", ""),
            "btts_no": goals.get("BTTS_NO", ""),
            "market_movement_status": fixture.get("market_movement", {}).get("status", "NO_DATA"),
            "market_movement_pattern": fixture.get("market_movement", {}).get("pattern", ""),
            "web_research_status": fixture.get("web_research", {}).get("status", "NOT_TRIGGERED"),
            "feature_definition": payload.get("feature_definition", ""),
            "note": "Predictions exploratoires; probabilites normalisees, non calibrees; la variante class_weighted est diagnostique uniquement.",
        }
        rows.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else [
        "generated_at_utc", "match_id", "competition_name", "match_name", "kickoff_utc",
        "prediction_1x2", "prob_home", "prob_draw", "prob_away",
        "balanced_prediction", "balanced_prob_home", "balanced_prob_draw",
        "balanced_prob_away", "training_n", "training_classes",
        "model_status", "validation_status", "calibration_status",
        "probability_integrity_status", "balanced_probability_status",
        "history_quality", "form_matches_home",
        "form_matches_away", "market_status", "max_abs_edge_pp", "comparison_status",
        "action_status", "goal_model_status", "lambda_home", "lambda_away",
        "goal_history_home_n", "goal_history_away_n", "over_1_5", "under_1_5",
        "over_2_5", "under_2_5", "over_3_5", "under_3_5", "btts_yes", "btts_no",
        "market_movement_status", "market_movement_pattern",
        "web_research_status", "feature_definition", "note",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("HEURE DE GENERATION UTC :", now.isoformat())
    print("MATCHS FUTURS EN BASE :", len(upcoming) + rejected.get("insufficient_prior_form", 0))
    print("PREDICTIONS ADMISSIBLES :", len(rows))
    print("EXCLUSIONS :", rejected)
    print("COUVERTURE CLASSES ENTRAINEMENT :", dict(counts))
    print("STATUT MODELE :", payload.get("status", "EXPERIMENTAL"))
    print("CSV :", OUT)


if __name__ == "__main__":
    main()
