import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from betclic_probe import list_upcoming_matches


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.offsets = []

    def get_matches(self, sport, offset=0):
        self.offsets.append(offset)
        return {"matches": self.pages.get(offset, []), "total": 0}


class BetclicPaginationTests(unittest.TestCase):
    def test_paginates_deduplicates_and_keeps_only_future_prematch(self):
        now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        live = SimpleNamespace(id=1, name="Live - Match", date="2026-11-22T14:00:00Z", is_live=True)
        past = SimpleNamespace(id=2, name="Past - Match", date="2026-11-22T10:00:00Z", is_live=False)
        future_a = SimpleNamespace(id=3, name="Future A - Match", date="2026-11-22T15:00:00Z", is_live=False)
        future_b = SimpleNamespace(id=4, name="Future B - Match", date="2026-11-23T15:00:00Z", is_live=False)
        client = FakeClient({0: [live, past, future_a], 40: [future_a, future_b], 80: []})

        upcoming, diagnostic = list_upcoming_matches(client, now=now)

        self.assertEqual([item.id for item in upcoming], [3, 4])
        self.assertEqual(client.offsets, [0, 40, 80])
        self.assertEqual(diagnostic["live_skipped"], 1)
        self.assertEqual(diagnostic["past_skipped"], 1)
        self.assertEqual(diagnostic["unique_listings"], 4)

    def test_stops_when_api_repeats_same_page(self):
        now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        future = SimpleNamespace(id=9, name="Future - Match", date="2026-11-23T15:00:00Z", is_live=False)
        client = FakeClient({0: [future], 40: [future]})

        upcoming, diagnostic = list_upcoming_matches(client, now=now)

        self.assertEqual([item.id for item in upcoming], [9])
        self.assertEqual(client.offsets, [0, 40])
        self.assertEqual(diagnostic["unique_listings"], 1)


if __name__ == "__main__":
    unittest.main()
