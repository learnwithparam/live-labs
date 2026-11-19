"""The dispatcher's board: the run's requests, each judged by its spans, as one JSON file the page reads.

Where the app's own result and the trace disagree, the trace wins: a request the app thinks it sent but
whose spans show no sending step is not counted as dispatched.
"""

import json
from pathlib import Path

from brickhaven_slice.config import OUT, P95_TARGET_MS
from brickhaven_slice.trace_check import RequestTrace, percentile


def read_records(path: Path) -> dict[str, dict]:
    """A JSON-lines file keyed by request id; a missing file is an empty outbox."""
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["request_id"]: row for row in rows}


def build_board(mode: str, run_id: str, messages: list[dict], app_results: list[dict], traces: list[RequestTrace],
                outbox_folder: Path = OUT) -> dict:
    dispatched = read_records(outbox_folder / f"dispatches-{mode}.jsonl")
    app_by_id = {result["request_id"]: result for result in app_results}
    trace_by_id = {trace.request_id: trace for trace in traces}
    cards = []
    for message in messages:
        trace, app = trace_by_id[message["id"]], app_by_id.get(message["id"], {})
        outcome = trace.outcome or app.get("outcome", "crashed")
        latency = trace.latency_ms
        if latency is None and trace.rows:
            latency = max(row["start_ms"] + row["duration_ms"] for row in trace.rows)
        cards.append({
            "request_id": message["id"], "tenant": message["tenant"], "text": message["text"], "outcome": outcome,
            "failed_step": trace.failed_step or app.get("failed_step", ""), "reason": trace.reason or app.get("reason", ""),
            "dispatch": dispatched.get(message["id"]) if outcome == "sent" else None,
            "complete": trace.complete, "problems": trace.problems, "latency_ms": latency,
            "input_tokens": trace.input_tokens, "output_tokens": trace.output_tokens, "rows": trace.rows})
    latencies = [trace.latency_ms for trace in traces if trace.latency_ms is not None]
    sent = sum(card["outcome"] == "sent" for card in cards)
    return {"run_id": run_id, "mode": mode, "target_ms": P95_TARGET_MS, "totals": {
        "requests": len(cards), "dispatched": sent, "human_queue": sum(card["outcome"] == "human_queue" for card in cards),
        "lost": sum(card["outcome"] not in ("sent", "human_queue") for card in cards),
        "completion_rate": sent / len(cards) if cards else 0.0,
        "complete_trees": sum(card["complete"] for card in cards),
        "p95_ms": round(percentile(latencies, 0.95), 1) if latencies else None,
        "input_tokens": sum(card["input_tokens"] for card in cards),
        "output_tokens": sum(card["output_tokens"] for card in cards)}, "cards": cards}


def save_board(board: dict, folder: Path = OUT) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"board-{board['mode']}.json"
    path.write_text(json.dumps(board, indent=1))
    return path
