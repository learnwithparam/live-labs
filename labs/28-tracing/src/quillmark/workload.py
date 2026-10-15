"""The month's AI usage, run for real: every ticket in `data/runs.jsonl`, four at a time, like a busy day.

Concurrency is the point. With one request at a time a log line could be matched to a customer by its
timestamp; with several in flight it cannot, and only the trace context still knows whose call it was.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from quillmark.agent import AppLog, handle_job, handle_request
from quillmark.billing import Invoice
from quillmark.config import DOWN_COLLECTOR, OUT, PARALLEL, accounts, runs
from quillmark.telemetry import Tracing

INVOICE, APP_LOG, MONTH = OUT / "invoice.json", OUT / "app_log.jsonl", OUT / "month.json"


def run_one(run: dict, every_account: dict, client, model: str, tracing: Tracing, app_log: AppLog, sleep) -> dict:
    """A failed run is recorded and the month goes on: one customer's error must not stop the others."""
    try:
        if run.get("job"):
            result = handle_job(run, client, model, tracing.tracer, app_log)
        else:
            result = handle_request(run, every_account[run["customer"]], client, model, tracing.tracer, app_log, sleep)
        return {**result, "ok": True}
    except Exception as error:  # noqa: BLE001 - the month report shows which run failed and why
        return {"run": run["id"], "ok": False, "error": str(error)}


def run_month(client, model: str, tracing: Tracing, sleep=None, parallel: int = PARALLEL,
              down_exporter=None, on_run=lambda result: None) -> dict:
    """Run the month and return what happened; the invoice fills itself at the HTTP client as calls are billed.

    Runs marked `"collector": "down"` go last, alone, while the exporter points at a collector that is not
    there. The customer still gets the answer; the spans are lost and counted.
    """
    sleep = sleep or time.sleep
    every_account, app_log, results = accounts(), AppLog(), []
    started = datetime.now(timezone.utc)
    healthy = [run for run in runs() if run.get("collector") != "down"]
    outage = [run for run in runs() if run.get("collector") == "down"]
    # Each worker thread starts from an empty context, as a web server's request threads do.
    with ThreadPoolExecutor(parallel) as pool:
        for result in pool.map(lambda run: run_one(run, every_account, client, model, tracing, app_log, sleep),
                               healthy):
            results.append(result)
            on_run(result)
    tracing.flush()
    down = down_exporter or OTLPSpanExporter(endpoint=DOWN_COLLECTOR, timeout=2)
    with tracing.exporter.pointed_at(down):
        for run in outage:
            result = run_one(run, every_account, client, model, tracing, app_log, sleep)
            results.append({**result, "collector": "down"})
            on_run(results[-1])
        tracing.flush()
    return {"started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(), "model": model,
            "runs": results, "export": tracing.exporter.counts(), "events": app_log.events}


def save(month: dict, invoice: Invoice) -> None:
    OUT.mkdir(exist_ok=True)
    invoice.save(INVOICE)
    APP_LOG.write_text("".join(json.dumps(event) + "\n" for event in month["events"]))
    MONTH.write_text(json.dumps({key: value for key, value in month.items() if key != "events"}, indent=1))
