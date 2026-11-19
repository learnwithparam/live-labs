"""The signed numbers, the model slots and the files the lab ships, each pinned so a drift shows."""

import json

from brickhaven_api.config import DATA, FALLBACK_MODEL, P95_TARGET_MS, PRICES, PRIMARY_MODEL, STEP_BUDGETS_MS
from brickhaven_api.keys import ClientKeys, hash_key
from brickhaven_api.loadtest import demo_keys, messages
from brickhaven_api.workorder import WorkOrderError, parse_work_order

import pytest


def test_the_architecture_rfc_numbers_are_the_ones_signed():
    # Copied from the signed RFC: a p95 of 8000 ms and step budgets that add up to 7200 ms.
    assert P95_TARGET_MS == 8000
    assert STEP_BUDGETS_MS == {"intake": 200, "extraction": 3500, "vendor_match": 600, "drafting": 2500, "sending": 400}
    assert sum(STEP_BUDGETS_MS.values()) == 7200 <= P95_TARGET_MS


def test_both_model_slots_have_a_price():
    assert PRIMARY_MODEL != FALLBACK_MODEL and set(PRICES) == {PRIMARY_MODEL, FALLBACK_MODEL}


def test_every_client_has_one_key_and_a_stored_hash_that_matches_it():
    clients = ClientKeys().clients
    assert {client.name for client in clients} == set(demo_keys())
    for client in clients:
        assert client.key_hash == hash_key(demo_keys()[client.name]) and 0 < client.burst <= client.per_minute


def test_the_runaway_client_has_the_smallest_limit():
    limits = {client.name: client.per_minute for client in ClientKeys().clients}
    assert min(limits, key=limits.get) == "runaway-sync"


def test_the_messages_are_twelve_distinct_texts():
    assert len(messages()) == 12 == len(set(messages()))
    assert all(json.loads(line)["id"] for line in (DATA / "messages.jsonl").read_text().splitlines())


def test_a_work_order_is_read_from_the_answer_and_a_bad_one_is_refused():
    order = parse_work_order('Here: {"trade": "heating", "urgency": "urgent", "unit": "7A", "safety_flag": false, "access_window": "after 6pm"}')
    assert order.trade == "heating" and order.unit == "7A"
    for bad in ["no json", '{"trade": "magic", "urgency": "urgent", "safety_flag": false}', '{"trade": "pest", "urgency": "routine", "safety_flag": "no"}', "{oops}"]:
        with pytest.raises(WorkOrderError):
            parse_work_order(bad)
