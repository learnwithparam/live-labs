"""Cost per 1,000 and the break-even batch: the GPU pays per wake-up, the hosted model per item."""

import pytest

from corvane_gpu.economics import break_even_items, compare, gpu_cost_for, per_thousand, wake_cost

PRICE = 0.0002


def test_a_wake_up_bills_the_cold_start_the_work_and_the_idle_window():
    bill = wake_cost(PRICE, cold_start_seconds=60, busy_seconds=20, idle_seconds=30)
    assert bill["billed_seconds"] == 110
    assert bill["cost"] == pytest.approx(110 * PRICE)
    assert per_thousand(bill["cost"], 200) == pytest.approx(round(110 * PRICE / 200 * 1000, 4))


def test_break_even_is_the_first_batch_where_the_gpu_is_no_dearer():
    fixed, per_item, hosted = 90.0, 0.1, 0.0001
    items = break_even_items(fixed, per_item, PRICE, hosted)
    # 90 s x $0.0002 = $0.018 fixed; each item saves $0.0001 - $0.00002 = $0.00008, so 225 items.
    assert items == 225
    assert gpu_cost_for(items, fixed, per_item, PRICE) <= items * hosted + 1e-12
    assert gpu_cost_for(items - 1, fixed, per_item, PRICE) > (items - 1) * hosted


def test_the_gpu_never_wins_when_an_item_costs_more_in_seconds_than_in_tokens():
    assert break_even_items(90.0, 1.0, PRICE, 0.0001) is None


def test_compare_prices_both_sides_on_the_same_items():
    wave = wake_cost(PRICE, cold_start_seconds=60, busy_seconds=20, idle_seconds=30)
    result = compare(wave, golden_items=200, hosted_total_cost=0.02, hosted_items=200, price_per_second=PRICE)
    assert result["gpu_per_thousand"] == pytest.approx(110 * PRICE / 200 * 1000, abs=1e-4)
    assert result["hosted_per_thousand"] == 0.1
    assert result["fixed_seconds_per_wake"] == 90
    assert result["gpu_seconds_per_item"] == 0.1
    assert result["break_even_items"] == 225
    by_volume = {row["items"]: row for row in result["by_volume"]}
    # Below the break-even the hosted model is cheaper; above it the GPU is.
    assert by_volume[100]["hosted"] < by_volume[100]["gpu"]
    assert by_volume[1000]["gpu"] < by_volume[1000]["hosted"]
