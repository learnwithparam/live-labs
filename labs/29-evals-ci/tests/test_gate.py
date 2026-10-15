"""The gate end to end on the real release files, with the scripted model: what it blocks, what it
lets through, how it fails closed, and how a person overrides it."""

import pytest

from fakes import FakeOpenAI
from kettlebay.assistant import load_golden, load_release
from kettlebay.gate import case_outcome, find_override, needed, record_override, run_release, summary_line, verdict
from kettlebay.store import load_store

GOLDEN = load_golden()


def run(name: str, repeats: int = 3, **fake) -> dict:
    return run_release(load_release(f"releases/{name}.json"), GOLDEN, load_store(), FakeOpenAI(GOLDEN, **fake), repeats)


@pytest.fixture(scope="module")
def live_run() -> dict:
    return run("live")


def test_live_passes_every_golden_case(live_run):
    result = verdict(GOLDEN, live_run, live_run)
    assert not result.blocked and {row["status"] for row in result.rows} == {"held"}


def test_removing_the_approval_rule_is_blocked_only_when_trajectories_are_read(live_run):
    candidate = run("faster-writes")
    full, answers = verdict(GOLDEN, live_run, candidate), verdict(GOLDEN, live_run, candidate, with_trajectory=False)
    assert full.unsafe == ["G05", "G06", "G07", "G12"]
    assert full.blocked and not answers.blocked
    assert summary_line(full) == "blocked: faster-writes has 4 unsafe (G05, G06, G07, G12)"


def test_dropping_figures_from_replies_is_blocked_by_both_gates(live_run):
    candidate = run("shorter-replies")
    assert verdict(GOLDEN, live_run, candidate, with_trajectory=False).blocked
    assert "G02" in verdict(GOLDEN, live_run, candidate).regressed


def test_a_model_that_skips_a_lookup_is_blocked_only_by_the_trajectory(live_run):
    candidate = run("cheaper-model", skip=("G12",))
    assert verdict(GOLDEN, live_run, candidate).regressed == ["G12"]
    assert not verdict(GOLDEN, live_run, candidate, with_trajectory=False).blocked


def test_a_harmless_prompt_change_passes(live_run):
    result = verdict(GOLDEN, live_run, run("order-numbers"))
    assert summary_line(result) == "passed: no golden case is worse with order-numbers"


def test_a_timeout_blocks_and_a_missing_case_blocks(live_run):
    timed_out = run("order-numbers", fail=("G03",))
    assert verdict(GOLDEN, live_run, timed_out).broken == ["G03"]
    del timed_out["cases"]["G04"]
    assert verdict(GOLDEN, live_run, timed_out).broken == ["G03", "G04"]


def test_one_wobble_in_three_passes_two_does_not(live_run):
    assert needed(3) == 2 and needed(5) == 4
    good, bad = run("live", repeats=1)["cases"]["G02"][0], run("shorter-replies", repeats=1)["cases"]["G02"][0]
    wobbly = {**live_run, "cases": {**live_run["cases"], "G02": [good, good, bad]}}
    broken = {**live_run, "cases": {**live_run["cases"], "G02": [good, bad, bad]}}
    assert not verdict(GOLDEN, live_run, wobbly).blocked
    assert verdict(GOLDEN, live_run, broken).regressed == ["G02"]


def test_one_unsafe_run_in_three_fails_the_case(live_run):
    unsafe = run("faster-writes", repeats=1)["cases"]["G06"][0]
    mostly_safe = {**live_run, "cases": {**live_run["cases"], "G06": live_run["cases"]["G06"][:2] + [unsafe]}}
    assert verdict(GOLDEN, live_run, mostly_safe).unsafe == ["G06"]
    # Two clean runs out of three would pass on their own, so this is the unsafe run failing the case.
    case = next(case for case in GOLDEN if case["id"] == "G06")
    outcome = case_outcome(case, mostly_safe["cases"]["G06"], with_trajectory=True)
    assert outcome["passes"] >= needed(3) and outcome["unsafe"] and not outcome["passed"]


def test_an_override_needs_a_name_and_a_reason_and_covers_only_that_release(live_run, tmp_path):
    log = tmp_path / "overrides.jsonl"
    result = verdict(GOLDEN, live_run, run("faster-writes"))
    with pytest.raises(ValueError, match="Name the person"):
        record_override(result, "abc", " ", "a reason long enough to read here", path=log)
    with pytest.raises(ValueError, match="at least 6 words"):
        record_override(result, "abc", "Owen Pratt", "fine", path=log)
    entry = record_override(result, "abc", "Owen Pratt", "Approval moves to the admin UI in the same deploy", path=log)
    assert entry["waived"] == ["G05", "G06", "G07", "G12"]
    assert find_override("abc", path=log)["by"] == "Owen Pratt"
    assert find_override("abd", path=log) is None


def test_no_override_for_a_case_with_no_result(live_run, tmp_path):
    result = verdict(GOLDEN, live_run, run("order-numbers", fail=("G01",)))
    with pytest.raises(ValueError, match="cannot be overridden"):
        record_override(result, "abc", "Owen Pratt", "ship it, the timeout was the provider", path=tmp_path / "log")


def test_changing_the_prompt_changes_the_fingerprint():
    assert len({load_release(f"releases/{name}.json").fingerprint
                for name in ("live", "faster-writes", "shorter-replies", "cheaper-model", "order-numbers")}) == 5


def test_ci_checks_the_releases_a_pull_request_touches():
    from kettlebay.cli import releases_touched

    assert releases_touched({"prompts/shorter-replies.txt"}) == ["releases/shorter-replies.json"]
    assert releases_touched({"tools/faster-writes.json", "README.md"}) == ["releases/faster-writes.json"]
    assert len(releases_touched({"data/golden.jsonl"})) == 4
    assert releases_touched({"README.md"}) == []
