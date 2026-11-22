"""Collecte SportScore des resultats football, sans liaison automatique Betclic.

API documentee : https://sportscore.com/developers/api/
Attribution requise par le fournisseur : Powered by SportScore.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
BASE_URL = "https://sportscore.com/api/v1/fixtures/"
PROVIDER = "sportscore"


def fetch_day(day, max_attempts=5):
    """Retry les erreurs temporaires du fournisseur sans checkpoint incomplet."""
    query = urllib.parse.urlencode({
        "sport": "football",
        "date": day.isoformat(),
        "status": "finished",
        "limit": 200,
    })
    request = urllib.request.Request(
        f"{BASE_URL}?{query}",
        headers={"User-Agent": "Betclic-Bookmaker-Intelligence/8.1"},
    )
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (408, 425, 429, 500, 502, 503, 504)
            if not retryable or attempt == max_attempts - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(60, max(1, int(retry_after))) if retry_after else min(30, 2 ** attempt)
            except (TypeError, ValueError):
                delay = min(30, 2 ** attempt)
            print(f"SportScore HTTP {exc.code} pour {day}; nouvelle tentative "
                  f"{attempt + 2}/{max_attempts} dans {delay}s")
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == max_attempts - 1:
                raise
            delay = min(30, 2 ** attempt)
            print(f"SportScore reseau indisponible pour {day}; nouvelle tentative "
                  f"{attempt + 2}/{max_attempts} dans {delay}s ({exc})")
            time.sleep(delay)
    matches = payload.get("matches", payload.get("data", []))
    if isinstance(matches, dict):
        matches = matches.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError(f"Format SportScore inattendu pour {day}: {type(matches).__name__}")
    return matches


def value_team(match, side):
    obj = match.get(side) or match.get(f"{side}Team") or {}
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("short_name") or obj.get("displayName")
    return None


def parse_score(value):
    """SportScore serialise parfois les scores en chaînes numériques."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
    return None


def value_score(match, side):
    score = match.get("score")
    if isinstance(score, dict):
        for key in (side, f"{side}_score", "fullTime"):
            val = score.get(key)
            if isinstance(val, dict):
                nested = val
                val = nested.get(side)
                if val is None:
                    val = nested.get("home" if side == "home" else "away")
            parsed = parse_score(val)
            if parsed is not None:
                return parsed
    return parse_score(match.get(f"{side}_score"))


def normalize(match, day):
    home, away = value_team(match, "home"), value_team(match, "away")
    home_score, away_score = value_score(match, "home"), value_score(match, "away")
    status = str(match.get("status") or match.get("status_text") or "").lower()
    match_time = match.get("utcDate") or match.get("utc_date") or match.get("time")
    provider_id = match.get("id") or match.get("match_id") or match.get("slug")
    if not provider_id and match.get("url"):
        # L'API SportScore expose un chemin de match, pas nécessairement un ID dédié.
        provider_id = str(match["url"]).rstrip("/").split("/")[-1]
    if not provider_id or not home or not away or home_score is None or away_score is None:
        return None
    if not any(term in status for term in ("finished", "ft", "full time", "ended")):
        return None
    if not match_time:
        match_time = f"{day.isoformat()}T00:00:00Z"
    competition = match.get("competition") or match.get("league") or {}
    if isinstance(competition, str):
        comp_name, comp_code = competition, None
    else:
        comp_name = competition.get("name") or competition.get("title")
        comp_code = competition.get("code") or competition.get("slug")
    return (
        PROVIDER, str(provider_id), comp_code, comp_name, match_time,
        "FINISHED", home, away, home_score, away_score,
        json.dumps(match, ensure_ascii=False, sort_keys=True),
    )


def ensure_collection_table(db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_collection_days (
                provider TEXT NOT NULL,
                utc_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status = 'COMPLETE'),
                fixtures_seen INTEGER NOT NULL DEFAULT 0,
                results_saved INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (provider, utc_date)
            )
        """)


def collect(days_back=365, db_path=DB_PATH):
    """Backfill resumable: chaque journée validée est persistée et checkpointée."""
    ensure_collection_table(db_path)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days_back)
    rows, api_count, rejected, skipped_days, failed_days = {}, 0, 0, 0, []
    cursor = start
    while cursor <= today:
        day_key = cursor.isoformat()
        with sqlite3.connect(db_path) as conn:
            done = conn.execute(
                "SELECT 1 FROM provider_collection_days WHERE provider=? AND utc_date=? AND status='COMPLETE'",
                (PROVIDER, day_key)
            ).fetchone()
        if done:
            skipped_days += 1
            cursor += timedelta(days=1)
            continue

        try:
            fixtures = fetch_day(cursor)
        except Exception as exc:
            # Ne pas checkpoint une date incomplete; laisser le pipeline continuer.
            failed_days.append(day_key)
            print(f"SportScore indisponible le {day_key}: {type(exc).__name__}: {exc}")
            print("Backfill interrompu proprement; les dates manquantes seront retentees au prochain run.")
            break
        api_count += 1
        day_rows = {}
        day_rejected = 0
        for item in fixtures:
            row = normalize(item, cursor)
            if row:
                day_rows[row[1]] = row
            else:
                day_rejected += 1

        # Les résultats sont persistés avant le checkpoint.
        store(list(day_rows.values()), db_path=db_path)
        fetched_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                INSERT INTO provider_collection_days (
                    provider, utc_date, status, fixtures_seen, results_saved, fetched_at
                ) VALUES (?, ?, 'COMPLETE', ?, ?, ?)
                ON CONFLICT(provider, utc_date) DO UPDATE SET
                    status='COMPLETE', fixtures_seen=excluded.fixtures_seen,
                    results_saved=excluded.results_saved, fetched_at=excluded.fetched_at
            """, (PROVIDER, day_key, len(fixtures), len(day_rows), fetched_at))
        rows.update(day_rows)
        rejected += day_rejected
        print(f"{day_key} : {len(fixtures)} fixtures, {len(day_rows)} scores retenus")
        cursor += timedelta(days=1)
    return list(rows.values()), api_count, rejected, start, today, skipped_days, failed_days


def store(rows, db_path=DB_PATH):
    fetched_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        for row in rows:
            conn.execute("""
                INSERT INTO provider_results (
                    provider, provider_match_id, competition_code, competition_name,
                    utc_date, status, home_team, away_team, home_score, away_score,
                    score_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                    competition_code=excluded.competition_code,
                    competition_name=excluded.competition_name,
                    utc_date=excluded.utc_date, status=excluded.status,
                    home_team=excluded.home_team, away_team=excluded.away_team,
                    home_score=excluded.home_score, away_score=excluded.away_score,
                    score_json=excluded.score_json, fetched_at=excluded.fetched_at
            """, row + (fetched_at,))
        total = conn.execute(
            "SELECT COUNT(*) FROM provider_results WHERE provider=?", (PROVIDER,)
        ).fetchone()[0]
    return total


def main():
    if not DB_PATH.is_file():
        print("ERREUR : base SQLite absente.")
        return 2
    try:
        rows, calls, rejected, start, end, skipped_days, failed_days = collect()
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM provider_results WHERE provider=?", (PROVIDER,)
            ).fetchone()[0]
    except urllib.error.HTTPError as exc:
        print(f"ERREUR SportScore HTTP {exc.code}: "
              f"{exc.read().decode('utf-8', errors='replace')[:500]}")
        return 1
    except Exception as exc:
        print(f"ERREUR SportScore : {type(exc).__name__}: {exc}")
        return 1
    print("FOURNISSEUR : SportScore")
    print("Profondeur historique cible : 365 jours (backfill incremental et reprenable)")
    print(f"Fenetre UTC : {start} -> {end}")
    print("Appels API effectues pendant ce run :", calls)
    print("Jours deja complets ignores :", skipped_days)
    print("Resultats uniques ajoutes pendant ce run :", len(rows))
    print("Lignes ignorees / format incomplet :", rejected)
    print("Jours en echec non checkpointes :", len(failed_days))
    print("Total SportScore en base :", total)
    print("RAPPROCHEMENT BETCLIC : NON EFFECTUE")
    print("Attribution : https://sportscore.com/")
    if failed_days:
        print("STATUT : PARTIEL - reprise au prochain run")
    else:
        print("STATUT : SUCCES")
    # Une panne fournisseur ne bloque pas les autres collectes ni la sauvegarde.
    return 0


if __name__ == "__main__":
    sys.exit(main())
