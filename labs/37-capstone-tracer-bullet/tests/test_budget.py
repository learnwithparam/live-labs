"""The signed numbers are pinned: changing one without the others fails here, not in production."""

from brickhaven_slice.config import COMPLETION_THRESHOLD, P95_TARGET_MS, STEP_BUDGET_MS, TOTAL_BUDGET_MS


def test_the_step_budgets_are_the_rfc_numbers_and_add_up_inside_the_target():
    assert STEP_BUDGET_MS == {"intake": 200, "extraction": 3500, "vendor_match": 600, "drafting": 2500, "sending": 400}
    assert sum(STEP_BUDGET_MS.values()) == TOTAL_BUDGET_MS == 7200
    assert P95_TARGET_MS == 8000 and TOTAL_BUDGET_MS <= P95_TARGET_MS


def test_the_completion_threshold_leaves_room_for_one_slip_only():
    from brickhaven_slice.vendors import load_messages

    assert len(load_messages()) == 16
    assert COMPLETION_THRESHOLD == 0.8 and 14 / 16 >= COMPLETION_THRESHOLD > 12 / 16
