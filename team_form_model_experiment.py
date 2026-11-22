"""Modele de forme des equipes: validation leave-one-out et predictions futures."""
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut

from baseline_market_evaluation import (
    CLASSES, build_dataset, norm, parse_utc, score_metrics, split_teams,
)

DB_PATH = Path("data/betclic_history.db")
PREDICTIONS_PATH = Path("reports/team_form_predictions.csv")
MODEL_PATH = Path("artifacts/team_form_model.joblib")
ROLLING_N = 5
MIN_PRIOR_MATCHES = 3
MIN_CLASS_COUNT_FOR_LOO = 2


def team_form(history, team, kickoff):
    prior = [r for r in history if r["date"] < kickoff and
             (r["home"] == team or r["away"] == team)]
    prior.sort(key=lambda r: r["date"])
    prior = prior[-ROLLING_N:]
    if len(prior) < MIN_PRIOR_MATCHES:
        return None
    points, scored, conceded = [], [], []
    for r in prior:
        is_home = r["home"] == team
        gf = r["hg"] if is_home else r["ag"]
        ga = r["ag"] if is_home else r["hg"]
        scored.append(gf)
        conceded.append(ga)
        points.append(3 if gf > ga else 1 if gf == ga else 0)
    n = len(prior)
    return [sum(points) / n, sum(scored) / n, sum(conceded) / n,
            (sum(scored) - sum(conceded)) / n]


def load_history(conn):
    rows = conn.execute("""
        SELECT utc_date, home_team, away_team, home_score, away_score
        FROM provider_results
        WHERE status='FINISHED' AND home_score IS NOT NULL
          AND away_score IS NOT NULL AND utc_date IS NOT NULL
    """).fetchall()
    history = []
    for date, home, away, hg, ag in rows:
        stamp = parse_utc(date)
        if stamp is None or not home or not away:
            continue
        history.append({"date": stamp, "home": norm(home), "away": norm(away),
                        "hg": int(hg), "ag": int(ag)})
    history.sort(key=lambda r: r["date"])
    return history


def build_team_dataset(db_path=DB_PATH):
    path = Path(db_path)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        history = load_history(conn)
        links = conn.execute("""
            SELECT l.betclic_match_id, l.home_score, l.away_score,
                   m.match_name, m.match_date
            FROM validated_match_links l JOIN matches m
              ON m.match_id=l.betclic_match_id
            WHERE l.validation_status='CONFIRMED'
        """).fetchall()

    samples = []
    rejected = defaultdict(int)
    for match_id, hs, aws, name, match_date in links:
        kickoff = parse_utc(match_date)
        home, away = split_teams(name)
        if kickoff is None or not home or not away:
            rejected["invalid_fixture"] += 1
            continue
        home_form = team_form(history, home, kickoff)
        away_form = team_form(history, away, kickoff)
        if home_form is None or away_form is None:
            rejected["insufficient_prior_form"] += 1
            continue
        if not isinstance(hs, int) or not isinstance(aws, int) or hs < 0 or aws < 0:
            rejected["invalid_label"] += 1
            continue
        outcome = "HOME" if hs > aws else "AWAY" if hs < aws else "DRAW"
        samples.append({"match_id": str(match_id), "kickoff": kickoff,
                        "outcome": outcome, "features": home_form + away_form})
    by_id = {}
    conflicted = set()
    for row in samples:
        mid = row["match_id"]
        if mid in conflicted:
            continue
        previous = by_id.get(mid)
        if previous and previous["outcome"] != row["outcome"]:
            rejected["conflicting_duplicate_label"] += 1
            by_id.pop(mid, None)
            conflicted.add(mid)
        elif previous is None:
            by_id[mid] = row
    return sorted(by_id.values(), key=lambda r: r["kickoff"]), dict(rejected)


def make_model():
    return LogisticRegression(C=0.1, solver="lbfgs", max_iter=2000,
                              random_state=42)


def evaluate(samples, market_rows):
    """LOOCV utilise chaque observation admissible au test une fois."""
    samples = sorted(samples, key=lambda r: r["kickoff"])
    counts = Counter(r["outcome"] for r in samples)
    result = {"eligible_n": len(samples), "class_counts": dict(counts),
              "validation": "leave_one_out"}
    if len(samples) < 6 or any(counts[c] < MIN_CLASS_COUNT_FOR_LOO for c in CLASSES):
        result["status"] = "INSUFFICIENT_CLASS_COVERAGE_FOR_LOOCV"
        return result

    X = [r["features"] for r in samples]
    y = [r["outcome"] for r in samples]
    oof_rows = [None] * len(samples)
    for train_idx, test_idx in LeaveOneOut().split(X):
        model = make_model()
        model.fit([X[i] for i in train_idx], [y[i] for i in train_idx])
        i = int(test_idx[0])
        vector = model.predict_proba([X[i]])[0]
        probs = {c: 0.0 for c in CLASSES}
        for label, p in zip(model.classes_, vector):
            probs[label] = float(p)
        oof_rows[i] = {"outcome": y[i], "probs": probs}

    result["cross_validation"] = score_metrics(oof_rows)
    market_by_id = {str(r["match_id"]): r for r in market_rows}
    paired_model, paired_market = [], []
    for sample, prediction in zip(samples, oof_rows):
        market = market_by_id.get(sample["match_id"])
        if market:
            paired_model.append(prediction)
            paired_market.append({"outcome": market["outcome"],
                                  "probs": market["probs"]})
    result["comparison_n"] = len(paired_model)
    result["model_paired"] = score_metrics(paired_model) if paired_model else None
    result["market_paired"] = score_metrics(paired_market) if paired_market else None
    if paired_model:
        result["better_log_loss_than_market"] = (
            result["model_paired"]["log_loss"] < result["market_paired"]["log_loss"])
        result["better_brier_than_market"] = (
            result["model_paired"]["brier_score"] < result["market_paired"]["brier_score"])
    result["status"] = "TEAM_FORM_LOOCV_EXPERIMENT_COMPLETED"
    result["feature_set"] = "rolling_last_5_points_goals_for_goals_against"
    result["team_name_matching"] = "exact_normalized_only"
    return result


def build_upcoming_dataset(db_path=DB_PATH):
    path = Path(db_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        history = load_history(conn)
        fixtures = conn.execute("""
            SELECT match_id, match_name, match_date
            FROM matches
            WHERE sport='football' AND COALESCE(is_live, 0)=0
        """).fetchall()
    upcoming, rejected = [], defaultdict(int)
    for match_id, name, match_date in fixtures:
        kickoff = parse_utc(match_date)
        if kickoff is None:
            rejected["invalid_date"] += 1
            continue
        if kickoff <= now:
            continue
        home, away = split_teams(name)
        if not home or not away:
            rejected["invalid_fixture"] += 1
            continue
        hf, af = team_form(history, home, kickoff), team_form(history, away, kickoff)
        if hf is None or af is None:
            rejected["insufficient_prior_form"] += 1
            continue
        upcoming.append({"match_id": str(match_id), "match_name": name,
                         "kickoff": kickoff, "features": hf + af})
    upcoming.sort(key=lambda r: r["kickoff"])
    return upcoming, dict(rejected)


def fit_final_model(samples):
    """Entrainement pilote permis avec >=1 observation par classe.
    La validation croisee reste bloquee si une classe est singleton.
    """
    counts = Counter(r["outcome"] for r in samples)
    if len(samples) < 6 or any(counts[c] < 1 for c in CLASSES):
        return None
    model = make_model()
    model.fit([r["features"] for r in samples], [r["outcome"] for r in samples])
    return model


def train_and_predict(samples, db_path=DB_PATH, evaluation_status=None):
    model = fit_final_model(samples)
    if model is None:
        print("ENTRAINEMENT FINAL NON EXECUTE : une classe est absente ou echantillon trop petit.")
        return False
    counts = Counter(r["outcome"] for r in samples)
    pilot_status = ("EXPERIMENTAL_SINGLETON_CLASS_NO_CV"
                    if any(counts[c] < MIN_CLASS_COUNT_FOR_LOO for c in CLASSES)
                    else "EXPERIMENTAL_NOT_VALIDATED")
    print("ENTRAINEMENT PILOTE : utilise les", len(samples), "matchs admissibles.")
    print("Couverture des classes :", dict(counts))
    print("Validation croisee :", evaluation_status or "non executee")
    print("Statut du modele :", pilot_status)
    upcoming, rejected = build_upcoming_dataset(db_path)
    predictions = []
    if upcoming:
        vectors = model.predict_proba([r["features"] for r in upcoming])
        for row, vector in zip(upcoming, vectors):
            probs = {c: 0.0 for c in CLASSES}
            for label, p in zip(model.classes_, vector):
                probs[label] = float(p)
            predictions.append({**row, "probs": probs,
                                "prediction": max(probs, key=probs.get)})
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PREDICTIONS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "match_id", "match_name", "kickoff_utc", "prediction",
            "prob_home", "prob_draw", "prob_away", "model_status"])
        writer.writeheader()
        for row in predictions:
            writer.writerow({
                "match_id": row["match_id"], "match_name": row["match_name"],
                "kickoff_utc": row["kickoff"].isoformat(),
                "prediction": row["prediction"],
                "prob_home": round(row["probs"]["HOME"], 6),
                "prob_draw": round(row["probs"]["DRAW"], 6),
                "prob_away": round(row["probs"]["AWAY"], 6),
                "model_status": pilot_status})
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": list(model.classes_),
                 "feature_definition": "home_then_away: points_per_game, goals_for_per_game, goals_against_per_game, goal_difference_per_game",
                 "training_n": len(samples),
                 "trained_at_utc": datetime.now(timezone.utc).isoformat(),
                 "class_counts": dict(counts),
                 "validation_status": evaluation_status or "NOT_EVALUATED",
                 "status": pilot_status}, MODEL_PATH)
    print("Matchs futurs avec historique suffisant :", len(upcoming))
    print("Matchs futurs exclus :", rejected)
    print("Predictions generees :", len(predictions))
    print("CSV predictions :", PREDICTIONS_PATH)
    print("Modele entraine sur l'ensemble admissible :", MODEL_PATH)
    return True


def main():
    samples, rejected = build_team_dataset()
    market_rows, market_diag = build_dataset()
    print("=== MODELE FORME DES EQUIPES - PILOTE SUR TOUT L'ECHANTILLON ===")
    print("Echantillons admissibles :", len(samples))
    print("Rejets agreges :", rejected)
    print("Diagnostic marche :", market_diag)
    result = evaluate(samples, market_rows)
    print("Evaluation :", result)
    # L'entrainement final utilise tous les labels disponibles des trois classes,
    # meme si la LOOCV est statistiquement impossible avec une classe singleton.
    # Dans ce cas, le modele est marque experimental sans validation.
    trained = train_and_predict(samples, evaluation_status=result["status"])
    if trained and result["status"] != "TEAM_FORM_LOOCV_EXPERIMENT_COMPLETED":
        final_status = "PILOT_TRAINED_EXPERIMENTAL_NO_CV"
    elif trained:
        final_status = "TEAM_FORM_PILOT_TRAINED_EXPERIMENTAL"
    else:
        final_status = result["status"]
    print("Les predictions sont experimentales et non validees.")
    print("STATUT EVALUATION :", result["status"])
    print("STATUT FINAL PILOTE :", final_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
