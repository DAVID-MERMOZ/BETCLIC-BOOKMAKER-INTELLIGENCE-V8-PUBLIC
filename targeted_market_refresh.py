"""Actualisation cible du volet marche pour les matchs signales.

Lecture seule de SQLite. Le modele de forme n'est pas re-entraine ici :
les probabilites du modele existantes sont conservees et explicitement marquees
comme non recalculees. Seules les probabilites de marche sont actualisees.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import unicodedata
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
CHANGES_PATH = Path("reports/changes_to_refresh.csv")
PREDICTIONS_PATH = Path("reports/team_form_predictions.csv")
OUTPUT_PATH = Path("reports/targeted_market_refresh.csv")
CLASSES = ("HOME", "DRAW", "AWAY")
FIELDS = [
    "match_id", "match_name", "kickoff_utc", "refresh_checked_at_utc",
    "market_name", "market_status", "market_snapshot_utc", "market_prob_home",
    "market_prob_draw", "market_prob_away", "model_status",
    "model_probabilities_status", "targeted_refresh_status",
    "reason",
]


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def split_teams(name: str):
    for sep in (" - ", " – ", " — ", " vs ", " v "):
        parts = str(name or "").split(sep)
        if len(parts) == 2 and norm(parts[0]) and norm(parts[1]):
            return norm(parts[0]), norm(parts[1])
    return None, None


def outcome_for_selection(selection, home, away):
    label = norm(selection)
    if label in {"x", "draw", "tie", "nul", "match nul", "empate"}:
        return "DRAW"
    if label in {"1", "home", "home win", "domicile", "victoire domicile"} or label == home:
        return "HOME"
    if label in {"2", "away", "away win", "exterieur", "victoire exterieur"} or label == away:
        return "AWAY"
    return None


def parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def latest_market_snapshot(rows, match_name, kickoff, now):
    home, away = split_teams(match_name)
    if not home or not away:
        return None
    kickoff_dt = parse_utc(kickoff)
    now_dt = now.astimezone(timezone.utc)
    if kickoff_dt is None or kickoff_dt <= now_dt:
        return None
    snapshots = defaultdict(dict)
    for selection, odds, suspended, stamp in rows:
        stamp_dt = parse_utc(stamp)
        if stamp_dt is None or stamp_dt > kickoff_dt or stamp_dt > now_dt:
            continue
        if suspended:
            continue
        try:
            odds = float(odds)
        except (TypeError, ValueError):
            continue
        if odds <= 1:
            continue
        outcome = outcome_for_selection(selection, home, away)
        if outcome:
            snapshots[stamp][outcome] = odds
    complete = []
    for stamp, prices in snapshots.items():
        if set(prices) != set(CLASSES):
            continue
        implied = {key: 1.0 / prices[key] for key in CLASSES}
        total = sum(implied.values())
        if total > 0:
            complete.append((stamp, {key: implied[key] / total for key in CLASSES}))
    return max(complete, key=lambda item: item[0]) if complete else None


def build_targeted_refresh(db_path, changes_path, predictions_path, now=None):
    now = now or datetime.now(timezone.utc)
    checked_at = now.astimezone(timezone.utc).isoformat()
    with changes_path.open(encoding="utf-8", newline="") as handle:
        change_rows = list(csv.DictReader(handle))
    affected = {str(row["match_id"]) for row in change_rows if row.get("match_id")}
    changed_markets = defaultdict(set)
    for row in change_rows:
        if row.get("match_id") and row.get("market_name"):
            changed_markets[str(row["match_id"])].add(row["market_name"])
    with predictions_path.open(encoding="utf-8", newline="") as handle:
        prediction_rows = {str(row["match_id"]): row for row in csv.DictReader(handle)}

    if not affected:
        return []
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        matches = conn.execute(
            f"SELECT match_id, match_name, match_date FROM matches WHERE match_id IN ({','.join('?' for _ in affected)})",
            tuple(sorted(affected)),
        ).fetchall()
        rows_by_id = defaultdict(list)
        for match_id, name, kickoff in matches:
            live_row = conn.execute(
                "SELECT COALESCE(is_live, 0) FROM matches WHERE match_id=?",
                (str(match_id),),
            ).fetchone()
            market_rows = {}
            for market_name in sorted(changed_markets.get(str(match_id), set())):
                odds_rows = conn.execute(
                    """SELECT selection_name, odds, suspended, collected_at
                       FROM odds_history
                       WHERE match_id=? AND market_name=?
                       ORDER BY collected_at""",
                    (str(match_id), market_name),
                ).fetchall()
                market_rows[market_name] = odds_rows
            rows_by_id[str(match_id)] = (name, kickoff, market_rows, bool(live_row and live_row[0]))

    output = []
    for match_id in sorted(affected):
        prior = prediction_rows.get(match_id, {})
        match_data = rows_by_id.get(match_id)
        if not match_data:
            output.append({
                "match_id": match_id, "match_name": prior.get("match_name", ""),
                "kickoff_utc": prior.get("kickoff_utc", ""),
                "refresh_checked_at_utc": checked_at,
                "market_name": "", "market_status": "MATCH_NOT_FOUND_IN_DB", "market_snapshot_utc": "",
                "market_prob_home": "", "market_prob_draw": "", "market_prob_away": "",
                "model_status": prior.get("model_status", "UNKNOWN"),
                "model_probabilities_status": "UNCHANGED_NOT_RECALCULATED",
                "targeted_refresh_status": "INCOMPLETE",
                "reason": "Match absent de la base ou ID non reconnu.",
            })
            continue
        name, kickoff, market_rows, is_live = match_data
        kickoff_dt = parse_utc(kickoff)
        if is_live or kickoff_dt is None or kickoff_dt <= now.astimezone(timezone.utc):
            output.append({
                "match_id": match_id, "match_name": name, "kickoff_utc": kickoff,
                "refresh_checked_at_utc": checked_at,
                "market_name": "", "market_status": "MATCH_NOT_ELIGIBLE_PREMATCH", "market_snapshot_utc": "",
                "market_prob_home": "", "market_prob_draw": "", "market_prob_away": "",
                "model_status": prior.get("model_status", "UNKNOWN"),
                "model_probabilities_status": "UNCHANGED_NOT_RECALCULATED",
                "targeted_refresh_status": "INCOMPLETE",
                "reason": "Match deja commence, en direct, ou date de coup d'envoi invalide.",
            })
            continue
        candidate_snapshots = []
        for market_name, odds_rows in market_rows.items():
            candidate = latest_market_snapshot(odds_rows, name, kickoff, now)
            if candidate:
                candidate_snapshots.append((candidate[0], market_name, candidate[1]))
        snapshot = max(candidate_snapshots, key=lambda item: item[0]) if candidate_snapshots else None
        common = {
            "match_id": match_id, "match_name": name, "kickoff_utc": kickoff,
            "refresh_checked_at_utc": checked_at,
            "model_status": prior.get("model_status", "NO_PRIOR_PREDICTION"),
            "model_probabilities_status": "UNCHANGED_NOT_RECALCULATED",
        }
        if snapshot is None:
            output.append({
                **common, "market_name": "", "market_status": "NO_COMPLETE_1X2_SNAPSHOT",
                "market_snapshot_utc": "", "market_prob_home": "",
                "market_prob_draw": "", "market_prob_away": "",
                "targeted_refresh_status": "INCOMPLETE",
                "reason": "Aucun snapshot 1X2 complet exploitable apres changement.",
            })
        else:
            stamp, market_name, probs = snapshot
            output.append({
                **common, "market_name": market_name, "market_status": "UPDATED_FROM_LATEST_DB_SNAPSHOT",
                "market_snapshot_utc": stamp,
                "market_prob_home": round(probs["HOME"], 6),
                "market_prob_draw": round(probs["DRAW"], 6),
                "market_prob_away": round(probs["AWAY"], 6),
                "targeted_refresh_status": "MARKET_ONLY_UPDATED",
                "reason": "Cotes 1X2 actualisees; probabilites du modele conservees.",
            })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--changes", type=Path, default=CHANGES_PATH)
    parser.add_argument("--predictions", type=Path, default=PREDICTIONS_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    for path in (args.db, args.changes, args.predictions):
        if not path.is_file():
            parser.error(f"Fichier requis absent : {path}")
    rows = build_targeted_refresh(args.db, args.changes, args.predictions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    updated = sum(row["targeted_refresh_status"] == "MARKET_ONLY_UPDATED" for row in rows)
    print(f"MATCHS CIBLES : {len(rows)}")
    print(f"MARCHES 1X2 ACTUALISES : {updated}")
    print(f"MODELE NON REENTRAINE : {len(rows)} (probabilites existantes conservees)")
    print(f"RAPPORT : {args.output}")


if __name__ == "__main__":
    main()
