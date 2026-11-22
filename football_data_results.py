"""Collecte des scores football-data.org, sans rapprochement automatique Betclic."""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
BASE_URL = "https://api.football-data.org/v4/matches"
PROVIDER = "football-data.org"


def retry_delay_seconds(exc):
    """Respecte Retry-After ou le delai indique dans le corps d'une reponse 429."""
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return max(1.0, float(header))
        except (TypeError, ValueError):
            pass
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    match = re.search(r"wait\\s+(\\d+)\\s+seconds?", body, re.IGNORECASE)
    if match:
        return max(1.0, float(match.group(1)))
    return 60.0


def open_json_with_retry(request, max_retries=5):
    """Reessaie les limites temporaires sans masquer les erreurs definitives."""
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt >= max_retries:
                raise
            delay = retry_delay_seconds(exc)
            print(f"Limite API HTTP 429 : pause de {delay:.0f}s "
                  f"(tentative {attempt + 1}/{max_retries})")
            time.sleep(delay)


def fetch_finished_matches(token, days_back=180, db_path=DB_PATH):
    """Telecharge par tranches de 10 jours, limite le debit et sauvegarde chaque tranche."""
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days_back)
    end_date = today + timedelta(days=1)  # borne exclusive
    all_matches = {}
    cursor = start_date
    windows = 0
    last_request_at = None

    while cursor < end_date:
        next_date = min(cursor + timedelta(days=10), end_date)
        date_from = cursor.isoformat()
        date_to = next_date.isoformat()
        query = urllib.parse.urlencode({
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": 500,
        })
        request = urllib.request.Request(
            f"{BASE_URL}?{query}",
            headers={
                "X-Auth-Token": token,
                "User-Agent": "Betclic-Bookmaker-Intelligence/8.1",
            },
        )
        # Le forfait API peut limiter le nombre de requetes par minute.
        if last_request_at is not None:
            remaining = 7.0 - (time.monotonic() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        last_request_at = time.monotonic()
        payload = open_json_with_retry(request)

        window_matches = payload.get("matches", [])
        # Sauvegarde immediate : une interruption ne fait pas perdre les fenetres deja lues.
        window_saved, window_skipped = store_finished_matches(window_matches, db_path)
        print(f"Sauvegarde intermediaire : {window_saved} resultats, "
              f"{window_skipped} ignores")

        for match in window_matches:
            # La cle API evite tout doublon si deux fenetres se chevauchent.
            match_id = match.get("id")
            if match_id is not None:
                all_matches[str(match_id)] = match

        windows += 1
        print(f"Fenetre {windows} : {date_from} -> {date_to} (exclusive), "
              f"{len(window_matches)} matchs")
        cursor = next_date

    return list(all_matches.values()), start_date.isoformat(), end_date.isoformat()


def store_finished_matches(matches, db_path=DB_PATH):
    fetched_at = datetime.now(timezone.utc).isoformat()
    saved = 0
    skipped = 0
    with sqlite3.connect(db_path) as conn:
        for match in matches:
            if match.get("status") != "FINISHED":
                skipped += 1
                continue
            home = (match.get("homeTeam") or {}).get("name")
            away = (match.get("awayTeam") or {}).get("name")
            score = match.get("score") or {}
            full_time = score.get("fullTime") or {}
            home_score, away_score = full_time.get("home"), full_time.get("away")
            if not home or not away or not isinstance(home_score, int) or not isinstance(away_score, int):
                skipped += 1
                continue
            competition = match.get("competition") or {}
            conn.execute("""
                INSERT INTO provider_results (
                    provider, provider_match_id, competition_code, competition_name,
                    utc_date, status, home_team, away_team, home_score, away_score,
                    score_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                    competition_code=excluded.competition_code,
                    competition_name=excluded.competition_name,
                    utc_date=excluded.utc_date,
                    status=excluded.status,
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    home_score=excluded.home_score,
                    away_score=excluded.away_score,
                    score_json=excluded.score_json,
                    fetched_at=excluded.fetched_at
            """, (
                PROVIDER, str(match["id"]), competition.get("code"),
                competition.get("name"), match.get("utcDate"), match["status"],
                home, away, home_score, away_score,
                json.dumps(score, ensure_ascii=False, sort_keys=True), fetched_at,
            ))
            saved += 1
    return saved, skipped


def main():
    print("=== COLLECTEUR DES RESULTATS FOOTBALL-DATA ===")
    print("Profondeur historique demandee : 180 jours")
    token = os.environ.get("FOOTBALL_DATA_API_TOKEN", "").strip()
    if not token:
        print("ERREUR : secret FOOTBALL_DATA_API_TOKEN absent.")
        return 2
    if not DB_PATH.is_file():
        print("ERREUR : base Betclic absente ; initialiser la base avant la collecte.")
        return 2
    try:
        matches, date_from, date_to = fetch_finished_matches(token)
        saved, skipped = store_finished_matches(matches)
    except urllib.error.HTTPError as exc:
        print(f"ERREUR API HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:800]}")
        return 1
    except Exception as exc:
        print(f"ERREUR : {type(exc).__name__}: {exc}")
        return 1
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM provider_results WHERE provider = ?", (PROVIDER,)
        ).fetchone()[0]
    print(f"Fenetre UTC : {date_from} a {date_to} (borne finale exclusive)")
    print("Matchs retournes par API :", len(matches))
    print("Resultats termines enregistres/mis a jour :", saved)
    print("Matchs ignores (non termines ou score incomplet) :", skipped)
    print("Total resultats fournisseur en base :", total)
    print("RAPPROCHEMENT BETCLIC : NON EFFECTUE (securite)")
    print("STATUT :", "SUCCES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
