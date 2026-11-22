"""Applique les validations humaines, conserve les corrections et matérialise les liens validés."""
import csv
import json
import sqlite3
from pathlib import Path

DB = Path("data/betclic_history.db")
REGISTRY = Path("reports/match_validation_registry.csv")
EXPORT = Path("reports/validated_match_results.csv")
SEATTLE_PROVIDER_ID = "vjxm8ghk4ellr6o"
SEATTLE_PROVIDER = "sportscore"


def _create_validation_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS validated_match_links (
            candidate_id TEXT PRIMARY KEY,
            betclic_match_id TEXT NOT NULL,
            betclic_match TEXT,
            betclic_date TEXT,
            provider TEXT NOT NULL,
            provider_match_id TEXT NOT NULL,
            provider_competition TEXT,
            provider_match TEXT,
            provider_date TEXT,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            validation_status TEXT NOT NULL CHECK (validation_status = 'CONFIRMED'),
            validated_by TEXT,
            validated_at TEXT,
            validation_notes TEXT,
            linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (provider, provider_match_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_validated_links_betclic
        ON validated_match_links (betclic_match_id)
    """)


def apply(db_path=DB, registry_path=REGISTRY, export_path=EXPORT):
    with registry_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    confirmed = [r for r in rows if r.get("validation_status") == "CONFIRMED"]
    if not confirmed:
        raise ValueError("Aucune correspondance confirmee dans le registre.")

    output_fields = [
        "candidate_id", "betclic_match_id", "betclic_match", "betclic_date",
        "provider", "provider_match_id", "provider_competition", "provider_match",
        "provider_date", "home_score", "away_score", "score", "validation_status",
        "validated_by", "validated_at", "validation_notes"
    ]
    exported = []
    inserted_links = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_result_corrections (
                provider TEXT NOT NULL,
                provider_match_id TEXT NOT NULL,
                original_home_score INTEGER,
                original_away_score INTEGER,
                corrected_home_score INTEGER NOT NULL,
                corrected_away_score INTEGER NOT NULL,
                reason TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                corrected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, provider_match_id)
            )
        """)
        _create_validation_table(conn)

        seattle = [r for r in confirmed if
                   r.get("provider") == SEATTLE_PROVIDER and
                   r.get("provider_match_id") == SEATTLE_PROVIDER_ID]
        if seattle and any(r.get("score") != "1-1" for r in seattle):
            raise RuntimeError("Validation Seattle incoherente : le score confirme doit etre 1-1.")

        # Vérifier toutes les lignes et les identifiants avant toute écriture.
        seen_candidates = set()
        for r in confirmed:
            required = ("candidate_id", "betclic_match_id", "provider", "provider_match_id", "score")
            if any(not (r.get(k) or "").strip() for k in required):
                raise RuntimeError("Validation CONFIRMED incomplète : identifiant ou score absent.")
            if r["candidate_id"] in seen_candidates:
                raise RuntimeError(f"candidate_id duplique dans le registre : {r['candidate_id']}")
            seen_candidates.add(r["candidate_id"])
            score_row = conn.execute("""
                SELECT home_score, away_score FROM provider_results
                WHERE provider=? AND provider_match_id=?
            """, (r["provider"], r["provider_match_id"])).fetchone()
            if score_row is None:
                raise RuntimeError(f"Resultat fournisseur absent pour {r['provider']}:{r['provider_match_id']}")
            actual = f"{score_row[0]}-{score_row[1]}"
            seattle_exception = (
                r["provider"] == SEATTLE_PROVIDER and
                r["provider_match_id"] == SEATTLE_PROVIDER_ID and
                r["score"] == "1-1" and actual == "3-5"
            )
            if actual != r["score"] and not seattle_exception:
                raise RuntimeError(
                    f"Incoherence {r.get('betclic_match', r['betclic_match_id'])}: "
                    f"registre={r['score']}, DB={actual}"
                )

        # Correction spéciale seulement après confirmation humaine.
        if seattle:
            row = conn.execute("""
                SELECT home_score, away_score, score_json FROM provider_results
                WHERE provider=? AND provider_match_id=?
            """, (SEATTLE_PROVIDER, SEATTLE_PROVIDER_ID)).fetchone()
            old_home, old_away, raw_json = row
            if (old_home, old_away) == (3, 5):
                conn.execute("""
                    INSERT OR IGNORE INTO provider_result_corrections (
                        provider, provider_match_id, original_home_score,
                        original_away_score, corrected_home_score,
                        corrected_away_score, reason, source_reference
                    ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                """, (SEATTLE_PROVIDER, SEATTLE_PROVIDER_ID, old_home, old_away,
                      "Score fournisseur 3-5 incompatible avec le score confirme 1-1; penalties 2-4 conservees separement.",
                      "MLSsoccer.com; Associated Press; Sky Sports"))
                conn.execute("""
                    UPDATE provider_results SET home_score=1, away_score=1, score_json=?
                    WHERE provider=? AND provider_match_id=?
                """, (json.dumps({
                    "source_payload_preserved": raw_json,
                    "correction": {
                        "regulation_and_extra_time": "1-1",
                        "penalties": "2-4",
                        "reason": "Score normalise; tirs au but conserves separement."
                    }
                }, ensure_ascii=False, sort_keys=True), SEATTLE_PROVIDER, SEATTLE_PROVIDER_ID))
            elif (old_home, old_away) != (1, 1):
                raise RuntimeError(f"Score Seattle inattendu: {old_home}-{old_away}; correction refusee.")

        # Insérer des liens audités. Un fournisseur ne peut pointer que vers un match Betclic.
        for r in confirmed:
            score_row = conn.execute("""
                SELECT home_score, away_score FROM provider_results
                WHERE provider=? AND provider_match_id=?
            """, (r["provider"], r["provider_match_id"])).fetchone()
            home_score, away_score = score_row
            score = f"{home_score}-{away_score}"
            if score != r["score"]:
                raise RuntimeError(f"Incoherence apres correction pour {r.get('betclic_match', r['betclic_match_id'])}: registre={r['score']}, DB={score}")

            existing = conn.execute("""
                SELECT betclic_match_id, provider, provider_match_id, home_score, away_score
                FROM validated_match_links WHERE candidate_id=?
            """, (r["candidate_id"],)).fetchone()
            identity = (r["betclic_match_id"], r["provider"], r["provider_match_id"], home_score, away_score)
            if existing is not None:
                if tuple(existing) != identity:
                    raise RuntimeError(f"Lien valide existant contradictoire pour {r['candidate_id']}; intervention requise.")
            else:
                try:
                    conn.execute("""
                        INSERT INTO validated_match_links (
                            candidate_id, betclic_match_id, betclic_match, betclic_date,
                            provider, provider_match_id, provider_competition, provider_match,
                            provider_date, home_score, away_score, validation_status,
                            validated_by, validated_at, validation_notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, ?)
                    """, (r["candidate_id"], r["betclic_match_id"], r.get("betclic_match", ""),
                          r.get("betclic_date", ""), r["provider"], r["provider_match_id"],
                          r.get("provider_competition", ""), r.get("provider_match", ""),
                          r.get("provider_date", ""), home_score, away_score,
                          r.get("validated_by", ""), r.get("validated_at", ""),
                          r.get("validation_notes", "")))
                    inserted_links += 1
                except sqlite3.IntegrityError as exc:
                    raise RuntimeError(
                        f"Conflit de liaison fournisseur {r['provider']}:{r['provider_match_id']}; "
                        "un contrôle humain est requis."
                    ) from exc

            exported.append({**{k: r.get(k, "") for k in output_fields},
                             "home_score": home_score, "away_score": away_score, "score": score})

        conn.commit()

    export_path.parent.mkdir(parents=True, exist_ok=True)
    temp_export = export_path.with_suffix(export_path.suffix + ".tmp")
    with temp_export.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(exported)
    temp_export.replace(export_path)
    print(f"CORRECTIONS SEATTLE : {1 if seattle else 0} (uniquement si CONFIRMED)")
    print(f"LIENS VALIDES AJOUTES : {inserted_links}")
    print(f"LIENS VALIDES TOTAL : {len(exported)}")
    print(f"RESULTATS VALIDES EXPORTES : {len(exported)}")
    print(f"EXPORT : {export_path}")
    return len(exported)


if __name__ == "__main__":
    apply()
