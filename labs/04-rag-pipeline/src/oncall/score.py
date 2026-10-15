"""The questions with the fact a supporting passage must hold, and the verdict on each answer."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from oncall.answer import Reply


@dataclass
class Question:
    question: str
    fact: str | None  # the command, threshold or name the right passage holds; None when the docs do not cover it


def load_questions(path: Path) -> list[Question]:
    return [Question(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def norm(text: str) -> str:
    """Compare facts the way an engineer reads them: case, commas in numbers and spacing do not matter."""
    return re.sub(r"\s+", " ", text.lower().replace(",", "")).strip()


def holds(text: str, fact: str) -> bool:
    return norm(fact) in norm(text)


@dataclass
class Verdict:
    supported: bool
    reason: str | None  # why a person takes it; None when the answer stands on its citation
    fact: str | None = None
    cited_text: dict[str, str] = field(default_factory=dict)


def judge(question: Question, reply: Reply) -> Verdict:
    """An answer stands only when it states the fact and a passage it cites holds that fact.
    Everything else goes to a person, with the reason."""
    # Only ids the model was actually shown count: a made-up id cannot hold anything.
    shown = {passage.id: passage.text for passage in reply.passages}
    cited = {passage_id: shown[passage_id] for passage_id in reply.cited if passage_id in shown}
    # Saying "not covered" is the right answer when no runbook covers it; a person still takes the question.
    if reply.answer is None:
        if question.fact is None:
            return Verdict(False, "the docs do not cover this, and the answer says so instead of guessing", None, cited)
        return Verdict(False, "no answer, though a runbook covers it: the search did not find the passage", question.fact, cited)
    if question.fact is None:
        return Verdict(False, "answered a question the docs do not cover: nothing it cites can hold the answer", None, cited)
    if not cited:
        return Verdict(False, "the answer cites no passage it was given", question.fact, cited)
    # The citation must hold the fact itself. A chunk cut at settlement-co does not, even if the answer is right.
    if not any(holds(text, question.fact) for text in cited.values()):
        return Verdict(False, f"no cited passage holds {question.fact!r}", question.fact, cited)
    # And the answer must say it: a right citation under a wrong command still sends the wrong command.
    if not holds(reply.answer, question.fact):
        return Verdict(False, f"the cited passage holds {question.fact!r} but the answer does not state it", question.fact, cited)
    return Verdict(True, None, question.fact, cited)


@dataclass
class Tally:
    questions: int
    supported: int
    answered: int
    person: int
    cost: float

    @property
    def share(self) -> float:
        """The metric: of the answers given, the share whose citation supports them."""
        return self.supported / self.answered if self.answered else 0.0


def tally(verdicts: list[Verdict], replies: list[Reply]) -> Tally:
    supported = sum(verdict.supported for verdict in verdicts)
    answered = sum(reply.answer is not None for reply in replies)
    return Tally(len(verdicts), supported, answered, len(verdicts) - supported, sum(reply.cost for reply in replies))
