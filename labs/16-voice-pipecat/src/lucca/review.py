"""Score the phone line against the orders a person keyed from the same calls.

Pipecat's eval suite plays each recorded call into a fresh bot, speaking the caller's lines in their own
voice and waiting for each reply the way a caller does. The bot writes its till ticket and turn timings
when the call ends; this module reads those back and scores them.

Two numbers decide whether the line can take calls alone. Orders taken with no staff counts only exact
matches, because a wrong sauce is a remade order. Turn latency is read at p50 and p95, because callers
hang up on the slow turns, not the average one.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from pipecat.evals.suite import EvalManifest, EvalSuite

from lucca.bot import CALLS_OUT, LAB
from lucca.till import Till, check

CALLS_FILE = Path("data/calls.json")
MANIFEST = Path("evals/manifest.yaml")
OUT_FILE = Path("out/calls.json")


def load_calls() -> list[dict]:
    return json.loads(CALLS_FILE.read_text())


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile. With a few dozen turns a p95 is the slowest turn or close to it, which is
    the honest reading of a small sample."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, -(-int(share * 100) * len(ordered) // 100))
    return ordered[rank - 1]


def score(rows: list[dict]) -> dict:
    """The metric from finished calls: exact orders, correct hand-offs, and the latency of every turn."""
    orders = [row for row in rows if not row["staff_expected"]]
    turns = [turn for row in rows for turn in row["turns"]]

    def p50(name: str) -> float | None:
        # A turn the caller interrupted has a total but no split, so each part reads the turns that have one.
        return percentile([turn[name] for turn in turns if turn[name] is not None], 0.50)

    return {
        "calls": len(rows),
        "orders": len(orders),
        "taken_alone": sum(row["ok"] for row in orders),
        "staff_expected": len(rows) - len(orders),
        "staff_correct": sum(row["ok"] for row in rows if row["staff_expected"]),
        "lines": sum(row["lines"] for row in rows),
        "timed_turns": len(turns),
        "p50_ms": p50("latency_ms"),
        "p95_ms": percentile([turn["latency_ms"] for turn in turns], 0.95),
        "p50_parts_ms": {"hear": p50("hear_ms"), "think": p50("think_ms"), "speak": p50("speak_ms")},
        "cost": round(sum(row["cost"] for row in rows), 6),
    }


async def review_calls(call_ids: list[str] | None = None, on_event: Callable[[dict], None] = lambda event: None) -> dict:
    """Run the eval suite, one call at a time as one phone line takes them, then keep what each till
    ended up with next to the keyed order."""
    calls = [call for call in load_calls() if not call_ids or call["id"] in call_ids]
    suite = EvalSuite(EvalManifest.load(LAB / MANIFEST))
    suite.runs = [run for run in suite.runs if run.label in {call["id"] for call in calls}]
    for call in calls:
        (CALLS_OUT / f"{call['id']}.json").unlink(missing_ok=True)

    @suite.event_handler("on_update")
    def on_update(suite, run):
        on_event({"kind": "call_" + ("start" if run.status == "running" else run.status), "id": run.label})

    await suite.run(LAB / "out/evals/logs", results_path=LAB / "out/evals/results.jsonl")
    rows = []
    for call in calls:
        played = CALLS_OUT / f"{call['id']}.json"
        # A bot that never wrote its call left nothing on the till, which scores as an open ticket.
        result = json.loads(played.read_text()) if played.exists() else {
            "ticket": Till().ticket(), "turns": [], "cost": 0, "events": []}
        ok, why = check(result["ticket"], call["keyed"])
        run = next(run for run in suite.runs if run.label == call["id"])
        rows.append({**result, "id": call["id"], "caller": call["caller"], "ok": ok, "why": why,
                     "staff_expected": "staff" in call["keyed"], "keyed": call["keyed"],
                     "lines": sum(1 for event in result["events"] if event["kind"] == "caller"),
                     "eval_error": run.error})
        print(f"{call['id']} {'MATCH' if ok else 'MISS '} {why}", flush=True)
    report = {"rows": rows, "metric": score(rows)}
    save_shift(rows)
    return report


def save_shift(rows: list[dict]) -> None:
    """Keep the kitchen display's whole shift. Replaying one call replaces only that call's row, so the
    other callers' tickets stay on screen, and the shift's numbers are scored again over all of them."""
    kept = json.loads(OUT_FILE.read_text())["rows"] if OUT_FILE.exists() else []
    by_id = {row["id"]: row for row in kept} | {row["id"]: row for row in rows}
    order = [call["id"] for call in load_calls()]
    shift = sorted(by_id.values(), key=lambda row: order.index(row["id"]) if row["id"] in order else len(order))
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps({"rows": shift, "metric": score(shift)}, indent=2))


def review(call_ids: list[str] | None = None) -> dict:
    return asyncio.run(review_calls(call_ids))
