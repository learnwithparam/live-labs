"""The desk page's API: local only, the metric from the log, and the officer's buttons doing what they say."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import KEY

from larkspur.config import DATA
from larkspur.serve import EvidenceRoom, serve
from larkspur.tamper import tampered_copy


@pytest.fixture
def desk_url(fortnight):
    server = serve(EvidenceRoom(KEY, DATA, fortnight["out"]), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", fortnight
    server.shutdown()
    server.server_close()


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_a_foreign_page_is_refused(desk_url):
    base, _ = desk_url
    status, _ = call(base + "/api/state", headers={"Origin": "https://evil.example"})
    assert status == 403


def test_the_page_and_the_state(desk_url):
    base, _ = desk_url
    status, page = call(base + "/")
    assert status == 200 and b'data-surface="audit request desk with evidence chain"' in page
    state = json.loads(call(base + "/api/state")[1])
    assert state["metric"]["answered"] == state["metric"]["total"] == 14
    assert state["chain"]["ok"] and len(state["requests"]) == 14
    found = json.loads(call(base + "/api/request/A-03")[1])
    assert found["governed"]["records"] and found["naive"]["steps"]


def test_signoff_is_refused_until_the_finding_is_decided(desk_url):
    base, _ = desk_url
    status, body = call(base + "/api/signoff", {})
    assert status == 409 and b"dispute-mailer" in body
    status, _ = call(base + "/api/decide", {"system": "dispute-mailer", "decision": "keep-blocked"})
    assert status == 200
    state = json.loads(call(base + "/api/signoff", {})[1])
    assert state["pack"]["signed_by"].startswith("Ines Duarte") and state["blockers"] == []


def test_the_tampered_copy_shows_broken(desk_url):
    base, fortnight = desk_url
    tampered_copy(fortnight["db"], fortnight["out"] / "audit-tampered.sqlite", "edit", 4)
    state = json.loads(call(base + "/api/state?log=tampered")[1])
    assert state["copy"] == "tampered" and not state["chain"]["ok"] and state["chain"]["broken_at"] == 4
    # The cause and the open finding, not fourteen failed answers.
    assert len(state["blockers"]) == 2 and "does not verify" in state["blockers"][0]
