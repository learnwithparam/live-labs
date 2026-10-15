"""Replay six customers' chats under each policy, then score what a new session recalls against what
each customer actually said, never against what the model wrote.

The truth is in data/customers.jsonl. `facts` are what the customer said that is still true; `gone`
are what they corrected or asked us to forget. A memory is matched by the words it must hold, so the
check needs no model. The simulated customer confirms a recalled memory that holds a current fact,
and a stored memory that holds a gone fact is a wrong memory: it is stale or forgotten, and it can
come back in any answer.
"""

import json
import re
from pathlib import Path

from pawsley.agent import recall
from pawsley.keeper import WRITERS, pending
from pawsley.memory import all_memories

CUSTOMERS = Path(__file__).resolve().parents[2] / "data" / "customers.jsonl"


def customers() -> list[dict]:
    return [json.loads(line) for line in CUSTOMERS.read_text().splitlines() if line.strip()]


def customer(customer_id: str) -> dict:
    found = [each for each in customers() if each["id"] == customer_id.upper()]
    if not found:
        raise SystemExit(f"No customer {customer_id}. They are " + ", ".join(each["id"] for each in customers()) + ".")
    return found[0]


def says(text: str, words: list[str]) -> bool:
    """Every word is in the text. "a|b" accepts either, and a word must start at a word edge, so 4 is not in 14."""
    lowered = text.lower()
    return all(any(re.search(r"(?<![a-z0-9])" + re.escape(choice), lowered) for choice in word.split("|"))
               for word in words)


def verdict(text: str, truth: dict) -> str:
    """confirmed, wrong, or unconfirmed: what the customer would say on seeing this memory."""
    for gone in truth["gone"]:
        # "Moved from Mill Lane to Harbour Road" holds the old words but tells the new truth.
        if says(text, gone["words"]) and not any(says(text, [word]) for word in gone["unless"]):
            return "wrong"
    return "confirmed" if any(says(text, fact["words"]) for fact in truth["facts"]) else "unconfirmed"


def replay(memory, model, policy: str, truth: dict, on_turn=lambda turn: None) -> None:
    """Every session, in order. Only the customer's words are saved, so the agent's replies are skipped."""
    for session_number, session in enumerate(truth["sessions"], 1):
        for message in session:
            events = WRITERS[policy](memory, model, truth["id"], message)
            on_turn({"customer": truth["id"], "session": session_number, "message": message, "events": events})


def score(memory, truth: dict) -> dict:
    """A new session asks each probe question. Its recall is what the customer gets to check."""
    recalled: dict[str, str] = {}
    for probe in truth["probes"]:
        for item in recall(memory, truth["id"], probe):
            recalled[item["id"]] = item["memory"]
    stored = all_memories(memory, truth["id"])
    kept = [item for item in stored if not pending(item)]
    waiting = [item for item in stored if pending(item)]
    checked = [{"text": text, "verdict": verdict(text, truth)} for text in recalled.values()]
    return {
        "customer": truth["id"],
        "recalled": checked,
        "confirmed": sum(row["verdict"] == "confirmed" for row in checked),
        "wrong_recalled": sum(row["verdict"] == "wrong" for row in checked),
        "wrong": [item["memory"] for item in kept if verdict(item["memory"], truth) == "wrong"],
        "facts_kept": sum(any(says(item["memory"], fact["words"]) for item in kept) for fact in truth["facts"]),
        "facts": len(truth["facts"]),
        "asked": [{"text": item["memory"], **(item.get("metadata") or {})} for item in waiting],
        # A conflict the script expects the agent to bring to the customer, rather than settle alone.
        "asks_met": sum(any(says(item["memory"], [word]) for item in waiting) for word in truth["asks"]),
        "asks": len(truth["asks"]),
    }


def totals(rows: list[dict], bill: dict) -> dict:
    summed = {key: sum(row[key] for row in rows) for key in ("confirmed", "wrong_recalled", "facts_kept", "facts", "asks_met", "asks")}
    return {**summed, "recalled": sum(len(row["recalled"]) for row in rows), "wrong": sum(len(row["wrong"]) for row in rows),
            "asked": sum(len(row["asked"]) for row in rows), "calls": bill["calls"], "cost": bill["cost"]}
