import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH = Path("data/betclic_history.db")

# PARAMETRES V3
SIGNIFICANT_MOVEMENT = 5.0
RAPID_MOVEMENT = 3.0
RAPID_WINDOW_MINUTES = 60
REPEATED_MOVEMENT = 1.0

ANALYSIS_WINDOW_HOURS = 24
MAX_MATCHES = 10


def get_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Base introuvable : {DB_PATH}")

    return sqlite3.connect(DB_PATH)


def initialize_alerts_table():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                match_name TEXT,
                market_name TEXT,
                selection_name TEXT,
                alert_type TEXT,
                direction TEXT,
                variation REAL,
                observed_at TEXT NOT NULL,
                UNIQUE (
                    match_id,
                    market_name,
                    selection_name,
                    alert_type,
                    observed_at
                )
            )
        """)


def parse_date(value):
    parsed = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def select_upcoming_matches(limit=MAX_MATCHES):
    """Ne retient que les matchs futurs non signalés comme étant en direct."""
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT match_id, match_name, match_date, is_live
            FROM matches
        """).fetchall()

    upcoming = []
    invalid_dates = 0
    live_skipped = 0
    past_skipped = 0

    for match_id, match_name, match_date, is_live in rows:
        if bool(is_live):
            live_skipped += 1
            continue
        try:
            kickoff = parse_date(match_date)
        except (ValueError, TypeError, AttributeError):
            invalid_dates += 1
            continue
        if kickoff <= now:
            past_skipped += 1
            continue
        upcoming.append((match_id, match_name, kickoff))

    upcoming.sort(key=lambda row: row[2])
    return upcoming[:limit], {
        "total": len(rows),
        "upcoming": len(upcoming),
        "live_skipped": live_skipped,
        "past_skipped": past_skipped,
        "invalid_dates": invalid_dates,
    }


def get_odds_history(match_id, market_name):
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=ANALYSIS_WINDOW_HOURS
    )

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT selection_name, odds, collected_at
            FROM odds_history
            WHERE match_id = ?
              AND market_name = ?
              AND odds > 1
              AND suspended = 0
            ORDER BY collected_at ASC, id ASC
        """, (str(match_id), market_name)).fetchall()

    history = {}

    for selection, odds, collected_at in rows:
        try:
            date = parse_date(collected_at)
        except (ValueError, TypeError, AttributeError):
            continue

        if date < cutoff:
            continue

        history.setdefault(selection, []).append({
            "odds": float(odds),
            "date": date,
            "date_text": collected_at
        })

    return history


def analyze_market(match_id, match_name, market_name):
    history = get_odds_history(match_id, market_name)
    alerts = []

    for selection, observations in history.items():

        if len(observations) < 2:
            continue

        first = observations[0]
        latest = observations[-1]

        variation = (
            (latest["odds"] - first["odds"])
            / first["odds"]
        ) * 100

        # 1. MOUVEMENT IMPORTANT SUR 24 HEURES
        if abs(variation) >= SIGNIFICANT_MOVEMENT:
            alerts.append({
                "match_id": str(match_id),
                "match": match_name,
                "market": market_name,
                "selection": selection,
                "type": "MOUVEMENT IMPORTANT",
                "direction": "BAISSE" if variation < 0 else "HAUSSE",
                "variation": variation,
                "observed_at": latest["date_text"]
            })

        # 2. MOUVEMENT RAPIDE SUR 60 MINUTES
        previous = observations[-2]

        elapsed = (
            latest["date"] - previous["date"]
        ).total_seconds() / 60

        if 0 <= elapsed <= RAPID_WINDOW_MINUTES:

            rapid_variation = (
                (latest["odds"] - previous["odds"])
                / previous["odds"]
            ) * 100

            if abs(rapid_variation) >= RAPID_MOVEMENT:
                alerts.append({
                    "match_id": str(match_id),
                    "match": match_name,
                    "market": market_name,
                    "selection": selection,
                    "type": "MOUVEMENT RAPIDE",
                    "direction": (
                        "BAISSE" if rapid_variation < 0 else "HAUSSE"
                    ),
                    "variation": rapid_variation,
                    "observed_at": latest["date_text"]
                })

        # 3. MOUVEMENT REPETE
        if len(observations) >= 4:

            recent = observations[-4:]
            directions = []

            for index in range(1, 4):

                old_odds = recent[index - 1]["odds"]
                new_odds = recent[index]["odds"]

                change = (
                    (new_odds - old_odds) / old_odds
                ) * 100

                if abs(change) >= REPEATED_MOVEMENT:
                    directions.append(
                        "BAISSE" if change < 0 else "HAUSSE"
                    )
                else:
                    directions.append("STABLE")

            if (
                directions[0] == directions[1] == directions[2]
                and directions[0] != "STABLE"
            ):
                alerts.append({
                    "match_id": str(match_id),
                    "match": match_name,
                    "market": market_name,
                    "selection": selection,
                    "type": "MOUVEMENT REPETE",
                    "direction": directions[0],
                    "variation": variation,
                    "observed_at": latest["date_text"]
                })

    return alerts


def save_alerts(alerts):
    saved = 0

    with get_connection() as conn:
        for alert in alerts:

            cursor = conn.execute("""
                INSERT OR IGNORE INTO odds_alerts (
                    match_id,
                    match_name,
                    market_name,
                    selection_name,
                    alert_type,
                    direction,
                    variation,
                    observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert["match_id"],
                alert["match"],
                alert["market"],
                alert["selection"],
                alert["type"],
                alert["direction"],
                alert["variation"],
                alert["observed_at"]
            ))

            saved += cursor.rowcount

    return saved


def show_recent_alerts(limit=20):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                match_name,
                market_name,
                selection_name,
                alert_type,
                direction,
                variation,
                observed_at
            FROM odds_alerts
            ORDER BY observed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

    print("\n========== HISTORIQUE DES ALERTES ==========")

    if not rows:
        print("Aucune alerte enregistrée.")
        return

    for row in rows:
        name, market, selection, alert_type, direction, variation, date = row

        print("\nMatch :", name)
        print("Marché :", market)
        print("Sélection :", selection)
        print("Type :", alert_type)
        print("Direction :", direction)
        print("Variation :", f"{variation:+.2f}%")
        print("Date :", date)


def main():
    print("======================================")
    print(" BETCLIC V8.1 - ODDS INTELLIGENCE V3")
    print("======================================")
    print("Date UTC :", datetime.now(timezone.utc).isoformat())
    print("Fenêtre d'analyse :", ANALYSIS_WINDOW_HOURS, "heures")
    print("Nombre maximal de matchs :", MAX_MATCHES)

    initialize_alerts_table()

    matches, selection_stats = select_upcoming_matches()

    print("\nSélection des matchs (pré-match uniquement)")
    print("Matchs dans la base :", selection_stats["total"])
    print("Matchs futurs éligibles :", selection_stats["upcoming"])
    print("Matchs en direct exclus :", selection_stats["live_skipped"])
    print("Matchs déjà commencés/terminés exclus :", selection_stats["past_skipped"])
    print("Dates illisibles exclues :", selection_stats["invalid_dates"])
    print("Matchs retenus pour analyse :", len(matches))

    all_alerts = []

    for match_id, match_name, kickoff in matches:

        print("\nAnalyse pré-match :", match_name, "| Début UTC :", kickoff.isoformat())

        with get_connection() as conn:
            markets = conn.execute("""
                SELECT DISTINCT market_name
                FROM odds_history
                WHERE match_id = ?
            """, (str(match_id),)).fetchall()

        for (market_name,) in markets:
            alerts = analyze_market(
                match_id,
                match_name,
                market_name
            )
            all_alerts.extend(alerts)

    saved = save_alerts(all_alerts)

    all_alerts.sort(
        key=lambda alert: abs(alert["variation"]),
        reverse=True
    )

    print("\n========== CLASSEMENT V3 ==========")

    if not all_alerts:
        print("Aucun mouvement ne dépasse les seuils définis.")

    for alert in all_alerts[:20]:
        print(
            f"\n{alert['match']} | "
            f"{alert['selection']} | "
            f"{alert['type']} | "
            f"{alert['direction']} | "
            f"{alert['variation']:+.2f}%"
        )

    print("\nAlertes détectées :", len(all_alerts))
    print("Nouvelles alertes enregistrées :", saved)

    show_recent_alerts()

    print("\n========== ANALYSE V3 TERMINEE ==========")


if __name__ == "__main__":
    main()
