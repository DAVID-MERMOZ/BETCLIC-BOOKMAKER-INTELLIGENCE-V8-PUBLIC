"""Verification non destructive de la couverture football-data.org."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.football-data.org/v4/competitions"


def main():
    token = os.environ.get("FOOTBALL_DATA_API_TOKEN", "").strip()
    print("=== FOOTBALL-DATA PROVIDER PROBE ===")
    print("Date UTC :", datetime.now(timezone.utc).isoformat())
    if not token:
        print("ERREUR : secret FOOTBALL_DATA_API_TOKEN absent ou vide.")
        return 2

    request = urllib.request.Request(
        API_URL,
        headers={"X-Auth-Token": token, "User-Agent": "Betclic-Bookmaker-Intelligence/8.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        print(f"ERREUR API HTTP {exc.code}: {body}")
        return 1
    except Exception as exc:
        print(f"ERREUR RESEAU: {type(exc).__name__}: {exc}")
        return 1

    competitions = payload.get("competitions", [])
    print("COMPETITIONS ACCESSIBLES :", len(competitions))
    for item in competitions:
        area = (item.get("area") or {}).get("name", "Zone inconnue")
        print(
            f"- {item.get('name', 'Sans nom')} | code={item.get('code')} "
            f"| zone={area} | id={item.get('id')}"
        )
    print("STATUT : API ACCESSIBLE")
    print("NOTE : cette verification ne telecharge ni ne modifie la base Betclic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
