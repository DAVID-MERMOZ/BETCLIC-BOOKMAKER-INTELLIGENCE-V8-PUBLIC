import unittest
from datetime import datetime, timezone

from odds_movement import summarize_1x2_movement


class OddsMovementTests(unittest.TestCase):
    def test_measures_opening_to_latest_without_claiming_cause(self):
        rows = [
            ("Equipe A", 2.0, "2026-11-23T10:00:00Z"),
            ("Nul", 3.0, "2026-11-23T10:00:00Z"),
            ("Equipe B", 4.0, "2026-11-23T10:00:00Z"),
            ("Equipe A", 1.8, "2026-11-23T11:00:00Z"),
            ("Nul", 3.2, "2026-11-23T11:00:00Z"),
            ("Equipe B", 4.5, "2026-11-23T11:00:00Z"),
        ]
        result = summarize_1x2_movement(rows, "Equipe A - Equipe B", datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "MOVEMENT_MEASURED")
        self.assertEqual(result["snapshots_n"], 2)
        self.assertEqual(result["cause_status"], "CAUSE_UNVERIFIED_NO_EVENT_FEED")

    def test_requires_two_complete_snapshots(self):
        rows = [("Equipe A", 2.0, "2026-11-23T10:00:00Z"), ("Nul", 3.0, "2026-11-23T10:00:00Z"), ("Equipe B", 4.0, "2026-11-23T10:00:00Z")]
        result = summarize_1x2_movement(rows, "Equipe A - Equipe B", datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "INSUFFICIENT_TWO_COMPLETE_SNAPSHOTS")


if __name__ == "__main__":
    unittest.main()
