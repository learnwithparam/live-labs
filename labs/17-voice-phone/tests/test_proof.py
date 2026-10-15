"""The lab's promise, on a real model and real audio: ten scripted callers ring the line through a local
LiveKit server, and most reach the outcome they rang for. Every verdict reads the booking system each
call changed and the handoff queue, never the agent's own account of the call."""

import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.proof
def test_most_simulated_callers_reach_their_outcome_and_every_transfer_names_its_reason():
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("needs LLM_API_KEY")
    subprocess.run(["phone", "review"], check=True)
    report = json.loads(Path("out/review.json").read_text())
    score = report["score"]
    assert score["calls"] == 10, score
    # Local speech on a laptop mishears a word now and then; 8 of 10 leaves room for one or two.
    assert score["share"] >= 0.8, [(call["id"], call["why"]) for call in report["calls"] if not call["reached"]]
    # A transfer is the right result only for the callers who need a person, and each carries its reason.
    expected = {call["id"]: call["expect"]["reason"] for call in report["calls"] if call["expect"]["outcome"] == "transferred"}
    assert all(expected.get(transfer["id"]) == transfer["reason"] for transfer in score["transfers"]), score["transfers"]
    assert score["resolved_without_person"] >= 5, score
    assert 0 < score["cost"] < 0.05, score
