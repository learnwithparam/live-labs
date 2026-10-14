"""Match a ledger entry to the card charge it pays for. The bank feed is the truth: the model never
sees it, so a match is a check the model cannot talk its way past.

Three signals must agree: the amount in the receipt's own currency (a tip added on the card machine
may raise it), the date (a charge posts up to a few days later), and the merchant name.
"""

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from receipts.schema import CENT, Receipt

CARD_CURRENCY = "USD"
POSTING_DAYS = range(-1, 6)  # the charge date minus the receipt date
MAX_TIP = 0.30  # a card-machine tip of up to 30% over the printed total


@dataclass
class Charge:
    id: str
    date: date
    descriptor: str
    amount: float
    original_amount: float | None
    original_currency: str | None

    def amount_in(self, currency: str) -> float | None:
        """What the bank says was paid, in the receipt's currency, or None when it does not say."""
        if currency == self.original_currency:
            return self.original_amount
        return self.amount if currency == CARD_CURRENCY and not self.original_currency else None


@dataclass
class Match:
    charge: Charge | None
    checks: dict[str, str]  # signal -> what was compared, for the desk
    reason: str  # why it matched, or why a person has to look


def load_charges(path: Path) -> list[Charge]:
    def money(value: str) -> float | None:
        return float(value) if value else None

    with path.open() as rows:
        return [
            Charge(r["id"], date.fromisoformat(r["date"]), r["descriptor"], float(r["amount"]), money(r["original_amount"]), r["original_currency"] or None)
            for r in csv.DictReader(rows)
        ]


def words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4}


def same_merchant(merchant: str, descriptor: str) -> bool:
    """A bank descriptor is the merchant's name cut short and upper-cased; one shared word is enough."""
    return any(a.startswith(b) or b.startswith(a) for a in words(merchant) for b in words(descriptor))


def tip_on(receipt: Receipt, charge: Charge) -> float | None:
    """The tip the card added over the printed total, 0 for an exact match, None when it cannot fit."""
    paid = charge.amount_in(receipt.currency)
    if paid is None:
        return None
    extra = round(paid - receipt.total, 2)
    if abs(extra) <= CENT:
        return 0.0
    return extra if 0 < extra <= receipt.total * MAX_TIP else None


def match(receipt: Receipt, charges: list[Charge], taken: set[str] = frozenset(), tips: bool = True) -> Match:
    """`tips=False` for an entry whose sum failed: only an exact amount can confirm its total."""
    fits = []
    for charge in charges:
        if charge.id in taken:
            continue
        tip = tip_on(receipt, charge)
        tip = None if tip and not tips else tip
        days = (charge.date - receipt.date).days
        if tip is not None and days in POSTING_DAYS and same_merchant(receipt.merchant, charge.descriptor):
            fits.append((tip > 0, abs(days), tip, charge))
    if not fits:
        first, last = (receipt.date + timedelta(days=d) for d in (POSTING_DAYS[0], POSTING_DAYS[-1]))
        return Match(None, {}, f"no card charge of {receipt.currency} {receipt.total:.2f} from {receipt.merchant} "
                     f"posted {first:%d %b} to {last:%d %b}: paid another way, or the extraction is wrong")
    fits.sort(key=lambda fit: fit[:2])
    if len(fits) > 1 and fits[0][:2] == fits[1][:2]:
        return Match(None, {}, f"two card charges fit equally: {fits[0][3].id} and {fits[1][3].id}")
    _, days, tip, charge = fits[0]
    paid = charge.amount_in(receipt.currency)
    checks = {
        "amount": f"{receipt.currency} {receipt.total:.2f} on the receipt, {paid:.2f} on the card",
        "date": f"{receipt.date} on the receipt, posted {charge.date}",
        "merchant": f"{receipt.merchant} / {charge.descriptor}",
    }
    reason = f"tip of {tip:.2f} added on the card" if tip else "amount, date and merchant agree"
    return Match(charge, checks, reason)


@dataclass
class Settled:
    """One receipt's outcome: matched to a charge, or waiting on a person, and what it cost."""

    extraction: "Extraction | None"
    failed: "ExtractionFailed | None"
    match: Match
    cost: float


def settle(receipt_input, client, model: str, charges: list[Charge], taken: set[str]) -> Settled:
    from receipts.extract import ExtractionFailed, extract

    try:
        result = extract(receipt_input, client, model)
    except ExtractionFailed as failed:
        found = confirmed_by_bank(failed.history[-1].reply, charges, taken)
        if found.charge:
            taken.add(found.charge.id)
        return Settled(None, failed, found, failed.cost)
    found = match(result.receipt, charges, taken)
    if found.charge:
        taken.add(found.charge.id)
    return Settled(result, None, found, result.cost)


def confirmed_by_bank(reply: str, charges: list[Charge], taken: set[str]) -> Match:
    """The lines never added up, but a total equal to a charge to the cent is one the bank confirms."""
    try:
        data = json.loads(reply)
        draft = Receipt.model_construct(**{**data, "date": date.fromisoformat(data["date"]), "total": float(data["total"])})
        found = match(draft, charges, taken, tips=False)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        found = Match(None, {}, "")
    if found.charge:
        return Match(found.charge, found.checks, "total confirmed by the bank; the lines need a look")
    return Match(None, {}, "the lines never added up and no charge confirms the total")
