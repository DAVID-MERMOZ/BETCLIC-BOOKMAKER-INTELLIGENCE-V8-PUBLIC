import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from apply_validated_results import apply


class ApplyValidatedResultsTests(unittest.TestCase):
    def test_corrects_seattle_and_exports_confirmed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db, registry, export = root/"test.db", root/"registry.csv", root/"export.csv"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE provider_results (
                    provider TEXT, provider_match_id TEXT, home_score INTEGER,
                    away_score INTEGER, score_json TEXT,
                    PRIMARY KEY(provider, provider_match_id))""")
                conn.execute("INSERT INTO provider_results VALUES ('sportscore','vjxm8ghk4ellr6o',3,5,'{}')")
                conn.execute("INSERT INTO provider_results VALUES ('provider2','abc',2,1,'{}')")
            fields=["candidate_id","betclic_match_id","betclic_match","betclic_date","provider",
                    "provider_match_id","provider_competition","provider_match","provider_date",
                    "score","validation_status","validated_by","validated_at","validation_notes"]
            with registry.open("w",newline="",encoding="utf-8") as f:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
                w.writerow(dict(candidate_id="a",betclic_match_id="1",betclic_match="Seattle",
                    provider="sportscore",provider_match_id="vjxm8ghk4ellr6o",score="1-1",
                    validation_status="CONFIRMED"))
                w.writerow(dict(candidate_id="b",betclic_match_id="2",betclic_match="Other",
                    provider="provider2",provider_match_id="abc",score="2-1",
                    validation_status="CONFIRMED"))
            self.assertEqual(apply(db,registry,export),2)
            with sqlite3.connect(db) as conn:
                score=conn.execute("SELECT home_score,away_score FROM provider_results WHERE provider='sportscore'").fetchone()
                audit=conn.execute("SELECT original_home_score,original_away_score FROM provider_result_corrections").fetchone()
            self.assertEqual(score,(1,1)); self.assertEqual(audit,(3,5))
            with export.open(newline="",encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))),2)

    def test_refuses_mismatch_instead_of_exporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); db=root/"x.db"; registry=root/"r.csv"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE provider_results (provider TEXT,provider_match_id TEXT,home_score INTEGER,away_score INTEGER,score_json TEXT)")
                conn.execute("INSERT INTO provider_results VALUES ('sportscore','vjxm8ghk4ellr6o',3,5,'{}')")
            with registry.open("w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=["provider","provider_match_id","score","validation_status"]);w.writeheader()
                w.writerow({"provider":"sportscore","provider_match_id":"vjxm8ghk4ellr6o","score":"0-0","validation_status":"CONFIRMED"})
            with self.assertRaises(RuntimeError): apply(db,registry,root/"out.csv")


    def test_creates_auditable_links_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db, registry, export = root/"db.sqlite", root/"registry.csv", root/"export.csv"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE provider_results (
                    provider TEXT, provider_match_id TEXT, home_score INTEGER,
                    away_score INTEGER, score_json TEXT,
                    PRIMARY KEY(provider, provider_match_id))""")
                conn.executemany("INSERT INTO provider_results VALUES (?, ?, ?, ?, '{}')", [
                    ("provider_a", "a1", 2, 0), ("provider_b", "b1", 2, 0)
                ])
            fields = ["candidate_id", "betclic_match_id", "betclic_match", "betclic_date",
                      "provider", "provider_match_id", "provider_competition", "provider_match",
                      "provider_date", "score", "validation_status", "validated_by",
                      "validated_at", "validation_notes"]
            with registry.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
                for cid, provider, pid in [("c1", "provider_a", "a1"), ("c2", "provider_b", "b1")]:
                    w.writerow({"candidate_id": cid, "betclic_match_id": "betclic-1",
                                "betclic_match": "Home - Away", "provider": provider,
                                "provider_match_id": pid, "score": "2-0",
                                "validation_status": "CONFIRMED", "validated_by": "human"})
            self.assertEqual(apply(db, registry, export), 2)
            self.assertEqual(apply(db, registry, export), 2)
            with sqlite3.connect(db) as conn:
                rows = conn.execute("""SELECT candidate_id, betclic_match_id, provider,
                    provider_match_id, home_score, away_score, validation_status
                    FROM validated_match_links ORDER BY candidate_id""").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], ("c1", "betclic-1", "provider_a", "a1", 2, 0, "CONFIRMED"))
            self.assertEqual(rows[1], ("c2", "betclic-1", "provider_b", "b1", 2, 0, "CONFIRMED"))

    def test_refuses_one_provider_result_linked_to_two_betclic_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db, registry = root/"db.sqlite", root/"registry.csv"
            with sqlite3.connect(db) as conn:
                conn.execute("""CREATE TABLE provider_results (
                    provider TEXT, provider_match_id TEXT, home_score INTEGER,
                    away_score INTEGER, score_json TEXT,
                    PRIMARY KEY(provider, provider_match_id))""")
                conn.execute("INSERT INTO provider_results VALUES ('provider_a', 'same', 1, 0, '{}')")
            fields = ["candidate_id", "betclic_match_id", "provider", "provider_match_id",
                      "score", "validation_status"]
            with registry.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
                w.writerow({"candidate_id": "c1", "betclic_match_id": "betclic-1",
                            "provider": "provider_a", "provider_match_id": "same",
                            "score": "1-0", "validation_status": "CONFIRMED"})
                w.writerow({"candidate_id": "c2", "betclic_match_id": "betclic-2",
                            "provider": "provider_a", "provider_match_id": "same",
                            "score": "1-0", "validation_status": "CONFIRMED"})
            with self.assertRaises(RuntimeError):
                apply(db, registry, root/"export.csv")
            with sqlite3.connect(db) as conn:
                # La transaction annule aussi la creation de la table : aucune trace partielle.
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='validated_match_links'"
                ).fetchone()
                self.assertIsNone(table)


if __name__ == "__main__":
    unittest.main()
