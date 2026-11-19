"""The gate logic with scripted outputs: per-field rates, thresholds and the no-regression rule."""

from brickhaven_evals.config import THRESHOLDS
from brickhaven_evals.extractor import parse_output, run_candidate
from brickhaven_evals.golden import load_golden
from brickhaven_evals.scoring import field_values, gate, overall_exact_match, pass_rates

from fakes import candidate_named, scripted_extract


def runs():
    golden = load_golden()
    extract = scripted_extract(golden)
    return golden, {name: run_candidate(candidate_named(name), golden, extract) for name in ("live", "bad", "harmless")}


def test_thresholds_are_pinned_to_the_signed_eval_plan():
    assert THRESHOLDS == {"trade": 0.95, "urgency": 0.90, "unit": 0.95, "safety_flag": 1.0, "access_window": 0.85}


def test_the_live_release_meets_every_threshold():
    golden, outputs = runs()
    rates = pass_rates(golden, outputs["live"])
    assert all(rates[name] >= threshold for name, threshold in THRESHOLDS.items()), rates
    assert rates["access_window"] == 27 / 30 and rates["safety_flag"] == 1.0


def test_the_one_number_gate_ships_the_bad_candidate_while_safety_drops():
    golden, outputs = runs()
    verdict = gate(golden, outputs["live"], outputs["bad"])
    assert verdict.naive_candidate > verdict.naive_live and verdict.naive_ships
    assert verdict.candidate_rates["safety_flag"] < verdict.live_rates["safety_flag"]


def test_the_field_gate_blocks_the_bad_candidate_and_names_the_case():
    golden, outputs = runs()
    verdict = gate(golden, outputs["live"], outputs["bad"])
    assert not verdict.ships and verdict.below_threshold == ["safety_flag"]
    assert [(regression.case_id, regression.field_name) for regression in verdict.regressions] == [("G01", "safety_flag")]


def test_the_harmless_candidate_ships_with_no_regression():
    golden, outputs = runs()
    verdict = gate(golden, outputs["live"], outputs["harmless"])
    assert verdict.ships and verdict.regressions == [] and verdict.below_threshold == []


def test_a_regression_blocks_even_when_the_field_still_meets_its_threshold():
    golden, outputs = runs()
    worse = {**outputs["live"], "P01": {"work_orders": [{**outputs["live"]["P01"]["work_orders"][0], "trade": "general"}]}}
    verdict = gate(golden, outputs["live"], worse)
    assert verdict.candidate_rates["trade"] >= THRESHOLDS["trade"] and not verdict.ships
    assert [(regression.case_id, regression.field_name) for regression in verdict.regressions] == [("P01", "trade")]


def test_a_dropped_second_issue_fails_the_trade_field():
    golden, outputs = runs()
    dropped = {**outputs["live"], "T01": {"work_orders": outputs["live"]["T01"]["work_orders"][:1]}}
    assert pass_rates(golden, dropped)["trade"] < pass_rates(golden, outputs["live"])["trade"]
    assert field_values(dropped["T01"], "trade") == ["plumbing"]


def test_a_reply_that_breaks_the_format_fails_every_field_never_skips():
    golden, outputs = runs()
    broken = {**outputs["live"], "P02": parse_output("sorry, I cannot help")}
    assert overall_exact_match(golden, broken) < overall_exact_match(golden, outputs["live"])
    assert gate(golden, outputs["live"], broken).regressions != []
