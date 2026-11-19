"""Only verified cases count, the dataset covers the named edge cases, and the bench verifies or corrects."""

import json

import pytest

from brickhaven_evals.golden import is_verified, load_golden, read_rows, verify_case, waiting_for_a_person
from brickhaven_evals.config import DATA

EDGE_TAGS = {"plain", "two-issues", "no-unit", "non-english", "gas-smell", "no-vendor"}


def test_the_golden_set_has_about_thirty_verified_cases_with_every_edge_tag():
    rows = read_rows(DATA / "golden.jsonl")
    assert len(rows) == 30 and all(is_verified(row) for row in rows)
    assert EDGE_TAGS <= {row["tag"] for row in rows}
    assert len({row["id"] for row in rows}) == 30


def test_the_expected_work_orders_use_the_signed_vocabulary():
    for row in read_rows(DATA / "golden.jsonl"):
        for order in row["expected"]["work_orders"]:
            assert set(order) == {"trade", "urgency", "unit", "safety_flag", "access_window"}
            assert order["urgency"] in {"emergency", "urgent", "routine"} and isinstance(order["safety_flag"], bool)
            assert order["access_window"] in {"morning", "afternoon", "evening", "anytime"}
    assert all(order["unit"] is None for row in read_rows(DATA / "golden.jsonl") if row["tag"] == "no-unit"
               for order in row["expected"]["work_orders"])


def test_a_case_without_a_name_or_a_date_never_counts(tmp_path):
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps({"id": "X1", "verified_by": "", "verified_on": ""}) + "\n"
                      + json.dumps({"id": "X2", "verified_by": "Dana", "verified_on": "2026-11-02"}) + "\n")
    assert [case["id"] for case in load_golden(golden_path=golden)] == ["X2"]


def test_verifying_a_proposal_adds_it_and_a_correction_replaces_the_proposal():
    assert len(load_golden()) == 30 and len(waiting_for_a_person()) == 3
    fixed = {"work_orders": [{"trade": "electrical", "urgency": "emergency", "unit": "4D", "safety_flag": True, "access_window": "anytime"}]}
    verified = verify_case("L01", "Dana Okafor", "2026-11-19", corrected=fixed)
    assert verified["expected"] == fixed and verified["corrected"]
    verify_case("L02", "Dana Okafor", "2026-11-19")
    assert len(load_golden()) == 32 and [case["id"] for case in waiting_for_a_person()] == ["L03"]


def test_verifying_needs_a_name_and_a_real_case():
    with pytest.raises(ValueError):
        verify_case("L01", "  ", "2026-11-19")
    with pytest.raises(KeyError):
        verify_case("nope", "Dana", "2026-11-19")
