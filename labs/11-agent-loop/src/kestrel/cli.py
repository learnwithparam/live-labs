"""`support`: run a chat on one engine, review all 10 on every engine, or open the help page."""

import argparse
import json
import sys
from pathlib import Path

from kestrel.config import llm_settings

REVIEW = Path("out/review.json")
# Seconds per model call. With no timeout a stalled call waits ten minutes and the customer waits with it.
TIMEOUT = 60
NAMES = {"reply": "Reply only", "loop": "Our loop", "adk": "Google ADK"}


def models(engines=("reply", "loop", "adk")) -> dict:
    from openai import OpenAI

    base, key, model = llm_settings()
    got = {"client": OpenAI(base_url=base, api_key=key, timeout=TIMEOUT, max_retries=2), "model": model}
    if "adk" in engines:
        from kestrel.adk import openrouter

        got["adk"] = openrouter(model, base, key, TIMEOUT)
    return got


def show(step: dict) -> None:
    if step["kind"] == "call":
        print(f"  call    {step['tool']}({', '.join(f'{k}={v!r}' for k, v in step['args'].items())})")
    elif step["kind"] == "result":
        print(f"  result  {json.dumps(step['result'])[:110]}")
    elif step["kind"] == "budget":
        print(f"  step {step['steps']} of {step['max_steps']}, ${step['cost']:.4f} of ${step['max_cost']:.2f}")
    elif step["kind"] == "handoff":
        print(f"  handed to a person: {step['reason']}")


def one_chat(chat_id: str, engine: str, max_steps: int | None) -> int:
    from kestrel.budget import Budget
    from kestrel.chats import chat
    from kestrel.review import outcome, run_chat

    c, m = chat(chat_id), models((engine,))
    print(f"{c.id} {NAMES[engine]}. Customer: {c.message}\n")
    budget = Budget(m["model"], **({"max_steps": max_steps} if max_steps else {}))
    shop, budget, got = run_chat(engine, c.customer, c.message, m, show, budget)
    print(f"\nAssistant: {got['reply'].strip()}\n")
    changes = shop.changes()
    print("Order system: " + ("; ".join(f"{x['kind']} {x['order_id']}" + (f" ${x['amount']:.2f}" if x["amount"] else "")
                                        for x in changes) if changes else "no change"))
    print(f"Outcome: {outcome(c, shop, got)}. {budget.steps} model calls, ${budget.cost:.4f}.")
    return 0


def review_all(max_steps: int | None) -> int:
    from kestrel.review import ENGINES, review

    m = models()
    print(f"10 chats on each engine, each on a fresh order system. Model {m['model']}.\n")
    got = review(m, max_steps=max_steps, on_row=lambda r: print(f"  {NAMES[r['engine']]:<11}{r['chat']}  {r['outcome']}"))
    print(f"\n{'':<12}{'resolved':<10}{'actions':<9}{'hand-offs':<11}{'wrong':<7}{'over budget':<13}{'steps':<7}cost")
    for engine in ENGINES:
        s = got[engine]["score"]
        print(f"{NAMES[engine]:<12}{s['resolved']} of {s['resolvable']:<5}{s['actions_done']} of {s['actions']:<4}"
              f"{s['handed_off_right']} of {s['should_hand_off']:<6}{s['wrong_state']:<7}{s['over_budget']:<13}{s['steps']:<7}${s['cost']:.4f}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(got, indent=1))
    print(f"\nEvery chat, reply and change: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="support")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("chat")
    one.add_argument("chat", help="a chat id, KC-01 to KC-10")
    one.add_argument("--engine", choices=["reply", "loop", "adk"], default="loop")
    one.add_argument("--max-steps", type=int, help="tighten the step budget")
    sub.add_parser("review").add_argument("--max-steps", type=int)
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "chat":
        sys.exit(one_chat(args.chat, args.engine, args.max_steps))
    if args.command == "review":
        sys.exit(review_all(args.max_steps))
    from kestrel.serve import main as serve

    sys.exit(serve(args.port))
