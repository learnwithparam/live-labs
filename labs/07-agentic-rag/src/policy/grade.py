"""Score an answer the way the expert would: does it make each point the expert's answer makes?

The expert wrote the points an acceptable answer must contain (`data/expert.jsonl`). A grading model
reads the answer and marks each point made or not, and flags advice that contradicts a point. The
code, not the model, decides acceptance: every point made and nothing contradicted.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from policy.llm import ask

INSTRUCTIONS = (
    "You check an answer an on-call engineer received against the points an expert says a correct "
    "answer must make. For each point, by its number, quote the words of the answer that make it, or "
    "leave quote empty, then say whether it is made: the same command, number, channel and limit, in "
    "any words. Then say whether the answer gives any advice that "
    "contradicts a point, such as telling them to do what a point forbids."
)


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    needs: list[str]
    points: list[str]


class Point(BaseModel):
    point: int
    quote: str
    made: bool


class Grade(BaseModel):
    points: list[Point]
    contradicts: bool
    reason: str


def load(path: Path) -> list[Question]:
    return [Question(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def grade(q: Question, answer: str, client, model: str) -> tuple[dict, float]:
    if not answer:
        # No answer is not accepted, but it is not wrong advice either: it goes to a person.
        return {"accepted": False, "made": [False] * len(q.points), "contradicts": False, "reason": "No answer."}, 0.0
    points = "\n".join(f"{n}. {p}" for n, p in enumerate(q.points, 1))
    user = f"Question: {q.question}\n\nExpert's points:\n{points}\n\nAnswer: {answer}"
    got, cost = ask(client, model, INSTRUCTIONS, user, Grade)
    if got is None:
        # One retry: a grade that did not parse says nothing about the answer.
        got, again = ask(client, model, INSTRUCTIONS, user, Grade)
        cost += again
    if got is None:
        return {"accepted": False, "made": [False] * len(q.points), "contradicts": False, "reason": "The grader did not return a result."}, cost
    # A point counts as made only with a quote behind it; a point the grader skipped is not made.
    by_number = {p.point: p.made and bool(p.quote.strip()) for p in got.points}
    made = [by_number.get(n, False) for n in range(1, len(q.points) + 1)]
    accepted = all(made) and not got.contradicts
    # The reason is written from the marks, so a page never shows "not accepted" with nothing under it.
    missed = [f"Missed point {n}: {p}" for n, (p, ok) in enumerate(zip(q.points, made), 1) if not ok]
    if got.contradicts:
        missed.append(f"Contradicts the expert: {got.reason.strip() or 'the grader gave no reason'}")
    reason = " ".join(missed) or "Every point made."
    return {"accepted": accepted, "made": made, "contradicts": got.contradicts, "reason": reason}, cost


def found(q: Question, sources: list[str]) -> int:
    """How many of the sections the expert used were in front of the model: no model, no opinion."""
    return sum(1 for need in q.needs if need in sources)
