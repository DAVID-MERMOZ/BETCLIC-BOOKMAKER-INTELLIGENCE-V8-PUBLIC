"""Probabilites implicites sans marge a partir du dernier snapshot Betclic.

Ce module ne predit pas encore les resultats : il transforme les cotes
d'un marche en probabilites de marche normalisees (marge retiree).
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("data/betclic_history.db")


def calculate_fair_probabilities(selections):
    """Retourne les probabilites normalisees et la marge d'un marche.

    selections: iterable de tuples (selection_name, odds).
    Les cotes invalides ou <= 1 sont ignorees. Une seule observation par
    selection est attendue dans le snapshot fourni.
    """
    valid = []
    seen = set()
    for name, odds in selections:
        try:
            value = float(odds)
        except (TypeError, ValueError):
            continue
        if not name or name in seen or value <= 1:
            continue
        seen.add(name)
        valid.append((str(name), value))

    if len(valid) < 2:
        return None

    implied = [(name, 1.0 / odds) for name, odds in valid]
    overround = sum(prob for _, prob in implied)
    if overround <= 0:
        return None

    return {
        "overround": overround,
        "bookmaker_margin_pct": (overround - 1.0) * 100.0,
        "probabilities": [
            {
                "selection": name,
                "odds": odds,
                "implied_probability": 1.0 / odds,
                "fair_probability": (1.0 / odds) / overround,
            }
            for (name, odds), (_, _) in zip(valid, implied)
        ],
    }


def latest_market_snapshots(db_path=DB_PATH):
    """Lit les dernieres cotes valides par match et marche, en lecture seule."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Base introuvable : {path}")

    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("""
            SELECT o.match_id, m.match_name, o.market_name,
                   o.selection_name, o.odds, o.collected_at
            FROM odds_history o
            LEFT JOIN matches m ON m.match_id = o.match_id
            JOIN (
                SELECT match_id, market_name, MAX(collected_at) AS latest_at
                FROM odds_history
                WHERE suspended = 0 AND odds > 1
                GROUP BY match_id, market_name
            ) latest
              ON latest.match_id = o.match_id
             AND latest.market_name IS o.market_name
             AND latest.latest_at = o.collected_at
            WHERE o.suspended = 0 AND o.odds > 1
            ORDER BY o.match_id, o.market_name, o.selection_name
        """).fetchall()

    grouped = {}
    for match_id, match_name, market, selection, odds, collected_at in rows:
        key = (match_id, market, collected_at)
        grouped.setdefault(key, {
            "match_id": match_id,
            "match_name": match_name or str(match_id),
            "market": market,
            "collected_at": collected_at,
            "selections": [],
        })["selections"].append((selection, odds))

    output = []
    for snapshot in grouped.values():
        result = calculate_fair_probabilities(snapshot["selections"])
        if result is None:
            continue
        snapshot.update(result)
        output.append(snapshot)
    return output


def main():
    snapshots = latest_market_snapshots()
    print("=== BETCLIC V8.1 - PROBABILITES DE MARCHE ===")
    print("Date UTC :", datetime.now(timezone.utc).isoformat())
    print("Marches exploitables :", len(snapshots))
    for item in snapshots[:30]:
        print(
            f"\n{item['match_name']} | {item['market']} "
            f"| {item['collected_at']}"
        )
        print(f"Marge bookmaker estimee : {item['bookmaker_margin_pct']:.2f}%")
        for selection in sorted(
            item["probabilities"],
            key=lambda row: row["fair_probability"],
            reverse=True,
        ):
            print(
                f"  {selection['selection']}: "
                f"{selection['fair_probability'] * 100:.2f}% "
                f"(cote {selection['odds']})"
            )
    print("\nNote : probabilites de marche, pas encore des predictions du modele.")


if __name__ == "__main__":
    main()
