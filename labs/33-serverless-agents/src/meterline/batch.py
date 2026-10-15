"""The nightly batch: one durable execution per customer, a real crash part way through, and the evidence.

The batch plays two parts AWS plays in production. It is the EventBridge schedule that starts one
execution per customer, and it is the Lambda fleet that brings up a fresh process after one dies.
Everything it reports comes from the runner's execution history and the ledger, never from the job.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from meterline import billing, ledger
from meterline.bill import BILLED_EVENTS, lambda_bill, vm_bill
from meterline.config import DEFAULT_MODEL, PERIOD
from meterline.runtime import LocalCloud

DONE = {"SUCCEEDED", "FAILED", "TIMED_OUT", "STOPPED"}


def fresh_out(out: Path) -> None:
    """Each night starts from empty books, so every count in the report belongs to this run alone."""
    for name in ("durable.db", "meterline.db", "run.json", "report.json", "runner.log", "host.log"):
        for leftover in out.glob(name + "*"):
            leftover.unlink()
    out.mkdir(parents=True, exist_ok=True)


def waiting_for_review(out: Path) -> set[str]:
    return {row["customer_id"] for row in ledger.rows(out / "meterline.db", "select customer_id from review_queue where status = 'waiting'")}


def run_night(cloud: LocalCloud, crash_after_writes: int, deadline_seconds: float = 600) -> dict:
    """Start every job, let the host crash after N ledger writes, bring up a new host, wait for outcomes."""
    out = cloud.out
    fresh_out(out)
    cloud.start_runner()
    cloud.start_host(crash_after_writes)
    run = {"run_name": f"night-{int(time.time())}", "period": PERIOD, "started_at": time.time(),
           "crash_after_writes": crash_after_writes, "model": os.environ.get("LLM_MODEL") or DEFAULT_MODEL, "crash": None}
    run["executions"] = {customer_id: cloud.start_execution(customer_id, run["run_name"]) for customer_id in billing.tonights_jobs()}
    (out / "run.json").write_text(json.dumps(run, indent=1))
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        if cloud.host_died() and run["crash"] is None:
            # The host is gone mid-run. Lambda would place the next invoke on a fresh environment; so do we.
            # The kill lands straight after a ledger commit, so that commit's timestamp is the moment of death.
            killed = cloud.host.pid
            last_write = ledger.rows(out / "meterline.db", "select max(attempted_at) as at from write_attempts where process_id = ?", (killed,))
            run["crash"] = {"process_id": killed, "exit_code": cloud.host.returncode,
                            "at": (last_write[0]["at"] if last_write else None) or time.time()}
            run["crash"]["new_process_id"] = cloud.start_host()
            run["crash"]["restarted_at"] = time.time()
            (out / "run.json").write_text(json.dumps(run, indent=1))
        held = waiting_for_review(out)
        statuses = {customer_id: cloud.status(arn)["Status"] for customer_id, arn in run["executions"].items()}
        if all(status in DONE or customer_id in held for customer_id, status in statuses.items()):
            break
        time.sleep(0.5)
    run["finished_at"] = time.time()
    (out / "run.json").write_text(json.dumps(run, indent=1))
    report = build_report(cloud)
    (out / "report.json").write_text(json.dumps(report, indent=1))
    return report


def epoch(timestamp) -> float:
    return timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)


def lane_segments(events: list[dict], crash: dict | None) -> tuple[list[dict], int, int]:
    """Pair each start with its end. A start with no end before the next start of the same step is the
    work the crash cut off: it is drawn up to the crash and marked interrupted.
    """
    segments, open_starts, operations, bytes_written = [], {}, 0, 0
    for event in events:
        kind, when = event["EventType"], epoch(event["EventTimestamp"])
        operations += kind in BILLED_EVENTS
        details = next((value for key, value in event.items() if key.endswith("Details") and isinstance(value, dict)), {})
        payload = (details.get("Result") or {}).get("Payload")
        bytes_written += len(payload) if isinstance(payload, str) else 0
        name = event.get("Name", "")
        if kind in ("StepStarted", "CallbackStarted"):
            if event["Id"] in open_starts:
                cut = open_starts.pop(event["Id"])
                segments.append({**cut, "end": crash["at"] if crash else when, "state": "interrupted"})
            label = "finance_review" if kind == "CallbackStarted" else name
            open_starts[event["Id"]] = {"step": label, "start": when}
        elif kind in ("StepSucceeded", "StepFailed", "CallbackSucceeded", "CallbackFailed", "CallbackTimedOut"):
            started = open_starts.pop(event["Id"], {"step": name, "start": when})
            state = "done" if kind.endswith("Succeeded") else "failed"
            segments.append({**started, "end": when, "state": state})
    for started in open_starts.values():
        segments.append({**started, "end": None, "state": "waiting" if started["step"] == "finance_review" else "running"})
    return [segment for segment in segments if not segment["step"].startswith("finance_review ")], operations, bytes_written


def cut_off_work(state: Path, crash: dict | None, customer_id: str) -> list[dict]:
    """Work the killed process began but never checkpointed. The SDK records a step's start and end
    together when it finishes, so the history never saw it; the call log and the write log did. The proof
    that a checkpoint did not land is that a later process did the same work again.
    """
    if not crash:
        return []
    killed, cut = crash["process_id"], []
    for table, step, column in (("model_calls", "read_terms", "started_at"), ("write_attempts", "write_ledger", "attempted_at")):
        mine = ledger.rows(state, f"select {column} as at, process_id from {table} where customer_id = ? order by {column}", (customer_id,))
        for position, attempt in enumerate(mine):
            redone = [later["at"] for later in mine[position + 1:] if later["process_id"] != killed]
            if attempt["process_id"] == killed and redone:
                cut.append({"step": step, "start": attempt["at"], "end": crash["at"], "state": "interrupted", "redone_at": redone[0]})
    return cut


def start_redone_steps_late(segments: list[dict], cut: list[dict]) -> list[dict]:
    """The runner stamps a redone step with its first start, in the killed process. Draw the finished
    step from when the new process began it, so the cut-off part shows once, as cut off."""
    for segment in segments:
        for cut_segment in cut:
            if segment["step"] == cut_segment["step"] and segment["state"] == "done":
                segment["start"] = max(segment["start"], cut_segment["redone_at"])
    return segments + [{key: value for key, value in cut_segment.items() if key != "redone_at"} for cut_segment in cut]


def outcome_of(status: dict, held: dict) -> tuple[str, str]:
    if status["Status"] == "SUCCEEDED":
        result = json.loads(status.get("Result") or "{}")
        reason = "; ".join(result.get("reasons", [])) or (f"Approved by {result['reviewer']}" if result.get("reviewer") else "")
        return result.get("outcome", "done"), reason
    if status["Status"] in DONE:
        return "stopped", (status.get("Error") or {}).get("ErrorMessage", status["Status"])
    if held:
        return "held", held["reasons"]
    return "running", ""


def build_report(cloud: LocalCloud) -> dict:
    """The run console's data and the proof's numbers, read from the runner's history and the ledger."""
    out = cloud.out
    run = json.loads((out / "run.json").read_text())
    state = out / "meterline.db"
    crash, started_at = run.get("crash"), run["started_at"]
    reviews = {row["customer_id"]: row for row in ledger.rows(state, "select * from review_queue")}
    rows = ledger.rows(state, "select * from ledger order by row_id")
    calls = ledger.rows(state, "select * from model_calls order by started_at")
    attempts = ledger.rows(state, "select * from write_attempts order by attempted_at")
    lanes, operations, bytes_written, recalled, redone_calls = [], 0, 0, [], 0
    for customer_id, arn in run["executions"].items():
        status = cloud.status(arn)
        segments, lane_operations, lane_bytes = lane_segments(cloud.history(arn), crash)
        operations, bytes_written = operations + lane_operations, bytes_written + lane_bytes
        review = reviews.get(customer_id)
        outcome, reason = outcome_of(status, review if review and review["status"] == "waiting" else None)
        segments = start_redone_steps_late(segments, cut_off_work(state, crash, customer_id))
        # A completed model step is one whose checkpoint landed; any call that starts after it was paid twice.
        checkpointed = [segment["end"] for segment in segments if segment["step"] == "read_terms" and segment["state"] == "done"]
        recalled += [call for call in calls if call["customer_id"] == customer_id and checkpointed and call["started_at"] > checkpointed[0]]
        redone_calls += sum(segment["step"] == "read_terms" and segment["state"] == "interrupted" for segment in segments)
        invocations = [{"start": round(invocation["started_at"] - started_at, 3),
                        "end": round((invocation["finished_at"] or (crash or {}).get("at") or time.time()) - started_at, 3),
                        "killed": bool(crash) and invocation["process_id"] == crash["process_id"] and not invocation["finished_at"]}
                       for invocation in ledger.rows(state, "select * from invocations where execution = ?", (arn,))]
        relative = [{**segment, "start": round(segment["start"] - started_at, 3),
                     "end": None if segment["end"] is None else round(segment["end"] - started_at, 3)} for segment in segments]
        lanes.append({"customer_id": customer_id, "name": billing.customers()[customer_id]["name"], "outcome": outcome,
                      "reason": reason, "segments": sorted(relative, key=lambda segment: segment["start"]),
                      "invocations": invocations, "resumed": any(invocation["killed"] for invocation in invocations), "review": review,
                      "ledger": [row for row in rows if row["customer_id"] == customer_id],
                      "draft_total": billing.drafts()[customer_id]["total"], "invoice": billing.drafts()[customer_id]["invoice"]})
    finished_calls = [call for call in calls if call["finished_at"]]
    per_key: dict[tuple, int] = {}
    for row in rows:
        per_key[(row["customer_id"], row["period"])] = per_key.get((row["customer_id"], row["period"]), 0) + 1
    end_of = {crash["process_id"]: crash["at"]} if crash else {}
    seconds = [(call_end or end_of.get(process_id) or time.time()) - begin for begin, call_end, process_id in
               ((invocation["started_at"], invocation["finished_at"], invocation["process_id"])
                for invocation in ledger.rows(state, "select * from invocations"))]
    model_cost = sum(call["cost"] or 0 for call in finished_calls)
    cloud_bill = lambda_bill(seconds, operations, bytes_written)
    reached = [lane for lane in lanes if lane["outcome"] != "running"]
    return {
        "run": {key: run.get(key) for key in ("run_name", "period", "model", "crash_after_writes")},
        "duration": round(run.get("finished_at", time.time()) - started_at, 1),
        "crash": None if not crash else {"process_id": crash["process_id"], "new_process_id": crash["new_process_id"],
                                         "exit_code": crash["exit_code"],
                                         "at": round(crash["at"] - started_at, 3), "restarted_at": round(crash["restarted_at"] - started_at, 3)},
        "lanes": lanes,
        "metrics": {
            "jobs": len(lanes), "reached_outcome": len(reached),
            "outcomes": {name: sum(lane["outcome"] == name for lane in lanes) for name in
                         ("matched", "corrected", "approved", "held", "rejected", "stopped", "running")},
            "ledger_rows": len(rows), "duplicate_writes": sum(count - 1 for count in per_key.values()),
            "repeat_writes_absorbed": sum(1 for attempt in attempts if not attempt["inserted"]),
            "model_calls": len(calls), "model_calls_completed": len(finished_calls),
            "model_calls_cut_off_and_redone": redone_calls,
            "completed_model_steps_recalled": len(recalled),
            "held_written": sorted({lane["customer_id"] for lane in lanes if lane["outcome"] == "held" and lane["ledger"]}),
        },
        "bill": {"model": model_cost, "lambda": cloud_bill, "per_run": model_cost + cloud_bill["total"], "vm": vm_bill()},
    }
