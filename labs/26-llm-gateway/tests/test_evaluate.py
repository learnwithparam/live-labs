"""The metric: cost per request, held to the naive run's quality minus one item."""

from fakes import result
from fernway.evaluate import compare, summarise


def runs(routed_passes: int):
    naive = summarise("strong", "naive", [result(f"I-{number}", True, 0.0001, "strong") for number in range(4)])
    routed = summarise("routed", "routed", [result(f"I-{number}", number < routed_passes, 0.00004, "cheap")
                                            for number in range(4)])
    return naive, routed


def test_a_cheaper_run_with_one_miss_holds_quality():
    metric = compare(*runs(3))
    assert metric["cheaper"] and metric["quality_holds"] and round(metric["saving"], 2) == 0.6


def test_two_misses_break_the_bar_however_much_it_saves():
    assert not compare(*runs(2))["quality_holds"]


def test_the_summary_counts_tiers_and_flags():
    naive, _ = runs(4)
    assert naive["tiers"] == {"cheap": 0, "strong": 4, "strong-backup": 0} and naive["cost_per_request"] == 0.0001
