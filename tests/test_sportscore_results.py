import unittest

from sportscore_results import value_score


class SportScoreParsingTests(unittest.TestCase):
    def test_nested_full_time_score_parses_both_sides(self):
        match = {"score": {"fullTime": {"home": 2, "away": 1}}}
        self.assertEqual(value_score(match, "home"), 2)
        self.assertEqual(value_score(match, "away"), 1)

    def test_nested_side_score_with_missing_side_does_not_crash(self):
        match = {"score": {"home": {"home": 3}}}
        self.assertEqual(value_score(match, "home"), 3)
        self.assertIsNone(value_score(match, "away"))

    def test_numeric_string_score_is_supported(self):
        match = {"score": {"home": "4", "away": "2"}}
        self.assertEqual(value_score(match, "home"), 4)
        self.assertEqual(value_score(match, "away"), 2)


if __name__ == "__main__":
    unittest.main()
