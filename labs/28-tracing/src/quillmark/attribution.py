"""Split the provider's invoice by customer and feature, from traces or from logs, and find a slow step.

The invoice is the truth: every line is a generation the provider billed. Each line is joined to the
span that made it by the generation id, which the OpenAI instrumentation records as `gen_ai.response.id`.
The customer and feature on that span came from baggage. A line with no span, or a span with no
customer, is never spread across customers: it goes to the unattributed bucket for finance.
"""

from collections import defaultdict
from datetime import datetime

UNATTRIBUTED = "unattributed"


def owner_of(attributes: dict) -> tuple[str | None, str | None]:
    return attributes.get("customer.id"), attributes.get("feature")


def roots_by_trace(spans: list[dict]) -> dict[str, dict]:
    """The top span of each trace: the one whose parent is not in the store."""
    ids = {span["span_id"] for span in spans}
    return {span["trace_id"]: span for span in spans if not span["parent_id"] or span["parent_id"] not in ids}


def why_unattributed(span: dict | None, attributes: dict) -> str:
    if span is None:
        return ("No span carries this generation id. The call was billed, but its trace never reached the "
                "collector.")
    missing = [key for key, value in zip(("customer.id", "feature"), owner_of(attributes)) if not value]
    return f"The span that made this call has no {' or '.join(missing)}. Nothing upstream put it in baggage."


def attribute(lines: list[dict], spans: list[dict], decisions: dict | None = None) -> dict:
    """Every invoice line lands in exactly one place: a (customer, feature) cell or the unattributed bucket.

    `decisions` are finance's answers for unattributed lines, keyed by generation id. An assignment moves
    the line into a cell, marked as finance's; a flag keeps it unattributed and names the feature to fix.
    """
    decisions = decisions or {}
    by_generation = {span["attributes"]["gen_ai.response.id"]: span for span in spans
                     if span["attributes"].get("gen_ai.response.id")}
    roots = roots_by_trace(spans)
    cells: dict[tuple[str, str], dict] = defaultdict(lambda: {"cost": 0.0, "lines": 0, "by_finance": 0.0})
    unattributed, traced_cost, finance_cost = [], 0.0, 0.0
    for line in lines:
        span = by_generation.get(line["generation_id"])
        attributes = span["attributes"] if span else {}
        customer, feature = owner_of(attributes)
        decision = decisions.get(line["generation_id"], {})
        if customer and feature:
            traced_cost += line["cost"]
        elif decision.get("customer") and decision.get("feature"):
            customer, feature = decision["customer"], decision["feature"]
            finance_cost += line["cost"]
            cells[(customer, feature)]["by_finance"] += line["cost"]
        else:
            root = roots.get(span["trace_id"]) if span else None
            unattributed.append({**line, "reason": why_unattributed(span, attributes),
                                 "trace_id": span and span["trace_id"], "span_name": span and span["name"],
                                 "run_name": root and root["name"], "flag": decision.get("flag")})
            continue
        cells[(customer, feature)]["cost"] += line["cost"]
        cells[(customer, feature)]["lines"] += 1
    total = sum(line["cost"] for line in lines)
    unattributed_cost = sum(line["cost"] for line in unattributed)
    return {"invoice_total": total, "invoice_lines": len(lines),
            "cells": [{"customer": customer, "feature": feature, **cell} for (customer, feature), cell in sorted(cells.items())],
            "traced_cost": traced_cost, "finance_cost": finance_cost, "unattributed_cost": unattributed_cost,
            "unattributed": unattributed,
            # The metric: what tracing alone attributed, and what is attributed once finance has reviewed.
            "share_traced": traced_cost / total if total else 0.0,
            "share_after_review": (traced_cost + finance_cost) / total if total else 0.0,
            # The books must close: every cent of the invoice is in a cell or in the bucket, never lost.
            "reconciles": abs(traced_cost + finance_cost + unattributed_cost - total) < 1e-12}


def moment(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def attribute_from_logs(lines: list[dict], events: list[dict]) -> dict:
    """The pre-tracing attempt: join each billed call to whichever request was open at the time.

    A call line has a response id but no customer. A request line has a customer but no id. The only join
    left is time, and it holds only when exactly one customer's request was in flight.
    """
    calls = {event["response_id"]: event for event in events if event["event"] == "llm.call"}
    windows = [(moment(event["at"]) - event["duration_ms"] / 1000, moment(event["at"]), event["customer"], event["feature"])
               for event in events if event["event"] == "request.end"]
    attributed, ambiguous, orphaned = 0.0, 0, 0
    for line in lines:
        call = calls.get(line["generation_id"])
        if call is None:
            orphaned += 1
            continue
        call_end = moment(call["at"])
        call_start = call_end - call["duration_ms"] / 1000
        owners = {(customer, feature) for start, end, customer, feature in windows
                  if start <= call_start and call_end <= end}
        if len(owners) == 1:
            attributed += line["cost"]
        else:
            ambiguous += 1
    total = sum(line["cost"] for line in lines)
    return {"share": attributed / total if total else 0.0, "attributed_cost": attributed,
            "ambiguous_lines": ambiguous, "orphaned_lines": orphaned, "invoice_lines": len(lines)}


def runs(spans: list[dict]) -> list[dict]:
    """One row per agent run, slowest first, read only from the traces."""
    rows = []
    for trace_id, root in roots_by_trace(spans).items():
        if root["attributes"].get("gen_ai.operation.name") != "invoke_agent":
            continue
        customer, feature = owner_of(root["attributes"])
        rows.append({"trace_id": trace_id, "run": root["attributes"].get("run.id"), "name": root["name"],
                     "customer": customer, "feature": feature or root["attributes"].get("gen_ai.agent.name"),
                     "duration_ms": (root["end_ns"] - root["start_ns"]) / 1e6})
    return sorted(rows, key=lambda row: -row["duration_ms"])


def waterfall(spans: list[dict], trace_id: str, lines: list[dict] = ()) -> dict:
    """A run's steps on one time axis, each with its offset, duration, depth and billed cost.

    The slow step is the root's child with the longest duration: that is what made the customer wait.
    """
    in_trace = [span for span in spans if span["trace_id"] == trace_id]
    root = roots_by_trace(in_trace)[trace_id]
    parents = {span["span_id"]: span["parent_id"] for span in in_trace}
    cost_of = {line["generation_id"]: line["cost"] for line in lines}

    def depth(span: dict) -> int:
        level, parent = 0, span["parent_id"]
        while parent in parents:
            level, parent = level + 1, parents[parent]
        return level

    rows = [{"name": span["name"], "span_id": span["span_id"], "depth": depth(span),
             "offset_ms": (span["start_ns"] - root["start_ns"]) / 1e6,
             "duration_ms": (span["end_ns"] - span["start_ns"]) / 1e6,
             "input_tokens": span["attributes"].get("gen_ai.usage.input_tokens"),
             "output_tokens": span["attributes"].get("gen_ai.usage.output_tokens"),
             "cost": cost_of.get(span["attributes"].get("gen_ai.response.id"))}
            for span in sorted(in_trace, key=lambda span: span["start_ns"])]
    children = [row for row in rows if row["depth"] == 1]
    slowest = max(children, key=lambda row: row["duration_ms"]) if children else None
    return {"trace_id": trace_id, "run": root["attributes"].get("run.id"), "customer": root["attributes"].get("customer.id"),
            "feature": root["attributes"].get("feature") or root["attributes"].get("gen_ai.agent.name"),
            "duration_ms": rows[0]["duration_ms"], "rows": rows, "slow_step": slowest and slowest["name"],
            "slow_step_ms": slowest and slowest["duration_ms"]}
