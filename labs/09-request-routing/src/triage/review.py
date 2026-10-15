"""Route this morning's tickets three ways and count what Tallyroll's agents would have to move.

A ticket in the wrong queue waits, gets read by the wrong team, then gets reassigned. A ticket sent
to a person costs a minute of triage. The review counts both for each router, beside its speed and cost.
"""

import math
from concurrent.futures import ThreadPoolExecutor

from triage.route import PERSON, Route, destination, second_opinion
from triage.tickets import Ticket

# "both" is not a fourth model: it is the LLM's pick, kept only when the decision model agrees.
WAYS = ("embedding", "llm", "decision", "both")


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def summary(rows: list[dict], how: str) -> dict:
    mine = [r for r in rows if r["how"] == how]
    routed = [r for r in mine if r["to"] != PERSON]
    return {
        "tickets": len(mine),
        # The metric: routed straight to a queue that turned out to be the wrong one.
        "reassigned": sum(r["to"] != r["truth"] for r in routed),
        "to_person": len(mine) - len(routed),
        "right_first_time": sum(r["to"] == r["truth"] for r in routed),
        # Its top pick was wrong, but its confidence was low enough that a person caught it.
        "caught": sum(r["to"] == PERSON and r["queue"] != r["truth"] for r in mine),
        "top_pick_right": sum(r["queue"] == r["truth"] for r in mine),
        "p95_ms": round(p95([r["ms"] for r in mine]), 1),
        "cost_per_1000": round(1000 * sum(r["cost"] for r in mine) / max(1, len(mine)), 4),
    }


def row(how: str, ticket: Ticket, route: Route) -> dict:
    return {"how": how, "id": ticket.id, "truth": ticket.queue, "queue": route.queue, "to": destination(route),
            "confidence": round(route.confidence, 3), "probabilities": {q: round(p, 3) for q, p in route.probabilities.items()},
            "ms": round(route.ms, 1), "cost": route.cost}


def as_route(r: dict) -> Route:
    return Route(r["queue"], r["confidence"], r["probabilities"], r["ms"], r["cost"])


def review(tickets: list[Ticket], routers: dict, on_row=None) -> dict:
    """Every ticket through every router in `routers` (a name to a callable), then one summary per router."""
    rows = []
    for how, router in routers.items():
        # A router says how many calls it takes at once: four for a hosted API, one for a model on this laptop.
        with ThreadPoolExecutor(getattr(router, "workers", 1)) as pool:
            for ticket, route in zip(tickets, pool.map(router, tickets)):
                rows.append(row(how, ticket, route))
                if on_row:
                    on_row(rows[-1])
    if "llm" in routers and "decision" in routers:
        # Both ran on every ticket, so the second opinion costs no extra call: it reuses their answers.
        routes = {(r["how"], r["id"]): r for r in rows}
        for ticket in tickets:
            llm, decision = (as_route(routes[(how, ticket.id)]) for how in ("llm", "decision"))
            rows.append(row("both", ticket, second_opinion(llm, decision)))
            if on_row:
                on_row(rows[-1])
        routers = routers | {"both": None}
    return {"rows": rows} | {how: summary(rows, how) for how in routers}
