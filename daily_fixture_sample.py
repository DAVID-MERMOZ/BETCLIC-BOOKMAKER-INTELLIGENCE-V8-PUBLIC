"""Predictions quotidiennes dynamiques depuis les fixtures Betclic en base."""
import csv
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
DAILY_TIMEZONE = ZoneInfo("Africa/Douala")


def select_daily_fixtures(fixtures, now=None):
    """Conserve les matchs du jour civil local Africa/Douala."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_day = now.astimezone(DAILY_TIMEZONE).date()
    selected = []
    for fixture in fixtures:
        kickoff = fixture.get("kickoff")
        if not isinstance(kickoff, datetime):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff.astimezone(DAILY_TIMEZONE).date() == local_day:
            selected.append(fixture)
    return selected


def probabilities(model, vector):
    result = {label: 0.0 for label in CLASSES}
    for label, value in zip(model.classes_, model.predict_proba([vector])[0]):
        result[label] = float(value)
    return validate_probability_map(result)


def extract_market_snapshot(fixture, now=None):
    """Expose le snapshot 1X2 exact utilise pour la comparaison, avec son age."""
    empty = {
        "market_status": "NO_COMPLETE_PREMATCH_1X2",
        "market_snapshot_utc": "",
        "market_snapshot_age_minutes": "",
        "market_prob_home": "",
        "market_prob_draw": "",
        "market_prob_away": "",
    }
    snapshot = fixture.get("market_snapshot")
    if not snapshot:
        return empty, None

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        stamp, _market_name, raw_probs = snapshot
        if not isinstance(stamp, datetime):
            stamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        now_utc = now.astimezone(timezone.utc)
        if stamp > now_utc:
            raise ValueError("Snapshot marche posterieur a l'heure de generation.")
        market_probs = validate_probability_map(raw_probs)
        age_minutes = round((now_utc - stamp).total_seconds() / 60, 2)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
        return {**empty, "market_status": "INVALID_PREMATCH_1X2_SNAPSHOT"}, None

    return ({
        "market_status": "AVAILABLE",
        "market_snapshot_utc": stamp.isoformat(),
        "market_snapshot_age_minutes": age_minutes,
        "market_prob_home": round(market_probs["HOME"], 6),
        "market_prob_draw": round(market_probs["DRAW"], 6),
        "market_prob_away": round(market_probs["AWAY"], 6),
    }, market_probs)


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
    daily_upcoming = select_daily_fixtures(upcoming, now)
    history = load_history_from_db(DB_PATH)
    rows = []
    for fixture in daily_upcoming:
        vector = fixture["features"]
        probs = probabilities(model, vector)
        balanced = probabilities(balanced_model, vector) if balanced_model else None
        home_key, away_key = split_team_keys(fixture["match_name"])
        goals = predict_goal_markets(history, home_key, away_key, fixture["kickoff"])
        market_fields, market_probs = extract_market_snapshot(fixture, now)
        comparison_status = market_comparison_status(probs, market_probs)
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
            **market_fields,
            "max_abs_edge_pp": round(max(abs(probs[label] - market_probs[label])
                for label in CLASSES) * 100, 2) if market_probs else "",
            "comparison_status": comparison_status,
            "action_status": prediction_action_status(
                fixture["history_quality"], comparison_status,
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
        "form_matches_away", "market_status", "market_snapshot_utc",
        "market_snapshot_age_minutes", "market_prob_home", "market_prob_draw",
        "market_prob_away", "max_abs_edge_pp", "comparison_status",
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
    print("DATE LOCALE DE REFERENCE :", now.astimezone(DAILY_TIMEZONE).date().isoformat())
    print("MATCHS FUTURS ADMISSIBLES AVANT FILTRE JOUR :", len(upcoming))
    print("MATCHS DU JOUR AFRICA/DOUALA :", len(daily_upcoming))
    print("MATCHS FUTURS HORS JOUR EXCLUS :", len(upcoming) - len(daily_upcoming))
    print("PREDICTIONS ADMISSIBLES :", len(rows))
    available_markets = [row for row in rows if row.get("market_status") == "AVAILABLE"]
    market_ages = [float(row["market_snapshot_age_minutes"]) for row in available_markets
                   if row.get("market_snapshot_age_minutes") not in (None, "")]
    print("SNAPSHOTS 1X2 VALIDES :", len(available_markets), "/", len(rows))
    if market_ages:
        print("AGE MAX SNAPSHOT 1X2 (MIN) :", max(market_ages))
    print("EXCLUSIONS DONNEES/HISTORIQUE :", rejected)
    print("COUVERTURE CLASSES ENTRAINEMENT :", dict(counts))
    print("STATUT MODELE :", payload.get("status", "EXPERIMENTAL"))
    print("CSV :", OUT)


if __name__ == "__main__":
    main()
