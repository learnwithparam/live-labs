"""The agent's one model call, reading a contract into pricing terms, and the code that refuses to trust it.

The model reads free-text contracts that no parser handles well. Code then checks every number against a
verbatim quote and computes the invoice itself, so a wrong reading is held for finance, never billed.
"""

import json
import re

from openai import OpenAI

from meterline.config import PRICES, UNATTENDED_LIMIT

READ_INSTRUCTIONS = (
    "Read the pricing section of a customer contract for a usage-billed API. Report the monthly platform fee, "
    "the API calls included each month, the overage price per 1,000 calls, the overage discount percent (0 if "
    "none), and the monthly cap on total charges. Use null for any term the text does not state as a number. "
    "For every number you report, copy the sentence that states it into quotes, word for word."
)

TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "monthly_fee": {"type": ["number", "null"]},
        "included_calls": {"type": ["integer", "null"]},
        "overage_per_1000": {"type": ["number", "null"]},
        "overage_discount_percent": {"type": ["number", "null"]},
        "monthly_cap": {"type": ["number", "null"]},
        "quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["monthly_fee", "included_calls", "overage_per_1000", "overage_discount_percent", "monthly_cap", "quotes"],
    "additionalProperties": False,
}
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def model_client(base_url: str, api_key: str) -> OpenAI:
    # One retry inside the client only: the durable step owns the real retry budget and its backoff.
    return OpenAI(base_url=base_url, api_key=api_key, max_retries=1, timeout=60)


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
            "cost": float(billed) if billed is not None else listed, "billed": billed is not None}


def read_contract(client: OpenAI, model: str, contract: str) -> tuple[str, dict]:
    """One paid model call. Returns the raw reply text, unparsed, so validation sees exactly what came back."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": READ_INSTRUCTIONS}, {"role": "user", "content": contract}],
        response_format={"type": "json_schema", "json_schema": {"name": "contract_terms", "strict": True, "schema": TERMS_SCHEMA}},
        extra_body={"usage": {"include": True}},
    )
    return response.choices[0].message.content or "", usage_of(response, model)


def numbers_in(text: str) -> set[float]:
    return {float(found.replace(",", "")) for found in NUMBER.findall(text)}


def check_terms(reply: str, contract: str, api_calls: int) -> tuple[dict | None, list[str]]:
    """Parse and test the model's reading. Any problem returned here sends the job to finance, unwritten."""
    try:
        terms = json.loads(reply)
    except json.JSONDecodeError:
        return None, ["The model's reply is not JSON."]
    if not isinstance(terms, dict) or set(terms) != set(TERMS_SCHEMA["properties"]):
        return None, ["The model's reply does not have the contract terms fields."]
    problems = []
    quotes = [quote for quote in terms["quotes"] if isinstance(quote, str)]
    # A quote must be in the contract word for word, or it is the model's paraphrase, not evidence.
    invented = [quote for quote in quotes if " ".join(quote.split()) not in " ".join(contract.split())]
    problems += [f"Quote not found in the contract: {quote[:90]}" for quote in invented]
    quoted_numbers = numbers_in(" ".join(quote for quote in quotes if quote not in invented))
    for field in ("monthly_fee", "included_calls", "overage_per_1000", "monthly_cap"):
        value = terms[field]
        if value is not None and float(value) not in quoted_numbers:
            problems.append(f"{field} = {value} is not stated in any quote from the contract.")
    discount = terms["overage_discount_percent"] or 0
    if discount and float(discount) not in quoted_numbers:
        problems.append(f"overage_discount_percent = {discount} is not stated in any quote from the contract.")
    if terms["monthly_fee"] is None or terms["included_calls"] is None:
        problems.append("The contract's platform fee or included calls could not be read.")
    elif api_calls > terms["included_calls"] and terms["overage_per_1000"] is None:
        problems.append("The customer used more than the included calls, and the contract text states no overage price.")
    return terms, problems


def expected_total(terms: dict, api_calls: int) -> float:
    """The invoice the contract implies, computed in code: the model reads terms, it never does the sum."""
    overage_calls = max(0, api_calls - terms["included_calls"])
    overage = overage_calls / 1000 * (terms["overage_per_1000"] or 0)
    total = terms["monthly_fee"] + overage * (1 - (terms["overage_discount_percent"] or 0) / 100)
    if terms["monthly_cap"] is not None:
        total = min(total, terms["monthly_cap"])
    return round(total, 2)


def decide(reply: str, job: dict) -> dict:
    """Write, hold or leave alone. Every path that is not plainly safe ends in a hold with its reason."""
    terms, problems = check_terms(reply, job["contract"], job["api_calls"])
    draft = job["draft_total"]
    if problems:
        return {"action": "hold", "reasons": problems, "terms": terms, "proposed_total": draft, "adjustment": 0.0}
    total = expected_total(terms, job["api_calls"])
    adjustment = round(total - draft, 2)
    if abs(adjustment) > UNATTENDED_LIMIT:
        reason = (f"The contract implies {total:,.2f}, a change of {adjustment:+,.2f} to the draft. "
                  f"Changes over {UNATTENDED_LIMIT:,.0f} wait for finance.")
        return {"action": "hold", "reasons": [reason], "terms": terms, "proposed_total": total, "adjustment": adjustment}
    # A cent or two is rounding in the billing system, not an error worth a correction.
    if abs(adjustment) < 0.05:
        return {"action": "write", "reasons": [], "terms": terms, "proposed_total": draft, "adjustment": 0.0}
    return {"action": "write", "reasons": [], "terms": terms, "proposed_total": total, "adjustment": adjustment}
