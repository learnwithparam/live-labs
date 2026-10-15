"""Read the month's spans back from Jaeger and build finance's report: the invoice split by customer and
feature, the share the logs alone could split, every run's waterfall, and the slowest run's slow step.

The report reads spans from the trace store, not from memory, so it sees exactly what the collector
kept. A span lost in export is missing here too, and its invoice line shows up unattributed.
"""

import json
import urllib.parse
import urllib.request

from quillmark.attribution import attribute, attribute_from_logs, runs, waterfall
from quillmark.config import OUT, SERVICE_NAME, accounts, jaeger_url

SPANS, REPORT, DECISIONS = OUT / "spans.json", OUT / "report.json", OUT / "decisions.json"
VALUE_KINDS = ("stringValue", "intValue", "doubleValue", "boolValue")


def plain_value(value: dict):
    """OTLP JSON wraps every attribute value in its type; integers arrive as strings."""
    if "arrayValue" in value:
        return [plain_value(item) for item in value["arrayValue"].get("values", [])]
    for kind in VALUE_KINDS:
        if kind in value:
            return int(value[kind]) if kind == "intValue" else value[kind]
    return None


def flatten(otlp: dict) -> list[dict]:
    """Turn Jaeger's OTLP JSON into one flat dict per span: ids, name, times and attributes."""
    spans = []
    for resource_spans in otlp.get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                spans.append({"trace_id": span["traceId"], "span_id": span["spanId"],
                              "parent_id": span.get("parentSpanId") or None, "name": span["name"],
                              "start_ns": int(span["startTimeUnixNano"]), "end_ns": int(span["endTimeUnixNano"]),
                              "attributes": {item["key"]: plain_value(item["value"]) for item in span.get("attributes", [])}})
    return spans


def fetch_spans(started: str, finished: str) -> list[dict]:
    """Every span the helpdesk sent in the month's window, through Jaeger's v3 query API."""
    query = urllib.parse.urlencode({"query.service_name": SERVICE_NAME, "query.start_time_min": started,
                                    "query.start_time_max": finished, "query.num_traces": 1000})
    with urllib.request.urlopen(f"{jaeger_url()}/api/v3/traces?{query}", timeout=30) as response:
        return flatten(json.loads(response.read()).get("result", {}))


def decisions() -> dict:
    return json.loads(DECISIONS.read_text()) if DECISIONS.exists() else {}


def build(month: dict, invoice: dict, spans: list[dict], events: list[dict], finance: dict) -> dict:
    """Everything the finance page shows, computed from the invoice, the stored spans and the log lines."""
    lines = invoice["lines"]
    every_run = runs(spans)
    replies = {result["run"]: result for result in month["runs"]}
    waterfalls = {row["trace_id"]: {**waterfall(spans, row["trace_id"], lines),
                                    "reply": replies.get(row["run"], {}).get("reply", "")} for row in every_run}
    slowest = waterfalls[every_run[0]["trace_id"]] if every_run else None
    return {"model": month["model"], "period": {"started": month["started"], "finished": month["finished"]},
            "export": month["export"], "attribution": attribute(lines, spans, finance),
            "logs_only": attribute_from_logs(lines, events), "runs": every_run, "waterfalls": waterfalls,
            "slowest": slowest and {key: slowest[key] for key in ("trace_id", "run", "customer", "duration_ms",
                                                                  "slow_step", "slow_step_ms")},
            "failed_runs": [result for result in month["runs"] if not result.get("ok")],
            "customers": {key: account["name"] for key, account in accounts().items()},
            "features": ["reply-draft", "thread-summary", "weekly-digest"], "jaeger_url": jaeger_url()}


def rebuild() -> dict:
    """Recompute the report from what is saved in `out/`, after a finance decision or a fresh fetch."""
    from quillmark.workload import APP_LOG, INVOICE, MONTH

    events = [json.loads(line) for line in APP_LOG.read_text().splitlines() if line.strip()]
    report = build(json.loads(MONTH.read_text()), json.loads(INVOICE.read_text()), json.loads(SPANS.read_text()),
                   events, decisions())
    REPORT.write_text(json.dumps(report, indent=1))
    return report


def record_decision(generation_id: str, decision: dict) -> dict:
    """Finance's answer for one unattributed line, kept beside the invoice so every rebuild reuses it."""
    saved = decisions()
    saved[generation_id] = decision
    DECISIONS.write_text(json.dumps(saved, indent=1))
    return rebuild()
