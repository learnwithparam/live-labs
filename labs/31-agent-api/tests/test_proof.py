"""The lab's promise, through Envoy in Docker. The truth is the gateway's access log.

On the stand-in model: under the load test the people stay fast, the noisy script gets 429s, nothing
unauthorised reaches the agent, and the tenant at its workspace limit goes to the operator. On the real
model: the first token reaches the caller well before the answer ends, and a hang-up cancels the call.
"""

import json
import subprocess
import sys
import urllib.request

import pytest

from mosswood import gateway
from mosswood.cli import LOAD, ledger_row, logged
from mosswood.client import stream_answer
from mosswood.tokens import issue

pytestmark = pytest.mark.proof


def assistant(*args: str) -> str:
    done = subprocess.run([sys.executable, "-m", "mosswood.cli", *args], capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


@pytest.fixture(scope="module", autouse=True)
def stack():
    gateway.LIMITS_FILE.unlink(missing_ok=True)  # start from each plan's own limit
    yield
    assistant("down")
    gateway.LIMITS_FILE.unlink(missing_ok=True)  # leave the plans' own limits for the next run


def test_under_load_people_stay_fast_and_the_script_gets_429s():
    assistant("up", "--upstream", "stub")
    assistant("load", "--target", "direct")
    assistant("load", "--target", "gateway")
    naive = json.loads((LOAD / "direct.json").read_text())["client"]["person"]
    numbers = json.loads((LOAD / "gateway.json").read_text())["gateway"]
    people = [numbers["tenants"]["harlow"], numbers["tenants"]["pemberton"]]
    # The metric, from the gateway's log: p95 time to first token and error rate.
    # The stand-in needs 450 ms for a tool call and a first word. About 40 questions are answered, so the
    # p95 is the second slowest; the bar leaves room for a laptop running other work at the same time.
    assert numbers["overall"]["p50_first_token_ms"] < 1000
    assert numbers["overall"]["p95_first_token_ms"] < 3000
    assert numbers["overall"]["error_rate"] <= 0.02
    assert all(tenant["errors"] == 0 and tenant["refused_tenant"] == 0 for tenant in people), people
    assert numbers["users"]["pemberton/pf-sync-bot"]["refused_user"] > 100
    assert numbers["users"]["pemberton/priya.raman"]["refused_user"] == 0
    assert numbers["unauthorized_reached_agent"] == 0
    # Hit directly, with no limits, the same load fails a large share of the people's questions.
    assert naive["error_rate"] > 0.2
    assert [case["tenant"] for case in numbers["needs_operator"]] == ["ninebark"]


def test_the_operator_raises_the_tenant_limit_and_the_gateway_enforces_it():
    assistant("limit", "ninebark", "--raise-to", "12", "--reason", "four scientists, month-end audit")
    with urllib.request.urlopen("http://127.0.0.1:8311/config_dump", timeout=10) as response:
        dump = response.read().decode()
    assert '"value": "ninebark"' in dump and '"max_tokens": 12' in dump
    assert gateway.decisions()["ninebark"]["limit"] == 12


def test_missing_and_expired_tokens_stop_at_the_gateway():
    for token in (None, issue("harlow", "amara.osei", seconds=-120)):
        reply = stream_answer("What is overdue?", token)
        assert reply.status == 401
        row = logged(reply.request_id)
        assert row and row["status"] == 401 and row["upstream"] is None


def test_real_model_streams_the_first_token_before_the_answer_ends():
    assistant("up")
    reply = stream_answer("Write a full report on every project and every task, in detail: what is late, what is blocked, and why.",
                          issue("pemberton", "priya.raman"))
    assert reply.status == 200 and reply.finish and not reply.error and "PF-312" in reply.text
    # A fast model writes a few hundred words in well under a second, so the margin is 100 ms, not more.
    assert reply.first_text_ms < reply.total_ms - 100
    row = logged(reply.request_id)
    assert row["status"] == 200 and row["first_byte_ms"] < row["duration_ms"] - 100
    print(f"real model: first token {reply.first_text_ms:.0f} ms, end {reply.total_ms:.0f} ms, cost ${reply.finish['cost']:.6f}")


def test_hanging_up_cancels_the_real_model_call():
    reply = stream_answer("Write a full report on every project and every task, in detail.", issue("harlow", "jonah.weiss"), hang_up_after=5)
    assert reply.hung_up
    record = ledger_row(reply.request_id, seconds=10)
    assert record["status"] == "cancelled"
    assert "DC" in logged(reply.request_id)["flags"]
