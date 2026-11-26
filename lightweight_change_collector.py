"""Compare les deux derniers snapshots de cotes deja stockes, sans toucher a la DB.

Ce composant est volontairement local et en lecture seule : il ne contacte aucune
API, ne fabrique aucune composition et n'actualise pas lui-meme les predictions.
Il produit une liste des selections dont la cote ou le statut de suspension a change.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path("data/betclic_history.db")
DEFAULT_OUTPUT = Path("reports/changes_to_refresh.csv")
MAX_SNAPSHOT_AGE_MINUTES = 120
FIELDS = [
    "match_id", "match_name", "match_date", "market_name", "selection_name",
    "previous_odds", "latest_odds", "odds_change_pct",
    "previous_suspended", "latest_suspended",
    "previous_snapshot_at", "latest_snapshot_at", "change_type",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def collect_changes(db_path: Path, now: datetime | None = None) -> list[dict]:
    """Retourne les changements observables entre les deux derniers snapshots."""
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_iso = now.astimezone(timezone.utc).isoformat()

    # mode=ro garantit qu'aucune création/migration/écriture SQLite n'a lieu.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT m.match_id, m.match_name, m.match_date,
                   o.market_name, o.selection_name, o.odds, o.suspended,
                   o.collected_at
            FROM matches AS m
            JOIN odds_history AS o ON o.match_id = m.match_id
            WHERE m.sport = 'football'
              AND COALESCE(m.is_live, 0) = 0
              AND m.match_date > ?
            ORDER BY m.match_id, o.market_name, o.selection_name,
                     o.collected_at DESC
            """,
            (now_iso,),
        ).fetchall()

    grouped: dict[tuple, list[sqlite3.Row]] = {}
    for row in rows:
        key = (row["match_id"], row["market_name"], row["selection_name"])
        grouped.setdefault(key, []).append(row)

    changes = []
    for key, history in grouped.items():
        # Plusieurs lignes d'une même collecte ne comptent que comme un snapshot.
        snapshots = {}
        for row in history:
            snapshots.setdefault(row["collected_at"], row)
        stamps = sorted(snapshots, reverse=True)
        if len(stamps) < 2:
            continue
        latest = snapshots[stamps[0]]
        previous = snapshots[stamps[1]]
        # Un changement historique n'est pas un signal de rafraichissement actuel.
        # Rejeter aussi les horodatages futurs (horloge/source incoherente).
        try:
            latest_dt = datetime.fromisoformat(str(stamps[0]).replace("Z", "+00:00"))
            if latest_dt.tzinfo is None:
                latest_dt = latest_dt.replace(tzinfo=timezone.utc)
            latest_dt = latest_dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        age_minutes = (now.astimezone(timezone.utc) - latest_dt).total_seconds() / 60
        if age_minutes < 0 or age_minutes > MAX_SNAPSHOT_AGE_MINUTES:
            continue
        try:
            old_odds = float(previous["odds"])
            new_odds = float(latest["odds"])
        except (TypeError, ValueError):
            continue
        old_suspended = int(previous["suspended"] or 0)
        new_suspended = int(latest["suspended"] or 0)
        odds_changed = old_odds != new_odds
        suspension_changed = old_suspended != new_suspended
        if not odds_changed and not suspension_changed:
            continue

        pct = ((new_odds - old_odds) / old_odds * 100) if old_odds > 0 else None
        kinds = []
        if odds_changed:
            kinds.append("ODDS_CHANGED")
        if suspension_changed:
            kinds.append("SUSPENSION_CHANGED")
        changes.append({
            "match_id": latest["match_id"],
            "match_name": latest["match_name"],
            "match_date": latest["match_date"],
            "market_name": latest["market_name"],
            "selection_name": latest["selection_name"],
            "previous_odds": old_odds,
            "latest_odds": new_odds,
            "odds_change_pct": round(pct, 4) if pct is not None else "",
            "previous_suspended": old_suspended,
            "latest_suspended": new_suspended,
            "previous_snapshot_at": stamps[1],
            "latest_snapshot_at": stamps[0],
            "change_type": "+".join(kinds),
        })
    return sorted(changes, key=lambda r: (r["match_date"], r["match_id"], r["market_name"], r["selection_name"]))


def write_report(changes: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(changes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche les changements sans ecrire le CSV.")
    args = parser.parse_args()

    if not args.db.is_file():
        parser.error(f"Base absente : {args.db}")
    changes = collect_changes(args.db)
    match_ids = sorted({row["match_id"] for row in changes})
    print(f"CHANGEMENTS DE COTES : {len(changes)}")
    print(f"MATCHS A REEVALUER : {len(match_ids)}")
    for match_id in match_ids:
        sample = next(row for row in changes if row["match_id"] == match_id)
        print(f"- {sample['match_name']} | id={match_id}")
    if args.dry_run:
        print("DRY-RUN : aucun fichier ecrit.")
    else:
        write_report(changes, args.output)
        print(f"RAPPORT : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
