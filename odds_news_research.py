"""Recherche web declenchee par un mouvement de cote significatif.

Les resultats sont des pistes documentaires horodatees, jamais une preuve causale.
La recherche utilise le flux RSS Google News (aucune cle API requise).
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT = 10.0
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"


def significant_odds_moves(odds_change_pct, threshold=ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT):
    """Retourne les selections dont la variation absolue de cote atteint le seuil."""
    found = {}
    for selection, change in (odds_change_pct or {}).items():
        try:
            value = float(change)
        except (TypeError, ValueError):
            continue
        if abs(value) >= float(threshold):
            found[str(selection)] = round(value, 3)
    return found


def research_match_news(match_name, movements, timeout=12, max_items=5):
    """Cherche des articles recents associes au match et au mouvement observe."""
    triggered = significant_odds_moves(movements)
    checked_at = datetime.now(timezone.utc).isoformat()
    if not triggered:
        return {"status": "NOT_TRIGGERED", "triggered_moves": {},
                "checked_at_utc": checked_at, "items": []}

    query = (
        f'"{match_name}" football (injury OR injured OR lineup OR team news '
        "OR suspension OR postponed OR manager OR weather)"
    )
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 BETCLIC-V8.1 research"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(1_500_000)
        root = ET.fromstring(payload)
        items = []
        for node in root.findall("./channel/item")[:max_items]:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            description = (node.findtext("description") or "").strip()
            published = (node.findtext("pubDate") or "").strip()
            source_node = node.find("source")
            source = (source_node.text or "").strip() if source_node is not None else ""
            if not title or not link:
                continue
            try:
                published_utc = parsedate_to_datetime(published).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                published_utc = ""
            # Description from RSS may contain HTML; preserve a compact plain-text excerpt.
            import re
            excerpt = re.sub(r"<[^>]+>", " ", description)
            excerpt = re.sub(r"\s+", " ", excerpt).strip()[:500]
            items.append({"title": title, "source": source, "url": link,
                          "published_at_utc": published_utc, "excerpt": excerpt})
        return {
            "status": "RESULTS_FOUND" if items else "NO_RESULTS_FOUND",
            "triggered_moves": triggered, "query": query,
            "checked_at_utc": checked_at, "items": items,
        }
    except Exception as exc:
        return {
            "status": "SEARCH_ERROR", "triggered_moves": triggered,
            "query": query, "checked_at_utc": checked_at,
            "error": f"{type(exc).__name__}: {exc}"[:300], "items": [],
        }
