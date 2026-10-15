"""`support`: replay one customer, chat as them in a new session, list their memories, review both
policies, or open the support chat."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pawsley.config import OUT

REVIEW = OUT / "review.json"
TIMEOUT = 60  # seconds per model call


def store_folder(policy: str) -> Path:
    return OUT / "memory" / policy


def model():
    from openai import OpenAI

    from pawsley.config import llm_settings
    from pawsley.memory import BilledModel

    base_url, api_key, model_name = llm_settings()
    return BilledModel(OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name)


def open_store(policy: str, billed, embedder=None, fresh: bool = False):
    from pawsley.memory import local_embedder, open_memory

    return open_memory(store_folder(policy), billed, embedder or local_embedder(), fresh=fresh)


def show_turn(turn: dict) -> None:
    print(f"  session {turn['session']}  customer: {turn['message']}")
    for event in turn["events"]:
        detail = event.get("text") or ""
        if event["event"] in ("replaced", "forgotten"):
            detail = f"{detail}  (was: {event['was']})" if detail else f"was: {event['was']}"
        if event["event"] == "asked":
            detail = f"{detail}  -> asks: {event['question']}"
        print(f"     {event['event']:<10}{detail}")


def replay_one(customer_id: str, policy: str) -> int:
    from pawsley.keeper import NAMES
    from pawsley.review import customer, replay, score

    truth, billed = customer(customer_id), model()
    memory = open_store(policy, billed, fresh=True)
    print(f"{truth['id']} {truth['name']} ({truth['pets']}), {NAMES[policy]}. Model {billed.model}.\n")
    replay(memory, billed, policy, truth, show_turn)
    result = score(memory, truth)
    print("\nA new session asks: " + " / ".join(truth["probes"]))
    for row in result["recalled"]:
        print(f"  {row['verdict']:<12}{row['text']}")
    for item in result["asked"]:
        print(f"  waiting     {item['text']}  -> {item.get('question')}")
    print(f"\nRecalled {len(result['recalled'])}, confirmed {result['confirmed']}. "
          f"Wrong memories kept: {len(result['wrong'])}. Waiting on the customer: {len(result['asked'])}. "
          f"{billed.bill['calls']} model calls, ${billed.bill['cost']:.4f}.")
    return 0


def chat_once(customer_id: str, policy: str, message: str) -> int:
    from pawsley.agent import chat
    from pawsley.review import customer

    truth, billed = customer(customer_id), model()
    memory = open_store(policy, billed)
    turn = chat(memory, billed, policy, truth["id"], message)
    print("Recalled: " + ("; ".join(item["text"] for item in turn["recalled"]) or "nothing"))
    print(f"Agent: {turn['reply'].strip()}")
    show_turn({"session": "new", "message": message, "events": turn["events"]})
    return 0


def list_memories(customer_id: str, policy: str) -> int:
    from pawsley.keeper import pending
    from pawsley.memory import BilledModel, all_memories

    memory = open_store(policy, BilledModel(None, "openai/gpt-6-luna"))
    found = all_memories(memory, customer_id.upper())
    print(f"{customer_id.upper()}: {len(found)} memories in {store_folder(policy)}")
    for item in found:
        # A status change is an UPDATE too; only a change of words has a "was".
        changes = [row for row in memory.history(item["id"])
                   if row["event"] == "UPDATE" and row["old_memory"] != row["new_memory"]]
        was = f"  (was: {changes[-1]['old_memory']})" if changes else ""
        print(f"  {'WAITING ' if pending(item) else ''}{item['memory']}{was}")
    return 0


def review_all() -> int:
    from pawsley.keeper import NAMES, POLICIES
    from pawsley.memory import local_embedder
    from pawsley.review import customers, replay, score, totals

    embedder = local_embedder()
    everyone = customers()

    def run(policy: str) -> dict:
        billed = model()
        memory = open_store(policy, billed, embedder, fresh=True)
        rows = []
        for truth in everyone:
            replay(memory, billed, policy, truth)
            rows.append(score(memory, truth))
            print(f"  {NAMES[policy]:<24}{truth['id']}  confirmed {rows[-1]['confirmed']} of {len(rows[-1]['recalled'])}"
                  f"  wrong {len(rows[-1]['wrong'])}  waiting {len(rows[-1]['asked'])}", flush=True)
        return {"rows": rows, "score": totals(rows, billed.bill), "model": billed.model}

    print(f"{len(everyone)} customers, {sum(len(each['sessions']) for each in everyone)} chat sessions, replayed under each policy.\n")
    with ThreadPoolExecutor(len(POLICIES)) as pool:
        results = dict(zip(POLICIES, pool.map(run, POLICIES)))
    print(f"\n{'':<26}{'confirmed':<16}{'wrong memories':<16}{'facts kept':<13}{'asked':<8}{'calls':<7}cost")
    for policy, result in results.items():
        totals_row = result["score"]
        print(f"{NAMES[policy]:<26}{totals_row['confirmed']} of {totals_row['recalled']:<10}{totals_row['wrong']:<16}"
              f"{totals_row['facts_kept']} of {totals_row['facts']:<7}{totals_row['asked']:<8}{totals_row['calls']:<7}${totals_row['cost']:.4f}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(results, indent=1))
    print(f"\nEvery customer, recall and verdict: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="support")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("replay", "memories"):
        command = sub.add_parser(name)
        command.add_argument("customer")
        command.add_argument("--policy", choices=["append-only", "kept"], default="kept")
    talk = sub.add_parser("chat")
    talk.add_argument("customer")
    talk.add_argument("message")
    talk.add_argument("--policy", choices=["append-only", "kept"], default="kept")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "replay":
        sys.exit(replay_one(args.customer, args.policy))
    if args.command == "chat":
        sys.exit(chat_once(args.customer, args.policy, args.message))
    if args.command == "memories":
        sys.exit(list_memories(args.customer, args.policy))
    if args.command == "review":
        sys.exit(review_all())
    from pawsley.serve import main as serve

    sys.exit(serve(args.port))
