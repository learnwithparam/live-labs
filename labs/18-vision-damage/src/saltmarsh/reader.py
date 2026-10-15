"""Read claim photos with a vision model, three ways.

`read_photo` is the structured reader: one photo in, a JSON reading out that a strict schema shapes,
so every panel name is one the rate table prices. `same_vehicle` shows the model every photo of a claim
at once, because only then can it see that two photos are of two different cars. `free_text_estimate`
is the naive version most teams write first: photos in, a paragraph and a number out.
"""

import asyncio
import base64
import json
import re

from saltmarsh.claims import photo_path, rates
from saltmarsh.config import PARALLEL, PRICES

PANELS = list(rates()["panels"])
DAMAGE = ["dent", "scratch", "crack", "crush", "missing", "rust"]

# The schema is the contract between the model and the pricing code. Enums stop "N/S front wing" or
# "fender" from reaching a rate table keyed on `front_left_wing`, and strict mode makes the provider
# refuse any output that does not fit, instead of the code finding out later.
READING = {
    "type": "object", "additionalProperties": False,
    "required": ["vehicle", "usable", "quality_problem", "findings"],
    "properties": {
        "vehicle": {"type": "object", "additionalProperties": False, "required": ["make", "colour", "view"],
                    "properties": {"make": {"type": "string", "description": "Badge or shape; 'unknown' if neither shows."},
                                   "colour": {"type": "string"},
                                   "view": {"type": "string", "description": "Which side of the car faces the camera."}}},
        "usable": {"type": "boolean", "description": "False when glare, darkness, blur or distance hides the area the customer reports."},
        "quality_problem": {"type": "string", "description": "What makes the photo unusable, or an empty string."},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["panel", "damage", "severity", "action", "prior_damage", "box"],
            "properties": {
                "panel": {"type": "string", "enum": PANELS},
                "damage": {"type": "string", "enum": DAMAGE},
                "severity": {"type": "string", "enum": ["minor", "moderate", "severe"]},
                "action": {"type": "string", "enum": ["repair", "replace"]},
                "prior_damage": {"type": "boolean", "description": "True when the damage looks older than this incident."},
                "box": {"type": "array", "items": {"type": "integer"},
                        "description": "[left, top, right, bottom] around the damage, each 0 to 1000 of the photo's width or height."}}}},
    },
}

READ_PHOTO = """Read one photo a customer sent with a motor insurance claim. Report only damage you can see.

Left and right are the car's own sides, as the driver sits. When the front of the car points to the
right of the photo, the camera sees the car's right side; when it points left, the left side.
Seen head on, the car's left headlight is on the right of the photo.

Name every damaged panel, not only the worst one: the panels around an impact are usually hit too.
Choose `replace` when a panel is torn, split, crushed, sharply creased, cracked through or missing, and
`repair` for dents and scratches a body shop can pull out or paint. Rust, faded paint inside a scrape, or old filler is older
than this incident: set prior_damage for that finding.

Set usable to false only when the area the customer describes cannot be judged from this photo."""

SAME_VEHICLE = {
    "type": "object", "additionalProperties": False, "required": ["same_vehicle", "reason"],
    "properties": {"same_vehicle": {"type": "boolean"},
                   "reason": {"type": "string", "description": "One sentence naming what matches or differs: badge, plate, colour, body shape."}},
}

COMPARE = """These photos were sent with one motor insurance claim for the vehicle below. Do they all show
that same vehicle? Compare the badge, number plate, colour and body shape across photos."""

FREE_TEXT = "Here are the photos for a motor insurance claim. Estimate the cost to repair the damage in pounds."


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed}


def add_usage(total: dict, more: dict) -> dict:
    for key, value in more.items():
        total[key] = round(total.get(key, 0) + value, 8)
    return total


def image_part(photo_id: str) -> dict:
    """A photo as a data URL: the model reads the bytes we hold, not a link it might fail to fetch."""
    encoded = base64.b64encode(photo_path(photo_id).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def describe(claim: dict) -> str:
    vehicle = claim["vehicle"]
    return (f"Vehicle on the policy: {vehicle['year']} {vehicle['make']} {vehicle['model']}, {vehicle['colour']}, "
            f"registration {vehicle['reg']}.\nCustomer's account: {claim['account']}")


def schema_format(name: str, schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def clean_box(box: list[int]) -> list[int] | None:
    """Keep a box only when it has four numbers that make a rectangle; the page draws nothing otherwise."""
    if len(box) != 4:
        return None
    left, top, right, bottom = (min(max(int(value), 0), 1000) for value in box)
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    return [left, top, right, bottom] if right > left and bottom > top else None


async def read_photo(client, model: str, claim: dict, photo_id: str) -> dict:
    """One photo to one structured reading, with the call's tokens and cost."""
    response = await client.chat.completions.create(
        model=model, response_format=schema_format("photo_reading", READING),
        extra_body={"usage": {"include": True}},
        messages=[{"role": "system", "content": READ_PHOTO},
                  {"role": "user", "content": [{"type": "text", "text": describe(claim)}, image_part(photo_id)]}])
    reading = json.loads(response.choices[0].message.content)
    # The panel list is enforced by the schema; the box is only shaped by it, so code checks the numbers.
    for finding in reading["findings"]:
        finding["box"] = clean_box(finding["box"])
    if not reading["usable"]:
        reading["findings"] = []
    return {"photo": photo_id, **reading, "usage": usage_of(response, model)}


async def same_vehicle(client, model: str, claim: dict) -> dict:
    """Every photo of a claim in one call. A single-photo claim has nothing to disagree with."""
    if len(claim["photos"]) < 2:
        return {"same_vehicle": True, "reason": "One photo.", "usage": {}}
    response = await client.chat.completions.create(
        model=model, response_format=schema_format("same_vehicle", SAME_VEHICLE),
        extra_body={"usage": {"include": True}},
        messages=[{"role": "system", "content": COMPARE},
                  {"role": "user", "content": [{"type": "text", "text": describe(claim)},
                                               *(image_part(photo_id) for photo_id in claim["photos"])]}])
    return {**json.loads(response.choices[0].message.content), "usage": usage_of(response, model)}


async def read_claim(client, model: str, claim: dict, limit: asyncio.Semaphore | None = None) -> dict:
    """All of a claim's photos, read in parallel, plus the cross-photo check."""
    limit = limit or asyncio.Semaphore(PARALLEL)

    async def bounded(call):
        async with limit:
            return await call

    readings, vehicle_check = await asyncio.gather(
        asyncio.gather(*(bounded(read_photo(client, model, claim, photo_id)) for photo_id in claim["photos"])),
        bounded(same_vehicle(client, model, claim)))
    return {"readings": list(readings), "vehicle_check": vehicle_check}


AMOUNT = re.compile(r"£\s?(\d[\d,]*(?:\.\d+)?)(?:\s*(?:-|–|to)\s*£?\s?(\d[\d,]*(?:\.\d+)?))?")


def parse_amount(text: str) -> float | None:
    """The figure a person would take from a free-text estimate: the last pound amount, or the middle of
    the last range. This guesswork is the cost of not asking for structure."""
    matches = AMOUNT.findall(text)
    if not matches:
        return None
    low, high = matches[-1]
    low_value = float(low.replace(",", ""))
    return (low_value + float(high.replace(",", ""))) / 2 if high else low_value


async def free_text_estimate(client, model: str, claim: dict) -> dict:
    """The naive version: the same photos, no schema, no rate table, and a number scraped from prose."""
    response = await client.chat.completions.create(
        model=model, extra_body={"usage": {"include": True}},
        messages=[{"role": "user", "content": [{"type": "text", "text": f"{FREE_TEXT}\n\n{describe(claim)}"},
                                               *(image_part(photo_id) for photo_id in claim["photos"])]}])
    text = response.choices[0].message.content or ""
    return {"claim": claim["id"], "text": text, "amount": parse_amount(text), "usage": usage_of(response, model)}
