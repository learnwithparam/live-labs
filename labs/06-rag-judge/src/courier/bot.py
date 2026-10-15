"""The support bot: drafts a reply to a courier from the help centre sections search found.

The draft is not sent from here. It goes to the grounding check first, which is why the bot must
name the sections it used: the check reads exactly those, not whatever search happened to return.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from courier.kb import HelpCentre, listing
from courier.llm import ask

INSTRUCTIONS = (
    "You reply to a food delivery courier for Swiftbite courier support, using only the help centre "
    "sections given. Answer the courier's actual question in two to four short sentences. Copy amounts, "
    "times and menu names exactly. cited lists the id of every section the reply comes from. When the "
    "sections do not answer the question, set reply to null: a person on the support team will answer."
)


class Draft(BaseModel):
    # null, not a guess: an empty draft goes straight to a person.
    reply: str | None
    cited: list[str]


@dataclass
class Drafted:
    reply: str | None
    cited: list[str]
    cost: float


def draft(question: str, help_centre: HelpCentre, client, model: str) -> Drafted:
    found = help_centre.search(question)
    got, cost = ask(client, model, INSTRUCTIONS, f"Help centre sections:\n\n{listing(found)}\n\nCourier: {question}", Draft)
    if got is None:
        return Drafted(None, [], cost)
    # A model can cite an id it was not shown; only real sections count as sources.
    return Drafted(got.reply, [passage_id for passage_id in got.cited if passage_id in help_centre.by_id], cost)
