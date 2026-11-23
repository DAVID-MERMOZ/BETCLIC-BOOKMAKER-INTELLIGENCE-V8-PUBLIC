import unittest
from odds_news_research import (
    ODDS_MOVE_WEB_SEARCH_THRESHOLD_PCT,
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


if __name__ == "__main__":
    unittest.main()
