"""Turn the model's text into a checked work order, and say exactly what is wrong when it is not one.

A model asked for JSON still wraps it in a code fence, adds a sentence before it or leaves a trailing
comma. Throwing the whole request away for that wastes a good answer, so `parse_work_order` first
repairs what can be repaired without a model, and the pipeline spends a model call only when it cannot.
"""

import json
import re

TRADES = ("plumbing", "electrical", "heating", "locksmith", "appliance", "general", "pest")
URGENCIES = ("low", "normal", "high", "emergency")
FIELDS = ("trade", "urgency", "unit", "safety_flag", "access_window")


def strip_to_object(text: str) -> str:
    """Cut the text down to the outermost braces and drop trailing commas: the two repairs that need no model."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return text
    return re.sub(r",\s*([}\]])", r"\1", text[start:end + 1])


def check_work_order(order: object) -> list[str]:
    """Every way the object breaks the contract, in words the repair prompt and the human queue can show."""
    if not isinstance(order, dict):
        return ["the answer is not a JSON object"]
    problems = [f"missing {field}" for field in FIELDS if field not in order]
    if order.get("trade") not in TRADES and "trade" in order:
        problems.append(f"trade {order.get('trade')!r} is not one of {', '.join(TRADES)}")
    if order.get("urgency") not in URGENCIES and "urgency" in order:
        problems.append(f"urgency {order.get('urgency')!r} is not one of {', '.join(URGENCIES)}")
    if "safety_flag" in order and not isinstance(order["safety_flag"], bool):
        problems.append("safety_flag is not true or false")
    for field in ("unit", "access_window"):
        if order.get(field) is not None and not isinstance(order.get(field), str):
            problems.append(f"{field} is not text or null")
    return problems


def parse_work_order(text: str) -> tuple[dict | None, list[str]]:
    """The work order and no problems, or None and the reasons. Local repair runs before any model call."""
    try:
        order = json.loads(strip_to_object(text or ""))
    except json.JSONDecodeError as error:
        return None, [f"not valid JSON: {error.msg}"]
    problems = check_work_order(order)
    return (None, problems) if problems else ({field: order[field] for field in FIELDS}, [])
