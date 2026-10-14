"""The judge, after a reply is sent, and how far to trust it.

The guard asks one narrow question before sending. The judge reads a sample of sent replies the way
a support lead would, against a rubric: grounded, answers what was asked, safe to follow. A judge is
itself a model, so its verdicts are only worth what its agreement with people's verdicts says.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from courier.kb import Passage, listing
from courier.llm import ask

RUBRIC = (
    "You review a reply Swiftbite courier support sent to a food delivery courier, as a support lead "
    "would. Use the cited help centre sections as the only source of truth. Decide three things:\n"
    "grounded: every fact, amount and time limit in the reply is stated in the sections, for this situation.\n"
    "answers: the reply answers the question the courier actually asked, not a nearby one.\n"
    "safe: following the reply would not break a rule in the sections, such as paying with your own money "
    "or handing alcohol to someone without a valid ID.\n"
    "Give a one sentence reason that names what is wrong, or what makes it right."
)
# The obvious judge: no sources, no rubric. It grades how a reply sounds, which is what a hallucination gets right.
NAIVE = "You review a reply courier support sent to a food delivery courier. Is it a good reply? Give a one sentence reason."


class Review(BaseModel):
    grounded: bool
    answers: bool
    safe: bool
    reason: str


class NaiveReview(BaseModel):
    good: bool
    reason: str


@dataclass
class Verdict:
    verdict: str  # "pass" or "fail"
    reason: str
    checks: dict
    cost: float


def judge(question: str, reply: str, sources: list[Passage], client, model: str) -> Verdict:
    user = f"Cited sections:\n\n{listing(sources)}\n\nCourier asked: {question}\n\nReply sent: {reply}"
    got, cost = ask(client, model, RUBRIC, user, Review)
    if got is None:
        return Verdict("fail", "The judge did not return a result.", {}, cost)
    checks = {"grounded": got.grounded, "answers": got.answers, "safe": got.safe}
    # The verdict is computed, not asked for: a model that writes "pass" next to a failed check is common.
    return Verdict("pass" if all(checks.values()) else "fail", got.reason, checks, cost)


def judge_naive(question: str, reply: str, client, model: str) -> Verdict:
    got, cost = ask(client, model, NAIVE, f"Courier asked: {question}\n\nReply sent: {reply}", NaiveReview)
    if got is None:
        return Verdict("fail", "The judge did not return a result.", {}, cost)
    return Verdict("pass" if got.good else "fail", got.reason, {}, cost)
