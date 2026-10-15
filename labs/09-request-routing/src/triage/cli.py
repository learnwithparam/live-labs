"""`triage route`, `triage review`, `triage serve` and `triage decider`."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from triage.review import WAYS

NAMES = {"embedding": "embedding", "llm": "LLM", "decision": "decision model", "both": "LLM + decision"}


def routers(ways=("embedding", "llm", "decision")) -> dict:
    """Each named router, built once. The decision model is started on this machine when it is not running."""
    from triage.config import decision_settings, llm_settings
    from triage.tickets import history, queues

    built = {}
    if "embedding" in ways:
        from triage.embed import EmbeddingRouter, FastEmbed

        built["embedding"] = EmbeddingRouter(history(), FastEmbed())
    if "llm" in ways:
        from openai import OpenAI

        from triage.llm import LLMRouter

        base_url, key, model = llm_settings()
        built["llm"] = LLMRouter(queues(), OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model)
    if "decision" in ways:
        from triage.decide import DecisionRouter, start_local

        base_url, key, model = decision_settings()
        built["server"] = start_local(base_url, model)
        built["decision"] = DecisionRouter(queues(), base_url, model, key)
    return built


def stop(built: dict) -> None:
    if built.get("server"):
        built["server"].terminate()


def route(text: str) -> int:
    from triage.route import destination, second_opinion
    from triage.tickets import Ticket

    built = routers()
    ticket = Ticket("new", "", text, "")
    got = {how: built[how](ticket) for how in ("embedding", "llm", "decision")}
    got["both"] = second_opinion(got["llm"], got["decision"])
    stop(built)
    for how in ("embedding", "llm", "decision"):
        r = got[how]
        print(f"{NAMES[how]:<16}picks {r.queue:<13}confidence {r.confidence:.2f}  ->  {destination(r)}")
    agree = "agree" if got["llm"].queue == got["decision"].queue else "disagree"
    print(f"{NAMES['both']:<16}they {agree}, decision at {got['decision'].confidence:.2f}  ->  {destination(got['both'])}")
    print("\nDecision model, every queue:")
    for queue, p in sorted(got["decision"].probabilities.items(), key=lambda kv: -kv[1]):
        print(f"  {queue:<13}{p:.2f} {'#' * round(p * 40)}")
    return 0


def review() -> int:
    from triage.review import review as run
    from triage.tickets import inbox

    built = routers()
    tickets = inbox()
    print(f"Routing {len(tickets)} tickets three ways. The decision model takes a few seconds each on a laptop.")
    try:
        result = run(tickets, {how: built[how] for how in ("embedding", "llm", "decision")})
    finally:
        stop(built)
    print(f"\n{'':<16}{'reassigned':<12}{'to a person':<13}{'right first time':<18}{'p95':<10}cost per 1,000")
    for how in WAYS:
        s = result[how]
        print(f"{NAMES[how]:<16}{s['reassigned']:<12}{s['to_person']:<13}{s['right_first_time']:<18}"
              f"{s['p95_ms'] / 1000:<10.2f}${s['cost_per_1000']:.3f}")
    print("\nLLM + decision sent to a person, with what each said:")
    rows = {(r["how"], r["id"]): r for r in result["rows"]}
    for t in tickets:
        if rows[("both", t.id)]["to"] == "person":
            llm, dec = rows[("llm", t.id)], rows[("decision", t.id)]
            print(f"  {t.id}  LLM {llm['queue']} {llm['confidence']:.2f}, decision {dec['queue']} {dec['confidence']:.2f}, solved in {t.queue}")
    Path("out").mkdir(exist_ok=True)
    Path("out/review.json").write_text(json.dumps(result, indent=1))
    print("\nEvery ticket and every probability: out/review.json")
    return 0


def serve_page(port: int) -> int:
    from triage.serve import Room, serve

    built = routers()
    server = serve(Room(built), port)
    print(f"Tallyroll triage board on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop(built)
    return 0


def decider() -> int:
    """Run the decision model in the foreground, with its log, for when an automatic start fails."""
    from triage.config import decision_settings

    _, _, model = decision_settings()
    return subprocess.call(["uv", "run", "--frozen", "--group", "decider", "strands-decider", "serve", model, "--port", "8099"])


def main() -> None:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("route").add_argument("text")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    sub.add_parser("decider")
    args = parser.parse_args()
    if args.command == "route":
        sys.exit(route(args.text))
    if args.command == "review":
        sys.exit(review())
    if args.command == "decider":
        sys.exit(decider())
    sys.exit(serve_page(args.port))
