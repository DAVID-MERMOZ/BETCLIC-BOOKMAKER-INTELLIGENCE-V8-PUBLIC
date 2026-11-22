"""Audit read-only de l'aptitude des donnees Betclic a l'entrainement."""
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
MIN_LABELED_MATCHES = 200


def parse_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def audit_model_readiness(db_path=DB_PATH, min_labeled_matches=MIN_LABELED_MATCHES):
    """Mesure labels confirmes et snapshots de cotes strictement pre-match."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Base introuvable : {path}")

    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"matches", "odds_history", "validated_match_links"}
        missing = required - tables
        if missing:
            raise RuntimeError("Tables requises absentes : " + ", ".join(sorted(missing)))

        links = conn.execute("""
            SELECT l.betclic_match_id, l.home_score, l.away_score, m.match_date
            FROM validated_match_links l
            JOIN matches m ON m.match_id = l.betclic_match_id
            WHERE l.validation_status = 'CONFIRMED'
            ORDER BY l.betclic_match_id
        """).fetchall()

        odds_by_match = defaultdict(list)
        if links:
            ids = [str(row[0]) for row in links]
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(f"""
                SELECT match_id, market_name, selection_name, odds, suspended, collected_at
                FROM odds_history
                WHERE match_id IN ({placeholders})
                  AND suspended = 0 AND odds > 1
                ORDER BY collected_at
            """, ids).fetchall()
            for row in rows:
                odds_by_match[str(row[0])].append(row[1:])

    outcomes = Counter()
    invalid_labels = 0
    invalid_dates = 0
    conflicting_labels = 0
    matches_with_pre_match_odds = set()
    market_match_counts = Counter()
    market_selection_counts = defaultdict(set)
    pre_match_observations = 0

    # Plusieurs fournisseurs peuvent confirmer le même match Betclic.
    # Une rencontre ne doit contribuer qu'une fois aux labels et à la couverture.
    grouped_links = defaultdict(list)
    for row in links:
        grouped_links[str(row[0])].append(row[1:])

    unique_links = []
    for match_id, rows_for_match in grouped_links.items():
        score_pairs = {(row[0], row[1]) for row in rows_for_match}
        if len(score_pairs) != 1:
            conflicting_labels += 1
            continue
        home_score, away_score = next(iter(score_pairs))
        match_dates = [row[2] for row in rows_for_match if row[2]]
        match_date = match_dates[0] if match_dates else None
        unique_links.append((match_id, home_score, away_score, match_date))

    for match_id, home_score, away_score, match_date in unique_links:
        kickoff = parse_utc(match_date)
        if kickoff is None:
            invalid_dates += 1
            continue
        if not isinstance(home_score, int) or not isinstance(away_score, int) or home_score < 0 or away_score < 0:
            invalid_labels += 1
            continue

        outcome = "HOME" if home_score > away_score else "AWAY" if home_score < away_score else "DRAW"
        outcomes[outcome] += 1

        # Derniere cote disponible par selection AVANT le coup d'envoi.
        latest = {}
        for market, selection, odds, suspended, collected_at in odds_by_match.get(match_id, []):
            observed_at = parse_utc(collected_at)
            if observed_at is None or observed_at >= kickoff:
                continue
            if not market or not selection:
                continue
            key = (market, selection)
            if key not in latest or observed_at > latest[key][0]:
                latest[key] = (observed_at, float(odds))

        by_market = defaultdict(set)
        for (market, selection), (_, odds) in latest.items():
            by_market[market].add(selection)
            pre_match_observations += 1

        usable_markets = [market for market, selections in by_market.items() if len(selections) >= 2]
        if usable_markets:
            matches_with_pre_match_odds.add(match_id)
            for market in usable_markets:
                market_match_counts[market] += 1
                market_selection_counts[market].update(by_market[market])

    labeled_matches = sum(outcomes.values())
    coverage_pct = (len(matches_with_pre_match_odds) / labeled_matches * 100.0) if labeled_matches else 0.0
    ready = labeled_matches >= min_labeled_matches and len(matches_with_pre_match_odds) >= min_labeled_matches

    return {
        "confirmed_links": len(links),
        "unique_confirmed_matches": len(grouped_links),
        "labeled_matches": labeled_matches,
        "invalid_labels": invalid_labels,
        "invalid_dates": invalid_dates,
        "conflicting_labels": conflicting_labels,
        "outcomes": dict(outcomes),
        "matches_with_pre_match_odds": len(matches_with_pre_match_odds),
        "pre_match_coverage_pct": coverage_pct,
        "pre_match_selection_observations": pre_match_observations,
        "markets": {
            market: {"matches": count, "selections": len(market_selection_counts[market])}
            for market, count in sorted(market_match_counts.items())
        },
        "minimum_required_matches": min_labeled_matches,
        "status": "READY_FOR_BASELINE_EXPERIMENTS" if ready else "INSUFFICIENT_DATA",
    }


def main():
    report = audit_model_readiness()
    print("=== AUDIT READINESS DU MODELE - LECTURE SEULE ===")
    for key in ("confirmed_links", "unique_confirmed_matches", "labeled_matches",
                "invalid_labels", "invalid_dates", "conflicting_labels",
                "matches_with_pre_match_odds", "pre_match_coverage_pct",
                "pre_match_selection_observations", "minimum_required_matches", "status"):
        print(f"{key}: {report[key]}")
    print("Repartition des resultats:", report["outcomes"])
    print("Marches pre-match exploitables:")
    for market, stats in report["markets"].items():
        print(f"  {market}: {stats['matches']} matchs; {stats['selections']} selections")
    print("Aucune prediction ni performance de modele n'est calculee par cet audit.")


if __name__ == "__main__":
    main()
