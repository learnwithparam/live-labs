"""The estimator's server: it serves the page, the photos and the saved run, files the adjuster's
decisions, and only answers pages from this machine."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from saltmarsh.serve import Desk, serve


@pytest.fixture
def server(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"model": "test-model", "results": []}))
    running = serve(Desk(lambda: None, out=tmp_path), 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}", tmp_path
    running.shutdown()


def call(url, body=None, origin=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body else None)
    if origin:
        request.add_header("Origin", origin)
    with urllib.request.urlopen(request) as response:
        return response.read()


def test_the_page_photos_and_state_are_served(server):
    base, _ = server
    assert b'data-surface="claim photo estimator"' in call(base + "/")
    assert call(base + "/photos/P01.jpg")[:2] == b"\xff\xd8"
    state = json.loads(call(base + "/api/state"))
    assert state["run"]["model"] == "test-model"
    assert len(state["claims"]) == 10 and state["bills"]["SM-1041"]


def test_an_adjuster_decision_is_saved_and_a_bad_one_refused(server):
    base, folder = server
    call(base + "/api/decide", {"claim": "SM-1049", "decision": "new_photos"}, origin=base)
    assert json.loads((folder / "decisions.json").read_text()) == {"SM-1049": "new_photos"}
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/decide", {"claim": "SM-1049", "decision": "pay double"})
    assert refused.value.code == 400


def test_a_foreign_page_cannot_start_a_run(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/run", {"go": True}, origin="https://example.com")
    assert refused.value.code == 403


def test_a_run_without_a_key_ends_with_the_error_instead_of_hanging(tmp_path):
    def no_key():
        raise SystemExit("LLM_API_KEY is not set.")

    desk = Desk(no_key, out=tmp_path)
    desk.start()
    for _ in range(200):
        if not desk.run["running"]:
            break
        threading.Event().wait(0.01)
    assert not desk.run["running"]
    assert "LLM_API_KEY" in desk.run["error"]
