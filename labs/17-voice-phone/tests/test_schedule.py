"""The booking system refuses what the office would refuse, so the agent cannot talk its way past it."""

from northfield.schedule import Schedule, changes, original

SANDRA, PRIYA, MARCUS, NEW_CALLER = "+16145550101", "+16145550112", "+16145550178", "+16145550163"


def test_each_call_gets_its_own_copy_of_the_week(tmp_path):
    first, second = Schedule(tmp_path / "one.json"), Schedule(tmp_path / "two.json")
    first.book(SANDRA, "tune-up", "WED-PM", "tune-up")
    assert second.window("WED-PM")["spots"] == original()["windows"][4]["spots"]


def test_booking_takes_a_spot_and_a_full_window_is_refused(tmp_path):
    book = Schedule(tmp_path / "schedule.json")
    assert book.book(SANDRA, "tune-up", "THU-AM", "tune-up")["ok"]
    assert book.window("THU-AM")["spots"] == 0
    refused = book.book(SANDRA, "repair", "THU-AM", "no heat")
    assert not refused["ok"] and "full" in refused["reason"]


def test_a_new_caller_needs_details_and_an_address_in_the_service_area(tmp_path):
    book = Schedule(tmp_path / "schedule.json")
    assert "needs a name" in book.book(NEW_CALLER, "estimate", "FRI-PM", "new furnace")["reason"]
    outside = book.book(NEW_CALLER, "estimate", "FRI-PM", "new furnace", "Ray Ortiz", "1 Main St, Dayton", "45402")
    assert "outside the service area" in outside["reason"]
    # Speech to text writes a zip like a number; the book still reads five digits.
    booked = book.book(NEW_CALLER, "estimate", "FRI-PM", "new furnace", "Ray Ortiz", "88 Elm St, Grove City", "43,123")
    assert booked["ok"] and changes(original(), book.book_of_record)["customers"][0]["zip"] == "43123"


def test_moving_returns_the_old_spot_and_cancelling_frees_the_window(tmp_path):
    book = Schedule(tmp_path / "schedule.json")
    assert book.move(PRIYA, "V-2043", "FRI-AM", "3360")["ok"]
    assert book.window("TUE-PM")["spots"] == 1 and book.window("FRI-AM")["spots"] == 1
    assert book.cancel(PRIYA, "V-2043", "3,360")["ok"]
    assert book.window("FRI-AM")["spots"] == 2
    assert not book.cancel(SANDRA, "V-2041", "92")["ok"]


def test_caller_id_alone_cannot_change_a_visit(tmp_path):
    """A spoofed number gets past caller ID; it does not know the house number on file."""
    book = Schedule(tmp_path / "schedule.json")
    for house_number in ("", "1208", "Wyandotte"):
        assert "house number" in book.move(MARCUS, "V-2041", "FRI-AM", house_number)["reason"]
        assert "house number" in book.cancel(MARCUS, "V-2041", house_number)["reason"]
    assert changes(original(), book.book_of_record)["visits"] == []
    assert "Wyandotte" not in book.caller_card(MARCUS)


def test_a_handoff_needs_a_known_reason(tmp_path):
    book = Schedule(tmp_path / "schedule.json")
    assert not book.hand_off(SANDRA, "bored", "wants to chat")["ok"]
    assert book.hand_off(SANDRA, "billing", "asks about a charge")["ok"]
    assert changes(original(), book.book_of_record)["handoffs"][0]["reason"] == "billing"
