"""Keyless tests: a fake model stands in, so these check the graph's loop, its budget, the grading and the page."""

import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from policy.answer import INSTRUCTIONS as ANSWER
from policy.grade import INSTRUCTIONS as GRADE
from policy.grade import Question, found, grade, load
from policy.graph import CHECK, MAX_ROUNDS, PLAN, agentic
from policy.ingest import ingest
from policy.plain import plain
from policy.search import Policies
from policy.serve import Room, serve

LAB = Path(__file__).parent.parent


class Fake:
    """Answers like a model would, keyed on which step is asking. `checks` scripts the check step's replies."""

    def __init__(self, checks=None, made=(True, True), contradicts=False):
        self.checks, self.made, self.contradicts, self.calls = list(checks or []), made, contradicts, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **kwargs):
        system = messages[0]["content"]
        self.calls.append(system)
        if system == PLAN:
            body = {"queries": ["ledger-writer rollback", "on-call weekend pay"]}
        elif system == CHECK:
            body = self.checks.pop(0) if self.checks else {"enough": True, "missing": "", "queries": []}
        elif system == ANSWER:
            body = {"answer": "Roll back [ledger-writer-runbook#rollback].", "cited": ["ledger-writer-runbook#rollback", "made-up#id"], "covered": True}
        elif system == GRADE:
            body = {"points": [{"point": n, "quote": "Roll back" if m else "", "made": m} for n, m in enumerate(self.made, 1)],
                    "contradicts": self.contradicts, "reason": "checked"}
        else:
            raise AssertionError(system)
        message = SimpleNamespace(content=json.dumps(body))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(cost=0.001))


MISSING = {"enough": False, "missing": "the deploy freeze", "queries": ["deploy freeze window Friday"]}


@pytest.fixture(scope="module")
def policies():
    return Policies(ingest(LAB / "data/docs"))


def test_every_pdf_section_is_a_passage_and_every_expert_section_exists(policies):
    assert len(policies.passages) == 64
    questions = load(LAB / "data/expert.jsonl")
    assert len(questions) == 16
    assert all(need in policies.by_id for q in questions for need in q.needs)
    # Most questions need two or more sections: that is what makes one search fall short.
    assert sum(len(q.needs) > 1 for q in questions) >= 14


def test_search_finds_the_section_by_its_words(policies):
    assert policies.search("RECON-MISMATCH-22")[0].id == "reconciliation-job#recon-mismatch-22"
    assert policies.search("zzzz qqqq") == []


def test_plain_rag_searches_once_and_answers_in_one_call(policies):
    got = plain("Can I roll back ledger-writer?", policies, Fake(), "m")
    assert got["calls"] == 1 and len(got["searches"]) == 1


def test_the_agent_goes_back_for_what_the_check_says_is_missing(policies):
    fake = Fake(checks=[MISSING])
    got = agentic("Can I roll back ledger-writer on Friday?", policies, fake, "m")
    assert got["rounds"] == 2
    assert [s["query"] for s in got["searches"]][-1] == "deploy freeze window Friday"
    assert "deploy-policy#freeze-window" in got["sources"]
    # plan, check, check, answer
    assert got["calls"] == 4 and fake.calls.count(CHECK) == 2


def test_a_made_up_citation_is_dropped(policies):
    got = agentic("Can I roll back ledger-writer?", policies, Fake(), "m")
    assert got["cited"] == ["ledger-writer-runbook#rollback"]


def test_the_agent_stops_on_its_round_budget(policies):
    # A check that is never satisfied, with a new search each time, still ends after MAX_ROUNDS searches.
    checks = [{"enough": False, "missing": "more", "queries": [q]} for q in ["deploy freeze", "card number log", "kafka poison message", "fx freeze"]]
    got = agentic("Anything?", policies, Fake(checks=checks), "m")
    assert got["rounds"] == MAX_ROUNDS and got["answer"]


def test_a_retry_that_finds_nothing_new_stops_the_loop(policies):
    again = {"enough": False, "missing": "same", "queries": ["ledger-writer rollback"]}
    fake = Fake(checks=[again, again])
    got = agentic("Can I roll back?", policies, fake, "m")
    # The second search found only sections already in hand, so the check is skipped and the agent answers.
    assert got["rounds"] == 2 and fake.calls.count(CHECK) == 1


def test_an_answer_is_accepted_only_when_every_point_is_made_and_nothing_contradicts():
    q = Question("Q", "Can I roll back?", ["ledger-writer-runbook#rollback"], ["Roll back first.", "Get approval."])
    assert grade(q, "Roll back.", Fake(made=(True, True)), "j")[0]["accepted"] is True
    missed = grade(q, "Roll back.", Fake(made=(True, False)), "j")[0]
    assert missed["accepted"] is False and missed["reason"] == "Missed point 2: Get approval."
    assert grade(q, "Roll back.", Fake(contradicts=True), "j")[0]["accepted"] is False
    assert grade(q, "", Fake(), "j")[0] == {"accepted": False, "made": [False, False], "contradicts": False, "reason": "No answer."}
    assert found(q, ["ledger-writer-runbook#rollback", "x"]) == 1


def test_the_page_streams_each_step_and_serves_only_to_this_machine(policies, tmp_path):
    questions = load(LAB / "data/expert.jsonl")
    room = Room(policies, questions, Fake(checks=[MISSING]), "m", "j", tmp_path / "escalated.jsonl")
    server = serve(room, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path, body, origin=None):
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        if origin:
            req.add_header("Origin", origin)
        return urllib.request.urlopen(req).read().decode()

    try:
        assert 'data-surface="on-call policy desk"' in urllib.request.urlopen(base + "/").read().decode()
        events = [json.loads(line) for line in post("/api/ask", {"question": "Roll back on Friday?"}).splitlines()]
        assert [e["node"] for e in events] == ["plan", "search", "check", "search", "check", "answer"]
        # The answer carries the run's totals, not the last step's.
        assert events[-1]["calls"] == 4 and events[-1]["sources"][0]["doc"].startswith("RB-101")
        assert json.loads(post("/api/escalate", {"question": "Q?", "answer": ""})) == [{"question": "Q?", "answer": ""}]
        with pytest.raises(urllib.error.HTTPError):
            post("/api/escalate", {"question": "Q?"}, origin="http://evil.example")
    finally:
        server.shutdown()
