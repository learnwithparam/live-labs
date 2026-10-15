"""`research`: search the library, run either version on Anouk's question, compare both, or open the reader."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

OUT = Path("out")
NAMES = {"single": "Single agent", "lead": "Lead + subagents"}


def search(query: str) -> int:
    from kitebird.corpus import Library

    hits = Library().search(query)
    print(f"{len(hits)} sources for {query!r}:\n")
    for hit in hits:
        print(f"  {hit['source']}  {hit['score']:>5}  {hit['date']}  {hit['title']}")
        print(f"        {hit['publisher']}")
    return 0


def printer():
    """Print each agent's new searches and status changes as they happen, one line each."""
    shown: dict[str, tuple] = {}

    def on_event(state: dict) -> None:
        seen_status, seen_queries = shown.get(state["agent"], (None, 0))
        for query in state["queries"][seen_queries:]:
            print(f"  {state['agent']:<15}search  {query}")
        if state["status"] != seen_status:
            tokens = state["usage"].get("tokens", 0)
            print(f"  {state['agent']:<15}{state['status']:<8}" + (f"{tokens:,} tokens" if tokens else state["objective"][:80]))
        shown[state["agent"]] = (state["status"], len(state["queries"]))

    return on_event


def run(version: str) -> int:
    from kitebird.config import model_client
    from kitebird.lead import run_lead, run_single

    client, model_name = model_client()
    print(f"{NAMES[version]} on Anouk's question. Model {model_name}.\n")
    runner = run_lead if version == "lead" else run_single
    report = asyncio.run(runner(client, model_name, printer()))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{version}.json").write_text(json.dumps(report, indent=1))
    print(f"\n{report['title']}\n")
    for claim in (claim for section in report["sections"] for claim in section["claims"]):
        mark = "ok    " if claim["verdict"] == "supported" else "EDITOR"
        print(f"  {mark} [{claim.get('source')}] {claim['text'][:100]}")
        if claim["verdict"] != "supported":
            print(f"         {claim['verdict']}: {claim.get('reason', '')[:100]}")
    print("\n" + summary(report))
    print(f"Report, findings and every agent's searches: {OUT / f'{version}.json'}")
    return 0


def summary(report: dict) -> str:
    score, research = report["score"], report["usage"]["research"]
    return (f"{NAMES[report['version']]}: {score['supported']} of {score['claims']} claims supported by their cited source, "
            f"{score['editor']} for the editor, {len(score['covered'])} of {score['aspects']} questions covered, "
            f"{research.get('tokens', 0):,} tokens, ${research.get('cost', 0):.4f} per report.")


def compare() -> int:
    reports = {version: json.loads((OUT / f"{version}.json").read_text()) for version in NAMES if (OUT / f"{version}.json").exists()}
    if len(reports) < 2:
        print("Run `research run single` and `research run lead` first.")
        return 1
    print(f"{'':<18}{'supported':<12}{'editor':<8}{'covered':<9}{'agents':<8}{'tool calls':<12}{'tokens':<10}{'seconds':<9}cost")
    for version, report in reports.items():
        score, research = report["score"], report["usage"]["research"]
        tool_calls = sum(agent["tool_calls"] for agent in report["agents"])
        searchers = sum(agent["agent"] != "lead" for agent in report["agents"])
        print(f"{NAMES[version]:<18}{score['supported']} of {score['claims']:<6}{score['editor']:<8}"
              f"{len(score['covered'])} of {score['aspects']:<4}{searchers:<8}{tool_calls:<12}{research.get('tokens', 0):<10,}"
              f"{report['seconds']:<9}${research.get('cost', 0):.4f}")
    single, lead = reports["single"]["usage"]["research"], reports["lead"]["usage"]["research"]
    print(f"\nThe lead and its subagents used {lead['tokens'] / single['tokens']:.1f} times the single agent's tokens.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="research")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("search").add_argument("query")
    sub.add_parser("run").add_argument("version", choices=list(NAMES))
    sub.add_parser("compare")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "search":
        sys.exit(search(args.query))
    if args.command == "run":
        sys.exit(run(args.version))
    if args.command == "compare":
        sys.exit(compare())
    from kitebird.serve import main as serve

    sys.exit(serve(args.port))
