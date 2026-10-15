"""The naive baseline: ask a language model for a fraud score from 0 to 100.

It is the first thing many teams try, and it sounds reasonable: the model reads the refund and knows
what fraud looks like in general. What it does not know is this marketplace's history, so it cannot
learn which signals matter here, and nothing ties its 70 to "fraud 7 times in 10".
"""

import asyncio
import json
import random

from fennick.config import PARALLEL, PRICES

BATCH = 25  # refunds per call: enough to share the instructions, few enough to keep every id
SAMPLE = 1000  # test-window refunds the model scores; the decision model scores all of them anyway
INSTRUCTIONS = (
    "Fennick Market is an online marketplace. Each line below is one refund request from a buyer, with "
    "what the marketplace knows when the request arrives. For each refund, estimate the chance from 0 "
    "to 100 that it will turn out to be fraud (a chargeback, a carrier's proof of delivery, or a seller "
    "dispute that shows the buyer had the item). Use the whole range; most refunds are honest. Return "
    "every refund_id exactly once.")
SCHEMA = {
    "name": "refund_scores", "strict": True,
    "schema": {"type": "object", "additionalProperties": False, "required": ["scores"], "properties": {
        "scores": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["refund_id", "fraud_chance"],
            "properties": {"refund_id": {"type": "string"}, "fraud_chance": {"type": "integer"}}}}}},
}
SHOWN = ["refund_id", "category", "item", "amount", "reason", "delivery_scan", "days_since_delivery",
         "return_scanned", "account_age_days", "orders_lifetime", "refunds_30d", "address_changed_7d",
         "card_country_match"]


def sample(rows: list[dict], size: int = SAMPLE, seed: int = 19) -> list[dict]:
    """A seeded random sample of the test window, so every run scores the same refunds."""
    picked = random.Random(seed).sample(range(len(rows)), min(size, len(rows)))
    return [rows[number] for number in sorted(picked)]


def describe(row: dict) -> str:
    # The model sees exactly what the decision model sees, never the outcome or when it was found.
    return "; ".join(f"{name}={row[name] or 'n/a'}" for name in SHOWN)


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


async def score_batch(client, model: str, batch: list[dict]) -> tuple[dict[str, int], dict]:
    response = await client.chat.completions.create(
        model=model, response_format={"type": "json_schema", "json_schema": SCHEMA},
        extra_body={"usage": {"include": True}},
        messages=[{"role": "user", "content": INSTRUCTIONS + "\n\n" + "\n".join(map(describe, batch))}])
    answered = json.loads(response.choices[0].message.content)["scores"]
    wanted = {row["refund_id"] for row in batch}
    # Clamp to 0..100 and drop ids the model invented; a missing id is scored later as unknown.
    scores = {item["refund_id"]: max(0, min(100, int(item["fraud_chance"]))) for item in answered
              if item["refund_id"] in wanted}
    return scores, usage_of(response, model)


async def score_all(client, model: str, rows: list[dict], on_batch=lambda done, total: None) -> dict:
    """Score every refund in batches, a few calls at a time. A refund the model skipped gets 50: the
    model said nothing, so it is ranked as a coin toss rather than as safe."""
    gate = asyncio.Semaphore(PARALLEL)
    batches = [rows[start:start + BATCH] for start in range(0, len(rows), BATCH)]
    done = 0

    async def one(batch):
        nonlocal done
        async with gate:
            result = await score_batch(client, model, batch)
        done += 1
        on_batch(done, len(batches))
        return result

    results = await asyncio.gather(*(one(batch) for batch in batches))
    scores = {refund_id: chance for found, _ in results for refund_id, chance in found.items()}
    missing = [row["refund_id"] for row in rows if row["refund_id"] not in scores]
    usage = {key: sum(call[key] for _, call in results) for key in ("prompt_tokens", "completion_tokens", "cost")}
    return {"model": model, "scores": {row["refund_id"]: scores.get(row["refund_id"], 50) for row in rows},
            "missing": missing, "calls": len(batches), "usage": usage}
