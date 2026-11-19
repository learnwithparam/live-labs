"""The lab's promise, on a real model: every field of the live release meets its threshold on the
human-verified golden set, the bad candidate is blocked with a named regression, and the harmless
candidate ships with none. The truth is data/golden.jsonl, which a person verified."""

import pytest

import brickhaven_evals.config as settings
from brickhaven_evals.config import THRESHOLDS
from brickhaven_evals.extractor import load_candidate, run_candidate, save_run
from brickhaven_evals.golden import load_golden
from brickhaven_evals.scoring import gate, pass_rates


@pytest.mark.proof
def test_the_gate_blocks_the_bad_candidate_and_ships_the_harmless_one():
    golden = load_golden()
    runs = {name: run_candidate(load_candidate(name), golden) for name in ("live", "bad", "harmless")}
    for name, run in runs.items():
        save_run(name, run, settings.RUNS)  # the page reads these, so serve shows the same numbers
    live_rates = pass_rates(golden, runs["live"])
    assert all(live_rates[field] >= threshold for field, threshold in THRESHOLDS.items()), live_rates
    bad, harmless = gate(golden, runs["live"], runs["bad"]), gate(golden, runs["live"], runs["harmless"])
    assert not bad.ships and len(bad.regressions) >= 1
    assert any(regression.field_name == "safety_flag" for regression in bad.regressions)
    assert harmless.ships and len(harmless.regressions) == 0
