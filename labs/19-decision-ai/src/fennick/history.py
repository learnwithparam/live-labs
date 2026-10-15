"""Fennick Market's refund history: seven months of refund requests, and what was found out later.

The rows are synthetic and seeded, so every learner gets the same file and the same numbers. They are
built the way refund abuse looks in practice: a small set of buyers files most of the fraud, often on
new accounts, often claiming a parcel never came when the carrier has proof it did. Honest buyers file
the same reasons too, so no single signal separates the two.
"""

import csv
from datetime import date, timedelta
from pathlib import Path

import numpy

HISTORY = Path("data/refunds.csv")
SEED = 19
FIRST_DAY, LAST_DAY = date(2026, 1, 5), date(2026, 7, 31)
HONEST_BUYERS, ABUSING_BUYERS = 9_400, 190

CATEGORIES = ["electronics", "apparel", "home", "beauty", "toys", "gift_cards"]
REASONS = ["not_received", "damaged", "not_as_described", "wrong_item", "changed_mind"]
SCANS = ["signed", "photo", "left_no_proof", "in_transit", "no_scan"]
ITEMS = {
    "electronics": [("Aurora wireless earbuds", 129), ("Kestrel 27in monitor", 289), ("Pocket projector", 219),
                    ("Smartwatch, steel band", 249), ("Noise-cancelling headphones", 199), ("USB-C dock", 89)],
    "apparel": [("Wool overshirt", 79), ("Trail running shoes", 119), ("Rain shell jacket", 149), ("Linen dress", 69)],
    "home": [("Cast iron casserole", 95), ("Stick vacuum", 179), ("Duvet set, king", 109), ("Espresso grinder", 159)],
    "beauty": [("Vitamin C serum", 38), ("Hair dryer, ionic", 129), ("Fragrance, 100ml", 92)],
    "toys": [("Wooden train set", 64), ("Building kit, castle", 119), ("Ride-on scooter", 89)],
    "gift_cards": [("Gift card", 100), ("Gift card", 200), ("Gift card", 50)],
}

# What each kind of request looks like. Abuse leans on reasons that need no parcel back, on electronics
# and gift cards that resell, and on parcels the carrier marked delivered. Honest requests overlap.
LOOKS = {
    True: {"category": [0.32, 0.17, 0.14, 0.15, 0.10, 0.12], "reason": [0.48, 0.22, 0.17, 0.08, 0.05],
           "scan_not_received": [0.30, 0.36, 0.18, 0.09, 0.07], "return_scanned": 0.38,
           "address_changed": 0.20, "card_country_match": 0.86},
    False: {"category": [0.18, 0.32, 0.22, 0.14, 0.12, 0.02], "reason": [0.22, 0.25, 0.20, 0.10, 0.23],
            "scan_not_received": [0.14, 0.28, 0.28, 0.18, 0.12], "return_scanned": 0.68,
            "address_changed": 0.06, "card_country_match": 0.95},
}
SCAN_AFTER_DELIVERY = [0.25, 0.55, 0.20, 0.0, 0.0]  # a parcel that arrived and is being sent back


def buyers(rng: numpy.random.Generator) -> list[dict]:
    """Every buyer who asked for a refund. Most abusers sign up shortly before they strike; some use
    accounts aged for months, which is why account age alone is a weak rule."""
    found = []
    for number in range(HONEST_BUYERS + ABUSING_BUYERS):
        abuser = number >= HONEST_BUYERS
        if abuser and rng.random() < 0.7:
            signed_up = FIRST_DAY + timedelta(days=int(rng.integers(-20, 190)))
            refunds = int(rng.integers(2, 7))
        else:
            signed_up = FIRST_DAY - timedelta(days=int(rng.integers(5, 1800)))
            refunds = int(rng.integers(2, 6)) if abuser else 1 + int(rng.poisson(0.35))
        found.append({"buyer_id": f"B-{40_000 + number}", "abuser": abuser, "signed_up": signed_up,
                      "refunds": refunds})
    return found


def request_days(rng: numpy.random.Generator, buyer: dict) -> list[date]:
    """When a buyer asks. Abusers ask in bursts a few days apart; honest buyers months apart."""
    start = max(buyer["signed_up"] + timedelta(days=1), FIRST_DAY)
    span = (LAST_DAY - start).days
    if buyer["abuser"] and buyer["signed_up"] >= FIRST_DAY:
        span = min(span, 21)  # a fresh account strikes within weeks, before it draws attention
    if span < 1:
        return []
    first = start + timedelta(days=int(rng.integers(0, span)))
    gap = (2, 12) if buyer["abuser"] else (20, 120)
    days = [first]
    for _ in range(buyer["refunds"] - 1):
        days.append(days[-1] + timedelta(days=int(rng.integers(*gap))))
    return [day for day in days if day <= LAST_DAY]


def one_refund(rng: numpy.random.Generator, buyer: dict, day: date, fraud: bool) -> dict:
    looks = LOOKS[fraud]
    category = CATEGORIES[rng.choice(len(CATEGORIES), p=looks["category"])]
    item, list_price = ITEMS[category][rng.integers(len(ITEMS[category]))]
    reason = REASONS[rng.choice(len(REASONS), p=looks["reason"])]
    not_received = reason == "not_received"
    scan = SCANS[rng.choice(len(SCANS), p=looks["scan_not_received"] if not_received else SCAN_AFTER_DELIVERY)]
    delivered = scan in ("signed", "photo", "left_no_proof")
    quantity = 1 + int(rng.random() < (0.2 if fraud else 0.1))
    age = (day - buyer["signed_up"]).days
    return {
        "category": category, "item": item, "amount": round(list_price * quantity * rng.uniform(0.92, 1.0), 2),
        "reason": reason, "delivery_scan": scan,
        "days_since_delivery": int(rng.integers(0, 8 if fraud else 12)) if delivered else "",
        # A return scan only applies when the buyer is meant to send the item back.
        "return_scanned": "" if not_received else ("yes" if rng.random() < looks["return_scanned"] else "no"),
        "account_age_days": age,
        "orders_lifetime": 1 + int(rng.poisson(1.2 if buyer["abuser"] else 1 + age / 45)),
        "address_changed_7d": "yes" if rng.random() < looks["address_changed"] else "no",
        "card_country_match": "yes" if rng.random() < looks["card_country_match"] else "no",
    }


def outcome(rng: numpy.random.Generator, day: date, fraud: bool) -> tuple[str, str]:
    """What the merchant learned later. A chargeback lands weeks after the refund; a carrier's proof
    of delivery or a seller's dispute can take longer. Honest refunds end with nothing found."""
    if not fraud:
        return "nothing_found", ""
    found_by = ["chargeback", "carrier_proof", "seller_dispute"][rng.choice(3, p=[0.55, 0.30, 0.15])]
    return found_by, (day + timedelta(days=int(rng.integers(12, 70)))).isoformat()


def generate(seed: int = SEED) -> list[dict]:
    rng = numpy.random.default_rng(seed)
    rows = []
    for buyer in buyers(rng):
        for day in request_days(rng, buyer):
            # Abusers sometimes file a real claim; honest buyers rarely file a false one ("friendly fraud").
            fraud = rng.random() < (0.78 if buyer["abuser"] else 0.012)
            found_by, found_on = outcome(rng, day, fraud)
            rows.append({"buyer_id": buyer["buyer_id"], "requested_at": day.isoformat(),
                         **one_refund(rng, buyer, day, fraud), "found_later": found_by, "found_on": found_on})
    rows.sort(key=lambda row: (row["requested_at"], row["buyer_id"]))
    # Counted from the history itself, as a live system would: refunds this buyer asked for in the 30
    # days before this one.
    seen: dict[str, list[date]] = {}
    for number, row in enumerate(rows):
        day = date.fromisoformat(row["requested_at"])
        earlier = seen.setdefault(row["buyer_id"], [])
        row["refunds_30d"] = sum(1 for before in earlier if (day - before).days <= 30)
        earlier.append(day)
        row["refund_id"] = f"FM-{260_001 + number}"
    return rows


COLUMNS = ["refund_id", "requested_at", "buyer_id", "category", "item", "amount", "reason", "delivery_scan",
           "days_since_delivery", "return_scanned", "account_age_days", "orders_lifetime", "refunds_30d",
           "address_changed_7d", "card_country_match", "found_later", "found_on"]


def write(rows: list[dict], path: Path = HISTORY) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def load(path: Path = HISTORY) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def is_fraud(row: dict) -> bool:
    """The label: anything found later. The model never sees how or when it was found."""
    return row["found_later"] != "nothing_found"
