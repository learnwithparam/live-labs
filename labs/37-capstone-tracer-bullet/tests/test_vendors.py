from brickhaven_slice.vendors import load_messages, load_units, load_vendors, match_vendor


def order(trade, urgency="normal", safety=False):
    return {"trade": trade, "urgency": urgency, "unit": "x", "safety_flag": safety, "access_window": None}


def test_the_best_rated_vendor_for_the_building_wins():
    vendor, reason = match_vendor(order("electrical"), "Alder House", load_vendors())
    assert vendor["id"] == "volt-co" and reason == "matched"
    assert match_vendor(order("electrical"), "Cedar House", load_vendors())[0]["id"] == "spark-bros"


def test_a_trade_nobody_covers_says_so():
    assert match_vendor(order("pest"), "Birch House", load_vendors()) == (None, "no vendor covers pest")


def test_an_emergency_needs_a_vendor_who_takes_emergency_calls():
    vendor, reason = match_vendor(order("general", safety=True), "Alder House", load_vendors())
    assert vendor is None and "emergency" in reason
    assert match_vendor(order("general"), "Alder House", load_vendors())[0]["id"] == "fix-it-all"


def test_every_unit_in_a_message_is_a_known_flat():
    units = load_units()
    assert len(load_messages()) == 16 and {"4B", "8A", "2C"} <= set(units)
