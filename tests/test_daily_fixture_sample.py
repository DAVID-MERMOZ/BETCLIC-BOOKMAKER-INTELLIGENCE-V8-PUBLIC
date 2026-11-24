import unittest
from datetime import datetime, timezone

from daily_fixture_sample import select_daily_fixtures


class DailyFixtureSampleTests(unittest.TestCase):
    def test_selects_only_the_current_douala_calendar_day(self):
        now = datetime(2026, 11, 24, 6, 30, tzinfo=timezone.utc)
        today = {"match_id": "today", "kickoff": datetime(2026, 11, 24, 17, 30, tzinfo=timezone.utc)}
        next_day_local = {"match_id": "next-local-day", "kickoff": datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)}
        later = {"match_id": "later", "kickoff": datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)}

        self.assertEqual(
            [row["match_id"] for row in select_daily_fixtures([today, next_day_local, later], now)],
            ["today"],
        )

    def test_uses_douala_midnight_not_utc_midnight(self):
        # 23:30 UTC le 24 novembre correspond à 00:30 le 25 novembre à Douala.
        now = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)
        same_douala_day = {
            "match_id": "nov-25-local",
            "kickoff": datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc),
        }
        previous_douala_day = {
            "match_id": "nov-24-local",
            "kickoff": datetime(2026, 11, 24, 22, 30, tzinfo=timezone.utc),
        }

        self.assertEqual(
            [row["match_id"] for row in select_daily_fixtures(
                [previous_douala_day, same_douala_day], now
            )],
            ["nov-25-local"],
        )

    def test_empty_or_invalid_fixtures_are_ignored(self):
        now = datetime(2026, 11, 24, 6, 30, tzinfo=timezone.utc)
        rows = [
            {"match_id": "missing"},
            {"match_id": "invalid", "kickoff": "2026-11-24T17:30:00Z"},
        ]
        self.assertEqual(select_daily_fixtures(rows, now), [])

    def test_naive_datetimes_are_consistently_interpreted_as_utc(self):
        now = datetime(2026, 11, 24, 6, 30)
        fixture = {"match_id": "naive", "kickoff": datetime(2026, 11, 24, 17, 30)}
        self.assertEqual(
            [row["match_id"] for row in select_daily_fixtures([fixture], now)],
            ["naive"],
        )


if __name__ == "__main__":
    unittest.main()
