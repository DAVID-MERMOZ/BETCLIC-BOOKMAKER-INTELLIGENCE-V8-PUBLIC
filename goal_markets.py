"""Estimations experimentales des marches buts via modele de Poisson pre-match."""
from collections import defaultdict
from math import exp, factorial

MARKET_MIN_HISTORY = 3
SHRINKAGE_PRIOR = 4.0
MAX_GOALS = 12


def _prior_rows(history, team_key, kickoff):
    rows = [r for r in history if r["date"] < kickoff and
            (r["home"] == team_key or r["away"] == team_key)]
    rows.sort(key=lambda r: r["date"])
    return rows[-5:]


def _rates(rows, key):
    scored, conceded = [], []
    for row in rows:
        is_home = row["home"] == key
        scored.append(float(row["hg"] if is_home else row["ag"]))
        conceded.append(float(row["ag"] if is_home else row["hg"]))
    if not rows:
        return None
    return sum(scored) / len(scored), sum(conceded) / len(conceded)


def _poisson(lam, goals):
    return exp(-lam) * (lam ** goals) / factorial(goals)


def poisson_score_probabilities(lambda_home, lambda_away):
    """Retourne O/U 1.5, 2.5, 3.5 et BTTS depuis une matrice de scores."""
    if not (0 < lambda_home < 8 and 0 < lambda_away < 8):
        raise ValueError("Expected goals hors plage raisonnable")
    home_p = [_poisson(lambda_home, g) for g in range(MAX_GOALS + 1)]
    away_p = [_poisson(lambda_away, g) for g in range(MAX_GOALS + 1)]
    total = sum(home_p) * sum(away_p)
    if total <= 0:
        raise ValueError("Matrice de scores invalide")
    probs = {k: 0.0 for k in ("OVER_1_5", "OVER_2_5", "OVER_3_5", "BTTS_YES")}
    for hg, hp in enumerate(home_p):
        for ag, ap in enumerate(away_p):
            p = hp * ap / total
            goals = hg + ag
            if goals >= 2: probs["OVER_1_5"] += p
            if goals >= 3: probs["OVER_2_5"] += p
            if goals >= 4: probs["OVER_3_5"] += p
            if hg > 0 and ag > 0: probs["BTTS_YES"] += p
    probs["UNDER_1_5"] = 1.0 - probs["OVER_1_5"]
    probs["UNDER_2_5"] = 1.0 - probs["OVER_2_5"]
    probs["UNDER_3_5"] = 1.0 - probs["OVER_3_5"]
    probs["BTTS_NO"] = 1.0 - probs["BTTS_YES"]
    return probs


def predict_goal_markets(history, home_key, away_key, kickoff,
                         minimum_matches=MARKET_MIN_HISTORY):
    """Estime les buts attendus avec historique strictement antérieur au kickoff.

    Les taux domicile/extérieur sont ramenés vers les moyennes globales du
    corpus avant kickoff (prior de 4 matchs) pour limiter les petits échantillons.
    """
    home_rows = _prior_rows(history, home_key, kickoff)
    away_rows = _prior_rows(history, away_key, kickoff)
    if len(home_rows) < minimum_matches or len(away_rows) < minimum_matches:
        return {"status": "NO_BET_INSUFFICIENT_GOAL_HISTORY",
                "home_history_n": len(home_rows), "away_history_n": len(away_rows)}
    global_rows = [r for r in history if r["date"] < kickoff]
    if len(global_rows) < 10:
        return {"status": "NO_BET_INSUFFICIENT_GLOBAL_BASELINE",
                "home_history_n": len(home_rows), "away_history_n": len(away_rows)}
    league_home = sum(float(r["hg"]) for r in global_rows) / len(global_rows)
    league_away = sum(float(r["ag"]) for r in global_rows) / len(global_rows)
    if league_home <= 0 or league_away <= 0:
        return {"status": "NO_BET_INVALID_GLOBAL_BASELINE",
                "home_history_n": len(home_rows), "away_history_n": len(away_rows)}

    h_home = [r for r in home_rows if r["home"] == home_key]
    a_away = [r for r in away_rows if r["away"] == away_key]
    h_rates = _rates(home_rows, home_key)
    a_rates = _rates(away_rows, away_key)
    hh_rates = _rates(h_home, home_key) if h_home else h_rates
    aa_rates = _rates(a_away, away_key) if a_away else a_rates

    def shrink(value, baseline, n):
        weight = n / (n + SHRINKAGE_PRIOR)
        return weight * value + (1 - weight) * baseline

    home_attack = shrink(hh_rates[0], league_home, len(h_home) if h_home else len(home_rows))
    home_defence = shrink(hh_rates[1], league_away, len(h_home) if h_home else len(home_rows))
    away_attack = shrink(aa_rates[0], league_away, len(a_away) if a_away else len(away_rows))
    away_defence = shrink(aa_rates[1], league_home, len(a_away) if a_away else len(away_rows))
    lambda_home = max(0.15, min(4.5, league_home * (home_attack / league_home + away_defence / league_home) / 2))
    lambda_away = max(0.15, min(4.5, league_away * (away_attack / league_away + home_defence / league_away) / 2))
    probs = poisson_score_probabilities(lambda_home, lambda_away)
    return {"status": "EXPERIMENTAL_NOT_VALIDATED",
            "lambda_home": lambda_home, "lambda_away": lambda_away,
            "home_history_n": len(home_rows), "away_history_n": len(away_rows),
            "home_venue_n": len(h_home), "away_venue_n": len(a_away),
            **probs}
