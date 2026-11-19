"""Read a request's span tree back and judge it. The truth is the spans, never the app's own log.

A span record is the same whether it came from Jaeger's API or from the in-memory exporter in a test,
so one checker serves the proof and the keyless tests. A tree is complete when every step the outcome
promises is there, every span hangs from the `request` span, and the token totals add up.
"""

import datetime
import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from brickhaven_slice.config import SERVICE_NAME, STEPS

REQUIRED_CHILD = {"extraction": "chat", "vendor_match": "tool", "drafting": "chat", "sending": "tool", "human_queue": "tool"}


@dataclass(frozen=True)
class SpanRecord:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_us: int
    duration_us: int
    error: bool
    attributes: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass
class RequestTrace:
    """One request as the spans show it: the verdict on its tree, its latency and its waterfall."""

    request_id: str
    complete: bool
    problems: list[str]
    latency_ms: float | None
    outcome: str
    failed_step: str
    reason: str
    input_tokens: int
    output_tokens: int
    rows: list[dict]


def from_exporter(spans) -> list[SpanRecord]:
    """Records from the SDK's finished spans, the way the keyless tests read them."""
    records = []
    for span in spans:
        parent = span.parent.span_id if span.parent else None
        records.append(SpanRecord(format(span.context.trace_id, "032x"), format(span.context.span_id, "016x"),
                                  format(parent, "016x") if parent else None, span.name, span.start_time // 1000,
                                  (span.end_time - span.start_time) // 1000, span.status.status_code.name == "ERROR",
                                  dict(span.attributes)))
    return records


VALUE_KINDS = ("stringValue", "intValue", "doubleValue", "boolValue")


def plain_value(value: dict):
    """OTLP JSON wraps every attribute value in its type, and integers arrive as strings."""
    for kind in VALUE_KINDS:
        if kind in value:
            return int(value[kind]) if kind == "intValue" else value[kind]
    return None


def from_jaeger(payload: dict) -> list[SpanRecord]:
    """Records from Jaeger's v3 query answer, which is OTLP JSON: spans sit in resourceSpans, then scopeSpans."""
    records = []
    for resource_spans in payload.get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                attributes = {item["key"]: plain_value(item["value"]) for item in span.get("attributes", [])}
                started, ended = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                failed = span.get("status", {}).get("code") in (2, "STATUS_CODE_ERROR")
                records.append(SpanRecord(span["traceId"], span["spanId"], span.get("parentSpanId") or None, span["name"],
                                          started // 1000, (ended - started) // 1000, failed, attributes))
    return records


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def read_back(run_id: str, expected_requests: int, base_url: str, fetch=fetch_json, wait_seconds: float = 30) -> list[SpanRecord]:
    """Ask Jaeger for the service's recent spans, keep this run's, and ask again until every request has shown up.

    Export is batched, so the last spans of a run reach Jaeger a moment after the app exits.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    query = urllib.parse.urlencode({"query.service_name": SERVICE_NAME, "query.num_traces": 1500,
                                    "query.start_time_min": (now - datetime.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "query.start_time_max": (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    deadline = time.monotonic() + wait_seconds
    while True:
        everything = from_jaeger(fetch(f"{base_url}/api/v3/traces?{query}").get("result", {}))
        run_traces = {record.trace_id for record in everything if record.attributes.get("brickhaven.run_id") == run_id}
        records = [record for record in everything if record.trace_id in run_traces]
        seen = {record.attributes.get("brickhaven.request_id") for record in records}
        if len(seen - {None}) >= expected_requests or time.monotonic() > deadline:
            return records
        time.sleep(1)


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile: the smallest value that at least `share` of the values do not exceed."""
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def expected_steps(outcome: str, failed_step: str) -> list[str]:
    """The step spans a request of this outcome must have, in order."""
    if outcome == "sent":
        return list(STEPS)
    return [*STEPS[:STEPS.index(failed_step) + 1], "human_queue"] if failed_step in STEPS else []


def waterfall(spans: list[SpanRecord]) -> list[dict]:
    """Rows for the page: each span indented under its parent, with its start and length in milliseconds."""
    by_parent: dict[str | None, list[SpanRecord]] = {}
    ids = {span.span_id for span in spans}
    for span in sorted(spans, key=lambda item: item.start_us):
        by_parent.setdefault(span.parent_id if span.parent_id in ids else None, []).append(span)
    origin = min((span.start_us for span in spans), default=0)
    rows: list[dict] = []

    def walk(parent: str | None, depth: int) -> None:
        for span in by_parent.get(parent, []):
            rows.append({"name": span.name, "depth": depth, "start_ms": round((span.start_us - origin) / 1000, 1),
                         "duration_ms": round(span.duration_us / 1000, 1), "error": span.error})
            walk(span.span_id, depth + 1)

    walk(None, 0)
    return rows


def tree_problems(spans: list[SpanRecord]) -> list[str]:
    """Every way this request's spans fall short of a full tree; an empty list means complete."""
    if len({span.trace_id for span in spans}) != 1:
        return [f"the spans are spread over {len({span.trace_id for span in spans})} traces, none shows the request"]
    roots = [span for span in spans if span.parent_id is None]
    if len(roots) != 1 or roots[0].name != "request":
        return ["no single `request` span at the top"]
    root, ids = roots[0], {span.span_id for span in spans}
    problems = [f"{span.name!r} has a parent that was never exported" for span in spans
                if span.parent_id is not None and span.parent_id not in ids]
    children = {}
    for span in spans:
        children.setdefault(span.parent_id, []).append(span)
    steps = sorted(children.get(root.span_id, []), key=lambda span: span.start_us)
    names = [span.name.removeprefix("step ") for span in steps]
    outcome, failed_step = root.attributes.get("brickhaven.outcome", ""), root.attributes.get("brickhaven.failed_step", "")
    if names != expected_steps(outcome, failed_step):
        problems.append(f"steps {names} do not match the {outcome or 'unknown'} outcome")
    for step in steps:
        name = step.name.removeprefix("step ")
        under = [span.name.split(" ")[0] for span in descendants(step, children)]
        if name in REQUIRED_CHILD and not step.error and REQUIRED_CHILD[name] not in under:
            problems.append(f"step {name} has no {REQUIRED_CHILD[name]} span under it")
    chats = [span for span in spans if span.name.startswith("chat ")]
    for key in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"):
        if root.attributes.get(key, 0) != sum(span.attributes.get(key, 0) for span in chats):
            problems.append(f"{key} on the request is not the sum over its model calls")
    return problems


def descendants(span: SpanRecord, children: dict) -> list[SpanRecord]:
    found = []
    for child in children.get(span.span_id, []):
        found += [child, *descendants(child, children)]
    return found


def judge(records: list[SpanRecord], request_ids: list[str]) -> list[RequestTrace]:
    """One verdict per request id, in order. A request with no spans at all is a gap of its own."""
    traces = []
    for request_id in request_ids:
        spans = [record for record in records if record.attributes.get("brickhaven.request_id") == request_id]
        if not spans:
            traces.append(RequestTrace(request_id, False, ["no spans reached Jaeger"], None, "", "", "", 0, 0, []))
            continue
        # Jaeger returns whole traces, so take every span of the trace that holds this request's root.
        problems = tree_problems(spans)
        root = next((span for span in spans if span.name == "request" and span.parent_id is None), None)
        attrs = root.attributes if root else {}
        chats = [span for span in spans if span.name.startswith("chat ")]
        traces.append(RequestTrace(
            request_id, not problems, problems, root.duration_us / 1000 if root else None, attrs.get("brickhaven.outcome", ""),
            attrs.get("brickhaven.failed_step", ""), attrs.get("brickhaven.reason", ""),
            sum(span.attributes.get("gen_ai.usage.input_tokens", 0) for span in chats),
            sum(span.attributes.get("gen_ai.usage.output_tokens", 0) for span in chats), waterfall(spans)))
    return traces
