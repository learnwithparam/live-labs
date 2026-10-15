"""The lab's promise, on a real model: the gate that reads tool trajectories blocks more bad releases
than the gate that reads answers only, a release that drops the approval step is among them, and a
harmless prompt change still ships. The truth is the golden set and its signed-off trajectories."""

import json
from pathlib import Path

import pytest

from kettlebay.assistant import load_golden
from kettlebay.cli import check
from kettlebay.config import REPEATS
from kettlebay.gate import verdict

CANDIDATES = ("shorter-replies", "faster-writes", "cheaper-model", "order-numbers")


@pytest.mark.proof
def test_reading_trajectories_catches_regressions_an_answer_only_gate_ships():
    exits = {name: check(f"releases/{name}.json", fresh_live=name == CANDIDATES[0], repeats=REPEATS) for name in CANDIDATES}
    golden, live_run = load_golden(), json.loads(Path("runs/live.json").read_text())
    full, answers_only = {}, {}
    for name in CANDIDATES:
        candidate_run = json.loads(Path(f"runs/{name}.json").read_text())
        full[name] = verdict(golden, live_run, candidate_run)
        answers_only[name] = verdict(golden, live_run, candidate_run, with_trajectory=False)
        assert not full[name].broken, name  # every case measured: no timeouts, nothing missing
    assert exits["order-numbers"] == 0 and not full["order-numbers"].blocked
    assert exits["faster-writes"] == 1 and full["faster-writes"].unsafe and not answers_only["faster-writes"].blocked
    # Not its exit code: a person may have overridden this one, which the README shows how to do.
    assert full["shorter-replies"].blocked and answers_only["shorter-replies"].blocked
    # The metric: regressions caught before release, by each gate.
    caught_full = sum(result.blocked for result in full.values())
    caught_answers = sum(result.blocked for result in answers_only.values())
    assert caught_full >= 2 and caught_full > caught_answers
