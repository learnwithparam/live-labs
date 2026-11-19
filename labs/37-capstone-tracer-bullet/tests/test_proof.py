"""The lab's promise, with numbers, on a real model and Jaeger's own span store: the slice completes enough
requests, stays inside the p95 target, and every request has a full span tree. Needs LLM_API_KEY and Jaeger."""

import json

import pytest

from brickhaven_slice import cli
from brickhaven_slice.config import COMPLETION_THRESHOLD, OUT, P95_TARGET_MS


def board(mode: str) -> dict:
    assert cli.run(mode) == 0
    assert cli.report(mode) == 0
    return json.loads((OUT / f"board-{mode}.json").read_text())


@pytest.mark.proof
def test_the_slice_completes_inside_the_target_with_every_span_tree_whole():
    naive, shipped = board("naive"), board("slice")
    totals = shipped["totals"]
    # The metric: completion and p95 trace latency, both read from the spans in Jaeger.
    assert totals["completion_rate"] >= COMPLETION_THRESHOLD
    assert totals["p95_ms"] is not None and totals["p95_ms"] <= P95_TARGET_MS
    # The truth: every request has one complete tree, and no request vanished.
    assert totals["complete_trees"] == totals["requests"] and totals["lost"] == 0
    assert all(card["complete"] for card in shipped["cards"])
    # The naive version: the same requests, with gaps and lost work the slice does not have.
    assert naive["totals"]["complete_trees"] < totals["complete_trees"]
    assert naive["totals"]["completion_rate"] < totals["completion_rate"]
