"""The lab's metric with no key: on months it never saw, the calibrated decision model catches more of the
fraud found later than the hand rule at the same 5% review rate, and its chances hold up."""

import json

import numpy
import pytest

from fennick import backtest, decide, history, measure, model


@pytest.fixture(scope="module")
def trained():
    rows = history.load()
    models = model.train(rows)
    return rows, models, *backtest.run(rows, models, "test-version")


def test_the_split_is_by_time_and_never_overlaps():
    rows = history.load()
    train, calibrate, test = (model.window(rows, name) for name in ("train", "calibrate", "test"))
    assert len(train) + len(calibrate) + len(test) == len(rows)
    assert max(row["requested_at"] for row in train) < min(row["requested_at"] for row in calibrate)
    assert max(row["requested_at"] for row in calibrate) < min(row["requested_at"] for row in test)


def test_the_model_catches_far_more_fraud_than_the_hand_rule_at_the_same_review_rate(trained):
    _, _, result, _ = trained
    model_score, rule_score = result["at_budget"]["model"], result["at_budget"]["hand_rule"]
    assert model_score["reviewed"] == rule_score["reviewed"]
    assert model_score["caught"] >= 2 * rule_score["caught"], (model_score, rule_score)
    assert model_score["recall"] >= 0.65, model_score


def test_calibration_makes_the_chances_hold_up(trained):
    _, _, result, _ = trained
    calibrated, raw = result["calibration"]["calibrated"], result["calibration"]["raw"]
    assert calibrated["brier"] < raw["brier"], (calibrated["brier"], raw["brier"])
    assert calibrated["error"] < 0.01 < raw["error"], (calibrated["error"], raw["error"])
    # The hold line's promise: refunds the model put at 80% or more really were fraud most of the time.
    assert result["live"]["hold"]["precision"] >= 0.8, result["live"]["hold"]


def test_lines_picked_on_may_keep_the_june_and_july_review_rate_near_the_budget(trained):
    _, _, result, _ = trained
    assert abs(result["live"]["review_rate"] - decide.REVIEW_RATE) < 0.015, result["live"]


def test_every_decision_leaves_an_audit_row_with_the_model_version_and_lines(trained):
    rows, _, result, audit = trained
    assert len(audit) == result["window"]["refunds"] == len(model.window(rows, "test"))
    row = audit[0]
    assert row["model_version"] == "test-version" and row["review_line"] == round(result["lines"]["review"], 4)
    assert {entry["action"] for entry in audit} == {"pay", "review", "hold"}
    json.dumps(audit)


def test_the_actions_follow_the_lines():
    assert decide.action(0.95, review_line=0.2) == "hold"
    assert decide.action(0.30, review_line=0.2) == "review"
    assert decide.action(0.10, review_line=0.2) == "pay"


def test_ties_give_no_lucky_break():
    # Four refunds with one score, the fraud last in file order: a 25% budget reviews the first only.
    scores, frauds = numpy.array([0.5, 0.5, 0.5, 0.5]), numpy.array([0, 0, 0, 1])
    assert measure.caught_at_rate(scores, frauds, 0.25)["caught"] == 0


def test_reliability_and_brier_on_a_known_case():
    chances, frauds = numpy.array([0.1] * 10 + [0.9] * 10), numpy.array([0] * 9 + [1] + [1] * 9 + [0])
    found = measure.reliability(chances, frauds)
    assert [round(group["happened"], 2) for group in found["bins"]] == [0.1, 0.9]
    assert found["error"] < 1e-9
    assert measure.brier(chances, frauds) == pytest.approx(0.09)


def test_signals_name_the_evidence_a_reviewer_checks():
    row = {"account_age_days": "4", "refunds_30d": "2", "reason": "not_received", "delivery_scan": "signed",
           "return_scanned": "", "address_changed_7d": "yes", "card_country_match": "no", "category": "gift_cards"}
    found = decide.signals(row)
    assert found[0] == "Claims not received; carrier has a signature"
    assert "Account 4 days old" in found and "2 other refunds in the last 30 days" in found
