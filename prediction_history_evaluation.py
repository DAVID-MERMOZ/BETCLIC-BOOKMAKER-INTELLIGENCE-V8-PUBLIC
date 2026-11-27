"""Descriptive evaluation of archived forecasts against confirmed results.

Reads the private SQLite history in read-only mode and emits only aggregate
metrics to reports/prediction_history_evaluation.csv (uploaded as a workflow
artifact; never committed to the public repository).
"""
import csv
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
OUT_PATH = Path("reports/prediction_history_evaluation.csv")
CLASSES = ("HOME", "DRAW", "AWAY")
MAX_MARKET_AGE_MINUTES = 120
ECE_BINS = 10
FIELDS = [
    "evaluation_scope", "model_status", "n_matches", "brier_score",
    "log_loss", "accuracy", "ece_top_label", "market_matched_n",
    "earliest_kickoff_utc", "latest_kickoff_utc",
    "outcomes_home", "outcomes_draw", "outcomes_away",
    "excluded_conflicting_outcomes", "pending_snapshots",
    "interpretation",
]


def parse_utc(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def probabilities_from(row, prefix="prob"):
    result = {}
    for label, suffix in zip(CLASSES, ("home", "draw", "away")):
        value = row.get(f"{prefix}_{suffix}")
        if value in (None, ""):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            return None
        result[label] = value
    if abs(sum(result.values()) - 1.0) > 0.001:
        return None
    return result


def select_latest_pre_match(rows):
    """One latest strictly pre-kickoff forecast per match with a consistent result."""
    grouped = defaultdict(list)
    for row in rows:
        match_id = str(row.get("match_id") or "")
        if match_id:
            grouped[match_id].append(row)

    selected = []
    conflicts = 0
    excluded = 0
    for match_id, match_rows in grouped.items():
        labels = {
            (row.get("actual_outcome"), row.get("actual_home_score"),
             row.get("actual_away_score"))
            for row in match_rows
            if row.get("actual_outcome") in CLASSES
        }
        if len(labels) != 1:
            if len(labels) > 1:
                conflicts += 1
            else:
                excluded += 1
            continue
        outcome, home_score, away_score = next(iter(labels))
        if not isinstance(home_score, int) or not isinstance(away_score, int):
            excluded += 1
            continue
        score_outcome = ("HOME" if home_score > away_score else
                         "AWAY" if home_score < away_score else "DRAW")
        if outcome != score_outcome:
            conflicts += 1
            continue

        eligible = []
        for row in match_rows:
            kickoff = parse_utc(row.get("kickoff_utc"))
            generated = parse_utc(row.get("generated_at_utc"))
            if not kickoff or not generated or generated >= kickoff:
                continue
            if row.get("actual_outcome") != outcome:
                continue
            if probabilities_from(row, "prob") is None:
                continue
            row["_kickoff_parsed"] = kickoff
            row["_generated_parsed"] = generated
            eligible.append(row)
        if not eligible:
            excluded += 1
            continue
        eligible.sort(key=lambda r: (
            r["_generated_parsed"], int(r.get("id") or 0)
        ))
        selected.append(eligible[-1])

    selected.sort(key=lambda r: str(r.get("match_id")))
    return selected, {"conflicting_matches": conflicts, "excluded_matches": excluded}


def market_probabilities_if_fresh(row, max_age_minutes=MAX_MARKET_AGE_MINUTES):
    if row.get("market_status") != "AVAILABLE":
        return None
    market = probabilities_from(row, "market_prob")
    stamp = parse_utc(row.get("market_snapshot_utc"))
    generated = row.get("_generated_parsed") or parse_utc(row.get("generated_at_utc"))
    kickoff = row.get("_kickoff_parsed") or parse_utc(row.get("kickoff_utc"))
    if market is None or not stamp or not generated or not kickoff:
        return None
    if stamp > generated or stamp >= kickoff:
        return None
    age_minutes = (generated - stamp).total_seconds() / 60.0
    if age_minutes < 0 or age_minutes > max_age_minutes:
        return None
    return market


def score_metrics(rows, prefix="prob"):
    if not rows:
        return None
    brier = log_loss = correct = 0.0
    confidences = [[] for _ in range(ECE_BINS)]
    counts = Counter()
    for row in rows:
        probs = probabilities_from(row, prefix)
        outcome = row.get("actual_outcome")
        if probs is None or outcome not in CLASSES:
            continue
        counts[outcome] += 1
        brier += sum((probs[label] - float(outcome == label)) ** 2
                     for label in CLASSES)
        log_loss -= math.log(max(1e-15, probs[outcome]))
        predicted = max(CLASSES, key=lambda label: probs[label])
        correct += float(predicted == outcome)
        confidence = probs[predicted]
        bucket = min(ECE_BINS - 1, int(confidence * ECE_BINS))
        confidences[bucket].append((confidence, float(predicted == outcome)))
    n = sum(counts.values())
    if not n:
        return None
    ece = 0.0
    for bucket in confidences:
        if bucket:
            avg_conf = sum(x[0] for x in bucket) / len(bucket)
            avg_acc = sum(x[1] for x in bucket) / len(bucket)
            ece += len(bucket) / n * abs(avg_conf - avg_acc)
    return {
        "n": n,
        "brier_score": round(brier / n, 6),
        "log_loss": round(log_loss / n, 6),
        "accuracy": round(correct / n, 6),
        "ece_top_label": round(ece, 6),
        "outcomes": counts,
    }


def _report_row(scope, status, rows, metric_prefix="prob",
                market_matched_n=0, diagnostics=None, pending=0):
    metrics = score_metrics(rows, metric_prefix)
    metrics = metrics or {
        "n": 0, "brier_score": "", "log_loss": "", "accuracy": "",
        "ece_top_label": "", "outcomes": Counter(),
    }
    dates = [r["_kickoff_parsed"] for r in rows if r.get("_kickoff_parsed")]
    outcomes = metrics["outcomes"]
    diagnostics = diagnostics or {}
    return {
        "evaluation_scope": scope,
        "model_status": status,
        "n_matches": metrics["n"],
        "brier_score": metrics["brier_score"],
        "log_loss": metrics["log_loss"],
        "accuracy": metrics["accuracy"],
        "ece_top_label": metrics["ece_top_label"],
        "market_matched_n": market_matched_n,
        "earliest_kickoff_utc": min(dates).isoformat() if dates else "",
        "latest_kickoff_utc": max(dates).isoformat() if dates else "",
        "outcomes_home": outcomes.get("HOME", 0),
        "outcomes_draw": outcomes.get("DRAW", 0),
        "outcomes_away": outcomes.get("AWAY", 0),
        "excluded_conflicting_outcomes": diagnostics.get("conflicting_matches", 0),
        "pending_snapshots": pending,
        "interpretation": "DESCRIPTIVE_ONLY_NOT_MODEL_PROMOTION",
    }


def build_report(rows, pending_snapshots=0):
    selected, diagnostics = select_latest_pre_match(rows)
    by_status = defaultdict(list)
    for row in selected:
        by_status[str(row.get("model_status") or "UNKNOWN")].append(row)

    report = []
    for status, group in sorted(by_status.items()):
        market_matched = [row for row in group
                          if market_probabilities_if_fresh(row) is not None]
        report.append(_report_row(
            "MODEL_LATEST_PREMATCH_ALL", status, group,
            market_matched_n=len(market_matched),
            diagnostics=diagnostics, pending=pending_snapshots,
        ))
        report.append(_report_row(
            "MODEL_MATCHED_FRESH_MARKET", status, market_matched,
            market_matched_n=len(market_matched),
            diagnostics=diagnostics, pending=pending_snapshots,
        ))
        # Model and market metrics use exactly the same match IDs.
        market_rows = []
        for row in market_matched:
            market = market_probabilities_if_fresh(row)
            copy = dict(row)
            copy["market_prob_home"] = market["HOME"]
            copy["market_prob_draw"] = market["DRAW"]
            copy["market_prob_away"] = market["AWAY"]
            market_rows.append(copy)
        report.append(_report_row(
            "MARKET_MATCHED_FRESH", status, market_rows,
            metric_prefix="market_prob",
            market_matched_n=len(market_matched),
            diagnostics=diagnostics, pending=pending_snapshots,
        ))

    if not report:
        report.append(_report_row(
            "MODEL_LATEST_PREMATCH_ALL", "NO_EVALUATED_MATCHES", [],
            diagnostics=diagnostics, pending=pending_snapshots,
        ))
    return report, {
        "selected_unique_matches": len(selected),
        "conflicting_matches": diagnostics["conflicting_matches"],
        "excluded_matches": diagnostics["excluded_matches"],
        "pending_snapshots": pending_snapshots,
    }


def load_evaluated_rows(db_path=DB_PATH):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Base privee absente : {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "prediction_history" not in tables:
            raise RuntimeError("Table prediction_history absente; archive non initialisee.")
        pending = conn.execute("""
            SELECT COUNT(*) FROM prediction_history WHERE actual_outcome IS NULL
        """).fetchone()[0]
        rows = [dict(r) for r in conn.execute("""
            SELECT id, workflow_run_id, match_id, match_name, generated_at_utc,
                   kickoff_utc, prediction, prob_home, prob_draw, prob_away,
                   model_status, market_status, market_snapshot_utc,
                   market_prob_home, market_prob_draw, market_prob_away,
                   actual_outcome, actual_home_score, actual_away_score
            FROM prediction_history
            WHERE actual_outcome IS NOT NULL
        """)]
    return rows, pending


def write_report(rows, output=OUT_PATH, pending_snapshots=0):
    report, summary = build_report(rows, pending_snapshots)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(report)
    return summary


def main():
    rows, pending = load_evaluated_rows()
    summary = write_report(rows, pending_snapshots=pending)
    print("EVALUATION HISTORIQUE PRE-MATCH :", summary)
    print("Rapport descriptif (non promotion) :", OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
