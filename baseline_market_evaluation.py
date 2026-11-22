"""Benchmark exploratoire du marché Betclic 1X2, sans fuite temporelle."""
import math, re, sqlite3, unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/betclic_history.db")
MARKET = "Résultat du match (t. rég)"
CLASSES = ("HOME", "DRAW", "AWAY")


def parse_utc(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def split_teams(name):
    for sep in (" - ", " – ", " — ", " vs ", " v "):
        parts = str(name or "").split(sep)
        if len(parts) == 2 and all(norm(x) for x in parts):
            return norm(parts[0]), norm(parts[1])
    return None, None


def classify_selection(selection, home, away):
    label = norm(selection)
    if label in {"x", "draw", "tie", "nul", "match nul", "empate"}:
        return "DRAW"
    if label in {"1", "home", "home win", "domicile", "victoire domicile"}:
        return "HOME"
    if label in {"2", "away", "away win", "exterieur", "victoire exterieur"}:
        return "AWAY"
    if home and label == home:
        return "HOME"
    if away and label == away:
        return "AWAY"
    return None


def fair_probs(prices):
    if set(prices) != set(CLASSES):
        return None
    implied = {k: 1.0 / float(v) for k, v in prices.items()
               if v is not None and float(v) > 1.0}
    if len(implied) != 3:
        return None
    total = sum(implied.values())
    return {k: v / total for k, v in implied.items()} if total > 0 else None


def score_metrics(rows):
    n = len(rows)
    if not n:
        return None
    actual = [r["outcome"] for r in rows]
    brier = sum(sum((r["probs"][k] - (r["outcome"] == k)) ** 2
                    for k in CLASSES) for r in rows) / n
    logloss = -sum(math.log(max(1e-15, r["probs"][r["outcome"]]))
                   for r in rows) / n
    accuracy = sum(max(r["probs"], key=r["probs"].get) == r["outcome"]
                   for r in rows) / n
    counts = {k: actual.count(k) for k in CLASSES}
    return {"n": n, "brier_score": round(brier, 6),
            "log_loss": round(logloss, 6), "accuracy": round(accuracy, 6),
            "majority_class_accuracy": round(max(counts.values()) / n, 6),
            "outcomes": counts}


def build_dataset(db_path=DB_PATH):
    path = Path(db_path)
    diagnostics = {"confirmed_unique": 0, "conflicting_labels": 0,
                   "invalid_dates": 0, "no_pre_kickoff_1x2": 0,
                   "unmapped_selection_sets": 0, "incomplete_1x2": 0}
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        links = conn.execute("""
            SELECT l.betclic_match_id, l.home_score, l.away_score,
                   m.match_name, m.match_date
            FROM validated_match_links l JOIN matches m
              ON m.match_id=l.betclic_match_id
            WHERE l.validation_status='CONFIRMED'
        """).fetchall()
        grouped = defaultdict(list)
        for row in links:
            grouped[str(row[0])].append(row[1:])
        diagnostics["confirmed_unique"] = len(grouped)
        dataset = []
        for match_id, records in grouped.items():
            scores = {(r[0], r[1]) for r in records}
            if len(scores) != 1:
                diagnostics["conflicting_labels"] += 1
                continue
            hs, aws = next(iter(scores))
            if not isinstance(hs, int) or not isinstance(aws, int) or hs < 0 or aws < 0:
                continue
            name = next((r[2] for r in records if r[2]), "")
            kickoff = parse_utc(next((r[3] for r in records if r[3]), None))
            if kickoff is None:
                diagnostics["invalid_dates"] += 1
                continue
            home, away = split_teams(name)
            odds = conn.execute("""
                SELECT selection_name, odds, collected_at
                FROM odds_history
                WHERE match_id=? AND market_name=? AND suspended=0
                  AND odds>1 AND collected_at<?
                ORDER BY collected_at, id
            """, (match_id, MARKET, kickoff.isoformat())).fetchall()
            snapshots, unmapped = defaultdict(dict), False
            for selection, price, collected in odds:
                stamp = parse_utc(collected)
                if stamp is None or stamp >= kickoff:
                    continue
                outcome = classify_selection(selection, home, away)
                if outcome is None:
                    unmapped = True
                    continue
                snapshots[stamp][outcome] = float(price)
            if not snapshots:
                diagnostics["no_pre_kickoff_1x2"] += 1
                diagnostics["unmapped_selection_sets"] += int(unmapped)
                continue
            candidates = [(stamp, fair_probs(prices))
                          for stamp, prices in snapshots.items()]
            candidates = [(stamp, probs) for stamp, probs in candidates if probs]
            if not candidates:
                diagnostics["incomplete_1x2"] += 1
                continue
            _, probs = max(candidates, key=lambda x: x[0])
            outcome = "HOME" if hs > aws else "AWAY" if hs < aws else "DRAW"
            dataset.append({"match_id": str(match_id), "kickoff": kickoff, "outcome": outcome, "probs": probs})
    dataset.sort(key=lambda x: x["kickoff"])
    return dataset, diagnostics


def run_benchmark(db_path=DB_PATH):
    rows, diagnostics = build_dataset(db_path)
    print("=== BENCHMARK MARCHE BETCLIC 1X2 (EXPERIMENTAL) ===")
    print("Marché : résultat du match, temps réglementaire")
    print("Cotes : dernier snapshot complet strictement avant le coup d'envoi")
    print("Probabilités : normalisation des cotes, marge retirée")
    print("Diagnostic agrégé :", diagnostics)
    print("Échantillon complet et mappé :", len(rows))
    if len(rows) < 10:
        print("STATUT : INSUFFICIENT_MAPPED_SAMPLE")
        return 0
    cut = max(1, int(len(rows) * 0.70))
    test = rows[cut:]
    print("Découpage chronologique : 70% initial / 30% test hors période")
    print("Taille initiale :", cut, "| Taille test :", len(test))
    print("Métriques test :", score_metrics(test))
    print("STATUT :", "BASELINE_EXPERIMENT_COMPLETED" if len(test) >= 5 else "TEST_TOO_SMALL")
    print("Ceci évalue le marché, pas un modèle entraîné ni la rentabilité.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
