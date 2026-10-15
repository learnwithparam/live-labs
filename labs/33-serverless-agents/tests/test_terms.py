"""Keyless tests of the code that refuses to trust the model: every number needs a verbatim quote, the
sum is done in code, and anything unsure or large is held for finance instead of written."""

import json

from fakes import read_like_a_model
from meterline import billing
from meterline.terms import check_terms, decide, expected_total


def job_for(customer_id: str) -> dict:
    return billing.fetch(customer_id)


def test_a_faithful_reading_matches_finances_hand_checked_totals():
    for customer_id in billing.tonights_jobs():
        if customer_id in ("MC-117", "MC-119", "MC-122"):
            continue
        decision = decide(json.dumps(read_like_a_model(job_for(customer_id)["contract"])), job_for(customer_id))
        assert decision["action"] == "write", customer_id
        assert abs(decision["proposed_total"] - billing.checked_totals()[customer_id]) < 0.05, customer_id


def test_a_number_no_quote_states_is_held_never_written():
    job = job_for("MC-101")
    decision = decide(json.dumps(read_like_a_model(job["contract"], wrong_fee=True)), job)
    assert decision["action"] == "hold"
    assert any("monthly_fee = 10500.0 is not stated" in reason for reason in decision["reasons"])


def test_a_paraphrased_quote_is_not_evidence():
    job = job_for("MC-101")
    reading = read_like_a_model(job["contract"])
    reading["quotes"] = ["The fee is $9,500.00 a month for 2,000,000 calls, and $1.20 per 1,000 after that."]
    decision = decide(json.dumps(reading), job)
    assert decision["action"] == "hold"
    assert decision["reasons"][0].startswith("Quote not found in the contract")


def test_a_contract_that_points_at_a_missing_order_form_is_held():
    job = job_for("MC-122")
    decision = decide(json.dumps(read_like_a_model(job["contract"])), job)
    assert decision["action"] == "hold"
    assert decision["reasons"] == ["The customer used more than the included calls, and the contract text states no overage price."]


def test_a_change_over_the_unattended_limit_waits_for_finance():
    job = job_for("MC-119")
    decision = decide(json.dumps(read_like_a_model(job["contract"])), job)
    assert decision["action"] == "hold" and decision["proposed_total"] == 9000.0
    assert "Changes over 1,500 wait for finance" in decision["reasons"][0]


def test_a_reply_that_is_not_json_is_held():
    terms, problems = check_terms("Sure! The fee is $9,500.", "contract", 10)
    assert terms is None and problems == ["The model's reply is not JSON."]


def test_the_cap_and_discount_are_applied_in_code():
    terms = {"monthly_fee": 4000.0, "included_calls": 1_000_000, "overage_per_1000": 1.0,
             "overage_discount_percent": 10, "monthly_cap": 5000.0}
    assert expected_total(terms, 2_000_000) == 4900.0
    assert expected_total(terms, 3_000_000) == 5000.0


def test_a_missing_usage_export_raises_so_the_retry_budget_decides():
    try:
        billing.fetch("MC-117")
    except billing.MissingExport as error:
        assert "MC-117" in str(error)
    else:
        raise AssertionError("MC-117 has no usage row and must raise")


def test_a_quote_cut_from_inside_a_number_states_no_new_number():
    # "20 per 1,000 calls" is a substring of "$1.20 per 1,000 calls", but the contract never states a 20.
    job = job_for("MC-101")
    reading = read_like_a_model(job["contract"])
    reading["overage_discount_percent"] = 20
    reading["quotes"].append("20 per 1,000 calls")
    decision = decide(json.dumps(reading), job)
    assert decision["action"] == "hold"
    assert decision["reasons"] == ["overage_discount_percent = 20 is not stated in any quote from the contract."]


def test_a_price_written_without_a_leading_zero_is_read_as_cents():
    contract = "Customer pays $500.00 a month for 1,000 API calls. Overage is $.75 per 1,000 calls."
    reading = {"monthly_fee": 500.0, "included_calls": 1000, "overage_per_1000": 75.0,
               "overage_discount_percent": 0, "monthly_cap": None, "quotes": contract.split(". ")}
    _, problems = check_terms(json.dumps(reading), contract, 2000)
    assert problems == ["overage_per_1000 = 75.0 is not stated in any quote from the contract."]
    reading["overage_per_1000"] = 0.75
    assert check_terms(json.dumps(reading), contract, 2000)[1] == []
