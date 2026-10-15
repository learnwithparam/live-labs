"""The review queue: every item without a valid category waits for a person, and their decision shows."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from fakes import outcome

from corvane_gpu.golden import golden_set
from corvane_gpu.report import build_report
from corvane_gpu.review import decide, decisions
from corvane_gpu.serve import handler

GOLDEN = golden_set()[:4]


def gpu_run(outcomes: list[dict]) -> dict:
    billing = {"cold_start_seconds": 40.0, "busy_seconds": 4.0, "idle_seconds": 30.0}
    return {"about": {}, "auth": {}, "timeline": [], "settings": {}, "guard_hit": False,
            "waves": [{"name": "golden set", "items": len(outcomes), "started": True, "cold_start_seconds": 40.0,
                       "outcomes": outcomes, "billing": billing}]}


def run_with_failures() -> dict:
    first, second, third, fourth = (item["id"] for item in GOLDEN)
    return gpu_run([outcome(first, label=GOLDEN[0]["label"]),
                    outcome(second, status="timed_out", label=None),
                    {**outcome(third, label=None), "answer": "It could be meals or travel"},
                    outcome(fourth, status="error", label=None)])


def test_timeouts_and_unclear_answers_wait_for_a_reviewer():
    queue = build_report(run_with_failures(), None, GOLDEN, {})["queue"]
    assert [item["id"] for item in queue] == [item["id"] for item in GOLDEN[1:]]
    assert queue[0]["reason"].startswith("timed out")
    assert queue[1]["reason"].startswith("answer is not one category")
    assert queue[2]["reason"].startswith("request failed")
    assert all(item["decision"] is None for item in queue)


def test_a_decision_needs_a_queued_item_a_category_and_a_name(tmp_path):
    path, queued = tmp_path / "decisions.jsonl", {GOLDEN[1]["id"]}
    with pytest.raises(ValueError, match="not in the review queue"):
        decide(path, queued, GOLDEN[0]["id"], "meals", "Dana")
    with pytest.raises(ValueError, match="not a category"):
        decide(path, queued, GOLDEN[1]["id"], "travel", "Dana")
    with pytest.raises(ValueError, match="who is deciding"):
        decide(path, queued, GOLDEN[1]["id"], "meals", "  ")
    assert not path.exists()


def test_the_latest_decision_is_the_one_the_report_shows(tmp_path):
    path, item_id = tmp_path / "decisions.jsonl", GOLDEN[1]["id"]
    decide(path, {item_id}, item_id, "meals", "Dana")
    decide(path, {item_id}, item_id, "software", "Ravi")
    queue = build_report(run_with_failures(), None, GOLDEN, decisions(path))["queue"]
    decided = next(item for item in queue if item["id"] == item_id)["decision"]
    assert (decided["category"], decided["reviewer"]) == ("software", "Ravi")


def test_the_console_records_a_decision_and_shows_it(tmp_path, monkeypatch):
    run_path, decisions_path = tmp_path / "results.json", tmp_path / "decisions.jsonl"
    run_path.write_text(json.dumps(run_with_failures()))
    monkeypatch.setattr("corvane_gpu.cli.golden_set", lambda: GOLDEN, raising=False)
    monkeypatch.setattr("corvane_gpu.golden.golden_set", lambda *args: GOLDEN)
    monkeypatch.setattr("corvane_gpu.cli.DECISIONS", decisions_path)
    monkeypatch.setattr("corvane_gpu.cli.load_hosted", lambda: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(run_path, decisions_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps({"id": GOLDEN[2]["id"], "category": "meals", "reviewer": "Dana Okafor"}).encode()
        request = urllib.request.Request(base + "/api/review", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["category"] == "meals"
        with urllib.request.urlopen(base + "/api/run", timeout=5) as response:
            queue = json.loads(response.read())["queue"]
    finally:
        server.shutdown()
        server.server_close()
    decided = next(item for item in queue if item["id"] == GOLDEN[2]["id"])["decision"]
    assert decided["reviewer"] == "Dana Okafor"
