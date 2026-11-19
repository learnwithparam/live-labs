"""Keyless tests of the review: the draft is refused for named reasons, the RFC is approved, and each
rule bites when its input is broken."""

import shutil

import pytest

from brickhaven_rfc.benchmark import cases_per_tag, load_cases
from brickhaven_rfc.config import DATA, MIN_CASES_PER_FAILURE_MODE, RFC
from brickhaven_rfc.review import FIELDS, load_send_backs, review, send_back
from brickhaven_rfc.rfc import read_rfc


def test_the_draft_is_refused_with_every_failure_mode_unmeasured():
    rfc = read_rfc(RFC / "draft.md")
    verdict = review(rfc)
    assert len(verdict.modes) == 5 and len(verdict.unmeasured) == 5
    assert verdict.total_ms == 9500 > verdict.signed_ms
    assert verdict.plan_gaps == list(FIELDS)
    assert not verdict.approved


def test_the_rfc_is_approved_inside_the_signed_budget():
    verdict = review(read_rfc(RFC / "rfc.toml"))
    assert verdict.approved and not verdict.unmeasured
    assert verdict.total_ms == 7200 <= verdict.signed_ms == 8000


def test_a_failure_mode_with_no_labelled_cases_is_unmeasured():
    verdict = review(read_rfc(RFC / "rfc.toml"), without_tag="safety")
    assert [mode.id for mode in verdict.unmeasured] == ["missed-hazard"]
    assert "0 labelled cases" in verdict.unmeasured[0].problems[0]


def test_editing_the_benchmark_after_sign_off_blocks_approval(tmp_path):
    shutil.copytree(DATA, tmp_path / "data")
    (tmp_path / "data" / "benchmark.jsonl").write_text((DATA / "benchmark.jsonl").read_text() + "\n")
    verdict = review(read_rfc(RFC / "rfc.toml"), directory=tmp_path / "data")
    assert verdict.changed == ["benchmark.jsonl"] and not verdict.approved


def test_a_send_back_holds_for_this_version_only():
    rfc = read_rfc(RFC / "rfc.toml")
    send_back(rfc, "guessed-unit", "Dana Okafor", "The person who reads no-flat messages is not named")
    held = review(rfc)
    assert [mode.id for mode in held.unmeasured] == ["guessed-unit"] and not held.approved
    assert "Dana Okafor" in held.unmeasured[0].problems[0]
    rfc.digest = "a later version"  # an edited RFC is a new version, and the old note does not follow it
    assert review(rfc).approved and len(load_send_backs()) == 1


def test_a_send_back_needs_a_real_failure_mode_a_name_and_a_reason():
    rfc = read_rfc(RFC / "rfc.toml")
    with pytest.raises(SystemExit):
        send_back(rfc, "no-such-mode", "Dana", "a reason")
    with pytest.raises(SystemExit):
        send_back(rfc, "guessed-unit", " ", "a reason")


def test_the_benchmark_covers_what_the_rfc_promises_to_measure():
    cases, rfc = load_cases(), read_rfc(RFC / "rfc.toml")
    counts = cases_per_tag(cases)
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(set(order) == set(FIELDS) for case in cases for order in case["expected"])
    # Every tag except the plain one belongs to a failure mode, and every failure mode has its sample.
    assert set(counts) - {"plain"} == {mode.tag for mode in rfc.failure_modes}
    assert all(counts[mode.tag] >= MIN_CASES_PER_FAILURE_MODE for mode in rfc.failure_modes)
    assert all(mode.scored_field in FIELDS and mode.scored_field in rfc.eval_plan for mode in rfc.failure_modes)
