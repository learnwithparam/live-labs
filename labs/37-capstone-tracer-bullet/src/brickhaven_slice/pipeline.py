"""The vertical slice, twice: the naive version and the one you would ship.

Both turn a tenant message into a dispatched work order: intake, extraction, vendor match, drafting,
sending. `run_naive` has no error handling and spans only on the model calls, the way a first
prototype is written. `run_slice` gives every step and tool a span under one `request` span, retries
transient errors inside a deadline, repairs broken structured output, and hands what it cannot finish
to a human queue instead of dropping it.
"""

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry import trace

from brickhaven_slice.config import (MAX_ATTEMPTS, P95_TARGET_MS, RETRY_PAUSE_MS, STEP_BUDGET_MS, STEPS)
from brickhaven_slice.llm import TRANSIENT, Model, call_model
from brickhaven_slice.prompts import DRAFT_SYSTEM, EXTRACT_SYSTEM, REPAIR_SYSTEM
from brickhaven_slice.repair import parse_work_order, strip_to_object
from brickhaven_slice.telemetry import Ledger
from brickhaven_slice.vendors import match_vendor

# A model call with less than this left cannot finish, so the request goes to a person instead of starting one.
MIN_ATTEMPT_MS = 300
MAX_MESSAGE_CHARS = 1000


class NeedsPerson(Exception):
    """A step could not finish. The request goes to the human queue with this step and this reason."""

    def __init__(self, step: str, reason: str):
        super().__init__(reason)
        self.step, self.reason = step, reason


@dataclass
class Outbox:
    """Where dispatches and human-queue items are written: two JSON-lines files, one per run."""

    folder: Path
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, name: str, record: dict) -> None:
        with self.lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / name).open("a") as handle:
                handle.write(json.dumps(record) + "\n")


@dataclass
class Services:
    """Everything a request needs from outside: the model, the tracer and the systems of record."""

    model: Model
    tracer: object
    outbox: Outbox
    units: dict
    vendors: list
    sleep: object = time.sleep


@dataclass
class Result:
    """What the app itself believes happened. The proof does not trust it: it reads Jaeger."""

    request_id: str
    outcome: str  # "sent", "human_queue" or "crashed"
    failed_step: str = ""
    reason: str = ""


def tags_for(run_id: str, request_id: str, mode: str) -> dict:
    """The three attributes that let the proof find one request's spans in Jaeger."""
    return {"brickhaven.run_id": run_id, "brickhaven.request_id": request_id, "brickhaven.mode": mode}


def draft_prompt(message: dict, order: dict, vendor: dict) -> str:
    return (f"Tenant message: {message['text']}\nWork order: {json.dumps(order)}\nVendor: {vendor['name']}")


def run_naive(services: Services, message: dict, run_id: str) -> Result:
    """No error handling, spans on the two model calls only: what a first prototype looks like.

    Any timeout, any fenced or broken JSON, any message with no flat or no vendor raises, and the
    request vanishes. The model spans come out as separate one-span traces, so no trace shows the request.
    """
    tags = tags_for(run_id, message["id"], "naive")
    timeout_ms = STEP_BUDGET_MS["extraction"]
    text = call_model(services.model, services.tracer, "chat extraction", EXTRACT_SYSTEM, message["text"], timeout_ms, tags)
    order = json.loads(text)
    unit = services.units[order["unit"]]
    vendor, _ = match_vendor(order, unit["building"], services.vendors)
    draft = call_model(services.model, services.tracer, "chat drafting", DRAFT_SYSTEM,
                       draft_prompt(message, order, vendor), STEP_BUDGET_MS["drafting"], tags)
    services.outbox.append("dispatches-naive.jsonl", {"request_id": message["id"], "vendor": vendor["id"], "draft": draft})
    return Result(message["id"], "sent")


class Request:
    """One request's clock, ledger and tags, so each step asks it how much time is left."""

    def __init__(self, services: Services, message: dict, run_id: str):
        self.services, self.message = services, message
        self.tags = tags_for(run_id, message["id"], "slice")
        self.ledger = Ledger()
        self.started = time.perf_counter()

    def remaining_ms(self) -> float:
        return P95_TARGET_MS - (time.perf_counter() - self.started) * 1000

    def attempt_timeout_ms(self, step: str) -> int:
        """The step's own budget, shrunk so the steps after it still have theirs inside the deadline."""
        later = sum(STEP_BUDGET_MS[name] for name in STEPS[STEPS.index(step) + 1:])
        allowed = min(STEP_BUDGET_MS[step], self.remaining_ms() - later)
        if allowed < MIN_ATTEMPT_MS:
            raise NeedsPerson(step, f"out of time: {max(self.remaining_ms(), 0):.0f} ms left of the {P95_TARGET_MS} ms budget")
        return int(allowed)

    @contextmanager
    def step(self, name: str):
        """A `step <name>` span. A failure inside is recorded on it, then becomes a hand-off to a person."""
        with self.services.tracer.start_as_current_span(f"step {name}", attributes={
                **self.tags, "brickhaven.step": name, "brickhaven.budget_ms": STEP_BUDGET_MS.get(name, 0)}):
            try:
                yield
            except NeedsPerson:
                raise
            except Exception as error:  # any bug or outage in a step must end in the queue, not in a lost request
                raise NeedsPerson(name, f"{type(error).__name__}: {error}") from error

    @contextmanager
    def tool(self, name: str):
        with self.services.tracer.start_as_current_span(f"tool {name}", attributes={
                **self.tags, "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name}) as span:
            yield span

    def ask(self, step: str, span_name: str, system: str, user: str) -> str:
        """One model call with retries on transient errors; every attempt is its own `chat` span."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            timeout_ms = self.attempt_timeout_ms(step)
            try:
                return call_model(self.services.model, self.services.tracer, span_name, system, user, timeout_ms,
                                  {**self.tags, "brickhaven.attempt": attempt}, self.ledger)
            except TRANSIENT as error:
                last_error = error
                trace.get_current_span().add_event("retry", {"attempt": attempt, "error": type(error).__name__})
                self.services.sleep(RETRY_PAUSE_MS / 1000)
        raise NeedsPerson(step, f"model failed {MAX_ATTEMPTS} times: {type(last_error).__name__}")

    def intake(self) -> str:
        with self.step("intake"):
            text = " ".join(self.message["text"].split())
            if not text:
                raise NeedsPerson("intake", "the message is empty")
            with self.tool("read_message") as span:
                span.set_attribute("brickhaven.message_chars", len(text))
            return text[:MAX_MESSAGE_CHARS]

    def extraction(self, text: str) -> dict:
        with self.step("extraction"):
            raw = self.ask("extraction", "chat extraction", EXTRACT_SYSTEM, text)
            order, problems = parse_work_order(raw)
            if order is not None:
                trace.get_current_span().set_attribute("brickhaven.repaired_locally", strip_to_object(raw) != raw.strip())
                return order
            # The model's own text is the input: asking it to fix its answer costs less than asking again.
            with self.services.tracer.start_as_current_span("repair work_order", attributes={
                    **self.tags, "brickhaven.problems": "; ".join(problems)}):
                fixed = self.ask("extraction", "chat repair", REPAIR_SYSTEM, f"Problems: {'; '.join(problems)}\nText: {raw}")
                order, problems = parse_work_order(fixed)
            if order is None:
                raise NeedsPerson("extraction", "the answer stayed invalid after repair: " + "; ".join(problems))
            return order

    def vendor_match(self, order: dict) -> tuple[dict, dict]:
        with self.step("vendor_match"):
            with self.tool("lookup_unit") as span:
                unit = self.services.units.get(order["unit"] or "")
                span.set_attribute("brickhaven.unit_found", unit is not None)
            if unit is None:
                raise NeedsPerson("vendor_match", "no flat named in the message" if not order["unit"]
                                  else f"flat {order['unit']} is not one of ours")
            with self.tool("match_vendor") as span:
                vendor, reason = match_vendor(order, unit["building"], self.services.vendors)
                span.set_attribute("brickhaven.vendor", vendor["id"] if vendor else "none")
            if vendor is None:
                raise NeedsPerson("vendor_match", reason)
            return unit, vendor

    def drafting(self, order: dict, vendor: dict) -> str:
        with self.step("drafting"):
            draft = self.ask("drafting", "chat drafting", DRAFT_SYSTEM, draft_prompt(self.message, order, vendor)).strip()
            if not draft:
                raise NeedsPerson("drafting", "the draft came back empty")
            return draft

    def sending(self, order: dict, vendor: dict, draft: str) -> None:
        with self.step("sending"):
            with self.tool("write_outbox"):
                self.services.outbox.append("dispatches-slice.jsonl", {
                    "request_id": self.message["id"], "work_order": order, "vendor": vendor["id"], "draft": draft})

    def hand_to_person(self, handoff: NeedsPerson) -> None:
        """The fallback: the request lands in the queue with the step that failed and why."""
        with self.step("human_queue"):
            with self.tool("write_human_queue"):
                self.services.outbox.append("human-queue-slice.jsonl", {
                    "request_id": self.message["id"], "failed_step": handoff.step, "reason": handoff.reason})


def run_slice(services: Services, message: dict, run_id: str) -> Result:
    """Every step under one `request` span; the span ends with the outcome and the token totals."""
    request = Request(services, message, run_id)
    with services.tracer.start_as_current_span("request", attributes=request.tags) as root:
        try:
            text = request.intake()
            order = request.extraction(text)
            _, vendor = request.vendor_match(order)
            draft = request.drafting(order, vendor)
            request.sending(order, vendor, draft)
            result = Result(message["id"], "sent")
        except NeedsPerson as handoff:
            request.hand_to_person(handoff)
            result = Result(message["id"], "human_queue", handoff.step, handoff.reason)
        ledger = request.ledger
        root.set_attributes({"brickhaven.outcome": result.outcome, "brickhaven.failed_step": result.failed_step,
                             "brickhaven.reason": result.reason, "gen_ai.usage.input_tokens": ledger.input_tokens,
                             "gen_ai.usage.output_tokens": ledger.output_tokens, "brickhaven.cost_dollars": ledger.cost_dollars,
                             "brickhaven.model_calls": ledger.calls})
    return result


def run_all(services: Services, messages: list[dict], run_id: str, mode: str, workers: int) -> list[Result]:
    """Run every message, `workers` at a time. A crash in the naive version is caught here only so the run can go on."""
    from concurrent.futures import ThreadPoolExecutor

    handler = run_naive if mode == "naive" else run_slice

    def run_one(message: dict) -> Result:
        try:
            return handler(services, message, run_id)
        except Exception as error:  # the naive version's whole error handling: the harness notes the crash
            return Result(message["id"], "crashed", reason=f"{type(error).__name__}: {error}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run_one, messages))
