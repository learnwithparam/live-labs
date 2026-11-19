"""The page's server: the bench, the scorecard for each candidate, and the origin check on labelling."""

import json
import threading
from http.client import HTTPConnection

import pytest

import brickhaven_evals.config as settings
from brickhaven_evals.extractor import run_candidate, save_run
from brickhaven_evals.golden import load_golden
from brickhaven_evals.serve import serve

from fakes import candidate_named, scripted_extract


@pytest.fixture
def server():
    http = serve(0)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield http.server_address[1]
    http.shutdown()


def call(port, method, path, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port)
    connection.request(method, path, json.dumps(body) if body is not None else None, headers or {})
    response = connection.getresponse()
    return response.status, response.read()


def save_scripted_runs():
    golden = load_golden()
    for name in ("live", "bad", "harmless"):
        save_run(name, run_candidate(candidate_named(name), golden, scripted_extract(golden)), settings.RUNS)


def test_the_page_names_its_surface_and_serves(server):
    status, body = call(server, "GET", "/")
    assert status == 200 and b'data-surface="golden-set labelling bench' in body and b'rel="icon"' in body


def test_the_scorecard_says_not_run_until_a_run_is_saved(server):
    assert json.loads(call(server, "GET", "/api/scorecard?candidate=bad")[1])["ran"] is False


def test_the_scorecard_blocks_the_bad_candidate_and_ships_the_harmless_one(server):
    save_scripted_runs()
    bad = json.loads(call(server, "GET", "/api/scorecard?candidate=bad")[1])["verdict"]
    harmless = json.loads(call(server, "GET", "/api/scorecard?candidate=harmless")[1])["verdict"]
    assert not bad["ships"] and bad["naive_ships"] and len(bad["regressions"]) == 1
    assert harmless["ships"] and harmless["regressions"] == []
    assert call(server, "GET", "/api/scorecard?candidate=other")[0] == 404


def test_labelling_needs_a_name_and_a_local_origin_and_counts_after(server):
    bench = json.loads(call(server, "GET", "/api/bench")[1])
    assert bench["verified"] == 30 and len(bench["waiting"]) == 3
    assert call(server, "POST", "/api/label", {"case_id": "L01", "labeller": ""})[0] == 400
    assert call(server, "POST", "/api/label", {"case_id": "L01", "labeller": "Dana"}, {"Origin": "https://evil.example"})[0] == 403
    status, body = call(server, "POST", "/api/label", {"case_id": "L01", "labeller": "Dana Okafor"})
    assert status == 200 and json.loads(body)["verified"] == 31
    assert call(server, "POST", "/api/label", {"case_id": "nope", "labeller": "Dana"})[0] == 404
