"""Modele de forme des equipes: validation chronologique walk-forward."""
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
import sklearn
from sklearn.linear_model import LogisticRegression

from baseline_market_evaluation import (
    CLASSES, MARKET, build_dataset, classify_selection, fair_probs, parse_utc, score_metrics, split_teams,
)
from match_results_report import norm as canonical_team_norm, squad_profile, split_teams


def team_key(name):
    """Normalisation canonique avec séparation stricte des équipes F/jeunes/réserve."""
    profile = ",".join(sorted(squad_profile(name)))
    return canonical_team_norm(name) + "|" + profile


DB_PATH = Path("data/betclic_history.db")
PREDICTIONS_PATH = Path("reports/team_form_predictions.csv")
MODEL_PATH = Path("artifacts/team_form_model.joblib")
ROLLING_N = 5
MIN_PRIOR_MATCHES = 3
MIN_CLASS_COUNT_FOR_LOO = 2


def team_form(history, team, kickoff, minimum_matches=MIN_PRIOR_MATCHES):
    key = team_key(team)
    prior = [r for r in history if r["date"] < kickoff and
             (r["home"] == key or r["away"] == key)]
    prior.sort(key=lambda r: r["date"])
    prior = prior[-ROLLING_N:]
    if len(prior) < minimum_matches:
        return None
    points, scored, conceded = [], [], []
    for r in prior:
        is_home = r["home"] == key
        gf = r["hg"] if is_home else r["ag"]
        ga = r["ag"] if is_home else r["hg"]
        scored.append(gf)
        conceded.append(ga)
        points.append(3 if gf > ga else 1 if gf == ga else 0)
    n = len(prior)
    return [sum(points) / n, sum(scored) / n, sum(conceded) / n,
            (sum(scored) - sum(conceded)) / n]


def team_prior_match_count(history, team, kickoff):
    key = team_key(team)
    return min(ROLLING_N, sum(
        1 for row in history
        if row["date"] < kickoff and (row["home"] == key or row["away"] == key)
    ))


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
        history.append({"date": stamp, "home": team_key(home), "away": team_key(away),
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
    """Validation walk-forward: chaque prediction utilise uniquement le passe."""
    samples = sorted(samples, key=lambda r: r["kickoff"])
    counts = Counter(r["outcome"] for r in samples)
    result = {"eligible_n": len(samples), "class_counts": dict(counts),
              "validation": "expanding_window_chronological_70_30"}
    if len(samples) < 12:
        result["status"] = "INSUFFICIENT_SAMPLE_FOR_WALK_FORWARD"
        return result

    cut = max(6, int(len(samples) * 0.70))
    predictions = []
    for index in range(cut, len(samples)):
        train = samples[:index]
        train_counts = Counter(row["outcome"] for row in train)
        if any(train_counts[label] == 0 for label in CLASSES):
            continue
        model = make_model()
        model.fit([row["features"] for row in train],
                  [row["outcome"] for row in train])
        current = samples[index]
        vector = model.predict_proba([current["features"]])[0]
        probs = {label: 0.0 for label in CLASSES}
        for label, value in zip(model.classes_, vector):
            probs[label] = float(value)
        predictions.append({"match_id": current["match_id"],
                            "outcome": current["outcome"], "probs": probs,
                            "kickoff": current["kickoff"],
                            "train_n": len(train)})

    result["initial_train_n"] = cut
    result["walk_forward_predictions_n"] = len(predictions)
    if len(predictions) < 5:
        result["status"] = "INSUFFICIENT_WALK_FORWARD_PREDICTIONS"
        return result

    result["cross_validation"] = score_metrics(predictions)
    market_by_id = {str(row["match_id"]): row for row in market_rows}
    paired_model, paired_market = [], []
    for prediction in predictions:
        market = market_by_id.get(prediction["match_id"])
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
    result["status"] = "TEAM_FORM_WALK_FORWARD_EXPERIMENT_COMPLETED"
    result["feature_set"] = "rolling_last_5_points_goals_for_goals_against"
    result["team_name_matching"] = "curated_aliases_with_squad_separation"
    return result

def latest_complete_1x2_snapshot(odds_rows, match_name, cutoff):
    """Dernier snapshot Betclic 1X2 complet avant cutoff, marge retiree."""
    home, away = split_teams(match_name)
    if not home or not away:
        return None
    snapshots = defaultdict(dict)
    for selection, price, collected_at in odds_rows:
        stamp = parse_utc(collected_at)
        if stamp is None or stamp > cutoff or price is None or float(price) <= 1:
            continue
        outcome = classify_selection(selection, home, away)
        if outcome is not None:
            snapshots[stamp][outcome] = float(price)
    complete = []
    for stamp, prices in snapshots.items():
        probabilities = fair_probs(prices)
        if probabilities:
            complete.append((stamp, prices, probabilities))
    if not complete:
        return None
    return max(complete, key=lambda row: row[0])


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
        odds_by_match = defaultdict(list)
        fixture_ids = [str(row[0]) for row in fixtures]
        if fixture_ids:
            placeholders = ",".join("?" for _ in fixture_ids)
            odds_rows = conn.execute(f"""
                SELECT match_id, selection_name, odds, collected_at
                FROM odds_history
                WHERE match_id IN ({placeholders})
                  AND market_name=? AND suspended=0 AND odds>1
                ORDER BY collected_at, id
            """, (*fixture_ids, MARKET)).fetchall()
            for mid, selection, price, collected_at in odds_rows:
                odds_by_match[str(mid)].append((selection, price, collected_at))
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
        # Strict threshold (3 matches) remains mandatory for training and backtesting.
        # Upcoming fallback >=1 is explicitly marked as low-history experimental.
        home_n = team_prior_match_count(history, home, kickoff)
        away_n = team_prior_match_count(history, away, kickoff)
        hf = team_form(history, home, kickoff, minimum_matches=1)
        af = team_form(history, away, kickoff, minimum_matches=1)
        if hf is None or af is None:
            rejected["no_prior_match_even_with_fallback"] += 1
            continue
        market = latest_complete_1x2_snapshot(
            odds_by_match.get(str(match_id), []), name, now
        )
        upcoming.append({"match_id": str(match_id), "match_name": name,
                         "kickoff": kickoff, "features": hf + af,
                         "form_matches_home": home_n, "form_matches_away": away_n,
                         "history_quality": "STANDARD_3_PLUS" if min(home_n, away_n) >= MIN_PRIOR_MATCHES
                                            else "LOW_HISTORY_EXPERIMENTAL",
                         "market_snapshot": market})
    upcoming.sort(key=lambda r: r["kickoff"])
    return upcoming, dict(rejected)


def fit_final_model(samples):
    """Entrainement pilote permis avec >=1 observation par classe.
    La validation walk-forward reste bloquee si les classes ne sont pas disponibles dans le passe.
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
            "prob_home", "prob_draw", "prob_away", "model_status",
            "form_matches_home", "form_matches_away", "history_quality",
            "market_status", "market_snapshot_utc", "market_prob_home",
            "market_prob_draw", "market_prob_away", "edge_home_pp",
            "edge_draw_pp", "edge_away_pp"])
        writer.writeheader()
        for row in predictions:
            writer.writerow({
                "match_id": row["match_id"], "match_name": row["match_name"],
                "kickoff_utc": row["kickoff"].isoformat(),
                "prediction": row["prediction"],
                "prob_home": round(row["probs"]["HOME"], 6),
                "prob_draw": round(row["probs"]["DRAW"], 6),
                "prob_away": round(row["probs"]["AWAY"], 6),
                "model_status": pilot_status,
                "form_matches_home": row["form_matches_home"],
                "form_matches_away": row["form_matches_away"],
                "history_quality": row["history_quality"],
                "market_status": "AVAILABLE" if row.get("market_snapshot") else "NO_COMPLETE_PREMATCH_1X2",
                "market_snapshot_utc": row["market_snapshot"][0].isoformat()
                    if row.get("market_snapshot") else "",
                "market_prob_home": round(row["market_snapshot"][2]["HOME"], 6)
                    if row.get("market_snapshot") else "",
                "market_prob_draw": round(row["market_snapshot"][2]["DRAW"], 6)
                    if row.get("market_snapshot") else "",
                "market_prob_away": round(row["market_snapshot"][2]["AWAY"], 6)
                    if row.get("market_snapshot") else "",
                "edge_home_pp": round((row["probs"]["HOME"] - row["market_snapshot"][2]["HOME"]) * 100, 2)
                    if row.get("market_snapshot") else "",
                "edge_draw_pp": round((row["probs"]["DRAW"] - row["market_snapshot"][2]["DRAW"]) * 100, 2)
                    if row.get("market_snapshot") else "",
                "edge_away_pp": round((row["probs"]["AWAY"] - row["market_snapshot"][2]["AWAY"]) * 100, 2)
                    if row.get("market_snapshot") else ""})
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": list(model.classes_),
                 "feature_definition": "home_then_away: points_per_game, goals_for_per_game, goals_against_per_game, goal_difference_per_game",
                 "training_n": len(samples),
                 "trained_at_utc": datetime.now(timezone.utc).isoformat(),
                 "class_counts": dict(counts),
                 "sklearn_version": sklearn.__version__,
                 "joblib_version": joblib.__version__,
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
    # L'entrainement final utilise tous les labels disponibles des trois classes.
    # La validation walk-forward est distincte du statut de maturite production.
    trained = train_and_predict(samples, evaluation_status=result["status"])
    if trained and result["status"] == "TEAM_FORM_WALK_FORWARD_EXPERIMENT_COMPLETED":
        final_status = "PILOT_TRAINED_EXPERIMENTAL_WALK_FORWARD_COMPLETED"
    elif trained:
        final_status = "PILOT_TRAINED_EXPERIMENTAL_VALIDATION_INSUFFICIENT"
    else:
        final_status = result["status"]
    print("Les predictions sont experimentales et non validees.")
    print("STATUT EVALUATION :", result["status"])
    print("STATUT FINAL PILOTE :", final_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
