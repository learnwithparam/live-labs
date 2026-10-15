"""The review queue's server: it serves the page and the back-test, files a reviewer's call, refuses a bad
one, and answers only pages from this machine."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from fennick.serve import Queue, serve


@pytest.fixture
def server(tmp_path):
    queue = {"refund_id": "FM-270001", "score": 0.91}
    (tmp_path / "backtest.json").write_text(json.dumps({"queue": [queue], "model_version": "abc"}))
    (tmp_path / "llm.json").write_text(json.dumps({"model": "openai/gpt-6-luna", "scores": {"FM-270001": 80}}))
    running = serve(Queue(out=tmp_path), 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}", tmp_path
    running.shutdown()


def call(url, body=None, origin=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body else None)
    if origin:
        request.add_header("Origin", origin)
    with urllib.request.urlopen(request) as response:
        return response.read()


def test_the_page_and_state_are_served_without_the_llm_scores(server):
    base, _ = server
    assert b'data-surface="refund risk review queue"' in call(base + "/")
    state = json.loads(call(base + "/api/state"))
    assert state["backtest"]["model_version"] == "abc"
    assert "scores" not in state["llm"] and set(state["choices"]) == {"pay", "hold"}


def test_a_reviewer_call_is_filed_and_a_bad_one_refused(server):
    base, folder = server
    call(base + "/api/decide", {"refund": "FM-270001", "decision": "hold"}, origin=base)
    assert json.loads((folder / "reviews.json").read_text()) == {"FM-270001": "hold"}
    for bad in ({"refund": "FM-270001", "decision": "refund twice"}, {"refund": "FM-1", "decision": "pay"}):
        with pytest.raises(urllib.error.HTTPError) as refused:
            call(base + "/api/decide", bad)
        assert refused.value.code == 400


def test_a_foreign_page_cannot_pay_or_hold(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(base + "/api/decide", {"refund": "FM-270001", "decision": "pay"}, origin="https://example.com")
    assert refused.value.code == 403
