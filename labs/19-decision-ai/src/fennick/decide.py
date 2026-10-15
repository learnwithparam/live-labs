"""From a risk score to an action: pay at once, hold, or send to a person.

Stripe Radar works the same way: a score, a block line, a review line, and rules a merchant writes on
top. The lines are business choices. The review line comes from how many refunds the team can look at;
the hold line from how sure the model must be before a buyer waits without a person deciding.
"""

import numpy

# Fennick's review team can look at 5 of every 100 refunds. That budget, not a score, sets the line.
REVIEW_RATE = 0.05
# A refund is held at once only when it is at least this likely to be fraud. The number means
# something only because the model is calibrated: of refunds scored 0.8, about 8 in 10 were fraud.
HOLD_AT = 0.80


def hand_rule(row: dict) -> float:
    """The rule Fennick's ops team ran before the model: one point per red flag, ties broken by amount.

    Every flag is sensible on its own. What it cannot weigh is evidence: "not received" with a signed
    delivery scan is a different claim from "not received" with a parcel still in transit.
    """
    flags = [int(row["account_age_days"]) < 30, int(row["refunds_30d"]) >= 2, float(row["amount"]) > 250,
             row["category"] in ("electronics", "gift_cards"), row["reason"] == "not_received"]
    return sum(flags) + float(row["amount"]) / 100_000


def line_for_rate(scores: numpy.ndarray, rate: float) -> float:
    """The score above which `rate` of these refunds fall. Picked on the calibration month, then
    applied to new refunds, so the real review rate drifts a little from the budget, as it does live."""
    return float(numpy.quantile(scores, 1 - rate))


def action(score: float, review_line: float, hold_at: float = HOLD_AT) -> str:
    if score >= hold_at:
        return "hold"
    return "review" if score >= review_line else "pay"


def signals(row: dict) -> list[str]:
    """The evidence a reviewer reads first, in the buyer's terms. These are facts from the refund, not
    the model's reasons: a tree ensemble has no single reason, so the page shows what a person checks."""
    found = []
    age, recent = int(row["account_age_days"]), int(row["refunds_30d"])
    if row["reason"] == "not_received" and row["delivery_scan"] in ("signed", "photo"):
        found.append(f"Claims not received; carrier has a {'signature' if row['delivery_scan'] == 'signed' else 'doorstep photo'}")
    if row["return_scanned"] == "no":
        found.append("No return scan for the item sent back")
    if age < 30:
        found.append(f"Account {age} day{'s' if age != 1 else ''} old")
    if recent >= 1:
        found.append(f"{recent} other refund{'s' if recent > 1 else ''} in the last 30 days")
    if row["address_changed_7d"] == "yes":
        found.append("Shipping address changed in the week before the order")
    if row["card_country_match"] == "no":
        found.append("Card issued in a different country from the shipping address")
    if row["category"] in ("electronics", "gift_cards"):
        found.append(f"{'Gift card' if row['category'] == 'gift_cards' else 'Electronics'}, easy to resell")
    return found


# What the person in the loop chooses between. A hold is not a refusal: the buyer is asked for proof.
REVIEW_CHOICES = {"pay": "Pay the refund", "hold": "Hold it and ask the buyer for proof"}
