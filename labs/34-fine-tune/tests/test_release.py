"""The release gates and the routing: they fail closed and say why."""

import pytest
from fakes import prediction

from corvane.labels import CATEGORIES
from corvane.config import CASCADE_MARGIN, CLASS_FLOOR, COVERAGE_FLOOR, MARGIN, ROUTE_BUDGET
from corvane.release import cascade, decide_cascade, decide_release, decision_row, pick_threshold, route, score


def test_the_release_rules_are_the_ones_the_readme_teaches():
    # Pinned: moving a gate is a decision, so it has to change this line as well as the config.
    assert (MARGIN, CLASS_FLOOR, CASCADE_MARGIN, COVERAGE_FLOOR, ROUTE_BUDGET) == (0.03, 0.60, 0.01, 0.70, 0.20)


def items_and_predictions(wrong: dict[str, int], per_class: int = 10, cost: float = 0.00002):
    """`per_class` items in each category, with `wrong[category]` of them answered as another category."""
    items, predictions = [], []
    for category in CATEGORIES:
        for number in range(per_class):
            item_id = f"{category}-{number}"
            items.append({"id": item_id, "label": category})
            other = "meals" if category != "meals" else "software"
            predictions.append(prediction(item_id, other if number < wrong.get(category, 0) else category, cost=cost))
    return items, predictions


def test_score_counts_accuracy_recall_and_the_confusion_table():
    items, predictions = items_and_predictions({"telecom": 3})
    predictions[0] = prediction(predictions[0]["id"], None)
    result = score(items, predictions)
    assert result["total"] == 100 and result["correct"] == 96 and result["unparsed"] == 1
    assert result["per_class"]["telecom"] == {"total": 10, "correct": 7, "recall": 0.7}
    assert result["confusion"]["telecom"] == {"meals": 3, "telecom": 7}
    assert result["confusion"][CATEGORIES[0]]["unparsed"] == 1
    assert result["cost_per_thousand"] == pytest.approx(0.02)


def test_a_tuned_model_within_the_margin_and_above_every_floor_ships():
    tuned = score(*items_and_predictions({"telecom": 2}))
    large = score(*items_and_predictions({"telecom": 1}, cost=0.0001))
    release = decide_release(tuned, large)
    assert release["decision"] == "ship" and release["reasons"] == []


def test_a_tuned_model_past_the_margin_is_held_with_the_gap_named():
    misses = round(MARGIN * 100) + 2
    tuned = score(*items_and_predictions({category: 1 for category in CATEGORIES[:misses]}))
    large = score(*items_and_predictions({}, cost=0.0001))
    release = decide_release(tuned, large)
    assert release["decision"] == "hold"
    assert any("accuracy within margin failed" in reason and "trails" in reason for reason in release["reasons"])


def test_one_category_under_the_floor_holds_the_release_even_when_accuracy_is_fine():
    below = round(40 * (1 - CLASS_FLOOR)) + 1
    tuned = score(*items_and_predictions({"shipping": below}, per_class=40))
    large = score(*items_and_predictions({"shipping": below - 1}, per_class=40, cost=0.0001))
    release = decide_release(tuned, large)
    assert tuned["accuracy"] >= large["accuracy"] - MARGIN
    assert release["decision"] == "hold"
    assert any("shipping" in reason for reason in release["reasons"])


def test_a_model_that_costs_more_is_held():
    release = decide_release(score(*items_and_predictions({}, cost=0.001)), score(*items_and_predictions({}, cost=0.0001)))
    assert release["decision"] == "hold" and "cheaper per item failed" in release["reasons"][0]


def test_with_no_score_the_gate_holds():
    large = score(*items_and_predictions({}))
    assert decide_release(None, large)["decision"] == "hold"
    assert "tuned model" in decide_release(None, large)["reasons"][0]


ITEM = {"id": "NX-1", "descriptor": "EXPEDIA 7201", "amount": 410.0, "posted": "2026-10-01"}


def test_a_sure_answer_stays_with_the_small_model():
    routed = route(ITEM, prediction("NX-1", "lodging", 0.97), 0.80)
    assert routed["route"] == "small" and routed["reason"] is None


@pytest.mark.parametrize("tuned, expected", [
    (prediction("NX-1", None, 0.9, raw="air or hotel"), "is not one category"),
    (prediction("NX-1", "lodging", 0.62), "unsure: 62% on lodging, below the 80% threshold"),
])
def test_an_unsure_or_unclear_answer_goes_to_the_large_model_with_the_reason(tuned, expected):
    routed = route(ITEM, tuned, 0.80)
    assert routed["route"] == "large" and expected in routed["reason"]


def test_the_threshold_is_the_strictest_one_within_the_route_budget():
    confidences = [0.99] * 7 + [0.82, 0.6, 0.4]
    valid = [prediction(f"V-{number}", "meals", confidence) for number, confidence in enumerate(confidences)]
    # 0.80 sends two of ten up, inside a 20% budget; 0.85 would send three.
    assert pick_threshold(valid, budget=0.20) == 0.80
    valid[0] = prediction("V-0", None, 0.99)
    assert pick_threshold(valid, budget=0.20) == 0.60
    with pytest.raises(ValueError):
        pick_threshold([prediction("V-0", "meals", 0.1)], budget=0.0)


def cascade_case(low=(), large_wrong=(), small_wrong=(), small_cost=0.00002):
    """Ten items per category. `low` are the small model's unsure answers; the wrong sets answer another category."""
    items, small, large = [], [], []
    for category in CATEGORIES:
        other = "meals" if category != "meals" else "software"
        for number in range(10):
            item_id = f"{category}-{number}"
            items.append({"id": item_id, "label": category})
            small.append(prediction(item_id, other if item_id in small_wrong else category,
                                    0.5 if item_id in low else 0.99, cost=small_cost))
            large.append(prediction(item_id, other if item_id in large_wrong else category, None, cost=0.0001))
    return items, small, large


UNSURE = {f"{category}-0" for category in CATEGORIES}


def test_the_cascade_pays_the_small_model_always_and_the_large_one_only_for_what_it_sends_up():
    _, small, large = cascade_case(low=UNSURE)
    answers = {answer["id"]: answer for answer in cascade(small, large, 0.80)}
    assert answers["meals-1"]["model"] == "small" and answers["meals-1"]["cost"] == pytest.approx(0.00002)
    assert answers["meals-0"]["model"] == "large" and answers["meals-0"]["cost"] == pytest.approx(0.00012)
    assert answers["meals-0"]["small"]["confidence"] == 0.5 and "unsure" in answers["meals-0"]["reason"]


def test_a_cascade_that_keeps_what_it_knows_ships():
    release = decide_cascade(*cascade_case(low=UNSURE, large_wrong={"meals-9", "telecom-9"}), 0.80)
    assert release["decision"] == "ship" and release["reasons"] == []
    assert (release["kept"], release["routed"], release["coverage"]) == (90, 10, 0.9)


@pytest.mark.parametrize("case, failing", [
    ({"low": {f"{category}-{number}" for category in CATEGORIES for number in range(4)}}, "small model labels enough alone"),
    ({"low": UNSURE, "small_wrong": {"meals-5"}}, "small model within a point of the large one on what it keeps"),
    ({"low": UNSURE, "small_cost": 0.0002}, "cheaper per item"),
    ({"low": {f"telecom-{number}" for number in range(10)}, "large_wrong": {f"telecom-{number}" for number in range(5)}},
     "every category above the floor"),
])
def test_each_cascade_check_holds_the_release_on_its_own(case, failing):
    release = decide_cascade(*cascade_case(**case), 0.80)
    assert release["decision"] == "hold"
    assert [check["name"] for check in release["checks"] if not check["passed"]] == [failing]


def test_a_cascade_more_than_a_point_behind_the_large_model_is_held():
    release = decide_cascade(*cascade_case(low=UNSURE, small_wrong={"meals-5", "telecom-5"}), 0.80)
    assert any("cascade accuracy within a point" in reason for reason in release["reasons"])


def test_with_no_predictions_the_cascade_holds():
    assert decide_cascade([], [], [], 0.80)["decision"] == "hold"


def test_a_review_decision_has_the_queue_export_shape_and_only_known_categories():
    assert decision_row(ITEM, "airfare", "you") == {**ITEM, "label": "airfare", "reviewer": "you"}
    with pytest.raises(ValueError):
        decision_row(ITEM, "groceries", "you")


def test_the_adapter_card_names_its_data_its_run_and_the_decision():
    from corvane.release import adapter_card

    tuned = score(*items_and_predictions({"telecom": 2}))
    large = score(*items_and_predictions({"telecom": 1}, cost=0.0001))
    trained = {"base_model": "Qwen/Qwen3-0.6B", "revision": "abc", "dataset_version": "dbc198f308fb",
               "kernel": "someone/corvane-lora-1", "gpu": "Tesla T4", "gpu_minutes": 6.2}
    release = decide_cascade(*cascade_case(low=UNSURE), 0.80)
    card = adapter_card(trained, tuned, large, release)
    assert card["dataset_version"] == "dbc198f308fb" and card["trained_on"]["gpu_minutes"] == 6.2
    assert card["held_out"]["recall"]["telecom"] == 0.8 and card["decision"] == "ship"
    assert card["cascade"] == {"threshold": 0.80, "kept": 90, "routed": 10, "coverage": 0.9, "accuracy": 1.0,
                               "cost_per_thousand": 0.03}
