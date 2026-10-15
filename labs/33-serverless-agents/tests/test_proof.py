"""The lab's promise, on a real model: the host is killed with SIGKILL partway through the night, and still
every job reaches an outcome, no invoice is written twice, and no model step that finished is called
again. The truth is the job ledger, the runner's history and finance's hand-checked totals."""

import json
import os

import pytest

from meterline import billing
from meterline.batch import DONE, run_night
from meterline.config import load_dotenv
from meterline.naive import run_naive
from meterline.review import answer
from meterline.runtime import LocalCloud

# Its own ports and folder, so the proof never touches the run you made by hand in out/.
RUNNER_PORT, HOST_PORT = 8333, 8334


@pytest.mark.proof
def test_the_night_survives_a_crash_on_a_real_model(tmp_path):
    load_dotenv()
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY is not set")
    cloud = LocalCloud(tmp_path, RUNNER_PORT, HOST_PORT)
    try:
        report = run_night(cloud, crash_after_writes=12)
        metrics, lanes = report["metrics"], {lane["customer_id"]: lane for lane in report["lanes"]}
        assert report["crash"]["exit_code"] == -9
        assert metrics["reached_outcome"] == metrics["jobs"] == 30
        assert metrics["duplicate_writes"] == 0
        assert metrics["completed_model_steps_recalled"] == 0
        # Fail closed: a held job has no ledger row until a person answers.
        assert metrics["held_written"] == []
        assert {"MC-119", "MC-122"} <= {name for name, lane in lanes.items() if lane["outcome"] == "held"}
        assert lanes["MC-117"]["outcome"] == "stopped"
        assert "after 3 attempts" in lanes["MC-117"]["reason"]
        # The agent's corrections are graded against finance's own totals, not against its own claims.
        checked = billing.checked_totals()
        for lane in lanes.values():
            for row in lane["ledger"]:
                assert row["total"] == pytest.approx(checked[lane["customer_id"]], abs=0.011), lane["customer_id"]
        held = [name for name, lane in lanes.items() if lane["outcome"] == "held"]
        for customer_id in held:
            decided = answer(cloud, customer_id, customer_id == "MC-119", "Priya Nair, finance")
            assert decided["outcome"] == ("approved" if customer_id == "MC-119" else "rejected")
            assert len(decided["ledger"]) == (1 if customer_id == "MC-119" else 0)
        executions = json.loads((tmp_path / "run.json").read_text())["executions"]
        assert all(cloud.status(executions[customer_id])["Status"] in DONE for customer_id in held)
    finally:
        cloud.stop()
    naive = run_naive(tmp_path, crash_after_writes=12)
    assert naive["duplicate_writes"] >= 1

