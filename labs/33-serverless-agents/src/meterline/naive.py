"""The baseline: the same nightly job as a plain loop in one long-running process on an always-on VM.

It does the same work with the same model and the same validation. What it lacks is a checkpoint: when
the process dies, systemd restarts it from the top, so finished jobs pay for their model call again and
write their invoice again. Its plain insert has no idempotency key, the way first versions usually ship.
"""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from meterline import billing, ledger
from meterline.bill import vm_bill
from meterline.config import PERIOD
from meterline.host import arm_crash
from meterline.job import model
from meterline.terms import decide, read_contract

# The same width as the local runner's invoke pool, so both versions take about as long.
WORKERS = 12


def one_job(state: Path, customer_id: str) -> str:
    try:
        job = billing.fetch(customer_id)
    except billing.MissingExport:
        return "stopped"
    client, model_name = model()
    call_id = ledger.start_model_call(state, customer_id, 1)
    reply, usage = read_contract(client, model_name, job["contract"])
    ledger.finish_model_call(state, call_id, usage["cost"])
    decision = decide(reply, job)
    if decision["action"] == "hold":
        ledger.queue_for_review(state, customer_id, "", decision, job["draft_total"])
        return "held"
    ledger.write_invoice(state, {"customer_id": customer_id, "invoice": job["invoice"], "period": PERIOD,
                                 "total": decision["proposed_total"], "adjustment": decision["adjustment"],
                                 "approved_by": "agent"}, idempotency_key=None)
    return "written"


def worker(state: Path, crash_after_writes: int) -> None:
    """One process lifetime: loop over every customer from the top. It has no memory of a previous life."""
    if crash_after_writes:
        arm_crash(crash_after_writes)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda customer_id: one_job(state, customer_id), billing.tonights_jobs()))


def run_naive(out: Path, crash_after_writes: int) -> dict:
    """Run the loop, let it die after N writes, restart it the way `Restart=always` would, then count."""
    state = out / "naive.db"
    for leftover in out.glob("naive.db*"):
        leftover.unlink()
    out.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    lives = []
    for crash_after in (crash_after_writes, 0):
        process = subprocess.run([sys.executable, "-m", "meterline.naive", str(state), str(crash_after)])
        lives.append({"exit_code": process.returncode, "at": round(time.time() - started_at, 1)})
    rows = ledger.rows(state, "select customer_id, count(*) as copies from ledger group by customer_id, period")
    calls = ledger.rows(state, "select customer_id, started_at, finished_at, cost from model_calls")
    # Counted the same way as the durable run: a call that starts after the same job already had an answer.
    recalled = [call for call in calls if any(done["customer_id"] == call["customer_id"] and done["finished_at"]
                                              and done["finished_at"] < call["started_at"] for done in calls)]
    per_customer: dict[str, int] = {}
    for call in calls:
        per_customer[call["customer_id"]] = per_customer.get(call["customer_id"], 0) + 1
    held = ledger.rows(state, "select distinct customer_id from review_queue")
    model_cost = sum(call["cost"] or 0 for call in calls)
    report = {
        "lives": lives, "duration": round(time.time() - started_at, 1), "jobs": len(billing.tonights_jobs()),
        "ledger_rows": sum(row["copies"] for row in rows), "customers_written": len(rows), "held": len(held),
        "duplicate_writes": sum(row["copies"] - 1 for row in rows),
        "customers_billed_twice": sorted(row["customer_id"] for row in rows if row["copies"] > 1),
        "model_calls": len(calls), "model_calls_repeated": sum(count - 1 for count in per_customer.values()),
        "completed_model_steps_recalled": len(recalled),
        "bill": {"model": model_cost, "vm": vm_bill(), "per_run": model_cost + vm_bill()["total"]},
    }
    (out / "naive.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    worker(Path(sys.argv[1]), int(sys.argv[2]))
