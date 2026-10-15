"""The grounding check, before a reply reaches the courier.

It splits the draft into claims and asks, for each one, whether a cited section says it. One claim
without support holds the whole reply for a support agent. The check is narrow on purpose: it does
not judge tone or helpfulness, so it can be fast, cheap and run on every single reply.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from courier.kb import Passage, listing
from courier.llm import ask

INSTRUCTIONS = (
    "You check a support reply before it is sent to a food delivery courier. Split the reply into "
    "claims: each fact, amount, time limit, permission or instruction it states. For each claim, find "
    "the cited section that says it. A claim is supported only when a section states it for the "
    "courier's situation, including any condition or exception the section attaches. A claim that "
    "changes a number, drops an exception, or adds something no section says is unsupported. Quote "
    "the words of the section that decide it, or leave quote empty when none do."
)


class Claim(BaseModel):
    claim: str
    verdict: Literal["supported", "unsupported"]
    section: str | None
    quote: str


class Check(BaseModel):
    claims: list[Claim]


@dataclass
class Guarded:
    send: bool
    claims: list[dict] = field(default_factory=list)
    why: str = ""
    cost: float = 0.0


def guard(question: str, reply: str | None, sources: list[Passage], client, model: str) -> Guarded:
    # Nothing to check means nothing to send: no reply, or a reply that cites no section at all.
    if not reply:
        return Guarded(False, why="The bot had no answer from the help centre.")
    if not sources:
        return Guarded(False, why="The reply cites no help centre section.")
    user = f"Cited sections:\n\n{listing(sources)}\n\nCourier asked: {question}\n\nReply to check: {reply}"
    got, cost = ask(client, model, INSTRUCTIONS, user, Check)
    if got is None or not got.claims:
        # A check that did not run is not a pass.
        return Guarded(False, why="The grounding check did not return a result.", cost=cost)
    claims = [claim.model_dump() for claim in got.claims]
    bad = [claim for claim in claims if claim["verdict"] == "unsupported"]
    if bad:
        return Guarded(False, claims, f"{len(bad)} of {len(claims)} claims have no support: {bad[0]['claim'].rstrip('.')}.", cost)
    return Guarded(True, claims, f"All {len(claims)} claims found in the cited sections.", cost)
