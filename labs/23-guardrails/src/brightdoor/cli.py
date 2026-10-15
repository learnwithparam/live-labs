"""`listing`: show the rails, send one buyer message, review the labelled set, or open the property page."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REVIEW = Path("out/review.json")
TIMEOUT = 90  # seconds per model call


def models():
    """The assistant's client and model, and the Guard with its own rails model."""
    from openai import AsyncOpenAI

    from brightdoor.config import llm_settings
    from brightdoor.guard import Guard, rail_model

    settings = llm_settings()
    client = AsyncOpenAI(base_url=settings["base_url"], api_key=settings["api_key"], timeout=TIMEOUT, max_retries=2)
    guard = Guard(rail_model(settings["base_url"], settings["api_key"], settings["rail_model"]))
    return client, settings["model"], guard, settings["rail_model"]


def show_rails() -> int:
    from nemoguardrails import RailsConfig

    from brightdoor.guard import RAILS

    config = RailsConfig.from_path(str(RAILS))
    print(f"NeMo Guardrails config in {RAILS.relative_to(Path.cwd()) if RAILS.is_relative_to(Path.cwd()) else RAILS}\n")
    print("Input rails:  " + ", ".join(config.rails.input.flows))
    print("Output rails: " + ", ".join(config.rails.output.flows))
    policy = next(prompt.content for prompt in config.prompts if prompt.task == "self_check_input")
    print("\nself check input asks the rails model:\n\n  " + "\n  ".join(policy.splitlines()[:12]) + "\n  ...")
    return 0


def ask(message_id: str, prompt_only: bool) -> int:
    from brightdoor.assistant import listings, messages
    from brightdoor.review import NAMES, handle

    item = next((each for each in messages() if each["id"] == message_id.upper()), None)
    if not item:
        raise SystemExit(f"No message {message_id}. Try M-01 to M-{len(messages()):02d}.")
    client, model, guard, _ = models()
    listing = listings()[item["listing"]]
    print(f"{item['id']} on {listing['address']} ({item['label']}, {item['kind']}). "
          f"{NAMES['prompt' if prompt_only else 'rails']}.\nBuyer: {item['text']}\n")
    row = asyncio.run(handle(item, listing, client, model, None if prompt_only else guard))
    if row["draft"] and row["draft"] != row["reply"]:
        print(f"Drafted: {row['draft']}\n")
    print(f"Buyer sees: {row['reply']}\n")
    if row["held"]:
        print(f"Held by: {row['rail']} ({row['decision']}). {row['reason']}")
        if row["quote"]:
            print(f"Planted text: {row['quote']}")
    else:
        print("Held by: nothing; the answer went out.")
    print(f"Cost ${row['cost']:.5f}")
    return 0


def review_all() -> int:
    from brightdoor.review import NAMES, review

    client, model, guard, rail_model_name = models()
    print(f"68 labelled buyer messages, each with the rule in the prompt and behind NeMo Guardrails.\n"
          f"Assistant {model}, rails {rail_model_name}.\n")
    results = asyncio.run(review(client, model, guard, lambda arm, row: print(
        f"  {NAMES[arm]:<20}{row['id']}  {row['label']:<10}{'held by ' + row['rail'] if row['held'] else 'answered'}")))
    print(f"\n{'':<21}{'violations caught':<20}{'good answers blocked':<23}{'to a human':<12}cost")
    for arm, result in results.items():
        totals = result["score"]
        print(f"{NAMES[arm]:<21}{totals['caught']} of {totals['violating']:<14}{totals['wrongly_blocked']} of "
              f"{totals['compliant']:<17}{totals['to_human']:<12}${totals['cost']:.4f}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(results, indent=1))
    print(f"\nEvery message, rail and reply: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="listing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rails")
    one = sub.add_parser("ask")
    one.add_argument("message", help="a message id from data/messages.jsonl, M-01 to M-68")
    one.add_argument("--prompt-only", action="store_true", help="skip the rails: the rule lives only in the prompt")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8230)
    args = parser.parse_args()
    if args.command == "rails":
        sys.exit(show_rails())
    if args.command == "ask":
        sys.exit(ask(args.message, args.prompt_only))
    if args.command == "review":
        sys.exit(review_all())
    from brightdoor.serve import main as serve

    sys.exit(serve(args.port))
