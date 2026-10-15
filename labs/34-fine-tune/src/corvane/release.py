"""Score each model against the reviewers' labels, decide whether a release ships, and route each item
to the small model or the large one.

The gates fail closed. A release ships only when every check passes; a missing score, a class below the
floor or an accuracy gap past the margin each hold it, and the release says which.
"""

from collections import Counter

from corvane.config import CASCADE_MARGIN, CLASS_FLOOR, COVERAGE_FLOOR, MARGIN, ROUTE_BUDGET, THRESHOLDS
from corvane.labels import CATEGORIES


def score(items: list[dict], predictions: list[dict]) -> dict:
    """Accuracy, recall per category, the confusion table and cost, against the reviewers' labels.

    An unparseable answer counts as wrong. In production it is a reviewer's time, not a free pass.
    """
    by_id = {prediction["id"]: prediction for prediction in predictions}
    confusion = {category: Counter() for category in CATEGORIES}
    correct = unparsed = 0
    for item in items:
        predicted = by_id[item["id"]]["label"]
        unparsed += predicted is None
        correct += predicted == item["label"]
        confusion[item["label"]][predicted or "unparsed"] += 1
    per_class = {}
    for category in CATEGORIES:
        total = sum(confusion[category].values())
        right = confusion[category][category]
        per_class[category] = {"total": total, "correct": right, "recall": round(right / total, 3) if total else None}
    cost = sum(prediction["cost"] for prediction in predictions)
    return {"total": len(items), "correct": correct, "unparsed": unparsed,
            "accuracy": round(correct / len(items), 4) if items else 0.0,
            "per_class": per_class, "confusion": {category: dict(row) for category, row in confusion.items()},
            "cost_per_item": cost / len(items) if items else 0.0,
            "cost_per_thousand": round(cost / len(items) * 1000, 4) if items else 0.0}


def decide_release(tuned: dict | None, large: dict | None, margin: float = MARGIN,
                   class_floor: float = CLASS_FLOOR) -> dict:
    """Ship or hold, with every check named. Any check that fails, or cannot run, holds the release."""
    if not tuned or not large:
        missing = "tuned model" if not tuned else "large model"
        return {"decision": "hold", "checks": [], "reasons": [f"No held-out score for the {missing}, so nothing to compare."]}
    gap = round(large["accuracy"] - tuned["accuracy"], 4)
    checks = [
        {"name": "accuracy within margin", "passed": gap <= margin,
         "detail": f"tuned {tuned['accuracy']:.1%} against large {large['accuracy']:.1%}: "
                   f"{'trails' if gap > 0 else 'leads'} by {abs(gap):.1%}, margin {margin:.0%}"},
        floor_check(tuned, class_floor),
        {"name": "cheaper per item", "passed": tuned["cost_per_item"] < large["cost_per_item"],
         "detail": f"${tuned['cost_per_thousand']:.4f} against ${large['cost_per_thousand']:.4f} per 1,000 items"},
    ]
    return verdict(checks)


def floor_check(result: dict, class_floor: float) -> dict:
    """A model can be accurate on average and still miss a whole category. This names the ones it misses."""
    weak = {category: row["recall"] for category, row in result["per_class"].items()
            if row["recall"] is not None and row["recall"] < class_floor}
    detail = ("lowest recall " + ", ".join(f"{category} {recall:.0%}" for category, recall in sorted(weak.items()))
              + f", floor {class_floor:.0%}") if weak else f"every category at or above {class_floor:.0%}"
    return {"name": "every category above the floor", "passed": not weak, "detail": detail}


def verdict(checks: list[dict]) -> dict:
    reasons = [f"{check['name']} failed: {check['detail']}" for check in checks if not check["passed"]]
    return {"decision": "hold" if reasons else "ship", "checks": checks, "reasons": reasons}


def sure(prediction: dict, threshold: float) -> bool:
    """The small model keeps an item only when it gave one category and its own probability clears the bar."""
    return prediction["label"] is not None and (prediction["confidence"] or 0.0) >= threshold


def pick_threshold(valid_predictions: list[dict], budget: float = ROUTE_BUDGET,
                   thresholds: tuple[float, ...] = THRESHOLDS) -> float:
    """The strictest threshold that sends at most `budget` of the validation items to the large model.

    Strict keeps the small model to what it knows; the budget caps what the large model is paid for. It
    reads only the validation split, so the test split stays an honest check of the choice.
    """
    total = len(valid_predictions)
    within = [threshold for threshold in thresholds
              if sum(not sure(prediction, threshold) for prediction in valid_predictions) <= round(budget * total, 6)]
    if not within:
        raise ValueError(f"no threshold sends {budget:.0%} or less of the validation items to the large model")
    return max(within)


def why_routed(prediction: dict, threshold: float) -> str:
    if prediction["label"] is None:
        return f"The small model's answer {prediction['raw']!r} is not one category."
    return (f"The small model is unsure: {prediction['confidence']:.0%} on {prediction['label']}, "
            f"below the {threshold:.0%} threshold.")


def cascade(tuned_predictions: list[dict], large_predictions: list[dict], threshold: float) -> list[dict]:
    """One answer per item: the small model's when it is sure, otherwise the large model's.

    The small model runs on every item, so its cost is always paid; the large model is paid only for the
    items sent to it. That is the honest cost of the cascade, not the small model's price alone.
    """
    large_by_id = {prediction["id"]: prediction for prediction in large_predictions}
    answers = []
    for small in tuned_predictions:
        if sure(small, threshold):
            answers.append({**small, "model": "small", "reason": None})
            continue
        large = large_by_id[small["id"]]
        answers.append({**large, "cost": small["cost"] + large["cost"], "model": "large",
                        "small": {key: small[key] for key in ("label", "raw", "confidence")},
                        "reason": why_routed(small, threshold)})
    return answers


def decide_cascade(items: list[dict], tuned_predictions: list[dict], large_predictions: list[dict], threshold: float,
                   margin: float = CASCADE_MARGIN, coverage_floor: float = COVERAGE_FLOOR,
                   class_floor: float = CLASS_FLOOR) -> dict:
    """Ship or hold the cascade on the held-out test, with every check named.

    The second check is the one that earns the small model its place: on the items it keeps, it must be
    within the margin of the large model on those same items. The large model's answers vary between runs,
    so a strict tie flipped the verdict on one item.
    """
    if not items or not tuned_predictions or not large_predictions:
        return {"decision": "hold", "checks": [], "reasons": ["No held-out predictions from both models, so nothing to compare."]}
    answers = cascade(tuned_predictions, large_predictions, threshold)
    combined, large = score(items, answers), score(items, large_predictions)
    truth = {item["id"]: item["label"] for item in items}
    large_by_id = {prediction["id"]: prediction for prediction in large_predictions}
    kept = [answer for answer in answers if answer["model"] == "small"]
    small_right = sum(answer["label"] == truth[answer["id"]] for answer in kept)
    large_right = sum(large_by_id[answer["id"]]["label"] == truth[answer["id"]] for answer in kept)
    coverage = round(len(kept) / len(items), 4)
    gap = round(large["accuracy"] - combined["accuracy"], 4)
    checks = [
        {"name": "cascade accuracy within a point of the large model", "passed": gap <= margin,
         "detail": f"cascade {combined['accuracy']:.1%} against large {large['accuracy']:.1%}, margin {margin:.0%}"},
        {"name": "small model within a point of the large one on what it keeps",
         "passed": bool(kept) and (large_right - small_right) / len(kept) <= margin,
         "detail": f"{small_right} of {len(kept)} right, the large model {large_right} of the same {len(kept)}"},
        {"name": "small model labels enough alone", "passed": coverage >= coverage_floor,
         "detail": f"{len(kept)} of {len(items)} ({coverage:.1%}), floor {coverage_floor:.0%}"},
        {"name": "cheaper per item", "passed": combined["cost_per_item"] < large["cost_per_item"],
         "detail": f"${combined['cost_per_thousand']:.4f} against ${large['cost_per_thousand']:.4f} per 1,000 items"},
        floor_check(combined, class_floor),
    ]
    return {**verdict(checks), "threshold": threshold, "kept": len(kept), "routed": len(items) - len(kept),
            "total": len(items), "coverage": coverage, "score": combined}


def adapter_card(trained: dict | None, tuned: dict, large: dict, release: dict) -> dict:
    """What anyone deploying the adapter needs: what it was built from, how it scored and the decision.

    It travels with the adapter, so a model in production can always be traced to its data and its eval.
    """
    trained = trained or {}
    return {"base_model": trained.get("base_model"), "revision": trained.get("revision"),
            "dataset_version": trained.get("dataset_version"), "recipe": trained.get("recipe"),
            "trained_on": {key: trained.get(key) for key in ("kernel", "gpu", "gpu_minutes", "steps", "valid_accuracy")},
            "held_out": {"items": tuned["total"], "accuracy": tuned["accuracy"], "large_accuracy": large["accuracy"],
                         "recall": {category: row["recall"] for category, row in tuned["per_class"].items()},
                         "cost_per_thousand": tuned["cost_per_thousand"],
                         "large_cost_per_thousand": large["cost_per_thousand"]},
            "cascade": {key: release.get(key) for key in ("threshold", "kept", "routed", "coverage")}
            | {key: release.get("score", {}).get(key) for key in ("accuracy", "cost_per_thousand")},
            "decision": release["decision"], "reasons": release["reasons"]}


def route(item: dict, tuned: dict, threshold: float) -> dict:
    """Send the item to the small model's label or up to the large model, with the reason.

    Nothing waits for a person. A reviewer reads the routed items later, and their label trains the next
    adapter on exactly the items the small model found hard.
    """
    if sure(tuned, threshold):
        return {**item, "tuned": tuned, "route": "small", "reason": None}
    return {**item, "tuned": tuned, "route": "large", "reason": why_routed(tuned, threshold)}


def decision_row(item: dict, label: str, reviewer: str) -> dict:
    """A reviewer's decision, in the same shape as the queue export, so it trains the next round."""
    if label not in CATEGORIES:
        raise ValueError(f"{label!r} is not a category")
    return {key: item[key] for key in ("id", "descriptor", "amount", "posted")} | {"label": label, "reviewer": reviewer}
