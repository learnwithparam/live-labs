"""Score an audit run: was the audit right, what did it cost, and how much did the cache carry.

Right means every invoice got the finding the contract gives it, overcharge within a euro. Cost and
cache come from the provider's own numbers on each step, added up. Cost per completed audit is the
product number: a cheap run that got an invoice wrong completed nothing.
"""

from halyard.books import expected

OVERCHARGE_SLACK = 1.00  # euros; the model may round a fuel line differently from the carrier


def correct(finding: dict | None, want) -> bool:
    if not finding or finding["verdict"] != want.verdict:
        return False
    return want.verdict == "approve" or abs(finding["overcharge"] - want.overcharge) <= OVERCHARGE_SLACK


def score(run: dict) -> dict:
    wanted = expected()
    rows = [{"invoice": invoice_id, "want": vars(want), "got": run["findings"].get(invoice_id),
             "right": correct(run["findings"].get(invoice_id), want)} for invoice_id, want in wanted.items()]
    steps = run["steps"]
    prompt = sum(step["prompt_tokens"] for step in steps)
    cached = sum(step["cached_tokens"] for step in steps)
    cost = sum(step["cost"] for step in steps)
    right = sum(row["right"] for row in rows)
    # Finished and every finding right: only then has the month been audited.
    complete = run["summary"] is not None and right == len(rows)
    return {"invoices": len(rows), "right": right, "complete": complete, "steps": len(steps),
            "prompt_tokens": prompt, "cached_tokens": cached, "hit_rate": round(cached / prompt, 3) if prompt else 0.0,
            "cost": round(cost, 6), "cost_per_completed": round(cost, 6) if complete else None,
            "billed": all(step["billed"] for step in steps), "rows": rows}


def disputes(run: dict) -> list[dict]:
    """The invoices the agent disputed: each waits on the accounts payable clerk before it goes to the carrier."""
    return [finding for finding in run["findings"].values() if finding["verdict"] == "dispute"]
