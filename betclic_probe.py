"""Collecte paginee des seules rencontres Betclic a venir et de leurs cotes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import sqlite3
import time

from betclic_api import BetclicClient
from database import initialize_database, save_match, save_odds

MAX_WORKERS = 6
MAX_PAGES = 15
PAGE_SIZE = 40
MAX_UPCOMING_MATCHES = 60
DETAIL_RETRIES = 3
DB_PATH = "data/betclic_history.db"


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


def _listing_key(item):
    if getattr(item, "id", None) is not None:
        return str(item.id)
    return (getattr(item, "name", "") or "", getattr(item, "date", "") or "")


def list_upcoming_matches(client, now=None):
    """Parcourt les pages Betclic, dedoublonne, et ne garde que les fixtures futures."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    seen = set()
    upcoming = []
    pages_read = 0
    invalid_dates = 0
    live_skipped = 0
    past_skipped = 0

    for page_number in range(MAX_PAGES):
        offset = page_number * PAGE_SIZE
        result = client.get_matches("football", offset=offset)
        page = result.get("matches") or []
        pages_read += 1
        if not page:
            break

        new_items = []
        for item in page:
            key = _listing_key(item)
            if key in seen:
                continue
            seen.add(key)
            new_items.append(item)

            if getattr(item, "is_live", False):
                live_skipped += 1
                continue
            kickoff = parse_utc(getattr(item, "date", None))
            if kickoff is None:
                invalid_dates += 1
                continue
            if kickoff <= now:
                past_skipped += 1
                continue
            upcoming.append(item)
            if len(upcoming) >= MAX_UPCOMING_MATCHES:
                break

        print(
            f"PAGE {page_number + 1}/{MAX_PAGES} : "
            f"{len(page)} elements, {len(new_items)} nouveaux, "
            f"{len(upcoming)} fixtures futures cumulees"
        )

        if len(upcoming) >= MAX_UPCOMING_MATCHES:
            break
        # Si l'API ignore l'offset ou renvoie une page deja vue, ne pas boucler.
        if not new_items or len(page) < PAGE_SIZE:
            break

    return upcoming, {
        "pages_read": pages_read,
        "unique_listings": len(seen),
        "live_skipped": live_skipped,
        "past_skipped": past_skipped,
        "invalid_dates": invalid_dates,
    }


def fetch_match_detail(match_id):
    """Retry bref des erreurs transitoires; un client independant par tentative."""
    last_error = None
    for attempt in range(DETAIL_RETRIES):
        try:
            client = BetclicClient(locale="fr", timeout=(5, 15))
            return client.get_match(match_id)
        except Exception as error:
            last_error = error
            if attempt + 1 < DETAIL_RETRIES:
                time.sleep(1 + attempt * 2)
    raise last_error


def main():
    print("=== BETCLIC V8.1 - COLLECTE PAGINEE DES FIXTURES FUTURES ===")
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    print("Date UTC :", now.isoformat())

    initialize_database()
    listing_client = BetclicClient(locale="fr", timeout=(5, 15))
    matches, diagnostics = list_upcoming_matches(listing_client, now=now)
    print("DIAGNOSTIC LISTING :", diagnostics)
    print("FIXTURES FUTURES A ENRICHIR :", len(matches))

    if not matches:
        raise RuntimeError(
            "Aucune fixture future Betclic trouvee apres pagination; "
            "ne pas annoncer une collecte de fixtures reussie."
        )

    success = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_match_detail, item.id): item
            for item in matches
            if getattr(item, "id", None) is not None
        }
        for completed, future in enumerate(as_completed(futures), 1):
            listed_match = futures[future]
            try:
                match = future.result()
                kickoff = parse_utc(getattr(match, "date", None))
                if kickoff is None or kickoff <= now or getattr(match, "is_live", False):
                    print(
                        f"[{completed}/{len(futures)}] EXCLU APRES DETAIL : "
                        f"{listed_match.name} | date/live incoherent"
                    )
                    continue
                save_match(match)
                save_odds(match)
                success += 1
                print(
                    f"[{completed}/{len(futures)}] ENREGISTRE : "
                    f"{match.name} | debut={kickoff.isoformat()} | "
                    f"marches={len(match.markets)}"
                )
            except Exception as error:
                errors += 1
                print(
                    f"[{completed}/{len(futures)}] ERREUR : "
                    f"{listed_match.name} | {type(error).__name__}: {error}"
                )

    with sqlite3.connect(DB_PATH) as conn:
        match_count = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE sport='football'"
        ).fetchone()[0]
        future_count = conn.execute(
            "SELECT COUNT(*) FROM matches "
            "WHERE sport='football' AND COALESCE(is_live, 0)=0 AND match_date > ?",
            (now.isoformat(),),
        ).fetchone()[0]
        odds_count = conn.execute("SELECT COUNT(*) FROM odds_history").fetchone()[0]
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]

    elapsed = time.monotonic() - started
    print("INTEGRITE SQLITE :", integrity)
    print("MATCHS FOOTBALL EN BASE :", match_count)
    print("FIXTURES FUTURES EN BASE :", future_count)
    print("ENREGISTREMENTS DE COTES :", odds_count)
    print("MATCHS FUTURS ENRICHIS :", success)
    print("ERREURS DETAIL :", errors)
    print(f"DUREE TOTALE : {elapsed:.1f} secondes")
    print("STATUT :", "SUCCES" if success > 0 and future_count > 0 and integrity == "ok" else "ECHEC")

    if success == 0 or future_count == 0 or integrity != "ok":
        raise RuntimeError(
            "Collecte sans fixture future enrichie ou controle SQLite en echec."
        )


if __name__ == "__main__":
    main()
