"""The 10 seeded chats, what support would do for each, and the system prompt every engine shares."""

import json
from dataclasses import dataclass
from pathlib import Path

from kestrel.shop import DATA, Shop


@dataclass
class Chat:
    id: str
    customer: str
    message: str
    expect: list[dict]  # the changes support would make in the order system
    handoff: bool  # whether a person must take it


def chats(path: Path = DATA / "chats.jsonl") -> list[Chat]:
    return [Chat(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def chat(chat_id: str) -> Chat:
    return next(chat for chat in chats() if chat.id == chat_id)


def instruction(shop: Shop, customer: str, tools: bool = True) -> str:
    # The policy is in the prompt so the model can explain a refusal; the tools enforce it either way.
    policy = (DATA / "policy.md").read_text()
    head = (f"You are Kestrel Outdoor's order support assistant. Today is {shop.today}. The signed-in customer is "
            f"{shop.customer_name(customer)}.\n\n{policy}\n")
    if not tools:
        return head + "Help the customer with their request. Reply in two or three short sentences."
    # The second sentence fixes the commonest agent failure we saw: "I will cancel it" as the last turn,
    # with no tool call, so the loop ends and nothing happens.
    return head + ("Use the tools to look up orders and to act. Never say you did something unless a tool returned ok. "
                   "Never say you will do something: call the tool now, then tell the customer it is done. "
                   "If the customer does not give an order number, find it from their orders. Do everything they asked, "
                   "without asking them to confirm what they already asked for, then reply in two or three short sentences.")


def matches(chat: Chat, shop: Shop) -> bool:
    """Did the order system end up where support would have left it? Order and amount must match."""
    got = shop.changes()
    if len(got) != len(chat.expect):
        return False
    for want in chat.expect:
        hit = [change for change in got if change["kind"] == want["kind"] and change["order_id"] == want["order"]
               and ("amount" not in want or abs((change["amount"] or 0) - want["amount"]) < 0.005)
               and want.get("detail", "") in (change["detail"] or "")]
        if not hit:
            return False
    return True
