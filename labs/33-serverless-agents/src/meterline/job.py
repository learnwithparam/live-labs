"""One night's job for one customer, written as an AWS Lambda durable function.

Each `context.step` checkpoints its result with the durable execution service. When the process dies,
the service invokes the handler again, the SDK replays finished steps from their checkpoints, and only
the step that was running when the process died runs again. That is why the paid model call and the
ledger write each sit in their own step, and why the ledger write still carries an idempotency key.
"""

import json
import os
from functools import cache
from pathlib import Path

from aws_durable_execution_sdk_python import DurableContext, StepContext, durable_execution, durable_step
from aws_durable_execution_sdk_python.config import Duration, StepConfig, WaitForCallbackConfig
from aws_durable_execution_sdk_python.exceptions import ExecutionError, StepError
from aws_durable_execution_sdk_python.retries import JitterStrategy, RetryStrategyConfig, create_retry_strategy

from meterline import billing, ledger
from meterline.config import OUT, PERIOD, llm_settings
from meterline.terms import decide, model_client, read_contract

STEP_ATTEMPTS = 3
# Three tries a second apart, then the step fails for good. A late export or a model timeout gets
# another chance; a customer with no usage row stops with its reason instead of retrying all night.
RETRY = StepConfig(retry_strategy=create_retry_strategy(RetryStrategyConfig(
    max_attempts=STEP_ATTEMPTS, initial_delay=Duration.from_seconds(1), backoff_rate=1.0,
    jitter_strategy=JitterStrategy.NONE)))
# Finance answers within a day; while the job waits, it is suspended and nothing is billed.
REVIEW_WAIT = WaitForCallbackConfig(timeout=Duration.from_hours(24))


def state_file() -> Path:
    return Path(os.environ.get("METERLINE_DB", OUT / "meterline.db"))


@cache
def model() -> tuple:
    base_url, api_key, model_name = llm_settings()
    return model_client(base_url, api_key), model_name


@durable_step
def fetch_billing(step_context: StepContext, customer_id: str) -> dict:
    return billing.fetch(customer_id)


@durable_step
def read_terms(step_context: StepContext, customer_id: str, contract: str) -> dict:
    """The paid step. The call is logged before it starts, so a call cut off by a crash still shows up."""
    client, model_name = model()
    call_id = ledger.start_model_call(state_file(), customer_id, step_context.attempt)
    reply, usage = read_contract(client, model_name, contract)
    ledger.finish_model_call(state_file(), call_id, usage["cost"])
    return {"reply": reply, "usage": usage}


@durable_step
def write_ledger(step_context: StepContext, entry: dict) -> dict:
    # The row can commit and the process die before this step's checkpoint lands. The rerun then
    # inserts again, and the key turns that second insert into a no-op instead of a second invoice.
    return ledger.write_invoice(state_file(), entry, ledger.ledger_key(entry["customer_id"], entry["period"]))


def ledger_entry(job: dict, total: float, adjustment: float, approved_by: str) -> dict:
    return {"customer_id": job["customer_id"], "invoice": job["invoice"], "period": PERIOD,
            "total": total, "adjustment": adjustment, "approved_by": approved_by}


@durable_execution
def handler(event: dict, context: DurableContext) -> dict:
    customer_id = event["customer_id"]
    try:
        job = context.step(fetch_billing(customer_id), name="fetch_billing", config=RETRY)
    except StepError as error:
        # Past the retry budget: fail the execution with the reason, so the run shows why and writes nothing.
        raise ExecutionError(f"Stopped after {STEP_ATTEMPTS} attempts: {error.message}") from error
    reading = context.step(read_terms(customer_id, job["contract"]), name="read_terms", config=RETRY)
    # Validation is plain code over a checkpointed reply, so a replay reaches the same decision for free.
    decision = decide(reading["reply"], job)
    if decision["action"] == "write":
        written = context.step(write_ledger(ledger_entry(job, decision["proposed_total"], decision["adjustment"], "agent")),
                               name="write_ledger")
        return {"customer_id": customer_id, "outcome": "corrected" if decision["adjustment"] else "matched",
                "total": written["total"], "adjustment": decision["adjustment"]}

    def ask_finance(callback_id: str, callback_context) -> None:
        ledger.queue_for_review(state_file(), customer_id, callback_id, decision, job["draft_total"])

    answer = json.loads(context.wait_for_callback(ask_finance, name="finance_review", config=REVIEW_WAIT))
    ledger.close_review(state_file(), customer_id, answer["decision"], answer["reviewer"])
    if answer["decision"] != "approved":
        return {"customer_id": customer_id, "outcome": "rejected", "reasons": decision["reasons"], "reviewer": answer["reviewer"]}
    written = context.step(write_ledger(ledger_entry(job, decision["proposed_total"], decision["adjustment"], answer["reviewer"])),
                           name="write_ledger")
    return {"customer_id": customer_id, "outcome": "approved", "total": written["total"], "adjustment": decision["adjustment"],
            "reviewer": answer["reviewer"]}
