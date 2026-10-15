"""The lab's promise, on a real model: on the same sampled refunds and the same 5% review budget, the
calibrated decision model catches more of the fraud found later than an LLM asked for a 0 to 100 score,
and its chances are closer to what happened. Every grade comes from the history, never from a model."""

import json
from pathlib import Path

import pytest

from fennick.cli import ask_llm, backtest, train


@pytest.mark.proof
def test_the_decision_model_beats_an_llm_score_at_the_same_review_rate():
    assert train() == 0 and backtest() == 0 and ask_llm() == 0
    compared = json.loads(Path("out/llm.json").read_text())
    budget, calibration = compared["at_budget"], compared["calibration"]
    assert budget["model"]["reviewed"] == budget["llm"]["reviewed"]
    assert budget["model"]["caught"] > budget["llm"]["caught"], budget
    assert budget["model"]["caught"] > budget["hand_rule"]["caught"], budget
    assert calibration["model"]["brier"] < calibration["llm"]["brier"], calibration
    assert compared["missing"] <= compared["refunds"] * 0.02, compared["missing"]
    result = json.loads(Path("out/backtest.json").read_text())
    assert result["live"]["hold"]["precision"] >= 0.8, result["live"]["hold"]
