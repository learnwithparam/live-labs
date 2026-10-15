"""The lab's promise, against a real model: receipts match their card charges with no person, never
the wrong charge, and the one paid with a personal card waits for a person."""

from pathlib import Path

import pytest

from receipts.cli import client_and_model, receipts_in
from receipts.extract import read_receipt
from receipts.match import load_charges, settle

LAB = Path(__file__).parent.parent
# The answer key: which charge each receipt pays for. pharmacy.jpg was paid with a personal card.
PAYS = {
    "bangalore-cafe.jpg": "t07",
    "berlin-restaurant.jpg": "t06",
    "coffee.jpg": "t03",
    "grocery-coupon.jpg": "t09",
    "hardware.pdf": "t01",
    "hotel.pdf": "t02",
    "pharmacy.jpg": None,
    "taxi.txt": "t08",
}
MIN_MATCHED = 6  # of the 7 receipts the card paid for


@pytest.mark.proof
def test_receipts_match_their_card_charges_and_never_the_wrong_one():
    client, model = client_and_model()
    charges, taken, got = load_charges(LAB / "data/card-transactions.csv"), set(), {}
    paths = receipts_in(LAB / "data/receipts")
    assert {path.name for path in paths} == set(PAYS)
    for path in paths:
        charge = settle(read_receipt(path), client, model, charges, taken).match.charge
        got[path.name] = charge.id if charge else None
    wrong = {name: id for name, id in got.items() if id and id != PAYS[name]}
    matched = sum(1 for name, id in got.items() if id and id == PAYS[name])
    assert not wrong, f"{model} matched the wrong charge: {wrong}"
    assert matched >= MIN_MATCHED, f"{model} matched {matched} of 7: {got}"
