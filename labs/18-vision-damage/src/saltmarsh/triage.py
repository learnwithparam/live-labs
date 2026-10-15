"""Decide which claims pay out on the estimate and which go to a human adjuster, and say why.

Every rule here is code reading the structured photo readings, not a model's judgement. That keeps the
routing auditable: an adjuster who asks "why did this land on my desk?" gets a named reason and the
evidence behind it, and a regulator asking the same question gets the same answer next year.
"""

REASONS = {
    "image_quality": "A photo cannot show the damage the customer reported.",
    "prior_damage": "Some damage looks older than this incident.",
    "total_loss": "The repair costs too much of the car's value to be worth doing.",
    "photos_disagree": "The photos may not all show the insured car.",
}


def covered(readings: list[dict]) -> list[dict]:
    """Readings with old damage taken out. The policy pays for this incident only, so old rust is not
    priced, but it still routes the claim: someone has to tell the customer why it was left off."""
    return [{**reading, "findings": [finding for finding in reading["findings"] if not finding["prior_damage"]]}
            for reading in readings]


def route(readings: list[dict], vehicle_check: dict, estimate: dict, rates: dict) -> dict:
    """Auto-approve, or send to an adjuster with every reason that applies.

    All reasons are collected, not just the first, because an adjuster who settles a total loss should
    also know the second photo showed a different car.
    """
    reasons = []
    for reading in readings:
        if not reading["usable"]:
            reasons.append({"code": "image_quality", "photo": reading["photo"],
                            "detail": reading["quality_problem"] or REASONS["image_quality"]})
        for finding in reading["findings"]:
            if finding["prior_damage"]:
                reasons.append({"code": "prior_damage", "photo": reading["photo"],
                                "detail": f"{finding['damage']} on {finding['panel'].replace('_', ' ')}"})
    if not estimate["lines"] and not reasons:
        # The customer reported damage and the photos show none: paying nothing is as wrong as paying
        # too much, so a person looks before the claim closes.
        reasons.append({"code": "image_quality", "photo": None, "detail": "no damage visible in the photos sent"})
    if estimate["share_of_value"] >= rates["total_loss_share"]:
        # Insurers write a car off well before repairs reach its full value: salvage, hire car and
        # hidden damage found once panels come off all push the real cost up.
        reasons.append({"code": "total_loss", "photo": None,
                        "detail": f"estimate is {estimate['share_of_value']:.0%} of the car's value"})
    if not vehicle_check["same_vehicle"]:
        reasons.append({"code": "photos_disagree", "photo": None, "detail": vehicle_check["reason"]})
    return {"to": "adjuster" if reasons else "auto", "reasons": reasons}
