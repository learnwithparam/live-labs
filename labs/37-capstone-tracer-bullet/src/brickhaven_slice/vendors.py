"""The two systems of record the slice reads: the flats and the vendors. Both are plain JSON files."""

import json

from brickhaven_slice.config import DATA


def load_units() -> dict[str, dict]:
    return {row["unit"]: row for row in json.loads((DATA / "units.json").read_text())}


def load_vendors() -> list[dict]:
    return json.loads((DATA / "vendors.json").read_text())


def load_messages() -> list[dict]:
    return [json.loads(line) for line in (DATA / "messages.jsonl").read_text().splitlines() if line.strip()]


def match_vendor(order: dict, building: str, vendors: list[dict]) -> tuple[dict | None, str]:
    """The best-rated vendor for the trade in this building, and the reason when there is none.

    An emergency or a safety flag needs a vendor who takes emergency calls; a routine job does not. A
    reason is returned for every miss, because the human queue shows it to the coordinator verbatim.
    """
    urgent = order["urgency"] == "emergency" or order["safety_flag"]
    covering = [vendor for vendor in vendors if order["trade"] in vendor["trades"]]
    if not covering:
        return None, f"no vendor covers {order['trade']}"
    in_building = [vendor for vendor in covering if building in vendor["buildings"]]
    if not in_building:
        return None, f"no {order['trade']} vendor covers {building}"
    capable = [vendor for vendor in in_building if vendor["emergency"] or not urgent]
    if not capable:
        return None, f"no {order['trade']} vendor in {building} takes emergency calls"
    return max(capable, key=lambda vendor: vendor["rating"]), "matched"
