"""Experiment equipe independant: forme recente calculee sans fuite temporelle."""
import sqlite3
from collections import defaultdict
from pathlib import Path

from sklearn.linear_model import LogisticRegression

from baseline_market_evaluation import (
    CLASSES, build_dataset, norm, parse_utc, score_metrics, split_teams,
)

DB_PATH = Path("data/betclic_history.db")
ROLLING_N = 5
MIN_PRIOR_MATCHES = 3
MIN_TRAIN = 20
MIN_TEST = 5


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


def build_team_dataset(db_path=DB_PATH):
    path = Path(db_path)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        history_rows = conn.execute("""
            SELECT utc_date, home_team, away_team, home_score, away_score
            FROM provider_results
            WHERE status='FINISHED' AND home_score IS NOT NULL
              AND away_score IS NOT NULL AND utc_date IS NOT NULL
        """).fetchall()
        links = conn.execute("""
            SELECT l.betclic_match_id, l.home_score, l.away_score,
                   m.match_name, m.match_date
            FROM validated_match_links l JOIN matches m
              ON m.match_id=l.betclic_match_id
            WHERE l.validation_status='CONFIRMED'
        """).fetchall()

    history = []
    for date, home, away, hg, ag in history_rows:
        stamp = parse_utc(date)
        if stamp is None or not home or not away:
            continue
        history.append({"date": stamp, "home": norm(home), "away": norm(away),
                        "hg": int(hg), "ag": int(ag)})
    history.sort(key=lambda r: r["date"])

    samples = []
    rejected = defaultdict(int)
    for match_id, hs, aws, name, match_date in links:
        kickoff = parse_utc(match_date)
        home, away = split_teams(name)
        if kickoff is None or not home or not away:
            rejected["invalid_fixture"] += 1
            continue
        # Noms normalises exacts seulement: aucun alias approximatif.
        home_form = team_form(history, home, kickoff)
        away_form = team_form(history, away, kickoff)
        if home_form is None or away_form is None:
            rejected["insufficient_prior_form"] += 1
            continue
        outcome = "HOME" if hs > aws else "AWAY" if hs < aws else "DRAW"
        samples.append({"match_id": str(match_id), "kickoff": kickoff,
                        "outcome": outcome, "features": home_form + away_form})
    # Une seule observation par match Betclic; les doublons contradictoires sont exclus.
    by_id = {}
    for row in samples:
        previous = by_id.get(row["match_id"])
        if previous and previous["outcome"] != row["outcome"]:
            rejected["conflicting_duplicate_label"] += 1
            by_id.pop(row["match_id"], None)
            continue
        if row["match_id"] not in by_id:
            by_id[row["match_id"]] = row
    samples = sorted(by_id.values(), key=lambda r: r["kickoff"])
    return samples, dict(rejected)


def evaluate(samples, market_rows):
    samples = sorted(samples, key=lambda r: r["kickoff"])
    cut = int(len(samples) * .70)
    train, test = samples[:cut], samples[cut:]
    result = {"eligible_n": len(samples), "train_n": len(train),
              "test_n": len(test)}
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        result["status"] = "INSUFFICIENT_TEAM_FORM_SAMPLE"
        return result
    if {r["outcome"] for r in train} != set(CLASSES):
        result["status"] = "TRAINING_SET_MISSING_CLASS"
        return result

    model = LogisticRegression(C=0.1, solver="lbfgs", max_iter=2000,
                               random_state=42)
    model.fit([r["features"] for r in train], [r["outcome"] for r in train])
    predictions_by_time = {}
    for row, vector in zip(test, model.predict_proba([r["features"] for r in test])):
        probs = {c: 0.0 for c in CLASSES}
        for label, p in zip(model.classes_, vector):
            probs[label] = float(p)
        predictions_by_time[row["kickoff"]] = {
            "outcome": row["outcome"], "probs": probs
        }

    # Comparaison appariée: mêmes matchs et mêmes résultats pour les deux modèles.
    market_by_time = {}
    for row in market_rows:
        market_by_time[row["kickoff"]] = row
    common_times = sorted(set(predictions_by_time) & set(market_by_time))
    paired_model = [predictions_by_time[t] for t in common_times]
    paired_market = [market_by_time[t] for t in common_times]
    result["comparison_n"] = len(common_times)
    result["model"] = score_metrics(paired_model) if paired_model else None
    result["market"] = score_metrics(paired_market) if paired_market else None
    result["status"] = "TEAM_FORM_EXPERIMENT_COMPLETED"
    result["feature_set"] = "rolling_last_5_points_goals_for_goals_against"
    result["team_name_matching"] = "exact_normalized_only"
    return result


def main():
    samples, rejected = build_team_dataset()
    market_rows, market_diag = build_dataset()
    print("=== MODELE INDEPENDANT FORME DES EQUIPES ===")
    print("Echantillons admissibles :", len(samples))
    print("Rejets agreges :", rejected)
    print("Diagnostic marche :", market_diag)
    result = evaluate(samples, market_rows)
    print("Evaluation chronologique :", result)
    print("Historique strictement anterieur au coup d'envoi; noms exacts uniquement.")
    print("Aucun pari ni statut de modele valide n'est emis.")
    print("STATUT :", result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
