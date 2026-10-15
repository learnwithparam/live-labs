"""Keyless tests of the release console: only its own page may call it, it shows the metric and the waiting
decision, and its buttons move labels the same way the CLI does."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from fakes import FakeOpenAI

from driftwell.release import ReleaseLog
from driftwell.serve import Console, serve


@pytest.fixture
def console(registry, scores, tmp_path):
    ReleaseLog(tmp_path / "releases.json").add(at=0.0, kind="import", label="production", came_from=None, to=1,
                                               by="engineering", score=None, reason=None, served_after=None)
    desk = Console(lambda: registry, lambda: (FakeOpenAI(), "openai/gpt-6-luna"), scores.path, tmp_path / "releases.json")
    server = serve(desk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def request(url: str, body: dict | None = None, origin: str | None = None) -> tuple[int, dict | list | str]:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers)) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_another_site_cannot_move_a_label(console):
    status, _ = request(f"{console}/api/promote", {"version": 3, "by": "x", "override": "x"}, origin="https://evil.example")
    assert status == 403
    _, state = request(f"{console}/api/state")
    assert state["production"] == 1


def test_the_page_is_served_with_its_own_surface(console):
    with urllib.request.urlopen(f"{console}/") as response:
        assert 'data-surface="prompt release and rollback console"' in response.read().decode()


def test_the_state_carries_scores_labels_and_the_bar(console):
    _, state = request(f"{console}/api/state")
    assert (state["production"], state["staging"], state["needed"], state["golden"]) == (1, 3, 14, 16)
    assert [(row["number"], row["score"]["passed"]) for row in state["versions"]] == [(1, 15), (2, 16), (3, 1)]


def test_a_blocked_version_on_staging_waits_for_a_person_with_its_reason(console):
    _, state = request(f"{console}/api/state")
    waiting = state["metric"]["waiting"]
    assert [row["number"] for row in waiting] == [3]
    assert "below the bar of 14" in waiting[0]["reason"] and waiting[0]["author"] == "Sam Lee"


def test_promote_below_the_bar_is_refused_until_a_person_overrides(console):
    status, body = request(f"{console}/api/promote", {"version": 3, "by": "Sam Lee"})
    assert status == 409 and "below the bar" in body
    status, event = request(f"{console}/api/promote", {"version": 3, "by": "Sam Lee", "override": "brand launch"})
    assert status == 200 and event["kind"] == "override"


def test_rollback_from_the_page_is_timed_by_the_next_bot_reply(console):
    request(f"{console}/api/promote", {"version": 2, "by": "Maya Ortiz"})
    request(f"{console}/api/promote", {"version": 3, "by": "Sam Lee", "override": "brand launch"})
    status, event = request(f"{console}/api/rollback", {"by": "Maya Ortiz"})
    assert status == 200 and event["to"] == 2
    _, reply = request(f"{console}/api/ask", {})
    assert reply["version"] == 2 and reply["closed_rollback"]["served_after"] < 60
    _, state = request(f"{console}/api/state")
    assert state["metric"]["last_rollback"]["to"] == 2 and state["previous"] == 1
    assert state["replies"][0]["version"] == 2


def test_the_diff_shows_the_lines_a_version_adds(console):
    _, lines = request(f"{console}/api/diff?older=1&newer=2")
    added = [line["text"] for line in lines if line["kind"] == "added"]
    assert any("Export data" in text for text in added)
    status, _ = request(f"{console}/api/diff?older=one&newer=2")
    assert status == 400
