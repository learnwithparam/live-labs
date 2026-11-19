"""The slice on the scripted model: what it dispatches, what goes to a person, and what the naive version loses."""

import time

import pytest

from brickhaven_slice.config import COMPLETION_THRESHOLD, P95_TARGET_MS
from brickhaven_slice.pipeline import NeedsPerson, Request, Services
from brickhaven_slice.trace_check import judge
from brickhaven_slice.vendors import load_messages


def outcomes(run) -> dict:
    return {result.request_id: result for result in run["results"]}


def test_the_slice_dispatches_what_it_can_and_queues_the_rest_with_a_reason(slice_run):
    by_id = outcomes(slice_run)
    queued = {request_id: result for request_id, result in by_id.items() if result.outcome == "human_queue"}
    assert sorted(queued) == ["m06", "m11", "m12"]
    assert queued["m06"].failed_step == "extraction" and "failed 3 times" in queued["m06"].reason
    assert queued["m11"].reason == "no flat named in the message"
    assert queued["m12"].reason == "no vendor covers pest"
    assert sum(result.outcome == "sent" for result in by_id.values()) / len(by_id) >= COMPLETION_THRESHOLD


def test_retry_local_repair_and_model_repair_each_save_a_request(slice_run):
    by_id = outcomes(slice_run)
    assert all(by_id[request_id].outcome == "sent" for request_id in ("m01", "m02", "m04", "m08", "m15"))
    names = [record.name for record in slice_run["records"] if record.attributes.get("brickhaven.request_id") == "m04"]
    assert "repair work_order" in names and names.count("chat extraction") == 1 and "chat repair" in names


def test_every_slice_request_has_a_complete_span_tree(slice_run):
    traces = judge(slice_run["records"], [message["id"] for message in load_messages()])
    assert [trace.problems for trace in traces if not trace.complete] == []
    assert {trace.outcome for trace in traces} == {"sent", "human_queue"}


def test_a_failed_attempt_is_a_span_with_an_error(slice_run):
    chats = [record for record in slice_run["records"] if record.attributes.get("brickhaven.request_id") == "m06"
             and record.name == "chat extraction"]
    assert len(chats) == 3 and all(chat.error for chat in chats)


def test_tokens_on_the_request_are_the_sum_over_its_model_calls(slice_run):
    roots = [record for record in slice_run["records"] if record.name == "request"]
    assert len(roots) == 16 and sum(root.attributes["gen_ai.usage.input_tokens"] for root in roots) > 0
    m01 = next(root for root in roots if root.attributes["brickhaven.request_id"] == "m01")
    assert m01.attributes["brickhaven.model_calls"] == 2  # the timed-out attempt reported no usage, the retry did, then drafting


def test_the_naive_version_crashes_on_every_slip_and_leaves_gaps(naive_run):
    by_id = outcomes(naive_run)
    crashed = sorted(request_id for request_id, result in by_id.items() if result.outcome == "crashed")
    assert crashed == ["m01", "m02", "m04", "m06", "m08", "m11", "m12", "m15"]
    assert (16 - len(crashed)) / 16 < COMPLETION_THRESHOLD
    traces = judge(naive_run["records"], [message["id"] for message in load_messages()])
    assert not any(trace.complete for trace in traces)
    assert not any(record.name.startswith("step ") for record in naive_run["records"])


def test_a_request_with_no_time_left_goes_to_a_person_instead_of_starting_a_call(slice_run):
    services = Services(None, None, None, {}, [])
    request = Request(services, {"id": "m99", "text": "x"}, "test")
    request.started = time.perf_counter() - (P95_TARGET_MS - 100) / 1000
    with pytest.raises(NeedsPerson, match="out of time"):
        request.attempt_timeout_ms("extraction")


def test_later_steps_keep_their_budget_when_an_early_step_retries():
    request = Request(Services(None, None, None, {}, []), {"id": "m99", "text": "x"}, "test")
    assert 3400 < request.attempt_timeout_ms("extraction") <= 3500
    request.started = time.perf_counter() - 6
    assert request.attempt_timeout_ms("drafting") <= 2000
