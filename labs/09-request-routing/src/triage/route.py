"""What every router returns, and the rules that turn it into a queue or a person.

The three routers differ in how they pick a queue and how much their confidence can be trusted.
The rule after them is the same, so the review compares the routers and nothing else.
"""

from dataclasses import dataclass, field

# Below this, the ticket goes to a person on the triage rota instead of a queue. One number for all
# three routers: a router whose confidence means something sends its doubtful tickets here.
CONFIDENT = 0.6
PERSON = "person"


@dataclass
class Route:
    queue: str
    confidence: float
    # Every queue's probability, when the router has them. An LLM gives one number it wrote itself.
    probabilities: dict[str, float] = field(default_factory=dict)
    ms: float = 0.0
    cost: float = 0.0


def destination(route: Route) -> str:
    """Where the ticket goes: the router's queue when it is confident enough, otherwise a person."""
    return route.queue if route.confidence >= CONFIDENT else PERSON


def second_opinion(llm: Route, decision: Route) -> Route:
    """The LLM picks, the decision model checks: route only when both name the same queue, confidently.

    The LLM reads a vague ticket better, but its confidence is a number it wrote and is almost always
    0.9 or more. The decision model's probability is trained to match how often it is right, so a
    disagreement or a low probability is a real signal that a person should look first.
    """
    agreed = llm.queue == decision.queue and decision.confidence >= CONFIDENT
    return Route(llm.queue, decision.confidence if agreed else 0.0, decision.probabilities,
                 ms=max(llm.ms, decision.ms), cost=llm.cost + decision.cost)
