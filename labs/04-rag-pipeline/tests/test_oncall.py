"""Tests that need no key: ingestion, the search, the verdict, the page and env.example."""

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from oncall.answer import Reply
from oncall.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from oncall.ingest import Passage, by_section, by_size, ingest
from oncall.retrieve import Index
from oncall.score import Question, holds, judge, load_questions
from oncall.serve import Room, serve

LAB = Path(__file__).parent.parent
DOCS, QUESTIONS = LAB / "data/docs", LAB / "data/questions.jsonl"
FACTS = {question.question: question.fact for question in load_questions(QUESTIONS)}


class FakeClient:
    """Answers with the question's fact when a passage it was given holds it, citing that passage."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        user = kwargs["messages"][1]["content"]
        question = user.rsplit("Question: ", 1)[1]
        fact = FACTS[question]
        blocks = re.findall(r"^\[([^\]]+)\][^\n]*\n(.*?)(?=\n\n\[|\n\nQuestion: )", user, re.S | re.M)
        hit = next((passage_id for passage_id, body in blocks if fact and holds(body, fact)), None)
        reply = {"answer": f"Run {fact}." if hit else None, "cited": [hit] if hit else []}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reply)))], usage=SimpleNamespace(cost=0.0001))


def test_sections_drop_the_page_furniture_and_rejoin_wrapped_commands():
    passages = by_section(DOCS / "kafka-consumer-lag.pdf")
    assert [passage.section for passage in passages][:3] == ["Alert: SettlementConsumerLag", "Find the cause", "Poison message"]
    poison = passages[2]
    assert poison.id == "kafka-consumer-lag#poison-message" and poison.doc == "RB-102 Settlement consumer lag"
    assert "kcat-skip --group settlement-consumer --partition <n> --offset <offset>" in poison.text
    assert not any("Internal" in passage.text or "rev 7" in passage.text for passage in ingest(DOCS))


def test_every_fact_lives_whole_in_one_section_passage():
    passages = ingest(DOCS)
    for question in load_questions(QUESTIONS):
        if question.fact:
            assert any(holds(passage.text, question.fact) for passage in passages), question.question


def test_fixed_size_chunks_cut_some_facts_in_two():
    chunks = ingest(DOCS, "size")
    assert all(len(chunk.text) <= 300 for chunk in chunks)
    split = [question.fact for question in load_questions(QUESTIONS) if question.fact and not any(holds(chunk.text, question.fact) for chunk in chunks)]
    assert split, "no fact was cut; the obvious way would look as good as sections"


def test_the_search_finds_the_passage_that_holds_the_answer():
    index = Index(ingest(DOCS))
    for question in load_questions(QUESTIONS):
        if question.fact:
            assert any(holds(passage.text, question.fact) for passage in index.search(question.question, 4)), question.question


def passage(passage_id: str, text: str) -> Passage:
    return Passage(passage_id, "RB-1", "Rollback", 1, text)


def test_an_answer_stands_only_on_a_cited_passage_that_holds_the_fact():
    question = Question("How do I roll back?", "kubectl rollout undo")
    right, other = passage("a", "Run kubectl rollout undo now."), passage("b", "Page #payments-db.")
    assert judge(question, Reply("kubectl rollout undo", ["a"], [right, other], 0)).supported
    assert "no cited passage holds" in judge(question, Reply("kubectl rollout undo", ["b"], [right, other], 0)).reason
    assert "cites no passage" in judge(question, Reply("kubectl rollout undo", ["z"], [right], 0)).reason
    assert "does not state it" in judge(question, Reply("roll it back", ["a"], [right], 0)).reason
    assert "did not find" in judge(question, Reply(None, [], [other], 0)).reason
    off = Question("What is the weekend pay?", None)
    assert "says so instead of guessing" in judge(off, Reply(None, [], [other], 0)).reason
    assert "do not cover" in judge(off, Reply("Double pay.", ["b"], [other], 0)).reason


def start(room: Room):
    server = serve(room, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_the_page_answers_both_ways_and_shows_who_goes_to_a_person():
    server, base = start(Room(DOCS, QUESTIONS, FakeClient(), "any-model"))
    try:
        room = json.loads(urllib.request.urlopen(f"{base}/api/room").read())
        post = lambda how: json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/answer/{how}", method="POST")).read())
        size, section = post("size"), post("section")
        page = urllib.request.urlopen(base).read().decode()
    finally:
        server.shutdown()
    questions = len(room["questions"])
    assert section["tally"] | {"cost": 0} == {"questions": questions, "supported": questions - 2, "answered": questions - 2, "person": 2, "cost": 0, "share": 1.0}
    assert size["tally"]["supported"] < section["tally"]["supported"]
    off = [row for row in section["rows"] if row["fact"] is None]
    assert len(off) == 2 and all("instead of guessing" in row["reason"] for row in off)
    assert 'id="tally"' in page and "A person decides" in page and "No person needed" in page


def test_the_channel_answers_one_message_and_judges_it_only_when_the_set_has_its_fact(monkeypatch):
    skip = next(question for question in FACTS if "skip it" in question)
    server, base = start(Room(DOCS, QUESTIONS, FakeClient(), "any-model"))
    ask = lambda how, question: json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{base}/api/ask/{how}", data=json.dumps({"question": question}).encode(), method="POST")).read())
    try:
        section, tls = ask("section", skip), ask("section", next(question for question in FACTS if "TLS" in question))
        monkeypatch.setitem(FACTS, "Who wrote the ledger-writer runbook?", None)  # the fake can answer it; the set never approved it
        outside = ask("section", "Who wrote the ledger-writer runbook?")
    finally:
        server.shutdown()
    assert section["supported"] and section["cited"] == ["kafka-consumer-lag#poison-message"]
    assert tls["supported"] is False and "instead of guessing" in tls["reason"]
    assert outside["supported"] is None and outside["reason"] is None


@pytest.mark.parametrize("headers", [{"Host": "attacker.example:80"}, {"Origin": "http://attacker.example"}])
def test_the_page_refuses_other_sites(headers):
    server, base = start(Room(DOCS, QUESTIONS, FakeClient(), "any-model"))
    try:
        request = urllib.request.Request(f"{base}/api/answer/section", headers=headers, method="POST")
        with pytest.raises(urllib.error.HTTPError, match="403"):
            urllib.request.urlopen(request)
    finally:
        server.shutdown()


def test_env_example_matches_the_code_defaults():
    env = dict(
        line.split("=", 1) for line in (LAB / "env.example").read_text().splitlines() if "=" in line and not line.startswith("#")
    )
    assert env["LLM_MODEL"] == DEFAULT_MODEL
    assert env["LLM_BASE_URL"] == DEFAULT_BASE_URL
