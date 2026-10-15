"""The burst against a fake endpoint over a real socket: a timeout is a failed item, never a skipped one."""

import time

from fakes import FakeEndpoint

from corvane_gpu.burst import ERROR, OK, TIMED_OUT, Endpoint, ask, send_burst
from corvane_gpu.golden import EXAMPLES, golden_set, read_jsonl

GOLDEN = golden_set()
EXAMPLE_ITEMS = read_jsonl(EXAMPLES)


def items_with(word: str, count: int) -> list[dict]:
    hanging = [item for item in GOLDEN if word in item["descriptor"]][:1]
    return hanging + [item for item in GOLDEN if word not in item["descriptor"]][: count - 1]


def test_every_item_comes_back_and_the_hang_is_a_timeout():
    items = items_with("FIGMA", 12)
    with FakeEndpoint(hang_on="FIGMA", hang_seconds=3.0) as fake:
        endpoint = Endpoint(fake.base_url, fake.api_key, "stand-in")
        outcomes = send_burst(endpoint, items, EXAMPLE_ITEMS, concurrency=4, timeout=0.5)
    assert [outcome.id for outcome in outcomes] == [item["id"] for item in items]
    hung = outcomes[0]
    assert hung.status == TIMED_OUT and hung.label is None and "no answer" in hung.error
    assert all(outcome.status == OK for outcome in outcomes[1:])


def test_a_rambling_answer_is_answered_but_not_a_category():
    item = next(item for item in GOLDEN if "MARRIOTT" in item["descriptor"])
    with FakeEndpoint(ramble_on="MARRIOTT") as fake:
        result = ask(Endpoint(fake.base_url, fake.api_key, "stand-in"), item, EXAMPLE_ITEMS, 2.0, time.time())
    assert result.status == OK and result.label is None and "hard to say" in result.answer


def test_the_endpoint_refuses_a_wrong_key():
    with FakeEndpoint() as fake:
        result = ask(Endpoint(fake.base_url, "wrong", "stand-in"), GOLDEN[0], EXAMPLE_ITEMS, 2.0, time.time())
    assert result.status == ERROR and "401" in result.error


def test_items_past_the_guard_are_failed_with_the_reason():
    with FakeEndpoint() as fake:
        endpoint = Endpoint(fake.base_url, fake.api_key, "stand-in")
        outcomes = send_burst(endpoint, GOLDEN[:5], EXAMPLE_ITEMS, 2, 2.0, deadline=time.time() - 1)
    assert len(outcomes) == 5
    assert all(outcome.status == TIMED_OUT and "time guard" in outcome.error for outcome in outcomes)
