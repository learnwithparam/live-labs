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


def run_chat(engine: str, customer: str, message: str, models: dict, on_step: Callable[[dict], None] = lambda s: None,
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
    resolvable = [r for r in rows if not r["handoff"]]
    actions = [r for r in resolvable if r["expect"]]
    return {
        "chats": len(rows),
        "resolved": sum(r["outcome"] == "resolved" for r in resolvable),
        "resolvable": len(resolvable),
        # The chats that needed a change in the order system: a reply alone cannot do these.
        "actions_done": sum(r["outcome"] == "resolved" for r in actions),
        "actions": len(actions),
        "handed_off_right": sum(r["outcome"] == "handed off" for r in rows),
        "should_hand_off": sum(r["handoff"] for r in rows),
        "wrong_state": sum(r["outcome"] in ("wrong state", "missed hand-off") for r in rows),
        "needless_hand_offs": sum(r["outcome"] == "handed off needlessly" for r in rows),
        "over_budget": sum(r["outcome"] == "over budget" for r in rows),
        "steps": sum(r["steps"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 5),
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
            rows = list(pool.map(lambda c: one(engine, c), chats()))
        result[engine] = {"score": score(rows), "rows": rows}
    return result
