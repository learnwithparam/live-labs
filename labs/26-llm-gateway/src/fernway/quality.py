"""The labelled quality set: real-looking calls from four teams, each with a grader that is plain code.

The truth here was written by people who know the policy, not by a model. A route that saves money is
only a saving if these graders still pass, so every comparison in this lab is scored against them.
"""

import json
import re
from pathlib import Path

DATA = Path("data")

# What each team's app sends as the system message. Every task answers in JSON, so a grader can check
# a field instead of judging prose, and a cheap model cannot hide a wrong number behind nice wording.
INSTRUCTIONS = {
    "product_question": (
        "Answer a shopper's question about one product using only the product facts given. "
        'Reply with JSON only: {"answer": "yes" | "no" | "not stated", "reply": "<one sentence for the shopper>"}. '
        'Use "not stated" when the facts do not settle it. Where an allergy is involved, a "may contain" '
        "or shared-line warning means the answer is no."),
    "substitution": (
        "An item in a grocery order is out of stock. Pick one candidate to send instead. Match the product "
        "type and size first, brand second. Respect every customer note: diet, allergy, price limit, or "
        'no substitutes at all. Reply with JSON only: {"sku": "S1" | "S2" | "S3" | "none", "why": "<one sentence>"}. '
        'Use "none" when no candidate is acceptable.'),
    "care_reply": (
        "Apply Fernway's refund policy to one customer complaint. Policy: refund the price paid for each "
        "missing or damaged item, or the part of a weighed item that was not delivered. Perishables reported "
        "more than 48 hours after delivery get nothing. Delivery more than 30 minutes after the promised window "
        "earns a 5.00 credit; more than 60 minutes earns 10.00. If two or more items were missing or damaged, "
        "a goodwill credit of 10% of the refund applies, capped at 3.00. Credits never stack: give only the "
        "largest single credit that applies. action is refund if refund > 0, else credit if credit > 0, else "
        'no_action. Reply with JSON only: {"action": "refund" | "credit" | "no_action", "refund": <number>, '
        '"credit": <number>, "reply": "<two sentences to the customer>"}.'),
    "catalog_extraction": (
        "Turn one supplier listing into Fernway catalog fields. size is the amount in one unit, converted to "
        "grams or millilitres. pack_count is how many units a shopper gets in one purchase. organic is true "
        "only for a certified organic or bio product, never because a brand name contains the word. Reply "
        'with JSON only: {"brand": "<brand>", "size": <number>, "unit": "g" | "ml", "pack_count": <integer>, '
        '"organic": true | false}.'),
}


def items(path: Path = DATA / "quality.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def messages(item: dict) -> list[dict]:
    """The call exactly as the team's app makes it: its instruction, the facts, then the user's words."""
    return [{"role": "system", "content": INSTRUCTIONS[item["task"]]},
            {"role": "user", "content": f"{item['context']}\n\n{item['ask']}"}]


def parse_json(text: str) -> dict | None:
    # Models often wrap JSON in a code fence even when told not to. Take the outermost object.
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def same(expected, got) -> bool:
    if isinstance(expected, bool) or isinstance(got, bool):
        return expected is got
    if isinstance(expected, (int, float)):
        # Money and sizes are compared to the cent, so 6.75 and "6.750" both pass and 6.7 does not.
        try:
            return abs(float(got) - expected) < 0.005
        except (TypeError, ValueError):
            return False
    return str(got).strip().lower() == str(expected).strip().lower()


def grade(item: dict, reply: str | None) -> dict:
    """Pass or fail one reply against its label, with the fields that were wrong named for the console."""
    parsed = parse_json(reply or "")
    if parsed is None:
        return {"passed": False, "wrong": ["no JSON object in the reply"]}
    wrong = []
    for field, expected in item["expect"].items():
        got = parsed.get(field)
        # A list of acceptable answers, such as two substitutes that would both do.
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(same(option, got) for option in allowed):
            wrong.append(f"{field}: expected {expected!r}, got {got!r}")
    return {"passed": not wrong, "wrong": wrong}
