"""Backfill pre-match predictions from retained GitHub Actions artifacts.

Dry-run by default. Use --apply only inside the protected private-data runner.
"""
import argparse
import csv
import math
import os
import sqlite3
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
    # Artifact upload time is a conservative upper bound for when its CSV existed.
    # Run creation can precede prediction generation and admit post-kickoff rows.
    output = gh("api", "--jq",
                '.artifacts[] | select(.expired == false) | [.name, .created_at] | @tsv',
                f"repos/{PUBLIC_REPO}/actions/runs/{run_id}/artifacts")
    artifacts = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            artifacts.append((parts[0].strip(), parts[1].strip()))
    return artifacts


def process_artifact(run_id, generated_at, artifact, temp_root, apply, seen_keys):
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
            rows = []
            for row in reader:
                if not admissible_row(row, generated_at):
                    continue
                key = (str(run_id), str(row["match_id"]))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append(row)
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


def audit_existing_database(candidate_keys):
    """Compare unique candidates with the restored DB without modifying it."""
    uri = f"file:{DB_PATH.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        existing_keys = {
            (str(run_id), str(match_id))
            for run_id, match_id in conn.execute(
                "SELECT workflow_run_id, match_id FROM prediction_history"
            )
        }
        evaluated_ids = {
            str(row[0]) for row in conn.execute(
                "SELECT DISTINCT match_id FROM prediction_history "
                "WHERE actual_outcome IS NOT NULL"
            )
        }
        confirmed_rows = conn.execute("""
            SELECT betclic_match_id,
                   COUNT(DISTINCT CAST(home_score AS TEXT) || ':' ||
                                  CAST(away_score AS TEXT))
            FROM validated_match_links
            WHERE validation_status='CONFIRMED'
              AND home_score IS NOT NULL AND away_score IS NOT NULL
            GROUP BY betclic_match_id
        """).fetchall()
        unambiguous_confirmed_ids = {
            str(match_id) for match_id, variants in confirmed_rows if variants == 1
        }

    candidate_ids = {str(match_id) for _, match_id in candidate_keys}
    # Compare both raw IDs and whitespace-trimmed IDs. This is diagnostic only:
    # no fuzzy/team-name matching and no result link is created automatically.
    normalized_candidate_ids = {value.strip() for value in candidate_ids}
    normalized_evaluated_ids = {value.strip() for value in evaluated_ids}
    normalized_confirmed_ids = {value.strip() for value in unambiguous_confirmed_ids}
    existing_candidate_keys = candidate_keys & existing_keys
    exact_confirmed_overlap = candidate_ids & unambiguous_confirmed_ids
    trimmed_confirmed_overlap = normalized_candidate_ids & normalized_confirmed_ids
    exact_evaluated_overlap = candidate_ids & evaluated_ids
    trimmed_evaluated_overlap = normalized_candidate_ids & normalized_evaluated_ids
    return {
        "unique_candidate_keys": len(candidate_keys),
        "unique_candidate_match_ids": len(candidate_ids),
        "already_in_database_keys": len(existing_candidate_keys),
        "new_candidate_keys": len(candidate_keys - existing_keys),
        "candidate_matches_with_evaluated_prediction": len(exact_evaluated_overlap),
        "candidate_matches_with_unambiguous_confirmed_score": len(exact_confirmed_overlap),
        "trimmed_id_overlap_with_evaluated_prediction": len(trimmed_evaluated_overlap),
        "trimmed_id_overlap_with_confirmed_score": len(trimmed_confirmed_overlap),
        "candidate_ids_sample": sorted(candidate_ids)[:25],
        "confirmed_betclic_ids_sample": sorted(unambiguous_confirmed_ids)[:25],
        "evaluated_prediction_ids_sample": sorted(evaluated_ids)[:25],
        "confirmed_links_total": len(unambiguous_confirmed_ids),
        "evaluated_match_ids_total": len(evaluated_ids),
        "diagnostic_note": "ID-only comparison; samples are for manual diagnosis, never auto-link."
    }


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
    seen_keys = set()
    with tempfile.TemporaryDirectory(prefix="betclic-backfill-") as tmp:
        root = Path(tmp)
        for run_id, created_at in runs:
            scanned += 1
            for name, artifact_created_at in artifact_names(run_id):
                artifacts_seen += 1
                try:
                    result = process_artifact(
                        run_id, artifact_created_at, name, root, args.apply, seen_keys)
                    rows += result["rows"]
                    inserted += result["inserted"]
                    if result["csv"] and result["rows"]:
                        details.append(result)
                except subprocess.CalledProcessError as exc:
                    print(f"ARTIFACT_SKIP run={run_id} name={name} reason=download_or_api_error")
    audit = audit_existing_database(seen_keys)
    print("BACKFILL_DATABASE_AUDIT", audit)
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
