"""The wallboard's server: it serves the page and the saved review, files the office's decision on each
handoff, and only answers pages from this machine."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from northfield.serve import Desk, serve


@pytest.fixture
def server(tmp_path):
    (tmp_path / "review.json").write_text(json.dumps({"score": {"reached": 9, "calls": 10}, "calls": []}))
    running = serve(Desk(lambda: None, runner=lambda **_: None, out=tmp_path), 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}", tmp_path
    running.shutdown()


def call(url, body=None, origin=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body else None)
    if origin:
        request.add_header("Origin", origin)
    with urllib.request.urlopen(request) as response:
        return response.read()


def test_the_page_and_state_are_served(server):
    base, _ = server
    assert b'data-surface="inbound call wallboard"' in call(base + "/")
    state = json.loads(call(base + "/api/state"))
    assert state["company"].startswith("Northfield") and state["review"]["score"]["reached"] == 9


def test_the_office_decision_on_a_handoff_is_saved(server):
    base, folder = server
    call(base + "/api/decide", {"call": "NF-05", "decision": "dispatch"}, origin=base)
    assert json.loads((folder / "decisions.json").read_text()) == {"NF-05": "dispatch"}
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/decide", {"call": "NF-05", "decision": "ignore"})
    assert refused.value.code == 400


def test_a_foreign_page_cannot_start_a_run(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/run", {}, origin="https://example.com")
    assert refused.value.code == 403


def test_a_run_without_a_key_ends_with_the_error_instead_of_hanging(tmp_path):
    def no_key():
        raise SystemExit("LLM_API_KEY is not set.")

    desk = Desk(no_key, out=tmp_path)
    assert desk.start()
    for _ in range(200):
        if desk.run["done"]:
            break
        threading.Event().wait(0.01)
    assert desk.run["done"]
    assert "LLM_API_KEY" in desk.run["error"]
