"""The refund as a Temporal workflow: decide, wait for a manager when the amount is large, then pay once.

Temporal records each step's result in the workflow's history on the server. When a worker dies,
another worker replays that history: finished steps return their recorded result instead of running
again, so the model is not asked twice and a payout is not sent twice. The wait for a manager is a
line of code, and it costs nothing while it waits, for minutes or for days.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from refunds.decide import decide
    from refunds.ledger import Ledger
    from refunds.requests import orders, request

TASK_QUEUE = "refunds"
# A manager has three days to answer; after that the refund is escalated and keeps waiting.
APPROVAL_DEADLINE = timedelta(days=3)
# After this long without a decision the refund fails and goes to a person, however many tries it took.
DECIDE_GIVE_UP = timedelta(minutes=15)


@dataclass
class Payment:
    refund: str
    cents: int


class Steps:
    """The activities: the only code that touches the outside world, the model and the payments provider.

    `crash_after_pay` kills the worker right after the provider records a payout and before Temporal
    hears it finished: the worst moment, because the money has moved and nothing knows it yet.
    """

    def __init__(self, ledger: Ledger, client, model: str, crash_after_pay: bool = False, crash=lambda: os._exit(1)):
        self.ledger, self.client, self.model = ledger, client, model
        self.crash_after_pay, self.crash = crash_after_pay, crash
        self.crashed = ledger.path.parent / "crashed"

    @activity.defn(name="decide")
    def decide(self, refund: str) -> dict:
        self.ledger.count_call(refund, "durable")
        decision, cost = decide(request(refund), self.client, self.model)
        return decision | {"cost": cost}

    @activity.defn(name="pay")
    def pay(self, payment: Payment) -> dict:
        # The workflow id is the same on every retry, so the provider pays this refund once.
        key = f"{activity.info().workflow_id}:payout"
        customer = orders()[request(payment.refund).order]["customer"]
        payout = self.ledger.pay(payment.refund, customer, payment.cents, "durable", key)
        marker = self.crashed / payment.refund
        if self.crash_after_pay and not marker.exists():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            self.crash()
        return payout


@workflow.defn
class RefundWorkflow:
    def __init__(self) -> None:
        self.stage, self.decision, self.payout = "deciding", None, None
        self.verdict, self.manager, self.waiting_since = None, "", None

    @workflow.run
    async def run(self, refund: str) -> dict:
        # A model call can time out or fail; Temporal retries it. Once it returns, the answer is history.
        # Cap the time, not the attempts: a worker that dies mid-call also uses an attempt, and three
        # deaths in a row once failed a refund the model could have decided.
        self.decision = await workflow.execute_activity(
            "decide", refund, start_to_close_timeout=timedelta(seconds=90), schedule_to_close_timeout=DECIDE_GIVE_UP,
            retry_policy=RetryPolicy(maximum_attempts=0))
        if self.decision["needs_approval"]:
            self.stage, self.waiting_since = "waiting", workflow.now().isoformat()
            try:
                await workflow.wait_condition(lambda: self.verdict is not None, timeout=APPROVAL_DEADLINE)
            except asyncio.TimeoutError:
                # The timer lives on the server, so it fires even if no worker was running for those three days.
                self.stage = "escalated"
                await workflow.wait_condition(lambda: self.verdict is not None)
            if self.verdict == "rejected":
                self.stage = "rejected"
                return self.status()
        if self.decision["amount"] <= 0:
            self.stage = "declined"
            return self.status()
        self.stage = "paying"
        # A short timeout: if the worker dies mid-payment, the retry starts ten seconds later.
        self.payout = await workflow.execute_activity(
            "pay", Payment(refund, round(self.decision["amount"] * 100)), start_to_close_timeout=timedelta(seconds=10))
        self.stage = "paid"
        return self.status()

    @workflow.signal
    def approve(self, manager: str) -> None:
        self.verdict, self.manager = "approved", manager

    @workflow.signal
    def reject(self, manager: str) -> None:
        self.verdict, self.manager = "rejected", manager

    @workflow.query
    def status(self) -> dict:
        return {"stage": self.stage, "decision": self.decision, "payout": self.payout,
                "verdict": self.verdict, "manager": self.manager, "waiting_since": self.waiting_since}


def worker_parts(steps: Steps, queue: str = TASK_QUEUE) -> dict:
    """What a worker registers. The CLI and the tests build their workers from this one list.

    A worker takes any task on its queue. Two programs with different ledgers on one server need two
    queues, or one program's worker pays the other's refunds into the wrong ledger.
    """
    return {"task_queue": queue, "workflows": [RefundWorkflow], "activities": [steps.decide, steps.pay]}


def workflow_id(refund: str, run: str = "") -> str:
    """One workflow per refund. The id is the refund's identity: Temporal will not start a second one while it runs."""
    return f"refund-{refund}" + (f"-{run}" if run else "")
