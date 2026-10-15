"""The same refund as a plain script: decide, wait for the manager, pay. The way most teams start.

It works until the process dies. Run again, it starts from the top: it asks the model again, which
can decide a different amount, and it pays again, because nothing remembers the first payout.
"""

import os
import time

from refunds.decide import decide
from refunds.ledger import Ledger
from refunds.requests import orders, request


def run(refund: str, ledger: Ledger, client, model: str, crash_after_pay: bool = False, crash=lambda: os._exit(1),
        poll: float = 1.0) -> dict:
    refund_request = request(refund)
    ledger.count_call(refund, "naive")
    decision, _ = decide(refund_request, client, model)
    if decision["needs_approval"]:
        # The wait is a loop in this process. If the process dies, so does the wait.
        while (verdict := ledger.verdict(refund)) is None:
            time.sleep(poll)
        if verdict == "rejected":
            return {"stage": "rejected", "decision": decision}
    if decision["amount"] <= 0:
        return {"stage": "declined", "decision": decision}
    # No idempotency key: each run is a new payout as far as the provider can tell.
    payout = ledger.pay(refund, orders()[refund_request.order]["customer"], round(decision["amount"] * 100), "naive", None)
    if crash_after_pay:
        crash()
    return {"stage": "paid", "decision": decision, "payout": payout}
