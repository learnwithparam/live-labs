"""Run every claim through read, price and route, then grade the run against the adjuster's file.

The grade uses three things the model never sees: the panels an adjuster confirmed on each photo, the
repairer's final bill for each claim that was repaired, and which claims an adjuster had to handle.
A run that writes nicely but misses the bill scores zero, which is the point of grading outside the model.
"""

import asyncio
import json
from pathlib import Path

from saltmarsh.claims import claims, rates, truth
from saltmarsh.config import PARALLEL
from saltmarsh.pricing import merge, price
from saltmarsh.reader import add_usage, free_text_estimate, read_claim
from saltmarsh.triage import covered, route

OUT = Path("out")


async def assess_claim(client, model: str, claim: dict, table: dict, limit: asyncio.Semaphore) -> dict:
    read = await read_claim(client, model, claim, limit)
    estimate = price(merge(covered(read["readings"])), claim["vehicle"], table)
    usage: dict = {}
    for reading in read["readings"]:
        add_usage(usage, reading["usage"])
    add_usage(usage, read["vehicle_check"]["usage"])
    return {"claim": claim["id"], **read, "estimate": estimate,
            "route": route(read["readings"], read["vehicle_check"], estimate, table), "usage": usage}


def within(estimate: float | None, bill: float, tolerance: float) -> bool:
    return estimate is not None and abs(estimate - bill) <= tolerance * bill


def score_panels(results: list[dict], labels: dict) -> dict:
    """Panels the model named against panels the adjuster confirmed, photo by photo.

    Photos the adjuster marked unusable are left out: there is nothing confirmed on them to find.
    Exact panel names only, so a right wing read as a left wing counts as one miss and one extra.
    """
    confirmed = found = right = 0
    for result in results:
        for reading in result["readings"]:
            label = labels[reading["photo"]]
            if not label["usable"]:
                continue
            truth_panels = {finding["panel"] for finding in label["findings"]}
            model_panels = {finding["panel"] for finding in reading["findings"]}
            confirmed += len(truth_panels)
            found += len(model_panels)
            right += len(truth_panels & model_panels)
    return {"confirmed": confirmed, "found": found, "right": right,
            "recall": round(right / confirmed, 3) if confirmed else 0.0,
            "precision": round(right / found, 3) if found else 0.0}


def score(results: list[dict], adjuster: dict, tolerance: float) -> dict:
    """The run's metric: estimates within tolerance of the bill, routing right, panels right."""
    billed = [result for result in results if "bill" in adjuster["claims"][result["claim"]]]
    close = [result["claim"] for result in billed
             if within(result["estimate"]["total"], adjuster["claims"][result["claim"]]["bill"], tolerance)]
    routed_right, reasons_caught = [], []
    for result in results:
        expected = adjuster["claims"][result["claim"]]["route"]
        codes = {reason["code"] for reason in result["route"]["reasons"]}
        # Routing is right when an expected-auto claim stays auto and an expected-adjuster claim goes to
        # an adjuster; the reason is scored apart, since the wrong reason misleads the adjuster.
        if (result["route"]["to"] == "adjuster") == bool(expected):
            routed_right.append(result["claim"])
        if expected and set(expected) <= codes:
            reasons_caught.append(result["claim"])
    return {"billed": len(billed), "within_tolerance": len(close), "close_claims": close,
            "claims": len(results), "routed_right": len(routed_right),
            "expected_adjuster": sum(1 for row in adjuster["claims"].values() if row["route"]),
            "reasons_caught": len(reasons_caught),
            "auto": sum(1 for result in results if result["route"]["to"] == "auto"),
            "adjuster": sum(1 for result in results if result["route"]["to"] == "adjuster"),
            "panels": score_panels(results, adjuster["photos"])}


async def assess(client, model: str) -> dict:
    """The whole queue. Claims run together, but one shared limit caps how many photos are in flight."""
    table, adjuster = rates(), truth()
    limit = asyncio.Semaphore(PARALLEL)
    results = await asyncio.gather(*(assess_claim(client, model, claim, table, limit) for claim in claims()))
    usage: dict = {}
    for result in results:
        add_usage(usage, result["usage"])
    run = {"model": model, "results": list(results), "score": score(list(results), adjuster, table["tolerance"]),
           "usage": usage}
    save("run.json", run)
    return run


async def naive(client, model: str) -> dict:
    """The free-text baseline on the claims that have a bill, graded by the same tolerance."""
    table, adjuster = rates(), truth()
    billed = [claim for claim in claims() if "bill" in adjuster["claims"][claim["id"]]]
    limit = asyncio.Semaphore(PARALLEL)

    async def bounded(claim):
        async with limit:
            return await free_text_estimate(client, model, claim)

    estimates = await asyncio.gather(*(bounded(claim) for claim in billed))
    usage: dict = {}
    for estimate in estimates:
        estimate["bill"] = adjuster["claims"][estimate["claim"]]["bill"]
        estimate["within"] = within(estimate["amount"], estimate["bill"], table["tolerance"])
        add_usage(usage, estimate["usage"])
    run = {"model": model, "estimates": list(estimates), "usage": usage,
           "within_tolerance": sum(1 for estimate in estimates if estimate["within"]), "billed": len(estimates)}
    save("naive.json", run)
    return run


def save(name: str, data: dict) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1))


def load(name: str) -> dict | None:
    path = OUT / name
    return json.loads(path.read_text()) if path.exists() else None
