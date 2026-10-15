"""Price the damage in code, from the repairer's rate table.

The model is good at seeing a crushed wing and bad at knowing what a wing costs at this repairer this
month. So the model names the panel, the damage and repair or replace; this module turns that into
pounds. The same findings always price the same, and a rate change is one edit to `data/rates.json`.
"""

SEVERITY_ORDER = {"minor": 0, "moderate": 1, "severe": 2}


def merge(readings: list[dict]) -> list[dict]:
    """One finding per panel across a claim's photos, keeping the worst one.

    Two photos of the same wing are one repair, not two. The worse reading wins because a repairer who
    finds less damage than estimated bills less, while one who finds more sends a supplement.
    """
    worst: dict[str, dict] = {}
    for reading in readings:
        if not reading["usable"]:
            continue
        for finding in reading["findings"]:
            seen = worst.get(finding["panel"])
            finding = {**finding, "photos": [reading["photo"]]}
            if seen is None:
                worst[finding["panel"]] = finding
                continue
            # Replace outranks repair, then severity breaks the tie.
            rank = (finding["action"] == "replace", SEVERITY_ORDER[finding["severity"]])
            seen_rank = (seen["action"] == "replace", SEVERITY_ORDER[seen["severity"]])
            photos = seen["photos"] + finding["photos"]
            worst[finding["panel"]] = {**(finding if rank > seen_rank else seen), "photos": photos}
    return list(worst.values())


def line(finding: dict, size: str, rates: dict) -> dict:
    """One estimate line: parts, labour hours and paint for one panel."""
    panel = rates["panels"][finding["panel"]]
    # Lamps, glass, grilles and wheels are never repaired, whatever the photo reading says.
    action = finding["action"] if panel["repairable"] else "replace"
    labour_rate, paint = rates["labour_rate"], rates["paint"]
    if action == "replace":
        parts, hours = float(panel["part"][size]), panel["fit_hours"]
    else:
        parts, hours = 0.0, rates["repair_hours"][finding["severity"]]
    paint_cost = paint["materials"] + paint["hours"] * labour_rate if panel["painted"] else 0.0
    total = round(parts + hours * labour_rate + paint_cost, 2)
    return {"panel": finding["panel"], "action": action, "severity": finding["severity"], "parts": parts,
            "labour_hours": hours, "labour": round(hours * labour_rate, 2), "paint": round(paint_cost, 2),
            "total": total, "overridden": action != finding["action"]}


def price(findings: list[dict], vehicle: dict, rates: dict) -> dict:
    """The itemised estimate for a claim, and how it compares with the car's market value."""
    lines = [line(finding, vehicle["size"], rates) for finding in findings]
    total = round(sum(each["total"] for each in lines), 2)
    return {"lines": lines, "total": total, "share_of_value": round(total / vehicle["value"], 3)}
