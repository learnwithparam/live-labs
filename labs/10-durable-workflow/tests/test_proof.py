"""The lab's promise, on a real model and a real worker killed after every payout: the plain script pays
some refunds twice, and the Temporal workflow pays every refund once, loses none, and asks the model
again only for a decide the crash interrupted."""

import json
from pathlib import Path

import pytest

from refunds.cli import review


@pytest.mark.proof
def test_the_workflow_survives_every_crash_and_the_plain_script_does_not():
    assert review() == 0
    got = json.loads(Path("out/review.json").read_text())
    naive, durable = got["naive"], got["durable"]
    assert durable["paid_twice"] == 0 and durable["lost"] == 0, f"{durable}"
    # Every model call is a decide attempt the history records: a finished decision is never asked again.
    assert durable["model_calls"] == got["decide_attempts"], f"{durable} vs {got['decide_attempts']} attempts"
    assert got["worker_deaths"] >= 1, "no worker died, so nothing was proven"
    assert naive["paid_twice"] >= 1, f"the plain script never paid twice: {naive}"
