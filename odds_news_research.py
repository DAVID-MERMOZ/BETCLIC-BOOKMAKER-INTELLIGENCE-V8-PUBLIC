"""Recherche web declenchee par un mouvement de cote significatif.

Les resultats sont des pistes documentaires horodatees, jamais une preuve causale.
Les articles trop anciens ou sans date fiable sont exclus avant diffusion.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import re
import xml.etree.ElementTree as ET

ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT = 10.0
MAX_NEWS_AGE_DAYS = 7
MAX_NEWS_FUTURE_HOURS = 24
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


def filter_recent_news_items(items, now=None, max_age_days=MAX_NEWS_AGE_DAYS):
    """Conserve uniquement les articles dates dans la fenetre recente."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    oldest = now - timedelta(days=max_age_days)
    newest = now + timedelta(hours=MAX_NEWS_FUTURE_HOURS)
    recent = []
    for item in items:
        raw = item.get("published_at_utc")
        try:
            published = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if published.tzinfo is None:
                continue
            published = published.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        if oldest <= published <= newest:
            recent.append(item)
    return recent


def research_match_news(match_name, movements, timeout=12, max_items=5, force=False):
    """Cherche des articles recents associes au match et au mouvement observe."""
    triggered = significant_odds_moves(movements)
    checked_at_dt = datetime.now(timezone.utc)
    checked_at = checked_at_dt.isoformat()
    if not triggered and not force:
        return {"status": "NOT_TRIGGERED", "triggered_moves": {},
                "checked_at_utc": checked_at, "items": []}

    query = (
        f'"{match_name}" football (injury OR injured OR lineup OR team news '
        "OR suspension OR unavailable OR motivation OR qualification OR title "
        "OR relegation OR fatigue OR rotation OR manager OR weather OR pitch)"
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
            excerpt = re.sub(r"<[^>]+>", " ", description)
            excerpt = re.sub(r"\s+", " ", excerpt).strip()[:500]
            items.append({"title": title, "source": source, "url": link,
                          "published_at_utc": published_utc, "excerpt": excerpt})
        items = filter_recent_news_items(items, now=checked_at_dt)
        return {
            "status": "RESULTS_FOUND" if items else "NO_RECENT_RESULTS",
            "research_mode": "FORCED_PREMATCH_CONTEXT" if force else "ODDS_MOVEMENT_TRIGGERED",
            "triggered_moves": triggered, "query": query,
            "checked_at_utc": checked_at, "items": items,
        }
    except Exception as exc:
        return {
            "status": "SEARCH_ERROR", "triggered_moves": triggered,
            "query": query, "checked_at_utc": checked_at,
            "error": f"{type(exc).__name__}: {exc}"[:300], "items": [],
        }
