import sqlite3
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")


def main():
    link_integrity_ok = True
    if not DB_PATH.is_file():
        raise SystemExit(f"ERREUR : base introuvable : {DB_PATH}")

    uri = f"file:{DB_PATH.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)

    try:
        print("=" * 60)
        print("DIAGNOSTIC SQLITE - LECTURE SEULE")
        print("Aucune donnée ne sera modifiée ou supprimée.")
        print("=" * 60)

        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        integrity_values = [row[0] for row in integrity]
        print("\n[1] INTEGRITE SQLITE")
        for value in integrity_values:
            print("-", value)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }

        print("\n[2] TABLES ET VOLUMES")
        for table in sorted(tables):
            quoted = '"' + table.replace('"', '""') + '"'
            count = conn.execute(
                f"SELECT COUNT(*) FROM {quoted}"
            ).fetchone()[0]
            columns = [
                row[1] for row in conn.execute(
                    f"PRAGMA table_info({quoted})"
                )
            ]
            print(f"- {table}: {count:,} lignes")
            print("  Colonnes:", ", ".join(columns))

        print("\n[3] CONTROLES DES DONNEES")
        if "matches" in tables:
            total = conn.execute(
                "SELECT COUNT(*) FROM matches"
            ).fetchone()[0]
            empty_ids = conn.execute(
                "SELECT COUNT(*) FROM matches "
                "WHERE match_id IS NULL OR TRIM(match_id) = ''"
            ).fetchone()[0]
            print(f"- Matches sans identifiant : {empty_ids} / {total}")

        if "odds_history" in tables:
            total = conn.execute(
                "SELECT COUNT(*) FROM odds_history"
            ).fetchone()[0]
            null_required = conn.execute("""
                SELECT COUNT(*) FROM odds_history
                WHERE match_id IS NULL OR TRIM(match_id) = ''
                   OR collected_at IS NULL OR TRIM(collected_at) = ''
            """).fetchone()[0]
            invalid_odds = conn.execute("""
                SELECT COUNT(*) FROM odds_history
                WHERE odds IS NULL OR odds <= 1
            """).fetchone()[0]
            print(f"- Cotes : {total:,}")
            print(f"- Lignes sans identifiant/date : {null_required}")
            print(f"- Cotes nulles ou <= 1 : {invalid_odds}")

            date_range = conn.execute("""
                SELECT MIN(collected_at), MAX(collected_at)
                FROM odds_history
            """).fetchone()
            print(f"- Première collecte : {date_range[0]}")
            print(f"- Dernière collecte : {date_range[1]}")

            duplicate_groups = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT match_id, market_name, selection_name,
                           odds, suspended, collected_at
                    FROM odds_history
                    GROUP BY match_id, market_name, selection_name,
                             odds, suspended, collected_at
                    HAVING COUNT(*) > 1
                )
            """).fetchone()[0]
            duplicate_rows = conn.execute("""
                SELECT COALESCE(SUM(n - 1), 0) FROM (
                    SELECT COUNT(*) AS n
                    FROM odds_history
                    GROUP BY match_id, market_name, selection_name,
                             odds, suspended, collected_at
                    HAVING COUNT(*) > 1
                )
            """).fetchone()[0]
            print(f"- Groupes de doublons exacts : {duplicate_groups}")
            print(f"- Lignes en surplus dans ces doublons : {duplicate_rows}")

            print("\\n[3B] ANALYSE DES DOUBLONS - LECTURE SEULE")
            repeat_distribution = conn.execute("""
                SELECT n, COUNT(*) AS groups_count
                FROM (
                    SELECT COUNT(*) AS n
                    FROM odds_history
                    GROUP BY match_id, market_name, selection_name,
                             odds, suspended, collected_at
                    HAVING COUNT(*) > 1
                )
                GROUP BY n
                ORDER BY n
            """).fetchall()
            print("- Répartition (nombre de copies -> groupes) :")
            if repeat_distribution:
                for copies, groups_count in repeat_distribution:
                    print(f"  {copies} copies -> {groups_count} groupes")
            else:
                print("  Aucun doublon exact.")

            null_fields = conn.execute("""
                SELECT
                    SUM(CASE WHEN market_name IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN selection_name IS NULL THEN 1 ELSE 0 END)
                FROM odds_history
            """).fetchone()
            print(f"- Lignes avec market_name NULL : {null_fields[0] or 0}")
            print(f"- Lignes avec selection_name NULL : {null_fields[1] or 0}")

            same_payload_other_times = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT match_id, market_name, selection_name,
                           odds, suspended
                    FROM odds_history
                    GROUP BY match_id, market_name, selection_name,
                             odds, suspended
                    HAVING COUNT(DISTINCT collected_at) > 1
                )
            """).fetchone()[0]
            print("- Combinaisons identiques observées à plusieurs dates : "
                  f"{same_payload_other_times}")

            examples = conn.execute("""
                SELECT match_id, market_name, selection_name, odds,
                       suspended, collected_at, COUNT(*) AS copies
                FROM odds_history
                GROUP BY match_id, market_name, selection_name,
                         odds, suspended, collected_at
                HAVING COUNT(*) > 1
                ORDER BY copies DESC, collected_at DESC
                LIMIT 10
            """).fetchall()
            print("- 10 exemples (match, marché, sélection, cote, suspendu, "
                  "date, copies) :")
            for row in examples:
                print("  ", row)


            if "matches" in tables:
                orphaned = conn.execute("""
                    SELECT COUNT(*) FROM odds_history o
                    LEFT JOIN matches m ON m.match_id = o.match_id
                    WHERE m.match_id IS NULL
                """).fetchone()[0]
                print(f"- Cotes sans match correspondant : {orphaned}")


        print("\\n[3C] CONTROLES DES LIENS DE RESULTATS VALIDES")
        if "validated_match_links" not in tables:
            print("- Table validated_match_links absente : aucun lien auditable matérialisé.")
        else:
            link_count = conn.execute(
                "SELECT COUNT(*) FROM validated_match_links"
            ).fetchone()[0]
            confirmed_count = conn.execute("""
                SELECT COUNT(*) FROM validated_match_links
                WHERE validation_status = 'CONFIRMED'
            """).fetchone()[0]
            invalid_status = conn.execute("""
                SELECT COUNT(*) FROM validated_match_links
                WHERE validation_status IS NULL OR validation_status <> 'CONFIRMED'
            """).fetchone()[0]
            duplicate_provider_links = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT provider, provider_match_id
                    FROM validated_match_links
                    GROUP BY provider, provider_match_id
                    HAVING COUNT(*) > 1
                )
            """).fetchone()[0]
            invalid_scores = conn.execute("""
                SELECT COUNT(*) FROM validated_match_links
                WHERE home_score IS NULL OR away_score IS NULL
                   OR home_score < 0 OR away_score < 0
            """).fetchone()[0]
            print(f"- Liens matérialisés : {link_count}")
            print(f"- Liens CONFIRMED : {confirmed_count}")
            print(f"- Statuts invalides : {invalid_status}")
            print(f"- Groupes de liens fournisseur dupliqués : {duplicate_provider_links}")
            print(f"- Scores absents ou négatifs : {invalid_scores}")

            if "provider_results" in tables:
                missing_provider = conn.execute("""
                    SELECT COUNT(*) FROM validated_match_links l
                    LEFT JOIN provider_results p
                      ON p.provider = l.provider
                     AND p.provider_match_id = l.provider_match_id
                    WHERE p.provider_match_id IS NULL
                """).fetchone()[0]
                score_mismatches = conn.execute("""
                    SELECT COUNT(*) FROM validated_match_links l
                    JOIN provider_results p
                      ON p.provider = l.provider
                     AND p.provider_match_id = l.provider_match_id
                    WHERE l.home_score <> p.home_score
                       OR l.away_score <> p.away_score
                """).fetchone()[0]
                print(f"- Liens sans résultat fournisseur : {missing_provider}")
                print(f"- Scores lien/base fournisseur divergents : {score_mismatches}")
            else:
                missing_provider = score_mismatches = None
                print("- provider_results absente : contrôle fournisseur impossible.")

            if "matches" in tables:
                missing_betclic = conn.execute("""
                    SELECT COUNT(*) FROM validated_match_links l
                    LEFT JOIN matches m ON m.match_id = l.betclic_match_id
                    WHERE m.match_id IS NULL
                """).fetchone()[0]
                print(f"- Liens sans match Betclic correspondant : {missing_betclic}")
            else:
                missing_betclic = None
                print("- matches absente : contrôle Betclic impossible.")

            failures = [invalid_status, duplicate_provider_links, invalid_scores]
            failures += [x for x in (missing_provider, score_mismatches, missing_betclic)
                         if x is not None]
            if any(failures):
                link_integrity_ok = False
                print("- ALERTE : incohérences détectées dans les liens validés.")
            else:
                print("- Contrôles de cohérence des liens : OK.")

        if "odds_alerts" in tables:
            total_alerts = conn.execute(
                "SELECT COUNT(*) FROM odds_alerts"
            ).fetchone()[0]
            print(f"- Alertes enregistrées : {total_alerts:,}")

        print("\n[4] CONCLUSION TECHNIQUE")
        if integrity_values == ["ok"] and link_integrity_ok:
            print("INTEGRITE SQLITE ET LIENS VALIDES : OK")
        else:
            print("INTEGRITE SQLITE / LIENS VALIDES : ECHEC - examiner les erreurs ci-dessus.")
            raise SystemExit(1)

        print("Diagnostic terminé. Base ouverte en lecture seule.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
