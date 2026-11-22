from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import sqlite3
import time

from betclic_api import BetclicClient
from database import initialize_database, save_match, save_odds

MAX_WORKERS = 4
DB_PATH = "data/betclic_history.db"


def fetch_match_detail(match_id):
    """Use one independent API client per worker; SQLite writes stay on main thread."""
    client = BetclicClient(locale="fr", timeout=(5, 15))
    return client.get_match(match_id)


print("=== BETCLIC V8.1 - COLLECTE CONCURRENTE ===")
started = time.monotonic()
print("Date UTC :", datetime.now(timezone.utc).isoformat())

initialize_database()

listing_client = BetclicClient(locale="fr", timeout=(5, 15))
result = listing_client.get_matches("football")
matches = result["matches"]
print("MATCHS DISPONIBLES :", len(matches))
print("TOTAL ANNONCE :", result.get("total", len(matches)))
print("CONCURRENCE :", MAX_WORKERS, "requêtes simultanées")

success = 0
errors = 0
completed = 0

# Fetch concurrently, but persist sequentially to keep SQLite writes safe.
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {
        executor.submit(fetch_match_detail, item.id): item
        for item in matches
    }

    for future in as_completed(futures):
        listed_match = futures[future]
        completed += 1
        try:
            match = future.result()
            save_match(match)
            save_odds(match)
            success += 1
            print(
                f"[{completed}/{len(matches)}] ENREGISTRE : "
                f"{listed_match.name} | marchés={len(match.markets)}"
            )
        except Exception as error:
            errors += 1
            print(
                f"[{completed}/{len(matches)}] ERREUR : "
                f"{listed_match.name} | {type(error).__name__}: {error}"
            )

print("\\n=== VERIFICATION DE LA BASE SQLITE ===")
with sqlite3.connect(DB_PATH) as conn:
    match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    odds_count = conn.execute("SELECT COUNT(*) FROM odds_history").fetchone()[0]
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]

elapsed = time.monotonic() - started
print("INTEGRITE SQLITE :", integrity)
print("MATCHS DANS LA BASE :", match_count)
print("ENREGISTREMENTS DE COTES :", odds_count)
print("\\n========== BILAN ==========")
print("Matchs disponibles :", len(matches))
print("Matchs enregistrés :", success)
print("Erreurs :", errors)
print(f"Durée totale : {elapsed:.1f} secondes")
print("STATUT :", "SUCCES" if success > 0 and integrity == "ok" else "ECHEC")
print("===========================")

if success == 0 or integrity != "ok":
    raise RuntimeError("Collecte sans résultat exploitable ou contrôle SQLite en échec.")
