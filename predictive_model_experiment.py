"""Premier modele multinomial exploratoire, compare au marche 1X2 hors periode.

Le modele apprend une recalibration des probabilites Betclic (log-probabilites).
Il ne constitue pas encore un modele independant base sur statistiques d'equipes.
"""
from collections import Counter
import math

from sklearn.linear_model import LogisticRegression

from baseline_market_evaluation import CLASSES, build_dataset, score_metrics

MIN_TRAIN = 20
MIN_TEST = 5


def features(rows):
    return [[math.log(max(1e-8, float(row["probs"][key]))) for key in CLASSES]
            for row in rows]


def top_label_calibration_error(rows, bins=5):
    """ECE descriptif sur la confiance du choix le plus probable."""
    if not rows:
        return None
    buckets = [[] for _ in range(bins)]
    for row in rows:
        confidence = max(row["probs"].values())
        predicted = max(row["probs"], key=row["probs"].get)
        correct = float(predicted == row["outcome"])
        bucket = min(bins - 1, int(confidence * bins))
        buckets[bucket].append((confidence, correct))
    total = len(rows)
    error = 0.0
    details = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_conf = sum(x[0] for x in bucket) / len(bucket)
        accuracy = sum(x[1] for x in bucket) / len(bucket)
        error += len(bucket) / total * abs(mean_conf - accuracy)
        details.append({
            "bin": f"{index / bins:.1f}-{(index + 1) / bins:.1f}",
            "n": len(bucket),
            "mean_confidence": round(mean_conf, 6),
            "accuracy": round(accuracy, 6),
        })
    return {"ece": round(error, 6), "bins": details}


def evaluate_chronological_model(rows):
    """Entraine sur les 70% plus anciens, teste sur les 30% les plus recents."""
    rows = sorted(rows, key=lambda row: row["kickoff"])
    cut = max(1, int(len(rows) * 0.70))
    train, test = rows[:cut], rows[cut:]
    result = {"train_n": len(train), "test_n": len(test)}
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        result["status"] = "INSUFFICIENT_TRAIN_OR_TEST_SAMPLE"
        return result

    train_classes = {row["outcome"] for row in train}
    if train_classes != set(CLASSES):
        result["status"] = "TRAINING_SET_MISSING_CLASS"
        return result

    model = LogisticRegression(
        C=0.1,
        solver="lbfgs",
        max_iter=2000,
        random_state=42,
    )
    model.fit(features(train), [row["outcome"] for row in train])

    model_rows = []
    for row, vector in zip(test, model.predict_proba(features(test))):
        probs = {key: 0.0 for key in CLASSES}
        for label, probability in zip(model.classes_, vector):
            probs[label] = float(probability)
        model_rows.append({"outcome": row["outcome"], "probs": probs})

    market_rows = [{"outcome": row["outcome"], "probs": row["probs"]}
                   for row in test]
    majority = Counter(row["outcome"] for row in train).most_common(1)[0][0]
    model_metrics = score_metrics(model_rows)
    market_metrics = score_metrics(market_rows)
    result.update({
        "status": "MODEL_EXPERIMENT_COMPLETED",
        "model": model_metrics,
        "market": market_metrics,
        "model_top_label_calibration": top_label_calibration_error(model_rows),
        "market_top_label_calibration": top_label_calibration_error(market_rows),
        "training_majority_accuracy": round(
            sum(row["outcome"] == majority for row in test) / len(test), 6
        ),
        "better_log_loss_than_market": (
            model_metrics["log_loss"] < market_metrics["log_loss"]
        ),
        "better_brier_than_market": (
            model_metrics["brier_score"] < market_metrics["brier_score"]
        ),
        "model_type": "multinomial_logistic_recalibration_of_market_probabilities",
        "independence": "NOT_INDEPENDENT_MARKET_DERIVED",
    })
    return result


def filter_to_eligible_matches(market_rows, eligible_samples):
    """Ne conserve que les matchs ayant assez d'historique equipe valide."""
    eligible_ids = {row["match_id"] for row in eligible_samples}
    return [row for row in market_rows if row.get("match_id") in eligible_ids]


def main():
    market_rows, diagnostics = build_dataset()
    rows = market_rows
    print("=== RECALIBRAGE DU MARCHE BETCLIC 1X2 - TEST CHRONOLOGIQUE ===")
    print("Diagnostic marche brut :", diagnostics)
    print("Observations 1X2 completes disponibles :", len(rows))
    print("Ce module est un benchmark derive du marche, pas le modele independant de forme.")
    result = evaluate_chronological_model(rows)
    print("Evaluation chronologique :", result)
    print("Calibration top-label (ECE) : descriptive, prudence sur petit echantillon.")
    print("Aucun ajustement sur l'echantillon test; aucune promesse de rentabilite.")
    if result["status"] != "MODEL_EXPERIMENT_COMPLETED":
        print("STATUT :", result["status"])
    else:
        print("STATUT : MODEL_EXPERIMENT_COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
