"""The lab's promise, on a real model with local speech: the phone line keys most orders exactly as a
person keyed them from the same call, hands the two it cannot hold to staff with a reason, and every
answered turn has its latency measured. The score is read from the till, never from what the agent said."""

import json
from pathlib import Path

import pytest

from lucca.review import review


@pytest.mark.proof
def test_the_line_takes_orders_with_no_staff_and_hands_off_what_the_menu_cannot_hold():
    review()
    results = json.loads(Path("out/calls.json").read_text())
    metric = results["metric"]
    assert metric["taken_alone"] >= 5, metric
    assert metric["staff_correct"] == metric["staff_expected"] == 2, metric
    for row in results["rows"]:
        if row["staff_expected"]:
            # Nothing reaches the kitchen, and staff are told why they are calling back.
            assert row["ticket"]["status"] == "staff" and row["ticket"]["staff_reason"], row["why"]
    # Each caller line got an answer the clock could time, so p50 and p95 cover the whole shift.
    assert metric["timed_turns"] >= metric["turns"] - 1, metric
    assert metric["p50_ms"] and metric["p95_ms"] >= metric["p50_ms"], metric
