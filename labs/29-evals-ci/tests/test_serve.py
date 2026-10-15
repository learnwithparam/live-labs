"""Keyless tests of the check page's API: local only, the metric for both gates, the trajectory diff
for a failing case, and an override that is refused without a reason and shown once recorded."""

import json
import threading
import urllib.error
import urllib.request

import pytest

import kettlebay.serve as page_server
from fakes import FakeOpenAI
from kettlebay.assistant import load_golden, load_release
from kettlebay.gate import run_release
from kettlebay.store import load_store


@pytest.fixture
def desk_url(tmp_path, monkeypatch):
    golden = load_golden()
    runs = tmp_path / "runs"
    runs.mkdir()
    for name in ("live", "faster-writes", "shorter-replies", "order-numbers"):
        result = run_release(load_release(f"releases/{name}.json"), golden, load_store(), FakeOpenAI(golden), 3)
        (runs / f"{name}.json").write_text(json.dumps(result))
    monkeypatch.setattr(page_server, "RUNS", runs)
    server = page_server.serve(page_server.Desk(overrides_file=tmp_path / "overrides.jsonl"), 0)
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


def test_another_site_cannot_override_a_release(desk_url):
    status, _ = request(f"{desk_url}/api/check/faster-writes/override", {"by": "x", "reason": "y"},
                        origin="https://evil.example")
    assert status == 403


def test_the_checks_list_carries_both_gates_and_skips_a_release_with_no_run(desk_url):
    _, overview = request(f"{desk_url}/api/checks")
    states = {check["name"]: check["state"] for check in overview["checks"]}
    assert states == {"shorter-replies": "blocked", "faster-writes": "blocked", "cheaper-model": "not run",
                      "order-numbers": "passed"}
    assert overview["metric"] == {"judged": 3, "blocked": 2, "blocked_answers_only": 1}


def test_a_failing_case_shows_the_missing_approval(desk_url):
    _, detail = request(f"{desk_url}/api/check/faster-writes")
    case = next(case for case in detail["cases"] if case["id"] == "G06")
    assert case["status"] == "unsafe" and case["answers_only_status"] == "held"
    assert [row["kind"] for row in case["runs"][0]["diff"]] == ["match", "missing", "match"]
    assert any(line["file"] == "tools/faster-writes.json" for line in detail["changes"])


def test_an_override_needs_a_reason_then_shows_on_the_check(desk_url):
    status, error = request(f"{desk_url}/api/check/faster-writes/override", {"by": "Owen Pratt", "reason": "ok"})
    assert status == 400 and "at least 6 words" in error
    reason = "Approval moves into the admin UI in the same deploy"
    status, detail = request(f"{desk_url}/api/check/faster-writes/override", {"by": "Owen Pratt", "reason": reason})
    assert status == 200 and detail["state"] == "overridden"
    assert detail["overrides"][0]["reason"] == reason
