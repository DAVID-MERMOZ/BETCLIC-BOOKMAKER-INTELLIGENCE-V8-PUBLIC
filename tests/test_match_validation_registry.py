import csv
import tempfile
import unittest
from pathlib import Path
from match_validation_registry import build_registry, candidate_id


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "review.csv"
        self.registry = self.root / "registry.csv"
        self.fields = ["betclic_match_id", "betclic_match", "betclic_date",
                       "provider", "provider_match_id", "provider_competition",
                       "provider_match", "provider_date", "score",
                       "home_similarity", "away_similarity", "date_delta_hours",
                       "candidate_score", "rank", "decision"]

    def tearDown(self):
        self.tmp.cleanup()

    def write_source(self, rows):
        with self.source.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.fields)
            w.writeheader()
            w.writerows(rows)

    def read_registry(self):
        with self.registry.open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def row(self, provider="sportscore", pid="match-1"):
        return dict(betclic_match_id="bet-1", betclic_match="A - B",
                    betclic_date="2026-11-20T12:00:00Z", provider=provider,
                    provider_match_id=pid, provider_competition="League",
                    provider_match="A - B", provider_date="2026-11-20T12:00:00Z",
                    score="1-0", home_similarity="1", away_similarity="1",
                    date_delta_hours="0", candidate_score="1", rank="1",
                    decision="CANDIDAT_TRES_FORT_A_VALIDER")

    def test_new_candidates_start_pending(self):
        self.write_source([self.row()])
        self.assertEqual(build_registry(self.source, self.registry, "101"), 1)
        row = self.read_registry()[0]
        self.assertEqual(row["validation_status"], "PENDING")
        self.assertEqual(row["source_run_id"], "101")

    def test_manual_decision_survives_refresh(self):
        self.write_source([self.row()])
        build_registry(self.source, self.registry, "101")
        rows = self.read_registry()
        rows[0].update(validation_status="CONFIRMED", validated_by="analyst",
                       validated_at="2026-11-21T10:00:00Z",
                       validation_notes="Verified against independent source")
        with self.registry.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        self.write_source([self.row()])
        build_registry(self.source, self.registry, "102")
        row = self.read_registry()[0]
        self.assertEqual(row["validation_status"], "CONFIRMED")
        self.assertEqual(row["validated_by"], "analyst")
        self.assertEqual(row["validation_notes"], "Verified against independent source")
        self.assertEqual(row["source_run_id"], "102")

    def test_refresh_is_idempotent_and_keeps_multiple_providers(self):
        self.write_source([self.row(), self.row("football-data.org", "fd-2")])
        build_registry(self.source, self.registry, "1")
        build_registry(self.source, self.registry, "2")
        rows = self.read_registry()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["candidate_id"] for r in rows}), 2)

    def test_old_candidates_are_not_deleted_when_missing_from_new_report(self):
        self.write_source([self.row()])
        build_registry(self.source, self.registry, "1")
        self.write_source([])
        build_registry(self.source, self.registry, "2")
        self.assertEqual(len(self.read_registry()), 1)

    def test_candidate_id_is_stable(self):
        self.assertEqual(candidate_id(self.row()), candidate_id(self.row()))


if __name__ == "__main__":
    unittest.main()
