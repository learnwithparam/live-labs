"""Ten scripted calls through the real stack, each judged against the booking system it should change.

The review starts the agent worker on this machine and rings it through LiveKit Cloud. Each
call gets its own room and its own copy of the week's schedule. The verdict reads only that schedule
and the handoff queue, never the agent's own account of how the call went."""

import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from livekit import api

from northfield.caller import place_call, scripts
from northfield.config import AGENT_NAME, OUT, livekit_settings, llm_settings

RESULT_WAIT = 30  # seconds for the agent to write its result after the caller hangs up
DISPATCH_TRIES, DISPATCH_WAIT = 6, 10  # a busy worker gets six chances, up to ten seconds each


def wait_for(check, seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise SystemExit(f"Timed out waiting for {what}.")
        time.sleep(0.2)


@contextmanager
def phone_stack(out: Path):
    """The agent worker on this laptop, registered with LiveKit Cloud and stopped on exit. The rooms, the
    speech models and the turn detector all run in LiveKit Cloud, so nothing else starts here."""
    out.mkdir(parents=True, exist_ok=True)
    agent_log_path = out / "agent.log"
    worker = subprocess.Popen([sys.executable, "-m", "northfield.cli", "agent"],
                              stdout=agent_log_path.open("w"), stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: "agent registered" in agent_log_path.read_text() or worker.poll() is not None,
                 60, "the agent worker")
        if worker.poll() is not None:
            raise SystemExit(f"The agent worker stopped. See {agent_log_path}.")
        yield lambda: worker.poll() is None
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()


def judge(expect: dict, result: dict) -> tuple[bool, str]:
    """Did the book end up the way this caller needed? Returns the verdict and the reason for it."""
    changed = result.get("changes", {"visits": [], "customers": [], "handoffs": []})
    visits, handoffs, customers = changed["visits"], changed["handoffs"], changed["customers"]
    outcome = expect["outcome"]
    if outcome == "transferred":
        reasons = [handoff["reason"] for handoff in handoffs]
        if visits:
            return False, f"transferred but also changed {visits[0]['id']}"
        return reasons == [expect["reason"]], f"handoffs {reasons or 'none'}, expected {expect['reason']}"
    if handoffs:
        return False, f"sent to a person ({handoffs[0]['reason']}) when the agent could resolve it"
    if outcome in ("answered", "declined"):
        if visits or customers:
            return False, f"changed the book: {[visit['id'] for visit in visits]}"
        if outcome == "declined":
            return True, "declined with no booking"
        said = " ".join(turn["text"] for turn in result.get("turns", []) if turn["who"] == "agent").lower()
        found = [phrase for phrase in expect["says"] if phrase in said]
        return bool(found), f"agent said {found[0]!r}" if found else f"agent never said any of {expect['says']}"
    if len(visits) != 1:
        return False, f"{len(visits)} visits changed, expected 1"
    visit = visits[0]
    if outcome == "cancelled":
        return visit["id"] == expect["visit"] and visit["status"] == "cancelled", f"{visit['id']} is {visit['status']}"
    if outcome == "moved":
        fine = visit["id"] == expect["visit"] and visit["window"] == expect["window"] and visit["status"] == "booked"
        return fine, f"{visit['id']} now in {visit['window']}"
    fine = visit["kind"] == expect["kind"] and visit["window"] == expect["window"] and visit["status"] == "booked"
    if "new_customer_zip" in expect:
        fine = fine and [person["zip"] for person in customers] == [expect["new_customer_zip"]]
    return fine, f"{visit['kind']} in {visit['window']}"


async def dispatch_until_answered(livekit: api.LiveKitAPI, room_name: str, metadata: str) -> None:
    """Send the call to the agent, and send it again if no agent joins: a worker still hanging up the last
    call reports itself busy, and LiveKit drops a dispatch that finds no free worker instead of queueing it."""
    for _ in range(DISPATCH_TRIES):
        await livekit.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME, room=room_name, metadata=metadata))
        # A dispatch a worker took carries a job; one that found no free worker stays empty. Checking twice
        # a second lets the call ring in as soon as the agent has it.
        for _ in range(DISPATCH_WAIT * 2):
            await asyncio.sleep(0.5)
            if any(dispatch.state.jobs for dispatch in await livekit.agent_dispatch.list_dispatch(room_name)):
                return


async def one_call(script: dict, folder: Path) -> dict:
    """Dispatch the agent to a fresh room, ring in as the caller, then read what the agent wrote down."""
    shutil.rmtree(folder, ignore_errors=True)
    room_name = f"call-{script['id']}-{uuid.uuid4().hex[:6]}"
    url, key, secret = livekit_settings()
    http_url = url.replace("ws", "http", 1)
    metadata = json.dumps({"call": script["id"], "folder": str(folder.resolve())})
    async with api.LiveKitAPI(http_url, key, secret) as livekit:
        answering = asyncio.ensure_future(dispatch_until_answered(livekit, room_name, metadata))
        observed = await place_call(script, room_name)
        answering.cancel()
    deadline = time.monotonic() + RESULT_WAIT
    while not (folder / "result.json").exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.3)
    result = json.loads((folder / "result.json").read_text()) if (folder / "result.json").exists() else {}
    reached, why = judge(script["expect"], result) if result else (False, "the agent never wrote a result")
    handoff = (result.get("changes", {}).get("handoffs") or [None])[0]
    return {"id": script["id"], "caller": script["caller"], "number": script["number"],
            "situation": script["situation"], "expect": script["expect"], "reached": reached, "why": why,
            "transfer": handoff, "resolved_without_person": reached and handoff is None,
            "turns": result.get("turns", []), "tools": result.get("tools", []), "cost": result.get("cost", 0.0),
            "tokens": result.get("tokens", {}), **observed}


def reply_waits(calls: list[dict]) -> list[float]:
    """Seconds from the caller's last word to the agent's first sound, for every reply LiveKit timed. The
    greeting and a reply that follows a tool call's own reply have no caller turn to time from."""
    return [turn["e2e_latency"] for call in calls for turn in call["turns"] if turn.get("e2e_latency") is not None]


def score(calls: list[dict]) -> dict:
    reached = sum(call["reached"] for call in calls)
    waits = sorted(reply_waits(calls))
    return {"calls": len(calls), "reached": reached, "share": round(reached / len(calls), 2) if calls else 0,
            "resolved_without_person": sum(call["resolved_without_person"] for call in calls),
            "transfers": [{"id": call["id"], "reason": call["transfer"]["reason"]} for call in calls if call["transfer"]],
            "talked_over": sum(call["talked_over"] for call in calls),
            "cost": round(sum(call["cost"] for call in calls), 6),
            "seconds": round(sum(call["seconds"] for call in calls), 1),
            # The median reply is how the line feels; callers hang up on the slow ones, so p95 is kept too.
            "timed_replies": len(waits),
            "reply_p50": round(statistics.median(waits), 2) if waits else None,
            "reply_p95": round(waits[max(0, -(-95 * len(waits) // 100) - 1)], 2) if waits else None}


def review_calls(only: list[str] | None = None, out: Path = OUT, on_call=None) -> dict:
    """Ring the scripted calls one after another, and write out/review.json when all ten ran."""
    _, _, model = llm_settings()  # fail on a missing key before starting any process
    livekit_settings()
    chosen = [script for call_id, script in scripts().items() if not only or call_id in only]

    async def all_calls(worker_alive) -> list[dict]:
        # One line, one call at a time. Two calls joining the same thread-based worker at once crashed
        # LiveKit's native layer on a busy laptop; production runs a worker process per call instead.
        calls = []
        for script in chosen:
            if not worker_alive():
                raise SystemExit(f"The agent worker stopped during the review. See {out / 'agent.log'}.")
            if on_call:
                on_call(script["id"])
            calls.append(await one_call(script, out / "calls" / script["id"]))
        return calls

    with phone_stack(out) as worker_alive:
        calls = asyncio.run(all_calls(worker_alive))
    report = {"model": model, "calls": calls, "score": score(calls)}
    if not only:
        (out / "review.json").write_text(json.dumps(report, indent=1))
    return report
