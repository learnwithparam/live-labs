"""The reader's guards: the schema names only priced panels, boxes are checked, and free text is parsed."""

import asyncio

from saltmarsh.claims import claim, rates
from saltmarsh.reader import READING, clean_box, parse_amount, read_claim, read_photo, same_vehicle

from fakes import FakeModel


def test_the_schema_offers_exactly_the_panels_the_rate_table_prices():
    panel = READING["properties"]["findings"]["items"]["properties"]["panel"]
    assert set(panel["enum"]) == set(rates()["panels"])


def test_a_box_is_clamped_and_ordered_or_dropped():
    assert clean_box([600, 700, 100, 1200]) == [100, 700, 600, 1000]
    assert clean_box([5, 5, 5, 9]) is None
    assert clean_box([1, 2, 3]) is None


def test_free_text_gives_the_last_amount_or_the_middle_of_the_last_range():
    assert parse_amount("About £550. If the bracket is broken, £900–£1,300.") == 1100
    assert parse_amount("Expect £1,800 in total.") == 1800
    assert parse_amount("Hard to say without an inspection.") is None


def test_an_unusable_photo_keeps_no_findings_and_the_request_carries_the_photo():
    model = FakeModel()
    reading = asyncio.run(read_photo(model, "test-model", claim("SM-1049"), "P10"))
    assert reading["usable"] is False and reading["findings"] == []
    request = model.requests[0]
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "Customer's account" in request["messages"][1]["content"][0]["text"]


def test_a_claim_with_one_photo_skips_the_vehicle_check():
    model = FakeModel()
    check = asyncio.run(same_vehicle(model, "test-model", claim("SM-1042")))
    assert check["same_vehicle"] and model.requests == []


def test_a_claims_photos_are_read_in_parallel_within_the_limit():
    model = FakeModel()
    result = asyncio.run(read_claim(model, "test-model", claim("SM-1050"), asyncio.Semaphore(2)))
    assert [reading["photo"] for reading in result["readings"]] == ["P11", "P12"]
    assert result["vehicle_check"]["same_vehicle"] is False
    assert model.most_in_flight == 2
