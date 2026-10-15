"""Pick a tier for one call: free rules first, then a one-word label from the cheap model.

RouteLLM showed most calls do not need the strongest model. The catch is the calls that do: a wrong
refund or an allergy answer costs far more than the tokens saved. So the router only sends a call to
the cheap tier when both the rules and the label agree it is safe, and anything it is unsure about
goes to the strong tier and is flagged for the platform team.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

# A call that touches health is never worth saving a fraction of a cent on.
SAFETY = re.compile(r"\b(allerg\w*|coeliac|celiac|anaphyla\w*|epipen|peanuts?)\b", re.I)
# Packs inside packs: a case of trays of cans. Small models multiply the sizes together, so no label sends these cheap.
NESTED_PACKS = re.compile(r"\b(cases?|trays?|crates?|outers?)\b", re.I)
# Words that mean the answer depends on comparing or adding amounts, where small models slip.
AMOUNTS = re.compile(r"\b(more than|less than|at least|at most|over|above|below|total|charged|per ?cent)\b", re.I)
CHEAP_LABELS = {"lookup", "extract"}
LABELS = CHEAP_LABELS | {"judgment", "unclear"}

# The classifier sees only the user's message, not the team's instruction. A policy in the instruction
# would make every care call look hard, and the cheap tier handles the simple ones well.
CLASSIFY_PROMPT = (
    "Label this request for a model router. lookup: the answer is stated directly in the text. "
    "extract: copy or reformat fields from the text, with no rule to apply. judgment: it needs a policy, "
    "arithmetic, a unit conversion, a count of packs inside packs, a diet or allergy constraint, a fact the "
    "text may not state, or wording that could mislead. "
    "unclear: you cannot tell. Reply with one word: lookup, extract, judgment or unclear.")


@dataclass
class Decision:
    tier: str  # "cheap" or "strong"
    reason: str
    flagged: bool  # True when the router was unsure and a person should look
    label: str | None = None
    signals: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


def read_label(reply: str | None) -> str:
    """The first known label in the reply. Anything else counts as unclear, which fails closed."""
    for word in re.findall(r"[a-z]+", (reply or "").lower()):
        if word in LABELS:
            return word
    return "unclear"


async def route(user_text: str, classify: Callable[[str], Awaitable[str]]) -> Decision:
    safety = SAFETY.search(user_text)
    if safety:
        # A rule this sure needs no model call, which also saves the classifier's cost.
        return Decision("strong", f"Safety rule: the request mentions {safety.group().lower()!r}.", False,
                        signals=("safety",))
    nested = NESTED_PACKS.search(user_text)
    if nested:
        return Decision("strong", f"Pack rule: {nested.group().lower()!r} means packs inside packs.", False,
                        signals=("packs",))
    amounts = sorted({match.group().lower() for match in AMOUNTS.finditer(user_text)})
    try:
        label = read_label(await classify(user_text))
    # A classifier outage must not stop the call or send it cheap: the strong tier answers, and it is flagged.
    except Exception as error:
        return Decision("strong", f"The classifier failed ({type(error).__name__}), so the strong tier answers.",
                        True, "unclear")
    signals = ("amounts",) if amounts else ()
    if label == "unclear":
        return Decision("strong", "The classifier could not label this request.", True, label, signals)
    if label == "judgment":
        return Decision("strong", "The classifier says this needs judgment.", False, label, signals)
    if amounts:
        # The rules and the model disagree. Fail closed: pay for the strong tier and ask a person.
        return Decision("strong", f"The classifier says {label}, but the request compares amounts "
                        f"({', '.join(repr(word) for word in amounts)}).", True, label, signals)
    return Decision("cheap", f"The classifier says {label}, and no rule objects.", False, label, signals)
