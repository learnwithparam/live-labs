"""The whole night with no key: the real SDK runner, the real host process, a real SIGKILL after twelve
ledger writes, and a scripted model behind a local HTTP endpoint. The metric is asserted from the
runner's history and the ledger, the same way the proof does it on a real model."""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from fakes import FakeModel
from meterline.batch import run_night
from meterline.naive import run_naive
from meterline.review import answer
from meterline.runtime import LocalCloud
from meterline.serve import start

RUNNER_PORT, HOST_PORT, MODEL_PORT = 8335, 8336, 8338


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    out = tmp_path_factory.mktemp("night")
    fake = FakeModel(MODEL_PORT, delay=0.4)
    saved = {name: os.environ.get(name) for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")}
    os.environ.update(LLM_BASE_URL=fake.base_url, LLM_API_KEY="keyless-test", LLM_MODEL="openai/gpt-6-luna")
    cloud = LocalCloud(out, RUNNER_PORT, HOST_PORT)
    try:
        report = run_night(cloud, crash_after_writes=12)
    finally:
        cloud.stop()
    yield {"out": out, "report": report, "fake": fake}
    fake.close()
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_the_host_really_died_and_a_new_process_finished_the_night(night):
    crash = night["report"]["crash"]
    assert crash["exit_code"] == -9
    assert crash["new_process_id"] != crash["process_id"]
    assert any(lane["resumed"] for lane in night["report"]["lanes"])


def test_every_job_reaches_an_outcome_with_no_duplicate_write_and_no_completed_call_repeated(night):
    metrics = night["report"]["metrics"]
    assert metrics["reached_outcome"] == metrics["jobs"] == 30
    assert metrics["duplicate_writes"] == 0
    assert metrics["completed_model_steps_recalled"] == 0
    assert metrics["ledger_rows"] == metrics["outcomes"]["matched"] + metrics["outcomes"]["corrected"]
    assert metrics["held_written"] == []


def test_the_retry_budget_stops_a_job_with_its_reason(night):
    lane = next(lane for lane in night["report"]["lanes"] if lane["customer_id"] == "MC-117")
    assert lane["outcome"] == "stopped"
    assert lane["reason"].startswith("Stopped after 3 attempts: No September usage export for MC-117")
    assert [segment["state"] for segment in lane["segments"]] == ["failed", "failed", "failed"]
    assert lane["ledger"] == []


def request(url: str, body: dict | None = None, origin: str | None = None) -> tuple[int, dict | str]:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=90) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_finance_approves_one_held_job_on_the_console_and_rejects_the_other(night):
    held = {lane["customer_id"] for lane in night["report"]["lanes"] if lane["outcome"] == "held"}
    assert held == {"MC-119", "MC-122"}
    calls_before = len(night["fake"].calls)
    cloud = LocalCloud(night["out"], RUNNER_PORT, HOST_PORT)
    server, _ = start(cloud, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    console = f"http://127.0.0.1:{server.server_port}"
    try:
        assert request(f"{console}/api/run", origin="https://evil.example")[0] == 403
        status, state = request(f"{console}/api/run")
        assert status == 200 and state["report"]["metrics"]["reached_outcome"] == 30
        assert request(f"{console}/api/review", {"customer_id": "MC-119", "decision": "approve"})[0] == 400
        status, approved = request(f"{console}/api/review", {"customer_id": "MC-119", "decision": "approve", "reviewer": "Priya Nair"})
        assert status == 200
        rejected = answer(cloud, "MC-122", False, "Priya Nair")
        assert request(f"{console}/api/review", {"customer_id": "MC-119", "decision": "approve", "reviewer": "Priya Nair"})[0] == 409
    finally:
        server.shutdown()
        cloud.stop()
    assert approved["outcome"] == "approved"
    assert [(row["total"], row["approved_by"]) for row in approved["ledger"]] == [(9000.0, "Priya Nair")]
    assert rejected["outcome"] == "rejected" and rejected["ledger"] == []
    # Resuming after the wait replayed the finished steps from checkpoints: no new model call was paid for.
    assert len(night["fake"].calls) == calls_before


def test_the_plain_loop_pays_twice_and_writes_twice(night):
    report = run_naive(night["out"], crash_after_writes=12)
    assert report["lives"][0]["exit_code"] == -9
    assert report["duplicate_writes"] >= 12
    assert report["model_calls_repeated"] >= 12
    assert report["completed_model_steps_recalled"] >= 12
    assert report["customers_written"] == 27 and report["ledger_rows"] == 27 + report["duplicate_writes"]
