"""Rapprochement candidat Betclic / Football-Data, sans liaison automatique."""
import csv
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

DB = Path("data/betclic_history.db")
OUT = Path("reports")
PROVIDERS = ("football-data.org", "sportscore")
TEAM_SUFFIXES = {"fc", "cf", "sc", "afc", "fk", "club", "football"}
SQUAD_MARKERS = {
    "f": "women", "fem": "women", "female": "women",
    "feminine": "women", "women": "women", "woman": "women",
    "ladies": "women",
    "u17": "youth", "u18": "youth", "u19": "youth", "u20": "youth",
    "u21": "youth", "u23": "youth",
    "reserves": "reserve", "reserve": "reserve", "ii": "reserve",
    "b": "reserve", "academy": "academy"
}
SQUAD_TOKENS = set(SQUAD_MARKERS)
TEAM_ALIASES = {
    # Alias exacts observes dans les rapprochements rejetes (pas de remplacement partiel).
    "qpr": "queens park rangers",
    "naples": "ssc napoli",
    "kyoto": "kyoto sanga",
    "western sydney": "western sydney wanderers",
    "derby": "derby county",
    "southampton": "southampton",
    "west ham": "west ham united",
    "newcastle": "newcastle united",
    "bolton": "bolton wanderers",
    "swansea": "swansea city",
    "coventry": "coventry city",
    "celta vigo": "rc celta de vigo",
    "rc lens": "racing club de lens",
    "feyenoord": "feyenoord rotterdam",
    # Variantes exactes relevées dans les résultats fournisseurs de la fenêtre auditée.
    "kashiwa reysol": "kashiwa reysol",
    "jef united chiba": "jef united ichihara chiba",
    "lazio": "ss lazio",
    "lecce": "us lecce",
    "elversberg": "sv 07 elversberg",
    "fribourg": "freiburg",
    "incheon united": "incheon united",
    "ulsan hyundai": "ulsan hd",
    "mb rouissat": "rouisset",
    "cardiff": "cardiff city",
    "rayo vallecano": "rayo vallecano de madrid",
    "valence": "valencia",
    "telstar": "telstar 1963",
    "zwolle": "pec zwolle",
    "samgurali tskaltubo": "samgurali tskh",
    "iberia 1999": "iberia 1999 tbilisi",
    "vitoria ba": "ec vitoria",
    "bragantino": "rb bragantino",
    "tottenham": "tottenham hotspur",
    "ipswich": "ipswich town",
    "eintracht francfort": "eintracht frankfurt",
    "hoffenheim": "tsg 1899 hoffenheim",
    "once caldas": "deportiva once caldas",
    "atletico nacional": "atletico nacional medellin",
    "servette chenois": "servette",
    "troyes": "es troyes ac",
    "nice": "ogc nice",
    "paris sg": "paris saint germain",
    "leeds": "leeds united",
    "alaves": "deportivo alaves",
    "deportivo a corogne": "rc deportivo la coruna",
    "uanl tigres": "tigres uanl",
    "america": "club america",
    "gazisehir gaziantep": "gaziantep futbol kulubu",
    "athletic bilbao": "athletic",
    "espanyol": "rcd espanyol de barcelona",
    "bayern munich": "bayern munchen",
    "cologne": "1 koln",
    "strasbourg": "rc strasbourg alsace",
    "brest": "stade brestois 29",
    "como": "como 1907",
    "cagliari": "cagliari calcio",
    "stoke": "stoke city",
    "birmingham": "birmingham city",
    "wolverhampton": "wolverhampton wanderers",
    "charlton": "charlton athletic",
    "come": "como 1907",
    "seville": "sevilla",
    "betis": "real betis balompie",
    "unam pumas": "pumas unam",
    "monterrey": "rayados de monterrey",
    "pumas u n a m": "pumas unam",
}
MAX_DATE_DELTA_HOURS = 12


def _normalized_tokens(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return value.split()


def norm(value):
    # Les marqueurs d'effectif sont vérifiés séparément par squad_profile.
    # Les retirer ici permet de comparer le nom du club sans confondre F. et Women.
    tokens = [
        t for t in _normalized_tokens(value)
        if t not in TEAM_SUFFIXES and t not in SQUAD_TOKENS
    ]
    normalized = " ".join(tokens)
    return TEAM_ALIASES.get(normalized, normalized)


def split_teams(match_name):
    """Sépare uniquement sur des délimiteurs entourés d'espaces."""
    text = (match_name or "").strip()
    parts = re.split(r"\s+(?:-|vs\.?|v\.?|@)\s+", text, maxsplit=1, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return parts[0].strip(), parts[1].strip()
    return None, None


def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def squad_profile(value):
    """Retourne les marqueurs d'équipe (féminine, jeunes, réserve)."""
    tokens = set(_normalized_tokens(value))
    return {category for token, category in SQUAD_MARKERS.items() if token in tokens}


def squad_compatible(a, b):
    """Évite de rapprocher première équipe, réserve, jeunes et équipes féminines."""
    return squad_profile(a) == squad_profile(b)


def similarity(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def date_similarity(delta_hours):
    """Score temporel normalisé sur la fenêtre réellement autorisée."""
    return max(0.0, 1.0 - delta_hours / MAX_DATE_DELTA_HOURS)


def diagnostic_reason(results_in_window, squad_compatible_count,
                      team_similarity_count, candidate_count):
    """Classe la cause principale d’absence de candidat."""
    if results_in_window == 0:
        return "AUCUN_RESULTAT_FOURNISSEUR_DANS_12H"
    if squad_compatible_count == 0:
        return "PROFIL_EQUIPE_INCOMPATIBLE"
    if team_similarity_count == 0:
        return "SIMILARITE_EQUIPE_INSUFFISANTE"
    if candidate_count == 0:
        return "SCORE_CANDIDAT_SOUS_SEUIL"
    return "CANDIDAT_PRESENT"


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not DB.is_file():
        raise SystemExit("Base SQLite absente.")
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        betclic = conn.execute(
            "SELECT match_id, match_name, match_date, sport FROM matches ORDER BY match_date"
        ).fetchall()
        results = conn.execute("""
            SELECT provider, provider_match_id, competition_code, competition_name,
                   utc_date, home_team, away_team, home_score, away_score
            FROM provider_results
            WHERE provider IN (?, ?) AND status='FINISHED'
        """, PROVIDERS).fetchall()

    fields = [
        "betclic_match_id", "betclic_match", "betclic_date", "provider", "provider_match_id",
        "provider_competition", "provider_match", "provider_date", "score",
        "home_similarity", "away_similarity", "date_delta_hours",
        "candidate_score", "rank", "decision", "results_in_window",
        "football_data_in_window", "sportscore_in_window", "squad_compatible_in_window",
        "team_similarity_qualified", "diagnostic_reason", "best_rejected_provider", "best_rejected_match", "best_rejected_date", "best_home_similarity", "best_away_similarity"
    ]
    candidates, unmatched, review, non_evaluable = [], [], [], []
    parsed_results = [(r, parse_date(r["utc_date"])) for r in results]
    parsed_results = [(r, d) for r, d in parsed_results if d is not None]

    for b in betclic:
        # Un identifiant manquant ne doit jamais devenir une fausse clé "None".
        if not b["match_id"]:
            non_evaluable.append({
                "betclic_match_id": "", "betclic_match": b["match_name"] or "",
                "betclic_date": b["match_date"] or "", "provider": "", "provider_match_id": "",
                "provider_competition": "", "provider_match": "", "provider_date": "",
                "score": "", "home_similarity": "", "away_similarity": "",
                "date_delta_hours": "", "candidate_score": "", "rank": "",
                "decision": "IDENTIFIANT_BETCLIC_ABSENT"
            })
            continue
        # La base stocke actuellement le football ; le filtre protège contre
        # l'introduction future d'autres sports dans le rapport.
        if (b["sport"] or "").lower() != "football":
            continue
        bh, ba = split_teams(b["match_name"])
        bd = parse_date(b["match_date"])
        if not bh or not ba or not bd:
            non_evaluable.append({
                "betclic_match_id": str(b["match_id"]), "betclic_match": b["match_name"] or "",
                "betclic_date": b["match_date"] or "", "provider": "", "provider_match_id": "",
                "provider_competition": "", "provider_match": "", "provider_date": "",
                "score": "", "home_similarity": "", "away_similarity": "",
                "date_delta_hours": "", "candidate_score": "", "rank": "",
                "decision": "NON_EVALUABLE_FORMAT_OU_DATE"
            })
            continue

        found = []
        best_rejected = None
        results_in_window = football_data_in_window = sportscore_in_window = 0
        squad_compatible_count = team_similarity_count = 0
        for r, rd in parsed_results:
            delta = abs((bd - rd).total_seconds()) / 3600
            if delta > MAX_DATE_DELTA_HOURS:
                continue
            results_in_window += 1
            if r["provider"] == "football-data.org":
                football_data_in_window += 1
            elif r["provider"] == "sportscore":
                sportscore_in_window += 1
            # Ne jamais confondre première équipe, réserve, jeunes ou féminin.
            if not squad_compatible(bh, r["home_team"]) or not squad_compatible(ba, r["away_team"]):
                continue
            squad_compatible_count += 1
            hs, aws = similarity(bh, r["home_team"]), similarity(ba, r["away_team"])
            rejected_key = (min(hs, aws), (hs + aws) / 2, -delta)
            if best_rejected is None or rejected_key > best_rejected[0]:
                best_rejected = (rejected_key, r, hs, aws, delta)
            # Les deux équipes doivent présenter une similarité minimale.
            if hs < 0.80 or aws < 0.80:
                continue
            team_similarity_count += 1
            date_score = date_similarity(delta)
            score = 0.45 * hs + 0.45 * aws + 0.10 * date_score
            if score >= 0.62:
                found.append((score, delta, hs, aws, r))

        reason = diagnostic_reason(
            results_in_window, squad_compatible_count,
            team_similarity_count, len(found)
        )

        found.sort(key=lambda x: (-x[0], x[1]))
        if not found:
            unmatched.append({
                "betclic_match_id": str(b["match_id"]), "betclic_match": b["match_name"] or "",
                "betclic_date": b["match_date"] or "", "provider_match_id": "",
                "provider_competition": "", "provider_match": "", "provider_date": "",
                "score": "", "home_similarity": "", "away_similarity": "",
                "date_delta_hours": "", "candidate_score": "", "rank": "",
                "decision": f"AUCUN_CANDIDAT_DANS_{MAX_DATE_DELTA_HOURS}H",
                "results_in_window": results_in_window,
                "football_data_in_window": football_data_in_window,
                "sportscore_in_window": sportscore_in_window,
                "squad_compatible_in_window": squad_compatible_count,
                "team_similarity_qualified": team_similarity_count,
                "diagnostic_reason": reason,
                "best_rejected_provider": best_rejected[1]["provider"] if best_rejected else "",
                "best_rejected_match": (best_rejected[1]["home_team"] + " - " + best_rejected[1]["away_team"]) if best_rejected else "",
                "best_rejected_date": best_rejected[1]["utc_date"] if best_rejected else "",
                "best_home_similarity": round(best_rejected[2], 4) if best_rejected else "",
                "best_away_similarity": round(best_rejected[3], 4) if best_rejected else ""
            })
        for rank, (score, delta, hs, aws, r) in enumerate(found[:5], 1):
            if score >= 0.94 and hs >= 0.95 and aws >= 0.95 and delta <= 6:
                decision = "CANDIDAT_TRES_FORT_A_VALIDER"
            elif score >= 0.82 and hs >= 0.80 and aws >= 0.80:
                decision = "CANDIDAT_POSSIBLE_A_VALIDER"
            else:
                decision = "FAIBLE_SIMILARITE_A_CONTROLER"
            row = {
                "betclic_match_id": str(b["match_id"]), "betclic_match": b["match_name"] or "",
                "betclic_date": b["match_date"] or "", "provider": r["provider"], "provider_match_id": str(r["provider_match_id"]),
                "provider_competition": r["competition_code"] or r["competition_name"] or "",
                "provider_match": f'{r["home_team"]} - {r["away_team"]}',
                "provider_date": r["utc_date"], "score": f'{r["home_score"]}-{r["away_score"]}',
                "home_similarity": round(hs, 4), "away_similarity": round(aws, 4),
                "date_delta_hours": round(delta, 2), "candidate_score": round(score, 4),
                "rank": rank, "decision": decision
            }
            candidates.append(row)
            # Tous les candidats restent à vérifier manuellement, y compris les très forts.
            review.append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "match_result_candidates.csv", candidates + unmatched + non_evaluable, fields)
    write_csv(OUT / "confirmed_matches.csv", [], fields)  # aucune liaison automatique
    write_csv(OUT / "matches_to_review.csv", review, fields)
    write_csv(OUT / "unmatched_matches.csv", unmatched, fields)
    write_csv(OUT / "non_evaluable_matches.csv", non_evaluable, fields)

    print("RAPPROCHEMENT : AUCUNE LIAISON ECRITE")
    print("Matchs Betclic football avec ID :", sum(1 for b in betclic if b["match_id"] and (b["sport"] or "").lower() == "football"))
    print("Resultats Football-Data :", sum(1 for r in results if r["provider"] == "football-data.org"))
    print("Resultats SportScore :", sum(1 for r in results if r["provider"] == "sportscore"))
    print("Resultats fournisseurs total :", len(results))
    print("Lignes candidates :", len(candidates))
    print("A examiner :", len(review))
    print("Sans candidat :", len(unmatched))
    print("Non evaluables / ID absent :", len(non_evaluable))
    print("Identifiants Betclic absents :", sum(1 for b in betclic if not b["match_id"]))
    print("Rapports CSV :", OUT)


if __name__ == "__main__":
    main()
