"""A scripted provider and an in-memory span store, so the attribution logic runs with no key and no Jaeger.

The provider speaks OpenRouter's wire format through a real `openai.OpenAI` client, so the real
instrumentation makes the real `chat` spans and the real invoice hook reads the billed cost.
"""

import itertools
import json
import re
import threading
import time

import httpx
from openai import OpenAI
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from quillmark.billing import Invoice
from quillmark.telemetry import Tracing
from quillmark.workload import run_month

MODEL = "openai/gpt-6-luna"
ORDER_ID = re.compile(r"\b[A-Z]{2}-\d{4,5}\b")


class ScriptedProvider:
    """Answers chat completions like OpenRouter: a generation id, a tool call when one is required, a cost."""

    def __init__(self, delay: float = 0.02):
        self.delay = delay
        self.numbers = itertools.count(1)
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # A short wait keeps several customers' calls in flight together, as on a busy day.
        time.sleep(self.delay)
        with self.lock:
            number = next(self.numbers)
        message = {"role": "assistant", "content": f"Scripted answer {number}."}
        finish = "stop"
        if body.get("tool_choice") == "required":
            thread = " ".join(str(message_in["content"]) for message_in in body["messages"])
            found = ORDER_ID.search(thread)
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call-{number}", "type": "function",
                 "function": {"name": "order_lookup", "arguments": json.dumps({"order_id": found and found.group()})}}]}
            finish = "tool_calls"
        usage = {"prompt_tokens": 400, "completion_tokens": 60, "total_tokens": 460, "cost": 0.00007 + number * 1e-7}
        return httpx.Response(200, json={
            "id": f"gen-fake-{number}", "object": "chat.completion", "created": int(time.time()), "model": body["model"],
            "choices": [{"index": 0, "finish_reason": finish, "message": message}], "usage": usage})


class DeadCollector(InMemorySpanExporter):
    """A collector that is being redeployed: every export fails, as an unreachable OTLP endpoint does."""

    def export(self, spans) -> SpanExportResult:
        return SpanExportResult.FAILURE


def as_rows(finished) -> list[dict]:
    """The same flat shape `report.flatten` makes from Jaeger's OTLP JSON."""
    return [{"trace_id": format(span.context.trace_id, "032x"), "span_id": format(span.context.span_id, "016x"),
             "parent_id": span.parent and format(span.parent.span_id, "016x"), "name": span.name,
             "start_ns": span.start_time, "end_ns": span.end_time,
             "attributes": {key: (list(value) if isinstance(value, tuple) else value)
                            for key, value in (span.attributes or {}).items()}}
            for span in finished]


def run_fake_month() -> dict:
    """The whole month on the scripted provider. Order systems answer 20 times faster than in real life."""
    store = InMemorySpanExporter()
    tracing = Tracing(store, batch=False)
    invoice = Invoice()
    client = OpenAI(base_url="http://provider.test/api/v1", api_key="test-key", max_retries=0,
                    http_client=invoice.http_client(transport=httpx.MockTransport(ScriptedProvider())))
    result = run_month(client, MODEL, tracing, sleep=lambda seconds: time.sleep(seconds / 20),
                       down_exporter=DeadCollector())
    return {"result": result, "lines": invoice.lines, "spans": as_rows(store.get_finished_spans()),
            "events": result["events"], "tracing": tracing}
