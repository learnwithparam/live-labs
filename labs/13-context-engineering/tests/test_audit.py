"""Keyless tests: the books give five disputes, the engineered context keeps the start of every request
the same, each naive habit alone breaks the cache, and the run console only answers this machine."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from fakes import FakeAuditor
from halyard.agent import TOOLS, Desk, audit, usage_of
from halyard.books import contract, expected, invoices, owed
from halyard.context import BREAKS, ENGINEERED, NAIVE, build, first_change, windowed, with_breaks
from halyard.review import score

MODEL = "openai/gpt-6-luna"


def ticking_clock():
    """A clock that moves seven seconds a step, as a real run's would."""
    moments = (datetime(2026, 10, 1, 9, 0) + timedelta(seconds=7 * step) for step in range(1000))
    return lambda: next(moments)


def run(rules, auditor=None):
    auditor = auditor or FakeAuditor()
    return score(audit(rules, auditor, MODEL, clock=ticking_clock())), auditor


def test_the_contract_disputes_five_of_sixteen_invoices():
    disputed = {invoice_id: finding.overcharge for invoice_id, finding in expected().items() if finding.verdict == "dispute"}
    assert disputed == {"INV-04": 179.41, "INV-07": 6.06, "INV-08": 35.00, "INV-10": 90.00, "INV-15": 829.35}
    assert expected()["INV-15"].reason == "duplicate of INV-03"


def test_the_rate_engine_pays_no_unbooked_service_and_no_free_waiting_hour():
    terms, by_id = contract(), {invoice["id"]: invoice for invoice in invoices()}
    # INV-08 delivered by tail-lift that nobody booked; INV-10 billed the two free hours.
    assert owed(by_id["INV-08"], terms) == 811.50
    assert round(sum(line["amount"] for line in by_id["INV-10"]["lines"]) - owed(by_id["INV-10"], terms), 2) == 90.00


def test_the_engineered_context_is_mostly_cached_and_the_naive_one_hardly_at_all():
    naive, _ = run(NAIVE)
    engineered, _ = run(ENGINEERED)
    assert engineered["complete"] and naive["complete"]
    assert engineered["hit_rate"] > 0.8, engineered["hit_rate"]
    assert naive["hit_rate"] < 0.1, naive["hit_rate"]
    assert engineered["cost"] < naive["cost"]


@pytest.mark.parametrize("habit", BREAKS)
def test_each_naive_habit_alone_costs_cache(habit):
    engineered, _ = run(ENGINEERED)
    broken, _ = run(with_breaks(ENGINEERED, [habit]))
    assert broken["hit_rate"] < engineered["hit_rate"], (habit, broken["hit_rate"], engineered["hit_rate"])


def test_first_change_names_where_each_request_stops_matching():
    _, auditor = run(NAIVE)
    labels = [first_change(before, now) for before, now in zip([None] + auditor.requests, auditor.requests)]
    assert labels[0] == "first request"
    assert set(labels[1:-1]) == {"system prompt"}
    _, auditor = run(ENGINEERED)
    labels = [first_change(before, now) for before, now in zip([None] + auditor.requests, auditor.requests)]
    assert labels[1:] == [""] * (len(labels) - 1)
    hidden = build(NAIVE, TOOLS, [[{"role": "user", "content": "go"}]], 16, 16, datetime(2026, 10, 1))
    assert first_change(build(NAIVE, TOOLS, [[{"role": "user", "content": "go"}]], 15, 16, datetime(2026, 10, 1)), hidden) == "tool list"


def test_the_window_forgets_the_invoice_a_duplicate_repeats():
    first_bill = next(invoice["number"] for invoice in invoices() if invoice["id"] == "INV-03")
    # Requests run list, then read and record per invoice: the record for INV-15 is request 30.
    _, auditor = run(NAIVE)
    assert first_bill not in json.dumps(auditor.requests[30]["messages"])
    _, auditor = run(ENGINEERED)
    assert first_bill in json.dumps(auditor.requests[30]["messages"])


def test_the_window_never_splits_a_tool_call_from_its_result():
    turns = [[{"role": "user", "content": "go"}]] + [[{"role": "assistant", "tool_calls": [{"id": f"call{index}"}]},
                                                       {"role": "tool", "tool_call_id": f"call{index}"}] for index in range(10)]
    kept = windowed(turns, 6)
    assert kept[0]["content"] == "go" and kept[1]["role"] == "assistant" and len(kept) == 13


def test_finish_refuses_while_invoices_have_no_finding():
    desk = Desk()
    assert "INV-01" in desk.call("finish", {"summary": "done"}, ENGINEERED) and desk.summary is None


def test_recitation_ends_each_finding_with_where_the_audit_stands():
    desk = Desk()
    said = desk.call("record_finding", {"invoice": "INV-01", "verdict": "approve", "duplicate_of": "", "reason": "ok"}, ENGINEERED)
    assert said.endswith("1 of 16 invoices have a finding. Next: INV-02.")
    assert "Next" not in Desk().call("record_finding", {"invoice": "INV-01", "verdict": "approve", "reason": "ok"}, NAIVE)


def test_the_code_not_the_model_works_out_the_overcharge():
    desk = Desk()
    desk.call("record_finding", {"invoice": "INV-15", "verdict": "dispute", "duplicate_of": "INV-03", "reason": "twice"}, ENGINEERED)
    desk.call("record_finding", {"invoice": "INV-04", "verdict": "dispute", "duplicate_of": "", "reason": "rate"}, ENGINEERED)
    assert desk.findings["INV-15"].overcharge == 829.35 and desk.findings["INV-04"].overcharge == 179.41


def test_cost_is_the_providers_bill_when_it_sends_one():
    billed, _ = run(ENGINEERED, FakeAuditor(billed_cost=0.001))
    assert billed["billed"] and billed["cost"] == round(0.001 * billed["steps"], 6)
    listed, _ = run(ENGINEERED)
    assert not listed["billed"] and listed["cost"] > 0


def test_one_wrong_finding_means_no_completed_audit():
    result = audit(ENGINEERED, FakeAuditor(), MODEL, clock=ticking_clock())
    result["findings"]["INV-07"]["verdict"] = "approve"
    scored = score(result)
    assert scored["right"] == 15 and not scored["complete"] and scored["cost_per_completed"] is None


def test_the_run_console_answers_only_this_machine(tmp_path):
    from halyard.serve import Console, serve

    server = serve(Console(lambda: (FakeAuditor(), MODEL), tmp_path / "review.json"), 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert 'data-surface="freight audit run console"' in page
        foreign = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=b"{}", method="POST",
                                         headers={"Origin": "https://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(foreign)
        assert refused.value.code == 403
        started = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=json.dumps({"context": "engineered"}).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
        run_id = json.loads(urllib.request.urlopen(started).read())["id"]
        for _ in range(200):
            state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run/{run_id}").read())
            if state["done"]:
                break
            threading.Event().wait(0.05)
        assert state["score"]["complete"] and len(state["steps"]) == 34
    finally:
        server.shutdown()
