"""Keyless tests: fakes stand in for the models, so these check the rule, the second opinion, the review and the page."""

import json
import threading
import urllib.error
import urllib.request
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from triage.decide import QUESTION, DecisionRouter, question
from triage.embed import EmbeddingRouter
from triage.llm import LLMRouter
from triage.review import review, summary
from triage.route import CONFIDENT, PERSON, Route, destination, second_opinion
from triage.serve import Room, serve
from triage.tickets import Ticket, history, inbox, queues


def test_six_queues_ten_past_tickets_each_and_a_morning_inbox():
    names = list(queues())
    assert names == ["payroll", "tax", "benefits", "billing", "access", "integrations"]
    assert Counter(ticket.queue for ticket in history()) == {queue: 10 for queue in names}
    tickets = inbox()
    assert len(tickets) == 72 and len({ticket.id for ticket in tickets}) == 72
    assert {ticket.queue for ticket in tickets} == set(names)


def test_a_confident_route_goes_to_its_queue_and_a_doubtful_one_to_a_person():
    assert destination(Route("tax", CONFIDENT)) == "tax"
    assert destination(Route("tax", CONFIDENT - 0.01)) == PERSON


def test_the_second_opinion_routes_only_when_both_agree_confidently():
    llm = Route("tax", 0.95, ms=900, cost=0.0001)
    assert destination(second_opinion(llm, Route("tax", 0.8, {"tax": 0.8}))) == "tax"
    # The LLM's 0.95 does not count: a disagreement or a doubtful decision model sends it to a person.
    assert destination(second_opinion(llm, Route("payroll", 0.9))) == PERSON
    assert destination(second_opinion(llm, Route("tax", 0.5))) == PERSON
    both = second_opinion(llm, Route("tax", 0.8, ms=2000))
    assert both.ms == 2000 and both.cost == 0.0001


def test_the_decision_question_is_the_routing_guide_and_the_reply_becomes_a_route():
    seen = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, body=json.loads(request.content))
        probs = {"payroll": 0.1, "tax": 0.7, "benefits": 0.2}
        return httpx.Response(200, json={"answers": {"queue": {"choice": "tax", "probabilities": probs}}})

    guide = queues()
    client = httpx.Client(base_url="http://127.0.0.1:8099", transport=httpx.MockTransport(answer))
    router = DecisionRouter(guide, "http://127.0.0.1:8099", "m", client=client)
    got = router(Ticket("T-1", "Letter from the IRS", "Penalty for Q1", "tax"))
    assert seen["path"] == "/v1/systemone"
    assert seen["body"]["questions"] == {"queue": {"type": "choice", "instructions": QUESTION, "criteria": guide}}
    assert "Letter from the IRS" in seen["body"]["state"]
    assert (got.queue, got.confidence, got.cost) == ("tax", 0.7, 0.0)
    assert router.workers == 1  # a laptop server takes one request at a time
    assert DecisionRouter(guide, "https://api.example.com", "m").workers == 4
    assert question(guide)["queue"]["criteria"] is guide


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        assert kwargs["response_format"]["type"] == "json_schema"
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(cost=0.00005))


def test_the_llm_route_keeps_its_own_confidence_and_an_unparsed_reply_goes_to_a_person():
    ticket = inbox()[0]
    got = LLMRouter(queues(), FakeLLM('{"queue": "payroll", "confidence": 0.9}'), "m")(ticket)
    assert (got.queue, got.confidence, got.cost) == ("payroll", 0.9, 0.00005)
    # A seventh queue fails the schema, so the reply counts as unparsed.
    bad = LLMRouter(queues(), FakeLLM('{"queue": "sales", "confidence": 1}'), "m")(ticket)
    assert bad.queue == "unparsed" and destination(bad) == PERSON


def test_the_embedding_router_lets_the_five_nearest_vote():
    past = [Ticket(f"H{index}", "", word, queue) for index, (word, queue) in enumerate(
        [("pay", "payroll")] * 3 + [("tax", "tax")] * 2 + [("login", "access")] * 4)]
    vectors = {"pay": [1, 0, 0], "tax": [0.9, 0.1, 0], "login": [0, 0, 1]}
    router = EmbeddingRouter(past, lambda texts: [vectors[text.split()[-1]] for text in texts])
    got = router(Ticket("T", "", "pay", "payroll"))
    assert (got.queue, got.confidence) == ("payroll", 0.6)
    assert got.probabilities == {"payroll": 0.6, "tax": 0.4}


class Fixed:
    """A router that answers from a table: ticket id to (queue, confidence)."""

    def __init__(self, table: dict, default=("payroll", 0.9)):
        self.table, self.default = table, default

    def __call__(self, ticket: Ticket) -> Route:
        queue, confidence = self.table.get(ticket.id, self.default)
        return Route(queue, confidence, {queue: confidence}, ms=1, cost=0.001 if confidence > 0.85 else 0.0)


TICKETS = [Ticket("A", "", "a", "payroll"), Ticket("B", "", "b", "tax"), Ticket("C", "", "c", "tax")]


def test_review_counts_reassigned_person_and_caught_and_adds_the_second_opinion():
    llm = Fixed({"B": ("payroll", 0.95), "C": ("tax", 0.9)})
    decision = Fixed({"A": ("payroll", 0.8), "B": ("billing", 0.4), "C": ("tax", 0.7)})
    got = review(TICKETS, {"llm": llm, "decision": decision})
    assert got["llm"] | {"p95_ms": 0, "cost_per_1000": 0} == {"tickets": 3, "reassigned": 1, "to_person": 0, "right_first_time": 2,
                                                              "caught": 0, "top_pick_right": 2, "p95_ms": 0, "cost_per_1000": 0}
    assert got["decision"]["to_person"] == 1 and got["decision"]["caught"] == 1 and got["decision"]["reassigned"] == 0
    # The pair reuses both answers: B's wrong LLM pick is caught because the decision model disagreed.
    assert got["both"]["reassigned"] == 0 and got["both"]["to_person"] == 1 and got["both"]["right_first_time"] == 2
    assert [row["to"] for row in got["rows"] if row["how"] == "both"] == ["payroll", PERSON, "tax"]


def test_the_second_opinion_never_reassigns_more_than_the_llm_alone():
    tickets = inbox()
    llm = Fixed({ticket.id: ("payroll", 0.9) for ticket in tickets[::3]}, default=("tax", 0.9))
    decision = Fixed({ticket.id: ("payroll", 0.7) for ticket in tickets[::2]}, default=("tax", 0.65))
    got = review(tickets, {"llm": llm, "decision": decision})
    assert got["both"]["reassigned"] <= got["llm"]["reassigned"]
    assert summary(got["rows"], "both")["tickets"] == len(tickets)


@pytest.fixture()
def page(tmp_path):
    room = Room({"llm": Fixed({}), "decision": Fixed({}, default=("tax", 0.4))}, tmp_path / "review.json", tmp_path / "handoffs.jsonl")
    server = serve(room, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", room
    server.shutdown()


def call(url: str, data: dict | None = None, headers: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, body, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request) as response:
        return response.read().decode()


def test_the_board_routes_a_new_ticket_and_logs_a_persons_choice(page):
    base, room = page
    room_info = json.loads(call(base + "/api/room"))
    assert room_info["ways"] == ["llm", "decision", "both"] and len(room_info["tickets"]) == 72
    routed = json.loads(call(base + "/api/route", {"text": "Our W-2s have the wrong address"}))
    assert routed["llm"]["to"] == "payroll" and routed["decision"]["to"] == PERSON and routed["both"]["to"] == PERSON
    assert json.loads(call(base + "/api/handoff", {"id": "T-4161", "queue": "benefits"})) == [{"id": "T-4161", "queue": "benefits"}]
    with pytest.raises(urllib.error.HTTPError) as no_queue:
        call(base + "/api/handoff", {"id": "T-4161", "queue": "sales"})
    assert no_queue.value.code == 400


def test_the_board_streams_the_review_and_keeps_it(page):
    base, room = page
    lines = [json.loads(line) for line in call(base + "/api/review", {}).splitlines()]
    assert sum("row" in line for line in lines) == 72 * 3 and "summary" in lines[-1]
    assert room.report and room.review_file.exists()


def test_the_board_refuses_a_foreign_host_or_origin(page):
    base, _ = page
    for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}):
        with pytest.raises(urllib.error.HTTPError) as refused:
            call(base + "/api/room", headers=headers)
        assert refused.value.code == 403
