"""The naive bot: one model call that answers the customer, with their orders pasted in and no tools.

It reads well. It apologises, it promises a refund, it says the order is cancelled. Then nothing in
the order system changes, because talking is not doing. The review scores it by the order system.
"""

import json
from typing import Callable

from kestrel.budget import Budget


def run(message: str, system: str, orders: list[dict], client, model: str, budget: Budget,
        on_step: Callable[[dict], None] = lambda step: None) -> dict:
    # It can see the orders, so it can answer "where is my parcel". It cannot change them.
    context = f"{system}\n\nThe customer's orders:\n{json.dumps(orders, indent=1)}"
    response = client.chat.completions.create(model=model, messages=[
        {"role": "system", "content": context}, {"role": "user", "content": message}])
    budget.charge(response.usage.prompt_tokens, response.usage.completion_tokens)
    on_step({"kind": "budget", **budget.state()})
    reply = response.choices[0].message.content or ""
    on_step({"kind": "reply", "text": reply})
    return {"reply": reply, "stopped": "reply"}
