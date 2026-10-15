"""The lab's promise, on a real model and real Mem0: memories kept like ChatGPT's are recalled and
confirmed by the customer, with almost no stale or forgotten fact left, while Mem0's append-only `add`
keeps every one. The score is read from the memory store against the customers' own words."""

import json
from pathlib import Path

import pytest

from pawsley.cli import review_all


@pytest.mark.proof
def test_kept_memories_drop_stale_facts_that_append_only_keeps_and_ask_on_a_conflict():
    assert review_all() == 0
    results = json.loads(Path("out/review.json").read_text())
    naive, kept = results["append-only"]["score"], results["kept"]["score"]
    # A wrong memory is a moved address, an old weight, a forgotten phone number still on file.
    assert naive["wrong"] >= 4 and kept["wrong"] <= 1, (naive, kept)
    assert kept["confirmed"] >= 0.9 * kept["recalled"], kept
    assert kept["facts_kept"] >= 0.85 * kept["facts"], kept
    # The chicken jerky message waits for Priya instead of quietly overwriting the allergy.
    assert kept["asks_met"] == kept["asks"], kept
