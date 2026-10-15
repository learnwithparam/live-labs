"""Keyless tests of the admin console: it answers only its own page, shows the metric, and records
the admin's decisions on a budget breach and a flagged route."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from fakes import result
from fernway.evaluate import result_path, summarise
from fernway.serve import Console, serve


@pytest.fixture
def console(fresh_state):
    fresh_state.mkdir(parents=True)
    runs = {"strong": [result("CARE-05", True, 0.0001, "strong"), result("QA-01", True, 0.0001, "strong")],
            "routed": [result("CARE-05", True, 0.0001, "strong", flagged=True), result("QA-01", True, 0.00001, "cheap")]}
    for mode, results in runs.items():
        result_path(mode).write_text(json.dumps(summarise(mode, mode, results)))
    server = serve(Console(), 0)
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


def test_another_site_cannot_raise_a_budget(console):
    status, _ = request(f"{console}/api/budget", {"team": "growth", "budget": 900}, origin="https://evil.example")
    assert status == 403


def test_the_state_carries_the_metric_the_breach_and_the_flagged_call(console):
    status, state = request(f"{console}/api/state")
    assert status == 200 and state["metric"]["quality_holds"] and state["metric"]["saving"] == pytest.approx(0.45)
    assert [team["team"] for team in state["teams"] if team["over"]] == ["growth"]
    assert [call["item"] for call in state["flagged"]] == ["CARE-05"]


def test_raising_the_budget_clears_the_breach(console):
    status, state = request(f"{console}/api/budget", {"team": "growth", "budget": 300})
    growth = next(team for team in state["teams"] if team["team"] == "growth")
    assert status == 200 and not growth["over"] and growth["raised"]["by"] == "platform admin"


def test_labelling_a_flagged_call_stores_the_tier(console):
    status, state = request(f"{console}/api/route", {"call_id": "call-CARE-05", "tier": "strong"})
    assert status == 200 and state["flagged"][0]["decision"]["tier"] == "strong"


def test_a_label_for_an_unflagged_call_or_a_silly_budget_is_refused(console):
    assert request(f"{console}/api/route", {"call_id": "call-QA-01", "tier": "cheap"})[0] == 400
    assert request(f"{console}/api/budget", {"team": "growth", "budget": 1_000_000})[0] == 400
