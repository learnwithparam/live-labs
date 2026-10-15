"""The claim file: what the customer sent at first notice of loss, the repairer's rate table, and the
adjuster's confirmed findings.

Every file here was written by people, never by the model. That is what lets it grade the model: the
labels say which panels an adjuster confirmed on each photo, and the bills say what the repair cost.
"""

import json
from pathlib import Path

DATA = Path("data")


def load(name: str):
    return json.loads((DATA / name).read_text())


def claims() -> list[dict]:
    return load("claims.json")


def claim(claim_id: str) -> dict:
    found = next((each for each in claims() if each["id"] == claim_id), None)
    if not found:
        raise SystemExit(f"No claim {claim_id!r}. Claims run from SM-1041 to SM-1050.")
    return found


def rates() -> dict:
    return load("rates.json")


def truth() -> dict:
    """The adjuster's file: confirmed damage per photo, and per claim the outcome, the bill and the routing."""
    return load("adjuster.json")


def photo_path(photo_id: str) -> Path:
    return DATA / "photos" / f"{photo_id}.jpg"


def photo_ids() -> list[str]:
    return [photo_id for each in claims() for photo_id in each["photos"]]
