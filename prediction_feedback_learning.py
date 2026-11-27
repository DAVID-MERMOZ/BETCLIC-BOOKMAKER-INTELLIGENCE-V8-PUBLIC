"""Guarded feedback learning from completed archived pre-match forecasts.

A feedback audit/training attempt is triggered at each new multiple of ten
confirmed unique matches. Between thresholds, no model is fitted or changed.
Calibration still requires the minimum sample and chronological holdout gains.
The private SQLite database is opened read-only.
"""
import csv
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sklearn.linear_model import LogisticRegression

CLASSES = ("HOME", "DRAW", "AWAY")
MIN_HISTORY = 80
FEEDBACK_AUDIT_INTERVAL = 10
MIN_TRAIN = 50
MIN_HOLDOUT = 20
MIN_BRIER_GAIN = 0.002
MIN_LOGLOSS_GAIN = 0.005
DB_PATH = Path("data/betclic_history.db")
STATUS_PATH = Path("reports/prediction_feedback_status.csv")


def parse_time(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def valid_probs(row):
    try:
        probs = [float(row[k]) for k in ("prob_home", "prob_draw", "prob_away")]
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    if any(not math.isfinite(p) or p < 0 or p > 1 for p in probs):
        return None
    if abs(sum(probs) - 1.0) > 0.001:
        return None
    return probs


def log_features(probs):
    return [math.log(max(1e-6, p)) for p in probs]


def metrics(rows, predicted):
    if not rows:
        return None
    brier = logloss = 0.0
    for row, probs in zip(rows, predicted):
        actual = row["outcome"]
        brier += sum((probs[i] - float(actual == label)) ** 2
                     for i, label in enumerate(CLASSES))
        logloss -= math.log(max(1e-15, probs[CLASSES.index(actual)]))
    n = len(rows)
    return {"n": n, "brier": brier / n, "logloss": logloss / n}


def _model_predictions(model, rows):
    class_index = {label: i for i, label in enumerate(model.classes_)}
    result = []
    for vector in model.predict_proba([log_features(r["probs"]) for r in rows]):
        result.append([float(vector[class_index[label]]) for label in CLASSES])
    return result


def load_completed_forecasts(db_path=DB_PATH):
    """Return one latest valid strictly pre-kickoff forecast per confirmed match."""
    path = Path(db_path).resolve()
    if not path.is_file():
        return []
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "prediction_history" not in tables:
            return []
        rows = [dict(r) for r in conn.execute("""
            SELECT match_id, generated_at_utc, kickoff_utc,
                   prob_home, prob_draw, prob_away, actual_outcome,
                   actual_home_score, actual_away_score
            FROM prediction_history
            WHERE actual_outcome IS NOT NULL
        """)]

    grouped = defaultdict(list)
    for row in rows:
        if row.get("actual_outcome") not in CLASSES:
            continue
        kickoff = parse_time(row.get("kickoff_utc"))
        generated = parse_time(row.get("generated_at_utc"))
        probs = valid_probs(row)
        if not kickoff or not generated or generated >= kickoff or probs is None:
            continue
        row["_kickoff"] = kickoff
        row["_generated"] = generated
        row["probs"] = probs
        grouped[str(row["match_id"])].append(row)

    selected = []
    for match_id, forecasts in grouped.items():
        outcomes = {
            (r["actual_outcome"], r.get("actual_home_score"),
             r.get("actual_away_score")) for r in forecasts
        }
        if len(outcomes) != 1:
            continue
        outcome, home_score, away_score = next(iter(outcomes))
        try:
            home_score, away_score = int(home_score), int(away_score)
        except (TypeError, ValueError, OverflowError):
            continue
        derived = "HOME" if home_score > away_score else (
            "AWAY" if home_score < away_score else "DRAW")
        if outcome != derived:
            continue
        forecasts.sort(key=lambda r: (r["_generated"], str(r.get("match_id"))))
        selected.append({
            "match_id": match_id, "kickoff": forecasts[-1]["_kickoff"],
            "generated": forecasts[-1]["_generated"],
            "outcome": outcome, "probs": forecasts[-1]["probs"],
        })
    selected.sort(key=lambda r: (r["kickoff"], r["match_id"]))
    return selected


def _chronological_split(rows):
    kickoffs = sorted({r["kickoff"] for r in rows})
    if len(kickoffs) < 2:
        return [], []
    cut = max(1, min(len(kickoffs) - 1, int(len(kickoffs) * 0.75)))
    cutoff = kickoffs[cut]
    return [r for r in rows if r["kickoff"] < cutoff], [
        r for r in rows if r["kickoff"] >= cutoff]


def fit_feedback_calibrator(rows):
    """Fit only after chronological holdout proves minimum gains on both scores."""
    status = {
        "status": "FEEDBACK_DISABLED_INSUFFICIENT_HISTORY",
        "history_n": len(rows), "train_n": 0, "holdout_n": 0,
        "raw_brier": "", "calibrated_brier": "",
        "raw_logloss": "", "calibrated_logloss": "",
        "brier_gain": "", "logloss_gain": "",
        "activation_reason": "Minimum 80 unique completed pre-match forecasts required.",
    }
    if len(rows) < MIN_HISTORY:
        status["activation_reason"] = (
            f"Waiting for {MIN_HISTORY} unique confirmed pre-match forecasts; "
            f"currently {len(rows)}.")
        status["next_audit_at"] = MIN_HISTORY
        return {"model": None, "status": status}

    # The history evaluator still runs on every pipeline cycle. Expensive
    # feedback fitting is gated to each ten-match milestone, avoiding noisy
    # parameter changes after every newly completed fixture.
    remainder = len(rows) % FEEDBACK_AUDIT_INTERVAL
    if remainder:
        next_audit = len(rows) + (FEEDBACK_AUDIT_INTERVAL - remainder)
        status["status"] = "FEEDBACK_AUDIT_WAITING_FOR_NEXT_10"
        status["activation_reason"] = (
            f"No fitting between milestones; next audit at "
            f"{next_audit} unique confirmed matches.")
        status["next_audit_at"] = next_audit
        return {"model": None, "status": status}

    status["next_audit_at"] = len(rows) + FEEDBACK_AUDIT_INTERVAL
    train, holdout = _chronological_split(rows)
    status["train_n"], status["holdout_n"] = len(train), len(holdout)
    if len(train) < MIN_TRAIN or len(holdout) < MIN_HOLDOUT:
        status["status"] = "FEEDBACK_DISABLED_INSUFFICIENT_HOLDOUT"
        status["activation_reason"] = "Chronological train/holdout minimums not met."
        return {"model": None, "status": status}
    if set(r["outcome"] for r in train) != set(CLASSES):
        status["status"] = "FEEDBACK_DISABLED_TRAINING_CLASS_MISSING"
        status["activation_reason"] = "Training window must contain all 3 outcomes."
        return {"model": None, "status": status}

    model = LogisticRegression(C=0.1, solver="lbfgs", max_iter=2000,
                              random_state=42)
    model.fit([log_features(r["probs"]) for r in train],
              [r["outcome"] for r in train])
    raw = metrics(holdout, [r["probs"] for r in holdout])
    corrected = metrics(holdout, _model_predictions(model, holdout))
    brier_gain = raw["brier"] - corrected["brier"]
    logloss_gain = raw["logloss"] - corrected["logloss"]
    status.update({
        "raw_brier": round(raw["brier"], 6),
        "calibrated_brier": round(corrected["brier"], 6),
        "raw_logloss": round(raw["logloss"], 6),
        "calibrated_logloss": round(corrected["logloss"], 6),
        "brier_gain": round(brier_gain, 6),
        "logloss_gain": round(logloss_gain, 6),
    })
    if brier_gain < MIN_BRIER_GAIN or logloss_gain < MIN_LOGLOSS_GAIN:
        status["status"] = "FEEDBACK_DISABLED_NO_ROBUST_HOLDOUT_GAIN"
        status["activation_reason"] = (
            "Calibration must improve holdout Brier by >=0.002 and Log Loss by >=0.005.")
        return {"model": None, "status": status}

    final_model = LogisticRegression(C=0.1, solver="lbfgs", max_iter=2000,
                                     random_state=42)
    final_model.fit([log_features(r["probs"]) for r in rows],
                    [r["outcome"] for r in rows])
    status["status"] = "FEEDBACK_ACTIVE_VALIDATED_ON_CHRONOLOGICAL_HOLDOUT"
    status["activation_reason"] = "Both holdout metrics improved above pre-set minimum gains."
    return {"model": final_model, "status": status}


def apply_calibrator(probs, fitted):
    """Apply validated calibration and preserve the caller's probability shape.

    The prediction pipeline passes a {HOME, DRAW, AWAY} mapping; feedback
    training helpers/tests may pass a three-item sequence. Never turn a
    mapping into a list of its keys.
    """
    is_mapping = isinstance(probs, dict)
    raw = [float(probs[label]) for label in CLASSES] if is_mapping else list(probs)

    def restore(values):
        return dict(zip(CLASSES, values)) if is_mapping else list(values)

    model = fitted.get("model") if fitted else None
    if model is None:
        return restore(raw)
    vector = model.predict_proba([log_features(raw)])[0]
    index = {label: i for i, label in enumerate(model.classes_)}
    calibrated = [float(vector[index[label]]) for label in CLASSES]
    total = sum(calibrated)
    if not math.isfinite(total) or total <= 0:
        return restore(raw)
    return restore([p / total for p in calibrated])


def write_status(status, output=STATUS_PATH):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(status)
    with output.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(status)


def load_and_fit(db_path=DB_PATH, status_path=STATUS_PATH):
    rows = load_completed_forecasts(db_path)
    fitted = fit_feedback_calibrator(rows)
    write_status(fitted["status"], status_path)
    print("APPRENTISSAGE PAR RETOUR D'EXPERIENCE :", fitted["status"])
    print("Predictions historiques uniques confirmees :", fitted["status"]["history_n"])
    print("Prochain audit feedback :", fitted["status"].get("next_audit_at", ""))
    print("Statut :", fitted["status"]["status"])
    return fitted


if __name__ == "__main__":
    load_and_fit()
