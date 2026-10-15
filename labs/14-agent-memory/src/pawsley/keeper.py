"""How a customer's words become memories: append-only, as Mem0's `add` ships, or kept like ChatGPT's
memory, where a correction replaces the old fact and "forget it" deletes it.

Mem0's open-source `add` is additive: its extraction prompt says its "sole operation is ADD". That is
the right default for a library, because only your product knows when a fact is stale. So the policy
lives here, in one reconcile call after each `add`, and every change it makes goes through Mem0's own
`update` and `delete`, which keep a history of each memory.
"""

from mem0 import Memory

from pawsley.memory import BilledModel, all_memories

POLICIES = ["append-only", "kept"]
NAMES = {"append-only": "Append-only (Mem0 add)", "kept": "Kept like ChatGPT"}

# No persona: the call gets the job, the rules and the data. Short numbered ids, not UUIDs, because a
# model copies a short id exactly and garbles a long one; Mem0's own extractor does the same.
RECONCILE = """A pet food company's support agent keeps memories about each customer. The customer just
sent `message`. Mem0 has already saved `new` from it. `older` are memories saved before.

Return JSON {"actions": [...]} with one item per change, using these shapes:
- {"do": "replace", "old": id, "new": id}: the customer corrected or updated the old fact (they moved,
  changed a schedule, a weight, a recipe). The new memory takes the old one's place.
- {"do": "rewrite", "id": id, "text": "..."}: part of an older memory is no longer true (a pet left the
  plan). Give the memory without that part. Use "forget" if nothing true is left.
- {"do": "forget", "id": id}: the customer asked to forget or stop using this. Also forget any new
  memory that only records the request to forget.
- {"do": "ask", "old": id, "new": id, "question": "..."}: the new memory disagrees with an older one
  and the message does not say the older one is wrong. The question asks the customer which is true.
Read every older memory, not only the closest one: a stale detail can sit inside a longer memory about
something else. Change nothing that the message does not touch. Return {"actions": []} when nothing is stale."""


def append_only(memory: Memory, model: BilledModel, customer: str, message: str) -> list[dict]:
    """Mem0's `add`, as shipped. New facts land beside the old ones, and nothing is ever taken away."""
    added = memory.add([{"role": "user", "content": message}], user_id=customer)["results"]
    return [{"event": "saved", "id": item["id"], "text": item["memory"]} for item in added]


def kept(memory: Memory, model: BilledModel, customer: str, message: str) -> list[dict]:
    """`add`, then one reconcile call that replaces, rewrites, forgets, or asks the customer."""
    events = append_only(memory, model, customer, message)
    new_ids = {item["id"] for item in events}
    if not new_ids:
        return events
    # One customer's memories fit in a prompt. With thousands, search for the ones near the message instead.
    everything = all_memories(memory, customer)
    numbered = {str(number): item for number, item in enumerate(everything)}
    texts = {number: item["memory"] for number, item in numbered.items()}
    answer = model.json(RECONCILE, {
        "message": message,
        "new": [{"id": number, "text": text} for number, text in texts.items() if numbered[number]["id"] in new_ids],
        "older": [{"id": number, "text": text} for number, text in texts.items()
                  if numbered[number]["id"] not in new_ids and not pending(numbered[number])]})
    done: set[str] = set()
    for action in answer.get("actions") or []:
        events += apply(memory, customer, action, numbered, done)
    return events


def apply(memory: Memory, customer: str, action: dict, numbered: dict, done: set) -> list[dict]:
    """Carry out one reconcile action. An id the model invented, or one already changed, is skipped."""
    kind = action.get("do")
    old, new = numbered.get(str(action.get("old"))), numbered.get(str(action.get("new")))
    target = numbered.get(str(action.get("id")))
    touched = [item["id"] for item in (old, new, target) if item]
    if not touched or done.intersection(touched):
        return []
    done.update(touched)
    if kind == "replace" and old and new:
        # The old memory takes the new words, so Mem0's history shows what it was before.
        memory.update(old["id"], text=new["memory"])
        memory.delete(new["id"])
        return [{"event": "replaced", "id": old["id"], "text": new["memory"], "was": old["memory"]}]
    if kind == "rewrite" and target and str(action.get("text") or "").strip():
        memory.update(target["id"], text=action["text"].strip())
        return [{"event": "replaced", "id": target["id"], "text": action["text"].strip(), "was": target["memory"]}]
    if kind == "forget" and target:
        forget(memory, customer, target["id"])
        return [{"event": "forgotten", "id": target["id"], "was": target["memory"]}]
    if kind == "ask" and old and new:
        # The new fact waits, unused, until the customer says which is true. The old one is still recalled.
        question = str(action.get("question") or f"Is this still true: {old['memory']}?")
        memory.update(new["id"], metadata={"status": "pending", "conflicts_with": old["id"], "question": question})
        return [{"event": "asked", "id": new["id"], "text": new["memory"], "was": old["memory"], "question": question}]
    done.difference_update(touched)
    return []


def pending(item: dict) -> bool:
    return (item.get("metadata") or {}).get("status") == "pending"


def forget(memory: Memory, customer: str, memory_id: str) -> None:
    """Delete a memory so it cannot come back.

    `delete` alone is not enough. Mem0 keeps the old text in its history table, and it keeps the
    customer's last ten messages, which its extractor rereads on the next `add`: a forgotten phone
    number would be saved again. Both are cleared for this customer.
    """
    memory.delete(memory_id)
    with memory.db._lock:
        memory.db.connection.execute("DELETE FROM history WHERE memory_id = ?", (memory_id,))
        memory.db.connection.execute("DELETE FROM messages WHERE session_scope = ?", (f"user_id={customer}",))
        memory.db.connection.commit()


def edit(memory: Memory, memory_id: str, text: str) -> None:
    """The customer rewrote a memory in the panel. Their words win, and it stops waiting if it was."""
    memory.update(memory_id, text=text.strip(), metadata={"status": "confirmed"})


def settle(memory: Memory, customer: str, memory_id: str, use_new: bool) -> dict:
    """The human in the loop answers a pending question: the new fact replaces the old, or it is dropped."""
    waiting = memory.get(memory_id)
    old_id = (waiting.get("metadata") or {}).get("conflicts_with")
    old = memory.get(old_id) if old_id else None
    if use_new and old:
        memory.update(old_id, text=waiting["memory"], metadata={"status": "confirmed"})
        forget(memory, customer, memory_id)
        return {"event": "replaced", "id": old_id, "text": waiting["memory"], "was": old["memory"]}
    if use_new:
        memory.update(memory_id, metadata={"status": "confirmed"})
        return {"event": "saved", "id": memory_id, "text": waiting["memory"]}
    forget(memory, customer, memory_id)
    return {"event": "forgotten", "id": memory_id, "was": waiting["memory"]}


WRITERS = {"append-only": append_only, "kept": kept}
