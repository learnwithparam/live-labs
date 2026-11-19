"""`brickhaven-slice`: run the messages through the slice, read the spans back from Jaeger, show the board."""

import argparse
import json
import logging
import sys
import time
import urllib.request

from brickhaven_slice.config import MODES, OUT, WORKERS, P95_TARGET_MS, COMPLETION_THRESHOLD, jaeger_url, otlp_endpoint


def wait_for_jaeger(seconds: int = 30) -> None:
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(f"{jaeger_url()}/api/services", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"Jaeger is not answering at {jaeger_url()}. Start it with the docker command in the README.")


def run(mode: str) -> int:
    from openai import OpenAI
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from brickhaven_slice.config import llm_settings
    from brickhaven_slice.llm import Model
    from brickhaven_slice.pipeline import Outbox, Services, run_all
    from brickhaven_slice.telemetry import Tracing
    from brickhaven_slice.vendors import load_messages, load_units, load_vendors

    wait_for_jaeger()
    logging.getLogger("opentelemetry.exporter").setLevel(logging.CRITICAL)
    base_url, api_key, model_name = llm_settings()
    # The client never retries on its own: the slice retries, so each attempt is a span and counts in the ledger.
    client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
    tracing = Tracing(OTLPSpanExporter(endpoint=otlp_endpoint()))
    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob(f"*-{mode}.jsonl"):
        stale.unlink()
    services = Services(Model(client, model_name), tracing.tracer, Outbox(OUT), load_units(), load_vendors())
    messages, run_id = load_messages(), f"run-{int(time.time())}"
    results = run_all(services, messages, run_id, mode, WORKERS)
    tracing.flush()
    tracing.shutdown()
    saved = {"run_id": run_id, "mode": mode, "results": [vars(result) for result in results]}
    (OUT / f"run-{mode}.json").write_text(json.dumps(saved, indent=1))
    counts = {kind: sum(result.outcome == kind for result in results) for kind in ("sent", "human_queue", "crashed")}
    print(f"{mode}: {counts['sent']} of {len(results)} dispatched, {counts['human_queue']} in the human queue, "
          f"{counts['crashed']} crashed. Run {run_id}.")
    return 0


def report(mode: str) -> int:
    from brickhaven_slice.board import build_board, save_board
    from brickhaven_slice.trace_check import judge, read_back
    from brickhaven_slice.vendors import load_messages

    saved_path = OUT / f"run-{mode}.json"
    if not saved_path.exists():
        raise SystemExit(f"No {mode} run yet. Run `brickhaven-slice run --mode {mode}` first.")
    saved, messages = json.loads(saved_path.read_text()), load_messages()
    records = read_back(saved["run_id"], len(messages), jaeger_url())
    traces = judge(records, [message["id"] for message in messages])
    board = build_board(mode, saved["run_id"], messages, saved["results"], traces)
    save_board(board)
    totals = board["totals"]
    print(f"Spans read back from Jaeger: {len(records)}")
    print(f"Trace trees complete: {totals['complete_trees']} of {totals['requests']}")
    print(f"Completion rate: {totals['completion_rate']:.0%} (proof threshold {COMPLETION_THRESHOLD:.0%})")
    if totals["p95_ms"] is None:
        print("p95 trace latency: no request span to measure")
    else:
        print(f"p95 trace latency: {totals['p95_ms']:.0f} ms (target {P95_TARGET_MS} ms)")
    print(f"Tokens: {totals['input_tokens']} in, {totals['output_tokens']} out. "
          f"Human queue: {totals['human_queue']}. Lost: {totals['lost']}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="brickhaven-slice")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "report"):
        commands.add_parser(name).add_argument("--mode", choices=MODES, required=True)
    commands.add_parser("serve")
    arguments = parser.parse_args()
    if arguments.command == "serve":
        from brickhaven_slice.serve import run as serve_board

        serve_board()
        return
    sys.exit({"run": run, "report": report}[arguments.command](arguments.mode))
