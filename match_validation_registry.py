"""Registre persistant de validation humaine des rapprochements candidats."""
import csv
import hashlib
from pathlib import Path

SOURCE = Path("reports/matches_to_review.csv")
REGISTRY = Path("reports/match_validation_registry.csv")
FIELDS = [
    "candidate_id", "source_run_id", "betclic_match_id", "betclic_match",
    "betclic_date", "provider", "provider_match_id", "provider_competition",
    "provider_match", "provider_date", "score", "home_similarity",
    "away_similarity", "date_delta_hours", "candidate_score", "rank",
    "decision", "validation_status", "validated_by", "validated_at",
    "validation_notes",
]
MANUAL_FIELDS = ("validation_status", "validated_by", "validated_at", "validation_notes")


def candidate_id(row):
    identity = "|".join(str(row.get(k, "") or "").strip() for k in
                        ("betclic_match_id", "provider", "provider_match_id"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def build_registry(source=SOURCE, registry=REGISTRY, source_run_id="local"):
    if not source.exists():
        raise FileNotFoundError(f"Rapport source absent: {source}")
    previous = {row["candidate_id"]: row for row in read_rows(registry)
                if row.get("candidate_id")}
    merged = {}
    for candidate in read_rows(source):
        if not candidate.get("betclic_match_id") or not candidate.get("provider") or not candidate.get("provider_match_id"):
            continue
        key = candidate_id(candidate)
        old = previous.get(key, {})
        row = {field: candidate.get(field, "") for field in FIELDS}
        row["candidate_id"] = key
        row["source_run_id"] = str(source_run_id)
        # Un candidat déjà examiné conserve intégralement sa décision humaine.
        for field in MANUAL_FIELDS:
            row[field] = old.get(field) or ("PENDING" if field == "validation_status" else "")
        merged[key] = row

    # Conserver aussi les entrées précédentes absentes du dernier rapport :
    # elles ne disparaissent pas lors d'une collecte partielle.
    for key, old in previous.items():
        if key not in merged:
            merged[key] = {field: old.get(field, "") for field in FIELDS}

    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(merged.values(), key=lambda r: (
            r["betclic_date"], r["betclic_match_id"], r["provider"], r["provider_match_id"]
        )))
    print(f"REGISTRE: {len(merged)} candidats; aucune confirmation automatique.")
    return len(merged)


if __name__ == "__main__":
    import os
    build_registry(source_run_id=os.environ.get("GITHUB_RUN_ID", "local"))
