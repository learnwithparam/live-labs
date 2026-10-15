"""Run every refund both ways, crash each one right after its payout, and count what the ledger shows.

The plain script crashes, then an operator runs it again, as anyone would. The workflow's worker
crashes, then a supervisor starts a new worker, as any deploy does. The manager approves every
refund that asks. Then the payments ledger, not either program's own story, says what happened.
"""

import asyncio
import subprocess
import time
from collections import Counter
from collections.abc import Callable

from temporalio.client import Client

from refunds import naive
from refunds.history import status, timeline
from refunds.ledger import Ledger
from refunds.requests import Request
from refunds.workflow import RefundWorkflow, TASK_QUEUE, workflow_id

MANAGER = "Priya Shah, store manager"
WAYS = ("naive", "durable")


class Crash(Exception):
    """The plain script's process dying, raised in place of exiting so the review itself keeps running."""


def raise_crash() -> None:
    raise Crash()


def naive_way(reqs: list[Request], ledger: Ledger, client, model: str, on_event=None) -> dict[str, float]:
    """Each refund's last decided amount, after the crash and the rerun."""
    decided = {}
    for refund_request in reqs:
        ledger.record_verdict(refund_request.id, "approved", MANAGER)
        try:
            got = naive.run(refund_request.id, ledger, client, model, crash_after_pay=True, crash=raise_crash, poll=0.05)
        except Crash:
            if on_event:
                on_event({"how": "naive", "refund": refund_request.id, "event": "crashed after paying; run again"})
            # The operator's restart: same script, same refund, from the top.
            got = naive.run(refund_request.id, ledger, client, model, poll=0.05)
        decided[refund_request.id] = got["decision"]["amount"]
        if on_event:
            on_event({"how": "naive", "refund": refund_request.id, "event": got["stage"]})
    return decided


async def durable_way(temporal: Client, reqs: list[Request], start_worker: Callable[[], subprocess.Popen], run: str,
                      queue: str = TASK_QUEUE, on_event=None, timeout: float = 600) -> tuple[dict[str, float], int, int]:
    """Start every refund, keep a worker alive, approve what waits.

    Returns the decided amounts, the worker's deaths, and how many decide attempts the histories record.
    """
    worker, deaths = start_worker(), 0
    handles = {refund_request.id: await temporal.start_workflow(RefundWorkflow.run, refund_request.id, id=workflow_id(refund_request.id, run), task_queue=queue)
               for refund_request in reqs}
    approved, finished, deadline = set(), set(), time.monotonic() + timeout
    try:
        while len(finished) < len(handles):
            if time.monotonic() > deadline:
                raise TimeoutError(f"refunds still open after {timeout:.0f} s: {sorted(set(handles) - finished)}")
            if worker.poll() is not None:
                # The worker died. A new one picks up every open workflow from its history on the server.
                deaths += 1
                if on_event:
                    on_event({"how": "durable", "event": "worker died; a new worker starts"})
                worker = start_worker()
            for refund, handle in handles.items():
                if refund in finished:
                    continue
                # Read the history, not a query: a query waits for a worker, and the worker may be dead.
                now = status(await handle.fetch_history())
                if now["done"]:
                    finished.add(refund)
                    if on_event:
                        on_event({"how": "durable", "refund": refund, "event": "done"})
                    continue
                if now["stage"] == "waiting" and refund not in approved:
                    await handle.signal(RefundWorkflow.approve, MANAGER)
                    approved.add(refund)
            await asyncio.sleep(0.5)
    finally:
        worker.terminate()
    decided = {}
    for refund, handle in handles.items():
        # A failed workflow has no result. None marks it: if nothing was paid, the refund was lost.
        got = status(await handle.fetch_history())
        decided[refund] = got["decision"]["amount"] if got["stage"] != "failed" and got["decision"] else None
    # A decide still running when the worker died had no result to record, so Temporal ran it again.
    # The history counts those attempts; a finished decide is never attempted again.
    attempts = 0
    for handle in handles.values():
        attempts += sum(event["attempt"] for event in timeline(await handle.fetch_history()) if event["event"] == "completed" and event["step"] == "decide")
    return decided, deaths, attempts


def score(reqs: list[Request], ledger: Ledger, how: str, decided: dict[str, float]) -> dict:
    """What the payments ledger says for one way: the metric is paid twice plus lost."""
    payouts = ledger.payouts(how)
    per = Counter(payout["refund"] for payout in payouts)
    due = [refund_request for refund_request in reqs if refund_request.ruling > 0]
    return {
        "refunds": len(reqs),
        "paid_twice": sum(times_paid > 1 for times_paid in per.values()),
        # The program decided to pay and the manager approved, but the ledger shows no payout.
        "lost": sum((amount is None or amount > 0) and per[refund] == 0 for refund, amount in decided.items()),
        "paid_right": sum(per[refund_request.id] == 1 and next(payout["cents"] for payout in payouts if payout["refund"] == refund_request.id) == round(refund_request.ruling * 100) for refund_request in due),
        "due": len(due),
        "dollars_out": round(sum(payout["cents"] for payout in payouts) / 100, 2),
        "dollars_due": round(sum(refund_request.ruling for refund_request in reqs), 2),
        "model_calls": sum(ledger.calls(how).values()),
        # The AI step's quality, apart from the crash: how often the decided amount matches finance's ruling.
        "decided_right": sum(decided.get(refund_request.id) is not None and abs(decided[refund_request.id] - refund_request.ruling) < 0.005 for refund_request in reqs),
    }
