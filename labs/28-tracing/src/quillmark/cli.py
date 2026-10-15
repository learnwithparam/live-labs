"""`quill`: run the month's AI usage, read the traces back, split the invoice, and record finance's calls."""

import argparse
import json
import logging
import sys
import time
import urllib.request

from quillmark.config import PORT, jaeger_url, otlp_endpoint

FEATURES = ("reply-draft", "thread-summary", "weekly-digest")


def wait_for_jaeger(seconds: int = 30) -> None:
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(f"{jaeger_url()}/api/v3/services", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"Jaeger is not answering at {jaeger_url()}. Start it with the docker command in the README.")


def month() -> int:
    from openai import OpenAI
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from quillmark.billing import Invoice
    from quillmark.config import TIMEOUT, llm_settings
    from quillmark.telemetry import Tracing
    from quillmark.workload import INVOICE, run_month, save

    wait_for_jaeger()
    # The exporter logs every retry; the counting exporter reports failures once, in the summary below.
    logging.getLogger("opentelemetry.exporter").setLevel(logging.CRITICAL)
    base_url, api_key, model = llm_settings()
    invoice = Invoice()
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2, http_client=invoice.http_client())
    tracing = Tracing(OTLPSpanExporter(endpoint=otlp_endpoint()))
    print(f"Running the month's tickets on {model}, sending spans to {otlp_endpoint()}.\n")

    def show(result: dict) -> None:
        status = "ok" if result["ok"] else f"FAILED: {result['error']}"
        note = "  (collector down while it ran)" if result.get("collector") else ""
        print(f"  {result['run']}  {status}{note}")

    result = run_month(client, model, tracing, on_run=show)
    tracing.shutdown()
    save(result, invoice)
    export = result["export"]
    print(f"\nInvoice: {len(invoice.lines)} generations billed, ${invoice.total():.6f}. Saved to {INVOICE}.")
    print(f"Spans exported: {export['exported_spans']}. Failed exports: {export['failed_exports']}, "
          f"{export['lost_spans']} spans lost. Every customer still got an answer.")
    return 0 if all(run["ok"] for run in result["runs"]) else 1


def report() -> int:
    from quillmark.report import SPANS, fetch_spans, rebuild
    from quillmark.workload import MONTH

    wait_for_jaeger()
    month_run = json.loads(MONTH.read_text())
    # Jaeger indexes spans a moment after they arrive, so read until two reads agree.
    spans, previous = [], -1
    for _ in range(20):
        spans = fetch_spans(month_run["started"], month_run["finished"])
        if spans and len(spans) == previous:
            break
        previous = len(spans)
        time.sleep(1)
    SPANS.write_text(json.dumps(spans))
    print(f"Read {len(spans)} spans from Jaeger.\n")
    return show_report(rebuild())


def show_report(built: dict) -> int:
    attribution, logs = built["attribution"], built["logs_only"]
    total = attribution["invoice_total"]
    print(f"{'customer':<14}" + "".join(f"{feature:>16}" for feature in FEATURES) + f"{'total':>14}")
    customers = sorted({cell["customer"] for cell in attribution["cells"]})
    for customer in customers:
        costs = {cell["feature"]: cell["cost"] for cell in attribution["cells"] if cell["customer"] == customer}
        print(f"{customer:<14}" + "".join(f"{('$%.6f' % costs[feature]) if feature in costs else '-':>16}"
                                           for feature in FEATURES) + f"{'$%.6f' % sum(costs.values()):>14}")
    print(f"{'UNATTRIBUTED':<14}{'':>48}{'$%.6f' % attribution['unattributed_cost']:>14}")
    print(f"{'invoice':<14}{'':>48}{'$%.6f' % total:>14}\n")
    for number, line in enumerate(attribution["unattributed"], 1):
        flag = " Flagged to engineering." if line["flag"] else ""
        run = f" Run: {line['run_name']}." if line["run_name"] else ""
        print(f"  #{number} {line['generation_id']} ${line['cost']:.6f}: {line['reason']}{run}{flag}")
    print(f"\nAttributed to a customer and a feature by tracing: {attribution['share_traced']:.1%} of the invoice.")
    if attribution["finance_cost"]:
        print(f"After finance's review: {attribution['share_after_review']:.1%}.")
    print(f"From the logs alone: {logs['share']:.1%} ({logs['ambiguous_lines']} of {logs['invoice_lines']} lines "
          f"had more than one customer's request open).")
    print(f"Books reconcile to the invoice: {'yes' if attribution['reconciles'] else 'NO'}.")
    slowest = built["slowest"]
    if slowest:
        print(f"\nSlowest run: {slowest['run']} for {slowest['customer']}, {slowest['duration_ms'] / 1000:.1f} s. "
              f"Slow step: {slowest['slow_step']}, {slowest['slow_step_ms'] / 1000:.1f} s.")
        print(f"Open it in Jaeger: {built['jaeger_url']}/trace/{slowest['trace_id']}")
    return 0


def decide(selector: str, decision: dict) -> int:
    """`selector` is a generation id, or the number the report printed beside an unattributed line."""
    from quillmark.report import rebuild, record_decision

    unattributed = rebuild()["attribution"]["unattributed"]
    if selector.isdigit():
        if not 1 <= int(selector) <= len(unattributed):
            raise SystemExit(f"There is no unattributed line #{selector}.")
        selector = unattributed[int(selector) - 1]["generation_id"]
    print(f"Recorded for {selector}: {decision}.\n")
    return show_report(record_decision(selector, decision))


def main() -> None:
    parser = argparse.ArgumentParser(prog="quill")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("month", help="run every ticket in data/runs.jsonl on the real model, traced")
    sub.add_parser("report", help="read the spans back from Jaeger and split the invoice")
    assign = sub.add_parser("assign", help="finance assigns an unattributed line to a customer and a feature")
    assign.add_argument("generation")
    assign.add_argument("--customer", required=True)
    assign.add_argument("--feature", required=True, choices=FEATURES)
    flag = sub.add_parser("flag", help="finance sends an unattributed line back to engineering to fix the tagging")
    flag.add_argument("generation")
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "month":
        sys.exit(month())
    if args.command == "report":
        sys.exit(report())
    if args.command == "assign":
        sys.exit(decide(args.generation, {"customer": args.customer, "feature": args.feature}))
    if args.command == "flag":
        sys.exit(decide(args.generation, {"flag": True}))
    from quillmark.serve import main as serve

    sys.exit(serve(args.port))
