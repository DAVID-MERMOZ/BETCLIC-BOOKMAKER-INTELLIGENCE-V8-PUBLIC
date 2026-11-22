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
    })
    return result


def main():
    rows, diagnostics = build_dataset()
    print("=== MODELE PREDICTIF EXPLORATOIRE BETCLIC 1X2 ===")
    print("Diagnostic agrege :", diagnostics)
    print("Observations 1X2 completes :", len(rows))
    result = evaluate_chronological_model(rows)
    print("Evaluation chronologique :", result)
    print("Aucun ajustement sur l'echantillon test; aucune promesse de rentabilite.")
    if result["status"] != "MODEL_EXPERIMENT_COMPLETED":
        print("STATUT :", result["status"])
    else:
        print("STATUT : MODEL_EXPERIMENT_COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
