"""Pawsley's support chat: recall what the customer told us before, answer, then save what they said.

Each chat is a new session with no transcript from the last one. Everything the agent knows about the
customer comes back through Mem0's `search`, which is the whole point: the transcript is gone, the
memories are not.
"""

from mem0 import Memory

from pawsley.keeper import WRITERS, pending
from pawsley.memory import BilledModel

# Five memories cover a customer's few facts. A wider net pulls in stale ones that the model may trust.
RECALL = 5

REPLY = """Answer a Pawsley customer in the support chat. Pawsley delivers dog and cat food on a schedule.
`memories` are what this customer told us in earlier chats; use them when they help, and do not ask
again for what they already told us. A memory that the customer asked to switch or change something
means it is changed from now on. If two memories disagree, ask which one is right. Keep the
answer to two or three sentences. Do not invent orders, prices or dates."""


def recall(memory: Memory, customer: str, message: str) -> list[dict]:
    """The memories a new session starts from. A memory waiting for the customer's answer is held back."""
    found = memory.search(message, filters={"user_id": customer}, top_k=RECALL + 3)["results"]
    return [item for item in found if not pending(item)][:RECALL]


def chat(memory: Memory, model: BilledModel, policy: str, customer: str, message: str,
         earlier: list[dict] | None = None) -> dict:
    """One customer turn: recall, reply, then save. Saving after the reply keeps the answer fast."""
    recalled = recall(memory, customer, message)
    reply = model.generate_response([
        {"role": "system", "content": REPLY},
        {"role": "system", "content": "memories: " + "; ".join(item["memory"] for item in recalled)},
        *(earlier or []),
        {"role": "user", "content": message}])
    events = WRITERS[policy](memory, model, customer, message)
    return {"reply": reply, "recalled": [{"id": item["id"], "text": item["memory"]} for item in recalled],
            "events": events}
