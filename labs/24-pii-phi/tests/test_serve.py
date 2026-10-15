"""Keyless tests of the transcript editor: it answers only its own page, shows the privacy metric, and a
clinician's decision changes what the model would see."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from fakes import FakeOpenAI
from wrenfield.serve import Desk, serve


@pytest.fixture
def editor(tmp_path):
    desk = Desk(lambda: (FakeOpenAI(), "openai/gpt-6-luna"), tmp_path / "notes.json", tmp_path / "vault.json")
    server = serve(desk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def request(url: str, body: dict | None = None, origin: str | None = None) -> tuple[int, dict | str]:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers)) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_another_site_cannot_read_the_transcripts(editor):
    status, _ = request(f"{editor}/api/visit/WF-01", origin="https://evil.example")
    assert status == 403


def test_the_state_carries_the_metric_and_the_held_visit(editor):
    status, state = request(f"{editor}/api/state")
    assert status == 200
    assert state["metric"]["missed"] == 0 and state["metric"]["spans"] == 66
    assert state["metric"]["regex_missed"] > 50 and state["metric"]["held"] == 1
    assert [row["id"] for row in state["visits"] if row["status"] == "held"] == ["WF-14"]


def test_releasing_the_lot_number_clears_the_review_and_writing_stores_a_note(editor):
    _, view = request(f"{editor}/api/visit/WF-07")
    card = next(card for card in view["review"] if card["kind"] == "unsure")
    status, view = request(f"{editor}/api/visit/WF-07/release", card)
    assert status == 200 and view["review"] == []
    _, view = request(f"{editor}/api/visit/WF-07/note", {})
    assert view["note"]["leaked"] == [] and "Nkem Eze" in view["note"]["restored"]


def test_a_span_outside_the_transcript_is_refused(editor):
    status, _ = request(f"{editor}/api/visit/WF-01/add", {"start": 0, "end": 10_000, "type": "NAME"})
    assert status == 400
