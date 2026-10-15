"""Run one chat on one engine against a fresh order system, and score all 10 chats per engine.

The score reads the order system after each chat, never the reply. A chat is resolved when the
changes match what support would have made, no person was needed, and the budget held.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from kestrel import loop, reply
from kestrel.budget import Budget
from kestrel.chats import Chat, chats, instruction, matches
from kestrel.shop import Shop
from kestrel.tools import OrderTools

ENGINES = ("reply", "loop", "adk")


def run_chat(engine: str, customer: str, message: str, models: dict, on_step: Callable[[dict], None] = lambda step: None,
             budget: Budget | None = None) -> tuple[Shop, Budget, dict]:
    """One chat. `models` holds an OpenAI client, its model name, and an ADK model for the ADK engine."""
    shop = Shop()
    tools = OrderTools(shop, customer)
    budget = budget or Budget(models["model"])
    if engine == "reply":
        got = reply.run(message, instruction(shop, customer, tools=False), shop.orders_of(customer),
                        models["client"], models["model"], budget, on_step)
    elif engine == "loop":
        got = loop.run(message, instruction(shop, customer), tools.all(), models["client"], models["model"], budget,
                       tools.hand_off, on_step)
    else:
        from kestrel import adk  # ADK is a heavy import; the other engines do not need it

        got = adk.run(message, instruction(shop, customer), tools.all(), models["adk"], budget, tools.hand_off, on_step)
    return shop, budget, got


def outcome(chat: Chat, shop: Shop, got: dict) -> str:
    """One word per chat, the same on the page and in the review."""
    handed = bool(shop.handoffs())
    if got["stopped"] == "budget":
        return "over budget"
    if chat.handoff:
        return "handed off" if handed and not shop.changes() else "missed hand-off"
    if handed:
        return "handed off needlessly"
    return "resolved" if matches(chat, shop) else "wrong state"


def score(rows: list[dict]) -> dict:
    resolvable = [row for row in rows if not row["handoff"]]
    actions = [row for row in resolvable if row["expect"]]
    return {
        "chats": len(rows),
        "resolved": sum(row["outcome"] == "resolved" for row in resolvable),
        "resolvable": len(resolvable),
        # The chats that needed a change in the order system: a reply alone cannot do these.
        "actions_done": sum(row["outcome"] == "resolved" for row in actions),
        "actions": len(actions),
        "handed_off_right": sum(row["outcome"] == "handed off" for row in rows),
        "should_hand_off": sum(row["handoff"] for row in rows),
        "wrong_state": sum(row["outcome"] in ("wrong state", "missed hand-off") for row in rows),
        "needless_hand_offs": sum(row["outcome"] == "handed off needlessly" for row in rows),
        "over_budget": sum(row["outcome"] == "over budget" for row in rows),
        "steps": sum(row["steps"] for row in rows),
        "cost": round(sum(row["cost"] for row in rows), 5),
    }


def review(models: dict, engines=ENGINES, max_steps: int | None = None, on_row=None) -> dict:
    def one(engine: str, chat: Chat) -> dict:
        budget = Budget(models["model"], **({"max_steps": max_steps} if max_steps else {}))
        shop, budget, got = run_chat(engine, chat.customer, chat.message, models, budget=budget)
        row = {"engine": engine, "chat": chat.id, "handoff": chat.handoff, "expect": chat.expect,
               "outcome": outcome(chat, shop, got), "changes": shop.changes(), "handoffs": shop.handoffs(),
               "reply": got["reply"], "steps": budget.steps, "cost": budget.cost}
        if on_row:
            on_row(row)
        return row

    result = {}
    for engine in engines:
        # Chats are independent, each on its own order system, so they run side by side.
        with ThreadPoolExecutor(5) as pool:
            rows = list(pool.map(lambda customer_chat: one(engine, customer_chat), chats()))
        result[engine] = {"score": score(rows), "rows": rows}
    return result
