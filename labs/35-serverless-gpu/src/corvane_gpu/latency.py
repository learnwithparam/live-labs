"""Latency and throughput from a burst's outcomes: p50, p95, a histogram and tokens per second.

Every item counts. A failed or timed-out item stays in the count and in the failure rate, because a
latency figure that drops the slowest requests is the number that hides an outage.
"""

import math

from corvane_gpu.burst import OK

HISTOGRAM_EDGES = [0.25, 0.5, 1, 2, 4, 8, 16, 32]


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile: the smallest value at least `share` of the values are at or below.

    Nearest rank always returns a latency that really happened, unlike interpolation, which can
    report a time no request took.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def histogram(latencies: list[float], edges: list[float] = HISTOGRAM_EDGES) -> list[dict]:
    """Counts per latency bucket, each bucket up to and including its edge, the last one open-ended."""
    buckets = [{"up_to": edge, "count": 0} for edge in edges] + [{"up_to": None, "count": 0}]
    for latency in latencies:
        index = next((position for position, edge in enumerate(edges) if latency <= edge), len(edges))
        buckets[index]["count"] += 1
    return buckets


def summarise(outcomes: list[dict]) -> dict:
    """The numbers the platform team watches for one burst.

    Latency percentiles are over answered items. Failures are reported beside them as a count and a
    rate, so a fast p95 with ten timeouts never reads as a healthy endpoint.
    """
    answered = [outcome for outcome in outcomes if outcome["status"] == OK]
    latencies = [outcome["latency_seconds"] for outcome in answered]
    starts = [outcome["started_at"] for outcome in outcomes]
    ends = [outcome["started_at"] + outcome["latency_seconds"] for outcome in outcomes]
    wall = (max(ends) - min(starts)) if outcomes else 0.0
    tokens = sum(outcome["prompt_tokens"] + outcome["completion_tokens"] for outcome in answered)
    generated = sum(outcome["completion_tokens"] for outcome in answered)
    return {
        "items": len(outcomes),
        "answered": len(answered),
        "failed": len(outcomes) - len(answered),
        "timed_out": sum(outcome["status"] == "timed_out" for outcome in outcomes),
        "p50_seconds": percentile(latencies, 0.50),
        "p95_seconds": percentile(latencies, 0.95),
        "max_seconds": max(latencies) if latencies else None,
        "wall_seconds": round(wall, 3),
        "tokens_per_second": round(tokens / wall, 1) if wall else None,
        "generated_tokens_per_second": round(generated / wall, 1) if wall else None,
        "histogram": histogram(latencies),
    }


def accuracy(outcomes: list[dict], truth: dict[str, str]) -> dict:
    """Agreement with the reviewers' labels over every golden item, failures counted as wrong.

    A failed item has no answer, so it cannot be right. Leaving it out would let an endpoint that
    times out on hard items look more accurate than one that answers them.
    """
    correct = sum(1 for outcome in outcomes if outcome["label"] is not None and outcome["label"] == truth.get(outcome["id"]))
    return {"total": len(outcomes), "correct": correct,
            "accuracy": round(correct / len(outcomes), 4) if outcomes else 0.0}
