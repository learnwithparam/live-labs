"""The session page end to end with a scripted model: a card waits for the page, and only this machine may answer."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from fakes import FakeOpenAI, call

from wrenfield.serve import Session, serve


@pytest.fixture
def page(tmp_path, monkeypatch, cerbos):
    monkeypatch.chdir(tmp_path)  # the session's scratch repos land under this test's own folder
    model = FakeOpenAI([[call("delete_branch", branch="release/2.4")], "Deleted release/2.4."])
    session = Session(lambda: (model, "openai/gpt-6-luna"), cerbos, tmp_path / "review.json", tmp_path / "audit.jsonl")
    server = serve(session, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", session
    server.shutdown()


def request(url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {})) as response:
        return json.loads(response.read())


def until(check, seconds: float = 20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("waited too long")


def test_the_state_carries_the_checked_policy_table_and_the_tools(page):
    base, _ = page
    state = request(f"{base}/api/state")
    assert all(cell["ok"] for cell in state["cells"])
    assert {tool["name"] for tool in state["tools"] if tool["destructiveHint"]} == {"delete_branch", "force_push", "run_migration", "run_sql"}


def test_a_card_waits_for_the_page_and_an_approve_runs_the_call(page):
    base, session = page
    run_id = request(f"{base}/api/run", {"mode": "policy", "person": "marcus.lee", "message": "Delete release/2.4."})["id"]
    pending = until(lambda: request(f"{base}/api/run/{run_id}")["pending"])
    assert pending["kind"] == "card" and pending["says"] == "The agent wants to delete branch release/2.4"
    assert request(f"{base}/api/run/{run_id}/answer", {"pending": pending["id"], "answer": True}) == {}
    finished = until(lambda: (view := request(f"{base}/api/run/{run_id}"))["done"] and view)
    assert finished["unapproved"] == 0
    entry = request(f"{base}/api/state")["audit"][-1]
    assert (entry["tool"], entry["verdict"], entry["approved_by"], entry["ran"], entry["target"]) == ("delete_branch", "ask", "card", True, "release/2.4")


def test_another_origin_cannot_approve(page):
    base, _ = page
    with pytest.raises(urllib.error.HTTPError) as refused:
        request(f"{base}/api/run", {"mode": "policy", "person": "marcus.lee", "message": "x"}, {"Origin": "http://evil.example"})
    assert refused.value.code == 403


def test_an_answer_for_nothing_pending_is_refused(page):
    base, _ = page
    run_id = request(f"{base}/api/run", {"mode": "policy", "person": "marcus.lee", "message": "Delete release/2.4."})["id"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        request(f"{base}/api/run/{run_id}/answer", {"pending": "not-this-one", "answer": True})
    assert refused.value.code == 409
