"""The golden set grades replies the real model gave: the checks, not the model, decide a version's score."""

from fakes import RECORDED

from driftwell.golden import cases, failed_checks, fingerprint, score
from driftwell.release import needed


def score_recording(name: str) -> dict:
    return score(cases(), lambda case: {"reply": RECORDED[name][case["id"]], "usage": {"cost": 0.0}})


def test_each_recorded_version_scores_what_the_real_run_scored():
    assert score_recording("v1")["passed"] == 15
    assert score_recording("v2")["passed"] == 16
    assert score_recording("v3")["passed"] == 1


def test_the_code_prompt_fails_only_the_data_export_question():
    failed = [row for row in score_recording("v1")["rows"] if not row["passed"]]
    assert [row["id"] for row in failed] == ["G11"]


def test_a_refund_promised_outside_policy_is_named_as_the_broken_rule():
    late_return = next(case for case in cases() if case["id"] == "G02")
    failed = failed_checks(late_return, "So sorry! We'll give you a full refund straight away.")
    assert any(reason.startswith("breaks:") for reason in failed)


def test_a_curly_apostrophe_still_passes_a_check_written_with_a_straight_one():
    card_rule = {"checks": [{"says": "do not share card details", "must": "don'?t share"}]}
    assert failed_checks(card_rule, "Please don’t share your card number in chat.") == []


def test_the_bar_is_a_count_rounded_up():
    assert needed(16, 0.85) == 14
    assert needed(20, 0.85) == 17


def test_scores_follow_the_words_not_the_version_number():
    assert fingerprint("same words") == fingerprint("same words")
    assert fingerprint("same words") != fingerprint("same words.")
