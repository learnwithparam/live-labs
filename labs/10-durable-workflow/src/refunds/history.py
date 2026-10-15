"""Read a refund from its Temporal event history: the record the server keeps of every step.

The history is the source of truth for a workflow. A worker replays it to carry on after a crash, and
anyone can read it while no worker runs at all. A query needs a live worker to answer, so a desk or a
supervisor that must work through a crash reads the history instead.
"""

from temporalio.converter import default


def timeline(history) -> list[dict]:
    """Temporal's event history, cut to the events a person reads: what started, finished, failed or arrived."""
    names, attempts, out = {}, {}, []
    for e in history.events:
        kind = e.WhichOneof("attributes")
        at = e.event_time.ToDatetime().isoformat() + "Z"
        if kind == "activity_task_scheduled_event_attributes":
            names[e.event_id] = e.activity_task_scheduled_event_attributes.activity_type.name
            out.append({"at": at, "event": "scheduled", "step": names[e.event_id]})
        elif kind == "activity_task_started_event_attributes":
            # Retries are not separate events: the started event says which attempt finally ran.
            attempts[e.activity_task_started_event_attributes.scheduled_event_id] = e.activity_task_started_event_attributes.attempt
        elif kind == "activity_task_completed_event_attributes":
            scheduled = e.activity_task_completed_event_attributes.scheduled_event_id
            out.append({"at": at, "event": "completed", "step": names.get(scheduled), "attempt": attempts.get(scheduled, 1)})
        elif kind == "activity_task_timed_out_event_attributes":
            out.append({"at": at, "event": "timed out", "step": names.get(e.activity_task_timed_out_event_attributes.scheduled_event_id)})
        elif kind == "activity_task_failed_event_attributes":
            out.append({"at": at, "event": "failed", "step": names.get(e.activity_task_failed_event_attributes.scheduled_event_id)})
        elif kind == "workflow_execution_signaled_event_attributes":
            out.append({"at": at, "event": "signal", "step": e.workflow_execution_signaled_event_attributes.signal_name})
        elif kind == "timer_started_event_attributes":
            out.append({"at": at, "event": "timer", "step": "approval deadline"})
        elif kind == "workflow_execution_started_event_attributes":
            out.append({"at": at, "event": "started", "step": "workflow"})
        elif kind == "workflow_execution_completed_event_attributes":
            out.append({"at": at, "event": "completed", "step": "workflow"})
    return out


def decoded(payloads) -> object:
    return default().payload_converter.from_payloads(payloads.payloads)[0] if payloads.payloads else None


def status(history) -> dict:
    """Where a refund stands: its stage, decision, payout and the manager's verdict, from the history alone."""
    stage, decision, payout, verdict, waiting_since, steps = "deciding", None, None, None, None, {}
    for e in history.events:
        kind = e.WhichOneof("attributes")
        if kind == "activity_task_scheduled_event_attributes":
            steps[e.event_id] = e.activity_task_scheduled_event_attributes.activity_type.name
            if steps[e.event_id] == "pay":
                stage = "paying"
        elif kind == "activity_task_completed_event_attributes":
            attrs = e.activity_task_completed_event_attributes
            if steps.get(attrs.scheduled_event_id) == "decide":
                decision = decoded(attrs.result)
                stage = "waiting" if decision["needs_approval"] else "deciding"
                waiting_since = e.event_time.ToDatetime().isoformat() + "Z" if decision["needs_approval"] else None
            else:
                payout, stage = decoded(attrs.result), "paid"
        elif kind == "timer_fired_event_attributes" and verdict is None:
            stage = "escalated"
        elif kind == "workflow_execution_signaled_event_attributes":
            verdict = "approved" if e.workflow_execution_signaled_event_attributes.signal_name == "approve" else "rejected"
        elif kind == "workflow_execution_completed_event_attributes":
            stage = decoded(e.workflow_execution_completed_event_attributes.result)["stage"]
    return {"stage": stage, "decision": decision, "payout": payout, "verdict": verdict, "waiting_since": waiting_since,
            "done": history.events[-1].WhichOneof("attributes") == "workflow_execution_completed_event_attributes"}
