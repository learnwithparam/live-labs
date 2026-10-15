"""Run the decision model over months it never saw, as if each refund arrived that day.

The lines are picked on the calibration month and then frozen, so the test months are judged the way
production would be: the review rate drifts, fraud found later is the grade, and every decision leaves
an audit row naming the model version and the lines it used.
"""

import numpy

from fennick import decide, measure, model
from fennick.history import is_fraud

RATES = [round(0.01 * step, 2) for step in range(1, 21)]  # the slider's range: 1% to 20% reviewed
QUEUE_FIELDS = ["refund_id", "requested_at", "buyer_id", "category", "item", "amount", "reason", "delivery_scan",
                "days_since_delivery", "return_scanned", "account_age_days", "orders_lifetime", "refunds_30d",
                "address_changed_7d", "card_country_match"]


def run(rows: list[dict], models: dict, version: str) -> tuple[dict, list[dict]]:
    """Score the test window, decide each refund, and measure the result against what was found later.

    Returns the summary the page and the proof read, and one audit row per decision.
    """
    calibrate_rows, test_rows = model.window(rows, "calibrate"), model.window(rows, "test")
    review_line = decide.line_for_rate(model.risk(models, calibrate_rows), decide.REVIEW_RATE)
    chances, raw = model.risk(models, test_rows), model.risk(models, test_rows, calibrated=False)
    rule = numpy.array([decide.hand_rule(row) for row in test_rows])
    frauds = model.labels(test_rows)

    audit, queue = [], []
    for row, chance in zip(test_rows, chances):
        action = decide.action(float(chance), review_line)
        signals = decide.signals(row)
        audit.append({"refund_id": row["refund_id"], "decided_on": row["requested_at"], "model_version": version,
                      "score": round(float(chance), 4), "review_line": round(review_line, 4),
                      "hold_at": decide.HOLD_AT, "action": action, "signals": signals})
        queue.append({**{name: row[name] for name in QUEUE_FIELDS}, "score": round(float(chance), 4),
                      "action": action, "signals": signals, "fraud": is_fraud(row),
                      "found_later": row["found_later"], "found_on": row["found_on"]})

    stopped = chances >= review_line
    summary = {
        "model_version": version, "trained_on": models["trained_on"], "calibrated_on": models["calibrated_on"],
        "window": {"from": test_rows[0]["requested_at"], "to": test_rows[-1]["requested_at"],
                   "refunds": len(test_rows), "frauds": int(frauds.sum())},
        "lines": {"review": review_line, "hold": decide.HOLD_AT, "budget": decide.REVIEW_RATE},
        # With the frozen lines: what share really went to a person, and how much fraud it stopped.
        "live": {"review_rate": float(stopped.mean()), "stopped": int(stopped.sum()),
                 "caught": int(frauds[stopped].sum()), "paid_fraud": int(frauds[~stopped].sum()),
                 "hold": measure.precision_above(chances, frauds, decide.HOLD_AT)},
        # Every scorer gets the same budget: its riskiest 5% of the test window.
        "at_budget": {"model": measure.caught_at_rate(chances, frauds, decide.REVIEW_RATE),
                      "hand_rule": measure.caught_at_rate(rule, frauds, decide.REVIEW_RATE)},
        "calibration": {"calibrated": {"brier": measure.brier(chances, frauds), **measure.reliability(chances, frauds)},
                        "raw": {"brier": measure.brier(raw, frauds), **measure.reliability(raw, frauds)}},
        "curve": {"model": measure.curve(chances, frauds, RATES), "hand_rule": measure.curve(rule, frauds, RATES)},
    }
    return {**summary, "queue": queue}, audit


def compare_with_llm(rows: list[dict], models: dict, asked: dict) -> dict:
    """The model, the hand rule and the LLM on the same sampled refunds and the same 5% budget.

    The LLM's 0 to 100 is read as a chance, as a team would read it. Its Brier score shows whether that
    reading is safe; the decision model's isotonic map was fitted for exactly this.
    """
    sampled = [row for row in model.window(rows, "test") if row["refund_id"] in asked["scores"]]
    frauds = model.labels(sampled)
    chances = model.risk(models, sampled)
    llm = numpy.array([asked["scores"][row["refund_id"]] / 100 for row in sampled])
    rule = numpy.array([decide.hand_rule(row) for row in sampled])
    return {
        "model": asked["model"], "refunds": len(sampled), "frauds": int(frauds.sum()), "usage": asked["usage"],
        "missing": len(asked["missing"]), "calls": asked["calls"],
        "at_budget": {"model": measure.caught_at_rate(chances, frauds, decide.REVIEW_RATE),
                      "llm": measure.caught_at_rate(llm, frauds, decide.REVIEW_RATE),
                      "hand_rule": measure.caught_at_rate(rule, frauds, decide.REVIEW_RATE)},
        "calibration": {"model": {"brier": measure.brier(chances, frauds), **measure.reliability(chances, frauds)},
                        "llm": {"brier": measure.brier(llm, frauds), **measure.reliability(llm, frauds)}},
        "scores": asked["scores"],
    }
