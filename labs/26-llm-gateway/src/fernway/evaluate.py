"""Replay the quality set through the gateway in one mode, then score it with the graders and the bill.

The naive mode asks for the strong tier on every call, the way most teams start. The routed mode asks
for `auto` and lets the gateway pick. Both runs use each team's own key, so the cost lands on the team
that would pay it, and the comparison is cost per request at the same quality.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fernway.budgets import Ledger, state_dir
from fernway.quality import grade, items, messages

MODES = {"strong": "strong", "routed": "auto", "cheap": "cheap"}
# The routed run may pass at most this many fewer items than the naive run. One item in 29 is the bar.
ALLOWED_DROP = 1
PARALLEL_CALLS = 4
LEDGER_WAIT_SECONDS = 20


def result_path(mode: str) -> Path:
    return state_dir() / f"eval-{mode}.json"


def ask_one(client_for, item: dict, mode: str, run: str) -> dict:
    client = client_for(item["team"])
    try:
        response = client.chat.completions.create(
            model=MODES[mode], messages=messages(item), temperature=0,
            extra_body={"metadata": {"item": item["id"], "run": run}})
        reply = response.choices[0].message.content
    # A refused or failed call is a failed item, never a skipped one: skipping would flatter the score.
    except Exception as error:
        return {"id": item["id"], "reply": None, "error": str(error)[:300], **grade(item, None)}
    return {"id": item["id"], "reply": reply, "error": None, **grade(item, reply)}


def wait_for_ledger(run: str, expected: int, ledger: Ledger) -> list[dict]:
    """The proxy books each call just after it answers. Wait until every answer has its row."""
    deadline = time.monotonic() + LEDGER_WAIT_SECONDS
    while True:
        rows = [row for row in ledger.rows() if row.get("run") == run]
        answered = {row["item"] for row in rows if row.get("purpose") == "answer" and row.get("status") != "provider_error"}
        if len(answered) >= expected or time.monotonic() > deadline:
            return rows
        time.sleep(0.25)


def run_mode(mode: str, client_for, ledger: Ledger | None = None, only: list[str] | None = None) -> dict:
    ledger = ledger or Ledger()
    chosen = [item for item in items() if not only or item["id"] in only]
    run = f"{mode}-{uuid.uuid4().hex[:6]}"
    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        answers = list(pool.map(lambda item: ask_one(client_for, item, mode, run), chosen))
    rows = wait_for_ledger(run, sum(answer["error"] is None for answer in answers), ledger)
    results = []
    for item, answer in zip(chosen, answers):
        mine = [row for row in rows if row.get("item") == item["id"]]
        final = next((row for row in mine if row.get("purpose") == "answer" and row["status"] in ("ok", "refused")), {})
        results.append({**answer, "team": item["team"], "task": item["task"], "difficulty": item["difficulty"],
                        "tier": final.get("tier"), "model": final.get("model"), "reason": final.get("reason"),
                        "flagged": final.get("flagged", False), "fallback_from": final.get("fallback_from"),
                        "status": final.get("status", "error"), "call_id": final.get("call_id"),
                        # The classifier's call is part of what routing costs, so it is in the request's cost.
                        "cost": sum(row.get("cost") or 0.0 for row in mine),
                        "billed": all(row.get("billed") for row in mine if row["status"] == "ok") and bool(mine)})
    summary = summarise(mode, run, results)
    result_path(mode).parent.mkdir(parents=True, exist_ok=True)
    result_path(mode).write_text(json.dumps(summary, indent=1))
    return summary


def summarise(mode: str, run: str, results: list[dict]) -> dict:
    total_cost = sum(result["cost"] for result in results)
    return {"mode": mode, "run": run, "requests": len(results), "passed": sum(result["passed"] for result in results),
            "cost": total_cost, "cost_per_request": total_cost / len(results) if results else 0.0,
            "tiers": {tier: sum(result["tier"] == tier for result in results) for tier in ("cheap", "strong", "strong-backup")},
            "flagged": sum(result["flagged"] for result in results),
            "all_billed": all(result["billed"] for result in results), "results": results}


def compare(naive: dict, routed: dict) -> dict:
    """The metric: cost per request, held to the naive run's quality minus the allowed drop."""
    saving = 1 - routed["cost_per_request"] / naive["cost_per_request"] if naive["cost_per_request"] else 0.0
    return {"naive_passed": naive["passed"], "routed_passed": routed["passed"], "requests": naive["requests"],
            "naive_cost_per_request": naive["cost_per_request"], "routed_cost_per_request": routed["cost_per_request"],
            "saving": saving, "quality_holds": routed["passed"] >= naive["passed"] - ALLOWED_DROP,
            "cheaper": routed["cost_per_request"] < naive["cost_per_request"]}


def load(mode: str) -> dict | None:
    path = result_path(mode)
    return json.loads(path.read_text()) if path.exists() else None
