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


def item(match_id, date, live=False):
    return SimpleNamespace(
        id=match_id, name=f"Team {match_id} - Opponent",
        date=date, is_live=live
    )


class BetclicPaginationTests(unittest.TestCase):
    def test_paginates_deduplicates_and_keeps_only_future_prematch(self):
        now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        first_page = [
            item(1, "2026-11-22T14:00:00Z", True),
            item(2, "2026-11-22T10:00:00Z"),
            item(3, "2026-11-22T15:00:00Z"),
        ] + [item(i, "2026-11-22T09:00:00Z") for i in range(10, 47)]
        second_page = [first_page[2], item(4, "2026-11-23T15:00:00Z")]
        client = FakeClient({0: first_page, 40: second_page})

        upcoming, diagnostic = list_upcoming_matches(client, now=now)

        self.assertEqual([entry.id for entry in upcoming], [3, 4])
        self.assertEqual(client.offsets, [0, 40])
        self.assertEqual(diagnostic["live_skipped"], 1)
        self.assertEqual(diagnostic["past_skipped"], 38)
        self.assertEqual(diagnostic["unique_listings"], 41)

    def test_stops_when_api_repeats_same_page(self):
        now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        first_page = [item(i, "2026-11-22T09:00:00Z") for i in range(40)]
        client = FakeClient({0: first_page, 40: first_page})

        upcoming, diagnostic = list_upcoming_matches(client, now=now)

        self.assertEqual(upcoming, [])
        self.assertEqual(client.offsets, [0, 40])
        self.assertEqual(diagnostic["unique_listings"], 40)


if __name__ == "__main__":
    unittest.main()
