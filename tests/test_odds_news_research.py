import unittest
from datetime import datetime, timezone, timedelta
from odds_news_research import (
    ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT,
    filter_recent_news_items,
    research_match_news,
    significant_odds_moves,
)


class OddsNewsResearchTests(unittest.TestCase):
    def test_threshold_is_ten_percent_and_inclusive(self):
        self.assertEqual(ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT, 10.0)
        moves = significant_odds_moves({
            "HOME": 9.99, "DRAW": -10.0, "AWAY": 12.5, "INVALID": "n/a"
        })
        self.assertEqual(moves, {"DRAW": -10.0, "AWAY": 12.5})

    def test_no_search_trigger_without_significant_movement(self):
        result = research_match_news("Team A - Team B", {"HOME": 9.9, "AWAY": -3})
        self.assertEqual(result["status"], "NOT_TRIGGERED")
        self.assertEqual(result["items"], [])

    def test_empty_movement_is_not_triggered(self):
        self.assertEqual(significant_odds_moves({}), {})

    def test_old_or_undated_articles_are_excluded(self):
        now = datetime(2026, 11, 23, 18, tzinfo=timezone.utc)
        items = [
            {"title": "recent", "published_at_utc": (now - timedelta(days=2)).isoformat()},
            {"title": "stale", "published_at_utc": (now - timedelta(days=30)).isoformat()},
            {"title": "undated", "published_at_utc": ""},
            {"title": "naive", "published_at_utc": "2026-11-23T10:00:00"},
            {"title": "future", "published_at_utc": (now + timedelta(days=3)).isoformat()},
        ]
        result = filter_recent_news_items(items, now=now)
        self.assertEqual([item["title"] for item in result], ["recent"])

    def test_recent_window_is_inclusive_at_seven_days(self):
        now = datetime(2026, 11, 23, 18, tzinfo=timezone.utc)
        item = {"published_at_utc": (now - timedelta(days=7)).isoformat()}
        self.assertEqual(filter_recent_news_items([item], now=now), [item])


if __name__ == "__main__":
    unittest.main()
