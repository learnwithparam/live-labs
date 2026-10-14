"""`support ask`, `support review` and `support serve`."""

import argparse
import json
import sys
from pathlib import Path

HELP = Path("data/help")
LABELLED = Path("data/labelled.jsonl")
QUESTIONS = Path("data/questions.jsonl")


def client_and_models():
    from openai import OpenAI

    from courier.config import settings

    base_url, key, model, judge = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model, judge


def help_centre():
    from courier.kb import HelpCentre, load

    return HelpCentre(load(HELP))


def ask(question: str) -> int:
    from courier.desk import handle

    client, model, _ = client_and_models()
    got = handle(question, help_centre(), client, model)
    print(f"Draft: {got['reply']}\nCited: {', '.join(got['cited']) or 'nothing'}\n")
    for claim in got["claims"]:
        print(f"  {'ok ' if claim['verdict'] == 'supported' else 'NO '} {claim['claim']}")
    print(f"\n{'Sent' if got['send'] else 'Held for an agent'}: {got['why']}")
    return 0


def show(name: str, result: dict) -> None:
    m = result["matrix"]
    print(f"{name:7} agrees with agents on {result['agree']} of {result['total']}, kappa {result['kappa']}")
    print(f"        agent pass: judge pass {m['pass']['pass']}, judge fail {m['pass']['fail']}")
    print(f"        agent fail: judge pass {m['fail']['pass']}, judge fail {m['fail']['fail']}")


def review(naive: bool) -> int:
    from courier.agree import load_labelled
    from courier.desk import review as run

    client, model, judge = client_and_models()
    rows = load_labelled(LABELLED)
    result = run(rows, help_centre(), client, model, judge)
    g = result["guard"]
    print(f"{len(rows)} replies agents labelled. Guard on {model}, judge on {judge}.\n")
    print(f"Without the guard: {g['unsupported']} unsupported replies sent")
    print(f"With the guard:    {len(g['sent'])} unsupported replies sent {g['sent']}")
    print(f"Good replies held for an agent: {len(g['held_good'])} {g['held_good']}\n")
    if naive:
        show("Naive", result["naive"])
    show("Judge", result["judge"])
    by_id = {r["id"]: r for r in result["rows"]}
    print("\nWhere the judge and the agent disagree:")
    for i in result["judge"]["disagree"]:
        r = by_id[i]
        print(f"  {i} agent {r['human']}, judge {r['judge']}: {r['judge_reason']}")
    Path("out").mkdir(exist_ok=True)
    Path("out/review.json").write_text(json.dumps(result, indent=1))
    print(f"\nCost ${result['cost']:.4f}. Full results in out/review.json")
    return 0


def serve_page(port: int) -> int:
    from courier.agree import load_labelled
    from courier.serve import Room, serve

    client, model, judge = client_and_models()
    questions = [json.loads(line)["question"] for line in QUESTIONS.read_text().splitlines() if line.strip()]
    room = Room(help_centre(), load_labelled(LABELLED), questions, client, model, judge, Path("out/agent.jsonl"))
    server = serve(room, port)
    print(f"Courier support inbox on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="support")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ask").add_argument("question")
    sub.add_parser("review").add_argument("--naive", action="store_true", help="also score the judge with no sources or rubric")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "ask":
        sys.exit(ask(args.question))
    if args.command == "review":
        sys.exit(review(args.naive))
    sys.exit(serve_page(args.port))
