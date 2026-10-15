"""The lab's promise, on a real model: structured readings priced in code land near the repairer's
bill more often than a free-text estimate, and the doubtful claims reach an adjuster with the right
reason. Every number is graded against the adjuster's file, never against what the model said."""

import json
from pathlib import Path

import pytest

from saltmarsh.cli import assess_all, naive_all


@pytest.mark.proof
def test_structured_estimates_beat_free_text_and_the_doubtful_claims_reach_an_adjuster():
    assert assess_all() == 0 and naive_all() == 0
    run, naive = json.loads(Path("out/run.json").read_text()), json.loads(Path("out/naive.json").read_text())
    score = run["score"]
    assert score["within_tolerance"] >= 2, score
    assert score["within_tolerance"] >= naive["within_tolerance"], (score, naive["within_tolerance"])
    assert score["routed_right"] >= 9, score
    assert score["reasons_caught"] >= 3, score
    assert score["panels"]["recall"] >= 0.7, score["panels"]
    # The second photo on SM-1050 is another car: paying on it is the costly mistake.
    swapped = next(result for result in run["results"] if result["claim"] == "SM-1050")
    assert swapped["route"]["to"] == "adjuster"
    assert "photos_disagree" in {reason["code"] for reason in swapped["route"]["reasons"]}, swapped["route"]
