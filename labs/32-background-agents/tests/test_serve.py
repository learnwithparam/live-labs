"""The queue page's API: local callers only, red CI cannot be merged from the page, and a run reports errors."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from fakes import FakeEgress, local_sandboxes

from fernhill.cli import issues
from fernhill.forge import Forge
from fernhill.orchestrator import Fleet
from fernhill.serve import Queue, serve


@pytest.fixture
def page(tmp_path):
    forge = Forge(tmp_path / "forge")
    forge.reset()
    scripts = {"FH-101": {"script": "fix"}, "FH-104": {"script": "cheat"}, "FH-110": {"script": "refuse"}}

    def start_fleet(forge_for_run):
        egress = FakeEgress()
        return Fleet(forge_for_run, egress, *local_sandboxes(egress, scripts), "scripted", tmp_path, 3), lambda: None

    def save_run(forge_for_run, sessions, model, wall, queue, fresh):
        (tmp_path / "run.json").write_text(json.dumps({"model": model, "sessions": [one.public() for one in sessions],
                                                       "board": {"wall_seconds": wall}}))

    queue = Queue(tmp_path / "forge", tmp_path / "run.json", tmp_path / "laptop.json",
                  issues(["FH-101", "FH-104", "FH-110"]), start_fleet, save_run)
    server = serve(queue, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", queue
    server.shutdown()


def call(url: str, path: str, body: dict | None = None, origin: str | None = None) -> tuple[int, dict | str]:
    headers = {"Origin": origin} if origin else {}
    request = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return response.status, json.loads(payload) if response.headers["Content-Type"] == "application/json" else payload
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def finished_run(url: str, queue: Queue) -> dict:
    assert call(url, "/api/run", {})[0] == 202
    queue.worker.join(timeout=60)
    return call(url, "/api/state")[1]


def test_a_run_from_the_page_fills_the_queue_and_the_metric(page):
    url, queue = page
    state = finished_run(url, queue)
    assert state["error"] is None and state["running"] is False
    assert {card["id"]: card["status"] for card in state["sessions"]} == {
        "FH-101": "pull request", "FH-104": "pull request", "FH-110": "gave up"}
    assert state["board"]["pulls"] == 2 and state["board"]["ci_green"] == 1


def test_the_page_merges_green_ci_with_a_name_and_refuses_red_ci(page):
    url, queue = page
    state = finished_run(url, queue)
    by_issue = {pull["id"]: pull["number"] for pull in state["pulls"]}
    status, refusal = call(url, f"/api/pulls/{by_issue['FH-104']}/merge", {"reviewer": "Dana Okafor"})
    assert status == 409 and "red CI" in refusal
    assert call(url, f"/api/pulls/{by_issue['FH-101']}/merge", {"reviewer": ""})[0] == 409
    status, merged = call(url, f"/api/pulls/{by_issue['FH-101']}/merge", {"reviewer": "Dana Okafor"})
    assert status == 200 and merged["state"] == "merged" and merged["reviewer"] == "Dana Okafor"
    status, closed = call(url, f"/api/pulls/{by_issue['FH-104']}/close", {"reviewer": "Dana Okafor", "reason": "red"})
    assert closed["close_reason"] == "red"
    assert call(url, "/api/state")[1]["board"]["merged_share"] == 0.5


def test_a_foreign_origin_gets_nothing(page):
    url, _ = page
    assert call(url, "/api/state", origin="http://evil.example")[0] == 403
    assert call(url, "/api/pulls/1/merge", {"reviewer": "x"}, origin="http://evil.example")[0] == 403


def test_a_failed_run_shows_its_reason(page):
    url, queue = page

    def no_key(forge):
        raise SystemExit("LLM_API_KEY is not set.")

    queue.start_fleet = no_key
    assert finished_run(url, queue)["error"] == "LLM_API_KEY is not set."
