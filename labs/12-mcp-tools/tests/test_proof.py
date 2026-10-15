"""The lab's promise, on a real model against the store: the same assistant does at least as many of the
merchant's tasks, in fewer tool calls and with no more wrong ones, when its MCP tools are designed for
the job, and asks which one when a request is ambiguous. A strong model can finish most tasks on the raw
API, so the saving shows in calls; a weaker one also fails tasks there. The score is read from the
store's state, never from what the assistant said."""

import json
from pathlib import Path

import pytest

from tidepool.cli import review_all


@pytest.mark.proof
def test_designed_tools_beat_the_api_as_is_and_ask_when_the_request_is_ambiguous():
    assert review_all() == 0
    results = json.loads(Path("out/review.json").read_text())
    api, designed = results["api"]["score"], results["designed"]["score"]
    assert designed["succeeded"] >= api["succeeded"], (api, designed)
    assert designed["calls"] < api["calls"], (api, designed)
    assert designed["succeeded"] >= 18, designed
    # A wrong call is a step the model wasted on a tool that misled it.
    assert designed["wrong_calls"] <= api["wrong_calls"], (api, designed)
    for row in results["designed"]["rows"]:
        if row["task"] in ("TP-11", "TP-18"):
            assert row["ok"], row
