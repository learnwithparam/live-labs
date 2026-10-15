"""The reader's server: it serves the page and saved reports, files the editor's decisions, and only
answers pages from this machine."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from kitebird.serve import Desk, serve


@pytest.fixture
def server(tmp_path):
    (tmp_path / "lead.json").write_text(json.dumps({"id": "r1", "version": "lead"}))
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


def test_the_page_and_state_are_served(server):
    base, _ = server
    assert b'data-surface="research report reader"' in call(base + "/")
    state = json.loads(call(base + "/api/state"))
    assert state["reports"]["lead"]["id"] == "r1"
    assert "S09" in state["sources"]
    assert json.loads(call(base + "/api/source/S09"))["publisher"].startswith("Berliner")


def test_an_editor_decision_is_saved_under_the_report(server):
    base, folder = server
    call(base + "/api/edit", {"report": "r1", "claim": "C3", "decision": "cut"}, origin=base)
    assert json.loads((folder / "edits.json").read_text()) == {"r1": {"C3": "cut"}}
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/edit", {"report": "r1", "claim": "C3", "decision": "maybe"})
    assert refused.value.code == 400


def test_a_foreign_page_cannot_start_a_run(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/run", {"version": "lead"}, origin="https://example.com")
    assert refused.value.code == 403


def test_a_run_without_a_key_ends_with_the_error_instead_of_hanging(tmp_path):
    def no_key():
        raise SystemExit("Set LLM_API_KEY in .env first.")

    desk = Desk(no_key, out=tmp_path)
    run_id = desk.start("lead")
    for _ in range(200):
        if desk.runs[run_id]["done"]:
            break
        threading.Event().wait(0.01)
    assert desk.runs[run_id]["done"]
    assert "LLM_API_KEY" in desk.runs[run_id]["error"]
