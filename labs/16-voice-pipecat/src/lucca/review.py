"""Score the phone line against the orders a person keyed from the same calls.

Two numbers decide whether the line can take calls alone. Orders taken with no staff counts only exact
matches, because a wrong sauce is a remade order. Turn latency is read at p50 and p95, because callers
hang up on the slow turns, not the average one.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from lucca.caller import load_calls
from lucca.pipeline import run_call
from lucca.till import check
from lucca.turns import percentile

OUT_FILE = Path("out/calls.json")
FIRST_ORDER_NUMBER = 412  # the till's next ticket number when the shift starts


def score(rows: list[dict]) -> dict:
    """The metric from finished calls: exact orders, correct hand-offs, and the latency of every turn
    that got an answer."""
    orders = [row for row in rows if not row["staff_expected"]]
    latencies = [turn["latency_ms"] for row in rows for turn in row["turns"] if turn["latency_ms"] is not None]
    hear = [turn["hear_ms"] for row in rows for turn in row["turns"] if turn["hear_ms"] is not None]
    think = [turn["think_ms"] for row in rows for turn in row["turns"] if turn["think_ms"] is not None]
    speak = [turn["speak_ms"] for row in rows for turn in row["turns"] if turn["speak_ms"] is not None]
    return {
        "calls": len(rows),
        "orders": len(orders),
        "taken_alone": sum(row["ok"] for row in orders),
        "staff_expected": len(rows) - len(orders),
        "staff_correct": sum(row["ok"] for row in rows if row["staff_expected"]),
        "turns": sum(len(row["turns"]) for row in rows),
        "timed_turns": len(latencies),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p50_parts_ms": {"hear": percentile(hear, 0.5), "think": percentile(think, 0.5), "speak": percentile(speak, 0.5)},
        "cost": round(sum(row["cost"] for row in rows), 6),
    }


async def review_calls(call_ids: list[str] | None = None, on_event: Callable[[dict], None] = lambda event: None) -> dict:
    """Play each recorded call, one at a time, and keep what the till ended up with next to the keyed
    order. Calls run one after another, as one phone line takes them."""
    rows = []
    calls = [call for call in load_calls() if not call_ids or call["id"] in call_ids]
    for order_number, call in enumerate(calls, FIRST_ORDER_NUMBER):
        on_event({"kind": "call_start", "id": call["id"], "caller": call["caller"]})
        result = await run_call(call, order_number, lambda event, call_id=call["id"]: on_event({"call": call_id, **event}))
        ok, why = check(result["ticket"], call["keyed"])
        row = {**result, "ok": ok, "why": why, "staff_expected": "staff" in call["keyed"], "keyed": call["keyed"]}
        rows.append(row)
        on_event({"kind": "call_end", "id": call["id"], "ok": ok, "why": why})
        print(f"{call['id']} {'MATCH' if ok else 'MISS '} {why}", flush=True)
    report = {"rows": rows, "metric": score(rows)}
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(report, indent=2))
    return report


def review(call_ids: list[str] | None = None) -> dict:
    return asyncio.run(review_calls(call_ids))
