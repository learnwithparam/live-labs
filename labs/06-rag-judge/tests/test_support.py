"""Keyless tests: a fake model stands in, so these check the plumbing, the maths and the page."""

import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from courier.agree import agreement, kappa, load_labelled
from courier.bot import INSTRUCTIONS as BOT
from courier.bot import Draft
from courier.guard import Check
from courier.judge import NaiveReview, Review
from courier.llm import ask
from courier.desk import handle, review
from courier.guard import INSTRUCTIONS as GUARD
from courier.guard import guard
from courier.judge import NAIVE, RUBRIC, judge
from courier.kb import HelpCentre, load
from courier.serve import Room, serve

LAB = Path(__file__).parent.parent


class Fake:
    """Answers like a model would, keyed on which step is asking. Each reply is JSON for that step."""

    def __init__(self, unsupported: str = "first delivery", bad: str = "never"):
        self.unsupported, self.bad, self.calls = unsupported, bad, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **kwargs):
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append(system)
        reply = user.rsplit(": ", 1)[-1]
        if system == BOT:
            body = {"reply": None, "cited": []} if "crypto" in user else {"reply": "Instant payout costs $1.99.", "cited": ["getting-paid/instant-payout", "made-up/id"]}
        elif system == GUARD:
            verdict = "unsupported" if self.unsupported in reply else "supported"
            body = {"claims": [{"claim": reply, "verdict": verdict, "section": "getting-paid/instant-payout", "quote": "for a fee of $1.99"}]}
        elif system == RUBRIC:
            ok = self.bad not in reply
            body = {"grounded": ok, "answers": True, "safe": True, "reason": "checked"}
        elif system == NAIVE:
            body = {"good": True, "reason": "sounds helpful"}
        else:
            raise AssertionError(system)
        message = SimpleNamespace(content=json.dumps(body))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(cost=0.001))


@pytest.fixture(scope="module")
def centre():
    return HelpCentre(load(LAB / "data/help"))


def test_help_centre_splits_articles_into_sections_and_finds_them(centre):
    assert len(centre.passages) == 33
    assert centre.search("How much does instant payout cost?")[0].id == "getting-paid/instant-payout"
    assert centre.search("zzzz qqqq") == []


def test_every_labelled_reply_cites_real_sections_and_half_fail():
    rows = load_labelled(LAB / "data/labelled.jsonl")
    ids = {passage.id for passage in load(LAB / "data/help")}
    assert len(rows) == 20 and all(set(row.cited) <= ids for row in rows)
    assert sum(row.human == "fail" for row in rows) == 10
    assert {row.kind for row in rows} == {"supported", "unsupported", "unsafe", "off-question"}
    assert all((row.human == "pass") == (row.kind == "supported") for row in rows)


def test_kappa_is_one_for_full_agreement_and_zero_for_a_judge_that_passes_everything():
    human = ["pass", "fail", "pass", "fail"]
    assert kappa(human, human) == 1.0
    assert kappa(human, ["pass"] * 4) == 0.0


def test_agreement_counts_the_matrix_and_names_disagreements():
    rows = load_labelled(LAB / "data/labelled.jsonl")[:4]  # pass, fail, pass, fail
    got = agreement(rows, ["pass", "pass", "pass", "fail"])
    assert got["agree"] == 3 and got["matrix"]["fail"]["pass"] == 1 and got["disagree"] == [rows[1].id]


def test_the_guard_holds_a_reply_with_an_unsupported_claim(centre):
    sources = centre.get(["getting-paid/instant-payout"])
    assert guard("q", "Instant payout costs $1.99.", sources, Fake(), "m").send
    held = guard("q", "Available after your first delivery.", sources, Fake(), "m")
    assert not held.send and "1 of 1 claims have no support" in held.why


def test_the_guard_never_sends_without_a_reply_or_a_source(centre):
    fake = Fake()
    assert not guard("q", None, centre.passages[:1], fake, "m").send
    assert not guard("q", "Something.", [], fake, "m").send
    assert fake.calls == []  # neither case spends a model call


def test_a_bot_citation_of_an_unknown_section_is_dropped(centre):
    got = handle("How much does instant payout cost?", centre, Fake(), "m")
    assert got["cited"] == ["getting-paid/instant-payout"] and got["send"]
    assert not handle("Can I get paid in crypto?", centre, Fake(), "m")["send"]


def test_the_judge_computes_its_verdict_from_the_rubric_checks(centre):
    sources = centre.get(["red-card/card-declined"])
    assert judge("q", "Tap Card declined.", sources, Fake(), "m").verdict == "pass"
    assert judge("q", "You never need the card.", sources, Fake(), "m").verdict == "fail"


def test_review_counts_unsupported_replies_sent_and_both_judges(centre):
    rows = load_labelled(LAB / "data/labelled.jsonl")
    got = review(rows, centre, Fake(unsupported="first delivery", bad="first delivery"), "m", "j")
    assert got["guard"]["unsupported"] == 8
    assert "T-1042" not in got["guard"]["sent"] and len(got["guard"]["sent"]) == 7
    assert got["naive"]["matrix"]["fail"]["pass"] == 10  # the naive fake passes everything
    assert got["judge"]["disagree"] == [row.id for row in rows if row.human == "fail" and row.id != "T-1042"]


def test_the_page_serves_only_to_this_machine_and_records_decisions(centre, tmp_path):
    rows = load_labelled(LAB / "data/labelled.jsonl")
    room = Room(centre, rows, ["Q?"], Fake(), "m", "j", tmp_path / "agent.jsonl")
    server = serve(room, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert 'data-surface="courier support inbox"' in page
        assert len(json.loads(urllib.request.urlopen(base + "/api/room").read())["tickets"]) == 20

        def post(path, body, origin=None):
            request = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
            if origin:
                request.add_header("Origin", origin)
            return json.loads(urllib.request.urlopen(request).read())

        assert post("/api/check", {"ticket": "T-1042"})["send"] is False
        assert post("/api/decide", {"ticket": "T-1042", "decision": "rewrite"}) == [{"ticket": "T-1042", "decision": "rewrite"}]
        with pytest.raises(urllib.error.HTTPError):
            post("/api/decide", {"ticket": "T-1042", "decision": "send"}, origin="http://evil.example")
    finally:
        server.shutdown()


class SchemaRecorder:
    """Stands in for the client and keeps the reply schema each call sent."""

    def __init__(self):
        self.schemas = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.schemas.append(request["response_format"]["json_schema"]["schema"])
        message = SimpleNamespace(content="")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def objects_in(node):
    """Every object in a JSON schema, nested definitions included."""
    if isinstance(node, dict):
        if "properties" in node:
            yield node
        for value in node.values():
            yield from objects_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects_in(value)


def test_every_reply_schema_meets_openai_strict_mode():
    # OpenAI models reject a schema where an object leaves a property optional or allows extra keys.
    recorder = SchemaRecorder()
    for shape in (Draft, Check, Review, NaiveReview):
        ask(recorder, "any-model", "system", "user", shape)
    objects = [found for schema in recorder.schemas for found in objects_in(schema)]
    assert len(objects) >= 5, "one per shape, plus each claim inside a check"
    for found in objects:
        assert found["required"] == list(found["properties"])
        assert found["additionalProperties"] is False
