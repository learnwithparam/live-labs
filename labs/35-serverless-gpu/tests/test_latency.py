"""Latency and accuracy maths: nearest-rank p95, failures kept in every count, failures scored as wrong."""

from fakes import outcome

from corvane_gpu.latency import accuracy, histogram, percentile, summarise


def test_p95_is_a_latency_that_really_happened():
    latencies = [float(second) for second in range(1, 21)]
    assert percentile(latencies, 0.95) == 19.0
    assert percentile(latencies, 0.50) == 10.0
    assert percentile([0.3], 0.95) == 0.3
    assert percentile([], 0.95) is None


def test_histogram_puts_each_latency_in_one_bucket():
    buckets = histogram([0.1, 0.25, 0.3, 40.0])
    assert buckets[0] == {"up_to": 0.25, "count": 2}
    assert buckets[1] == {"up_to": 0.5, "count": 1}
    assert buckets[-1] == {"up_to": None, "count": 1}
    assert sum(bucket["count"] for bucket in buckets) == 4


def test_a_timeout_stays_in_the_counts_but_not_in_the_latency():
    outcomes = [outcome(f"t{number}", latency=0.2, started_at=0.0) for number in range(9)]
    outcomes.append(outcome("t9", status="timed_out", latency=30.0, label=None, started_at=0.0, tokens=(0, 0)))
    summary = summarise(outcomes)
    assert (summary["items"], summary["answered"], summary["failed"], summary["timed_out"]) == (10, 9, 1, 1)
    assert summary["p95_seconds"] == 0.2
    # The wall clock still includes the request that hung, so throughput is not flattered.
    assert summary["wall_seconds"] == 30.0
    assert summary["tokens_per_second"] == round(9 * 102 / 30.0, 1)


def test_a_failed_item_counts_as_wrong():
    truth = {"a": "meals", "b": "meals", "c": "software"}
    outcomes = [outcome("a"), outcome("b", status="timed_out", label=None), outcome("c", label="meals")]
    assert accuracy(outcomes, truth) == {"total": 3, "correct": 1, "accuracy": 0.3333}
