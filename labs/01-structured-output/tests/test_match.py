"""The matcher against the card feed, no key: each trap a real feed holds, and the one with no charge."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from receipts.match import load_charges, match
from receipts.schema import Receipt

CHARGES = load_charges(Path(__file__).parent.parent / "data/card-transactions.csv")


def receipt(merchant: str, day: str, currency: str, total: float) -> Receipt:
    line = {"description": "all", "amount": total}
    return Receipt(merchant=merchant, date=date.fromisoformat(day), currency=currency, line_items=[line], total=total)


def test_a_foreign_receipt_matches_on_the_amount_the_bank_saw_in_its_currency():
    found = match(receipt("Gasthaus am Kanal", "2026-09-18", "EUR", 45.70), CHARGES)
    assert found.charge.id == "t06" and "posted 2026-09-19" in found.checks["date"]


def test_a_net_total_would_pass_as_a_tip_so_the_reconcile_rule_stops_it_first():
    # 45.70 paid, 38.40 net: on amount alone the 7.30 of VAT looks like a tip.
    assert match(receipt("Gasthaus am Kanal", "2026-09-18", "EUR", 38.40), CHARGES).reason == "tip of 7.30 added on the card"
    lines = [{"description": "2x Wiener Schnitzel", "amount": 37.00}, {"description": "drinks", "amount": 8.70}]
    with pytest.raises(ValidationError, match="add up to 45.70"):
        Receipt(merchant="Gasthaus am Kanal", date=date(2026, 9, 18), currency="EUR", line_items=lines, tax=7.30, tax_included=True, total=38.40)


def test_a_tip_added_on_the_card_machine_still_matches_and_says_so():
    found = match(receipt("London Black Cab", "2026-09-23", "GBP", 25.40), CHARGES)
    assert found.charge.id == "t08" and found.reason == "tip of 2.60 added on the card"


def test_two_coffees_the_same_morning_split_on_the_amount():
    assert match(receipt("Blue Door Coffee", "2026-09-14", "USD", 16.70), CHARGES).charge.id == "t03"
    assert match(receipt("Blue Door Coffee", "2026-09-14", "USD", 5.25), CHARGES).charge.id == "t04"


def test_the_same_amount_at_another_merchant_is_not_a_match():
    found = match(receipt("Blue Door Coffee", "2026-09-14", "USD", 16.70), CHARGES, taken={"t03"})
    assert found.charge is None


def test_a_hotel_folio_matches_the_charge_at_checkout():
    assert match(receipt("Harborview Hotel Seattle", "2026-09-10", "USD", 483.76), CHARGES).charge.id == "t02"


def test_a_receipt_paid_with_a_personal_card_needs_a_person():
    found = match(receipt("Corner Pharmacy #2231", "2026-10-01", "USD", 16.68), CHARGES)
    assert found.charge is None
    assert found.reason.startswith("no card charge of USD 16.68 from Corner Pharmacy #2231 posted 30 Sep to 06 Oct")


def test_two_equal_fits_go_to_a_person_instead_of_a_guess():
    twin = CHARGES[2].__class__("t99", CHARGES[2].date, CHARGES[2].descriptor, 16.70, None, None)
    found = match(receipt("Blue Door Coffee", "2026-09-14", "USD", 16.70), [*CHARGES, twin])
    assert found.charge is None and "t03 and t99" in found.reason


def test_a_total_the_bank_confirms_matches_even_when_the_lines_do_not_add_up():
    from receipts.match import confirmed_by_bank

    reply = '{"merchant": "Greenleaf Market", "date": "2026-09-27", "currency": "USD", "total": 11.60}'
    found = confirmed_by_bank(reply, CHARGES, set())
    assert found.charge.id == "t09" and found.reason.endswith("the lines need a look")


def test_a_failed_sum_never_matches_through_a_tip():
    from receipts.match import confirmed_by_bank

    reply = '{"merchant": "Gasthaus am Kanal", "date": "2026-09-18", "currency": "EUR", "total": 38.40}'
    assert confirmed_by_bank(reply, CHARGES, set()).charge is None
