"""The lab's promise, against a real model: the grounding check lets almost no unsupported reply reach a
courier, holds almost no good one, and the rubric judge agrees with support agents well past chance,
and better than a judge with no sources."""

from pathlib import Path

import pytest

from courier.agree import load_labelled
from courier.cli import client_and_models
from courier.desk import review
from courier.kb import HelpCentre, load

LAB = Path(__file__).parent.parent
MAX_SENT = 1  # unsupported replies the guard may let through, of 8
MAX_HELD = 2  # good replies it may hold for an agent, of 10
MIN_AGREE = 18  # of 20 agent verdicts the judge must match
MIN_KAPPA = 0.7


@pytest.mark.proof
def test_the_guard_stops_unsupported_replies_and_the_judge_agrees_with_agents():
    client, model, judge = client_and_models()
    got = review(load_labelled(LAB / "data/labelled.jsonl"), HelpCentre(load(LAB / "data/help")), client, model, judge)
    assert len(got["guard"]["sent"]) <= MAX_SENT, f"{model} sent {got['guard']['sent']}"
    assert len(got["guard"]["held_good"]) <= MAX_HELD, f"{model} held {got['guard']['held_good']}"
    assert got["judge"]["agree"] >= MIN_AGREE and got["judge"]["kappa"] >= MIN_KAPPA, f"{judge}: {got['judge']}"
    assert got["judge"]["agree"] > got["naive"]["agree"], f"naive {got['naive']}, judge {got['judge']}"
