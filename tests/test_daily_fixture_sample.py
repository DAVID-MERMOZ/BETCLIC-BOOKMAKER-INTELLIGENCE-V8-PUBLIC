import unittest
from datetime import datetime, timezone

from daily_fixture_sample import extract_market_snapshot, select_daily_fixtures


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

    def test_exports_the_market_probabilities_and_snapshot_age_used_by_the_model(self):
        now = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
        snapshot_at = datetime(2026, 11, 24, 7, 30, tzinfo=timezone.utc)
        fixture = {
            "market_snapshot": (
                snapshot_at,
                "Résultat du match (t. rég)",
                {"HOME": 0.50, "DRAW": 0.30, "AWAY": 0.20},
            )
        }

        fields, probabilities = extract_market_snapshot(fixture, now)

        self.assertEqual(fields["market_status"], "AVAILABLE")
        self.assertEqual(fields["market_snapshot_utc"], snapshot_at.isoformat())
        self.assertEqual(fields["market_snapshot_age_minutes"], 30.0)
        self.assertEqual(fields["market_prob_home"], 0.5)
        self.assertEqual(fields["market_prob_draw"], 0.3)
        self.assertEqual(fields["market_prob_away"], 0.2)
        self.assertEqual(probabilities, {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2})

    def test_stale_market_snapshot_is_not_used_for_comparison(self):
        now = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
        snapshot_at = datetime(2026, 11, 25, 5, 59, tzinfo=timezone.utc)
        fixture = {
            "market_snapshot": (
                snapshot_at,
                "Résultat du match (t. rég)",
                {"HOME": 0.50, "DRAW": 0.30, "AWAY": 0.20},
            )
        }

        fields, probabilities = extract_market_snapshot(fixture, now)

        self.assertEqual(fields["market_status"], "STALE_PREMATCH_1X2_SNAPSHOT")
        self.assertEqual(fields["market_snapshot_age_minutes"], 121.0)
        self.assertEqual(fields["market_snapshot_utc"], snapshot_at.isoformat())
        self.assertEqual(fields["market_prob_home"], "")
        self.assertIsNone(probabilities)

    def test_missing_or_invalid_market_snapshot_is_never_marked_available(self):
        missing, missing_probs = extract_market_snapshot({}, datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc))
        invalid, invalid_probs = extract_market_snapshot(
            {"market_snapshot": (
                datetime(2026, 11, 24, 7, 30, tzinfo=timezone.utc),
                "Résultat du match",
                {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.3},
            )},
            datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(missing["market_status"], "NO_COMPLETE_PREMATCH_1X2")
        self.assertIsNone(missing_probs)
        self.assertEqual(invalid["market_status"], "INVALID_PREMATCH_1X2_SNAPSHOT")
        self.assertIsNone(invalid_probs)


if __name__ == "__main__":
    unittest.main()
