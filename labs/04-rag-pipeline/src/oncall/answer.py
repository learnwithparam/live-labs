"""Answer a question from the passages found for it, citing the ids of the ones the answer comes from."""

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from oncall.ingest import Passage

INSTRUCTIONS = (
    "You answer an on-call engineer's question from the runbook passages given, and from nothing else. "
    "Copy commands, thresholds and channel names exactly as the passage writes them. "
    "cited lists the id of every passage the answer comes from. "
    "When the passages do not answer the question, set answer to null and cited to an empty list: "
    "a guess sends an engineer to run the wrong command at 3 a.m. Reply with JSON only."
)
BLIND = "You answer an on-call engineer's question about their company's payments platform. Be specific."


class Answer(BaseModel):
    # null, not a guess, when the passages do not hold the answer: the check sends those to a person.
    answer: str | None
    cited: list[str]


@dataclass
class Reply:
    answer: str | None
    cited: list[str]
    passages: list[Passage]
    cost: float


def schema_format() -> dict:
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": Answer.model_json_schema()}}


def listing(passages: list[Passage]) -> str:
    # Each passage carries its id in brackets, so the model can cite exactly the ones it used.
    return "\n\n".join(f"[{p.id}] {p.doc}, {p.section}, page {p.page}\n{p.text}" for p in passages)


def answer(question: str, passages: list[Passage], client, model: str) -> Reply:
    messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": f"Passages:\n\n{listing(passages)}\n\nQuestion: {question}"}]
    response = client.chat.completions.create(model=model, messages=messages, temperature=0, response_format=schema_format())
    cost = getattr(response.usage, "cost", None) or 0.0
    try:
        got = Answer.model_validate_json(response.choices[0].message.content)
    except ValidationError:
        return Reply(None, [], passages, cost)
    return Reply(got.answer, got.cited, passages, cost)


def ask_blind(question: str, client, model: str) -> str:
    """The obvious way: ask the model with no docs at all."""
    messages = [{"role": "system", "content": BLIND}, {"role": "user", "content": question}]
    response = client.chat.completions.create(model=model, messages=messages, temperature=0, max_tokens=300)
    return response.choices[0].message.content


def dump(reply: Reply) -> str:
    return json.dumps({"answer": reply.answer, "cited": reply.cited})
