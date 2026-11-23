"""Analyse les mouvements pre-match 1X2 sans inventer leur cause.

Les variations sont des faits observes. Les causes sportives (blessure,
composition, meteo, information interne) ne sont jamais deduites des seules cotes.
"""
from collections import defaultdict

from baseline_market_evaluation import CLASSES, classify_selection, fair_probs, parse_utc


def summarize_1x2_movement(odds_rows, match_name, cutoff):
    """Compare les premiers et derniers snapshots 1X2 complets avant cutoff.

    odds_rows: iterable de tuples (selection_name, odds, collected_at).
    """
    snapshots = defaultdict(dict)
    for selection, price, collected_at in odds_rows:
        stamp = parse_utc(collected_at)
        if stamp is None or stamp > cutoff:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 1:
            continue
        outcome = classify_selection(selection, *(__import__(
            "baseline_market_evaluation"
        ).split_teams(match_name)))
        if outcome in CLASSES:
            snapshots[stamp][outcome] = price

    complete = []
    for stamp, prices in snapshots.items():
        probabilities = fair_probs(prices)
        if probabilities and all(label in probabilities for label in CLASSES):
            complete.append((stamp, prices, probabilities))
    if len(complete) < 2:
        return {
            "status": "INSUFFICIENT_TWO_COMPLETE_SNAPSHOTS",
            "cause_status": "CAUSE_UNVERIFIED_NO_EVENT_FEED",
        }

    complete.sort(key=lambda row: row[0])
    opening_at, opening_odds, opening_probs = complete[0]
    latest_at, latest_odds, latest_probs = complete[-1]
    deltas = {label: (latest_probs[label] - opening_probs[label]) * 100
              for label in CLASSES}
    odds_changes = {
        label: ((latest_odds[label] - opening_odds[label]) /
                opening_odds[label]) * 100
        for label in CLASSES
    }
    changed = [label for label, delta in deltas.items() if abs(delta) >= 1.0]
    if len(changed) >= 2:
        pattern = "REPRICING_MULTI_SELECTION"
    elif len(changed) == 1:
        pattern = "REPRICING_SELECTION_CIBLEE"
    else:
        pattern = "NO_SIGNIFICANT_1PP_PROBABILITY_SHIFT"

    return {
        "status": "MOVEMENT_MEASURED",
        "opening_at": opening_at.isoformat(),
        "latest_at": latest_at.isoformat(),
        "snapshots_n": len(complete),
        "opening_probabilities": opening_probs,
        "latest_probabilities": latest_probs,
        "probability_delta_pp": deltas,
        "odds_change_pct": odds_changes,
        "pattern": pattern,
        "cause_status": "CAUSE_UNVERIFIED_NO_EVENT_FEED",
    }
