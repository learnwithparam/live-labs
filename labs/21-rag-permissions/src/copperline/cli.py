"""`search`: sync the access lists into OpenFGA, ask as any employee, explain access, run the leak suite."""

import argparse
import asyncio
import json
import sys
import time
import urllib.request

from copperline.config import OUT, PORT, fga_url

LEAKS = OUT / "leaks.json"
NAMES = {"naive": "No check, prompt only", "post": "OpenFGA post-filter", "pre": "OpenFGA pre-filter"}


def wait_for_fga(seconds: int = 30) -> None:
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(f"{fga_url()}/healthz", timeout=2) as response:
                if b"SERVING" in response.read():
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"OpenFGA is not answering at {fga_url()}. Start it with the docker command in the README.")


def sync() -> int:
    from copperline.access import load_store
    from copperline.sync import build

    wait_for_fga()
    built = build()
    ids = load_store(built["tuples"])
    print(f"Synced {len(built['tuples'])} tuples from drive, wiki and tickets into OpenFGA store {ids['store_id']}.")
    for held in built["held"]:
        print(f"Held for an admin: {held['document']}, its access list {held['reasons'][0]}. "
              f"Visible to {held['owner'] or 'an admin'} only until an admin decides.")
    return 0


def why(user: str, document_id: str) -> int:
    from copperline.access import Authorizer, describe
    from copperline.sync import labels

    authorizer = Authorizer()
    allowed = document_id in authorizer.allowed(user, [document_id])
    print(f"Can {user} view {document_id}? {'yes' if allowed else 'no'}")
    for line in describe(authorizer.why(user, document_id), labels()) if allowed else []:
        print(f"  {line}")
    return 0


def ask(question: str, user: str, mode: str) -> int:
    from copperline.access import Authorizer
    from copperline.answer import answer, gather
    from copperline.config import model_client
    from copperline.retrieve import Index, chunks_of
    from copperline.sync import documents, people

    authorizer = None if mode == "naive" else Authorizer()
    gathered = gather(mode, user, question, Index(chunks_of(documents())), authorizer)
    client, model_name = model_client()
    reply = asyncio.run(answer(client, model_name, mode, people()[user], question, gathered["chunks"]))
    shown = list(dict.fromkeys(chunk["document"] for chunk in gathered["chunks"]))
    print(f"{NAMES[mode]}, as {user}. The model read: {', '.join(shown) or 'nothing'}")
    if mode == "post":
        print(f"OpenFGA dropped {gathered['dropped']} chunks before the model saw them.")
    print(f"\nAnswer: {reply['text']}\n\nCited: {', '.join(reply['cited']) or 'none'}. Cost ${reply['cost']:.5f}.")
    return 0


def leaks() -> int:
    from copperline.access import Authorizer
    from copperline.config import model_client
    from copperline.leaks import run_suite

    client, model_name = model_client()
    print(f"Permission test pairs, each asked as a denied and an allowed user. Model {model_name}.\n")

    def show(row: dict) -> None:
        if row["who"] == "denied":
            print(f"  {row['pair']}  {row['user']:<6}{NAMES[row['mode']]:<24}{'LEAKED' if row['leaked'] else 'safe'}")

    result = asyncio.run(run_suite(client, model_name, Authorizer(), show))
    modes = result["score"]["modes"]
    print(f"\n{'':<24}{'leaked':<10}{'in prompt':<11}in answer")
    for mode, totals in modes.items():
        print(f"{NAMES[mode]:<24}{totals['leaked']} of {totals['pairs']:<5}{totals['in_prompt']:<11}{totals['in_answer']}")
    print(f"\nAllowed users answered: {modes['post']['answered_allowed']} of {result['pairs']}. "
          f"Cost ${result['score']['cost']:.4f}.")
    OUT.mkdir(exist_ok=True)
    LEAKS.write_text(json.dumps(result, indent=1))
    print(f"Every run, answer and leak: {LEAKS}")
    return 0


def decide(document_id: str, mapping: str | None) -> int:
    from copperline.sync import record_decision

    if mapping:
        unknown, _, known = mapping.partition("=")
        record_decision(document_id, {"map": {unknown: known}})
        print(f"Recorded: {unknown} means the directory group {known}.")
    else:
        record_decision(document_id, {"keep": True})
        print(f"Recorded: {document_id} stays visible to its owner only.")
    return sync()


def main() -> None:
    parser = argparse.ArgumentParser(prog="search")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="translate the three access lists into OpenFGA tuples and load them")
    check = sub.add_parser("why", help="can this person view this document, and through which relations")
    check.add_argument("user")
    check.add_argument("document")
    one = sub.add_parser("ask")
    one.add_argument("question")
    one.add_argument("--as", dest="user", required=True)
    one.add_argument("--mode", choices=["naive", "post", "pre"], default="post")
    sub.add_parser("leaks", help="run every permission test pair in every mode")
    held = sub.add_parser("decide", help="an admin's answer for a held document")
    held.add_argument("document")
    held.add_argument("--map", help="unknown-group=directory-group")
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "sync":
        sys.exit(sync())
    if args.command == "why":
        sys.exit(why(args.user, args.document))
    if args.command == "ask":
        sys.exit(ask(args.question, args.user, args.mode))
    if args.command == "leaks":
        sys.exit(leaks())
    if args.command == "decide":
        sys.exit(decide(args.document, args.map))
    from copperline.serve import main as serve

    sys.exit(serve(args.port))
