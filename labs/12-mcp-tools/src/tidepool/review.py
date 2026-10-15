"""Score both MCP servers on the 20 merchant tasks, from the store's state, never from the reply.

A task succeeds when the store changed exactly as expected: every expected field has its new value
and nothing else moved. A question succeeds when the reply holds the facts and nothing changed. An
ambiguous ask succeeds only if nothing changed and the assistant asked the merchant which one.
A wrong-tool call is any call the server answered with an error: a wrong tool, a wrong ID, a bad
argument. Each one costs a model step and teaches us where a tool misled the model.
"""

import asyncio
import json
from pathlib import Path

from tidepool import api_server, tools_server
from tidepool.assistant import ask
from tidepool.config import PRICES
from tidepool.store import Store

TASKS = Path(__file__).resolve().parents[2] / "data" / "tasks.jsonl"
SERVERS = {"api": api_server.build, "designed": tools_server.build}
NAMES = {"api": "API as is", "designed": "Designed tools"}
# Tasks run a few at a time: fast enough for a live lesson, gentle on the provider's rate limit.
PARALLEL = 5


def tasks() -> list[dict]:
    return [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]


def task(task_id: str) -> dict:
    found = [each for each in tasks() if each["id"] == task_id.upper()]
    if not found:
        raise SystemExit(f"No task {task_id}. The tasks are TP-01 to TP-{len(tasks()):02d}.")
    return found[0]


def same(want, got) -> bool:
    if isinstance(want, dict):
        # A discount is checked on the fields the task named; the rest may take any value.
        return isinstance(got, dict) and all(same(value, got.get(field)) for field, value in want.items())
    if isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(want, bool):
        return abs(want - got) < 0.01
    if isinstance(want, list):
        return sorted(want) == sorted(got or [])
    return want == got


def judge(task: dict, changes: dict, reply: str) -> tuple[bool, str]:
    """Did the store end up where the merchant wanted? Returns (success, why)."""
    expect = task["expect"]
    missing = [path for path, value in expect.items() if not same(value, changes.get(path))]
    extra = [path for path in changes if path not in expect]
    if extra:
        return False, "changed what was not asked: " + ", ".join(extra)
    if missing:
        return False, "not done: " + ", ".join(missing)
    if task.get("should_ask") and "?" not in reply:
        return False, "should have asked which one"
    lacking = [fact for fact in task.get("answer_has", []) if fact.lower() not in reply.lower()]
    if lacking:
        return False, "answer lacks " + ", ".join(lacking)
    return True, "asked the merchant" if task.get("should_ask") else "done"


def cost(model: str, tokens: dict) -> float:
    input_price, output_price = PRICES[model]
    return (tokens["prompt"] * input_price + tokens["completion"] * output_price) / 1e6


async def run_task(server: str, task: dict, client, model: str, on_step=lambda step: None) -> dict:
    store = Store()
    run = await ask(SERVERS[server](store), task["ask"], client, model, on_step)
    ok, why = judge(task, store.changes(), run["reply"])
    return {"server": server, "task": task["id"], "ask": task["ask"], "ok": ok, "why": why, "reply": run["reply"],
            "calls": run["calls"], "changes": store.changes(), "tools": run["tools"], "tokens": run["tokens"],
            "cost": cost(model, run["tokens"])}


def score(rows: list[dict]) -> dict:
    return {"tasks": len(rows), "succeeded": sum(row["ok"] for row in rows),
            "calls": sum(len(row["calls"]) for row in rows),
            "wrong_calls": sum(call["error"] for row in rows for call in row["calls"]),
            "tools": rows[0]["tools"] if rows else 0,
            "prompt_tokens": sum(row["tokens"]["prompt"] for row in rows),
            "cost": round(sum(row["cost"] for row in rows), 5)}


async def review(client, model: str, on_row=lambda row: None) -> dict:
    gate = asyncio.Semaphore(PARALLEL)

    async def run_one(server, task):
        async with gate:
            row = await run_task(server, task, client, model)
            on_row(row)
            return row

    results = {}
    for server in SERVERS:
        rows = await asyncio.gather(*(run_one(server, task) for task in tasks()))
        results[server] = {"score": score(list(rows)), "rows": sorted(rows, key=lambda row: row["task"])}
    return results
