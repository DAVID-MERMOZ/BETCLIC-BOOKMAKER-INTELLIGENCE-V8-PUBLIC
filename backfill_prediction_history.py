"""Backfill pre-match predictions from retained GitHub Actions artifacts.

Dry-run by default. Use --apply only inside the protected private-data runner.
"""
import argparse
import csv
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prediction_history import persist_predictions

PUBLIC_REPO = "DAVID-MERMOZ/BETCLIC-BOOKMAKER-INTELLIGENCE-V8-PUBLIC"
MAIN_WORKFLOW = ".github/workflows/main.yml"
DB_PATH = Path("data/betclic_history.db")
REQUIRED = ("match_id", "match_name", "kickoff_utc", "prediction",
            "prob_home", "prob_draw", "prob_away", "model_status")


def parse_utc(value):
    if not value:
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def admissible_row(row, generated_at):
    if any(not row.get(key) for key in REQUIRED):
        return False
    if row["prediction"] not in {"HOME", "DRAW", "AWAY", "1", "N", "2"}:
        return False
    try:
        kickoff = parse_utc(row["kickoff_utc"])
        if parse_utc(generated_at) >= kickoff:
            return False
        probs = [float(row[k]) for k in ("prob_home", "prob_draw", "prob_away")]
        if not all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probs):
            return False
        if abs(sum(probs) - 1.0) > 0.02:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def gh(*args):
    env = os.environ.copy()
    env.setdefault("GH_TOKEN", env.get("PRIVATE_REPO_TOKEN", ""))
    result = subprocess.run(["gh", *args], check=True, text=True,
                            capture_output=True, env=env)
    return result.stdout


def list_runs():
    output = gh("api", "--paginate", "--jq",
                '.workflow_runs[] | select(.path == "' + MAIN_WORKFLOW +
                '" and .conclusion == "success") | [.id, .created_at] | @tsv',
                f"repos/{PUBLIC_REPO}/actions/runs?per_page=100")
    runs = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            runs.append((parts[0], parts[1]))
    return runs


def artifact_names(run_id):
    output = gh("api", "--jq",
                '.artifacts[] | select(.expired == false) | .name',
                f"repos/{PUBLIC_REPO}/actions/runs/{run_id}/artifacts")
    return [name.strip() for name in output.splitlines() if name.strip()]


def process_artifact(run_id, generated_at, artifact, temp_root, apply):
    target = temp_root / f"run-{run_id}-{len(list(temp_root.iterdir()))}"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gh", "run", "download", run_id, "--repo", PUBLIC_REPO,
                    "--name", artifact, "--dir", str(target)],
                   check=True, text=True, capture_output=True)
    csv_files = list(target.rglob("team_form_predictions.csv"))
    if not csv_files:
        return {"run_id": run_id, "artifact": artifact, "csv": False,
                "rows": 0, "inserted": 0}
    inserted = 0
    valid_count = 0
    for csv_path in csv_files:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not set(REQUIRED).issubset(reader.fieldnames):
                continue
            rows = [row for row in reader if admissible_row(row, generated_at)]
            valid_count += len(rows)
            if apply and rows:
                staged = target / f"validated-{csv_path.name}"
                with staged.open("w", newline="", encoding="utf-8") as out:
                    writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                result = persist_predictions(
                    db_path=DB_PATH, csv_path=staged, run_id=run_id,
                    generated_at=parse_utc(generated_at).isoformat())
                inserted += result.get("inserted", 0)
    return {"run_id": run_id, "artifact": artifact, "csv": True,
            "rows": valid_count, "inserted": inserted}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write validated rows into the private SQLite database")
    args = parser.parse_args()
    if args.apply and os.getenv("BETCLIC_BACKFILL_APPROVED") != "YES":
        raise SystemExit("--apply requires BETCLIC_BACKFILL_APPROVED=YES")
    runs = list_runs()
    scanned = artifacts_seen = rows = inserted = 0
    details = []
    with tempfile.TemporaryDirectory(prefix="betclic-backfill-") as tmp:
        root = Path(tmp)
        for run_id, created_at in runs:
            scanned += 1
            for name in artifact_names(run_id):
                artifacts_seen += 1
                try:
                    result = process_artifact(run_id, created_at, name, root, args.apply)
                    rows += result["rows"]
                    inserted += result["inserted"]
                    if result["csv"] and result["rows"]:
                        details.append(result)
                except subprocess.CalledProcessError as exc:
                    print(f"ARTIFACT_SKIP run={run_id} name={name} reason=download_or_api_error")
    print("BACKFILL_SUMMARY", {
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "successful_main_runs": scanned,
        "unexpired_artifacts_scanned": artifacts_seen,
        "eligible_pre_match_rows": rows,
        "inserted_rows": inserted,
        "artifacts_with_eligible_rows": len(details),
    })
    for item in details:
        print("BACKFILL_ARTIFACT", item)


if __name__ == "__main__":
    main()
