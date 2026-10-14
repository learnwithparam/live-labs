"""`policy ask`, `policy review` and `policy serve`."""

import argparse
import json
import sys
from pathlib import Path

DOCS = Path("data/docs")
EXPERT = Path("data/expert.jsonl")


def client_and_models():
    from openai import OpenAI

    from policy.config import settings

    base_url, key, model, judge = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model, judge


def policies():
    from policy.ingest import ingest
    from policy.search import Policies

    return Policies(ingest(DOCS))


def show_step(node: str, update: dict) -> None:
    if node == "plan":
        print(f"plan    {len(update['queries'])} searches")
    elif node == "search":
        for s in update["searches"]:
            print(f"search  {s['query']!r}\n          found {', '.join(s['found']) or 'nothing'}")
    elif node == "check":
        print(f"check   missing: {update['missing']}" if update["queries"] else "check   enough to answer")


def ask(question: str, use_plain: bool) -> int:
    from policy.graph import build, start
    from policy.plain import plain

    client, model, _ = client_and_models()
    found = policies()
    if use_plain:
        result = plain(question, found, client, model)
        print(f"search  {question!r}\n          found {', '.join(result['sources'])}")
    else:
        result, cost, calls = start(question), 0.0, 0
        # stream_mode="updates" yields each node's output as it finishes: the trace you watch.
        for step in build(found, client, model).stream(result, stream_mode="updates"):
            for node, update in step.items():
                show_step(node, update)
                cost, calls = cost + update.get("cost", 0.0), calls + update.get("calls", 0)
                result |= update | {"cost": cost, "calls": calls}
    print(f"\n{result['answer'] or 'Not covered: this goes to the policy owners.'}")
    print(f"\nCited: {', '.join(result['cited']) or 'nothing'}")
    print(f"{result['calls']} model calls, ${result['cost']:.5f}")
    return 0


def review() -> int:
    from policy.grade import load
    from policy.review import WAYS
    from policy.review import review as run

    client, model, judge = client_and_models()
    questions = load(EXPERT)
    result = run(questions, policies(), client, model, judge)
    print(f"{len(questions)} questions with an expert's answer. Pipelines on {model}, graded by {judge}.\n")
    print(f"{'':9}{'accepted':<12}{'sections found':<17}{'calls':<8}cost per question")
    for how in WAYS:
        s = result[how]
        print(f"{how:9}{str(s['accepted']) + ' of ' + str(s['total']):<12}{str(s['found']) + ' of ' + str(s['needs']):<17}"
              f"{s['calls_per_question']:<8}${s['cost_per_question']:.5f}")
    rows = {(r["id"], r["how"]): r for r in result["rows"]}
    print("\nNot accepted:")
    for q in questions:
        for how in WAYS:
            r = rows[(q.id, how)]
            if not r["accepted"]:
                print(f"  {q.id} {how:8} {r['reason']}")
    print(f"\nSent to a person (agentic): {', '.join(result['agentic']['to_a_person']) or 'none'}")
    Path("out").mkdir(exist_ok=True)
    Path("out/review.json").write_text(json.dumps(result, indent=1))
    print(f"Grading cost ${result['grading_cost']:.4f}. Full results in out/review.json")
    return 0


def serve_page(port: int) -> int:
    from policy.grade import load
    from policy.serve import Room, serve

    client, model, judge = client_and_models()
    server = serve(Room(policies(), load(EXPERT), client, model, judge, Path("out/escalated.jsonl")), port)
    print(f"Ledgerline policy desk on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="policy")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("ask")
    one.add_argument("question")
    one.add_argument("--plain", action="store_true", help="search once with the question, no agent")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "ask":
        sys.exit(ask(args.question, args.plain))
    if args.command == "review":
        sys.exit(review())
    sys.exit(serve_page(args.port))
