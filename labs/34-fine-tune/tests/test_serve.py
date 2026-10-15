"""The release console's server: local only, and a reviewer's label lands where the next export reads it."""

import http.client
import json
import threading

import pytest
from fakes import prediction

from corvane.labels import read_jsonl
from corvane.release import route, score
from corvane.serve import Console, serve

ITEMS = [{"id": "TX-1", "descriptor": "UBER *EATS", "amount": 20.0, "posted": "2026-08-01", "label": "meals"},
         {"id": "TX-2", "descriptor": "EXPEDIA 7201", "amount": 410.0, "posted": "2026-08-02", "label": "airfare"}]
INCOMING = {"id": "NX-1", "descriptor": "FEDEX OFFICE 4410", "amount": 31.0, "posted": "2026-10-01"}


@pytest.fixture
def console(tmp_path):
    tuned = [prediction("TX-1", "meals"), prediction("TX-2", "lodging")]
    large = [prediction("TX-1", "meals", None, 0.0001), prediction("TX-2", "airfare", None, 0.0001)]
    scores = {"tuned": {**score(ITEMS, tuned), "predictions": tuned}, "large": {**score(ITEMS, large), "predictions": large}}
    (tmp_path / "scores.json").write_text(json.dumps(scores))
    (tmp_path / "heldout.jsonl").write_text("".join(json.dumps(item) + "\n" for item in ITEMS))
    sent_up = route(INCOMING, prediction("NX-1", "office_supplies", 0.55), 0.80) | {"large": prediction("NX-1", "shipping")}
    (tmp_path / "queue.json").write_text(json.dumps([sent_up]))
    return Console(tmp_path, tmp_path / "decisions.jsonl")


@pytest.fixture
def server(console):
    running = serve(console, 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running
    running.shutdown()


def call(server, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    connection.request(method, path, json.dumps(body) if body else None, headers or {})
    response = connection.getresponse()
    return response.status, response.read()


def test_state_shows_the_misses_what_was_sent_up_and_holds_with_no_cascade(console):
    state = console.state()
    assert [miss["id"] for miss in state["misses"]] == ["TX-2"]
    assert state["misses"][0] == {"id": "TX-2", "descriptor": "EXPEDIA 7201", "amount": 410.0,
                                  "truth": "airfare", "tuned": "lodging", "large": "airfare"}
    assert state["queue"] == {**state["queue"], "total": 1, "small": 0}
    assert state["queue"]["sent_up"][0]["id"] == "NX-1" and "below the 80% threshold" in state["queue"]["sent_up"][0]["reason"]
    assert state["plain"]["decision"] == "hold"
    assert state["cascade"]["decision"] == "hold" and "tune cascade" in state["cascade"]["reasons"][0]


def test_state_reads_the_cascade_release_the_cli_wrote(console):
    (console.out_dir / "release.json").write_text(json.dumps({"decision": "ship", "checks": [], "reasons": [],
                                                              "kept": 1, "routed": 1, "routed_items": []}))
    assert console.state()["cascade"]["decision"] == "ship"


def test_a_page_from_another_origin_is_refused(server):
    assert call(server, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403
    port = server.server_port
    status, _ = call(server, "POST", "/api/review", {"id": "NX-1", "label": "shipping"},
                     {"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example"})
    assert status == 403


def test_a_reviewer_label_is_saved_for_the_next_training_set(server, console):
    status, body = call(server, "POST", "/api/review", {"id": "NX-1", "label": "shipping"})
    assert status == 200
    assert json.loads(body)["queue"]["sent_up"][0]["decided"] == "shipping"
    assert read_jsonl(console.decisions_file) == [{**INCOMING, "label": "shipping", "reviewer": "you"}]


def test_an_unknown_item_or_category_is_rejected(server):
    assert call(server, "POST", "/api/review", {"id": "NX-9", "label": "shipping"})[0] == 400
    assert call(server, "POST", "/api/review", {"id": "NX-1", "label": "groceries"})[0] == 400
