"""Finance's answer to a held job, sent as a durable callback so the suspended execution resumes.

While a job waits for finance it holds no process and costs nothing. Approving or rejecting completes
the callback the job is waiting on; the runner then invokes the handler, which replays its finished
steps from checkpoints and runs only what comes after the wait.
"""

import json
import time

from meterline import ledger
from meterline.batch import DONE, build_report
from meterline.runtime import LocalCloud


def answer(cloud: LocalCloud, customer_id: str, approved: bool, reviewer: str, wait_seconds: float = 60) -> dict:
    """Send the decision and wait for the job's outcome. Only a job still waiting can be answered."""
    state = cloud.out / "meterline.db"
    waiting = ledger.rows(state, "select * from review_queue where customer_id = ? and status = 'waiting'", (customer_id,))
    if not waiting:
        raise ValueError(f"{customer_id} is not waiting for finance.")
    decision = "approved" if approved else "rejected"
    cloud.answer_callback(waiting[0]["callback_id"], {"decision": decision, "reviewer": reviewer})
    run = json.loads((cloud.out / "run.json").read_text())
    arn = run["executions"][customer_id]
    deadline = time.time() + wait_seconds
    while cloud.status(arn)["Status"] not in DONE and time.time() < deadline:
        time.sleep(0.3)
    report = build_report(cloud)
    (cloud.out / "report.json").write_text(json.dumps(report, indent=1))
    return next(lane for lane in report["lanes"] if lane["customer_id"] == customer_id)
