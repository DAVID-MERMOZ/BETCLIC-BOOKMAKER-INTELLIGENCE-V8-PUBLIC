"""Modele de forme des equipes: validation chronologique walk-forward."""
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import joblib
import sklearn
from sklearn.linear_model import LogisticRegression

from baseline_market_evaluation import (
    CLASSES, MARKET, build_dataset, classify_selection, fair_probs, parse_utc, score_metrics, validate_probability_map, split_teams as split_teams_norm,
)
from match_results_report import norm as canonical_team_norm, squad_profile, split_teams
from goal_markets import predict_goal_markets
from odds_movement import summarize_1x2_movement
from odds_news_research import research_match_news
from prediction_feedback_learning import apply_calibrator, load_and_fit


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
PREMATCH_TIMEZONE = ZoneInfo("Africa/Douala")


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


def _summarize_team_matches(prior, key):
    """Resume les performances d'une equipe sur une liste de matchs deja filtres."""
    points, scored, conceded = [], [], []
    for row in prior:
        is_home = row["home"] == key
        gf = row["hg"] if is_home else row["ag"]
        ga = row["ag"] if is_home else row["hg"]
        scored.append(gf)
        conceded.append(ga)
        points.append(3 if gf > ga else 1 if gf == ga else 0)
    n = len(prior)
    return [sum(points) / n, sum(scored) / n, sum(conceded) / n,
            (sum(scored) - sum(conceded)) / n]


def team_form_features(history, team, kickoff, venue, minimum_matches=MIN_PRIOR_MATCHES):
    """Combine forme globale et forme au lieu pertinent, sans regarder le futur.

    venue='HOME' pour l'equipe qui recoit, 'AWAY' pour l'equipe visiteuse.
    La forme au lieu est neutralisee vers la forme globale si moins de 2
    observations pertinentes existent, afin d'eviter une statistique trop fragile.
    """
    key = team_key(team)
    overall = team_form(history, team, kickoff, minimum_matches)
    if overall is None:
        return None
    if venue not in ("HOME", "AWAY"):
        raise ValueError("venue doit etre HOME ou AWAY")
    venue_rows = [row for row in history
                  if row["date"] < kickoff and
                  ((venue == "HOME" and row["home"] == key) or
                   (venue == "AWAY" and row["away"] == key))]
    venue_rows.sort(key=lambda row: row["date"])
    venue_rows = venue_rows[-ROLLING_N:]
    venue_form = (_summarize_team_matches(venue_rows, key)
                  if len(venue_rows) >= 2 else overall)
    return overall + venue_form



def team_last_five_details(history, team, kickoff):
    """Cinq derniers matchs termines avant le coup d'envoi, sans fuite temporelle."""
    key = team_key(team)
    prior = [row for row in history if row["date"] < kickoff
             and (row["home"] == key or row["away"] == key)]
    prior.sort(key=lambda row: row["date"], reverse=True)
    details = []
    for row in prior[:ROLLING_N]:
        is_home = row["home"] == key
        gf = row["hg"] if is_home else row["ag"]
        ga = row["ag"] if is_home else row["hg"]
        outcome = "W" if gf > ga else "D" if gf == ga else "L"
        details.append({"date_utc": row["date"].isoformat(),
                        "opponent": row["away"] if is_home else row["home"],
                        "venue": "HOME" if is_home else "AWAY",
                        "score_for": gf, "score_against": ga,
                        "outcome": outcome,
                        "points": 3 if outcome == "W" else 1 if outcome == "D" else 0})
    return details


def team_prior_match_count(history, team, kickoff):
    key = team_key(team)
    return min(ROLLING_N, sum(
        1 for row in history
        if row["date"] < kickoff and (row["home"] == key or row["away"] == key)
    ))


def _history_from_provider_rows(rows):
    """Nettoie l'historique multi-fournisseurs avant tout calcul de forme.

    Une rencontre identique fournie par deux sources ne doit compter qu'une fois.
    Les rencontres entre catégories d'effectifs incompatibles (senior/femmes,
    senior/jeunes, etc.) sont écartées pour éviter de contaminer la forme d'un club.
    """
    history = []
    seen = set()
    for date, home, away, hg, ag in rows:
        stamp = parse_utc(date)
        if stamp is None or not home or not away:
            continue
        if squad_profile(home) != squad_profile(away):
            continue
        try:
            home_goals, away_goals = int(hg), int(ag)
        except (TypeError, ValueError, OverflowError):
            continue
        if home_goals < 0 or away_goals < 0:
            continue
        home_key, away_key = team_key(home), team_key(away)
        # Identité indépendante du fournisseur et de l'ordre domicile/extérieur.
        # Les buts restent associés à leur équipe pour reconnaître un doublon inversé.
        team_scores = tuple(sorted(((home_key, home_goals), (away_key, away_goals))))
        identity = (stamp.replace(second=0, microsecond=0), team_scores)
        if identity in seen:
            continue
        seen.add(identity)
        history.append({"date": stamp, "home": home_key, "away": away_key,
                        "hg": home_goals, "ag": away_goals})
    history.sort(key=lambda r: r["date"])
    return history


def load_history(conn):
    rows = conn.execute("""
        SELECT utc_date, home_team, away_team, home_score, away_score
        FROM provider_results
        WHERE status='FINISHED' AND home_score IS NOT NULL
          AND away_score IS NOT NULL AND utc_date IS NOT NULL
    """).fetchall()
    return _history_from_provider_rows(rows)


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
                        "outcome": outcome, "features": team_form_features(history, home, kickoff, "HOME") + team_form_features(history, away, kickoff, "AWAY")})
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
    evaluation_groups = []
    # Les rencontres ayant le même coup d'envoi forment un seul lot.
    # Aucune étiquette du lot courant ne peut entrer dans l'entraînement
    # d'une autre rencontre simultanée (fuite temporelle intra-journée).
    group_start = 0
    while group_start < len(samples):
        kickoff = samples[group_start]["kickoff"]
        group_end = group_start + 1
        while (group_end < len(samples)
               and samples[group_end]["kickoff"] == kickoff):
            group_end += 1
        if group_start >= cut:
            train = [row for row in samples if row["kickoff"] < kickoff]
            train_counts = Counter(row["outcome"] for row in train)
            if not any(train_counts[label] == 0 for label in CLASSES):
                model = make_model()
                model.fit([row["features"] for row in train],
                          [row["outcome"] for row in train])
                current_group = samples[group_start:group_end]
                vectors = model.predict_proba(
                    [row["features"] for row in current_group]
                )
                for current, vector in zip(current_group, vectors):
                    probs = {label: 0.0 for label in CLASSES}
                    for label, value in zip(model.classes_, vector):
                        probs[label] = float(value)
                    probs = validate_probability_map(probs)
                    predictions.append({
                        "match_id": current["match_id"],
                        "outcome": current["outcome"], "probs": probs,
                        "kickoff": current["kickoff"], "train_n": len(train)
                    })
                evaluation_groups.append({
                    "kickoff": kickoff.isoformat(),
                    "train_n": len(train),
                    "test_group_n": len(current_group),
                    "match_ids": [row["match_id"] for row in current_group]
                })
        group_start = group_end

    result["initial_train_n"] = cut
    result["walk_forward_groups_n"] = len(evaluation_groups)
    result["walk_forward_groups"] = evaluation_groups
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
    result["calibration_status"] = "DESCRIPTIVE_ECE_ONLY_NOT_CALIBRATED"
    result["feature_set"] = "per_team_8_features_overall_plus_home_away_venue_form_last_5"
    result["team_name_matching"] = "curated_aliases_with_squad_separation"
    return result

def latest_complete_1x2_snapshot(odds_rows, match_name, cutoff):
    """Dernier snapshot Betclic 1X2 complet avant cutoff, marge retiree."""
    home, away = split_teams_norm(match_name)
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



def market_comparison_status(model_probs, market_probs, threshold=0.15):
    """Classe les grands ecarts comme anomalies a examiner, jamais comme value bets."""
    if not market_probs:
        return "NO_MARKET"
    max_gap = max(abs(float(model_probs[label]) - float(market_probs[label]))
                  for label in CLASSES)
    return "REVIEW_LARGE_DIVERGENCE" if max_gap >= threshold else "NO_LARGE_DIVERGENCE"

def prediction_action_status(history_quality, comparison_status, model_status):
    """Bloque toute recommandation tant que le modele n'est pas valide."""
    if history_quality != "STANDARD_3_PLUS":
        return "NO_BET_LOW_HISTORY"
    if comparison_status == "REVIEW_LARGE_DIVERGENCE":
        return "NO_BET_LARGE_DIVERGENCE"
    if model_status != "VALIDATED":
        return "NO_BET_MODEL_UNVALIDATED"
    return "ELIGIBLE_FOR_REVIEW_ONLY"


def build_upcoming_dataset(db_path=DB_PATH):
    path = Path(db_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        history = load_history(conn)
        # Les anciennes bases Betclic ne possedent pas toutes la colonne
        # competition_name. Detecter le schema evite de bloquer toute prediction.
        match_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
        }
        competition_expr = "competition_name" if "competition_name" in match_columns else "NULL"
        fixtures = conn.execute(f"""
            SELECT match_id, match_name, match_date, {competition_expr}
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
    for match_id, name, match_date, competition_name in fixtures:
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
        home_last5 = team_last_five_details(history, home, kickoff)
        away_last5 = team_last_five_details(history, away, kickoff)
        match_odds = odds_by_match.get(str(match_id), [])
        market = latest_complete_1x2_snapshot(match_odds, name, now)
        market_movement = summarize_1x2_movement(match_odds, name, now)
        is_today_local = kickoff.astimezone(PREMATCH_TIMEZONE).date() == now.astimezone(PREMATCH_TIMEZONE).date()
        movements = market_movement.get(
            "max_step_odds_change_pct", market_movement.get("odds_change_pct", {})
        )
        if is_today_local:
            # Recherche limitee aux matchs du jour pour preserver la duree du pipeline.
            web_research = research_match_news(name, movements, timeout=5, force=True)
        elif market_movement.get("status") == "MOVEMENT_MEASURED":
            web_research = research_match_news(name, movements, timeout=5)
        else:
            web_research = {
                "status": "NOT_TRIGGERED", "triggered_moves": {},
                "checked_at_utc": datetime.now(timezone.utc).isoformat(), "items": []
            }
        upcoming.append({"match_id": str(match_id), "match_name": name,
                         "competition_name": competition_name or "Championnat non renseigné",
"kickoff": kickoff, "features": (team_form_features(history, home, kickoff, "HOME", minimum_matches=1) + team_form_features(history, away, kickoff, "AWAY", minimum_matches=1)),
                         "form_matches_home": home_n, "form_matches_away": away_n,
                         "form_last5_home": home_last5, "form_last5_away": away_last5,
                         "history_quality": "STANDARD_3_PLUS" if min(home_n, away_n) >= MIN_PRIOR_MATCHES
                                            else "LOW_HISTORY_EXPERIMENTAL",
                         "market_snapshot": market,
                         "market_movement": market_movement,
                         "web_research": web_research})
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


def split_team_keys(match_name):
    home, away = split_teams(match_name)
    return team_key(home), team_key(away)


def load_history_from_db(db_path):
    with sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True) as conn:
        return load_history(conn)


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
    feedback = load_and_fit(db_path)
    upcoming, rejected = build_upcoming_dataset(db_path)
    history_for_goals = load_history_from_db(db_path)
    predictions = []
    if upcoming:
        vectors = model.predict_proba([r["features"] for r in upcoming])
        for row, vector in zip(upcoming, vectors):
            probs = {c: 0.0 for c in CLASSES}
            for label, p in zip(model.classes_, vector):
                probs[label] = float(p)
            probs = apply_calibrator(probs, feedback)
            probs = validate_probability_map(probs)
            goal_probs = predict_goal_markets(history_for_goals, *split_team_keys(row["match_name"]), row["kickoff"])
            predictions.append({**row, "probs": probs,
                                "prediction": max(probs, key=probs.get),
                                "goal_markets": goal_probs})
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PREDICTIONS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "match_id", "competition_name", "match_name", "kickoff_utc", "prediction",
            "prob_home", "prob_draw", "prob_away", "model_status",
            "feedback_status", "feedback_history_n",
            "form_matches_home", "form_matches_away", "history_quality",
            "market_status", "market_snapshot_utc", "market_prob_home",
            "market_prob_draw", "market_prob_away", "edge_home_pp",
            "edge_draw_pp", "edge_away_pp", "max_abs_edge_pp", "comparison_status", "action_status", "goal_model_status", "lambda_home", "lambda_away", "goal_history_home_n", "goal_history_away_n", "over_1_5", "under_1_5", "over_2_5", "under_2_5", "over_3_5", "under_3_5", "btts_yes", "btts_no", "market_movement_status", "market_movement_pattern", "movement_cause_status", "movement_snapshots_n", "movement_opening_at", "movement_latest_at", "movement_delta_home_pp", "movement_delta_draw_pp", "movement_delta_away_pp", "web_research_status", "web_research_triggered_moves", "web_research_checked_at_utc", "web_research_findings_json"])
        writer.writeheader()
        for row in predictions:
            writer.writerow({
                "match_id": row["match_id"],
                "competition_name": row.get("competition_name", "Championnat non renseigné"),
                "match_name": row["match_name"],
                "kickoff_utc": row["kickoff"].isoformat(),
                "prediction": row["prediction"],
                "prob_home": round(row["probs"]["HOME"], 6),
                "prob_draw": round(row["probs"]["DRAW"], 6),
                "prob_away": round(row["probs"]["AWAY"], 6),
                "model_status": pilot_status,
                "feedback_status": feedback["status"]["status"],
                "feedback_history_n": feedback["status"]["history_n"],
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
                    if row.get("market_snapshot") else "",
                "max_abs_edge_pp": round(max(abs(row["probs"][c] - row["market_snapshot"][2][c])
                    for c in CLASSES) * 100, 2) if row.get("market_snapshot") else "",
                "comparison_status": market_comparison_status(
                    row["probs"], row["market_snapshot"][2] if row.get("market_snapshot") else None),
                "action_status": prediction_action_status(
                    row["history_quality"],
                    market_comparison_status(row["probs"],
                        row["market_snapshot"][2] if row.get("market_snapshot") else None),
                    pilot_status),
                "goal_model_status": row["goal_markets"].get("status"),
                "lambda_home": round(row["goal_markets"]["lambda_home"], 4) if "lambda_home" in row["goal_markets"] else "",
                "lambda_away": round(row["goal_markets"]["lambda_away"], 4) if "lambda_away" in row["goal_markets"] else "",
                "goal_history_home_n": row["goal_markets"].get("home_history_n", ""),
                "goal_history_away_n": row["goal_markets"].get("away_history_n", ""),
                "over_1_5": round(row["goal_markets"]["OVER_1_5"], 6) if "OVER_1_5" in row["goal_markets"] else "",
                "under_1_5": round(row["goal_markets"]["UNDER_1_5"], 6) if "UNDER_1_5" in row["goal_markets"] else "",
                "over_2_5": round(row["goal_markets"]["OVER_2_5"], 6) if "OVER_2_5" in row["goal_markets"] else "",
                "under_2_5": round(row["goal_markets"]["UNDER_2_5"], 6) if "UNDER_2_5" in row["goal_markets"] else "",
                "over_3_5": round(row["goal_markets"]["OVER_3_5"], 6) if "OVER_3_5" in row["goal_markets"] else "",
                "under_3_5": round(row["goal_markets"]["UNDER_3_5"], 6) if "UNDER_3_5" in row["goal_markets"] else "",
                "btts_yes": round(row["goal_markets"]["BTTS_YES"], 6) if "BTTS_YES" in row["goal_markets"] else "",
                "btts_no": round(row["goal_markets"]["BTTS_NO"], 6) if "BTTS_NO" in row["goal_markets"] else "",
                "market_movement_status": row["market_movement"]["status"],
                "market_movement_pattern": row["market_movement"].get("pattern", ""),
                "movement_cause_status": row["market_movement"]["cause_status"],
                "movement_snapshots_n": row["market_movement"].get("snapshots_n", ""),
                "movement_opening_at": row["market_movement"].get("opening_at", ""),
                "movement_latest_at": row["market_movement"].get("latest_at", ""),
                "movement_delta_home_pp": round(row["market_movement"]["probability_delta_pp"]["HOME"], 3) if "probability_delta_pp" in row["market_movement"] else "",
                "movement_delta_draw_pp": round(row["market_movement"]["probability_delta_pp"]["DRAW"], 3) if "probability_delta_pp" in row["market_movement"] else "",
                "movement_delta_away_pp": round(row["market_movement"]["probability_delta_pp"]["AWAY"], 3) if "probability_delta_pp" in row["market_movement"] else "",
                "web_research_status": row["web_research"].get("status", ""),
                "web_research_triggered_moves": json.dumps(row["web_research"].get("triggered_moves", {}), ensure_ascii=False),
                "web_research_checked_at_utc": row["web_research"].get("checked_at_utc", ""),
                "web_research_findings_json": json.dumps(row["web_research"].get("items", []), ensure_ascii=False)})
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "classes": list(model.classes_),
                 "feature_definition": "16 total: per team overall_form[points,gf,ga,gd] + relevant_venue_form[points,gf,ga,gd]; venue_form falls back to overall when fewer than 2 venue matches",
                  "calibration_status": "DESCRIPTIVE_ECE_ONLY_NOT_CALIBRATED",
                 "feedback_learning": feedback["status"],
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
    print("Correction par historique :", feedback["status"]["status"])
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
